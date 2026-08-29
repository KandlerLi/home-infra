from __future__ import annotations

import unittest
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
ROLE_ROOT = PROJECT_ROOT / "ansible/roles/nfs_server"


class NfsServerRoleTests(unittest.TestCase):
    def test_disabled_and_empty_by_default(self) -> None:
        defaults = (ROLE_ROOT / "defaults/main.yml").read_text(encoding="utf-8")

        self.assertIn("nfs_server_enabled: false", defaults)
        self.assertIn("nfs_server_exports: []", defaults)

    def test_validation_rejects_cidr_ranges_and_wildcards(self) -> None:
        tasks = (ROLE_ROOT / "tasks/main.yml").read_text(encoding="utf-8")

        self.assertIn("rejectattr('client', 'search', '/')", tasks)
        self.assertIn("rejectattr('client', 'equalto', '0.0.0.0')", tasks)

    def test_validation_forbids_no_root_squash(self) -> None:
        tasks = (ROLE_ROOT / "tasks/main.yml").read_text(encoding="utf-8")

        self.assertIn(
            "rejectattr('options', 'search', 'no_root_squash')", tasks
        )

    def test_exports_reload_only_when_the_file_actually_changed(self) -> None:
        tasks = (ROLE_ROOT / "tasks/main.yml").read_text(encoding="utf-8")

        reload_task = tasks.split("Reload NFS exports", 1)[1].split(
            "- name:", 1
        )[0]
        self.assertIn(
            "when: nfs_server_exports_file_result.changed", reload_task
        )

    def test_template_renders_one_line_per_export(self) -> None:
        from jinja2 import Environment, FileSystemLoader

        env = Environment(
            loader=FileSystemLoader(str(ROLE_ROOT / "templates"))
        )
        rendered = env.get_template("exports.j2").render(
            nfs_server_exports=[
                {
                    "path": "/mnt/black-hdd/downloads",
                    "client": "192.168.101.10",
                    "options": "rw,sync,no_subtree_check,root_squash",
                },
                {
                    "path": "/mnt/black-hdd/deluge-config",
                    "client": "192.168.101.10",
                    "options": "rw,sync,no_subtree_check,root_squash",
                },
            ]
        )

        self.assertIn(
            "/mnt/black-hdd/downloads "
            "192.168.101.10(rw,sync,no_subtree_check,root_squash)",
            rendered,
        )
        self.assertIn(
            "/mnt/black-hdd/deluge-config "
            "192.168.101.10(rw,sync,no_subtree_check,root_squash)",
            rendered,
        )

    def test_role_runs_before_docker_and_deluge(self) -> None:
        site_yml = (
            PROJECT_ROOT / "ansible/playbooks/site.yml"
        ).read_text(encoding="utf-8")

        nfs_server_index = site_yml.index("- nfs_server")
        docker_index = site_yml.index("- docker")
        deluge_index = site_yml.index("- deluge")
        self.assertLess(nfs_server_index, docker_index)
        self.assertLess(nfs_server_index, deluge_index)

    def test_group_vars_export_exactly_deluges_own_directories(
        self,
    ) -> None:
        # Never the whole /mnt/black-hdd -- Nextcloud's own data and the
        # GitHub runner VM's disk live there too.
        group_vars = (
            PROJECT_ROOT / "ansible/inventory/group_vars/all/main.yml"
        ).read_text(encoding="utf-8")

        self.assertIn("path: /mnt/black-hdd/downloads", group_vars)
        self.assertIn("path: /mnt/black-hdd/deluge-config", group_vars)
        self.assertNotIn("path: /mnt/black-hdd\n", group_vars)


if __name__ == "__main__":
    unittest.main()
