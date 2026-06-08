#!/usr/bin/env bash
# Deploy the generated shared-hy2 appliance to the San Jose VPS.
#
# This script intentionally keeps secrets in ignored local/remote private
# directories. It does not print generated HY2 links or Telegram values.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SHARED_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
REPO_ROOT="$(cd "$SHARED_DIR/.." && pwd)"
SECRETS_FILE="$SHARED_DIR/.secrets.local/shared-hy2.env"
OUT_DIR="$SHARED_DIR/.secrets.local/out"
ENV_FILES=()
ONLY_USERS=()
INSTALL_CRON=1

usage() {
  cat <<'USAGE'
Usage: bash shared-hy2/scripts/deploy-shared-hy2.sh [--env-file FILE] [--canary u01] [--no-cron]

Generates private artifacts, checks remote Docker/Compose/ports/certs, uploads
to REMOTE_DIR, installs management commands, opens only required UDP ports in
UFW, and starts the generated shared HY2 services.

Options:
  --env-file FILE  load env file; may be repeated, later files override earlier ones
  --canary USER    render and deploy only one user, normally u01
  --no-cron        upload and start services without installing cron entries
USAGE
}

shell_quote() {
  printf "%q" "$1"
}

pick_ssh() {
  local target="$1"
  local resolved
  if [ -n "${SSH_BIN:-}" ]; then
    echo "$SSH_BIN"
    return 0
  fi
  if command -v ssh >/dev/null 2>&1; then
    resolved="$(ssh -G "$target" 2>/dev/null | awk '$1=="hostname"{print $2; exit}' || true)"
  else
    resolved=""
  fi
  if [ -n "$resolved" ] && [ "$resolved" != "$target" ]; then
    echo ssh
    return 0
  fi
  if command -v ssh.exe >/dev/null 2>&1; then
    resolved="$(ssh.exe -G "$target" 2>/dev/null | awk '$1=="hostname"{print $2; exit}' || true)"
  else
    resolved=""
  fi
  if [ -n "$resolved" ] && [ "$resolved" != "$target" ]; then
    echo ssh.exe
    return 0
  fi
  echo ssh
}

find_python() {
  if command -v python3 >/dev/null 2>&1; then
    command -v python3
  elif command -v python >/dev/null 2>&1; then
    command -v python
  else
    echo "ERROR: python3/python not found" >&2
    exit 1
  fi
}

while [ "$#" -gt 0 ]; do
  case "$1" in
    --env-file)
      shift
      [ "$#" -gt 0 ] || { echo "ERROR: --env-file requires a path" >&2; exit 1; }
      ENV_FILES+=("$1")
      ;;
    --canary)
      shift
      [ "$#" -gt 0 ] || { echo "ERROR: --canary requires u01/u02/u03" >&2; exit 1; }
      case "$1" in
        u01|u02|u03) ONLY_USERS+=("$1") ;;
        *) echo "ERROR: invalid canary user: $1" >&2; exit 1 ;;
      esac
      ;;
    --no-cron) INSTALL_CRON=0 ;;
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
    echo "Copy shared-hy2/examples/shared-hy2.env.example first." >&2
    exit 1
  fi
done

PYTHON_BIN="$(find_python)"

while IFS= read -r assignment; do
  if [ -n "$assignment" ]; then
    eval "$assignment"
  fi
done < <("$PYTHON_BIN" - "${ENV_FILES[@]}" <<'PY'
import shlex
import sys

env = {}
for raw_path in sys.argv[1:]:
    with open(raw_path, encoding="utf-8-sig") as fh:
        for line in fh:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, value = line.split("=", 1)
            env[key.strip()] = value.strip().strip('"').strip("'")
for key, value in env.items():
    print(f"{key}={shlex.quote(value)}")
PY
)

SSH_TARGET="${SSH_TARGET:-sjc-snap}"
REMOTE_DIR="${REMOTE_DIR:-/opt/shared-hy2}"
SSH_BIN="$(pick_ssh "$SSH_TARGET")"
SSH_ARGS=(-o BatchMode=yes)
if [ -n "${SSH_PORT:-}" ]; then
  SSH_ARGS+=(-p "$SSH_PORT")
fi
if [ -n "${SSH_IDENTITY_FILE:-}" ]; then
  SSH_ARGS+=(-i "$SSH_IDENTITY_FILE")
fi

GEN_ENV_ARGS=()
GEN_ONLY_ARGS=()
for env_file in "${ENV_FILES[@]}"; do
  GEN_ENV_ARGS+=(--env-file "$env_file")
done
for user in "${ONLY_USERS[@]}"; do
  GEN_ONLY_ARGS+=(--only "$user")
done

cd "$REPO_ROOT"

echo "[1/5] Validate shared HY2 registry..."
"$PYTHON_BIN" "$SHARED_DIR/generate.py" --check "${GEN_ENV_ARGS[@]}" "${GEN_ONLY_ARGS[@]}"

echo
echo "[2/5] Generate private appliance artifacts..."
"$PYTHON_BIN" "$SHARED_DIR/generate.py" "${GEN_ENV_ARGS[@]}" "${GEN_ONLY_ARGS[@]}" --out-dir "$OUT_DIR"

for required in compose.yaml users.json secrets/shared-hy2.env config/acl.txt bin/shared-hy2-report bin/shared-hy2-monitor; do
  [ -e "$OUT_DIR/$required" ] || { echo "ERROR: missing generated artifact: $required" >&2; exit 1; }
done
cp "$SHARED_DIR/generate.py" "$OUT_DIR/generate.py"
cp "$SHARED_DIR/shared-users.tsv" "$OUT_DIR/shared-users.tsv"

REQUIRED_UDP_PORTS="$(
  "$PYTHON_BIN" - "$OUT_DIR/compose.yaml" <<'PY'
import re, sys
from pathlib import Path
text = Path(sys.argv[1]).read_text(encoding="utf-8")
ports = sorted({int(m.group(1)) for m in re.finditer(r'"(\d+):\d+/udp"', text)})
print(" ".join(str(p) for p in ports))
PY
)"
REQUIRED_STATS_PORTS="$(
  "$PYTHON_BIN" - "$OUT_DIR/compose.yaml" <<'PY'
import re, sys
from pathlib import Path
text = Path(sys.argv[1]).read_text(encoding="utf-8")
ports = sorted({int(m.group(1)) for m in re.finditer(r'"127\.0\.0\.1:(\d+):\d+"', text)})
print(" ".join(str(p) for p in ports))
PY
)"
GENERATED_USERS="$(
  "$PYTHON_BIN" - "$OUT_DIR/users.json" <<'PY'
import json, sys
print(" ".join(u["user_id"] for u in json.load(open(sys.argv[1], encoding="utf-8"))["users"]))
PY
)"

echo "  users: $GENERATED_USERS"
echo "  udp ports: $REQUIRED_UDP_PORTS"
echo "  stats ports: $REQUIRED_STATS_PORTS"

echo
echo "[3/5] Remote preflight..."
REMOTE_PREFLIGHT_CMD="REMOTE_DIR=$(shell_quote "$REMOTE_DIR") REQUIRED_UDP_PORTS=$(shell_quote "$REQUIRED_UDP_PORTS") REQUIRED_STATS_PORTS=$(shell_quote "$REQUIRED_STATS_PORTS") HOST_CERTIFICATE=$(shell_quote "${SHARED_HY2_HOST_CERTIFICATE:-}") HOST_PRIVATE_KEY=$(shell_quote "${SHARED_HY2_HOST_PRIVATE_KEY:-}") bash -s"
"$SSH_BIN" "${SSH_ARGS[@]}" "$SSH_TARGET" "$REMOTE_PREFLIGHT_CMD" <<'REMOTE'
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
if ! command -v python3 >/dev/null 2>&1; then
  echo "ERROR: python3 is required on remote host" >&2
  exit 1
fi

shared_owns_port() {
  local port="$1" proto="$2"
  local container
  for container in $(docker ps --format '{{.Names}}' | grep '^shared-hy2-' || true); do
    docker port "$container" 2>/dev/null | grep -Eq "^${port}/${proto} -> .*:${port}$" && return 0
  done
  return 1
}

for cert_path in "${HOST_CERTIFICATE:-}" "${HOST_PRIVATE_KEY:-}"; do
  if [ -z "$cert_path" ] || [ ! -r "$cert_path" ]; then
    echo "ERROR: configured HY2 certificate/key path is not readable on remote host." >&2
    exit 3
  fi
done

for port in ${REQUIRED_UDP_PORTS:-}; do
  if ss -lunH "sport = :$port" | grep -q . && ! shared_owns_port "$port" udp; then
    echo "ERROR: required shared HY2 UDP port is busy: $port" >&2
    ss -lunp "sport = :$port" 2>/dev/null || true
    exit 3
  fi
done
for port in ${REQUIRED_STATS_PORTS:-}; do
  if ss -ltnH "sport = :$port" | grep -q . && ! shared_owns_port "$port" tcp; then
    echo "ERROR: required shared HY2 Stats port is busy: $port" >&2
    ss -ltnp "sport = :$port" 2>/dev/null || true
    exit 3
  fi
done

mkdir -p "$REMOTE_DIR" "$REMOTE_DIR/secrets" /var/lib/shared-hy2 /var/log/shared-hy2 /usr/local/lib/shared-hy2
echo "  compose: $COMPOSE_CMD"
echo "  remote dir: $REMOTE_DIR"
REMOTE

echo
echo "[4/5] Upload artifacts..."
tar -C "$OUT_DIR" -cz compose.yaml users.json shared-users.tsv generate.py config secrets bin links \
  | "$SSH_BIN" "${SSH_ARGS[@]}" "$SSH_TARGET" "tar -xzf - -C '$REMOTE_DIR'"

echo
echo "[5/5] Install and start shared HY2..."
REMOTE_START_CMD="REMOTE_DIR=$(shell_quote "$REMOTE_DIR") REQUIRED_UDP_PORTS=$(shell_quote "$REQUIRED_UDP_PORTS") GENERATED_USERS=$(shell_quote "$GENERATED_USERS") INSTALL_CRON=$(shell_quote "$INSTALL_CRON") bash -s"
"$SSH_BIN" "${SSH_ARGS[@]}" "$SSH_TARGET" "$REMOTE_START_CMD" <<'REMOTE'
set -euo pipefail
cd "$REMOTE_DIR"

if docker compose version >/dev/null 2>&1; then
  COMPOSE=(docker compose -f "$REMOTE_DIR/compose.yaml")
else
  COMPOSE=(docker-compose -f "$REMOTE_DIR/compose.yaml")
fi

chmod 700 "$REMOTE_DIR/secrets" "$REMOTE_DIR/config" "$REMOTE_DIR/links" /var/lib/shared-hy2 /var/log/shared-hy2
chmod 600 "$REMOTE_DIR/shared-users.tsv" "$REMOTE_DIR/generate.py"
find "$REMOTE_DIR/secrets" "$REMOTE_DIR/config" "$REMOTE_DIR/links" -type f -exec chmod 600 {} \;
find "$REMOTE_DIR/bin" -type f -exec chmod 755 {} \;
install -m 755 "$REMOTE_DIR/bin/shared-hy2-lib" /usr/local/lib/shared-hy2/shared-hy2-lib
for cmd in shared-hy2-report shared-hy2-monitor shared-hy2-disable shared-hy2-enable shared-hy2-rotate shared-hy2-upgrade-all; do
  install -m 755 "$REMOTE_DIR/bin/$cmd" "/usr/local/bin/$cmd"
done

if command -v ufw >/dev/null 2>&1 && ufw status | grep -qi '^Status: active'; then
  for port in ${REQUIRED_UDP_PORTS:-}; do
    ufw allow "$port/udp" comment "shared-hy2" >/dev/null
  done
fi

if [ "${INSTALL_CRON:-1}" = "1" ]; then
  cat > /etc/cron.d/shared-hy2 <<'CRON'
SHELL=/bin/bash
PATH=/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin

*/10 * * * * root /usr/local/bin/shared-hy2-monitor >> /var/log/shared-hy2/monitor.log 2>&1
0 22 * * * root /usr/local/bin/shared-hy2-monitor --daily >> /var/log/shared-hy2/monitor.log 2>&1
20 9 * * * root /usr/local/bin/shared-hy2-upgrade-all >> /var/log/shared-hy2/upgrade.log 2>&1
CRON
  chmod 644 /etc/cron.d/shared-hy2
fi

if ! "${COMPOSE[@]}" config -q >/dev/null 2>&1; then
  "${COMPOSE[@]}" config >/dev/null
fi
"${COMPOSE[@]}" pull
"${COMPOSE[@]}" up -d

for user in ${GENERATED_USERS:-}; do
  status="$(docker inspect "shared-hy2-${user}" --format '{{.State.Status}}' 2>/dev/null || true)"
  if [ "$status" != "running" ]; then
    echo "ERROR: shared-hy2-${user} is not running" >&2
    docker logs --tail 80 "shared-hy2-${user}" 2>&1 || true
    exit 4
  fi
  port="$(python3 - "$REMOTE_DIR/users.json" "$user" <<'PY'
import json, sys
data=json.load(open(sys.argv[1], encoding="utf-8"))
for u in data["users"]:
    if u["user_id"] == sys.argv[2]:
        print(u["udp_port"])
        raise SystemExit(0)
raise SystemExit(2)
PY
)"
  stats_port="$(python3 - "$REMOTE_DIR/users.json" "$user" <<'PY'
import json, sys
data=json.load(open(sys.argv[1], encoding="utf-8"))
for u in data["users"]:
    if u["user_id"] == sys.argv[2]:
        print(u["stats_port"])
        raise SystemExit(0)
raise SystemExit(2)
PY
)"
  secret="$(awk -F= '$1=="SHARED_HY2_STATS_SECRET"{print substr($0, index($0,$2))}' "$REMOTE_DIR/secrets/shared-hy2.env")"
  curl -fsS -H "Authorization: $secret" "http://127.0.0.1:${stats_port}/online" >/dev/null
  ss -H -u -lpn | grep -q ":${port}" || {
    echo "ERROR: UDP port is not listening for ${user}" >&2
    exit 4
  }
done

/usr/local/bin/shared-hy2-report
REMOTE

echo
echo "shared-hy2 deploy complete."
echo "Private client files are under shared-hy2/.secrets.local/out/links locally and REMOTE_DIR/links on the VPS."
