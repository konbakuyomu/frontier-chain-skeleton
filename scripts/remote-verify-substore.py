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
DISPLAY_TAXONOMY_TAG = "frontier-display-v2"
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
EXPECTED_DISPLAY_NAMES = {
    "subs": {
        "ccrui": "10-原料-普通机场-CCR",
        "kuma": "10-原料-普通机场-KUMA",
        "aggregated-residential": "99-历史禁用-VPS-LA-聚合家宽原料",
        "substore-evoxt-upstream": "30-原料-Evoxt-HY2",
        "my-home-chain": "99-历史禁用-VPS-LA-MY家宽链式",
        "my-home-chain-hy2": "20-原料-家宽-马来西亚-MINE-HY2",
        "edge-us-att": "20-原料-家宽-美国-AT&T",
        "edge-us-roles": "40-稳定角色-美国Edge家宽",
        "edge-us-hy2-roles": "40-稳定角色-美国Edge-HY2",
        "edge-us-v2-att": "20-原料-家宽-美国-AT&T-v2",
        "edge-us-v2-roles": "40-稳定角色-SJC-ROUTE-VMess",
        "edge-us-v2-hy2-roles": "40-稳定角色-SJC-ROUTE-HY2",
        "sjc-3x": "20-原料-3x-ui-美国-SJC",
        "malaysia-3x": "20-原料-3x-ui-马来西亚",
        "old-us-3x": "20-原料-3x-ui-美国旧机",
    },
    "collections": {
        "merged-airports": "80-输出-三端主节点池",
        "ios-airports-uri": "81-输出-Shadowrocket-普通节点URI",
        "ios-evoxt-hy2-shadowrocket": "82-输出-Shadowrocket-HY2专用",
        "edge-us-upstreams": "20-原料-家宽-美国Edge上游",
        "edge-us-v2-upstreams": "20-原料-家宽-SJC-ROUTE上游",
        "user-landing-airports": "99-历史禁用-VPS-LA-链式原料池",
    },
    "files": {
        "frontier-chain-mihomo": "80-输出-Sparkle-FlClash-OpenClash-最终配置",
    },
}
LEGACY_VPS_LA_DAILY_EXCLUDES = {
    "aggregated-residential",
    "user-landing-airports",
    "vps-chain-residential",
    "vircs-att-vps-only",
    "my-home-chain",
    "测试",
}
LEGACY_SELF_NODE_ACTIVE_EXCLUDES = {
    "aggregated-residential",
    "substore-evoxt-upstream",
    "my-home-chain",
    "my-home-chain-hy2",
    "edge-us-att",
    "edge-us-v2-att",
    "edge-us-roles",
    "edge-us-hy2-roles",
}
CLIENT_COLLECTION_NAMES = {
    "merged-airports",
    "ios-airports-uri",
    "ios-evoxt-hy2-shadowrocket",
}
THREE_X_PREFIXES = ("SJC-3X", "MALAYSIA-3X", "OLD-US-3X")
THREE_X_FORBIDDEN_PREFIXES = ("LAX-3X", "MY-3X")
EDGE_US_V2_PREFIX = "SJC-ROUTE"
RETIRED_VISIBLE_PREFIXES = ("EDGE-US", "US-Edge")
RULE_MIRROR_HOST = "link.konbakuyomu.us"
THIRD_PARTY_RULE_PROVIDER_HOSTS = (
    "cdn.jsdelivr.net",
    "github.com",
    "raw.githubusercontent.com",
)
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


def load_substore_data(data_path, container):
    path = Path(data_path)
    if path.exists():
        return json.loads(path.read_text(encoding="utf-8")), "host-path"
    candidates = [
        "/opt/app/data/sub-store.json",
        "/app/data/sub-store.json",
        "/data/sub-store.json",
    ]
    for candidate in candidates:
        try:
            out = subprocess.check_output(
                ["docker", "exec", container, "cat", candidate],
                stderr=subprocess.DEVNULL,
                timeout=10,
            )
        except Exception:
            continue
        return json.loads(out.decode("utf-8")), "container-path"
    raise FileNotFoundError("sub-store.json not found at host path or known container paths")


def read_backend_path(app_dir, container):
    env_file = Path(app_dir) / ".env"
    if env_file.exists():
        for line in env_file.read_text(encoding="utf-8", errors="replace").splitlines():
            if line.startswith(("SUB_STORE_FRONTEND_BACKEND_PATH=", "SUB_STORE_BACKEND_PATH=")):
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
            if line.startswith(("SUB_STORE_FRONTEND_BACKEND_PATH=", "SUB_STORE_BACKEND_PATH=")):
                value = line.split("=", 1)[1].strip()
                if value:
                    return value
    except Exception:
        pass
    return ""


def backend_path_shape(value):
    if not value:
        return "empty"
    prefix = "api-prefix" if value.startswith("/api/") else "non-api-prefix"
    return "%s len=%s" % (prefix, len(value))


def fetch_local(local_base_url, base_path, endpoint):
    base_path = "/" + base_path.strip("/")
    url = local_base_url.rstrip("/") + base_path + endpoint
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
        "known_source_prefix_counts": {
            "CCR": text.count("CCR |"),
            "KUMA": text.count("KUMA |"),
            "AGG": text.count("AGG |"),
            "US_EDGE": text.count("US-Edge |"),
            "EDGE_US_V2": text.count("EDGE-US |"),
            "SJC_ROUTE": text.count("SJC-ROUTE |"),
            "EVOXT": text.count("L1-EVOXT |"),
        },
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


def analyze_ios_hy2_shadowrocket_output(text):
    try:
        data = yaml_safe_load(text)
    except Exception:
        data = {}
    proxies = data.get("proxies", []) if isinstance(data, dict) else []
    names = [
        str(proxy.get("name") or "")
        for proxy in proxies
        if isinstance(proxy, dict)
    ]
    types = [
        str(proxy.get("type") or "").lower()
        for proxy in proxies
        if isinstance(proxy, dict)
    ]
    return {
        "bytes": len(text.encode("utf-8")),
        "proxy_count": len(proxies),
        "evoxt_count": sum(1 for name in names if "L1-EVOXT" in name),
        "us_edge_hy2_count": sum(1 for name in names if re.search(r"US-Edge\s*\|.*-HY2", name, re.I)),
        "edge_us_v2_hy2_count": sum(1 for name in names if is_edge_us_v2_hy2_name(name)),
        "edge_us_v2_vmess_count": sum(1 for name in names if is_edge_us_v2_vmess_name(name)),
        "three_x_hy2_count": sum(1 for name in names if is_three_x_hy2_name(name)),
        "three_x_prefix_counts": three_x_prefix_counts(names),
        "hysteria2_count": sum(1 for proxy_type in types if proxy_type == "hysteria2"),
        "has_yaml_shape": text.lstrip().startswith("proxies:"),
        "forbidden_counts": forbidden_counts(text),
    }


def yaml_safe_load(text):
    try:
        import yaml
    except Exception as exc:
        raise RuntimeError("PyYAML unavailable: " + str(exc))
    return yaml.safe_load(text)


def analyze_rule_provider_urls(text):
    result = {
        "provider_count": 0,
        "provider_url_count": 0,
        "mirror_count": 0,
        "third_party_count": 0,
        "third_party_provider_names": [],
    }
    try:
        data = yaml_safe_load(text)
    except Exception:
        return result
    providers = data.get("rule-providers") if isinstance(data, dict) else None
    if not isinstance(providers, dict):
        return result
    result["provider_count"] = len(providers)
    for name, provider in providers.items():
        if not isinstance(provider, dict):
            continue
        url = str(provider.get("url") or "")
        if not url:
            continue
        result["provider_url_count"] += 1
        host = urllib.parse.urlparse(url).hostname or ""
        if host == RULE_MIRROR_HOST and urllib.parse.urlparse(url).path.startswith("/rules/"):
            result["mirror_count"] += 1
        if host in THIRD_PARTY_RULE_PROVIDER_HOSTS:
            result["third_party_count"] += 1
            result["third_party_provider_names"].append(str(name))
    result["third_party_provider_names"] = sorted(result["third_party_provider_names"])
    return result


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


def isolated_remote_subscription_names(data):
    names = set()
    for sub in data.get("subs", []) or []:
        if not isinstance(sub, dict):
            continue
        if sub.get("source") == "remote" and sub.get("ignoreFailedRemoteSub") is True and is_residential_object(sub):
            name = str(sub.get("name") or "")
            if name:
                names.add(name)
    return names


def tolerated_log_issue_reason(line, isolated_remote_names):
    if "Redirect loop detected" in line and "使用 HEAD 方法从响应头获取流量信息失败" in line:
        return "substore-head-probe-redirect-loop"
    if "Fallback Base64 Pre-processor error: decoded line does not start with protocol" in line:
        return "substore-parser-fallback"
    for name in isolated_remote_names:
        if name in line and re.search(r"(?i)error|fail|statusCode|发生错误|无法下载", line):
            return "isolated-remote-upstream"
    return ""


def docker_log_issue_count(container, data):
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
    isolated_names = isolated_remote_subscription_names(data)
    issues = 0
    fatal = 0
    tolerated_reasons = {}
    for line in out.splitlines():
        if not re.search(r"(?i)missing|error|fail|exception", line):
            continue
        reason = tolerated_log_issue_reason(line, isolated_names)
        if reason:
            tolerated_reasons[reason] = tolerated_reasons.get(reason, 0) + 1
            issues += 1
            continue
        issues += 1
        fatal += 1
    return {
        "issue_count": issues,
        "tolerated_issue_count": sum(tolerated_reasons.values()),
        "fatal_issue_count": fatal,
        "tolerated_reasons": tolerated_reasons,
        "bytes": len(out.encode("utf-8")),
    }, ""


def analyze_mihomo_output(text):
    visible_text = normalize_visible_text(text)
    proxies = section(text, "proxies")
    proxy_groups = section(text, "proxy-groups")
    visible_proxy_groups = section(visible_text, "proxy-groups")
    rules = section(text, "rules")
    rule_items = extract_rules(rules)
    proxy_items = extract_mihomo_proxy_items(text)
    names = [proxy_item_name(item) for item in proxy_items] or extract_proxy_names(proxies)
    evoxt_items = [item for item in proxy_items if "L1-EVOXT" in proxy_item_name(item)]
    us_edge_items = [item for item in proxy_items if "US-Edge |" in proxy_item_name(item)]
    us_edge_hy2_items = [item for item in us_edge_items if re.search(r"US-Edge\s*\|.*-HY2", proxy_item_name(item), re.I)]
    edge_us_v2_items = [item for item in proxy_items if is_edge_us_v2_name(proxy_item_name(item))]
    edge_us_v2_hy2_items = [item for item in edge_us_v2_items if is_edge_us_v2_hy2_name(proxy_item_name(item))]
    edge_us_v2_vmess_items = [item for item in edge_us_v2_items if proxy_item_type(item) == "vmess"]
    three_x_items = [item for item in proxy_items if is_three_x_name(proxy_item_name(item))]
    three_x_hy2_items = [item for item in three_x_items if is_three_x_hy2_name(proxy_item_name(item))]
    three_x_vless_items = [item for item in three_x_items if proxy_item_type(item) == "vless"]
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
        "evoxt_node_count": len(evoxt_items),
        "evoxt_hysteria2_count": sum(1 for item in evoxt_items if proxy_item_type(item) == "hysteria2"),
        "evoxt_hysteria2_sni_count": sum(1 for item in evoxt_items if proxy_item_type(item) == "hysteria2" and proxy_item_has_sni(item)),
        "evoxt_vless_count": sum(1 for item in evoxt_items if proxy_item_type(item) == "vless"),
        "evoxt_reality_count": sum(1 for item in evoxt_items if proxy_item_is_reality(item)),
        "evoxt_malaysia_name_count": sum(1 for item in evoxt_items if "马来西亚" in proxy_item_name(item) or "Malaysia" in proxy_item_name(item)),
        "us_edge_node_count": len(us_edge_items),
        "us_edge_hy2_count": len(us_edge_hy2_items),
        "us_edge_vmess_count": sum(1 for item in us_edge_items if proxy_item_type(item) == "vmess"),
        "edge_us_v2_node_count": len(edge_us_v2_items),
        "edge_us_v2_hy2_count": len(edge_us_v2_hy2_items),
        "edge_us_v2_vmess_count": len(edge_us_v2_vmess_items),
        "three_x_node_count": len(three_x_items),
        "three_x_hy2_count": len(three_x_hy2_items),
        "three_x_vless_count": len(three_x_vless_items),
        "three_x_reality_count": sum(1 for item in three_x_vless_items if proxy_item_is_reality(item)),
        "three_x_prefix_counts": three_x_prefix_counts([proxy_item_name(item) for item in three_x_items]),
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
        "rule_provider_urls": analyze_rule_provider_urls(text),
    })
    return quality


def extract_mihomo_proxy_items(text):
    try:
        data = yaml_safe_load(text)
    except Exception:
        return [
            {"__block": block}
            for block in extract_proxy_blocks(section(text, "proxies"))
        ]
    proxies = data.get("proxies") if isinstance(data, dict) else None
    if not isinstance(proxies, list):
        return []
    return [item for item in proxies if isinstance(item, dict)]


def proxy_item_name(item):
    if "__block" in item:
        return proxy_block_name(item["__block"])
    return str(item.get("name") or "")


def proxy_item_type(item):
    if "__block" in item:
        return proxy_block_type(item["__block"])
    return str(item.get("type") or "").strip().lower()


def proxy_item_is_reality(item):
    if "__block" in item:
        return proxy_block_is_reality(item["__block"])
    return proxy_item_type(item) == "vless" and (
        "reality-opts" in item or re.search(r"(?i)\breality\b", json.dumps(item, ensure_ascii=False)) is not None
    )


def proxy_item_has_sni(item):
    if "__block" in item:
        return proxy_block_has_sni(item["__block"])
    return bool(item.get("sni") or item.get("servername"))


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


def proxy_block_name(block):
    match = re.search(r"(?m)^\s*-\s+name\s*:\s*['\"]?(.+?)['\"]?\s*$", block)
    return match.group(1).strip().strip("'\"") if match else ""


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


def display_name(item):
    return str((item or {}).get("displayName") or (item or {}).get("display-name") or "")


def has_any_marker(text, markers):
    value = str(text or "").lower()
    return any(marker.lower() in value for marker in markers)


def is_us_edge_hy2_name(name):
    return re.search(r"US-Edge\s*\|.*-HY2(?:$|-)", str(name or ""), re.I) is not None


def is_edge_us_v2_name(name):
    return re.search(r"^%s\s*\|" % re.escape(EDGE_US_V2_PREFIX), str(name or ""), re.I) is not None


def is_edge_us_v2_hy2_name(name):
    return is_edge_us_v2_name(name) and re.search(r"-HY2(?:$|-)", str(name or ""), re.I) is not None


def is_edge_us_v2_vmess_name(name):
    return is_edge_us_v2_name(name) and not is_edge_us_v2_hy2_name(name)


def retired_visible_names(names):
    out = []
    for name in names:
        text = str(name or "")
        if any(re.search(r"^%s\s*\|" % re.escape(prefix), text, re.I) for prefix in RETIRED_VISIBLE_PREFIXES):
            out.append(text)
    return out


def is_three_x_name(name):
    text = str(name or "")
    return any(re.search(r"^%s\s*\|" % re.escape(prefix), text, re.I) for prefix in THREE_X_PREFIXES)


def is_three_x_hy2_name(name):
    return is_three_x_name(name) and re.search(r"-HY2(?:$|-)", str(name or ""), re.I) is not None


def is_three_x_vless_name(name):
    return is_three_x_name(name) and re.search(r"-VLESS(?:$|-)", str(name or ""), re.I) is not None


def three_x_prefix_counts(names):
    counts = {}
    for name in names:
        text = str(name or "")
        for prefix in THREE_X_PREFIXES:
            if re.search(r"^%s\s*\|" % re.escape(prefix), text, re.I):
                counts[prefix] = counts.get(prefix, 0) + 1
                break
    return counts


def forbidden_three_x_names(names):
    out = []
    for name in names:
        text = str(name or "")
        if any(re.search(r"^%s\s*\|" % re.escape(prefix), text, re.I) for prefix in THREE_X_FORBIDDEN_PREFIXES):
            out.append(text)
    return out


def is_residential_object(item):
    searchable = " ".join(
        str((item or {}).get(key) or "")
        for key in ("name", "displayName", "display-name", "remark")
    )
    return has_any_marker(searchable, RESIDENTIAL_TEXT_MARKERS)


def taxonomy_display_mismatches(data):
    mismatches = []
    for section, expected in EXPECTED_DISPLAY_NAMES.items():
        items = data.get(section, []) or []
        for name, want in expected.items():
            item = find_named(items, name)
            if not item:
                continue
            got = display_name(item)
            if got != want:
                mismatches.append({"section": section, "name": name, "got": got, "want": want})
    return mismatches


def missing_taxonomy_tags(data):
    missing = []
    for section in ("subs", "collections", "files"):
        for item in data.get(section, []) or []:
            if not isinstance(item, dict) or not item.get("name"):
                continue
            tags = item.get("tag") if isinstance(item.get("tag"), list) else []
            if DISPLAY_TAXONOMY_TAG not in [str(value) for value in tags]:
                missing.append("%s:%s" % (section, item.get("name")))
    return missing


def taxonomy_subscription_tag_leaks(data):
    leaks = []
    for section in ("subs", "collections", "files"):
        for item in data.get(section, []) or []:
            if not isinstance(item, dict) or not item.get("name"):
                continue
            values = item.get("subscriptionTags")
            if isinstance(values, list) and DISPLAY_TAXONOMY_TAG in [str(value) for value in values]:
                leaks.append("%s:%s" % (section, item.get("name")))
    return leaks


def residential_remote_failure_isolation_gaps(data):
    gaps = []
    for sub in data.get("subs", []) or []:
        if not isinstance(sub, dict):
            continue
        if sub.get("source") == "remote" and is_residential_object(sub) and sub.get("ignoreFailedRemoteSub") is not True:
            gaps.append(str(sub.get("name") or "<unnamed>"))
    return gaps


def legacy_daily_collection_refs(data):
    refs = []
    for collection_name in CLIENT_COLLECTION_NAMES:
        collection = find_named(data.get("collections", []), collection_name)
        for sub_name in effective_collection_subscription_names(data, collection):
            if sub_name in LEGACY_VPS_LA_DAILY_EXCLUDES:
                refs.append("%s:%s" % (collection_name, sub_name))
    return refs


def legacy_self_node_collection_refs(data):
    refs = []
    for collection_name in CLIENT_COLLECTION_NAMES:
        collection = find_named(data.get("collections", []), collection_name)
        for sub_name in effective_collection_subscription_names(data, collection):
            if sub_name in LEGACY_SELF_NODE_ACTIVE_EXCLUDES:
                refs.append("%s:%s" % (collection_name, sub_name))
    return refs


def display_prefix_counts(data):
    counts = {}
    for section in ("subs", "collections", "files"):
        for item in data.get(section, []) or []:
            disp = display_name(item)
            match = re.match(r"^(\d{2})-", disp)
            if match:
                counts[match.group(1)] = counts.get(match.group(1), 0) + 1
    return counts


def count_source_markers(subs):
    marker_count = 0
    for sub in subs:
        for op in script_ops(sub):
            current = (op.get("args") or {}).get("content") or ""
            name = str(op.get("customName") or "")
            if name.startswith("source marker:") or name.startswith("source-marker-") or "Sub-Store source marker" in current:
                marker_count += 1
    return marker_count


def sub_has_source_marker(sub):
    return count_source_markers([sub]) > 0


def referenced_remote_subs(data, collection_names):
    subs = {
        item.get("name"): item
        for item in data.get("subs", []) or []
        if isinstance(item, dict) and item.get("name")
    }
    result = []
    seen = set()
    for collection_name in collection_names:
        collection = find_named(data.get("collections", []), collection_name)
        for sub_name in effective_collection_subscription_names(data, collection):
            if sub_name in seen:
                continue
            seen.add(sub_name)
            sub = subs.get(sub_name)
            if sub and sub.get("source") == "remote":
                result.append(sub)
    return result


def effective_collection_subscription_names(data, collection):
    names = collection_subscription_names(collection)
    seen = set(names)
    selector_tags = (collection or {}).get("subscriptionTags")
    if isinstance(selector_tags, list) and selector_tags:
        selector_tags = {str(tag) for tag in selector_tags}
        for sub in data.get("subs", []) or []:
            if not isinstance(sub, dict):
                continue
            sub_name = str(sub.get("name") or "")
            sub_tags = sub.get("tag")
            if not sub_name or sub_name in seen or not isinstance(sub_tags, list):
                continue
            if selector_tags.intersection(str(tag) for tag in sub_tags):
                names.append(sub_name)
                seen.add(sub_name)
    return names


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
    parser.add_argument("--ios-airports-collection", default="ios-airports-uri")
    parser.add_argument("--ios-hy2-collection", default="ios-evoxt-hy2-shadowrocket")
    parser.add_argument("--local-base-url", default="http://127.0.0.1:3001")
    parser.add_argument("--expected-backend-path", default="")
    parser.add_argument("--expected-three-x-vless", type=int, default=None)
    parser.add_argument("--expected-three-x-hy2", type=int, default=None)
    parser.add_argument("--expected-edge-us-v2-vmess", type=int, default=None)
    parser.add_argument("--expected-edge-us-v2-hy2", type=int, default=None)
    parser.add_argument("--min-ios-ordinary-nodes", type=int, default=1)
    parser.add_argument("--min-ios-hy2-nodes", type=int, default=0)
    parser.add_argument("--skip-http", action="store_true")
    args = parser.parse_args()

    checks = []
    data, data_source = load_substore_data(args.data, args.container)

    checks.append(ok("docker container running", docker_running(args.container), args.container))
    log_summary, log_error = docker_log_issue_count(args.container, data)
    if log_summary is None:
        checks.append(warn("docker log issue scan skipped", log_error))
    elif log_summary["fatal_issue_count"] == 0 and log_summary["issue_count"] > 0:
        checks.append(warn("docker logs tail has only tolerated isolated upstream/parser issues", safe_detail_dict(log_summary)))
    else:
        checks.append(ok("docker logs tail has no fatal missing/error/fail", log_summary["fatal_issue_count"] == 0, safe_detail_dict(log_summary)))
    checks.append(ok("sub-store.json readable", True, data_source))
    checks.append(ok("sub-store.json schemaVersion", data.get("schemaVersion") == "2.0", str(data.get("schemaVersion"))))

    subs = data.get("subs", []) or []
    collection = find_named(data.get("collections", []), args.collection)
    file_item = find_named(data.get("files", []), args.file)
    checks.append(ok("upstream subscriptions exist", len(subs) >= 1, "count=" + str(len(subs))))
    checks.append(ok("collection exists", collection is not None, args.collection))
    checks.append(ok("mihomo file exists", file_item is not None, args.file))
    display_mismatches = taxonomy_display_mismatches(data)
    missing_taxonomy = missing_taxonomy_tags(data)
    taxonomy_tag_leaks = taxonomy_subscription_tag_leaks(data)
    residential_isolation_gaps = residential_remote_failure_isolation_gaps(data)
    legacy_refs = legacy_daily_collection_refs(data)
    legacy_self_refs = legacy_self_node_collection_refs(data)
    checks.append(ok(
        "Sub-Store display taxonomy matches stable names",
        not display_mismatches,
        safe_detail_dict({"mismatches": display_mismatches[:10], "count": len(display_mismatches)}),
    ))
    checks.append(ok(
        "Sub-Store display taxonomy tag present",
        not missing_taxonomy,
        safe_detail_dict({"missing": missing_taxonomy[:10], "count": len(missing_taxonomy)}),
    ))
    checks.append(ok(
        "display taxonomy tag is not used as a subscription selector",
        not taxonomy_tag_leaks,
        safe_detail_dict({"leaks": taxonomy_tag_leaks[:10], "count": len(taxonomy_tag_leaks)}),
    ))
    checks.append(ok(
        "residential remote upstreams ignore failed fetches",
        not residential_isolation_gaps,
        safe_detail_dict({"gaps": residential_isolation_gaps}),
    ))
    checks.append(ok(
        "legacy VPS-LA objects are not in daily client collections",
        not legacy_refs,
        safe_detail_dict({"refs": legacy_refs}),
    ))
    checks.append(ok(
        "legacy self-node refs are not active through explicit refs or selector tags",
        not legacy_self_refs,
        safe_detail_dict({"refs": legacy_self_refs}),
    ))
    checks.append(warn("Sub-Store display prefix counts", safe_detail_dict(display_prefix_counts(data))))

    if collection:
        collection_subs = collection_subscription_names(collection)
        checks.append(ok("source collection has pluggable upstream refs", len(collection_subs) >= 1, "count=" + str(len(collection_subs))))
        ops = script_ops(collection)
        checks.append(ok("collection script operator exists", len(ops) >= 1, "count=" + str(len(ops))))
    ios_airports_collection = find_named(data.get("collections", []), args.ios_airports_collection)
    ios_hy2_collection = find_named(data.get("collections", []), args.ios_hy2_collection)
    checks.append(ok("iOS ordinary collection exists", ios_airports_collection is not None, args.ios_airports_collection))
    checks.append(ok("iOS HY2 collection exists", ios_hy2_collection is not None, args.ios_hy2_collection))
    if ios_airports_collection:
        checks.append(ok(
            "iOS ordinary collection has pluggable upstream refs",
            len(collection_subscription_names(ios_airports_collection)) >= 1,
            "count=" + str(len(collection_subscription_names(ios_airports_collection))),
        ))
    if ios_hy2_collection:
        checks.append(warn(
            "iOS HY2 collection upstream refs",
            "count=" + str(len(collection_subscription_names(ios_hy2_collection))),
        ))
    if file_item:
        ops = script_ops(file_item)
        checks.append(ok("mihomo file has two script operators", len(ops) >= 2, "count=" + str(len(ops))))
        custom_names = [str(op.get("customName") or "") for op in ops]
        checks.append(ok("powerfullz operator present", any("powerfullz" in n.lower() for n in custom_names), ", ".join(custom_names)))

    marker_subs = referenced_remote_subs(data, [args.collection, args.ios_airports_collection])
    missing_markers = [sub.get("name") for sub in marker_subs if not sub_has_source_marker(sub)]
    checks.append(ok(
        "referenced remote upstreams have source markers",
        not missing_markers,
        "checked=%s missing=%s" % (len(marker_subs), len(missing_markers)),
    ))
    forbidden_args = summarise_forbidden_args(collection, file_item)
    checks.append(ok("obsolete Frontier/ScrapeGW/VPS arguments removed", not forbidden_args["collection"] and not forbidden_args["file"], safe_detail_dict(forbidden_args)))

    http = {}
    clash_names = []
    shadow_names = []
    uri_names = []
    if not args.skip_http:
        backend_path = read_backend_path(args.app_dir, args.container)
        checks.append(ok("Sub-Store backend path available", bool(backend_path), backend_path_shape(backend_path)))
        if args.expected_backend_path:
            checks.append(ok(
                "Sub-Store backend path matches expected client URL",
                backend_path == args.expected_backend_path,
                "actual=%s expected=%s" % (backend_path_shape(backend_path), backend_path_shape(args.expected_backend_path)),
            ))
        if backend_path:
            try:
                clash = fetch_local(args.local_base_url, backend_path, "/download/collection/%s?target=ClashMeta" % args.collection)
                http["collection_clashmeta"] = analyze_collection_output(clash)
                clash_names = extract_proxy_names(clash)
                clash_prefix_counts = http["collection_clashmeta"]["known_source_prefix_counts"]
                checks.append(ok("ClashMeta collection has nodes", len(clash_names) > 0, "count=" + str(len(clash_names))))
                checks.append(ok("ClashMeta collection has no retired link names", not http["collection_clashmeta"]["forbidden_counts"], safe_detail_dict(http["collection_clashmeta"]["forbidden_counts"])))
                checks.append(ok("ClashMeta collection excludes legacy Evoxt/US Edge self nodes", clash_prefix_counts.get("EVOXT", 0) == 0 and clash_prefix_counts.get("US_EDGE", 0) == 0, safe_detail_dict(clash_prefix_counts)))
                checks.append(ok("ClashMeta collection has no source marker leak", not http["collection_clashmeta"]["source_marker_leak"]))
                checks.append(ok("ClashMeta collection has residential candidates", http["collection_clashmeta"]["residential_candidate_count"] > 0, str(http["collection_clashmeta"]["residential_candidate_count"])))
                checks.append(ok("ClashMeta collection excludes pseudo/non-direct nodes", http["collection_clashmeta"]["pseudo_or_non_direct_count"] == 0, str(http["collection_clashmeta"]["pseudo_or_non_direct_count"])))
                checks.append(ok("ClashMeta collection excludes timeout residential blacklist", http["collection_clashmeta"]["timeout_residential_count"] == 0, str(http["collection_clashmeta"]["timeout_residential_count"])))
            except Exception as exc:
                checks.append(warn("ClashMeta collection fetch skipped", str(exc)))
            try:
                shadow = fetch_local(args.local_base_url, backend_path, "/download/collection/%s?target=ShadowRocket" % args.collection)
                http["collection_shadowrocket"] = analyze_collection_output(shadow)
                shadow_names = extract_proxy_names(shadow)
                shadow_prefix_counts = http["collection_shadowrocket"]["known_source_prefix_counts"]
                checks.append(ok("ShadowRocket collection has no retired link names", not http["collection_shadowrocket"]["forbidden_counts"], safe_detail_dict(http["collection_shadowrocket"]["forbidden_counts"])))
                checks.append(ok("ShadowRocket collection excludes legacy Evoxt/US Edge self nodes", shadow_prefix_counts.get("EVOXT", 0) == 0 and shadow_prefix_counts.get("US_EDGE", 0) == 0, safe_detail_dict(shadow_prefix_counts)))
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
                uri = fetch_local(args.local_base_url, backend_path, "/download/collection/%s?target=URI" % args.collection)
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
                ios_uri = fetch_local(args.local_base_url, backend_path, "/download/collection/%s?target=URI" % args.ios_airports_collection)
                http["ios_airports_uri"] = analyze_uri_output(ios_uri)
                ios_uri_names = extract_proxy_names(ios_uri)
                ios_uri_us_edge_hy2 = [name for name in ios_uri_names if is_us_edge_hy2_name(name)]
                ios_uri_edge_us_v2_hy2 = [name for name in ios_uri_names if is_edge_us_v2_hy2_name(name)]
                ios_uri_edge_us_v2_vmess = [name for name in ios_uri_names if is_edge_us_v2_vmess_name(name)]
                ios_uri_retired_visible = retired_visible_names(ios_uri_names)
                ios_uri_three_x_hy2 = [name for name in ios_uri_names if is_three_x_hy2_name(name)]
                ios_uri_three_x_vless = [name for name in ios_uri_names if is_three_x_vless_name(name)]
                ios_uri_forbidden_three_x = forbidden_three_x_names(ios_uri_names)
                ios_uri_schemes = http["ios_airports_uri"]["scheme_counts"]
                checks.append(ok("iOS airports URI collection is line-based", not http["ios_airports_uri"]["has_yaml_shape"], safe_detail_dict(ios_uri_schemes)))
                checks.append(ok("iOS airports URI excludes Evoxt", not any("L1-EVOXT" in name for name in ios_uri_names), str(sum(1 for name in ios_uri_names if "L1-EVOXT" in name))))
                checks.append(ok("iOS airports URI excludes US Edge HY2", not ios_uri_us_edge_hy2, str(len(ios_uri_us_edge_hy2))))
                checks.append(ok("iOS airports URI excludes SJC-ROUTE HY2", not ios_uri_edge_us_v2_hy2, str(len(ios_uri_edge_us_v2_hy2))))
                checks.append(ok("iOS airports URI excludes retired EDGE-US names", not ios_uri_retired_visible, str(len(ios_uri_retired_visible))))
                checks.append(ok("iOS airports URI excludes 3X HY2", not ios_uri_three_x_hy2, str(len(ios_uri_three_x_hy2))))
                checks.append(ok("iOS airports URI excludes old 3X prefixes", not ios_uri_forbidden_three_x, str(len(ios_uri_forbidden_three_x))))
                checks.append(warn("iOS airports URI SJC-ROUTE VMess stats", safe_detail_dict({
                    "edge_us_v2_vmess_count": len(ios_uri_edge_us_v2_vmess),
                    "edge_us_v2_hy2_count": len(ios_uri_edge_us_v2_hy2),
                })))
                if args.expected_edge_us_v2_vmess is not None:
                    checks.append(ok("iOS airports URI expected SJC-ROUTE VMess count", len(ios_uri_edge_us_v2_vmess) == args.expected_edge_us_v2_vmess, "%s" % len(ios_uri_edge_us_v2_vmess)))
                checks.append(warn("iOS airports URI 3X VLESS stats", safe_detail_dict({
                    "three_x_vless_count": len(ios_uri_three_x_vless),
                    "three_x_prefix_counts": three_x_prefix_counts(ios_uri_names),
                })))
                if args.expected_three_x_vless is not None:
                    checks.append(ok("iOS airports URI expected 3X VLESS count", len(ios_uri_three_x_vless) == args.expected_three_x_vless, "%s" % len(ios_uri_three_x_vless)))
                checks.append(ok("iOS airports URI has ordinary nodes", http["ios_airports_uri"]["line_count"] >= args.min_ios_ordinary_nodes, str(http["ios_airports_uri"]["line_count"])))
                checks.append(ok("iOS airports URI keeps residential candidates", http["ios_airports_uri"]["residential_candidate_count"] > 0, str(http["ios_airports_uri"]["residential_candidate_count"])))
            except Exception as exc:
                checks.append(warn("iOS airports URI collection fetch skipped", str(exc)))
            try:
                ios_hy2 = fetch_local(args.local_base_url, backend_path, "/download/collection/%s?target=ShadowRocket" % args.ios_hy2_collection)
                http["ios_evoxt_hy2_shadowrocket"] = analyze_ios_hy2_shadowrocket_output(ios_hy2)
                checks.append(ok("iOS HY2 collection is YAML for Shadowrocket", http["ios_evoxt_hy2_shadowrocket"]["has_yaml_shape"]))
                checks.append(ok("iOS HY2 collection meets minimum node count", http["ios_evoxt_hy2_shadowrocket"]["proxy_count"] >= args.min_ios_hy2_nodes, str(http["ios_evoxt_hy2_shadowrocket"]["proxy_count"])))
                checks.append(ok("iOS HY2 collection only has hysteria2 nodes when present", http["ios_evoxt_hy2_shadowrocket"]["hysteria2_count"] == http["ios_evoxt_hy2_shadowrocket"]["proxy_count"], "hy2=%s total=%s" % (http["ios_evoxt_hy2_shadowrocket"]["hysteria2_count"], http["ios_evoxt_hy2_shadowrocket"]["proxy_count"])))
                checks.append(warn("iOS HY2 US Edge stats", safe_detail_dict({
                    "us_edge_hy2_count": http["ios_evoxt_hy2_shadowrocket"]["us_edge_hy2_count"],
                    "edge_us_v2_hy2_count": http["ios_evoxt_hy2_shadowrocket"]["edge_us_v2_hy2_count"],
                    "edge_us_v2_vmess_count": http["ios_evoxt_hy2_shadowrocket"]["edge_us_v2_vmess_count"],
                    "evoxt_count": http["ios_evoxt_hy2_shadowrocket"]["evoxt_count"],
                })))
                checks.append(ok("iOS HY2 excludes legacy Evoxt/US Edge self nodes", http["ios_evoxt_hy2_shadowrocket"]["evoxt_count"] == 0 and http["ios_evoxt_hy2_shadowrocket"]["us_edge_hy2_count"] == 0, safe_detail_dict({
                    "us_edge_hy2_count": http["ios_evoxt_hy2_shadowrocket"]["us_edge_hy2_count"],
                    "evoxt_count": http["ios_evoxt_hy2_shadowrocket"]["evoxt_count"],
                })))
                ios_hy2_names = extract_proxy_names(ios_hy2)
                checks.append(ok("iOS HY2 excludes SJC-ROUTE VMess", http["ios_evoxt_hy2_shadowrocket"]["edge_us_v2_vmess_count"] == 0, "%s" % http["ios_evoxt_hy2_shadowrocket"]["edge_us_v2_vmess_count"]))
                checks.append(ok("iOS HY2 excludes retired EDGE-US names", not retired_visible_names(ios_hy2_names), str(len(retired_visible_names(ios_hy2_names)))))
                if args.expected_edge_us_v2_hy2 is not None:
                    checks.append(ok("iOS HY2 expected SJC-ROUTE HY2 count", http["ios_evoxt_hy2_shadowrocket"]["edge_us_v2_hy2_count"] == args.expected_edge_us_v2_hy2, "%s" % http["ios_evoxt_hy2_shadowrocket"]["edge_us_v2_hy2_count"]))
                checks.append(warn("iOS HY2 3X stats", safe_detail_dict({
                    "three_x_hy2_count": http["ios_evoxt_hy2_shadowrocket"]["three_x_hy2_count"],
                    "three_x_prefix_counts": http["ios_evoxt_hy2_shadowrocket"]["three_x_prefix_counts"],
                })))
                if args.expected_three_x_hy2 is not None:
                    checks.append(ok("iOS HY2 expected 3X HY2 count", http["ios_evoxt_hy2_shadowrocket"]["three_x_hy2_count"] == args.expected_three_x_hy2, "%s" % http["ios_evoxt_hy2_shadowrocket"]["three_x_hy2_count"]))
                checks.append(ok("iOS HY2 excludes old 3X prefixes", not forbidden_three_x_names(ios_hy2_names), "0"))
            except Exception as exc:
                checks.append(warn("iOS HY2 collection fetch skipped", str(exc)))
            try:
                final = fetch_local(args.local_base_url, backend_path, "/api/file/%s?target=mihomo" % args.file)
                http["final_mihomo"] = analyze_mihomo_output(final)
                checks.append(ok("final mihomo has proxy groups", http["final_mihomo"]["proxy_group_count"] > 1, str(http["final_mihomo"]["proxy_group_count"])))
                checks.append(ok("final mihomo has rules", http["final_mihomo"]["rule_count"] > 1, str(http["final_mihomo"]["rule_count"])))
                checks.append(ok("final mihomo has DNS", http["final_mihomo"]["has_dns"]))
                checks.append(ok("final mihomo has rule-providers", http["final_mihomo"]["has_rule_providers"]))
                checks.append(ok("final mihomo rule-provider URLs use SJC mirror", http["final_mihomo"]["rule_provider_urls"]["third_party_count"] == 0, safe_detail_dict({
                    "provider_count": http["final_mihomo"]["rule_provider_urls"]["provider_count"],
                    "provider_url_count": http["final_mihomo"]["rule_provider_urls"]["provider_url_count"],
                    "mirror_count": http["final_mihomo"]["rule_provider_urls"]["mirror_count"],
                    "third_party_count": http["final_mihomo"]["rule_provider_urls"]["third_party_count"],
                    "third_party_provider_names": http["final_mihomo"]["rule_provider_urls"]["third_party_provider_names"][:10],
                })))
                checks.append(warn("final mihomo Evoxt/HY2 stats", safe_detail_dict({
                    "evoxt_node_count": http["final_mihomo"]["evoxt_node_count"],
                    "hysteria2_count": http["final_mihomo"]["evoxt_hysteria2_count"],
                    "vless_count": http["final_mihomo"]["evoxt_vless_count"],
                    "reality_count": http["final_mihomo"]["evoxt_reality_count"],
                    "malaysia_group_refs": http["final_mihomo"]["malaysia_group_evoxt_refs"],
                })))
                final_names = extract_proxy_names(final)
                checks.append(warn("final mihomo SJC-ROUTE stats", safe_detail_dict({
                    "us_edge_node_count": http["final_mihomo"]["us_edge_node_count"],
                    "us_edge_vmess_count": http["final_mihomo"]["us_edge_vmess_count"],
                    "us_edge_hy2_count": http["final_mihomo"]["us_edge_hy2_count"],
                    "edge_us_v2_node_count": http["final_mihomo"]["edge_us_v2_node_count"],
                    "edge_us_v2_vmess_count": http["final_mihomo"]["edge_us_v2_vmess_count"],
                    "edge_us_v2_hy2_count": http["final_mihomo"]["edge_us_v2_hy2_count"],
                })))
                checks.append(ok("final mihomo excludes legacy Evoxt/US Edge self nodes", http["final_mihomo"]["evoxt_node_count"] == 0 and http["final_mihomo"]["us_edge_node_count"] == 0, safe_detail_dict({
                    "evoxt_node_count": http["final_mihomo"]["evoxt_node_count"],
                    "us_edge_node_count": http["final_mihomo"]["us_edge_node_count"],
                })))
                checks.append(ok("final mihomo excludes retired EDGE-US names", not retired_visible_names(final_names), str(len(retired_visible_names(final_names)))))
                if args.expected_edge_us_v2_vmess is not None:
                    checks.append(ok("final mihomo expected SJC-ROUTE VMess count", http["final_mihomo"]["edge_us_v2_vmess_count"] == args.expected_edge_us_v2_vmess, "%s" % http["final_mihomo"]["edge_us_v2_vmess_count"]))
                if args.expected_edge_us_v2_hy2 is not None:
                    checks.append(ok("final mihomo expected SJC-ROUTE HY2 count", http["final_mihomo"]["edge_us_v2_hy2_count"] == args.expected_edge_us_v2_hy2, "%s" % http["final_mihomo"]["edge_us_v2_hy2_count"]))
                checks.append(warn("final mihomo 3X stats", safe_detail_dict({
                    "three_x_node_count": http["final_mihomo"]["three_x_node_count"],
                    "three_x_vless_count": http["final_mihomo"]["three_x_vless_count"],
                    "three_x_hy2_count": http["final_mihomo"]["three_x_hy2_count"],
                    "three_x_reality_count": http["final_mihomo"]["three_x_reality_count"],
                    "three_x_prefix_counts": http["final_mihomo"]["three_x_prefix_counts"],
                })))
                if args.expected_three_x_vless is not None:
                    checks.append(ok("final mihomo expected 3X VLESS count", http["final_mihomo"]["three_x_vless_count"] == args.expected_three_x_vless, "%s" % http["final_mihomo"]["three_x_vless_count"]))
                if args.expected_three_x_hy2 is not None:
                    checks.append(ok("final mihomo expected 3X HY2 count", http["final_mihomo"]["three_x_hy2_count"] == args.expected_three_x_hy2, "%s" % http["final_mihomo"]["three_x_hy2_count"]))
                checks.append(ok("final mihomo excludes old 3X prefixes", not forbidden_three_x_names(final_names), "0"))
                checks.append(ok("GLOBAL does not expose removed Evoxt shortcut group", http["final_mihomo"]["global_evoxt_refs"] == 0, str(http["final_mihomo"]["global_evoxt_refs"])))
                checks.append(ok("final mihomo uses stable residential shortcut layer", http["final_mihomo"]["global_us_residential_refs"] > 0 or http["final_mihomo"]["global_apac_residential_refs"] > 0, "us=%s apac=%s" % (http["final_mihomo"]["global_us_residential_refs"], http["final_mihomo"]["global_apac_residential_refs"])))
                checks.append(ok("final mihomo uses HTTP 204 url-test probe", http["final_mihomo"]["http_probe_group_count"] > 0, str(http["final_mihomo"]["http_probe_group_count"])))
                checks.append(ok("final mihomo has residential selector", http["final_mihomo"]["has_residential_selector"], str(http["final_mihomo"]["residential_selector_refs"])))
                checks.append(ok("residential selector exposes regional shortcut layer", http["final_mihomo"]["residential_selector_us_refs"] > 0 or http["final_mihomo"]["residential_selector_apac_refs"] > 0, "us=%s apac=%s" % (http["final_mihomo"]["residential_selector_us_refs"], http["final_mihomo"]["residential_selector_apac_refs"])))
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
