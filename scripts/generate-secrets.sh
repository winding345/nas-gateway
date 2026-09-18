#!/usr/bin/env bash
# ══════════════════════════════════════════════════════════════
#  生成 Authelia 所需的各种密钥
#
#  · 只生成一次；已存在的文件不会被覆盖（避免让所有会话失效）
#  · 生成在 authelia/secrets/，该目录已 gitignore
#  · 权限 600
#
#  注意：这些密钥一旦丢失，已登录的会话会全部失效（用户需要重新登录），
#        但用户账号本身不受影响。
# ══════════════════════════════════════════════════════════════
set -euo pipefail
cd "$(dirname "$0")/.."

SECRETS_DIR="authelia/secrets"
mkdir -p "$SECRETS_DIR"

gen() {
  local name="$1" file="$SECRETS_DIR/$1"
  if [ -s "$file" ]; then
    echo "  · $name  已存在，跳过"
    return 0
  fi
  # 64 字节 → 128 位十六进制
  head -c 64 /dev/urandom | od -An -tx1 | tr -d ' \n' > "$file"
  chmod 600 "$file"
  echo "  ✅ $name  已生成"
}

echo "生成密钥到 $SECRETS_DIR/"
gen session_secret
gen storage_encryption_key
gen identity_validation_secret
echo "完成。"
