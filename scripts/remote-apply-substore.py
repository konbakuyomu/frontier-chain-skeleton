#!/usr/bin/env python3
"""
Apply frontier-chain-skeleton source files to a live Sub-Store data file.

This script is intended to run on the VPS. It updates non-secret Script
Operator content, can add one residential upstream subscription from stdin, and
preserves existing tokens plus unrelated upstream subscription URLs. Client
facing iOS collections are preserved by default so a target Sub-Store can own
its upstream pool without hardcoded supplier names.
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
DEFAULT_AGGREGATOR_DISPLAY_NAME = "20-原料-家宽-聚合"
DEFAULT_AGGREGATOR_SOURCE_PREFIX = "AGG"
DEFAULT_MIHOMO_DISPLAY_NAME = "80-输出-Sparkle-FlClash-OpenClash-最终配置"
DEFAULT_MIHOMO_CONTENT_PLACEHOLDER = "# Sub-Store mihomoProfile placeholder\n"
IOS_AIRPORTS_COLLECTION = "ios-airports-uri"
IOS_AIRPORTS_DISPLAY_NAME = "81-输出-Shadowrocket-普通节点URI"
IOS_EVOXT_HY2_COLLECTION = "ios-evoxt-hy2-shadowrocket"
IOS_EVOXT_HY2_DISPLAY_NAME = "82-输出-Shadowrocket-HY2专用"
LEGACY_EVOXT_HY2_SUBSCRIPTION = "substore-evoxt-upstream"

CLIENT_COLLECTIONS = {
    "merged-airports",
    IOS_AIRPORTS_COLLECTION,
    IOS_EVOXT_HY2_COLLECTION,
}
EDGE_COLLECTIONS = {"edge-us-upstreams"}
LEGACY_VPS_LA_OBJECT_NAMES = {
    "aggregated-residential",
    "user-landing-airports",
    "vps-chain-residential",
    "vircs-att-vps-only",
    "my-home-chain",
    "测试",
}
RESIDENTIAL_TEXT_MARKERS = (
    "residential",
    "resi",
    "home",
    "broadband",
    "家宽",
    "家庭宽带",
    "家庭住宅",
    "住宅宽带",
    "住宅",
    "宽带",
    "att",
    "at&t",
)
DISPLAY_TAXONOMY_TAG = "frontier-display-v2"


def read_text(path):
    return Path(path).read_text(encoding="utf-8")


def short_hash(text):
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:12]


def find_named(items, name):
    for item in items or []:
        if isinstance(item, dict) and item.get("name") == name:
            return item
    return None


def split_names(value):
    if value is None:
        return None
    if isinstance(value, (list, tuple)):
        values = value
    else:
        text = str(value).strip()
        if not text:
            return None
        values = text.replace("\n", ",").split(",")
    names = []
    seen = set()
    for raw in values:
        name = str(raw).strip()
        if not name or name in seen:
            continue
        names.append(name)
        seen.add(name)
    return names


def collection_subscription_names(collection):
    values = []
    for item in (collection or {}).get("subscriptions", []) or []:
        if isinstance(item, str):
            values.append(item)
        elif isinstance(item, dict):
            values.append(str(item.get("name") or item.get("tag") or ""))
    return [value for value in values if value]


def has_any_marker(text, markers):
    value = str(text or "").lower()
    return any(marker.lower() in value for marker in markers)


def is_residential_object(item):
    searchable = " ".join(
        str(item.get(key) or "")
        for key in ("name", "displayName", "display-name", "remark")
    )
    return has_any_marker(searchable, RESIDENTIAL_TEXT_MARKERS)


def is_legacy_vps_la_object(item):
    name = str(item.get("name") or "")
    display = str(item.get("displayName") or item.get("display-name") or "")
    return (
        name in LEGACY_VPS_LA_OBJECT_NAMES
        or "VPS-LA" in display
        or "L3" in display
        or "L4" in display
        or "链式" in display
        or "只给VPS-LA" in display
    )


def ensure_list_field(item, key):
    values = item.get(key)
    if not isinstance(values, list):
        values = []
        item[key] = values
    return values


def add_tag(item, tag):
    changed = False
    for key in ("tag", "subscriptionTags"):
        values = ensure_list_field(item, key)
        if tag not in values:
            values.append(tag)
            changed = True
    return changed


def set_display_fields(item, display_name):
    changed = []
    for key in ("displayName", "display-name"):
        if item.get(key) != display_name:
            item[key] = display_name
            changed.append(key)
    return changed


def set_remark(item, remark):
    if item.get("remark") != remark:
        item["remark"] = remark
        return True
    return False


def display_name_for_sub(sub):
    name = str(sub.get("name") or "")
    if name == "ccrui":
        return "10-原料-普通机场-CCR"
    if name == "kuma":
        return "10-原料-普通机场-KUMA"
    if name == "substore-evoxt-upstream":
        return "30-原料-Evoxt-HY2"
    if name == "edge-us-att":
        return "20-原料-家宽-美国-AT&T"
    if name == "edge-us-roles":
        return "40-稳定角色-美国Edge家宽"
    if name == "aggregated-residential":
        return "99-历史禁用-VPS-LA-聚合家宽原料"
    if name == "my-home-chain":
        return "99-历史禁用-VPS-LA-MY家宽链式"
    if name == "my-home-chain-hy2":
        return "20-原料-家宽-马来西亚-MINE-HY2"
    if name == "测试":
        return "99-历史禁用-测试对象"
    if is_residential_object(sub):
        return "20-原料-家宽-" + name
    return None


def display_name_for_collection(collection):
    name = str(collection.get("name") or "")
    if name == "merged-airports":
        return "80-输出-三端主节点池"
    if name == IOS_AIRPORTS_COLLECTION:
        return IOS_AIRPORTS_DISPLAY_NAME
    if name == IOS_EVOXT_HY2_COLLECTION:
        return IOS_EVOXT_HY2_DISPLAY_NAME
    if name == "edge-us-upstreams":
        return "20-原料-家宽-美国Edge上游"
    if name == "user-landing-airports":
        return "99-历史禁用-VPS-LA-链式原料池"
    return None


def display_name_for_file(file_item):
    if file_item.get("name") == "frontier-chain-mihomo":
        return DEFAULT_MIHOMO_DISPLAY_NAME
    return None


def remark_for_item(item, section):
    name = str(item.get("name") or "")
    if section == "subs":
        if name in ("ccrui", "kuma"):
            return "普通机场原料；暂不角色化，仍由 Sub-Store 集合统一清洗后输出。"
        if name == "substore-evoxt-upstream":
            return "Evoxt HY2 专用原料；Shadowrocket HY2 输出单独消费，不混入普通 URI feed。"
        if name == "edge-us-att":
            return "美国 edge 的 AT&T 家宽原料；客户端只看美国 Edge 稳定角色。"
        if name == "edge-us-roles":
            return "美国 edge 生成的稳定角色节点；供主节点池和 Shadowrocket 普通节点 feed 消费。"
        if name == "my-home-chain-hy2":
            return "马来西亚 MINE 家宽 HY2 原料；可作为上游保留，客户端仍通过稳定家宽选择层消费。"
        if is_legacy_vps_la_object(item):
            return "历史 VPS-LA 链式对象；保留用于追溯，不进入日常输出池。"
        if is_residential_object(item):
            return "家宽原料；供应商可替换，客户端通过稳定家宽角色选择。"
    if section == "collections":
        if name == "merged-airports":
            return "三端普通主节点池；Sparkle/FlClash/OpenClash 通过 final Mihomo 间接消费，Shadowrocket 普通 URI feed 也从这里拆分。"
        if name == IOS_AIRPORTS_COLLECTION:
            return "Shadowrocket 普通节点 URI 输出；包含普通机场和稳定家宽角色，不含 Evoxt HY2。"
        if name == IOS_EVOXT_HY2_COLLECTION:
            return "Shadowrocket HY2 专用输出；内部名沿用旧 Evoxt 命名，不混入普通节点和美国 edge 角色。"
        if name == "edge-us-upstreams":
            return "美国 edge 家宽上游集合；只放 AT&T 和未来美国住宅上游。"
        if is_legacy_vps_la_object(item):
            return "历史 VPS-LA 链式集合；保留用于追溯，不作为日常客户端入口。"
    if section == "files" and name == "frontier-chain-mihomo":
        return "Sparkle、FlClash、OpenClash 共用的最终 Mihomo 配置输出。"
    return item.get("remark", "")


def apply_display_taxonomy(data):
    changed = []
    for section, resolver in (
        ("subs", display_name_for_sub),
        ("collections", display_name_for_collection),
        ("files", display_name_for_file),
    ):
        for item in data.get(section, []) or []:
            if not isinstance(item, dict):
                continue
            name = item.get("name") or "<unnamed>"
            display = resolver(item)
            if display:
                fields = set_display_fields(item, display)
                if fields:
                    changed.append("%s-display:%s:%s" % (section, name, ",".join(fields)))
            remark = remark_for_item(item, section)
            if remark and set_remark(item, remark):
                changed.append("%s-remark:%s" % (section, name))
            if add_tag(item, DISPLAY_TAXONOMY_TAG):
                changed.append("%s-tag:%s" % (section, name))
            if section == "subs" and item.get("source") == "remote" and is_residential_object(item):
                if item.get("ignoreFailedRemoteSub") is not True:
                    item["ignoreFailedRemoteSub"] = True
                    changed.append("sub-ignore-failed-enabled:" + str(name))
            if section == "collections" and (
                item.get("name") in EDGE_COLLECTIONS or is_residential_object(item)
            ):
                if item.get("ignoreFailedRemoteSub") is not True:
                    item["ignoreFailedRemoteSub"] = True
                    changed.append("collection-ignore-failed-enabled:" + str(name))
    return changed


def existing_collection_subscriptions(data, collection_name):
    collection = find_named(data.get("collections", []), collection_name)
    if not collection:
        return None
    names = collection_subscription_names(collection)
    return names or None


def subscription_exists(data, subscription_name):
    return find_named(data.get("subs", []), subscription_name) is not None


def resolve_ios_hy2_subscriptions(data, explicit_names):
    if explicit_names is not None:
        return explicit_names
    existing = existing_collection_subscriptions(data, IOS_EVOXT_HY2_COLLECTION)
    if existing is not None:
        return existing
    if subscription_exists(data, LEGACY_EVOXT_HY2_SUBSCRIPTION):
        return [LEGACY_EVOXT_HY2_SUBSCRIPTION]
    return []


def resolve_ios_airports_subscriptions(data, source_collection_name, explicit_names, hy2_names):
    if explicit_names is not None:
        return explicit_names
    existing = existing_collection_subscriptions(data, IOS_AIRPORTS_COLLECTION)
    if existing is not None:
        return existing
    source = find_named(data.get("collections", []), source_collection_name)
    if not source:
        raise RuntimeError("missing collection: " + source_collection_name)
    hy2_set = set(hy2_names or [])
    return [
        name for name in collection_subscription_names(source)
        if name not in hy2_set
    ]


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
            "ignoreFailedRemoteSub": True,
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
        if sub.get("ignoreFailedRemoteSub") is not True:
            sub["ignoreFailedRemoteSub"] = True
            changed.append("sub-ignore-failed-enabled:" + name)
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


def set_collection_script_argument(data, collection_name, key, value):
    collection = find_named(data.get("collections", []), collection_name)
    if not collection:
        return []
    ops = script_ops(collection)
    if not ops:
        return []
    op = ops[-1]
    args = op.setdefault("args", {})
    arguments = args.setdefault("arguments", {})
    if not isinstance(arguments, dict):
        arguments = {}
        args["arguments"] = arguments
    if arguments.get(key) == value:
        return []
    arguments[key] = value
    return ["collection-script-argument:%s:%s" % (collection_name, key)]


def update_ios_collection_scripts(data, content):
    changed = []
    for collection_name, profile in (
        (IOS_AIRPORTS_COLLECTION, "ordinary"),
        (IOS_EVOXT_HY2_COLLECTION, "hy2"),
    ):
        collection = find_named(data.get("collections", []), collection_name)
        if not collection:
            continue
        ops = script_ops(collection)
        if not ops:
            continue
        op = ops[-1]
        if set_script_content(op, content, "frontier-chain nodes normalizer"):
            changed.append("ios-collection-script-updated:" + collection_name)
        changed += set_collection_script_argument(data, collection_name, "collection_profile", profile)
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


def ensure_ios_shadowrocket_collections(
    data,
    source_collection_name,
    ios_airports_subscriptions=None,
    ios_hy2_subscriptions=None,
):
    changed = []
    resolved_hy2 = resolve_ios_hy2_subscriptions(data, ios_hy2_subscriptions)
    resolved_airports = resolve_ios_airports_subscriptions(
        data,
        source_collection_name,
        ios_airports_subscriptions,
        resolved_hy2,
    )
    changed += ensure_collection_variant(
        data,
        source_collection_name,
        IOS_AIRPORTS_COLLECTION,
        IOS_AIRPORTS_DISPLAY_NAME,
        resolved_airports,
        "iPhone Shadowrocket 普通机场/家宽节点订阅；使用 target=URI，避免 target=ShadowRocket YAML 兼容问题。",
    )
    changed += set_collection_script_argument(data, IOS_AIRPORTS_COLLECTION, "collection_profile", "ordinary")
    changed += ensure_collection_variant(
        data,
        source_collection_name,
        IOS_EVOXT_HY2_COLLECTION,
        IOS_EVOXT_HY2_DISPLAY_NAME,
        resolved_hy2,
        "iPhone Shadowrocket HY2 专用节点订阅；内部名沿用旧 Evoxt 命名，使用 target=ShadowRocket，保留实机可测速的 HY2 YAML 形态。",
    )
    changed += set_collection_script_argument(data, IOS_EVOXT_HY2_COLLECTION, "collection_profile", "hy2")
    changed += ensure_collection_share_token(data, IOS_AIRPORTS_COLLECTION, "iOS节点-普通机场家宽-URI")
    changed += ensure_collection_share_token(data, IOS_EVOXT_HY2_COLLECTION, "iOS节点-HY2专用-ShadowRocket")
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
    parser.add_argument("--ios-airports-subscriptions", default="")
    parser.add_argument("--ios-hy2-subscriptions", "--ios-hy2-subscription", dest="ios_hy2_subscriptions", default="")
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
        names += update_ios_collection_scripts(data, content)
        changes.append({
            "target": "nodes-injector",
            "changed": names,
            "bytes": len(content.encode("utf-8")),
            "sha256": short_hash(content),
        })

    ios_names = ensure_ios_shadowrocket_collections(
        data,
        args.collection,
        split_names(args.ios_airports_subscriptions),
        split_names(args.ios_hy2_subscriptions),
    )
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

    taxonomy_changes = apply_display_taxonomy(data)
    changes.append({
        "target": "display-taxonomy",
        "changed": taxonomy_changes,
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
