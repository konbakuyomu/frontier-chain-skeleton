from __future__ import annotations

import ast
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


sys.dont_write_bytecode = True
SOURCE_ROOT = Path(__file__).resolve().parents[1]
if str(SOURCE_ROOT) not in sys.path:
    sys.path.insert(0, str(SOURCE_ROOT))

import registry


class RegistryTests(unittest.TestCase):
    def valid_registry(self) -> dict[str, object]:
        return json.loads((SOURCE_ROOT / "registry.json").read_text(encoding="utf-8"))

    def assert_invalid(self, data: dict[str, object]) -> registry.RegistryValidationError:
        with self.assertRaises(registry.RegistryValidationError) as captured:
            registry.validate_registry(data)
        return captured.exception

    def test_documented_registry_is_valid(self) -> None:
        data = self.valid_registry()
        self.assertEqual(
            registry.validate_registry(data),
            {"policies": 6, "hosts": 6, "endpoints": 5, "routes": 4, "publications": 6},
        )

    def test_duplicate_stable_id_is_rejected(self) -> None:
        data = self.valid_registry()
        duplicate = dict(data["endpoints"][0])
        data["endpoints"].append(duplicate)
        data["endpoints"].sort(key=lambda item: item["endpoint_id"])

        error = self.assert_invalid(data)
        self.assertTrue(any("duplicates a stable ID" in item for item in error.errors))

    def test_dangling_endpoint_reference_is_rejected(self) -> None:
        data = self.valid_registry()
        data["routes"][0]["endpoint_id"] = "endpoint-missing"

        error = self.assert_invalid(data)
        self.assertTrue(any("has no matching endpoint" in item for item in error.errors))

    def test_sensitive_field_name_is_rejected(self) -> None:
        data = self.valid_registry()
        data["hosts"][0]["host_name"] = "opaque"

        error = self.assert_invalid(data)
        self.assertTrue(any("forbidden field" in item for item in error.errors))

    def test_endpoint_like_value_is_rejected_without_echoing_it(self) -> None:
        data = self.valid_registry()
        endpoint_like_value = ".".join(("198", "51", "100", "42"))
        data["hosts"][0]["display_name"] = f"opaque {endpoint_like_value}"

        error = self.assert_invalid(data)
        self.assertTrue(any("connection or secret material" in item for item in error.errors))
        self.assertNotIn(endpoint_like_value, str(error))

    def test_ipv6_like_value_is_rejected_without_echoing_it(self) -> None:
        data = self.valid_registry()
        endpoint_like_value = ":".join(
            ("2001", "0db8", "0000", "0000", "0000", "0000", "0000", "0001")
        )
        data["hosts"][0]["display_name"] = f"opaque {endpoint_like_value}"

        error = self.assert_invalid(data)
        self.assertTrue(any("connection or secret material" in item for item in error.errors))
        self.assertNotIn(endpoint_like_value, str(error))

    def test_raw_proxy_uri_shape_is_rejected_without_echoing_it(self) -> None:
        data = self.valid_registry()
        uri_like_value = "".join(("vm", "ess", "://", "opaque"))
        data["routes"][0]["display_name"] = uri_like_value

        error = self.assert_invalid(data)
        self.assertTrue(any("connection or secret material" in item for item in error.errors))
        self.assertNotIn(uri_like_value, str(error))

    def test_host_policy_must_match_its_role(self) -> None:
        data = self.valid_registry()
        data["hosts"][0]["policy_id"] = "policy-edge-router"

        error = self.assert_invalid(data)
        self.assertTrue(any("does not apply to the host role" in item for item in error.errors))

    def test_current_state_requires_non_stale_evidence(self) -> None:
        for evidence_tier in ("unknown", "historical"):
            with self.subTest(evidence_tier=evidence_tier):
                data = self.valid_registry()
                data["endpoints"][1]["evidence_tier"] = evidence_tier

                error = self.assert_invalid(data)
                self.assertTrue(
                    any("insufficient evidence" in item for item in error.errors)
                )

    def test_hy2_cannot_use_the_ordinary_uri_channel(self) -> None:
        data = self.valid_registry()
        data["publications"][1]["channel"] = "shadowrocket-ordinary-uri"

        error = self.assert_invalid(data)
        self.assertTrue(any("not eligible for the route protocol" in item for item in error.errors))

    def test_bare_singbox_host_requires_reality(self) -> None:
        data = self.valid_registry()
        data["endpoints"] = [
            item
            for item in data["endpoints"]
            if item["endpoint_id"] != "endpoint-bare-data-reality"
        ]

        error = self.assert_invalid(data)
        self.assertTrue(any("requires a Reality endpoint" in item for item in error.errors))

    def test_bare_singbox_vmess_requires_compatibility_marker(self) -> None:
        data = self.valid_registry()
        data["endpoints"].append(
            {
                "endpoint_id": "endpoint-bare-data-vmess",
                "host_id": "host-bare-data-01",
                "protocol": "vmess",
                "desired_state": "deferred",
                "observed_state": "deferred",
                "evidence_tier": "snapshot",
                "compatibility_only": False,
            }
        )
        data["endpoints"].sort(key=lambda item: item["endpoint_id"])

        error = self.assert_invalid(data)
        self.assertTrue(
            any("must mark VMess as compatibility-only" in item for item in error.errors)
        )

    def test_edge_vmess_requires_compatibility_marker(self) -> None:
        data = self.valid_registry()
        for endpoint in data["endpoints"]:
            if endpoint["endpoint_id"] == "endpoint-edge-vmess":
                endpoint["compatibility_only"] = False

        error = self.assert_invalid(data)
        self.assertTrue(
            any("must mark VMess as compatibility-only" in item for item in error.errors)
        )

    def test_bare_singbox_hy2_requires_a_declared_desired_state(self) -> None:
        data = self.valid_registry()
        for endpoint in data["endpoints"]:
            if endpoint["endpoint_id"] == "endpoint-bare-data-hy2":
                endpoint["desired_state"] = "unknown"
                endpoint["observed_state"] = "blocked"

        error = self.assert_invalid(data)
        self.assertTrue(
            any("declared desired capability state" in item for item in error.errors)
        )

    def test_non_data_plane_role_cannot_enter_client_publications(self) -> None:
        data = self.valid_registry()
        data["publications"].append(
            {
                "publication_id": "publication-public-gateway-final-mihomo",
                "role_id": "route-public-gateway-http-socks",
                "channel": "final-mihomo",
            }
        )
        data["publications"].sort(key=lambda item: item["publication_id"])

        error = self.assert_invalid(data)
        self.assertTrue(
            any("cannot expose this host role" in item for item in error.errors)
        )

    def test_duplicate_publication_channel_is_rejected(self) -> None:
        data = self.valid_registry()
        data["publications"].append(
            {
                "publication_id": "publication-bare-data-reality-secondary",
                "role_id": "route-bare-data-reality",
                "channel": "final-mihomo",
            }
        )
        data["publications"].sort(key=lambda item: item["publication_id"])

        error = self.assert_invalid(data)
        self.assertTrue(
            any("duplicates a publication channel" in item for item in error.errors)
        )

    def test_plan_is_deterministic_sorted_and_preserves_snapshot_staleness(self) -> None:
        data = self.valid_registry()
        before = json.dumps(data, sort_keys=True)

        first = registry.plan_registry(data)
        second = registry.plan_registry(data)

        self.assertEqual(first, second)
        self.assertEqual(before, json.dumps(data, sort_keys=True))
        host_ids = [item["host_id"] for item in first["hosts"]]
        self.assertEqual(host_ids, sorted(host_ids))
        evidence_statuses = [
            endpoint["evidence_status"]
            for host in first["hosts"]
            for endpoint in host["endpoints"]
        ]
        self.assertIn("stale-snapshot", evidence_statuses)
        self.assertEqual(first["summary"]["eligible_publication_count"], 0)

    def test_status_selects_one_host_and_preserves_stale_publication_blockers(self) -> None:
        data = self.valid_registry()
        before = json.dumps(data, sort_keys=True)

        status = registry.status_registry(data, ["host-bare-data-01"])

        self.assertEqual(status["evidence_plane"], "local")
        self.assertEqual(status["host"]["host_id"], "host-bare-data-01")
        self.assertNotIn("host-edge-01", json.dumps(status, sort_keys=True))
        self.assertEqual(status["summary"], {
            "eligible_publication_count": 0,
            "endpoint_count": 2,
            "ineligible_publication_count": 4,
            "publication_count": 4,
            "stale_evidence_count": 2,
        })
        self.assertTrue(
            all(
                endpoint["evidence_status"] == "stale-snapshot"
                for endpoint in status["host"]["endpoints"]
            )
        )
        publication_rows = [
            publication
            for endpoint in status["host"]["endpoints"]
            for route in endpoint["routes"]
            for publication in route["publication_eligibility"]
        ]
        self.assertTrue(publication_rows)
        self.assertTrue(all(not publication["eligible"] for publication in publication_rows))
        self.assertEqual(before, json.dumps(data, sort_keys=True))

    def test_status_rejects_invalid_or_nonunique_host_selection_without_echoing_it(self) -> None:
        data = self.valid_registry()
        rejected_selections = (
            [],
            ["Host-bare-data-01"],
            ["host-missing-01"],
            ["host-bare-data-01", "host-bare-data-01"],
        )

        for host_ids in rejected_selections:
            with self.subTest(host_ids=host_ids):
                with self.assertRaises(registry.RegistryValidationError) as captured:
                    registry.status_registry(data, host_ids)
                self.assertNotIn("Host-bare-data-01", str(captured.exception))
                self.assertNotIn("host-missing-01", str(captured.exception))

    def test_inventory_plan_is_local_only_and_lists_the_remote_adapter_gate(self) -> None:
        data = self.valid_registry()

        plan = registry.inventory_plan_registry(data, ["host-bare-data-01"])

        self.assertEqual(plan["status"]["evidence_plane"], "local")
        self.assertEqual(plan["inventory_request"], {
            "host_id": "host-bare-data-01",
            "mode": "local-plan-only",
            "private_mapping_resolution": "not-performed",
            "promotion_allowed": False,
            "remote_contact_performed": False,
            "required_next_gate": "approved-explicit-host-remote-adapter",
            "required_proof_categories": [
                "explicit-host-selection",
                "private-adapter-outside-public-fleet-package",
                "DIRECT-key-only-tmux-provider-recovery",
                "redacted-host-readback",
            ],
        })

    def test_inventory_plan_fails_closed_for_a_retired_host(self) -> None:
        data = self.valid_registry()

        plan = registry.inventory_plan_registry(data, ["host-legacy-01"])

        self.assertEqual(plan["status"]["host"]["lifecycle"], "retired")
        self.assertEqual(
            plan["status"]["host"]["endpoints"][0]["evidence_status"],
            "stale-historical",
        )
        self.assertEqual(plan["inventory_request"], {
            "blocker": "host-lifecycle-retired",
            "host_id": "host-legacy-01",
            "inventory_allowed": False,
            "mode": "local-plan-only",
            "private_mapping_resolution": "not-performed",
            "promotion_allowed": False,
            "remote_contact_performed": False,
            "required_next_gate": "retired-host-remains-blocked",
        })

    def test_host_scoped_json_is_deterministic(self) -> None:
        data = self.valid_registry()

        first_status = registry.render_status_json(data, ["host-bare-data-01"])
        second_status = registry.render_status_json(data, ["host-bare-data-01"])
        first_inventory_plan = registry.render_inventory_plan_json(data, ["host-bare-data-01"])
        second_inventory_plan = registry.render_inventory_plan_json(data, ["host-bare-data-01"])

        self.assertEqual(first_status, second_status)
        self.assertEqual(first_inventory_plan, second_inventory_plan)
        self.assertEqual(json.loads(first_inventory_plan)["status"], json.loads(first_status))

    def test_cli_is_local_only_and_does_not_create_files(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            registry_path = Path(temp_dir) / "registry.json"
            registry_path.write_text(
                json.dumps(self.valid_registry()), encoding="utf-8"
            )
            before_bytes = registry_path.read_bytes()
            before_entries = sorted(item.name for item in Path(temp_dir).iterdir())
            environment = dict(os.environ, PYTHONDONTWRITEBYTECODE="1")
            command = [
                sys.executable,
                str(SOURCE_ROOT / "fleetctl.py"),
                "validate",
                "--registry",
                str(registry_path),
            ]
            validate_result = subprocess.run(
                command,
                cwd=SOURCE_ROOT,
                capture_output=True,
                check=False,
                env=environment,
                text=True,
            )
            self.assertEqual(validate_result.returncode, 0, validate_result.stderr)
            self.assertEqual(
                validate_result.stdout,
                "valid: policies=6 hosts=6 endpoints=5 routes=4 publications=6\n",
            )

            plan_command = command[:2] + ["plan"] + command[3:]
            first_plan = subprocess.run(
                plan_command,
                cwd=SOURCE_ROOT,
                capture_output=True,
                check=False,
                env=environment,
                text=True,
            )
            second_plan = subprocess.run(
                plan_command,
                cwd=SOURCE_ROOT,
                capture_output=True,
                check=False,
                env=environment,
                text=True,
            )
            self.assertEqual(first_plan.returncode, 0, first_plan.stderr)
            self.assertEqual(first_plan.stdout, second_plan.stdout)
            self.assertEqual(json.loads(first_plan.stdout)["summary"]["host_count"], 6)

            status_command = command[:2] + ["status"] + command[3:] + [
                "--host",
                "host-bare-data-01",
            ]
            first_status = subprocess.run(
                status_command,
                cwd=SOURCE_ROOT,
                capture_output=True,
                check=False,
                env=environment,
                text=True,
            )
            second_status = subprocess.run(
                status_command,
                cwd=SOURCE_ROOT,
                capture_output=True,
                check=False,
                env=environment,
                text=True,
            )
            self.assertEqual(first_status.returncode, 0, first_status.stderr)
            self.assertEqual(first_status.stdout, second_status.stdout)
            self.assertEqual(json.loads(first_status.stdout)["evidence_plane"], "local")

            inventory_plan_command = command[:2] + ["inventory-plan"] + command[3:] + [
                "--host",
                "host-bare-data-01",
            ]
            first_inventory_plan = subprocess.run(
                inventory_plan_command,
                cwd=SOURCE_ROOT,
                capture_output=True,
                check=False,
                env=environment,
                text=True,
            )
            second_inventory_plan = subprocess.run(
                inventory_plan_command,
                cwd=SOURCE_ROOT,
                capture_output=True,
                check=False,
                env=environment,
                text=True,
            )
            self.assertEqual(first_inventory_plan.returncode, 0, first_inventory_plan.stderr)
            self.assertEqual(first_inventory_plan.stdout, second_inventory_plan.stdout)
            self.assertFalse(
                json.loads(first_inventory_plan.stdout)["inventory_request"][
                    "remote_contact_performed"
                ]
            )
            self.assertEqual(registry_path.read_bytes(), before_bytes)
            self.assertEqual(
                sorted(item.name for item in Path(temp_dir).iterdir()), before_entries
            )

    def test_cli_redacts_invalid_or_duplicate_host_selection(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            registry_path = Path(temp_dir) / "registry.json"
            registry_path.write_text(json.dumps(self.valid_registry()), encoding="utf-8")
            unknown_host = "host-missing-01"
            result = subprocess.run(
                [
                    sys.executable,
                    str(SOURCE_ROOT / "fleetctl.py"),
                    "status",
                    "--registry",
                    str(registry_path),
                    "--host",
                    unknown_host,
                ],
                cwd=SOURCE_ROOT,
                capture_output=True,
                check=False,
                env=dict(os.environ, PYTHONDONTWRITEBYTECODE="1"),
                text=True,
            )

            self.assertEqual(result.returncode, 2)
            self.assertEqual(
                result.stderr, "invalid registry: validation failed (1 issue(s))\n"
            )
            self.assertNotIn(unknown_host, result.stdout)
            self.assertNotIn(unknown_host, result.stderr)

            duplicate = subprocess.run(
                [
                    sys.executable,
                    str(SOURCE_ROOT / "fleetctl.py"),
                    "inventory-plan",
                    "--registry",
                    str(registry_path),
                    "--host",
                    "host-bare-data-01",
                    "--host",
                    "host-bare-data-01",
                ],
                cwd=SOURCE_ROOT,
                capture_output=True,
                check=False,
                env=dict(os.environ, PYTHONDONTWRITEBYTECODE="1"),
                text=True,
            )

            self.assertEqual(duplicate.returncode, 2)
            self.assertEqual(
                duplicate.stderr, "invalid registry: validation failed (1 issue(s))\n"
            )

            missing = subprocess.run(
                [
                    sys.executable,
                    str(SOURCE_ROOT / "fleetctl.py"),
                    "status",
                    "--registry",
                    str(registry_path),
                ],
                cwd=SOURCE_ROOT,
                capture_output=True,
                check=False,
                env=dict(os.environ, PYTHONDONTWRITEBYTECODE="1"),
                text=True,
            )

            self.assertEqual(missing.returncode, 2)
            self.assertIn("--host", missing.stderr)

    def test_cli_does_not_echo_rejected_sensitive_values(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            data = self.valid_registry()
            endpoint_like_value = ".".join(("198", "51", "100", "42"))
            data["hosts"][0]["display_name"] = f"opaque {endpoint_like_value}"
            registry_path = Path(temp_dir) / "registry.json"
            registry_path.write_text(json.dumps(data), encoding="utf-8")
            result = subprocess.run(
                [
                    sys.executable,
                    str(SOURCE_ROOT / "fleetctl.py"),
                    "validate",
                    "--registry",
                    str(registry_path),
                ],
                cwd=SOURCE_ROOT,
                capture_output=True,
                check=False,
                env=dict(os.environ, PYTHONDONTWRITEBYTECODE="1"),
                text=True,
            )

            self.assertEqual(result.returncode, 2)
            self.assertNotIn(endpoint_like_value, result.stdout)
            self.assertNotIn(endpoint_like_value, result.stderr)

    def test_duplicate_json_field_cannot_hide_sensitive_material(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            endpoint_like_value = ".".join(("198", "51", "100", "42"))
            source = (SOURCE_ROOT / "registry.json").read_text(encoding="utf-8")
            registry_path = Path(temp_dir) / "registry.json"
            registry_path.write_text(
                source.replace(
                    '"display_name": "EU data plane snapshot"',
                    f'"display_name": "opaque {endpoint_like_value}", '
                    '"display_name": "EU data plane snapshot"',
                    1,
                ),
                encoding="utf-8",
            )

            with self.assertRaises(registry.RegistryLoadError) as captured:
                registry.load_registry(registry_path)
            self.assertNotIn(endpoint_like_value, str(captured.exception))

            result = subprocess.run(
                [
                    sys.executable,
                    str(SOURCE_ROOT / "fleetctl.py"),
                    "validate",
                    "--registry",
                    str(registry_path),
                ],
                cwd=SOURCE_ROOT,
                capture_output=True,
                check=False,
                env=dict(os.environ, PYTHONDONTWRITEBYTECODE="1"),
                text=True,
            )

            self.assertEqual(result.returncode, 2)
            self.assertNotIn(endpoint_like_value, result.stdout)
            self.assertNotIn(endpoint_like_value, result.stderr)

    def test_cli_redacts_invalid_text_encoding(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            registry_path = Path(temp_dir) / "registry.json"
            registry_path.write_bytes(b"\xff")

            result = subprocess.run(
                [
                    sys.executable,
                    str(SOURCE_ROOT / "fleetctl.py"),
                    "validate",
                    "--registry",
                    str(registry_path),
                ],
                cwd=SOURCE_ROOT,
                capture_output=True,
                check=False,
                env=dict(os.environ, PYTHONDONTWRITEBYTECODE="1"),
                text=True,
            )

            self.assertEqual(result.returncode, 2)
            self.assertNotIn("Traceback", result.stderr)

    def test_production_modules_do_not_import_transport_or_execution_modules(self) -> None:
        forbidden_modules = {
            "aiohttp",
            "asyncio",
            "asyncssh",
            "dns",
            "fabric",
            "ftplib",
            "http",
            "httpx",
            "os",
            "paramiko",
            "requests",
            "socket",
            "subprocess",
            "telnetlib",
            "urllib",
            "urllib3",
            "websocket",
            "websockets",
        }
        source_paths = (
            source_path
            for source_path in SOURCE_ROOT.rglob("*.py")
            if "tests" not in source_path.relative_to(SOURCE_ROOT).parts
        )
        for source_path in sorted(source_paths):
            tree = ast.parse(source_path.read_text(encoding="utf-8"))
            imported_modules: set[str] = set()
            for node in ast.walk(tree):
                if isinstance(node, ast.Import):
                    imported_modules.update(alias.name.split(".")[0] for alias in node.names)
                elif isinstance(node, ast.ImportFrom) and node.module:
                    imported_modules.add(node.module.split(".")[0])
            self.assertFalse(imported_modules & forbidden_modules, source_path.name)


if __name__ == "__main__":
    unittest.main()
