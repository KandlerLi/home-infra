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
        # This role used to gate everything behind deluge_enabled (opt-in
        # Docker container). Deluge itself now runs as a k3s-native copy
        # (infra/k3s-apps) -- this role only keeps the host prerequisites
        # (service account, directories, ACL) that copy still depends on,
        # so those need to always run, not be conditional on a flag that
        # no longer exists.
        defaults = (ROLE_ROOT / "defaults/main.yml").read_text(encoding="utf-8")
        tasks = (ROLE_ROOT / "tasks/main.yml").read_text(encoding="utf-8")

        self.assertNotIn("deluge_enabled", defaults)
        self.assertNotIn("deluge_enabled", tasks)
        self.assertNotIn("linuxserver/deluge", tasks)
        self.assertNotIn("docker_container", tasks)

    def test_service_account_uid_gid_is_validated_against_k3s_apps(self) -> None:
        # infra/k3s-apps' Deployment hardcodes PUID=993/PGID=986 rather
        # than looking them up dynamically -- a homeserver rebuild that
        # ever assigned this account a different uid/gid would otherwise
        # let the k3s copy silently write into these directories as the
        # wrong uid instead of failing loudly.
        tasks = (ROLE_ROOT / "tasks/main.yml").read_text(encoding="utf-8")

        self.assertIn("Create Deluge service account", tasks)
        self.assertIn('deluge_uid == "993"', tasks)
        self.assertIn('deluge_gid == "986"', tasks)

    def test_downloads_and_config_directories_are_kept_separate(self) -> None:
        tasks = (ROLE_ROOT / "tasks/main.yml").read_text(encoding="utf-8")

        self.assertIn("deluge_downloads_dir != deluge_config_dir", tasks)

    def test_downloads_directory_is_world_readable_but_config_is_not(self) -> None:
        # Confirmed live: Nextcloud's external storage mount reads this
        # path as its own container's runtime uid (www-data, not a member
        # of the deluge group), so with the downloads directory at 0750
        # ("other" gets no permissions at all) it got EACCES and showed an
        # empty folder even though Deluge had already written real files
        # into 0755 per-torrent subfolders underneath -- the top-level
        # directory itself was the only thing blocking visibility.
        # deluge_config_dir holds session state/password hashes and must
        # stay private, so only downloads gets the wider mode.
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
        # Confirmed live: 0755's "other" bits are read+execute only, so
        # Nextcloud (running as www-data, uid 33, which also exists as a
        # real host account -- Docker shares the host uid namespace here)
        # could list and open files but got EACCES deleting/renaming them,
        # since Unix delete/rename needs write on the *containing*
        # directory. Fixed via ACL grants scoped to that one account, not
        # by widening "other" to rwx (0757), which would let any process on
        # the host write here, not just Nextcloud's.
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
        # Confirmed live: fixing the host ACL alone wasn't enough --
        # Nextcloud's UI kept showing no delete option (checked from a
        # fresh, uncached browser profile) until this mount was explicitly
        # rescanned, since Nextcloud caches per-file permissions from the
        # last scan rather than checking the filesystem live.
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
