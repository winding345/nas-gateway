#!/usr/bin/env bash
# ══════════════════════════════════════════════════════════════
#  首次部署向导
#
#    ./scripts/setup.sh
#
#  会做：
#    1. 从模板创建 services.yml / .env / users_database.yml（若不存在）
#    2. 校验 services.yml 里填了域名
#    3. 生成密钥
#    4. 构建 admin 镜像并生成配置
#    5. 启动全部容器
#    6. 打印后续步骤
# ══════════════════════════════════════════════════════════════
set -euo pipefail
cd "$(dirname "$0")/.."

echo "════════════════════════════════════════════"
echo "  nas-gateway 首次部署"
echo "════════════════════════════════════════════"
echo

# ── 1. 模板 ─────────────────────────────────────────────────
if [ ! -f services.yml ]; then
  cp services.yml.example services.yml
  echo "✅ 已创建 services.yml"
  NEED_DOMAIN=1
else
  echo "· services.yml 已存在"
fi

if [ ! -f .env ]; then
  cp .env.example .env
  # 自动填入当前用户的 uid/gid，保证容器生成的文件属主是你自己
  sed -i "s/^PUID=.*/PUID=$(id -u)/; s/^PGID=.*/PGID=$(id -g)/" .env
  echo "✅ 已创建 .env（PUID=$(id -u) PGID=$(id -g)，可覆盖 ADMIN_BIND 等）"
fi

if [ ! -f authelia/users_database.yml ]; then
  cp authelia/users_database.yml.example authelia/users_database.yml
  chmod 600 authelia/users_database.yml
  echo "✅ 已创建 authelia/users_database.yml"
fi

# ── 2. 校验域名 ─────────────────────────────────────────────
if grep -q "REPLACE-ME" services.yml; then
  echo
  echo "⚠️  请先编辑 services.yml，把 domain 改成你的真实域名："
  echo
  echo "      domain: \"$( (tailscale status --json 2>/dev/null | grep -o '"DNSName": *"[^"]*"' | head -1 | sed 's/.*"\([^"]*\)"$/\1/' | sed 's/\.$//') 2>/dev/null || echo '你的设备名.xxx.ts.net')\""
  echo
  echo "   查询命令： tailscale status --json | grep -i dnsname"
  echo
  echo "   改完再跑一次： ./scripts/setup.sh"
  exit 1
fi

# ── 3. 密钥 ─────────────────────────────────────────────────
echo
echo "▸ 生成密钥"
./scripts/generate-secrets.sh

# ── 4. 构建 + 生成配置 ──────────────────────────────────────
echo
echo "▸ 构建 admin 镜像（第一次会慢一点）"
docker compose build admin

echo
echo "▸ 生成配置"
docker compose run --rm --no-deps -T admin \
  python /app/generate_config.py --root /gateway --sync-env

# ── 5. 启动 ─────────────────────────────────────────────────
echo
echo "▸ 启动容器"
docker compose up -d
sleep 4
docker compose ps

# ── 6. 后续步骤 ─────────────────────────────────────────────
NAS_IP="$(hostname -I 2>/dev/null | awk '{print $1}')"
ADMIN_PORT="$(grep -E '^\s*ADMIN_PORT=' .env 2>/dev/null | cut -d= -f2 || echo 9092)"
ADMIN_PORT="${ADMIN_PORT:-9092}"
DOMAIN="$(grep -E '^domain:' services.yml | sed 's/^domain:[[:space:]]*//; s/["'"'"']//g')"

cat <<EOF

════════════════════════════════════════════
 部署完成，接下来 3 步
════════════════════════════════════════════

1️⃣  设置管理页密码（只能局域网访问）
     浏览器打开： http://${NAS_IP:-<你的NAS_IP>}:${ADMIN_PORT}/

2️⃣  在管理页创建一个用户（组选 users），这就是你登录服务用的账号

3️⃣  让 Tailscale Funnel 指向网关
     tailscale funnel --bg 8080
     （如果 Tailscale 在容器里：docker exec <容器名> tailscale funnel --bg 8080）

然后访问： https://${DOMAIN}
   会跳转到登录页 → 用第 2 步创建的账号登录 → 进入黄页

EOF

echo "常用命令："
echo "  改完 services.yml 后应用：  ./scripts/apply-config.sh"
echo "  查看状态：                  docker compose ps"
echo "  查看日志：                  docker compose logs -f caddy authelia admin"
echo "  自检：                      ./scripts/check.sh"
echo "  备份：                      ./scripts/backup.sh"
