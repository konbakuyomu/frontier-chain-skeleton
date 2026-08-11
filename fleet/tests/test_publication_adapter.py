from __future__ import annotations

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

import publication_adapter
import registry


class PublicationAdapterTests(unittest.TestCase):
    def valid_registry(self) -> dict[str, object]:
        return json.loads((SOURCE_ROOT / "registry.json").read_text(encoding="utf-8"))

    def valid_contract(self) -> dict[str, object]:
        return json.loads(
            (SOURCE_ROOT / "publication-contract.json").read_text(encoding="utf-8")
        )

    def assert_invalid_contract(
        self, contract: dict[str, object]
    ) -> publication_adapter.PublicationContractValidationError:
        with self.assertRaises(
            publication_adapter.PublicationContractValidationError
        ) as captured:
            publication_adapter.validate_publication_contract(contract)
        return captured.exception

    def test_documented_contract_derives_three_separate_stale_unbound_channels(self) -> None:
        plan = publication_adapter.publication_plan(self.valid_registry())
        rows = plan["publications"]

        self.assertEqual(
            publication_adapter.validate_publication_contract(self.valid_contract()),
            {"channel_contracts": 3, "route_bindings": 4},
        )
        self.assertEqual(plan["summary"], {
            "eligible_publication_count": 0,
            "ineligible_publication_count": 6,
            "promotion_ready_count": 0,
            "publication_count": 6,
            "unbound_publication_count": 6,
        })
        self.assertTrue(all(not row["eligible"] for row in rows))
        self.assertTrue(all(row["binding_state"] == "unbound" for row in rows))
        self.assertTrue(all(row["evidence_status"] == "stale-snapshot" for row in rows))
        self.assertEqual(
            {row["blocker"] for row in rows}, {"observed-blocked", "stale-snapshot"}
        )

        channel_rows: dict[str, set[tuple[str, str, str]]] = {}
        for row in rows:
            channel_rows.setdefault(row["channel"], set()).add(
                (row["protocol"], row["output_shape"], row["semantic_profile"])
            )
        self.assertEqual(
            channel_rows["final-mihomo"],
            {
                ("hy2", "mihomo-profile", "shared-mihomo"),
                ("reality", "mihomo-profile", "shared-mihomo"),
                ("vmess", "mihomo-profile", "shared-mihomo"),
            },
        )
        self.assertEqual(
            channel_rows["shadowrocket-ordinary-uri"],
            {
                ("reality", "uri-feed", "ordinary"),
                ("vmess", "uri-feed", "ordinary"),
            },
        )
        self.assertEqual(
            channel_rows["shadowrocket-hy2-only"],
            {("hy2", "hy2-yaml", "hy2")},
        )

    def test_publication_plan_is_deterministic_sorted_and_does_not_mutate_registry(self) -> None:
        data = self.valid_registry()
        before = json.dumps(data, sort_keys=True)

        first = publication_adapter.render_publication_plan_json(data)
        second = publication_adapter.render_publication_plan_json(data)
        rows = json.loads(first)["publications"]

        self.assertEqual(first, second)
        self.assertEqual(before, json.dumps(data, sort_keys=True))
        self.assertEqual(
            [
                (row["role_id"], row["channel"], row["publication_id"])
                for row in rows
            ],
            sorted(
                (row["role_id"], row["channel"], row["publication_id"])
                for row in rows
            ),
        )

    def test_documented_contract_matches_semantics_but_reports_every_row_unbound(self) -> None:
        data = self.valid_registry()
        contract = self.valid_contract()
        before_registry = json.dumps(data, sort_keys=True)
        before_contract = json.dumps(contract, sort_keys=True)

        first = publication_adapter.publication_diff(data, contract)
        second = publication_adapter.publication_diff(data, contract)

        self.assertEqual(first, second)
        self.assertEqual(first["summary"], {
            "matched_count": 6,
            "mismatch_count": 0,
            "missing_count": 0,
            "promotion_ready_count": 0,
            "unbound_count": 6,
            "unexpected_count": 0,
        })
        self.assertEqual(first["matched"], first["unbound"])
        self.assertEqual(first["missing"], [])
        self.assertEqual(first["mismatch"], [])
        self.assertEqual(first["unexpected"], [])
        self.assertEqual(before_registry, json.dumps(data, sort_keys=True))
        self.assertEqual(before_contract, json.dumps(contract, sort_keys=True))

    def test_diff_reports_missing_and_unexpected_route_bindings_without_promoting_rows(self) -> None:
        contract = self.valid_contract()
        contract["route_bindings"] = [
            item
            for item in contract["route_bindings"]
            if item["role_id"] != "route-bare-data-reality"
        ]
        contract["route_bindings"].append(
            {"role_id": "route-unexpected", "binding_state": "unbound"}
        )
        contract["route_bindings"].sort(key=lambda item: item["role_id"])

        diff = publication_adapter.publication_diff(self.valid_registry(), contract)

        self.assertEqual(
            [row["publication_id"] for row in diff["missing"]],
            [
                "publication-bare-data-reality-final-mihomo",
                "publication-bare-data-reality-ordinary-uri",
            ],
        )
        self.assertEqual(
            diff["unexpected"],
            [{"binding_state": "unbound", "role_id": "route-unexpected"}],
        )
        self.assertEqual(diff["summary"]["promotion_ready_count"], 0)
        self.assertEqual(len(diff["unbound"]), 6)

    def test_registry_channel_policy_rejects_wrong_rows_before_they_can_match(self) -> None:
        hy2_in_ordinary = self.valid_registry()
        hy2_in_ordinary["publications"][1]["channel"] = "shadowrocket-ordinary-uri"
        with self.assertRaises(registry.RegistryValidationError):
            publication_adapter.publication_diff(hy2_in_ordinary, self.valid_contract())

        reality_in_hy2_only = self.valid_registry()
        for publication in reality_in_hy2_only["publications"]:
            if publication["publication_id"] == "publication-bare-data-reality-ordinary-uri":
                publication["channel"] = "shadowrocket-hy2-only"
        with self.assertRaises(registry.RegistryValidationError):
            publication_adapter.publication_diff(reality_in_hy2_only, self.valid_contract())

    def test_contract_rejects_unknown_duplicate_unsafe_and_noncanonical_data_without_echoing_it(self) -> None:
        cases: list[tuple[str, str, dict[str, object]]] = []

        unknown_channel = self.valid_contract()
        unknown_channel["channel_contracts"][0]["channel"] = "unknown-channel"
        cases.append(("unknown", "unknown-channel", unknown_channel))

        wrong_semantics = self.valid_contract()
        wrong_semantics["channel_contracts"][0]["output_shape"] = "wrong-shape"
        cases.append(("semantics", "wrong-shape", wrong_semantics))

        wrong_profile = self.valid_contract()
        wrong_profile["channel_contracts"][0]["semantic_profile"] = "wrong-profile"
        cases.append(("profile", "wrong-profile", wrong_profile))

        duplicate_binding = self.valid_contract()
        duplicate_binding["route_bindings"].append(
            dict(duplicate_binding["route_bindings"][0])
        )
        duplicate_binding["route_bindings"].sort(key=lambda item: item["role_id"])
        cases.append(("duplicate", "route-bare-data-hy2", duplicate_binding))

        unsafe_value = ".".join(("198", "51", "100", "42"))
        unsafe_contract = self.valid_contract()
        unsafe_contract["channel_contracts"][0]["semantic_profile"] = unsafe_value
        cases.append(("unsafe", unsafe_value, unsafe_contract))

        for name, rejected_value, contract in cases:
            with self.subTest(name=name):
                error = self.assert_invalid_contract(contract)
                self.assertNotIn(rejected_value, str(error))

    def test_contract_loader_redacts_duplicate_json_fields(self) -> None:
        unsafe_value = ".".join(("198", "51", "100", "42"))
        source = (SOURCE_ROOT / "publication-contract.json").read_text(encoding="utf-8")
        duplicate_source = source.replace(
            '"semantic_profile": "ordinary"',
            f'"semantic_profile": "ordinary", "semantic_profile": "opaque {unsafe_value}"',
            1,
        )

        with tempfile.TemporaryDirectory() as temp_dir:
            contract_path = Path(temp_dir) / "contract.json"
            contract_path.write_text(duplicate_source, encoding="utf-8")
            with self.assertRaises(
                publication_adapter.PublicationContractLoadError
            ) as captured:
                publication_adapter.load_publication_contract(contract_path)

        self.assertNotIn(unsafe_value, str(captured.exception))

    def test_publication_cli_is_stdout_only_deterministic_and_redacts_bad_contracts(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            registry_path = temp_path / "registry.json"
            contract_path = temp_path / "contract.json"
            invalid_contract_path = temp_path / "invalid-contract.json"
            registry_path.write_text(json.dumps(self.valid_registry()), encoding="utf-8")
            contract_path.write_text(json.dumps(self.valid_contract()), encoding="utf-8")

            unsafe_value = ".".join(("198", "51", "100", "42"))
            invalid_contract = self.valid_contract()
            invalid_contract["channel_contracts"][0]["semantic_profile"] = unsafe_value
            invalid_contract_path.write_text(json.dumps(invalid_contract), encoding="utf-8")

            before_registry = registry_path.read_bytes()
            before_contract = contract_path.read_bytes()
            before_invalid_contract = invalid_contract_path.read_bytes()
            before_entries = sorted(item.name for item in temp_path.iterdir())
            environment = dict(os.environ, PYTHONDONTWRITEBYTECODE="1")
            command_prefix = [sys.executable, str(SOURCE_ROOT / "fleetctl.py")]

            plan_command = command_prefix + [
                "publication-plan",
                "--registry",
                str(registry_path),
            ]
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
            self.assertEqual(
                json.loads(first_plan.stdout)["summary"]["promotion_ready_count"], 0
            )

            diff_command = command_prefix + [
                "publication-diff",
                "--registry",
                str(registry_path),
                "--contract",
                str(contract_path),
            ]
            first_diff = subprocess.run(
                diff_command,
                cwd=SOURCE_ROOT,
                capture_output=True,
                check=False,
                env=environment,
                text=True,
            )
            second_diff = subprocess.run(
                diff_command,
                cwd=SOURCE_ROOT,
                capture_output=True,
                check=False,
                env=environment,
                text=True,
            )
            self.assertEqual(first_diff.returncode, 0, first_diff.stderr)
            self.assertEqual(first_diff.stdout, second_diff.stdout)
            self.assertEqual(json.loads(first_diff.stdout)["summary"]["unbound_count"], 6)

            invalid_result = subprocess.run(
                command_prefix
                + [
                    "publication-diff",
                    "--registry",
                    str(registry_path),
                    "--contract",
                    str(invalid_contract_path),
                ],
                cwd=SOURCE_ROOT,
                capture_output=True,
                check=False,
                env=environment,
                text=True,
            )
            self.assertEqual(invalid_result.returncode, 2)
            self.assertTrue(
                invalid_result.stderr.startswith(
                    "invalid publication contract: validation failed ("
                )
            )
            self.assertNotIn(unsafe_value, invalid_result.stdout)
            self.assertNotIn(unsafe_value, invalid_result.stderr)

            self.assertEqual(registry_path.read_bytes(), before_registry)
            self.assertEqual(contract_path.read_bytes(), before_contract)
            self.assertEqual(invalid_contract_path.read_bytes(), before_invalid_contract)
            self.assertEqual(sorted(item.name for item in temp_path.iterdir()), before_entries)


if __name__ == "__main__":
    unittest.main()
