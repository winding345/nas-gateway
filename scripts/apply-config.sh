#!/usr/bin/env bash
# ══════════════════════════════════════════════════════════════
#  应用配置：读 services.yml + authelia/base.yml
#             → 生成 caddy/Caddyfile / authelia/configuration.yml / portal/index.html
#             → 把 admin.port / admin.bind 同步进 .env
#             → 让 Caddy / Authelia / admin 重新加载
#
#  services.yml 是唯一的配置入口，改完跑这个即可。
# ══════════════════════════════════════════════════════════════
set -euo pipefail
cd "$(dirname "$0")/.."

if [ ! -f services.yml ]; then
  echo "❌ 找不到 services.yml"
  echo "   先执行：cp services.yml.example services.yml  然后编辑它"
  exit 1
fi

if grep -q "REPLACE-ME" services.yml; then
  echo "❌ services.yml 里的 domain 还是占位值 REPLACE-ME.ts.net"
  echo "   查询你的域名：tailscale status --json | grep -i dnsname"
  exit 1
fi

# 记录 .env 里当前的 admin 配置，用于判断是否需要重建容器
OLD_ADMIN_PORT="$(grep -E '^ADMIN_PORT=' .env 2>/dev/null | cut -d= -f2 | tr -d ' ' || true)"
OLD_ADMIN_BIND="$(grep -E '^ADMIN_BIND=' .env 2>/dev/null | cut -d= -f2 | tr -d ' ' || true)"

# ── 确保 admin 镜像存在 ──────────────────────────────────────
if ! docker image inspect nas-gateway-admin:latest >/dev/null 2>&1; then
  echo "▸ 首次运行，构建 admin 镜像…"
  docker compose build admin
fi

# ── 生成配置 + 同步 .env（在容器里跑，因为它有 PyYAML）─────────
echo "▸ 生成配置…"
docker compose run --rm --no-deps -T admin \
  python /app/generate_config.py --root /gateway --sync-env

NEW_ADMIN_PORT="$(grep -E '^ADMIN_PORT=' .env 2>/dev/null | cut -d= -f2 | tr -d ' ' || true)"
NEW_ADMIN_BIND="$(grep -E '^ADMIN_BIND=' .env 2>/dev/null | cut -d= -f2 | tr -d ' ' || true)"

# ── Caddy 重新加载 ──────────────────────────────────────────
if docker compose ps --status running --services 2>/dev/null | grep -qx caddy; then
  echo "▸ 重载 Caddy…"
  if docker compose exec -T caddy caddy reload --config /etc/caddy/Caddyfile; then
    echo "  ✅ Caddy 已重载"
  else
    echo "  ⚠️ Caddy reload 失败，改为重启"
    docker compose restart caddy
  fi
else
  echo "  · Caddy 未运行，跳过重载"
fi

# ── Authelia 重启（不支持热重载配置）─────────────────────────
if docker compose ps --status running --services 2>/dev/null | grep -qx authelia; then
  echo "▸ 重启 Authelia…"
  docker compose restart authelia >/dev/null
  echo "  ✅ Authelia 已重启（会话在 sqlite 里，不会掉线）"
else
  echo "  · Authelia 未运行，跳过"
fi

# ── admin 端口/绑定变了就重建容器 ────────────────────────────
if [ "${OLD_ADMIN_PORT:-}" != "${NEW_ADMIN_PORT:-}" ] || \
   [ "${OLD_ADMIN_BIND:-}" != "${NEW_ADMIN_BIND:-}" ]; then
  echo "▸ admin 端口/绑定有变化（${OLD_ADMIN_PORT:-?} → ${NEW_ADMIN_PORT:-?}），重建 admin 容器…"
  docker compose up -d admin >/dev/null
  echo "  ✅ admin 已重建，现在监听 ${NEW_ADMIN_BIND:-0.0.0.0}:${NEW_ADMIN_PORT:-9092}"
else
  docker compose up -d admin >/dev/null 2>&1 || true
fi

GW_LISTEN="$(grep -E '^[[:space:]]*listen:' services.yml | head -1 | sed 's/.*listen:[[:space:]]*//' | tr -d "\"' ")"
echo
echo "✅ 配置已应用。"
echo "   网关入口 : ${GW_LISTEN:-:8080}"
echo "   管理页   : http://<NAS的局域网IP>:${NEW_ADMIN_PORT:-9092}/"
