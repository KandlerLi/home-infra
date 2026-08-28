from __future__ import annotations

import json
import unittest
from pathlib import Path

import yaml
from jinja2 import Environment, FileSystemLoader

PROJECT_ROOT = Path(__file__).resolve().parents[1]
ROLE_ROOT = PROJECT_ROOT / "ansible/roles/monitoring"
SHARED_INGRESS_ROOT = PROJECT_ROOT / "ansible/roles/shared_ingress"


def render(name: str, **overrides: object) -> str:
    env = Environment(loader=FileSystemLoader(str(ROLE_ROOT / "templates")))
    env.filters["bool"] = bool
    defaults = yaml.safe_load((ROLE_ROOT / "defaults/main.yml").read_text())
    # monitoring_blocky_* are default()-guarded cross-role references
    # (see defaults/main.yml) -- their raw, un-rendered "{{ ... }}"
    # string is truthy under a plain bool() call, so every render() that
    # doesn't care about blocky must say so explicitly, the same way
    # callers already pass real values for other required vars below.
    ctx = {**defaults, "monitoring_blocky_enabled": False, **overrides}
    return env.get_template(name).render(**ctx)


def render_shared_ingress(**overrides: object) -> str:
    env = Environment(
        loader=FileSystemLoader(str(SHARED_INGRESS_ROOT / "templates"))
    )
    env.filters["bool"] = bool
    defaults = yaml.safe_load(
        (SHARED_INGRESS_ROOT / "defaults/main.yml").read_text()
    )
    ctx = {**defaults, **overrides}
    return env.get_template("dynamic.yml.j2").render(**ctx)


class MonitoringRoleTests(unittest.TestCase):
    def test_defaults_are_opt_in_and_loopback_only(self) -> None:
        defaults = (ROLE_ROOT / "defaults/main.yml").read_text(encoding="utf-8")

        self.assertIn("monitoring_enabled: false", defaults)
        self.assertIn("monitoring_bind_address: 127.0.0.1", defaults)

    def test_secrets_are_required_not_left_default(self) -> None:
        # Grafana's admin password, the ntfy topic, and the SES SMTP
        # credentials all have empty-string defaults and must be set
        # through SOPS before the first deploy, same as
        # deluge_web_password.
        tasks = (ROLE_ROOT / "tasks/main.yml").read_text(encoding="utf-8")
        defaults = (ROLE_ROOT / "defaults/main.yml").read_text(encoding="utf-8")

        self.assertIn('monitoring_grafana_admin_password: ""', defaults)
        self.assertIn('monitoring_ntfy_topic: ""', defaults)
        self.assertIn('monitoring_ses_smtp_username: ""', defaults)
        self.assertIn('monitoring_ses_smtp_password: ""', defaults)
        self.assertIn("monitoring_grafana_admin_password | trim | length >= 16", tasks)
        self.assertIn("monitoring_ntfy_topic | trim | length >= 20", tasks)
        self.assertIn("monitoring_ses_smtp_username | trim | length > 0", tasks)
        self.assertIn("monitoring_ses_smtp_password | trim | length > 0", tasks)

    def test_grafana_admin_password_is_file_based_not_a_raw_env_var(self) -> None:
        # Grafana's own docker image supports the "_FILE" env-var suffix
        # convention specifically so a secret doesn't sit in plain env,
        # visible to anyone with `docker inspect` access -- the same
        # reasoning home_agent's OPENAI_API_KEY_FILE already follows.
        tasks = (ROLE_ROOT / "tasks/main.yml").read_text(encoding="utf-8")

        self.assertIn("GF_SECURITY_ADMIN_PASSWORD__FILE", tasks)
        self.assertNotIn("GF_SECURITY_ADMIN_PASSWORD:", tasks)

    def test_every_container_uses_host_networking_consistently(self) -> None:
        # Mixing host and bridge networking would mean 127.0.0.1 inside a
        # bridge-networked container resolves to its own loopback, not
        # the host's, silently breaking scrapes between containers --
        # confirmed live as a real bug before this was ever deployed.
        # published_ports has no meaning under network_mode: host, so
        # it must never appear alongside it.
        tasks = (ROLE_ROOT / "tasks/main.yml").read_text(encoding="utf-8")

        # node_exporter, cAdvisor, blackbox_exporter, Prometheus,
        # Alertmanager, Grafana -- every container this role runs.
        self.assertEqual(tasks.count("        network_mode: host"), 6)
        self.assertNotIn("published_ports:", tasks)

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

    def test_blackbox_probes_every_public_service_and_traefiks_own_ping(self) -> None:
        defaults = (ROLE_ROOT / "defaults/main.yml").read_text(encoding="utf-8")

        for target in [
            "https://nextcloud.jkandler.de/",
            "https://ai.jkandler.de/healthz",
            "https://torrent.jkandler.de/",
            "https://www.jkandler.de/",
            "http://jkandler.de/",
            "http://127.0.0.1:8082/ping",
        ]:
            with self.subTest(target=target):
                self.assertIn(target, defaults)

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
        # this role (only the bcrypt hash shared_ingress uses) -- probing
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

    def test_alert_rules_cover_all_four_adr_0017_scope_areas(self) -> None:
        rendered = render("alert_rules.yml.j2")
        parsed = yaml.safe_load(rendered)

        group_names = {group["name"] for group in parsed["groups"]}
        self.assertEqual(
            group_names,
            {"host_health", "container_health", "service_reachability", "certificate_expiry"},
        )

    def test_alertmanager_routes_to_both_ntfy_and_ses_email(self) -> None:
        # Dual-channel on purpose: one channel being misconfigured
        # shouldn't mean silence. ntfy goes through the local relay
        # (which reformats Alertmanager's JSON before it ever reaches
        # ntfy.sh), not straight to ntfy.sh itself.
        rendered = render(
            "alertmanager.yml.j2",
            monitoring_ntfy_topic="a" * 25,
            monitoring_ses_smtp_username="AKIAEXAMPLE",
            monitoring_ses_smtp_password="examplepassword",
        )
        parsed = yaml.safe_load(rendered)

        receiver = parsed["receivers"][0]
        self.assertIn("webhook_configs", receiver)
        self.assertIn("email_configs", receiver)
        self.assertEqual(
            receiver["webhook_configs"][0]["url"], "http://127.0.0.1:9096/webhook"
        )
        self.assertEqual(
            receiver["email_configs"][0]["to"], "julian.kandler@outlook.com"
        )

    def test_grafana_datasource_points_at_prometheus_on_host_loopback(self) -> None:
        rendered = render("grafana_datasources.yml.j2")
        parsed = yaml.safe_load(rendered)

        self.assertEqual(len(parsed["datasources"]), 1)
        datasource = parsed["datasources"][0]
        self.assertEqual(datasource["type"], "prometheus")
        self.assertEqual(datasource["url"], "http://127.0.0.1:9090")
        self.assertTrue(datasource["isDefault"])

    def test_dashboards_are_valid_json_and_reference_the_prometheus_datasource(
        self,
    ) -> None:
        dashboards_dir = ROLE_ROOT / "files/dashboards"
        expected = {
            "host-health.json",
            "container-health.json",
            "service-reachability.json",
        }

        for name in expected:
            with self.subTest(dashboard=name):
                data = json.loads((dashboards_dir / name).read_text(encoding="utf-8"))
                self.assertTrue(data["panels"])
                for panel in data["panels"]:
                    for target in panel.get("targets", []):
                        self.assertEqual(target["datasource"]["uid"], "prometheus")

    def test_dashboards_are_installed_and_referenced_by_the_provider_config(
        self,
    ) -> None:
        tasks = (ROLE_ROOT / "tasks/main.yml").read_text(encoding="utf-8")
        rendered = render("grafana_dashboards_provider.yml.j2")
        parsed = yaml.safe_load(rendered)

        self.assertIn("host-health.json", tasks)
        self.assertIn("container-health.json", tasks)
        self.assertIn("service-reachability.json", tasks)
        self.assertEqual(
            parsed["providers"][0]["options"]["path"], "/var/lib/grafana-dashboards"
        )

    def test_role_is_registered_in_site_yml_after_shared_ingress(self) -> None:
        # After shared_ingress specifically: by the time monitoring
        # deploys, every service it probes/monitors should already exist.
        site_yml = (PROJECT_ROOT / "ansible/playbooks/site.yml").read_text(
            encoding="utf-8"
        )

        shared_ingress_index = site_yml.index("- shared_ingress")
        monitoring_index = site_yml.index("- monitoring")
        self.assertGreater(monitoring_index, shared_ingress_index)


class BlockyIntegrationTests(unittest.TestCase):
    def test_scrape_job_and_datasource_only_appear_when_blocky_is_enabled(
        self,
    ) -> None:
        prometheus_off = yaml.safe_load(render("prometheus.yml.j2"))
        datasources_off = yaml.safe_load(render("grafana_datasources.yml.j2"))

        job_names_off = {job["job_name"] for job in prometheus_off["scrape_configs"]}
        self.assertNotIn("blocky", job_names_off)
        self.assertEqual(len(datasources_off["datasources"]), 1)

    def test_scrape_job_and_datasource_render_correctly_when_enabled(self) -> None:
        overrides = {
            "monitoring_blocky_enabled": True,
            "monitoring_blocky_http_port": 4000,
            "monitoring_blocky_postgres_port": 5432,
            "monitoring_blocky_postgres_database": "blocky_query_log",
            "monitoring_blocky_postgres_user": "blocky",
            "monitoring_blocky_postgres_password": "a-generated-password-1234",
        }
        prometheus_on = yaml.safe_load(render("prometheus.yml.j2", **overrides))
        datasources_on = yaml.safe_load(render("grafana_datasources.yml.j2", **overrides))

        blocky_job = next(
            job
            for job in prometheus_on["scrape_configs"]
            if job["job_name"] == "blocky"
        )
        self.assertIn(
            "127.0.0.1:4000", blocky_job["static_configs"][0]["targets"]
        )

        blocky_datasource = next(
            ds
            for ds in datasources_on["datasources"]
            if ds["uid"] == "blocky-postgres"
        )
        self.assertEqual(blocky_datasource["type"], "postgres")
        self.assertEqual(blocky_datasource["url"], "127.0.0.1:5432")
        # database belongs in jsonData, not as a top-level field --
        # confirmed live (2026-08-28): with it only at the top level,
        # Grafana 13.1.4 showed "You do not currently have a default
        # database configured for this data source" on every panel, even
        # though the datasource's own health check reported OK.
        self.assertEqual(
            blocky_datasource["jsonData"]["database"], "blocky_query_log"
        )
        self.assertNotIn("database", blocky_datasource)
        self.assertEqual(
            blocky_datasource["secureJsonData"]["password"],
            "a-generated-password-1234",
        )

    def test_datasource_install_task_never_logs_the_postgres_password(self) -> None:
        tasks = (ROLE_ROOT / "tasks/main.yml").read_text(encoding="utf-8")

        install_task = tasks.split(
            "Install Grafana datasource provisioning", 1
        )[1].split("- name:", 1)[0]
        self.assertIn("no_log: true", install_task)

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

    def test_dashboards_are_installed_only_when_blocky_is_enabled_and_cleaned_up_otherwise(
        self,
    ) -> None:
        tasks = (ROLE_ROOT / "tasks/main.yml").read_text(encoding="utf-8")

        install_task = tasks.split("Install Grafana dashboards", 1)[1].split(
            "- name:", 1
        )[0]
        self.assertIn("blocky.json", install_task)
        self.assertIn("blocky-postgres.json", install_task)
        self.assertIn("monitoring_blocky_enabled", install_task)

        self.assertIn(
            "Remove Blocky Grafana dashboards when Blocky is disabled", tasks
        )
        cleanup_task = tasks.split(
            "Remove Blocky Grafana dashboards when Blocky is disabled", 1
        )[1]
        self.assertIn("state: absent", cleanup_task)

    def test_pre_rename_datasource_file_is_actively_removed(self) -> None:
        # Renaming grafana_datasources.yml.j2's destination away from
        # prometheus.yml (to make room for the Blocky datasource) doesn't
        # itself clean up the old file on an already-deployed homeserver
        # -- a dedicated removal task does.
        tasks = (ROLE_ROOT / "tasks/main.yml").read_text(encoding="utf-8")

        self.assertIn(
            "Remove pre-rename Grafana datasource provisioning file", tasks
        )
        cleanup_task = tasks.split(
            "Remove pre-rename Grafana datasource provisioning file", 1
        )[1]
        self.assertIn(
            "datasources/prometheus.yml", cleanup_task.split("- name:", 1)[0]
        )
        self.assertIn("state: absent", cleanup_task.split("- name:", 1)[0])


class GrafanaIngressTests(unittest.TestCase):
    def test_grafana_route_requires_its_own_basic_auth_and_resource_limits(
        self,
    ) -> None:
        defaults = (SHARED_INGRESS_ROOT / "defaults/main.yml").read_text(
            encoding="utf-8"
        )
        self.assertIn("shared_ingress_grafana_domain: grafana.jkandler.de", defaults)
        self.assertIn("shared_ingress_grafana_upstream: http://127.0.0.1:3000", defaults)

        dynamic = (SHARED_INGRESS_ROOT / "templates/dynamic.yml.j2").read_text(
            encoding="utf-8"
        )
        self.assertIn("shared_ingress_grafana_domain", dynamic)
        self.assertIn("/etc/traefik/users", dynamic)

        # grafana-rate-limit/-request-limit/-security-headers are generated
        # by a loop over shared_ingress_rate_limited_routes (shared with
        # deluge/home), not literal source text -- render it to confirm
        # they actually come out the other side for this route.
        # The domain/upstream/rate values above already match this role's
        # own defaults -- only the enabled flag needs overriding to render
        # the route at all.
        data = yaml.safe_load(
            render_shared_ingress(shared_ingress_grafana_enabled=True)
        )

        self.assertIn("grafana-rate-limit", data["http"]["middlewares"])
        self.assertIn("grafana-request-limit", data["http"]["middlewares"])
        self.assertIn("grafana-security-headers", data["http"]["middlewares"])

    def test_grafana_route_reuses_the_shared_auth_credential(self) -> None:
        # Grafana deliberately shares one Basic Auth credential/usersFile
        # with the agent and Deluge routes (fewer passwords to manage),
        # rather than getting its own -- see shared_ingress_auth_username
        # in defaults/main.yml for the accepted blast-radius tradeoff.
        # Only the enabled flag needs overriding to render the route --
        # everything else this template needs is already correct in
        # shared_ingress's own defaults.
        data = yaml.safe_load(
            render_shared_ingress(shared_ingress_grafana_enabled=True)
        )

        grafana_chain_middlewares = data["http"]["middlewares"]["grafana-chain"][
            "chain"
        ]["middlewares"]
        self.assertIn("shared-auth", grafana_chain_middlewares)

    def test_grafana_route_renders_independently_of_other_feature_flags(
        self,
    ) -> None:
        env = Environment(
            loader=FileSystemLoader(str(SHARED_INGRESS_ROOT / "templates"))
        )
        env.filters["bool"] = bool
        template = env.get_template("dynamic.yml.j2")

        rendered = template.render(
            shared_ingress_nextcloud_domain="nextcloud.jkandler.de",
            shared_ingress_nextcloud_upstream="http://127.0.0.1:11000",
            shared_ingress_agent_enabled=False,
            shared_ingress_open_webui_enabled=False,
            shared_ingress_deluge_enabled=False,
            shared_ingress_grafana_enabled=True,
            shared_ingress_grafana_domain="grafana.jkandler.de",
            shared_ingress_grafana_upstream="http://127.0.0.1:3000",
            shared_ingress_grafana_rate_average=120,
            shared_ingress_grafana_rate_period="1m",
            shared_ingress_grafana_rate_burst=240,
            shared_ingress_grafana_max_request_body_bytes=1048576,
        )
        data = yaml.safe_load(rendered)

        self.assertIn("grafana", data["http"]["routers"])
        self.assertIn("grafana", data["http"]["services"])
        self.assertIn("grafana-chain", data["http"]["middlewares"])
        self.assertNotIn("home-agent", data["http"]["services"])
        self.assertNotIn("deluge", data["http"]["services"])

    def test_initial_grafana_publication_requires_confirmation(self) -> None:
        tasks = (SHARED_INGRESS_ROOT / "tasks/main.yml").read_text(encoding="utf-8")
        publish_playbook = (
            PROJECT_ROOT / "ansible/playbooks/publish-grafana.yml"
        ).read_text(encoding="utf-8")

        self.assertIn("PUBLISH_GRAFANA", tasks)
        self.assertIn("grafana_publish_confirmation", publish_playbook)

    def test_rollback_playbook_requires_confirmation(self) -> None:
        rollback_playbook = (
            PROJECT_ROOT / "ansible/playbooks/rollback-grafana.yml"
        ).read_text(encoding="utf-8")

        self.assertIn("ROLL_BACK_GRAFANA", rollback_playbook)
        self.assertIn("shared_ingress_grafana_enabled: false", rollback_playbook)


class NtfyRelayServiceTests(unittest.TestCase):
    def test_defaults_are_loopback_only_and_do_not_reuse_grafanas_secrets_dir(self) -> None:
        defaults = (ROLE_ROOT / "defaults/main.yml").read_text(encoding="utf-8")

        self.assertIn("monitoring_ntfy_relay_port: 9096", defaults)
        self.assertIn(
            "monitoring_ntfy_relay_service_user: monitoring-ntfy-relay", defaults
        )

    def test_relay_gets_its_own_directory_not_grafanas_shared_secrets_dir(self) -> None:
        # The shared secrets/ dir is owned by Grafana's service account
        # (0750) -- a different user can't read a file dropped in there.
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

    def test_relay_is_installed_before_alertmanager_and_health_checked(self) -> None:
        tasks = (ROLE_ROOT / "tasks/main.yml").read_text(encoding="utf-8")

        relay_index = tasks.index("Install ntfy relay script")
        alertmanager_index = tasks.index("Ensure Alertmanager container is running")
        self.assertLess(relay_index, alertmanager_index)
        self.assertIn("Verify ntfy relay is ready on host loopback", tasks)
        self.assertIn("/healthz", tasks)

    def test_alertmanager_config_points_at_the_relay_not_ntfy_sh_directly(self) -> None:
        rendered = render(
            "alertmanager.yml.j2",
            monitoring_ntfy_topic="a" * 25,
            monitoring_ses_smtp_username="AKIAEXAMPLE",
            monitoring_ses_smtp_password="examplepassword",
        )

        parsed = yaml.safe_load(rendered)
        webhook_url = parsed["receivers"][0]["webhook_configs"][0]["url"]
        self.assertNotIn("ntfy.sh", webhook_url)
        self.assertEqual(webhook_url, "http://127.0.0.1:9096/webhook")


if __name__ == "__main__":
    unittest.main()
