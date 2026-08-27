from __future__ import annotations

import unittest
from pathlib import Path

import yaml
from jinja2 import Environment, FileSystemLoader

PROJECT_ROOT = Path(__file__).resolve().parents[1]
ROLE_ROOT = PROJECT_ROOT / "ansible/roles/landing_page"
SHARED_INGRESS_ROOT = PROJECT_ROOT / "ansible/roles/shared_ingress"


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


class LandingPageRoleTests(unittest.TestCase):
    def test_defaults_are_opt_in_loopback_only_and_digest_pinned(self) -> None:
        defaults = (ROLE_ROOT / "defaults/main.yml").read_text(encoding="utf-8")

        self.assertIn("landing_page_enabled: false", defaults)
        self.assertIn("landing_page_bind_address: 127.0.0.1", defaults)
        self.assertIn("landing_page_port: 8095", defaults)
        self.assertIn("nginxinc/nginx-unprivileged", defaults)
        self.assertIn(
            "sha256:65e3e85dbaed8ba248841d9d58a899b6197106c23cb0ff1a132b7bfe0547e4c0",
            defaults,
        )

    def test_container_is_hardened_and_has_no_docker_socket(self) -> None:
        tasks = (ROLE_ROOT / "tasks/main.yml").read_text(encoding="utf-8")

        self.assertNotIn("/var/run/docker.sock", tasks)
        self.assertNotIn("network_mode: host", tasks)
        self.assertIn("read_only: true", tasks)
        self.assertIn("no-new-privileges:true", tasks)
        self.assertIn("cap_drop:", tasks)
        self.assertIn("- ALL", tasks)

    def test_content_is_mounted_read_only_from_a_host_directory(self) -> None:
        tasks = (ROLE_ROOT / "tasks/main.yml").read_text(encoding="utf-8")

        self.assertIn(
            "{{ landing_page_content_dir }}:/usr/share/nginx/html:ro", tasks
        )

    def test_markup_is_pure_html_css_with_no_javascript(self) -> None:
        index_html = (ROLE_ROOT / "templates/index.html.j2").read_text(
            encoding="utf-8"
        )
        style_css = (ROLE_ROOT / "files/style.css").read_text(encoding="utf-8")

        self.assertNotIn("<script", index_html)
        self.assertIn('<link rel="stylesheet" href="style.css">', index_html)
        self.assertIn("{% for link in landing_page_links %}", index_html)
        self.assertTrue(style_css.strip())

    def test_links_default_to_the_other_home_services(self) -> None:
        defaults = (ROLE_ROOT / "defaults/main.yml").read_text(encoding="utf-8")

        self.assertIn("https://nextcloud.jkandler.de/", defaults)
        self.assertIn("https://ai.jkandler.de/", defaults)
        self.assertIn("https://torrent.jkandler.de/", defaults)
        self.assertIn("https://grafana.jkandler.de/", defaults)

    def test_each_default_link_has_an_inline_svg_icon(self) -> None:
        import yaml

        defaults = yaml.safe_load(
            (ROLE_ROOT / "defaults/main.yml").read_text(encoding="utf-8")
        )

        links = defaults["landing_page_links"]
        self.assertEqual(len(links), 4)
        for link in links:
            self.assertIn("icon", link)
            self.assertIn("<svg", link["icon"])
            # Generic glyphs only, not the services' own logos/branding.
            self.assertNotIn("<image", link["icon"])

    def test_icons_are_rendered_unescaped_and_stay_self_contained(self) -> None:
        index_html = (ROLE_ROOT / "templates/index.html.j2").read_text(
            encoding="utf-8"
        )

        self.assertIn("{{ link.icon | default('') | safe }}", index_html)
        # No external icon font/CDN request -- svg markup is inlined
        # directly by the role, not fetched by the browser.
        self.assertNotIn("fonts.googleapis.com", index_html)
        self.assertNotIn("cdn.", index_html)

    def test_recreates_on_image_or_content_change(self) -> None:
        tasks = (ROLE_ROOT / "tasks/main.yml").read_text(encoding="utf-8")

        self.assertIn("landing_page_image_pull.changed", tasks)
        self.assertIn("landing_page_index_file.changed", tasks)
        self.assertIn("landing_page_style_file.changed", tasks)

    def test_role_is_registered_in_site_yml_before_shared_ingress(self) -> None:
        # Before shared_ingress: its "verify before publishing" health
        # check needs the loopback container already running.
        site_yml = (
            PROJECT_ROOT / "ansible/playbooks/site.yml"
        ).read_text(encoding="utf-8")

        landing_page_index = site_yml.index("- landing_page")
        shared_ingress_index = site_yml.index("- shared_ingress")
        self.assertLess(landing_page_index, shared_ingress_index)


class LandingPageIngressTests(unittest.TestCase):
    def test_home_route_requires_the_shared_auth_credential(self) -> None:
        defaults = (SHARED_INGRESS_ROOT / "defaults/main.yml").read_text(
            encoding="utf-8"
        )
        self.assertIn("shared_ingress_home_domain: home.jkandler.de", defaults)
        self.assertIn(
            "shared_ingress_home_upstream: http://127.0.0.1:8095", defaults
        )

        dynamic = (SHARED_INGRESS_ROOT / "templates/dynamic.yml.j2").read_text(
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
        tasks = (SHARED_INGRESS_ROOT / "tasks/main.yml").read_text(
            encoding="utf-8"
        )
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
            shared_ingress_grafana_enabled=False,
            shared_ingress_home_enabled=True,
            shared_ingress_home_domain="home.jkandler.de",
            shared_ingress_home_upstream="http://127.0.0.1:8095",
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
