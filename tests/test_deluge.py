from __future__ import annotations

import unittest
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
ROLE_ROOT = PROJECT_ROOT / "ansible/roles/deluge"
SHARED_INGRESS_ROOT = PROJECT_ROOT / "ansible/roles/shared_ingress"
NEXTCLOUD_AIO_ROOT = PROJECT_ROOT / "ansible/roles/nextcloud_aio"


class DelugeRoleTests(unittest.TestCase):
    def test_defaults_are_opt_in_and_web_ui_is_loopback_only(self) -> None:
        defaults = (ROLE_ROOT / "defaults/main.yml").read_text(encoding="utf-8")

        self.assertIn("deluge_enabled: false", defaults)
        self.assertIn("deluge_web_bind_address: 127.0.0.1", defaults)
        self.assertIn("linuxserver/deluge:", defaults)

    def test_image_is_pinned_and_asserted(self) -> None:
        defaults = (ROLE_ROOT / "defaults/main.yml").read_text(encoding="utf-8")
        tasks = (ROLE_ROOT / "tasks/main.yml").read_text(encoding="utf-8")

        self.assertNotIn(":latest", defaults)
        self.assertIn("deluge_image is match", tasks)

    def test_only_the_peer_port_binds_beyond_loopback(self) -> None:
        # The web UI must stay loopback-only (reached only via Traefik). The
        # BitTorrent peer port is a deliberate, narrow exception: it's a raw
        # TCP/UDP protocol port, not an HTTP attack surface, and peer
        # connectivity fundamentally requires a directly reachable port --
        # it cannot be proxied through Traefik like the web UI can.
        tasks = (ROLE_ROOT / "tasks/main.yml").read_text(encoding="utf-8")

        self.assertIn(
            '"{{ deluge_web_bind_address }}:{{ deluge_web_port }}:8112"', tasks
        )
        self.assertIn('"{{ deluge_peer_port }}:{{ deluge_peer_port }}/tcp"', tasks)
        self.assertIn('"{{ deluge_peer_port }}:{{ deluge_peer_port }}/udp"', tasks)

    def test_service_runs_as_a_dedicated_unprivileged_account(self) -> None:
        tasks = (ROLE_ROOT / "tasks/main.yml").read_text(encoding="utf-8")

        self.assertIn("Create Deluge service account", tasks)
        self.assertIn("no-new-privileges:true", tasks)

    def test_downloads_and_config_directories_are_kept_separate(self) -> None:
        tasks = (ROLE_ROOT / "tasks/main.yml").read_text(encoding="utf-8")

        self.assertIn("deluge_downloads_dir != deluge_config_dir", tasks)


class DelugeIngressTests(unittest.TestCase):
    def test_deluge_route_requires_its_own_basic_auth_and_resource_limits(
        self,
    ) -> None:
        dynamic = (SHARED_INGRESS_ROOT / "templates/dynamic.yml.j2").read_text(
            encoding="utf-8"
        )

        defaults = (SHARED_INGRESS_ROOT / "defaults/main.yml").read_text(
            encoding="utf-8"
        )
        self.assertIn("shared_ingress_deluge_domain: torrent.jkandler.de", defaults)
        self.assertIn("shared_ingress_deluge_domain", dynamic)
        self.assertIn("deluge-auth", dynamic)
        self.assertIn("/etc/traefik/deluge-users", dynamic)
        self.assertIn("deluge-rate-limit", dynamic)
        self.assertIn("deluge-request-limit", dynamic)
        self.assertIn("deluge-security-headers", dynamic)

    def test_deluge_credential_is_independent_of_the_agent_credential(
        self,
    ) -> None:
        dynamic = (SHARED_INGRESS_ROOT / "templates/dynamic.yml.j2").read_text(
            encoding="utf-8"
        )

        # Deluge's usersFile must differ from the agent's, so the two
        # credentials have independent blast radius (one leaking doesn't
        # grant access to the other service).
        deluge_auth_block = dynamic.split("deluge-auth:", 1)[1].split(
            "deluge-rate-limit:", 1
        )[0]
        self.assertIn("deluge-users", deluge_auth_block)
        self.assertNotIn("usersFile: /etc/traefik/users\n", deluge_auth_block)

    def test_deluge_route_renders_independently_of_the_agent_feature_flag(
        self,
    ) -> None:
        # Deluge has its own subdomain and its own enable flag; it must not
        # be nested inside shared_ingress_agent_enabled's conditional block
        # the way open-webui is (they share ai.jkandler.de by design), so it
        # has to render correctly with agent/open-webui both disabled.
        import yaml
        from jinja2 import Environment, FileSystemLoader

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
            shared_ingress_deluge_enabled=True,
            shared_ingress_deluge_domain="torrent.jkandler.de",
            shared_ingress_deluge_upstream="http://127.0.0.1:8112",
            shared_ingress_deluge_rate_average=10,
            shared_ingress_deluge_rate_period="1m",
            shared_ingress_deluge_rate_burst=5,
            shared_ingress_deluge_max_request_body_bytes=1048576,
        )
        data = yaml.safe_load(rendered)

        self.assertIn("deluge", data["http"]["routers"])
        self.assertIn("deluge", data["http"]["services"])
        self.assertIn("deluge-chain", data["http"]["middlewares"])
        self.assertNotIn("home-agent", data["http"]["services"])
        self.assertNotIn("open-webui", data["http"]["services"])

    def test_initial_deluge_publication_requires_confirmation(self) -> None:
        tasks = (SHARED_INGRESS_ROOT / "tasks/main.yml").read_text(encoding="utf-8")
        publish_playbook = (
            PROJECT_ROOT / "ansible/playbooks/publish-deluge.yml"
        ).read_text(encoding="utf-8")

        self.assertIn("PUBLISH_DELUGE", tasks)
        self.assertIn("deluge_publish_confirmation", publish_playbook)

    def test_rollback_playbook_requires_confirmation(self) -> None:
        rollback_playbook = (
            PROJECT_ROOT / "ansible/playbooks/rollback-deluge.yml"
        ).read_text(encoding="utf-8")

        self.assertIn("ROLL_BACK_DELUGE", rollback_playbook)
        self.assertIn("shared_ingress_deluge_enabled: false", rollback_playbook)


class NextcloudAioMountTests(unittest.TestCase):
    def test_mount_is_disabled_by_default(self) -> None:
        defaults = (NEXTCLOUD_AIO_ROOT / "defaults/main.yml").read_text(
            encoding="utf-8"
        )

        self.assertIn('nextcloud_aio_mount_dir: ""', defaults)
        self.assertIn('nextcloud_aio_mount_applicable_user: ""', defaults)

    def test_mount_env_var_and_bind_mount_are_conditional(self) -> None:
        tasks = (NEXTCLOUD_AIO_ROOT / "tasks/main.yml").read_text(encoding="utf-8")

        self.assertIn("NEXTCLOUD_MOUNT", tasks)
        self.assertIn("nextcloud_aio_mount_dir | length > 0", tasks)
        self.assertIn("volumes: strict", tasks)

    def test_external_storage_visibility_requires_an_explicit_user(self) -> None:
        tasks = (NEXTCLOUD_AIO_ROOT / "tasks/main.yml").read_text(encoding="utf-8")

        self.assertIn("files_external:create", tasks)
        self.assertIn("files_external:applicable", tasks)
        self.assertIn("nextcloud_aio_mount_applicable_user | length > 0", tasks)


if __name__ == "__main__":
    unittest.main()
