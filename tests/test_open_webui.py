from __future__ import annotations

import unittest
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
ROLE_ROOT = PROJECT_ROOT / "ansible/roles/open_webui"


class OpenWebUITests(unittest.TestCase):
    def test_defaults_define_the_service_account_and_data_directory(self) -> None:
        defaults = (ROLE_ROOT / "defaults/main.yml").read_text(encoding="utf-8")

        self.assertIn(
            "open_webui_container_user: open-webui-container", defaults
        )
        self.assertIn("open_webui_data_dir: /var/lib/open-webui", defaults)

    def test_tasks_run_unconditionally_now_container_is_retired(self) -> None:
        defaults = (ROLE_ROOT / "defaults/main.yml").read_text(encoding="utf-8")
        tasks = (ROLE_ROOT / "tasks/main.yml").read_text(encoding="utf-8")

        self.assertNotIn("open_webui_enabled", defaults)
        self.assertNotIn("open_webui_enabled", tasks)
        self.assertNotIn("docker_container", tasks)
        self.assertNotIn("docker_image", tasks)
        self.assertNotIn("open-webui/open-webui", tasks)
        self.assertNotIn("home-agent-frontend", tasks)

    def test_service_account_uid_is_validated_against_k3s_apps(self) -> None:
        # k3s-apps hardcodes run_as_user=995, so a different uid must fail here.
        tasks = (ROLE_ROOT / "tasks/main.yml").read_text(encoding="utf-8")

        self.assertIn("Create Open WebUI container account", tasks)
        self.assertIn('open_webui_container_uid == "995"', tasks)

    def test_data_directories_are_owned_by_the_service_account_and_root_group(
        self,
    ) -> None:
        # group root, matching k3s-apps' run_as_group.
        tasks = (ROLE_ROOT / "tasks/main.yml").read_text(encoding="utf-8")

        self.assertIn("Create Open WebUI state directories", tasks)
        self.assertIn('group: root', tasks)
        self.assertIn('mode: "0750"', tasks)


if __name__ == "__main__":
    unittest.main()
