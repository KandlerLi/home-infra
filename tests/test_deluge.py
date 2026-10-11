from __future__ import annotations

import unittest
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
ROLE_ROOT = PROJECT_ROOT / "ansible/roles/deluge"
NEXTCLOUD_AIO_ROOT = PROJECT_ROOT / "ansible/roles/nextcloud_aio"


class DelugeRoleTests(unittest.TestCase):
    def test_defaults_define_the_service_account_and_directories(self) -> None:
        defaults = (ROLE_ROOT / "defaults/main.yml").read_text(encoding="utf-8")

        self.assertIn("deluge_service_user: deluge", defaults)
        self.assertIn("deluge_service_group: deluge", defaults)
        self.assertIn("deluge_downloads_dir: /mnt/black-hdd/downloads", defaults)
        self.assertIn("deluge_config_dir: /mnt/black-hdd/deluge-config", defaults)

    def test_tasks_run_unconditionally_now_container_is_retired(self) -> None:
        defaults = (ROLE_ROOT / "defaults/main.yml").read_text(encoding="utf-8")
        tasks = (ROLE_ROOT / "tasks/main.yml").read_text(encoding="utf-8")

        self.assertNotIn("deluge_enabled", defaults)
        self.assertNotIn("deluge_enabled", tasks)
        self.assertNotIn("linuxserver/deluge", tasks)
        self.assertNotIn("docker_container", tasks)

    def test_service_account_uid_gid_is_validated_against_k3s_apps(self) -> None:
        # k3s-apps hardcodes PUID 993/PGID 986, so a different uid must fail here.
        tasks = (ROLE_ROOT / "tasks/main.yml").read_text(encoding="utf-8")

        self.assertIn("Create Deluge service account", tasks)
        self.assertIn('deluge_uid == "993"', tasks)
        self.assertIn('deluge_gid == "986"', tasks)

    def test_downloads_and_config_directories_are_kept_separate(self) -> None:
        tasks = (ROLE_ROOT / "tasks/main.yml").read_text(encoding="utf-8")

        self.assertIn("deluge_downloads_dir != deluge_config_dir", tasks)

    def test_downloads_directory_is_world_readable_but_config_is_not(self) -> None:
        # Nextcloud reads as www-data, outside the deluge group, so others need read access.
        tasks = (ROLE_ROOT / "tasks/main.yml").read_text(encoding="utf-8")

        downloads_index = tasks.index("path: \"{{ deluge_downloads_dir }}\"")
        config_index = tasks.index(
            "path: \"{{ deluge_config_dir }}\"", downloads_index
        )
        downloads_block = tasks[downloads_index:config_index]
        config_block = tasks[config_index : config_index + 200]

        self.assertIn('mode: "0755"', downloads_block)
        self.assertIn('mode: "0750"', config_block)

    def test_nextcloud_process_gets_write_access_via_acl_not_wider_mode(
        self,
    ) -> None:
        # www-data gets write via an ACL, since others only have read+execute.
        defaults = (ROLE_ROOT / "defaults/main.yml").read_text(encoding="utf-8")
        tasks = (ROLE_ROOT / "tasks/main.yml").read_text(encoding="utf-8")

        self.assertIn("deluge_downloads_nextcloud_user: www-data", defaults)
        self.assertNotIn('mode: "0757"', tasks)
        self.assertNotIn('mode: "0777"', tasks)

        acl_tasks = tasks.count("ansible.posix.acl:")
        self.assertEqual(
            acl_tasks,
            2,
            "expected one default-ACL task (future subfolders) and one"
            " recursive access-ACL task (existing content)",
        )
        self.assertIn("default: true", tasks)
        self.assertIn("recursive: true", tasks)
        self.assertIn(
            'entity: "{{ deluge_downloads_nextcloud_user }}"',
            tasks,
        )


class NextcloudAioMountTests(unittest.TestCase):
    def test_mount_is_disabled_by_default(self) -> None:
        defaults = (NEXTCLOUD_AIO_ROOT / "defaults/main.yml").read_text(
            encoding="utf-8"
        )

        self.assertIn('nextcloud_aio_mount_dir: ""', defaults)
        self.assertIn('nextcloud_aio_mount_applicable_user: ""', defaults)

    def test_mount_env_var_and_bind_mount_are_conditional(self) -> None:
        tasks = (NEXTCLOUD_AIO_ROOT / "tasks/main.yml").read_text(encoding="utf-8")

        self.assertIn("NEXTCLOUD_MOUNT", tasks)
        self.assertIn("nextcloud_aio_mount_dir | length > 0", tasks)
        self.assertIn("volumes: strict", tasks)

    def test_external_storage_visibility_requires_an_explicit_user(self) -> None:
        tasks = (NEXTCLOUD_AIO_ROOT / "tasks/main.yml").read_text(encoding="utf-8")

        self.assertIn("files_external:create", tasks)
        self.assertIn("files_external:applicable", tasks)
        self.assertIn("nextcloud_aio_mount_applicable_user | length > 0", tasks)

    def test_downloads_mount_is_rescanned_so_delete_permission_is_current(
        self,
    ) -> None:
        # Nextcloud caches permissions until the mount is rescanned.
        tasks = (NEXTCLOUD_AIO_ROOT / "tasks/main.yml").read_text(encoding="utf-8")

        applicable_index = tasks.index("Make the downloads mount visible")
        rescan_index = tasks.index("files:scan", applicable_index)
        self.assertGreater(
            rescan_index,
            applicable_index,
            "the rescan must run after the mount is registered/made visible",
        )

        rescan_block = tasks[applicable_index:]
        self.assertIn(
            "--path=/{{ nextcloud_aio_mount_applicable_user }}"
            "/files/{{ nextcloud_aio_mount_point_name }}",
            rescan_block,
        )


if __name__ == "__main__":
    unittest.main()
