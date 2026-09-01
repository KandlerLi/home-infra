from __future__ import annotations

import unittest
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
PLAYBOOK = (
    PROJECT_ROOT / "ansible/playbooks/blocky-k3s-cutover.yml"
).read_text(encoding="utf-8")


class BlockyK3sCutoverTests(unittest.TestCase):
    def test_requires_explicit_confirmation(self) -> None:
        self.assertIn(
            'k3s_ingress_cutover_confirmation | default(\'\')\n            '
            '== "CUTOVER_BLOCKY_TO_K3S"',
            PLAYBOOK,
        )

    def test_defaults_to_cutover_not_rollback(self) -> None:
        self.assertIn("blocky_k3s_cutover_action: cutover", PLAYBOOK)

    def test_cutover_stops_both_containers_without_removing_them(self) -> None:
        blocky_stop = PLAYBOOK.split("Stop blocky's own container", 1)[1]
        self.assertIn("name: blocky", blocky_stop)
        self.assertIn("state: stopped", blocky_stop)
        self.assertIn('when: blocky_k3s_cutover_action == "cutover"', blocky_stop)

        postgres_stop = PLAYBOOK.split(
            "Stop blocky-postgres's own container", 1
        )[1]
        self.assertIn("name: blocky-postgres", postgres_stop)
        self.assertIn("state: stopped", postgres_stop)
        self.assertIn(
            'when: blocky_k3s_cutover_action == "cutover"', postgres_stop
        )

    def test_rollback_starts_both_containers_without_recreating(self) -> None:
        for task_name, container_name in (
            ("Start blocky-postgres's own container back up", "blocky-postgres"),
            ("Start blocky's own container back up", "blocky"),
        ):
            with self.subTest(container=container_name):
                start_task = PLAYBOOK.split(task_name, 1)[1]
                self.assertIn(f"name: {container_name}", start_task)
                self.assertIn("state: started", start_task)
                self.assertIn(
                    'when: blocky_k3s_cutover_action == "rollback"', start_task
                )
                # No image/env/etc given -- only name + state, so
                # Ansible has nothing to reconcile against and won't
                # recreate the container.
                self.assertNotIn("image:", start_task)

    def test_invalid_action_is_rejected(self) -> None:
        self.assertIn(
            'blocky_k3s_cutover_action in ["cutover", "rollback"]',
            PLAYBOOK,
        )

    def test_runs_against_the_homeserver(self) -> None:
        self.assertIn("hosts: home_servers", PLAYBOOK)

    def test_not_imported_into_site_yml(self) -> None:
        site = (PROJECT_ROOT / "ansible/playbooks/site.yml").read_text(
            encoding="utf-8"
        )

        self.assertNotIn("blocky-k3s-cutover", site)


if __name__ == "__main__":
    unittest.main()
