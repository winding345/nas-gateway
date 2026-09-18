#!/usr/bin/env bash
# ══════════════════════════════════════════════════════════════
#  命令行管理用户（应急用）
#
#  日常请在管理页操作： http://<你的NAS_IP>:9092
#
#  用法：
#    ./scripts/add-user.sh list
#    ./scripts/add-user.sh add <用户名> <组>     # 组：admins | users（会提示输密码）
#    ./scripts/add-user.sh passwd <用户名>
#    ./scripts/add-user.sh delete <用户名>
# ══════════════════════════════════════════════════════════════
set -euo pipefail
cd "$(dirname "$0")/.."

if [ $# -eq 0 ]; then
  sed -n '2,14p' "$0" | sed 's/^# \{0,1\}//'
  exit 1
fi

if ! docker image inspect nas-gateway-admin:latest >/dev/null 2>&1; then
  echo "▸ 构建 admin 镜像…"
  docker compose build admin
fi

# -T 关闭 TTY 分配，但我们需要交互输密码；所以有 TTY 时保留
if [ -t 0 ]; then
  docker compose run --rm --no-deps admin python /app/cli.py "$@"
else
  docker compose run --rm --no-deps -T admin python /app/cli.py "$@"
fi
