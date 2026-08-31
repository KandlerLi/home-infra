from __future__ import annotations

import sys
import unittest
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "scripts"))

import json

from sync_github_runner_repositories import render, render_tfvars, runner_repositories


class RunnerRepositoriesTests(unittest.TestCase):
    def test_filters_to_runner_enabled_repositories_only(self) -> None:
        config = {
            "dyndns": {"action_variables": {}, "runner": True},
            "testing": {},
            "no-runner-flag": {"action_variables": {"FOO": "bar"}},
        }

        self.assertEqual(
            runner_repositories(config),
            [{"id": "dyndns", "repository": "dyndns"}],
        )

    def test_sorts_by_repository_name(self) -> None:
        config = {"zeta": {"runner": True}, "alpha": {"runner": True}}

        self.assertEqual(
            runner_repositories(config),
            [
                {"id": "alpha", "repository": "alpha"},
                {"id": "zeta", "repository": "zeta"},
            ],
        )

    def test_tolerates_null_entries(self) -> None:
        config = {"testing": None}

        self.assertEqual(runner_repositories(config), [])

    def test_render_produces_valid_yaml_with_header(self) -> None:
        text = render([{"id": "dyndns", "repository": "dyndns"}])

        self.assertIn("GENERATED FILE", text)
        self.assertIn("github_runner_github_repositories:", text)
        self.assertIn("id: dyndns", text)

    def test_render_tfvars_produces_valid_json_the_module_can_for_each_over(
        self,
    ) -> None:
        repositories = [
            {"id": "dyndns", "repository": "dyndns"},
            {"id": "website", "repository": "website"},
        ]

        text = render_tfvars(repositories)
        parsed = json.loads(text)

        self.assertEqual(
            parsed, {"github_runner_repositories": repositories}
        )


if __name__ == "__main__":
    unittest.main()
