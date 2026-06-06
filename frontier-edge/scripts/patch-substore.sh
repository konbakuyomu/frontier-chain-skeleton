#!/usr/bin/env bash
# PATCH the existing target Sub-Store local sub with the generated role bundle.
#
# This intentionally does not create the sub. Create edge-us-roles once in the
# Sub-Store Web panel, then this script can update its content safely.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
EDGE_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
SECRETS_FILE="$EDGE_DIR/.secrets.local/edge-us.env"
ENV_FILES=()
OUT_DIR="$EDGE_DIR/.secrets.local/out"
NO_BACKUP=0

usage() {
  cat <<'USAGE'
Usage: bash frontier-edge/scripts/patch-substore.sh [--env-file FILE] [--no-backup]

Requires env files and generated vmess-bundle.txt. --env-file may be repeated;
later files override earlier ones.
By default, SUBSTORE_SSH_TARGET must be set so the script can back up
sub-store.json before PATCH. Use --no-backup only for an intentional dry lab.
USAGE
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
    --no-backup) NO_BACKUP=1 ;;
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
    exit 1
  fi
done

set -a
for env_file in "${ENV_FILES[@]}"; do
  # shellcheck disable=SC1090
  . <(tr -d '\r' < "$env_file")
done
set +a

SUBSTORE_BACKEND_PATH="${SUBSTORE_BACKEND_PATH:-${BACKEND_PATH:-}}"
EDGE_ROLE_SUB="${EDGE_ROLE_SUB:-edge-us-roles}"

if [ -z "${SUBSTORE_PUBLIC_BASE_URL:-}" ] || [ -z "$SUBSTORE_BACKEND_PATH" ]; then
  echo "ERROR: SUBSTORE_PUBLIC_BASE_URL and SUBSTORE_BACKEND_PATH are required" >&2
  exit 1
fi
if [ ! -f "$OUT_DIR/vmess-bundle.txt" ]; then
  echo "ERROR: generated bundle not found: $OUT_DIR/vmess-bundle.txt" >&2
  echo "Run python3 frontier-edge/generate.py first." >&2
  exit 1
fi

BASE="${SUBSTORE_PUBLIC_BASE_URL%/}"
API_BASE="${BASE}${SUBSTORE_BACKEND_PATH}"

if [ "$NO_BACKUP" != "1" ]; then
  if [ -z "${SUBSTORE_SSH_TARGET:-}" ]; then
    echo "ERROR: SUBSTORE_SSH_TARGET is required for the pre-PATCH backup." >&2
    echo "Set it in edge-us.env, or rerun with --no-backup only for a dry lab." >&2
    exit 1
  fi
  SSH_ARGS=(-o BatchMode=yes)
  if [ -n "${SUBSTORE_SSH_PORT:-}" ]; then
    SSH_ARGS+=(-p "$SUBSTORE_SSH_PORT")
  fi
  if [ -n "${SUBSTORE_SSH_IDENTITY_FILE:-}" ]; then
    SSH_ARGS+=(-i "$SUBSTORE_SSH_IDENTITY_FILE")
  elif [ -n "${SSH_IDENTITY_FILE:-}" ]; then
    SSH_ARGS+=(-i "$SSH_IDENTITY_FILE")
  fi
  echo "[backup] target Sub-Store data..."
  ssh "${SSH_ARGS[@]}" "$SUBSTORE_SSH_TARGET" 'docker exec sub-store sh -c '"'"'TS=$(date +%s); cp /opt/app/data/sub-store.json "/opt/app/data/sub-store.json.bak-frontier-edge-${TS}"'"'"''
fi

echo "[check] existing sub: $EDGE_ROLE_SUB"
HTTP_CODE="$(curl -sS -o /dev/null -w "%{http_code}" "${API_BASE}/api/sub/${EDGE_ROLE_SUB}")"
if [ "$HTTP_CODE" != "200" ]; then
  echo "ERROR: Sub-Store sub '$EDGE_ROLE_SUB' is missing or inaccessible (HTTP $HTTP_CODE)." >&2
  echo "Create it once in the Web panel as a local sub, then add it to the normal node collection." >&2
  exit 2
fi

BUNDLE="$(cat "$OUT_DIR/vmess-bundle.txt")"
PAYLOAD="$(python3 -c 'import json,sys; print(json.dumps({"content": sys.argv[1]}))' "$BUNDLE")"

echo "[patch] update existing sub content..."
PATCH_RESPONSE="$(curl -sS -X PATCH \
  -H "Content-Type: application/json" \
  -d "$PAYLOAD" \
  "${API_BASE}/api/sub/${EDGE_ROLE_SUB}")"
printf '%s' "$PATCH_RESPONSE" | python3 -c '
import json
import sys

try:
    data = json.load(sys.stdin)
except Exception:
    print("patch response: non-json")
    raise SystemExit(1)

status = data.get("status")
item = data.get("data") if isinstance(data.get("data"), dict) else {}
name = item.get("name") or "<unknown>"
display = item.get("displayName") or item.get("display-name") or ""
if status != "success":
    print("patch status:", status)
    raise SystemExit(1)
print(f"patch status: {status}; sub: {name}; display: {display}")
'

echo
echo "PATCH complete. Refresh final profiles and validate with mihomo.exe -t."
