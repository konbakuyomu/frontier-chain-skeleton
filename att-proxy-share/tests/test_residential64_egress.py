import contextlib
import copy
import io
import json
import sys
import tempfile
import unittest
from pathlib import Path


SOURCE_ROOT = Path(__file__).resolve().parents[1]
if str(SOURCE_ROOT) not in sys.path:
    sys.path.insert(0, str(SOURCE_ROOT))

import residential64_egress as egress


class Residential64EgressTests(unittest.TestCase):
    def base_config(self):
        return {
            "log-level": "warning",
            "proxy-providers": {
                "att-upstreams": {
                    "type": "http",
                    "url": "https://att.example.test/raw",
                    "interval": 300,
                    "exclude-filter": "ATT-only",
                    "health-check": {"enable": True, "interval": 300},
                }
            },
            "proxy-groups": [
                {"name": "ATT", "type": "select", "use": ["att-upstreams"]},
                {"name": "unrelated", "type": "select", "proxies": ["ATT"]},
            ],
            "listeners": [
                {
                    "name": "att-egress-in",
                    "type": "mixed",
                    "listen": "127.0.0.1",
                    "port": 17082,
                    "proxy": "ATT",
                    "udp": False,
                }
            ],
            "rules": ["IP-CIDR,127.0.0.1/32,REJECT", "MATCH,REJECT"],
            "unrelated": {"keep": ["this", "unchanged"]},
        }

    def test_add_preserves_existing_objects_and_routes_new_listener_to_its_role(self):
        source = self.base_config()
        original = copy.deepcopy(source)
        candidate, status = egress.build_candidate(
            source, "https://raw.example.test/residential-64"
        )

        self.assertEqual(source, original)
        self.assertEqual(status, "added")
        self.assertEqual(candidate["proxy-providers"]["att-upstreams"], original["proxy-providers"]["att-upstreams"])
        self.assertEqual(candidate["proxy-groups"][:-1], original["proxy-groups"])
        self.assertEqual(candidate["listeners"][:-1], original["listeners"])
        self.assertEqual(candidate["rules"], original["rules"])
        self.assertEqual(candidate["unrelated"], original["unrelated"])
        self.assertEqual(
            candidate["proxy-providers"][egress.RESIDENTIAL64_PROVIDER_NAME]["filter"],
            egress.exact_node_filter(),
        )
        self.assertNotIn(
            "exclude-filter", candidate["proxy-providers"][egress.RESIDENTIAL64_PROVIDER_NAME]
        )
        self.assertEqual(candidate["proxy-groups"][-1], egress.residential64_group())
        self.assertEqual(candidate["proxy-groups"][-1]["empty-fallback"], "REJECT")
        self.assertEqual(candidate["listeners"][-1], egress.residential64_listener())
        self.assertNotIn("DIRECT", json.dumps(candidate))

    def test_matching_shape_is_idempotent(self):
        provider_url = "https://raw.example.test/residential-64"
        applied, _ = egress.build_candidate(self.base_config(), provider_url)
        candidate, status = egress.build_candidate(applied, provider_url)

        self.assertEqual(status, "already-current")
        self.assertEqual(candidate, applied)

    def test_provider_cache_path_is_separate_from_att(self):
        source = self.base_config()
        source["proxy-providers"]["att-upstreams"]["path"] = "./providers/att.yaml"
        original = copy.deepcopy(source)

        candidate, _ = egress.build_candidate(source, "https://raw.example.test/residential-64")

        self.assertEqual(source, original)
        self.assertEqual(
            candidate["proxy-providers"][egress.RESIDENTIAL64_PROVIDER_NAME]["path"],
            "./providers/att.yaml.residential64",
        )
        self.assertEqual(
            candidate["proxy-providers"][egress.ATT_PROVIDER_NAME]["path"],
            "./providers/att.yaml",
        )

    def test_provider_cache_path_collision_is_rejected_before_mutation(self):
        source = self.base_config()
        source["proxy-providers"]["att-upstreams"]["path"] = "./providers/att.yaml"
        source["proxy-providers"]["other-upstreams"] = {
            "path": "./providers/att.yaml.residential64"
        }
        original = copy.deepcopy(source)

        with self.assertRaisesRegex(ValueError, "residential provider path collision"):
            egress.build_candidate(source, "https://raw.example.test/residential-64")

        self.assertEqual(source, original)

    def test_conflicting_group_or_port_is_rejected(self):
        group_collision = self.base_config()
        group_collision["proxy-groups"].append({"name": egress.RESIDENTIAL64_ROLE, "type": "url-test"})
        original_group_collision = copy.deepcopy(group_collision)
        with self.assertRaisesRegex(ValueError, "residential group collision"):
            egress.build_candidate(group_collision, "https://raw.example.test/residential-64")
        self.assertEqual(group_collision, original_group_collision)

        port_collision = self.base_config()
        port_collision["listeners"].append({"name": "other", "port": egress.RESIDENTIAL64_PORT})
        original_port_collision = copy.deepcopy(port_collision)
        with self.assertRaisesRegex(ValueError, "residential listener port collision"):
            egress.build_candidate(port_collision, "https://raw.example.test/residential-64")
        self.assertEqual(port_collision, original_port_collision)

    def test_cli_reads_private_url_file_and_never_prints_the_url(self):
        provider_url = "https://raw.example.test/residential-64"
        with tempfile.TemporaryDirectory() as root:
            root_path = Path(root)
            input_path = root_path / "egress.json"
            provider_path = root_path / "provider-url"
            output_path = root_path / "candidate.json"
            input_path.write_text(json.dumps(self.base_config()), encoding="utf-8")
            provider_path.write_text(provider_url + "\n", encoding="utf-8")

            first = io.StringIO()
            with contextlib.redirect_stdout(first):
                self.assertEqual(
                    egress.main(
                        [
                            "--input",
                            str(input_path),
                            "--provider-url-file",
                            str(provider_path),
                            "--output",
                            str(output_path),
                        ]
                    ),
                    0,
                )
            self.assertIn("status=added", first.getvalue())
            self.assertNotIn(provider_url, first.getvalue())
            self.assertEqual(
                json.loads(output_path.read_text(encoding="utf-8"))["proxy-providers"]
                [egress.RESIDENTIAL64_PROVIDER_NAME]["url"],
                provider_url,
            )

            second = io.StringIO()
            with contextlib.redirect_stdout(second):
                egress.main(
                    [
                        "--input",
                        str(input_path),
                        "--provider-url-file",
                        str(provider_path),
                        "--output",
                        str(output_path),
                    ]
                )
            self.assertIn("status=already-current", second.getvalue())
            self.assertNotIn(provider_url, second.getvalue())

    def test_cli_refuses_to_overwrite_the_att_input(self):
        with tempfile.TemporaryDirectory() as root:
            root_path = Path(root)
            input_path = root_path / "egress.json"
            provider_path = root_path / "provider-url"
            original = json.dumps(self.base_config())
            input_path.write_text(original, encoding="utf-8")
            provider_path.write_text("https://raw.example.test/residential-64\n", encoding="utf-8")

            with self.assertRaisesRegex(ValueError, "must differ from the ATT egress input"):
                egress.main(
                    [
                        "--input",
                        str(input_path),
                        "--provider-url-file",
                        str(provider_path),
                        "--output",
                        str(input_path),
                    ]
                )
            self.assertEqual(input_path.read_text(encoding="utf-8"), original)


if __name__ == "__main__":
    unittest.main()
