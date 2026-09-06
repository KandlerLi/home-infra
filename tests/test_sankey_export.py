from __future__ import annotations

import sys
import unittest
from pathlib import Path

import yaml
from jinja2 import Environment, FileSystemLoader

PROJECT_ROOT = Path(__file__).resolve().parents[1]
ROLE_ROOT = PROJECT_ROOT / "ansible/roles/sankey_export"

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _load_module import load_module_from_path

sankey = load_module_from_path(
    "finanzfluss_export",
    "ansible/roles/sankey_export/files/finanzfluss_export.py",
)


class SankeyExportRoleTests(unittest.TestCase):
    def test_defaults_are_disabled_and_track_the_live_aio_endpoint(self) -> None:
        # Confirmed live 2026-09-06: a hardcoded "127.0.0.1" literal here
        # silently went stale for weeks once nextcloud_aio_apache_ip_binding
        # itself moved off loopback during the k3s ingress migration --
        # tracking that variable directly instead of copying its value
        # means this can't drift out of sync the same way again.
        defaults = (ROLE_ROOT / "defaults/main.yml").read_text(encoding="utf-8")
        tasks = (ROLE_ROOT / "tasks/main.yml").read_text(encoding="utf-8")

        self.assertIn("sankey_export_enabled: false", defaults)
        self.assertIn(
            "sankey_export_endpoint_host: \"{{ nextcloud_aio_apache_ip_binding }}\"",
            defaults,
        )
        self.assertNotIn('sankey_export_endpoint_host: 127.0.0.1', defaults)
        self.assertIn("sankey_export_endpoint_port: 11000", defaults)
        self.assertIn("sankey_export_http_host: nextcloud.jkandler.de", defaults)
        self.assertIn(
            "sankey_export_endpoint_host == nextcloud_aio_apache_ip_binding", tasks
        )

    def test_service_runs_as_a_dedicated_unprivileged_account_not_julian(self) -> None:
        tasks = (ROLE_ROOT / "tasks/main.yml").read_text(encoding="utf-8")
        unit = (ROLE_ROOT / "templates/sankey-export.service.j2").read_text(
            encoding="utf-8"
        )

        self.assertIn("Create Sankey export service account", tasks)
        self.assertIn("create_home: false", tasks)
        self.assertIn("User={{ sankey_export_service_user }}", unit)
        self.assertNotIn("julian", unit)
        self.assertNotIn("LoadCredentialEncrypted", unit)

    def test_app_password_file_is_generated_not_sops(self) -> None:
        # A Nextcloud app-password token can't be pre-chosen (occ always
        # generates it) so, unlike deluge_web_password, this has no SOPS
        # entry to encrypt ahead of time -- it's generated once via occ and
        # written to a host-only 0400 file, same as nextcloud_tools' own
        # account. See the plan/ADR for the full reasoning.
        secrets = (
            PROJECT_ROOT / "ansible/inventory/group_vars/all/secrets.sops.yml"
        ).read_text(encoding="utf-8")
        tasks = (ROLE_ROOT / "tasks/main.yml").read_text(encoding="utf-8")
        playbook = (
            PROJECT_ROOT / "ansible/playbooks/sankey-export.yml"
        ).read_text(encoding="utf-8")

        self.assertNotIn("sankey_export_app_password", secrets)
        self.assertIn('mode: "0400"', tasks)
        self.assertIn(
            "generate_and_validate_nextcloud_app_password.yml", playbook
        )

    def test_bootstrap_requires_confirmation(self) -> None:
        playbook = (
            PROJECT_ROOT / "ansible/playbooks/sankey-export.yml"
        ).read_text(encoding="utf-8")

        self.assertIn("BOOTSTRAP_SANKEY_EXPORT", playbook)

    def test_timer_polls_at_the_configured_interval(self) -> None:
        defaults = yaml.safe_load((ROLE_ROOT / "defaults/main.yml").read_text())
        env = Environment(loader=FileSystemLoader(str(ROLE_ROOT / "templates")))
        rendered = env.get_template("sankey-export.timer.j2").render(**defaults)

        self.assertIn("OnUnitActiveSec=1min", rendered)
        self.assertIn("OnBootSec=1min", rendered)

    def test_service_unit_grants_write_access_only_to_the_state_dir(self) -> None:
        defaults = yaml.safe_load((ROLE_ROOT / "defaults/main.yml").read_text())
        env = Environment(loader=FileSystemLoader(str(ROLE_ROOT / "templates")))
        rendered = env.get_template("sankey-export.timer.j2").render(**defaults)

        rendered_service = env.get_template("sankey-export.service.j2").render(**defaults)
        self.assertIn("ProtectSystem=strict", rendered_service)
        self.assertIn(
            f"ReadWritePaths={defaults['sankey_export_state_dir']}", rendered_service
        )

    def test_renders_locally_no_third_party_scraping(self) -> None:
        # 2026-09-06: replaced the old Playwright-driven scrape of
        # finanzfluss.de's own chart export -- see PARKED.md's own
        # "sankey_export: move away from Finanzfluss" writeup for why
        # that was fragile (broke the moment their site changed
        # anything, with zero warning). Kaleido still needs a real
        # Chromium-family browser to drive via CDP, so this checks the
        # replacement's own shape (apt-installed, explicitly pinned via
        # BROWSER_PATH) rather than asserting "no browser at all".
        script = (ROLE_ROOT / "files/finanzfluss_export.py").read_text(encoding="utf-8")
        tasks = (ROLE_ROOT / "tasks/main.yml").read_text(encoding="utf-8")
        unit = (ROLE_ROOT / "templates/sankey-export.service.j2").read_text(
            encoding="utf-8"
        )
        requirements = (ROLE_ROOT / "files/requirements.txt").read_text(
            encoding="utf-8"
        )

        self.assertNotIn("playwright", script.lower())
        # Checking for the actual URL-building/browser-driving constructs,
        # not a bare mention of the domain -- the module docstring
        # legitimately explains *why* it no longer scrapes finanzfluss.de,
        # which would false-fail a bare substring check.
        self.assertNotIn("www.finanzfluss.de", script)
        self.assertNotIn("sync_playwright", script)
        self.assertNotIn(".goto(", script)
        self.assertNotIn("playwright", requirements.lower())
        self.assertIn("kaleido", requirements.lower())
        self.assertIn("plotly", requirements.lower())

        self.assertIn("name: chromium", tasks)
        # Checking for the two old tasks' own exact former names, not a
        # bare substring -- the replacement task's own comment
        # legitimately explains what it replaced (mentioning Playwright
        # and install-deps by name), which would false-fail a blanket
        # check on either word.
        self.assertNotIn(
            "name: Install system dependencies for headless Chromium", tasks
        )
        self.assertNotIn("name: Install Chromium for Playwright", tasks)

        self.assertIn('Environment="BROWSER_PATH=/usr/bin/chromium"', unit)
        self.assertNotIn("PLAYWRIGHT_BROWSERS_PATH", unit)

        # Confirmed live 2026-09-06: BROWSER_PATH alone wasn't enough --
        # choreographer/platformdirs still wants a real, writable $HOME
        # for its own cache dir and tempfiles, which this account has
        # neither (create_home: false) nor could use under ProtectHome
        # below anyway ("PermissionError: .../.local/share/
        # choreographer/deps/chrome-linux64/chrome").
        self.assertIn(
            'Environment="HOME={{ sankey_export_state_dir }}"', unit
        )

    def test_site_yml_wires_the_role_in(self) -> None:
        site = (PROJECT_ROOT / "ansible/playbooks/site.yml").read_text(
            encoding="utf-8"
        )

        self.assertIn("sankey_export", site)


class FinanzflussExportScriptTests(unittest.TestCase):
    def test_clean_amount_handles_german_thousands_and_decimal_separators(self) -> None:
        self.assertEqual(
            sankey.clean_amount("1.234,56 €", row_description="x"), 1234.56
        )
        self.assertEqual(sankey.clean_amount("42,5", row_description="x"), 42.5)
        self.assertEqual(sankey.clean_amount(100, row_description="x"), 100.0)

    def test_clean_amount_rejects_zero_or_negative(self) -> None:
        with self.assertRaises(ValueError):
            sankey.clean_amount(0, row_description="x")
        with self.assertRaises(ValueError):
            sankey.clean_amount(-5, row_description="x")

    def test_format_euro_uses_german_thousands_and_decimal_separators(self) -> None:
        self.assertEqual(sankey.format_euro(1234.5), "1.234,50 €")
        self.assertEqual(sankey.format_euro(42), "42,00 €")

    def test_summarize_budget_renames_colliding_blank_subcategories(self) -> None:
        # Blank-subcategory renaming only kicks in once the category has at
        # least one *named* subcategory (has_named_subcategory) -- an
        # all-blank category is left with no subcategory breakdown at all,
        # which is exercised separately below.
        costs = [
            sankey.CostRow(category="Wohnen", subcategory="Miete", amount=100.0),
            sankey.CostRow(category="Wohnen", subcategory="", amount=50.0),
            sankey.CostRow(category="Wohnen", subcategory="", amount=30.0),
        ]
        incomes = [sankey.IncomeRow(name="Gehalt", amount=200.0)]

        summary = sankey.summarize_budget(incomes, costs)

        names = {sub.name for sub in summary.categories[0].subcategories}
        self.assertEqual(names, {"Miete", "Sonstiges", "Sonstiges 2"})
        self.assertEqual(summary.income_total, 200.0)
        self.assertEqual(summary.expense_total, 180.0)
        self.assertEqual(summary.budget, 20.0)
        self.assertTrue(any("Sonstiges" in warning for warning in summary.warnings))

    def test_summarize_budget_leaves_an_all_blank_category_without_a_breakdown(
        self,
    ) -> None:
        costs = [
            sankey.CostRow(category="Sonstiges", subcategory="", amount=100.0),
        ]
        incomes = [sankey.IncomeRow(name="Gehalt", amount=200.0)]

        summary = sankey.summarize_budget(incomes, costs)

        self.assertEqual(summary.categories[0].subcategories, [])

    def test_summarize_budget_flags_expenses_exceeding_income(self) -> None:
        incomes = [sankey.IncomeRow(name="Gehalt", amount=100.0)]
        costs = [sankey.CostRow(category="Miete", subcategory="", amount=150.0)]

        summary = sankey.summarize_budget(incomes, costs)

        self.assertLess(summary.budget, 0)
        self.assertTrue(any("exceed" in warning for warning in summary.warnings))

    def test_build_sankey_figure_links_income_through_hub_to_categories(self) -> None:
        # Pure/no-I/O -- doesn't call Kaleido's own write_image, so this
        # runs without a browser. The actual PNG write (render_sankey_png)
        # is smoke-tested manually, not here -- same split this repo's
        # own Terraform/Ansible convention uses (compute vs. apply).
        incomes = [sankey.IncomeRow(name="Gehalt", amount=1000.0)]
        costs = [
            sankey.CostRow(category="Auto", subcategory="Sprit", amount=100.0),
            sankey.CostRow(category="Auto", subcategory="Versicherung", amount=50.0),
            sankey.CostRow(category="Miete", subcategory="", amount=500.0),
        ]
        summary = sankey.summarize_budget(incomes, costs)

        figure = sankey.build_sankey_figure(summary)
        sankey_trace = figure.data[0]

        labels = list(sankey_trace.node.label)
        self.assertTrue(any(label.startswith("Einkommen") for label in labels))
        self.assertTrue(any(label.startswith("Gehalt") for label in labels))
        self.assertTrue(any(label.startswith("Auto") for label in labels))
        self.assertTrue(any(label.startswith("Sprit") for label in labels))
        self.assertTrue(any(label.startswith("Budget") for label in labels))

        # 1 income->hub, 2 hub->category (Auto, Miete), 2 category->
        # subcategory (Sprit, Versicherung), 1 hub->Budget = 6 links.
        self.assertEqual(len(sankey_trace.link.value), 6)

    def test_etag_skip_avoids_touching_the_renderer(self) -> None:
        # load_cached_etag/save_cached_etag are the whole mechanism that
        # keeps the 1-minute timer cheap -- a change check that doesn't
        # correctly round-trip would make every tick expensive again.
        import tempfile

        with tempfile.TemporaryDirectory() as tmp:
            state_path = Path(tmp) / "last-etag"

            self.assertIsNone(sankey.load_cached_etag(state_path))

            sankey.save_cached_etag(state_path, '"abc123"')
            self.assertEqual(sankey.load_cached_etag(state_path), '"abc123"')


if __name__ == "__main__":
    unittest.main()
