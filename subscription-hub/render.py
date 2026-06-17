#!/usr/bin/env python3
"""Render the private subscription entry hub.

The source tree contains templates and placeholder config only. Live secrets
stay in a root-only env file on SJC.
"""

from __future__ import annotations

import argparse
import base64
import datetime as dt
import hashlib
import html
import io
import json
import os
import re
import sys
from pathlib import Path
from urllib.parse import urlparse


ROOT = Path(__file__).resolve().parents[1]
HUB_DIR = Path(__file__).resolve().parent
DEFAULT_TEMPLATE = HUB_DIR / "templates" / "index.html"
DEFAULT_SHADOWROCKET_CONF = ROOT / "shadowrocket.conf"
DEFAULT_RULE_REGISTRY = HUB_DIR / "rules.json"
DEFAULT_RULE_MIRROR = HUB_DIR / "rule_mirror.py"

REQUIRED_KEYS = [
    "HUB_PUBLIC_BASE_URL",
    "HUB_PAGE_PATH",
    "HUB_SPARKLE_PATH",
    "HUB_SHADOWROCKET_CONFIG_PATH",
    "HUB_IOS_ORDINARY_PATH",
    "HUB_IOS_HY2_PATH",
    "SUBSTORE_LOCAL_BASE_URL",
    "SUBSTORE_BACKEND_PATH",
    "SUBSTORE_MIHOMO_FILE",
    "SUBSTORE_IOS_ORDINARY_COLLECTION",
    "SUBSTORE_IOS_HY2_COLLECTION",
]

SECRETISH_KEYS = {
    "SUBSTORE_BACKEND_PATH",
    "HUB_SPARKLE_PATH",
    "HUB_SHADOWROCKET_CONFIG_PATH",
    "HUB_IOS_ORDINARY_PATH",
    "HUB_IOS_HY2_PATH",
    "HUB_BASIC_AUTH_PASSWORD",
}


def parse_env(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    if not path.exists():
        raise SystemExit(f"missing env file: {path}")
    for line_no, raw_line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if "=" not in line:
            raise SystemExit(f"invalid env line {line_no}: missing '='")
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if not re.fullmatch(r"[A-Z0-9_]+", key):
            raise SystemExit(f"invalid env key on line {line_no}: {key}")
        values[key] = value
    for key in REQUIRED_KEYS:
        if not values.get(key):
            raise SystemExit(f"missing required env key: {key}")
    return values


def ensure_safe_path(value: str, key: str, *, allow_placeholder: bool) -> str:
    if not value.startswith("/"):
        raise SystemExit(f"{key} must start with '/'")
    if re.search(r"[\s'\";`]", value):
        raise SystemExit(f"{key} contains unsafe characters")
    if not allow_placeholder and ("<" in value or ">" in value):
        raise SystemExit(f"{key} still contains placeholder text")
    return value.rstrip("/") if value != "/" else value


def normalize_base_url(value: str) -> str:
    if not value.startswith(("https://", "http://")):
        raise SystemExit("HUB_PUBLIC_BASE_URL must start with https:// or http://")
    return value.rstrip("/")


def public_url(base: str, path: str) -> str:
    return base + path


def public_host(env: dict[str, str]) -> str:
    parsed = urlparse(normalize_base_url(env["HUB_PUBLIC_BASE_URL"]))
    if not parsed.hostname:
        raise SystemExit("HUB_PUBLIC_BASE_URL must include a host")
    return parsed.hostname


def public_hosts(env: dict[str, str]) -> list[str]:
    hosts = [public_host(env)]
    aliases = env.get("HUB_PUBLIC_HOST_ALIASES", "")
    for alias in re.split(r"[\s,]+", aliases.strip()):
        if not alias:
            continue
        if not re.fullmatch(r"[A-Za-z0-9.-]+", alias):
            raise SystemExit("HUB_PUBLIC_HOST_ALIASES contains an unsafe host")
        hosts.append(alias.lower())
    deduped: list[str] = []
    for host in hosts:
        normalized = host.lower()
        if normalized not in deduped:
            deduped.append(normalized)
    return deduped


def substore_url(env: dict[str, str], suffix: str) -> str:
    base = env["SUBSTORE_LOCAL_BASE_URL"].rstrip("/")
    backend = ensure_safe_path(env["SUBSTORE_BACKEND_PATH"], "SUBSTORE_BACKEND_PATH", allow_placeholder=True)
    return base + backend + suffix


def rule_registry(path: Path) -> dict[str, object]:
    try:
        registry = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise SystemExit(f"missing rule registry: {path}") from exc
    items = registry.get("items")
    if not isinstance(items, list) or not items:
        raise SystemExit("rule registry must contain non-empty items[]")
    seen_ids: set[str] = set()
    seen_files: set[str] = set()
    for item in items:
        if not isinstance(item, dict):
            raise SystemExit("rule registry item must be an object")
        rule_id = str(item.get("id") or "")
        filename = str(item.get("file") or "")
        source = str(item.get("source") or "")
        client = str(item.get("client") or "")
        if not re.fullmatch(r"[a-z0-9][a-z0-9-]*", rule_id):
            raise SystemExit(f"invalid rule id: {rule_id}")
        if rule_id in seen_ids:
            raise SystemExit(f"duplicate rule id: {rule_id}")
        seen_ids.add(rule_id)
        if not re.fullmatch(r"[A-Za-z0-9._-]+", filename):
            raise SystemExit(f"invalid rule filename: {filename}")
        if filename in seen_files:
            raise SystemExit(f"duplicate rule filename: {filename}")
        seen_files.add(filename)
        if not source.startswith("https://"):
            raise SystemExit(f"rule source must be https for {rule_id}")
        if client not in {"shadowrocket", "mihomo"}:
            raise SystemExit(f"invalid rule client for {rule_id}: {client}")
    return registry


def rule_path_prefix(registry: dict[str, object]) -> str:
    value = str(registry.get("rules_path_prefix") or "/rules")
    return ensure_safe_path(value, "rules_path_prefix", allow_placeholder=False)


def rule_mirror_url_map(env: dict[str, str], registry: dict[str, object]) -> dict[str, str]:
    base = normalize_base_url(env["HUB_PUBLIC_BASE_URL"])
    prefix = rule_path_prefix(registry).rstrip("/")
    mapping: dict[str, str] = {}
    for item in registry["items"]:
        source = str(item["source"])
        filename = str(item["file"])
        target = public_url(base, prefix + "/" + filename)
        mapping[source] = target
        mapping["https://link.konbakuyomu.us" + prefix + "/" + filename] = target
    return mapping


def rewrite_rule_urls(text: str, mapping: dict[str, str]) -> str:
    out = text
    for source, target in sorted(mapping.items(), key=lambda pair: len(pair[0]), reverse=True):
        out = out.replace(source, target)
    return out


def endpoint_defs(env: dict[str, str]) -> list[dict[str, str]]:
    base = normalize_base_url(env["HUB_PUBLIC_BASE_URL"])
    paths = {
        "sparkle": ensure_safe_path(env["HUB_SPARKLE_PATH"], "HUB_SPARKLE_PATH", allow_placeholder=True),
        "sr_config": ensure_safe_path(env["HUB_SHADOWROCKET_CONFIG_PATH"], "HUB_SHADOWROCKET_CONFIG_PATH", allow_placeholder=True),
        "ios_ordinary": ensure_safe_path(env["HUB_IOS_ORDINARY_PATH"], "HUB_IOS_ORDINARY_PATH", allow_placeholder=True),
        "ios_hy2": ensure_safe_path(env["HUB_IOS_HY2_PATH"], "HUB_IOS_HY2_PATH", allow_placeholder=True),
    }
    return [
        {
            "id": "sparkle",
            "group": "mihomo",
            "title": "Sparkle / FlClash / OpenClash",
            "description": "完整规则和节点配置，给 Mihomo 系客户端使用。",
            "url": public_url(base, paths["sparkle"]),
            "path": paths["sparkle"],
            "target": substore_url(env, f"/api/file/{env['SUBSTORE_MIHOMO_FILE']}?target=ClashMeta"),
            "kind": "proxy",
        },
        {
            "id": "sr-config",
            "group": "shadowrocket",
            "title": "Shadowrocket 配置订阅",
            "description": "只管规则和分组；改配置后仍复制这一条稳定链接。",
            "url": public_url(base, paths["sr_config"]),
            "path": paths["sr_config"],
            "target": "static:shadowrocket.conf",
            "kind": "static",
        },
        {
            "id": "ios-ordinary",
            "group": "shadowrocket",
            "title": "Shadowrocket 普通节点",
            "description": "普通机场、家宽、VLESS/VMess 等节点；不含 HY2。",
            "url": public_url(base, paths["ios_ordinary"]),
            "path": paths["ios_ordinary"],
            "target": substore_url(env, f"/download/collection/{env['SUBSTORE_IOS_ORDINARY_COLLECTION']}?target=URI"),
            "kind": "proxy",
        },
        {
            "id": "ios-hy2",
            "group": "shadowrocket",
            "title": "Shadowrocket HY2 节点",
            "description": "只放 HY2 节点，和普通节点订阅分开刷新。",
            "url": public_url(base, paths["ios_hy2"]),
            "path": paths["ios_hy2"],
            "target": substore_url(env, f"/download/collection/{env['SUBSTORE_IOS_HY2_COLLECTION']}?target=ShadowRocket"),
            "kind": "proxy",
        },
    ]


def status_for(endpoints: list[dict[str, str]]) -> dict[str, object]:
    now = dt.datetime.now(dt.timezone.utc).astimezone().isoformat(timespec="seconds")
    items = []
    for item in endpoints:
        safe_url_hash = hashlib.sha256(item["url"].encode("utf-8")).hexdigest()[:16]
        items.append(
            {
                "id": item["id"],
                "title": item["title"],
                "status": "generated",
                "url_hash": safe_url_hash,
                "last_checked": "",
                "bytes": None,
                "sha256": "",
            }
        )
    items.append(
        {
            "id": "rule-mirror",
            "title": "Third-party rule mirrors",
            "status": "generated",
            "url_hash": "",
            "last_checked": "",
            "bytes": None,
            "sha256": "",
            "shape": "",
        }
    )
    return {"generated_at": now, "verified_at": "not verified", "items": items}


def render_index(template_path: Path, endpoints: list[dict[str, str]], status: dict[str, object]) -> str:
    template = template_path.read_text(encoding="utf-8")
    replacements = {
        "{{generated_at}}": html.escape(str(status.get("generated_at") or "")),
        "{{verified_at}}": html.escape(str(status.get("verified_at") or "")),
        "{{links_path}}": "links.json",
        "{{status_path}}": "status.json",
    }
    for key, value in replacements.items():
        template = template.replace(key, value)
    return template


def qr_data_uri(value: str) -> str:
    try:
        import segno  # type: ignore
    except ImportError as exc:
        raise SystemExit("missing Python package 'segno'; run: python -m pip install segno") from exc
    return segno.make(value, error="m").svg_data_uri(scale=6, border=2, xmldecl=False)


def links_payload(endpoints: list[dict[str, str]], status: dict[str, object]) -> dict[str, object]:
    by_id = {str(item["id"]): item for item in status.get("items", [])}
    public_items = []
    for item in endpoints:
        state = by_id.get(item["id"], {})
        public_items.append(
            {
                "id": item["id"],
                "group": item["group"],
                "title": item["title"],
                "description": item["description"],
                "url": item["url"],
                "qr": qr_data_uri(item["url"]),
                "status": state.get("status") or "generated",
                "last_checked": state.get("last_checked") or "",
                "bytes": state.get("bytes"),
                "sha256": state.get("sha256") or "",
            }
        )
    return {
        "generated_at": status.get("generated_at") or "",
        "verified_at": status.get("verified_at") or "",
        "items": public_items,
    }


def rule_status_payload(registry: dict[str, object]) -> dict[str, object]:
    now = dt.datetime.now(dt.timezone.utc).astimezone().isoformat(timespec="seconds")
    items = []
    for item in registry["items"]:
        items.append(
            {
                "id": item["id"],
                "client": item["client"],
                "rule_type": item["rule_type"],
                "format": item["format"],
                "file": item["file"],
                "source_host": urlparse(str(item["source"])).hostname or "",
                "status": "generated",
                "http_status": None,
                "last_checked": "",
                "last_success": "",
                "bytes": None,
                "sha256": "",
                "error": "",
            }
        )
    return {
        "generated_at": now,
        "summary": {
            "total": len(items),
            "ok": 0,
            "stale": 0,
            "failed": 0,
        },
        "items": items,
    }


def nginx_location_for(env: dict[str, str], item: dict[str, str]) -> str:
    path = item["path"]
    if item["kind"] == "static":
        return f"""
location = {path} {{
    access_log off;
    add_header Cache-Control "no-store" always;
    add_header X-Content-Type-Options "nosniff" always;
    default_type text/plain;
    alias {env.get('HUB_REMOTE_ROOT', '/opt/frontier/subscription-hub')}/public/shadowrocket.conf;
}}
""".strip()
    return f"""
location = {path} {{
    access_log off;
    proxy_set_header Host $host;
    proxy_set_header X-Real-IP $remote_addr;
    proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
    proxy_set_header X-Forwarded-Proto $scheme;
    proxy_pass {item['target']};
}}
""".strip()


def nginx_fragment(env: dict[str, str], endpoints: list[dict[str, str]]) -> str:
    root = env.get("HUB_REMOTE_ROOT", "/opt/frontier/subscription-hub").rstrip("/")
    page_path = ensure_safe_path(env["HUB_PAGE_PATH"], "HUB_PAGE_PATH", allow_placeholder=True).rstrip("/")
    lines = [
        "# BEGIN subscription-hub managed block",
        f"location = / {{ return 302 {page_path}/; }}",
        f"location = {page_path} {{ return 302 {page_path}/; }}",
        f"location ^~ {page_path}/ {{",
        "    auth_basic \"Subscription Hub\";",
        f"    auth_basic_user_file {root}/secrets/htpasswd;",
        "    add_header Cache-Control \"no-store\" always;",
        f"    alias {root}/public/;",
        "    index index.html;",
        "    try_files $uri $uri/ /index.html;",
        "}",
    ]
    for item in endpoints:
        lines.append(nginx_location_for(env, item))
    lines.append("# END subscription-hub managed block")
    return "\n\n".join(lines) + "\n"


def caddy_handle_for(env: dict[str, str], item: dict[str, str]) -> str:
    path = item["path"]
    return f"""
handle {path} {{
    reverse_proxy {env.get('HUB_LOCAL_SERVICE_URL', 'http://127.0.0.1:19180').rstrip('/')}
}}
""".strip()


def caddy_fragment(env: dict[str, str], endpoints: list[dict[str, str]]) -> str:
    hosts = ", ".join(public_hosts(env))
    upstream = env.get("HUB_LOCAL_SERVICE_URL", "http://127.0.0.1:19180").rstrip("/")
    lines = [
        "# BEGIN subscription-hub managed block",
        f"{hosts} {{",
        "    encode zstd gzip",
        "    header Cache-Control \"no-store\"",
        "    header X-Content-Type-Options \"nosniff\"",
        f"    reverse_proxy {upstream}",
        "}",
    ]
    lines.append("# END subscription-hub managed block")
    return "\n\n".join(lines) + "\n"


def internal_caddyfile(env: dict[str, str], registry: dict[str, object]) -> str:
    page_path = ensure_safe_path(env["HUB_PAGE_PATH"], "HUB_PAGE_PATH", allow_placeholder=True).rstrip("/")
    sparkle_path = ensure_safe_path(env["HUB_SPARKLE_PATH"], "HUB_SPARKLE_PATH", allow_placeholder=True)
    sr_config_path = ensure_safe_path(env["HUB_SHADOWROCKET_CONFIG_PATH"], "HUB_SHADOWROCKET_CONFIG_PATH", allow_placeholder=True)
    ios_ordinary_path = ensure_safe_path(env["HUB_IOS_ORDINARY_PATH"], "HUB_IOS_ORDINARY_PATH", allow_placeholder=True)
    ios_hy2_path = ensure_safe_path(env["HUB_IOS_HY2_PATH"], "HUB_IOS_HY2_PATH", allow_placeholder=True)
    mihomo = env["SUBSTORE_MIHOMO_FILE"]
    ios_ordinary = env["SUBSTORE_IOS_ORDINARY_COLLECTION"]
    ios_hy2 = env["SUBSTORE_IOS_HY2_COLLECTION"]
    rules_prefix = rule_path_prefix(registry).rstrip("/")
    return f"""{{
    auto_https off
    admin off
}}

:19180 {{
    log {{
        output discard
    }}

    header Cache-Control "no-store"
    header X-Content-Type-Options "nosniff"

    handle /healthz {{
        respond "ok" 200
    }}

    @hubRoot path /
    handle @hubRoot {{
        redir * {page_path}/ 302
    }}

    handle {page_path} {{
        basic_auth {{
            {{$HUB_BASIC_AUTH_USER}} {{$HUB_BASIC_AUTH_HASH}}
        }}
        redir * {page_path}/ 302
    }}

    handle {page_path}/links.json {{
        basic_auth {{
            {{$HUB_BASIC_AUTH_USER}} {{$HUB_BASIC_AUTH_HASH}}
        }}
        root * /srv/public
        rewrite * /links.json
        file_server
    }}

    handle {page_path}/status.json {{
        basic_auth {{
            {{$HUB_BASIC_AUTH_USER}} {{$HUB_BASIC_AUTH_HASH}}
        }}
        root * /srv/public
        rewrite * /status.json
        file_server
    }}

    handle_path {page_path}/* {{
        basic_auth {{
            {{$HUB_BASIC_AUTH_USER}} {{$HUB_BASIC_AUTH_HASH}}
        }}
        root * /srv/public
        try_files {{path}} {{path}}/ /index.html
        file_server
    }}

    handle {sr_config_path} {{
        root * /srv/public
        rewrite * /shadowrocket.conf
        file_server
    }}

    handle {rules_prefix}/* {{
        root * /srv/public
        file_server
    }}

    handle {sparkle_path} {{
        rewrite * {{$SUBSTORE_BACKEND_PATH}}/api/file/{mihomo}?target=ClashMeta
        reverse_proxy http://frontier-sub-store:3001
    }}

    handle {ios_ordinary_path} {{
        rewrite * {{$SUBSTORE_BACKEND_PATH}}/download/collection/{ios_ordinary}?target=URI
        reverse_proxy http://frontier-sub-store:3001
    }}

    handle {ios_hy2_path} {{
        rewrite * {{$SUBSTORE_BACKEND_PATH}}/download/collection/{ios_hy2}?target=ShadowRocket
        reverse_proxy http://frontier-sub-store:3001
    }}

    handle {{
        respond "not found" 404
    }}
}}
"""


def htpasswd_line(env: dict[str, str]) -> str:
    user = env.get("HUB_BASIC_AUTH_USER", "viewer")
    password = env.get("HUB_BASIC_AUTH_PASSWORD", "")
    if not password:
        return ""
    # nginx supports the portable apr1/bcrypt variants. For generation without
    # external dependencies, emit a crypt-style SHA-1 line accepted by Apache
    # htpasswd consumers and modern nginx auth_basic builds.
    digest = base64.b64encode(hashlib.sha1(password.encode("utf-8")).digest()).decode("ascii")
    return f"{user}:{{SHA}}{digest}\n"


def caddy_auth_line(env: dict[str, str]) -> str:
    user = env.get("HUB_BASIC_AUTH_USER", "viewer")
    hashed = env.get("HUB_BASIC_AUTH_HASH", "")
    if hashed:
        return f"{user} {hashed}\n"
    return ""


def service_py() -> str:
    return r'''#!/usr/bin/env python3
from __future__ import annotations

import base64
import hmac
import http.server
import os
import pathlib
import urllib.request

ROOT = pathlib.Path(os.environ.get("HUB_ROOT", "/opt/frontier/subscription-hub"))
PUBLIC = ROOT / "public"
USER = os.environ.get("HUB_BASIC_AUTH_USER", "")
PASSWORD = os.environ.get("HUB_BASIC_AUTH_PASSWORD", "")
SPARKLE_PATH = os.environ["HUB_SPARKLE_PATH"]
SR_CONFIG_PATH = os.environ["HUB_SHADOWROCKET_CONFIG_PATH"]
IOS_ORDINARY_PATH = os.environ["HUB_IOS_ORDINARY_PATH"]
IOS_HY2_PATH = os.environ["HUB_IOS_HY2_PATH"]
PAGE_PATH = os.environ.get("HUB_PAGE_PATH", "/links").rstrip("/")
SUBSTORE = os.environ["SUBSTORE_LOCAL_BASE_URL"].rstrip("/")
BACKEND = os.environ["SUBSTORE_BACKEND_PATH"]
MIHOMO = os.environ.get("SUBSTORE_MIHOMO_FILE", "frontier-chain-mihomo")
IOS_ORDINARY = os.environ.get("SUBSTORE_IOS_ORDINARY_COLLECTION", "ios-airports-uri")
IOS_HY2 = os.environ.get("SUBSTORE_IOS_HY2_COLLECTION", "ios-evoxt-hy2-shadowrocket")


def substore_url(suffix: str) -> str:
    return SUBSTORE + BACKEND + suffix


TARGETS = {
    SPARKLE_PATH: substore_url(f"/api/file/{MIHOMO}?target=ClashMeta"),
    IOS_ORDINARY_PATH: substore_url(f"/download/collection/{IOS_ORDINARY}?target=URI"),
    IOS_HY2_PATH: substore_url(f"/download/collection/{IOS_HY2}?target=ShadowRocket"),
}


class Handler(http.server.BaseHTTPRequestHandler):
    server_version = "subscription-hub"

    def log_message(self, fmt, *args):
        # Avoid logging opaque subscription paths.
        return

    def send_no_store(self):
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")

    def unauthorized(self):
        self.send_response(401)
        self.send_header("WWW-Authenticate", 'Basic realm="Subscription Hub"')
        self.send_header("Cache-Control", "no-store")
        self.end_headers()

    def authorized(self) -> bool:
        if not USER or not PASSWORD:
            return False
        raw = self.headers.get("Authorization", "")
        prefix = "Basic "
        if not raw.startswith(prefix):
            return False
        try:
            decoded = base64.b64decode(raw[len(prefix):]).decode("utf-8")
        except Exception:
            return False
        expected = USER + ":" + PASSWORD
        return hmac.compare_digest(decoded, expected)

    def serve_file(self, rel: str, content_type: str):
        path = (PUBLIC / rel).resolve()
        if not str(path).startswith(str(PUBLIC.resolve())) or not path.exists():
            self.send_error(404)
            return
        data = path.read_bytes()
        self.send_response(200)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(data)))
        self.send_no_store()
        self.end_headers()
        self.wfile.write(data)

    def proxy(self, target: str):
        req = urllib.request.Request(target, headers={"User-Agent": "subscription-hub/1.0"})
        try:
            with urllib.request.urlopen(req, timeout=20) as resp:
                data = resp.read()
                ctype = resp.headers.get("Content-Type", "text/plain")
        except Exception:
            self.send_error(502)
            return
        self.send_response(200)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.send_no_store()
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        path = self.path.split("?", 1)[0]
        if path == "/healthz":
            self.send_response(200)
            self.send_header("Content-Type", "text/plain; charset=utf-8")
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(b"ok\n")
            return
        if path == "/":
            self.send_response(302)
            self.send_header("Location", PAGE_PATH + "/")
            self.send_no_store()
            self.end_headers()
            return
        if path == PAGE_PATH:
            if not self.authorized():
                self.unauthorized()
                return
            self.send_response(302)
            self.send_header("Location", PAGE_PATH + "/")
            self.send_no_store()
            self.end_headers()
            return
        if path == PAGE_PATH + "/" or path.startswith(PAGE_PATH + "/"):
            if not self.authorized():
                self.unauthorized()
                return
            rel = "index.html" if path in {PAGE_PATH + "/", PAGE_PATH + "/index.html"} else path[len(PAGE_PATH) + 1:]
            ctype = "text/html; charset=utf-8" if rel.endswith(".html") else "image/svg+xml" if rel.endswith(".svg") else "application/octet-stream"
            self.serve_file(rel, ctype)
            return
        if path == SR_CONFIG_PATH:
            self.serve_file("shadowrocket.conf", "text/plain; charset=utf-8")
            return
        if path in TARGETS:
            self.proxy(TARGETS[path])
            return
        self.send_error(404)


if __name__ == "__main__":
    port = int(os.environ.get("HUB_PORT", "19180"))
    bind_host = os.environ.get("HUB_BIND_HOST", "127.0.0.1")
    http.server.ThreadingHTTPServer((bind_host, port), Handler).serve_forever()
'''


def systemd_unit() -> str:
    return """[Unit]
Description=Subscription Hub
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
EnvironmentFile=/opt/frontier/subscription-hub/secrets/hub.runtime.env
Environment=HUB_ROOT=/opt/frontier/subscription-hub
Environment=HUB_PORT=19180
ExecStart=/usr/bin/python3 /opt/frontier/subscription-hub/app/server.py
Restart=on-failure
RestartSec=3
NoNewPrivileges=true

[Install]
WantedBy=multi-user.target
"""


def rule_mirror_service() -> str:
    return """[Unit]
Description=Subscription Hub third-party rule mirror
After=network-online.target
Wants=network-online.target

[Service]
Type=oneshot
EnvironmentFile=/opt/frontier/subscription-hub/secrets/hub.runtime.env
ExecStart=/usr/bin/python3 /opt/frontier/subscription-hub/app/rule_mirror.py --sync --registry /opt/frontier/subscription-hub/app/rules.json --public-dir /opt/frontier/subscription-hub/public
NoNewPrivileges=true
"""


def rule_mirror_timer() -> str:
    return """[Unit]
Description=Refresh Subscription Hub third-party rule mirrors

[Timer]
OnBootSec=3min
OnUnitActiveSec=6h
RandomizedDelaySec=20min
Persistent=true

[Install]
WantedBy=timers.target
"""


def write_qr_svgs(public_dir: Path, endpoints: list[dict[str, str]]) -> None:
    try:
      import segno  # type: ignore
    except ImportError as exc:
        raise SystemExit("missing Python package 'segno'; run: python -m pip install segno") from exc
    for item in endpoints:
        safe_id = re.sub(r"[^A-Za-z0-9_-]", "-", item["id"])
        qr = segno.make(item["url"], error="m")
        qr.save(public_dir / f"qr-{safe_id}.svg", scale=6, border=2, xmldecl=False)


def write_outputs(args: argparse.Namespace, env: dict[str, str]) -> None:
    out_dir = Path(args.out_dir)
    public_dir = out_dir / "public"
    secrets_dir = out_dir / "secrets"
    ingress_dir = out_dir / "ingress"
    public_dir.mkdir(parents=True, exist_ok=True)
    secrets_dir.mkdir(parents=True, exist_ok=True)
    ingress_dir.mkdir(parents=True, exist_ok=True)

    registry = rule_registry(Path(args.rule_registry))
    mirror_map = rule_mirror_url_map(env, registry)
    endpoints = endpoint_defs(env)
    status = status_for(endpoints)
    (public_dir / "status.json").write_text(json.dumps(status, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    rules_dir = public_dir / "rules"
    rules_dir.mkdir(parents=True, exist_ok=True)
    (rules_dir / "status.json").write_text(json.dumps(rule_status_payload(registry), ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    (public_dir / "index.html").write_text(render_index(Path(args.template), endpoints, status), encoding="utf-8")
    (public_dir / "links.json").write_text(json.dumps(links_payload(endpoints, status), ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    sr_conf = rewrite_rule_urls(Path(args.shadowrocket_conf).read_text(encoding="utf-8"), mirror_map)
    (public_dir / "shadowrocket.conf").write_text(sr_conf, encoding="utf-8")
    write_qr_svgs(public_dir, endpoints)
    (ingress_dir / "subscription-hub.nginx.conf").write_text(nginx_fragment(env, endpoints), encoding="utf-8")
    (ingress_dir / "subscription-hub.Caddyfile").write_text(caddy_fragment(env, endpoints), encoding="utf-8")
    (ingress_dir / "internal.Caddyfile").write_text(internal_caddyfile(env, registry), encoding="utf-8")
    app_dir = out_dir / "app"
    app_dir.mkdir(parents=True, exist_ok=True)
    (app_dir / "server.py").write_text(service_py(), encoding="utf-8")
    (app_dir / "rule_mirror.py").write_text(Path(args.rule_mirror).read_text(encoding="utf-8"), encoding="utf-8")
    (app_dir / "rules.json").write_text(json.dumps(registry, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    (out_dir / "subscription-hub.service").write_text(systemd_unit(), encoding="utf-8")
    (out_dir / "subscription-rule-mirror.service").write_text(rule_mirror_service(), encoding="utf-8")
    (out_dir / "subscription-rule-mirror.timer").write_text(rule_mirror_timer(), encoding="utf-8")
    hp = htpasswd_line(env)
    if hp:
        (secrets_dir / "htpasswd").write_text(hp, encoding="utf-8")
    ca = caddy_auth_line(env)
    if ca:
        (secrets_dir / "Caddyfile.auth").write_text(ca, encoding="utf-8")
    manifest = {
        "generated_at": status["generated_at"],
        "page_path_length": len(env["HUB_PAGE_PATH"]),
        "page_url_hash": hashlib.sha256(public_url(normalize_base_url(env["HUB_PUBLIC_BASE_URL"]), env["HUB_PAGE_PATH"]).encode("utf-8")).hexdigest()[:16],
        "endpoint_count": len(endpoints),
        "endpoint_hashes": {item["id"]: hashlib.sha256(item["url"].encode("utf-8")).hexdigest()[:16] for item in endpoints},
        "rule_mirror_count": len(registry["items"]),
        "rule_mirror_path_prefix": rule_path_prefix(registry),
    }
    (out_dir / "manifest.safe.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def check_env(env: dict[str, str], *, allow_placeholders: bool, registry_path: Path, shadowrocket_conf: Path) -> None:
    normalize_base_url(env["HUB_PUBLIC_BASE_URL"])
    for key in [
        "HUB_PAGE_PATH",
        "HUB_SPARKLE_PATH",
        "HUB_SHADOWROCKET_CONFIG_PATH",
        "HUB_IOS_ORDINARY_PATH",
        "HUB_IOS_HY2_PATH",
        "SUBSTORE_BACKEND_PATH",
    ]:
        ensure_safe_path(env[key], key, allow_placeholder=allow_placeholders)
    paths = [
        env["HUB_PAGE_PATH"].rstrip("/"),
        env["HUB_SPARKLE_PATH"],
        env["HUB_SHADOWROCKET_CONFIG_PATH"],
        env["HUB_IOS_ORDINARY_PATH"],
        env["HUB_IOS_HY2_PATH"],
    ]
    if len(paths) != len(set(paths)):
        raise SystemExit("hub paths must be unique")
    if not shadowrocket_conf.exists():
        raise SystemExit(f"missing shadowrocket.conf: {shadowrocket_conf}")
    registry = rule_registry(registry_path)
    items = registry["items"]
    sr_count = sum(1 for item in items if item.get("client") == "shadowrocket")
    mihomo_count = sum(1 for item in items if item.get("client") == "mihomo")
    if sr_count != 18:
        raise SystemExit(f"expected 18 Shadowrocket rule mirrors, got {sr_count}")
    if mihomo_count != 16:
        raise SystemExit(f"expected 16 Mihomo rule mirrors, got {mihomo_count}")
    rewritten = rewrite_rule_urls(shadowrocket_conf.read_text(encoding="utf-8"), rule_mirror_url_map(env, registry))
    third_party_sr = re.findall(r"(?m)^(?:RULE-SET|DOMAIN-SET),https://(?:cdn\.jsdelivr\.net|github\.com)/", rewritten)
    if third_party_sr:
        raise SystemExit("shadowrocket.conf still contains third-party rule URLs after mirror rewrite")
    if not allow_placeholders:
        if not env.get("HUB_BASIC_AUTH_USER") or not env.get("HUB_BASIC_AUTH_PASSWORD"):
            if not env.get("HUB_BASIC_AUTH_HASH"):
                raise SystemExit("HUB_BASIC_AUTH_PASSWORD or HUB_BASIC_AUTH_HASH is required in strict mode")
        try:
            import segno  # noqa: F401
        except ImportError as exc:
            raise SystemExit("missing Python package 'segno'; run: python -m pip install segno") from exc


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description="Render subscription hub artifacts.")
    parser.add_argument("--env-file", required=True)
    parser.add_argument("--out-dir", default=str(HUB_DIR / ".secrets.local" / "out"))
    parser.add_argument("--template", default=str(DEFAULT_TEMPLATE))
    parser.add_argument("--shadowrocket-conf", default=str(DEFAULT_SHADOWROCKET_CONF))
    parser.add_argument("--rule-registry", default=str(DEFAULT_RULE_REGISTRY))
    parser.add_argument("--rule-mirror", default=str(DEFAULT_RULE_MIRROR))
    parser.add_argument("--check", action="store_true")
    parser.add_argument("--strict", action="store_true", help="reject placeholder values")
    args = parser.parse_args(argv)

    env = parse_env(Path(args.env_file))
    check_env(
        env,
        allow_placeholders=not args.strict,
        registry_path=Path(args.rule_registry),
        shadowrocket_conf=Path(args.shadowrocket_conf),
    )
    if args.check:
        print("OK: subscription hub env and templates are valid")
        return 0
    write_outputs(args, env)
    print("OK: rendered subscription hub artifacts")
    print("output_dir=" + str(Path(args.out_dir)))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
