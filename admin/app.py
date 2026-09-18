#!/usr/bin/env python3
# ══════════════════════════════════════════════════════════════
#  nas-gateway —— 用户管理页
#
#  设计原则：
#   · 只监听局域网（NAS 没有公网 IP，Funnel 也不暴露这个端口）
#   · 用标准库 http.server，不引入 Web 框架
#   · 管理员密码用 argon2id 哈希存储
#   · 直接读写 Authelia 的 users_database.yml（Authelia watch=true 会自动重载）
#
#  环境变量：
#    GATEWAY_ROOT    项目根目录（默认 /gateway）
#    ADMIN_DATA_DIR  本服务自己的数据目录（默认 /app/data）
#    ADMIN_LOG       操作日志（默认 /logs/admin/activity.log）
#    ADMIN_PORT      监听端口（默认 9092）
# ══════════════════════════════════════════════════════════════
from __future__ import annotations

import hashlib
import hmac
import html
import json
import os
import secrets
import sys
import threading
import time
from datetime import datetime
from http.cookies import SimpleCookie
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, quote, urlparse

import yaml
from argon2 import PasswordHasher
from argon2.low_level import Type

# ── Authelia 的 argon2id 参数（必须与 authelia/base.yml 一致）──
PH = PasswordHasher(
    time_cost=3,
    memory_cost=65536,
    parallelism=4,
    hash_len=32,
    salt_len=16,
    type=Type.ID,
)

GROUPS = ("admins", "users")
SESSION_TTL = 7 * 24 * 3600          # 管理页登录 7 天
MAX_LOGIN_FAILS = 5
LOGIN_LOCK_SECONDS = 900             # 失败 5 次锁 15 分钟

ROOT = Path(os.environ.get("GATEWAY_ROOT", "/gateway")).resolve()
DATA_DIR = Path(os.environ.get("ADMIN_DATA_DIR", "/app/data")).resolve()
LOG_FILE = Path(os.environ.get("ADMIN_LOG", "/logs/admin/activity.log"))
PORT = int(os.environ.get("ADMIN_PORT", "9092"))

USERS_FILE = ROOT / "authelia" / "users_database.yml"
SETTINGS_FILE = DATA_DIR / "admin.json"

_lock = threading.Lock()
_login_fails: dict[str, list[float]] = {}


# ──────────────────────────────────────────────────────────────
#  存储
# ──────────────────────────────────────────────────────────────
def _load_settings() -> dict:
    if not SETTINGS_FILE.exists():
        return {}
    try:
        return json.loads(SETTINGS_FILE.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _save_settings(s: dict) -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    tmp = SETTINGS_FILE.with_suffix(".tmp")
    tmp.write_text(json.dumps(s, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(tmp, SETTINGS_FILE)
    try:
        os.chmod(SETTINGS_FILE, 0o600)
    except OSError:
        pass


def _ensure_secret() -> bytes:
    s = _load_settings()
    if not s.get("session_secret"):
        s["session_secret"] = secrets.token_hex(32)
        _save_settings(s)
    return bytes.fromhex(s["session_secret"])


def _load_users() -> dict:
    if not USERS_FILE.exists():
        return {}
    try:
        data = yaml.safe_load(USERS_FILE.read_text(encoding="utf-8")) or {}
    except yaml.YAMLError as e:
        raise RuntimeError(f"users_database.yml 解析失败：{e}") from e
    users = data.get("users") or {}
    return users if isinstance(users, dict) else {}


def _save_users(users: dict) -> None:
    USERS_FILE.parent.mkdir(parents=True, exist_ok=True)
    body = yaml.safe_dump(
        {"users": users}, allow_unicode=True, sort_keys=True, default_flow_style=False
    )
    tmp = USERS_FILE.with_suffix(".tmp")
    tmp.write_text(body, encoding="utf-8")
    os.replace(tmp, USERS_FILE)
    try:
        os.chmod(USERS_FILE, 0o600)
    except OSError:
        pass


def log_activity(actor: str, action: str, detail: str = "", ip: str = "") -> None:
    try:
        LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
        ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        with LOG_FILE.open("a", encoding="utf-8") as f:
            f.write(f"{ts}\t{actor}\t{action}\t{detail}\t{ip}\n")
    except OSError:
        pass


def tail_activity(n: int = 30) -> list[list[str]]:
    if not LOG_FILE.exists():
        return []
    try:
        lines = LOG_FILE.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return []
    return [ln.split("\t") for ln in lines[-n:]][::-1]


# ──────────────────────────────────────────────────────────────
#  会话
# ──────────────────────────────────────────────────────────────
def make_session(secret: bytes, user: str) -> str:
    exp = int(time.time()) + SESSION_TTL
    payload = f"{user}|{exp}"
    sig = hmac.new(secret, payload.encode(), hashlib.sha256).hexdigest()
    return f"{payload}|{sig}"


def check_session(secret: bytes, token: str) -> str | None:
    try:
        user, exp_s, sig = token.split("|")
        exp = int(exp_s)
    except (ValueError, AttributeError):
        return None
    expected = hmac.new(secret, f"{user}|{exp}".encode(), hashlib.sha256).hexdigest()
    if not hmac.compare_digest(sig, expected):
        return None
    return user if exp > time.time() else None


# ──────────────────────────────────────────────────────────────
#  页面
# ──────────────────────────────────────────────────────────────
STYLE = """
:root{--bg:#f5f7f6;--card:#fff;--txt:#1a1d1b;--muted:#6b7280;--accent:#12a150;
--danger:#dc2626;--bd:#e5e9e7;--input:#fff}
@media(prefers-color-scheme:dark){:root{--bg:#121714;--card:#1b211d;--txt:#e8ede9;
--muted:#93a09a;--accent:#34d97b;--danger:#f87171;--bd:#2a332d;--input:#141915}}
*{box-sizing:border-box}
body{margin:0;background:var(--bg);color:var(--txt);
font:15px/1.55 -apple-system,BlinkMacSystemFont,"PingFang SC","Microsoft YaHei",sans-serif;
-webkit-font-smoothing:antialiased}
.wrap{max-width:720px;margin:0 auto;padding:32px 18px 64px}
h1{margin:0 0 4px;font-size:22px;letter-spacing:-.4px}
h2{margin:32px 0 12px;font-size:15px;color:var(--muted);font-weight:600}
.sub{color:var(--muted);font-size:13px;margin:0 0 24px}
.card{background:var(--card);border:1px solid var(--bd);border-radius:14px;padding:18px}
table{width:100%;border-collapse:collapse;font-size:14px}
th,td{text-align:left;padding:9px 8px;border-bottom:1px solid var(--bd)}
th{color:var(--muted);font-weight:600;font-size:12.5px}
tr:last-child td{border-bottom:0}
label{display:block;font-size:12.5px;color:var(--muted);margin:0 0 5px}
input,select{width:100%;padding:10px 12px;border:1px solid var(--bd);border-radius:9px;
background:var(--input);color:var(--txt);font-size:15px;font-family:inherit}
.row{display:flex;gap:10px;flex-wrap:wrap}
.row>div{flex:1 1 150px}
button{padding:10px 16px;border:0;border-radius:9px;background:var(--accent);color:#fff;
font-size:14.5px;font-weight:600;font-family:inherit;cursor:pointer}
button.ghost{background:transparent;color:var(--muted);border:1px solid var(--bd)}
button.danger{background:transparent;color:var(--danger);border:1px solid var(--bd);
padding:6px 10px;font-size:12.5px;font-weight:500}
button.mini{background:transparent;color:var(--muted);border:1px solid var(--bd);
padding:6px 10px;font-size:12.5px;font-weight:500}
.msg{padding:11px 14px;border-radius:10px;margin:0 0 18px;font-size:14px}
.ok{background:rgba(18,161,80,.12);color:var(--accent)}
.err{background:rgba(220,38,38,.1);color:var(--danger)}
.tag{display:inline-block;padding:2px 8px;border-radius:6px;font-size:11.5px;
background:var(--bd);color:var(--muted)}
.tag.a{background:rgba(18,161,80,.15);color:var(--accent)}
.log{font:12px/1.7 ui-monospace,SFMono-Regular,Menlo,monospace;color:var(--muted)}
.top{display:flex;align-items:baseline;gap:10px;justify-content:space-between}
.narrow{max-width:380px;margin:12vh auto 0}
"""

PAGE = """<!doctype html><html lang="zh-CN"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<meta name="robots" content="noindex,nofollow"><title>{title}</title>
<style>{style}</style></head><body>{body}</body></html>"""


def e(v) -> str:
    return html.escape(str(v if v is not None else ""))


def page(title: str, body: str) -> bytes:
    return PAGE.format(title=e(title), style=STYLE, body=body).encode("utf-8")


def render_login(msg: str = "", err: str = "") -> bytes:
    body = f"""<div class="wrap narrow">
      <h1>🔐 NAS 网关管理</h1>
      <p class="sub">请使用管理员密码登录</p>
      {f'<div class="msg ok">{e(msg)}</div>' if msg else ''}
      {f'<div class="msg err">{e(err)}</div>' if err else ''}
      <form class="card" method="post" action="/login">
        <label>管理员密码</label>
        <input type="password" name="password" autofocus required>
        <div style="margin-top:14px"><button type="submit">登录</button></div>
      </form>
      <p class="sub" style="margin-top:20px">本页面只监听局域网，
      公网无法访问。用户数据保存在 authelia/users_database.yml。</p>
    </div>"""
    return page("登录 · NAS 网关", body)


def render_setup(err: str = "") -> bytes:
    body = f"""<div class="wrap narrow">
      <h1>🔐 首次使用</h1>
      <p class="sub">设置管理页密码（用于管理用户，与家库登录密码无关）</p>
      {f'<div class="msg err">{e(err)}</div>' if err else ''}
      <form class="card" method="post" action="/setup">
        <label>管理页密码（至少 8 位）</label>
        <input type="password" name="p1" autofocus required>
        <div style="height:12px"></div>
        <label>再输一次</label>
        <input type="password" name="p2" required>
        <div style="margin-top:14px"><button type="submit">设置</button></div>
      </form>
      <p class="sub" style="margin-top:20px">密码只保存在本机
      （admin/data/admin.json），不会上传到任何地方。</p>
    </div>"""
    return page("首次设置 · NAS 网关", body)


def render_dashboard(user: str, msg: str = "", err: str = "") -> bytes:
    users = _load_users()
    rows = []
    for name, u in sorted(users.items()):
        groups = u.get("groups") or []
        gtags = " ".join(
            f'<span class="tag{" a" if g == "admins" else ""}">{e(g)}</span>' for g in groups
        )
        dn = e(u.get("displayname") or "")
        rows.append(
            f"""<tr>
              <td><strong>{e(name)}</strong>{f'<div style="color:var(--muted);font-size:12px">{dn}</div>' if dn else ''}</td>
              <td>{gtags}</td>
              <td style="text-align:right;white-space:nowrap">
                <form method="post" action="/users/password" style="display:inline">
                  <input type="hidden" name="name" value="{e(name)}">
                  <button class="mini" type="submit">改密码</button>
                </form>
                <form method="post" action="/users/delete" style="display:inline"
                      onsubmit="return confirm('确定删除用户 {e(name)} ？')">
                  <input type="hidden" name="name" value="{e(name)}">
                  <button class="danger" type="submit">删除</button>
                </form>
              </td>
            </tr>"""
        )
    table = (
        "<table><thead><tr><th>用户名</th><th>组</th><th></th></tr></thead><tbody>"
        + "".join(rows)
        + "</tbody></table>"
        if rows
        else '<p class="sub">还没有用户，用下面的表单创建一个。</p>'
    )

    logs = tail_activity(25)
    log_html = (
        "<div class='log'>"
        + "<br>".join(
            f"{e(x[0])} · {e(x[1])} · {e(x[2])} {e(x[3])}" for x in logs if len(x) >= 4
        )
        + "</div>"
        if logs
        else '<p class="sub">暂无记录</p>'
    )

    body = f"""<div class="wrap">
      <div class="top">
        <div><h1>🔐 NAS 网关管理</h1>
        <p class="sub">已登录：{e(user)}</p></div>
        <form method="post" action="/logout"><button class="ghost" type="submit">退出</button></form>
      </div>
      {f'<div class="msg ok">{e(msg)}</div>' if msg else ''}
      {f'<div class="msg err">{e(err)}</div>' if err else ''}

      <h2>用户（{len(users)}）</h2>
      <div class="card">{table}</div>

      <h2>新建用户</h2>
      <form class="card" method="post" action="/users/create">
        <div class="row">
          <div><label>用户名</label><input name="name" required placeholder="例如 小明"></div>
          <div><label>密码（至少 8 位）</label><input name="password" type="password" required></div>
          <div><label>组</label>
            <select name="group">
              <option value="users" selected>users —— 普通用户（访问服务）</option>
              <option value="admins">admins —— 管理员标记</option>
            </select>
          </div>
        </div>
        <div style="margin-top:14px"><button type="submit">创建用户</button></div>
      </form>
      <p class="sub" style="margin-top:10px">
        创建后自动写入 Authelia 用户库并生效。用户到
        <strong>https://你的域名</strong> 用这个账号密码登录即可。</p>

      <h2>最近操作</h2>
      <div class="card">{log_html}</div>

      <h2>提示</h2>
      <div class="card sub" style="margin:0">
        · 用户忘记密码 → 在这里点「改密码」<br>
        · 本网关不使用 2FA：用户只要账号密码即可登录<br>
        · 组目前只是标记（admins / users），服务内权限由各服务自己判断<br>
        · 本页面只监听局域网，公网访问不到
      </div>
    </div>"""
    return page("管理 · NAS 网关", body)


# ──────────────────────────────────────────────────────────────
#  HTTP
# ──────────────────────────────────────────────────────────────
class Handler(BaseHTTPRequestHandler):
    server_version = "nas-gateway-admin"
    protocol_version = "HTTP/1.1"

    # ── 工具 ──
    def _send(self, code: int, body: bytes, ctype="text/html; charset=utf-8", extra=None):
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "same-origin")
        self.send_header("Cache-Control", "no-store")
        for k, v in (extra or []):
            self.send_header(k, v)
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)

    def _redirect(self, to: str):
        self.send_response(303)
        self.send_header("Location", to)
        self.send_header("Content-Length", "0")
        self.end_headers()

    def _cookie_user(self) -> str | None:
        raw = self.headers.get("Cookie")
        if not raw:
            return None
        c = SimpleCookie()
        try:
            c.load(raw)
        except Exception:
            return None
        morsel = c.get("ng_admin")
        if not morsel:
            return None
        return check_session(_ensure_secret(), morsel.value)

    def _form(self) -> dict:
        n = int(self.headers.get("Content-Length") or 0)
        if n <= 0 or n > 65536:
            return {}
        raw = self.rfile.read(n).decode("utf-8", errors="replace")
        return {k: v[0] for k, v in parse_qs(raw, keep_blank_values=True).items()}

    @property
    def client_ip(self) -> str:
        return self.client_address[0] if self.client_address else ""

    def _fail_count(self, ip: str) -> int:
        now = time.time()
        lst = [t for t in _login_fails.get(ip, []) if now - t < LOGIN_LOCK_SECONDS]
        _login_fails[ip] = lst
        return len(lst)

    # ── 路由 ──
    def do_GET(self):  # noqa: N802
        path = urlparse(self.path).path
        settings = _load_settings()

        if path == "/healthz":
            return self._send(200, b'{"ok":true}', "application/json")

        if not settings.get("password_hash"):
            return self._send(200, render_setup())

        user = self._cookie_user()
        if not user:
            return self._send(200, render_login())
        if path in ("/", "/index.html"):
            return self._send(200, render_dashboard(user))
        return self._redirect("/")

    def do_HEAD(self):  # noqa: N802
        return self.do_GET()

    def do_POST(self):  # noqa: N802
        path = urlparse(self.path).path
        settings = _load_settings()
        ip = self.client_ip

        # ── 首次设置密码 ──
        if path == "/setup":
            if settings.get("password_hash"):
                return self._redirect("/")
            f = self._form()
            p1, p2 = f.get("p1", ""), f.get("p2", "")
            if len(p1) < 8:
                return self._send(200, render_setup("密码至少 8 位"))
            if p1 != p2:
                return self._send(200, render_setup("两次输入不一致"))
            settings["password_hash"] = PH.hash(p1)
            settings.setdefault("session_secret", secrets.token_hex(32))
            _save_settings(settings)
            log_activity("system", "setup", "设置管理页密码", ip)
            return self._redirect("/")

        if not settings.get("password_hash"):
            return self._redirect("/")

        # ── 登录 ──
        if path == "/login":
            if self._fail_count(ip) >= MAX_LOGIN_FAILS:
                log_activity("unknown", "login_locked", "", ip)
                return self._send(
                    200,
                    render_login(err=f"失败次数过多，请 {LOGIN_LOCK_SECONDS // 60} 分钟后再试"),
                )
            f = self._form()
            try:
                ok = PH.verify(settings["password_hash"], f.get("password", ""))
            except Exception:
                ok = False
            if not ok:
                _login_fails.setdefault(ip, []).append(time.time())
                log_activity("unknown", "login_fail", "", ip)
                left = MAX_LOGIN_FAILS - self._fail_count(ip)
                return self._send(200, render_login(err=f"密码错误（还可尝试 {max(left,0)} 次）"))
            _login_fails.pop(ip, None)
            token = make_session(_ensure_secret(), "admin")
            log_activity("admin", "login", "", ip)
            return self._send(
                200,
                render_dashboard("admin", "登录成功"),
                extra=[("Set-Cookie", f"ng_admin={token}; Path=/; HttpOnly; SameSite=Lax; Max-Age={SESSION_TTL}")],
            )

        # ── 以下都需要登录 ──
        user = self._cookie_user()
        if not user:
            return self._redirect("/")

        if path == "/logout":
            log_activity(user, "logout", "", ip)
            return self._send(
                200, render_login(msg="已退出"), extra=[("Set-Cookie", "ng_admin=; Path=/; Max-Age=0")]
            )

        if path == "/users/create":
            f = self._form()
            name = (f.get("name") or "").strip()
            pwd = f.get("password") or ""
            group = f.get("group") or "users"
            if not name or len(name) > 64 or any(c in name for c in ":\n\r\t"):
                return self._send(200, render_dashboard(user, err="用户名不合法"))
            if group not in GROUPS:
                return self._send(200, render_dashboard(user, err="组不合法"))
            if len(pwd) < 8:
                return self._send(200, render_dashboard(user, err="密码至少 8 位"))
            with _lock:
                users = _load_users()
                if name in users:
                    return self._send(200, render_dashboard(user, err=f"用户 {name} 已存在"))
                users[name] = {
                    "displayname": name,
                    "password": PH.hash(pwd),
                    "email": "",
                    "groups": [group],
                }
                _save_users(users)
            log_activity(user, "create_user", f"{name} ({group})", ip)
            return self._send(200, render_dashboard(user, msg=f"已创建用户 {name}"))

        if path == "/users/delete":
            f = self._form()
            name = (f.get("name") or "").strip()
            with _lock:
                users = _load_users()
                if name not in users:
                    return self._send(200, render_dashboard(user, err=f"用户 {name} 不存在"))
                admin_n = sum(1 for u in users.values() if "admins" in (u.get("groups") or []))
                if "admins" in (users[name].get("groups") or []) and admin_n <= 1:
                    return self._send(200, render_dashboard(user, err="不能删除最后一个 admins 用户"))
                del users[name]
                _save_users(users)
            log_activity(user, "delete_user", name, ip)
            return self._send(200, render_dashboard(user, msg=f"已删除用户 {name}"))

        if path == "/users/password":
            f = self._form()
            name = (f.get("name") or "").strip()
            with _lock:
                users = _load_users()
                if name not in users:
                    return self._send(200, render_dashboard(user, err=f"用户 {name} 不存在"))
            return self._send(200, render_change_password(user, name))

        return self._redirect("/")

    def do_PUT(self):  # noqa: N802
        self._redirect("/")

    def log_message(self, fmt, *args):  # 静音默认日志
        return


def render_change_password(actor: str, target: str, err: str = "") -> bytes:
    body = f"""<div class="wrap narrow">
      <h1>修改密码</h1>
      <p class="sub">用户：<strong>{e(target)}</strong></p>
      {f'<div class="msg err">{e(err)}</div>' if err else ''}
      <form class="card" method="post" action="/users/password/save">
        <input type="hidden" name="name" value="{e(target)}">
        <label>新密码（至少 8 位）</label>
        <input type="password" name="password" autofocus required>
        <div style="margin-top:14px">
          <button type="submit">保存</button>
          <a href="/" style="margin-left:10px"><button class="ghost" type="button"
             onclick="location.href='/'">取消</button></a>
        </div>
      </form>
    </div>"""
    return page("改密码 · NAS 网关", body)


class HandlerWithSave(Handler):
    def do_POST(self):  # noqa: N802
        if urlparse(self.path).path == "/users/password/save":
            actor = self._cookie_user()
            if not actor:
                return self._redirect("/")
            f = self._form()
            name = (f.get("name") or "").strip()
            pwd = f.get("password") or ""
            if len(pwd) < 8:
                return self._send(200, render_change_password(actor, name, "密码至少 8 位"))
            with _lock:
                users = _load_users()
                if name not in users:
                    return self._send(200, render_dashboard(actor, err=f"用户 {name} 不存在"))
                users[name]["password"] = PH.hash(pwd)
                _save_users(users)
            log_activity(actor, "change_password", name, self.client_ip)
            return self._send(200, render_dashboard(actor, msg=f"已更新 {name} 的密码"))
        return super().do_POST()


def main() -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    if not USERS_FILE.exists():
        USERS_FILE.parent.mkdir(parents=True, exist_ok=True)
        USERS_FILE.write_text("users: {}\n", encoding="utf-8")
    print(f"[admin] 项目根目录 : {ROOT}")
    print(f"[admin] 用户库     : {USERS_FILE}")
    print(f"[admin] 监听       : 0.0.0.0:{PORT}")
    srv = ThreadingHTTPServer(("0.0.0.0", PORT), HandlerWithSave)
    srv.daemon_threads = True
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
