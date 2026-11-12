from __future__ import annotations

import unittest
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
ROLE_ROOT = PROJECT_ROOT / "ansible/roles/luks_tang_test"


def _task(tasks: str, name: str) -> str:
    start = tasks.index(f"- name: {name}")
    end = tasks.find("\n- name:", start + 1)
    return tasks[start:] if end == -1 else tasks[start:end]


class LuksTangTestRoleTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tasks = (ROLE_ROOT / "tasks/main.yml").read_text(encoding="utf-8")
        self.defaults = (ROLE_ROOT / "defaults/main.yml").read_text(encoding="utf-8")

    def test_only_formats_an_image_file_that_is_not_luks_yet(self) -> None:
        self.assertIn("luks_tang_test_image: /var/lib/", self.defaults)
        fmt = _task(self.tasks, "Format the test volume with LUKS2")
        self.assertIn("luks_tang_test_needs_format | bool", fmt)
        self.assertIn("luks_tang_test_needs_format: \"{{ luks_tang_test_is_luks.rc != 0 }}\"", self.tasks)

    def test_passphrase_comes_from_the_environment_never_a_default_value(self) -> None:
        self.assertIn("lookup('ansible.builtin.env', 'LUKS_TANG_TEST_PASSPHRASE')", self.defaults)

    def test_tasks_handling_the_passphrase_hide_it(self) -> None:
        for name in ("Format the test volume with LUKS2", "Bind the test volume to Tang"):
            task = _task(self.tasks, name)
            self.assertIn("no_log: true", task)
            self.assertIn("stdin: \"{{ luks_tang_test_passphrase }}\"", task)
            self.assertIn("stdin_add_newline: false", task)

    def test_binding_pins_the_tang_thumbprint(self) -> None:
        bind = _task(self.tasks, "Bind the test volume to Tang")
        self.assertIn("'thp': luks_tang_test_tang_thp", bind)
        self.assertRegex(self.defaults, r'luks_tang_test_tang_thp: "[A-Za-z0-9_-]{43}"')

    def test_a_locked_volume_does_not_stop_the_boot(self) -> None:
        crypttab = _task(self.tasks, "Unlock the test volume at boot through Tang")
        mount = _task(self.tasks, "Mount the test volume")
        self.assertIn("luks,_netdev,nofail\n", crypttab)
        self.assertIn('opts: "_netdev,nofail"', mount)

    def test_crypttab_is_read_at_boot(self) -> None:
        install = _task(self.tasks, "Install cryptsetup and Clevis")
        self.assertIn("- systemd-cryptsetup", install)

    def test_boot_time_unlock_is_enabled(self) -> None:
        self.assertIn("- clevis-luks-askpass.path", self.tasks)
        self.assertIn("- remote-cryptsetup.target", self.tasks)

    def test_crypttab_names_the_loop_device_not_the_file(self) -> None:
        # clevis-luks-askpass skips anything that isn't a block device.
        crypttab = _task(self.tasks, "Unlock the test volume at boot through Tang")
        self.assertIn("{{ luks_tang_test_loop_device }} none", crypttab)
        self.assertIn("luks_tang_test_loop_device: /dev/loop", self.defaults)

    def test_loop_unit_is_pulled_in_by_a_drop_in_not_crypttab(self) -> None:
        # The crypttab generator ignores x-systemd.requires=; at boot loop7 stayed empty.
        crypttab = _task(self.tasks, "Unlock the test volume at boot through Tang")
        self.assertNotIn("x-systemd.requires", crypttab)
        dropin = _task(self.tasks, "Make the test volume's unlock attach the loop device first")
        self.assertIn("{{ luks_tang_test_cryptsetup_unit }}.d/loop.conf", dropin)
        self.assertIn("Requires={{ luks_tang_test_loop_unit }}", dropin)
        self.assertIn("After={{ luks_tang_test_loop_unit }}", dropin)
        self.assertLess(
            self.tasks.index("Make the test volume's unlock attach the loop device first"),
            self.tasks.index("Regenerate systemd units from crypttab"),
        )

    def test_loop_device_is_detached_by_its_unit_at_shutdown(self) -> None:
        unit = (ROLE_ROOT / "templates/luks-tang-test-loop.service.j2").read_text(encoding="utf-8")
        self.assertIn("RemainAfterExit=yes", unit)
        self.assertIn("ExecStop=/usr/sbin/losetup -d {{ luks_tang_test_loop_device }}", unit)

    def test_volume_is_only_opened_through_systemd(self) -> None:
        # systemd must own the mapping, so it is torn down in order at shutdown.
        self.assertNotIn("- unlock", self.tasks)
        unlock = _task(self.tasks, "Unlock the test volume through its systemd unit")
        self.assertIn('- "{{ luks_tang_test_cryptsetup_unit }}"', unlock)
        self.assertIn("timeout: 120", unlock)

    def test_routine_rollouts_never_run_it(self) -> None:
        playbooks = PROJECT_ROOT / "ansible/playbooks"
        self.assertNotIn("luks_tang_test", (playbooks / "site.yml").read_text(encoding="utf-8"))
        self.assertIn("- luks_tang_test", (playbooks / "luks-test.yml").read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
