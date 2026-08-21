from __future__ import annotations

import unittest
from pathlib import Path

import yaml
from jinja2 import Environment

PROJECT_ROOT = Path(__file__).resolve().parents[1]


class NextcloudToolsAnsibleTests(unittest.TestCase):
    def test_defaults_are_disabled_and_loopback_only(self) -> None:
        defaults = (
            PROJECT_ROOT / "ansible/roles/nextcloud_tools/defaults/main.yml"
        ).read_text()

        self.assertIn("nextcloud_tools_enabled: false", defaults)
        self.assertIn("nextcloud_tools_endpoint_host: 127.0.0.1", defaults)
        self.assertIn("nextcloud_tools_endpoint_port: 11000", defaults)
        self.assertIn("nextcloud_tools_username: home-agent", defaults)
        self.assertIn("nextcloud_tools_allowed_root: AI Workspace", defaults)

        inventory = (
            PROJECT_ROOT / "ansible/inventory/group_vars/all/main.yml"
        ).read_text()
        self.assertIn(
            "nextcloud_tools_allowed_root: Shared/AI Workspace",
            inventory,
        )
        self.assertIn("nextcloud_tools_enabled: true", inventory)
        self.assertIn("home_agent_enabled: true", inventory)

    def test_service_is_network_and_process_restricted(self) -> None:
        unit = (
            PROJECT_ROOT
            / "ansible/roles/nextcloud_tools/templates/nextcloud-tools.service.j2"
        ).read_text()

        self.assertIn("IPAddressDeny=any", unit)
        self.assertIn("IPAddressAllow=127.0.0.1", unit)
        self.assertIn("RuntimeDirectoryPreserve=restart", unit)
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

    def test_service_does_not_unlink_a_replacement_socket_at_shutdown(self) -> None:
        service = (
            PROJECT_ROOT
            / "ansible/roles/nextcloud_tools/files/nextcloud_tools_service.py"
        ).read_text()

        self.assertEqual(service.count("socket_path.unlink(missing_ok=True)"), 1)

    def test_both_roles_deploy_their_socket_service_through_the_shared_task_file(
        self,
    ) -> None:
        shared_tasks = yaml.safe_load(
            (PROJECT_ROOT / "ansible/tasks/deploy_socket_service.yml").read_text()
        )
        shared_task_names = {task["name"] for task in shared_tasks}
        self.assertIn(
            "Install restricted {{ socket_service_name }} service", shared_task_names
        )
        self.assertIn(
            "Wait for restricted {{ socket_service_name }} socket", shared_task_names
        )

        expected_vars = {
            "home_agent": {
                "socket_service_unit_name": "home-tools.service",
                "socket_service_handler": "Restart home tools",
                "socket_service_socket_path": "{{ home_agent_tools_socket_path }}",
            },
            "nextcloud_tools": {
                "socket_service_unit_name": "nextcloud-tools.service",
                "socket_service_handler": "Restart nextcloud tools",
                "socket_service_socket_path": "{{ nextcloud_tools_socket_path }}",
            },
        }
        for role_name, expected in expected_vars.items():
            with self.subTest(role=role_name):
                tasks = yaml.safe_load(
                    (
                        PROJECT_ROOT / f"ansible/roles/{role_name}/tasks/main.yml"
                    ).read_text()
                )
                deploy_task = next(
                    task
                    for task in tasks[0]["block"]
                    if "deploy_socket_service.yml"
                    in task.get("ansible.builtin.include_tasks", "")
                )
                self.assertEqual(
                    deploy_task["vars"]["socket_service_unit_name"],
                    expected["socket_service_unit_name"],
                )
                self.assertEqual(
                    deploy_task["vars"]["socket_service_handler"],
                    expected["socket_service_handler"],
                )
                self.assertEqual(
                    deploy_task["vars"]["socket_service_socket_path"],
                    expected["socket_service_socket_path"],
                )

    def test_role_validation_expressions_compile(self) -> None:
        tasks_path = (
            PROJECT_ROOT / "ansible/roles/nextcloud_tools/tasks/main.yml"
        )
        tasks = yaml.safe_load(tasks_path.read_text())
        validation = next(
            task
            for task in tasks[0]["block"]
            if task["name"] == "Validate Nextcloud tools configuration"
        )
        expressions = validation["ansible.builtin.assert"]["that"]
        environment = Environment()
        environment.tests["match"] = lambda value, pattern: True

        for expression in expressions:
            with self.subTest(expression=expression):
                environment.compile_expression(str(expression))

    def test_disable_playbook_detaches_agent_before_stopping_service(self) -> None:
        playbook = (
            PROJECT_ROOT / "ansible/playbooks/disable-nextcloud-tools.yml"
        ).read_text()

        detach_position = playbook.index("home_agent_nextcloud_tools_enabled: false")
        stop_position = playbook.index("Stop and disable Nextcloud tools service")
        self.assertLess(detach_position, stop_position)

    def test_bootstrap_is_guarded_and_never_requests_a_login_password(self) -> None:
        playbook = (
            PROJECT_ROOT / "ansible/playbooks/nextcloud-tools.yml"
        ).read_text()
        # Token generation lives in the shared task file both bootstrap and
        # rotation include, not inline in either playbook.
        shared_password_tasks = (
            PROJECT_ROOT
            / "ansible/tasks/generate_and_validate_nextcloud_app_password.yml"
        ).read_text()

        self.assertIn("BOOTSTRAP_NEXTCLOUD_TOOLS", playbook)
        self.assertIn("--generate-password", playbook)
        self.assertIn(
            "generate_and_validate_nextcloud_app_password.yml", playbook
        )
        self.assertIn("user:auth-tokens:add", shared_password_tasks)
        self.assertIn("--no-interaction", shared_password_tasks)
        self.assertNotIn("--password-from-env", playbook)
        self.assertNotIn("--password-from-env", shared_password_tasks)
        self.assertIn("no_log: true", playbook)
        self.assertIn("home_agent_force_recreate: true", playbook)

    def test_rotation_validates_replacement_before_revoking_old_tokens(self) -> None:
        playbook = (
            PROJECT_ROOT
            / "ansible/playbooks/rotate-nextcloud-tools-token.yml"
        ).read_text()

        self.assertIn("ROTATE_NEXTCLOUD_TOOLS_TOKEN", playbook)
        self.assertNotIn("Require an existing managed app password file", playbook)
        validate_position = playbook.index(
            "Validate replacement token against loopback WebDAV"
        )
        revoke_position = playbook.index("Revoke superseded managed tokens")
        self.assertLess(validate_position, revoke_position)


if __name__ == "__main__":
    unittest.main()
