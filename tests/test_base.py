from __future__ import annotations

import unittest
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
ROLE_ROOT = PROJECT_ROOT / "ansible/roles/base"


class BaseRoleTests(unittest.TestCase):
    def test_iperf3_is_purged_not_just_disabled(self) -> None:
        # Found live: apt installing iperf3 for a one-off test also silently
        # enables its systemd service, which listens unauthenticated on all
        # interfaces (not loopback) -- purging (not just stopping/disabling)
        # means a future ad-hoc `apt install iperf3` can't quietly
        # reintroduce the same exposure.
        tasks = (ROLE_ROOT / "tasks/main.yml").read_text(encoding="utf-8")

        self.assertIn("name: iperf3", tasks)
        self.assertIn("state: absent", tasks)
        self.assertIn("purge: true", tasks)

    def test_requires_root(self) -> None:
        tasks = (ROLE_ROOT / "tasks/main.yml").read_text(encoding="utf-8")

        self.assertIn('effective_user.stdout == "root"', tasks)


if __name__ == "__main__":
    unittest.main()
