from __future__ import annotations

import unittest
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]


class NextcloudToolsAnsibleTests(unittest.TestCase):
    def test_defaults_are_disabled_and_loopback_only(self) -> None:
        defaults = (
            PROJECT_ROOT / "ansible/roles/nextcloud_tools/defaults/main.yml"
        ).read_text()

        self.assertIn("nextcloud_tools_enabled: false", defaults)
        self.assertIn("nextcloud_tools_endpoint_host: 127.0.0.1", defaults)
        self.assertIn("nextcloud_tools_endpoint_port: 11000", defaults)
        self.assertIn("nextcloud_tools_allowed_root: AI Workspace", defaults)

    def test_service_is_network_and_process_restricted(self) -> None:
        unit = (
            PROJECT_ROOT
            / "ansible/roles/nextcloud_tools/templates/nextcloud-tools.service.j2"
        ).read_text()

        self.assertIn("IPAddressDeny=any", unit)
        self.assertIn("IPAddressAllow=127.0.0.1", unit)
        self.assertIn("RestrictAddressFamilies=AF_UNIX AF_INET", unit)
        self.assertIn("NoNewPrivileges=true", unit)
        self.assertIn("CapabilityBoundingSet=", unit)

    def test_agent_receives_socket_but_not_nextcloud_credentials(self) -> None:
        tasks = (
            PROJECT_ROOT / "ansible/roles/home_agent/tasks/main.yml"
        ).read_text()
        service_tasks = (
            PROJECT_ROOT / "ansible/roles/nextcloud_tools/tasks/main.yml"
        ).read_text()

        self.assertIn("/run/nextcloud-tools:ro", tasks)
        self.assertIn("NEXTCLOUD_TOOLS_SOCKET", tasks)
        self.assertNotIn("NEXTCLOUD_APP_PASSWORD", tasks)
        self.assertNotIn("nextcloud_tools_app_password_path", tasks)
        self.assertIn('owner: "{{ nextcloud_tools_service_user }}"', service_tasks)
        self.assertIn('mode: "0400"', service_tasks)

    def test_service_implements_no_webdav_write_methods(self) -> None:
        service = (
            PROJECT_ROOT
            / "ansible/roles/nextcloud_tools/files/nextcloud_tools_service.py"
        ).read_text()

        for method in ('"PUT"', '"DELETE"', '"MOVE"', '"COPY"', '"MKCOL"'):
            with self.subTest(method=method):
                self.assertNotIn(method, service)

    def test_disable_playbook_detaches_agent_before_stopping_service(self) -> None:
        playbook = (
            PROJECT_ROOT / "ansible/playbooks/disable-nextcloud-tools.yml"
        ).read_text()

        detach_position = playbook.index("home_agent_nextcloud_tools_enabled: false")
        stop_position = playbook.index("Stop and disable Nextcloud tools service")
        self.assertLess(detach_position, stop_position)


if __name__ == "__main__":
    unittest.main()
