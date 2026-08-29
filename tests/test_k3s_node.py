from __future__ import annotations

import unittest
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
ROLE_ROOT = PROJECT_ROOT / "ansible/roles/k3s_node"


class K3sNodeTests(unittest.TestCase):
    def test_disk_path_is_pinned_to_dedicated_directory(self) -> None:
        # Same convention every other stateful service here follows:
        # persistent state goes on /mnt/black-hdd, never root.
        tasks = (ROLE_ROOT / "tasks/provision_vm.yml").read_text(encoding="utf-8")
        defaults = (ROLE_ROOT / "defaults/main.yml").read_text(
            encoding="utf-8"
        )

        self.assertIn(
            'k3s_node_vm_disk_path == "/mnt/black-hdd/k3s/k3s-node-1.qcow2"',
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

    def test_network_does_not_collide_with_lan_or_runner_network(
        self,
    ) -> None:
        defaults = (ROLE_ROOT / "defaults/main.yml").read_text(
            encoding="utf-8"
        )
        runner_defaults = (
            PROJECT_ROOT / "ansible/roles/github_runner/defaults/main.yml"
        ).read_text(encoding="utf-8")

        self.assertIn("192.168.101.1", defaults)
        self.assertNotIn("192.168.178.", defaults)
        self.assertNotIn("192.168.100.", defaults)
        # And the bridge/MAC are distinct from the runner VM's own,
        # confirmed directly against that role's real current defaults
        # rather than assumed.
        self.assertIn("virbr10", runner_defaults)
        self.assertIn("virbr11", defaults)
        self.assertIn('52:54:00:00:00:10', runner_defaults)
        self.assertIn('52:54:00:00:00:20', defaults)

    def test_reuses_the_same_cached_debian_image_as_github_runner(
        self,
    ) -> None:
        # Deliberate: both roles point at the same cache file so the
        # image is only ever downloaded once, not once per VM.
        defaults = (ROLE_ROOT / "defaults/main.yml").read_text(
            encoding="utf-8"
        )
        runner_defaults = (
            PROJECT_ROOT / "ansible/roles/github_runner/defaults/main.yml"
        ).read_text(encoding="utf-8")

        self.assertIn(
            "debian-13-genericcloud-amd64.qcow2", defaults
        )
        self.assertIn(
            "debian-13-genericcloud-amd64.qcow2", runner_defaults
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
