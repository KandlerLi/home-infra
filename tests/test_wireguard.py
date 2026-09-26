from __future__ import annotations

import unittest
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
ROLE_ROOT = PROJECT_ROOT / "ansible/roles/wireguard"


class WireguardRoleTests(unittest.TestCase):
    def setUp(self) -> None:
        self.defaults = (ROLE_ROOT / "defaults/main.yml").read_text(encoding="utf-8")
        self.tasks = (ROLE_ROOT / "tasks/main.yml").read_text(encoding="utf-8")

    def test_disabled_by_default(self) -> None:
        self.assertIn("wireguard_enabled: false", self.defaults)

    def test_subnet_avoids_existing_networks(self) -> None:
        self.assertIn("wireguard_subnet: 10.13.13.0/24", self.defaults)

    def test_private_key_never_logged(self) -> None:
        # Both the slurp and the template that embeds the key.
        self.assertEqual(self.tasks.count("no_log: true"), 2)
        self.assertIn('mode: "0600"', self.tasks)

    def test_peer_changes_reload_instead_of_restart(self) -> None:
        # A restart would drop the tunnel an apply may be running over.
        self.assertIn("state: reloaded", self.tasks)
        self.assertNotIn("restarted", self.tasks)

    def test_masquerade_scoped_to_vpn_subnet(self) -> None:
        masquerade = self.tasks[self.tasks.index("jump: MASQUERADE") - 400 :]
        self.assertIn('source: "{{ wireguard_subnet }}"', masquerade)


if __name__ == "__main__":
    unittest.main()
