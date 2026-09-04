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


class ApacheNetworkReconnectTests(unittest.TestCase):
    # Confirmed live on two independent reboots (2026-09-01, 2026-09-04)
    # that nextcloud-aio-apache silently drops off the nextcloud-aio
    # Docker network -- see PARKED.md's "Homeserver: make a reboot a
    # complete non-event".
    def test_script_is_idempotent_and_bounded(self) -> None:
        script = (ROLE_ROOT / "files/reconnect_apache_network.sh").read_text(
            encoding="utf-8"
        )

        self.assertIn("set -Eeuo pipefail", script)
        # Already-connected is a clean no-op, not an error -- this runs
        # on every apply as well as every boot.
        self.assertIn("already connected to", script)
        # Apache never appearing at all is a different problem than a
        # dropped network membership -- exits cleanly rather than
        # failing the whole Ansible run.
        self.assertIn("WAIT_TIMEOUT_SECONDS", script)
        self.assertIn("docker network connect", script)

    def test_systemd_unit_gives_real_headroom_over_the_scripts_own_wait(
        self,
    ) -> None:
        unit = (
            ROLE_ROOT
            / "templates/nextcloud-aio-apache-network-fix.service.j2"
        ).read_text(encoding="utf-8")
        script = (ROLE_ROOT / "files/reconnect_apache_network.sh").read_text(
            encoding="utf-8"
        )

        script_wait_seconds = int(
            script.split("WAIT_TIMEOUT_SECONDS=", 1)[1].split("\n", 1)[0]
        )
        unit_timeout_seconds = int(
            unit.split("TimeoutStartSec=", 1)[1].split("\n", 1)[0]
        )

        self.assertGreater(unit_timeout_seconds, script_wait_seconds)
        self.assertIn("After=docker.service", unit)
        self.assertIn("WantedBy=multi-user.target", unit)

    def test_reruns_on_every_apply_not_just_the_first(self) -> None:
        # A plain "started" against an oneshot systemd already considers
        # "active (exited)" from a prior run is a no-op -- would
        # silently stop this from ever self-healing again after its
        # first success. Must be "restarted" to actually re-execute.
        tasks = (ROLE_ROOT / "tasks/main.yml").read_text(encoding="utf-8")

        reconnect_task = tasks.split(
            "Run the Apache network-reconnect check", 1
        )[1]
        self.assertIn("state: restarted", reconnect_task)

    def test_installed_with_the_default_path_used_by_the_task_and_unit(
        self,
    ) -> None:
        defaults = (ROLE_ROOT / "defaults/main.yml").read_text(
            encoding="utf-8"
        )
        tasks = (ROLE_ROOT / "tasks/main.yml").read_text(encoding="utf-8")
        unit = (
            ROLE_ROOT
            / "templates/nextcloud-aio-apache-network-fix.service.j2"
        ).read_text(encoding="utf-8")

        self.assertIn("nextcloud_aio_apache_network_fix_install_path", defaults)
        self.assertIn(
            "dest: \"{{ nextcloud_aio_apache_network_fix_install_path }}\"",
            tasks,
        )
        self.assertIn(
            "ExecStart={{ nextcloud_aio_apache_network_fix_install_path }}",
            unit,
        )


if __name__ == "__main__":
    unittest.main()
