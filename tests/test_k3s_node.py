from __future__ import annotations

import unittest
from pathlib import Path

import yaml

PROJECT_ROOT = Path(__file__).resolve().parents[1]
ROLE_ROOT = PROJECT_ROOT / "ansible/roles/k3s_node"


class K3sNodeTests(unittest.TestCase):
    def test_disk_path_is_pinned_to_dedicated_directory(self) -> None:
        # Same convention every other stateful service here follows:
        # persistent state goes on /mnt/black-hdd, never root. The check
        # is derived from k3s_node_vm_name rather than a literal
        # "k3s-node-1" string specifically so it holds for k3s-node-2
        # (the agent node) too, without weakening the original
        # protection for either.
        tasks = (ROLE_ROOT / "tasks/provision_vm.yml").read_text(encoding="utf-8")
        defaults = (ROLE_ROOT / "defaults/main.yml").read_text(
            encoding="utf-8"
        )

        self.assertIn(
            "k3s_node_vm_disk_path\n        == (k3s_node_vm_storage_dir "
            "~ '/' ~ k3s_node_vm_name ~ '.qcow2')",
            tasks,
        )
        self.assertIn(
            "k3s_node_vm_disk_path: /mnt/black-hdd/k3s/k3s-node-1.qcow2",
            defaults,
        )

    def test_disk_existence_check_skips_hashing_the_whole_qcow2_file(
        self,
    ) -> None:
        # get_checksum defaults to true on ansible.builtin.stat -- left
        # alone, this task would SHA1 the entire multi-GB VM disk over
        # SSH on every run just to answer .stat.exists.
        tasks = (ROLE_ROOT / "tasks/provision_vm.yml").read_text(encoding="utf-8")

        self.assertIn(
            'path: "{{ k3s_node_vm_disk_path }}"\n'
            "    # Only .stat.exists is ever read below",
            tasks,
        )
        self.assertIn("get_checksum: false", tasks)

    def test_storage_mount_is_verified_before_disk_creation(self) -> None:
        # github_runner learned this the hard way in an earlier repo
        # history -- refuse to create the VM disk on the host's root
        # filesystem if /mnt/black-hdd isn't actually mounted.
        tasks = (ROLE_ROOT / "tasks/provision_vm.yml").read_text(encoding="utf-8")

        self.assertIn("findmnt", tasks)
        self.assertIn(
            "Refusing to create the\n      VM disk on the host's root "
            "filesystem.",
            tasks,
        )

    def test_vm_and_disk_recovery_state_is_validated(self) -> None:
        # Never silently delete or recreate a disk/domain that's out of
        # sync with the other -- same safety property github_runner's
        # provision_vm mode enforces.
        tasks = (ROLE_ROOT / "tasks/provision_vm.yml").read_text(encoding="utf-8")

        self.assertIn("Validate VM and disk recovery state", tasks)
        self.assertIn("will not\n      delete or overwrite either one", tasks)

    def test_existing_definitions_are_validated_before_reuse(self) -> None:
        # Refuse to modify an existing VM or network definition that
        # doesn't match the configured disk/network/MAC -- prevents this
        # role from silently taking over something it didn't create.
        tasks = (ROLE_ROOT / "tasks/provision_vm.yml").read_text(encoding="utf-8")

        self.assertIn(
            "Validate existing k3s node virtual machine definition", tasks
        )
        self.assertIn("Validate existing k3s network definition", tasks)

    def test_vcpu_resize_shuts_down_redefines_and_restarts_an_existing_vm(
        self,
    ) -> None:
        # 2026-09-08: the "Define" task below only ever fires for a
        # brand-new VM (when: ... not in list_vms) -- vcpu
        # placement="static" has no live hotplug path, so without this
        # block, bumping k3s_node_vm_vcpus for an already-running node
        # would silently do nothing at all on re-apply.
        tasks = (ROLE_ROOT / "tasks/provision_vm.yml").read_text(encoding="utf-8")

        self.assertIn(
            "Extract the current vCPU count from the existing domain definition",
            tasks,
        )
        self.assertIn(
            "Gracefully shut down the k3s node VM to apply a changed vCPU allocation",
            tasks,
        )
        self.assertIn(
            "Redefine the k3s node VM with its updated vCPU allocation", tasks
        )
        self.assertIn(
            "k3s_node_existing_vm_vcpus | int != k3s_node_vm_vcpus | int", tasks
        )

    def test_vcpu_resize_wait_is_skipped_under_check_mode(self) -> None:
        # A simulated shutdown under --check never actually stops the
        # domain, so polling for real would burn the full retries/delay
        # budget (5min) on every syntax-check/dry-run while a resize is
        # pending -- confirmed this is guarded, not just assumed safe
        # because the module itself supports check_mode.
        tasks = (ROLE_ROOT / "tasks/provision_vm.yml").read_text(encoding="utf-8")

        self.assertIn(
            "Wait for the k3s node VM to actually stop", tasks
        )
        self.assertIn(
            "not ansible_check_mode\n    and k3s_node_vm_name in "
            "k3s_node_defined_vms.list_vms\n    and k3s_node_existing_vm_vcpus",
            tasks,
        )

    def test_node1_vcpus_bumped_node2_untouched(self) -> None:
        # 2026-09-08: node-1 was requesting 94% of its 2-vCPU budget
        # while the physical host itself had real headroom (~23% CPU,
        # ~5.3GB RAM available) -- scoped to node-1 specifically via
        # the play's own override, not the role default, so node-2
        # (the CI runner node) stays untouched.
        k3s_playbook = (
            PROJECT_ROOT / "ansible/playbooks/k3s.yml"
        ).read_text(encoding="utf-8")
        defaults = (ROLE_ROOT / "defaults/main.yml").read_text(encoding="utf-8")

        self.assertIn("k3s_node_vm_vcpus: 3", k3s_playbook)
        self.assertIn("k3s_node_vm_vcpus: 2", defaults)
        self.assertNotIn("k3s_node_vm_vcpus", k3s_playbook.split("Second node:")[1])

    def test_disk_creation_never_overwrites_an_existing_disk(self) -> None:
        tasks = (ROLE_ROOT / "tasks/provision_vm.yml").read_text(encoding="utf-8")

        create_disk_task = tasks.split(
            "Create k3s node system disk from Debian cloud image", 1
        )[1].split("- name:", 1)[0]
        self.assertIn("creates: \"{{ k3s_node_vm_disk_path }}\"", create_disk_task)

    def test_network_does_not_collide_with_lan(self) -> None:
        # 192.168.100.0/24 (virbr10) was the old VM-based github_runner
        # role's own isolated network -- that role is gone now (moved
        # to infra/k3s-apps entirely), so this only guards against the
        # home LAN itself these days, not a cross-role collision.
        defaults = (ROLE_ROOT / "defaults/main.yml").read_text(
            encoding="utf-8"
        )

        self.assertIn("192.168.101.1", defaults)
        self.assertNotIn("192.168.178.", defaults)
        self.assertIn("virbr11", defaults)
        self.assertIn('52:54:00:00:00:20', defaults)

    def test_network_autostart_bug_has_a_plain_virsh_workaround(self) -> None:
        # community.libvirt.virt_net's own autostart: true parameter
        # (the "Activate and enable k3s libvirt network" task, just
        # above this one) is a known, long-standing upstream bug --
        # confirmed live 2026-09-04: Autostart still showed "no"
        # immediately after that exact task ran successfully, which is
        # what left both k3s VMs shut off after a real host reboot
        # despite this supposedly already being fixed on 2026-09-01.
        # Matches ansible-collections/community.libvirt#107 and
        # ansible/ansible#27064.
        tasks = (ROLE_ROOT / "tasks/provision_vm.yml").read_text(
            encoding="utf-8"
        )

        workaround_task = tasks.split(
            "Work around community.libvirt.virt_net's autostart bug", 1
        )[1]
        self.assertIn("virsh", workaround_task)
        self.assertIn("net-autostart", workaround_task)
        self.assertNotIn("community.libvirt.virt_net", workaround_task)
        self.assertIn(
            r"k3s_node_network_info.stdout is search('Autostart:\s+no')",
            workaround_task,
        )

    def test_debian_image_is_cached_locally(self) -> None:
        # Used to be shared with the old VM-based github_runner role's
        # own identical cache path (both roles pointed at the same
        # file so the image was only ever downloaded once) -- that
        # role is gone now, so this just confirms the cache setup
        # itself, not the cross-role sharing.
        defaults = (ROLE_ROOT / "defaults/main.yml").read_text(
            encoding="utf-8"
        )

        self.assertIn(
            "debian-13-genericcloud-amd64.qcow2", defaults
        )
        self.assertIn(
            "k3s_node_image_cache_dir: /var/lib/libvirt/cloud-images",
            defaults,
        )

    def test_playbook_is_not_imported_into_site_yml(self) -> None:
        # This is a learning project -- it must never run as a side
        # effect of a normal homelab apply. Checks the actual roles
        # list structurally, not a blind substring scan of the whole
        # file -- found live 2026-09-09: a comment merely mentioning
        # "infra/k3s-apps" (unrelated to this concern -- k3s-apps is a
        # real, always-applied production repo, not the learning
        # project this test guards against) false-positived a plain
        # `assertNotIn("k3s", site)` the moment such a comment landed
        # in site.yml.
        site = yaml.safe_load(
            (PROJECT_ROOT / "ansible/playbooks/site.yml").read_text(
                encoding="utf-8"
            )
        )
        roles = [play.get("roles", []) for play in site]
        role_names = [
            role if isinstance(role, str) else role.get("role")
            for play_roles in roles
            for role in play_roles
        ]

        self.assertNotIn("k3s_node", role_names)

    def test_cloud_init_never_installs_k3s_directly(self) -> None:
        # The VM's cloud-init only ever prepares a bare Debian box --
        # k3s installation happens later, deliberately, through
        # configure_guest.yml's checksum-verified binary download, never
        # baked into first boot.
        user_data = (
            ROLE_ROOT / "templates/cloud-init-user-data.yml.j2"
        ).read_text(encoding="utf-8")

        self.assertNotIn("get.k3s.io", user_data)
        self.assertNotIn("install.sh", user_data)
        self.assertNotIn("systemctl enable --now k3s", user_data)

    def test_role_mode_dispatches_between_provision_and_configure(
        self,
    ) -> None:
        # Same dual-mode shape as github_runner: one mode runs on the
        # homeserver, the other runs inside the guest.
        main_tasks = (ROLE_ROOT / "tasks/main.yml").read_text(
            encoding="utf-8"
        )

        self.assertIn(
            'k3s_node_role_mode in ["provision_vm", "configure_guest"]',
            main_tasks,
        )

    def test_k3s_binary_is_pinned_and_checksum_verified_by_url(
        self,
    ) -> None:
        # Same idiom provision_vm.yml already uses for the Debian cloud
        # image: point get_url's checksum param at k3s's own published
        # checksum file instead of hand-transcribing a hex digest here,
        # so there's no risk of a copy-paste error silently accepting
        # the wrong binary.
        defaults = (ROLE_ROOT / "defaults/main.yml").read_text(
            encoding="utf-8"
        )
        tasks = (ROLE_ROOT / "tasks/configure_guest.yml").read_text(
            encoding="utf-8"
        )

        self.assertIn("k3s_node_version: v1.36.4+k3s1", defaults)
        self.assertIn(
            'checksum: "sha256:{{ k3s_node_release_base_url }}'
            '/sha256sum-amd64.txt"',
            tasks,
        )
        self.assertNotIn("| sh", tasks)
        self.assertNotIn("curl", tasks)

    def test_k3s_release_url_percent_encodes_the_plus_sign(self) -> None:
        # GitHub release asset URLs need the "+" in a k3s tag
        # (e.g. v1.36.4+k3s1) escaped as %2B or the download 404s --
        # confirmed live against the real release asset URL.
        defaults = (ROLE_ROOT / "defaults/main.yml").read_text(
            encoding="utf-8"
        )

        self.assertIn(
            "k3s_node_version | replace('+', '%2B')", defaults
        )

    def test_systemd_unit_matches_upstream_resource_settings(self) -> None:
        # These aren't cosmetic -- KillMode=process and Delegate=yes are
        # required for containerd to manage its own child processes and
        # cgroups correctly; copied from k3s's own upstream unit rather
        # than guessed.
        unit = (ROLE_ROOT / "templates/k3s.service.j2").read_text(
            encoding="utf-8"
        )

        self.assertIn("Type=notify", unit)
        self.assertIn("KillMode=process", unit)
        self.assertIn("Delegate=yes", unit)
        self.assertIn("TasksMax=infinity", unit)
        self.assertIn("Restart=always", unit)

    def test_kubeconfig_is_readable_without_sudo_on_this_single_user_vm(
        self,
    ) -> None:
        config = (ROLE_ROOT / "templates/k3s-config.yaml.j2").read_text(
            encoding="utf-8"
        )
        defaults = (ROLE_ROOT / "defaults/main.yml").read_text(
            encoding="utf-8"
        )

        self.assertIn("write-kubeconfig-mode", config)
        self.assertIn('k3s_node_kubeconfig_mode: "0644"', defaults)

    def test_bundled_traefik_is_disabled_in_favor_of_k3s_apps_ingress(
        self,
    ) -> None:
        config = (ROLE_ROOT / "templates/k3s-config.yaml.j2").read_text(
            encoding="utf-8"
        )

        self.assertIn("disable:", config)
        self.assertIn("- traefik", config)

    def test_waits_for_node_ready_before_finishing(self) -> None:
        tasks = (ROLE_ROOT / "tasks/configure_guest.yml").read_text(
            encoding="utf-8"
        )

        self.assertIn("until: \"'Ready' in k3s_node_status.stdout\"", tasks)

    def test_playbook_runs_configure_guest_against_k3s_nodes_group(
        self,
    ) -> None:
        playbook = (
            PROJECT_ROOT / "ansible/playbooks/k3s.yml"
        ).read_text(encoding="utf-8")

        self.assertIn("hosts: k3s_nodes", playbook)
        self.assertIn("k3s_node_role_mode: configure_guest", playbook)

    def test_terraform_is_pinned_and_checksum_verified_by_url(self) -> None:
        # Same idiom as the k3s binary itself: point get_url's checksum
        # param at HashiCorp's own published checksum file rather than
        # hand-transcribing a hex digest.
        defaults = (ROLE_ROOT / "defaults/main.yml").read_text(
            encoding="utf-8"
        )
        tasks = (ROLE_ROOT / "tasks/configure_guest.yml").read_text(
            encoding="utf-8"
        )

        self.assertIn('k3s_node_terraform_version: "1.16.0"', defaults)
        self.assertIn(
            'checksum: "sha256:{{ k3s_node_terraform_checksum_url }}"',
            tasks,
        )
        self.assertNotIn("| sh", tasks)

    def test_terraform_extraction_is_idempotent(self) -> None:
        # Only re-extracts when the downloaded zip actually changed, or
        # the binary is missing -- not on every run.
        tasks = (ROLE_ROOT / "tasks/configure_guest.yml").read_text(
            encoding="utf-8"
        )

        extract_task = tasks.split("Extract Terraform binary", 1)[1]
        self.assertIn(
            "k3s_node_terraform_zip.changed or not "
            "k3s_node_terraform_binary.stat.exists",
            extract_task,
        )

    def test_agent_mode_defaults_off_and_join_vars_are_empty(self) -> None:
        # k3s-node-1 must be completely unaffected by default -- the
        # join_* vars are only ever filled in by
        # ansible/playbooks/k3s.yml's own agent-node play, never given
        # a real default here.
        defaults = (ROLE_ROOT / "defaults/main.yml").read_text(
            encoding="utf-8"
        )

        self.assertIn("k3s_node_join_mode: server", defaults)
        self.assertIn('k3s_node_join_server_url: ""', defaults)
        self.assertIn('k3s_node_join_token: ""', defaults)
        self.assertIn('k3s_node_agent_node_taint: ""', defaults)
        self.assertIn('k3s_node_agent_node_label: ""', defaults)
        self.assertIn("k3s_node_inventory_group: k3s_nodes", defaults)

    def test_agent_join_configuration_is_validated(self) -> None:
        tasks = (ROLE_ROOT / "tasks/configure_guest.yml").read_text(
            encoding="utf-8"
        )

        self.assertIn("Validate k3s agent join configuration", tasks)
        self.assertIn('when: k3s_node_join_mode == "agent"', tasks)

    def test_agent_unit_joins_with_a_taint_and_label(self) -> None:
        # The taint is what actually keeps every other workload off
        # this node -- infra/k3s-apps' own github_runner module supplies
        # the matching toleration.
        unit = (ROLE_ROOT / "templates/k3s-agent.service.j2").read_text(
            encoding="utf-8"
        )
        env = (ROLE_ROOT / "templates/k3s-agent.env.j2").read_text(
            encoding="utf-8"
        )

        self.assertIn("{{ k3s_node_binary_path }} agent", unit)
        self.assertIn("--node-taint {{ k3s_node_agent_node_taint }}", unit)
        self.assertIn("--node-label {{ k3s_node_agent_node_label }}", unit)
        self.assertIn(
            "EnvironmentFile={{ k3s_node_agent_env_path }}", unit
        )
        self.assertIn("K3S_URL={{ k3s_node_join_server_url }}", env)
        self.assertIn("K3S_TOKEN={{ k3s_node_join_token }}", env)

    def test_agent_env_file_is_not_world_readable(self) -> None:
        # Unlike k3s-node-1's own 0644 kubeconfig (a documented,
        # deliberate exception for a single-user learning node with no
        # real privilege boundary), this file holds the actual
        # cluster-join secret.
        tasks = (ROLE_ROOT / "tasks/configure_guest.yml").read_text(
            encoding="utf-8"
        )

        install_task = tasks.split(
            "Install k3s agent environment file", 1
        )[1].split("- name:", 1)[0]
        self.assertIn('mode: "0600"', install_task)

    def test_node_token_is_never_logged(self) -> None:
        playbook = (
            PROJECT_ROOT / "ansible/playbooks/k3s.yml"
        ).read_text(encoding="utf-8")

        token_play = playbook.split(
            "Retrieve k3s cluster join token", 1
        )[1].split("- name: Install k3s on the runner node", 1)[0]
        self.assertIn("no_log: true", token_play)
        self.assertIn(
            "/var/lib/rancher/k3s/server/node-token", token_play
        )

    def test_agent_node_lands_in_its_own_inventory_group(self) -> None:
        # A generic loop over both nodes would let configure_guest's
        # server-only tasks run against the agent by accident -- two
        # distinct groups (k3s_nodes / k3s_agent_nodes) rule that out
        # structurally instead of relying on a conditional alone.
        playbook = (
            PROJECT_ROOT / "ansible/playbooks/k3s.yml"
        ).read_text(encoding="utf-8")
        tasks = (ROLE_ROOT / "tasks/provision_vm.yml").read_text(
            encoding="utf-8"
        )

        self.assertIn("k3s_node_inventory_group: k3s_agent_nodes", playbook)
        self.assertIn("hosts: k3s_agent_nodes", playbook)
        self.assertIn(
            'groups:\n      - "{{ k3s_node_inventory_group }}"', tasks
        )

    def test_second_node_reservation_added_live_not_by_redefining(
        self,
    ) -> None:
        # A full net redefine only updates libvirt's persistent config,
        # not the already-running dnsmasq instance -- restarting the
        # network to pick it up could interrupt k3s-node-1's own active
        # connections. net-update --live --config avoids that entirely.
        tasks = (ROLE_ROOT / "tasks/provision_vm.yml").read_text(
            encoding="utf-8"
        )

        self.assertIn("net-update", tasks)
        self.assertIn("--live", tasks)
        self.assertIn("--config", tasks)
        self.assertIn(
            "when: k3s_node_network_name not in "
            "k3s_node_defined_networks.list_nets",
            tasks,
        )

    def test_second_node_uses_a_distinct_identity(self) -> None:
        playbook = (
            PROJECT_ROOT / "ansible/playbooks/k3s.yml"
        ).read_text(encoding="utf-8")

        self.assertIn("k3s_node_vm_name: k3s-node-2", playbook)
        self.assertIn(
            "k3s_node_vm_disk_path: /mnt/black-hdd/k3s/k3s-node-2.qcow2",
            playbook,
        )
        self.assertIn("k3s_node_vm_ip: 192.168.101.11", playbook)
        self.assertIn('k3s_node_vm_mac: "52:54:00:00:00:21"', playbook)
        # Same k3s_network/virbr11 as k3s-node-1 -- a deliberate choice
        # (shared cluster trust boundary already accepted), not a new
        # isolated network of its own.
        self.assertNotIn("192.168.100.", playbook)

    def test_nfs_client_is_installed_for_pv_support(self) -> None:
        # k3s's kubelet shells out to the host's mount.nfs helper (from
        # nfs-common) for NFS-backed PersistentVolumes -- without it,
        # the mount silently fails.
        tasks = (ROLE_ROOT / "tasks/configure_guest.yml").read_text(
            encoding="utf-8"
        )

        self.assertIn("nfs-common", tasks)

    def test_systemd_resolved_stub_listener_is_disabled(self) -> None:
        # glibc's NSS resolver (curl, dig, getent) talks to
        # systemd-resolved's smarter D-Bus/Varlink interface regardless
        # of the stub listener -- but Go binaries (containerd included)
        # use Go's own pure-Go resolver, which fires a raw UDP query
        # straight at whatever's in /etc/resolv.conf, bypassing that
        # smarter path. Confirmed live (2026-09-01): with a real,
        # working network underneath, containerd's own image pulls
        # still failed repeatedly with "lookup ghcr.io: Try again" --
        # stub-listener flakiness a full k3s/containerd restart didn't
        # clear, since the stub itself was the problem.
        tasks = (ROLE_ROOT / "tasks/configure_guest.yml").read_text(
            encoding="utf-8"
        )

        self.assertIn("DNSStubListener=no", tasks)
        self.assertIn(
            "/etc/systemd/resolved.conf.d/disable-stub-listener.conf",
            tasks,
        )

    def test_resolv_conf_points_at_the_real_nameserver_file_not_the_stub(
        self,
    ) -> None:
        # /run/systemd/resolve/resolv.conf always holds the real
        # upstream nameservers, maintained by systemd-resolved
        # regardless of the stub listener setting -- unlike
        # stub-resolv.conf (127.0.0.53), Go's raw-UDP resolver talks to
        # it directly without hitting the stub's own flakiness.
        tasks = (ROLE_ROOT / "tasks/configure_guest.yml").read_text(
            encoding="utf-8"
        )

        resolv_conf_task = tasks.split(
            "Point /etc/resolv.conf at systemd-resolved's real-nameserver "
            "file",
            1,
        )[1]
        self.assertIn("path: /etc/resolv.conf", resolv_conf_task)
        self.assertIn("src: /run/systemd/resolve/resolv.conf", resolv_conf_task)
        self.assertIn("state: link", resolv_conf_task)
        self.assertIn("force: true", resolv_conf_task)

    def test_systemd_resolved_restart_checks_live_reality_not_just_this_runs_changes(
        self,
    ) -> None:
        # Confirmed live (2026-09-01): a run whose copy/file tasks both
        # reported ok (config already matched, from an earlier apply)
        # still left the stub listening, because that earlier apply's
        # own restart never actually happened either. Gating the
        # restart purely on .changed from this run's own tasks would
        # silently never self-heal a node stuck in that state -- a
        # direct probe of whether the stub is still listening is
        # required too.
        tasks = (ROLE_ROOT / "tasks/configure_guest.yml").read_text(
            encoding="utf-8"
        )

        probe_task = tasks.split(
            "Check whether systemd-resolved's stub listener is still "
            "active",
            1,
        )[1].split(
            "Restart systemd-resolved when its config changed or its "
            "stub listener is still active",
            1,
        )[0]
        self.assertIn("host: 127.0.0.53", probe_task)
        self.assertIn("port: 53", probe_task)
        self.assertIn("register: k3s_node_resolved_stub_probe", probe_task)
        self.assertIn("ignore_errors: true", probe_task)

        restart_task = tasks.split(
            "Restart systemd-resolved when its config changed or its "
            "stub listener is still active",
            1,
        )[1]
        self.assertIn("name: systemd-resolved", restart_task)
        self.assertIn("state: restarted", restart_task)
        self.assertIn("k3s_node_resolved_stub_config.changed", restart_task)
        self.assertIn("k3s_node_resolv_conf_link.changed", restart_task)
        self.assertIn(
            "k3s_node_resolved_stub_probe is succeeded", restart_task
        )


if __name__ == "__main__":
    unittest.main()
