import copy
import importlib.util
import json
import sys
import tempfile
import textwrap
import unittest
from pathlib import Path
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "subscription-hub"))

import ai_routing
import rule_mirror


def load_renderer():
    spec = importlib.util.spec_from_file_location("render_ai_routing", ROOT / "scripts" / "render-ai-routing.py")
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def load_remote_verifier():
    spec = importlib.util.spec_from_file_location(
        "remote_verify_substore",
        ROOT / "scripts" / "remote-verify-substore.py",
    )
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class AiRoutingTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.contract = ai_routing.load_contract(ROOT / "ai-routing-contract.json")
        cls.registry = ai_routing.validate_registry(
            json.loads((ROOT / "subscription-hub" / "rules.json").read_text(encoding="utf-8")),
            cls.contract,
        )

    def test_contract_rejects_required_negative_conflict(self):
        contract = copy.deepcopy(self.contract)
        contract["services"]["xai"]["required"].append({"type": "suffix", "value": "x.com"})
        with self.assertRaises(ai_routing.ContractError):
            ai_routing.validate_contract(contract)

        contract = copy.deepcopy(self.contract)
        contract["services"]["openai"]["shared_not_ai"].append(
            {"type": "suffix", "value": "api.openai.com"}
        )
        with self.assertRaises(ai_routing.ContractError):
            ai_routing.validate_contract(contract)

        contract = copy.deepcopy(self.contract)
        contract["services"]["openai"]["required"].append({"type": "suffix", "value": "x.com"})
        with self.assertRaises(ai_routing.ContractError):
            ai_routing.validate_contract(contract)

    def test_machine_readable_schema_is_present(self):
        schema = json.loads((ROOT / "ai-routing-contract.schema.json").read_text(encoding="utf-8"))
        self.assertEqual(schema["$schema"], "https://json-schema.org/draft/2020-12/schema")
        self.assertIn("services", schema["required"])

    def test_projection_strips_broad_x_and_regex_but_keeps_exact_grok_entry(self):
        raw, report = ai_routing.parse_domain_list("+.x.com\n+.grok.x.com\nregexp:^grok-.+$\ngrok.com\n")
        projected, excluded = ai_routing.project_rules(raw, self.contract)
        self.assertEqual(report["plus_suffix"], 2)
        self.assertEqual(excluded["excluded_negative"], 1)
        self.assertEqual(excluded["excluded_regex_review"], 1)
        self.assertNotIn(ai_routing.CanonicalRule("suffix", "x.com"), projected)
        self.assertIn(ai_routing.CanonicalRule("suffix", "grok.x.com"), projected)

    def test_registry_has_one_active_logical_provider_per_client(self):
        counts = ai_routing.derived_registry_counts(self.registry)
        self.assertEqual(counts["logical_providers"], {"mihomo": 4, "shadowrocket": 4})
        self.assertEqual(counts["disabled"], 4)
        self.assertTrue(all(isinstance(item.get("enabled"), bool) for item in self.registry["items"]))

        missing_enabled = copy.deepcopy(self.registry)
        missing_enabled["items"][0].pop("enabled")
        with self.assertRaisesRegex(ai_routing.RegistryError, "enabled must be explicitly set"):
            ai_routing.validate_registry(missing_enabled, self.contract)

        mismatched_source = copy.deepcopy(self.registry)
        logical_item = next(item for item in mismatched_source["items"] if item.get("logical_provider") == "ai-openai")
        logical_item["semantic_source"] = "https://example.com/different-reviewed-source"
        with self.assertRaisesRegex(ai_routing.RegistryError, "source == semantic_source"):
            ai_routing.validate_registry(mismatched_source, self.contract)

    def test_semantic_required_may_reference_conditional_contract_rules(self):
        item = next(
            entry for entry in self.registry["items"]
            if entry.get("id") == "sr-ai-anthropic-v2"
        )
        declared = [rule["value"] for rule in item["semantic_required"]]
        # Conditional contract rows carry the first-party Claude hosts, so sentinels must
        # be able to name them; otherwise a silent upstream drop stays gate-invisible.
        for expected in ("claude.com", "claude.ai", "clau.de", "claudemcpclient.com"):
            self.assertIn(expected, declared)
        ai_routing.validate_registry(copy.deepcopy(self.registry), self.contract)

    def test_semantic_required_rejects_rules_outside_the_contract(self):
        patched = copy.deepcopy(self.registry)
        item = next(
            entry for entry in patched["items"]
            if entry.get("id") == "sr-ai-anthropic-v2"
        )
        item["semantic_required"].append({"type": "suffix", "value": "not-in-contract.example"})
        with self.assertRaisesRegex(ai_routing.RegistryError, "required or conditional"):
            ai_routing.validate_registry(patched, self.contract)

    def test_failed_logical_refresh_keeps_last_known_good(self):
        item = next(
            entry for entry in ai_routing.active_items(self.registry, "mihomo")
            if entry.get("logical_provider") == "ai-openai"
        )
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / str(item["file"])
            target.write_bytes(b"last-known-good")
            with patch.object(rule_mirror, "download_rule", return_value=(b"openai.com\n", 200)):
                status = rule_mirror.sync_one(item, Path(directory), self.contract, 1, 1, 0)
            self.assertEqual(status["status"], "stale")
            self.assertEqual(target.read_bytes(), b"last-known-good")

    def test_failed_first_publish_does_not_create_a_rule_file(self):
        item = next(
            entry for entry in ai_routing.active_items(self.registry, "mihomo")
            if entry.get("logical_provider") == "ai-openai"
        )
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / str(item["file"])
            with patch.object(rule_mirror, "download_rule", return_value=(b"openai.com\n", 200)):
                status = rule_mirror.sync_one(item, Path(directory), self.contract, 1, 1, 0)
            self.assertEqual(status["status"], "failed")
            self.assertFalse(target.exists())

    def test_unreviewed_domain_list_semantics_fail_closed(self):
        item = next(
            entry for entry in ai_routing.active_items(self.registry, "mihomo")
            if entry.get("logical_provider") == "ai-openai"
        )
        parsed_rules, parsed = ai_routing.parse_domain_list(
            "openai.com\nchatgpt.com\noaistatic.com\noaiusercontent.com\noaistatsig.com\n"
            "@unreviewed\nregexp:^new-shared-host\\.example$\n"
        )
        self.assertTrue(parsed_rules)
        self.assertEqual(parsed["attribute_names"], ["unreviewed"])
        with self.assertRaises(ai_routing.SemanticError):
            ai_routing.validate_declared_exclusions(item, parsed)
        with self.assertRaisesRegex(ai_routing.SemanticError, "unknown attribute syntax"):
            ai_routing.parse_domain_list("openai.com\n@not.valid\n")

    def test_large_normalized_change_requires_manual_promotion(self):
        previous = [ai_routing.CanonicalRule("suffix", f"old-{index}.example") for index in range(10)]
        current = [ai_routing.CanonicalRule("suffix", f"new-{index}.example") for index in range(40)]
        with self.assertRaises(ai_routing.SemanticError):
            ai_routing.gated_normalized_diff(previous, current)

    def test_source_replacement_does_not_change_client_blocks(self):
        renderer = load_renderer()
        replacement = copy.deepcopy(self.registry)
        for item in replacement["items"]:
            if item.get("logical_provider") != "ai-openai":
                continue
            item["source"] = "https://example.com/reviewed-openai-source"
            item["semantic_source"] = item["source"]
            item["adapter"] = "classical-to-classical"
        replacement = ai_routing.validate_registry(replacement, self.contract)
        self.assertEqual(
            renderer.render_mihomo(self.contract, self.registry),
            renderer.render_mihomo(self.contract, replacement),
        )
        self.assertEqual(
            renderer.render_shadowrocket_providers(self.registry),
            renderer.render_shadowrocket_providers(replacement),
        )

    def test_classical_source_adapter_projects_to_the_same_stable_format(self):
        item = copy.deepcopy(next(
            entry for entry in ai_routing.active_items(self.registry, "mihomo")
            if entry.get("logical_provider") == "ai-openai"
        ))
        item["adapter"] = "classical-to-classical"
        source = (
            "DOMAIN-SUFFIX,openai.com\nDOMAIN-SUFFIX,chatgpt.com\n"
            "DOMAIN-SUFFIX,oaistatic.com\nDOMAIN-SUFFIX,oaiusercontent.com\n"
            "DOMAIN-SUFFIX,oaistatsig.com\nDOMAIN-SUFFIX,sentry.io\n"
        ).encode("utf-8")
        with tempfile.TemporaryDirectory() as directory:
            candidate, semantic = rule_mirror.logical_candidate(
                item,
                source,
                self.contract,
                Path(directory) / str(item["file"]),
            )
        text = candidate.decode("utf-8")
        self.assertIn("DOMAIN-SUFFIX,openai.com", text)
        self.assertNotIn("sentry.io", text)
        self.assertEqual(semantic["status"], "passed")

    def test_renderer_is_current_and_marker_only(self):
        renderer = load_renderer()
        main_before, main_after = renderer.render_file(
            ROOT / "main.js",
            renderer.MAIN_BEGIN,
            renderer.MAIN_END,
            renderer.render_mihomo(self.contract, self.registry),
        )
        sr_before, sr_after = renderer.render_file(
            ROOT / "shadowrocket.conf",
            renderer.SHADOWROCKET_BEGIN,
            renderer.SHADOWROCKET_END,
            renderer.render_shadowrocket_baseline(self.contract),
        )
        sr_provider_before, sr_provider_after = renderer.render_file(
            ROOT / "shadowrocket.conf",
            renderer.SHADOWROCKET_PROVIDERS_BEGIN,
            renderer.SHADOWROCKET_PROVIDERS_END,
            renderer.render_shadowrocket_providers(self.registry),
        )
        self.assertEqual(main_before, main_after)
        self.assertEqual(sr_before, sr_after)
        self.assertEqual(sr_provider_before, sr_provider_after)
        sr_text = sr_before.decode("utf-8")
        self.assertEqual(sr_text.count("shadowrocket-advertising-domain.list"), 1)
        self.assertEqual(sr_text.count("shadowrocket-advertising.list"), 1)
        self.assertNotIn("DOMAIN-SUFFIX,x.com,🤖 AI 服务", sr_text)
        drift = main_before.decode("utf-8").replace("Generated from ai-routing-contract.json", "drift", 1)
        repaired = renderer.replace_block(
            drift,
            renderer.MAIN_BEGIN,
            renderer.MAIN_END,
            renderer.render_mihomo(self.contract, self.registry),
        )
        self.assertNotEqual(drift, repaired)

    def test_generated_main_removes_legacy_provider_objects_and_keeps_x_negative(self):
        renderer = load_renderer()
        main_block = "\n".join(renderer.render_mihomo(self.contract, self.registry))
        self.assertIn("delete retainedRuleProviders[providerName]", main_block)
        self.assertIn("DOMAIN,grok.x.com,${aiGroup}", main_block)
        self.assertNotIn("DOMAIN-SUFFIX,x.com,${aiGroup}", main_block)

        verifier = load_remote_verifier()
        profile = textwrap.dedent(
            """\
            proxy-groups:
              - name: AI服务
                type: select
                proxies:
                  - 🏡 家宽选择
                  - 选择代理
              - name: PayPal
                type: select
                proxies:
                  - 🏡 家宽选择
                  - 选择代理
            rules:
              - MATCH,选择代理
            """
        )
        analyzed = verifier.analyze_mihomo_output(profile)
        self.assertEqual(analyzed["ai_group_selector_refs"], 1)
        self.assertEqual(analyzed["paypal_group_selector_refs"], 1)
        self.assertEqual(analyzed["ai_group_region_residential_refs"], 0)
        self.assertEqual(analyzed["paypal_group_region_residential_refs"], 0)

    def test_remote_verifier_treats_shared_not_ai_hosts_as_first_match_controls(self):
        verifier = load_remote_verifier()
        profile = textwrap.dedent(
            """\
            proxy-groups:
              - name: AI服务
                type: select
                proxies:
                  - 🏡 家宽选择
              - name: 选择代理
                type: select
                proxies:
                  - DIRECT
            rules:
              - DOMAIN-SUFFIX,github.com,选择代理
              - DOMAIN,x.com,选择代理
              - MATCH,选择代理
            """
        )
        analyzed = verifier.analyze_mihomo_output(profile, self.contract)
        controls = analyzed["ai_negative_matches"]
        self.assertEqual(controls["github.com"], "选择代理")
        self.assertEqual(controls["x.com"], "选择代理")

    def test_remote_verifier_requires_exact_logical_ai_provider_files(self):
        verifier = load_remote_verifier()
        logical_files = verifier.load_ai_routing_registry(ROOT / "subscription-hub" / "rules.json")
        providers = "\n".join(
            f"  {name}:\n    type: http\n    url: https://link.konbakuyomu.us/rules/{filename}"
            for name, filename in logical_files.items()
        )
        profile = "rule-providers:\n" + providers + "\n"
        analyzed = verifier.analyze_mihomo_output(profile, self.contract, logical_files)
        provider_urls = analyzed["rule_provider_urls"]
        self.assertEqual(provider_urls["missing_logical_ai_providers"], [])
        self.assertEqual(provider_urls["wrong_logical_ai_provider_files"], [])

        broken = profile.replace("mihomo-ai-xai-v2.list", "mihomo-ai-xai-legacy.list")
        broken_urls = verifier.analyze_mihomo_output(broken, self.contract, logical_files)["rule_provider_urls"]
        self.assertEqual(broken_urls["missing_logical_ai_providers"], [])
        self.assertEqual(broken_urls["wrong_logical_ai_provider_files"], ["ai-xai"])


if __name__ == "__main__":
    unittest.main()
