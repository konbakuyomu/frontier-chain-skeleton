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
import base64
import subprocess
import sys
import tempfile
import urllib.parse
import urllib.request
from pathlib import Path


RESIDENTIAL_PATTERN = re.compile(
    r"[Rr]esi(dential)?|[Hh]ome[-_ ]?[Ii][Pp]|[Hh]ome[-_ ]?[Bb]roadband|[Bb]roadband|[Ii][Ss][Pp]|家宽|家庭宽带|家庭住宅|住宅宽带|住宅|宽带"
)
EXCLUDE_INFO_PATTERN = re.compile(
    r"导航|剩余|套餐|到期|重置|官网|订阅|回国|回程|国内专线|地址|保底|客服|流量|距离下次|不可直连|小白不要连接"
)
FORBIDDEN_RUNTIME_TERMS = ["[VPS→家宽]", "[机场→家宽]", "🏠 [VPS→家宽]", "🏠 [机场→家宽]", "Frontier", "ScrapeGW"]
OBSOLETE_ARG_PREFIXES = ("frontier_", "scrapegw_", "vps_")
DEFAULT_TIMEOUT_RESIDENTIAL_NAMES = [
    "cf加速|越南动态家宽🇻🇳",
    "越南-cf加速 动态 🇻🇳-家宽",
    "cf加速|美国备用家宽一🇺🇸",
    "美国-cf加速 备用 一🇺🇸-家宽",
    "cf加速|美国备用动态家宽三🇺🇸",
    "美国-cf加速 备用动态 三🇺🇸-家宽",
    "【5x】中转|美国备用家宽🇺🇸",
    "美国-【5x】中转 备用 🇺🇸-家宽",
    "【5x】中转|加拿大家宽🇨🇦",
    "加拿大-【5x】中转-家宽",
    "【5x】中转|韩国KT家宽",
    "韩国-【5x】中转 KT-家宽",
    "美国-密西西比州Comcast家宽-001",
    "【备用-2】美国AT&T备用家宽vless🇺🇸",
    "美国-【备用-2】 AT&T备用 🇺🇸-家宽",
    "新英国家宽🇬🇧vless",
    "英国-新英-家宽",
    "专线|尼日利亚家宽🇳🇬",
    "尼日利亚-专线-家宽",
    "尼日利亚家宽🇳🇬hy2",
    "尼日利亚-🇳🇬hy2-家宽",
]


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


def obsolete_argument_keys(item):
    keys = []
    for op in script_ops(item or {}):
        arguments = (op.get("args") or {}).get("arguments") or {}
        if not isinstance(arguments, dict):
            continue
        for key in arguments.keys():
            if any(str(key).startswith(prefix) for prefix in OBSOLETE_ARG_PREFIXES):
                keys.append(str(key))
    return sorted(set(keys))


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


def forbidden_counts(text):
    return {term: text.count(term) for term in FORBIDDEN_RUNTIME_TERMS if text.count(term)}


def normalize_visible_text(text):
    """Normalize YAML/JSON escaped display names before text-based checks."""
    return (
        text
        .replace("\\U0001F3E1", "🏡")
        .replace("\\uD83C\\uDFE1", "🏡")
        .replace("\\ud83c\\udfe1", "🏡")
    )


def pad_base64(value):
    return value + "=" * (-len(value) % 4)


def extract_vmess_name(uri):
    payload = uri[len("vmess://"):].strip()
    try:
        decoded = base64.b64decode(pad_base64(payload)).decode("utf-8", errors="replace")
        data = json.loads(decoded)
        name = data.get("ps") or data.get("name")
        return str(name).strip() if name else ""
    except Exception:
        return ""


def extract_proxy_names(text):
    names = []
    for match in re.finditer(r'"name"\s*:\s*"((?:\\.|[^"\\])*)"', text):
        try:
            names.append(json.loads('"' + match.group(1) + '"'))
        except Exception:
            names.append(match.group(1))
    if names:
        return [name for name in names if name]
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        match = re.match(r"^-\s*name\s*:\s*['\"]?(.+?)['\"]?\s*$", stripped)
        if match:
            names.append(match.group(1).strip().strip("'\""))
            continue
        match = re.match(r"^-\s*\{\s*name\s*:\s*['\"]?([^,'\"}]+)", stripped)
        if match:
            names.append(match.group(1).strip().strip("'\""))
            continue
        match = re.match(r"^name\s*:\s*['\"]?(.+?)['\"]?\s*$", stripped)
        if match:
            names.append(match.group(1).strip().strip("'\""))
            continue
        if stripped.startswith("vmess://"):
            name = extract_vmess_name(stripped)
            if name:
                names.append(name)
            continue
        if "://" in stripped and "#" in stripped:
            fragment = stripped.rsplit("#", 1)[1]
            if fragment:
                names.append(urllib.parse.unquote(fragment).strip())
    return [name for name in names if name]


def name_quality(names):
    timeout_hits = [name for name in names if name in DEFAULT_TIMEOUT_RESIDENTIAL_NAMES]
    pseudo_hits = [name for name in names if EXCLUDE_INFO_PATTERN.search(name)]
    residential = [name for name in names if RESIDENTIAL_PATTERN.search(name) and not EXCLUDE_INFO_PATTERN.search(name)]
    return {
        "names_count": len(names),
        "unique_names_count": len(set(names)),
        "residential_candidate_count": len(residential),
        "pseudo_or_non_direct_count": len(pseudo_hits),
        "timeout_residential_count": len(timeout_hits),
    }


def analyze_collection_output(text):
    names = extract_proxy_names(text)
    quality = name_quality(names)
    quality.update({
        "bytes": len(text.encode("utf-8")),
        "ccr_count": text.count("CCR |"),
        "kuma_count": text.count("KUMA |"),
        "agg_count": text.count("AGG |"),
        "forbidden_counts": forbidden_counts(text),
        "source_marker_leak": "__sourcePrefix" in text or "_sourcePrefix" in text,
        "dialer_refs": count_regex(text, r"dialer-proxy\s*:"),
        "underlying_refs": count_regex(text, r"underlying-proxy\s*:"),
    })
    return quality


def analyze_uri_output(text):
    lines = [
        line.strip()
        for line in text.splitlines()
        if line.strip() and not line.strip().startswith("#")
    ]
    scheme_counts = {}
    for line in lines:
        match = re.match(r"^([A-Za-z0-9+.-]+)://", line)
        scheme = match.group(1).lower() if match else "non-uri"
        scheme_counts[scheme] = scheme_counts.get(scheme, 0) + 1
    names = extract_proxy_names(text)
    quality = name_quality(names)
    quality.update({
        "bytes": len(text.encode("utf-8")),
        "line_count": len(lines),
        "scheme_counts": scheme_counts,
        "has_yaml_shape": any(line == "proxies:" or line.startswith("- {") for line in lines[:5]),
        "forbidden_counts": forbidden_counts(text),
    })
    return quality


def profile_check(text):
    checker = None
    for candidate in ("mihomo", "clash"):
        if shutil_which(candidate):
            checker = candidate
            break
    if not checker:
        return None, "mihomo/clash binary not found"
    temp_dir = Path(tempfile.mkdtemp(prefix="frontier-substore-check-"))
    config_path = temp_dir / "profile.yaml"
    try:
        config_path.write_text(text, encoding="utf-8")
        proc = subprocess.run(
            [checker, "-t", "-d", str(temp_dir), "-f", str(config_path)],
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            timeout=60,
        )
        return proc.returncode == 0, "checker=%s exit=%s" % (checker, proc.returncode)
    except Exception as exc:
        return False, str(exc)
    finally:
        try:
            config_path.unlink()
        except FileNotFoundError:
            pass
        except Exception:
            pass
        try:
            temp_dir.rmdir()
        except Exception:
            pass


def shutil_which(name):
    for directory in os.environ.get("PATH", "").split(os.pathsep):
        path = Path(directory) / name
        if path.exists() and os.access(path, os.X_OK):
            return str(path)
    return ""


def docker_log_issue_count(container):
    try:
        started_at = subprocess.check_output(
            ["docker", "inspect", "-f", "{{.State.StartedAt}}", container],
            stderr=subprocess.STDOUT,
            text=True,
            timeout=10,
        ).strip()
        out = subprocess.check_output(
            ["docker", "logs", container, "--since", started_at, "--tail", "200"],
            stderr=subprocess.STDOUT,
            text=True,
            timeout=20,
        )
    except Exception as exc:
        return None, str(exc)
    sanitized_lines = []
    for line in out.splitlines():
        if "Redirect loop detected" in line and "使用 HEAD 方法从响应头获取流量信息失败" in line:
            continue
        sanitized_lines.append(line)
    sanitized = "\n".join(sanitized_lines)
    hits = re.findall(r"(?i)missing|error|fail|exception", sanitized)
    return {
        "issue_count": len(hits),
        "bytes": len(out.encode("utf-8")),
    }, ""


def analyze_mihomo_output(text):
    visible_text = normalize_visible_text(text)
    proxies = section(text, "proxies")
    proxy_groups = section(text, "proxy-groups")
    visible_proxy_groups = section(visible_text, "proxy-groups")
    rules = section(text, "rules")
    rule_items = extract_rules(rules)
    names = extract_proxy_names(proxies)
    evoxt_blocks = [block for block in extract_proxy_blocks(proxies) if "L1-EVOXT" in block]
    quality = name_quality(names)
    quality.update({
        "bytes": len(text.encode("utf-8")),
        "has_proxies": bool(proxies),
        "has_proxy_groups": bool(proxy_groups),
        "has_rules": bool(rules),
        "has_rule_providers": bool(section(text, "rule-providers")),
        "has_dns": bool(section(text, "dns")),
        "proxy_group_count": count_regex(proxy_groups, r"^\s*-\s*name\s*:"),
        "rule_count": count_regex(rules, r"^\s*-\s*"),
        "evoxt_node_count": len(evoxt_blocks),
        "evoxt_hysteria2_count": sum(1 for block in evoxt_blocks if proxy_block_type(block) == "hysteria2"),
        "evoxt_hysteria2_sni_count": sum(1 for block in evoxt_blocks if proxy_block_type(block) == "hysteria2" and proxy_block_has_sni(block)),
        "evoxt_vless_count": sum(1 for block in evoxt_blocks if proxy_block_type(block) == "vless"),
        "evoxt_reality_count": sum(1 for block in evoxt_blocks if proxy_block_is_reality(block)),
        "evoxt_malaysia_name_count": sum(1 for block in evoxt_blocks if "马来西亚" in block or "Malaysia" in block),
        "has_evoxt_group": "name: Evoxt 自建" in visible_proxy_groups or "name: 'Evoxt 自建'" in visible_proxy_groups or "name: \"Evoxt 自建\"" in visible_proxy_groups,
        "evoxt_group_refs": group_body_refs(visible_proxy_groups, "Evoxt 自建", "L1-EVOXT |"),
        "evoxt_group_http_probe": group_body_refs(visible_proxy_groups, "Evoxt 自建", "http://cp.cloudflare.com/generate_204"),
        "primary_group_evoxt_refs": group_body_refs(visible_proxy_groups, "选择代理", "Evoxt 自建"),
        "global_evoxt_refs": group_body_refs(visible_proxy_groups, "GLOBAL", "Evoxt 自建"),
        "malaysia_group_evoxt_refs": group_body_refs(visible_proxy_groups, "马来西亚节点", "L1-EVOXT |"),
        "primary_group_malaysia_refs": group_body_refs(visible_proxy_groups, "选择代理", "马来西亚节点"),
        "global_malaysia_refs": group_body_refs(visible_proxy_groups, "GLOBAL", "马来西亚节点"),
        "global_us_residential_refs": group_body_refs(visible_proxy_groups, "GLOBAL", "🏡 美国家宽"),
        "global_apac_residential_refs": group_body_refs(visible_proxy_groups, "GLOBAL", "🏡 亚太家宽"),
        "residential_selector_us_refs": group_body_refs(visible_proxy_groups, "🏡 家宽选择", "🏡 美国家宽"),
        "residential_selector_apac_refs": group_body_refs(visible_proxy_groups, "🏡 家宽选择", "🏡 亚太家宽"),
        "http_probe_group_count": count_regex(proxy_groups, r"url\s*:\s*['\"]?http://cp\.cloudflare\.com/generate_204"),
        "has_residential_selector": "name: 🏡 家宽选择" in visible_proxy_groups or "name: '🏡 家宽选择'" in visible_proxy_groups or "name: \"🏡 家宽选择\"" in visible_proxy_groups,
        "residential_selector_refs": visible_text.count("🏡 家宽选择"),
        "business_selector_refs": visible_proxy_groups.count("🏡 家宽选择"),
        "has_ai_group": "name: AI服务" in visible_proxy_groups or "name: 'AI服务'" in visible_proxy_groups or "name: \"AI服务\"" in visible_proxy_groups,
        "has_paypal_group": "name: PayPal" in visible_proxy_groups or "name: 'PayPal'" in visible_proxy_groups or "name: \"PayPal\"" in visible_proxy_groups,
        "has_self_domain_group": "name: 自有域名" in visible_proxy_groups or "name: '自有域名'" in visible_proxy_groups or "name: \"自有域名\"" in visible_proxy_groups,
        "primary_group_selector_refs": group_body_refs(visible_proxy_groups, "选择代理", "🏡 家宽选择"),
        "ai_group_selector_refs": group_body_refs(visible_proxy_groups, "AI服务", "🏡 家宽选择"),
        "paypal_group_selector_refs": group_body_refs(visible_proxy_groups, "PayPal", "🏡 家宽选择"),
        "self_domain_direct_refs": group_body_refs(visible_proxy_groups, "自有域名", "DIRECT"),
        "self_domain_first_proxy": first_group_proxy(visible_proxy_groups, "自有域名"),
        "global_self_domain_refs": group_body_refs(visible_proxy_groups, "GLOBAL", "自有域名"),
        "konbakuyomu_rule_refs": rule_items.count("DOMAIN-SUFFIX,konbakuyomu.us,自有域名"),
        "wechat_direct_rule_refs": rule_items.count("DOMAIN-SUFFIX,weixin.qq.com,DIRECT"),
        "qq_direct_rule_refs": rule_items.count("DOMAIN-SUFFIX,qq.com,DIRECT"),
        "tencent_geosite_direct_refs": rule_items.count("GEOSITE,tencent,DIRECT"),
        "cn_geosite_direct_refs": rule_items.count("GEOSITE,geolocation-cn,DIRECT") + rule_items.count("GEOSITE,cn,DIRECT"),
        "domestic_direct_before_match": rule_before_match(rule_items, "DOMAIN-SUFFIX,weixin.qq.com,DIRECT"),
        "ai_group_region_residential_refs": group_body_regex_count(visible_proxy_groups, "AI服务", r"🏡 .+?家宽"),
        "paypal_group_region_residential_refs": group_body_regex_count(visible_proxy_groups, "PayPal", r"🏡 .+?家宽"),
        "forbidden_counts": forbidden_counts(visible_text),
    })
    return quality


def extract_proxy_blocks(proxies_text):
    blocks = []
    current = []
    for line in proxies_text.splitlines():
        if re.match(r"^\s*-\s+name\s*:", line):
            if current:
                blocks.append("\n".join(current))
            current = [line]
        elif current:
            current.append(line)
    if current:
        blocks.append("\n".join(current))
    return blocks


def proxy_block_type(block):
    match = re.search(r"(?m)^\s*type\s*:\s*['\"]?([^'\"\s#]+)", block)
    return match.group(1).strip().lower() if match else ""


def proxy_block_is_reality(block):
    return proxy_block_type(block) == "vless" and (
        "reality-opts" in block or re.search(r"(?i)\breality\b", block) is not None
    )


def proxy_block_has_sni(block):
    return re.search(r"(?m)^\s*(sni|servername)\s*:", block) is not None


def extract_rules(rules_text):
    return [
        match.group(1).strip().strip("'\"")
        for match in re.finditer(r"(?m)^\s*-\s*(.+?)\s*$", rules_text)
    ]


def rule_before_match(rule_items, rule):
    try:
        rule_idx = rule_items.index(rule)
    except ValueError:
        return False
    match_idx = next((i for i, item in enumerate(rule_items) if item.startswith("MATCH,")), -1)
    return match_idx < 0 or rule_idx < match_idx


def group_body(proxy_groups_text, group_name):
    pattern = r"(?m)^\s*-\s+name\s*:\s*['\"]?" + re.escape(group_name) + r"['\"]?\s*$"
    match = re.search(pattern, proxy_groups_text)
    if not match:
        return ""
    start = match.end()
    next_match = re.search(r"(?m)^\s*-\s+name\s*:", proxy_groups_text[start:])
    return proxy_groups_text[start:] if not next_match else proxy_groups_text[start:start + next_match.start()]


def group_body_refs(proxy_groups_text, group_name, needle):
    return group_body(proxy_groups_text, group_name).count(needle)


def first_group_proxy(proxy_groups_text, group_name):
    body = group_body(proxy_groups_text, group_name)
    proxies = re.search(r"(?ms)^\s*proxies\s*:\s*\n(?P<items>(?:\s*-\s+.+\n?)+)", body)
    if not proxies:
        return ""
    first = re.search(r"(?m)^\s*-\s+(.+?)\s*$", proxies.group("items"))
    return first.group(1).strip().strip("'\"") if first else ""


def group_body_regex_count(proxy_groups_text, group_name, regex):
    return len(re.findall(regex, group_body(proxy_groups_text, group_name)))


def same_names(left, right):
    if not left or not right:
        return None
    return left == right


def safe_detail_dict(data):
    return json.dumps(data, ensure_ascii=False, sort_keys=True)


def collection_subscription_names(collection):
    values = []
    for item in (collection or {}).get("subscriptions", []) or []:
        if isinstance(item, str):
            values.append(item)
        elif isinstance(item, dict):
            values.append(str(item.get("name") or item.get("tag") or ""))
    return [value for value in values if value]


def count_source_markers(subs):
    marker_count = 0
    for sub in subs:
        for op in script_ops(sub):
            current = (op.get("args") or {}).get("content") or ""
            name = str(op.get("customName") or "")
            if name.startswith("source marker:") or name.startswith("source-marker-") or "Sub-Store source marker" in current:
                marker_count += 1
    return marker_count


def summarise_forbidden_args(collection, file_item):
    return {
        "collection": obsolete_argument_keys(collection),
        "file": obsolete_argument_keys(file_item),
    }


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--app-dir", default="/opt/1panel/apps/sub-store/sub-store")
    parser.add_argument("--data", default="/opt/1panel/apps/sub-store/sub-store/data/sub-store.json")
    parser.add_argument("--container", default="sub-store")
    parser.add_argument("--collection", default="merged-airports")
    parser.add_argument("--file", default="frontier-chain-mihomo")
    parser.add_argument("--aggregator-name", default="aggregated-residential")
    parser.add_argument("--skip-http", action="store_true")
    args = parser.parse_args()

    checks = []
    data_path = Path(args.data)
    data = json.loads(data_path.read_text(encoding="utf-8"))

    checks.append(ok("docker container running", docker_running(args.container), args.container))
    log_summary, log_error = docker_log_issue_count(args.container)
    if log_summary is None:
        checks.append(warn("docker log issue scan skipped", log_error))
    else:
        checks.append(ok("docker logs tail has no missing/error/fail", log_summary["issue_count"] == 0, safe_detail_dict(log_summary)))
    checks.append(ok("sub-store.json schemaVersion", data.get("schemaVersion") == "2.0", str(data.get("schemaVersion"))))

    subs = data.get("subs", []) or []
    collection = find_named(data.get("collections", []), args.collection)
    file_item = find_named(data.get("files", []), args.file)
    checks.append(ok("upstream subscriptions exist", len(subs) >= 1, "count=" + str(len(subs))))
    checks.append(ok("collection exists", collection is not None, args.collection))
    checks.append(ok("mihomo file exists", file_item is not None, args.file))

    if collection:
        collection_subs = collection_subscription_names(collection)
        checks.append(ok("collection keeps ccrui and kuma", all(name in collection_subs for name in ("ccrui", "kuma")), ",".join(collection_subs)))
        checks.append(ok("collection includes residential aggregator", args.aggregator_name in collection_subs, "count=" + str(len(collection_subs))))
        ops = script_ops(collection)
        checks.append(ok("collection script operator exists", len(ops) >= 1, "count=" + str(len(ops))))
    if file_item:
        ops = script_ops(file_item)
        checks.append(ok("mihomo file has two script operators", len(ops) >= 2, "count=" + str(len(ops))))
        custom_names = [str(op.get("customName") or "") for op in ops]
        checks.append(ok("powerfullz operator present", any("powerfullz" in n.lower() for n in custom_names), ", ".join(custom_names)))

    marker_count = count_source_markers(subs)
    checks.append(ok("source marker operators present", marker_count >= len(subs), "markers=%s subs=%s" % (marker_count, len(subs))))
    forbidden_args = summarise_forbidden_args(collection, file_item)
    checks.append(ok("obsolete Frontier/ScrapeGW/VPS arguments removed", not forbidden_args["collection"] and not forbidden_args["file"], safe_detail_dict(forbidden_args)))

    http = {}
    clash_names = []
    shadow_names = []
    uri_names = []
    if not args.skip_http:
        backend_path = read_backend_path(args.app_dir, args.container)
        if backend_path:
            try:
                clash = fetch_local(backend_path, "/download/collection/%s?target=ClashMeta" % args.collection)
                http["collection_clashmeta"] = analyze_collection_output(clash)
                clash_names = extract_proxy_names(clash)
                checks.append(ok("ClashMeta collection has normalized prefixes", any(prefix in clash for prefix in ("CCR |", "KUMA |", "AGG |"))))
                checks.append(ok("ClashMeta collection has no retired link names", not http["collection_clashmeta"]["forbidden_counts"], safe_detail_dict(http["collection_clashmeta"]["forbidden_counts"])))
                checks.append(ok("ClashMeta collection has no source marker leak", not http["collection_clashmeta"]["source_marker_leak"]))
                checks.append(ok("ClashMeta collection has residential candidates", http["collection_clashmeta"]["residential_candidate_count"] > 0, str(http["collection_clashmeta"]["residential_candidate_count"])))
                checks.append(ok("ClashMeta collection excludes pseudo/non-direct nodes", http["collection_clashmeta"]["pseudo_or_non_direct_count"] == 0, str(http["collection_clashmeta"]["pseudo_or_non_direct_count"])))
                checks.append(ok("ClashMeta collection excludes timeout residential blacklist", http["collection_clashmeta"]["timeout_residential_count"] == 0, str(http["collection_clashmeta"]["timeout_residential_count"])))
            except Exception as exc:
                checks.append(warn("ClashMeta collection fetch skipped", str(exc)))
            try:
                shadow = fetch_local(backend_path, "/download/collection/%s?target=ShadowRocket" % args.collection)
                http["collection_shadowrocket"] = analyze_collection_output(shadow)
                shadow_names = extract_proxy_names(shadow)
                checks.append(ok("ShadowRocket collection has no retired link names", not http["collection_shadowrocket"]["forbidden_counts"], safe_detail_dict(http["collection_shadowrocket"]["forbidden_counts"])))
                checks.append(ok("ShadowRocket collection has residential candidates", http["collection_shadowrocket"]["residential_candidate_count"] > 0, str(http["collection_shadowrocket"]["residential_candidate_count"])))
                checks.append(ok("ShadowRocket collection excludes pseudo/non-direct nodes", http["collection_shadowrocket"]["pseudo_or_non_direct_count"] == 0, str(http["collection_shadowrocket"]["pseudo_or_non_direct_count"])))
                checks.append(ok("ShadowRocket collection excludes timeout residential blacklist", http["collection_shadowrocket"]["timeout_residential_count"] == 0, str(http["collection_shadowrocket"]["timeout_residential_count"])))
            except Exception as exc:
                checks.append(warn("ShadowRocket collection fetch skipped", str(exc)))
            equal_names = same_names(clash_names, shadow_names)
            if equal_names is None:
                checks.append(warn("collection target name equality skipped", "clash=%s shadow=%s" % (len(clash_names), len(shadow_names))))
            else:
                checks.append(ok("ClashMeta and ShadowRocket names match", equal_names, "clash=%s shadow=%s" % (len(clash_names), len(shadow_names))))
            try:
                uri = fetch_local(backend_path, "/download/collection/%s?target=URI" % args.collection)
                http["collection_uri"] = analyze_uri_output(uri)
                uri_names = extract_proxy_names(uri)
                uri_schemes = http["collection_uri"]["scheme_counts"]
                checks.append(ok("URI collection is line-based, not YAML", not http["collection_uri"]["has_yaml_shape"], safe_detail_dict(uri_schemes)))
                checks.append(ok("URI collection has no retired link names", not http["collection_uri"]["forbidden_counts"], safe_detail_dict(http["collection_uri"]["forbidden_counts"])))
                checks.append(ok("URI collection has residential candidates", http["collection_uri"]["residential_candidate_count"] > 0, str(http["collection_uri"]["residential_candidate_count"])))
                checks.append(ok("URI collection exposes expected schemes", sum(uri_schemes.get(s, 0) for s in ("ss", "vmess", "vless", "trojan", "hysteria2")) == http["collection_uri"]["line_count"], safe_detail_dict(uri_schemes)))
                checks.append(ok("ClashMeta and URI names match", same_names(clash_names, uri_names), "clash=%s uri=%s" % (len(clash_names), len(uri_names))))
            except Exception as exc:
                checks.append(warn("URI collection fetch skipped", str(exc)))
            try:
                final = fetch_local(backend_path, "/api/file/%s?target=mihomo" % args.file)
                http["final_mihomo"] = analyze_mihomo_output(final)
                checks.append(ok("final mihomo has proxy groups", http["final_mihomo"]["proxy_group_count"] > 1, str(http["final_mihomo"]["proxy_group_count"])))
                checks.append(ok("final mihomo has rules", http["final_mihomo"]["rule_count"] > 1, str(http["final_mihomo"]["rule_count"])))
                checks.append(ok("final mihomo has DNS", http["final_mihomo"]["has_dns"]))
                checks.append(ok("final mihomo has rule-providers", http["final_mihomo"]["has_rule_providers"]))
                checks.append(ok("final mihomo keeps Evoxt HY2 nodes", http["final_mihomo"]["evoxt_hysteria2_count"] >= 3, str(http["final_mihomo"]["evoxt_hysteria2_count"])))
                checks.append(ok("final mihomo keeps Evoxt HY2 close to Hiddify native export", http["final_mihomo"]["evoxt_hysteria2_sni_count"] == 0, str(http["final_mihomo"]["evoxt_hysteria2_sni_count"])))
                checks.append(ok("final mihomo excludes unverified Evoxt VLESS candidates", http["final_mihomo"]["evoxt_vless_count"] == 0, str(http["final_mihomo"]["evoxt_vless_count"])))
                checks.append(ok("final mihomo excludes broken Evoxt Reality", http["final_mihomo"]["evoxt_reality_count"] == 0, str(http["final_mihomo"]["evoxt_reality_count"])))
                checks.append(ok("final mihomo names Evoxt nodes as Malaysia", http["final_mihomo"]["evoxt_malaysia_name_count"] >= 3, str(http["final_mihomo"]["evoxt_malaysia_name_count"])))
                checks.append(ok("final mihomo removes Evoxt shortcut group", not http["final_mihomo"]["has_evoxt_group"]))
                checks.append(ok("Malaysia group contains Evoxt nodes", http["final_mihomo"]["malaysia_group_evoxt_refs"] >= 3, str(http["final_mihomo"]["malaysia_group_evoxt_refs"])))
                checks.append(ok("primary select exposes Malaysia group", http["final_mihomo"]["primary_group_malaysia_refs"] > 0, str(http["final_mihomo"]["primary_group_malaysia_refs"])))
                checks.append(ok("GLOBAL exposes Malaysia group", http["final_mihomo"]["global_malaysia_refs"] > 0, str(http["final_mihomo"]["global_malaysia_refs"])))
                checks.append(ok("GLOBAL does not expose removed Evoxt shortcut group", http["final_mihomo"]["global_evoxt_refs"] == 0, str(http["final_mihomo"]["global_evoxt_refs"])))
                checks.append(ok("GLOBAL exposes US residential shortcut", http["final_mihomo"]["global_us_residential_refs"] > 0, str(http["final_mihomo"]["global_us_residential_refs"])))
                checks.append(ok("GLOBAL exposes APAC residential shortcut", http["final_mihomo"]["global_apac_residential_refs"] > 0, str(http["final_mihomo"]["global_apac_residential_refs"])))
                checks.append(ok("final mihomo uses HTTP 204 url-test probe", http["final_mihomo"]["http_probe_group_count"] > 0, str(http["final_mihomo"]["http_probe_group_count"])))
                checks.append(ok("final mihomo has residential selector", http["final_mihomo"]["has_residential_selector"], str(http["final_mihomo"]["residential_selector_refs"])))
                checks.append(ok("residential selector exposes US shortcut", http["final_mihomo"]["residential_selector_us_refs"] > 0, str(http["final_mihomo"]["residential_selector_us_refs"])))
                checks.append(ok("residential selector exposes APAC shortcut", http["final_mihomo"]["residential_selector_apac_refs"] > 0, str(http["final_mihomo"]["residential_selector_apac_refs"])))
                checks.append(ok("final mihomo keeps AI group", http["final_mihomo"]["has_ai_group"]))
                checks.append(ok("final mihomo has PayPal group", http["final_mihomo"]["has_paypal_group"]))
                checks.append(ok("final mihomo has self-domain group", http["final_mihomo"]["has_self_domain_group"]))
                checks.append(ok("primary select can select residential selector", http["final_mihomo"]["primary_group_selector_refs"] > 0, str(http["final_mihomo"]["primary_group_selector_refs"])))
                checks.append(ok("AI group can select residential selector", http["final_mihomo"]["ai_group_selector_refs"] > 0, str(http["final_mihomo"]["ai_group_selector_refs"])))
                checks.append(ok("PayPal group can select residential selector", http["final_mihomo"]["paypal_group_selector_refs"] > 0, str(http["final_mihomo"]["paypal_group_selector_refs"])))
                checks.append(ok("self-domain group defaults to DIRECT", http["final_mihomo"]["self_domain_first_proxy"] == "DIRECT", http["final_mihomo"]["self_domain_first_proxy"]))
                checks.append(ok("self-domain group can select DIRECT", http["final_mihomo"]["self_domain_direct_refs"] > 0, str(http["final_mihomo"]["self_domain_direct_refs"])))
                checks.append(ok("GLOBAL exposes self-domain group", http["final_mihomo"]["global_self_domain_refs"] > 0, str(http["final_mihomo"]["global_self_domain_refs"])))
                checks.append(ok("konbakuyomu.us routes to self-domain group", http["final_mihomo"]["konbakuyomu_rule_refs"] > 0, str(http["final_mihomo"]["konbakuyomu_rule_refs"])))
                checks.append(ok("WeChat domain routes DIRECT", http["final_mihomo"]["wechat_direct_rule_refs"] > 0, str(http["final_mihomo"]["wechat_direct_rule_refs"])))
                checks.append(ok("QQ domain routes DIRECT", http["final_mihomo"]["qq_direct_rule_refs"] > 0, str(http["final_mihomo"]["qq_direct_rule_refs"])))
                checks.append(ok("Tencent geosite routes DIRECT", http["final_mihomo"]["tencent_geosite_direct_refs"] > 0, str(http["final_mihomo"]["tencent_geosite_direct_refs"])))
                checks.append(ok("China geosite routes DIRECT", http["final_mihomo"]["cn_geosite_direct_refs"] > 0, str(http["final_mihomo"]["cn_geosite_direct_refs"])))
                checks.append(ok("domestic DIRECT rules appear before MATCH fallback", http["final_mihomo"]["domestic_direct_before_match"]))
                checks.append(ok("AI group only exposes residential selector layer", http["final_mihomo"]["ai_group_region_residential_refs"] <= 1, str(http["final_mihomo"]["ai_group_region_residential_refs"])))
                checks.append(ok("PayPal group only exposes residential selector layer", http["final_mihomo"]["paypal_group_region_residential_refs"] <= 1, str(http["final_mihomo"]["paypal_group_region_residential_refs"])))
                checks.append(ok("final mihomo has residential candidates", http["final_mihomo"]["residential_candidate_count"] > 0, str(http["final_mihomo"]["residential_candidate_count"])))
                checks.append(ok("final mihomo has no retired link names", not http["final_mihomo"]["forbidden_counts"], safe_detail_dict(http["final_mihomo"]["forbidden_counts"])))
                checks.append(ok("final mihomo excludes pseudo/non-direct nodes", http["final_mihomo"]["pseudo_or_non_direct_count"] == 0, str(http["final_mihomo"]["pseudo_or_non_direct_count"])))
                checks.append(ok("final mihomo excludes timeout residential blacklist", http["final_mihomo"]["timeout_residential_count"] == 0, str(http["final_mihomo"]["timeout_residential_count"])))
                profile_ok, profile_detail = profile_check(final)
                if profile_ok is None:
                    checks.append(warn("mihomo profile-check skipped", profile_detail))
                else:
                    checks.append(ok("mihomo profile-check passed", profile_ok, profile_detail))
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
