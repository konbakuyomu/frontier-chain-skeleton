#!/usr/bin/env python3
"""
Render the shared-hy2 no-panel appliance.

Generated artifacts and live secrets stay under shared-hy2/.secrets.local by
default. Git-tracked files must remain public-safe.
"""

from __future__ import annotations

import argparse
import copy
import json
import secrets
import shlex
import string
import sys
from pathlib import Path
from urllib.parse import quote


SHARED_DIR = Path(__file__).resolve().parent
USERS_PATH = SHARED_DIR / "shared-users.tsv"
DEFAULT_SECRETS = SHARED_DIR / ".secrets.local" / "shared-hy2.env"
EXAMPLE_SECRETS = SHARED_DIR / "examples" / "shared-hy2.env.example"
DEFAULT_OUT = SHARED_DIR / ".secrets.local" / "out"

USER_IDS = ("u01", "u02", "u03")
DEFAULT_IMAGE = "tobyxdd/hysteria:latest"
DEFAULT_REMOTE_DIR = "/opt/shared-hy2"
DEFAULT_QUOTA_GB = 200
DEFAULT_WARN_GB = 160
DEFAULT_CLIENT_DOWN = "100 Mbps"
DEFAULT_CLIENT_UP = "20 Mbps"
DEFAULT_MAX_ONLINE = 2

SENSITIVE_KEYS = (
    "PASSWORD",
    "TOKEN",
    "SECRET",
    "PRIVATE",
    "CHATID",
    "CERTIFICATE",
    "HOST",
)


def die(message: str) -> None:
    sys.stderr.write(f"ERROR: {message}\n")
    raise SystemExit(1)


def yaml_sq(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"


def mbps_value(value: str) -> int:
    normalized = value.strip().lower().replace("mbps", " mbps")
    parts = normalized.split()
    if len(parts) != 2 or parts[1] != "mbps":
        die(f"bandwidth value must look like '100 Mbps': {value}")
    try:
        mbps = int(parts[0])
    except ValueError:
        die(f"bandwidth value must use integer Mbps: {value}")
    if mbps <= 0:
        die(f"bandwidth value must be positive: {value}")
    return mbps


def read_env_file(path: Path) -> dict[str, str]:
    if not path.exists():
        die(f"env file not found: {path}")
    env: dict[str, str] = {}
    for lineno, line in enumerate(path.read_text(encoding="utf-8-sig").splitlines(), 1):
        raw = line.strip()
        if not raw or raw.startswith("#"):
            continue
        if "=" not in raw:
            die(f"{path}:{lineno}: expected KEY=VALUE")
        key, value = raw.split("=", 1)
        env[key.strip()] = value.strip().strip('"').strip("'")
    return env


def load_env(paths: list[Path]) -> dict[str, str]:
    env: dict[str, str] = {}
    for path in paths:
        env.update(read_env_file(path))

    env.setdefault("SSH_TARGET", "sjc-snap")
    env.setdefault("REMOTE_DIR", DEFAULT_REMOTE_DIR)
    env.setdefault("SHARED_HY2_IMAGE", DEFAULT_IMAGE)
    env.setdefault("SHARED_HY2_CLIENT_DOWN", DEFAULT_CLIENT_DOWN)
    env.setdefault("SHARED_HY2_CLIENT_UP", DEFAULT_CLIENT_UP)
    env.setdefault("SHARED_HY2_MONTHLY_QUOTA_GB", str(DEFAULT_QUOTA_GB))
    env.setdefault("SHARED_HY2_WARN_GB", str(DEFAULT_WARN_GB))
    env.setdefault("SHARED_HY2_MAX_ONLINE_DEVICES", str(DEFAULT_MAX_ONLINE))
    env.setdefault("SHARED_HY2_CLIENT_SKIP_CERT_VERIFY", "false")
    env.setdefault("TG_TOKEN", "")
    env.setdefault("TG_CHATID", "")
    env.setdefault("SHARED_HY2_ENABLE_DIUN", "auto")

    required = [
        "SHARED_HY2_PUBLIC_HOST",
        "SHARED_HY2_CLIENT_SNI",
        "SHARED_HY2_CERTIFICATE",
        "SHARED_HY2_PRIVATE_KEY",
        "SHARED_HY2_HOST_CERTIFICATE",
        "SHARED_HY2_HOST_PRIVATE_KEY",
        "SHARED_HY2_STATS_SECRET",
    ]
    for user_id in USER_IDS:
        required.append(f"SHARED_HY2_{user_id.upper()}_PASSWORD")

    missing = [key for key in required if not env.get(key) or env.get(key, "").startswith("replace-with-")]
    if missing:
        die(
            "missing private env keys: "
            + ", ".join(missing)
            + "\nRun: python3 shared-hy2/generate.py --init-secrets"
        )

    for key in [
        "SHARED_HY2_MONTHLY_QUOTA_GB",
        "SHARED_HY2_WARN_GB",
        "SHARED_HY2_MAX_ONLINE_DEVICES",
    ]:
        try:
            value = int(env[key])
        except ValueError:
            die(f"{key} must be an integer")
        if value < 0:
            die(f"{key} must be non-negative")

    if env["SHARED_HY2_CLIENT_SKIP_CERT_VERIFY"].lower() not in {"true", "false"}:
        die("SHARED_HY2_CLIENT_SKIP_CERT_VERIFY must be true or false")
    if env["SHARED_HY2_ENABLE_DIUN"].lower() not in {"auto", "true", "false"}:
        die("SHARED_HY2_ENABLE_DIUN must be auto, true, or false")
    if "://" in env["SHARED_HY2_PUBLIC_HOST"] or "/" in env["SHARED_HY2_PUBLIC_HOST"]:
        die("SHARED_HY2_PUBLIC_HOST must be a host name, not a URL")
    for key in [
        "SHARED_HY2_CERTIFICATE",
        "SHARED_HY2_PRIVATE_KEY",
        "SHARED_HY2_HOST_CERTIFICATE",
        "SHARED_HY2_HOST_PRIVATE_KEY",
    ]:
        if not env[key].startswith("/"):
            die(f"{key} must be an absolute path")

    return env


def random_secret(length: int = 32) -> str:
    alphabet = string.ascii_letters + string.digits
    return "".join(secrets.choice(alphabet) for _ in range(length))


def init_secrets(path: Path) -> None:
    if path.exists():
        env = read_env_file(path)
        text = path.read_text(encoding="utf-8-sig")
    else:
        path.parent.mkdir(parents=True, exist_ok=True)
        text = EXAMPLE_SECRETS.read_text(encoding="utf-8")
        env = {}

    replacements: dict[str, str] = {}
    for user_id in USER_IDS:
        key = f"SHARED_HY2_{user_id.upper()}_PASSWORD"
        if not env.get(key) or env.get(key, "").startswith("replace-with-"):
            replacements[key] = random_secret(28)
    if not env.get("SHARED_HY2_STATS_SECRET") or env.get("SHARED_HY2_STATS_SECRET", "").startswith("replace-with-"):
        replacements["SHARED_HY2_STATS_SECRET"] = random_secret(40)

    for key, value in replacements.items():
        if f"{key}=" in text:
            lines = []
            for line in text.splitlines():
                if line.startswith(f"{key}="):
                    lines.append(f"{key}={value}")
                else:
                    lines.append(line)
            text = "\n".join(lines) + "\n"
        else:
            text += f"{key}={value}\n"
    path.write_text(text, encoding="utf-8", newline="\n")
    print(f"Initialized private env: {path}")


def parse_users(path: Path) -> list[dict[str, object]]:
    if not path.exists():
        die(f"users file not found: {path}")
    users: list[dict[str, object]] = []
    seen_ids: set[str] = set()
    seen_ports: set[int] = set()
    errors: list[str] = []

    for lineno, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        raw = line.strip()
        if not raw or raw.startswith("#"):
            continue
        parts = [part.strip() for part in line.rstrip("\n").split("\t")]
        if len(parts) != 5:
            errors.append(f"{path}:{lineno}: expected 5 TAB-separated fields")
            continue
        user_id, udp_raw, stats_raw, display, enabled_raw = parts
        if user_id not in USER_IDS:
            errors.append(f"{path}:{lineno}: unexpected user_id {user_id!r}")
        if user_id in seen_ids:
            errors.append(f"{path}:{lineno}: duplicate user_id {user_id}")
        seen_ids.add(user_id)
        try:
            udp_port = int(udp_raw)
            stats_port = int(stats_raw)
        except ValueError:
            errors.append(f"{path}:{lineno}: ports must be integers")
            continue
        for port in (udp_port, stats_port):
            if port < 1 or port > 65535:
                errors.append(f"{path}:{lineno}: invalid port {port}")
            if port in seen_ports:
                errors.append(f"{path}:{lineno}: duplicate port {port}")
            seen_ports.add(port)
        if enabled_raw not in {"0", "1"}:
            errors.append(f"{path}:{lineno}: enabled must be 0 or 1")
        users.append(
            {
                "user_id": user_id,
                "udp_port": udp_port,
                "stats_port": stats_port,
                "display": display,
                "enabled": enabled_raw == "1",
            }
        )

    missing = sorted(set(USER_IDS) - seen_ids)
    if missing:
        errors.append(f"{path}: missing users: {', '.join(missing)}")
    if errors:
        die("\n".join(errors))
    return users


def filter_users(users: list[dict[str, object]], only: list[str]) -> list[dict[str, object]]:
    if not only:
        return users
    wanted = set(only)
    known = {str(user["user_id"]) for user in users}
    unknown = sorted(wanted - known)
    if unknown:
        die(f"unknown --only users: {', '.join(unknown)}")
    return [user for user in users if str(user["user_id"]) in wanted]


def bytes_per_month(quota_gb: int) -> int:
    return quota_gb * 1024 * 1024 * 1024


def gen_acl(out_path: Path) -> None:
    # Conservative baseline: block obvious abuse and local networks. Domain-based
    # BT/PT blocking is best-effort and intentionally not exhaustive.
    lines = [
        "# Generated by shared-hy2/generate.py - DO NOT EDIT MANUALLY",
        "reject(10.0.0.0/8)",
        "reject(172.16.0.0/12)",
        "reject(192.168.0.0/16)",
        "reject(127.0.0.0/8)",
        "reject(169.254.0.0/16)",
        "reject(fc00::/7)",
        "reject(fe80::/10)",
        "reject(all, tcp/25)",
        "reject(all, tcp/465)",
        "reject(all, tcp/587)",
        "reject(all, tcp/6881-6999)",
        "reject(all, udp/6881-6999)",
        "reject(suffix:tracker.openbittorrent.com)",
        "reject(suffix:opentrackr.org)",
        "reject(suffix:open.tracker.cl)",
        "reject(suffix:tracker.internetwarriors.net)",
        "direct(all)",
    ]
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text("\n".join(lines) + "\n", encoding="utf-8", newline="\n")


def gen_server_config(user: dict[str, object], env: dict[str, str], out_path: Path) -> None:
    user_id = str(user["user_id"])
    password = env[f"SHARED_HY2_{user_id.upper()}_PASSWORD"]
    server_up = env["SHARED_HY2_CLIENT_DOWN"]
    server_down = env["SHARED_HY2_CLIENT_UP"]
    config = f"""# Generated by shared-hy2/generate.py - DO NOT EDIT MANUALLY
listen: :{int(user['udp_port'])}

tls:
  cert: {yaml_sq(env['SHARED_HY2_CERTIFICATE'])}
  key: {yaml_sq(env['SHARED_HY2_PRIVATE_KEY'])}
  sniGuard: dns-san

auth:
  type: userpass
  userpass:
    {user_id}: {yaml_sq(password)}

bandwidth:
  up: {yaml_sq(server_up)}
  down: {yaml_sq(server_down)}

ignoreClientBandwidth: false

sniff:
  enable: true
  timeout: 2s
  rewriteDomain: false
  tcpPorts: 80,443,8000-9000
  udpPorts: all

acl:
  file: /etc/hysteria/acl.txt
  geoUpdateInterval: 168h

trafficStats:
  listen: :{int(user['stats_port'])}
  secret: {yaml_sq(env['SHARED_HY2_STATS_SECRET'])}

masquerade:
  type: string
  string:
    statusCode: 404
    content: "not found"
"""
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(config, encoding="utf-8", newline="\n")


def gen_client_files(user: dict[str, object], env: dict[str, str], out_dir: Path) -> None:
    user_id = str(user["user_id"])
    password = env[f"SHARED_HY2_{user_id.upper()}_PASSWORD"]
    auth = f"{user_id}:{password}"
    host = env["SHARED_HY2_PUBLIC_HOST"]
    port = int(user["udp_port"])
    sni = env["SHARED_HY2_CLIENT_SNI"]
    insecure = env["SHARED_HY2_CLIENT_SKIP_CERT_VERIFY"].lower() == "true"
    client_yaml = f"""# Generated private Hysteria2 client config for {user_id}
server: {yaml_sq(f'{host}:{port}')}
auth: {yaml_sq(auth)}
bandwidth:
  up: {yaml_sq(env['SHARED_HY2_CLIENT_UP'])}
  down: {yaml_sq(env['SHARED_HY2_CLIENT_DOWN'])}
tls:
  sni: {yaml_sq(sni)}
  insecure: {'true' if insecure else 'false'}
socks5:
  listen: 127.0.0.1:1080
"""
    mihomo_yaml = f"""proxies:
  - name: {yaml_sq(str(user['display']))}
    type: hysteria2
    server: {yaml_sq(host)}
    port: {port}
    password: {yaml_sq(auth)}
    udp: true
    sni: {yaml_sq(sni)}
    skip-cert-verify: {'true' if insecure else 'false'}
    up: {yaml_sq(env['SHARED_HY2_CLIENT_UP'])}
    down: {yaml_sq(env['SHARED_HY2_CLIENT_DOWN'])}
"""
    mihomo_profile_yaml = f"""# Generated private Mihomo profile for {user_id}
# Import this whole file into Clash Verge / Sparkle / other Mihomo clients.
allow-lan: false
mode: rule
log-level: warning
ipv6: false

proxies:
  - name: {yaml_sq(str(user['display']))}
    type: hysteria2
    server: {yaml_sq(host)}
    port: {port}
    password: {yaml_sq(auth)}
    udp: true
    sni: {yaml_sq(sni)}
    skip-cert-verify: {'true' if insecure else 'false'}
    up: {yaml_sq(env['SHARED_HY2_CLIENT_UP'])}
    down: {yaml_sq(env['SHARED_HY2_CLIENT_DOWN'])}

proxy-groups:
  - name: PROXY
    type: select
    proxies:
      - {yaml_sq(str(user['display']))}

rules:
  - IP-CIDR,10.0.0.0/8,DIRECT,no-resolve
  - IP-CIDR,172.16.0.0/12,DIRECT,no-resolve
  - IP-CIDR,192.168.0.0/16,DIRECT,no-resolve
  - IP-CIDR,127.0.0.0/8,DIRECT,no-resolve
  - IP-CIDR,169.254.0.0/16,DIRECT,no-resolve
  - IP-CIDR6,fc00::/7,DIRECT,no-resolve
  - IP-CIDR6,fe80::/10,DIRECT,no-resolve
  - MATCH,PROXY
"""
    sing_box_config = {
        "log": {"level": "warn"},
        "inbounds": [
            {
                "type": "mixed",
                "tag": "mixed-in",
                "listen": "127.0.0.1",
                "listen_port": 1080,
            }
        ],
        "outbounds": [
            {
                "type": "hysteria2",
                "tag": "proxy",
                "server": host,
                "server_port": port,
                "password": auth,
                "up_mbps": mbps_value(env["SHARED_HY2_CLIENT_UP"]),
                "down_mbps": mbps_value(env["SHARED_HY2_CLIENT_DOWN"]),
                "tls": {
                    "enabled": True,
                    "server_name": sni,
                    "insecure": insecure,
                },
            },
            {"type": "direct", "tag": "direct"},
        ],
        "route": {
            "rules": [
                {"ip_cidr": ["10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16", "127.0.0.0/8", "169.254.0.0/16", "fc00::/7", "fe80::/10"], "outbound": "direct"}
            ],
            "final": "proxy",
        },
    }
    sing_box_v2rayn_config = copy.deepcopy(sing_box_config)
    sing_box_v2rayn_config["inbounds"][0]["listen_port"] = 10808
    query = f"sni={quote(sni)}&insecure={'1' if insecure else '0'}"
    uri = f"hysteria2://{quote(auth, safe='')}@{host}:{port}/?{query}#{quote(str(user['display']))}\n"
    user_dir = out_dir / user_id
    user_dir.mkdir(parents=True, exist_ok=True)
    (user_dir / "hysteria-client.yaml").write_text(client_yaml, encoding="utf-8", newline="\n")
    (user_dir / "mihomo.yaml").write_text(mihomo_yaml, encoding="utf-8", newline="\n")
    (user_dir / "mihomo-profile.yaml").write_text(mihomo_profile_yaml, encoding="utf-8", newline="\n")
    (user_dir / "sing-box-client.json").write_text(json.dumps(sing_box_config, ensure_ascii=False, indent=2) + "\n", encoding="utf-8", newline="\n")
    (user_dir / "sing-box-v2rayn.json").write_text(json.dumps(sing_box_v2rayn_config, ensure_ascii=False, indent=2) + "\n", encoding="utf-8", newline="\n")
    (user_dir / "uri.txt").write_text(uri, encoding="utf-8", newline="\n")


def gen_compose(users: list[dict[str, object]], env: dict[str, str], out_path: Path) -> None:
    services: list[str] = ["# Generated by shared-hy2/generate.py - DO NOT EDIT MANUALLY", "services:"]
    for user in users:
        user_id = str(user["user_id"])
        if not bool(user["enabled"]):
            continue
        services.append(
            f"""  {user_id}:
    image: {env['SHARED_HY2_IMAGE']}
    container_name: shared-hy2-{user_id}
    restart: unless-stopped
    command: ["server", "-c", "/etc/hysteria/hysteria.yaml"]
    ports:
      - "{int(user['udp_port'])}:{int(user['udp_port'])}/udp"
      - "127.0.0.1:{int(user['stats_port'])}:{int(user['stats_port'])}"
    volumes:
      - ./config/{user_id}/hysteria.yaml:/etc/hysteria/hysteria.yaml:ro
      - ./config/acl.txt:/etc/hysteria/acl.txt:ro
      - {env['SHARED_HY2_HOST_CERTIFICATE']}:{env['SHARED_HY2_CERTIFICATE']}:ro
      - {env['SHARED_HY2_HOST_PRIVATE_KEY']}:{env['SHARED_HY2_PRIVATE_KEY']}:ro
      - ./data/{user_id}:/var/lib/hysteria
    labels:
      - diun.enable=true
      - autoupgrade.enable=true
      - autoupgrade.strategy=full
      - autoupgrade.max-backups=2
      - autoupgrade.min-disk-gb=5
      - autoupgrade.healthcheck-sec=60
      - shared-hy2.user={user_id}
"""
        )
    enable_diun = env.get("SHARED_HY2_ENABLE_DIUN", "auto").lower()
    should_emit_diun = enable_diun == "true" or (
        enable_diun == "auto" and bool(env.get("TG_TOKEN")) and bool(env.get("TG_CHATID"))
    )
    if should_emit_diun:
        services.append(
            """  diun:
    image: crazymax/diun:latest
    container_name: shared-hy2-diun
    restart: unless-stopped
    command: serve
    env_file:
      - ./secrets/shared-hy2.env
    environment:
      - TZ=Asia/Shanghai
      - LOG_LEVEL=info
      - DIUN_WATCH_WORKERS=5
      - DIUN_WATCH_SCHEDULE=0 */6 * * *
      - DIUN_PROVIDERS_DOCKER=true
      - DIUN_PROVIDERS_DOCKER_WATCHBYDEFAULT=false
    volumes:
      - /var/run/docker.sock:/var/run/docker.sock:ro
      - ./data/diun:/data
    labels:
      - diun.enable=false
"""
        )
    out_path.write_text("\n".join(services) + "\n", encoding="utf-8", newline="\n")


def gen_private_env(env: dict[str, str], out_path: Path) -> None:
    keys = [
        "SSH_TARGET",
        "REMOTE_DIR",
        "SHARED_HY2_IMAGE",
        "SHARED_HY2_STATS_SECRET",
        "SHARED_HY2_PUBLIC_HOST",
        "SHARED_HY2_CLIENT_SNI",
        "SHARED_HY2_CLIENT_SKIP_CERT_VERIFY",
        "SHARED_HY2_CERTIFICATE",
        "SHARED_HY2_PRIVATE_KEY",
        "SHARED_HY2_HOST_CERTIFICATE",
        "SHARED_HY2_HOST_PRIVATE_KEY",
        "SHARED_HY2_CLIENT_DOWN",
        "SHARED_HY2_CLIENT_UP",
        "SHARED_HY2_MONTHLY_QUOTA_GB",
        "SHARED_HY2_WARN_GB",
        "SHARED_HY2_MAX_ONLINE_DEVICES",
        "SHARED_HY2_ENABLE_DIUN",
        "TG_TOKEN",
        "TG_CHATID",
    ]
    for user_id in USER_IDS:
        keys.append(f"SHARED_HY2_{user_id.upper()}_PASSWORD")
    lines = ["# Generated private env for remote management. Do not commit."]
    for key in keys:
        lines.append(f"{key}={shlex.quote(env.get(key, ''))}")
    if env.get("TG_TOKEN") and env.get("TG_CHATID"):
        lines.append(f"DIUN_NOTIF_TELEGRAM_TOKEN={shlex.quote(env.get('TG_TOKEN', ''))}")
        lines.append(f"DIUN_NOTIF_TELEGRAM_CHATIDS={shlex.quote(env.get('TG_CHATID', ''))}")
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text("\n".join(lines) + "\n", encoding="utf-8", newline="\n")


def gen_users_json(users: list[dict[str, object]], env: dict[str, str], out_path: Path) -> None:
    data = {
        "quota_gb": int(env["SHARED_HY2_MONTHLY_QUOTA_GB"]),
        "warn_gb": int(env["SHARED_HY2_WARN_GB"]),
        "max_online_devices": int(env["SHARED_HY2_MAX_ONLINE_DEVICES"]),
        "users": users,
    }
    out_path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8", newline="\n")


def gen_management_scripts(out_dir: Path) -> None:
    bin_dir = out_dir / "bin"
    bin_dir.mkdir(parents=True, exist_ok=True)
    scripts = {
        "shared-hy2-lib": LIB_SH,
        "shared-hy2-report": REPORT_SH,
        "shared-hy2-monitor": MONITOR_SH,
        "shared-hy2-disable": DISABLE_SH,
        "shared-hy2-enable": ENABLE_SH,
        "shared-hy2-rotate": ROTATE_SH,
        "shared-hy2-streams": STREAMS_SH,
        "shared-hy2-upgrade-all": UPGRADE_ALL_SH,
    }
    for name, content in scripts.items():
        path = bin_dir / name
        path.write_text(content, encoding="utf-8", newline="\n")
        try:
            path.chmod(0o755)
        except OSError:
            pass


LIB_SH = r'''#!/usr/bin/env bash
set -euo pipefail

SHARED_HY2_HOME="${SHARED_HY2_HOME:-/opt/shared-hy2}"
SHARED_HY2_STATE="${SHARED_HY2_STATE:-/var/lib/shared-hy2}"
SHARED_HY2_LOG="${SHARED_HY2_LOG:-/var/log/shared-hy2}"
SHARED_HY2_ENV="$SHARED_HY2_HOME/secrets/shared-hy2.env"
SHARED_HY2_USERS="$SHARED_HY2_HOME/users.json"

if [ -f "$SHARED_HY2_ENV" ]; then
  set -a
  # shellcheck disable=SC1090
  . "$SHARED_HY2_ENV"
  set +a
fi

mkdir -p "$SHARED_HY2_STATE" "$SHARED_HY2_LOG"

compose_cmd() {
  if docker compose version >/dev/null 2>&1; then
    docker compose -f "$SHARED_HY2_HOME/compose.yaml" "$@"
  else
    docker-compose -f "$SHARED_HY2_HOME/compose.yaml" "$@"
  fi
}

all_users() {
  python3 - "$SHARED_HY2_USERS" <<'PY'
import json, sys
data=json.load(open(sys.argv[1], encoding='utf-8'))
print(" ".join(u["user_id"] for u in data["users"]))
PY
}

user_field() {
  local user="$1" field="$2"
  python3 - "$SHARED_HY2_USERS" "$user" "$field" <<'PY'
import json, sys
data=json.load(open(sys.argv[1], encoding='utf-8'))
for u in data["users"]:
    if u["user_id"] == sys.argv[2]:
        print(u[sys.argv[3]])
        raise SystemExit(0)
raise SystemExit(2)
PY
}

stats_get() {
  local user="$1" endpoint="$2"
  local port
  port="$(user_field "$user" stats_port)"
  curl -fsS -H "Authorization: ${SHARED_HY2_STATS_SECRET:?missing stats secret}" \
    "http://127.0.0.1:${port}${endpoint}"
}

tg_send() {
  local text="$1"
  if [ -z "${TG_TOKEN:-}" ] || [ -z "${TG_CHATID:-}" ]; then
    return 0
  fi
  curl -fsS -X POST "https://api.telegram.org/bot${TG_TOKEN}/sendMessage" \
    --data-urlencode "chat_id=${TG_CHATID}" \
    --data-urlencode "text=${text}" >/dev/null 2>>"$SHARED_HY2_LOG/telegram.log" || true
}
'''


REPORT_SH = r'''#!/usr/bin/env bash
set -euo pipefail
. /usr/local/lib/shared-hy2/shared-hy2-lib

target="${1:-}"
users="$(all_users)"
if [ -n "$target" ]; then
  users="$target"
fi
live_file="$(mktemp)"
trap 'rm -f "$live_file"' EXIT

for user in $users; do
  if [ -f "$SHARED_HY2_STATE/disabled-${user}" ]; then
    traffic='{}'
    online='{}'
    state='disabled'
  else
    traffic="$(stats_get "$user" '/traffic' 2>/dev/null || echo '{}')"
    online="$(stats_get "$user" '/online' 2>/dev/null || echo '{}')"
    state="$(docker inspect "shared-hy2-${user}" --format '{{.State.Status}}' 2>/dev/null || echo missing)"
  fi
  python3 - "$user" "$traffic" "$online" "$state" >> "$live_file" <<'PY'
import json, sys
user=sys.argv[1]
traffic=json.loads(sys.argv[2] or "{}")
online=json.loads(sys.argv[3] or "{}")
state=sys.argv[4]
entry={}
if isinstance(traffic, dict):
    entry = traffic.get(user) or (next(iter(traffic.values())) if traffic else {})
online_count=0
if isinstance(online, dict):
    online_count = int(online.get(user, next(iter(online.values()), 0)) if online else 0)
print(json.dumps({
    "user": user,
    "rx": int(entry.get("rx", 0)) if isinstance(entry, dict) else 0,
    "tx": int(entry.get("tx", 0)) if isinstance(entry, dict) else 0,
    "online": online_count,
    "state": state,
}, ensure_ascii=False))
PY
done

python3 - "$SHARED_HY2_USERS" "$SHARED_HY2_STATE/usage-ledger.jsonl" "$users" "$live_file" <<'PY'
import json, sys, time
from datetime import datetime, timezone

users_cfg = json.load(open(sys.argv[1], encoding="utf-8"))
ledger_path = sys.argv[2]
wanted = sys.argv[3].split()
live_path = sys.argv[4]
quota = int(users_cfg["quota_gb"])
warn = int(users_cfg["warn_gb"])
max_online = int(users_cfg["max_online_devices"])
month = datetime.now().strftime("%Y-%m")
today = datetime.now().strftime("%Y-%m-%d")
summary = {u: {"today": 0, "month": 0, "online": 0, "rx": 0, "tx": 0, "state": "unknown", "high_samples": 0} for u in wanted}
try:
    with open(ledger_path, encoding="utf-8") as fh:
        for line in fh:
            if not line.strip():
                continue
            row=json.loads(line)
            u=row.get("user")
            if u not in summary:
                continue
            total=int(row.get("rx",0))+int(row.get("tx",0))
            if str(row.get("date","")).startswith(month):
                summary[u]["month"] += total
            if row.get("date") == today:
                summary[u]["today"] += total
                if total >= 5 * 1024 * 1024 * 1024:
                    summary[u]["high_samples"] += 1
            summary[u]["online"] = max(summary[u]["online"], int(row.get("online", 0)))
            summary[u]["rx"] += int(row.get("rx",0))
            summary[u]["tx"] += int(row.get("tx",0))
except FileNotFoundError:
    pass
try:
    with open(live_path, encoding="utf-8") as fh:
        for line in fh:
            if not line.strip():
                continue
            row=json.loads(line)
            u=row.get("user")
            if u not in summary:
                continue
            live_total=int(row.get("rx",0))+int(row.get("tx",0))
            summary[u]["today"] += live_total
            summary[u]["month"] += live_total
            summary[u]["online"] = int(row.get("online", 0))
            summary[u]["rx"] += int(row.get("rx",0))
            summary[u]["tx"] += int(row.get("tx",0))
            summary[u]["state"] = row.get("state", "unknown")
except FileNotFoundError:
    pass

def gb(n): return n / 1024 / 1024 / 1024
print(f"Shared HY2 usage - {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
print("")
print(f"{'USER':<5} {'TODAY':>9} {'MONTH':>10} {'ONLINE':>7} {'STATE':>9} STATUS")
for u in wanted:
    s=summary.setdefault(u, {"today":0,"month":0,"online":0,"rx":0,"tx":0,"state":"unknown","high_samples":0})
    flags=[]
    if s["state"] not in {"running", "disabled"}:
        flags.append("NOT_RUNNING")
    if gb(s["month"]) >= quota:
        flags.append("OVER_QUOTA")
    elif gb(s["month"]) >= warn:
        flags.append("WARN_QUOTA")
    if s["online"] > max_online:
        flags.append("MANY_DEVICES")
    if s["tx"] > max(s["rx"], 1) * 0.8 and gb(s["tx"]) > 5:
        flags.append("HIGH_UPLOAD")
    if s["high_samples"] >= 2:
        flags.append("HIGH_RATE")
    status="OK" if not flags else ",".join(flags)
    print(f"{u:<5} {gb(s['today']):8.1f}G {gb(s['month']):9.1f}G {s['online']:7d} {s['state']:>9} {status}")
PY
'''


MONITOR_SH = r'''#!/usr/bin/env bash
set -euo pipefail
. /usr/local/lib/shared-hy2/shared-hy2-lib

daily=0
if [ "${1:-}" = "--daily" ]; then
  daily=1
fi

date_str="$(date +%F)"
ts="$(date -Is)"
tmp_report="$(mktemp)"
trap 'rm -f "$tmp_report"' EXIT

for user in $(all_users); do
  if [ -f "$SHARED_HY2_STATE/disabled-${user}" ]; then
    continue
  fi
  traffic="$(stats_get "$user" '/traffic?clear=1' || echo '{}')"
  online="$(stats_get "$user" '/online' || echo '{}')"
  python3 - "$user" "$date_str" "$ts" "$traffic" "$online" >> "$SHARED_HY2_STATE/usage-ledger.jsonl" <<'PY'
import json, sys
user, date, ts = sys.argv[1], sys.argv[2], sys.argv[3]
traffic=json.loads(sys.argv[4] or "{}")
online=json.loads(sys.argv[5] or "{}")
entry={}
if isinstance(traffic, dict):
    entry = traffic.get(user) or (next(iter(traffic.values())) if traffic else {})
online_count=0
if isinstance(online, dict):
    online_count = int(online.get(user, next(iter(online.values()), 0)) if online else 0)
row={
  "ts": ts,
  "date": date,
  "user": user,
  "rx": int(entry.get("rx", 0)) if isinstance(entry, dict) else 0,
  "tx": int(entry.get("tx", 0)) if isinstance(entry, dict) else 0,
  "online": online_count,
}
print(json.dumps(row, ensure_ascii=False))
PY
done

/usr/local/bin/shared-hy2-report > "$tmp_report"
if [ "$daily" = "1" ]; then
  tg_send "[Shared-HY2] Daily report
$(cat "$tmp_report")"
  cat "$tmp_report"
  exit 0
fi

alerts="$(grep -E 'WARN_QUOTA|OVER_QUOTA|MANY_DEVICES|HIGH_UPLOAD|HIGH_RATE|NOT_RUNNING' "$tmp_report" || true)"
if [ -n "$alerts" ]; then
  tg_send "[Shared-HY2] Alert
$alerts"
fi
cat "$tmp_report"
'''


DISABLE_SH = r'''#!/usr/bin/env bash
set -euo pipefail
. /usr/local/lib/shared-hy2/shared-hy2-lib
user="${1:?usage: shared-hy2-disable u01}"
compose_cmd stop "$user"
touch "$SHARED_HY2_STATE/disabled-${user}"
tg_send "[Shared-HY2] Disabled ${user}"
'''


ENABLE_SH = r'''#!/usr/bin/env bash
set -euo pipefail
. /usr/local/lib/shared-hy2/shared-hy2-lib
user="${1:?usage: shared-hy2-enable u01}"
rm -f "$SHARED_HY2_STATE/disabled-${user}"
compose_cmd up -d "$user"
tg_send "[Shared-HY2] Enabled ${user}"
'''


ROTATE_SH = r'''#!/usr/bin/env bash
set -euo pipefail
. /usr/local/lib/shared-hy2/shared-hy2-lib
user="${1:?usage: shared-hy2-rotate u01}"
case " $(all_users) " in
  *" ${user} "*) ;;
  *) echo "ERROR: unknown or inactive shared-hy2 user: ${user}" >&2; exit 2 ;;
esac

generator="$SHARED_HY2_HOME/generate.py"
users_tsv="$SHARED_HY2_HOME/shared-users.tsv"
if [ ! -f "$generator" ] || [ ! -f "$users_tsv" ]; then
  echo "ERROR: missing remote generator files under $SHARED_HY2_HOME" >&2
  exit 3
fi

only_args=()
for active_user in $(all_users); do
  only_args+=(--only "$active_user")
done

python3 "$generator" --env-file "$SHARED_HY2_ENV" --rotate "$user" >/dev/null
python3 "$generator" --env-file "$SHARED_HY2_ENV" --users-file "$users_tsv" --out-dir "$SHARED_HY2_HOME" "${only_args[@]}" >/dev/null
chmod 700 "$SHARED_HY2_HOME/secrets" "$SHARED_HY2_HOME/config" "$SHARED_HY2_HOME/links"
find "$SHARED_HY2_HOME/secrets" "$SHARED_HY2_HOME/config" "$SHARED_HY2_HOME/links" -type f -exec chmod 600 {} \;
set -a
# shellcheck disable=SC1090
. "$SHARED_HY2_ENV"
set +a

compose_cmd up -d --force-recreate "$user" >/dev/null
ok=0
for _attempt in $(seq 1 45); do
  if stats_get "$user" "/online" >/dev/null 2>>"$SHARED_HY2_LOG/healthcheck.log"; then
    ok=1
    break
  fi
  sleep 2
done
if [ "$ok" != "1" ]; then
  echo "ERROR: ${user} Stats API did not become healthy after rotation" >&2
  exit 4
fi
tg_send "[Shared-HY2] Rotated ${user}"
echo "Rotated ${user}. New private client files are under: $SHARED_HY2_HOME/links/${user}/"
'''


STREAMS_SH = r'''#!/usr/bin/env bash
set -euo pipefail
. /usr/local/lib/shared-hy2/shared-hy2-lib
user="${1:?usage: shared-hy2-streams u01}"
payload="$(stats_get "$user" "/dump/streams" 2>/dev/null || echo '{}')"
python3 - "$payload" <<'PY'
import json, sys
try:
    data = json.loads(sys.argv[1] or "{}")
except json.JSONDecodeError:
    print("No active stream data")
    raise SystemExit(0)
streams = data.get("streams", []) if isinstance(data, dict) else []
print(f"Active streams: {len(streams)}")
print(f"{'AUTH':<5} {'STATE':<8} {'TX':>10} {'RX':>10} {'REQ':<32} HOOKED")
for row in streams[:80]:
    print(
        f"{str(row.get('auth','')):<5} "
        f"{str(row.get('state','')):<8} "
        f"{int(row.get('tx',0)):>10} "
        f"{int(row.get('rx',0)):>10} "
        f"{str(row.get('req_addr',''))[:32]:<32} "
        f"{str(row.get('hooked_req_addr',''))[:80]}"
    )
PY
'''


UPGRADE_ALL_SH = r'''#!/usr/bin/env bash
set -euo pipefail
. /usr/local/lib/shared-hy2/shared-hy2-lib

users="$*"
if [ -z "$users" ]; then
  users="$(all_users)"
fi

verify_user() {
  local user="$1"
  local status port
  sleep 8
  status="$(docker inspect "shared-hy2-${user}" --format '{{.State.Status}}' 2>/dev/null || true)"
  if [ "$status" != "running" ]; then
    echo "${user}: container status is ${status:-missing}" >&2
    return 1
  fi
  stats_get "$user" "/online" >/dev/null || {
    echo "${user}: Stats API failed" >&2
    return 1
  }
  port="$(user_field "$user" udp_port)"
  ss -H -u -lpn | grep -q ":${port}" || {
    echo "${user}: UDP port ${port} is not listening" >&2
    return 1
  }
}

for user in $(all_users); do
  case " $users " in
    *" $user "*) ;;
    *) continue ;;
  esac
  if [ -f "$SHARED_HY2_STATE/disabled-${user}" ]; then
    echo "${user}: disabled; skipping"
    continue
  fi
  before="$(docker inspect "shared-hy2-${user}" --format '{{.Image}}' 2>/dev/null || true)"
  image_ref="$(docker inspect "shared-hy2-${user}" --format '{{.Config.Image}}' 2>/dev/null || true)"
  rollback_tag=""
  if [ -n "$before" ] && [ -n "$image_ref" ]; then
    rollback_tag="${image_ref%:*}:shared-hy2-rollback-${user}"
    docker rmi "$rollback_tag" >/dev/null 2>&1 || true
    docker tag "$before" "$rollback_tag" >/dev/null 2>&1 || rollback_tag=""
  fi
  tg_send "[Shared-HY2] Upgrading ${user}"
  if ! compose_cmd pull "$user"; then
    tg_send "[Shared-HY2] Pull failed for ${user}; stopping upgrade chain"
    exit 1
  fi
  if ! compose_cmd up -d "$user"; then
    tg_send "[Shared-HY2] Recreate failed for ${user}; stopping upgrade chain"
    exit 1
  fi
  if ! verify_user "$user"; then
    tg_send "[Shared-HY2] ${user} health failed; attempting image rollback"
    if [ -n "$rollback_tag" ] && [ -n "$image_ref" ]; then
      docker tag "$rollback_tag" "$image_ref" >/dev/null
      compose_cmd up -d "$user" || true
      if verify_user "$user"; then
        tg_send "[Shared-HY2] ${user} rolled back; stopping upgrade chain"
      else
        tg_send "[Shared-HY2] ${user} rollback failed; manual repair needed"
      fi
    fi
    exit 1
  fi
  after="$(docker inspect "shared-hy2-${user}" --format '{{.Image}}' 2>/dev/null || true)"
  if [ "$before" = "$after" ]; then
    echo "${user}: already up to date"
  else
    tg_send "[Shared-HY2] ${user} upgraded"
  fi
done
'''


def render(users: list[dict[str, object]], env: dict[str, str], out_dir: Path) -> None:
    out_dir.mkdir(parents=True, exist_ok=True)
    gen_acl(out_dir / "config" / "acl.txt")
    for user in users:
        gen_server_config(user, env, out_dir / "config" / str(user["user_id"]) / "hysteria.yaml")
        gen_client_files(user, env, out_dir / "links")
    gen_compose(users, env, out_dir / "compose.yaml")
    gen_private_env(env, out_dir / "secrets" / "shared-hy2.env")
    gen_users_json(users, env, out_dir / "users.json")
    gen_management_scripts(out_dir)


def check_public_safe(paths: list[Path]) -> None:
    # Lightweight guard: generated public files should not contain common secret markers.
    for path in paths:
        if not path.exists():
            continue
        text = path.read_text(encoding="utf-8", errors="ignore")
        for marker in ("hysteria2://", "SHARED_HY2_U01_PASSWORD=", "TG_TOKEN=", "TG_CHATID="):
            if marker in text:
                die(f"public tracked file contains private marker {marker!r}: {path}")


def main() -> None:
    parser = argparse.ArgumentParser(description="Render shared HY2 appliance artifacts.")
    parser.add_argument("--users-file", default=str(USERS_PATH))
    parser.add_argument("--env-file", action="append", default=[])
    parser.add_argument("--out-dir", default=str(DEFAULT_OUT))
    parser.add_argument("--check", action="store_true")
    parser.add_argument("--init-secrets", action="store_true")
    parser.add_argument("--rotate", choices=USER_IDS)
    parser.add_argument("--only", action="append", choices=USER_IDS, default=[])
    args = parser.parse_args()

    env_paths = [Path(p) for p in args.env_file] or [DEFAULT_SECRETS]
    if args.init_secrets:
        init_secrets(env_paths[0])
        if not args.check and not args.rotate:
            return

    if args.rotate:
        path = env_paths[0]
        if not path.exists():
            die(f"env file not found: {path}")
        key = f"SHARED_HY2_{args.rotate.upper()}_PASSWORD"
        lines = []
        found = False
        for line in path.read_text(encoding="utf-8-sig").splitlines():
            if line.startswith(f"{key}="):
                lines.append(f"{key}={random_secret(28)}")
                found = True
            else:
                lines.append(line)
        if not found:
            lines.append(f"{key}={random_secret(28)}")
        path.write_text("\n".join(lines) + "\n", encoding="utf-8", newline="\n")
        print(f"Rotated {args.rotate} password in private env: {path}")
        return

    users = filter_users(parse_users(Path(args.users_file)), args.only)
    env = load_env(env_paths)
    if args.check:
        print(f"OK: {len(users)} shared HY2 users validated from {args.users_file}")
        return
    render(users, env, Path(args.out_dir))
    print(f"Generated shared HY2 appliance under {args.out_dir}")


if __name__ == "__main__":
    main()
