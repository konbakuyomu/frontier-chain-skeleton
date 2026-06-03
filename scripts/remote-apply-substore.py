#!/usr/bin/env python3
"""
Apply frontier-chain-skeleton source files to a live Sub-Store data file.

This script is intended to run on the VPS. It updates non-secret Script
Operator content, can add one residential upstream subscription from stdin, and
preserves existing tokens plus unrelated upstream subscription URLs.
"""

import argparse
import copy
import hashlib
import json
import os
import secrets
import shutil
import stat
import subprocess
import sys
import time
from pathlib import Path


OBSOLETE_RESIDENTIAL_ARG_PREFIXES = ("frontier_", "scrapegw_", "vps_")
DEFAULT_AGGREGATOR_NAME = "aggregated-residential"
DEFAULT_AGGREGATOR_DISPLAY_NAME = "家宽聚合订阅"
DEFAULT_AGGREGATOR_SOURCE_PREFIX = "AGG"
DEFAULT_MIHOMO_DISPLAY_NAME = "家宽选择层 mihomo 配置"
DEFAULT_MIHOMO_CONTENT_PLACEHOLDER = "# Sub-Store mihomoProfile placeholder\n"
IOS_AIRPORTS_COLLECTION = "ios-airports-uri"
IOS_AIRPORTS_DISPLAY_NAME = "iOS Shadowrocket 节点订阅 - 普通机场与家宽 URI"
IOS_EVOXT_HY2_COLLECTION = "ios-evoxt-hy2-shadowrocket"
IOS_EVOXT_HY2_DISPLAY_NAME = "iOS Shadowrocket 节点订阅 - Evoxt HY2"


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


def clear_obsolete_residential_args(op):
    args = op.setdefault("args", {})
    arguments = args.setdefault("arguments", {})
    if not isinstance(arguments, dict):
        args["arguments"] = {}
        return True
    removed = []
    for key in list(arguments.keys()):
        if any(str(key).startswith(prefix) for prefix in OBSOLETE_RESIDENTIAL_ARG_PREFIXES):
            removed.append(key)
            del arguments[key]
    return bool(removed)


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
    changed = clear_obsolete_residential_args(op) or changed
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
    changed = clear_obsolete_residential_args(op) or changed
    if file_item.get("content") != DEFAULT_MIHOMO_CONTENT_PLACEHOLDER:
        file_item["content"] = DEFAULT_MIHOMO_CONTENT_PLACEHOLDER
        changed = True
    if file_item.get("source") != "local":
        file_item["source"] = "local"
        changed = True
    if file_item.get("displayName") != DEFAULT_MIHOMO_DISPLAY_NAME:
        file_item["displayName"] = DEFAULT_MIHOMO_DISPLAY_NAME
        changed = True
    if file_item.get("display-name") != DEFAULT_MIHOMO_DISPLAY_NAME:
        file_item["display-name"] = DEFAULT_MIHOMO_DISPLAY_NAME
        changed = True
    return [file_name] if changed else []


def make_quick_setting_operator():
    return {
        "type": "Quick Setting Operator",
        "args": {
            "udp": True,
            "tfo": False,
            "scert": False,
            "vmess aead": True,
            "useless": False,
        },
    }


def make_source_marker_operator(content, source_prefix):
    return {
        "type": "Script Operator",
        "customName": "source marker: " + source_prefix,
        "disabled": False,
        "args": {
            "mode": "script",
            "content": content,
            "arguments": {"source_prefix": source_prefix},
        },
    }


def ensure_residential_subscription(
    data,
    name,
    display_name,
    url,
    source_prefix,
    source_marker_content,
    collection_name,
):
    changed = []
    subs = data.setdefault("subs", [])
    sub = find_named(subs, name)
    if sub is None:
        sub = {
            "name": name,
            "display-name": display_name,
            "displayName": display_name,
            "source": "remote",
            "url": url,
            "content": "",
            "form": "",
            "ua": "",
            "mergeSources": "",
            "passThroughUA": False,
            "ignoreFailedRemoteSub": False,
            "isIconColor": True,
            "icon": "",
            "tag": [],
            "subscriptionTags": [],
            "process": [
                make_quick_setting_operator(),
                make_source_marker_operator(source_marker_content, source_prefix),
            ],
        }
        subs.append(sub)
        changed.append("sub-created:" + name)
    else:
        if sub.get("url") != url:
            sub["url"] = url
            changed.append("sub-url-updated:" + name)
        if sub.get("display-name") != display_name:
            sub["display-name"] = display_name
            changed.append("sub-display-updated:" + name)
        if sub.get("displayName") != display_name:
            sub["displayName"] = display_name
            changed.append("sub-displayName-updated:" + name)
        sub.setdefault("source", "remote")
        sub.setdefault("content", "")
        sub.setdefault("form", "")
        sub.setdefault("tag", [])
        sub.setdefault("subscriptionTags", [])
        ops = script_ops(sub)
        marker_ops = [
            op for op in ops
            if str(op.get("customName") or "").startswith("source marker:")
            or "Sub-Store source marker" in str((op.get("args") or {}).get("content") or "")
        ]
        if not marker_ops:
            sub.setdefault("process", []).append(make_source_marker_operator(source_marker_content, source_prefix))
            changed.append("source-marker-added:" + name)
        else:
            op = marker_ops[-1]
            if set_script_content(op, source_marker_content, "source marker: " + source_prefix):
                changed.append("source-marker-content-updated:" + name)
            args = op.setdefault("args", {})
            arguments = args.setdefault("arguments", {})
            if arguments.get("source_prefix") != source_prefix:
                arguments["source_prefix"] = source_prefix
                changed.append("source-marker-prefix-updated:" + name)

    collection = find_named(data.get("collections", []), collection_name)
    if not collection:
        raise RuntimeError("missing collection: " + collection_name)
    subscriptions = collection.setdefault("subscriptions", [])
    if name not in subscriptions:
        subscriptions.append(name)
        changed.append("collection-linked:" + collection_name)
    return changed


def ensure_collection_variant(data, source_collection_name, name, display_name, subscriptions, remark):
    source = find_named(data.get("collections", []), source_collection_name)
    if not source:
        raise RuntimeError("missing collection: " + source_collection_name)
    collections = data.setdefault("collections", [])
    item = find_named(collections, name)
    changed = []
    if item is None:
        item = copy.deepcopy(source)
        item["name"] = name
        collections.append(item)
        changed.append("collection-created:" + name)
    for key, value in (
        ("displayName", display_name),
        ("display-name", display_name),
        ("subscriptions", subscriptions),
        ("remark", remark),
        ("firstSubFlow", True),
    ):
        if item.get(key) != value:
            item[key] = value
            changed.append("collection-updated:" + name + ":" + key)
    item.setdefault("tag", [])
    item.setdefault("subscriptionTags", [])
    return changed


def ensure_collection_share_token(data, name, display_name):
    tokens = data.setdefault("tokens", [])
    item = None
    for token in tokens:
        if isinstance(token, dict) and token.get("type") == "col" and token.get("name") == name:
            item = token
            break
    changed = []
    if item is None:
        now = int(time.time() * 1000)
        item = {
            "type": "col",
            "name": name,
            "displayName": display_name,
            "remark": "",
            "tag": [],
            "token": secrets.token_urlsafe(18),
            "createdAt": now,
            "mode": "duration",
            "expiresIn": "1095d",
            "exp": now + 1095 * 24 * 60 * 60 * 1000,
        }
        tokens.append(item)
        changed.append("token-created:" + name)
    else:
        if item.get("displayName") != display_name:
            item["displayName"] = display_name
            changed.append("token-display-updated:" + name)
        if not item.get("token"):
            item["token"] = secrets.token_urlsafe(18)
            changed.append("token-created:" + name)
        item.setdefault("mode", "duration")
        item.setdefault("expiresIn", "1095d")
        item.setdefault("tag", [])
        item.setdefault("remark", "")
    return changed


def ensure_ios_shadowrocket_collections(data, source_collection_name, evoxt_subscription_name):
    changed = []
    changed += ensure_collection_variant(
        data,
        source_collection_name,
        IOS_AIRPORTS_COLLECTION,
        IOS_AIRPORTS_DISPLAY_NAME,
        ["ccrui", "kuma", DEFAULT_AGGREGATOR_NAME],
        "iPhone Shadowrocket 普通机场/家宽节点订阅；使用 target=URI，避免 target=ShadowRocket YAML 兼容问题。",
    )
    changed += ensure_collection_variant(
        data,
        source_collection_name,
        IOS_EVOXT_HY2_COLLECTION,
        IOS_EVOXT_HY2_DISPLAY_NAME,
        [evoxt_subscription_name],
        "iPhone Shadowrocket Evoxt HY2 专用节点订阅；使用 target=ShadowRocket，保留实机可测速的 HY2 YAML 形态。",
    )
    changed += ensure_collection_share_token(data, IOS_AIRPORTS_COLLECTION, "iOS节点-普通机场家宽-URI")
    changed += ensure_collection_share_token(data, IOS_EVOXT_HY2_COLLECTION, "iOS节点-Evoxt-HY2-ShadowRocket")
    return changed


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
    parser.add_argument("--aggregator-url-stdin", action="store_true")
    parser.add_argument("--aggregator-name", default=DEFAULT_AGGREGATOR_NAME)
    parser.add_argument("--aggregator-display-name", default=DEFAULT_AGGREGATOR_DISPLAY_NAME)
    parser.add_argument("--aggregator-source-prefix", default=DEFAULT_AGGREGATOR_SOURCE_PREFIX)
    parser.add_argument("--no-backup", action="store_true")
    parser.add_argument("--no-restart", action="store_true")
    args = parser.parse_args()

    data_path = Path(args.data)
    data = json.loads(data_path.read_text(encoding="utf-8"))
    changes = []
    source_marker_content = read_text(args.source_marker) if args.source_marker else None

    if args.aggregator_url_stdin:
        if not source_marker_content:
            raise RuntimeError("--aggregator-url-stdin requires --source-marker")
        aggregator_url = sys.stdin.read().strip()
        if not aggregator_url:
            raise RuntimeError("empty aggregator URL from stdin")
        names = ensure_residential_subscription(
            data,
            args.aggregator_name,
            args.aggregator_display_name,
            aggregator_url,
            args.aggregator_source_prefix,
            source_marker_content,
            args.collection,
        )
        changes.append({
            "target": "residential-upstream",
            "changed": names,
            "name": args.aggregator_name,
            "source_prefix": args.aggregator_source_prefix,
        })

    if args.source_marker:
        content = source_marker_content
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

    ios_names = ensure_ios_shadowrocket_collections(data, args.collection, "substore-evoxt-upstream")
    changes.append({
        "target": "ios-shadowrocket-collections",
        "changed": ios_names,
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
