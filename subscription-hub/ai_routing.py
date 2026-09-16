"""Shared AI-routing contract, registry, and semantic projection helpers.

The subscription hub only publishes public third-party rule material.  This
module keeps the official routing policy separate from those sources so a
source can be replaced without changing either client configuration.
"""

from __future__ import annotations

from dataclasses import dataclass
import json
import re
from pathlib import Path
from typing import Any, Iterable


SERVICE_IDS = ("openai", "anthropic", "xai")
CONTRACT_CATEGORIES = ("required", "conditional", "shared_not_ai", "negative")
RULE_TYPES = {"domain", "suffix", "regex"}
LOGICAL_PROVIDERS = (
    "ai-openai",
    "ai-anthropic",
    "ai-xai",
    "ai-community-supplement",
)
HOST_RE = re.compile(r"(?=.{1,253}\Z)(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z0-9-]{2,63}\Z")


class ContractError(ValueError):
    """Raised when the official policy contract is malformed or ambiguous."""


class RegistryError(ValueError):
    """Raised when a rule-mirror registry cannot provide a stable interface."""


class SemanticError(ValueError):
    """Raised when a downloaded source cannot safely become a client rule set."""


@dataclass(frozen=True, order=True)
class CanonicalRule:
    """A client-neutral domain-routing rule."""

    kind: str
    value: str


def _as_mapping(value: object, label: str) -> dict[str, object]:
    if not isinstance(value, dict):
        raise ContractError(f"{label} must be an object")
    return value


def _validate_host(value: object, label: str) -> str:
    if not isinstance(value, str):
        raise ContractError(f"{label} must be a string")
    normalized = value.strip().lower().rstrip(".")
    if not HOST_RE.fullmatch(normalized):
        raise ContractError(f"{label} must be a DNS hostname")
    return normalized


def _validate_semantic_host(value: object, label: str) -> str:
    try:
        return _validate_host(value, label)
    except ContractError as exc:
        raise SemanticError(str(exc)) from exc


def normalize_contract_rule(value: object, label: str) -> CanonicalRule:
    item = _as_mapping(value, label)
    unexpected = set(item) - {"type", "value", "emit"}
    if unexpected:
        raise ContractError(f"{label} has unsupported fields: {', '.join(sorted(unexpected))}")
    rule_type = item.get("type")
    if rule_type not in RULE_TYPES:
        raise ContractError(f"{label} has unsupported rule type: {rule_type}")
    if "emit" in item and not isinstance(item["emit"], bool):
        raise ContractError(f"{label}.emit must be boolean")
    raw_value = item.get("value")
    if rule_type == "regex":
        if not isinstance(raw_value, str) or not raw_value.strip():
            raise ContractError(f"{label}.value must be a non-empty regex")
        return CanonicalRule("regex", raw_value.strip())
    return CanonicalRule(str(rule_type), _validate_host(raw_value, f"{label}.value"))


def rule_covers(candidate: CanonicalRule, expected: CanonicalRule) -> bool:
    """Return whether ``candidate`` routes every hostname required by ``expected``."""

    if candidate.kind == "regex" or expected.kind == "regex":
        return candidate == expected
    if candidate.kind == "domain":
        return expected.kind == "domain" and candidate.value == expected.value
    if candidate.kind == "suffix":
        if expected.kind == "domain":
            return expected.value == candidate.value or expected.value.endswith("." + candidate.value)
        return expected.kind == "suffix" and expected.value == candidate.value
    return False


def rule_intersects(candidate: CanonicalRule, control: CanonicalRule) -> bool:
    """Return whether publishing ``candidate`` could route a control hostname."""

    if candidate.kind == "regex" or control.kind == "regex":
        return candidate == control
    if control.kind == "domain":
        return rule_covers(candidate, control)
    if control.kind == "suffix":
        if candidate.kind == "domain":
            return candidate.value == control.value or candidate.value.endswith("." + control.value)
        if candidate.kind == "suffix":
            return candidate.value == control.value or candidate.value.endswith("." + control.value)
    return False


def validate_contract(contract: object) -> dict[str, object]:
    data = _as_mapping(contract, "AI routing contract")
    if data.get("version") != 1:
        raise ContractError("AI routing contract version must be 1")
    unexpected = set(data) - {"$schema", "version", "services", "evidence"}
    if unexpected:
        raise ContractError("AI routing contract has unsupported fields: " + ", ".join(sorted(unexpected)))
    if data.get("$schema") not in {None, "./ai-routing-contract.schema.json"}:
        raise ContractError("AI routing contract has an unexpected schema reference")
    services = _as_mapping(data.get("services"), "AI routing contract services")
    unknown_services = set(services) - set(SERVICE_IDS)
    missing_services = set(SERVICE_IDS) - set(services)
    if unknown_services:
        raise ContractError("AI routing contract has unknown services: " + ", ".join(sorted(unknown_services)))
    if missing_services:
        raise ContractError("AI routing contract misses services: " + ", ".join(sorted(missing_services)))

    routed_rules: list[tuple[str, str, CanonicalRule]] = []
    control_rules: list[tuple[str, str, CanonicalRule]] = []
    for service in SERVICE_IDS:
        service_data = _as_mapping(services[service], f"services.{service}")
        unexpected_categories = set(service_data) - set(CONTRACT_CATEGORIES)
        missing_categories = set(CONTRACT_CATEGORIES) - set(service_data)
        if unexpected_categories:
            raise ContractError(f"services.{service} has unknown categories: {', '.join(sorted(unexpected_categories))}")
        if missing_categories:
            raise ContractError(f"services.{service} misses categories: {', '.join(sorted(missing_categories))}")
        seen: set[CanonicalRule] = set()
        rules_by_category: dict[str, list[CanonicalRule]] = {}
        for category in CONTRACT_CATEGORIES:
            raw_rules = service_data[category]
            if not isinstance(raw_rules, list):
                raise ContractError(f"services.{service}.{category} must be a list")
            normalized: list[CanonicalRule] = []
            for index, raw_rule in enumerate(raw_rules):
                rule = normalize_contract_rule(raw_rule, f"services.{service}.{category}[{index}]")
                if rule in seen:
                    raise ContractError(f"services.{service} repeats or conflicts on {rule.kind}:{rule.value}")
                if category in {"shared_not_ai", "negative"} and isinstance(raw_rule, dict) and raw_rule.get("emit"):
                    raise ContractError(f"services.{service}.{category}[{index}] must not emit a client rule")
                seen.add(rule)
                normalized.append(rule)
            rules_by_category[category] = normalized
        if not rules_by_category["required"]:
            raise ContractError(f"services.{service}.required must not be empty")
        for category in ("required", "conditional"):
            routed_rules.extend((service, category, rule) for rule in rules_by_category[category])
        for category in ("shared_not_ai", "negative"):
            control_rules.extend((service, category, rule) for rule in rules_by_category[category])

    for routed_service, routed_category, routed_rule in routed_rules:
        for control_service, control_category, control_rule in control_rules:
            if rule_intersects(routed_rule, control_rule) or rule_intersects(control_rule, routed_rule):
                raise ContractError(
                    f"services.{routed_service}.{routed_category} conflicts with "
                    f"services.{control_service}.{control_category}: "
                    f"{routed_rule.kind}:{routed_rule.value} vs {control_rule.kind}:{control_rule.value}"
                )

    evidence = data.get("evidence")
    if not isinstance(evidence, list):
        raise ContractError("AI routing contract evidence must be a list")
    evidence_services: set[str] = set()
    for index, entry in enumerate(evidence):
        if not isinstance(entry, dict):
            raise ContractError(f"evidence[{index}] must be an object")
        if set(entry) != {"service", "url", "verified_at"}:
            raise ContractError(f"evidence[{index}] must contain only service, url, verified_at")
        service = entry.get("service")
        url = entry.get("url")
        verified_at = entry.get("verified_at")
        if service not in SERVICE_IDS:
            raise ContractError(f"evidence[{index}] has unknown service: {service}")
        if not isinstance(url, str) or not url.startswith("https://"):
            raise ContractError(f"evidence[{index}].url must be public https")
        if not isinstance(verified_at, str) or not re.fullmatch(r"\d{4}-\d{2}-\d{2}", verified_at):
            raise ContractError(f"evidence[{index}].verified_at must be YYYY-MM-DD")
        evidence_services.add(str(service))
    if set(SERVICE_IDS) - evidence_services:
        raise ContractError("AI routing contract requires public evidence for every service")
    return data


def load_contract(path: Path) -> dict[str, object]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise ContractError(f"missing AI routing contract: {path}") from exc
    except json.JSONDecodeError as exc:
        raise ContractError(f"invalid AI routing contract JSON: {exc.msg}") from exc
    return validate_contract(data)


def contract_rules(contract: dict[str, object], service: str, category: str) -> list[CanonicalRule]:
    services = _as_mapping(contract["services"], "AI routing contract services")
    service_data = _as_mapping(services[service], f"services.{service}")
    return [
        normalize_contract_rule(item, f"services.{service}.{category}[{index}]")
        for index, item in enumerate(service_data[category])
    ]


def emitted_contract_rules(contract: dict[str, object]) -> list[CanonicalRule]:
    """Return the deterministic local baseline, required before selected conditional rules."""

    rules: list[CanonicalRule] = []
    for category in ("required", "conditional"):
        for service in SERVICE_IDS:
            services = _as_mapping(contract["services"], "AI routing contract services")
            service_data = _as_mapping(services[service], f"services.{service}")
            raw_rules = service_data[category]
            assert isinstance(raw_rules, list)
            for index, raw_rule in enumerate(raw_rules):
                if category == "conditional" and not bool(raw_rule.get("emit")):
                    continue
                rule = normalize_contract_rule(raw_rule, f"services.{service}.{category}[{index}]")
                if rule not in rules:
                    rules.append(rule)
    return rules


def policy_controls(contract: dict[str, object]) -> list[tuple[str, CanonicalRule]]:
    controls: list[tuple[str, CanonicalRule]] = []
    for category in ("negative", "shared_not_ai"):
        for service in SERVICE_IDS:
            for rule in contract_rules(contract, service, category):
                item = (category, rule)
                if item not in controls:
                    controls.append(item)
    return controls


def item_enabled(item: dict[str, object]) -> bool:
    """Return the explicit registry state; an omitted value is never active."""

    return item.get("enabled") is True


def _require_public_https(value: object, label: str) -> str:
    if not isinstance(value, str) or not value.startswith("https://"):
        raise RegistryError(f"{label} must be public https")
    return value


def validate_registry(registry: object, contract: dict[str, object]) -> dict[str, object]:
    data = _as_mapping(registry, "rule registry")
    if data.get("version") != 2:
        raise RegistryError("rule registry version must be 2")
    if not isinstance(data.get("rules_path_prefix"), str) or not str(data["rules_path_prefix"]).startswith("/"):
        raise RegistryError("rule registry rules_path_prefix must start with '/'")
    items = data.get("items")
    if not isinstance(items, list) or not items:
        raise RegistryError("rule registry must contain non-empty items[]")
    seen_ids: set[str] = set()
    seen_files: set[str] = set()
    logical_by_client: dict[str, dict[str, int]] = {"mihomo": {}, "shadowrocket": {}}
    for index, raw_item in enumerate(items):
        if not isinstance(raw_item, dict):
            raise RegistryError(f"rule item {index + 1} must be an object")
        rule_id = raw_item.get("id")
        filename = raw_item.get("file")
        source = raw_item.get("source")
        client = raw_item.get("client")
        fmt = raw_item.get("format")
        rule_type = raw_item.get("rule_type")
        if not isinstance(rule_id, str) or not re.fullmatch(r"[a-z0-9][a-z0-9-]*", rule_id):
            raise RegistryError(f"invalid rule id: {rule_id}")
        if rule_id in seen_ids:
            raise RegistryError(f"duplicate rule id: {rule_id}")
        seen_ids.add(rule_id)
        if not isinstance(filename, str) or not re.fullmatch(r"[A-Za-z0-9._-]+", filename):
            raise RegistryError(f"invalid rule filename for {rule_id}: {filename}")
        if filename in seen_files:
            raise RegistryError(f"duplicate rule filename: {filename}")
        seen_files.add(filename)
        source = _require_public_https(source, f"rule source for {rule_id}")
        if client not in {"shadowrocket", "mihomo"}:
            raise RegistryError(f"invalid client for {rule_id}: {client}")
        if fmt not in {"text", "yaml", "mrs"}:
            raise RegistryError(f"invalid format for {rule_id}: {fmt}")
        if rule_type not in {"RULE-SET", "DOMAIN-SET", "rule-provider"}:
            raise RegistryError(f"invalid rule_type for {rule_id}: {rule_type}")
        if client == "shadowrocket" and rule_type not in {"RULE-SET", "DOMAIN-SET"}:
            raise RegistryError(f"shadowrocket item has invalid rule_type: {rule_id}")
        if client == "mihomo" and rule_type != "rule-provider":
            raise RegistryError(f"mihomo item must be rule-provider: {rule_id}")
        if "enabled" not in raw_item:
            raise RegistryError(f"enabled must be explicitly set for {rule_id}")
        if not isinstance(raw_item["enabled"], bool):
            raise RegistryError(f"enabled must be boolean for {rule_id}")

        logical = raw_item.get("logical_provider")
        if logical is None:
            continue
        if logical not in LOGICAL_PROVIDERS:
            raise RegistryError(f"invalid logical_provider for {rule_id}: {logical}")
        if not item_enabled(raw_item):
            continue
        if fmt == "mrs":
            raise RegistryError(
                f"active logical provider {rule_id} cannot publish opaque MRS; "
                "publish a policy-projected classical text rule set instead"
            )
        adapter = raw_item.get("adapter")
        if adapter not in {"domain-list-to-classical", "classical-to-classical"}:
            raise RegistryError(f"active logical provider {rule_id} has invalid adapter: {adapter}")
        semantic_source = _require_public_https(raw_item.get("semantic_source"), f"semantic_source for {rule_id}")
        if source != semantic_source:
            raise RegistryError(f"active logical provider {rule_id} must use source == semantic_source")
        covers = raw_item.get("covers")
        if not isinstance(covers, list) or any(service not in SERVICE_IDS for service in covers):
            raise RegistryError(f"invalid covers for {rule_id}")
        if logical == "ai-community-supplement":
            if covers:
                raise RegistryError(f"community supplement must not declare required service coverage: {rule_id}")
        elif len(covers) != 1:
            raise RegistryError(f"service provider must declare exactly one covered service: {rule_id}")
        if fmt != "text":
            raise RegistryError(f"active logical provider adapters must publish text: {rule_id}")
        behavior = raw_item.get("provider_behavior")
        if client == "mihomo" and behavior != "classical":
            raise RegistryError(f"mihomo logical provider must use classical behavior: {rule_id}")
        if client == "shadowrocket" and behavior is not None:
            raise RegistryError(f"shadowrocket logical provider must not declare provider_behavior: {rule_id}")
        semantic_required = raw_item.get("semantic_required", [])
        if not isinstance(semantic_required, list):
            raise RegistryError(f"semantic_required must be a list for {rule_id}")
        for item_index, raw_rule in enumerate(semantic_required):
            source_rule = normalize_contract_rule(raw_rule, f"{rule_id}.semantic_required[{item_index}]")
            if not any(
                source_rule == contract_rule
                for service in covers
                for category in ("required", "conditional")
                for contract_rule in contract_rules(contract, service, category)
            ):
                raise RegistryError(
                    f"semantic_required for {rule_id} must come from its required or conditional contract rules"
                )
        allowed_attributes = raw_item.get("allowed_attributes", [])
        if not isinstance(allowed_attributes, list) or any(
            not isinstance(value, str) or not re.fullmatch(r"[A-Za-z0-9_-]+", value)
            for value in allowed_attributes
        ):
            raise RegistryError(f"allowed_attributes must contain attribute names for {rule_id}")
        allowed_regex = raw_item.get("allowed_regex_exclusions", [])
        if not isinstance(allowed_regex, list) or any(not isinstance(value, str) or not value for value in allowed_regex):
            raise RegistryError(f"allowed_regex_exclusions must contain regex strings for {rule_id}")
        for value in allowed_regex:
            try:
                re.compile(value)
            except re.error as exc:
                raise RegistryError(f"invalid allowed regex exclusion for {rule_id}: {exc}") from exc
        logical_by_client[str(client)][str(logical)] = logical_by_client[str(client)].get(str(logical), 0) + 1

    for client, logical_counts in logical_by_client.items():
        missing = set(LOGICAL_PROVIDERS) - set(logical_counts)
        duplicate = [logical for logical, count in logical_counts.items() if count != 1]
        if missing or duplicate:
            detail = []
            if missing:
                detail.append("missing=" + ",".join(sorted(missing)))
            if duplicate:
                detail.append("duplicate=" + ",".join(sorted(duplicate)))
            raise RegistryError(f"{client} logical providers invalid: {' '.join(detail)}")
    return data


def active_items(registry: dict[str, object], client: str | None = None) -> list[dict[str, object]]:
    items = registry["items"]
    assert isinstance(items, list)
    return [
        item
        for item in items
        if isinstance(item, dict) and item_enabled(item) and (client is None or item.get("client") == client)
    ]


def logical_items(registry: dict[str, object], client: str) -> list[dict[str, object]]:
    by_name = {
        str(item["logical_provider"]): item
        for item in active_items(registry, client)
        if item.get("logical_provider") in LOGICAL_PROVIDERS
    }
    return [by_name[name] for name in LOGICAL_PROVIDERS]


def derived_registry_counts(registry: dict[str, object]) -> dict[str, object]:
    enabled = active_items(registry)
    clients = {
        client: sum(1 for item in enabled if item.get("client") == client)
        for client in ("shadowrocket", "mihomo")
    }
    return {
        "total": len(enabled),
        "disabled": len(registry["items"]) - len(enabled),
        "clients": clients,
        "logical_providers": {
            client: len(logical_items(registry, client))
            for client in ("shadowrocket", "mihomo")
        },
    }


def parse_domain_list(text: str) -> tuple[list[CanonicalRule], dict[str, object]]:
    """Parse the v2fly-style domain-list subset used by active AI providers.

    Every unsupported construct is explicit: attributes and regexes are
    returned in the report or projection path, while unknown directives fail.
    """

    rules: list[CanonicalRule] = []
    report: dict[str, object] = {
        "comments": 0,
        "attributes": 0,
        "attribute_names": [],
        "regex": 0,
        "regex_values": [],
        "bare": 0,
        "full": 0,
        "plus_suffix": 0,
    }
    for line_number, raw_line in enumerate(text.splitlines(), 1):
        line = raw_line.strip()
        if not line or line.startswith("#") or line.startswith("//"):
            report["comments"] += 1
            continue
        if line.startswith("include:"):
            raise SemanticError(f"line {line_number}: include directives require explicit expansion")
        if line.startswith("regexp:"):
            value = line[len("regexp:"):].strip()
            if not value:
                raise SemanticError(f"line {line_number}: empty regexp")
            rules.append(CanonicalRule("regex", value))
            report["regex"] += 1
            report["regex_values"].append(value)
            continue
        if line.startswith("full:"):
            value = line[len("full:"):].strip()
            if " " in value or "\t" in value:
                attribute_match = re.fullmatch(r"([^\s]+)\s+@([A-Za-z0-9_-]+)", value)
                if attribute_match:
                    _validate_semantic_host(attribute_match.group(1), f"line {line_number}")
                    report["attributes"] += 1
                    report["attribute_names"].append(attribute_match.group(2))
                    continue
                raise SemanticError(f"line {line_number}: unknown full: syntax")
            rules.append(CanonicalRule("domain", _validate_semantic_host(value, f"line {line_number}")))
            report["full"] += 1
            continue
        if line.startswith("+."):
            value = line[2:].strip()
            rules.append(CanonicalRule("suffix", _validate_semantic_host(value, f"line {line_number}")))
            report["plus_suffix"] += 1
            continue
        if line.startswith("@"):
            attribute_match = re.fullmatch(r"@([A-Za-z0-9_-]+)", line)
            if not attribute_match:
                raise SemanticError(f"line {line_number}: unknown attribute syntax")
            report["attributes"] += 1
            report["attribute_names"].append(attribute_match.group(1))
            continue
        if " " in line or "\t" in line:
            attribute_match = re.fullmatch(r"([^\s]+)\s+@([A-Za-z0-9_-]+)", line)
            if attribute_match:
                _validate_semantic_host(attribute_match.group(1), f"line {line_number}")
                report["attributes"] += 1
                report["attribute_names"].append(attribute_match.group(2))
                continue
            raise SemanticError(f"line {line_number}: unknown domain-list syntax")
        rules.append(CanonicalRule("suffix", _validate_semantic_host(line, f"line {line_number}")))
        report["bare"] += 1
    if not rules:
        raise SemanticError("domain-list has no routable rules")
    return rules, report


def parse_classical_rules(text: str) -> list[CanonicalRule]:
    rules: list[CanonicalRule] = []
    for line_number, raw_line in enumerate(text.splitlines(), 1):
        line = raw_line.strip()
        if not line or line.startswith("#") or line.startswith("//"):
            continue
        parts = [part.strip() for part in line.split(",")]
        if len(parts) < 2:
            raise SemanticError(f"line {line_number}: malformed classical rule")
        kind_map = {"DOMAIN": "domain", "DOMAIN-SUFFIX": "suffix", "DOMAIN-REGEX": "regex"}
        kind = kind_map.get(parts[0])
        if not kind:
            raise SemanticError(f"line {line_number}: unsupported classical rule type {parts[0]}")
        value = parts[1]
        if kind == "regex":
            if not value:
                raise SemanticError(f"line {line_number}: empty classical regex")
            rules.append(CanonicalRule(kind, value))
        else:
            rules.append(CanonicalRule(kind, _validate_semantic_host(value, f"line {line_number}")))
    if not rules:
        raise SemanticError("classical output has no routable rules")
    return rules


def project_rules(
    raw_rules: Iterable[CanonicalRule],
    contract: dict[str, object],
    *,
    regex_policy: str = "exclude-with-review",
) -> tuple[list[CanonicalRule], dict[str, int]]:
    """Apply the official negative/shared policy before a client sees a rule."""

    if regex_policy not in {"exclude-with-review", "domain-regex"}:
        raise SemanticError(f"unsupported regex policy: {regex_policy}")
    controls = policy_controls(contract)
    projected: list[CanonicalRule] = []
    report = {
        "excluded_negative": 0,
        "excluded_shared_not_ai": 0,
        "excluded_regex_review": 0,
    }
    for rule in raw_rules:
        if rule.kind == "regex" and regex_policy == "exclude-with-review":
            report["excluded_regex_review"] += 1
            continue
        excluded = False
        for category, control in controls:
            if rule_intersects(rule, control):
                report["excluded_" + category] += 1
                excluded = True
                break
        if not excluded and rule not in projected:
            projected.append(rule)
    if not projected:
        raise SemanticError("policy projection removed every routable rule")
    return projected, report


def validate_declared_exclusions(item: dict[str, object], parsed: dict[str, object]) -> dict[str, int]:
    """Allow only reviewed source constructs that the classical projection omits."""

    observed_attributes = set(str(value) for value in parsed.get("attribute_names", []))
    allowed_attributes = set(str(value) for value in item.get("allowed_attributes", []))
    unknown_attributes = observed_attributes - allowed_attributes
    if unknown_attributes:
        raise SemanticError(
            f"{item.get('id')} has unreviewed attributes: {', '.join(sorted(unknown_attributes))}"
        )

    observed_regex = set(str(value) for value in parsed.get("regex_values", []))
    allowed_regex = set(str(value) for value in item.get("allowed_regex_exclusions", []))
    unknown_regex = observed_regex - allowed_regex
    if unknown_regex:
        raise SemanticError(f"{item.get('id')} has unreviewed regex semantics")
    return {
        "reviewed_attribute_count": int(parsed.get("attributes", 0)),
        "reviewed_regex_count": int(parsed.get("regex", 0)),
    }


def semantic_required_rules(item: dict[str, object], contract: dict[str, object]) -> list[CanonicalRule]:
    raw_required = item.get("semantic_required")
    if isinstance(raw_required, list) and raw_required:
        return [
            normalize_contract_rule(rule, f"{item.get('id')}.semantic_required[{index}]")
            for index, rule in enumerate(raw_required)
        ]
    required: list[CanonicalRule] = []
    for service in item.get("covers", []):
        required.extend(contract_rules(contract, str(service), "required"))
    return required


def validate_semantic_rules(item: dict[str, object], rules: Iterable[CanonicalRule], contract: dict[str, object]) -> dict[str, int]:
    normalized = list(rules)
    required = semantic_required_rules(item, contract)
    missing = [expected for expected in required if not any(rule_covers(candidate, expected) for candidate in normalized)]
    if missing:
        raise SemanticError(f"{item.get('id')} misses {len(missing)} required semantic sentinels")
    return {"normalized_count": len(normalized), "required_sentinel_count": len(required)}


def render_classical_rules(item: dict[str, object], rules: Iterable[CanonicalRule]) -> bytes:
    lines = ["# Generated by subscription-hub rule_mirror.py; policy-projected public domains."]
    kind_map = {"domain": "DOMAIN", "suffix": "DOMAIN-SUFFIX", "regex": "DOMAIN-REGEX"}
    for rule in sorted(set(rules)):
        lines.append(kind_map[rule.kind] + "," + rule.value)
    return ("\n".join(lines) + "\n").encode("utf-8")


def normalized_diff(previous: Iterable[CanonicalRule], current: Iterable[CanonicalRule]) -> dict[str, int]:
    previous_set = set(previous)
    current_set = set(current)
    removed = previous_set - current_set
    added = current_set - previous_set
    previous_by_value = {rule.value: rule.kind for rule in previous_set}
    current_by_value = {rule.value: rule.kind for rule in current_set}
    type_changed = sum(
        1
        for value in previous_by_value.keys() & current_by_value.keys()
        if previous_by_value[value] != current_by_value[value]
    )
    return {"added": len(added), "removed": len(removed), "type_changed": type_changed}


def gated_normalized_diff(
    previous: Iterable[CanonicalRule],
    current: Iterable[CanonicalRule],
) -> dict[str, object]:
    """Reject unexplained large source changes before replacing last-known-good."""

    previous_rules = list(previous)
    current_rules = list(current)
    diff = normalized_diff(previous_rules, current_rules)
    report: dict[str, object] = {
        **diff,
        "previous_count": len(set(previous_rules)),
        "current_count": len(set(current_rules)),
        "gate": "first-publish" if not previous_rules else "passed",
    }
    if not previous_rules:
        return report

    removed_limit = max(1, len(set(previous_rules)) // 2)
    added_limit = max(5, len(set(previous_rules)) * 2)
    report["removed_limit"] = removed_limit
    report["added_limit"] = added_limit
    if diff["type_changed"]:
        raise SemanticError("normalized rule types changed; manual source review required")
    if diff["removed"] > removed_limit:
        raise SemanticError("normalized rule removal exceeds the automatic promotion limit")
    if diff["added"] > added_limit:
        raise SemanticError("normalized rule expansion exceeds the automatic promotion limit")
    return report
