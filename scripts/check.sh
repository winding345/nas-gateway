#!/usr/bin/env bash
# ══════════════════════════════════════════════════════════════
#  自检：一条命令检查整条链路
#
#    ./scripts/check.sh
# ══════════════════════════════════════════════════════════════
set -uo pipefail
cd "$(dirname "$0")/.."

PASS=0; FAIL=0; WARN=0
ok()   { echo "  ✅ $1"; PASS=$((PASS+1)); }
bad()  { echo "  ❌ $1"; FAIL=$((FAIL+1)); }
warn() { echo "  ⚠️  $1"; WARN=$((WARN+1)); }
hr()   { echo "────────────────────────────────────────────"; }

# ── 读取配置 ────────────────────────────────────────────────
DOMAIN=""; GW_LISTEN=""
if [ -f services.yml ]; then
  DOMAIN="$(grep -E '^domain:' services.yml | sed 's/^domain:[[:space:]]*//; s/["'"'"']//g' | tr -d ' ')"
  GW_LISTEN="$(grep -A3 -E '^gateway:' services.yml | grep -E 'listen:' | head -1 | sed 's/.*listen:[[:space:]]*//; s/["'"'"']//g' | tr -d ' ')"
fi
GW_LISTEN="${GW_LISTEN:-:8080}"
GW_PORT="${GW_LISTEN##*:}"
ADMIN_PORT="$(grep -E '^ADMIN_PORT=' .env 2>/dev/null | cut -d= -f2 | tr -d ' ')"
ADMIN_PORT="${ADMIN_PORT:-9092}"
NAS_IP="$(hostname -I 2>/dev/null | awk '{print $1}')"

echo
echo "════════ 1. 配置文件 ════════"
[ -f services.yml ] && ok "services.yml 存在" || bad "services.yml 不存在（cp services.yml.example services.yml）"
if [ -n "$DOMAIN" ] && [ "$DOMAIN" != "REPLACE-ME.ts.net" ]; then
  ok "域名：$DOMAIN"
else
  bad "域名未填写（services.yml 的 domain）"
fi
[ -f authelia/users_database.yml ] && ok "用户库存在" || bad "用户库不存在"
[ -f caddy/Caddyfile ] && ok "Caddyfile 已生成" || bad "Caddyfile 未生成（跑 ./scripts/apply-config.sh）"
[ -f authelia/configuration.yml ] && ok "Authelia 配置已生成" || bad "Authelia 配置未生成"
[ -f portal/index.html ] && ok "黄页已生成" || warn "黄页未生成"

echo
echo "════════ 2. 密钥 ════════"
for s in session_secret storage_encryption_key identity_validation_secret; do
  if [ -s "authelia/secrets/$s" ]; then ok "$s"; else bad "$s 缺失（跑 ./scripts/generate-secrets.sh）"; fi
done

echo
echo "════════ 3. 容器 ════════"
for c in ng-caddy ng-authelia ng-admin; do
  st="$(docker inspect -f '{{.State.Status}}' "$c" 2>/dev/null || echo missing)"
  case "$st" in
    running) ok "$c 运行中" ;;
    missing) bad "$c 不存在" ;;
    *)       bad "$c 状态：$st" ;;
  esac
done

echo
echo "════════ 4. 服务端口 ════════"
if curl -fsS -m 5 -o /dev/null "http://127.0.0.1:$GW_PORT/authelia/" 2>/dev/null; then
  ok "Caddy ($GW_PORT) 可达"
else
  warn "Caddy ($GW_PORT) 无响应（未登录时返回 401/302 也算正常，这里只做连通性判断）"
  curl -s -o /dev/null -m 5 -w '' "http://127.0.0.1:$GW_PORT/" 2>/dev/null && ok "Caddy 端口有响应" || bad "Caddy 端口 ($GW_PORT) 不通"
fi

code="$(curl -s -o /dev/null -m 5 -w '%{http_code}' "http://127.0.0.1:9091/api/health" 2>/dev/null)"
[ "$code" = "200" ] && ok "Authelia (9091) 健康" || bad "Authelia (9091) 返回 $code"

code="$(curl -s -o /dev/null -m 5 -w '%{http_code}' "http://127.0.0.1:$ADMIN_PORT/healthz" 2>/dev/null)"
[ "$code" = "200" ] && ok "管理页 ($ADMIN_PORT) 健康" || bad "管理页 ($ADMIN_PORT) 返回 $code"

echo
echo "════════ 5. 生成的路由 ════════"
if [ -f caddy/Caddyfile ]; then
  n="$(grep -c 'reverse_proxy 127.0.0.1' caddy/Caddyfile || true)"
  ok "Caddyfile 里有 $n 条 reverse_proxy"
  grep -E '^\s*handle_path ' caddy/Caddyfile | sed 's/^/     /'
  grep -q 'request_header -Remote-User' caddy/Caddyfile \
    && ok "已剥离伪造 header" || bad "缺少 request_header -Remote-User"
fi

echo
echo "════════ 6. 认证是否生效 ════════"
if [ -n "$DOMAIN" ] && [ "$DOMAIN" != "REPLACE-ME.ts.net" ]; then
  code="$(curl -s -o /dev/null -m 10 -w '%{http_code}' -H "Host: $DOMAIN" "http://127.0.0.1:$GW_PORT/" 2>/dev/null)"
  case "$code" in
    302|303|401) ok "未登录访问黄页 → $code（被拦截，符合预期）" ;;
    200)         bad "未登录访问黄页 → 200（认证没生效！）" ;;
    *)           warn "未登录访问黄页 → $code" ;;
  esac
else
  warn "域名未填，跳过认证检查"
fi

echo
echo "════════ 7. 局域网免登录（家库直连）════════"
REPO_ROOT="$(cd .. && pwd)"
# 从 services.yml 里取第一个 upstream 的端口试探
UP="$(grep -E '^\s*upstream:' services.yml 2>/dev/null | head -1 | sed 's/.*upstream:[[:space:]]*//; s/["'"'"']//g' | tr -d ' ')"
if [ -n "$UP" ]; then
  code="$(curl -s -o /dev/null -m 5 -w '%{http_code}' "$UP/" 2>/dev/null)"
  [ "$code" = "200" ] && ok "后端 $UP 直连可访问（局域网免登录路径正常）" || warn "后端 $UP 返回 $code"
else
  warn "services.yml 里没找到 upstream"
fi

echo
echo "════════ 8. 外部可达性（信息）════════"
if [ -n "$DOMAIN" ] && [ "$DOMAIN" != "REPLACE-ME.ts.net" ]; then
  if command -v tailscale >/dev/null 2>&1; then
    tailscale funnel status 2>/dev/null | sed 's/^/     /' || warn "无法读取 funnel 状态（Tailscale 可能在容器里）"
  else
    echo "     Tailscale 不在 PATH（可能在容器里），无法查询 funnel 状态"
  fi
fi

hr
echo "结果： ✅ $PASS   ⚠️  $WARN   ❌ $FAIL"
[ "$FAIL" -eq 0 ] && echo "全部通过 🎉" || echo "有 $FAIL 项需要处理"
echo
