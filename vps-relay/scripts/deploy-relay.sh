#!/usr/bin/env bash
# vps-relay/scripts/deploy-relay.sh
#
# 一行命令部署链式代理改动到 VPS-LA + Sub-Store。
# 流程：
#   1. 跑 generate.py 生成 mihomo / nginx / vmess-bundle 三个产物
#   2. scp config.yaml → VPS → docker restart mihomo-relay
#   3. docker cp relay.conf → openresty 容器 → nginx -s reload
#   4. PATCH Sub-Store sub vps-chain-residential 的 content（需用户先一次性手动建空 sub）
#
# 前置：
#   - vps-relay/.secrets.local/credentials.env 含
#       RELAY_VM_UUID, RELAY_WS_PATH, BACKEND_PATH, RELAY_PUBLIC_HOST
#       可选：OPENRESTY_CONTAINER, OPENRESTY_SITE_DIR, SSH_TARGET
#   - SSH alias `vps` 已配（参 .trellis/spec/network/ssh-tmux-remote-ops.md）
#   - Sub-Store 后台存在 sub `vps-chain-residential`（local 类型，content 可初始为空）
#     已加进 merged-airports collection 的 subscriptions 列表

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
RELAY_DIR="$REPO_ROOT/vps-relay"
OUT_DIR="$RELAY_DIR/.secrets.local/out"
SECRETS_FILE="$RELAY_DIR/.secrets.local/credentials.env"

cd "$REPO_ROOT"

if [ ! -f "$SECRETS_FILE" ]; then
  echo "ERROR: secrets file not found: $SECRETS_FILE"
  exit 1
fi

set -a
# shellcheck disable=SC1090
. "$SECRETS_FILE"
set +a

SSH_TARGET="${SSH_TARGET:-vps}"
OPENRESTY_CONTAINER="${OPENRESTY_CONTAINER:-}"
OPENRESTY_SITE_DIR="${OPENRESTY_SITE_DIR:-/www/sites/${RELAY_PUBLIC_HOST}/proxy}"

echo "[1/4] 生成产物..."
python3 "$RELAY_DIR/generate.py" --out-dir "$OUT_DIR"

[ -f "$OUT_DIR/config.yaml" ] || { echo "ERROR: config.yaml 未生成"; exit 1; }
[ -f "$OUT_DIR/relay.conf" ] || { echo "ERROR: relay.conf 未生成"; exit 1; }
[ -f "$OUT_DIR/vmess-bundle.txt" ] || { echo "ERROR: vmess-bundle.txt 未生成"; exit 1; }

echo
echo "[2/4] 部署 mihomo-relay config.yaml..."
scp -q "$OUT_DIR/config.yaml" "$SSH_TARGET:/opt/1panel/apps/mihomo-relay/config.yaml"
ssh "$SSH_TARGET" 'docker restart mihomo-relay >/dev/null && sleep 5 && docker ps --filter name=mihomo-relay --format "{{.Status}}"'

echo
echo "[3/4] 部署 nginx relay.conf..."
scp -q "$OUT_DIR/relay.conf" "$SSH_TARGET:/tmp/relay.conf"
ssh "$SSH_TARGET" "OPENRESTY_CONTAINER='$OPENRESTY_CONTAINER' OPENRESTY_SITE_DIR='$OPENRESTY_SITE_DIR' bash -s" <<'REMOTE'
  set -euo pipefail
  if [ -z "${OPENRESTY_CONTAINER:-}" ]; then
    OPENRESTY_CONTAINER=$(docker ps --format "{{.Names}}" | grep -E 'openresty|OpenResty' | head -n 1 || true)
  fi
  if [ -z "${OPENRESTY_CONTAINER:-}" ]; then
    echo "ERROR: OPENRESTY_CONTAINER is not set and no openresty container was detected"
    exit 1
  fi
  docker cp /tmp/relay.conf "${OPENRESTY_CONTAINER}:${OPENRESTY_SITE_DIR}/relay.conf" &&
  docker exec "$OPENRESTY_CONTAINER" nginx -t 2>&1 | tail -2 &&
  docker exec "$OPENRESTY_CONTAINER" nginx -s reload &&
  rm /tmp/relay.conf
REMOTE

echo
echo "[4/4] PATCH Sub-Store sub vps-chain-residential..."

# 上传 bundle 到 VPS（避免 ssh argv 长度限制 + base64 特殊字符 escape 问题）
scp -q "$OUT_DIR/vmess-bundle.txt" "$SSH_TARGET:/tmp/vmess-bundle.txt"

# 检测 sub 存在 + PATCH
ssh "$SSH_TARGET" "RELAY_PUBLIC_HOST='$RELAY_PUBLIC_HOST' bash -s" <<'REMOTE'
  set -euo pipefail
  BACKEND=$(docker inspect sub-store --format "{{range .Config.Env}}{{println .}}{{end}}" | grep BACKEND_PATH | cut -d= -f2)
  HTTP_CODE=$(curl -s -o /dev/null -w "%{http_code}" "http://127.0.0.1:3001${BACKEND}/api/sub/vps-chain-residential")
  if [ "$HTTP_CODE" != "200" ]; then
    echo "  ⚠️  sub vps-chain-residential 不存在 (HTTP $HTTP_CODE)"
    echo
    echo "  ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
    echo "  首次部署需要在 Sub-Store 面板创建空 sub（一次性，5 步）："
    echo
    echo "  1. 浏览器打开 https://${RELAY_PUBLIC_HOST}/"
    echo "  2. 订阅管理 → 添加 → 类型：本地（local）"
    echo "  3. 名称：vps-chain-residential"
    echo "     显示名称：VPS 链式家宽"
    echo "     content 字段：留空（脚本会自动 PATCH 填）"
    echo "  4. 保存"
    echo "  5. 订阅集合 → merged-airports → 编辑 → 关联订阅勾上 vps-chain-residential → 保存"
    echo "  6. 重跑 bash vps-relay/scripts/deploy-relay.sh"
    echo "  ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
    rm /tmp/vmess-bundle.txt
    exit 2
  fi

  BUNDLE=$(cat /tmp/vmess-bundle.txt)
  PAYLOAD=$(python3 -c "import json,sys; print(json.dumps({\"content\": sys.argv[1]}))" "$BUNDLE")
  RESP=$(curl -s -X PATCH -H "Content-Type: application/json" -d "$PAYLOAD" "http://127.0.0.1:3001${BACKEND}/api/sub/vps-chain-residential")
  echo "  PATCH resp: ${RESP:0:120}"
  rm /tmp/vmess-bundle.txt
REMOTE

echo
echo "Deploy 完成。三端订阅 30s 内自动同步新链式节点列表。"
echo
echo "客户端验收：选 \`🏡 家宽选择\` selector → 看到 \`🏡 链式 美国-XX 家宽\` 等节点 → 选中 → 访问 ipinfo.io 看出口 IP。"
