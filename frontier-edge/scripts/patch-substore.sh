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
EDGE_ENABLE_HY2="${EDGE_ENABLE_HY2:-0}"
EDGE_HY2_ROLE_SUB="${EDGE_HY2_ROLE_SUB:-edge-us-hy2-roles}"

if [ -z "${SUBSTORE_PUBLIC_BASE_URL:-}" ] || [ -z "$SUBSTORE_BACKEND_PATH" ]; then
  echo "ERROR: SUBSTORE_PUBLIC_BASE_URL and SUBSTORE_BACKEND_PATH are required" >&2
  exit 1
fi
if [ ! -f "$OUT_DIR/vmess-bundle.txt" ]; then
  echo "ERROR: generated bundle not found: $OUT_DIR/vmess-bundle.txt" >&2
  echo "Run python3 frontier-edge/generate.py first." >&2
  exit 1
fi
if [ "$EDGE_ENABLE_HY2" = "1" ] && [ ! -f "$OUT_DIR/hy2-bundle.txt" ]; then
  echo "ERROR: generated HY2 bundle not found: $OUT_DIR/hy2-bundle.txt" >&2
  echo "Run python3 frontier-edge/generate.py with EDGE_ENABLE_HY2=1 first." >&2
  exit 1
fi

BASE="${SUBSTORE_PUBLIC_BASE_URL%/}"
API_BASE="${BASE}${SUBSTORE_BACKEND_PATH}"

check_existing_sub() {
  local sub_name="$1"
  echo "[check] existing sub: $sub_name"
  local http_code
  http_code="$(curl -sS -o /dev/null -w "%{http_code}" "${API_BASE}/api/sub/${sub_name}")"
  if [ "$http_code" != "200" ]; then
    echo "ERROR: Sub-Store sub '$sub_name' is missing or inaccessible (HTTP $http_code)." >&2
    echo "Create it once in the Web panel as a local sub, then add it to the intended collection." >&2
    exit 2
  fi
}

echo "[preflight] target API: <redacted>/api"
curl -sS -o /dev/null "${API_BASE}/api/utils/env" || {
  echo "ERROR: cannot reach Sub-Store API. Check SUBSTORE_PUBLIC_BASE_URL/SUBSTORE_BACKEND_PATH." >&2
  exit 1
}
check_existing_sub "$EDGE_ROLE_SUB"
if [ "$EDGE_ENABLE_HY2" = "1" ]; then
  check_existing_sub "$EDGE_HY2_ROLE_SUB"
fi

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

patch_existing_sub() {
  local sub_name="$1"
  local bundle_path="$2"
  local purpose="$3"

  local payload
  payload="$(python3 - "$bundle_path" <<'PY'
import json
import sys
from pathlib import Path

print(json.dumps({"content": Path(sys.argv[1]).read_text(encoding="utf-8")}))
PY
)"

  echo "[patch] update existing sub content: $purpose"
  local patch_response
  patch_response="$(curl -sS -X PATCH \
    -H "Content-Type: application/json" \
    -d "$payload" \
    "${API_BASE}/api/sub/${sub_name}")"
  printf '%s' "$patch_response" | python3 -c '
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
}

patch_existing_sub "$EDGE_ROLE_SUB" "$OUT_DIR/vmess-bundle.txt" "VMess edge roles"
if [ "$EDGE_ENABLE_HY2" = "1" ]; then
  patch_existing_sub "$EDGE_HY2_ROLE_SUB" "$OUT_DIR/hy2-bundle.txt" "HY2 edge roles"
fi

echo
echo "PATCH complete. Refresh final profiles and validate with mihomo.exe -t."
