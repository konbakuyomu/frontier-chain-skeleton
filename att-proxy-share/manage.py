#!/usr/bin/env python3
"""Manage short-lived authenticated HTTP/SOCKS access to the AT&T egress."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import secrets
import socket
import stat
import string
import subprocess
import tempfile
import time
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parent
RUNTIME_DIR = ROOT / ".runtime"
HANDOFF_DIR = RUNTIME_DIR / "handoff"
BACKUP_DIR = RUNTIME_DIR / "backups"
STATE_PATH = RUNTIME_DIR / "users.json"
CONFIG_PATH = RUNTIME_DIR / "config.json"
PUBLIC_HOST_PATH = RUNTIME_DIR / "public-host"
CERTBOT_DIR = RUNTIME_DIR / "certbot"
CERTBOT_CONFIG_DIR = CERTBOT_DIR / "config"
CERTBOT_WORK_DIR = CERTBOT_DIR / "work"
CERTBOT_LOGS_DIR = CERTBOT_DIR / "logs"
COMPOSE_PATH = ROOT / "compose.yaml"
CONTAINER = "att-proxy-share"
IMAGE = "metacubex/mihomo:v1.19.29"
CERTBOT_IMAGE = (
    "certbot/certbot:v5.7.0@sha256:"
    "d07bd043d61d6bee1114235ac12c2e9a5c54b6931b3ccf5e1174d6c8c4afaa95"
)
DEFAULT_PORT = 34567
TLS_PORT = 8443
TLS_PUBLIC_HOST = "edge-us.konbakuyomu.us"
CERTBOT_CONFIG_CONTAINER_DIR = "/etc/letsencrypt"
CERTBOT_WORK_CONTAINER_DIR = "/var/lib/letsencrypt"
CERTBOT_LOGS_CONTAINER_DIR = "/var/log/letsencrypt"
CERTBOT_CONTAINER = "att-certbot-acme"
CERTBOT_NETWORK = "edge_ingress"
CERTBOT_HTTP01_ADDRESS = "0.0.0.0"
CERTBOT_HTTP01_PORT = 18081
CERTBOT_PREFERRED_CHAIN = "ISRG Root X1"
CERTBOT_RSA_KEY_SIZE = 2048
TLS_CERT_SOURCE_DIR = CERTBOT_CONFIG_DIR
TLS_CERT_CONTAINER_DIR = "/root/.config/mihomo/certs"
TLS_CERT_FILENAME = "fullchain.pem"
TLS_KEY_FILENAME = "privkey.pem"
TLS_CERT_PATH = f"{TLS_CERT_CONTAINER_DIR}/live/{TLS_PUBLIC_HOST}/{TLS_CERT_FILENAME}"
TLS_KEY_PATH = f"{TLS_CERT_CONTAINER_DIR}/live/{TLS_PUBLIC_HOST}/{TLS_KEY_FILENAME}"
TLS_CERT_MIN_VALIDITY_SECONDS = 7 * 24 * 60 * 60
SYSTEM_CA_BUNDLE = Path("/etc/ssl/certs/ca-certificates.crt")
UPSTREAM_SERVER = "172.19.0.1"
UPSTREAM_PORT = 1082
SCHEMA_VERSION = 1
ROLE = "ATT"
PROTOCOLS = ["socks5", "http"]
NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,31}$")
TOKEN_ALPHABET = string.ascii_letters + string.digits
PEM_CERTIFICATE_RE = re.compile(
    r"-----BEGIN CERTIFICATE-----\s+.*?-----END CERTIFICATE-----", re.DOTALL
)
SENSITIVE_COMMAND_OPTIONS = {"--email", "--password", "--token", "--api-key", "--credentials"}

# These paths are read only during the one-time migration from the pre-runtime MVP.
LEGACY_PATHS = {
    "state": ROOT / "users.json",
    "config": ROOT / "config.json",
    "public_host": ROOT / "public-host",
}


def fail(message: str) -> None:
    raise SystemExit(message)


def redacted_command(command: list[str]) -> str:
    """Keep process failures useful without leaking command-line credentials."""
    parts: list[str] = []
    redact_next = False
    for part in command:
        if redact_next:
            parts.append("<redacted>")
            redact_next = False
            continue
        option, separator, _ = part.partition("=")
        if option in SENSITIVE_COMMAND_OPTIONS:
            parts.append(f"{option}=<redacted>" if separator else option)
            redact_next = not separator
            continue
        parts.append(part)
    return " ".join(parts)


def redacted_command_output(command: list[str], output: str) -> str:
    values: list[str] = []
    for index, part in enumerate(command):
        option, separator, value = part.partition("=")
        if option in SENSITIVE_COMMAND_OPTIONS:
            if separator:
                values.append(value)
            elif index + 1 < len(command):
                values.append(command[index + 1])
    for value in values:
        if value:
            output = output.replace(value, "<redacted>")
    return output


def run(command: list[str], *, check: bool = True) -> subprocess.CompletedProcess[str]:
    result = subprocess.run(command, text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    if check and result.returncode != 0:
        detail = result.stderr.strip() or result.stdout.strip() or "command failed"
        fail(
            f"command failed ({result.returncode}): {redacted_command(command)}: "
            f"{redacted_command_output(command, detail)}"
        )
    return result


def ensure_mode(path: Path, mode: int) -> None:
    try:
        os.chmod(path, mode)
    except OSError as exc:
        fail(f"cannot set private permissions on {path}: {exc}")


def ensure_fd_mode(fd: int, mode: int) -> None:
    if hasattr(os, "fchmod"):
        os.fchmod(fd, mode)


def ensure_runtime() -> None:
    try:
        for directory in (
            RUNTIME_DIR,
            HANDOFF_DIR,
            BACKUP_DIR,
            CERTBOT_DIR,
            CERTBOT_CONFIG_DIR,
            CERTBOT_WORK_DIR,
            CERTBOT_LOGS_DIR,
        ):
            directory.mkdir(mode=0o700, exist_ok=True)
    except OSError as exc:
        fail(f"cannot create runtime directories: {exc}")
    for directory in (
        RUNTIME_DIR,
        HANDOFF_DIR,
        BACKUP_DIR,
        CERTBOT_DIR,
        CERTBOT_CONFIG_DIR,
        CERTBOT_WORK_DIR,
        CERTBOT_LOGS_DIR,
    ):
        ensure_mode(directory, 0o700)


def migrate_legacy() -> None:
    """Move the old root-level runtime files without overwriting new state."""
    present = [(name, path) for name, path in LEGACY_PATHS.items() if path.exists()]
    if not present:
        return

    target_paths = {"state": STATE_PATH, "config": CONFIG_PATH, "public_host": PUBLIC_HOST_PATH}
    conflicts = [name for name, path in present if target_paths[name].exists()]
    if conflicts:
        fail("legacy runtime migration refused: both legacy and .runtime files exist")

    moved: list[tuple[Path, Path]] = []
    try:
        for name, source in present:
            target = target_paths[name]
            os.replace(source, target)
            ensure_mode(target, 0o600)
            moved.append((source, target))
    except OSError as exc:
        for source, target in reversed(moved):
            if target.exists() and not source.exists():
                os.replace(target, source)
        fail(f"legacy runtime migration failed: {exc}")


def default_state() -> dict[str, Any]:
    return {
        "schema_version": SCHEMA_VERSION,
        "enabled": True,
        "port": DEFAULT_PORT,
        "users": {},
    }


def validate_name(name: str) -> str:
    if not NAME_RE.fullmatch(name):
        fail("name must be 1-32 characters: letters, digits, dot, dash, underscore")
    return name


def validate_port(value: Any) -> int:
    try:
        port = int(value)
    except (TypeError, ValueError):
        fail("port must be an integer between 1024 and 65535")
    if not 1024 <= port <= 65535:
        fail("port must be an integer between 1024 and 65535")
    return port


def validate_public_host(value: str) -> str:
    host = value.strip()
    if not host or any(ch.isspace() for ch in host) or ":" in host or "/" in host:
        fail("public host must be a hostname or IPv4 address")
    return host


def read_public_host() -> str:
    try:
        host = PUBLIC_HOST_PATH.read_text(encoding="utf-8")
    except OSError as exc:
        fail(f"cannot read runtime public host; provision {PUBLIC_HOST_PATH}: {exc}")
    return validate_public_host(host)


def token(length: int) -> str:
    return "".join(secrets.choice(TOKEN_ALPHABET) for _ in range(length))


def normalize_user(label: str, item: Any) -> dict[str, Any]:
    if not isinstance(item, dict):
        fail(f"invalid credential record for {label}")
    username = item.get("username")
    password = item.get("password")
    if not isinstance(username, str) or not username or any(ch.isspace() for ch in username):
        fail(f"invalid credential username for {label}")
    if not isinstance(password, str) or not password or any(ch.isspace() for ch in password):
        fail(f"invalid credential password for {label}")
    logical_role = item.get("logical_role", ROLE)
    protocols = item.get("protocols", PROTOCOLS)
    if logical_role != ROLE or protocols != PROTOCOLS:
        fail(f"credential {label} must use the fixed ATT SOCKS5/HTTP role")
    try:
        created_at = int(item.get("created_at", 0))
    except (TypeError, ValueError):
        fail(f"invalid created_at for {label}")
    if created_at < 0:
        fail(f"invalid created_at for {label}")
    return {
        "username": username,
        "password": password,
        "logical_role": ROLE,
        "protocols": list(PROTOCOLS),
        "created_at": created_at,
    }


def normalize_state(raw: Any) -> dict[str, Any]:
    if not isinstance(raw, dict):
        fail("invalid runtime state: expected an object")
    try:
        schema_version = int(raw.get("schema_version", SCHEMA_VERSION))
    except (TypeError, ValueError):
        fail("invalid runtime state schema_version")
    if schema_version != SCHEMA_VERSION:
        fail(f"unsupported runtime state schema_version: {schema_version}")
    enabled = raw.get("enabled", True)
    if not isinstance(enabled, bool):
        fail("invalid runtime state enabled flag")
    users = raw.get("users", {})
    if not isinstance(users, dict):
        fail("invalid runtime state users")
    port = validate_port(raw.get("port", DEFAULT_PORT))
    if port == TLS_PORT:
        fail("legacy public port conflicts with the reserved TLS listener")
    normalized_users: dict[str, dict[str, Any]] = {}
    usernames: set[str] = set()
    for raw_label, item in users.items():
        label = validate_name(str(raw_label))
        if label in normalized_users:
            fail(f"duplicate credential label: {label}")
        record = normalize_user(label, item)
        if record["username"] in usernames:
            fail("duplicate credential username in runtime state")
        usernames.add(record["username"])
        normalized_users[label] = record
    return {
        "schema_version": SCHEMA_VERSION,
        "enabled": enabled,
        "port": port,
        "users": normalized_users,
    }


def load_state() -> dict[str, Any]:
    ensure_runtime()
    migrate_legacy()
    if not STATE_PATH.exists():
        return default_state()
    try:
        raw = json.loads(STATE_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        fail(f"cannot read runtime state: {exc}")
    state = normalize_state(raw)
    ensure_mode(STATE_PATH, 0o600)
    return state


def config_for(state: dict[str, Any]) -> dict[str, Any]:
    state = normalize_state(state)
    users = state["users"]
    public = bool(state["enabled"] and users)
    listen = "0.0.0.0" if public else "127.0.0.1"
    config: dict[str, Any] = {
        "allow-lan": public,
        "bind-address": listen,
        "mode": "rule",
        "log-level": "warning",
        "ipv6": False,
        "udp": False,
        "listeners": [
            {
                "name": "ATT-PUBLIC",
                "type": "mixed",
                "port": state["port"],
                "listen": listen,
                "udp": False,
            },
            {
                "name": "ATT-PUBLIC-TLS",
                "type": "mixed",
                "port": TLS_PORT,
                "listen": listen,
                "udp": False,
                "certificate": TLS_CERT_PATH,
                "private-key": TLS_KEY_PATH,
            },
        ],
        "proxies": [
            {
                "name": "ATT-UPSTREAM",
                "type": "http",
                "server": UPSTREAM_SERVER,
                "port": UPSTREAM_PORT,
            }
        ],
        "proxy-groups": [{"name": ROLE, "type": "select", "proxies": ["ATT-UPSTREAM"]}],
        "rules": [
            f"IN-USER,{item['username']},{item['logical_role']}"
            for item in users.values()
        ]
        + ["MATCH,REJECT"],
    }
    if users:
        config["authentication"] = [
            f"{item['username']}:{item['password']}" for item in users.values()
        ]
    return config


def write_atomic(path: Path, content: bytes, mode: int = 0o600) -> None:
    path.parent.mkdir(mode=0o700, exist_ok=True)
    ensure_mode(path.parent, 0o700)
    fd, temp_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    temp_path = Path(temp_name)
    try:
        ensure_fd_mode(fd, mode)
        with os.fdopen(fd, "wb") as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp_path, path)
        ensure_mode(path, mode)
    finally:
        if temp_path.exists():
            temp_path.unlink()


def json_bytes(value: Any) -> bytes:
    return (json.dumps(value, ensure_ascii=False, indent=2) + "\n").encode("utf-8")


def tls_source_paths() -> tuple[Path, Path]:
    live_dir = TLS_CERT_SOURCE_DIR / "live" / TLS_PUBLIC_HOST
    return live_dir / TLS_CERT_FILENAME, live_dir / TLS_KEY_FILENAME


def resolve_certbot_material(path: Path, label: str) -> Path:
    """Resolve Certbot's live -> archive link without accepting paths outside its config root."""
    try:
        config_root = TLS_CERT_SOURCE_DIR.resolve(strict=True)
        resolved = path.resolve(strict=True)
    except OSError as exc:
        fail(f"TLS {label} is unavailable: {exc}")
    try:
        resolved.relative_to(config_root)
    except ValueError:
        fail(f"TLS {label} resolves outside the Certbot config directory")
    if not resolved.is_file():
        fail(f"TLS {label} is unavailable")
    return resolved


def tls_material_fingerprint_or_none() -> str | None:
    """Return a content fingerprint only when both live Certbot links are safely resolvable."""
    certificate, private_key = tls_source_paths()
    try:
        config_root = TLS_CERT_SOURCE_DIR.resolve(strict=True)
        resolved_certificate = certificate.resolve(strict=True)
        resolved_private_key = private_key.resolve(strict=True)
        resolved_certificate.relative_to(config_root)
        resolved_private_key.relative_to(config_root)
        if not resolved_certificate.is_file() or not resolved_private_key.is_file():
            return None
        digest = hashlib.sha256()
        digest.update(resolved_certificate.read_bytes())
        digest.update(b"\0")
        digest.update(resolved_private_key.read_bytes())
        return digest.hexdigest()
    except (OSError, ValueError):
        return None


def write_tls_chain_tempfiles(certificate: Path) -> tuple[Path, Path]:
    try:
        blocks = PEM_CERTIFICATE_RE.findall(certificate.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError) as exc:
        fail(f"TLS certificate chain cannot be read: {exc}")
    if len(blocks) < 2:
        fail("TLS certificate chain is incomplete")

    leaf_fd: int | None = None
    leaf_path: Path | None = None
    try:
        leaf_fd, leaf_name = tempfile.mkstemp(prefix="tls-leaf-", dir=RUNTIME_DIR)
        leaf_path = Path(leaf_name)
        chain_fd, chain_name = tempfile.mkstemp(prefix="tls-chain-", dir=RUNTIME_DIR)
    except OSError as exc:
        if leaf_fd is not None:
            os.close(leaf_fd)
        if leaf_path is not None and leaf_path.exists():
            leaf_path.unlink()
        fail(f"TLS certificate chain temporary files cannot be created: {exc}")
    leaf_path = Path(leaf_name)
    chain_path = Path(chain_name)
    try:
        ensure_fd_mode(leaf_fd, 0o600)
        with os.fdopen(leaf_fd, "w", encoding="utf-8") as handle:
            handle.write(blocks[0] + "\n")
        ensure_fd_mode(chain_fd, 0o600)
        with os.fdopen(chain_fd, "w", encoding="utf-8") as handle:
            handle.write("\n".join(blocks[1:]) + "\n")
    except BaseException:
        for path in (leaf_path, chain_path):
            if path.exists():
                path.unlink()
        raise
    return leaf_path, chain_path


def validate_tls_chain(certificate: Path) -> None:
    if not SYSTEM_CA_BUNDLE.is_file():
        fail("TLS system CA bundle is unavailable")
    leaf_path, chain_path = write_tls_chain_tempfiles(certificate)
    try:
        result = run(
            [
                "openssl",
                "verify",
                "-show_chain",
                "-purpose",
                "sslserver",
                "-CAfile",
                str(SYSTEM_CA_BUNDLE),
                "-untrusted",
                str(chain_path),
                str(leaf_path),
            ],
            check=False,
        )
    except OSError:
        fail("TLS certificate chain validation cannot run")
    finally:
        for path in (leaf_path, chain_path):
            if path.exists():
                path.unlink()
    if result.returncode != 0:
        fail("TLS certificate chain validation failed")
    if CERTBOT_PREFERRED_CHAIN not in result.stdout:
        fail(f"TLS certificate chain does not terminate at {CERTBOT_PREFERRED_CHAIN}")


def validate_tls_material() -> None:
    """Reject a candidate before Mihomo reads missing, weak, or mismatched Certbot material."""
    ensure_runtime()
    certificate_path, private_key_path = tls_source_paths()
    if not TLS_CERT_SOURCE_DIR.is_dir():
        fail("Certbot TLS config directory is unavailable")
    if not certificate_path.is_file() or not private_key_path.is_file():
        fail("TLS certificate or private key is unavailable")
    certificate = resolve_certbot_material(certificate_path, "certificate")
    private_key = resolve_certbot_material(private_key_path, "private key")

    checks = [
        (
            [
                "openssl",
                "x509",
                "-checkend",
                str(TLS_CERT_MIN_VALIDITY_SECONDS),
                "-noout",
                "-in",
                str(certificate),
            ],
            "TLS certificate is expired or expires within seven days",
        ),
        (
            ["openssl", "x509", "-checkhost", TLS_PUBLIC_HOST, "-noout", "-in", str(certificate)],
            "TLS certificate does not match the configured public host",
        ),
        (
            ["openssl", "pkey", "-in", str(private_key), "-noout", "-passin", "pass:"],
            "TLS private key is unreadable",
        ),
    ]
    for command, message in checks:
        try:
            result = run(command, check=False)
        except OSError:
            fail("TLS certificate preflight cannot run")
        if result.returncode != 0:
            fail(message)
        if command[1:3] == ["x509", "-checkhost"] and "does NOT match" in result.stdout:
            fail(message)

    try:
        certificate_details = run(
            ["openssl", "x509", "-in", str(certificate), "-noout", "-text"], check=False
        )
        private_key_details = run(
            ["openssl", "pkey", "-in", str(private_key), "-noout", "-text", "-passin", "pass:"],
            check=False,
        )
    except OSError:
        fail("TLS RSA key validation cannot run")
    if certificate_details.returncode != 0 or private_key_details.returncode != 0:
        fail("TLS RSA key validation failed")
    if "Public Key Algorithm: rsaEncryption" not in certificate_details.stdout or not re.search(
        rf"Public-Key:\s*\({CERTBOT_RSA_KEY_SIZE} bit\)", certificate_details.stdout
    ):
        fail(f"TLS certificate must use RSA-{CERTBOT_RSA_KEY_SIZE}")
    if (
        not re.search(rf"Private-Key:\s*\({CERTBOT_RSA_KEY_SIZE} bit", private_key_details.stdout)
        or "modulus:" not in private_key_details.stdout
        or "privateExponent:" not in private_key_details.stdout
    ):
        fail(f"TLS private key must use RSA-{CERTBOT_RSA_KEY_SIZE}")

    try:
        certificate_public_key = run(
            ["openssl", "x509", "-in", str(certificate), "-noout", "-pubkey"], check=False
        )
        private_key_public_key = run(
            ["openssl", "pkey", "-in", str(private_key), "-pubout", "-passin", "pass:"],
            check=False,
        )
    except OSError:
        fail("TLS certificate/key matching check cannot run")
    if certificate_public_key.returncode != 0 or private_key_public_key.returncode != 0:
        fail("TLS certificate/key matching check failed")
    if certificate_public_key.stdout.strip() != private_key_public_key.stdout.strip():
        fail("TLS certificate does not match the configured private key")
    validate_tls_chain(certificate)


def certbot_network_available() -> None:
    result = run(["docker", "network", "inspect", CERTBOT_NETWORK], check=False)
    if result.returncode != 0:
        fail(f"Certbot Docker network is unavailable: {CERTBOT_NETWORK}")


def certbot_container_name_available() -> None:
    result = run(
        [
            "docker",
            "ps",
            "--all",
            "--filter",
            f"name=^/{CERTBOT_CONTAINER}$",
            "--format",
            "{{.Names}}",
        ],
        check=False,
    )
    if result.returncode != 0:
        fail("cannot inspect the Certbot container name")
    if CERTBOT_CONTAINER in {line.strip() for line in result.stdout.splitlines()}:
        fail(f"Certbot container name is already in use: {CERTBOT_CONTAINER}")


def certbot_docker_command(certbot_args: list[str]) -> list[str]:
    """Build the one-shot container command; port 18081 remains inside edge_ingress."""
    return [
        "docker",
        "compose",
        "-f",
        str(COMPOSE_PATH),
        "--profile",
        "certbot",
        "run",
        "--rm",
        "--name",
        CERTBOT_CONTAINER,
        "-T",
        "--no-deps",
        "certbot",
        *certbot_args,
    ]


def certbot_common_arguments() -> list[str]:
    return [
        "--config-dir",
        CERTBOT_CONFIG_CONTAINER_DIR,
        "--work-dir",
        CERTBOT_WORK_CONTAINER_DIR,
        "--logs-dir",
        CERTBOT_LOGS_CONTAINER_DIR,
        "--non-interactive",
        "--agree-tos",
        "--no-eff-email",
    ]


def read_certbot_email(path_value: str) -> str:
    path = Path(path_value).expanduser()
    try:
        details = path.stat()
        content = path.read_text(encoding="utf-8")
    except OSError as exc:
        fail(f"cannot read Certbot registration email file: {exc}")
    if not stat.S_ISREG(details.st_mode):
        fail("Certbot registration email path must be a regular file")
    if os.name != "nt" and stat.S_IMODE(details.st_mode) & 0o077:
        fail("Certbot registration email file must be root-only")
    email = content.strip()
    if not email or any(char.isspace() for char in email) or "@" not in email:
        fail("Certbot registration email file is invalid")
    return email


def certbot_issue_arguments(email: str) -> list[str]:
    return [
        "certonly",
        "--standalone",
        "--http-01-address",
        CERTBOT_HTTP01_ADDRESS,
        "--http-01-port",
        str(CERTBOT_HTTP01_PORT),
        "--cert-name",
        TLS_PUBLIC_HOST,
        "--key-type",
        "rsa",
        "--rsa-key-size",
        str(CERTBOT_RSA_KEY_SIZE),
        "--preferred-chain",
        CERTBOT_PREFERRED_CHAIN,
        "--email",
        email,
        "-d",
        TLS_PUBLIC_HOST,
        *certbot_common_arguments(),
    ]


def certbot_renew_arguments(*, dry_run: bool) -> list[str]:
    arguments = [
        "renew",
        "--standalone",
        "--http-01-address",
        CERTBOT_HTTP01_ADDRESS,
        "--http-01-port",
        str(CERTBOT_HTTP01_PORT),
        "--key-type",
        "rsa",
        "--rsa-key-size",
        str(CERTBOT_RSA_KEY_SIZE),
        "--preferred-chain",
        CERTBOT_PREFERRED_CHAIN,
        *certbot_common_arguments(),
    ]
    if dry_run:
        arguments.append("--dry-run")
    return arguments


def run_certbot(certbot_args: list[str]) -> None:
    ensure_runtime()
    certbot_network_available()
    certbot_container_name_available()
    run(certbot_docker_command(certbot_args))


def refresh_tls_after_material_change(before: str | None) -> tuple[bool, bool]:
    """Recreate only the gateway after Certbot has atomically changed valid material."""
    validate_tls_material()
    after = tls_material_fingerprint_or_none()
    if after is None:
        fail("TLS certificate material fingerprint is unavailable")
    if before == after:
        return False, False

    state = load_state()
    if not state["enabled"] or not state["users"]:
        return True, False

    config = config_for(state)
    test_config(config)
    old_config_bytes = read_bytes(CONFIG_PATH)
    write_atomic(CONFIG_PATH, json_bytes(config))
    try:
        apply_container(True, (state["port"], TLS_PORT))
    except BaseException:
        restore(CONFIG_PATH, old_config_bytes)
        try:
            apply_container(True, (state["port"], TLS_PORT))
        except BaseException:
            pass
        raise
    return True, True


def test_config(config: dict[str, Any]) -> None:
    ensure_runtime()
    validate_tls_material()
    fd, temp_name = tempfile.mkstemp(prefix="mihomo-test-", dir=RUNTIME_DIR)
    temp_path = Path(temp_name)
    try:
        ensure_fd_mode(fd, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(config, handle, ensure_ascii=False, indent=2)
            handle.write("\n")
        result = run(
            [
                "docker",
                "run",
                "--rm",
                "--network",
                "host",
                "-v",
                f"{temp_path}:/root/.config/mihomo/config.yaml:ro",
                "-v",
                f"{TLS_CERT_SOURCE_DIR}:{TLS_CERT_CONTAINER_DIR}:ro",
                IMAGE,
                "-t",
                "-f",
                "/root/.config/mihomo/config.yaml",
            ],
            check=False,
        )
        if result.returncode != 0:
            fail("mihomo config check failed")
    finally:
        if temp_path.exists():
            temp_path.unlink()


def container_running() -> bool:
    result = run(["docker", "inspect", "-f", "{{.State.Running}}", CONTAINER], check=False)
    return result.returncode == 0 and result.stdout.strip() == "true"


def listeners_owned_by_container(ports: tuple[int, ...]) -> bool:
    """Reject a host-network port that happens to be held by another process."""
    try:
        inspect = run(["docker", "inspect", "-f", "{{.State.Pid}}", CONTAINER], check=False)
        sockets = run(["ss", "-H", "-lntp"], check=False)
    except OSError:
        return False
    pid = inspect.stdout.strip()
    if inspect.returncode != 0 or not pid.isdecimal() or int(pid) <= 0 or sockets.returncode != 0:
        return False
    for port in ports:
        if not any(
            re.search(rf":{port}(?=\s)", line) and f"pid={pid}," in line
            for line in sockets.stdout.splitlines()
        ):
            return False
    return True


def listeners_ready(ports: tuple[int, ...] = (DEFAULT_PORT, TLS_PORT)) -> bool:
    for port in ports:
        try:
            with socket.create_connection(("127.0.0.1", port), timeout=0.5):
                pass
        except OSError:
            return False
    return listeners_owned_by_container(ports)


def apply_container(
    should_run: bool, expected_ports: tuple[int, ...] = (DEFAULT_PORT, TLS_PORT)
) -> None:
    if should_run:
        run(
            [
                "docker",
                "compose",
                "-f",
                str(COMPOSE_PATH),
                "up",
                "-d",
                "--force-recreate",
                "--no-deps",
                CONTAINER,
            ]
        )
        if not container_running():
            fail(f"{CONTAINER} did not stay running")
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            if listeners_ready(expected_ports):
                return
            time.sleep(0.2)
        fail(f"{CONTAINER} did not open both TCP listeners with expected ownership")
    if container_running():
        run(["docker", "stop", CONTAINER])


def read_bytes(path: Path) -> bytes | None:
    return path.read_bytes() if path.exists() else None


def restore(path: Path, content: bytes | None) -> None:
    if content is None:
        if path.exists():
            path.unlink()
        return
    write_atomic(path, content)


def backup_current(old_state_bytes: bytes | None, old_config_bytes: bytes | None) -> Path | None:
    if old_state_bytes is None and old_config_bytes is None:
        return None
    stamp = time.strftime("%Y%m%d-%H%M%S") + f"-{os.getpid()}-{token(6)}"
    directory = BACKUP_DIR / stamp
    directory.mkdir(mode=0o700)
    ensure_mode(directory, 0o700)
    if old_state_bytes is not None:
        write_atomic(directory / "users.json", old_state_bytes)
    if old_config_bytes is not None:
        write_atomic(directory / "config.json", old_config_bytes)
    return directory


def apply_state(old_state: dict[str, Any], new_state: dict[str, Any]) -> None:
    ensure_runtime()
    old_state = normalize_state(old_state)
    new_state = normalize_state(new_state)
    new_config = config_for(new_state)
    should_run = bool(new_state["enabled"] and new_state["users"])
    if should_run:
        test_config(new_config)
    old_state_bytes = read_bytes(STATE_PATH)
    old_config_bytes = read_bytes(CONFIG_PATH)
    backup_current(old_state_bytes, old_config_bytes)
    write_atomic(CONFIG_PATH, json_bytes(new_config))
    write_atomic(STATE_PATH, json_bytes(new_state))
    try:
        apply_container(should_run, (new_state["port"], TLS_PORT))
    except BaseException:
        restore(CONFIG_PATH, old_config_bytes)
        restore(STATE_PATH, old_state_bytes)
        try:
            apply_container(
                bool(old_state["enabled"] and old_state["users"]),
                (old_state["port"], TLS_PORT),
            )
        except BaseException:
            pass
        raise


def credential_fingerprint(item: dict[str, Any]) -> str:
    digest = hashlib.sha256(f"{item['username']}:{item['password']}".encode("utf-8")).hexdigest()
    return digest[:12]


def handoff_path(name: str) -> Path:
    return HANDOFF_DIR / f"{validate_name(name)}.txt"


def handoff_guidance_lines(state: dict[str, Any], *, legacy_host: str) -> list[str]:
    return [
        "[preferred-mainland]",
        "transport=tcp",
        f"host={TLS_PUBLIC_HOST}",
        f"port={TLS_PORT}",
        "client_modes=HTTPS,HTTP+TLS,SOCKS5+TLS",
        "https_dropdown_protocol=HTTPS",
        "https_dropdown_note=If a protocol dropdown offers HTTPS but no separate Proxy TLS switch, select HTTPS.",
        "https_semantics=TLS to proxy, then authenticated HTTP CONNECT.",
        "proxy_tls_switch_protocols=HTTP,SOCKS5",
        "proxy_tls=on",
        "proxy_tls_switch_note=With a separate Proxy TLS switch, select HTTP+TLS or SOCKS5+TLS.",
        f"warning=Do not select plain SOCKS5 on {TLS_PORT}: it is a protocol mismatch; use HTTPS or SOCKS5+TLS.",
        "",
        "[legacy]",
        "protocols=socks5,http",
        "transport=tcp",
        f"host={legacy_host}",
        f"port={state['port']}",
        "raw_fallback=true",
        "proxy_tls=off",
        "note=Raw plaintext fallback: choose SOCKS5 or HTTP, leave Proxy TLS off, and use HTTPS targets.",
    ]


def handoff_content(state: dict[str, Any], item: dict[str, Any], *, legacy_host: str | None = None) -> bytes:
    host = legacy_host if legacy_host is not None else read_public_host()
    lines = [
        "# att-proxy-share handoff",
        "",
        *handoff_guidance_lines(state, legacy_host=host),
        "",
        "[authentication]",
        f"username={item['username']}",
        f"password={item['password']}",
        "field_format=host:port:username:password",
    ]
    return ("\n".join(lines) + "\n").encode("utf-8")


def write_handoff(state: dict[str, Any], name: str, item: dict[str, Any]) -> Path:
    path = handoff_path(name)
    write_atomic(path, handoff_content(state, item))
    return path


def refresh_handoffs(state: dict[str, Any]) -> int:
    state = normalize_state(state)
    if not state["users"]:
        return 0
    validate_tls_material()
    legacy_host = read_public_host()
    contents = {
        handoff_path(name): handoff_content(state, item, legacy_host=legacy_host)
        for name, item in state["users"].items()
    }
    previous = {path: read_bytes(path) for path in contents}
    try:
        for path, content in contents.items():
            write_atomic(path, content)
    except BaseException:
        for path, content in previous.items():
            restore(path, content)
        raise
    return len(contents)


def print_result(message: str) -> None:
    # Keep normal lifecycle output safe for shell logs and automation.
    print(message)


def new_credential(state: dict[str, Any]) -> dict[str, Any]:
    usernames = {item["username"] for item in state["users"].values()}
    username = f"att-{token(10)}"
    while username in usernames:
        username = f"att-{token(10)}"
    return {
        "username": username,
        "password": token(32),
        "logical_role": ROLE,
        "protocols": list(PROTOCOLS),
        "created_at": int(time.time()),
    }


def cmd_add(args: argparse.Namespace) -> None:
    name = validate_name(args.name)
    state = load_state()
    if name in state["users"]:
        fail(f"user already exists: {name}; rotate it instead")
    candidate = dict(state)
    candidate["users"] = dict(state["users"])
    candidate["users"][name] = new_credential(state)
    path = write_handoff(candidate, name, candidate["users"][name])
    try:
        apply_state(state, candidate)
    except BaseException:
        if path.exists():
            path.unlink()
        raise
    item = candidate["users"][name]
    print_result(
        f"created label={name} role={ROLE} protocols=socks5,http "
        f"handoff={path} credential_fingerprint={credential_fingerprint(item)} "
        f"preferred_host={TLS_PUBLIC_HOST} preferred_port={TLS_PORT} preferred_proxy_tls=on"
    )


def cmd_remove(args: argparse.Namespace) -> None:
    name = validate_name(args.name)
    state = load_state()
    if name not in state["users"]:
        fail(f"user not found: {name}")
    candidate = dict(state)
    candidate["users"] = dict(state["users"])
    del candidate["users"][name]
    apply_state(state, candidate)
    path = handoff_path(name)
    if path.exists():
        path.unlink()
    print_result(f"removed label={name}; active_users={len(candidate['users'])}")


def cmd_rotate(args: argparse.Namespace) -> None:
    name = validate_name(args.name)
    state = load_state()
    if name not in state["users"]:
        fail(f"user not found: {name}")
    candidate = dict(state)
    candidate["users"] = dict(state["users"])
    candidate["users"][name] = new_credential(state)
    path = handoff_path(name)
    old_handoff = read_bytes(path)
    write_handoff(candidate, name, candidate["users"][name])
    try:
        apply_state(state, candidate)
    except BaseException:
        restore(path, old_handoff)
        raise
    item = candidate["users"][name]
    print_result(
        f"rotated label={name} handoff={path} credential_fingerprint={credential_fingerprint(item)} "
        f"preferred_host={TLS_PUBLIC_HOST} preferred_port={TLS_PORT} preferred_proxy_tls=on"
    )


def cmd_list(_: argparse.Namespace) -> None:
    state = load_state()
    if not state["users"]:
        print("no active users")
        return
    for name, item in state["users"].items():
        protocols = ",".join(item["protocols"])
        print(
            f"label={name}\tcreated_at={item['created_at']}\trole={item['logical_role']}"
            f"\tprotocols={protocols}\tfingerprint={credential_fingerprint(item)}"
        )


def cmd_show(args: argparse.Namespace) -> None:
    if not args.reveal:
        fail("show requires explicit --reveal; use the 0600 handoff file for normal sharing")
    name = validate_name(args.name)
    state = load_state()
    item = state["users"].get(name)
    if item is None:
        fail(f"user not found: {name}")
    print(f"{read_public_host()}:{state['port']}:{item['username']}:{item['password']}")


def cmd_disable(_: argparse.Namespace) -> None:
    state = load_state()
    if not state["enabled"]:
        print_result("already disabled")
        return
    candidate = dict(state)
    candidate["enabled"] = False
    apply_state(state, candidate)
    print_result("disabled public proxy; credentials retained")


def cmd_enable(_: argparse.Namespace) -> None:
    state = load_state()
    if state["enabled"]:
        print_result("already enabled")
        return
    candidate = dict(state)
    candidate["enabled"] = True
    apply_state(state, candidate)
    print_result(f"enabled public proxy; active_users={len(candidate['users'])}")


def cmd_status(_: argparse.Namespace) -> None:
    state = load_state()
    config_hash = "missing"
    if CONFIG_PATH.exists():
        config_hash = hashlib.sha256(CONFIG_PATH.read_bytes()).hexdigest()[:12]
    print(f"container_running={str(container_running()).lower()}")
    print(f"listeners_ready={str(listeners_ready((state['port'], TLS_PORT))).lower()}")
    print(f"enabled={str(state['enabled']).lower()}")
    print(f"active_users={len(state['users'])}")
    print(f"port={state['port']}")
    print(f"tls_port={TLS_PORT}")
    print(f"config_sha256_12={config_hash}")
    print(f"public_host_present={str(PUBLIC_HOST_PATH.exists()).lower()}")


def cmd_reconcile(_: argparse.Namespace) -> None:
    """Regenerate the listener from existing state after a source/image deploy."""
    state = load_state()
    validate_tls_material()
    apply_state(state, state)
    handoffs_refreshed = refresh_handoffs(state)
    print_result(
        f"reconciled enabled={str(state['enabled']).lower()} "
        f"active_users={len(state['users'])} config_sha256_12="
        f"{hashlib.sha256(CONFIG_PATH.read_bytes()).hexdigest()[:12]} "
        f"handoffs_refreshed={handoffs_refreshed}"
    )


def cmd_refresh_handoffs(_: argparse.Namespace) -> None:
    """Refresh recipient files after an ingress-only source update without recreating accounts."""
    state = load_state()
    handoffs_refreshed = refresh_handoffs(state)
    print_result(
        f"refreshed_handoffs={handoffs_refreshed} preferred_host={TLS_PUBLIC_HOST} "
        f"preferred_port={TLS_PORT} preferred_proxy_tls=on"
    )


def cmd_check_tls(_: argparse.Namespace) -> None:
    """Run the non-secret Certbot certificate preflight used by deployment and reconcile."""
    validate_tls_material()
    print_result(
        "tls_certificate=valid certificate_type=rsa2048 "
        "chain=isrg-root-x1 minimum_remaining_days=7"
    )


def cmd_prepare_certbot(_: argparse.Namespace) -> None:
    """Create the root-only Certbot state hierarchy before initial issuance."""
    ensure_runtime()
    print_result("certbot_runtime=ready state_root=.runtime/certbot")


def cmd_issue_tls(args: argparse.Namespace) -> None:
    """Issue or reuse the domain certificate, then recreate Mihomo only for new material."""
    before = tls_material_fingerprint_or_none()
    email = read_certbot_email(args.email_file)
    run_certbot(certbot_issue_arguments(email))
    changed, recreated = refresh_tls_after_material_change(before)
    print_result(
        "certbot_issue=completed "
        f"material_changed={str(changed).lower()} mihomo_recreated={str(recreated).lower()}"
    )


def cmd_renew_tls(args: argparse.Namespace) -> None:
    """Renew with the same Docker-only HTTP-01 path and refresh only changed material."""
    before = tls_material_fingerprint_or_none()
    run_certbot(certbot_renew_arguments(dry_run=args.dry_run))
    if args.dry_run:
        validate_tls_material()
        if tls_material_fingerprint_or_none() != before:
            fail("Certbot dry-run unexpectedly changed TLS material")
        print_result("certbot_renew=dry_run material_changed=false mihomo_recreated=false")
        return
    changed, recreated = refresh_tls_after_material_change(before)
    print_result(
        "certbot_renew=completed "
        f"material_changed={str(changed).lower()} mihomo_recreated={str(recreated).lower()}"
    )


def parser() -> argparse.ArgumentParser:
    root = argparse.ArgumentParser(description=__doc__)
    sub = root.add_subparsers(dest="command", required=True)

    add = sub.add_parser("add", help="create one credential and start the proxy")
    add.add_argument("name")
    add.set_defaults(func=cmd_add)

    remove = sub.add_parser("remove", help="revoke one credential")
    remove.add_argument("name")
    remove.set_defaults(func=cmd_remove)

    rotate = sub.add_parser("rotate", help="replace one credential and revoke the old one")
    rotate.add_argument("name")
    rotate.set_defaults(func=cmd_rotate)

    show = sub.add_parser("show", help="print one credential only with explicit --reveal")
    show.add_argument("name")
    show.add_argument("--reveal", action="store_true")
    show.set_defaults(func=cmd_show)

    listing = sub.add_parser("list", help="list labels without secrets")
    listing.set_defaults(func=cmd_list)

    status = sub.add_parser("status", help="show service status without secrets")
    status.set_defaults(func=cmd_status)

    disable = sub.add_parser("disable", help="stop the public listener but retain credentials")
    disable.set_defaults(func=cmd_disable)

    enable = sub.add_parser("enable", help="restore the public listener")
    enable.set_defaults(func=cmd_enable)

    reconcile = sub.add_parser(
        "reconcile", help="regenerate config and recreate the container from existing state"
    )
    reconcile.set_defaults(func=cmd_reconcile)

    refresh_handoffs_parser = sub.add_parser(
        "refresh-handoffs", help="refresh existing recipient files without rotating credentials"
    )
    refresh_handoffs_parser.set_defaults(func=cmd_refresh_handoffs)

    check_tls = sub.add_parser("check-tls", help="validate Certbot TLS material without changing state")
    check_tls.set_defaults(func=cmd_check_tls)

    prepare_certbot = sub.add_parser(
        "prepare-certbot", help="create the root-only Certbot state hierarchy without issuing"
    )
    prepare_certbot.set_defaults(func=cmd_prepare_certbot)

    issue_tls = sub.add_parser(
        "issue-tls", help="issue the RSA-2048 certificate through the Docker-only HTTP-01 route"
    )
    issue_tls.add_argument("--email-file", required=True)
    issue_tls.set_defaults(func=cmd_issue_tls)

    renew_tls = sub.add_parser(
        "renew-tls", help="renew TLS material and recreate Mihomo only when it changed"
    )
    renew_tls.add_argument("--dry-run", action="store_true")
    renew_tls.set_defaults(func=cmd_renew_tls)
    return root


if __name__ == "__main__":
    parsed = parser().parse_args()
    try:
        parsed.func(parsed)
    except AttributeError:
        raise SystemExit("invalid command")
