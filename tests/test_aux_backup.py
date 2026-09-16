from __future__ import annotations

import unittest
from pathlib import Path

import yaml
from jinja2 import Environment, FileSystemLoader

PROJECT_ROOT = Path(__file__).resolve().parent.parent
ROLE_ROOT = PROJECT_ROOT / "ansible/roles/aux_backup"


def render(template_name: str, **overrides: object) -> str:
    env = Environment(loader=FileSystemLoader(str(ROLE_ROOT / "templates")))
    defaults = yaml.safe_load((ROLE_ROOT / "defaults/main.yml").read_text())
    ctx = {**defaults, **overrides}
    return env.get_template(template_name).render(**ctx)


class DeploymentTests(unittest.TestCase):
    def test_disabled_by_default_with_real_sources_configured(self) -> None:
        defaults = (ROLE_ROOT / "defaults/main.yml").read_text(encoding="utf-8")

        self.assertIn("aux_backup_enabled: false", defaults)
        self.assertIn("name: deluge-config", defaults)
        self.assertIn("path: /mnt/black-hdd/deluge-config", defaults)
        self.assertIn("name: open-webui", defaults)
        self.assertIn("path: /var/lib/open-webui", defaults)

    def test_open_webui_flags_its_live_sqlite_database(self) -> None:
        defaults = yaml.safe_load((ROLE_ROOT / "defaults/main.yml").read_text())
        sources = {s["name"]: s for s in defaults["aux_backup_sources"]}

        self.assertEqual(sources["open-webui"]["sqlite_files"], ["webui.db"])
        self.assertNotIn("sqlite_files", sources["deluge-config"])

    def test_rsync_is_installed(self) -> None:
        # The generated script shells out to rsync for any source with
        # sqlite_files -- confirmed live it isn't on this host by
        # default.
        tasks = (ROLE_ROOT / "tasks/main.yml").read_text(encoding="utf-8")

        self.assertIn("name: rsync", tasks)
        self.assertIn("ansible.builtin.apt", tasks)

    def test_downloads_directory_is_deliberately_not_a_source(self) -> None:
        # Large and re-attainable by re-downloading -- not worth
        # backup storage/time, unlike deluge-config's small session
        # state.
        defaults = (ROLE_ROOT / "defaults/main.yml").read_text(encoding="utf-8")

        self.assertNotIn("/mnt/black-hdd/downloads", defaults)

    def test_validation_requires_at_least_one_source_when_enabled(self) -> None:
        tasks = (ROLE_ROOT / "tasks/main.yml").read_text(encoding="utf-8")

        self.assertIn("aux_backup_sources | length > 0", tasks)

    def test_site_yml_includes_the_role(self) -> None:
        site = (PROJECT_ROOT / "ansible/playbooks/site.yml").read_text(
            encoding="utf-8"
        )

        self.assertIn("- aux_backup", site)

    def test_destination_directory_is_created_before_the_service_unit_is_installed(
        self,
    ) -> None:
        # Real regression, confirmed live: the service unit's own
        # ReadWritePaths= needs this directory to already exist when
        # systemd sets up the unit's mount namespace, before the
        # script itself ever runs -- without this task, the service
        # fails immediately with exit code 226/NAMESPACE and zero log
        # output, not a script bug.
        tasks = (ROLE_ROOT / "tasks/main.yml").read_text(encoding="utf-8")
        create_dest_index = tasks.index("Create aux_backup destination directory")
        install_unit_index = tasks.index("Install aux-backup systemd service unit")

        self.assertIn('path: "{{ aux_backup_dest_dir }}"', tasks)
        self.assertLess(
            create_dest_index,
            install_unit_index,
            "destination directory must be created before the service unit "
            "that references it via ReadWritePaths=",
        )

    def test_disabling_actually_stops_the_timer_not_just_skips_creation(self) -> None:
        tasks = (ROLE_ROOT / "tasks/main.yml").read_text(encoding="utf-8")

        toggle_task = tasks.split(
            "Ensure aux-backup timer is in its desired running state", 1
        )[1]
        self.assertIn("'stopped'", toggle_task)
        self.assertIn("aux_backup_enabled | bool", toggle_task)


class ScriptRenderingTests(unittest.TestCase):
    def test_renders_a_plain_tar_for_a_source_with_no_sqlite_files(self) -> None:
        rendered = render("aux-backup.sh.j2")

        self.assertIn(
            'tar -czf "${DEST_DIR}/deluge-config-${DATE}.tar.gz" '
            '-C "$(dirname "/mnt/black-hdd/deluge-config")" '
            '"$(basename "/mnt/black-hdd/deluge-config")"',
            rendered,
        )

    def test_renders_a_staged_sqlite_aware_backup_for_open_webui(self) -> None:
        rendered = render("aux-backup.sh.j2")

        # Raw db/-shm/-wal files excluded from the plain rsync copy --
        # the live -shm file is held locked by the real Open WebUI
        # process and can't be safely read directly.
        self.assertIn('--exclude="webui.db"', rendered)
        self.assertIn('--exclude="webui.db-shm"', rendered)
        self.assertIn('--exclude="webui.db-wal"', rendered)
        self.assertIn(
            'rsync -a', rendered
        )
        # The database itself is snapshotted via SQLite's own online
        # backup API (Python's stdlib sqlite3 binding), not copied
        # raw.
        self.assertIn("source_conn.backup(dest_conn)", rendered)
        self.assertIn(
            '"/var/lib/open-webui/webui.db" "$STAGING_TARGET/webui.db"', rendered
        )
        # Final archive still comes from the staging dir, not the live
        # directory directly.
        self.assertIn(
            'tar -czf "${DEST_DIR}/open-webui-${DATE}.tar.gz" '
            '-C "$STAGING_DIR" "$(basename "/var/lib/open-webui")"',
            rendered,
        )

    def test_staging_dirs_are_cleaned_up_via_a_single_accumulating_trap(self) -> None:
        # Real bug class avoided: registering `trap ... EXIT` once per
        # source (inside the Jinja loop) would silently replace the
        # previous handler instead of accumulating, since bash only
        # keeps the last one -- leaking every earlier source's staging
        # dir. Check there's exactly one trap registration for the
        # whole script, not one per source.
        rendered = render("aux-backup.sh.j2")

        self.assertEqual(rendered.count("trap cleanup EXIT"), 1)
        self.assertIn("STAGING_DIRS+=(", rendered)

    def test_refuses_to_back_up_a_missing_or_empty_source(self) -> None:
        rendered = render("aux-backup.sh.j2")

        self.assertIn(
            'if [ ! -d "/mnt/black-hdd/deluge-config" ] '
            '|| [ -z "$(ls -A "/mnt/black-hdd/deluge-config" 2>/dev/null)" ]; then',
            rendered,
        )
        self.assertIn("exit 1", rendered)

    def test_prunes_archives_older_than_the_configured_retention(self) -> None:
        rendered = render("aux-backup.sh.j2", aux_backup_retention_days=30)

        self.assertIn('RETENTION_DAYS="30"', rendered)
        self.assertIn("-mtime \"+${RETENTION_DAYS}\"", rendered)
        self.assertIn("-delete", rendered)

    def test_has_strict_error_handling(self) -> None:
        rendered = render("aux-backup.sh.j2")

        self.assertIn("set -euo pipefail", rendered)


class ServiceUnitTests(unittest.TestCase):
    def test_runs_as_root_with_no_dedicated_service_user(self) -> None:
        # Deliberate -- see the role's own README/service-template
        # comment: this job reads across several different
        # dedicated-host-uid directories in one pass, which a single
        # non-root user can't do without being added to every one of
        # those groups.
        rendered = render("aux-backup.service.j2")

        self.assertNotIn("User=", rendered)
        self.assertNotIn("Group=", rendered)

    def test_only_the_destination_directory_is_writable(self) -> None:
        rendered = render("aux-backup.service.j2")

        self.assertIn("ProtectSystem=strict", rendered)
        self.assertIn("ReadWritePaths=/mnt/red-hdd/aux-backups", rendered)

    def test_keeps_the_full_hardening_stack_that_does_not_touch_dac(self) -> None:
        rendered = render("aux-backup.service.j2")

        for directive in (
            "NoNewPrivileges=true",
            "PrivateNetwork=true",
            "ProtectKernelTunables=true",
            "ProtectKernelModules=true",
            "RestrictNamespaces=true",
            "LockPersonality=true",
            "MemoryDenyWriteExecute=true",
            "SystemCallArchitectures=native",
        ):
            with self.subTest(directive=directive):
                self.assertIn(directive, rendered)

    def test_depends_on_local_fs_target_not_a_guessed_mount_unit_name(self) -> None:
        rendered = render("aux-backup.service.j2")
        directive_lines = [
            line for line in rendered.splitlines() if not line.strip().startswith("#")
        ]

        self.assertIn("After=local-fs.target", rendered)
        self.assertIn("Requires=local-fs.target", rendered)
        self.assertFalse(any(".mount" in line for line in directive_lines))

    def test_executes_the_installed_script(self) -> None:
        rendered = render(
            "aux-backup.service.j2", aux_backup_install_dir="/usr/local/lib/aux-backup"
        )

        self.assertIn(
            "ExecStart=/usr/local/lib/aux-backup/aux-backup.sh", rendered
        )


class TimerUnitTests(unittest.TestCase):
    def test_uses_the_configured_schedule(self) -> None:
        rendered = render(
            "aux-backup.timer.j2", aux_backup_schedule="*-*-* 04:15:00"
        )

        self.assertIn("OnCalendar=*-*-* 04:15:00", rendered)

    def test_catches_up_on_a_missed_run(self) -> None:
        rendered = render("aux-backup.timer.j2")

        self.assertIn("Persistent=true", rendered)

    def test_wanted_by_timers_target(self) -> None:
        rendered = render("aux-backup.timer.j2")

        self.assertIn("WantedBy=timers.target", rendered)


if __name__ == "__main__":
    unittest.main()
