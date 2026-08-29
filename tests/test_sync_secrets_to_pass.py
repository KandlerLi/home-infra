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
    VAR_MAPPINGS,
    decrypt_secrets,
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


class DecryptSecretsTests(unittest.TestCase):
    def test_returns_every_decrypted_key(self) -> None:
        fake_result = subprocess.CompletedProcess(
            args=[],
            returncode=0,
            stdout="deluge_web_password: hunter2\nmonitoring_grafana_admin_password: hunter3\n",
        )
        with patch("sync_secrets_to_pass.subprocess.run", return_value=fake_result):
            secrets = decrypt_secrets(Path("secrets.sops.yml"))

        self.assertEqual(secrets["deluge_web_password"], "hunter2")
        self.assertEqual(secrets["monitoring_grafana_admin_password"], "hunter3")


class SyncEntryTests(unittest.TestCase):
    def test_skips_when_already_in_sync(self) -> None:
        with patch("sync_secrets_to_pass.pass_show", return_value="hunter2"), patch(
            "sync_secrets_to_pass.pass_insert"
        ) as insert:
            changed = sync_entry("deluge/password", "hunter2", check_only=False)

        self.assertFalse(changed)
        insert.assert_not_called()

    def test_updates_when_drifted(self) -> None:
        with patch("sync_secrets_to_pass.pass_show", return_value="old-value"), patch(
            "sync_secrets_to_pass.pass_insert"
        ) as insert:
            changed = sync_entry("deluge/password", "hunter2", check_only=False)

        self.assertTrue(changed)
        insert.assert_called_once_with("deluge/password", "hunter2")

    def test_treats_a_missing_entry_as_drift(self) -> None:
        with patch("sync_secrets_to_pass.pass_show", return_value=None), patch(
            "sync_secrets_to_pass.pass_insert"
        ) as insert:
            changed = sync_entry("deluge/password", "hunter2", check_only=False)

        self.assertTrue(changed)
        insert.assert_called_once_with("deluge/password", "hunter2")

    def test_check_only_reports_drift_without_writing(self) -> None:
        with patch("sync_secrets_to_pass.pass_show", return_value="old-value"), patch(
            "sync_secrets_to_pass.pass_insert"
        ) as insert:
            changed = sync_entry("deluge/password", "hunter2", check_only=True)

        self.assertTrue(changed)
        insert.assert_not_called()


class MappingScopeTests(unittest.TestCase):
    def test_only_human_login_credentials_are_mapped(self) -> None:
        # Deliberately narrow scope: only secrets a human actually types
        # into a login prompt or browser. Service-to-service secrets a
        # container reads on its own don't belong in a personal password
        # manager -- mirroring them would just add another place for the
        # same secret to leak from with no real convenience benefit.
        mapped_keys = {key for key, _ in SECRET_MAPPINGS}
        excluded_keys = {
            "shared_ingress_auth_password_hash",
            "home_agent_openai_api_key",
            "monitoring_ses_smtp_username",
            "monitoring_ses_smtp_password",
            "monitoring_ntfy_topic",
            "github_runner_github_token",
            "blocky_postgres_password",
        }

        self.assertTrue(mapped_keys.isdisjoint(excluded_keys))
        self.assertEqual(
            mapped_keys,
            {
                "shared_ingress_auth_password",
                "deluge_web_password",
                "monitoring_grafana_admin_password",
            },
        )

    def test_every_mapping_targets_a_distinct_pass_entry(self) -> None:
        entries = [entry for _, entry in SECRET_MAPPINGS] + [entry for _, entry, _ in VAR_MAPPINGS]

        self.assertEqual(len(entries), len(set(entries)))


if __name__ == "__main__":
    unittest.main()
