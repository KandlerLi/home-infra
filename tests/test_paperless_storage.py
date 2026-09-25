from __future__ import annotations

import unittest
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
ROLE_ROOT = PROJECT_ROOT / "ansible/roles/paperless_storage"


class PaperlessStorageRoleTests(unittest.TestCase):
    def test_disabled_by_default(self) -> None:
        defaults = (ROLE_ROOT / "defaults/main.yml").read_text(encoding="utf-8")

        self.assertIn("paperless_storage_enabled: false", defaults)

    def test_live_data_on_black_hdd_backup_on_red_hdd(self) -> None:
        defaults = (ROLE_ROOT / "defaults/main.yml").read_text(encoding="utf-8")

        self.assertIn("paperless_storage_media_dir: /mnt/black-hdd/", defaults)
        self.assertIn("paperless_storage_export_dir: /mnt/red-hdd/", defaults)

    def test_uid_and_gid_are_pinned(self) -> None:
        # infra/k3s-apps hardcodes these, so the account must never be
        # left to the system allocator.
        tasks = (ROLE_ROOT / "tasks/main.yml").read_text(encoding="utf-8")

        self.assertIn('uid: "{{ paperless_storage_uid }}"', tasks)
        self.assertIn('gid: "{{ paperless_storage_gid }}"', tasks)

    def test_runs_before_nfs_server(self) -> None:
        # exportfs -ra fails on an export whose directory doesn't exist.
        site = (PROJECT_ROOT / "ansible/playbooks/site.yml").read_text(
            encoding="utf-8"
        )

        self.assertLess(
            site.index("- paperless_storage"), site.index("- nfs_server")
        )


if __name__ == "__main__":
    unittest.main()
