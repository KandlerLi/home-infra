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

    def test_restic_is_opt_in_and_never_reinitialises(self) -> None:
        defaults = (ROLE_ROOT / "defaults/main.yml").read_text(encoding="utf-8")
        tasks = (ROLE_ROOT / "tasks/main.yml").read_text(encoding="utf-8")

        self.assertIn("nextcloud_storage_restic_enabled: false", defaults)
        # Only "repository does not exist" may lead to init; a wrong password (12) must fail.
        self.assertIn("rc not in [0, 10]", tasks)
        self.assertIn("nextcloud_storage_restic_config.rc == 10", tasks)

    def test_restic_unit_runs_unprivileged_and_marks_only_success(self) -> None:
        from jinja2 import Environment, FileSystemLoader

        defaults = yaml.safe_load(
            (ROLE_ROOT / "defaults/main.yml").read_text(encoding="utf-8")
        )
        env = Environment(loader=FileSystemLoader(str(ROLE_ROOT / "templates")))
        values = {k: v for k, v in defaults.items()}
        # Resolve the defaults' own references the way Ansible would.
        for _ in range(3):
            values = {
                k: yaml.safe_load(env.from_string(yaml.safe_dump(v)).render(**values))
                for k, v in values.items()
            }
        unit = env.get_template("nextcloud-restic.service.j2").render(**values)
        timer = env.get_template("nextcloud-restic.timer.j2").render(**values)

        self.assertIn("User=www-data", unit)
        self.assertIn("ExecStartPost=/usr/bin/touch /var/lib/nextcloud-restic/last-success", unit)
        self.assertIn("ReadWritePaths=/mnt/red-hdd/nextcloud-restic", unit)
        self.assertIn("--exclude=/mnt/black-hdd/nextcloud/appdata_*/preview", unit)
        self.assertIn(" /mnt/black-hdd/nextcloud /mnt/red-hdd/nextcloud-backup", unit)
        self.assertIn("OnCalendar=*-*-* 03:45:00 UTC", timer)

    def test_monitoring_watches_both_backups(self) -> None:
        monitoring = yaml.safe_load(
            (PROJECT_ROOT / "ansible/roles/monitoring/defaults/main.yml").read_text(
                encoding="utf-8"
            )
        )
        role = yaml.safe_load(
            (ROLE_ROOT / "defaults/main.yml").read_text(encoding="utf-8")
        )

        markers = {m["name"]: m["path"] for m in monitoring["monitoring_backup_markers"]}
        self.assertEqual(markers["nextcloud-restic"], role["nextcloud_storage_restic_marker"])
        self.assertIn("nextcloud-backup", monitoring["monitoring_backup_k3s_cronjobs"])


if __name__ == "__main__":
    unittest.main()
