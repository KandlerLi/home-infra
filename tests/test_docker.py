from __future__ import annotations

import unittest
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
ROLE_ROOT = PROJECT_ROOT / "ansible/roles/docker"


class DockerRoleTests(unittest.TestCase):
    def test_daemon_config_defaults_to_untouched_docker_defaults(self) -> None:
        defaults = (ROLE_ROOT / "defaults/main.yml").read_text(encoding="utf-8")

        self.assertIn("docker_daemon_config: {}", defaults)

    def test_daemon_config_file_is_only_installed_when_configured(self) -> None:
        tasks = (ROLE_ROOT / "tasks/main.yml").read_text(encoding="utf-8")

        self.assertIn("dest: /etc/docker/daemon.json", tasks)
        self.assertIn("when: docker_daemon_config | length > 0", tasks)

    def test_daemon_config_override_is_removed_when_unconfigured(self) -> None:
        # Going back to {} must remove a previously installed override.
        tasks = (ROLE_ROOT / "tasks/main.yml").read_text(encoding="utf-8")

        removal_task = tasks.split(
            "Remove Docker daemon configuration override", 1
        )[1].split("- name:", 1)[0]
        self.assertIn("path: /etc/docker/daemon.json", removal_task)
        self.assertIn("state: absent", removal_task)
        self.assertIn("when: docker_daemon_config | length == 0", removal_task)

    def test_docker_restarts_when_daemon_config_changes_either_way(self) -> None:
        tasks = (ROLE_ROOT / "tasks/main.yml").read_text(encoding="utf-8")

        service_task = tasks.split(
            "Ensure Docker service is enabled and running", 1
        )[1]
        self.assertIn("docker_daemon_config_file.changed", service_task)
        self.assertIn("docker_daemon_config_removed.changed", service_task)

    def test_homeserver_containers_get_a_fixed_dns_server(self) -> None:
        group_vars = (
            PROJECT_ROOT / "ansible/inventory/group_vars/all/main.yml"
        ).read_text(encoding="utf-8")

        config = group_vars.split("docker_daemon_config:", 1)[1].split("\n\n", 1)[0]
        self.assertIn("dns:", config)
        self.assertIn("- 192.168.178.1", config)

    def test_retired_wait_for_nameserver_dropin_is_removed(self) -> None:
        tasks = (ROLE_ROOT / "tasks/main.yml").read_text(encoding="utf-8")

        removal_task = tasks.split(
            "Remove retired wait-for-nameserver drop-in", 1
        )[1].split("- name:", 1)[0]
        self.assertIn("wait-for-nameserver.conf", removal_task)
        self.assertIn("state: absent", removal_task)
        self.assertNotIn("ExecStartPre", tasks)


if __name__ == "__main__":
    unittest.main()
