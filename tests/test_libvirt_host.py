from __future__ import annotations

import unittest
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
ROLE_ROOT = PROJECT_ROOT / "ansible/roles/libvirt_host"


class LibvirtHostRoleTests(unittest.TestCase):
    def test_packages_include_the_full_kvm_libvirt_toolchain(self) -> None:
        # Each package has a downstream consumer that breaks late without it.
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
        # Fail early with a clear message when virtualization is unavailable.
        tasks = (ROLE_ROOT / "tasks/main.yml").read_text(encoding="utf-8")

        self.assertIn("path: /dev/kvm", tasks)
        self.assertIn("kvm_device.stat.exists", tasks)
        self.assertIn(
            "/dev/kvm does not exist. Hardware virtualization may be "
            "disabled\n      in the BIOS/UEFI or unsupported by the CPU.",
            tasks,
        )

    def test_qemu_user_and_group_are_verified_via_getent(self) -> None:
        # getent fails on its own when the key is missing.
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
        # The cloud-image cache directory, downloaded once.
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
        # k3s_node is the only consumer left.
        site = (PROJECT_ROOT / "ansible/playbooks/site.yml").read_text(
            encoding="utf-8"
        )
        k3s_playbook = (
            PROJECT_ROOT / "ansible/playbooks/k3s.yml"
        ).read_text(encoding="utf-8")

        self.assertNotIn("libvirt_host", site)
        self.assertIn("libvirt_host", k3s_playbook)


class DefaultNetworkAutostartTests(unittest.TestCase):
    # libvirt's default network didn't autostart after a reboot.
    def test_checks_existence_before_acting(self) -> None:
        tasks = (ROLE_ROOT / "tasks/main.yml").read_text(encoding="utf-8")

        autostart_task = tasks.split(
            "Ensure the default libvirt network autostarts", 1
        )[1]
        self.assertIn(
            "'default' in libvirt_host_defined_networks.stdout_lines",
            autostart_task,
        )

    def test_only_acts_when_autostart_is_currently_off(self) -> None:
        # Keeps Ansible's change reporting accurate.
        tasks = (ROLE_ROOT / "tasks/main.yml").read_text(encoding="utf-8")

        autostart_task = tasks.split(
            "Ensure the default libvirt network autostarts", 1
        )[1]
        self.assertIn(
            r"libvirt_host_default_network_info.stdout is search"
            r"('Autostart:\s+no')",
            autostart_task,
        )

    def test_uses_plain_virsh_not_the_buggy_module(self) -> None:
        tasks = (ROLE_ROOT / "tasks/main.yml").read_text(encoding="utf-8")

        autostart_task = tasks.split(
            "Ensure the default libvirt network autostarts", 1
        )[1]
        self.assertIn("virsh", autostart_task)
        self.assertIn("net-autostart", autostart_task)
        self.assertNotIn("community.libvirt.virt_net", autostart_task)


if __name__ == "__main__":
    unittest.main()
