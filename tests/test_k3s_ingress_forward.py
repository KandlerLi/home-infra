from __future__ import annotations

import unittest
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
ROLE_ROOT = PROJECT_ROOT / "ansible/roles/k3s_ingress_forward"


class K3sIngressForwardRoleTests(unittest.TestCase):
    def test_disabled_by_default(self) -> None:
        # Same opt-in convention every other role here follows --
        # enabling this one forwards real public traffic, so it must
        # never happen as a side effect of a normal apply.
        defaults = (ROLE_ROOT / "defaults/main.yml").read_text(
            encoding="utf-8"
        )

        self.assertIn("k3s_ingress_forward_enabled: false", defaults)

    def test_target_ip_is_pinned_and_validated(self) -> None:
        defaults = (ROLE_ROOT / "defaults/main.yml").read_text(
            encoding="utf-8"
        )
        tasks = (ROLE_ROOT / "tasks/main.yml").read_text(encoding="utf-8")

        self.assertIn(
            "k3s_ingress_forward_target_ip: 192.168.101.10", defaults
        )
        self.assertIn(
            'k3s_ingress_forward_target_ip == "192.168.101.10"', tasks
        )

    def test_default_rules_forward_80_and_443(self) -> None:
        defaults = (ROLE_ROOT / "defaults/main.yml").read_text(
            encoding="utf-8"
        )

        self.assertIn("public_port: 80", defaults)
        self.assertIn("public_port: 443", defaults)

    def test_forward_accept_rule_is_inserted_ahead_of_docker_and_libvirt(
        self,
    ) -> None:
        # Confirmed live on the homeserver: libvirt's own LIBVIRT_FWI
        # chain only accepts RELATED,ESTABLISHED traffic into a
        # NAT-mode network by default, REJECTing a fresh externally-
        # initiated connection otherwise. rule_num 1 on the base
        # FORWARD chain terminates before Docker's or libvirt's own
        # chains are ever reached, regardless of their contents.
        tasks = (ROLE_ROOT / "tasks/main.yml").read_text(encoding="utf-8")

        accept_task = tasks.split(
            "Allow forwarded ingress traffic to reach k3s", 1
        )[1]
        self.assertIn("action: insert", accept_task)
        self.assertIn("rule_num: 1", accept_task)
        self.assertIn("chain: FORWARD", accept_task)

    def test_dnat_rule_targets_prerouting_nat_table(self) -> None:
        tasks = (ROLE_ROOT / "tasks/main.yml").read_text(encoding="utf-8")

        dnat_task = tasks.split(
            "Relay ingress traffic to k3s via DNAT", 1
        )[1]
        self.assertIn("table: nat", dnat_task)
        self.assertIn("chain: PREROUTING", dnat_task)
        self.assertIn("jump: DNAT", dnat_task)

    def test_dnat_rule_excludes_traffic_from_the_k3s_bridge_itself(
        self,
    ) -> None:
        # Without this exclusion, the k3s VM's own outbound traffic on
        # source port 80/443 (any HTTPS image pull) matches the DNAT
        # rule's own destination_port just as well as real inbound
        # traffic does, and gets hairpinned straight back to the VM's
        # own address instead of ever leaving -- confirmed live
        # (2026-09-01), surfaced as ImagePullBackOff with no other
        # visible cause.
        defaults = (ROLE_ROOT / "defaults/main.yml").read_text(
            encoding="utf-8"
        )
        tasks = (ROLE_ROOT / "tasks/main.yml").read_text(encoding="utf-8")

        self.assertIn(
            "k3s_ingress_forward_target_bridge: virbr11", defaults
        )
        dnat_task = tasks.split(
            "Relay ingress traffic to k3s via DNAT", 1
        )[1]
        self.assertIn(
            'in_interface: "!{{ k3s_ingress_forward_target_bridge }}"',
            dnat_task,
        )

    def test_rule_protocol_defaults_to_tcp_but_is_overridable(self) -> None:
        # Every rule needed only tcp until DNS -- DNS needs both tcp and
        # udp forwarded to the same port, so this has to be a real
        # per-rule field, not a role-wide constant.
        tasks = (ROLE_ROOT / "tasks/main.yml").read_text(encoding="utf-8")

        self.assertIn("item.protocol | default('tcp')", tasks)

    def test_enabled_flag_is_a_real_toggle_not_just_a_skip_guard(
        self,
    ) -> None:
        # Disabling this role must actually remove the rules again
        # (the plan's own rollback step), not merely skip creating
        # them on a host that never had them.
        tasks = (ROLE_ROOT / "tasks/main.yml").read_text(encoding="utf-8")

        self.assertIn(
            "state: \"{{ 'present' if k3s_ingress_forward_enabled "
            "else 'absent' }}\"",
            tasks,
        )

    def test_iptables_persistent_prompts_are_preseeded(self) -> None:
        tasks = (ROLE_ROOT / "tasks/main.yml").read_text(encoding="utf-8")

        self.assertIn("ansible.builtin.debconf", tasks)
        self.assertIn("iptables-persistent/autosave_", tasks)

    def test_rules_only_persisted_when_something_actually_changed(
        self,
    ) -> None:
        tasks = (ROLE_ROOT / "tasks/main.yml").read_text(encoding="utf-8")

        persist_task = tasks.split("Persist iptables rules across reboots", 1)[
            1
        ]
        self.assertIn("netfilter-persistent", persist_task)
        self.assertIn("k3s_ingress_forward_accept_result is changed", persist_task)
        self.assertIn("k3s_ingress_forward_dnat_result is changed", persist_task)

    def test_runs_against_the_homeserver_not_inside_a_k3s_vm(self) -> None:
        playbook = (
            PROJECT_ROOT / "ansible/playbooks/k3s.yml"
        ).read_text(encoding="utf-8")

        play = playbook.split(
            "Forward ingress traffic to k3s's own Traefik", 1
        )[1]
        self.assertIn("hosts: home_servers", play)

    def test_not_imported_into_site_yml(self) -> None:
        site = (PROJECT_ROOT / "ansible/playbooks/site.yml").read_text(
            encoding="utf-8"
        )

        self.assertNotIn("k3s_ingress_forward", site)


if __name__ == "__main__":
    unittest.main()
