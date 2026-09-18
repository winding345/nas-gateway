#!/usr/bin/env bash
# ══════════════════════════════════════════════════════════════
#  备份配置 / 用户库 / 密钥（不含 Caddy 证书缓存，那些可以重新签发）
#
#    ./scripts/backup.sh                 # 存到 ./backups/
#    ./scripts/backup.sh /path/to/dir    # 存到指定目录
#
#  建议加到 crontab：
#    0 3 * * * /path/to/nas-gateway/scripts/backup.sh >> /var/log/ng-backup.log 2>&1
# ══════════════════════════════════════════════════════════════
set -euo pipefail
cd "$(dirname "$0")/.."

DEST="${1:-./backups}"
KEEP=14
STAMP="$(date +%Y%m%d-%H%M%S)"

mkdir -p "$DEST"
DEST="$(cd "$DEST" && pwd)"

TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT

mkdir -p "$TMP/nas-gateway"

# ── 要备份的东西（都是小文件）──────────────────────────────
for f in services.yml .env authelia/base.yml authelia/configuration.yml \
         authelia/users_database.yml authelia/db.sqlite3 caddy/Caddyfile; do
  [ -e "$f" ] && cp -a "$f" "$TMP/nas-gateway/" 2>/dev/null || true
done

# 目录型
for d in authelia/secrets admin/data; do
  if [ -d "$d" ]; then
    mkdir -p "$TMP/nas-gateway/$d"
    cp -a "$d/." "$TMP/nas-gateway/$d/" 2>/dev/null || true
  fi
done

# ── 打包 ────────────────────────────────────────────────────
OUT="$DEST/nas-gateway-$STAMP.tar.gz"
tar -czf "$OUT" -C "$TMP" nas-gateway
chmod 600 "$OUT"

echo "✅ 已备份： $OUT"
echo "   大小： $(du -h "$OUT" | cut -f1)"

# ── 清理旧备份 ──────────────────────────────────────────────
COUNT=$(ls -1 "$DEST"/nas-gateway-*.tar.gz 2>/dev/null | wc -l)
if [ "$COUNT" -gt "$KEEP" ]; then
  ls -1t "$DEST"/nas-gateway-*.tar.gz | tail -n +$((KEEP + 1)) | while read -r old; do
    rm -f "$old" && echo "   已删除旧备份： $(basename "$old")"
  done
fi

echo
echo "⚠️  备份含密钥和用户密码哈希，请妥善保管（权限已设为 600）"
