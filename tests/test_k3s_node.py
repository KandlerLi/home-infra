from __future__ import annotations

import unittest
from pathlib import Path

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
        # effect of a normal homelab apply.
        site = (PROJECT_ROOT / "ansible/playbooks/site.yml").read_text(
            encoding="utf-8"
        )

        self.assertNotIn("k3s", site)

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


if __name__ == "__main__":
    unittest.main()
