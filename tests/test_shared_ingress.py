from __future__ import annotations

import unittest
from pathlib import Path

import yaml
from jinja2 import Environment, FileSystemLoader

PROJECT_ROOT = Path(__file__).resolve().parents[1]
ROLE_ROOT = PROJECT_ROOT / "ansible/roles/shared_ingress"


def render_shared_ingress(**overrides: object) -> str:
    env = Environment(loader=FileSystemLoader(str(ROLE_ROOT / "templates")))
    env.filters["bool"] = bool
    defaults = yaml.safe_load((ROLE_ROOT / "defaults/main.yml").read_text())
    ctx = {**defaults, **overrides}
    return env.get_template("dynamic.yml.j2").render(**ctx)


class SharedIngressTests(unittest.TestCase):
    def test_defaults_are_opt_in_and_upstreams_are_loopback_only(self) -> None:
        defaults = (ROLE_ROOT / "defaults/main.yml").read_text(encoding="utf-8")

        self.assertIn("shared_ingress_enabled: false", defaults)
        self.assertIn("shared_ingress_start: false", defaults)
        self.assertIn("traefik:v3.7.1", defaults)
        self.assertIn("http://127.0.0.1:11000", defaults)
        self.assertIn("http://127.0.0.1:8090", defaults)
        self.assertIn("shared_ingress_open_webui_rate_burst: 240", defaults)

    def test_home_upstream_allows_only_loopback_or_the_k3s_cluster(
        self,
    ) -> None:
        # home.jkandler.de is cut over to the k3s learning cluster's own
        # Ingress (infra/k3s-apps) -- the one deliberate exception to
        # every other upstream staying on loopback. 192.168.101.10 is
        # the k3s VM's address on an isolated libvirt NAT network that
        # only exists as a directly-connected route on this homeserver
        # (confirmed live), so it's not actually leaving the host's
        # trust boundary. This still refuses anything else -- widening
        # the check to allow arbitrary upstreams would defeat the whole
        # point of the loopback guard.
        tasks = (ROLE_ROOT / "tasks/main.yml").read_text(encoding="utf-8")

        self.assertIn(
            'shared_ingress_home_upstream == "http://127.0.0.1:8095"\n'
            '            or shared_ingress_home_upstream == '
            '"http://192.168.101.10:80"',
            tasks,
        )

    def test_deluge_upstream_allows_only_loopback_or_the_k3s_cluster(
        self,
    ) -> None:
        # Same deliberate exception as shared_ingress_home_upstream:
        # torrent.jkandler.de is cut over to the k3s learning cluster's
        # own Ingress. Confirmed live before this cutover: the Docker
        # container stopped cleanly, the k3s copy read the existing
        # session state correctly (daemon connected, real external IP,
        # real free space through the NFS mount, no errors) with
        # nothing else contending for the same config files.
        tasks = (ROLE_ROOT / "tasks/main.yml").read_text(encoding="utf-8")

        self.assertIn(
            'shared_ingress_deluge_upstream == "http://127.0.0.1:8112"\n'
            '            or shared_ingress_deluge_upstream == '
            '"http://192.168.101.10:80"',
            tasks,
        )

    def test_nextcloud_upstream_allows_only_loopback_or_the_k3s_vm_address(
        self,
    ) -> None:
        # A different shape from home/deluge's exception: nextcloud.jkandler.de
        # itself isn't moving anywhere -- this is about nextcloud_tools
        # (which talks to AIO Apache) potentially running inside the k3s
        # VM instead of on this host, which means AIO Apache's single
        # IP_BINDING has to switch from 127.0.0.1 to this host's own
        # address on the k3s VM's isolated network (192.168.101.1) so
        # both this host and that VM can still reach it. Not yet
        # switched live -- this only widens what the guard accepts.
        tasks = (ROLE_ROOT / "tasks/main.yml").read_text(encoding="utf-8")

        self.assertIn(
            'shared_ingress_nextcloud_upstream == "http://127.0.0.1:11000"\n'
            '            or shared_ingress_nextcloud_upstream == '
            '"http://192.168.101.1:11000"',
            tasks,
        )

    def test_agent_upstream_allows_only_loopback_or_the_k3s_cluster(
        self,
    ) -> None:
        # Same deliberate exception as shared_ingress_home_upstream/
        # shared_ingress_deluge_upstream: ai.jkandler.de's home_agent +
        # open_webui pairing is cut over to the k3s cluster's own
        # Ingress. Confirmed live before this cutover: the Docker
        # open-webui container stopped cleanly, the k3s copy read the
        # existing accounts/chat history correctly (persisted database
        # settings came back, not fresh env-var defaults) over the new
        # NFS export.
        tasks = (ROLE_ROOT / "tasks/main.yml").read_text(encoding="utf-8")

        self.assertIn(
            'shared_ingress_agent_upstream == "http://127.0.0.1:8090"\n'
            '            or shared_ingress_agent_upstream == '
            '"http://192.168.101.10:80"',
            tasks,
        )

    def test_open_webui_upstream_allows_only_loopback_or_the_k3s_cluster(
        self,
    ) -> None:
        # Moves in lockstep with shared_ingress_agent_upstream above --
        # both branches of this file's own /healthz + /v1/chat vs.
        # everything-else path split have to point at the same k3s
        # address once either one does, since the k3s Ingress (infra/
        # k3s-apps) does the equivalent split on its own side.
        tasks = (ROLE_ROOT / "tasks/main.yml").read_text(encoding="utf-8")

        self.assertIn(
            'shared_ingress_open_webui_upstream == "http://127.0.0.1:8091"\n'
            '            or shared_ingress_open_webui_upstream == '
            '"http://192.168.101.10:80"',
            tasks,
        )

    def test_deluge_health_check_sends_the_real_host_header(self) -> None:
        # Same fix as the landing page's own health check: once
        # shared_ingress_deluge_upstream points at the k3s cluster's
        # Ingress, a bare request by IP matches no routing rule and
        # 404s even though the backend is healthy -- Traefik there
        # routes purely on Host.
        tasks = (ROLE_ROOT / "tasks/main.yml").read_text(encoding="utf-8")

        verify_task = tasks.split(
            "Verify Deluge before publishing it", 1
        )[1].split("- name:", 1)[0]
        self.assertIn(
            'Host: "{{ shared_ingress_deluge_domain }}"', verify_task
        )

    def test_proxy_has_no_docker_socket_and_keeps_hardening(self) -> None:
        tasks = (ROLE_ROOT / "tasks/main.yml").read_text(encoding="utf-8")

        self.assertNotIn("/var/run/docker.sock", tasks)
        self.assertIn("network_mode: host", tasks)
        self.assertIn("read_only: true", tasks)
        self.assertIn("no-new-privileges:true", tasks)
        self.assertIn("NET_BIND_SERVICE", tasks)
        self.assertIn("- ALL", tasks)
        self.assertIn("capabilities:", tasks)
        self.assertNotIn("cap_add:", tasks)

    def test_agent_route_requires_auth_and_resource_limits(self) -> None:
        dynamic = (ROLE_ROOT / "templates/dynamic.yml.j2").read_text(
            encoding="utf-8"
        )

        self.assertIn("basicAuth:", dynamic)
        self.assertIn("rateLimit:", dynamic)
        self.assertIn("maxRequestBodyBytes:", dynamic)
        self.assertIn("agent-security-headers", dynamic)
        self.assertIn("certResolver: letsencrypt", dynamic)

    def test_open_webui_route_preserves_the_bounded_legacy_api(self) -> None:
        dynamic = (ROLE_ROOT / "templates/dynamic.yml.j2").read_text(
            encoding="utf-8"
        )

        self.assertIn("home-agent-api:", dynamic)
        self.assertIn("Path(`/healthz`)", dynamic)
        self.assertIn("Path(`/v1/chat`)", dynamic)
        self.assertIn("open-webui:", dynamic)
        self.assertIn("priority: 100", dynamic)
        self.assertIn("open-webui-chain", dynamic)
        self.assertIn("open-webui-request-limit", dynamic)
        open_webui_chain = dynamic.split("open-webui-chain:", 1)[1].split(
            "{% endif %}", 1
        )[0]
        self.assertNotIn("shared-auth", open_webui_chain)

    def test_initial_open_webui_publication_requires_confirmation(self) -> None:
        tasks = (ROLE_ROOT / "tasks/main.yml").read_text(encoding="utf-8")
        publish_playbook = (
            PROJECT_ROOT / "ansible/playbooks/publish-open-webui.yml"
        ).read_text(encoding="utf-8")

        self.assertIn("PUBLISH_OPEN_WEBUI", tasks)
        self.assertIn("open_webui_publish_confirmation", publish_playbook)

    def test_apex_redirect_is_opt_in_and_targets_www(self) -> None:
        defaults = (ROLE_ROOT / "defaults/main.yml").read_text(encoding="utf-8")
        dynamic = (ROLE_ROOT / "templates/dynamic.yml.j2").read_text(
            encoding="utf-8"
        )

        self.assertIn("shared_ingress_apex_redirect_enabled: false", defaults)
        self.assertIn("shared_ingress_apex_domain: jkandler.de", defaults)
        self.assertIn(
            "shared_ingress_apex_redirect_target_host: www.jkandler.de",
            defaults,
        )
        self.assertIn("apex-redirect:", dynamic)
        self.assertIn("redirectRegex:", dynamic)
        self.assertIn("service: noop@internal", dynamic)
        self.assertIn("permanent: true", dynamic)

    def test_cutover_has_a_human_confirmation_sentinel(self) -> None:
        nextcloud_tasks = (
            PROJECT_ROOT / "ansible/roles/nextcloud_aio/tasks/main.yml"
        ).read_text(encoding="utf-8")
        ingress_tasks = (ROLE_ROOT / "tasks/main.yml").read_text(encoding="utf-8")

        self.assertIn("MIGRATE_NEXTCLOUD_INGRESS", nextcloud_tasks)
        self.assertIn("MIGRATE_NEXTCLOUD_INGRESS", ingress_tasks)
        self.assertIn("nextcloud-aio-apache is still running", nextcloud_tasks)
        self.assertIn(
            "Prevent an implicit reverse proxy rollback", nextcloud_tasks
        )


class SharedAuthCredentialTests(unittest.TestCase):
    def test_only_one_shared_auth_middleware_is_defined(self) -> None:
        dynamic = (ROLE_ROOT / "templates/dynamic.yml.j2").read_text(
            encoding="utf-8"
        )

        self.assertEqual(dynamic.count("shared-auth:"), 1)
        self.assertNotIn("agent-auth:", dynamic)
        self.assertNotIn("deluge-auth:", dynamic)
        self.assertNotIn("grafana-auth:", dynamic)

    def test_shared_auth_credential_is_a_single_pair_of_variables(self) -> None:
        defaults = (ROLE_ROOT / "defaults/main.yml").read_text(encoding="utf-8")

        self.assertIn("shared_ingress_auth_username: julian", defaults)
        self.assertIn('shared_ingress_auth_password_hash: ""', defaults)
        self.assertNotIn("shared_ingress_agent_auth_username", defaults)
        self.assertNotIn("shared_ingress_deluge_auth_username", defaults)
        self.assertNotIn("shared_ingress_grafana_auth_username", defaults)

    def test_validation_requires_the_shared_credential_when_any_route_is_on(
        self,
    ) -> None:
        tasks = (ROLE_ROOT / "tasks/main.yml").read_text(encoding="utf-8")

        self.assertIn("shared_ingress_auth_username | length > 0", tasks)
        self.assertIn("shared_ingress_auth_password_hash | length >= 20", tasks)

    def test_landing_page_health_check_sends_the_real_host_header(
        self,
    ) -> None:
        # Found live: once shared_ingress_home_upstream points at the
        # k3s cluster's Ingress, a bare request by IP with no Host
        # header matches no routing rule and 404s even though the
        # backend is perfectly healthy -- Traefik there routes purely
        # on Host. This check needs to send the real domain explicitly,
        # not rely on whatever the backend happens to default to.
        tasks = (ROLE_ROOT / "tasks/main.yml").read_text(encoding="utf-8")

        verify_task = tasks.split(
            "Verify the landing page before publishing it", 1
        )[1].split("- name:", 1)[0]
        self.assertIn("Host: \"{{ shared_ingress_home_domain }}\"", verify_task)

    def test_only_one_authentication_file_is_installed(self) -> None:
        tasks = (ROLE_ROOT / "tasks/main.yml").read_text(encoding="utf-8")

        self.assertIn("Install shared ingress authentication file", tasks)
        self.assertNotIn("Install Deluge authentication file", tasks)
        self.assertNotIn("Install Grafana authentication file", tasks)
        self.assertEqual(tasks.count("dest: \"{{ shared_ingress_config_dir }}/"), 3)


class HomeRouteIngressTests(unittest.TestCase):
    # home.jkandler.de -- moved here from test_landing_page.py once the
    # landing_page role that used to back this route was deleted. The
    # route itself is unrelated to which role/backend serves it, and
    # stays live (now via the k3s cluster, see
    # shared_ingress_home_upstream in group_vars).

    def test_home_route_requires_the_shared_auth_credential(self) -> None:
        defaults = (ROLE_ROOT / "defaults/main.yml").read_text(
            encoding="utf-8"
        )
        self.assertIn("shared_ingress_home_domain: home.jkandler.de", defaults)

        dynamic = (ROLE_ROOT / "templates/dynamic.yml.j2").read_text(
            encoding="utf-8"
        )
        self.assertIn("shared_ingress_home_domain", dynamic)

        # home-chain is generated by a loop over
        # shared_ingress_rate_limited_routes (shared with deluge/grafana),
        # not literal source text -- render it to confirm the shared
        # credential actually ends up in this route's chain. Only the
        # enabled flag needs overriding: everything else this template
        # needs is already correct in shared_ingress's own defaults.
        data = yaml.safe_load(
            render_shared_ingress(shared_ingress_home_enabled=True)
        )
        home_chain_middlewares = data["http"]["middlewares"]["home-chain"]["chain"][
            "middlewares"
        ]
        self.assertIn("shared-auth", home_chain_middlewares)

    def test_initial_home_publication_requires_confirmation(self) -> None:
        tasks = (ROLE_ROOT / "tasks/main.yml").read_text(encoding="utf-8")
        publish_playbook = (
            PROJECT_ROOT / "ansible/playbooks/publish-home.yml"
        ).read_text(encoding="utf-8")
        rollback_playbook = (
            PROJECT_ROOT / "ansible/playbooks/rollback-home.yml"
        ).read_text(encoding="utf-8")

        self.assertIn("PUBLISH_HOME", tasks)
        self.assertIn("home_publish_confirmation", publish_playbook)
        self.assertIn("ROLL_BACK_HOME", rollback_playbook)

    def test_home_route_renders_independently_of_other_feature_flags(self) -> None:
        env = Environment(loader=FileSystemLoader(str(ROLE_ROOT / "templates")))
        env.filters["bool"] = bool
        template = env.get_template("dynamic.yml.j2")

        rendered = template.render(
            shared_ingress_nextcloud_domain="nextcloud.jkandler.de",
            shared_ingress_nextcloud_upstream="http://127.0.0.1:11000",
            shared_ingress_agent_enabled=False,
            shared_ingress_open_webui_enabled=False,
            shared_ingress_deluge_enabled=False,
            shared_ingress_grafana_enabled=False,
            shared_ingress_home_enabled=True,
            shared_ingress_home_domain="home.jkandler.de",
            shared_ingress_home_upstream="http://192.168.101.10:80",
            shared_ingress_home_rate_average=10,
            shared_ingress_home_rate_period="1m",
            shared_ingress_home_rate_burst=5,
            shared_ingress_home_max_request_body_bytes=16384,
        )
        data = yaml.safe_load(rendered)

        self.assertIn("home", data["http"]["routers"])
        self.assertIn("home", data["http"]["services"])
        self.assertIn("home-chain", data["http"]["middlewares"])
        self.assertNotIn("grafana", data["http"]["services"])
        self.assertNotIn("deluge", data["http"]["services"])


if __name__ == "__main__":
    unittest.main()
