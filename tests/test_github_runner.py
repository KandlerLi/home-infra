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

    def test_docker_role_runs_before_github_runner_on_the_vm(self) -> None:
        # Before github_runner's configure_guest: each per-repo service
        # account needs the docker group to already exist when it's
        # created, so every repository's CI can run inside a container.
        playbook = (
            PROJECT_ROOT / "ansible/playbooks/github-runner.yml"
        ).read_text(encoding="utf-8")

        vm_play = playbook.split("hosts: github_runner_vms", 1)[1]
        docker_index = vm_play.index("- role: docker")
        runner_index = vm_play.index("- role: github_runner")
        self.assertLess(docker_index, runner_index)

    def test_per_repo_service_account_can_use_docker_without_sudo(self) -> None:
        tasks = (ROLE_ROOT / "tasks/configure_repository.yml").read_text(
            encoding="utf-8"
        )

        create_user_task = tasks.split(
            "Create service user for", 1
        )[1].split("- name:", 1)[0]
        self.assertIn("groups: docker", create_user_task)
        self.assertIn("append: true", create_user_task)

    def test_runner_restarts_when_docker_group_membership_changes(self) -> None:
        # A user's supplementary-group change doesn't affect an
        # already-running process -- the runner has to actually restart
        # to pick up docker-group access, same as it already does for a
        # binary update or a systemd unit change.
        tasks = (ROLE_ROOT / "tasks/configure_repository.yml").read_text(
            encoding="utf-8"
        )

        stop_task = tasks.split(
            "Stop runner before changing", 1
        )[1].split("- name:", 1)[0]
        self.assertIn("github_runner_service_account.changed", stop_task)

    def test_workspace_ownership_is_reclaimed_from_root_owned_ci_files(
        self,
    ) -> None:
        # A containerized job runs as root by default and can leave
        # root-owned files behind in the reused on-disk workspace,
        # blocking the next unprivileged job's checkout.
        tasks = (ROLE_ROOT / "tasks/configure_repository.yml").read_text(
            encoding="utf-8"
        )

        reclaim_task = tasks.split(
            "Reclaim workspace ownership for", 1
        )[1].split("- name:", 1)[0]
        self.assertIn(
            "path: \"{{ github_runner_repository_install_dir }}/_work\"",
            reclaim_task,
        )
        self.assertIn(
            "owner: \"{{ github_runner_repository_service_user }}\"",
            reclaim_task,
        )
        self.assertIn("recurse: true", reclaim_task)


if __name__ == "__main__":
    unittest.main()
