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


if __name__ == "__main__":
    unittest.main()
