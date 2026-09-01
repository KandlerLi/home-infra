from __future__ import annotations

import unittest
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
PLAYBOOK = (
    PROJECT_ROOT / "ansible/playbooks/shared-ingress-k3s-cutover.yml"
).read_text(encoding="utf-8")


class SharedIngressK3sCutoverTests(unittest.TestCase):
    def test_requires_explicit_confirmation(self) -> None:
        self.assertIn(
            'k3s_ingress_cutover_confirmation | default(\'\')\n            '
            '== "CUTOVER_SHARED_INGRESS_TO_K3S"',
            PLAYBOOK,
        )

    def test_defaults_to_cutover_not_rollback(self) -> None:
        self.assertIn("shared_ingress_k3s_cutover_action: cutover", PLAYBOOK)

    def test_cutover_stops_the_container_without_removing_it(self) -> None:
        stop_task = PLAYBOOK.split("Stop shared_ingress's own container", 1)[1]
        self.assertIn("state: stopped", stop_task)
        self.assertIn('when: shared_ingress_k3s_cutover_action == "cutover"', stop_task)

    def test_rollback_starts_the_existing_container_without_recreating(
        self,
    ) -> None:
        start_task = PLAYBOOK.split(
            "Start shared_ingress's own container back up", 1
        )[1]
        self.assertIn("state: started", start_task)
        self.assertIn(
            'when: shared_ingress_k3s_cutover_action == "rollback"', start_task
        )
        # No image/env/etc given -- only name + state, so Ansible has
        # nothing to reconcile against and won't recreate the container.
        self.assertNotIn("image:", start_task)

    def test_invalid_action_is_rejected(self) -> None:
        self.assertIn(
            'shared_ingress_k3s_cutover_action in ["cutover", "rollback"]',
            PLAYBOOK,
        )

    def test_runs_against_the_homeserver(self) -> None:
        self.assertIn("hosts: home_servers", PLAYBOOK)


if __name__ == "__main__":
    unittest.main()
