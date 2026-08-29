from __future__ import annotations

import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "scripts"))

from sync_ingress_credential_to_pass import (
    decrypt_password,
    resolve_username,
    sync_entry,
)


class ResolveUsernameTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.tmp_path = Path(self._tmp.name)

    def _write(self, name: str, content: str) -> Path:
        path = self.tmp_path / name
        path.write_text(content)
        return path

    def test_uses_inventory_override_when_present(self) -> None:
        inventory = self._write("inventory.yml", "shared_ingress_auth_username: someone-else\n")
        role_defaults = self._write("defaults.yml", "shared_ingress_auth_username: julian\n")

        self.assertEqual(resolve_username(inventory, role_defaults), "someone-else")

    def test_falls_back_to_role_default_when_not_overridden(self) -> None:
        inventory = self._write("inventory.yml", "some_other_var: true\n")
        role_defaults = self._write("defaults.yml", "shared_ingress_auth_username: julian\n")

        self.assertEqual(resolve_username(inventory, role_defaults), "julian")

    def test_raises_when_neither_source_has_it(self) -> None:
        inventory = self._write("inventory.yml", "some_other_var: true\n")
        role_defaults = self._write("defaults.yml", "some_role_var: 1\n")

        with self.assertRaises(ValueError):
            resolve_username(inventory, role_defaults)


class DecryptPasswordTests(unittest.TestCase):
    def test_returns_the_decrypted_password(self) -> None:
        fake_result = subprocess.CompletedProcess(
            args=[], returncode=0, stdout="shared_ingress_auth_password: hunter2\n"
        )
        with patch("sync_ingress_credential_to_pass.subprocess.run", return_value=fake_result):
            self.assertEqual(decrypt_password(Path("secrets.sops.yml")), "hunter2")

    def test_raises_when_the_key_is_missing(self) -> None:
        fake_result = subprocess.CompletedProcess(args=[], returncode=0, stdout="other_key: value\n")
        with patch("sync_ingress_credential_to_pass.subprocess.run", return_value=fake_result):
            with self.assertRaises(ValueError):
                decrypt_password(Path("secrets.sops.yml"))


class SyncEntryTests(unittest.TestCase):
    def test_skips_when_already_in_sync(self) -> None:
        with patch("sync_ingress_credential_to_pass.pass_show", return_value="hunter2"), patch(
            "sync_ingress_credential_to_pass.pass_insert"
        ) as insert:
            changed = sync_entry("ingress/password", "hunter2", check_only=False)

        self.assertFalse(changed)
        insert.assert_not_called()

    def test_updates_when_drifted(self) -> None:
        with patch("sync_ingress_credential_to_pass.pass_show", return_value="old-value"), patch(
            "sync_ingress_credential_to_pass.pass_insert"
        ) as insert:
            changed = sync_entry("ingress/password", "hunter2", check_only=False)

        self.assertTrue(changed)
        insert.assert_called_once_with("ingress/password", "hunter2")

    def test_treats_a_missing_entry_as_drift(self) -> None:
        with patch("sync_ingress_credential_to_pass.pass_show", return_value=None), patch(
            "sync_ingress_credential_to_pass.pass_insert"
        ) as insert:
            changed = sync_entry("ingress/password", "hunter2", check_only=False)

        self.assertTrue(changed)
        insert.assert_called_once_with("ingress/password", "hunter2")

    def test_check_only_reports_drift_without_writing(self) -> None:
        with patch("sync_ingress_credential_to_pass.pass_show", return_value="old-value"), patch(
            "sync_ingress_credential_to_pass.pass_insert"
        ) as insert:
            changed = sync_entry("ingress/password", "hunter2", check_only=True)

        self.assertTrue(changed)
        insert.assert_not_called()


if __name__ == "__main__":
    unittest.main()
