"""Derive public-safe publication expectations without contacting Sub-Store."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

if __package__:
    from . import registry
else:
    import registry


CONTRACT_SCHEMA_VERSION = 1
CONTRACT_TOP_LEVEL_FIELDS = frozenset(
    {"schema_version", "channel_contracts", "route_bindings"}
)
CHANNEL_CONTRACT_FIELDS = frozenset(
    {"channel", "output_shape", "semantic_profile"}
)
ROUTE_BINDING_FIELDS = frozenset({"role_id", "binding_state"})

CHANNEL_SEMANTICS = {
    "final-mihomo": {
        "output_shape": "mihomo-profile",
        "semantic_profile": "shared-mihomo",
    },
    "shadowrocket-hy2-only": {
        "output_shape": "hy2-yaml",
        "semantic_profile": "hy2",
    },
    "shadowrocket-ordinary-uri": {
        "output_shape": "uri-feed",
        "semantic_profile": "ordinary",
    },
}


class PublicationContractLoadError(ValueError):
    """Raised when the requested publication contract cannot be parsed locally."""


class PublicationContractValidationError(ValueError):
    """Raised when a public-safe publication contract is not satisfied."""

    def __init__(self, errors: list[str]):
        self.errors = tuple(errors)
        super().__init__("publication contract validation failed")


def load_publication_contract(path: str | Path) -> object:
    """Read only the caller-supplied JSON publication contract path."""

    try:
        source = Path(path).read_text(encoding="utf-8")
    except (OSError, UnicodeError) as error:
        raise PublicationContractLoadError("publication contract could not be read") from error

    try:
        return json.loads(source, object_pairs_hook=_reject_duplicate_json_fields)
    except PublicationContractLoadError:
        raise
    except json.JSONDecodeError as error:
        raise PublicationContractLoadError("publication contract is not valid JSON") from error


def validate_publication_contract(contract: object) -> dict[str, int]:
    """Validate a static semantic contract without reading any external state."""

    errors: list[str] = []
    if not isinstance(contract, dict):
        raise PublicationContractValidationError(["contract must be a JSON object"])

    _scan_contract_payload(contract, errors)
    if set(contract) != CONTRACT_TOP_LEVEL_FIELDS:
        errors.append("contract must contain only the documented top-level fields")

    schema_version = contract.get("schema_version")
    if type(schema_version) is not int:
        errors.append("contract.schema_version must be an integer")
    elif schema_version != CONTRACT_SCHEMA_VERSION:
        errors.append("contract.schema_version is not supported")

    channel_contracts = _validate_channel_contracts(contract, errors)
    route_bindings = _validate_route_bindings(contract, errors)

    if errors:
        raise PublicationContractValidationError(errors)

    return {
        "channel_contracts": len(channel_contracts),
        "route_bindings": len(route_bindings),
    }


def publication_plan(registry_document: object) -> dict[str, Any]:
    """Flatten a validated registry plan into deterministic publication expectations."""

    registry_plan = registry.plan_registry(registry_document)
    rows: list[dict[str, Any]] = []

    for host in registry_plan["hosts"]:
        for endpoint in host["endpoints"]:
            for route in endpoint["routes"]:
                for publication in route["publication_eligibility"]:
                    semantics = CHANNEL_SEMANTICS[publication["channel"]]
                    rows.append(
                        {
                            "binding_state": "unbound",
                            "blocker": publication["blocker"],
                            "channel": publication["channel"],
                            "eligible": publication["eligible"],
                            "evidence_status": endpoint["evidence_status"],
                            "output_shape": semantics["output_shape"],
                            "protocol": endpoint["protocol"],
                            "publication_id": publication["publication_id"],
                            "role_id": route["role_id"],
                            "semantic_profile": semantics["semantic_profile"],
                        }
                    )

    rows.sort(key=lambda row: (row["role_id"], row["channel"], row["publication_id"]))
    return {
        "publications": rows,
        "schema_version": CONTRACT_SCHEMA_VERSION,
        "summary": {
            "eligible_publication_count": sum(row["eligible"] for row in rows),
            "ineligible_publication_count": sum(not row["eligible"] for row in rows),
            "promotion_ready_count": sum(
                row["eligible"] and row["binding_state"] != "unbound" for row in rows
            ),
            "publication_count": len(rows),
            "unbound_publication_count": len(rows),
        },
    }


def render_publication_plan_json(registry_document: object) -> str:
    """Return byte-stable publication expectations without writing an artifact."""

    return json.dumps(
        publication_plan(registry_document), ensure_ascii=True, indent=2, sort_keys=True
    ) + "\n"


def publication_diff(registry_document: object, contract: object) -> dict[str, Any]:
    """Compare local publication semantics with an explicit static contract fixture."""

    plan = publication_plan(registry_document)
    validate_publication_contract(contract)
    assert isinstance(contract, dict)

    contract_by_channel = {
        item["channel"]: item
        for item in contract["channel_contracts"]
        if isinstance(item, dict)
    }
    bindings_by_role = {
        item["role_id"]: item["binding_state"]
        for item in contract["route_bindings"]
        if isinstance(item, dict)
    }
    route_ids = _validated_route_ids(registry_document)

    matched: list[dict[str, Any]] = []
    missing: list[dict[str, Any]] = []
    unexpected: list[dict[str, Any]] = []
    mismatch: list[dict[str, Any]] = []
    unbound: list[dict[str, Any]] = []

    for row in plan["publications"]:
        expected = contract_by_channel.get(row["channel"])
        if expected is None or not _semantics_match(row, expected):
            mismatch.append(_mismatch_row(row, expected))
        elif row["role_id"] not in bindings_by_role:
            missing.append(dict(row))
        else:
            matched.append(dict(row))

        if bindings_by_role.get(row["role_id"], "unbound") == "unbound":
            unbound.append(dict(row))

    for role_id in sorted(bindings_by_role):
        if role_id not in route_ids:
            unexpected.append(
                {
                    "binding_state": bindings_by_role[role_id],
                    "role_id": role_id,
                }
            )

    return {
        "matched": matched,
        "mismatch": mismatch,
        "missing": missing,
        "schema_version": CONTRACT_SCHEMA_VERSION,
        "summary": {
            "matched_count": len(matched),
            "mismatch_count": len(mismatch),
            "missing_count": len(missing),
            "promotion_ready_count": sum(
                row["eligible"] and row["binding_state"] != "unbound" for row in matched
            ),
            "unbound_count": len(unbound),
            "unexpected_count": len(unexpected),
        },
        "unbound": unbound,
        "unexpected": unexpected,
    }


def render_publication_diff_json(registry_document: object, contract: object) -> str:
    """Return byte-stable publication semantics diff without writing an artifact."""

    return json.dumps(
        publication_diff(registry_document, contract),
        ensure_ascii=True,
        indent=2,
        sort_keys=True,
    ) + "\n"


def _reject_duplicate_json_fields(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise PublicationContractLoadError("publication contract contains duplicate JSON fields")
        result[key] = value
    return result


def _scan_contract_payload(contract: dict[str, Any], errors: list[str]) -> None:
    """Reuse registry redaction rules while allowing the contract schema version integer."""

    for collection_name in ("channel_contracts", "route_bindings"):
        if collection_name in contract:
            registry._scan_sensitive_material(
                contract[collection_name], f"contract.{collection_name}", errors
            )


def _validate_channel_contracts(
    contract: dict[str, Any], errors: list[str]
) -> dict[str, dict[str, Any]]:
    source = contract.get("channel_contracts")
    if not isinstance(source, list):
        errors.append("contract.channel_contracts must be an array")
        return {}

    by_channel: dict[str, dict[str, Any]] = {}
    channels: list[str] = []
    for index, item in enumerate(source):
        path = f"contract.channel_contracts[{index}]"
        if not isinstance(item, dict):
            errors.append(f"{path} must be an object")
            continue
        if set(item) != CHANNEL_CONTRACT_FIELDS:
            errors.append(f"{path} must contain only the documented fields")
        channel = item.get("channel")
        if not isinstance(channel, str) or channel not in CHANNEL_SEMANTICS:
            errors.append(f"{path}.channel is not an allowed value")
            continue
        channels.append(channel)
        if channel in by_channel:
            errors.append(f"{path}.channel duplicates a channel contract")
            continue
        by_channel[channel] = item
        semantics = CHANNEL_SEMANTICS[channel]
        if (
            item.get("output_shape") != semantics["output_shape"]
            or item.get("semantic_profile") != semantics["semantic_profile"]
        ):
            errors.append(f"{path} does not match canonical channel semantics")

    if channels != sorted(channels):
        errors.append("contract.channel_contracts must be sorted by channel")
    if set(by_channel) != set(CHANNEL_SEMANTICS):
        errors.append("contract.channel_contracts must define every canonical channel")
    return by_channel


def _validate_route_bindings(
    contract: dict[str, Any], errors: list[str]
) -> dict[str, str]:
    source = contract.get("route_bindings")
    if not isinstance(source, list):
        errors.append("contract.route_bindings must be an array")
        return {}

    by_role: dict[str, str] = {}
    role_ids: list[str] = []
    for index, item in enumerate(source):
        path = f"contract.route_bindings[{index}]"
        if not isinstance(item, dict):
            errors.append(f"{path} must be an object")
            continue
        if set(item) != ROUTE_BINDING_FIELDS:
            errors.append(f"{path} must contain only the documented fields")
        role_id = item.get("role_id")
        if not isinstance(role_id, str) or registry.STABLE_ID_RE.fullmatch(role_id) is None:
            errors.append(f"{path}.role_id must be a normalized stable ID")
            continue
        role_ids.append(role_id)
        if role_id in by_role:
            errors.append(f"{path}.role_id duplicates a route binding")
            continue
        binding_state = item.get("binding_state")
        if binding_state != "unbound":
            errors.append(f"{path}.binding_state is not an allowed value")
            continue
        by_role[role_id] = binding_state

    if role_ids != sorted(role_ids):
        errors.append("contract.route_bindings must be sorted by role ID")
    return by_role


def _validated_route_ids(registry_document: object) -> set[str]:
    """Return route IDs only after publication_plan has already validated the registry."""

    assert isinstance(registry_document, dict)
    return {
        route["role_id"]
        for route in registry_document["routes"]
        if isinstance(route, dict)
    }


def _semantics_match(row: dict[str, Any], expected: dict[str, Any]) -> bool:
    return (
        row["output_shape"] == expected["output_shape"]
        and row["semantic_profile"] == expected["semantic_profile"]
    )


def _mismatch_row(
    row: dict[str, Any], expected: dict[str, Any] | None
) -> dict[str, Any]:
    result = dict(row)
    if expected is not None:
        result["contract_output_shape"] = expected["output_shape"]
        result["contract_semantic_profile"] = expected["semantic_profile"]
    return result
