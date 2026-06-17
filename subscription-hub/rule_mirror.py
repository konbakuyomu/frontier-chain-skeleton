#!/usr/bin/env python3
"""Mirror third-party client rule files for subscription-hub.

The source URLs in rules.json are public rule libraries, not private
subscriptions. Runtime status intentionally records only source hosts, byte
counts, and hashes so the same code path remains safe if a private source is
ever added later.
"""

from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import os
import re
import sys
import tempfile
import time
import urllib.error
import urllib.request
from pathlib import Path
from urllib.parse import urlparse


HUB_DIR = Path(__file__).resolve().parent
DEFAULT_REGISTRY = HUB_DIR / "rules.json"
DEFAULT_PUBLIC_DIR = Path(os.environ.get("HUB_PUBLIC_DIR", "/opt/frontier/subscription-hub/public"))


def now_iso() -> str:
    return dt.datetime.now(dt.timezone.utc).astimezone().isoformat(timespec="seconds")


def short_hash(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()[:16]


def load_registry(path: Path) -> dict[str, object]:
    try:
        registry = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise SystemExit(f"missing rule registry: {path}") from exc
    validate_registry(registry)
    return registry


def validate_registry(registry: dict[str, object]) -> None:
    items = registry.get("items")
    if not isinstance(items, list) or not items:
        raise SystemExit("rule registry must contain non-empty items[]")
    seen_ids: set[str] = set()
    seen_files: set[str] = set()
    for idx, item in enumerate(items, 1):
        if not isinstance(item, dict):
            raise SystemExit(f"rule item {idx} must be an object")
        rule_id = str(item.get("id") or "")
        filename = str(item.get("file") or "")
        source = str(item.get("source") or "")
        client = str(item.get("client") or "")
        fmt = str(item.get("format") or "")
        rule_type = str(item.get("rule_type") or "")
        if not re.fullmatch(r"[a-z0-9][a-z0-9-]*", rule_id):
            raise SystemExit(f"invalid rule id: {rule_id}")
        if rule_id in seen_ids:
            raise SystemExit(f"duplicate rule id: {rule_id}")
        seen_ids.add(rule_id)
        if not re.fullmatch(r"[A-Za-z0-9._-]+", filename):
            raise SystemExit(f"invalid rule filename for {rule_id}: {filename}")
        if filename in seen_files:
            raise SystemExit(f"duplicate rule filename: {filename}")
        seen_files.add(filename)
        if not source.startswith("https://"):
            raise SystemExit(f"rule source must be https for {rule_id}")
        if client not in {"shadowrocket", "mihomo"}:
            raise SystemExit(f"invalid client for {rule_id}: {client}")
        if fmt not in {"text", "yaml", "mrs"}:
            raise SystemExit(f"invalid format for {rule_id}: {fmt}")
        if rule_type not in {"RULE-SET", "DOMAIN-SET", "rule-provider"}:
            raise SystemExit(f"invalid rule_type for {rule_id}: {rule_type}")
        if client == "shadowrocket" and rule_type not in {"RULE-SET", "DOMAIN-SET"}:
            raise SystemExit(f"shadowrocket item has invalid rule_type: {rule_id}")
        if client == "mihomo" and rule_type != "rule-provider":
            raise SystemExit(f"mihomo item must be rule-provider: {rule_id}")


def content_lines(data: bytes) -> list[str]:
    text = data.decode("utf-8", errors="replace")
    return [
        line.strip()
        for line in text.splitlines()
        if line.strip() and not line.strip().startswith(("#", "//"))
    ]


def looks_like_mihomo_payload_domain(lines: list[str]) -> bool:
    if not any(line == "payload:" for line in lines):
        return False
    return any(re.search(r"^\s*-\s*['\"]?\+?\.[^'\"]+\.[^'\"]+['\"]?\s*$", line) for line in lines)


def validate_content(item: dict[str, object], data: bytes) -> None:
    rule_id = str(item["id"])
    if len(data) < 8:
        raise ValueError("downloaded rule is too small")
    head = data[:256].lstrip().lower()
    if head.startswith(b"<") or b"<!doctype html" in head or b"<html" in head:
        raise ValueError("downloaded rule looks like HTML")
    if item.get("format") == "mrs":
        return
    lines = content_lines(data)
    if not lines:
        raise ValueError("downloaded text rule has no usable lines")
    if item.get("rule_type") == "DOMAIN-SET":
        domain_like = [line for line in lines if "," not in line and "." in line]
        if not domain_like:
            raise ValueError("DOMAIN-SET rule does not look like a domain set")
    elif item.get("rule_type") == "RULE-SET":
        rule_like = [line for line in lines if "," in line]
        if not rule_like:
            raise ValueError("RULE-SET rule does not contain comma-delimited rules")
    else:
        # Mihomo text providers in this registry are either classical lists or
        # Clash/Mihomo YAML payload domain lists.
        if not any("," in line for line in lines) and not looks_like_mihomo_payload_domain(lines):
            raise ValueError(f"text provider for {rule_id} does not look classical or payload-domain")


def download_rule(source: str, timeout: int) -> tuple[bytes, int]:
    req = urllib.request.Request(
        source,
        headers={
            "User-Agent": "subscription-hub-rule-mirror/1.0",
            "Accept": "*/*",
        },
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        status = getattr(resp, "status", 200)
        data = resp.read()
    return data, int(status)


def atomic_write(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(prefix=path.name + ".", suffix=".tmp", dir=str(path.parent))
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp_name, path)
    finally:
        tmp = Path(tmp_name)
        if tmp.exists():
            tmp.unlink()


def existing_file_status(path: Path) -> tuple[int | None, str]:
    if not path.exists():
        return None, ""
    data = path.read_bytes()
    return len(data), short_hash(data)


def safe_error(exc: BaseException) -> str:
    name = exc.__class__.__name__
    message = str(exc).splitlines()[0] if str(exc) else ""
    message = re.sub(r"https://\S+", "<url>", message)
    return (name + (": " + message if message else ""))[:180]


def sync_one(item: dict[str, object], public_rules_dir: Path, timeout: int, attempts: int, retry_delay: float) -> dict[str, object]:
    rule_id = str(item["id"])
    source = str(item["source"])
    target = public_rules_dir / str(item["file"])
    source_host = urlparse(source).hostname or ""
    status: dict[str, object] = {
        "id": rule_id,
        "client": item["client"],
        "rule_type": item["rule_type"],
        "format": item["format"],
        "file": item["file"],
        "source_host": source_host,
        "status": "pending",
        "http_status": None,
        "last_checked": now_iso(),
        "last_success": "",
        "bytes": None,
        "sha256": "",
        "error": "",
        "attempts": 0,
    }
    last_error: Exception | None = None
    for attempt in range(1, max(1, attempts) + 1):
        status["attempts"] = attempt
        status["last_checked"] = now_iso()
        try:
            data, http_status = download_rule(source, timeout)
            validate_content(item, data)
            atomic_write(target, data)
            status.update(
                {
                    "status": "ok",
                    "http_status": http_status,
                    "last_success": status["last_checked"],
                    "bytes": len(data),
                    "sha256": short_hash(data),
                }
            )
            return status
        except Exception as exc:
            last_error = exc
            if attempt < max(1, attempts) and retry_delay > 0:
                time.sleep(retry_delay)
    existing_bytes, existing_hash = existing_file_status(target)
    if existing_bytes is not None:
        status.update(
            {
                "status": "stale",
                "bytes": existing_bytes,
                "sha256": existing_hash,
                "error": safe_error(last_error) if last_error else "unknown error",
            }
        )
    else:
        status.update({"status": "failed", "error": safe_error(last_error) if last_error else "unknown error"})
    return status


def counts_for(items: list[dict[str, object]]) -> dict[str, int]:
    return {
        "total": len(items),
        "ok": sum(1 for item in items if item.get("status") == "ok"),
        "stale": sum(1 for item in items if item.get("status") == "stale"),
        "failed": sum(1 for item in items if item.get("status") == "failed"),
    }


def combined_hash(items: list[dict[str, object]]) -> str:
    value = "|".join(str(item.get("sha256") or "") for item in items)
    return hashlib.sha256(value.encode("utf-8")).hexdigest()[:16]


def write_rule_status(status_file: Path, items: list[dict[str, object]]) -> dict[str, object]:
    status_file.parent.mkdir(parents=True, exist_ok=True)
    counts = counts_for(items)
    payload = {
        "generated_at": now_iso(),
        "summary": counts,
        "items": items,
    }
    status_file.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return payload


def merge_hub_status(hub_status_file: Path, rule_status: dict[str, object]) -> None:
    if not hub_status_file:
        return
    try:
        hub_status = json.loads(hub_status_file.read_text(encoding="utf-8"))
    except FileNotFoundError:
        hub_status = {"generated_at": now_iso(), "verified_at": "not verified", "items": []}
    items = hub_status.get("items")
    if not isinstance(items, list):
        items = []
        hub_status["items"] = items
    summary = rule_status.get("summary") if isinstance(rule_status.get("summary"), dict) else {}
    rule_items = rule_status.get("items") if isinstance(rule_status.get("items"), list) else []
    failed = int(summary.get("failed", 0) or 0)
    stale = int(summary.get("stale", 0) or 0)
    status_value = "failed" if failed else "stale" if stale else "ok"
    mirror_item = {
        "id": "rule-mirror",
        "title": "Third-party rule mirrors",
        "status": status_value,
        "url_hash": "",
        "last_checked": rule_status.get("generated_at") or now_iso(),
        "bytes": sum(int(item.get("bytes") or 0) for item in rule_items),
        "sha256": combined_hash(rule_items),
        "shape": f"rule-mirror:{summary.get('ok', 0)}/{summary.get('total', 0)}",
        "summary": summary,
    }
    replaced = False
    for idx, item in enumerate(items):
        if isinstance(item, dict) and item.get("id") == "rule-mirror":
            items[idx] = mirror_item
            replaced = True
            break
    if not replaced:
        items.append(mirror_item)
    hub_status["rule_mirror"] = {
        "generated_at": rule_status.get("generated_at"),
        "summary": summary,
    }
    hub_status_file.write_text(json.dumps(hub_status, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description="Mirror third-party rule files.")
    parser.add_argument("--registry", default=str(DEFAULT_REGISTRY))
    parser.add_argument("--public-dir", default=str(DEFAULT_PUBLIC_DIR))
    parser.add_argument("--rules-subdir", default="rules")
    parser.add_argument("--status-file", default="")
    parser.add_argument("--hub-status-file", default="")
    parser.add_argument("--timeout", type=int, default=30)
    parser.add_argument("--attempts", type=int, default=2, help="download attempts per rule before keeping stale content")
    parser.add_argument("--retry-delay", type=float, default=2.0, help="seconds to wait between retry attempts")
    parser.add_argument("--check", action="store_true", help="validate registry only")
    parser.add_argument("--sync", action="store_true", help="download and update mirror files")
    args = parser.parse_args(argv)

    registry = load_registry(Path(args.registry))
    items = registry["items"]
    if args.check:
        by_client: dict[str, int] = {}
        for item in items:
            by_client[str(item["client"])] = by_client.get(str(item["client"]), 0) + 1
        print("OK: rule registry valid")
        print("rule_count=" + str(len(items)))
        print("clients=" + ",".join(f"{key}:{value}" for key, value in sorted(by_client.items())))
        return 0

    public_dir = Path(args.public_dir)
    rules_dir = public_dir / args.rules_subdir.strip("/")
    status_file = Path(args.status_file) if args.status_file else rules_dir / "status.json"
    hub_status_file = Path(args.hub_status_file) if args.hub_status_file else public_dir / "status.json"
    statuses = [sync_one(item, rules_dir, args.timeout, args.attempts, args.retry_delay) for item in items]
    rule_status = write_rule_status(status_file, statuses)
    merge_hub_status(hub_status_file, rule_status)
    summary = rule_status["summary"]
    print(
        "rule_mirror "
        + " ".join(f"{key}={summary[key]}" for key in ("total", "ok", "stale", "failed"))
        + " sha256="
        + combined_hash(statuses)
    )
    return 1 if int(summary.get("failed", 0) or 0) else 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
