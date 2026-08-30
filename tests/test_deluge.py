from __future__ import annotations

import unittest
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
ROLE_ROOT = PROJECT_ROOT / "ansible/roles/deluge"
SHARED_INGRESS_ROOT = PROJECT_ROOT / "ansible/roles/shared_ingress"
NEXTCLOUD_AIO_ROOT = PROJECT_ROOT / "ansible/roles/nextcloud_aio"


class DelugeRoleTests(unittest.TestCase):
    def test_defaults_define_the_service_account_and_directories(self) -> None:
        defaults = (ROLE_ROOT / "defaults/main.yml").read_text(encoding="utf-8")

        self.assertIn("deluge_service_user: deluge", defaults)
        self.assertIn("deluge_service_group: deluge", defaults)
        self.assertIn("deluge_downloads_dir: /mnt/black-hdd/downloads", defaults)
        self.assertIn("deluge_config_dir: /mnt/black-hdd/deluge-config", defaults)

    def test_tasks_run_unconditionally_now_container_is_retired(self) -> None:
        # This role used to gate everything behind deluge_enabled (opt-in
        # Docker container). Deluge itself now runs as a k3s-native copy
        # (infra/k3s-apps) -- this role only keeps the host prerequisites
        # (service account, directories, ACL) that copy still depends on,
        # so those need to always run, not be conditional on a flag that
        # no longer exists.
        defaults = (ROLE_ROOT / "defaults/main.yml").read_text(encoding="utf-8")
        tasks = (ROLE_ROOT / "tasks/main.yml").read_text(encoding="utf-8")

        self.assertNotIn("deluge_enabled", defaults)
        self.assertNotIn("deluge_enabled", tasks)
        self.assertNotIn("linuxserver/deluge", tasks)
        self.assertNotIn("docker_container", tasks)

    def test_service_account_uid_gid_is_validated_against_k3s_apps(self) -> None:
        # infra/k3s-apps' Deployment hardcodes PUID=993/PGID=986 rather
        # than looking them up dynamically -- a homeserver rebuild that
        # ever assigned this account a different uid/gid would otherwise
        # let the k3s copy silently write into these directories as the
        # wrong uid instead of failing loudly.
        tasks = (ROLE_ROOT / "tasks/main.yml").read_text(encoding="utf-8")

        self.assertIn("Create Deluge service account", tasks)
        self.assertIn('deluge_uid == "993"', tasks)
        self.assertIn('deluge_gid == "986"', tasks)

    def test_downloads_and_config_directories_are_kept_separate(self) -> None:
        tasks = (ROLE_ROOT / "tasks/main.yml").read_text(encoding="utf-8")

        self.assertIn("deluge_downloads_dir != deluge_config_dir", tasks)

    def test_downloads_directory_is_world_readable_but_config_is_not(self) -> None:
        # Confirmed live: Nextcloud's external storage mount reads this
        # path as its own container's runtime uid (www-data, not a member
        # of the deluge group), so with the downloads directory at 0750
        # ("other" gets no permissions at all) it got EACCES and showed an
        # empty folder even though Deluge had already written real files
        # into 0755 per-torrent subfolders underneath -- the top-level
        # directory itself was the only thing blocking visibility.
        # deluge_config_dir holds session state/password hashes and must
        # stay private, so only downloads gets the wider mode.
        tasks = (ROLE_ROOT / "tasks/main.yml").read_text(encoding="utf-8")

        downloads_index = tasks.index("path: \"{{ deluge_downloads_dir }}\"")
        config_index = tasks.index(
            "path: \"{{ deluge_config_dir }}\"", downloads_index
        )
        downloads_block = tasks[downloads_index:config_index]
        config_block = tasks[config_index : config_index + 200]

        self.assertIn('mode: "0755"', downloads_block)
        self.assertIn('mode: "0750"', config_block)

    def test_nextcloud_process_gets_write_access_via_acl_not_wider_mode(
        self,
    ) -> None:
        # Confirmed live: 0755's "other" bits are read+execute only, so
        # Nextcloud (running as www-data, uid 33, which also exists as a
        # real host account -- Docker shares the host uid namespace here)
        # could list and open files but got EACCES deleting/renaming them,
        # since Unix delete/rename needs write on the *containing*
        # directory. Fixed via ACL grants scoped to that one account, not
        # by widening "other" to rwx (0757), which would let any process on
        # the host write here, not just Nextcloud's.
        defaults = (ROLE_ROOT / "defaults/main.yml").read_text(encoding="utf-8")
        tasks = (ROLE_ROOT / "tasks/main.yml").read_text(encoding="utf-8")

        self.assertIn("deluge_downloads_nextcloud_user: www-data", defaults)
        self.assertNotIn('mode: "0757"', tasks)
        self.assertNotIn('mode: "0777"', tasks)

        acl_tasks = tasks.count("ansible.posix.acl:")
        self.assertEqual(
            acl_tasks,
            2,
            "expected one default-ACL task (future subfolders) and one"
            " recursive access-ACL task (existing content)",
        )
        self.assertIn("default: true", tasks)
        self.assertIn("recursive: true", tasks)
        self.assertIn(
            'entity: "{{ deluge_downloads_nextcloud_user }}"',
            tasks,
        )


class DelugeLegacyCleanupTests(unittest.TestCase):
    def test_deluge_playbook_removes_the_retired_container_and_image(
        self,
    ) -> None:
        # One-time cleanup, not standing config: the deluge role dropped
        # its container_name/image variables entirely in the reshape
        # (they're hardcoded here instead), so this asserts the exact
        # values the old role used to manage are actually being removed,
        # not silently left behind as orphaned Docker state.
        playbook = (
            PROJECT_ROOT / "ansible/playbooks/deluge.yml"
        ).read_text(encoding="utf-8")

        self.assertIn("name: deluge", playbook)
        self.assertIn(
            "linuxserver/deluge:2.2.0-ls381@sha256:"
            "33a939576f7ecfc1227db1a0cb2afce030ce983e620ec9d93c956e3700e21fe9",
            playbook,
        )
        self.assertEqual(playbook.count("state: absent"), 2)


class DelugeIngressTests(unittest.TestCase):
    def test_deluge_route_requires_its_own_basic_auth_and_resource_limits(
        self,
    ) -> None:
        defaults = (SHARED_INGRESS_ROOT / "defaults/main.yml").read_text(
            encoding="utf-8"
        )
        self.assertIn("shared_ingress_deluge_domain: torrent.jkandler.de", defaults)

        dynamic = (SHARED_INGRESS_ROOT / "templates/dynamic.yml.j2").read_text(
            encoding="utf-8"
        )
        self.assertIn("shared_ingress_deluge_domain", dynamic)
        self.assertIn("/etc/traefik/users", dynamic)

        # deluge-rate-limit/-request-limit/-security-headers are generated
        # by a loop over shared_ingress_rate_limited_routes (shared with
        # grafana/home), not literal source text -- render it to confirm
        # they actually come out the other side for this route.
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
            shared_ingress_deluge_rate_average=120,
            shared_ingress_deluge_rate_period="1m",
            shared_ingress_deluge_rate_burst=240,
            shared_ingress_deluge_max_request_body_bytes=1048576,
        )
        data = yaml.safe_load(rendered)

        self.assertIn("deluge-rate-limit", data["http"]["middlewares"])
        self.assertIn("deluge-request-limit", data["http"]["middlewares"])
        self.assertIn("deluge-security-headers", data["http"]["middlewares"])

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

    def test_deluge_route_reuses_the_shared_auth_credential(self) -> None:
        # Deluge deliberately shares one Basic Auth credential/usersFile
        # with the agent and Grafana routes (fewer passwords to manage),
        # rather than getting its own -- see shared_ingress_auth_username
        # in defaults/main.yml for the accepted blast-radius tradeoff.
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
            shared_ingress_deluge_rate_average=120,
            shared_ingress_deluge_rate_period="1m",
            shared_ingress_deluge_rate_burst=240,
            shared_ingress_deluge_max_request_body_bytes=1048576,
        )
        data = yaml.safe_load(rendered)

        deluge_chain_middlewares = data["http"]["middlewares"]["deluge-chain"]["chain"][
            "middlewares"
        ]
        self.assertIn("shared-auth", deluge_chain_middlewares)

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

    def test_downloads_mount_is_rescanned_so_delete_permission_is_current(
        self,
    ) -> None:
        # Confirmed live: fixing the host ACL alone wasn't enough --
        # Nextcloud's UI kept showing no delete option (checked from a
        # fresh, uncached browser profile) until this mount was explicitly
        # rescanned, since Nextcloud caches per-file permissions from the
        # last scan rather than checking the filesystem live.
        tasks = (NEXTCLOUD_AIO_ROOT / "tasks/main.yml").read_text(encoding="utf-8")

        applicable_index = tasks.index("Make the downloads mount visible")
        rescan_index = tasks.index("files:scan", applicable_index)
        self.assertGreater(
            rescan_index,
            applicable_index,
            "the rescan must run after the mount is registered/made visible",
        )

        rescan_block = tasks[applicable_index:]
        self.assertIn(
            "--path=/{{ nextcloud_aio_mount_applicable_user }}"
            "/files/{{ nextcloud_aio_mount_point_name }}",
            rescan_block,
        )


if __name__ == "__main__":
    unittest.main()
