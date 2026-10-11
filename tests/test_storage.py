from __future__ import annotations

import unittest
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
ROLE_ROOT = PROJECT_ROOT / "ansible/roles/storage"


class StorageRoleTests(unittest.TestCase):
    def test_mount_point_matches_the_declared_production_path(self) -> None:
        # /mnt/black-hdd is production data, so the default is pinned.
        defaults = (ROLE_ROOT / "defaults/main.yml").read_text(encoding="utf-8")

        self.assertIn('black_hdd_mount_point: "/mnt/black-hdd"', defaults)

    def test_mount_uses_a_stable_uuid_not_a_device_path(self) -> None:
        # A UUID is stable; /dev/sdX paths can change.
        tasks = (ROLE_ROOT / "tasks/main.yml").read_text(encoding="utf-8")

        self.assertIn('src: "UUID={{ black_hdd_device_uuid }}"', tasks)

    def test_mount_is_actually_mounted_not_just_an_fstab_entry(self) -> None:
        # mounted, not present: present only writes fstab.
        tasks = (ROLE_ROOT / "tasks/main.yml").read_text(encoding="utf-8")

        mount_task = tasks.split("Mount black HDD", 1)[1]
        self.assertIn("state: mounted", mount_task)

    def test_mount_point_directory_is_created_before_mounting(self) -> None:
        tasks = (ROLE_ROOT / "tasks/main.yml").read_text(encoding="utf-8")

        self.assertLess(
            tasks.index("Ensure black HDD mount point exists"),
            tasks.index("Mount black HDD"),
        )

    def test_mount_point_mode_sets_the_setgid_bit(self) -> None:
        # 02770: the setgid bit makes new entries inherit the group.
        defaults = (ROLE_ROOT / "defaults/main.yml").read_text(encoding="utf-8")

        self.assertIn('black_hdd_mode: "02770"', defaults)


if __name__ == "__main__":
    unittest.main()
