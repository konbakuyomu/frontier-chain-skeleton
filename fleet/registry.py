"""Validate and render the public-safe fleet registry without host access."""

from __future__ import annotations

import json
import re
from collections.abc import Sequence
from pathlib import Path
from typing import Any


SCHEMA_VERSION = 1

HOST_ROLES = frozenset(
    {
        "bare-singbox-data-plane",
        "edge-router",
        "control-plane",
        "business-docker",
        "legacy-rollback",
        "public-proxy-gateway",
    }
)
PROTOCOLS = frozenset({"reality", "hy2", "vmess", "http-socks"})
CAPABILITY_STATES = frozenset(
    {"current", "desired", "blocked", "unsupported", "deferred", "unknown"}
)
EVIDENCE_TIERS = frozenset(
    {"host-readback", "data-plane", "output-shape", "snapshot", "historical", "unknown"}
)
PUBLICATION_CHANNELS = frozenset(
    {"final-mihomo", "shadowrocket-ordinary-uri", "shadowrocket-hy2-only"}
)
LIFECYCLES = frozenset({"documented-snapshot", "active-intent", "retired"})
ROUTE_PURPOSES = frozenset({"balanced", "fast", "compatibility", "region-manual"})

TOP_LEVEL_FIELDS = frozenset(
    {"schema_version", "policies", "hosts", "endpoints", "routes", "publications"}
)
POLICY_FIELDS = frozenset(
    {
        "policy_id",
        "host_role",
        "allowed_protocols",
        "required_protocols",
        "preferred_protocols",
        "compatibility_protocols",
        "allowed_states",
        "allow_client_publications",
    }
)
HOST_FIELDS = frozenset(
    {"host_id", "role", "lifecycle", "region", "policy_id", "display_name"}
)
ENDPOINT_FIELDS = frozenset(
    {
        "endpoint_id",
        "host_id",
        "protocol",
        "desired_state",
        "observed_state",
        "evidence_tier",
        "compatibility_only",
    }
)
ROUTE_FIELDS = frozenset(
    {"role_id", "endpoint_id", "purpose", "region", "display_name"}
)
PUBLICATION_FIELDS = frozenset({"publication_id", "role_id", "channel"})
KNOWN_FIELDS = (
    TOP_LEVEL_FIELDS
    | POLICY_FIELDS
    | HOST_FIELDS
    | ENDPOINT_FIELDS
    | ROUTE_FIELDS
    | PUBLICATION_FIELDS
)

STABLE_ID_RE = re.compile(r"^[a-z][a-z0-9]*(?:-[a-z0-9]+)*$")
SAFE_LABEL_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9 -]*$")
IPV4_RE = re.compile(r"(?<![0-9])(?:[0-9]{1,3}\.){3}[0-9]{1,3}(?![0-9])")
IPV6_RE = re.compile(r"(?i)(?<![0-9a-f])(?:[0-9a-f]{1,4}:){2,}[0-9a-f:]+(?![0-9a-f])")
URI_RE = re.compile(
    r"(?i)(?:^|[^a-z0-9+.-])(?:https?|socks5?|vless|vmess|trojan|hysteria2|hy2)://"
)
HOSTNAME_RE = re.compile(
    r"(?i)(?<![a-z0-9-])(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z]{2,63}(?![a-z0-9-])"
)
HOST_PORT_RE = re.compile(
    r"(?i)(?:\b(?:port|listen|bind)\b\s*[:=]?\s*[1-9][0-9]{0,4}\b|\b[a-z0-9-]+:[1-9][0-9]{0,4}\b)"
)
UUID_RE = re.compile(
    r"(?i)\b[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\b"
)
ABSOLUTE_PATH_RE = re.compile(r"(?:^[A-Za-z]:[\\/]|(?:^|\s)/[^\s]+)")
PEM_RE = re.compile(r"-----BEGIN [A-Z ]+-----")
AUTH_RE = re.compile(r"(?i)\b(?:bearer|basic)\s+[a-z0-9._-]+")
TOKEN_RE = re.compile(r"\b[A-Za-z0-9_]{40,}\b")
EMAIL_RE = re.compile(r"\b[^\s@]+@[^\s@]+\.[^\s@]+\b")

ALLOWED_IDENTIFIER_FIELDS = frozenset(
    {
        "schema_version",
        "policy_id",
        "host_id",
        "endpoint_id",
        "role_id",
        "publication_id",
        "host_role",
    }
)
FORBIDDEN_FIELD_TOKENS = frozenset(
    {
        "address",
        "connection",
        "credential",
        "certificate",
        "cert",
        "domain",
        "endpoint",
        "fqdn",
        "host",
        "hostname",
        "ip",
        "key",
        "password",
        "passwd",
        "path",
        "port",
        "private",
        "secret",
        "server",
        "ssh",
        "token",
        "uri",
        "url",
        "user",
        "username",
    }
)

ROLE_POLICY_CONTRACTS = {
    "bare-singbox-data-plane": {
        "allowed_protocols": frozenset({"hy2", "reality", "vmess"}),
        "required_protocols": frozenset({"reality"}),
        "preferred_protocols": frozenset({"hy2"}),
        "compatibility_protocols": frozenset({"vmess"}),
        "allowed_states": CAPABILITY_STATES,
        "allow_client_publications": True,
    },
    "edge-router": {
        "allowed_protocols": frozenset({"hy2", "reality", "vmess"}),
        "required_protocols": frozenset(),
        "preferred_protocols": frozenset(),
        "compatibility_protocols": frozenset({"vmess"}),
        "allowed_states": CAPABILITY_STATES,
        "allow_client_publications": True,
    },
    "control-plane": {
        "allowed_protocols": frozenset(),
        "required_protocols": frozenset(),
        "preferred_protocols": frozenset(),
        "compatibility_protocols": frozenset(),
        "allowed_states": frozenset({"deferred", "unknown"}),
        "allow_client_publications": False,
    },
    "business-docker": {
        "allowed_protocols": frozenset(),
        "required_protocols": frozenset(),
        "preferred_protocols": frozenset(),
        "compatibility_protocols": frozenset(),
        "allowed_states": frozenset({"deferred", "unknown"}),
        "allow_client_publications": False,
    },
    "legacy-rollback": {
        "allowed_protocols": frozenset({"http-socks", "hy2", "reality", "vmess"}),
        "required_protocols": frozenset(),
        "preferred_protocols": frozenset(),
        "compatibility_protocols": frozenset({"vmess"}),
        "allowed_states": frozenset({"deferred", "unknown"}),
        "allow_client_publications": False,
    },
    "public-proxy-gateway": {
        "allowed_protocols": frozenset({"http-socks"}),
        "required_protocols": frozenset(),
        "preferred_protocols": frozenset(),
        "compatibility_protocols": frozenset(),
        "allowed_states": CAPABILITY_STATES,
        "allow_client_publications": False,
    },
}

CHANNEL_PROTOCOLS = {
    "final-mihomo": frozenset({"hy2", "reality", "vmess"}),
    "shadowrocket-ordinary-uri": frozenset({"http-socks", "reality", "vmess"}),
    "shadowrocket-hy2-only": frozenset({"hy2"}),
}


class RegistryLoadError(ValueError):
    """Raised when the requested registry cannot be parsed locally."""


class RegistryValidationError(ValueError):
    """Raised when the public-safe registry contract is not satisfied."""

    def __init__(self, errors: list[str]):
        self.errors = tuple(errors)
        super().__init__("registry validation failed")


def _reject_duplicate_json_fields(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    """Build a JSON object while rejecting duplicate fields that could hide data."""

    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise RegistryLoadError("registry contains duplicate JSON fields")
        result[key] = value
    return result


def load_registry(path: str | Path) -> object:
    """Read only the caller-supplied JSON registry path."""

    try:
        source = Path(path).read_text(encoding="utf-8")
    except (OSError, UnicodeError) as error:
        raise RegistryLoadError("registry could not be read") from error

    try:
        return json.loads(source, object_pairs_hook=_reject_duplicate_json_fields)
    except RegistryLoadError:
        raise
    except json.JSONDecodeError as error:
        raise RegistryLoadError("registry is not valid JSON") from error


def validate_registry(registry: object) -> dict[str, int]:
    """Validate the registry without changing it or consulting any external state."""

    errors: list[str] = []
    _scan_sensitive_material(registry, "registry", errors)

    if not isinstance(registry, dict):
        errors.append("registry must be a JSON object")
        raise RegistryValidationError(errors)

    _validate_top_level(registry, errors)
    if not isinstance(registry.get("schema_version"), int) or isinstance(
        registry.get("schema_version"), bool
    ):
        errors.append("registry.schema_version must be an integer")
    elif registry["schema_version"] != SCHEMA_VERSION:
        errors.append("registry.schema_version is not supported")

    policies, policy_by_id = _collect_records(
        registry, "policies", "policy_id", POLICY_FIELDS, errors
    )
    hosts, host_by_id = _collect_records(registry, "hosts", "host_id", HOST_FIELDS, errors)
    endpoints, endpoint_by_id = _collect_records(
        registry, "endpoints", "endpoint_id", ENDPOINT_FIELDS, errors
    )
    routes, route_by_id = _collect_records(registry, "routes", "role_id", ROUTE_FIELDS, errors)
    publications, _ = _collect_records(
        registry, "publications", "publication_id", PUBLICATION_FIELDS, errors
    )

    _validate_global_identifier_uniqueness(
        (
            ("policies", policies, "policy_id"),
            ("hosts", hosts, "host_id"),
            ("endpoints", endpoints, "endpoint_id"),
            ("routes", routes, "role_id"),
            ("publications", publications, "publication_id"),
        ),
        errors,
    )

    policy_by_role = _validate_policies(policies, errors)
    host_policy_by_id = _validate_hosts(hosts, policy_by_id, policy_by_role, errors)
    _validate_endpoints(endpoints, host_by_id, host_policy_by_id, errors)
    _validate_bare_singbox_hosts(hosts, endpoints, errors)
    _validate_routes(routes, endpoint_by_id, host_by_id, errors)
    _validate_publications(
        publications,
        route_by_id,
        endpoint_by_id,
        host_by_id,
        host_policy_by_id,
        errors,
    )

    if errors:
        raise RegistryValidationError(errors)

    return {
        "policies": len(policies),
        "hosts": len(hosts),
        "endpoints": len(endpoints),
        "routes": len(routes),
        "publications": len(publications),
    }


def plan_registry(registry: object) -> dict[str, Any]:
    """Validate first, then return a deterministic public-safe desired-state plan."""

    counts = validate_registry(registry)
    assert isinstance(registry, dict)

    hosts = {item["host_id"]: item for _, item in _record_items(registry["hosts"])}
    endpoints = {
        item["endpoint_id"]: item for _, item in _record_items(registry["endpoints"])
    }
    routes_by_endpoint: dict[str, list[dict[str, Any]]] = {}
    for _, route in _record_items(registry["routes"]):
        routes_by_endpoint.setdefault(route["endpoint_id"], []).append(route)

    publications_by_route: dict[str, list[dict[str, Any]]] = {}
    for _, publication in _record_items(registry["publications"]):
        publications_by_route.setdefault(publication["role_id"], []).append(publication)

    host_rows: list[dict[str, Any]] = []
    eligible_publication_count = 0
    stale_evidence_count = 0

    for host_id in sorted(hosts):
        host = hosts[host_id]
        endpoint_rows: list[dict[str, Any]] = []
        host_endpoints = sorted(
            (endpoint for endpoint in endpoints.values() if endpoint["host_id"] == host_id),
            key=lambda endpoint: endpoint["endpoint_id"],
        )
        for endpoint in host_endpoints:
            evidence_status = _evidence_status(endpoint["evidence_tier"])
            if evidence_status.startswith("stale"):
                stale_evidence_count += 1
            blocker = _endpoint_blocker(endpoint)
            route_rows: list[dict[str, Any]] = []
            for route in sorted(
                routes_by_endpoint.get(endpoint["endpoint_id"], []),
                key=lambda item: item["role_id"],
            ):
                publication_rows: list[dict[str, Any]] = []
                for publication in sorted(
                    publications_by_route.get(route["role_id"], []),
                    key=lambda item: item["publication_id"],
                ):
                    eligible = blocker is None and endpoint["observed_state"] == "current"
                    publication_blocker = blocker
                    if eligible:
                        eligible_publication_count += 1
                    publication_rows.append(
                        {
                            "blocker": publication_blocker,
                            "channel": publication["channel"],
                            "eligible": eligible,
                            "publication_id": publication["publication_id"],
                        }
                    )
                route_rows.append(
                    {
                        "display_name": route["display_name"],
                        "publication_eligibility": publication_rows,
                        "purpose": route["purpose"],
                        "region": route["region"],
                        "role_id": route["role_id"],
                    }
                )
            endpoint_rows.append(
                {
                    "blocker": blocker,
                    "capability_gap": _capability_gap(endpoint),
                    "desired_state": endpoint["desired_state"],
                    "endpoint_id": endpoint["endpoint_id"],
                    "evidence_status": evidence_status,
                    "evidence_tier": endpoint["evidence_tier"],
                    "observed_state": endpoint["observed_state"],
                    "protocol": endpoint["protocol"],
                    "routes": route_rows,
                }
            )
        host_rows.append(
            {
                "display_name": host["display_name"],
                "endpoints": endpoint_rows,
                "host_id": host_id,
                "lifecycle": host["lifecycle"],
                "region": host["region"],
                "role": host["role"],
            }
        )

    return {
        "hosts": host_rows,
        "schema_version": SCHEMA_VERSION,
        "summary": {
            "eligible_publication_count": eligible_publication_count,
            "endpoint_count": counts["endpoints"],
            "host_count": counts["hosts"],
            "ineligible_publication_count": counts["publications"] - eligible_publication_count,
            "publication_count": counts["publications"],
            "route_count": counts["routes"],
            "stale_evidence_count": stale_evidence_count,
        },
    }


def render_plan_json(registry: object) -> str:
    """Return byte-stable JSON without writing it anywhere."""

    return json.dumps(plan_registry(registry), ensure_ascii=True, indent=2, sort_keys=True) + "\n"


def status_registry(registry: object, host_ids: Sequence[str]) -> dict[str, Any]:
    """Return the local plan row for exactly one explicit public host ID."""

    plan = plan_registry(registry)
    host_id = _select_host_id(host_ids)
    for host in plan["hosts"]:
        if host["host_id"] == host_id:
            return {
                "evidence_plane": "local",
                "host": host,
                "schema_version": SCHEMA_VERSION,
                "summary": _host_status_summary(host),
            }
    raise RegistryValidationError(["selected host is not registered"])


def render_status_json(registry: object, host_ids: Sequence[str]) -> str:
    """Return byte-stable local status JSON for one selected host."""

    return json.dumps(
        status_registry(registry, host_ids), ensure_ascii=True, indent=2, sort_keys=True
    ) + "\n"


def inventory_plan_registry(registry: object, host_ids: Sequence[str]) -> dict[str, Any]:
    """Describe the next remote inventory gate without resolving or contacting a host."""

    status = status_registry(registry, host_ids)
    host = status["host"]
    request: dict[str, Any] = {
        "host_id": host["host_id"],
        "mode": "local-plan-only",
        "private_mapping_resolution": "not-performed",
        "remote_contact_performed": False,
    }

    if host["lifecycle"] == "retired":
        request.update(
            {
                "blocker": "host-lifecycle-retired",
                "inventory_allowed": False,
                "promotion_allowed": False,
                "required_next_gate": "retired-host-remains-blocked",
            }
        )
    else:
        request.update(
            {
                "promotion_allowed": False,
                "required_next_gate": "approved-explicit-host-remote-adapter",
                "required_proof_categories": [
                    "explicit-host-selection",
                    "private-adapter-outside-public-fleet-package",
                    "DIRECT-key-only-tmux-provider-recovery",
                    "redacted-host-readback",
                ],
            }
        )

    return {"inventory_request": request, "status": status}


def render_inventory_plan_json(registry: object, host_ids: Sequence[str]) -> str:
    """Return byte-stable local inventory-plan JSON for one selected host."""

    return json.dumps(
        inventory_plan_registry(registry, host_ids),
        ensure_ascii=True,
        indent=2,
        sort_keys=True,
    ) + "\n"


def _validate_top_level(registry: dict[str, Any], errors: list[str]) -> None:
    if set(registry) != TOP_LEVEL_FIELDS:
        errors.append("registry must contain only the documented top-level fields")


def _collect_records(
    registry: dict[str, Any],
    collection_name: str,
    identifier_field: str,
    required_fields: frozenset[str],
    errors: list[str],
) -> tuple[list[tuple[int, dict[str, Any]]], dict[str, dict[str, Any]]]:
    source = registry.get(collection_name)
    if not isinstance(source, list):
        errors.append(f"registry.{collection_name} must be an array")
        return [], {}

    records: list[tuple[int, dict[str, Any]]] = []
    by_identifier: dict[str, dict[str, Any]] = {}
    identifiers: list[str] = []
    for index, record in enumerate(source):
        path = f"registry.{collection_name}[{index}]"
        if not isinstance(record, dict):
            errors.append(f"{path} must be an object")
            continue
        records.append((index, record))
        if set(record) != required_fields:
            errors.append(f"{path} must contain only the documented fields")
        identifier = _validate_identifier(record, identifier_field, path, errors)
        if identifier is None:
            continue
        identifiers.append(identifier)
        if identifier in by_identifier:
            errors.append(f"{path}.{identifier_field} duplicates a stable ID")
            continue
        by_identifier[identifier] = record

    if identifiers != sorted(identifiers):
        errors.append(f"registry.{collection_name} must be sorted by stable ID")
    return records, by_identifier


def _validate_global_identifier_uniqueness(
    collections: tuple[tuple[str, list[tuple[int, dict[str, Any]]], str], ...],
    errors: list[str],
) -> None:
    seen: set[str] = set()
    for collection_name, records, identifier_field in collections:
        for index, record in records:
            identifier = record.get(identifier_field)
            if not isinstance(identifier, str) or not STABLE_ID_RE.fullmatch(identifier):
                continue
            if identifier in seen:
                errors.append(
                    f"registry.{collection_name}[{index}].{identifier_field} duplicates another stable ID"
                )
            seen.add(identifier)


def _validate_policies(
    policies: list[tuple[int, dict[str, Any]]], errors: list[str]
) -> dict[str, dict[str, Any]]:
    policies_by_role: dict[str, dict[str, Any]] = {}
    for index, policy in policies:
        path = f"registry.policies[{index}]"
        host_role = _validate_enum(policy, "host_role", HOST_ROLES, path, errors)
        protocol_lists = {
            field: _validate_symbol_list(policy, field, PROTOCOLS, path, errors)
            for field in (
                "allowed_protocols",
                "required_protocols",
                "preferred_protocols",
                "compatibility_protocols",
            )
        }
        allowed_states = _validate_symbol_list(
            policy, "allowed_states", CAPABILITY_STATES, path, errors
        )
        allow_client_publications = _validate_boolean(
            policy, "allow_client_publications", path, errors
        )
        if host_role is None:
            continue
        if host_role in policies_by_role:
            errors.append(f"{path}.host_role duplicates a role policy")
            continue
        policies_by_role[host_role] = policy
        contract = ROLE_POLICY_CONTRACTS[host_role]
        if (
            any(
                values is None or frozenset(values) != contract[field]
                for field, values in protocol_lists.items()
            )
            or allowed_states is None
            or frozenset(allowed_states) != contract["allowed_states"]
            or allow_client_publications is None
            or allow_client_publications != contract["allow_client_publications"]
        ):
            errors.append(f"{path} does not match the required role policy contract")

    if set(policies_by_role) != HOST_ROLES:
        errors.append("registry must define exactly one policy for every supported host role")
    return policies_by_role


def _validate_hosts(
    hosts: list[tuple[int, dict[str, Any]]],
    policies_by_id: dict[str, dict[str, Any]],
    policies_by_role: dict[str, dict[str, Any]],
    errors: list[str],
) -> dict[str, dict[str, Any]]:
    host_policy_by_id: dict[str, dict[str, Any]] = {}
    for index, host in hosts:
        path = f"registry.hosts[{index}]"
        host_id = _validate_identifier(host, "host_id", path, errors)
        role = _validate_enum(host, "role", HOST_ROLES, path, errors)
        _validate_enum(host, "lifecycle", LIFECYCLES, path, errors)
        _validate_identifier(host, "region", path, errors)
        policy_id = _validate_identifier(host, "policy_id", path, errors)
        _validate_label(host, "display_name", path, errors)
        if host_id is None or role is None or policy_id is None:
            continue
        policy = policies_by_id.get(policy_id)
        if policy is None:
            errors.append(f"{path}.policy_id has no matching policy")
            continue
        if policy.get("host_role") != role:
            errors.append(f"{path}.policy_id does not apply to the host role")
            continue
        if policies_by_role.get(role) is not policy:
            errors.append(f"{path}.policy_id does not select the role policy")
            continue
        host_policy_by_id[host_id] = policy
    return host_policy_by_id


def _validate_endpoints(
    endpoints: list[tuple[int, dict[str, Any]]],
    hosts_by_id: dict[str, dict[str, Any]],
    host_policy_by_id: dict[str, dict[str, Any]],
    errors: list[str],
) -> None:
    for index, endpoint in endpoints:
        path = f"registry.endpoints[{index}]"
        _validate_identifier(endpoint, "endpoint_id", path, errors)
        host_id = _validate_identifier(endpoint, "host_id", path, errors)
        protocol = _validate_enum(endpoint, "protocol", PROTOCOLS, path, errors)
        desired_state = _validate_enum(
            endpoint, "desired_state", CAPABILITY_STATES, path, errors
        )
        observed_state = _validate_enum(
            endpoint, "observed_state", CAPABILITY_STATES, path, errors
        )
        evidence_tier = _validate_enum(endpoint, "evidence_tier", EVIDENCE_TIERS, path, errors)
        compatibility_only = _validate_boolean(endpoint, "compatibility_only", path, errors)

        if host_id is None or protocol is None:
            continue
        host = hosts_by_id.get(host_id)
        if host is None:
            errors.append(f"{path}.host_id has no matching host")
            continue
        policy = host_policy_by_id.get(host_id)
        if policy is None:
            continue
        allowed_protocols = policy.get("allowed_protocols", [])
        if protocol not in allowed_protocols:
            errors.append(f"{path}.protocol is not allowed by the host policy")
        allowed_states = policy.get("allowed_states", [])
        if desired_state is not None and desired_state not in allowed_states:
            errors.append(f"{path}.desired_state is not allowed by the host policy")
        if observed_state is not None and observed_state not in allowed_states:
            errors.append(f"{path}.observed_state is not allowed by the host policy")
        if (
            (desired_state == "current" or observed_state == "current")
            and evidence_tier in {"historical", "unknown"}
        ):
            errors.append(f"{path} records current capability with insufficient evidence")
        if compatibility_only is True and protocol != "vmess":
            errors.append(f"{path}.compatibility_only is valid only for VMess")
        if protocol == "vmess" and compatibility_only is not True:
            errors.append(f"{path}.compatibility_only must mark VMess as compatibility-only")


def _validate_bare_singbox_hosts(
    hosts: list[tuple[int, dict[str, Any]]],
    endpoints: list[tuple[int, dict[str, Any]]],
    errors: list[str],
) -> None:
    endpoints_by_host: dict[str, list[dict[str, Any]]] = {}
    for _, endpoint in endpoints:
        host_id = endpoint.get("host_id")
        if isinstance(host_id, str):
            endpoints_by_host.setdefault(host_id, []).append(endpoint)

    for index, host in hosts:
        if host.get("role") != "bare-singbox-data-plane":
            continue
        path = f"registry.hosts[{index}]"
        host_id = host.get("host_id")
        if not isinstance(host_id, str):
            continue
        host_endpoints = endpoints_by_host.get(host_id, [])
        if not any(endpoint.get("protocol") == "reality" for endpoint in host_endpoints):
            errors.append(f"{path} requires a Reality endpoint")
        hy2_endpoints = [
            endpoint for endpoint in host_endpoints if endpoint.get("protocol") == "hy2"
        ]
        if not hy2_endpoints:
            errors.append(f"{path} must declare HY2 capability")
        elif all(endpoint.get("desired_state") == "unknown" for endpoint in hy2_endpoints):
            errors.append(f"{path} must give HY2 a declared desired capability state")


def _validate_routes(
    routes: list[tuple[int, dict[str, Any]]],
    endpoints_by_id: dict[str, dict[str, Any]],
    hosts_by_id: dict[str, dict[str, Any]],
    errors: list[str],
) -> None:
    for index, route in routes:
        path = f"registry.routes[{index}]"
        _validate_identifier(route, "role_id", path, errors)
        endpoint_id = _validate_identifier(route, "endpoint_id", path, errors)
        _validate_enum(route, "purpose", ROUTE_PURPOSES, path, errors)
        region = _validate_identifier(route, "region", path, errors)
        _validate_label(route, "display_name", path, errors)
        if endpoint_id is None:
            continue
        endpoint = endpoints_by_id.get(endpoint_id)
        if endpoint is None:
            errors.append(f"{path}.endpoint_id has no matching endpoint")
            continue
        host = hosts_by_id.get(endpoint.get("host_id"))
        if host is not None and region is not None and region != host.get("region"):
            errors.append(f"{path}.region must match the target host region")


def _validate_publications(
    publications: list[tuple[int, dict[str, Any]]],
    routes_by_id: dict[str, dict[str, Any]],
    endpoints_by_id: dict[str, dict[str, Any]],
    hosts_by_id: dict[str, dict[str, Any]],
    host_policy_by_id: dict[str, dict[str, Any]],
    errors: list[str],
) -> None:
    seen_route_channels: set[tuple[str, str]] = set()
    for index, publication in publications:
        path = f"registry.publications[{index}]"
        _validate_identifier(publication, "publication_id", path, errors)
        role_id = _validate_identifier(publication, "role_id", path, errors)
        channel = _validate_enum(publication, "channel", PUBLICATION_CHANNELS, path, errors)
        if role_id is None or channel is None:
            continue
        route_channel = (role_id, channel)
        if route_channel in seen_route_channels:
            errors.append(f"{path} duplicates a publication channel for its route")
        seen_route_channels.add(route_channel)

        route = routes_by_id.get(role_id)
        if route is None:
            errors.append(f"{path}.role_id has no matching route")
            continue
        endpoint = endpoints_by_id.get(route.get("endpoint_id"))
        if endpoint is None:
            continue
        host_id = endpoint.get("host_id")
        host = hosts_by_id.get(host_id)
        policy = host_policy_by_id.get(host_id)
        if host is None or policy is None:
            continue
        if policy.get("allow_client_publications") is not True:
            errors.append(f"{path} cannot expose this host role to client publications")
        protocol = endpoint.get("protocol")
        if protocol not in CHANNEL_PROTOCOLS[channel]:
            errors.append(f"{path}.channel is not eligible for the route protocol")


def _validate_identifier(
    record: dict[str, Any], field: str, path: str, errors: list[str]
) -> str | None:
    value = record.get(field)
    if not isinstance(value, str) or not STABLE_ID_RE.fullmatch(value):
        errors.append(f"{path}.{field} must be a normalized stable ID")
        return None
    return value


def _validate_enum(
    record: dict[str, Any], field: str, allowed: frozenset[str], path: str, errors: list[str]
) -> str | None:
    value = record.get(field)
    if not isinstance(value, str) or value not in allowed:
        errors.append(f"{path}.{field} is not an allowed value")
        return None
    return value


def _validate_label(record: dict[str, Any], field: str, path: str, errors: list[str]) -> None:
    value = record.get(field)
    if (
        not isinstance(value, str)
        or not SAFE_LABEL_RE.fullmatch(value)
        or len(value) > 80
    ):
        errors.append(f"{path}.{field} must be a short public-safe label")


def _validate_boolean(
    record: dict[str, Any], field: str, path: str, errors: list[str]
) -> bool | None:
    value = record.get(field)
    if not isinstance(value, bool):
        errors.append(f"{path}.{field} must be a boolean")
        return None
    return value


def _validate_symbol_list(
    record: dict[str, Any],
    field: str,
    allowed: frozenset[str],
    path: str,
    errors: list[str],
) -> tuple[str, ...] | None:
    value = record.get(field)
    if not isinstance(value, list):
        errors.append(f"{path}.{field} must be an array")
        return None
    items: list[str] = []
    for item in value:
        if not isinstance(item, str) or item not in allowed:
            errors.append(f"{path}.{field} contains an unsupported value")
            continue
        items.append(item)
    if len(items) != len(set(items)):
        errors.append(f"{path}.{field} must not contain duplicates")
    return tuple(items)


def _record_items(source: object) -> list[tuple[int, dict[str, Any]]]:
    if not isinstance(source, list):
        return []
    return [(index, item) for index, item in enumerate(source) if isinstance(item, dict)]


def _select_host_id(host_ids: Sequence[str]) -> str:
    if isinstance(host_ids, str) or not isinstance(host_ids, Sequence) or len(host_ids) != 1:
        raise RegistryValidationError(["selected host must be exactly one normalized stable ID"])

    host_id = host_ids[0]
    if not isinstance(host_id, str) or STABLE_ID_RE.fullmatch(host_id) is None:
        raise RegistryValidationError(["selected host must be exactly one normalized stable ID"])
    return host_id


def _host_status_summary(host: dict[str, Any]) -> dict[str, int]:
    endpoints = host["endpoints"]
    publication_rows = [
        publication
        for endpoint in endpoints
        for route in endpoint["routes"]
        for publication in route["publication_eligibility"]
    ]
    return {
        "eligible_publication_count": sum(
            publication["eligible"] for publication in publication_rows
        ),
        "endpoint_count": len(endpoints),
        "ineligible_publication_count": sum(
            not publication["eligible"] for publication in publication_rows
        ),
        "publication_count": len(publication_rows),
        "stale_evidence_count": sum(
            endpoint["evidence_status"].startswith("stale") for endpoint in endpoints
        ),
    }


def _scan_sensitive_material(value: object, path: str, errors: list[str]) -> None:
    if isinstance(value, dict):
        for key, item in value.items():
            if not isinstance(key, str):
                errors.append(f"{path} contains a non-string field name")
                continue
            if _is_forbidden_field_name(key):
                errors.append(f"{path} contains a forbidden field")
            child_path = f"{path}.{key}" if key in KNOWN_FIELDS else f"{path}.field"
            _scan_sensitive_material(item, child_path, errors)
        return
    if isinstance(value, list):
        for index, item in enumerate(value):
            _scan_sensitive_material(item, f"{path}[{index}]", errors)
        return
    if isinstance(value, str):
        if _looks_sensitive(value):
            errors.append(f"{path} contains connection or secret material")
        return
    if type(value) in {int, float} and path != "registry.schema_version":
        errors.append(f"{path} contains a numeric value that is not allowed in the public registry")


def _is_forbidden_field_name(name: str) -> bool:
    normalized = name.lower()
    if normalized in ALLOWED_IDENTIFIER_FIELDS:
        return False
    if normalized in FORBIDDEN_FIELD_TOKENS:
        return True
    return any(token in FORBIDDEN_FIELD_TOKENS for token in normalized.split("_"))


def _looks_sensitive(value: str) -> bool:
    return any(
        pattern.search(value) is not None
        for pattern in (
            IPV4_RE,
            IPV6_RE,
            URI_RE,
            HOSTNAME_RE,
            HOST_PORT_RE,
            UUID_RE,
            ABSOLUTE_PATH_RE,
            PEM_RE,
            AUTH_RE,
            TOKEN_RE,
            EMAIL_RE,
        )
    )


def _capability_gap(endpoint: dict[str, Any]) -> str:
    desired_state = endpoint["desired_state"]
    observed_state = endpoint["observed_state"]
    if desired_state == observed_state:
        return "none"
    return f"desired-{desired_state}-observed-{observed_state}"


def _evidence_status(evidence_tier: str) -> str:
    if evidence_tier == "snapshot":
        return "stale-snapshot"
    if evidence_tier == "historical":
        return "stale-historical"
    if evidence_tier == "unknown":
        return "unknown"
    return "observed"


def _endpoint_blocker(endpoint: dict[str, Any]) -> str | None:
    observed_state = endpoint["observed_state"]
    if observed_state != "current":
        return f"observed-{observed_state}"
    evidence_status = _evidence_status(endpoint["evidence_tier"])
    if evidence_status != "observed":
        return evidence_status
    if endpoint["desired_state"] != "current":
        return "capability-gap"
    return None
