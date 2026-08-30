from __future__ import annotations

import unittest
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
ROLE_ROOT = PROJECT_ROOT / "ansible/roles/nextcloud_aio"


class NextcloudAioApacheBindingTests(unittest.TestCase):
    def test_apache_ip_binding_defaults_to_loopback(self) -> None:
        defaults = (ROLE_ROOT / "defaults/main.yml").read_text(encoding="utf-8")

        self.assertIn("nextcloud_aio_apache_ip_binding: 127.0.0.1", defaults)

    def test_apache_ip_binding_allows_only_loopback_or_the_k3s_vm_address(
        self,
    ) -> None:
        # 192.168.101.1 is this homeserver's own address on the k3s VM's
        # isolated libvirt NAT network, not a public or LAN-wide one --
        # the same trust boundary shared_ingress's own
        # shared_ingress_home_upstream/shared_ingress_deluge_upstream
        # exceptions already rely on. AIO's mastercontainer only accepts
        # one APACHE_IP_BINDING value at a time, so this is a switch
        # between the two, not an addition -- shared_ingress's own
        # nextcloud_upstream must move in lockstep or it loses its route
        # to Apache entirely. Not yet switched live -- this only widens
        # what the guard accepts.
        tasks = (ROLE_ROOT / "tasks/main.yml").read_text(encoding="utf-8")

        self.assertIn(
            'nextcloud_aio_apache_ip_binding == "127.0.0.1"\n'
            '            or nextcloud_aio_apache_ip_binding == '
            '"192.168.101.1"',
            tasks,
        )

    def test_still_refuses_an_arbitrary_binding_address(self) -> None:
        # The guard must still be a closed allowlist of exactly two
        # values -- widening it to accept anything would defeat the
        # whole point of pinning Apache to a known, narrow address.
        tasks = (ROLE_ROOT / "tasks/main.yml").read_text(encoding="utf-8")

        self.assertEqual(
            tasks.count("nextcloud_aio_apache_ip_binding =="), 2
        )


if __name__ == "__main__":
    unittest.main()
