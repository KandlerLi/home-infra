from __future__ import annotations

import unittest
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
ROLE_ROOT = PROJECT_ROOT / "ansible/roles/unattended_upgrades"


class UnattendedUpgradesRoleTests(unittest.TestCase):
    def test_security_origin_only(self) -> None:
        defaults = (ROLE_ROOT / "defaults/main.yml").read_text(encoding="utf-8")

        self.assertIn("${distro_codename}-security,label=Debian-Security", defaults)
        self.assertNotIn("label=Debian\"", defaults)

    def test_debian_default_origins_are_cleared(self) -> None:
        # Without #clear, apt appends to 50unattended-upgrades' list, point releases included.
        template = (ROLE_ROOT / "templates/52unattended-upgrades-local.j2").read_text(
            encoding="utf-8"
        )

        self.assertIn("#clear Unattended-Upgrade::Origins-Pattern;", template)
        self.assertLess(
            template.index("#clear"),
            template.index("Unattended-Upgrade::Origins-Pattern {"),
        )

    def test_never_reboots(self) -> None:
        template = (ROLE_ROOT / "templates/52unattended-upgrades-local.j2").read_text(
            encoding="utf-8"
        )

        self.assertIn('Unattended-Upgrade::Automatic-Reboot "false";', template)

    def test_needrestart_only_lists_during_unattended_runs(self) -> None:
        tasks = (ROLE_ROOT / "tasks/main.yml").read_text(encoding="utf-8")

        self.assertIn("apt-daily-upgrade.service.d/needrestart-list-only.conf", tasks)
        self.assertIn("Environment=NEEDRESTART_MODE=l", tasks)

    def test_applied_to_host_and_k3s_nodes(self) -> None:
        playbooks = PROJECT_ROOT / "ansible/playbooks"
        site = (playbooks / "site.yml").read_text(encoding="utf-8")
        k3s = (playbooks / "k3s.yml").read_text(encoding="utf-8")

        self.assertIn("- unattended_upgrades", site)
        self.assertIn("hosts: k3s_nodes:k3s_agent_nodes", k3s)
        self.assertIn("role: unattended_upgrades", k3s)


if __name__ == "__main__":
    unittest.main()
