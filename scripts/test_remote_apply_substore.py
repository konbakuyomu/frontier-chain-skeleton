import copy
import importlib.util
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
FILE_NAME = "frontier-chain-mihomo"


def load_applier():
    spec = importlib.util.spec_from_file_location(
        "remote_apply_substore",
        ROOT / "scripts" / "remote-apply-substore.py",
    )
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


APPLIER = load_applier()


class MihomoMainProcessSelectionTests(unittest.TestCase):
    def three_process_file(self):
        return {
            "name": FILE_NAME,
            "content": APPLIER.DEFAULT_MIHOMO_CONTENT_PLACEHOLDER,
            "source": "local",
            "displayName": APPLIER.DEFAULT_MIHOMO_DISPLAY_NAME,
            "display-name": APPLIER.DEFAULT_MIHOMO_DISPLAY_NAME,
            "process": [
                {
                    "type": APPLIER.SCRIPT_OPERATOR_TYPE,
                    "customName": "powerfullz override-rules inline latest",
                    "disabled": False,
                    "args": {
                        "mode": "script",
                        "content": "powerfullz-content",
                        "arguments": {"powerfullz_option": "keep"},
                    },
                },
                {
                    "type": APPLIER.RESPONSE_TRANSFORMER_TYPE,
                    "customName": APPLIER.MIHOMO_MAIN_OPERATOR_NAME,
                    "disabled": False,
                    "args": {
                        "mode": "script",
                        "content": "old-main-content",
                        "arguments": {"frontier_legacy": "remove", "main_option": "keep"},
                    },
                },
                {
                    "type": APPLIER.RESPONSE_TRANSFORMER_TYPE,
                    "customName": "frontier-front-selector",
                    "disabled": False,
                    "args": {
                        "mode": "script",
                        "content": "front-selector-content",
                        "arguments": {"frontier_selector": "must-stay", "selector_option": "keep"},
                    },
                },
            ],
        }

    def fixture(self):
        return {"files": [self.three_process_file()]}

    def test_main_update_targets_only_the_named_main_and_is_idempotent(self):
        data = self.fixture()
        before = copy.deepcopy(data["files"][0]["process"])

        self.assertEqual(
            APPLIER.update_mihomo_main(data, FILE_NAME, "new-main-content"),
            [FILE_NAME],
        )

        process = data["files"][0]["process"]
        self.assertEqual([op["customName"] for op in process], [op["customName"] for op in before])
        self.assertEqual(process[0], before[0])
        self.assertEqual(process[2], before[2])
        self.assertEqual(process[1]["type"], APPLIER.RESPONSE_TRANSFORMER_TYPE)
        self.assertEqual(process[1]["args"]["content"], "new-main-content")
        self.assertEqual(process[1]["args"]["arguments"], {"main_option": "keep"})

        snapshot = copy.deepcopy(data)
        self.assertEqual(APPLIER.update_mihomo_main(data, FILE_NAME, "new-main-content"), [])
        self.assertEqual(data, snapshot)

    def test_extra_hosts_targets_only_the_named_main_and_is_idempotent(self):
        data = self.fixture()
        before = copy.deepcopy(data["files"][0]["process"])

        self.assertEqual(
            APPLIER.set_mihomo_extra_ai_api_hosts(
                data,
                FILE_NAME,
                ["API.EXAMPLE.TEST.", "api.example.test", "other.example.test"],
            ),
            [FILE_NAME],
        )

        process = data["files"][0]["process"]
        self.assertEqual([op["customName"] for op in process], [op["customName"] for op in before])
        self.assertEqual(process[0], before[0])
        self.assertEqual(process[2], before[2])
        self.assertEqual(
            process[1]["args"]["arguments"]["extra_ai_api_hosts"],
            ["api.example.test", "other.example.test"],
        )

        snapshot = copy.deepcopy(data)
        self.assertEqual(
            APPLIER.set_mihomo_extra_ai_api_hosts(
                data,
                FILE_NAME,
                ["api.example.test", "other.example.test"],
            ),
            [],
        )
        self.assertEqual(data, snapshot)

    def test_missing_or_ambiguous_main_rejects_without_mutating_input(self):
        for label, expected_count, arrange in (
            ("missing", 0, lambda process: process.pop(1)),
            ("ambiguous", 2, lambda process: process.append(copy.deepcopy(process[1]))),
        ):
            for update in (
                lambda data: APPLIER.update_mihomo_main(data, FILE_NAME, "new-main-content"),
                lambda data: APPLIER.set_mihomo_extra_ai_api_hosts(data, FILE_NAME, ["api.example.test"]),
            ):
                with self.subTest(label=label, update=update):
                    data = self.fixture()
                    arrange(data["files"][0]["process"])
                    before = copy.deepcopy(data)

                    with self.assertRaisesRegex(RuntimeError, "found %d" % expected_count):
                        update(data)

                    self.assertEqual(data, before)


if __name__ == "__main__":
    unittest.main()
