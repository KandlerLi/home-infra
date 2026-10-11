from __future__ import annotations

import unittest
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
ROLE_ROOT = PROJECT_ROOT / "ansible/roles/nextcloud_aio"


class NextcloudAioApacheBindingTests(unittest.TestCase):
    def test_apache_ip_binding_defaults_to_loopback(self) -> None:
        defaults = (ROLE_ROOT / "defaults/main.yml").read_text(encoding="utf-8")

        self.assertIn("nextcloud_aio_apache_ip_binding: 127.0.0.1", defaults)

    def test_apache_ip_binding_allows_only_loopback_or_the_k3s_vm_address(
        self,
    ) -> None:
        # The homeserver's k3s-network address, the only allowed alternative to loopback.
        tasks = (ROLE_ROOT / "tasks/main.yml").read_text(encoding="utf-8")

        self.assertIn(
            'nextcloud_aio_apache_ip_binding == "127.0.0.1"\n'
            '            or nextcloud_aio_apache_ip_binding == '
            '"192.168.101.1"',
            tasks,
        )

    def test_still_refuses_an_arbitrary_binding_address(self) -> None:
        # A closed allowlist of exactly two values.
        tasks = (ROLE_ROOT / "tasks/main.yml").read_text(encoding="utf-8")

        self.assertEqual(
            tasks.count("nextcloud_aio_apache_ip_binding =="), 2
        )


class ApacheNetworkReconnectTests(unittest.TestCase):
    # Apache drops off the AIO Docker network after reboots, so a script reconnects it.
    def test_script_is_idempotent_and_bounded(self) -> None:
        script = (ROLE_ROOT / "files/reconnect_apache_network.sh").read_text(
            encoding="utf-8"
        )

        self.assertIn("set -Eeuo pipefail", script)
        # Already connected is a clean no-op.
        self.assertIn("already connected to", script)
        # Apache never appearing is a different problem, so the script exits cleanly.
        self.assertIn("WAIT_TIMEOUT_SECONDS", script)
        self.assertIn("docker network connect", script)

    def test_systemd_unit_gives_real_headroom_over_the_scripts_own_wait(
        self,
    ) -> None:
        unit = (
            ROLE_ROOT
            / "templates/nextcloud-aio-apache-network-fix.service.j2"
        ).read_text(encoding="utf-8")
        script = (ROLE_ROOT / "files/reconnect_apache_network.sh").read_text(
            encoding="utf-8"
        )

        script_wait_seconds = int(
            script.split("WAIT_TIMEOUT_SECONDS=", 1)[1].split("\n", 1)[0]
        )
        unit_timeout_seconds = int(
            unit.split("TimeoutStartSec=", 1)[1].split("\n", 1)[0]
        )

        self.assertGreater(unit_timeout_seconds, script_wait_seconds)
        self.assertIn("After=docker.service", unit)
        self.assertIn("WantedBy=multi-user.target", unit)

    def test_reruns_on_every_apply_not_just_the_first(self) -> None:
        # restarted, not started: started is a no-op on an exited oneshot.
        tasks = (ROLE_ROOT / "tasks/main.yml").read_text(encoding="utf-8")

        reconnect_task = tasks.split(
            "Run the Apache network-reconnect check", 1
        )[1]
        self.assertIn("state: restarted", reconnect_task)

    def test_installed_with_the_default_path_used_by_the_task_and_unit(
        self,
    ) -> None:
        defaults = (ROLE_ROOT / "defaults/main.yml").read_text(
            encoding="utf-8"
        )
        tasks = (ROLE_ROOT / "tasks/main.yml").read_text(encoding="utf-8")
        unit = (
            ROLE_ROOT
            / "templates/nextcloud-aio-apache-network-fix.service.j2"
        ).read_text(encoding="utf-8")

        self.assertIn("nextcloud_aio_apache_network_fix_install_path", defaults)
        self.assertIn(
            "dest: \"{{ nextcloud_aio_apache_network_fix_install_path }}\"",
            tasks,
        )
        self.assertIn(
            "ExecStart={{ nextcloud_aio_apache_network_fix_install_path }}",
            unit,
        )

    def test_install_directory_is_created_before_the_script_is_copied(
        self,
    ) -> None:
        # copy doesn't create missing parent directories.
        tasks = (ROLE_ROOT / "tasks/main.yml").read_text(encoding="utf-8")

        self.assertLess(
            tasks.index("Create Nextcloud AIO Apache network-reconnect install directory"),
            tasks.index("Install Nextcloud AIO Apache network-reconnect script"),
        )

        create_dir_task = tasks.split(
            "Create Nextcloud AIO Apache network-reconnect install directory",
            1,
        )[1].split("- name:", 1)[0]
        self.assertIn("state: directory", create_dir_task)
        self.assertIn(
            "path: \"{{ nextcloud_aio_apache_network_fix_install_dir }}\"",
            create_dir_task,
        )


if __name__ == "__main__":
    unittest.main()
