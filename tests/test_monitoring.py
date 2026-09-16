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
    # monitoring_blocky_enabled now defaults to a real, unconditional
    # true in defaults/main.yml (the blocky role it used to be a
    # default()-guarded cross-role reference into is gone entirely --
    # see that file's own comment) -- forced off here so every render()
    # that doesn't care about blocky stays isolated from it by default,
    # the same way callers already pass real values for other required
    # vars below, and must say so explicitly to test the "on" path.
    ctx = {**defaults, "monitoring_blocky_enabled": False, **overrides}
    return env.get_template(name).render(**ctx)


class MonitoringRoleTests(unittest.TestCase):
    def test_defaults_are_opt_in_and_loopback_only(self) -> None:
        defaults = (ROLE_ROOT / "defaults/main.yml").read_text(encoding="utf-8")

        self.assertIn("monitoring_enabled: false", defaults)
        self.assertIn("monitoring_bind_address: 127.0.0.1", defaults)

    def test_image_pull_task_uses_digest_only_not_tag_and_digest(self) -> None:
        # Confirmed live 2026-09-07: pulling a combined name:tag@digest
        # reference (what the docker_container tasks below correctly
        # use) left the local image stored with an untagged (<none>)
        # tag -- Docker's own behavior, not an Ansible module bug --
        # so this task's idempotency check never found a match and
        # reported "changed" on every single apply. Digest alone (no
        # tag) sidesteps it: Docker doesn't try to apply a tag it
        # wasn't given, so the before/after comparison matches on
        # repeat runs.
        tasks = (ROLE_ROOT / "tasks/main.yml").read_text(encoding="utf-8")

        self.assertIn(
            'name: "{{ item.name }}@{{ item.digest }}"', tasks
        )
        # The old shape pulled the loop var directly (the full
        # tag@digest string) -- checking for that exact task body, not
        # a bare substring, since monitoring_prometheus_image (tag@
        # digest) legitimately still appears elsewhere for the
        # docker_container tasks, which are correct as-is.
        self.assertNotIn('name: "{{ item }}"\n        source: pull', tasks)

    def test_secrets_are_required_not_left_default(self) -> None:
        # The ntfy topic and the SES SMTP credentials all have
        # empty-string defaults and must be set through SOPS before the
        # first deploy, same as deluge_web_password.
        # monitoring_grafana_admin_password isn't among these any more
        # -- Grafana no longer runs as a Docker container this role
        # manages (see GrafanaK3sMigrationTests), so nothing here
        # consumes that variable.
        tasks = (ROLE_ROOT / "tasks/main.yml").read_text(encoding="utf-8")
        defaults = (ROLE_ROOT / "defaults/main.yml").read_text(encoding="utf-8")

        self.assertIn('monitoring_ntfy_topic: ""', defaults)
        self.assertIn('monitoring_ses_smtp_username: ""', defaults)
        self.assertIn('monitoring_ses_smtp_password: ""', defaults)
        self.assertIn("monitoring_ntfy_topic | trim | length >= 20", tasks)
        self.assertIn("monitoring_ses_smtp_username | trim | length > 0", tasks)
        self.assertIn("monitoring_ses_smtp_password | trim | length > 0", tasks)

    def test_every_container_uses_host_networking_consistently(self) -> None:
        # Mixing host and bridge networking would mean 127.0.0.1 inside a
        # bridge-networked container resolves to its own loopback, not
        # the host's, silently breaking scrapes between containers --
        # confirmed live as a real bug before this was ever deployed.
        # published_ports has no meaning under network_mode: host, so
        # it must never appear alongside it.
        tasks = (ROLE_ROOT / "tasks/main.yml").read_text(encoding="utf-8")

        # node_exporter, cAdvisor, blackbox_exporter, Prometheus --
        # every container this role runs now that Grafana's and
        # Alertmanager's own containers are both gone (see
        # GrafanaK3sMigrationTests/AlertmanagerK3sMigrationTests).
        self.assertEqual(tasks.count("        network_mode: host"), 4)
        self.assertNotIn("published_ports:", tasks)

    def test_cadvisor_port_does_not_collide_with_nextcloud_aio_admin_ui(
        self,
    ) -> None:
        # Confirmed live: cAdvisor's network_mode: host listener silently
        # won port 8080 over Nextcloud AIO's own mastercontainer admin
        # interface (unrelated to Ansible, always meant to be reachable
        # at https://<homeserver>:8080), leaving nothing listening there
        # for AIO's own interface -- only noticed when that interface was
        # needed again and found unreachable.
        defaults = (ROLE_ROOT / "defaults/main.yml").read_text(encoding="utf-8")

        self.assertNotIn("monitoring_cadvisor_port: 8080", defaults)

    def test_cadvisor_docker_socket_access_is_read_only(self) -> None:
        # Precedented by nextcloud_aio's own master container already
        # mounting docker.sock read-only -- not a new exception.
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
        # Not the HDD -- small footprint, and the HDD is spinning disk
        # already busy with Nextcloud/Deluge I/O. The root disk only had
        # 23 GiB free at last check, so retention is capped by both time
        # and size, not just time.
        defaults = (ROLE_ROOT / "defaults/main.yml").read_text(encoding="utf-8")

        self.assertIn("monitoring_data_dir: /var/lib/monitoring", defaults)
        self.assertNotIn("black-hdd", defaults)
        self.assertIn("monitoring_prometheus_retention_time: 30d", defaults)
        self.assertIn("monitoring_prometheus_retention_size: 2GB", defaults)

    def test_prometheus_k3s_bind_address_defaults_to_disabled(self) -> None:
        defaults = (ROLE_ROOT / "defaults/main.yml").read_text(encoding="utf-8")

        self.assertIn('monitoring_prometheus_k3s_bind_address: ""', defaults)

    def test_prometheus_k3s_bind_address_is_additive_and_allowlisted(self) -> None:
        # Additive (Prometheus's own --web.listen-address flag can be
        # repeated), not a switch away from monitoring_bind_address --
        # node_exporter/cAdvisor/blackbox/self-scrape all stay hardcoded
        # to 127.0.0.1 in prometheus.yml.j2 regardless of Prometheus's
        # own listen address, so loopback must keep working. 192.168.101.1
        # is this homeserver's own address on the k3s VM's isolated
        # network, the same trust boundary every other such exception in
        # this repo uses -- a closed allowlist of one address, never
        # 0.0.0.0 (Prometheus's API has no auth of its own at all).
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
        # Same additive shape as monitoring_prometheus_k3s_bind_address
        # above -- the still-Docker-based Alertmanager on this same host
        # keeps needing the loopback listener throughout the migration,
        # so this only ever adds a second one (see ntfy_relay.py's own
        # LISTEN_HOST_K3S comment), never replaces it.
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
        ]:
            with self.subTest(target=target):
                self.assertIn(target, defaults)

    def test_stale_shared_ingress_ping_probe_is_gone(self) -> None:
        # Confirmed live (2026-09-01) as a real, actively-firing false
        # alarm: shared_ingress (and its own loopback :8082 ping
        # endpoint) was deleted the same day Traefik moved fully into
        # k3s, but this probe target was never removed, so it kept
        # alerting on a service that was never coming back. The role's
        # own comment explaining the removal still names the old port
        # for documentation, so check the actual target list, not the
        # whole file's text.
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
        # relabel_configs must stay nested under the blackbox_http job --
        # a real bug during development had it accidentally hoisted to
        # the document root by a stray Jinja whitespace-trim, silently
        # dropping it from the job it was meant to configure.
        self.assertIn("relabel_configs", blackbox_job)
        self.assertNotIn("relabel_configs", parsed)

    def test_authenticated_targets_are_probed_with_the_401_tolerant_module(self) -> None:
        # torrent.jkandler.de and ai.jkandler.de sit behind Traefik Basic
        # Auth, and no plaintext credential for either is available to
        # this role (only the bcrypt hash the k3s-native ingress uses)
        # -- probing
        # them with the plain http_2xx module would permanently alert,
        # since blackbox_exporter never sends the required Authorization
        # header and always gets 401. Confirmed live: both fired
        # ServiceUnreachable continuously until this module was added.
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
        # A real switch (rendering exactly one target), not an addition
        # -- Alertmanager's own target list notifies every address in it
        # for the same firing alert, so both at once would double-fire
        # every real notification. See monitoring_alertmanager_upstream's
        # own comment in defaults/main.yml.
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
        # blocky_health added 2026-09-16, on top of ADR 0017's original
        # four -- a real, separately-motivated gap (Blocky's own
        # query-log writer has no reconnect logic), not part of that
        # ADR's own scope.
        self.assertEqual(
            group_names,
            {
                "host_health",
                "container_health",
                "service_reachability",
                "certificate_expiry",
                "blocky_health",
            },
        )

    def test_container_down_ignores_stale_ids_from_ordinary_recreates(self) -> None:
        # Confirmed live (2026-08-29): cAdvisor's container_last_seen is
        # also labeled by "id" (the container's cgroup path), which
        # changes every time site.yml *recreates* a container (a config
        # change, not just a restart). The old id's series then just
        # freezes -- it doesn't disappear -- while a fresh series starts
        # under the new id. Comparing staleness per exact label set (the
        # bug this regresses) fires ContainerDown for that dead ghost
        # series on every ordinary recreate, even though the actual
        # current container is healthy. This alert fired via both ntfy
        # and email on a real, unremarkable site.yml run before the fix.
        rendered = render("alert_rules.yml.j2")
        parsed = yaml.safe_load(rendered)

        container_health = next(
            group for group in parsed["groups"] if group["name"] == "container_health"
        )
        container_down = next(
            rule for rule in container_health["rules"] if rule["alert"] == "ContainerDown"
        )

        self.assertIn("max by (name) (container_last_seen", container_down["expr"])

    def test_blocky_query_log_stale_alert_covers_both_missing_and_flat_metric(
        self,
    ) -> None:
        # Real gap this closes, confirmed live 2026-09-02: an ~18h
        # silent logging gap after Postgres restarted mid-Pod-lifetime,
        # only caught by chance -- Blocky's own `up` metric stayed
        # green the whole time. Both failure shapes need covering:
        # absent() if the exporter scrape target itself is down, and a
        # flat increase() if the exporter is up but Blocky's own writer
        # silently stopped.
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

    def test_dashboards_are_valid_json_and_reference_the_prometheus_datasource(
        self,
    ) -> None:
        # These files are no longer installed by this role -- Grafana
        # itself runs as a k3s-native copy now (see
        # GrafanaK3sMigrationTests) -- but files/dashboards/ stays as
        # the canonical source infra/k3s-apps' own modules/grafana
        # vendors a synced copy from, same pattern as
        # nextcloud_tools_service.py's own role.
        dashboards_dir = ROLE_ROOT / "files/dashboards"
        expected = {
            "host-health.json",
            "container-health.json",
            "service-reachability.json",
            "service-status.json",
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
        # The Blocky datasource half of this used to be checked here too
        # (including the "database belongs in jsonData, not as a
        # top-level field" fix confirmed live 2026-08-28), but that
        # datasource is now provisioned by infra/k3s-apps' own
        # modules/grafana (templates/datasources.yaml.tftpl) instead of
        # a template in this role -- see that file for the equivalent
        # fixed structure, confirmed live 2026-08-30 via Grafana's own
        # /api/datasources response.
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
        # Same clean-switch reasoning as monitoring_alertmanager_upstream
        # -- see monitoring_blocky_upstream's own comment in
        # defaults/main.yml.
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
        # Added 2026-09-16 to close a real gap: Blocky's own query-log
        # Postgres writer has no reconnect logic, and Blocky's own `up`
        # metric stays green through a silent logging gap -- see
        # BlockyQueryLogStale's own tests below for the alert this
        # scrape target enables.
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
        # Unlike monitoring_blocky_upstream, this scrape target only
        # ever has one possible value (infra/k3s-apps' own modules/
        # blocky) -- there's no homeserver-side Postgres to point at
        # instead, so it's gated on the same flag rather than getting
        # its own.
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

        # Neither dashboard should still carry unresolved Grafana
        # "import with variables" placeholders -- these are file-
        # provisioned with fixed values, not imported interactively.
        for path in (dashboards_dir / "blocky.json", dashboards_dir / "blocky-postgres.json"):
            with self.subTest(path=path.name):
                raw = path.read_text()
                self.assertNotIn("${DS_", raw)
                self.assertNotIn("${VAR_", raw)

    def test_query_variables_refresh_on_dashboard_load(self) -> None:
        # Confirmed live (2026-08-28, a real deploy): the DNS Type,
        # Client name, and Response type filters used refresh: 2 ("On
        # Time Range Change"), inherited as-is from the vendored
        # dashboard. That means their defining SQL never ran on a plain
        # dashboard load, only on a time-range change -- so on first
        # view "options" stayed empty, "All" (the default selection)
        # expanded to nothing, and every panel's `IN (...)` clause
        # became `IN ()`, a Postgres syntax error (SQLSTATE 42601),
        # reproduced directly through Grafana's own query API. refresh:
        # 1 ("On Dashboard Load") forces these to populate with real
        # values every time the dashboard is opened, not just after a
        # time-range change.
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
        # Grafana itself now runs as a k3s-native copy (infra/k3s-apps,
        # modules/grafana), reached through
        # shared_ingress_grafana_upstream -- confirmed live 2026-08-30
        # (real admin login, both datasources healthy, a real PromQL
        # query returning real scrape data) before this role's own
        # Docker deployment was removed.
        tasks = (ROLE_ROOT / "tasks/main.yml").read_text(encoding="utf-8")

        self.assertNotIn("Ensure Grafana container is running", tasks)
        self.assertNotIn("Verify Grafana is ready on host loopback", tasks)
        self.assertNotIn("Install Grafana", tasks)
        self.assertNotIn("monitoring_grafana_image", tasks)
        self.assertNotIn("monitoring_grafana_container_user", tasks)

    def test_dead_defaults_and_templates_are_gone(self) -> None:
        defaults = (ROLE_ROOT / "defaults/main.yml").read_text(encoding="utf-8")

        # Checked as an actual variable assignment ("name:"), not a bare
        # substring -- these are legitimately still mentioned in prose in
        # defaults/main.yml's comments explaining why they went away.
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
        # Learned directly from a real near-miss during open_webui's own
        # reshape this same session: an unrelated site.yml run can
        # silently resurrect an already-stopped container as long as its
        # role still manages one. Removing the old config/data/secrets
        # here, unconditionally, closes that window rather than leaving
        # stale state a future change could accidentally depend on. This
        # same task also handles Alertmanager's own retired state --
        # see AlertmanagerK3sMigrationTests.
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
        # Alertmanager itself now runs as a k3s-native copy (infra/
        # k3s-apps, modules/alertmanager), reached by Prometheus over
        # monitoring_alertmanager_upstream -- confirmed live 2026-08-31
        # (a synthetic test alert posted straight to its own API reached
        # both ntfy and the real SES inbox, and Prometheus's own
        # /api/v1/alertmanagers showed exactly that target, healthy,
        # nothing dropped) before this role's own Docker deployment of
        # it was removed.
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
        # Unlike Alertmanager/Grafana, Blocky's own Docker deployment
        # never lived inside this role -- it was a fully separate role
        # (ansible/roles/blocky), deleted entirely once infra/k3s-apps'
        # own modules/blocky was confirmed serving real production LAN
        # DNS traffic (2026-09-01), the same bar shared_ingress's own
        # deletion was held to.
        self.assertFalse((PROJECT_ROOT / "ansible/roles/blocky").exists())

        site = (PROJECT_ROOT / "ansible/playbooks/site.yml").read_text(
            encoding="utf-8"
        )
        # Exact line, not a substring check -- "- blocky_ipv6_relay" (a
        # real, separate, still-existing role) also contains "- blocky".
        self.assertNotIn("\n    - blocky\n", site)

    def test_blocky_enabled_is_unconditional_not_a_dead_cross_role_reference(
        self,
    ) -> None:
        # monitoring_blocky_enabled used to be a default()-guarded
        # cross-role reference into the now-deleted blocky role's own
        # blocky_enabled -- with that role gone, nothing sets
        # blocky_enabled anywhere any more, so the reference would
        # silently always resolve to the guard's own false default.
        # Blocky itself is a permanent, always-on k3s fixture now, not
        # optionally toggled by any Ansible role, so this is a plain,
        # unconditional true instead.
        defaults = (ROLE_ROOT / "defaults/main.yml").read_text(encoding="utf-8")

        self.assertIn("monitoring_blocky_enabled: true", defaults)
        self.assertNotIn("blocky_enabled | default", defaults)

    def test_dead_postgres_cross_role_vars_are_gone(self) -> None:
        # monitoring_blocky_postgres_port/_database/_user/_password
        # were already unused anywhere in this role even before the
        # blocky role's own deletion -- Grafana's own blocky-postgresql
        # datasource is provisioned by infra/k3s-apps' own
        # modules/grafana (templates/datasources.yaml.tftpl), not by
        # anything reading these.
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
        # Historically the shared secrets/ dir was owned by Grafana's own
        # service account (0750) -- a different user couldn't read a file
        # dropped in there, so the relay always needed its own directory.
        # That secrets/ dir is gone now that Grafana's own container is
        # (see GrafanaK3sMigrationTests), but the relay's independence
        # from it is still the point being tested here.
        tasks = (ROLE_ROOT / "tasks/main.yml").read_text(encoding="utf-8")

        self.assertIn("monitoring_data_dir }}/ntfy-relay/ntfy_topic", tasks)
        self.assertIn("monitoring_ntfy_relay_service_user }}\"\n        group:", tasks)

    def test_systemd_unit_is_hardened_and_allows_outbound_https(self) -> None:
        rendered = render("ntfy-relay.service.j2")

        self.assertIn("NoNewPrivileges=true", rendered)
        self.assertIn("ProtectSystem=strict", rendered)
        self.assertIn("CapabilityBoundingSet=\n", rendered)
        self.assertIn("RestrictAddressFamilies=AF_INET AF_INET6", rendered)
        # Deliberately NOT restricted to loopback via IPAddressAllow=, unlike
        # nextcloud-tools' unit -- this service's job is an outbound HTTPS
        # call to ntfy.sh, a CDN-backed host without a fixed IP to pin. (The
        # unit's own comment mentions the directive name in prose, so check
        # for the directive itself, not just the substring.)
        self.assertNotIn("IPAddressAllow=", rendered)
        self.assertIn("ExecStart=/usr/bin/python3", rendered)
        self.assertIn("ntfy_relay.py", rendered)

    def test_relay_is_health_checked(self) -> None:
        # Used to also assert this installs before Alertmanager's own
        # docker_container task -- moot now that Alertmanager isn't
        # deployed by this role at all any more (see
        # AlertmanagerK3sMigrationTests). The relay's own real config
        # (webhook target, dual-channel routing) now lives in
        # infra/k3s-apps' own modules/alertmanager templates/
        # alertmanager.yml.tftpl instead of a template here.
        tasks = (ROLE_ROOT / "tasks/main.yml").read_text(encoding="utf-8")

        self.assertIn("Verify ntfy relay is ready on host loopback", tasks)
        self.assertIn("/healthz", tasks)


if __name__ == "__main__":
    unittest.main()
