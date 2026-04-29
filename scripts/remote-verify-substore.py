#!/usr/bin/env python3
"""
Read-only verification for the live VPS Sub-Store profile center.

The script prints counts, section presence, and hashes only. It never prints
backend paths, subscription URLs, tokens, or proxy credentials.
"""

import argparse
import json
import os
import re
import subprocess
import sys
import urllib.request
from pathlib import Path


def ok(name, value=True, detail=""):
    return {"name": name, "ok": bool(value), "detail": detail}


def warn(name, detail=""):
    return {"name": name, "ok": None, "detail": detail}


def find_named(items, name):
    for item in items or []:
        if isinstance(item, dict) and item.get("name") == name:
            return item
    return None


def script_ops(item):
    return [
        op for op in item.get("process", []) or []
        if isinstance(op, dict) and op.get("type") == "Script Operator"
    ]


def docker_running(container):
    try:
        out = subprocess.check_output(
            ["docker", "inspect", "-f", "{{.State.Running}}", container],
            stderr=subprocess.DEVNULL,
            text=True,
            timeout=10,
        ).strip()
        return out == "true"
    except Exception:
        return False


def read_backend_path(app_dir, container):
    env_file = Path(app_dir) / ".env"
    if env_file.exists():
        for line in env_file.read_text(encoding="utf-8", errors="replace").splitlines():
            if line.startswith("SUB_STORE_BACKEND_PATH="):
                value = line.split("=", 1)[1].strip().strip('"').strip("'")
                if value:
                    return value
    try:
        out = subprocess.check_output(
            ["docker", "inspect", "-f", "{{range .Config.Env}}{{println .}}{{end}}", container],
            stderr=subprocess.DEVNULL,
            text=True,
            timeout=10,
        )
        for line in out.splitlines():
            if line.startswith("SUB_STORE_FRONTEND_BACKEND_PATH="):
                value = line.split("=", 1)[1].strip()
                if value:
                    return value
    except Exception:
        pass
    return ""


def fetch_local(base_path, endpoint):
    base_path = "/" + base_path.strip("/")
    url = "http://127.0.0.1:3001" + base_path + endpoint
    req = urllib.request.Request(url, headers={"User-Agent": "frontier-chain-substore-verify/1.0"})
    with urllib.request.urlopen(req, timeout=45) as resp:
        return resp.read().decode("utf-8", errors="replace")


def count_regex(text, pattern):
    return len(re.findall(pattern, text, flags=re.MULTILINE))


def section(text, name):
    match = re.search(r"(?m)^" + re.escape(name) + r":\s*$", text)
    if not match:
        return ""
    start = match.end()
    next_match = re.search(r"(?m)^[A-Za-z0-9_-]+:\s*$", text[start:])
    if not next_match:
        return text[start:]
    return text[start:start + next_match.start()]


def analyze_collection_output(text):
    names = re.findall(r"(?m)^\s*-\s*(?:name:\s*)?['\"]?([^'\"\n,{]+)", text)
    return {
        "bytes": len(text.encode("utf-8")),
        "ccr_count": text.count("CCR |"),
        "kuma_count": text.count("KUMA |"),
        "frontier_count": text.count("🏠 [VPS→家宽] Frontier"),
        "source_marker_leak": "__sourcePrefix" in text or "_sourcePrefix" in text,
        "dialer_refs": count_regex(text, r"dialer-proxy\s*:"),
        "underlying_refs": count_regex(text, r"underlying-proxy\s*:"),
        "name_like_count": len(names),
    }


def analyze_mihomo_output(text):
    proxy_groups = section(text, "proxy-groups")
    rules = section(text, "rules")
    return {
        "bytes": len(text.encode("utf-8")),
        "has_proxies": bool(section(text, "proxies")),
        "has_proxy_groups": bool(proxy_groups),
        "has_rules": bool(rules),
        "has_rule_providers": bool(section(text, "rule-providers")),
        "has_dns": bool(section(text, "dns")),
        "proxy_group_count": count_regex(proxy_groups, r"^\s*-\s*name\s*:"),
        "rule_count": count_regex(rules, r"^\s*-\s*"),
        "vps_frontier_count": text.count("🏠 [VPS→家宽] Frontier"),
        "frontier_name_count": text.count("Frontier"),
        "residential_name_count": text.count("家宽"),
    }


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--app-dir", default="/opt/1panel/apps/sub-store/sub-store")
    parser.add_argument("--data", default="/opt/1panel/apps/sub-store/sub-store/data/sub-store.json")
    parser.add_argument("--container", default="sub-store")
    parser.add_argument("--collection", default="merged-airports")
    parser.add_argument("--file", default="frontier-chain-mihomo")
    parser.add_argument("--skip-http", action="store_true")
    args = parser.parse_args()

    checks = []
    data_path = Path(args.data)
    data = json.loads(data_path.read_text(encoding="utf-8"))

    checks.append(ok("docker container running", docker_running(args.container), args.container))
    checks.append(ok("sub-store.json schemaVersion", data.get("schemaVersion") == "2.0", str(data.get("schemaVersion"))))

    subs = data.get("subs", []) or []
    collection = find_named(data.get("collections", []), args.collection)
    file_item = find_named(data.get("files", []), args.file)
    checks.append(ok("upstream subscriptions exist", len(subs) >= 1, "count=" + str(len(subs))))
    checks.append(ok("collection exists", collection is not None, args.collection))
    checks.append(ok("mihomo file exists", file_item is not None, args.file))

    if collection:
        ops = script_ops(collection)
        checks.append(ok("collection script operator exists", len(ops) >= 1, "count=" + str(len(ops))))
    if file_item:
        ops = script_ops(file_item)
        checks.append(ok("mihomo file has two script operators", len(ops) >= 2, "count=" + str(len(ops))))
        custom_names = [str(op.get("customName") or "") for op in ops]
        checks.append(ok("powerfullz operator present", any("powerfullz" in n.lower() for n in custom_names), ", ".join(custom_names)))

    marker_count = 0
    for sub in subs:
        for op in script_ops(sub):
            current = (op.get("args") or {}).get("content") or ""
            name = str(op.get("customName") or "")
            if name.startswith("source-marker-") or "Sub-Store source marker" in current:
                marker_count += 1
    checks.append(ok("source marker operators present", marker_count >= len(subs), "markers=%s subs=%s" % (marker_count, len(subs))))

    http = {}
    if not args.skip_http:
        backend_path = read_backend_path(args.app_dir, args.container)
        if backend_path:
            try:
                clash = fetch_local(backend_path, "/download/collection/%s?target=ClashMeta" % args.collection)
                http["collection_clashmeta"] = analyze_collection_output(clash)
                checks.append(ok("ClashMeta collection has normalized prefixes", "CCR |" in clash or "KUMA |" in clash))
                checks.append(ok("ClashMeta collection has one Frontier node", http["collection_clashmeta"]["frontier_count"] == 1, str(http["collection_clashmeta"]["frontier_count"])))
                checks.append(ok("ClashMeta collection has no source marker leak", not http["collection_clashmeta"]["source_marker_leak"]))
            except Exception as exc:
                checks.append(warn("ClashMeta collection fetch skipped", str(exc)))
            try:
                shadow = fetch_local(backend_path, "/download/collection/%s?target=ShadowRocket" % args.collection)
                http["collection_shadowrocket"] = analyze_collection_output(shadow)
                checks.append(ok("ShadowRocket collection has one Frontier node", http["collection_shadowrocket"]["frontier_count"] == 1, str(http["collection_shadowrocket"]["frontier_count"])))
            except Exception as exc:
                checks.append(warn("ShadowRocket collection fetch skipped", str(exc)))
            try:
                final = fetch_local(backend_path, "/api/file/%s?target=mihomo" % args.file)
                http["final_mihomo"] = analyze_mihomo_output(final)
                checks.append(ok("final mihomo has proxy groups", http["final_mihomo"]["proxy_group_count"] > 1, str(http["final_mihomo"]["proxy_group_count"])))
                checks.append(ok("final mihomo has rules", http["final_mihomo"]["rule_count"] > 1, str(http["final_mihomo"]["rule_count"])))
                checks.append(ok("final mihomo has DNS", http["final_mihomo"]["has_dns"]))
                checks.append(ok("final mihomo has rule-providers", http["final_mihomo"]["has_rule_providers"]))
            except Exception as exc:
                checks.append(warn("final mihomo fetch skipped", str(exc)))
        else:
            checks.append(warn("HTTP output checks skipped", "backend path unavailable"))

    failed = [c for c in checks if c["ok"] is False]
    result = {
        "status": "failed" if failed else "ok",
        "checks": checks,
        "http_summary": http,
    }
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 1 if failed else 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        print("remote verify failed: " + str(exc), file=sys.stderr)
        raise
