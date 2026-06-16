#!/usr/bin/env python3
"""
frontier-edge/generate.py

Render a no-panel edge appliance from a small role registry:

  edge-roles.tsv -> mihomo/config.yaml, ingress config, compose.yaml, vmess-bundle.txt
  edge-hy2-roles.tsv -> mihomo/config.yaml UDP listeners, hy2-bundle.txt

Secrets live in frontier-edge/.secrets.local/edge-us.env by default and are never
written to git. The generated output also stays under .secrets.local/out.
"""

from __future__ import annotations

import argparse
import base64
import json
import re
import sys
import uuid
from pathlib import Path


EDGE_DIR = Path(__file__).resolve().parent
ROLES_PATH = EDGE_DIR / "edge-roles.tsv"
HY2_ROLES_PATH = EDGE_DIR / "edge-hy2-roles.tsv"
DEFAULT_OUT = EDGE_DIR / ".secrets.local" / "out"
DEFAULT_SECRETS = EDGE_DIR / ".secrets.local" / "edge-us.env"

MODES = {"direct", "url-test", "select"}
HY2_PROFILES = {"latency", "bandwidth", "parked"}
INGRESS_MODES = {"caddy", "openresty"}
DEFAULT_CADDY_LISTENER_BASE_PORT = 18443
DEFAULT_OPENRESTY_LISTENER_BASE_PORT = 19443
DEFAULT_CADDY_CONTROLLER_PORT = 9090
DEFAULT_OPENRESTY_CONTROLLER_PORT = 19092
DEFAULT_HY2_CERTIFICATE = "/root/.config/mihomo/certs/hy2-fullchain.pem"
DEFAULT_HY2_PRIVATE_KEY = "/root/.config/mihomo/certs/hy2-privkey.pem"
HY2_BANDWIDTH_CLIENT_UP = "100 Mbps"
HY2_BANDWIDTH_CLIENT_DOWN = "500 Mbps"
HY2_BANDWIDTH_SERVER_UP = HY2_BANDWIDTH_CLIENT_DOWN
HY2_BANDWIDTH_SERVER_DOWN = HY2_BANDWIDTH_CLIENT_UP
HY2_BANDWIDTH_STREAM_WINDOW = 26843545
HY2_BANDWIDTH_CONNECTION_WINDOW = 67108864
ROLE_ID_RE = re.compile(r"^[a-z0-9][a-z0-9-]{1,40}$")
REGION_RE = re.compile(r"^[A-Z0-9-]{2,16}$")
UUID_RE = re.compile(
    r"\b[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}\b"
)
SENSITIVE_RE = re.compile(
    r"(://|ss://|vmess://|vless://|trojan://|hysteria2://|hy2://|"
    r"backend[_-]?path|share[_-]?token|subscription|password|passwd|"
    r"uuid=|token=|secret=|private[_-]?key)",
    re.IGNORECASE,
)


def die(message: str) -> None:
    sys.stderr.write(f"ERROR: {message}\n")
    raise SystemExit(1)


def warn(message: str) -> None:
    sys.stderr.write(f"WARN: {message}\n")


def yaml_sq(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"


def read_env_file(path: Path) -> dict[str, str]:
    if not path.exists():
        die(
            f"secrets file not found: {path}\n"
            "Copy examples/edge-us.env.example to frontier-edge/.secrets.local/edge-us.env first."
        )

    env: dict[str, str] = {}
    for lineno, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        raw = line.strip()
        if not raw or raw.startswith("#"):
            continue
        if "=" not in raw:
            die(f"{path}:{lineno}: expected KEY=VALUE")
        key, value = raw.split("=", 1)
        env[key.strip()] = value.strip().strip('"').strip("'")
    return env


def load_env(paths: list[Path] | Path) -> dict[str, str]:
    if isinstance(paths, Path):
        paths = [paths]
    if not paths:
        paths = [DEFAULT_SECRETS]

    env: dict[str, str] = {}
    for path in paths:
        env.update(read_env_file(path))

    if "SUBSTORE_BACKEND_PATH" not in env and "BACKEND_PATH" in env:
        env["SUBSTORE_BACKEND_PATH"] = env["BACKEND_PATH"]

    required = [
        "EDGE_VM_UUID",
        "EDGE_WS_PATH",
        "EDGE_PUBLIC_HOST",
        "SUBSTORE_PUBLIC_BASE_URL",
        "SUBSTORE_BACKEND_PATH",
    ]
    missing = [key for key in required if not env.get(key)]
    if missing:
        die(f"missing secret keys: {', '.join(missing)}")

    try:
        uuid.UUID(env["EDGE_VM_UUID"])
    except ValueError as exc:
        die(f"EDGE_VM_UUID is not a valid UUID: {exc}")

    if not env["EDGE_WS_PATH"].startswith("/"):
        die("EDGE_WS_PATH must start with '/'")
    if "://" in env["EDGE_PUBLIC_HOST"] or "/" in env["EDGE_PUBLIC_HOST"]:
        die("EDGE_PUBLIC_HOST must be only a host name, not a URL")
    if not env["SUBSTORE_PUBLIC_BASE_URL"].startswith(("https://", "http://")):
        die("SUBSTORE_PUBLIC_BASE_URL must start with https:// or http://")
    if not env["SUBSTORE_BACKEND_PATH"].startswith("/"):
        die("SUBSTORE_BACKEND_PATH must start with '/'")

    env.setdefault("EDGE_INGRESS_MODE", "caddy")
    if env["EDGE_INGRESS_MODE"] not in INGRESS_MODES:
        die(f"EDGE_INGRESS_MODE must be one of {sorted(INGRESS_MODES)}")

    ingress_mode = env["EDGE_INGRESS_MODE"]
    default_listener_base = (
        DEFAULT_OPENRESTY_LISTENER_BASE_PORT
        if ingress_mode == "openresty"
        else DEFAULT_CADDY_LISTENER_BASE_PORT
    )
    default_controller_port = (
        DEFAULT_OPENRESTY_CONTROLLER_PORT
        if ingress_mode == "openresty"
        else DEFAULT_CADDY_CONTROLLER_PORT
    )

    env.setdefault("EDGE_LISTENER_BASE_PORT", str(default_listener_base))
    env.setdefault("EDGE_CONTROLLER_PORT", str(default_controller_port))
    env["EDGE_LISTENER_BASE_PORT"] = str(parse_port(env["EDGE_LISTENER_BASE_PORT"], "EDGE_LISTENER_BASE_PORT"))
    env["EDGE_CONTROLLER_PORT"] = str(parse_port(env["EDGE_CONTROLLER_PORT"], "EDGE_CONTROLLER_PORT"))

    env.setdefault("EDGE_UPSTREAM_COLLECTION", "edge-us-upstreams")
    env.setdefault("EDGE_ROLE_SUB", "edge-us-roles")
    env.setdefault("EDGE_CONTROLLER_SECRET", "frontier-edge-local-only")
    env.setdefault("EDGE_ENABLE_SUBSTORE_PROXY", "0")
    env.setdefault("EDGE_ENABLE_HOST_GATEWAY", "0")
    env.setdefault("EDGE_HOST_GATEWAY_IP", "host-gateway")
    env.setdefault("EDGE_ENABLE_HY2", "0")
    env.setdefault("EDGE_HY2_ROLE_SUB", "edge-us-hy2-roles")
    env.setdefault("EDGE_HY2_CERTIFICATE", DEFAULT_HY2_CERTIFICATE)
    env.setdefault("EDGE_HY2_PRIVATE_KEY", DEFAULT_HY2_PRIVATE_KEY)
    env.setdefault("EDGE_HY2_CLIENT_SNI", env["EDGE_PUBLIC_HOST"])
    env.setdefault("EDGE_HY2_CLIENT_SKIP_CERT_VERIFY", "false")
    env.setdefault("EDGE_LEGACY_HTTP_HOST", "")
    env.setdefault("EDGE_LEGACY_HTTP_UPSTREAM", "")
    env.setdefault("SUBSTORE_PROVIDER_BASE_URL", env["SUBSTORE_PUBLIC_BASE_URL"])
    env.setdefault("SUBSTORE_INTERNAL_UPSTREAM", "sub-store:3001")
    env.setdefault("EDGE_OPENRESTY_CONTAINER", "1Panel-openresty-kOZu")
    env.setdefault("EDGE_OPENRESTY_CONF_NAME", f"{env['EDGE_PUBLIC_HOST']}.conf")
    env["SUBSTORE_PUBLIC_BASE_URL"] = env["SUBSTORE_PUBLIC_BASE_URL"].rstrip("/")
    env["SUBSTORE_PROVIDER_BASE_URL"] = env["SUBSTORE_PROVIDER_BASE_URL"].rstrip("/")
    env["EDGE_WS_PATH"] = env["EDGE_WS_PATH"].rstrip("/")

    if ingress_mode == "openresty":
        required_openresty = [
            "EDGE_OPENRESTY_CONF_DIR",
            "EDGE_OPENRESTY_CERTIFICATE",
            "EDGE_OPENRESTY_CERTIFICATE_KEY",
        ]
        missing_openresty = [key for key in required_openresty if not env.get(key)]
        if missing_openresty:
            die(f"missing OpenResty-mode keys: {', '.join(missing_openresty)}")

    if env["EDGE_ENABLE_SUBSTORE_PROXY"] not in {"0", "1"}:
        die("EDGE_ENABLE_SUBSTORE_PROXY must be 0 or 1")
    if env["EDGE_ENABLE_HOST_GATEWAY"] not in {"0", "1"}:
        die("EDGE_ENABLE_HOST_GATEWAY must be 0 or 1")
    if env["EDGE_ENABLE_HY2"] not in {"0", "1"}:
        die("EDGE_ENABLE_HY2 must be 0 or 1")
    if env["EDGE_HY2_CLIENT_SKIP_CERT_VERIFY"].lower() not in {"true", "false"}:
        die("EDGE_HY2_CLIENT_SKIP_CERT_VERIFY must be true or false")
    if env["EDGE_ENABLE_HY2"] == "1":
        if not env.get("EDGE_HY2_PASSWORD"):
            die("EDGE_HY2_PASSWORD is required when EDGE_ENABLE_HY2=1")
        for key in ["EDGE_HY2_CERTIFICATE", "EDGE_HY2_PRIVATE_KEY"]:
            value = env[key]
            if not value.startswith("/") or re.search(r"[\s;{}]", value):
                die(f"{key} must be an absolute container path without whitespace or nginx metacharacters")
    if env["EDGE_ENABLE_HOST_GATEWAY"] == "1":
        gateway = env["EDGE_HOST_GATEWAY_IP"]
        if gateway != "host-gateway" and not re.match(r"^\d{1,3}(?:\.\d{1,3}){3}$", gateway):
            die("EDGE_HOST_GATEWAY_IP must be 'host-gateway' or an IPv4 address")
    if not env["SUBSTORE_PROVIDER_BASE_URL"].startswith(("https://", "http://")):
        die("SUBSTORE_PROVIDER_BASE_URL must start with https:// or http://")
    if env["EDGE_CONTROLLER_PORT"] == env["EDGE_LISTENER_BASE_PORT"]:
        die("EDGE_CONTROLLER_PORT must not equal EDGE_LISTENER_BASE_PORT")
    if "/" in env["EDGE_OPENRESTY_CONF_NAME"] or not env["EDGE_OPENRESTY_CONF_NAME"].endswith(".conf"):
        die("EDGE_OPENRESTY_CONF_NAME must be a simple .conf filename")
    for key in ["EDGE_OPENRESTY_CONF_DIR", "EDGE_OPENRESTY_CERTIFICATE", "EDGE_OPENRESTY_CERTIFICATE_KEY"]:
        value = env[key]
        if not value.startswith("/") or re.search(r"[\s;{}]", value):
            die(f"{key} must be an absolute path without whitespace or nginx metacharacters")
    if bool(env["EDGE_LEGACY_HTTP_HOST"]) != bool(env["EDGE_LEGACY_HTTP_UPSTREAM"]):
        die("EDGE_LEGACY_HTTP_HOST and EDGE_LEGACY_HTTP_UPSTREAM must be set together")
    if env["EDGE_LEGACY_HTTP_HOST"]:
        if "://" in env["EDGE_LEGACY_HTTP_HOST"] or "/" in env["EDGE_LEGACY_HTTP_HOST"]:
            die("EDGE_LEGACY_HTTP_HOST must be only a host name, not a URL")
        if env["EDGE_LEGACY_HTTP_HOST"] == env["EDGE_PUBLIC_HOST"]:
            die("EDGE_LEGACY_HTTP_HOST must not equal EDGE_PUBLIC_HOST")
        if "://" in env["EDGE_LEGACY_HTTP_UPSTREAM"] or "/" in env["EDGE_LEGACY_HTTP_UPSTREAM"]:
            die("EDGE_LEGACY_HTTP_UPSTREAM must be host:port, not a URL")
    return env


def parse_port(value: str, key: str) -> int:
    try:
        port = int(value)
    except ValueError:
        die(f"{key} must be an integer port")
    if port < 1 or port > 65535:
        die(f"{key} must be between 1 and 65535")
    return port


def scan_public_safe(value: str, path: Path, lineno: int, field: str) -> list[str]:
    errors: list[str] = []
    if UUID_RE.search(value):
        errors.append(f"{path}:{lineno}: {field} contains a UUID-like value")
    if SENSITIVE_RE.search(value):
        errors.append(f"{path}:{lineno}: {field} looks like it contains a secret or URL")
    return errors


def parse_roles(path: Path, listener_base_port: int = DEFAULT_CADDY_LISTENER_BASE_PORT) -> list[dict[str, object]]:
    if not path.exists():
        die(f"roles file not found: {path}")

    roles: list[dict[str, object]] = []
    errors: list[str] = []
    seen: set[str] = set()
    ports: set[int] = set()

    for lineno, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        raw = line.rstrip("\n")
        if not raw.strip() or raw.lstrip().startswith("#"):
            continue
        parts = [part.strip() for part in raw.split("\t")]
        if len(parts) != 5:
            errors.append(f"{path}:{lineno}: expected 5 TAB-separated fields, got {len(parts)}")
            continue

        role_id, mode, region, upstream_filter, display = parts
        for field, value in [
            ("role_id", role_id),
            ("region", region),
            ("upstream_filter", upstream_filter),
            ("client_display_name", display),
        ]:
            errors.extend(scan_public_safe(value, path, lineno, field))

        if not ROLE_ID_RE.match(role_id):
            errors.append(f"{path}:{lineno}: invalid role_id {role_id!r}; use lowercase letters, digits, and '-'")
        if role_id in seen:
            errors.append(f"{path}:{lineno}: duplicate role_id {role_id!r}")
        seen.add(role_id)

        if mode not in MODES:
            errors.append(f"{path}:{lineno}: invalid mode {mode!r}; expected one of {sorted(MODES)}")
        if not REGION_RE.match(region):
            errors.append(f"{path}:{lineno}: invalid region {region!r}; use uppercase tags such as US")
        if not display:
            errors.append(f"{path}:{lineno}: client_display_name must not be empty")

        if mode == "direct":
            if upstream_filter != "-":
                errors.append(f"{path}:{lineno}: direct roles must use '-' as upstream_filter")
        else:
            if not upstream_filter or upstream_filter == "-":
                errors.append(f"{path}:{lineno}: {mode} roles must define a non-empty upstream_filter")
            try:
                re.compile(upstream_filter)
            except re.error as exc:
                errors.append(f"{path}:{lineno}: upstream_filter is not a valid regex: {exc}")

        port = listener_base_port + len(roles)
        if port > 65535:
            errors.append(f"{path}:{lineno}: generated port {port} exceeds 65535")
        if port in ports:
            errors.append(f"{path}:{lineno}: duplicate generated port {port}")
        ports.add(port)

        roles.append(
            {
                "role_id": role_id,
                "mode": mode,
                "region": region,
                "upstream_filter": upstream_filter,
                "display": display,
                "port": port,
            }
        )

    if not roles:
        errors.append(f"{path}: no valid role rows")
    if errors:
        for error in errors:
            sys.stderr.write(error + "\n")
        raise SystemExit(1)
    return roles


def parse_hy2_roles(path: Path) -> list[dict[str, object]]:
    if not path.exists():
        die(f"HY2 roles file not found: {path}")

    roles: list[dict[str, object]] = []
    errors: list[str] = []
    seen: set[str] = set()
    ports: set[int] = set()

    for lineno, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        raw = line.rstrip("\n")
        if not raw.strip() or raw.lstrip().startswith("#"):
            continue
        parts = [part.strip() for part in raw.split("\t")]
        if len(parts) != 7:
            errors.append(f"{path}:{lineno}: expected 7 TAB-separated fields, got {len(parts)}")
            continue

        role_id, mode, region, udp_port_raw, upstream_filter, display, profile = parts
        for field, value in [
            ("role_id", role_id),
            ("region", region),
            ("upstream_filter", upstream_filter),
            ("client_display_name", display),
            ("profile", profile),
        ]:
            errors.extend(scan_public_safe(value, path, lineno, field))

        if not ROLE_ID_RE.match(role_id):
            errors.append(f"{path}:{lineno}: invalid role_id {role_id!r}; use lowercase letters, digits, and '-'")
        if role_id in seen:
            errors.append(f"{path}:{lineno}: duplicate role_id {role_id!r}")
        seen.add(role_id)

        if mode not in MODES:
            errors.append(f"{path}:{lineno}: invalid mode {mode!r}; expected one of {sorted(MODES)}")
        if not REGION_RE.match(region):
            errors.append(f"{path}:{lineno}: invalid region {region!r}; use uppercase tags such as US")
        if not display:
            errors.append(f"{path}:{lineno}: client_display_name must not be empty")
        if profile not in HY2_PROFILES:
            errors.append(f"{path}:{lineno}: invalid profile {profile!r}; expected one of {sorted(HY2_PROFILES)}")

        udp_port: int | None = None
        if profile == "parked":
            if udp_port_raw != "-":
                errors.append(f"{path}:{lineno}: parked HY2 roles must use '-' as udp_port")
        else:
            try:
                udp_port = int(udp_port_raw)
            except ValueError:
                errors.append(f"{path}:{lineno}: udp_port must be an integer port")
                continue
            if udp_port < 1 or udp_port > 65535:
                errors.append(f"{path}:{lineno}: udp_port must be between 1 and 65535")
            if udp_port in ports:
                errors.append(f"{path}:{lineno}: duplicate udp_port {udp_port}")
            ports.add(udp_port)

        if mode == "direct":
            if upstream_filter != "-":
                errors.append(f"{path}:{lineno}: direct HY2 roles must use '-' as upstream_filter")
        else:
            if not upstream_filter or upstream_filter == "-":
                errors.append(f"{path}:{lineno}: {mode} HY2 roles must define a non-empty upstream_filter")
            try:
                re.compile(upstream_filter)
            except re.error as exc:
                errors.append(f"{path}:{lineno}: upstream_filter is not a valid regex: {exc}")

        if profile == "parked":
            continue

        roles.append(
            {
                "role_id": role_id,
                "mode": mode,
                "region": region,
                "upstream_filter": upstream_filter,
                "display": display,
                "udp_port": udp_port,
                "profile": profile,
            }
        )

    if not roles:
        errors.append(f"{path}: no active HY2 role rows")
    if errors:
        for error in errors:
            sys.stderr.write(error + "\n")
        raise SystemExit(1)
    return roles


def role_path(ws_base: str, role_id: str) -> str:
    return f"{ws_base}-{role_id}"


def matcher_name(role_id: str) -> str:
    return "ws_" + role_id.replace("-", "_")


def gen_role_group(role: dict[str, object], provider_name: str) -> str:
    role_id = str(role["role_id"])
    mode = str(role["mode"])
    if mode == "direct":
        return f"""  - name: {role_id}-out
    type: select
    proxies:
      - DIRECT
"""
    if mode == "url-test":
        return f"""  - name: {role_id}-out
    type: url-test
    use:
      - {provider_name}
    filter: {yaml_sq(str(role['upstream_filter']))}
    url: http://cp.cloudflare.com/generate_204
    interval: 300
    tolerance: 50
    lazy: false
"""
    if mode == "select":
        return f"""  - name: {role_id}-out
    type: select
    use:
      - {provider_name}
    filter: {yaml_sq(str(role['upstream_filter']))}
"""
    die(f"unexpected mode {mode!r}")


def gen_hy2_profile_lines(role: dict[str, object], indent: str, endpoint: str) -> str:
    profile = str(role.get("profile", "latency"))
    if profile == "latency":
        return ""
    if profile == "bandwidth":
        if endpoint == "server":
            up = HY2_BANDWIDTH_SERVER_UP
            down = HY2_BANDWIDTH_SERVER_DOWN
        elif endpoint == "client":
            up = HY2_BANDWIDTH_CLIENT_UP
            down = HY2_BANDWIDTH_CLIENT_DOWN
        else:
            die(f"unexpected HY2 endpoint {endpoint!r}")
        lines = [
            f"{indent}up: {yaml_sq(up)}",
            f"{indent}down: {yaml_sq(down)}",
            f"{indent}initial-stream-receive-window: {HY2_BANDWIDTH_STREAM_WINDOW}",
            f"{indent}max-stream-receive-window: {HY2_BANDWIDTH_STREAM_WINDOW}",
            f"{indent}initial-connection-receive-window: {HY2_BANDWIDTH_CONNECTION_WINDOW}",
            f"{indent}max-connection-receive-window: {HY2_BANDWIDTH_CONNECTION_WINDOW}",
        ]
        return "\n".join(lines) + "\n"
    die(f"unexpected HY2 profile {profile!r}")


def gen_mihomo_config(
    roles: list[dict[str, object]],
    hy2_roles: list[dict[str, object]],
    env: dict[str, str],
    out_path: Path,
) -> None:
    provider_name = env["EDGE_UPSTREAM_COLLECTION"]
    upstream_url = (
        f"{env['SUBSTORE_PROVIDER_BASE_URL']}{env['SUBSTORE_BACKEND_PATH']}"
        f"/download/collection/{provider_name}?target=ClashMeta"
    )

    listeners: list[str] = []
    groups: list[str] = []
    for role in roles:
        role_id = str(role["role_id"])
        port = int(role["port"])
        listeners.append(
            f"""  - name: {role_id}
    type: vmess
    port: {port}
    listen: 0.0.0.0
    users:
      - username: edge
        uuid: {env['EDGE_VM_UUID']}
        alterId: 0
    ws-path: {role_path(env['EDGE_WS_PATH'], role_id)}
    proxy: {role_id}-out
"""
        )
        groups.append(gen_role_group(role, provider_name))

    for role in hy2_roles:
        role_id = str(role["role_id"])
        profile_lines = gen_hy2_profile_lines(role, "    ", "server")
        listeners.append(
            f"""  - name: {role_id}
    type: hysteria2
    port: {int(role['udp_port'])}
    listen: 0.0.0.0
    users:
      edge: {yaml_sq(env['EDGE_HY2_PASSWORD'])}
    certificate: {yaml_sq(env['EDGE_HY2_CERTIFICATE'])}
    private-key: {yaml_sq(env['EDGE_HY2_PRIVATE_KEY'])}
{profile_lines.rstrip()}
    proxy: {role_id}-out
"""
        )
        groups.append(gen_role_group(role, provider_name))

    config = f"""# Generated by frontier-edge/generate.py - DO NOT EDIT MANUALLY
# Source: frontier-edge/edge-roles.tsv ({len(roles)} VMess roles)
# HY2 roles: {len(hy2_roles)}

mixed-port: 0
log-level: info
external-controller: 0.0.0.0:{env['EDGE_CONTROLLER_PORT']}
secret: {yaml_sq(env['EDGE_CONTROLLER_SECRET'])}
allow-lan: false
ipv6: false

listeners:
{''.join(listeners)}
proxy-providers:
  # Target Sub-Store collection. Keep the collection itself US-edge scoped;
  # role-level filters decide which upstreams back each public role.
  {provider_name}:
    type: http
    url: {yaml_sq(upstream_url)}
    interval: 30
    path: ./data/providers/{provider_name}.yaml
    health-check:
      enable: true
      url: http://cp.cloudflare.com/generate_204
      interval: 300

proxy-groups:
{''.join(groups)}
rules:
  - MATCH,DIRECT
"""
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(config, encoding="utf-8")


def gen_caddyfile(roles: list[dict[str, object]], env: dict[str, str], out_path: Path) -> None:
    blocks = [
        "# Generated by frontier-edge/generate.py - DO NOT EDIT MANUALLY",
        f"{env['EDGE_PUBLIC_HOST']} {{",
        "    encode zstd gzip",
        "    header {",
        "        -Server",
        "    }",
        "",
    ]
    for role in roles:
        role_id = str(role["role_id"])
        path = role_path(env["EDGE_WS_PATH"], role_id)
        blocks.extend(
            [
                f"    handle {path} {{",
                f"        reverse_proxy mihomo:{int(role['port'])}",
                "    }",
                "",
            ]
        )
    if env.get("EDGE_ENABLE_SUBSTORE_PROXY") == "1":
        blocks.extend(
            [
                "    handle {",
                f"        reverse_proxy {env['SUBSTORE_INTERNAL_UPSTREAM']}",
                "    }",
                "}",
                "",
            ]
        )
    else:
        blocks.extend(["    handle {", "        respond 404", "    }", "}", ""])

    if env["EDGE_LEGACY_HTTP_HOST"]:
        blocks.extend(
            [
                f"{env['EDGE_LEGACY_HTTP_HOST']} {{",
                "    encode zstd gzip",
                "    header {",
                "        -Server",
                "    }",
                "",
                "    handle {",
                f"        reverse_proxy {env['EDGE_LEGACY_HTTP_UPSTREAM']} {{",
                "            header_up Host {host}",
                "            header_up X-Forwarded-Proto https",
                "        }",
                "    }",
                "}",
                "",
            ]
        )
    out_path.write_text("\n".join(blocks), encoding="utf-8")


def gen_openresty_config(roles: list[dict[str, object]], env: dict[str, str], out_path: Path) -> None:
    blocks = [
        "# Generated by frontier-edge/generate.py - DO NOT EDIT MANUALLY",
        "server {",
        "    listen 80;",
        "    listen 443 ssl http2;",
        f"    server_name {env['EDGE_PUBLIC_HOST']};",
        "",
        "    if ($scheme = http) {",
        "        return 301 https://$host$request_uri;",
        "    }",
        "",
        f"    ssl_certificate {env['EDGE_OPENRESTY_CERTIFICATE']};",
        f"    ssl_certificate_key {env['EDGE_OPENRESTY_CERTIFICATE_KEY']};",
        "    ssl_protocols TLSv1.3 TLSv1.2;",
        "    ssl_prefer_server_ciphers on;",
        "    ssl_session_cache shared:SSL:10m;",
        "    ssl_session_timeout 10m;",
        "    error_page 497 https://$host$request_uri;",
        "",
    ]
    for role in roles:
        role_id = str(role["role_id"])
        path = role_path(env["EDGE_WS_PATH"], role_id)
        blocks.extend(
            [
                f"    location ^~ {path} {{",
                '        if ($http_upgrade != "websocket") { return 404; }',
                f"        proxy_pass http://127.0.0.1:{int(role['port'])};",
                "        proxy_http_version 1.1;",
                "        proxy_set_header Upgrade $http_upgrade;",
                '        proxy_set_header Connection "upgrade";',
                "        proxy_set_header Host $host;",
                "        proxy_set_header X-Real-IP $remote_addr;",
                "        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;",
                "        proxy_set_header X-Forwarded-Proto https;",
                "        proxy_read_timeout 300s;",
                "        access_log off;",
                "    }",
                "",
            ]
        )

    blocks.extend(
        [
            "    location / {",
            "        return 404;",
            "    }",
            "}",
            "",
        ]
    )
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text("\n".join(blocks), encoding="utf-8")


def gen_compose(
    roles: list[dict[str, object]],
    hy2_roles: list[dict[str, object]],
    env: dict[str, str],
    out_path: Path,
) -> None:
    extra_service_network = ""
    extra_network_def = ""
    caddy_extra_hosts = ""
    substore_network = env.get("SUBSTORE_DOCKER_NETWORK", "").strip()
    if substore_network:
        extra_service_network = "      - sub-store\n"
        extra_network_def = f"""
  sub-store:
    external: true
    name: {substore_network}
"""
    if env.get("EDGE_ENABLE_HOST_GATEWAY") == "1" or "host.docker.internal" in env.get(
        "EDGE_LEGACY_HTTP_UPSTREAM", ""
    ):
        caddy_extra_hosts = (
            f'    extra_hosts:\n      - "host.docker.internal:{env["EDGE_HOST_GATEWAY_IP"]}"\n'
        )

    if env["EDGE_INGRESS_MODE"] == "openresty":
        published_ports = [f'      - "127.0.0.1:{int(role["port"])}:{int(role["port"])}"' for role in roles]
        published_ports.append(f'      - "127.0.0.1:{env["EDGE_CONTROLLER_PORT"]}:{env["EDGE_CONTROLLER_PORT"]}"')
        for role in hy2_roles:
            published_ports.append(f'      - "{int(role["udp_port"])}:{int(role["udp_port"])}/udp"')
        compose = f"""# Generated by frontier-edge/generate.py - DO NOT EDIT MANUALLY
services:
  mihomo:
    image: metacubex/mihomo:latest
    container_name: frontier-edge-mihomo
    restart: unless-stopped
    command:
      - -d
      - /root/.config/mihomo
      - -f
      - /root/.config/mihomo/config.yaml
    ports:
{chr(10).join(published_ports)}
    volumes:
      - ./mihomo:/root/.config/mihomo
    networks:
      - frontier-edge
{extra_service_network.rstrip()}

networks:
  frontier-edge:
    name: frontier-edge-net
{extra_network_def.rstrip()}
"""
    else:
        hy2_mihomo_ports = ""
        if hy2_roles:
            hy2_mihomo_ports = "    ports:\n" + "\n".join(
                f'      - "{int(role["udp_port"])}:{int(role["udp_port"])}/udp"' for role in hy2_roles
            ) + "\n"
        compose = f"""# Generated by frontier-edge/generate.py - DO NOT EDIT MANUALLY
services:
  caddy:
    image: caddy:2-alpine
    container_name: frontier-edge-caddy
    restart: unless-stopped
    depends_on:
      - mihomo
    ports:
      - "80:80"
      - "443:443"
    volumes:
      - ./Caddyfile:/etc/caddy/Caddyfile:ro
      - caddy_data:/data
      - caddy_config:/config
    networks:
      - frontier-edge
{extra_service_network.rstrip()}
{caddy_extra_hosts.rstrip()}

  mihomo:
    image: metacubex/mihomo:latest
    container_name: frontier-edge-mihomo
    restart: unless-stopped
    command:
      - -d
      - /root/.config/mihomo
      - -f
      - /root/.config/mihomo/config.yaml
{hy2_mihomo_ports.rstrip()}
    volumes:
      - ./mihomo:/root/.config/mihomo
    networks:
      - frontier-edge
{extra_service_network.rstrip()}

networks:
  frontier-edge:
    name: frontier-edge-net
{extra_network_def.rstrip()}

volumes:
  caddy_data:
  caddy_config:
"""
    out_path.write_text(compose, encoding="utf-8")


def gen_vmess_bundle(roles: list[dict[str, object]], env: dict[str, str], out_path: Path) -> None:
    links: list[str] = []
    for role in roles:
        role_id = str(role["role_id"])
        item = {
            "v": "2",
            "ps": role["display"],
            "add": env["EDGE_PUBLIC_HOST"],
            "port": "443",
            "id": env["EDGE_VM_UUID"],
            "aid": "0",
            "scy": "auto",
            "net": "ws",
            "type": "none",
            "host": env["EDGE_PUBLIC_HOST"],
            "path": role_path(env["EDGE_WS_PATH"], role_id),
            "tls": "tls",
            "sni": env["EDGE_PUBLIC_HOST"],
        }
        encoded = base64.b64encode(json.dumps(item, ensure_ascii=False).encode("utf-8")).decode("ascii")
        links.append("vmess://" + encoded)

    out_path.write_text("\n".join(links) + "\n", encoding="utf-8")


def gen_hy2_bundle(roles: list[dict[str, object]], env: dict[str, str], out_path: Path) -> None:
    skip_cert = env["EDGE_HY2_CLIENT_SKIP_CERT_VERIFY"].lower()
    lines = ["proxies:"]
    for role in roles:
        profile_lines = gen_hy2_profile_lines(role, "    ", "client")
        lines.extend(
            [
                f"  - name: {yaml_sq(str(role['display']))}",
                "    type: hysteria2",
                f"    server: {yaml_sq(env['EDGE_PUBLIC_HOST'])}",
                f"    port: {int(role['udp_port'])}",
                f"    password: {yaml_sq(env['EDGE_HY2_PASSWORD'])}",
                "    udp: true",
                f"    sni: {yaml_sq(env['EDGE_HY2_CLIENT_SNI'])}",
                f"    skip-cert-verify: {skip_cert}",
            ]
        )
        if profile_lines:
            lines.extend(profile_lines.rstrip().splitlines())
    out_path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def render(
    roles: list[dict[str, object]],
    hy2_roles: list[dict[str, object]],
    env: dict[str, str],
    out_dir: Path,
) -> None:
    out_dir.mkdir(parents=True, exist_ok=True)
    stale_caddyfile = out_dir / "Caddyfile"
    stale_openresty_file = out_dir / "openresty" / env["EDGE_OPENRESTY_CONF_NAME"]
    if env["EDGE_INGRESS_MODE"] == "openresty" and stale_caddyfile.exists():
        stale_caddyfile.unlink()
    if env["EDGE_INGRESS_MODE"] == "caddy" and stale_openresty_file.exists():
        stale_openresty_file.unlink()
    gen_mihomo_config(roles, hy2_roles, env, out_dir / "mihomo" / "config.yaml")
    if env["EDGE_INGRESS_MODE"] == "openresty":
        gen_openresty_config(roles, env, out_dir / "openresty" / env["EDGE_OPENRESTY_CONF_NAME"])
    else:
        gen_caddyfile(roles, env, out_dir / "Caddyfile")
    gen_compose(roles, hy2_roles, env, out_dir / "compose.yaml")
    gen_vmess_bundle(roles, env, out_dir / "vmess-bundle.txt")
    hy2_bundle = out_dir / "hy2-bundle.txt"
    if hy2_roles:
        gen_hy2_bundle(hy2_roles, env, hy2_bundle)
    elif hy2_bundle.exists():
        hy2_bundle.unlink()


def main() -> None:
    parser = argparse.ArgumentParser(description="Render Frontier Edge role appliance artifacts.")
    parser.add_argument("--roles-file", default=str(ROLES_PATH))
    parser.add_argument("--hy2-roles-file", default=str(HY2_ROLES_PATH))
    parser.add_argument(
        "--env-file",
        "--secrets-file",
        dest="secrets_files",
        action="append",
        default=[],
        help="env file to load; may be passed more than once, later files override earlier ones",
    )
    parser.add_argument("--out-dir", default=str(DEFAULT_OUT))
    parser.add_argument("--check", action="store_true", help="validate edge-roles.tsv; add --env-file to validate env too")
    args = parser.parse_args()

    roles_file = Path(args.roles_file)
    hy2_roles_file = Path(args.hy2_roles_file)
    if args.check:
        if args.secrets_files:
            env = load_env([Path(path) for path in args.secrets_files])
            roles = parse_roles(roles_file, int(env["EDGE_LISTENER_BASE_PORT"]))
            hy2_roles = parse_hy2_roles(hy2_roles_file) if env["EDGE_ENABLE_HY2"] == "1" else []
            print(
                f"OK: {len(roles)} edge VMess roles and {len(hy2_roles)} HY2 roles validated from {roles_file} "
                f"({env['EDGE_INGRESS_MODE']} mode)"
            )
        else:
            roles = parse_roles(roles_file)
            hy2_roles = parse_hy2_roles(hy2_roles_file)
            print(
                f"OK: {len(roles)} edge VMess roles and {len(hy2_roles)} active HY2 roles validated from "
                f"{roles_file} / {hy2_roles_file}"
            )
        return

    env = load_env([Path(path) for path in args.secrets_files] if args.secrets_files else DEFAULT_SECRETS)
    roles = parse_roles(roles_file, int(env["EDGE_LISTENER_BASE_PORT"]))
    hy2_roles = parse_hy2_roles(hy2_roles_file) if env["EDGE_ENABLE_HY2"] == "1" else []
    out_dir = Path(args.out_dir)
    render(roles, hy2_roles, env, out_dir)

    print(f"Generated {len(roles)} edge VMess roles:")
    for role in roles:
        print(f"  - {role['role_id']} ({role['mode']}) -> {role['display']} :{role['port']}")
    if hy2_roles:
        print(f"Generated {len(hy2_roles)} edge HY2 roles:")
        for role in hy2_roles:
            print(f"  - {role['role_id']} ({role['mode']}) -> {role['display']} :{role['udp_port']}/udp")
    print(f"Output dir: {out_dir}")
    print(f"Public host: {env['EDGE_PUBLIC_HOST']}")
    print(f"Ingress mode: {env['EDGE_INGRESS_MODE']}")
    print(f"Role sub: {env['EDGE_ROLE_SUB']}")
    if hy2_roles:
        print(f"HY2 role sub: {env['EDGE_HY2_ROLE_SUB']}")


if __name__ == "__main__":
    main()
