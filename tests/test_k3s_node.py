from __future__ import annotations

import unittest
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
ROLE_ROOT = PROJECT_ROOT / "ansible/roles/k3s_node"


class K3sNodeTests(unittest.TestCase):
    def test_disk_path_is_pinned_to_dedicated_directory(self) -> None:
        # Same convention every other stateful service here follows:
        # persistent state goes on /mnt/black-hdd, never root.
        tasks = (ROLE_ROOT / "tasks/main.yml").read_text(encoding="utf-8")
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
        tasks = (ROLE_ROOT / "tasks/main.yml").read_text(encoding="utf-8")

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
        tasks = (ROLE_ROOT / "tasks/main.yml").read_text(encoding="utf-8")

        self.assertIn("Validate VM and disk recovery state", tasks)
        self.assertIn("will not\n      delete or overwrite either one", tasks)

    def test_existing_definitions_are_validated_before_reuse(self) -> None:
        # Refuse to modify an existing VM or network definition that
        # doesn't match the configured disk/network/MAC -- prevents this
        # role from silently taking over something it didn't create.
        tasks = (ROLE_ROOT / "tasks/main.yml").read_text(encoding="utf-8")

        self.assertIn(
            "Validate existing k3s node virtual machine definition", tasks
        )
        self.assertIn("Validate existing k3s network definition", tasks)

    def test_disk_creation_never_overwrites_an_existing_disk(self) -> None:
        tasks = (ROLE_ROOT / "tasks/main.yml").read_text(encoding="utf-8")

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

    def test_cloud_init_carries_no_k3s_installation_yet(self) -> None:
        # This role's whole point right now is "bare VM only" -- k3s
        # installation is a deliberately separate, later step.
        user_data = (
            ROLE_ROOT / "templates/cloud-init-user-data.yml.j2"
        ).read_text(encoding="utf-8")

        self.assertNotIn("get.k3s.io", user_data)
        self.assertNotIn("install.sh", user_data)
        self.assertNotIn("systemctl enable --now k3s", user_data)


if __name__ == "__main__":
    unittest.main()
