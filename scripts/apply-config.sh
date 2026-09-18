#!/usr/bin/env bash
# ══════════════════════════════════════════════════════════════
#  应用配置：读 services.yml + authelia/base.yml
#             → 生成 caddy/Caddyfile / authelia/configuration.yml / portal/index.html
#             → 让 Caddy 和 Authelia 重新加载
#
#  改完 services.yml 后跑这个即可，不用手动改生成的配置文件。
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

# ── 确保 admin 镜像存在 ──────────────────────────────────────
if ! docker image inspect nas-gateway-admin:latest >/dev/null 2>&1; then
  echo "▸ 首次运行，构建 admin 镜像…"
  docker compose build admin
fi

# ── 生成配置（在容器里跑，因为它有 PyYAML）────────────────────
echo "▸ 生成配置…"
docker compose run --rm --no-deps -T admin \
  python /app/generate_config.py --root /gateway

# ── 让 Caddy 重新加载 ────────────────────────────────────────
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

# ── Authelia 重启（它不支持热重载配置）───────────────────────
if docker compose ps --status running --services 2>/dev/null | grep -qx authelia; then
  echo "▸ 重启 Authelia…"
  docker compose restart authelia >/dev/null
  echo "  ✅ Authelia 已重启（会话存储在 sqlite 里，不会掉线）"
else
  echo "  · Authelia 未运行，跳过"
fi

echo
echo "✅ 配置已应用。"
