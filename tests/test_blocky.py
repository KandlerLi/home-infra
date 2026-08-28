from __future__ import annotations

import unittest
from pathlib import Path

import yaml
from jinja2 import Environment, FileSystemLoader

PROJECT_ROOT = Path(__file__).resolve().parents[1]
ROLE_ROOT = PROJECT_ROOT / "ansible/roles/blocky"


def render_blocky_config(**overrides: object) -> str:
    env = Environment(loader=FileSystemLoader(str(ROLE_ROOT / "templates")))
    defaults = yaml.safe_load((ROLE_ROOT / "defaults/main.yml").read_text())
    ctx = {**defaults, "blocky_bind_address": "192.168.178.100", **overrides}
    return env.get_template("config.yml.j2").render(**ctx)


class BlockyRoleTests(unittest.TestCase):
    def test_defaults_are_opt_in_and_image_is_pinned(self) -> None:
        defaults = (ROLE_ROOT / "defaults/main.yml").read_text(encoding="utf-8")

        self.assertIn("blocky_enabled: false", defaults)
        self.assertIn("ghcr.io/0xerr0r/blocky", defaults)
        self.assertIn("postgres", defaults)
        self.assertNotIn(":latest", defaults)

    def test_dns_binds_the_real_lan_ip_not_loopback(self) -> None:
        # The whole point is network-wide reachability -- unlike every
        # other opt-in service in this repo, which defaults to loopback
        # until shared_ingress explicitly publishes it, Blocky's DNS
        # port has to be reachable directly from day one.
        defaults = (ROLE_ROOT / "defaults/main.yml").read_text(encoding="utf-8")

        self.assertIn(
            'blocky_bind_address: "{{ ansible_facts.default_ipv4.address }}"',
            defaults,
        )
        self.assertNotIn("blocky_bind_address: 127.0.0.1", defaults)

    def test_fact_references_use_the_namespaced_form(self) -> None:
        # Confirmed live (2026-08-28, a real production failure): this
        # repo's ansible.cfg sets inject_facts_as_vars = False, so the
        # legacy bare ansible_default_ipv4 (and any other bare
        # ansible_<fact> variable) is never defined -- only
        # ansible_facts.<fact> is. My own first cut of this role used
        # the bare form and it failed live; a throwaway verification
        # playbook that manually stubbed ansible_default_ipv4 as a
        # plain var masked the bug instead of catching it, since it
        # bypassed real fact-gathering entirely.
        project_root = PROJECT_ROOT
        ansible_cfg = (project_root / "ansible.cfg").read_text(encoding="utf-8")
        self.assertIn("inject_facts_as_vars = False", ansible_cfg)

        for path in [ROLE_ROOT / "defaults/main.yml", ROLE_ROOT / "tasks/main.yml"]:
            with self.subTest(path=path.name):
                text = path.read_text(encoding="utf-8")
                self.assertNotIn("ansible_default_ipv4", text)
                self.assertNotIn("{{ ansible_hostname", text)
                self.assertNotIn("{{ ansible_distribution", text)

    def test_http_api_and_metrics_stay_loopback_only(self) -> None:
        rendered = yaml.safe_load(render_blocky_config())

        self.assertEqual(rendered["ports"]["http"], "127.0.0.1:4000")
        self.assertEqual(rendered["ports"]["dns"], "192.168.178.100:53")

    def test_upstreams_and_bootstrap_dns_are_static_ips_not_hostnames(self) -> None:
        # Avoids a chicken-and-egg problem once Blocky becomes the
        # network's only resolver: it must never need DNS to resolve its
        # own upstreams.
        defaults = (ROLE_ROOT / "defaults/main.yml").read_text(encoding="utf-8")

        for value in ("1.1.1.1", "9.9.9.9"):
            with self.subTest(value=value):
                self.assertIn(value, defaults)
        self.assertNotIn("https://", defaults.split("blocky_denylists:")[0])

    def test_blocky_container_drops_all_capabilities_but_bind_service(self) -> None:
        tasks = (ROLE_ROOT / "tasks/main.yml").read_text(encoding="utf-8")

        blocky_container_task = tasks.split(
            "Ensure Blocky container is running", 1
        )[1]
        self.assertIn("cap_drop:", blocky_container_task)
        self.assertIn("- ALL", blocky_container_task)
        self.assertIn("capabilities:", blocky_container_task)
        self.assertIn("- NET_BIND_SERVICE", blocky_container_task)
        self.assertIn("read_only: true", blocky_container_task)

    def test_postgres_is_loopback_only_via_listen_addresses(self) -> None:
        # Postgres shares Blocky's host network namespace (network_mode:
        # host), so nothing stops it listening on every host interface by
        # default -- listen_addresses is what actually keeps it
        # loopback-only, not network isolation.
        tasks = (ROLE_ROOT / "tasks/main.yml").read_text(encoding="utf-8")

        self.assertIn("listen_addresses=127.0.0.1", tasks)

    def test_postgres_does_not_drop_capabilities(self) -> None:
        # Deliberate exception: the official postgres image's entrypoint
        # needs to start as root to gosu/chown into its own postgres user
        # on first run -- cap_drop: ALL would break that. Documented in
        # tasks/main.yml and the role's README.
        tasks = (ROLE_ROOT / "tasks/main.yml").read_text(encoding="utf-8")

        postgres_task = tasks.split(
            "Ensure Blocky Postgres container is running", 1
        )[1].split("- name:", 1)[0]
        self.assertNotIn("cap_drop", postgres_task)

    def test_postgres_data_lives_off_the_constrained_root_disk(self) -> None:
        defaults = (ROLE_ROOT / "defaults/main.yml").read_text(encoding="utf-8")

        self.assertIn(
            "blocky_postgres_data_dir: /mnt/black-hdd/blocky/postgres", defaults
        )

    def test_query_log_targets_postgres_with_bounded_retention(self) -> None:
        rendered = yaml.safe_load(
            render_blocky_config(
                blocky_postgres_user="blocky",
                blocky_postgres_password="a-generated-password-1234",
            )
        )

        self.assertEqual(rendered["queryLog"]["type"], "postgresql")
        self.assertIn(
            "postgres://blocky:a-generated-password-1234@127.0.0.1",
            rendered["queryLog"]["target"],
        )
        self.assertEqual(rendered["queryLog"]["logRetentionDays"], 7)

    def test_config_install_and_postgres_container_never_log_the_password(
        self,
    ) -> None:
        tasks = (ROLE_ROOT / "tasks/main.yml").read_text(encoding="utf-8")

        config_task = tasks.split("Install Blocky configuration", 1)[1].split(
            "- name:", 1
        )[0]
        postgres_task = tasks.split(
            "Ensure Blocky Postgres container is running", 1
        )[1].split("- name:", 1)[0]
        self.assertIn("no_log: true", config_task)
        self.assertIn("no_log: true", postgres_task)

    def test_validation_rejects_a_weak_or_default_postgres_password(self) -> None:
        tasks_path = ROLE_ROOT / "tasks/main.yml"
        tasks = yaml.safe_load(tasks_path.read_text())
        validation = next(
            task
            for task in tasks[0]["block"]
            if task["name"] == "Validate Blocky configuration"
        )
        expressions = validation["ansible.builtin.assert"]["that"]
        environment = Environment()

        for expression in expressions:
            with self.subTest(expression=expression):
                environment.compile_expression(str(expression))

        self.assertIn(
            "blocky_postgres_password | trim | length >= 16", expressions
        )
        self.assertIn(
            'blocky_postgres_password | trim != "CHANGE_ME"', expressions
        )

    def test_role_is_registered_in_site_yml(self) -> None:
        site_yml = (
            PROJECT_ROOT / "ansible/playbooks/site.yml"
        ).read_text(encoding="utf-8")

        self.assertIn("- blocky", site_yml)


if __name__ == "__main__":
    unittest.main()
