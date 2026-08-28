from __future__ import annotations

import unittest
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
ROLE_ROOT = PROJECT_ROOT / "ansible/roles/base"


class BaseRoleTests(unittest.TestCase):
    def test_requires_root(self) -> None:
        tasks = (ROLE_ROOT / "tasks/main.yml").read_text(encoding="utf-8")

        self.assertIn('effective_user.stdout == "root"', tasks)

    def test_interactive_shell_packages_come_from_apt_not_homebrew(
        self,
    ) -> None:
        # Was Homebrew-installed by hand before, entirely outside this
        # repo -- migrated to apt + pinned git clones so the interactive
        # shell setup isn't an untracked install any more.
        tasks = (ROLE_ROOT / "tasks/main.yml").read_text(encoding="utf-8")

        install_task = tasks.split(
            "Install base packages", 1
        )[1].split("- name:", 1)[0]
        self.assertIn("zsh", install_task)
        self.assertIn("zsh-autosuggestions", install_task)
        self.assertIn("zsh-syntax-highlighting", install_task)

    def test_oh_my_zsh_is_pinned_not_a_moving_branch(self) -> None:
        tasks = (ROLE_ROOT / "tasks/main.yml").read_text(encoding="utf-8")

        clone_task = tasks.split("Install Oh My Zsh", 1)[1].split(
            "- name:", 1
        )[0]
        self.assertIn("repo: https://github.com/ohmyzsh/ohmyzsh.git", clone_task)
        self.assertIn(
            "version: 146461f7c6d95f4ba1220559d66eb113418b40a8", clone_task
        )

    def test_powerlevel10k_is_pinned_to_the_version_already_in_use(
        self,
    ) -> None:
        # Pinned to v1.20.0 -- the exact version previously installed via
        # Homebrew, so migrating off it doesn't change the prompt's look.
        tasks = (ROLE_ROOT / "tasks/main.yml").read_text(encoding="utf-8")

        clone_task = tasks.split("Install Powerlevel10k theme", 1)[1].split(
            "- name:", 1
        )[0]
        self.assertIn(
            "repo: https://github.com/romkatv/powerlevel10k.git", clone_task
        )
        self.assertIn("version: v1.20.0", clone_task)


if __name__ == "__main__":
    unittest.main()
