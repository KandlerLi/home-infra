from __future__ import annotations

import sys
import unittest
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _load_module import load_module_from_path

role_recap = load_module_from_path(
    "role_recap", "ansible/callback_plugins/role_recap.py"
)


def _result(role_name: str | None, **result_fields) -> SimpleNamespace:
    role = SimpleNamespace(get_name=lambda: role_name) if role_name else None
    task = SimpleNamespace(_role=role)
    return SimpleNamespace(_task=task, _result=result_fields)


class RoleRecapCallbackTests(unittest.TestCase):
    # The built-in PLAY RECAP only ever aggregates by host, which doesn't
    # say *which* role changed something out of ~11 applied in one run --
    # these exercise the per-role grouping directly, without spinning up
    # a real ansible-playbook run (see the scratchpad smoke test from
    # 2026-09-06 for that end-to-end check).
    def setUp(self) -> None:
        self.callback = role_recap.CallbackModule()

    def test_groups_ok_and_changed_results_by_role(self) -> None:
        self.callback.v2_runner_on_ok(_result("alpha", changed=True))
        self.callback.v2_runner_on_ok(_result("alpha", changed=False))
        self.callback.v2_runner_on_ok(_result("beta", changed=True))

        self.assertEqual(self.callback._counts["alpha"]["changed"], 1)
        self.assertEqual(self.callback._counts["alpha"]["ok"], 1)
        self.assertEqual(self.callback._counts["beta"]["changed"], 1)

    def test_tasks_outside_any_role_are_grouped_not_dropped(self) -> None:
        self.callback.v2_runner_on_ok(_result(None, changed=False))

        self.assertEqual(self.callback._counts["(no role)"]["ok"], 1)

    def test_failed_skipped_and_unreachable_are_tracked_separately(self) -> None:
        self.callback.v2_runner_on_failed(_result("alpha"))
        self.callback.v2_runner_on_failed(_result("alpha"), ignore_errors=True)
        self.callback.v2_runner_on_skipped(_result("beta"))
        self.callback.v2_runner_on_unreachable(_result("beta"))

        self.assertEqual(self.callback._counts["alpha"]["failed"], 1)
        self.assertEqual(self.callback._counts["alpha"]["ignored"], 1)
        self.assertEqual(self.callback._counts["beta"]["skipped"], 1)
        self.assertEqual(self.callback._counts["beta"]["unreachable"], 1)

    def test_stats_banner_is_skipped_when_nothing_ran(self) -> None:
        printed = []
        self.callback._display = SimpleNamespace(
            banner=lambda *a: printed.append(("banner", *a)),
            display=lambda *a: printed.append(("display", *a)),
        )

        self.callback.v2_playbook_on_stats(stats=None)

        self.assertEqual(printed, [])

    def test_stats_banner_prints_one_sorted_line_per_role(self) -> None:
        self.callback.v2_runner_on_ok(_result("sankey_export", changed=True))
        self.callback.v2_runner_on_ok(_result("base", changed=False))
        printed = []
        self.callback._display = SimpleNamespace(
            banner=lambda *a: printed.append(("banner", *a)),
            display=lambda *a: printed.append(("display", *a)),
        )

        self.callback.v2_playbook_on_stats(stats=None)

        self.assertEqual(printed[0], ("banner", "ROLE RECAP"))
        role_lines = [line for kind, line in printed[1:]]
        self.assertEqual(len(role_lines), 2)
        # Sorted alphabetically, so "base" prints before "sankey_export".
        self.assertTrue(role_lines[0].startswith("base"))
        self.assertIn("changed=0", role_lines[0])
        self.assertTrue(role_lines[1].startswith("sankey_export"))
        self.assertIn("changed=1", role_lines[1])


if __name__ == "__main__":
    unittest.main()
