from __future__ import annotations

import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "scripts"))

from sync_secrets_to_pass import (
    SECRET_MAPPINGS,
    STATIC_MAPPINGS,
    VAR_MAPPINGS,
    fetch_secret,
    resolve_var,
    sync_entry,
)


class ResolveVarTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.tmp_path = Path(self._tmp.name)

    def _write(self, name: str, content: str) -> Path:
        path = self.tmp_path / name
        path.write_text(content)
        return path

    def test_uses_inventory_override_when_present(self) -> None:
        inventory = self._write("inventory.yml", "monitoring_grafana_admin_user: someone-else\n")
        role_defaults = self._write("defaults.yml", "monitoring_grafana_admin_user: admin\n")

        self.assertEqual(resolve_var("monitoring_grafana_admin_user", inventory, role_defaults), "someone-else")

    def test_falls_back_to_role_default_when_not_overridden(self) -> None:
        inventory = self._write("inventory.yml", "some_other_var: true\n")
        role_defaults = self._write("defaults.yml", "monitoring_grafana_admin_user: admin\n")

        self.assertEqual(resolve_var("monitoring_grafana_admin_user", inventory, role_defaults), "admin")

    def test_raises_when_neither_source_has_it(self) -> None:
        inventory = self._write("inventory.yml", "some_other_var: true\n")
        role_defaults = self._write("defaults.yml", "some_role_var: 1\n")

        with self.assertRaises(ValueError):
            resolve_var("monitoring_grafana_admin_user", inventory, role_defaults)


class FetchSecretTests(unittest.TestCase):
    def test_returns_decoded_json_value(self) -> None:
        fake_result = subprocess.CompletedProcess(
            args=[],
            returncode=0,
            stdout='{"monitoring_grafana_admin_password": "hunter3"}',
        )
        with patch("sync_secrets_to_pass.subprocess.run", return_value=fake_result) as run:
            secret = fetch_secret("home-infra/grafana")

        self.assertEqual(secret["monitoring_grafana_admin_password"], "hunter3")
        # Region is explicit, not left to the CLI's own default -- found
        # live 2026-09-09 that it doesn't match where these secrets
        # actually live, 404ing instead of erroring clearly.
        called_args = run.call_args.args[0]
        self.assertIn("--region", called_args)
        self.assertIn("eu-central-1", called_args)
        self.assertIn("home-infra/grafana", called_args)

    def test_retries_once_then_succeeds(self) -> None:
        # Found live 2026-09-09, repeatedly: this workspace's own
        # `aws login` credential flow occasionally fails a
        # CreateOAuth2Token exchange that a bare retry a moment later
        # reliably clears.
        failure = subprocess.CompletedProcess(args=[], returncode=254, stdout="", stderr="transient auth error")
        success = subprocess.CompletedProcess(
            args=[], returncode=0, stdout='{"shared_ingress_auth_password": "hunter2"}'
        )
        with patch("sync_secrets_to_pass.subprocess.run", side_effect=[failure, success]):
            secret = fetch_secret("home-infra/ingress")

        self.assertEqual(secret["shared_ingress_auth_password"], "hunter2")

    def test_raises_with_the_real_error_after_retries_exhausted(self) -> None:
        failure = subprocess.CompletedProcess(
            args=[], returncode=254, stdout="", stderr="AccessDeniedException: real permission problem"
        )
        with patch("sync_secrets_to_pass.subprocess.run", return_value=failure):
            with self.assertRaisesRegex(RuntimeError, "real permission problem"):
                fetch_secret("home-infra/ingress")


class SyncEntryTests(unittest.TestCase):
    def test_skips_when_already_in_sync(self) -> None:
        with patch("sync_secrets_to_pass.pass_show", return_value="hunter2"), patch(
            "sync_secrets_to_pass.pass_insert"
        ) as insert:
            changed = sync_entry("ingress/password", "hunter2", check_only=False)

        self.assertFalse(changed)
        insert.assert_not_called()

    def test_updates_when_drifted(self) -> None:
        with patch("sync_secrets_to_pass.pass_show", return_value="old-value"), patch(
            "sync_secrets_to_pass.pass_insert"
        ) as insert:
            changed = sync_entry("ingress/password", "hunter2", check_only=False)

        self.assertTrue(changed)
        insert.assert_called_once_with("ingress/password", "hunter2")

    def test_treats_a_missing_entry_as_drift(self) -> None:
        with patch("sync_secrets_to_pass.pass_show", return_value=None), patch(
            "sync_secrets_to_pass.pass_insert"
        ) as insert:
            changed = sync_entry("ingress/password", "hunter2", check_only=False)

        self.assertTrue(changed)
        insert.assert_called_once_with("ingress/password", "hunter2")

    def test_check_only_reports_drift_without_writing(self) -> None:
        with patch("sync_secrets_to_pass.pass_show", return_value="old-value"), patch(
            "sync_secrets_to_pass.pass_insert"
        ) as insert:
            changed = sync_entry("ingress/password", "hunter2", check_only=True)

        self.assertTrue(changed)
        insert.assert_not_called()


class MappingScopeTests(unittest.TestCase):
    def test_only_human_login_credentials_are_mapped(self) -> None:
        # Deliberately narrow scope: only secrets a human actually types
        # into a login prompt or browser. Service-to-service secrets a
        # container reads on its own don't belong in a personal password
        # manager -- mirroring them would just add another place for the
        # same secret to leak from with no real convenience benefit.
        mapped_keys = {key for _, key, _ in SECRET_MAPPINGS}
        excluded_keys = {
            "shared_ingress_auth_password_hash",
            "home_agent_openai_api_key",
            "monitoring_ses_smtp_username",
            "monitoring_ses_smtp_password",
            "monitoring_ntfy_topic",
            "github_runner_github_token",
            "blocky_postgres_password",
            # deluge_web_password has no live consumer anywhere any more
            # (Deluge's k3s copy hardcodes a blank password now that
            # Authelia gates torrent.jkandler.de) -- deliberately dropped
            # from this script's own mapping too, not carried forward.
            "deluge_web_password",
        }

        self.assertTrue(mapped_keys.isdisjoint(excluded_keys))
        self.assertEqual(
            mapped_keys,
            {
                "shared_ingress_auth_password",
                "monitoring_grafana_admin_password",
            },
        )

    def test_every_mapping_targets_a_distinct_pass_entry(self) -> None:
        entries = (
            [entry for _, _, entry in SECRET_MAPPINGS]
            + [entry for _, entry, _ in VAR_MAPPINGS]
            + [entry for entry, _ in STATIC_MAPPINGS]
        )

        self.assertEqual(len(entries), len(set(entries)))


if __name__ == "__main__":
    unittest.main()
