#!/usr/bin/env python3
# ══════════════════════════════════════════════════════════════
#  nas-gateway —— 配置生成器
#
#  输入：
#    <root>/services.yml          你的配置（域名 / 服务列表 / 策略）
#    <root>/authelia/base.yml     Authelia 策略基座
#
#  输出：
#    <root>/caddy/Caddyfile           Caddy 反向代理配置
#    <root>/authelia/configuration.yml Authelia 实际读取的配置
#    <root>/portal/index.html          黄页
#
#  用法（在容器里跑，或用 scripts/apply-config.sh）：
#    python generate_config.py --root /gateway
# ══════════════════════════════════════════════════════════════
from __future__ import annotations

import argparse
import html
import json
import re
import sys
from pathlib import Path

import yaml

AUTHELIA_PORT = 9091
PATH_RE = re.compile(r"^/[A-Za-z0-9._~/-]*$")


# ──────────────────────────────────────────────────────────────
#  工具
# ──────────────────────────────────────────────────────────────
def die(msg: str) -> None:
    print(f"❌ {msg}", file=sys.stderr)
    sys.exit(1)


def load_yaml(path: Path) -> dict:
    if not path.exists():
        die(f"找不到配置文件：{path}")
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
    except yaml.YAMLError as e:
        die(f"YAML 解析失败 {path}：\n{e}")
    if not isinstance(data, dict):
        die(f"{path} 内容不是一个 YAML 映射")
    return data


def norm_domain(raw: str) -> str:
    d = (raw or "").strip().lower()
    d = re.sub(r"^https?://", "", d).rstrip("/")
    if not d or "." not in d:
        die(f"services.yml 里的 domain 不合法：{raw!r}（应形如 fnos-nas-1.xxx.ts.net）")
    return d


def norm_path(raw: str) -> str:
    p = (raw or "").strip().rstrip("/")
    if not p.startswith("/"):
        die(f"服务 path 必须以 / 开头：{raw!r}")
    if not PATH_RE.match(p):
        die(f"服务 path 含非法字符：{raw!r}（只允许字母数字 . _ ~ - /）")
    if p == "":
        die("服务 path 不能为空")
    return p


def norm_upstream(raw: str) -> str:
    u = (raw or "").strip()
    if not re.match(r"^https?://", u):
        die(f"服务 upstream 必须以 http:// 或 https:// 开头：{raw!r}")
    return u


def validate(cfg: dict) -> tuple[str, list[dict]]:
    domain = norm_domain(cfg.get("domain", ""))
    raw_services = cfg.get("services") or []
    if not isinstance(raw_services, list):
        die("services 必须是一个列表")

    services, seen_ids, seen_paths = [], set(), set()
    for i, s in enumerate(raw_services):
        if not isinstance(s, dict):
            die(f"services[{i}] 不是一个映射")
        if not s.get("enabled", True):
            continue
        sid = str(s.get("id") or "").strip()
        if not re.match(r"^[a-z0-9][a-z0-9-]*$", sid):
            die(f"services[{i}].id 不合法：{sid!r}（小写字母/数字/连字符）")
        if sid in seen_ids:
            die(f"服务 id 重复：{sid}")
        seen_ids.add(sid)

        path = norm_path(s.get("path", ""))
        if path in seen_paths:
            die(f"服务 path 重复：{path}")
        seen_paths.add(path)

        auth = str(s.get("auth", "required")).strip().lower()
        if auth not in ("required", "bypass"):
            die(f"服务 {sid} 的 auth 只能是 required 或 bypass，收到 {auth!r}")

        services.append(
            {
                "id": sid,
                "name": str(s.get("name") or sid),
                "desc": str(s.get("desc") or ""),
                "icon": str(s.get("icon") or "🔗"),
                "path": path,
                "upstream": norm_upstream(s.get("upstream", "")),
                "strip_prefix": bool(s.get("strip_prefix", True)),
                "auth": auth,
            }
        )

    if not services:
        die("services.yml 里没有启用任何服务（enabled: true 的服务的数量为 0）")
    return domain, services


# ──────────────────────────────────────────────────────────────
#  Caddyfile
# ──────────────────────────────────────────────────────────────
def gen_caddyfile(cfg: dict, domain: str, services: list[dict]) -> str:
    listen = str((cfg.get("gateway") or {}).get("listen") or ":8080").strip()
    # Authelia 的认证端点路径随版本不同：
    #   v4.38+ → /api/authz/forward-auth   （当前默认）
    #   更老的版本 → /api/verify
    # 用 services.yml 的 authelia.auth_endpoint 可以覆盖。
    auth_ep = str(
        (cfg.get("authelia") or {}).get("auth_endpoint") or "/api/authz/forward-auth"
    ).strip()
    if not auth_ep.startswith("/"):
        auth_ep = "/" + auth_ep

    L: list[str] = []
    a = L.append
    a("# ⚠️ 本文件由 scripts/apply-config.sh 自动生成，请勿手工修改")
    a("#    要改路由/服务，请编辑 services.yml 后重新生成")
    a("{")
    a("\tauto_https off # HTTPS 由 Tailscale Funnel 提供")
    a("\tadmin localhost:2019 # 本地管理 API，供 caddy reload 使用")
    a("\tlog {")
    a("\t\toutput file /var/log/caddy/access.log {")
    a("\t\t\troll_size 10MiB")
    a("\t\t\troll_keep 5")
    a("\t\t}")
    a("\t\tformat json")
    a("\t}")
    a("}")
    a("")
    a(f"{listen} {{")
    a("\t# ── 安全响应头 ──────────────────────────────────")
    a("\theader {")
    a('\t\tStrict-Transport-Security "max-age=31536000"')
    a('\t\tX-Content-Type-Options "nosniff"')
    a('\t\tX-Frame-Options "DENY"')
    a('\t\tReferrer-Policy "same-origin"')
    a("\t\t-Server")
    a("\t}")
    a("")
    a("\t# ── Authelia 登录门户（公开，不鉴权）──────────────")
    # /authelia（不带尾斜杠）不匹配下面的 handle /authelia/*，会掉进兜底路由被要求登录
    # → 登录后又跳回 /authelia → 死循环。这里补一条 308 跳到带斜杠的规范地址。
    a("\tredir /authelia /authelia/ 308")
    # ⚠️ 这里必须用 handle（保留前缀），不能用 handle_path（会剥掉前缀）！
    #    authelia/base.yml 里把 server.address 配成了 tcp://0.0.0.0:9091/authelia，
    #    Authelia 只有看到 URI 以 /authelia 开头时才会把登录页的 <base href> 和
    #    前端路由 basename 设成 /authelia/。一旦被 Caddy 剥掉前缀，<base> 就会变成
    #    https://<域名>/ ，页面里的 ./static/... 会被解析到 /static/... →
    #    资源全挂，浏览器报 MIME 错误，页面上只剩
    #    "There was an issue retrieving the current user state"。
    #    （另：认证端点仍按文档要求走不带前缀的 /api/authz/*，Authelia 两个路径都监听。）
    a("\thandle /authelia/* {")
    a(f"\t\treverse_proxy 127.0.0.1:{AUTHELIA_PORT} {{")
    # ⚠️ 这里也必须强制 https！Tailscale Funnel 终止 TLS 后用 http 转发给 Caddy，
    #    不强制的话 Authelia 会以为自己在 http 上，生成 http:// 的 <base>，
    #    被自己的 CSP "base-uri 'self'" 拦掉。
    a("\t\t\theader_up X-Forwarded-Proto https")
    a("\t\t}")
    a("\t}")
    a("")

    for s in services:
        p = s["path"]
        a(f"\t# ── {s['name']}  ({p}) ──────────────────────")
        a(f"\tredir {p} {p}/ 308")
        a(f"\thandle {p}* {{")
        # ⚠️ 这里必须用 route { } 把内部指令包起来，强制它们【按书写顺序】执行。
        #    不包的话 Caddy 按内置指令顺序排序，uri 排在 forward_auth 之前，
        #    于是 uri strip_prefix 先跑，forward_auth 交给 Authelia 的就是已经
        #    剥掉前缀的路径 —— Authelia 据此生成 ?rd=…，用户登录后会被送到
        #    https://<域名>/ 而不是原本想去的 /<服务>/…（实测就是这样丢的）。
        #    包上 route 之后 forward_auth 先看到完整路径，?rd= 才正确。
        a("\t\troute {")
        if s["auth"] == "required":
            # ⚠️ 两个关键点（都踩过坑）：
            #  1. header_up X-Forwarded-Proto https —— Tailscale Funnel 终止 TLS 后用
            #     http 转发给 Caddy；不强制上报 https 的话 Authelia 会以
            #     "insecure scheme http" 返回 400。
            #  2. 这里【不要】写 request_header -Remote-User 之类。Caddy 的
            #     request_header 在本路由中晚于 forward_auth 执行，会把刚注入的
            #     真实身份又剥掉。copy_headers 本身就会用 Authelia 返回的值
            #     覆盖客户端伪造的同名头（已验证）。
            #  3. uri 后面【不要】再拼 ?rd=<门户地址>。那是老文档的写法，现代
            #     Authelia 会忽略它、自己按 X-Forwarded-Uri 算目标地址；写了反而
            #     容易误导。认证端点也不带 /authelia 前缀（官方要求）。
            a(f"\t\t\tforward_auth 127.0.0.1:{AUTHELIA_PORT} {{")
            a("\t\t\t\theader_up X-Forwarded-Proto https")
            a(f"\t\t\t\turi {auth_ep}")
            a("\t\t\t\tcopy_headers Remote-User Remote-Groups Remote-Name Remote-Email")
            a("\t\t\t}")
        else:
            # 免登录路由没有 forward_auth 来覆盖，所以必须显式剥离客户端伪造的内部头
            for h in ("Remote-User", "Remote-Groups", "Remote-Name", "Remote-Email"):
                a(f"\t\t\trequest_header -{h}")
        if s["strip_prefix"]:
            a(f"\t\t\turi strip_prefix {p}")
        a(f"\t\t\treverse_proxy {s['upstream']} {{")
        # 让后端知道原始请求是 https（Funnel 终止 TLS，Caddy 这段是明文 http）
        a("\t\t\t\theader_up X-Forwarded-Proto https")
        if s["strip_prefix"]:
            a(f"\t\t\t\theader_up X-Forwarded-Prefix {p}")
        a("\t\t\t}")
        a("\t\t}")
        a("\t}")
        a("")

    a("\t# ── 黄页（兜底路由，静态文件，不存在伪造身份的问题）──────")
    a("\thandle {")
    a(f"\t\tforward_auth 127.0.0.1:{AUTHELIA_PORT} {{")
    a("\t\t\theader_up X-Forwarded-Proto https")
    a(f"\t\t\turi {auth_ep}")
    a("\t\t}")
    a("\t\troot * /srv/portal")
    a("\t\tfile_server")
    a("\t}")
    a("}")
    a("")
    return "\n".join(L)


# ──────────────────────────────────────────────────────────────
#  Authelia configuration.yml
# ──────────────────────────────────────────────────────────────
def gen_authelia(base: dict, cfg: dict, domain: str, services: list[dict]) -> dict:
    out = json.loads(json.dumps(base))  # 深拷贝（base 来自 YAML，都是基本类型）

    # ── 会话 cookie（需要域名）──
    # authelia_url 末尾的斜杠【要保留】：Authelia 用它拼重定向地址
    # （Location: https://<域名>/authelia/?rd=…）。不带斜杠的话浏览器会先落到
    # /authelia 再被 Caddy 308 跳到 /authelia/，多一跳，而且中间那一步容易
    # 被误判成"登录页打不开"。
    session = out.setdefault("session", {})
    session["cookies"] = [
        {
            "domain": domain,
            "authelia_url": f"https://{domain}/authelia/",
            "default_redirection_url": f"https://{domain}/",
        }
    ]

    # ── 身份验证重置密码用的 JWT 密钥（从文件读）──
    out["identity_validation"] = {
        "reset_password": {
            "jwt_lifespan": "5 minutes",
            "jwt_algorithm": "HS256",
        }
    }

    # ── 访问控制 ──
    #   本项目不启用 2FA：
    #     · 管理页只监听局域网，用独立的管理密码保护，不走 Authelia
    #     · 普通用户只需要账号密码（one_factor）即可访问所有已暴露服务
    #   规则按顺序匹配，第一条命中即生效。
    rules: list[dict] = [
        # 登录门户自身放行（它是公开资源，否则会无限重定向）
        {
            "domain": domain,
            "resources": [r"^/authelia(/.*)?$"],
            "policy": "bypass",
        },
        # 其余一切都要求登录（单因素）
        {"domain": domain, "policy": "one_factor"},
    ]

    access = out.setdefault("access_control", {})
    access["default_policy"] = "deny"
    access["rules"] = rules

    # ── 明确关闭 TOTP ──
    out.setdefault("totp", {})["disable"] = True

    return out


# ──────────────────────────────────────────────────────────────
#  黄页
# ──────────────────────────────────────────────────────────────
PORTAL_TEMPLATE = """<!doctype html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<meta name="robots" content="noindex,nofollow">
<title>{title}</title>
<style>
  :root{{--bg:#f5f7f6;--card:#fff;--txt:#1a1d1b;--muted:#6b7280;--accent:#12a150;--bd:#e5e9e7}}
  @media (prefers-color-scheme:dark){{
    :root{{--bg:#121714;--card:#1b211d;--txt:#e8ede9;--muted:#93a09a;--accent:#34d97b;--bd:#2a332d}}
  }}
  *{{box-sizing:border-box}}
  body{{margin:0;background:var(--bg);color:var(--txt);
    font:15px/1.55 -apple-system,BlinkMacSystemFont,"PingFang SC","Microsoft YaHei",sans-serif;
    -webkit-font-smoothing:antialiased}}
  .wrap{{max-width:640px;margin:0 auto;padding:56px 20px 72px}}
  h1{{margin:0 0 6px;font-size:26px;letter-spacing:-.5px}}
  .sub{{margin:0 0 32px;color:var(--muted);font-size:14px}}
  .list{{display:grid;gap:12px}}
  a.card{{display:flex;align-items:center;gap:14px;padding:16px 18px;background:var(--card);
    border:1px solid var(--bd);border-radius:14px;text-decoration:none;color:inherit;
    transition:transform .12s ease,border-color .12s ease}}
  a.card:hover{{transform:translateY(-2px);border-color:var(--accent)}}
  .ic{{font-size:26px;line-height:1;width:34px;text-align:center;flex:none}}
  .nm{{font-weight:600;font-size:16px}}
  .ds{{color:var(--muted);font-size:13px;margin-top:2px}}
  .arrow{{margin-left:auto;color:var(--muted);flex:none}}
  footer{{margin-top:36px;color:var(--muted);font-size:12px;text-align:center}}
</style>
</head>
<body>
<div class="wrap">
  <h1>{title}</h1>
  <p class="sub">{subtitle}</p>
  <div class="list">
{cards}
  </div>
  <footer>已登录 · 访问受 Authelia 保护</footer>
</div>
</body>
</html>
"""

CARD_TEMPLATE = """    <a class="card" href="{path}/">
      <span class="ic">{icon}</span>
      <span>
        <div class="nm">{name}</div>
        <div class="ds">{desc}</div>
      </span>
      <span class="arrow">›</span>
    </a>"""


def gen_portal(cfg: dict, services: list[dict]) -> str:
    site = cfg.get("site") or {}
    title = html.escape(str(site.get("title") or "我的 NAS"))
    subtitle = html.escape(str(site.get("subtitle") or "私有服务入口"))
    cards = "\n".join(
        CARD_TEMPLATE.format(
            path=html.escape(s["path"]),
            icon=html.escape(s["icon"]),
            name=html.escape(s["name"]),
            desc=html.escape(s["desc"]),
        )
        for s in services
    )
    return PORTAL_TEMPLATE.format(title=title, subtitle=subtitle, cards=cards)


def sync_env(root: Path, cfg: dict) -> tuple[str, bool]:
    """把 services.yml 里的 admin 配置同步到 .env（docker-compose 从这里读）。

    这样 services.yml 就是唯一的配置入口，不用再去改 .env。
    返回 (摘要, 是否有变化)
    """
    admin = cfg.get("admin") or {}
    want: dict[str, str] = {}
    if admin.get("port") not in (None, ""):
        want["ADMIN_PORT"] = str(admin["port"]).strip()
    if admin.get("bind") not in (None, ""):
        want["ADMIN_BIND"] = str(admin["bind"]).strip()

    env_path = root / ".env"
    old_lines = env_path.read_text(encoding="utf-8").splitlines() if env_path.exists() else []
    old_values = {
        ln.split("=", 1)[0]: ln.split("=", 1)[1]
        for ln in old_lines
        if "=" in ln and not ln.lstrip().startswith("#")
    }

    changed = any(old_values.get(k) != v for k, v in want.items())
    if not changed and env_path.exists():
        return ("\n".join(f"  {k}={v}" for k, v in want.items()), False)

    # 逐行更新；没出现的追加到末尾
    remaining = dict(want)
    out: list[str] = []
    for ln in old_lines:
        key = ln.split("=", 1)[0] if "=" in ln and not ln.lstrip().startswith("#") else None
        if key in remaining:
            out.append(f"{key}={remaining.pop(key)}")
        else:
            out.append(ln)
    if remaining:
        if out and out[-1].strip():
            out.append("")
        out.append("# 由 services.yml 的 admin 段自动同步（改 services.yml 即可）")
        for k, v in remaining.items():
            out.append(f"{k}={v}")

    env_path.write_text("\n".join(out) + "\n", encoding="utf-8")
    return ("\n".join(f"  {k}={v}" for k, v in want.items()), True)


# ──────────────────────────────────────────────────────────────
def main() -> None:
    ap = argparse.ArgumentParser(description="nas-gateway 配置生成器")
    ap.add_argument("--root", default=".", help="项目根目录（含 services.yml）")
    ap.add_argument(
        "--sync-env",
        action="store_true",
        help="同时把 services.yml 的 admin.port / admin.bind 同步到 .env",
    )
    args = ap.parse_args()
    root = Path(args.root).resolve()

    cfg = load_yaml(root / "services.yml")
    base = load_yaml(root / "authelia" / "base.yml")
    domain, services = validate(cfg)

    out_caddy = root / "caddy" / "Caddyfile"
    out_auth = root / "authelia" / "configuration.yml"
    out_portal = root / "portal" / "index.html"
    for p in (out_caddy, out_auth, out_portal):
        p.parent.mkdir(parents=True, exist_ok=True)

    out_caddy.write_text(gen_caddyfile(cfg, domain, services), encoding="utf-8")
    out_auth.write_text(
        "# ⚠️ 由 scripts/apply-config.sh 自动生成，请勿手工修改\n"
        "#    要改策略请编辑 authelia/base.yml，要改域名请编辑 services.yml\n"
        + yaml.safe_dump(
            gen_authelia(base, cfg, domain, services),
            allow_unicode=True,
            sort_keys=False,
            default_flow_style=False,
        ),
        encoding="utf-8",
    )
    out_portal.write_text(gen_portal(cfg, services), encoding="utf-8")

    print(f"✅ 域名：{domain}")
    print(f"✅ 已启用服务 {len(services)} 个：")
    for s in services:
        tag = "需登录" if s["auth"] == "required" else "免登录"
        print(f"   · {s['path']:<22} → {s['upstream']:<28} [{tag}]")
    print()
    print(f"   生成 {out_caddy.relative_to(root)}")
    print(f"   生成 {out_auth.relative_to(root)}")
    print(f"   生成 {out_portal.relative_to(root)}")

    if args.sync_env:
        summary, changed = sync_env(root, cfg)
        print()
        print("   同步到 .env：" if changed else "   .env 已是最新：")
        print(summary)
        if changed:
            print("   ⚠️ admin 端口/绑定有变化 → 需要重建 admin 容器")
            print("      docker compose up -d admin")


if __name__ == "__main__":
    main()
