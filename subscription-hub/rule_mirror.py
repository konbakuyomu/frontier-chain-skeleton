#!/usr/bin/env python3
"""Mirror public third-party rule material through a policy-projected gateway.

The registry exposes stable logical provider names.  AI sources are parsed and
sanitized before publication; an opaque MRS file can never become an active AI
provider merely because its HTTP request succeeded.
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
import urllib.request
from pathlib import Path
from urllib.parse import urlparse

from ai_routing import (
    SemanticError,
    active_items,
    derived_registry_counts,
    gated_normalized_diff,
    load_contract,
    parse_classical_rules,
    parse_domain_list,
    project_rules,
    render_classical_rules,
    validate_declared_exclusions,
    validate_registry,
    validate_semantic_rules,
)


HUB_DIR = Path(__file__).resolve().parent
DEFAULT_REGISTRY = HUB_DIR / "rules.json"
DEFAULT_PUBLIC_DIR = Path(os.environ.get("HUB_PUBLIC_DIR", "/opt/frontier/subscription-hub/public"))


def default_contract_path() -> Path:
    """Find the source-tree contract locally and the co-located one on SJC."""

    for candidate in (HUB_DIR.parent / "ai-routing-contract.json", HUB_DIR / "ai-routing-contract.json"):
        if candidate.exists():
            return candidate
    return HUB_DIR.parent / "ai-routing-contract.json"


def now_iso() -> str:
    return dt.datetime.now(dt.timezone.utc).astimezone().isoformat(timespec="seconds")


def short_hash(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()[:16]


def load_registry(path: Path, contract: dict[str, object]) -> dict[str, object]:
    try:
        registry = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise SystemExit(f"missing rule registry: {path}") from exc
    except json.JSONDecodeError as exc:
        raise SystemExit(f"invalid rule registry JSON: {exc.msg}") from exc
    try:
        return validate_registry(registry, contract)
    except ValueError as exc:
        raise SystemExit(str(exc)) from exc


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


def validate_generic_content(item: dict[str, object], data: bytes) -> None:
    """Keep the existing non-AI mirror shape checks strict and format-aware."""

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
        if not any("," not in line and "." in line for line in lines):
            raise ValueError("DOMAIN-SET rule does not look like a domain set")
    elif item.get("rule_type") == "RULE-SET":
        if not any("," in line for line in lines):
            raise ValueError("RULE-SET rule does not contain comma-delimited rules")
    elif not any("," in line for line in lines) and not looks_like_mihomo_payload_domain(lines):
        raise ValueError(f"text provider for {rule_id} does not look classical or payload-domain")


def download_rule(source: str, timeout: int) -> tuple[bytes, int]:
    request = urllib.request.Request(
        source,
        headers={"User-Agent": "subscription-hub-rule-mirror/2.0", "Accept": "*/*"},
    )
    with urllib.request.urlopen(request, timeout=timeout) as response:
        status = getattr(response, "status", 200)
        data = response.read()
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
        temporary = Path(tmp_name)
        if temporary.exists():
            temporary.unlink()


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


def logical_candidate(
    item: dict[str, object],
    data: bytes,
    contract: dict[str, object],
    target: Path,
) -> tuple[bytes, dict[str, object]]:
    """Parse, project, and render one active logical AI provider."""

    if item.get("source") != item.get("semantic_source"):
        raise SemanticError(f"{item.get('id')} must use the inspected semantic source as its publication input")
    try:
        source_text = data.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise SemanticError(f"{item.get('id')} source is not UTF-8 text") from exc
    if item.get("adapter") == "domain-list-to-classical":
        raw_rules, parsed = parse_domain_list(source_text)
    elif item.get("adapter") == "classical-to-classical":
        raw_rules = parse_classical_rules(source_text)
        regex_values = [rule.value for rule in raw_rules if rule.kind == "regex"]
        parsed = {
            "comments": 0,
            "attributes": 0,
            "attribute_names": [],
            "regex": len(regex_values),
            "regex_values": regex_values,
            "bare": 0,
            "full": 0,
            "plus_suffix": 0,
        }
    else:
        raise SemanticError(f"{item.get('id')} has no safe text projection adapter")
    reviewed_exclusions = validate_declared_exclusions(item, parsed)
    projected, projection = project_rules(raw_rules, contract)
    semantic = validate_semantic_rules(item, projected, contract)
    previous = []
    if target.exists():
        try:
            previous = parse_classical_rules(target.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, SemanticError) as exc:
            raise SemanticError(f"{item.get('id')} existing last-known-good is not parseable") from exc
    semantic.update(
        {
            "status": "passed",
            "parsed": parsed,
            "reviewed_exclusions": reviewed_exclusions,
            "projection": projection,
            "diff": gated_normalized_diff(previous, projected),
        }
    )
    return render_classical_rules(item, projected), semantic


def status_template(item: dict[str, object]) -> dict[str, object]:
    source = str(item["source"])
    status: dict[str, object] = {
        "id": item["id"],
        "client": item["client"],
        "rule_type": item["rule_type"],
        "format": item["format"],
        "file": item["file"],
        "source_host": urlparse(source).hostname or "",
        "status": "pending",
        "http_status": None,
        "last_checked": now_iso(),
        "last_success": "",
        "bytes": None,
        "sha256": "",
        "error": "",
        "attempts": 0,
        "semantic": {"status": "not-applicable"},
    }
    if item.get("logical_provider"):
        status["logical_provider"] = item["logical_provider"]
        status["covers"] = list(item.get("covers") or [])
        status["semantic_source_host"] = urlparse(str(item.get("semantic_source") or "")).hostname or ""
    return status


def sync_one(
    item: dict[str, object],
    public_rules_dir: Path,
    contract: dict[str, object],
    timeout: int,
    attempts: int,
    retry_delay: float,
) -> dict[str, object]:
    target = public_rules_dir / str(item["file"])
    status = status_template(item)
    last_error: Exception | None = None
    for attempt in range(1, max(1, attempts) + 1):
        status["attempts"] = attempt
        status["last_checked"] = now_iso()
        try:
            data, http_status = download_rule(str(item["source"]), timeout)
            if item.get("logical_provider"):
                candidate, semantic = logical_candidate(item, data, contract, target)
                status["semantic"] = semantic
            else:
                validate_generic_content(item, data)
                candidate = data
            atomic_write(target, candidate)
            status.update(
                {
                    "status": "ok",
                    "http_status": http_status,
                    "last_success": status["last_checked"],
                    "bytes": len(candidate),
                    "sha256": short_hash(candidate),
                }
            )
            return status
        except Exception as exc:  # Existing last-known-good is intentionally retained below.
            last_error = exc
            if attempt < max(1, attempts) and retry_delay > 0:
                time.sleep(retry_delay)
    existing_bytes, existing_hash = existing_file_status(target)
    status["semantic"] = {"status": "failed" if item.get("logical_provider") else "not-applicable"}
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
    joined = "|".join(str(item.get("sha256") or "") for item in items)
    return hashlib.sha256(joined.encode("utf-8")).hexdigest()[:16]


def write_rule_status(
    status_file: Path,
    items: list[dict[str, object]],
    registry: dict[str, object],
) -> dict[str, object]:
    payload = {
        "generated_at": now_iso(),
        "summary": counts_for(items),
        "registry": derived_registry_counts(registry),
        "items": items,
    }
    atomic_write(status_file, (json.dumps(payload, ensure_ascii=False, indent=2) + "\n").encode("utf-8"))
    return payload


def merge_hub_status(hub_status_file: Path, rule_status: dict[str, object]) -> None:
    try:
        hub_status = json.loads(hub_status_file.read_text(encoding="utf-8"))
    except FileNotFoundError:
        hub_status = {"generated_at": now_iso(), "verified_at": "not verified", "items": []}
    items = hub_status.get("items")
    if not isinstance(items, list):
        items = []
        hub_status["items"] = items
    summary = rule_status["summary"]
    rule_items = rule_status["items"]
    assert isinstance(summary, dict)
    assert isinstance(rule_items, list)
    failed = int(summary.get("failed", 0) or 0)
    stale = int(summary.get("stale", 0) or 0)
    mirror_item = {
        "id": "rule-mirror",
        "title": "Third-party rule mirrors",
        "status": "failed" if failed else "stale" if stale else "ok",
        "url_hash": "",
        "last_checked": rule_status.get("generated_at") or now_iso(),
        "bytes": sum(int(item.get("bytes") or 0) for item in rule_items),
        "sha256": combined_hash(rule_items),
        "shape": f"rule-mirror:{summary.get('ok', 0)}/{summary.get('total', 0)}",
        "summary": summary,
    }
    for index, item in enumerate(items):
        if isinstance(item, dict) and item.get("id") == "rule-mirror":
            items[index] = mirror_item
            break
    else:
        items.append(mirror_item)
    hub_status["rule_mirror"] = {
        "generated_at": rule_status.get("generated_at"),
        "summary": summary,
        "registry": rule_status["registry"],
    }
    atomic_write(hub_status_file, (json.dumps(hub_status, ensure_ascii=False, indent=2) + "\n").encode("utf-8"))


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description="Mirror third-party rule files.")
    parser.add_argument("--registry", default=str(DEFAULT_REGISTRY))
    parser.add_argument("--contract", default=str(default_contract_path()))
    parser.add_argument("--public-dir", default=str(DEFAULT_PUBLIC_DIR))
    parser.add_argument("--rules-subdir", default="rules")
    parser.add_argument("--status-file", default="")
    parser.add_argument("--hub-status-file", default="")
    parser.add_argument("--timeout", type=int, default=30)
    parser.add_argument("--attempts", type=int, default=2, help="download attempts before retaining last-known-good")
    parser.add_argument("--retry-delay", type=float, default=2.0, help="seconds between attempts")
    parser.add_argument("--check", action="store_true", help="validate contract and registry only")
    parser.add_argument("--sync", action="store_true", help="download and atomically update active mirror files")
    args = parser.parse_args(argv)
    if args.check == args.sync:
        raise SystemExit("choose exactly one of --check or --sync")

    try:
        contract = load_contract(Path(args.contract))
    except ValueError as exc:
        raise SystemExit(str(exc)) from exc
    registry = load_registry(Path(args.registry), contract)
    active = active_items(registry)
    counts = derived_registry_counts(registry)
    if args.check:
        print("OK: AI routing contract and rule registry valid")
        print("active_rule_count=" + str(counts["total"]))
        print("disabled_rule_count=" + str(counts["disabled"]))
        clients = counts["clients"]
        assert isinstance(clients, dict)
        print("clients=" + ",".join(f"{key}:{value}" for key, value in sorted(clients.items())))
        return 0

    public_dir = Path(args.public_dir)
    rules_dir = public_dir / args.rules_subdir.strip("/")
    status_file = Path(args.status_file) if args.status_file else rules_dir / "status.json"
    hub_status_file = Path(args.hub_status_file) if args.hub_status_file else public_dir / "status.json"
    statuses = [sync_one(item, rules_dir, contract, args.timeout, args.attempts, args.retry_delay) for item in active]
    rule_status = write_rule_status(status_file, statuses, registry)
    merge_hub_status(hub_status_file, rule_status)
    summary = rule_status["summary"]
    assert isinstance(summary, dict)
    print(
        "rule_mirror "
        + " ".join(f"{key}={summary[key]}" for key in ("total", "ok", "stale", "failed"))
        + " sha256="
        + combined_hash(statuses)
    )
    return 1 if int(summary.get("failed", 0) or 0) else 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
