from __future__ import annotations

import json
import unittest
from pathlib import Path

import yaml
from jinja2 import Environment, FileSystemLoader

PROJECT_ROOT = Path(__file__).resolve().parents[1]
ROLE_ROOT = PROJECT_ROOT / "ansible/roles/monitoring"


def render(name: str, **overrides: object) -> str:
    env = Environment(loader=FileSystemLoader(str(ROLE_ROOT / "templates")))
    env.filters["bool"] = bool
    defaults = yaml.safe_load((ROLE_ROOT / "defaults/main.yml").read_text())
    # Blocky is always on in production; forced off so unrelated renders stay isolated.
    ctx = {**defaults, "monitoring_blocky_enabled": False, **overrides}
    return env.get_template(name).render(**ctx)


class MonitoringRoleTests(unittest.TestCase):
    def test_defaults_are_opt_in_and_loopback_only(self) -> None:
        defaults = (ROLE_ROOT / "defaults/main.yml").read_text(encoding="utf-8")

        self.assertIn("monitoring_enabled: false", defaults)
        self.assertIn("monitoring_bind_address: 127.0.0.1", defaults)

    def test_image_pull_task_uses_digest_only_not_tag_and_digest(self) -> None:
        # A name:tag@digest pull leaves an untagged image, so the pull task never became idempotent.
        tasks = (ROLE_ROOT / "tasks/main.yml").read_text(encoding="utf-8")

        self.assertIn(
            'name: "{{ item.name }}@{{ item.digest }}"', tasks
        )
        # Exact task body: the tag@digest string is still correct in the docker_container tasks.
        self.assertNotIn('name: "{{ item }}"\n        source: pull', tasks)

    def test_secrets_are_required_not_left_default(self) -> None:
        # Empty defaults that must be set via Secrets Manager before the first deploy.
        tasks = (ROLE_ROOT / "tasks/main.yml").read_text(encoding="utf-8")
        defaults = (ROLE_ROOT / "defaults/main.yml").read_text(encoding="utf-8")

        self.assertIn('monitoring_ntfy_topic: ""', defaults)
        self.assertIn('monitoring_ses_smtp_username: ""', defaults)
        self.assertIn('monitoring_ses_smtp_password: ""', defaults)
        self.assertIn("monitoring_ntfy_topic | trim | length >= 20", tasks)
        self.assertIn("monitoring_ses_smtp_username | trim | length > 0", tasks)
        self.assertIn("monitoring_ses_smtp_password | trim | length > 0", tasks)

    def test_every_container_uses_host_networking_consistently(self) -> None:
        # Mixed networking breaks scrapes: 127.0.0.1 in a bridge container is its own loopback.
        tasks = (ROLE_ROOT / "tasks/main.yml").read_text(encoding="utf-8")

        # Every container this role still runs.
        self.assertEqual(tasks.count("        network_mode: host"), 4)
        self.assertNotIn("published_ports:", tasks)

    def test_cadvisor_port_does_not_collide_with_nextcloud_aio_admin_ui(
        self,
    ) -> None:
        # cAdvisor on 8080 took Nextcloud AIO's admin interface port.
        defaults = (ROLE_ROOT / "defaults/main.yml").read_text(encoding="utf-8")

        self.assertNotIn("monitoring_cadvisor_port: 8080", defaults)

    def test_cadvisor_docker_socket_access_is_read_only(self) -> None:
        # Read-only, like nextcloud_aio's own docker.sock mount.
        tasks = (ROLE_ROOT / "tasks/main.yml").read_text(encoding="utf-8")
        nextcloud_aio_tasks = (
            PROJECT_ROOT / "ansible/roles/nextcloud_aio/tasks/main.yml"
        ).read_text(encoding="utf-8")

        self.assertIn("/var/run/docker.sock:/var/run/docker.sock:ro", tasks)
        self.assertNotIn("/var/run/docker.sock:/var/run/docker.sock:rw", tasks)
        self.assertIn("/var/run/docker.sock:/var/run/docker.sock:ro", nextcloud_aio_tasks)

    def test_node_exporter_mounts_host_filesystems_read_only(self) -> None:
        tasks = (ROLE_ROOT / "tasks/main.yml").read_text(encoding="utf-8")

        self.assertIn("/:/host:ro,rslave", tasks)
        self.assertIn("/proc:/host/proc:ro", tasks)
        self.assertIn("/sys:/host/sys:ro", tasks)
        self.assertIn("--path.rootfs=/host", tasks)

    def test_prometheus_data_lives_on_root_disk_with_bounded_retention(self) -> None:
        # Root disk, not the busy HDD; retention bounded by time and size.
        defaults = (ROLE_ROOT / "defaults/main.yml").read_text(encoding="utf-8")

        self.assertIn("monitoring_data_dir: /var/lib/monitoring", defaults)
        self.assertNotIn("black-hdd", defaults)
        self.assertIn("monitoring_prometheus_retention_time: 30d", defaults)
        self.assertIn("monitoring_prometheus_retention_size: 2GB", defaults)

    def test_prometheus_k3s_bind_address_defaults_to_disabled(self) -> None:
        defaults = (ROLE_ROOT / "defaults/main.yml").read_text(encoding="utf-8")

        self.assertIn('monitoring_prometheus_k3s_bind_address: ""', defaults)

    def test_prometheus_k3s_bind_address_is_additive_and_allowlisted(self) -> None:
        # Additive: the exporters stay on loopback, so loopback must keep working.
        tasks = (ROLE_ROOT / "tasks/main.yml").read_text(encoding="utf-8")

        self.assertIn(
            'monitoring_prometheus_k3s_bind_address | length == 0\n'
            '            or monitoring_prometheus_k3s_bind_address == "192.168.101.1"',
            tasks,
        )
        self.assertIn(
            "'--web.listen-address=' + monitoring_prometheus_k3s_bind_address",
            tasks,
        )

    def test_ntfy_relay_k3s_bind_address_defaults_to_disabled(self) -> None:
        defaults = (ROLE_ROOT / "defaults/main.yml").read_text(encoding="utf-8")

        self.assertIn('monitoring_ntfy_relay_k3s_bind_address: ""', defaults)

    def test_ntfy_relay_k3s_bind_address_is_additive_and_allowlisted(self) -> None:
        # Additive, like the Prometheus k3s listener.
        tasks = (ROLE_ROOT / "tasks/main.yml").read_text(encoding="utf-8")
        rendered = render("ntfy-relay.service.j2")

        self.assertIn(
            'monitoring_ntfy_relay_k3s_bind_address | length == 0\n'
            '            or monitoring_ntfy_relay_k3s_bind_address == "192.168.101.1"',
            tasks,
        )
        self.assertNotIn("LISTEN_HOST_K3S", rendered)

    def test_ntfy_relay_k3s_bind_address_is_rendered_when_set(self) -> None:
        rendered = render(
            "ntfy-relay.service.j2",
            monitoring_ntfy_relay_k3s_bind_address="192.168.101.1",
        )

        self.assertIn('Environment="LISTEN_HOST_K3S=192.168.101.1"', rendered)

    def test_blackbox_probes_every_public_service(self) -> None:
        defaults = (ROLE_ROOT / "defaults/main.yml").read_text(encoding="utf-8")

        for target in [
            "https://nextcloud.jkandler.de/",
            "https://ai.jkandler.de/healthz",
            "https://torrent.jkandler.de/",
            "https://www.jkandler.de/",
            "http://jkandler.de/",
            "https://auth.jkandler.de/api/health",
            "mail.jkandler.de:25",
            "mail.jkandler.de:465",
            "mail.jkandler.de:993",
        ]:
            with self.subTest(target=target):
                self.assertIn(target, defaults)

    def test_mail_ports_are_probed_with_their_own_protocol_module(self) -> None:
        parsed_prometheus = yaml.safe_load(render("prometheus.yml.j2"))
        modules = yaml.safe_load(render("blackbox.yml.j2"))["modules"]

        tcp_job = next(
            job for job in parsed_prometheus["scrape_configs"] if job["job_name"] == "blackbox_tcp"
        )
        probed = {
            config["targets"][0]: config["labels"]["module"] for config in tcp_job["static_configs"]
        }
        self.assertEqual(
            probed,
            {
                "mail.jkandler.de:25": "smtp_starttls",
                "mail.jkandler.de:465": "smtps",
                "mail.jkandler.de:993": "imaps",
            },
        )
        self.assertIn(
            {"source_labels": ["module"], "target_label": "__param_module"},
            tcp_job["relabel_configs"],
        )
        # A TLS failure has to fail the probe, not just a closed port.
        self.assertIn({"starttls": True}, modules["smtp_starttls"]["tcp"]["query_response"])
        self.assertTrue(modules["smtps"]["tcp"]["tls"])
        self.assertTrue(modules["imaps"]["tcp"]["tls"])

    def test_stale_shared_ingress_ping_probe_is_gone(self) -> None:
        # The retired shared_ingress ping probe kept firing a false alarm.
        defaults = yaml.safe_load(
            (ROLE_ROOT / "defaults/main.yml").read_text(encoding="utf-8")
        )

        self.assertNotIn("http://127.0.0.1:8082/ping", defaults["monitoring_probe_targets"])

    def test_prometheus_config_wires_blackbox_multi_target_probing(self) -> None:
        rendered = render("prometheus.yml.j2")
        parsed = yaml.safe_load(rendered)

        blackbox_job = next(
            job for job in parsed["scrape_configs"] if job["job_name"] == "blackbox_http"
        )
        self.assertEqual(blackbox_job["metrics_path"], "/probe")
        self.assertIn(
            "https://nextcloud.jkandler.de/",
            blackbox_job["static_configs"][0]["targets"],
        )
        # A stray whitespace-trim once hoisted relabel_configs out of the job.
        self.assertIn("relabel_configs", blackbox_job)
        self.assertNotIn("relabel_configs", parsed)

    def test_authenticated_targets_are_probed_with_the_401_tolerant_module(self) -> None:
        # No credentials for auth-protected targets here, so a 401 counts as up.
        rendered_prometheus = render("prometheus.yml.j2")
        parsed_prometheus = yaml.safe_load(rendered_prometheus)
        rendered_blackbox = render("blackbox.yml.j2")
        parsed_blackbox = yaml.safe_load(rendered_blackbox)

        auth_job = next(
            job
            for job in parsed_prometheus["scrape_configs"]
            if job["job_name"] == "blackbox_http_authenticated"
        )
        self.assertEqual(auth_job["params"]["module"], ["http_2xx_or_401"])
        for target in ["https://ai.jkandler.de/healthz", "https://torrent.jkandler.de/"]:
            with self.subTest(target=target):
                self.assertIn(target, auth_job["static_configs"][0]["targets"])

        self.assertIn(401, parsed_blackbox["modules"]["http_2xx_or_401"]["http"]["valid_status_codes"])
        self.assertIn(200, parsed_blackbox["modules"]["http_2xx_or_401"]["http"]["valid_status_codes"])

    def test_prometheus_forwards_firing_alerts_to_alertmanager(self) -> None:
        rendered = render("prometheus.yml.j2")
        parsed = yaml.safe_load(rendered)

        targets = parsed["alerting"]["alertmanagers"][0]["static_configs"][0]["targets"]
        self.assertIn("127.0.0.1:9093", targets)
        self.assertIn("/etc/prometheus/alert_rules.yml", parsed["rule_files"])

    def test_alertmanager_upstream_switches_cleanly_to_the_k3s_address(self) -> None:
        # Exactly one target: listing both Alertmanagers double-fires every alert.
        rendered = yaml.safe_load(
            render(
                "prometheus.yml.j2",
                monitoring_alertmanager_upstream="192.168.101.10:9093",
            )
        )

        targets = rendered["alerting"]["alertmanagers"][0]["static_configs"][0]["targets"]
        self.assertEqual(targets, ["192.168.101.10:9093"])

    def test_alertmanager_upstream_is_a_closed_allowlist(self) -> None:
        tasks = (ROLE_ROOT / "tasks/main.yml").read_text(encoding="utf-8")

        self.assertIn(
            'monitoring_alertmanager_upstream == "127.0.0.1:9093"\n'
            '            or monitoring_alertmanager_upstream == "192.168.101.10:9093"',
            tasks,
        )

    def test_alert_rules_cover_all_four_adr_0017_scope_areas(self) -> None:
        rendered = render("alert_rules.yml.j2")
        parsed = yaml.safe_load(rendered)

        group_names = {group["name"] for group in parsed["groups"]}
        # blocky_health goes beyond ADR 0017's four areas: Blocky's log writer never reconnects.
        self.assertEqual(
            group_names,
            {
                "host_health",
                "container_health",
                "service_reachability",
                "certificate_expiry",
                "blocky_health",
                "backups",
            },
        )

    def test_container_down_ignores_stale_ids_from_ordinary_recreates(self) -> None:
        # Recreated containers get a new cgroup id; the old series freezes instead of vanishing.
        rendered = render("alert_rules.yml.j2")
        parsed = yaml.safe_load(rendered)

        container_health = next(
            group for group in parsed["groups"] if group["name"] == "container_health"
        )
        container_down = next(
            rule for rule in container_health["rules"] if rule.get("alert") == "ContainerDown"
        )

        self.assertIn("max by (name) (container_last_seen", container_down["expr"])

    def test_nextcloud_backup_downtime_does_not_alert(self) -> None:
        parsed = yaml.safe_load(render("alert_rules.yml.j2"))
        rules = {
            rule.get("alert") or rule["record"]: rule
            for group in parsed["groups"]
            for rule in group["rules"]
        }

        self.assertIn(
            'container_last_seen{job="cadvisor", name="nextcloud-aio-borgbackup"}[15m]',
            rules["nextcloud_aio:backup_window"]["expr"],
        )
        container_down = rules["ContainerDown"]["expr"]
        self.assertIn('name!~"nextcloud-aio-(borgbackup|watchtower)"', container_down)
        self.assertIn("and on () nextcloud_aio:backup_window", container_down)
        self.assertIn(
            "and on () nextcloud_aio:backup_window", rules["ServiceUnreachable"]["expr"]
        )

    def test_blocky_query_log_stale_alert_covers_both_missing_and_flat_metric(
        self,
    ) -> None:
        # Both shapes of a silent logging gap: exporter down, or exporter up with no new rows.
        rendered = render("alert_rules.yml.j2")
        parsed = yaml.safe_load(rendered)

        blocky_health = next(
            group for group in parsed["groups"] if group["name"] == "blocky_health"
        )
        stale_alert = next(
            rule
            for rule in blocky_health["rules"]
            if rule["alert"] == "BlockyQueryLogStale"
        )

        self.assertIn(
            'absent(pg_stat_user_tables_n_tup_ins{relname="log_entries"})',
            stale_alert["expr"],
        )
        self.assertIn(
            'increase(pg_stat_user_tables_n_tup_ins{relname="log_entries"}[10m]) == 0',
            stale_alert["expr"],
        )
        self.assertEqual(stale_alert["for"], "10m")
        self.assertEqual(stale_alert["labels"]["severity"], "warning")

    def test_backup_rules_cover_every_backup_and_cronjobs_only_with_k3s(
        self,
    ) -> None:
        def backup_rules(**overrides: object) -> dict[str, dict]:
            parsed = yaml.safe_load(render("alert_rules.yml.j2", **overrides))
            group = next(g for g in parsed["groups"] if g["name"] == "backups")
            return {r.get("alert") or r["record"]: r for r in group["rules"]}

        rules = backup_rules(monitoring_k3s_node_health_enabled=True)
        recorded = rules["backup:last_success_timestamp_seconds"]["expr"]
        self.assertIn('cronjob=~"authelia-backup|paperless-export|stalwart-backup"', recorded)
        self.assertIn("> 26 * 3600", rules["BackupStale"]["expr"])
        for name in ("nextcloud-borg", "aux-backup", "authelia-backup", "paperless-export", "stalwart-backup"):
            with self.subTest(backup=name):
                self.assertIn(f'{{backup="{name}"}}', rules["BackupMissing"]["expr"])

        # Without k3s node health, kube-state-metrics isn't scraped, so no CronJobs are expected.
        rules = backup_rules(monitoring_k3s_node_health_enabled=False)
        self.assertNotIn("kube_cronjob", rules["backup:last_success_timestamp_seconds"]["expr"])
        self.assertNotIn("authelia-backup", rules["BackupMissing"]["expr"])

    def test_backup_freshness_writes_atomically_into_the_textfile_dir(self) -> None:
        tasks = (ROLE_ROOT / "tasks/main.yml").read_text(encoding="utf-8")
        self.assertIn(
            "--collector.textfile.directory=/host{{ monitoring_backup_textfile_dir }}",
            tasks,
        )

        script = render(
            "backup-freshness.sh.j2",
            monitoring_backup_textfile_dir="/var/lib/monitoring/node-exporter-textfile",
        )
        self.assertIn("set -euo pipefail", script)
        self.assertIn('mv "$TMP" "$OUT"', script)
        self.assertIn('backup=\\"nextcloud-borg\\"', script)
        self.assertIn("stat -c %Y /mnt/red-hdd/borg/index.*", script)

        unit = render(
            "backup-freshness.service.j2",
            monitoring_backup_textfile_dir="/var/lib/monitoring/node-exporter-textfile",
            monitoring_backup_freshness_script="/usr/local/lib/monitoring/backup-freshness.sh",
        )
        self.assertIn("ProtectSystem=strict", unit)
        self.assertIn("ReadWritePaths=/var/lib/monitoring/node-exporter-textfile", unit)
        self.assertIn("PrivateNetwork=true", unit)

    def test_dashboards_are_valid_json_and_reference_the_prometheus_datasource(
        self,
    ) -> None:
        # Canonical dashboard sources, vendored by k3s-apps' grafana module.
        dashboards_dir = ROLE_ROOT / "files/dashboards"
        expected = {
            "host-health.json",
            "container-health.json",
            "service-reachability.json",
            "service-status.json",
            "backups.json",
            "github-runners.json",
            "overview.json",
        }

        for name in expected:
            with self.subTest(dashboard=name):
                data = json.loads((dashboards_dir / name).read_text(encoding="utf-8"))
                self.assertTrue(data["panels"])
                for panel in data["panels"]:
                    for target in panel.get("targets", []):
                        self.assertEqual(target["datasource"]["uid"], "prometheus")

class BlockyIntegrationTests(unittest.TestCase):
    def test_scrape_job_only_appears_when_blocky_is_enabled(self) -> None:
        prometheus_off = yaml.safe_load(render("prometheus.yml.j2"))

        job_names_off = {job["job_name"] for job in prometheus_off["scrape_configs"]}
        self.assertNotIn("blocky", job_names_off)

    def test_scrape_job_renders_correctly_when_enabled(self) -> None:
        overrides = {
            "monitoring_blocky_enabled": True,
            "monitoring_blocky_http_port": 4000,
            "monitoring_blocky_upstream": "127.0.0.1:4000",
        }
        prometheus_on = yaml.safe_load(render("prometheus.yml.j2", **overrides))

        blocky_job = next(
            job
            for job in prometheus_on["scrape_configs"]
            if job["job_name"] == "blocky"
        )
        self.assertIn(
            "127.0.0.1:4000", blocky_job["static_configs"][0]["targets"]
        )

    def test_blocky_upstream_switches_cleanly_to_the_k3s_address(self) -> None:
        # A clean switch, like monitoring_alertmanager_upstream.
        overrides = {
            "monitoring_blocky_enabled": True,
            "monitoring_blocky_http_port": 4000,
            "monitoring_blocky_upstream": "192.168.101.10:4000",
        }
        rendered = yaml.safe_load(render("prometheus.yml.j2", **overrides))

        blocky_job = next(
            job
            for job in rendered["scrape_configs"]
            if job["job_name"] == "blocky"
        )
        self.assertEqual(
            blocky_job["static_configs"][0]["targets"], ["192.168.101.10:4000"]
        )

    def test_blocky_postgres_exporter_is_scraped_alongside_blocky(self) -> None:
        # Enables the BlockyQueryLogStale alert below.
        overrides = {
            "monitoring_blocky_enabled": True,
            "monitoring_blocky_postgres_exporter_upstream": "192.168.101.10:9187",
        }
        rendered = yaml.safe_load(render("prometheus.yml.j2", **overrides))

        exporter_job = next(
            job
            for job in rendered["scrape_configs"]
            if job["job_name"] == "blocky_postgres_exporter"
        )
        self.assertEqual(
            exporter_job["static_configs"][0]["targets"], ["192.168.101.10:9187"]
        )

    def test_blocky_postgres_exporter_is_gated_on_blocky_enabled(self) -> None:
        # Only one possible target, so it shares Blocky's flag.
        rendered = yaml.safe_load(render("prometheus.yml.j2"))

        job_names = {job["job_name"] for job in rendered["scrape_configs"]}
        self.assertNotIn("blocky_postgres_exporter", job_names)

    def test_blocky_upstream_is_a_closed_allowlist(self) -> None:
        tasks = (ROLE_ROOT / "tasks/main.yml").read_text(encoding="utf-8")

        self.assertIn(
            'monitoring_blocky_upstream == "127.0.0.1:" ~ monitoring_blocky_http_port\n'
            '            or monitoring_blocky_upstream == "192.168.101.10:" ~ monitoring_blocky_http_port',
            tasks,
        )

    def test_dashboards_are_valid_json_with_the_right_datasource_uids(self) -> None:
        dashboards_dir = ROLE_ROOT / "files/dashboards"

        metrics = json.loads((dashboards_dir / "blocky.json").read_text())
        self.assertTrue(metrics["panels"])
        for panel in metrics["panels"]:
            for target in panel.get("targets", []):
                datasource = target.get("datasource")
                if datasource and datasource.get("type") == "prometheus":
                    self.assertEqual(datasource["uid"], "prometheus")

        query_log = json.loads((dashboards_dir / "blocky-postgres.json").read_text())
        self.assertTrue(query_log["panels"])
        for panel in query_log["panels"]:
            for target in panel.get("targets", []):
                datasource = target.get("datasource")
                if datasource and datasource.get("type") == "grafana-postgresql-datasource":
                    self.assertEqual(datasource["uid"], "blocky-postgres")

        # File-provisioned with fixed values, so no import placeholders.
        for path in (dashboards_dir / "blocky.json", dashboards_dir / "blocky-postgres.json"):
            with self.subTest(path=path.name):
                raw = path.read_text()
                self.assertNotIn("${DS_", raw)
                self.assertNotIn("${VAR_", raw)

    def test_query_variables_refresh_on_dashboard_load(self) -> None:
        # Query variables must refresh on load, or the filters start empty.
        dashboards_dir = ROLE_ROOT / "files/dashboards"
        query_log = json.loads((dashboards_dir / "blocky-postgres.json").read_text())

        query_vars = [
            v for v in query_log["templating"]["list"] if v.get("type") == "query"
        ]
        self.assertTrue(query_vars)
        for variable in query_vars:
            with self.subTest(variable=variable["name"]):
                self.assertEqual(variable["refresh"], 1)


class GrafanaK3sMigrationTests(unittest.TestCase):
    def test_no_container_image_or_verify_tasks_remain(self) -> None:
        tasks = (ROLE_ROOT / "tasks/main.yml").read_text(encoding="utf-8")

        self.assertNotIn("Ensure Grafana container is running", tasks)
        self.assertNotIn("Verify Grafana is ready on host loopback", tasks)
        self.assertNotIn("Install Grafana", tasks)
        self.assertNotIn("monitoring_grafana_image", tasks)
        self.assertNotIn("monitoring_grafana_container_user", tasks)

    def test_dead_defaults_and_templates_are_gone(self) -> None:
        defaults = (ROLE_ROOT / "defaults/main.yml").read_text(encoding="utf-8")

        # An actual assignment, not prose explaining the removal.
        for dead_default in (
            "monitoring_grafana_container_name",
            "monitoring_grafana_image_name",
            "monitoring_grafana_port",
            "monitoring_grafana_memory_limit",
            "monitoring_grafana_admin_password",
            "monitoring_grafana_admin_user",
            "monitoring_grafana_container_user",
        ):
            with self.subTest(default=dead_default):
                self.assertNotIn(f"{dead_default}:", defaults)

        for dead_template in (
            "grafana_datasources.yml.j2",
            "grafana_dashboards_provider.yml.j2",
        ):
            with self.subTest(template=dead_template):
                self.assertFalse((ROLE_ROOT / "templates" / dead_template).exists())

    def test_old_local_state_is_actively_removed(self) -> None:
        # Removing old state keeps an unrelated site.yml run from resurrecting the container.
        tasks = (ROLE_ROOT / "tasks/main.yml").read_text(encoding="utf-8")

        self.assertIn("Remove retired local Grafana/Alertmanager state", tasks)
        cleanup_task = tasks.split(
            "Remove retired local Grafana/Alertmanager state", 1
        )[1].split("- name:", 1)[0]
        self.assertIn("monitoring_config_dir }}/grafana", cleanup_task)
        self.assertIn("monitoring_data_dir }}/grafana", cleanup_task)
        self.assertIn("monitoring_data_dir }}/secrets", cleanup_task)
        self.assertIn("state: absent", cleanup_task)


class AlertmanagerK3sMigrationTests(unittest.TestCase):
    def test_no_container_image_or_config_tasks_remain(self) -> None:
        tasks = (ROLE_ROOT / "tasks/main.yml").read_text(encoding="utf-8")

        self.assertNotIn("Ensure Alertmanager container is running", tasks)
        self.assertNotIn("Install Alertmanager configuration", tasks)
        self.assertNotIn("monitoring_alertmanager_image", tasks)
        self.assertNotIn("monitoring_alertmanager_container_user", tasks)

    def test_dead_defaults_and_template_are_gone(self) -> None:
        defaults = (ROLE_ROOT / "defaults/main.yml").read_text(encoding="utf-8")

        for dead_default in (
            "monitoring_alertmanager_container_name",
            "monitoring_alertmanager_image_name",
            "monitoring_alertmanager_port",
            "monitoring_alertmanager_memory_limit",
            "monitoring_alertmanager_container_user",
        ):
            with self.subTest(default=dead_default):
                self.assertNotIn(f"{dead_default}:", defaults)

        self.assertFalse(
            (ROLE_ROOT / "templates" / "alertmanager.yml.j2").exists()
        )

    def test_upstream_defaults_to_the_homeserver_and_only_two_values_allowed(
        self,
    ) -> None:
        defaults = (ROLE_ROOT / "defaults/main.yml").read_text(encoding="utf-8")
        tasks = (ROLE_ROOT / "tasks/main.yml").read_text(encoding="utf-8")

        self.assertIn(
            "monitoring_alertmanager_upstream: 127.0.0.1:9093", defaults
        )
        self.assertIn(
            'monitoring_alertmanager_upstream == "127.0.0.1:9093"\n'
            '            or monitoring_alertmanager_upstream == '
            '"192.168.101.10:9093"',
            tasks,
        )

    def test_old_local_state_is_included_in_the_cleanup_task(self) -> None:
        tasks = (ROLE_ROOT / "tasks/main.yml").read_text(encoding="utf-8")

        cleanup_task = tasks.split(
            "Remove retired local Grafana/Alertmanager state", 1
        )[1].split("- name:", 1)[0]
        self.assertIn("monitoring_config_dir }}/alertmanager.yml", cleanup_task)
        self.assertIn("monitoring_data_dir }}/alertmanager", cleanup_task)


class BlockyK3sMigrationTests(unittest.TestCase):
    def test_blocky_role_is_gone_entirely(self) -> None:
        self.assertFalse((PROJECT_ROOT / "ansible/roles/blocky").exists())

        site = (PROJECT_ROOT / "ansible/playbooks/site.yml").read_text(
            encoding="utf-8"
        )
        # Exact line: "- blocky_ipv6_relay" also contains "- blocky".
        self.assertNotIn("\n    - blocky\n", site)

    def test_blocky_enabled_is_unconditional_not_a_dead_cross_role_reference(
        self,
    ) -> None:
        # Blocky is always on; a guarded reference to the deleted role would always be false.
        defaults = (ROLE_ROOT / "defaults/main.yml").read_text(encoding="utf-8")

        self.assertIn("monitoring_blocky_enabled: true", defaults)
        self.assertNotIn("blocky_enabled | default", defaults)

    def test_dead_postgres_cross_role_vars_are_gone(self) -> None:
        defaults = (ROLE_ROOT / "defaults/main.yml").read_text(encoding="utf-8")

        for dead_default in (
            "monitoring_blocky_postgres_port",
            "monitoring_blocky_postgres_database",
            "monitoring_blocky_postgres_user",
            "monitoring_blocky_postgres_password",
        ):
            with self.subTest(default=dead_default):
                self.assertNotIn(f"{dead_default}:", defaults)


class NtfyRelayServiceTests(unittest.TestCase):
    def test_defaults_are_loopback_only_and_do_not_reuse_grafanas_secrets_dir(self) -> None:
        defaults = (ROLE_ROOT / "defaults/main.yml").read_text(encoding="utf-8")

        self.assertIn("monitoring_ntfy_relay_port: 9096", defaults)
        self.assertIn(
            "monitoring_ntfy_relay_service_user: monitoring-ntfy-relay", defaults
        )

    def test_relay_gets_its_own_directory_not_grafanas_shared_secrets_dir(self) -> None:
        # The relay keeps its own directory, independent of any other service.
        tasks = (ROLE_ROOT / "tasks/main.yml").read_text(encoding="utf-8")

        self.assertIn("monitoring_data_dir }}/ntfy-relay/ntfy_topic", tasks)
        self.assertIn("monitoring_ntfy_relay_service_user }}\"\n        group:", tasks)

    def test_systemd_unit_is_hardened_and_allows_outbound_https(self) -> None:
        rendered = render("ntfy-relay.service.j2")

        self.assertIn("NoNewPrivileges=true", rendered)
        self.assertIn("ProtectSystem=strict", rendered)
        self.assertIn("CapabilityBoundingSet=\n", rendered)
        self.assertIn("RestrictAddressFamilies=AF_INET AF_INET6", rendered)
        # Not loopback-restricted: it calls ntfy.sh, which has no fixed IP. Check the directive, not prose.
        self.assertNotIn("IPAddressAllow=", rendered)
        self.assertIn("ExecStart=/usr/bin/python3", rendered)
        self.assertIn("ntfy_relay.py", rendered)

    def test_relay_is_health_checked(self) -> None:
        tasks = (ROLE_ROOT / "tasks/main.yml").read_text(encoding="utf-8")

        self.assertIn("Verify ntfy relay is ready on host loopback", tasks)
        self.assertIn("/healthz", tasks)


class K3sKubeletMetricsTests(unittest.TestCase):
    def kubelet_job(self, **overrides: object) -> dict | None:
        parsed = yaml.safe_load(render("prometheus.yml.j2", **overrides))
        return next(
            (j for j in parsed["scrape_configs"] if j["job_name"] == "k3s_kubelet_cadvisor"),
            None,
        )

    def test_scrape_job_is_off_by_default(self) -> None:
        self.assertIsNone(self.kubelet_job())

    def test_scrape_verifies_tls_and_reads_the_token_from_a_file(self) -> None:
        job = self.kubelet_job(monitoring_k3s_kubelet_metrics_enabled=True)

        self.assertEqual(job["scheme"], "https")
        self.assertEqual(job["metrics_path"], "/metrics/cadvisor")
        self.assertEqual(
            job["authorization"]["credentials_file"], "/etc/prometheus/k3s/kubelet-token"
        )
        self.assertEqual(job["tls_config"]["ca_file"], "/etc/prometheus/k3s/server-ca.crt")
        self.assertNotIn("insecure_skip_verify", job["tls_config"])
        self.assertEqual(
            [c["labels"]["node"] for c in job["static_configs"]], ["k3s-node-1", "k3s-node-2"]
        )

    def test_allowlist_keeps_container_last_seen_out(self) -> None:
        # ContainerDown is the homeserver's alert; k3s Pods come and go by design.
        job = self.kubelet_job(monitoring_k3s_kubelet_metrics_enabled=True)
        relabel = job["metric_relabel_configs"][0]

        self.assertEqual(relabel["action"], "keep")
        self.assertIn("memory_working_set_bytes", relabel["regex"])
        self.assertNotIn("last_seen", relabel["regex"])

    def test_token_is_required_and_written_owner_only(self) -> None:
        tasks = (ROLE_ROOT / "tasks/main.yml").read_text(encoding="utf-8")
        defaults = (ROLE_ROOT / "defaults/main.yml").read_text(encoding="utf-8")

        self.assertIn('monitoring_k3s_kubelet_token: ""', defaults)
        self.assertIn("monitoring_k3s_kubelet_token | trim | length > 0", tasks)
        self.assertIn('dest: "{{ monitoring_k3s_kubelet_dir }}/kubelet-token"\n', tasks)
        self.assertIn('"{{ monitoring_k3s_kubelet_dir }}:/etc/prometheus/k3s:ro"', tasks)

    def test_homeserver_container_queries_filter_on_their_own_job(self) -> None:
        # Both cAdvisor jobs share metric names.
        alerts = render("alert_rules.yml.j2")
        dashboard = (ROLE_ROOT / "files/dashboards/container-health.json").read_text(
            encoding="utf-8"
        )

        self.assertNotIn("container_last_seen{name", alerts)
        self.assertEqual(alerts.count('container_last_seen{job="cadvisor", '), 4)
        self.assertNotIn('container_cpu_usage_seconds_total{name', dashboard)
        self.assertNotIn('container_memory_working_set_bytes{name', dashboard)


class GithubRunnerExporterRoleTests(unittest.TestCase):
    def test_scrape_job_only_when_enabled(self) -> None:
        def job_names(**overrides: object) -> set[str]:
            parsed = yaml.safe_load(render("prometheus.yml.j2", **overrides))
            return {j["job_name"] for j in parsed["scrape_configs"]}

        self.assertNotIn("github_runner_exporter", job_names())
        self.assertIn(
            "github_runner_exporter", job_names(monitoring_github_runner_exporter_enabled=True)
        )

    def test_unit_is_hardened_and_reads_the_token_from_a_file(self) -> None:
        unit = render(
            "github-runner-exporter.service.j2",
            monitoring_github_runner_exporter_repositories=["dyndns", "website"],
        )

        self.assertIn('Environment="GITHUB_REPOSITORIES=dyndns,website"', unit)
        self.assertIn("GITHUB_TOKEN_FILE=/var/lib/monitoring/github-runner-exporter/token", unit)
        self.assertIn("User=monitoring-github-runner-exporter", unit)
        self.assertIn("ProtectSystem=strict", unit)
        self.assertIn("CapabilityBoundingSet=\n", unit)

    def test_token_is_required_and_written_owner_only(self) -> None:
        tasks = (ROLE_ROOT / "tasks/main.yml").read_text(encoding="utf-8")
        defaults = (ROLE_ROOT / "defaults/main.yml").read_text(encoding="utf-8")

        self.assertIn('monitoring_github_runner_exporter_token: ""', defaults)
        self.assertIn("monitoring_github_runner_exporter_token | trim | length > 0", tasks)
        token_task = tasks[tasks.index("- name: Install GitHub runner exporter token") :]
        token_task = token_task[: token_task.index("- name:", 10)]
        self.assertIn('mode: "0400"', token_task)
        self.assertIn("no_log: true", token_task)


class StalwartBackupTests(unittest.TestCase):
    def test_stalwart_down_alert_needs_kube_state_metrics(self) -> None:
        def alerts(**overrides: object) -> dict[str, dict]:
            parsed = yaml.safe_load(render("alert_rules.yml.j2", **overrides))
            return {r["alert"]: r for g in parsed["groups"] for r in g["rules"] if "alert" in r}

        rule = alerts(monitoring_k3s_node_health_enabled=True)["StalwartDown"]
        self.assertIn('deployment="stalwart"', rule["expr"])
        self.assertIn("absent(", rule["expr"])
        self.assertEqual(rule["for"], "15m")
        self.assertNotIn("StalwartDown", alerts(monitoring_k3s_node_health_enabled=False))

    def test_role_pins_the_ids_the_cronjob_runs_as(self) -> None:
        defaults = yaml.safe_load(
            (PROJECT_ROOT / "ansible/roles/stalwart_backup/defaults/main.yml").read_text()
        )

        self.assertEqual((defaults["stalwart_backup_uid"], defaults["stalwart_backup_gid"]), (982, 977))
        self.assertFalse(defaults["stalwart_backup_enabled"])


if __name__ == "__main__":
    unittest.main()
