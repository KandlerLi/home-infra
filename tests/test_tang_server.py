from __future__ import annotations

import unittest
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
ROLE_ROOT = PROJECT_ROOT / "ansible/roles/tang_server"


class TangServerRoleTests(unittest.TestCase):
    def test_no_client_is_allowed_by_default(self) -> None:
        defaults = (ROLE_ROOT / "defaults/main.yml").read_text(encoding="utf-8")

        self.assertIn("tang_server_allowed_ipv4_clients: []", defaults)

    def test_allow_list_is_validated(self) -> None:
        tasks = (ROLE_ROOT / "tasks/main.yml").read_text(encoding="utf-8")

        self.assertIn("tang_server_allowed_ipv4_clients | length > 0", tasks)
        self.assertIn("select('search', '/')", tasks)

    def test_clients_are_inserted_before_the_drop_rules(self) -> None:
        tasks = (ROLE_ROOT / "tasks/main.yml").read_text(encoding="utf-8")

        self.assertIn("action: insert", tasks)
        self.assertLess(tasks.index("jump: ACCEPT"), tasks.index("jump: DROP"))

    def test_other_clients_are_dropped_on_both_ip_versions(self) -> None:
        tasks = (ROLE_ROOT / "tasks/main.yml").read_text(encoding="utf-8")
        drop_task = tasks[tasks.index("Drop every other client") :]

        self.assertIn("- ipv4", drop_task)
        self.assertIn("- ipv6", drop_task)

    def test_firewall_is_in_place_before_tang_is_installed(self) -> None:
        tasks = (ROLE_ROOT / "tasks/main.yml").read_text(encoding="utf-8")

        self.assertLess(tasks.index("jump: DROP"), tasks.index("name: tang\n"))

    def test_routine_rollouts_never_reach_the_pi(self) -> None:
        playbooks = PROJECT_ROOT / "ansible/playbooks"
        site = (playbooks / "site.yml").read_text(encoding="utf-8")
        rasp = (playbooks / "rasp.yml").read_text(encoding="utf-8")

        self.assertNotIn("tang_server", site)
        self.assertIn("hosts: rasp_servers", rasp)
        self.assertIn("- tang_server", rasp)


if __name__ == "__main__":
    unittest.main()
