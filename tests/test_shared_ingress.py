from __future__ import annotations

import unittest
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
ROLE_ROOT = PROJECT_ROOT / "ansible/roles/shared_ingress"


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

    def test_only_one_authentication_file_is_installed(self) -> None:
        tasks = (ROLE_ROOT / "tasks/main.yml").read_text(encoding="utf-8")

        self.assertIn("Install shared ingress authentication file", tasks)
        self.assertNotIn("Install Deluge authentication file", tasks)
        self.assertNotIn("Install Grafana authentication file", tasks)
        self.assertEqual(tasks.count("dest: \"{{ shared_ingress_config_dir }}/"), 3)


if __name__ == "__main__":
    unittest.main()
