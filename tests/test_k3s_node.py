from __future__ import annotations

import unittest
from pathlib import Path

import yaml

PROJECT_ROOT = Path(__file__).resolve().parents[1]
ROLE_ROOT = PROJECT_ROOT / "ansible/roles/k3s_node"


class K3sNodeTests(unittest.TestCase):

    def test_ready_waits_run_in_check_mode(self) -> None:
        # A dry-run skips kubectl, so the ready wait must not poll empty output.
        tasks = (ROLE_ROOT / "tasks/configure_guest.yml").read_text(
            encoding="utf-8"
        )

        for name in (
            "Wait for the k3s node to report Ready",
            "Wait for the k3s agent service to become active",
        ):
            task = tasks.split(name, 1)[1].split("- name:", 1)[0]
            self.assertIn("check_mode: false", task, name)

    def test_storage_dir_mode_is_not_fought_by_a_named_user_acl(self) -> None:
        # A named-user rwx entry raises the ACL mask, which chmod 0750 lowers again on every run.
        tasks = (ROLE_ROOT / "tasks/provision_vm.yml").read_text(
            encoding="utf-8"
        )

        self.assertNotIn("permissions: rwx", tasks)
        self.assertNotIn("Allow libvirt full access to k3s node VM storage", tasks)

    def test_disk_path_is_pinned_to_dedicated_directory(self) -> None:
        # Persistent state on /mnt/black-hdd, derived from the VM name so it holds for both nodes.
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

    def test_storage_mount_is_an_allowlist_including_root_for_the_ssd(self) -> None:
        tasks = (ROLE_ROOT / "tasks/provision_vm.yml").read_text(encoding="utf-8")
        defaults = yaml.safe_load((ROLE_ROOT / "defaults/main.yml").read_text(encoding="utf-8"))

        self.assertEqual(defaults["k3s_node_allowed_storage_mounts"], ["/mnt/black-hdd", "/"])
        self.assertIn("k3s_node_storage_mount in k3s_node_allowed_storage_mounts", tasks)
        self.assertNotIn('k3s_node_storage_mount == "/mnt/black-hdd"', tasks)
        acl = tasks.split("- name: Allow libvirt to traverse the HDD mount", 1)[1].split("\n- name:", 1)[0]
        self.assertIn('when: k3s_node_storage_mount != "/"', acl)

    def test_disk_move_is_opt_in_copies_sparse_and_keeps_the_previous_disk(self) -> None:
        tasks = (ROLE_ROOT / "tasks/provision_vm.yml").read_text(encoding="utf-8")
        defaults = yaml.safe_load((ROLE_ROOT / "defaults/main.yml").read_text(encoding="utf-8"))
        move = tasks.split("- name: Move the k3s node disk to its new path", 1)[1].split("\n- name: Check k3s node disk", 1)[0]

        self.assertEqual(defaults["k3s_node_vm_previous_disk_path"], "")
        self.assertIn("k3s_node_vm_previous_disk_path | length > 0", move)
        self.assertIn("k3s_node_vm_previous_disk_path in k3s_node_premove_vm_xml.get_xml", move)
        self.assertIn("--sparse=always", move)
        self.assertIn('creates: "{{ k3s_node_vm_disk_path }}"', move)
        # Never deletes or moves the previous disk.
        self.assertNotIn("state: absent", move)
        # Only the .partial copy is ever renamed, never the previous disk.
        self.assertIn('- "{{ k3s_node_vm_disk_path }}.partial"', move)
        self.assertNotIn('- mv\n          - "{{ k3s_node_vm_previous_disk_path }}"', move)
        # Shutdown is confirmed before copying.
        self.assertLess(move.index("Wait for the k3s node VM to stop"), move.index("Copy the k3s node disk"))
        # It runs before the disk/domain consistency check, which then sees the new path.
        self.assertLess(
            tasks.index("Move the k3s node disk to its new path"),
            tasks.index("Validate VM and disk recovery state"),
        )
        # The long copy is polled instead of holding one SSH channel open.
        copy = move.split("- name: Copy the k3s node disk to its new path", 1)[1].split("\n    - name:", 1)[0]
        self.assertIn("async:", copy)
        self.assertIn("poll:", copy)
        # A dry run stops at the move instead of failing on the not-yet-copied disk.
        end = move.split("- name: End the dry run for this node at the pending disk move", 1)[1]
        self.assertIn("ansible.builtin.meta: end_role", end)
        self.assertIn("when: ansible_check_mode and k3s_node_vm_move_pending | bool", end)
        self.assertLess(move.index("Redefine the k3s node VM with its new disk path"), move.index("End the dry run"))

    def test_disk_existence_check_skips_hashing_the_whole_qcow2_file(
        self,
    ) -> None:
        # Without get_checksum: false, stat would hash the whole multi-GB disk on every run.
        tasks = (ROLE_ROOT / "tasks/provision_vm.yml").read_text(encoding="utf-8")

        stat_task = tasks.split("- name: Check k3s node disk\n")[1].split("\n- name:")[0]
        self.assertIn('path: "{{ k3s_node_vm_disk_path }}"', stat_task)
        self.assertIn("get_checksum: false", stat_task)

    def test_storage_mount_is_verified_before_disk_creation(self) -> None:
        # Never create the VM disk on the root filesystem if /mnt/black-hdd isn't mounted.
        tasks = (ROLE_ROOT / "tasks/provision_vm.yml").read_text(encoding="utf-8")

        self.assertIn("findmnt", tasks)
        self.assertIn(
            "Refusing to create the\n      VM disk on the host's root "
            "filesystem.",
            tasks,
        )

    def test_vm_and_disk_recovery_state_is_validated(self) -> None:
        # Never silently delete or recreate a disk or domain that is out of sync with the other.
        tasks = (ROLE_ROOT / "tasks/provision_vm.yml").read_text(encoding="utf-8")

        self.assertIn("Validate VM and disk recovery state", tasks)
        self.assertIn("will not\n      delete or overwrite either one", tasks)

    def test_existing_definitions_are_validated_before_reuse(self) -> None:
        # Never take over an existing VM or network definition that doesn't match.
        tasks = (ROLE_ROOT / "tasks/provision_vm.yml").read_text(encoding="utf-8")

        self.assertIn(
            "Validate existing k3s node virtual machine definition", tasks
        )
        self.assertIn("Validate existing k3s network definition", tasks)

    def test_vcpu_resize_shuts_down_redefines_and_restarts_an_existing_vm(
        self,
    ) -> None:
        # Static vCPUs have no hotplug: a change needs shutdown, redefine and start.
        tasks = (ROLE_ROOT / "tasks/provision_vm.yml").read_text(encoding="utf-8")

        self.assertIn(
            "Extract the current vCPU count from the existing domain definition",
            tasks,
        )
        self.assertIn(
            "Gracefully shut down the k3s node VM to apply a changed vCPU, "
            "memory or disk allocation",
            tasks,
        )
        self.assertIn(
            "Redefine the k3s node VM with its updated vCPU, memory and disk "
            "allocation",
            tasks,
        )
        self.assertIn(
            "k3s_node_existing_vm_vcpus | int != k3s_node_vm_vcpus | int", tasks
        )

    def test_memory_only_change_also_triggers_the_resize(self) -> None:
        # A memory-only change must trigger the same resize path.
        tasks = (ROLE_ROOT / "tasks/provision_vm.yml").read_text(encoding="utf-8")

        self.assertIn(
            "k3s_node_existing_vm_memory_mb | int != k3s_node_vm_memory_mb | int",
            tasks,
        )
        for step in ("shut down", "Redefine", "actually stop"):
            self.assertIn(step, tasks)
        self.assertEqual(tasks.count("k3s_node_vm_resize_pending | bool"), 3)

    def test_disk_size_change_grows_an_existing_vm_only_while_it_is_stopped(
        self,
    ) -> None:
        # A disk size change must grow an existing VM, not only apply at creation.
        tasks = (ROLE_ROOT / "tasks/provision_vm.yml").read_text(encoding="utf-8")

        self.assertIn("k3s_node_vm_disk_grow_pending | bool }}", tasks)
        self.assertLess(
            tasks.index("Wait for the k3s node VM to actually stop"),
            tasks.index("Grow the k3s node system disk"),
        )
        self.assertLess(
            tasks.index("Grow the k3s node system disk"),
            tasks.index("Redefine the k3s node VM with its updated"),
        )
        grow = tasks[tasks.index("- name: Grow the k3s node system disk") :]
        grow = grow[: grow.index("\n\n")]
        self.assertIn("not ansible_check_mode", grow)
        self.assertIn("k3s_node_vm_disk_grow_pending | bool", grow)
        # Reads the size of a disk the running VM holds open.
        self.assertIn("- -U\n", tasks)
        self.assertIn("--output=json", tasks)

    def test_a_smaller_disk_size_is_refused_not_applied(self) -> None:
        tasks = (ROLE_ROOT / "tasks/provision_vm.yml").read_text(encoding="utf-8")

        self.assertIn("Refuse to shrink the existing k3s node disk", tasks)
        self.assertIn(
            "k3s_node_existing_vm_disk_gb | int <= k3s_node_vm_disk_size_gb | int",
            tasks,
        )
        # The refusal has to come before anything shuts the VM down.
        self.assertLess(
            tasks.index("Refuse to shrink the existing k3s node disk"),
            tasks.index("Gracefully shut down the k3s node VM"),
        )

    def test_guest_grows_its_partition_and_filesystem_idempotently(self) -> None:
        guest = (ROLE_ROOT / "tasks/configure_guest.yml").read_text(encoding="utf-8")

        self.assertIn("Grow the root partition to fill the disk", guest)
        self.assertIn("Grow the root filesystem to fill its partition", guest)
        # growpart exits 1 with NOCHANGE on an already-grown node.
        self.assertIn("NOCHANGE", guest)
        self.assertIn("Nothing to do", guest)
        defaults = (ROLE_ROOT / "defaults/main.yml").read_text(encoding="utf-8")
        self.assertIn("k3s_node_root_disk: /dev/vda", defaults)
        self.assertIn("k3s_node_root_partition: 1", defaults)

    def test_only_the_runner_node_gets_the_bigger_disk(self) -> None:
        k3s_playbook = (PROJECT_ROOT / "ansible/playbooks/k3s.yml").read_text(
            encoding="utf-8"
        )

        node2 = k3s_playbook[k3s_playbook.index("k3s_node_vm_name: k3s-node-2") :]
        node2 = node2[: node2.index("k3s_node_inventory_group")]
        self.assertIn("k3s_node_vm_disk_size_gb: 100", node2)
        # node-1 keeps the default; an override there would resize a disk nobody asked to change.
        node1 = k3s_playbook[: k3s_playbook.index("k3s_node_vm_name: k3s-node-2")]
        self.assertNotIn("k3s_node_vm_disk_size_gb", node1)

    def test_unparseable_memory_unit_fails_instead_of_rebooting_every_run(
        self,
    ) -> None:
        # A misread memory value would look like a pending resize and restart both nodes every run.
        tasks = (ROLE_ROOT / "tasks/provision_vm.yml").read_text(encoding="utf-8")

        self.assertIn("Verify the existing domain reports its memory in KiB", tasks)
        self.assertIn("<memory unit='KiB'>", tasks)

    def test_vcpu_resize_wait_is_skipped_under_check_mode(self) -> None:
        # Under --check the domain never stops, so polling would burn the whole retry budget.
        tasks = (ROLE_ROOT / "tasks/provision_vm.yml").read_text(encoding="utf-8")

        self.assertIn(
            "Wait for the k3s node VM to actually stop", tasks
        )
        self.assertIn(
            "not ansible_check_mode\n    and k3s_node_vm_name in "
            "k3s_node_defined_vms.list_vms\n    and k3s_node_vm_resize_pending",
            tasks,
        )

    def test_node1_vcpus_bumped_node2_untouched(self) -> None:
        # Only node-1 gets the extra vCPU; node-2 (the runner node) stays on the role default.
        k3s_playbook = (
            PROJECT_ROOT / "ansible/playbooks/k3s.yml"
        ).read_text(encoding="utf-8")
        defaults = (ROLE_ROOT / "defaults/main.yml").read_text(encoding="utf-8")

        self.assertIn("k3s_node_vm_vcpus: 3", k3s_playbook)
        self.assertIn("k3s_node_vm_vcpus: 2", defaults)
        self.assertNotIn("k3s_node_vm_vcpus", k3s_playbook.split("name: Provision k3s runner node")[1])

    def test_disk_creation_never_overwrites_an_existing_disk(self) -> None:
        tasks = (ROLE_ROOT / "tasks/provision_vm.yml").read_text(encoding="utf-8")

        create_disk_task = tasks.split(
            "Create k3s node system disk from Debian cloud image", 1
        )[1].split("- name:", 1)[0]
        self.assertIn("creates: \"{{ k3s_node_vm_disk_path }}\"", create_disk_task)

    def test_network_does_not_collide_with_lan(self) -> None:
        # Guards against overlapping the home LAN.
        defaults = (ROLE_ROOT / "defaults/main.yml").read_text(
            encoding="utf-8"
        )

        self.assertIn("192.168.101.1", defaults)
        self.assertNotIn("192.168.178.", defaults)
        self.assertIn("virbr11", defaults)
        self.assertIn('52:54:00:00:00:20', defaults)

    def test_network_autostart_bug_has_a_plain_virsh_workaround(self) -> None:
        # virt_net's autostart: true doesn't stick, so a plain virsh call backs it up.
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
        # Checks the roles list structurally; a substring scan matched a comment once.
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
        # Cloud-init prepares a bare Debian box; k3s comes later via a verified download.
        user_data = (
            ROLE_ROOT / "templates/cloud-init-user-data.yml.j2"
        ).read_text(encoding="utf-8")

        self.assertNotIn("get.k3s.io", user_data)
        self.assertNotIn("install.sh", user_data)
        self.assertNotIn("systemctl enable --now k3s", user_data)

    def test_role_mode_dispatches_between_provision_and_configure(
        self,
    ) -> None:
        # One mode runs on the homeserver, the other inside the guest.
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
        # Checksum from k3s's published file, never a hand-copied digest.
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
        # The "+" in a k3s tag must be %2B in the release URL, or the download 404s.
        defaults = (ROLE_ROOT / "defaults/main.yml").read_text(
            encoding="utf-8"
        )

        self.assertIn(
            "k3s_node_version | replace('+', '%2B')", defaults
        )

    def test_systemd_unit_matches_upstream_resource_settings(self) -> None:
        # KillMode=process and Delegate=yes are what containerd needs, copied from upstream.
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
        # Checksum from HashiCorp's published file, like the k3s binary.
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
        # Re-extracts only when the zip changed or the binary is missing.
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
        # node-1 is unaffected by default; join vars come only from the agent-node play.
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
        # The taint keeps everything else off; k3s-bootstrap's runner module tolerates it.
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
        # Holds the cluster-join secret, unlike node-1's deliberately world-readable kubeconfig.
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
        # Separate groups keep server-only tasks off the agent structurally.
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
        # net-update --live --config adds the reservation without restarting node-1's network.
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
            "k3s_node_vm_disk_path: /var/lib/libvirt/k3s/k3s-node-2.qcow2",
            playbook,
        )
        self.assertIn("k3s_node_vm_ip: 192.168.101.11", playbook)
        self.assertIn('k3s_node_vm_mac: "52:54:00:00:00:21"', playbook)
        # Same network and trust boundary as node-1, by choice.
        self.assertNotIn("192.168.100.", playbook)

    def test_nfs_client_is_installed_for_pv_support(self) -> None:
        # kubelet needs the host's mount.nfs for NFS PersistentVolumes.
        tasks = (ROLE_ROOT / "tasks/configure_guest.yml").read_text(
            encoding="utf-8"
        )

        self.assertIn("nfs-common", tasks)

    def test_systemd_resolved_stub_listener_is_disabled(self) -> None:
        # Go's resolver queries resolv.conf's server over raw UDP, which the stub listener mishandles.
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
        # The real upstream nameservers, which Go's resolver can query directly.
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
        # Restart on the live listener state, not only on file changes: an earlier restart had never run.
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
        # A TCP connect is no proof on node-1: Blocky's hostPort DNAT answers it.
        self.assertIn("- ss", probe_task)
        self.assertIn('"sport = :53"', probe_task)
        self.assertNotIn("wait_for", probe_task)
        self.assertIn("register: k3s_node_resolved_stub_probe", probe_task)
        self.assertIn("check_mode: false", probe_task)

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
            "'127.0.0.53' in k3s_node_resolved_stub_probe.stdout", restart_task
        )


    def test_kubelet_stops_pods_itself_at_vm_shutdown(self) -> None:
        # Without it, systemd unmounted NFS under running Pods and node-1 hung until SIGKILL.
        tasks = (ROLE_ROOT / "tasks/configure_guest.yml").read_text(
            encoding="utf-8"
        )
        defaults = (ROLE_ROOT / "defaults/main.yml").read_text(encoding="utf-8")

        dropin = tasks.split("Enable kubelet graceful node shutdown", 1)[1].split(
            "- name:", 1
        )[0]
        self.assertIn("kubelet.conf.d/50-graceful-node-shutdown.conf", dropin)
        self.assertIn("kind: KubeletConfiguration", dropin)
        self.assertIn("shutdownGracePeriod:", dropin)
        self.assertIn("shutdownGracePeriodCriticalPods:", dropin)
        self.assertIn("InhibitDelayMaxSec=", tasks)
        # Must sort after unattended-upgrades-logind-maxdelay.conf (30 s), or that file wins.
        logind = tasks.split(
            "Let kubelet's shutdown inhibitor hold the VM shutdown long enough", 1
        )[1].split("- name:", 1)[0]
        dest = logind.split("dest: /etc/systemd/logind.conf.d/", 1)[1].split()[0]
        self.assertGreater(dest, "unattended-upgrades-logind-maxdelay.conf")

        grace = int(
            defaults.split("k3s_node_shutdown_grace_period: ", 1)[1].split("s", 1)[0]
        )
        inhibit = int(
            defaults.split("k3s_node_logind_inhibit_delay_max_sec: ", 1)[1].split()[0]
        )
        self.assertGreater(inhibit, grace)

        restart = tasks.split("Restart k3s when its binary, config, or unit changed", 1)[1]
        self.assertIn("k3s_node_kubelet_shutdown_dropin.changed", restart)
        self.assertIn("k3s_node_logind_restart is changed", restart)
        self.assertLess(
            tasks.index("Restart logind when its live inhibitor cap is below"),
            tasks.index("Restart k3s when its binary, config, or unit changed"),
        )

    def test_logind_is_restarted_on_its_live_cap_not_reloaded(self) -> None:
        # logind kept 30 s across a SIGHUP reload, so kubelet disabled graceful shutdown.
        tasks = (ROLE_ROOT / "tasks/configure_guest.yml").read_text(
            encoding="utf-8"
        )

        self.assertNotIn("--signal=SIGHUP", tasks)
        probe = tasks.split("Read logind's live InhibitDelayMaxSec", 1)[1].split(
            "- name:", 1
        )[0]
        self.assertIn("InhibitDelayMaxUSec", probe)
        self.assertIn("check_mode: false", probe)
        restart = tasks.split(
            "Restart logind when its live inhibitor cap is below", 1
        )[1].split("- name:", 1)[0]
        self.assertIn("name: systemd-logind", restart)
        self.assertIn("state: restarted", restart)
        self.assertIn("k3s_node_logind_inhibit_delay_max_sec | int) * 1000000", restart)

    def test_vm_files_use_the_group_libvirt_sets_at_vm_start(self) -> None:
        # libvirt chowns disk and seed ISO to libvirt-qemu on every VM start; kvm here meant a "changed" after each reboot.
        tasks = (ROLE_ROOT / "tasks/provision_vm.yml").read_text(encoding="utf-8")
        defaults = (ROLE_ROOT / "defaults/main.yml").read_text(encoding="utf-8")

        self.assertIn("k3s_node_vm_file_group: libvirt-qemu", defaults)
        for name in (
            "Set k3s node cloud-init seed image permissions",
            "Set k3s node system disk permissions",
        ):
            task = tasks.split(name, 1)[1].split("- name:", 1)[0]
            self.assertIn('group: "{{ k3s_node_vm_file_group }}"', task, name)
        storage_dir = tasks.split(
            "Create dedicated k3s node VM storage directory", 1
        )[1].split("- name:", 1)[0]
        self.assertIn('group: "{{ k3s_node_libvirt_group }}"', storage_dir)

    def test_ran_one_time_logind_cleanup_is_gone(self) -> None:
        tasks = (ROLE_ROOT / "tasks/configure_guest.yml").read_text(
            encoding="utf-8"
        )

        self.assertNotIn("logind.conf.d/kubelet-graceful-shutdown.conf", tasks)

if __name__ == "__main__":
    unittest.main()
