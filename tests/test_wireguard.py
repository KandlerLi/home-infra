from __future__ import annotations

import unittest
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
ROLE_ROOT = PROJECT_ROOT / "ansible/roles/wireguard"


class WireguardTeardownTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tasks = (ROLE_ROOT / "tasks/main.yml").read_text(encoding="utf-8")

    def test_never_installs_anything(self) -> None:
        self.assertNotIn("state: present", self.tasks)
        self.assertNotIn("state: started", self.tasks)

    def test_removes_all_three_iptables_rules(self) -> None:
        self.assertEqual(self.tasks.count("ansible.builtin.iptables:"), 3)
        self.assertEqual(self.tasks.count("state: absent"), 6)

    def test_removes_server_key(self) -> None:
        self.assertIn("path: /etc/wireguard\n    state: absent", self.tasks)


if __name__ == "__main__":
    unittest.main()
