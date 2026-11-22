from __future__ import annotations

import unittest
from pathlib import Path

import yaml

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

    def test_exports_include_directory_is_created_first(self) -> None:
        # The package install doesn't create /etc/exports.d.
        tasks = (ROLE_ROOT / "tasks/main.yml").read_text(encoding="utf-8")

        create_dir_index = tasks.index("Create NFS exports include directory")
        install_index = tasks.index("Install NFS exports")
        self.assertLess(create_dir_index, install_index)

    def test_nfs_server_stops_only_after_the_vms(self) -> None:
        # systemd stops units in reverse start order, so Before= at start means after at shutdown.
        tasks = (ROLE_ROOT / "tasks/main.yml").read_text(encoding="utf-8")

        dropin_task = tasks.split(
            "Stop the NFS server only after the VMs at shutdown", 1
        )[1].split("- name:", 1)[0]
        self.assertIn("Before=libvirt-guests.service", dropin_task)
        self.assertIn(
            "dest: /etc/systemd/system/nfs-server.service.d/", dropin_task
        )
        self.assertLess(
            tasks.index("Create NFS server drop-in directory"),
            tasks.index("Stop the NFS server only after the VMs at shutdown"),
        )

    def test_dropin_change_only_reloads_systemd(self) -> None:
        tasks = (ROLE_ROOT / "tasks/main.yml").read_text(encoding="utf-8")

        reload_task = tasks.split(
            "Reload systemd after NFS server drop-in change", 1
        )[1].split("- name:", 1)[0]
        self.assertIn("daemon_reload: true", reload_task)
        self.assertIn("when: nfs_server_ordering_dropin.changed", reload_task)

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
                    "path": "/mnt/black-hdd/paperless-media",
                    "client": "192.168.101.10",
                    "options": "rw,sync,no_subtree_check,root_squash",
                },
                {
                    "path": "/mnt/red-hdd/authelia-backup",
                    "client": "192.168.101.10",
                    "options": "rw,sync,no_subtree_check,root_squash",
                },
            ]
        )

        self.assertIn(
            "/mnt/black-hdd/paperless-media "
            "192.168.101.10(rw,sync,no_subtree_check,root_squash)",
            rendered,
        )
        self.assertIn(
            "/mnt/red-hdd/authelia-backup "
            "192.168.101.10(rw,sync,no_subtree_check,root_squash)",
            rendered,
        )

    def test_role_runs_before_docker(self) -> None:
        site_yml = (
            PROJECT_ROOT / "ansible/playbooks/site.yml"
        ).read_text(encoding="utf-8")

        nfs_server_index = site_yml.index("- nfs_server")
        docker_index = site_yml.index("- docker")
        self.assertLess(nfs_server_index, docker_index)

    def test_group_vars_export_only_named_directories(
        self,
    ) -> None:
        # Never all of /mnt/black-hdd or /var/lib, only the named directories.
        group_vars = (
            PROJECT_ROOT / "ansible/inventory/group_vars/all/main.yml"
        ).read_text(encoding="utf-8")

        self.assertIn("path: /mnt/black-hdd/paperless-media", group_vars)
        self.assertIn("path: /mnt/red-hdd/authelia-backup", group_vars)
        self.assertNotIn("path: /mnt/black-hdd\n", group_vars)
        self.assertNotIn("path: /var/lib\n", group_vars)



class NfsServerInventoryTests(unittest.TestCase):
    def test_minecraft_backup_is_exported_to_node3_only(self) -> None:
        # The Minecraft Pod runs on its own node (ADR 0036); node-1 no longer needs the share.
        group_vars = yaml.safe_load(
            (PROJECT_ROOT / "ansible/inventory/group_vars/all/main.yml").read_text(encoding="utf-8")
        )
        clients = [
            e["client"] for e in group_vars["nfs_server_exports"]
            if e["path"] == "/mnt/red-hdd/minecraft-backup"
        ]

        self.assertEqual(clients, ["192.168.101.12"])


if __name__ == "__main__":
    unittest.main()
