from __future__ import annotations

import unittest
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
ROLE_ROOT = PROJECT_ROOT / "ansible/roles/base"


class BaseRoleTests(unittest.TestCase):
    def test_requires_root(self) -> None:
        tasks = (ROLE_ROOT / "tasks/main.yml").read_text(encoding="utf-8")

        self.assertIn('effective_user.stdout == "root"', tasks)

    def test_disables_pcie_aspm_for_the_e1000e_watchdog_hang_bug(self) -> None:
        # The Intel e1000e PCIe ASPM bug took the host down; effective after a reboot.
        tasks = (ROLE_ROOT / "tasks/main.yml").read_text(encoding="utf-8")

        grub_task = tasks.split("Disable PCIe ASPM", 1)[1].split(
            "- name:", 1
        )[0]
        self.assertIn("path: /etc/default/grub", grub_task)
        self.assertIn("pcie_aspm=off", grub_task)
        self.assertIn("register: grub_cmdline", grub_task)

        regen_task = tasks.split("Regenerate the GRUB configuration", 1)[
            1
        ].split("- name:", 1)[0]
        self.assertIn("update-grub", regen_task)
        self.assertIn("when: grub_cmdline.changed", regen_task)

        self.assertNotIn("ansible.builtin.reboot", tasks)

    def test_disables_nic_offloading_and_eee_for_the_e1000e_watchdog_hang_bug(
        self,
    ) -> None:
        # ASPM off alone wasn't enough; offloading and EEE are the other usual contributors.
        tasks = (ROLE_ROOT / "tasks/main.yml").read_text(encoding="utf-8")

        # ethtool wasn't installed on this host.
        install_task = tasks.split("Install ethtool", 1)[1].split(
            "- name:", 1
        )[0]
        self.assertIn("name: ethtool", install_task)
        self.assertIn("state: present", install_task)

        offload_task = tasks.split(
            "Disable TSO/GSO/GRO offloading", 1
        )[1].split("- name:", 1)[0]
        self.assertIn("tso off gso off gro off", offload_task)
        self.assertIn("base_e1000e_interface", offload_task)

        eee_task = tasks.split(
            "Disable Energy Efficient Ethernet", 1
        )[1].split("- name:", 1)[0]
        self.assertIn("--set-eee", eee_task)
        self.assertIn("eee off", eee_task)
        # Tolerates NICs without EEE support, without swallowing other failures.
        self.assertIn("Operation not supported", eee_task)
        self.assertIn("base_eee_result.rc != 0", eee_task)

    def test_nic_offload_and_eee_settings_persist_via_ifupdown_post_up(
        self,
    ) -> None:
        # ethtool settings are live only, so a post-up hook reapplies them.
        tasks = (ROLE_ROOT / "tasks/main.yml").read_text(encoding="utf-8")

        persist_task = tasks.split(
            "Persist the NIC offload/EEE settings", 1
        )[1].split("- name:", 1)[0]
        self.assertIn("path: /etc/network/interfaces", persist_task)
        self.assertIn("post-up /sbin/ethtool -K", persist_task)
        self.assertIn("tso off gso off gro off", persist_task)
        self.assertIn("post-up /sbin/ethtool --set-eee", persist_task)

    def test_installs_and_enables_the_e1000e_watchdog_recovery_service(
        self,
    ) -> None:
        # A safety net: the driver's own reset can get stuck for hours.
        tasks = (ROLE_ROOT / "tasks/main.yml").read_text(encoding="utf-8")

        # template doesn't create missing parent directories.
        mkdir_task = tasks.split(
            "Create e1000e watchdog recovery install directory", 1
        )[1].split("- name:", 1)[0]
        self.assertIn(
            "path: \"{{ base_e1000e_watchdog_recovery_install_dir }}\"",
            mkdir_task,
        )
        self.assertIn("state: directory", mkdir_task)

        script_task = tasks.split(
            "Install the e1000e watchdog recovery script", 1
        )[1].split("- name:", 1)[0]
        self.assertIn("e1000e-watchdog-recovery.sh.j2", script_task)
        self.assertIn("mode: \"0755\"", script_task)

        unit_task = tasks.split(
            "Install the e1000e watchdog recovery systemd unit", 1
        )[1].split("- name:", 1)[0]
        self.assertIn("e1000e-watchdog-recovery.service.j2", unit_task)
        self.assertIn(
            "dest: /etc/systemd/system/e1000e-watchdog-recovery.service",
            unit_task,
        )

        enable_task = tasks.split(
            "Ensure the e1000e watchdog recovery service is enabled", 1
        )[1]
        self.assertIn("name: e1000e-watchdog-recovery.service", enable_task)
        self.assertIn("enabled: true", enable_task)

        script = (
            ROLE_ROOT / "templates/e1000e-watchdog-recovery.sh.j2"
        ).read_text(encoding="utf-8")
        self.assertIn("NETDEV WATCHDOG", script)
        self.assertIn("unbind", script)
        self.assertIn("bind", script)
        self.assertIn("base_e1000e_pci_address", script)

        unit = (
            ROLE_ROOT / "templates/e1000e-watchdog-recovery.service.j2"
        ).read_text(encoding="utf-8")
        self.assertIn("Restart=always", unit)
        # Not sandboxed: it needs /sys/bus/pci and the kernel journal.
        self.assertNotIn("ProtectKernelLogs=", unit)
        self.assertNotIn("PrivateDevices=", unit)

    def test_interactive_shell_packages_come_from_apt_not_homebrew(
        self,
    ) -> None:
        # Moved from a hand-made Homebrew install to apt and pinned clones.
        tasks = (ROLE_ROOT / "tasks/main.yml").read_text(encoding="utf-8")

        install_task = tasks.split(
            "Install base packages", 1
        )[1].split("- name:", 1)[0]
        self.assertIn("zsh", install_task)
        self.assertIn("zsh-autosuggestions", install_task)
        self.assertIn("zsh-syntax-highlighting", install_task)

    def test_oh_my_zsh_is_pinned_not_a_moving_branch(self) -> None:
        tasks = (ROLE_ROOT / "tasks/main.yml").read_text(encoding="utf-8")

        clone_task = tasks.split("Install Oh My Zsh", 1)[1].split(
            "- name:", 1
        )[0]
        self.assertIn("repo: https://github.com/ohmyzsh/ohmyzsh.git", clone_task)
        self.assertIn(
            "version: 146461f7c6d95f4ba1220559d66eb113418b40a8", clone_task
        )

    def test_powerlevel10k_is_pinned_to_the_version_already_in_use(
        self,
    ) -> None:
        # The version Homebrew had installed, so the prompt looks the same.
        tasks = (ROLE_ROOT / "tasks/main.yml").read_text(encoding="utf-8")

        clone_task = tasks.split("Install Powerlevel10k theme", 1)[1].split(
            "- name:", 1
        )[0]
        self.assertIn(
            "repo: https://github.com/romkatv/powerlevel10k.git", clone_task
        )
        self.assertIn("version: v1.20.0", clone_task)


if __name__ == "__main__":
    unittest.main()
