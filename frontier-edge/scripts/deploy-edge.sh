#!/usr/bin/env bash
# Deploy generated Frontier Edge artifacts to a US VPS.
#
# Safety:
# - This script does not bypass SSH host-key warnings. Verify a changed host key
#   from the provider console before changing known_hosts.
# - If remote 80/443 are already listening, deploy stops unless this appliance
#   already owns them or EDGE_ALLOW_REPLACE_INGRESS=1 is set in the local secret
#   env with explicit EDGE_STOP_SERVICES.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
EDGE_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
REPO_ROOT="$(cd "$EDGE_DIR/.." && pwd)"
SECRETS_FILE="$EDGE_DIR/.secrets.local/edge-us.env"
ENV_FILES=()
OUT_DIR="$EDGE_DIR/.secrets.local/out"
PATCH_SUBSTORE=0

usage() {
  cat <<'USAGE'
Usage: bash frontier-edge/scripts/deploy-edge.sh [--env-file FILE] [--patch-substore]

Generates local artifacts, checks remote Docker/Compose and 80/443 availability,
uploads the appliance to REMOTE_DIR, and runs docker compose up -d.

Options:
  --env-file FILE     load env file; may be repeated, later files override earlier ones
                     set EDGE_ROLES_FILE / EDGE_HY2_ROLES_FILE in env to render
                     an alternate public-safe role registry such as EDGE-US v2
  --patch-substore   after deploy, PATCH existing target Sub-Store sub edge-us-roles
                     using scripts/patch-substore.sh
USAGE
}

shell_quote() {
  printf "%q" "$1"
}

while [ "$#" -gt 0 ]; do
  case "$1" in
    --env-file)
      shift
      if [ "$#" -eq 0 ]; then
        echo "ERROR: --env-file requires a path" >&2
        exit 1
      fi
      ENV_FILES+=("$1")
      ;;
    --patch-substore) PATCH_SUBSTORE=1 ;;
    -h|--help) usage; exit 0 ;;
    *) echo "ERROR: unknown argument: $1" >&2; usage; exit 1 ;;
  esac
  shift
done

if [ "${#ENV_FILES[@]}" -eq 0 ]; then
  ENV_FILES=("$SECRETS_FILE")
fi

for env_file in "${ENV_FILES[@]}"; do
  if [ ! -f "$env_file" ]; then
    echo "ERROR: env file not found: $env_file" >&2
    echo "Copy frontier-edge/examples/edge-us.env.example first." >&2
    exit 1
  fi
done

set -a
for env_file in "${ENV_FILES[@]}"; do
  # shellcheck disable=SC1090
  . <(tr -d '\r' < "$env_file")
done
set +a

SSH_TARGET="${SSH_TARGET:-US}"
REMOTE_DIR="${REMOTE_DIR:-/opt/frontier/frontier-edge}"
EDGE_INGRESS_MODE="${EDGE_INGRESS_MODE:-caddy}"
EDGE_OPENRESTY_CONTAINER="${EDGE_OPENRESTY_CONTAINER:-1Panel-openresty-kOZu}"
EDGE_OPENRESTY_CONF_DIR="${EDGE_OPENRESTY_CONF_DIR:-/opt/1panel/apps/openresty/openresty/conf/conf.d}"
EDGE_OPENRESTY_CONF_NAME="${EDGE_OPENRESTY_CONF_NAME:-${EDGE_PUBLIC_HOST}.conf}"
EDGE_ENABLE_HY2="${EDGE_ENABLE_HY2:-0}"
EDGE_ROLES_FILE="${EDGE_ROLES_FILE:-}"
EDGE_HY2_ROLES_FILE="${EDGE_HY2_ROLES_FILE:-}"
REQUIRED_LOCAL_PORTS=""
REQUIRED_UDP_PORTS=""
SSH_ARGS=(-o BatchMode=yes)
if [ -n "${SSH_PORT:-}" ]; then
  SSH_ARGS+=(-p "$SSH_PORT")
fi
if [ -n "${SSH_IDENTITY_FILE:-}" ]; then
  SSH_ARGS+=(-i "$SSH_IDENTITY_FILE")
fi

cd "$REPO_ROOT"

echo "[1/5] Validate role registry..."
GEN_ENV_ARGS=()
PATCH_ENV_ARGS=()
for env_file in "${ENV_FILES[@]}"; do
  GEN_ENV_ARGS+=(--env-file "$env_file")
  PATCH_ENV_ARGS+=(--env-file "$env_file")
done
if [ -n "$EDGE_ROLES_FILE" ]; then
  [ -f "$EDGE_ROLES_FILE" ] || { echo "ERROR: EDGE_ROLES_FILE not found: $EDGE_ROLES_FILE" >&2; exit 1; }
  GEN_ENV_ARGS+=(--roles-file "$EDGE_ROLES_FILE")
fi
if [ -n "$EDGE_HY2_ROLES_FILE" ]; then
  [ -f "$EDGE_HY2_ROLES_FILE" ] || { echo "ERROR: EDGE_HY2_ROLES_FILE not found: $EDGE_HY2_ROLES_FILE" >&2; exit 1; }
  GEN_ENV_ARGS+=(--hy2-roles-file "$EDGE_HY2_ROLES_FILE")
fi

python3 "$EDGE_DIR/generate.py" --check "${GEN_ENV_ARGS[@]}"

echo
echo "[2/5] Generate appliance artifacts..."
python3 "$EDGE_DIR/generate.py" "${GEN_ENV_ARGS[@]}" --out-dir "$OUT_DIR"

[ -f "$OUT_DIR/compose.yaml" ] || { echo "ERROR: compose.yaml was not generated" >&2; exit 1; }
[ -f "$OUT_DIR/mihomo/config.yaml" ] || { echo "ERROR: mihomo/config.yaml was not generated" >&2; exit 1; }
[ -f "$OUT_DIR/vmess-bundle.txt" ] || { echo "ERROR: vmess-bundle.txt was not generated" >&2; exit 1; }
if [ "$EDGE_ENABLE_HY2" = "1" ]; then
  [ -f "$OUT_DIR/hy2-bundle.txt" ] || { echo "ERROR: hy2-bundle.txt was not generated" >&2; exit 1; }
  REQUIRED_UDP_PORTS="$(
    python3 - "$OUT_DIR/compose.yaml" <<'PY'
import re
import sys
from pathlib import Path

text = Path(sys.argv[1]).read_text(encoding="utf-8")
ports = sorted({int(match.group(1)) for match in re.finditer(r'"(\d+):\d+/udp"', text)})
print(" ".join(str(port) for port in ports))
PY
  )"
fi
if [ "$EDGE_INGRESS_MODE" = "openresty" ]; then
  [ -f "$OUT_DIR/openresty/$EDGE_OPENRESTY_CONF_NAME" ] || { echo "ERROR: OpenResty config was not generated" >&2; exit 1; }
  REQUIRED_LOCAL_PORTS="$(
    python3 - "$OUT_DIR/compose.yaml" <<'PY'
import re
import sys
from pathlib import Path

text = Path(sys.argv[1]).read_text(encoding="utf-8")
ports = sorted({int(match.group(1)) for match in re.finditer(r'"127\.0\.0\.1:(\d+):\d+"', text)})
if not ports:
    raise SystemExit("no localhost ports found in generated compose")
print(" ".join(str(port) for port in ports))
PY
  )"
else
  [ -f "$OUT_DIR/Caddyfile" ] || { echo "ERROR: Caddyfile was not generated" >&2; exit 1; }
fi

echo
echo "[3/5] Remote preflight..."
REMOTE_PREFLIGHT_CMD="REMOTE_DIR=$(shell_quote "$REMOTE_DIR") EDGE_INGRESS_MODE=$(shell_quote "$EDGE_INGRESS_MODE") EDGE_PUBLIC_HOST=$(shell_quote "$EDGE_PUBLIC_HOST") PATCH_SUBSTORE=$(shell_quote "$PATCH_SUBSTORE") REQUIRED_LOCAL_PORTS=$(shell_quote "$REQUIRED_LOCAL_PORTS") REQUIRED_UDP_PORTS=$(shell_quote "$REQUIRED_UDP_PORTS") EDGE_ENABLE_HY2=$(shell_quote "$EDGE_ENABLE_HY2") EDGE_ALLOW_REPLACE_INGRESS=$(shell_quote "${EDGE_ALLOW_REPLACE_INGRESS:-0}") EDGE_STOP_SERVICES=$(shell_quote "${EDGE_STOP_SERVICES:-}") EDGE_OPENRESTY_CONTAINER=$(shell_quote "$EDGE_OPENRESTY_CONTAINER") EDGE_OPENRESTY_CONF_DIR=$(shell_quote "$EDGE_OPENRESTY_CONF_DIR") EDGE_OPENRESTY_CONF_NAME=$(shell_quote "$EDGE_OPENRESTY_CONF_NAME") EDGE_OPENRESTY_CERTIFICATE=$(shell_quote "${EDGE_OPENRESTY_CERTIFICATE:-}") EDGE_OPENRESTY_CERTIFICATE_KEY=$(shell_quote "${EDGE_OPENRESTY_CERTIFICATE_KEY:-}") bash -s"
ssh "${SSH_ARGS[@]}" "$SSH_TARGET" "$REMOTE_PREFLIGHT_CMD" <<'REMOTE'
set -euo pipefail

if ! command -v docker >/dev/null 2>&1; then
  echo "ERROR: docker is not installed on remote host" >&2
  exit 1
fi

if docker compose version >/dev/null 2>&1; then
  COMPOSE_CMD="docker compose"
elif command -v docker-compose >/dev/null 2>&1; then
  COMPOSE_CMD="docker-compose"
else
  echo "ERROR: neither docker compose nor docker-compose is available" >&2
  exit 1
fi

for port in ${REQUIRED_UDP_PORTS:-}; do
  if ss -lunH "sport = :$port" | grep -q .; then
    if docker ps --format '{{.Names}}' | grep -qx 'frontier-edge-mihomo' && \
      docker port frontier-edge-mihomo 2>/dev/null | grep -Eq "^${port}/udp -> .*:${port}$"; then
      continue
    fi
    echo "ERROR: required HY2 UDP port is already busy: $port" >&2
    ss -lunp "sport = :$port" 2>/dev/null || true
    exit 3
  fi
done

if [ "${EDGE_INGRESS_MODE:-caddy}" = "openresty" ]; then
  if ! docker ps --format '{{.Names}}' | grep -qx "$EDGE_OPENRESTY_CONTAINER"; then
    echo "ERROR: OpenResty container is not running: $EDGE_OPENRESTY_CONTAINER" >&2
    exit 3
  fi
  docker exec "$EDGE_OPENRESTY_CONTAINER" openresty -t >/dev/null
  if [ ! -d "$EDGE_OPENRESTY_CONF_DIR" ]; then
    echo "ERROR: OpenResty conf dir not found: $EDGE_OPENRESTY_CONF_DIR" >&2
    exit 3
  fi
  if [ "${EDGE_ENABLE_HY2:-0}" = "1" ]; then
    for cert_path in "${EDGE_OPENRESTY_CERTIFICATE:-}" "${EDGE_OPENRESTY_CERTIFICATE_KEY:-}"; do
      if [ -z "$cert_path" ] || ! docker exec "$EDGE_OPENRESTY_CONTAINER" test -r "$cert_path"; then
        echo "ERROR: HY2 is enabled but OpenResty certificate path is not readable in container." >&2
        exit 3
      fi
    done
  fi
  case "$EDGE_OPENRESTY_CONF_NAME" in
    */*|*.tmp|*~)
      echo "ERROR: invalid EDGE_OPENRESTY_CONF_NAME: $EDGE_OPENRESTY_CONF_NAME" >&2
      exit 3
      ;;
  esac
  PUBLIC_IP="$(curl -4s --max-time 5 https://ifconfig.co 2>/dev/null | tr -d '\r\n' || true)"
  DNS_IPS="$(python3 - "$EDGE_PUBLIC_HOST" <<'PY' 2>/dev/null || true
import json
import sys
import urllib.request

host = sys.argv[1]
answers = []
for url in [
    f"https://cloudflare-dns.com/dns-query?name={host}&type=A",
    f"https://dns.google/resolve?name={host}&type=A",
]:
    try:
        req = urllib.request.Request(url, headers={"accept": "application/dns-json"})
        data = json.load(urllib.request.urlopen(req, timeout=8))
        answers.extend(
            item.get("data")
            for item in data.get("Answer", [])
            if item.get("type") == 1 and item.get("data")
        )
    except Exception:
        pass
print(" ".join(sorted(set(answers))))
PY
)"
  if [ -z "$DNS_IPS" ]; then
    DNS_IPS="$(getent ahostsv4 "$EDGE_PUBLIC_HOST" 2>/dev/null | awk '{print $1}' | sort -u | paste -sd ' ' - || true)"
  fi
  if [ -z "$DNS_IPS" ] || [ -z "$PUBLIC_IP" ] || ! printf '%s\n' "$DNS_IPS" | grep -qw "$PUBLIC_IP"; then
    if [ "${PATCH_SUBSTORE:-0}" = "1" ]; then
      echo "ERROR: $EDGE_PUBLIC_HOST does not resolve to this VPS public IP; refusing to patch Sub-Store." >&2
      echo "  public-ip: ${PUBLIC_IP:-unknown}" >&2
      echo "  dns-ips: ${DNS_IPS:-none}" >&2
      exit 4
    fi
    echo "WARN: $EDGE_PUBLIC_HOST DNS is not confirmed on this VPS; deploy may proceed without Sub-Store patch."
  fi
  for port in $REQUIRED_LOCAL_PORTS; do
    if ss -ltnH "sport = :$port" | grep -q .; then
      if docker ps --format '{{.Names}}' | grep -qx 'frontier-edge-mihomo' && docker port frontier-edge-mihomo 2>/dev/null | grep -q "127.0.0.1:$port"; then
        continue
      fi
      echo "ERROR: required OpenResty-mode local port is busy: $port" >&2
      ss -ltnp "sport = :$port" 2>/dev/null || true
      exit 3
    fi
  done
  echo "  ingress: existing OpenResty"
else
  LISTEN="$(ss -H -ltn '( sport = :80 or sport = :443 )' 2>/dev/null || true)"
  if [ -n "$LISTEN" ]; then
    if docker ps --format '{{.Names}}' | grep -qx 'frontier-edge-caddy'; then
      echo "  80/443 are already owned by frontier-edge-caddy; continuing update."
    elif [ "${EDGE_ALLOW_REPLACE_INGRESS:-0}" = "1" ]; then
      if [ -z "${EDGE_STOP_SERVICES:-}" ]; then
        echo "ERROR: EDGE_ALLOW_REPLACE_INGRESS=1 requires EDGE_STOP_SERVICES." >&2
        echo "$LISTEN" >&2
        exit 3
      fi
      for svc in $EDGE_STOP_SERVICES; do
        case "$svc" in
          *[!A-Za-z0-9_.@-]*)
            echo "ERROR: invalid service name in EDGE_STOP_SERVICES: $svc" >&2
            exit 3
            ;;
        esac
        systemctl status "$svc" >/dev/null 2>&1 || {
          echo "ERROR: EDGE_STOP_SERVICES contains unknown or inactive service: $svc" >&2
          exit 3
        }
      done
      echo "  80/443 will be replaced after artifacts upload by stopping: $EDGE_STOP_SERVICES"
    else
      echo "ERROR: remote 80/443 already have listeners; deploy will not replace an existing ingress." >&2
      echo "$LISTEN" >&2
      exit 3
    fi
  fi
fi

mkdir -p "$REMOTE_DIR"
echo "  compose: $COMPOSE_CMD"
echo "  remote dir: $REMOTE_DIR"
REMOTE

echo
echo "[4/5] Upload artifacts..."
TAR_ITEMS=(compose.yaml mihomo vmess-bundle.txt)
if [ "$EDGE_ENABLE_HY2" = "1" ]; then
  TAR_ITEMS+=(hy2-bundle.txt)
fi
if [ "$EDGE_INGRESS_MODE" = "openresty" ]; then
  TAR_ITEMS+=(openresty)
  tar -C "$OUT_DIR" -cz "${TAR_ITEMS[@]}" \
    | ssh "${SSH_ARGS[@]}" "$SSH_TARGET" "tar -xzf - -C '$REMOTE_DIR'"
else
  TAR_ITEMS+=(Caddyfile)
  tar -C "$OUT_DIR" -cz "${TAR_ITEMS[@]}" \
    | ssh "${SSH_ARGS[@]}" "$SSH_TARGET" "tar -xzf - -C '$REMOTE_DIR'"
fi

echo
echo "[5/5] Start edge appliance..."
REMOTE_START_CMD="REMOTE_DIR=$(shell_quote "$REMOTE_DIR") EDGE_INGRESS_MODE=$(shell_quote "$EDGE_INGRESS_MODE") EDGE_ENABLE_HY2=$(shell_quote "$EDGE_ENABLE_HY2") EDGE_ALLOW_REPLACE_INGRESS=$(shell_quote "${EDGE_ALLOW_REPLACE_INGRESS:-0}") EDGE_STOP_SERVICES=$(shell_quote "${EDGE_STOP_SERVICES:-}") EDGE_OPENRESTY_CONTAINER=$(shell_quote "$EDGE_OPENRESTY_CONTAINER") EDGE_OPENRESTY_CONF_DIR=$(shell_quote "$EDGE_OPENRESTY_CONF_DIR") EDGE_OPENRESTY_CONF_NAME=$(shell_quote "$EDGE_OPENRESTY_CONF_NAME") EDGE_OPENRESTY_CERTIFICATE=$(shell_quote "${EDGE_OPENRESTY_CERTIFICATE:-}") EDGE_OPENRESTY_CERTIFICATE_KEY=$(shell_quote "${EDGE_OPENRESTY_CERTIFICATE_KEY:-}") bash -s"
ssh "${SSH_ARGS[@]}" "$SSH_TARGET" "$REMOTE_START_CMD" <<'REMOTE'
set -euo pipefail
cd "$REMOTE_DIR"
STOPPED_SERVICES=""
OPENRESTY_CONF_PATH="${EDGE_OPENRESTY_CONF_DIR%/}/$EDGE_OPENRESTY_CONF_NAME"
rollback_ingress() {
  if [ -n "$STOPPED_SERVICES" ]; then
    echo "rollback: restarting stopped ingress services: $STOPPED_SERVICES" >&2
    for svc in $STOPPED_SERVICES; do
      systemctl start "$svc" >/dev/null 2>&1 || true
    done
  fi
}
rollback_openresty_file() {
  if [ "${EDGE_INGRESS_MODE:-caddy}" = "openresty" ] && [ -f "$OPENRESTY_CONF_PATH" ]; then
    echo "rollback: removing OpenResty frontier-edge config" >&2
    rm -f "$OPENRESTY_CONF_PATH"
    docker exec "$EDGE_OPENRESTY_CONTAINER" openresty -t >/dev/null 2>&1 && \
      docker exec "$EDGE_OPENRESTY_CONTAINER" openresty -s reload >/dev/null 2>&1 || true
  fi
}
trap 'rc=$?; if [ "$rc" -ne 0 ]; then rollback_openresty_file; rollback_ingress; fi; exit "$rc"' EXIT

if docker compose version >/dev/null 2>&1; then
  docker compose -f compose.yaml config >/dev/null
  COMPOSE=(docker compose -f compose.yaml)
else
  docker-compose -f compose.yaml config >/dev/null
  COMPOSE=(docker-compose -f compose.yaml)
fi

if [ "${EDGE_INGRESS_MODE:-caddy}" = "openresty" ]; then
  if [ "${EDGE_ENABLE_HY2:-0}" = "1" ]; then
    mkdir -p "$REMOTE_DIR/mihomo/certs"
    docker cp "$EDGE_OPENRESTY_CONTAINER:$EDGE_OPENRESTY_CERTIFICATE" "$REMOTE_DIR/mihomo/certs/hy2-fullchain.pem" >/dev/null
    docker cp "$EDGE_OPENRESTY_CONTAINER:$EDGE_OPENRESTY_CERTIFICATE_KEY" "$REMOTE_DIR/mihomo/certs/hy2-privkey.pem" >/dev/null
    chmod 600 "$REMOTE_DIR/mihomo/certs/hy2-fullchain.pem" "$REMOTE_DIR/mihomo/certs/hy2-privkey.pem"
  fi
  docker run --rm \
    -v "$REMOTE_DIR/mihomo:/root/.config/mihomo:ro" \
    metacubex/mihomo:latest -t -d /root/.config/mihomo -f /root/.config/mihomo/config.yaml >/dev/null
  "${COMPOSE[@]}" up -d
  "${COMPOSE[@]}" restart mihomo >/dev/null
  "${COMPOSE[@]}" ps

  cp "$REMOTE_DIR/openresty/$EDGE_OPENRESTY_CONF_NAME" "$OPENRESTY_CONF_PATH"
  docker exec "$EDGE_OPENRESTY_CONTAINER" openresty -t >/dev/null
  docker exec "$EDGE_OPENRESTY_CONTAINER" openresty -s reload >/dev/null
  echo "OpenResty config installed: $OPENRESTY_CONF_PATH"
else
  docker run --rm \
    -v "$REMOTE_DIR/Caddyfile:/etc/caddy/Caddyfile:ro" \
    caddy:2-alpine caddy validate --config /etc/caddy/Caddyfile >/dev/null
  docker run --rm \
    -v "$REMOTE_DIR/mihomo:/root/.config/mihomo:ro" \
    metacubex/mihomo:latest -t -d /root/.config/mihomo -f /root/.config/mihomo/config.yaml >/dev/null

  LISTEN="$(ss -H -ltn '( sport = :80 or sport = :443 )' 2>/dev/null || true)"
  if [ -n "$LISTEN" ] && ! docker ps --format '{{.Names}}' | grep -qx 'frontier-edge-caddy'; then
    if [ "${EDGE_ALLOW_REPLACE_INGRESS:-0}" != "1" ]; then
      echo "ERROR: remote 80/443 became busy before start." >&2
      echo "$LISTEN" >&2
      exit 3
    fi
    echo "stopping ingress services: $EDGE_STOP_SERVICES"
    for svc in $EDGE_STOP_SERVICES; do
      systemctl stop "$svc"
      STOPPED_SERVICES="$STOPPED_SERVICES $svc"
    done
    sleep 2
    LISTEN_AFTER="$(ss -H -ltn '( sport = :80 or sport = :443 )' 2>/dev/null || true)"
    if [ -n "$LISTEN_AFTER" ]; then
      echo "ERROR: 80/443 are still busy after stopping EDGE_STOP_SERVICES." >&2
      echo "$LISTEN_AFTER" >&2
      exit 3
    fi
  fi

  "${COMPOSE[@]}" up -d
  if docker ps --format '{{.Names}}' | grep -qx 'frontier-edge-caddy'; then
    docker exec frontier-edge-caddy caddy reload --config /etc/caddy/Caddyfile >/dev/null \
      || "${COMPOSE[@]}" restart caddy
  fi
  "${COMPOSE[@]}" ps
fi
REMOTE

if [ "$PATCH_SUBSTORE" = "1" ]; then
  echo
    echo "[extra] PATCH target Sub-Store role sub..."
  bash "$EDGE_DIR/scripts/patch-substore.sh" "${PATCH_ENV_ARGS[@]}"
fi

echo
echo "Frontier Edge deploy complete."
echo "Next checks: mihomo controller role counts, final profile mihomo.exe -t, and exit IP tests."
exit 0
