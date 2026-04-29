#!/usr/bin/env python3
"""
Apply frontier-chain-skeleton source files to a live Sub-Store data file.

This script is intended to run on the VPS. It updates only non-secret Script
Operator content and preserves existing operator arguments, tokens, and upstream
subscription URLs.
"""

import argparse
import hashlib
import json
import os
import shutil
import stat
import subprocess
import sys
import time
from pathlib import Path


def read_text(path):
    return Path(path).read_text(encoding="utf-8")


def short_hash(text):
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:12]


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


def set_script_content(op, content, custom_name):
    args = op.setdefault("args", {})
    old = args.get("content") or ""
    args["mode"] = "script"
    args["content"] = content
    args.setdefault("arguments", {})
    op["customName"] = custom_name
    op["disabled"] = False
    return old != content


def update_source_markers(data, content):
    changed = []
    for sub in data.get("subs", []) or []:
        for op in script_ops(sub):
            name = str(op.get("customName") or op.get("name") or "")
            current = (op.get("args") or {}).get("content") or ""
            if name.startswith("source-marker-") or "Sub-Store source marker" in current:
                if set_script_content(op, content, name or "source-marker"):
                    changed.append(sub.get("name") or "<unnamed-sub>")
    return changed


def update_collection_nodes(data, collection_name, content):
    collection = find_named(data.get("collections", []), collection_name)
    if not collection:
        raise RuntimeError("missing collection: " + collection_name)
    ops = script_ops(collection)
    if not ops:
        raise RuntimeError(collection_name + " has no Script Operator")
    op = ops[-1]
    changed = set_script_content(op, content, "frontier-chain nodes normalizer")
    return [collection_name] if changed else []


def update_mihomo_main(data, file_name, content):
    file_item = find_named(data.get("files", []), file_name)
    if not file_item:
        raise RuntimeError("missing file: " + file_name)
    ops = script_ops(file_item)
    candidates = [
        op for op in ops
        if "powerfullz" not in str(op.get("customName") or "").lower()
    ]
    if not candidates:
        raise RuntimeError(file_name + " has no non-powerfullz Script Operator")
    op = candidates[-1]
    changed = set_script_content(op, content, "frontier-chain-skeleton main.js")
    return [file_name] if changed else []


def install_powerfullz_updater(src, app_dir):
    dst = Path(app_dir) / "update-powerfullz-inline.py"
    old = dst.read_text(encoding="utf-8") if dst.exists() else ""
    new = read_text(src)
    if old == new:
        return False, dst
    shutil.copy2(src, dst)
    mode = dst.stat().st_mode
    dst.chmod(mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
    return True, dst


def backup_data(data_path, backup_dir):
    Path(backup_dir).mkdir(parents=True, exist_ok=True)
    stamp = time.strftime("%Y%m%d-%H%M%S")
    backup = Path(backup_dir) / ("sub-store.json.bak-deploy-" + stamp)
    shutil.copy2(data_path, backup)
    return backup


def write_json_atomic(path, data):
    path = Path(path)
    tmp = path.with_suffix(path.suffix + ".tmp-deploy-" + time.strftime("%Y%m%d-%H%M%S"))
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    os.replace(tmp, path)


def restart_container(container):
    subprocess.run(["docker", "restart", container], check=True, stdout=subprocess.DEVNULL)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--app-dir", required=True)
    parser.add_argument("--data", required=True)
    parser.add_argument("--backup-dir", required=True)
    parser.add_argument("--container", default="sub-store")
    parser.add_argument("--collection", default="merged-airports")
    parser.add_argument("--file", default="frontier-chain-mihomo")
    parser.add_argument("--source-marker")
    parser.add_argument("--nodes-injector")
    parser.add_argument("--mihomo-main")
    parser.add_argument("--powerfullz-updater")
    parser.add_argument("--no-backup", action="store_true")
    parser.add_argument("--no-restart", action="store_true")
    args = parser.parse_args()

    data_path = Path(args.data)
    data = json.loads(data_path.read_text(encoding="utf-8"))
    changes = []

    if args.source_marker:
        content = read_text(args.source_marker)
        names = update_source_markers(data, content)
        changes.append({
            "target": "source-marker",
            "changed": names,
            "bytes": len(content.encode("utf-8")),
            "sha256": short_hash(content),
        })

    if args.nodes_injector:
        content = read_text(args.nodes_injector)
        names = update_collection_nodes(data, args.collection, content)
        changes.append({
            "target": "nodes-injector",
            "changed": names,
            "bytes": len(content.encode("utf-8")),
            "sha256": short_hash(content),
        })

    if args.mihomo_main:
        content = read_text(args.mihomo_main)
        names = update_mihomo_main(data, args.file, content)
        changes.append({
            "target": "mihomo-main",
            "changed": names,
            "bytes": len(content.encode("utf-8")),
            "sha256": short_hash(content),
        })

    updater_changed = False
    if args.powerfullz_updater:
        updater_changed, dst = install_powerfullz_updater(args.powerfullz_updater, args.app_dir)
        changes.append({
            "target": "powerfullz-updater",
            "changed": [str(dst)] if updater_changed else [],
            "bytes": Path(args.powerfullz_updater).stat().st_size,
            "sha256": short_hash(read_text(args.powerfullz_updater)),
        })

    json_changed = any(item["changed"] for item in changes if item["target"] != "powerfullz-updater")
    if not json_changed and not updater_changed:
        print(json.dumps({"status": "unchanged", "changes": changes}, ensure_ascii=False, indent=2))
        return 0

    backup = None
    if json_changed:
        if not args.no_backup:
            backup = backup_data(data_path, args.backup_dir)
        write_json_atomic(data_path, data)

    if json_changed and not args.no_restart:
        restart_container(args.container)

    print(json.dumps({
        "status": "updated",
        "backup": backup.name if backup else None,
        "restarted": bool(json_changed and not args.no_restart),
        "changes": changes,
    }, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        print("remote apply failed: " + str(exc), file=sys.stderr)
        raise
