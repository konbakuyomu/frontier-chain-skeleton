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
            self.assertEqual(registry_path.read_bytes(), before_bytes)
            self.assertEqual(
                sorted(item.name for item in Path(temp_dir).iterdir()), before_entries
            )

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
            "asyncssh",
            "fabric",
            "ftplib",
            "http",
            "paramiko",
            "requests",
            "socket",
            "subprocess",
            "telnetlib",
            "urllib",
        }
        for source_name in ("registry.py", "fleetctl.py"):
            tree = ast.parse((SOURCE_ROOT / source_name).read_text(encoding="utf-8"))
            imported_modules: set[str] = set()
            for node in ast.walk(tree):
                if isinstance(node, ast.Import):
                    imported_modules.update(alias.name.split(".")[0] for alias in node.names)
                elif isinstance(node, ast.ImportFrom) and node.module:
                    imported_modules.add(node.module.split(".")[0])
            self.assertFalse(imported_modules & forbidden_modules, source_name)


if __name__ == "__main__":
    unittest.main()
