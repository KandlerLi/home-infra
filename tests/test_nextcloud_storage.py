from __future__ import annotations

import unittest
from pathlib import Path

import yaml

PROJECT_ROOT = Path(__file__).resolve().parents[1]
ROLE_ROOT = PROJECT_ROOT / "ansible/roles/nextcloud_storage"


class NextcloudStorageRoleTests(unittest.TestCase):
    def test_disabled_by_default(self) -> None:
        defaults = (ROLE_ROOT / "defaults/main.yml").read_text(encoding="utf-8")

        self.assertIn("nextcloud_storage_enabled: false", defaults)

    def test_test_dir_is_not_the_live_data_dir(self) -> None:
        defaults = yaml.safe_load(
            (ROLE_ROOT / "defaults/main.yml").read_text(encoding="utf-8")
        )

        test_dir = defaults["nextcloud_storage_test_data_dir"]
        self.assertTrue(test_dir.startswith("/mnt/black-hdd/"))
        self.assertNotEqual(test_dir.rstrip("/"), "/mnt/black-hdd/nextcloud")

    def test_runs_before_nfs_server(self) -> None:
        # exportfs -ra fails on an export whose directory doesn't exist.
        site = (PROJECT_ROOT / "ansible/playbooks/site.yml").read_text(
            encoding="utf-8"
        )

        self.assertLess(
            site.index("- nextcloud_storage"), site.index("- nfs_server")
        )


if __name__ == "__main__":
    unittest.main()
