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

    def test_web_ui_password_is_required_not_left_default(self) -> None:
        # Deluge has no "no login required" mode -- deluge/ui/web/auth.py's
        # check_password() returns False for every password when pwd_sha1
        # is missing, which locks out login rather than bypassing it. So a
        # real password must be set and validated, the same way
        # home_agent_openai_api_key is.
        defaults = (ROLE_ROOT / "defaults/main.yml").read_text(encoding="utf-8")
        tasks = (ROLE_ROOT / "tasks/main.yml").read_text(encoding="utf-8")

        self.assertIn('deluge_web_password: ""', defaults)
        self.assertIn("deluge_web_password | trim | length >= 16", tasks)
        self.assertIn('deluge_web_password | trim != "CHANGE_ME"', tasks)
        self.assertNotIn("Disable Deluge's own login", tasks)

    def test_web_conf_password_hash_matches_deluges_own_algorithm(self) -> None:
        # deluge/ui/web/auth.py's Auth._change_password():
        #   salt = sha1(random); s = sha1(salt); s.update(password)
        # sha1.update(a); sha1.update(b) == sha1(a + b) for the same digest,
        # so (salt ~ password) | hash('sha1') must reproduce that exactly.
        tasks = (ROLE_ROOT / "tasks/main.yml").read_text(encoding="utf-8")

        self.assertIn(
            "deluge_web_pwd_sha1: \"{{ (deluge_web_pwd_salt ~ deluge_web_password)"
            " | hash('sha1') }}\"",
            tasks,
        )

    def test_web_conf_template_renders_as_two_valid_json_objects(self) -> None:
        # Deluge's config loader (deluge/config.py) requires exactly this
        # shape: a {"file": ..., "format": ...} header immediately followed
        # by the actual config object, found via brace-matching (not a
        # naive split), each parsed independently as JSON.
        #
        # "file": 2 matters, not just cosmetically: deluge/ui/web/server.py
        # constructs its ConfigManager with file_version=2. A first version
        # of this template said "file": 1, one version behind -- Deluge
        # accepted the file but ran its 1-to-2 migration path, which
        # (confirmed live, not just in theory) resulted in the well-known
        # default pwd_sha1/salt taking effect instead of the configured
        # password. Matching the current version exactly avoids that
        # migration path running at all.
        import json

        from jinja2 import Environment, FileSystemLoader

        env = Environment(
            loader=FileSystemLoader(str(ROLE_ROOT / "templates"))
        )
        rendered = env.get_template("web.conf.j2").render(
            deluge_web_pwd_salt="a" * 40,
            deluge_web_pwd_sha1="b" * 40,
        )

        split_at = rendered.index("}{") + 1
        header = json.loads(rendered[:split_at])
        body = json.loads(rendered[split_at:])

        self.assertEqual(header, {"file": 2, "format": 1})
        self.assertEqual(body["pwd_salt"], "a" * 40)
        self.assertEqual(body["pwd_sha1"], "b" * 40)
        self.assertEqual(body["port"], 8112)
        self.assertEqual(body["sessions"], {})
        self.assertIs(body["first_login"], False)

    def test_web_conf_template_matches_deluges_current_config_defaults(
        self,
    ) -> None:
        # deluge/ui/web/server.py's CONFIG_DEFAULTS, as of the pinned image
        # version -- every key it declares must be present in our seeded
        # file too, or Deluge silently falls back to defaults for whatever
        # is missing (harmless for most keys, but exactly how the
        # file-version mismatch above went unnoticed for pwd_sha1/salt).
        from jinja2 import Environment, FileSystemLoader

        env = Environment(
            loader=FileSystemLoader(str(ROLE_ROOT / "templates"))
        )
        rendered = env.get_template("web.conf.j2").render(
            deluge_web_pwd_salt="a" * 40,
            deluge_web_pwd_sha1="b" * 40,
        )

        expected_keys = {
            "enabled_plugins",
            "default_daemon",
            "pwd_salt",
            "pwd_sha1",
            "session_timeout",
            "sessions",
            "sidebar_show_zero",
            "sidebar_multiple_filters",
            "show_session_speed",
            "show_sidebar",
            "theme",
            "first_login",
            "language",
            "base",
            "interface",
            "port",
            "https",
            "pkey",
            "cert",
        }
        for key in expected_keys:
            with self.subTest(key=key):
                self.assertIn(f'"{key}"', rendered)

    def test_repairs_a_web_conf_left_with_the_default_password(self) -> None:
        # Must re-detect and fix the specific known-bad state (file exists
        # but still has the well-known default pwd_sha1) by content, not
        # just skip because a file is now present.
        tasks = (ROLE_ROOT / "tasks/main.yml").read_text(encoding="utf-8")

        self.assertIn("2ce1a410bcdcc53064129b6d950f2e9fee4edc1e", tasks)
        self.assertIn("deluge_web_conf_slurp.content | b64decode", tasks)

    def test_stops_the_old_container_before_rewriting_web_conf(self) -> None:
        # A still-running deluge-web process holds its own in-memory copy of
        # web.conf and periodically autosaves it. If that process is still
        # alive while the password-repair template task writes a fresh
        # web.conf, its next autosave silently overwrites the fix with its
        # own stale (default-password) state before the container is ever
        # recreated -- confirmed live via web.conf.bak's timestamp and
        # content sitting *before* the file that clobbered it, both before
        # the recreated container's own start time. The old container must
        # be stopped before the file is rewritten, not just recreated after.
        tasks = (ROLE_ROOT / "tasks/main.yml").read_text(encoding="utf-8")

        stop_index = tasks.index("Stop Deluge before rewriting its web.conf")
        install_index = tasks.index("Install Deluge's web.conf with the configured password")
        self.assertLess(
            stop_index,
            install_index,
            "the container must be stopped before web.conf is rewritten,"
            " or the old process can autosave over the fix",
        )
        self.assertIn("community.docker.docker_container_info", tasks)
        self.assertIn("state: stopped", tasks)


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

    def test_deluge_rate_limit_tolerates_its_polling_web_ui(self) -> None:
        # Deluge's web UI is a heavy ExtJS SPA that continuously polls
        # /json for live torrent/status updates (~every 2 seconds) once
        # open, on top of firing 14+ static asset requests on a cold load
        # -- confirmed live with "Too Many Requests" against the agent-style
        # defaults (average=10/min, burst=5) this route originally
        # inherited. Needs open_webui-style headroom, not the agent's
        # lightweight-API values.
        defaults = (SHARED_INGRESS_ROOT / "defaults/main.yml").read_text(
            encoding="utf-8"
        )

        self.assertIn("shared_ingress_deluge_rate_average: 120", defaults)
        self.assertIn("shared_ingress_deluge_rate_burst: 240", defaults)

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
