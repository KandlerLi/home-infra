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
    def test_defaults_are_disabled_and_loopback_only(self) -> None:
        defaults = (ROLE_ROOT / "defaults/main.yml").read_text(encoding="utf-8")

        self.assertIn("sankey_export_enabled: false", defaults)
        self.assertIn("sankey_export_endpoint_host: 127.0.0.1", defaults)
        self.assertIn("sankey_export_endpoint_port: 11000", defaults)
        self.assertIn("sankey_export_http_host: nextcloud.jkandler.de", defaults)

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
        rendered = env.get_template("sankey-export.service.j2").render(**defaults)

        self.assertIn("ProtectSystem=strict", rendered)
        self.assertIn(
            f"ReadWritePaths={defaults['sankey_export_state_dir']}", rendered
        )

    def test_playwright_launches_without_a_kernel_sandbox(self) -> None:
        # This browser only ever visits one fixed, self-generated
        # finanzfluss.de URL, so --no-sandbox is used unconditionally
        # rather than only as a root fallback -- see the comment in the
        # script for why (avoids fighting the unit's own hardening).
        script = (
            ROLE_ROOT / "files/finanzfluss_export.py"
        ).read_text(encoding="utf-8")

        self.assertIn('args=["--no-sandbox"]', script)

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

    def test_build_payload_renames_colliding_blank_subcategories(self) -> None:
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

        _, cost_payload, income_total, expense_total, budget, warnings = (
            sankey.build_payload(incomes, costs)
        )

        names = {position["n"] for position in cost_payload[0]["po"]}
        self.assertEqual(names, {"Miete", "Sonstiges", "Sonstiges 2"})
        self.assertEqual(income_total, 200.0)
        self.assertEqual(expense_total, 180.0)
        self.assertEqual(budget, 20.0)
        self.assertTrue(any("Sonstiges" in warning for warning in warnings))

    def test_build_payload_leaves_an_all_blank_category_without_a_breakdown(
        self,
    ) -> None:
        costs = [
            sankey.CostRow(category="Sonstiges", subcategory="", amount=100.0),
        ]
        incomes = [sankey.IncomeRow(name="Gehalt", amount=200.0)]

        _, cost_payload, *_ = sankey.build_payload(incomes, costs)

        self.assertEqual(cost_payload[0]["po"], [])
        self.assertEqual(cost_payload[0]["ro"], 0)

    def test_build_payload_flags_expenses_exceeding_income(self) -> None:
        incomes = [sankey.IncomeRow(name="Gehalt", amount=100.0)]
        costs = [sankey.CostRow(category="Miete", subcategory="", amount=150.0)]

        _, cost_payload, _, _, budget, warnings = sankey.build_payload(incomes, costs)

        self.assertLess(budget, 0)
        self.assertFalse(any(entry["n"] == "Budget" for entry in cost_payload))
        self.assertTrue(any("exceed" in warning for warning in warnings))

    def test_etag_skip_avoids_touching_playwright(self) -> None:
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
