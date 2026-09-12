import contextlib
import io
import json
import os
import stat
import subprocess
import sys
import tempfile
import unittest
from argparse import Namespace
from pathlib import Path
from unittest import mock


SOURCE_ROOT = Path(__file__).resolve().parents[1]
if str(SOURCE_ROOT) not in sys.path:
    sys.path.insert(0, str(SOURCE_ROOT))

import manage


class RuntimeHarness(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        root = Path(self.temp_dir.name)
        self.root = root
        self.originals = {
            name: getattr(manage, name)
            for name in (
                "ROOT",
                "RUNTIME_DIR",
                "HANDOFF_DIR",
                "BACKUP_DIR",
                "STATE_PATH",
                "CONFIG_PATH",
                "PUBLIC_HOST_PATH",
                "COMPOSE_PATH",
                "LEGACY_PATHS",
                "CERTBOT_DIR",
                "CERTBOT_CONFIG_DIR",
                "CERTBOT_WORK_DIR",
                "CERTBOT_LOGS_DIR",
                "TLS_CERT_SOURCE_DIR",
                "SYSTEM_CA_BUNDLE",
            )
        }
        manage.ROOT = root
        manage.RUNTIME_DIR = root / ".runtime"
        manage.HANDOFF_DIR = manage.RUNTIME_DIR / "handoff"
        manage.BACKUP_DIR = manage.RUNTIME_DIR / "backups"
        manage.STATE_PATH = manage.RUNTIME_DIR / "users.json"
        manage.CONFIG_PATH = manage.RUNTIME_DIR / "config.json"
        manage.PUBLIC_HOST_PATH = manage.RUNTIME_DIR / "public-host"
        manage.COMPOSE_PATH = root / "compose.yaml"
        manage.CERTBOT_DIR = manage.RUNTIME_DIR / "certbot"
        manage.CERTBOT_CONFIG_DIR = manage.CERTBOT_DIR / "config"
        manage.CERTBOT_WORK_DIR = manage.CERTBOT_DIR / "work"
        manage.CERTBOT_LOGS_DIR = manage.CERTBOT_DIR / "logs"
        manage.TLS_CERT_SOURCE_DIR = manage.CERTBOT_CONFIG_DIR
        manage.SYSTEM_CA_BUNDLE = root / "system-ca-bundle.pem"
        manage.SYSTEM_CA_BUNDLE.write_text("test CA bundle\n", encoding="utf-8")
        manage.LEGACY_PATHS = {
            "state": root / "users.json",
            "config": root / "config.json",
            "public_host": root / "public-host",
        }
        manage.PUBLIC_HOST_PATH.parent.mkdir(parents=True, exist_ok=True)
        manage.PUBLIC_HOST_PATH.write_text("proxy.example.test\n", encoding="utf-8")
        manage.COMPOSE_PATH.write_text("services: {}\n", encoding="utf-8")
        self.apply_calls = []

    def tearDown(self):
        for name, value in self.originals.items():
            setattr(manage, name, value)
        self.temp_dir.cleanup()

    def patch_runtime(self):
        return mock.patch.multiple(
            manage,
            test_config=mock.DEFAULT,
            apply_container=mock.Mock(
                side_effect=lambda should_run, expected_ports: self.apply_calls.append(should_run)
            ),
            validate_tls_material=mock.DEFAULT,
        )

    def base_state(self):
        return manage.default_state()

    def add_state_user(self, state, label="alice"):
        candidate = dict(state)
        candidate["users"] = dict(state["users"])
        candidate["users"][label] = {
            "username": "att-fixed-user",
            "password": "fixed-secret-for-test",
            "logical_role": "ATT",
            "protocols": ["socks5", "http"],
            "created_at": 1,
        }
        return candidate

    def write_tls_material(self):
        archive = manage.TLS_CERT_SOURCE_DIR / "archive" / manage.TLS_PUBLIC_HOST
        live = manage.TLS_CERT_SOURCE_DIR / "live" / manage.TLS_PUBLIC_HOST
        archive.mkdir(parents=True)
        live.mkdir(parents=True)
        certificate_data = (
            "-----BEGIN CERTIFICATE-----\nleaf\n-----END CERTIFICATE-----\n"
            "-----BEGIN CERTIFICATE-----\nintermediate\n-----END CERTIFICATE-----\n"
        )
        certificate_target = archive / "fullchain1.pem"
        key_target = archive / "privkey1.pem"
        certificate_target.write_text(certificate_data, encoding="utf-8")
        key_target.write_text("private-key fixture\n", encoding="utf-8")
        certificate, private_key = manage.tls_source_paths()
        try:
            certificate.symlink_to(Path("../../archive") / manage.TLS_PUBLIC_HOST / "fullchain1.pem")
            private_key.symlink_to(Path("../../archive") / manage.TLS_PUBLIC_HOST / "privkey1.pem")
            self.live_links_are_symlinks = True
        except OSError:
            certificate.write_text(certificate_data, encoding="utf-8")
            private_key.write_text("private-key fixture\n", encoding="utf-8")
            self.live_links_are_symlinks = False
        return certificate, private_key, certificate_target, key_target

    @staticmethod
    def completed(stdout="", returncode=0):
        return subprocess.CompletedProcess(args=["test"], returncode=returncode, stdout=stdout, stderr="")

    def valid_tls_results(self):
        return [
            self.completed(),
            self.completed("Hostname matches\n"),
            self.completed(),
            self.completed("Public Key Algorithm: rsaEncryption\nPublic-Key: (2048 bit)\n"),
            self.completed("Private-Key: (2048 bit, 2 primes)\nmodulus:\nprivateExponent:\n"),
            self.completed("shared-public-key\n"),
            self.completed("shared-public-key\n"),
            self.completed("leaf: OK\nChain:\ndepth=2: CN = ISRG Root X1\n"),
        ]


class ConfigTests(RuntimeHarness):
    def test_empty_state_is_loopback_and_fail_closed(self):
        config = manage.config_for(self.base_state())
        self.assertFalse(config["allow-lan"])
        self.assertEqual(config["bind-address"], "127.0.0.1")
        self.assertNotIn("mixed-port", config)
        self.assertFalse(config["listeners"][0]["udp"])
        self.assertNotIn("authentication", config)
        self.assertFalse(config["udp"])
        self.assertEqual(config["rules"], ["MATCH,REJECT"])
        self.assertNotIn("DIRECT", json.dumps(config))

    def test_users_get_auth_and_username_role_rules(self):
        state = self.add_state_user(self.add_state_user(self.base_state(), "alice"), "bob")
        state["users"]["bob"]["username"] = "att-fixed-user-b"
        state["users"]["bob"]["password"] = "another-fixed-secret"
        config = manage.config_for(state)
        self.assertTrue(config["allow-lan"])
        self.assertEqual(config["bind-address"], "0.0.0.0")
        self.assertFalse(config["udp"])
        self.assertEqual(config["listeners"][0]["type"], "mixed")
        self.assertEqual(config["listeners"][0]["listen"], "0.0.0.0")
        self.assertEqual(len(config["authentication"]), 2)
        self.assertEqual(
            config["rules"],
            ["IN-USER,att-fixed-user,ATT", "IN-USER,att-fixed-user-b,ATT", "MATCH,REJECT"],
        )
        self.assertNotIn("DIRECT", json.dumps(config))
        self.assertNotIn("url", json.dumps(config).lower())

    def test_tls_listener_keeps_the_legacy_listener_and_fail_closed_route(self):
        state = self.add_state_user(self.base_state())
        config = manage.config_for(state)
        legacy, tls = config["listeners"]
        self.assertEqual(
            legacy,
            {
                "name": "ATT-PUBLIC",
                "type": "mixed",
                "port": manage.DEFAULT_PORT,
                "listen": "0.0.0.0",
                "udp": False,
            },
        )
        self.assertEqual(tls["name"], "ATT-PUBLIC-TLS")
        self.assertEqual(tls["type"], "mixed")
        self.assertEqual(tls["port"], manage.TLS_PORT)
        self.assertEqual(tls["listen"], "0.0.0.0")
        self.assertFalse(tls["udp"])
        self.assertEqual(tls["certificate"], manage.TLS_CERT_PATH)
        self.assertEqual(tls["private-key"], manage.TLS_KEY_PATH)
        self.assertIn("/live/", manage.TLS_CERT_PATH)
        self.assertEqual(config["rules"][-1], "MATCH,REJECT")
        self.assertNotIn("DIRECT", json.dumps(config))

    def test_disabled_state_stops_public_bind(self):
        state = self.add_state_user(self.base_state())
        state["enabled"] = False
        config = manage.config_for(state)
        self.assertFalse(config["allow-lan"])
        self.assertEqual(config["bind-address"], "127.0.0.1")
        self.assertEqual(config["rules"][-1], "MATCH,REJECT")

    def test_legacy_port_cannot_conflict_with_tls_listener(self):
        state = self.base_state()
        state["port"] = manage.TLS_PORT
        with self.assertRaisesRegex(SystemExit, "reserved TLS listener"):
            manage.config_for(state)


class RoleTests(RuntimeHarness):
    def residential_state(self):
        state = self.add_state_user(self.base_state())
        state["users"]["cpa"] = {
            "username": "residential-fixed-user",
            "password": "residential-fixed-secret",
            "logical_role": manage.RESIDENTIAL64_ROLE,
            "protocols": ["socks5", "http"],
            "created_at": 2,
        }
        return state

    def test_legacy_att_record_keeps_the_original_single_role_config_shape(self):
        state = self.base_state()
        state["users"]["legacy"] = {
            "username": "att-legacy",
            "password": "legacy-secret",
            "created_at": 1,
        }

        config = manage.config_for(state)

        self.assertEqual(
            config["proxies"],
            [{"name": "ATT-UPSTREAM", "type": "http", "server": manage.UPSTREAM_SERVER, "port": manage.UPSTREAM_PORT}],
        )
        self.assertEqual(config["proxy-groups"], [{"name": "ATT", "type": "select", "proxies": ["ATT-UPSTREAM"]}])
        self.assertEqual(config["rules"], ["IN-USER,att-legacy,ATT", "MATCH,REJECT"])
        self.assertNotIn("RESIDENTIAL64", json.dumps(config))

    def test_add_cli_and_config_map_residential64_to_its_own_upstream(self):
        with self.patch_runtime(), contextlib.redirect_stdout(io.StringIO()):
            args = manage.parser().parse_args(["add", "cpa", "--role", manage.RESIDENTIAL64_ROLE])
            args.func(args)
            state = manage.load_state()

        self.assertEqual(state["users"]["cpa"]["logical_role"], manage.RESIDENTIAL64_ROLE)
        config = manage.config_for(state)
        self.assertEqual(config["proxy-groups"][-1], {"name": manage.RESIDENTIAL64_ROLE, "type": "select", "proxies": ["RESIDENTIAL64-UPSTREAM"]})
        self.assertEqual(config["rules"], [f"IN-USER,{state['users']['cpa']['username']},RESIDENTIAL64", "MATCH,REJECT"])

    def test_unknown_role_is_rejected(self):
        state = self.residential_state()
        state["users"]["cpa"]["logical_role"] = "UNKNOWN"
        with self.assertRaisesRegex(SystemExit, "ATT or RESIDENTIAL64"):
            manage.normalize_state(state)

    def test_rotation_preserves_the_stored_role(self):
        state = self.residential_state()
        manage.ensure_runtime()
        manage.write_atomic(manage.STATE_PATH, manage.json_bytes(state))

        with self.patch_runtime(), contextlib.redirect_stdout(io.StringIO()):
            manage.cmd_rotate(Namespace(name="cpa"))

        self.assertEqual(manage.load_state()["users"]["cpa"]["logical_role"], manage.RESIDENTIAL64_ROLE)


class LifecycleTests(RuntimeHarness):
    def test_apply_container_requires_both_listener_ports(self):
        running = self.completed()
        with (
            mock.patch.object(manage, "run", return_value=running),
            mock.patch.object(manage, "container_running", return_value=True),
            mock.patch.object(manage, "listeners_ready", side_effect=[False, True]) as ready,
            mock.patch.object(manage.time, "sleep"),
        ):
            manage.apply_container(True, (manage.DEFAULT_PORT, manage.TLS_PORT))
        self.assertEqual(ready.call_count, 2)

    def test_listener_owner_gate_requires_the_container_pid(self):
        inspect = self.completed("4242\n")
        owned = self.completed(
            'LISTEN 0 4096 *:34567 *:* users:(("mihomo",pid=4242,fd=8))\n'
            'LISTEN 0 4096 *:8443 *:* users:(("mihomo",pid=4242,fd=9))\n'
        )
        with mock.patch.object(manage, "run", side_effect=[inspect, owned]):
            self.assertTrue(manage.listeners_owned_by_container((manage.DEFAULT_PORT, manage.TLS_PORT)))

        wrong_owner = self.completed('LISTEN 0 4096 *:34567 *:* users:(("other",pid=9,fd=8))\n')
        with mock.patch.object(manage, "run", side_effect=[inspect, wrong_owner]):
            self.assertFalse(manage.listeners_owned_by_container((manage.DEFAULT_PORT,)))

    def test_lifecycle_redacts_secrets_from_normal_output(self):
        with self.patch_runtime():
            out = io.StringIO()
            with contextlib.redirect_stdout(out):
                manage.cmd_add(Namespace(name="alice"))
            text = out.getvalue()
            state = manage.load_state()
            item = state["users"]["alice"]
            self.assertIn("handoff=", text)
            self.assertIn("credential_fingerprint=", text)
            self.assertNotIn(item["username"], text)
            self.assertNotIn(item["password"], text)
            handoff = manage.handoff_path("alice")
            self.assertTrue(handoff.exists())
            if os.name != "nt":
                self.assertEqual(stat.S_IMODE(handoff.stat().st_mode), 0o600)

            listing = io.StringIO()
            with contextlib.redirect_stdout(listing):
                manage.cmd_list(Namespace())
            self.assertNotIn(item["username"], listing.getvalue())
            self.assertNotIn(item["password"], listing.getvalue())

            with self.assertRaises(SystemExit):
                manage.cmd_show(Namespace(name="alice", reveal=False))
            revealed = io.StringIO()
            with contextlib.redirect_stdout(revealed):
                manage.cmd_show(Namespace(name="alice", reveal=True))
            self.assertIn(item["password"], revealed.getvalue())

            old_username = item["username"]
            old_password = item["password"]
            rotation = io.StringIO()
            with contextlib.redirect_stdout(rotation):
                manage.cmd_rotate(Namespace(name="alice"))
            rotated = manage.load_state()["users"]["alice"]
            self.assertNotIn(old_username, rotation.getvalue())
            self.assertNotIn(old_password, rotation.getvalue())
            self.assertNotIn(rotated["username"], rotation.getvalue())
            self.assertNotIn(rotated["password"], rotation.getvalue())
            self.assertNotEqual(old_password, rotated["password"])
            self.assertTrue(manage.handoff_path("alice").exists())

            with contextlib.redirect_stdout(io.StringIO()):
                manage.cmd_remove(Namespace(name="alice"))
            self.assertNotIn("alice", manage.load_state()["users"])
            self.assertFalse(manage.handoff_path("alice").exists())
            self.assertEqual(self.apply_calls, [True, True, False])

    def test_disable_and_enable_keep_credentials(self):
        state = self.add_state_user(self.base_state())
        with self.patch_runtime():
            manage.apply_state(manage.default_state(), state)
            with contextlib.redirect_stdout(io.StringIO()):
                manage.cmd_disable(Namespace())
            self.assertFalse(manage.load_state()["enabled"])
            with contextlib.redirect_stdout(io.StringIO()):
                manage.cmd_enable(Namespace())
            self.assertTrue(manage.load_state()["enabled"])
            self.assertIn("alice", manage.load_state()["users"])
            self.assertEqual(self.apply_calls, [True, False, True])

    def test_failed_apply_restores_previous_files(self):
        old_state = self.add_state_user(self.base_state())
        with self.patch_runtime():
            manage.write_atomic(manage.STATE_PATH, manage.json_bytes(old_state))
            old_config = manage.config_for(old_state)
            manage.write_atomic(manage.CONFIG_PATH, manage.json_bytes(old_config))
            candidate = dict(old_state)
            candidate["enabled"] = False
            with mock.patch.object(manage, "apply_container", side_effect=RuntimeError("test failure")):
                with self.assertRaises(RuntimeError):
                    manage.apply_state(old_state, candidate)
            self.assertEqual(json.loads(manage.STATE_PATH.read_text()), old_state)
            self.assertEqual(json.loads(manage.CONFIG_PATH.read_text()), old_config)


class TlsPreflightTests(RuntimeHarness):
    def test_tls_preflight_rejects_missing_material(self):
        with self.assertRaisesRegex(SystemExit, "certificate or private key"):
            manage.validate_tls_material()

    def test_tls_preflight_rejects_expiring_certificate(self):
        self.write_tls_material()
        expired = self.completed(returncode=1)
        with mock.patch.object(manage, "run", return_value=expired):
            with self.assertRaisesRegex(SystemExit, "expires within seven days"):
                manage.validate_tls_material()

    def test_tls_preflight_rejects_hostname_mismatch_even_when_openssl_returns_zero(self):
        self.write_tls_material()
        results = [self.completed(), self.completed("Hostname does NOT match certificate\n")]
        with mock.patch.object(manage, "run", side_effect=results):
            with self.assertRaisesRegex(SystemExit, "does not match the configured public host"):
                manage.validate_tls_material()

    def test_tls_preflight_rejects_non_rsa_certificate(self):
        self.write_tls_material()
        results = self.valid_tls_results()[:3] + [
            self.completed("Public Key Algorithm: id-ecPublicKey\n"),
            self.completed("Private-Key: (2048 bit, 2 primes)\nmodulus:\nprivateExponent:\n"),
        ]
        with mock.patch.object(manage, "run", side_effect=results):
            with self.assertRaisesRegex(SystemExit, "certificate must use RSA-2048"):
                manage.validate_tls_material()

    def test_tls_preflight_rejects_mismatched_certificate_and_key(self):
        self.write_tls_material()
        results = self.valid_tls_results()
        results[6] = self.completed("different-public-key\n")
        with mock.patch.object(manage, "run", side_effect=results):
            with self.assertRaisesRegex(SystemExit, "does not match"):
                manage.validate_tls_material()

    def test_tls_preflight_rejects_wrong_trust_chain(self):
        self.write_tls_material()
        results = self.valid_tls_results()
        results[-1] = self.completed("leaf: OK\nChain:\ndepth=2: CN = Other Root\n")
        with mock.patch.object(manage, "run", side_effect=results):
            with self.assertRaisesRegex(SystemExit, "does not terminate"):
                manage.validate_tls_material()

    def test_tls_preflight_accepts_rsa2048_certbot_live_archive_material(self):
        certificate, private_key, certificate_target, key_target = self.write_tls_material()
        with mock.patch.object(manage, "run", side_effect=self.valid_tls_results()) as mocked_run:
            manage.validate_tls_material()

        resolved_certificate = manage.resolve_certbot_material(certificate, "certificate")
        resolved_private_key = manage.resolve_certbot_material(private_key, "private key")
        if self.live_links_are_symlinks:
            self.assertTrue(certificate.is_symlink())
            self.assertTrue(private_key.is_symlink())
            self.assertEqual(resolved_certificate, certificate_target)
            self.assertEqual(resolved_private_key, key_target)
        else:
            self.assertEqual(resolved_certificate, certificate)
            self.assertEqual(resolved_private_key, private_key)
        verify_command = mocked_run.call_args_list[-1].args[0]
        self.assertIn("verify", verify_command)
        self.assertIn(str(manage.SYSTEM_CA_BUNDLE), verify_command)

    def test_config_validation_mounts_the_whole_read_only_certbot_config(self):
        config = manage.config_for(self.add_state_user(self.base_state()))
        successful = self.completed()
        with (
            mock.patch.object(manage, "validate_tls_material") as validate_tls,
            mock.patch.object(manage, "run", return_value=successful) as mocked_run,
        ):
            manage.test_config(config)

        validate_tls.assert_called_once_with()
        command = mocked_run.call_args.args[0]
        self.assertEqual(command[:3], ["docker", "run", "--rm"])
        self.assertIn(f"{manage.TLS_CERT_SOURCE_DIR}:{manage.TLS_CERT_CONTAINER_DIR}:ro", command)
        self.assertNotIn(f"{manage.TLS_CERT_SOURCE_DIR}/live", command)

    def test_compose_mounts_certbot_config_and_one_shot_certbot_service(self):
        compose_path = Path(manage.__file__).resolve().parent / "compose.yaml"
        compose = compose_path.read_text(encoding="utf-8")
        self.assertIn("./.runtime/certbot/config:/root/.config/mihomo/certs:ro", compose)
        self.assertIn(manage.CERTBOT_IMAGE, compose)
        self.assertIn("edge_ingress", compose)
        self.assertIn("./.runtime/certbot/config:/etc/letsencrypt:rw", compose)
        self.assertIn("./.runtime/certbot/work:/var/lib/letsencrypt:rw", compose)
        self.assertIn("./.runtime/certbot/logs:/var/log/letsencrypt:rw", compose)
        self.assertNotIn("caddy_caddy_data", compose)


class CertbotCommandTests(RuntimeHarness):
    def test_issue_command_uses_rsa2048_standalone_without_host_publish(self):
        arguments = manage.certbot_issue_arguments("operator@example.test")
        command = manage.certbot_docker_command(arguments)
        self.assertEqual(command[:2], ["docker", "compose"])
        self.assertIn("--profile", command)
        self.assertIn("certbot", command)
        self.assertIn("--name", command)
        self.assertEqual(command[command.index("--name") + 1], manage.CERTBOT_CONTAINER)
        self.assertIn("certonly", command)
        self.assertIn("--standalone", command)
        self.assertEqual(command[command.index("--http-01-port") + 1], "18081")
        self.assertEqual(command[command.index("--http-01-address") + 1], "0.0.0.0")
        self.assertEqual(command[command.index("--key-type") + 1], "rsa")
        self.assertEqual(command[command.index("--rsa-key-size") + 1], "2048")
        self.assertEqual(command[command.index("--preferred-chain") + 1], "ISRG Root X1")
        self.assertNotIn("--publish", command)
        self.assertNotIn("-p", command)
        self.assertNotIn("host", command)

    def test_renew_command_uses_the_same_standalone_contract(self):
        command = manage.certbot_renew_arguments(dry_run=False)
        self.assertEqual(command[0], "renew")
        self.assertIn("--standalone", command)
        self.assertEqual(command[command.index("--http-01-port") + 1], "18081")
        self.assertEqual(command[command.index("--preferred-chain") + 1], "ISRG Root X1")

    def test_command_redaction_hides_registration_email(self):
        email = "operator@example.test"
        command = ["certbot", "--email", email, "--token=not-a-real-token"]
        self.assertNotIn(email, manage.redacted_command(command))
        self.assertNotIn("not-a-real-token", manage.redacted_command(command))
        self.assertNotIn(email, manage.redacted_command_output(command, f"failed for {email}"))

        failed = subprocess.CompletedProcess(
            args=command, returncode=1, stdout="", stderr=f"failed for {email}"
        )
        with mock.patch.object(manage.subprocess, "run", return_value=failed):
            with self.assertRaises(SystemExit) as error:
                manage.run(command)
        self.assertNotIn(email, str(error.exception))
        self.assertNotIn("not-a-real-token", str(error.exception))

    def test_network_and_container_name_preflight_fail_closed(self):
        unavailable = self.completed(returncode=1)
        with mock.patch.object(manage, "run", return_value=unavailable):
            with self.assertRaisesRegex(SystemExit, "network is unavailable"):
                manage.certbot_network_available()

        occupied = self.completed(f"{manage.CERTBOT_CONTAINER}\n")
        with mock.patch.object(manage, "run", return_value=occupied):
            with self.assertRaisesRegex(SystemExit, "name is already in use"):
                manage.certbot_container_name_available()

    def test_caddy_http01_template_is_narrow_and_docker_only(self):
        root = Path(manage.__file__).resolve().parent
        if not (root / "templates" / "certbot-http01.caddyfile").is_file():
            self.skipTest("partial candidate does not include the unchanged Caddy template")
        template = (root / "templates" / "certbot-http01.caddyfile").read_text(encoding="utf-8")
        self.assertIn("host edge-us.konbakuyomu.us", template)
        self.assertIn("path /.well-known/acme-challenge/*", template)
        self.assertIn("reverse_proxy att-certbot-acme:18081", template)
        self.assertNotIn("172.18.0.1", template)
        self.assertNotIn("0.0.0.0", template)

    def test_root_only_systemd_timer_uses_renew_command(self):
        root = Path(manage.__file__).resolve().parent / "systemd"
        if not root.is_dir():
            self.skipTest("partial candidate does not include unchanged systemd files")
        service = (root / "att-proxy-share-certbot-renew.service").read_text(encoding="utf-8")
        timer = (root / "att-proxy-share-certbot-renew.timer").read_text(encoding="utf-8")
        self.assertIn("User=root", service)
        self.assertIn("UMask=0077", service)
        self.assertIn("manage.py renew-tls", service)
        self.assertIn("ReadWritePaths=/opt/codex-stacks/att-proxy-share/.runtime", service)
        self.assertIn("Persistent=true", timer)
        self.assertIn("att-proxy-share-certbot-renew.service", timer)


class TlsRefreshTests(RuntimeHarness):
    def test_unchanged_material_does_not_recreate_mihomo(self):
        state = self.add_state_user(self.base_state())
        with (
            mock.patch.object(manage, "validate_tls_material"),
            mock.patch.object(manage, "tls_material_fingerprint_or_none", return_value="same"),
            mock.patch.object(manage, "load_state", return_value=state) as load_state,
            mock.patch.object(manage, "apply_container") as apply_container,
        ):
            changed, recreated = manage.refresh_tls_after_material_change("same")
        self.assertEqual((changed, recreated), (False, False))
        load_state.assert_not_called()
        apply_container.assert_not_called()

    def test_changed_material_recreates_only_an_active_gateway(self):
        state = self.add_state_user(self.base_state())
        with (
            mock.patch.object(manage, "validate_tls_material"),
            mock.patch.object(manage, "tls_material_fingerprint_or_none", return_value="after"),
            mock.patch.object(manage, "load_state", return_value=state),
            mock.patch.object(manage, "test_config") as test_config,
            mock.patch.object(manage, "apply_container") as apply_container,
        ):
            changed, recreated = manage.refresh_tls_after_material_change("before")
        self.assertEqual((changed, recreated), (True, True))
        test_config.assert_called_once()
        apply_container.assert_called_once_with(True, (manage.DEFAULT_PORT, manage.TLS_PORT))

    def test_changed_material_leaves_disabled_gateway_stopped(self):
        state = self.add_state_user(self.base_state())
        state["enabled"] = False
        with (
            mock.patch.object(manage, "validate_tls_material"),
            mock.patch.object(manage, "tls_material_fingerprint_or_none", return_value="after"),
            mock.patch.object(manage, "load_state", return_value=state),
            mock.patch.object(manage, "apply_container") as apply_container,
        ):
            changed, recreated = manage.refresh_tls_after_material_change("before")
        self.assertEqual((changed, recreated), (True, False))
        apply_container.assert_not_called()


class HandoffRefreshTests(RuntimeHarness):
    def save_state(self, state):
        manage.ensure_runtime()
        manage.write_atomic(manage.STATE_PATH, manage.json_bytes(state))

    def test_handoff_guidance_distinguishes_https_dropdown_and_proxy_tls_switch(self):
        guidance = "\n".join(
            manage.handoff_guidance_lines(self.base_state(), legacy_host="proxy.example.test")
        )

        self.assertIn("client_modes=HTTPS,HTTP+TLS,SOCKS5+TLS", guidance)
        self.assertIn("https_dropdown_protocol=HTTPS", guidance)
        self.assertIn("TLS to proxy, then authenticated HTTP CONNECT.", guidance)
        self.assertIn("proxy_tls_switch_protocols=HTTP,SOCKS5", guidance)
        self.assertIn("select HTTP+TLS or SOCKS5+TLS.", guidance)
        self.assertIn(
            f"Do not select plain SOCKS5 on {manage.TLS_PORT}: it is a protocol mismatch",
            guidance,
        )
        self.assertIn("raw_fallback=true", guidance)
        self.assertIn("port=34567", guidance)
        self.assertNotIn("9443", guidance)
        self.assertNotIn("username=", guidance)
        self.assertNotIn("password=", guidance)

    def test_readme_keeps_roxy_tls_and_legacy_contract_in_sync(self):
        readme = (Path(manage.__file__).resolve().parent / "README.md").read_text(encoding="utf-8")

        self.assertIn(f"`{manage.TLS_PUBLIC_HOST}:{manage.TLS_PORT}`", readme)
        self.assertIn(f"Roxy Browser, select `HTTPS` on `{manage.TLS_PORT}`.", readme)
        self.assertIn("TLS to the proxy followed by authenticated\n  HTTP CONNECT.", readme)
        self.assertIn("select `HTTP` or `SOCKS5` and enable it", readme)
        self.assertIn("`HTTP+TLS` or `SOCKS5+TLS`.", readme)
        self.assertIn(f"Do not select plain `SOCKS5` on `{manage.TLS_PORT}`.", readme)
        self.assertIn(f"`public-host:{manage.DEFAULT_PORT}`", readme)
        self.assertIn("leave `Proxy TLS` off.", readme)
        self.assertNotIn("9443", readme)

    def test_refresh_handoffs_updates_tls_preference_without_stdout_secrets(self):
        state = self.add_state_user(self.base_state())
        self.save_state(state)
        item = state["users"]["alice"]
        handoff = manage.handoff_path("alice")
        manage.write_atomic(handoff, b"legacy handoff\n")

        out = io.StringIO()
        with mock.patch.object(manage, "validate_tls_material") as validate_tls, contextlib.redirect_stdout(out):
            manage.cmd_refresh_handoffs(Namespace())

        output = out.getvalue()
        content = handoff.read_text(encoding="utf-8")
        validate_tls.assert_called_once_with()
        self.assertIn("refreshed_handoffs=1", output)
        self.assertNotIn(item["username"], output)
        self.assertNotIn(item["password"], output)
        self.assertIn("[preferred-mainland]", content)
        self.assertIn(f"host={manage.TLS_PUBLIC_HOST}", content)
        self.assertIn(f"port={manage.TLS_PORT}", content)
        self.assertIn("https_dropdown_protocol=HTTPS", content)
        self.assertIn("proxy_tls_switch_protocols=HTTP,SOCKS5", content)
        self.assertIn("Do not select plain SOCKS5 on 8443", content)
        self.assertIn("proxy_tls=on", content)
        self.assertIn("[legacy]", content)
        self.assertIn("host=proxy.example.test", content)
        self.assertIn(f"port={manage.DEFAULT_PORT}", content)
        self.assertIn("raw_fallback=true", content)
        self.assertIn("proxy_tls=off", content)
        self.assertNotIn("9443", content)
        self.assertIn(item["username"], content)
        self.assertIn(item["password"], content)
        self.assertEqual(self.apply_calls, [])

    def test_reconcile_refreshes_existing_handoffs_and_validates_tls(self):
        state = self.add_state_user(self.base_state())
        self.save_state(state)
        item = state["users"]["alice"]

        out = io.StringIO()
        with self.patch_runtime() as patched, contextlib.redirect_stdout(out):
            manage.cmd_reconcile(Namespace())

        output = out.getvalue()
        content = manage.handoff_path("alice").read_text(encoding="utf-8")
        self.assertIn("handoffs_refreshed=1", output)
        self.assertNotIn(item["username"], output)
        self.assertNotIn(item["password"], output)
        self.assertIn(f"host={manage.TLS_PUBLIC_HOST}", content)
        self.assertIn("proxy_tls=on", content)
        self.assertEqual(self.apply_calls, [True])
        self.assertGreaterEqual(patched["validate_tls_material"].call_count, 2)


class MigrationTests(RuntimeHarness):
    def test_runtime_creates_private_certbot_state_directories(self):
        manage.ensure_runtime()
        for path in (
            manage.CERTBOT_DIR,
            manage.CERTBOT_CONFIG_DIR,
            manage.CERTBOT_WORK_DIR,
            manage.CERTBOT_LOGS_DIR,
        ):
            self.assertTrue(path.is_dir())
            if os.name != "nt":
                self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o700)

    def test_legacy_files_move_once_into_runtime(self):
        manage.PUBLIC_HOST_PATH.unlink()
        legacy_state = {
            "port": manage.DEFAULT_PORT,
            "users": {
                "old": {
                    "username": "att-legacy",
                    "password": "legacy-secret",
                    "created_at": 2,
                }
            },
        }
        legacy_state_path = self.root / "users.json"
        legacy_config_path = self.root / "config.json"
        legacy_host_path = self.root / "public-host"
        legacy_state_path.write_text(json.dumps(legacy_state), encoding="utf-8")
        legacy_config_path.write_text("legacy-config\n", encoding="utf-8")
        legacy_host_path.write_text("legacy.example.test\n", encoding="utf-8")
        state = manage.load_state()
        self.assertIn("old", state["users"])
        self.assertTrue(manage.STATE_PATH.exists())
        self.assertTrue(manage.CONFIG_PATH.exists())
        self.assertTrue(manage.PUBLIC_HOST_PATH.exists())
        self.assertFalse(legacy_state_path.exists())
        self.assertFalse(legacy_config_path.exists())
        self.assertFalse(legacy_host_path.exists())
        if os.name != "nt":
            self.assertEqual(stat.S_IMODE(manage.RUNTIME_DIR.stat().st_mode), 0o700)

        second = manage.load_state()
        self.assertEqual(second, state)

    def test_legacy_conflict_is_rejected_without_moving(self):
        manage.ensure_runtime()
        manage.STATE_PATH.write_text(json.dumps(manage.default_state()), encoding="utf-8")
        legacy_state_path = self.root / "users.json"
        legacy_state_path.write_text(json.dumps(manage.default_state()), encoding="utf-8")
        with self.assertRaises(SystemExit):
            manage.load_state()
        self.assertTrue(legacy_state_path.exists())


if __name__ == "__main__":
    unittest.main()
