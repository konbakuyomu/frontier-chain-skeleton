#!/usr/bin/env python3
"""Render the contract-owned AI routing blocks without touching surrounding text."""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "subscription-hub"))

from ai_routing import (  # noqa: E402
    CanonicalRule,
    contract_rules,
    emitted_contract_rules,
    load_contract,
    logical_items,
    validate_registry,
)


MAIN_BEGIN = "// AI-ROUTING:BEGIN"
MAIN_END = "// AI-ROUTING:END"
SHADOWROCKET_BEGIN = "# AI-ROUTING:BEGIN"
SHADOWROCKET_END = "# AI-ROUTING:END"
SHADOWROCKET_PROVIDERS_BEGIN = "# AI-ROUTING-PROVIDERS:BEGIN"
SHADOWROCKET_PROVIDERS_END = "# AI-ROUTING-PROVIDERS:END"
MIRROR_BASE_URL = "https://link.konbakuyomu.us/rules"

GEMINI_MIHOMO_RULES = (
    "DOMAIN,cloudcode-pa.googleapis.com",
    "DOMAIN,cloudaicompanion.googleapis.com",
    "DOMAIN-SUFFIX,generativelanguage.googleapis.com",
    "DOMAIN-SUFFIX,aistudio.google.com",
    "DOMAIN,claude.googleapis.com",
)
GEMINI_SHADOWROCKET_RULES = (
    "DOMAIN,gemini.google.com",
    "DOMAIN,aistudio.google.com",
    "DOMAIN-SUFFIX,generativelanguage.googleapis.com",
    "DOMAIN-SUFFIX,makersuite.google.com",
    "DOMAIN,bard.google.com",
    "DOMAIN,ai.google.dev",
    "DOMAIN,alkalimakersuite-pa.clients6.google.com",
    "DOMAIN,alkalicore-pa.clients6.google.com",
    "DOMAIN,proactivebackend-pa.googleapis.com",
    "DOMAIN,geller-pa.googleapis.com",
    "DOMAIN,cloudcode-pa.googleapis.com",
    "DOMAIN,cloudaicompanion.googleapis.com",
    "DOMAIN,claude.googleapis.com",
)
HISTORICAL_BROAD_AI_RULES = (
    "DOMAIN-SUFFIX,sentry.io",
    "DOMAIN-SUFFIX,statsigapi.net",
    "DOMAIN-SUFFIX,datadoghq.com",
    "DOMAIN-KEYWORD,browser-intake",
    "DOMAIN-KEYWORD,datadog",
    "DOMAIN-KEYWORD,sentry",
    "DOMAIN-KEYWORD,statsig",
    "DOMAIN-KEYWORD,sift",
    "DOMAIN-SUFFIX,intercom.io",
    "DOMAIN-SUFFIX,intercomcdn.com",
    "DOMAIN-SUFFIX,website-files.com",
    "DOMAIN-SUFFIX,challenges.cloudflare.com",
    "DOMAIN,static.cloudflareinsights.com",
    "DOMAIN-SUFFIX,host.livekit.cloud",
    "DOMAIN-SUFFIX,turn.livekit.cloud",
    "DOMAIN-SUFFIX,client-api.arkoselabs.com",
    "GEOSITE,category-ntp",
)


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


def rule_to_client(rule: CanonicalRule, target: str) -> str:
    kind_map = {"domain": "DOMAIN", "suffix": "DOMAIN-SUFFIX", "regex": "DOMAIN-REGEX"}
    return f"{kind_map[rule.kind]},{rule.value},{target}"


def js_string(value: str) -> str:
    return json.dumps(value, ensure_ascii=True)


def render_mihomo(contract: dict[str, object], registry: dict[str, object]) -> list[str]:
    providers = logical_items(registry, "mihomo")
    provider_names = [str(item["logical_provider"]) for item in providers]
    managed_names = tuple(dict.fromkeys(("ai-dustin", "ai-openai", "ai-claude", *provider_names, "ai-gemini")))
    official_rules = [rule_to_client(rule, "${aiGroup}") for rule in emitted_contract_rules(contract)]
    negative_rules = [
        rule
        for service in ("openai", "anthropic", "xai")
        for rule in contract_rules(contract, service, "negative")
    ]
    negative_domain_controls = sorted({rule.value for rule in negative_rules if rule.kind == "domain"})
    negative_suffix_controls = sorted({rule.value for rule in negative_rules if rule.kind == "suffix"})
    if any(rule.kind == "regex" for rule in negative_rules):
        raise SystemExit("AI routing negative regex controls require an explicit Mihomo first-match adapter")
    lines = [
        MAIN_BEGIN,
        "// Generated from ai-routing-contract.json and subscription-hub/rules.json.",
        "const managedAiProviderNames = new Set([",
        *[f"  {js_string(name)}," for name in managed_names],
        "]);",
    ]
    lines.extend(
        [
            "const historicalBroadAiRules = new Set([",
            *[f"  {js_string(rule)}," for rule in HISTORICAL_BROAD_AI_RULES],
            "]);",
        ]
    )
    lines.extend(
        [
            "const negativeAiDomainControls = new Set([",
            *[f"  {js_string(value)}," for value in negative_domain_controls],
            "]);",
            "const negativeAiSuffixControls = new Set([",
            *[f"  {js_string(value)}," for value in negative_suffix_controls],
            "]);",
            "function isNegativeAiControl(parts) {",
            "  const ruleType = parts[0];",
            "  const ruleValue = String(parts[1] || \"\").toLowerCase().replace(/[.]$/, \"\");",
            "  if (ruleType !== \"DOMAIN\" && ruleType !== \"DOMAIN-SUFFIX\") return false;",
            "  if (negativeAiDomainControls.has(ruleValue)) return true;",
            "  return [...negativeAiSuffixControls].some(control =>",
            "    ruleValue === control || ruleValue.endsWith(`.${control}`)",
            "  );",
            "}",
            "const aiProviders = {",
        ]
    )
    for item in providers:
        logical = str(item["logical_provider"])
        filename = str(item["file"])
        lines.extend(
            [
                f"  {js_string(logical)}: {{",
                "    type: \"http\", behavior: \"classical\", format: \"text\",",
                f"    url: ruleMirrorUrl({js_string(filename)}),",
                f"    path: \"./ruleset/{logical}.list\", interval: 86400,",
                "    proxy: selectGroup,",
                "  },",
            ]
        )
    lines.extend(
        [
            "  \"ai-gemini\": {",
            "    type: \"http\", behavior: \"classical\", format: \"text\",",
            "    url: ruleMirrorUrl(\"mihomo-ai-gemini.list\"),",
            "    path: \"./ruleset/ai-gemini.list\", interval: 86400,",
            "    proxy: selectGroup,",
            "  },",
            "};",
            "const retainedRuleProviders = { ...(config[\"rule-providers\"] || {}) };",
            "for (const providerName of managedAiProviderNames) delete retainedRuleProviders[providerName];",
            "config[\"rule-providers\"] = { ...retainedRuleProviders, ...aiProviders };",
            "function readExtraAiApiHosts() {",
            "  const collected = [];",
            "  if (typeof globalThis !== \"undefined\" && Array.isArray(globalThis.__frontierExtraAiApiHosts)) {",
            "    collected.push(...globalThis.__frontierExtraAiApiHosts);",
            "  }",
            "  try {",
            "    if (typeof $arguments !== \"undefined\" && $arguments && $arguments.extra_ai_api_hosts) {",
            "      const raw = $arguments.extra_ai_api_hosts;",
            "      if (Array.isArray(raw)) collected.push(...raw);",
            "      else if (typeof raw === \"string\" && raw.trim()) collected.push(...raw.split(/[\\s,]+/));",
            "    }",
            "  } catch (error) {}",
            "  const hostRe = /^(?=.{1,253}$)(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\\.)+[a-z0-9-]{2,63}$/i;",
            "  const unique = [];",
            "  for (const item of collected) {",
            "    const host = String(item || \"\").trim().toLowerCase().replace(/[.]$/, \"\");",
            "    if (!hostRe.test(host) || unique.includes(host)) continue;",
            "    unique.push(host);",
            "  }",
            "  return unique;",
            "}",
            "const extraAiApiHostRules = readExtraAiApiHosts().map(host => `DOMAIN,${host},${aiGroup}`);",
            "const aiRules = [",
            "  // Official required baseline, then selected conditional first-party hosts.",
            *[f"  `{rule}`," for rule in official_rules],
            "  // Preserve the existing Gemini CLI compatibility baseline.",
            *[f"  `{rule},${{aiGroup}}`," for rule in GEMINI_MIHOMO_RULES],
            "  ...extraAiApiHostRules,",
            "  // Policy-projected SJC logical providers only supplement the local baseline.",
            *[f"  `RULE-SET,{name},${{aiGroup}}`," for name in provider_names],
            "  `RULE-SET,ai-gemini,${aiGroup}`",
            "];",
            "const canonicalAiRules = new Set(aiRules.map(rule => String(rule).trim()));",
            "let removedManagedAiRules = 0;",
            "if (Array.isArray(config.rules)) {",
            "  config.rules = config.rules.filter(rule => {",
            "    const parts = String(rule).split(\",\").map(part => part.trim());",
            "    if (getRuleTarget(rule) !== aiGroup) return true;",
            "    const ruleHead = `${parts[0]},${parts[1]}`;",
            "    const isManagedProvider = parts[0] === \"RULE-SET\" && managedAiProviderNames.has(parts[1]);",
            "    const isCanonicalBaseline = canonicalAiRules.has(String(rule).trim());",
            "    if (!isManagedProvider && !isCanonicalBaseline && !historicalBroadAiRules.has(ruleHead) && !isNegativeAiControl(parts)) return true;",
            "    removedManagedAiRules += 1;",
            "    return false;",
            "  });",
            "}",
        ]
    )
    lines.extend(
        [
            "if (config.rules && Array.isArray(config.rules)) {",
            "  validateRuleTargets(config, aiRules);",
            "  const insertedAiRules = prependUniqueRules(config, aiRules);",
            f"  logInfo(`AI routing contract applied: removed=${{removedManagedAiRules}}, baseline={len(official_rules)}, providers={len(provider_names)}, inserted=${{insertedAiRules.length}}`);",
            "}",
            MAIN_END,
        ]
    )
    return lines


def render_shadowrocket_baseline(contract: dict[str, object]) -> list[str]:
    official_rules = [rule_to_client(rule, "🤖 AI 服务") for rule in emitted_contract_rules(contract)]
    return [
        SHADOWROCKET_BEGIN,
        "# Generated from ai-routing-contract.json.",
        "# Official required baseline and selected first-party conditionals precede remote mirrors.",
        *official_rules,
        "",
        "# Gemini compatibility baseline remains independent of the three-service contract.",
        *[f"{rule},🤖 AI 服务" for rule in GEMINI_SHADOWROCKET_RULES],
        SHADOWROCKET_END,
    ]


def render_shadowrocket_providers(registry: dict[str, object]) -> list[str]:
    providers = logical_items(registry, "shadowrocket")
    lines = [
        SHADOWROCKET_PROVIDERS_BEGIN,
        "# Policy-projected logical AI mirrors. These URLs stay stable when sources change.",
    ]
    for item in providers:
        lines.append(f"RULE-SET,{MIRROR_BASE_URL}/{item['file']},🤖 AI 服务")
    lines.append(SHADOWROCKET_PROVIDERS_END)
    return lines


def replace_block(text: str, begin: str, end: str, lines: list[str]) -> str:
    pattern = re.compile(
        rf"(?ms)^(?P<indent>[ \t]*){re.escape(begin)}\r?\n.*?^(?P=indent){re.escape(end)}(?:\r?\n|$)"
    )
    match = pattern.search(text)
    if not match:
        raise SystemExit(f"missing generated marker block: {begin}")
    if pattern.search(text, match.end()):
        raise SystemExit(f"duplicate generated marker block: {begin}")
    newline = "\r\n" if "\r\n" in text else "\n"
    indent = match.group("indent")
    block = newline.join(indent + line if line else "" for line in lines) + newline
    return text[:match.start()] + block + text[match.end():]


def render_file(path: Path, begin: str, end: str, lines: list[str]) -> tuple[bytes, bytes]:
    before = path.read_bytes()
    try:
        text = before.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise SystemExit(f"{path} is not UTF-8") from exc
    after = replace_block(text, begin, end, lines).encode("utf-8")
    return before, after


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description="Render contract-owned AI routing blocks.")
    parser.add_argument("--contract", default=str(ROOT / "ai-routing-contract.json"))
    parser.add_argument("--registry", default=str(ROOT / "subscription-hub" / "rules.json"))
    parser.add_argument("--main-js", default=str(ROOT / "main.js"))
    parser.add_argument("--shadowrocket-conf", default=str(ROOT / "shadowrocket.conf"))
    parser.add_argument("--check", action="store_true", help="fail when either generated block has drifted")
    parser.add_argument("--write", action="store_true", help="replace only the two generated marker blocks")
    args = parser.parse_args(argv)
    if args.check == args.write:
        raise SystemExit("choose exactly one of --check or --write")

    try:
        contract = load_contract(Path(args.contract))
    except ValueError as exc:
        raise SystemExit(str(exc)) from exc
    registry = load_registry(Path(args.registry), contract)
    targets = (
        (Path(args.main_js), MAIN_BEGIN, MAIN_END, render_mihomo(contract, registry)),
        (
            Path(args.shadowrocket_conf),
            SHADOWROCKET_BEGIN,
            SHADOWROCKET_END,
            render_shadowrocket_baseline(contract),
        ),
        (
            Path(args.shadowrocket_conf),
            SHADOWROCKET_PROVIDERS_BEGIN,
            SHADOWROCKET_PROVIDERS_END,
            render_shadowrocket_providers(registry),
        ),
    )
    drifted = []
    for path, begin, end, lines in targets:
        before, after = render_file(path, begin, end, lines)
        if before != after:
            drifted.append(path)
            if args.write:
                path.write_bytes(after)
    if args.check and drifted:
        raise SystemExit("AI routing generated block drift: " + ", ".join(str(path) for path in drifted))
    print("OK: AI routing generated blocks are current" if not drifted else "OK: AI routing generated blocks updated")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
