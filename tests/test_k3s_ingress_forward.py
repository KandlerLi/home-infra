from __future__ import annotations

import unittest
from pathlib import Path

import yaml

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

    def test_live_inventory_persists_it_enabled(self) -> None:
        # Confirmed live (2026-09-01): before this override existed,
        # k3s_ingress_forward_enabled was only ever passed via -e at
        # apply time -- since this role is a real toggle (state:
        # present/absent off this flag directly, not a skip-guard), one
        # ordinary ansible-playbook invocation that omitted that -e
        # flag defaulted to false and actively tore out the live
        # 80/443/53 DNAT rules, a real production outage for every
        # public *.jkandler.de service. Guards against that specific
        # regression recurring by omission.
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
        # The real cutover: infra/k3s-apps' own modules/blocky verified
        # healthy internally first (a real in-cluster nslookup
        # resolving correctly via both the Service name and this exact
        # 192.168.101.10 target) before ever forwarding real LAN DNS
        # traffic to it. Both protocols needed -- DNS falls back to tcp
        # for responses too large for a single udp datagram.
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
        # Inbound mail for infra/k3s-apps' own modules/stalwart. Port 25
        # only -- nothing else mail-related is meant to be publicly
        # reachable through this relay.
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

    def test_pre_fix_dnat_rule_shape_is_explicitly_reconciled(self) -> None:
        # Ansible's iptables module matches rules by their exact
        # parameter set -- simply re-applying the corrected rule would
        # add it alongside the old, unrestricted one rather than
        # replacing it, and since DNAT terminates further PREROUTING
        # processing, the old rule (evaluated first) would keep
        # winning. This task explicitly removes that old shape.
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

        # iptables -C requires every specified field to match exactly,
        # comment included -- this has to reproduce the role's very
        # first comment shape (commit 4accac7) verbatim, not the
        # current per-protocol template (commit 84662f5 prefixed it
        # with "tcp"/"udp"), or the live rule is silently never found.
        # Confirmed live (2026-09-01): the templated-comment version
        # of this task reported "ok" against a real live rule it had
        # actually failed to match at all.
        self.assertIn(
            "k3s_ingress_forward: {{ item.public_port }} ->",
            reconcile_task,
        )
        # loop_control's own label is cosmetic only -- it's the
        # module's own args (everything before the loop:) that has to
        # stay free of item.protocol, since that's what iptables -C
        # actually matches against.
        module_args = reconcile_task.split("loop:", 1)[0]
        self.assertNotIn("item.protocol", module_args)

    def test_pre_fix_forward_accept_rule_shape_is_explicitly_reconciled(
        self,
    ) -> None:
        # Same class of fix as the DNAT rule's own reconciliation above,
        # for the FORWARD accept rule's own pre-fix shape (always tcp,
        # no protocol in the comment) -- confirmed live (2026-09-01),
        # inspecting the actual persisted /etc/iptables/rules.v4: this
        # one was never cleaned up the way the DNAT rule's was, so
        # duplicate FORWARD ACCEPT rules for 80/443 had been sitting
        # there ever since (harmless -- both just ACCEPT the same
        # traffic -- but exactly the config drift this repo tries not
        # to accumulate).
        tasks = (ROLE_ROOT / "tasks/main.yml").read_text(encoding="utf-8")

        reconcile_task = tasks.split(
            "Remove the pre-fix FORWARD accept rule shape", 1
        )[1].split("Relay ingress traffic to k3s via DNAT", 1)[0]
        self.assertIn("state: absent", reconcile_task)
        self.assertIn("chain: FORWARD", reconcile_task)
        self.assertIn("protocol: tcp", reconcile_task)
        self.assertIn("jump: ACCEPT", reconcile_task)

        # Reproduces the role's very first comment shape (commit
        # 4accac7) verbatim, not the current per-protocol template
        # (commit 84662f5 prefixed it with "tcp"/"udp") -- same reason
        # as the DNAT reconciliation's own comment.
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

    def test_dnat_rule_is_restricted_to_this_hosts_own_address(self) -> None:
        # Confirmed live (2026-09-05): without a destination filter,
        # this rule matched *any* packet with a matching
        # destination_port regardless of where it was actually
        # addressed -- including locally-originated outbound traffic
        # from this same host (any Docker container, or a host
        # process) making a real connection to the real internet on
        # port 80/443/53. Nextcloud AIO's own mastercontainer, trying
        # to reach the real ghcr.io on port 443, got silently
        # hairpinned into this cluster's own Traefik instead -- a real
        # TLS handshake completed, with a real but wrong certificate,
        # surfacing as an indefinite crash-restart loop. ansible_host
        # is the same address inventory already uses to reach this
        # host, not a separate literal that could drift out of sync.
        tasks = (ROLE_ROOT / "tasks/main.yml").read_text(encoding="utf-8")

        dnat_task = tasks.split(
            "Relay ingress traffic to k3s via DNAT", 1
        )[1]
        self.assertIn('destination: "{{ ansible_host }}"', dnat_task)

    def test_destination_unrestricted_dnat_rule_shape_is_explicitly_reconciled(
        self,
    ) -> None:
        # Same class of fix, and same reasoning, as the in_interface
        # reconciliation above: simply re-applying the corrected rule
        # would add it alongside the old, destination-unrestricted one
        # rather than replacing it, and since DNAT terminates further
        # PREROUTING processing, the old rule (evaluated first) would
        # keep winning.
        tasks = (ROLE_ROOT / "tasks/main.yml").read_text(encoding="utf-8")

        reconcile_task = tasks.split(
            "Remove the destination-unrestricted DNAT rule shape", 1
        )[1].split("Relay ingress traffic to k3s via DNAT", 1)[0]
        self.assertIn("state: absent", reconcile_task)
        self.assertIn("table: nat", reconcile_task)
        self.assertIn("chain: PREROUTING", reconcile_task)
        self.assertIn("jump: DNAT", reconcile_task)
        # Matches the shape live between the 2026-09-01 interface fix
        # and this one: in_interface present, destination absent.
        self.assertIn(
            'in_interface: "!{{ k3s_ingress_forward_target_bridge }}"',
            reconcile_task,
        )
        # Not "destination:" generically -- to_destination: (the DNAT
        # target, present in every version of this rule) contains that
        # substring too. This checks specifically for the new filter.
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
            "state: \"{{ 'present' if (k3s_ingress_forward_enabled | bool) "
            "else 'absent' }}\"",
            tasks,
        )

    def test_enabled_flag_is_bool_filtered_everywhere_its_evaluated(
        self,
    ) -> None:
        # -e k3s_ingress_forward_enabled=... always hands the role a
        # string, never a real boolean. Ansible's own `when:` rejects a
        # bare string result outright; a plain Jinja `if` doesn't --
        # it just checks truthiness, so an unfiltered `if
        # k3s_ingress_forward_enabled` would treat even the string
        # "false" as enabled. Every place this variable gates
        # present/absent state needs the same `| bool` filter, not
        # just the one Ansible's own strict when: forced a fix for.
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
        # A real, one-time consequence of the reconcile-result
        # registration bug above: a run that removed the old rule live
        # (before that bug was fixed) still skipped persisting, so the
        # saved iptables-persistent state on disk is stale relative to
        # the live table until this runs once, unconditionally, for
        # every host that already picked up the live fix through that
        # window.
        tasks = (ROLE_ROOT / "tasks/main.yml").read_text(encoding="utf-8")

        force_persist_task = tasks.split(
            "Force-persist once to catch the saved state up", 1
        )[1].split("Persist iptables rules across reboots", 1)[0]
        self.assertIn("netfilter-persistent", force_persist_task)
        self.assertIn("when: k3s_ingress_forward_enabled | bool", force_persist_task)

    def test_reconcile_task_result_is_registered_and_gates_persisting(
        self,
    ) -> None:
        # Confirmed live (2026-09-01): the reconciliation task removed
        # a real live rule (changed: true) but, since its result
        # wasn't registered/checked here, the persist step skipped --
        # a reboot before the next apply would have restored the old,
        # broken rule from iptables-persistent's own saved state.
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
