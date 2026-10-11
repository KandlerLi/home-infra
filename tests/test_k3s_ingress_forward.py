from __future__ import annotations

import unittest
from pathlib import Path

import yaml

PROJECT_ROOT = Path(__file__).resolve().parents[1]
ROLE_ROOT = PROJECT_ROOT / "ansible/roles/k3s_ingress_forward"


class K3sIngressForwardRoleTests(unittest.TestCase):
    def test_disabled_by_default(self) -> None:
        # Opt-in: enabling forwards real public traffic.
        defaults = (ROLE_ROOT / "defaults/main.yml").read_text(
            encoding="utf-8"
        )

        self.assertIn("k3s_ingress_forward_enabled: false", defaults)

    def test_live_inventory_persists_it_enabled(self) -> None:
        # The flag must be persisted, or the next ordinary apply removes the rules.
        group_vars = (
            PROJECT_ROOT / "ansible/inventory/group_vars/all/main.yml"
        ).read_text(encoding="utf-8")

        self.assertIn("k3s_ingress_forward_enabled: true", group_vars)

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

    def test_default_rules_forward_dns_over_both_tcp_and_udp(self) -> None:
        # DNS needs both tcp and udp forwarded.
        defaults = yaml.safe_load(
            (ROLE_ROOT / "defaults/main.yml").read_text(encoding="utf-8")
        )

        dns_rules = [
            rule
            for rule in defaults["k3s_ingress_forward_rules"]
            if rule["public_port"] == 53
        ]
        protocols = {rule["protocol"] for rule in dns_rules}
        self.assertEqual(protocols, {"tcp", "udp"})
        for rule in dns_rules:
            self.assertEqual(rule["target_port"], 53)

    def test_default_rules_forward_inbound_smtp_over_tcp(self) -> None:
        # Port 25 only: nothing else mail-related is public through this relay.
        defaults = yaml.safe_load(
            (ROLE_ROOT / "defaults/main.yml").read_text(encoding="utf-8")
        )

        smtp_rules = [
            rule
            for rule in defaults["k3s_ingress_forward_rules"]
            if rule["public_port"] == 25
        ]
        self.assertEqual(len(smtp_rules), 1)
        self.assertEqual(smtp_rules[0]["target_port"], 25)
        self.assertEqual(smtp_rules[0]["protocol"], "tcp")

    def test_default_rules_forward_imaps_and_submissions_over_tcp(self) -> None:
        # TLS-only mail ports; plaintext 143/587 must never be forwarded.
        defaults = yaml.safe_load(
            (ROLE_ROOT / "defaults/main.yml").read_text(encoding="utf-8")
        )
        rules = defaults["k3s_ingress_forward_rules"]

        for port in (993, 465):
            matching = [r for r in rules if r["public_port"] == port]
            self.assertEqual(len(matching), 1, port)
            self.assertEqual(matching[0]["target_port"], port)
            self.assertEqual(matching[0]["protocol"], "tcp")
        forwarded = {r["public_port"] for r in rules}
        self.assertFalse(forwarded & {143, 587})

    def test_forward_accept_rule_is_inserted_ahead_of_docker_and_libvirt(
        self,
    ) -> None:
        # libvirt's chain rejects new inbound connections, so the accept rule goes first.
        tasks = (ROLE_ROOT / "tasks/main.yml").read_text(encoding="utf-8")

        accept_task = tasks.split(
            "Allow forwarded ingress traffic to reach k3s", 1
        )[1]
        self.assertIn("action: insert", accept_task)
        self.assertIn("rule_num: 1", accept_task)
        self.assertIn("chain: FORWARD", accept_task)

    def test_pre_fix_dnat_rule_shape_is_explicitly_reconciled(self) -> None:
        # Re-applying the fixed rule would add it next to the old one, so the old shape is removed explicitly.
        tasks = (ROLE_ROOT / "tasks/main.yml").read_text(encoding="utf-8")

        reconcile_task = tasks.split(
            "Remove the pre-fix DNAT rule shape (no interface restriction)", 1
        )[1].split(
            "Remove the destination-unrestricted DNAT rule shape", 1
        )[0]
        self.assertIn("state: absent", reconcile_task)
        self.assertIn("table: nat", reconcile_task)
        self.assertIn("chain: PREROUTING", reconcile_task)
        self.assertIn("jump: DNAT", reconcile_task)
        self.assertNotIn("in_interface", reconcile_task)

        # iptables -C matches the comment too, so this reproduces the first rule's exact comment.
        self.assertIn(
            "k3s_ingress_forward: {{ item.public_port }} ->",
            reconcile_task,
        )
        # The module args, not the loop label, must stay free of item.protocol.
        module_args = reconcile_task.split("loop:", 1)[0]
        self.assertNotIn("item.protocol", module_args)

    def test_pre_fix_forward_accept_rule_shape_is_explicitly_reconciled(
        self,
    ) -> None:
        # Same reconciliation for the FORWARD accept rule's first shape.
        tasks = (ROLE_ROOT / "tasks/main.yml").read_text(encoding="utf-8")

        reconcile_task = tasks.split(
            "Remove the pre-fix FORWARD accept rule shape", 1
        )[1].split("Relay ingress traffic to k3s via DNAT", 1)[0]
        self.assertIn("state: absent", reconcile_task)
        self.assertIn("chain: FORWARD", reconcile_task)
        self.assertIn("protocol: tcp", reconcile_task)
        self.assertIn("jump: ACCEPT", reconcile_task)

        # Reproduces the first rule's exact comment, like the DNAT reconciliation.
        self.assertIn(
            "k3s_ingress_forward: allow ->\n"
            "      {{ k3s_ingress_forward_target_ip }}:{{ item.target_port }}",
            reconcile_task,
        )
        module_args = reconcile_task.split("loop:", 1)[0]
        self.assertNotIn("item.protocol", module_args)

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
        # Without the exclusion, the VM's own outbound 80/443 traffic gets hairpinned back to it.
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

    def test_dnat_rule_is_restricted_to_this_hosts_own_address(self) -> None:
        # Without a destination filter, the rule also caught this host's own outbound traffic.
        tasks = (ROLE_ROOT / "tasks/main.yml").read_text(encoding="utf-8")

        dnat_task = tasks.split(
            "Relay ingress traffic to k3s via DNAT", 1
        )[1]
        self.assertIn('destination: "{{ ansible_host }}"', dnat_task)

    def test_destination_unrestricted_dnat_rule_shape_is_explicitly_reconciled(
        self,
    ) -> None:
        # Same explicit removal for the destination-unrestricted shape.
        tasks = (ROLE_ROOT / "tasks/main.yml").read_text(encoding="utf-8")

        reconcile_task = tasks.split(
            "Remove the destination-unrestricted DNAT rule shape", 1
        )[1].split("Relay ingress traffic to k3s via DNAT", 1)[0]
        self.assertIn("state: absent", reconcile_task)
        self.assertIn("table: nat", reconcile_task)
        self.assertIn("chain: PREROUTING", reconcile_task)
        self.assertIn("jump: DNAT", reconcile_task)
        # The shape live between the interface fix and the destination fix.
        self.assertIn(
            'in_interface: "!{{ k3s_ingress_forward_target_bridge }}"',
            reconcile_task,
        )
        # to_destination contains "destination:" too, so this checks for the new filter specifically.
        self.assertNotIn('destination: "{{ ansible_host }}"', reconcile_task)
        self.assertIn(
            "register: k3s_ingress_forward_dnat_reconcile_result",
            reconcile_task,
        )

        persist_task = tasks.split("Persist iptables rules across reboots", 1)[
            1
        ]
        self.assertIn(
            "k3s_ingress_forward_dnat_reconcile_result is changed",
            persist_task,
        )

    def test_rule_protocol_defaults_to_tcp_but_is_overridable(self) -> None:
        # DNS needs tcp and udp on one port, so protocol is per rule.
        tasks = (ROLE_ROOT / "tasks/main.yml").read_text(encoding="utf-8")

        self.assertIn("item.protocol | default('tcp')", tasks)

    def test_enabled_flag_is_a_real_toggle_not_just_a_skip_guard(
        self,
    ) -> None:
        # Disabling must remove the rules again.
        tasks = (ROLE_ROOT / "tasks/main.yml").read_text(encoding="utf-8")

        self.assertIn(
            "state: \"{{ 'present' if (k3s_ingress_forward_enabled | bool) "
            "else 'absent' }}\"",
            tasks,
        )

    def test_enabled_flag_is_bool_filtered_everywhere_its_evaluated(
        self,
    ) -> None:
        # -e passes a string; an unfiltered Jinja if would treat "false" as true.
        tasks = (ROLE_ROOT / "tasks/main.yml").read_text(encoding="utf-8")

        occurrences = tasks.count("k3s_ingress_forward_enabled")
        bool_filtered = tasks.count("k3s_ingress_forward_enabled | bool")
        self.assertEqual(occurrences, bool_filtered)

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
        self.assertIn(
            "k3s_ingress_forward_accept_reconcile_result is changed", persist_task
        )
        self.assertIn("k3s_ingress_forward_dnat_result is changed", persist_task)

    def test_one_time_force_persist_catches_up_the_saved_state(self) -> None:
        # A one-time catch-up for saved state left stale by the registration bug below.
        tasks = (ROLE_ROOT / "tasks/main.yml").read_text(encoding="utf-8")

        force_persist_task = tasks.split(
            "Force-persist once to catch the saved state up", 1
        )[1].split("Persist iptables rules across reboots", 1)[0]
        self.assertIn("netfilter-persistent", force_persist_task)
        self.assertIn("when: k3s_ingress_forward_enabled | bool", force_persist_task)

    def test_reconcile_task_result_is_registered_and_gates_persisting(
        self,
    ) -> None:
        # An unregistered reconcile result skipped persisting, so a reboot would restore the old rule.
        tasks = (ROLE_ROOT / "tasks/main.yml").read_text(encoding="utf-8")

        reconcile_task = tasks.split(
            "Remove the pre-fix DNAT rule shape (no interface restriction)", 1
        )[1].split(
            "Remove the destination-unrestricted DNAT rule shape", 1
        )[0]
        self.assertIn(
            "register: k3s_ingress_forward_reconcile_result", reconcile_task
        )

        persist_task = tasks.split("Persist iptables rules across reboots", 1)[
            1
        ]
        self.assertIn(
            "k3s_ingress_forward_reconcile_result is changed", persist_task
        )

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
