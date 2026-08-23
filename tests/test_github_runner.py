from __future__ import annotations

import unittest
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
ROLE_ROOT = PROJECT_ROOT / "ansible/roles/github_runner"


class GithubRunnerTests(unittest.TestCase):
    def test_runner_downloads_query_uses_a_stable_repository_not_first(
        self,
    ) -> None:
        # Found live: the runner-application download catalog is the same
        # regardless of which repository you ask (it's not repo-specific
        # data, the API just requires a repo-scoped token), but a brand-new
        # repository can return an empty catalog until GitHub finishes
        # provisioning Actions for it. Querying whichever repository
        # happened to sort first in github_runner_github_repositories broke
        # the very first time a new repository sorted before the
        # long-established one.
        tasks = (ROLE_ROOT / "tasks/configure_guest.yml").read_text(
            encoding="utf-8"
        )

        self.assertIn("github_runner_downloads_repository", tasks)
        self.assertNotIn(
            "(github_runner_github_repositories | first).repository", tasks
        )

    def test_downloads_repository_defaults_to_an_established_repository(
        self,
    ) -> None:
        defaults = (ROLE_ROOT / "defaults/main.yml").read_text(
            encoding="utf-8"
        )

        self.assertIn("github_runner_downloads_repository: dyndns", defaults)

    def test_downloads_repository_is_validated_against_the_repository_list(
        self,
    ) -> None:
        tasks = (ROLE_ROOT / "tasks/configure_guest.yml").read_text(
            encoding="utf-8"
        )

        self.assertIn(
            "github_runner_downloads_repository\n"
            "        in (github_runner_github_repositories | "
            "map(attribute='repository'))",
            tasks,
        )

    def test_aws_cli_is_installed_on_the_runner_vm(self) -> None:
        # Found live: website's apply.yml runs `aws s3 sync`/`aws
        # cloudfront create-invalidation` directly, which GitHub-hosted
        # runners have preinstalled but this VM never needed before
        # website moved onto the self-hosted runner.
        tasks = (ROLE_ROOT / "tasks/configure_guest.yml").read_text(
            encoding="utf-8"
        )

        self.assertIn("awscli", tasks)


if __name__ == "__main__":
    unittest.main()
