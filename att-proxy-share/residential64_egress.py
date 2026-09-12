#!/usr/bin/env python3
"""Build a private residential-64 egress candidate without changing ATT."""

from __future__ import annotations

import argparse
import copy
import json
import os
import stat
import tempfile
from pathlib import Path
from typing import Any
from urllib.parse import urlparse


ATT_PROVIDER_NAME = "att-upstreams"
RESIDENTIAL64_PROVIDER_NAME = "residential64-upstreams"
RESIDENTIAL64_ROLE = "RESIDENTIAL64"
RESIDENTIAL64_LISTENER_NAME = "residential64-egress-in"
RESIDENTIAL64_NODE_NAME = "家宽-64"
RESIDENTIAL64_PORT = 17083


def fail(message: str) -> None:
    raise ValueError(message)


def exact_node_filter() -> str:
    return f"^{RESIDENTIAL64_NODE_NAME}$"


def read_provider_url(path: Path) -> str:
    try:
        details = path.stat()
        values = [line.strip() for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
    except OSError as exc:
        fail(f"cannot read provider URL file: {exc}")
    if not stat.S_ISREG(details.st_mode):
        fail("provider URL file must be a regular file")
    if os.name != "nt" and stat.S_IMODE(details.st_mode) & 0o077:
        fail("provider URL file must be root-only")
    if len(values) != 1:
        fail("provider URL file must contain exactly one non-empty line")
    parsed = urlparse(values[0])
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        fail("provider URL file must contain an HTTP or HTTPS URL")
    return values[0]


def named_items(config: dict[str, Any], key: str) -> tuple[list[dict[str, Any]], dict[str, dict[str, Any]]]:
    value = config.get(key)
    if not isinstance(value, list):
        fail(f"egress config {key} must be a list")
    result: dict[str, dict[str, Any]] = {}
    for item in value:
        name = item.get("name") if isinstance(item, dict) else None
        if not isinstance(name, str) or not name:
            fail(f"egress config {key} has an unnamed item")
        if name in result:
            fail(f"egress config {key} has a duplicate name")
        result[name] = item
    return value, result


def collision_or_current(
    existing: dict[str, Any] | None, expected: dict[str, Any], label: str
) -> bool:
    if existing is None:
        return False
    if existing != expected:
        fail(f"{label} collision")
    return True


def residential64_provider(att_provider: dict[str, Any], provider_url: str) -> dict[str, Any]:
    candidate = copy.deepcopy(att_provider)
    candidate["url"] = provider_url
    candidate["filter"] = exact_node_filter()
    candidate.pop("exclude-filter", None)
    if "path" in candidate:
        path = candidate["path"]
        if not isinstance(path, str) or not path.strip():
            fail("ATT provider path must be a non-empty string")
        candidate["path"] = f"{path}.residential64"
    return candidate


def residential64_group() -> dict[str, Any]:
    return {
        "name": RESIDENTIAL64_ROLE,
        "type": "select",
        "use": [RESIDENTIAL64_PROVIDER_NAME],
        "empty-fallback": "REJECT",
    }


def residential64_listener() -> dict[str, Any]:
    return {
        "name": RESIDENTIAL64_LISTENER_NAME,
        "type": "mixed",
        "listen": "127.0.0.1",
        "port": RESIDENTIAL64_PORT,
        "proxy": RESIDENTIAL64_ROLE,
        "udp": False,
    }


def build_candidate(config: dict[str, Any], provider_url: str) -> tuple[dict[str, Any], str]:
    if not isinstance(config, dict):
        fail("egress config must be an object")
    candidate = copy.deepcopy(config)
    providers = candidate.get("proxy-providers")
    if not isinstance(providers, dict):
        fail("egress config proxy-providers must be an object")
    att_provider = providers.get(ATT_PROVIDER_NAME)
    if not isinstance(att_provider, dict):
        fail("egress config is missing the ATT provider template")

    groups, groups_by_name = named_items(candidate, "proxy-groups")
    listeners, listeners_by_name = named_items(candidate, "listeners")
    expected_provider = residential64_provider(att_provider, provider_url)
    expected_group = residential64_group()
    expected_listener = residential64_listener()

    provider_path = expected_provider.get("path")
    if provider_path is not None:
        for name, provider in providers.items():
            if (
                name != RESIDENTIAL64_PROVIDER_NAME
                and isinstance(provider, dict)
                and provider.get("path") == provider_path
            ):
                fail("residential provider path collision")

    provider_current = collision_or_current(
        providers.get(RESIDENTIAL64_PROVIDER_NAME), expected_provider, "residential provider"
    )
    group_current = collision_or_current(
        groups_by_name.get(RESIDENTIAL64_ROLE), expected_group, "residential group"
    )
    listener_current = collision_or_current(
        listeners_by_name.get(RESIDENTIAL64_LISTENER_NAME), expected_listener, "residential listener"
    )
    for listener in listeners:
        if listener["name"] != RESIDENTIAL64_LISTENER_NAME and listener.get("port") == RESIDENTIAL64_PORT:
            fail("residential listener port collision")

    if not provider_current:
        providers[RESIDENTIAL64_PROVIDER_NAME] = expected_provider
    if not group_current:
        groups.append(expected_group)
    if not listener_current:
        listeners.append(expected_listener)
    status = "already-current" if provider_current and group_current and listener_current else "added"
    return candidate, status


def load_json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        fail(f"cannot read egress JSON: {exc}")
    if not isinstance(value, dict):
        fail("egress JSON must be an object")
    return value


def write_json(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    fd, temp_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    temp_path = Path(temp_name)
    try:
        if hasattr(os, "fchmod"):
            os.fchmod(fd, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(value, handle, ensure_ascii=False, indent=2)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp_path, path)
        os.chmod(path, 0o600)
    finally:
        if temp_path.exists():
            temp_path.unlink()


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(description=__doc__)
    result.add_argument("--input", required=True, type=Path)
    result.add_argument("--provider-url-file", required=True, type=Path)
    result.add_argument("--output", required=True, type=Path)
    return result


def main(argv: list[str] | None = None) -> int:
    args = parser().parse_args(argv)
    if args.input.resolve() == args.output.resolve():
        fail("candidate output must differ from the ATT egress input")
    candidate, status = build_candidate(load_json(args.input), read_provider_url(args.provider_url_file))
    if args.output.exists():
        if load_json(args.output) != candidate:
            fail("candidate output exists with a different shape")
        status = "already-current"
    else:
        write_json(args.output, candidate)
    print(
        f"status={status} providers={len(candidate['proxy-providers'])} "
        f"proxy_groups={len(candidate['proxy-groups'])} listeners={len(candidate['listeners'])}"
    )
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except ValueError as exc:
        raise SystemExit(str(exc))
