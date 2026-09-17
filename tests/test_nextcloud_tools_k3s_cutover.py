from __future__ import annotations

import unittest
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
PLAYBOOK = (
    PROJECT_ROOT / "ansible/playbooks/nextcloud-tools-k3s-cutover.yml"
).read_text(encoding="utf-8")


class NextcloudToolsK3sCutoverTests(unittest.TestCase):
    def test_requires_explicit_confirmation(self) -> None:
        self.assertIn(
            "nextcloud_tools_cutover_confirmation | default('')\n            "
            '== "CUTOVER_NEXTCLOUD_TOOLS_TO_K3S"',
            PLAYBOOK,
        )

    def test_defaults_to_cutover_not_rollback(self) -> None:
        self.assertIn("nextcloud_tools_k3s_cutover_action: cutover", PLAYBOOK)

    def test_cutover_stops_and_disables_the_service_without_removing_it(
        self,
    ) -> None:
        stop_task = PLAYBOOK.split("Stop and disable nextcloud-tools.service", 1)[1]
        self.assertIn("state: stopped", stop_task)
        self.assertIn("enabled: false", stop_task)
        self.assertIn(
            'when: nextcloud_tools_k3s_cutover_action == "cutover"', stop_task
        )

    def test_rollback_starts_the_existing_service_without_reinstalling(
        self,
    ) -> None:
        start_task = PLAYBOOK.split(
            "Start and enable nextcloud-tools.service back up", 1
        )[1]
        self.assertIn("state: started", start_task)
        self.assertIn("enabled: true", start_task)
        self.assertIn(
            'when: nextcloud_tools_k3s_cutover_action == "rollback"', start_task
        )

    def test_invalid_action_is_rejected(self) -> None:
        self.assertIn(
            'nextcloud_tools_k3s_cutover_action in ["cutover", "rollback"]',
            PLAYBOOK,
        )

    def test_runs_against_the_homeserver(self) -> None:
        self.assertIn("hosts: home_servers", PLAYBOOK)


if __name__ == "__main__":
    unittest.main()
