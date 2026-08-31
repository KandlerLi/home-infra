from __future__ import annotations

import unittest
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
ROLE_ROOT = PROJECT_ROOT / "ansible/roles/libvirt_host"


class LibvirtHostRoleTests(unittest.TestCase):
    def test_packages_include_the_full_kvm_libvirt_toolchain(self) -> None:
        # Missing any of these breaks a downstream consumer in a way
        # that's easy to overlook until it's actually needed:
        # python3-libvirt/python3-lxml for the community.libvirt Ansible
        # modules k3s_node relies on, xorriso for cloud-init ISO
        # building, virtinst/qemu-utils for domain/disk creation.
        defaults = (ROLE_ROOT / "defaults/main.yml").read_text(
            encoding="utf-8"
        )

        for package in (
            "acl",
            "libvirt-clients",
            "libvirt-daemon-system",
            "python3-libvirt",
            "python3-lxml",
            "qemu-system-x86",
            "qemu-utils",
            "virtinst",
            "xorriso",
        ):
            self.assertIn(package, defaults)

    def test_libvirtd_is_enabled_and_started(self) -> None:
        tasks = (ROLE_ROOT / "tasks/main.yml").read_text(encoding="utf-8")

        service_task = tasks.split("Ensure libvirt is enabled and running", 1)[1]
        self.assertIn("enabled: true", service_task)
        self.assertIn("state: started", service_task)

    def test_fails_fast_with_a_clear_message_when_kvm_is_unavailable(
        self,
    ) -> None:
        # Hardware virtualization disabled in the BIOS/UEFI, or
        # unsupported by the CPU, should stop the whole run here with a
        # message that says so -- not surface later as an opaque
        # "cannot create domain" failure deep inside k3s_node.
        tasks = (ROLE_ROOT / "tasks/main.yml").read_text(encoding="utf-8")

        self.assertIn("path: /dev/kvm", tasks)
        self.assertIn("kvm_device.stat.exists", tasks)
        self.assertIn(
            "/dev/kvm does not exist. Hardware virtualization may be "
            "disabled\n      in the BIOS/UEFI or unsupported by the CPU.",
            tasks,
        )

    def test_qemu_user_and_group_are_verified_via_getent(self) -> None:
        # getent fails the task on its own if the key isn't found (same
        # idiom deluge's role uses to look up its own service account)
        # -- confirms this role relies on that fail-fast behavior rather
        # than a raw getent shell-out plus a separate assert.
        tasks = (ROLE_ROOT / "tasks/main.yml").read_text(encoding="utf-8")
        defaults = (ROLE_ROOT / "defaults/main.yml").read_text(
            encoding="utf-8"
        )

        self.assertIn("ansible.builtin.getent", tasks)
        self.assertIn("database: passwd", tasks)
        self.assertIn("database: group", tasks)
        self.assertIn(
            "libvirt_host_qemu_user: libvirt-qemu", defaults
        )
        self.assertIn("libvirt_host_qemu_group: kvm", defaults)

    def test_image_cache_directory_is_created(self) -> None:
        # Shared by both k3s_node and (formerly) the now-deleted
        # github_runner role, so both VMs' Debian cloud image only ever
        # gets downloaded once -- see k3s_node's own defaults/main.yml
        # comment on this exact path.
        tasks = (ROLE_ROOT / "tasks/main.yml").read_text(encoding="utf-8")
        defaults = (ROLE_ROOT / "defaults/main.yml").read_text(
            encoding="utf-8"
        )

        self.assertIn(
            "libvirt_host_image_cache_dir: /var/lib/libvirt/cloud-images",
            defaults,
        )
        create_dir_task = tasks.split("Create cloud image cache directory", 1)[1]
        self.assertIn("state: directory", create_dir_task)

    def test_only_used_by_k3s_yml_not_site_yml(self) -> None:
        # A GitHub Actions runner VM used to be this role's other
        # consumer -- gone as of 2026-08-31 (moved to a k3s-native
        # replacement entirely). k3s_node is the only one left.
        site = (PROJECT_ROOT / "ansible/playbooks/site.yml").read_text(
            encoding="utf-8"
        )
        k3s_playbook = (
            PROJECT_ROOT / "ansible/playbooks/k3s.yml"
        ).read_text(encoding="utf-8")

        self.assertNotIn("libvirt_host", site)
        self.assertIn("libvirt_host", k3s_playbook)


if __name__ == "__main__":
    unittest.main()
