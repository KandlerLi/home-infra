from __future__ import annotations

import unittest
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
ROLE_ROOT = PROJECT_ROOT / "ansible/roles/open_webui"


class OpenWebUITests(unittest.TestCase):
    def test_defaults_are_opt_in_loopback_only_and_digest_pinned(self) -> None:
        defaults = (ROLE_ROOT / "defaults/main.yml").read_text(encoding="utf-8")

        self.assertIn("open_webui_enabled: false", defaults)
        self.assertIn("open_webui_bind_address: 127.0.0.1", defaults)
        self.assertIn("open_webui_port: 8091", defaults)
        self.assertIn("open_webui_image_tag: v0.11.0-slim", defaults)
        self.assertIn(
            "sha256:88da9f0e08b8ada8e40bc6d6291494e2c6775a62d24b67b0cefb74ffee4ce621",
            defaults,
        )

    def test_container_has_no_host_control_surface_or_provider_secret(self) -> None:
        tasks = (ROLE_ROOT / "tasks/main.yml").read_text(encoding="utf-8")

        self.assertNotIn("/var/run/docker.sock", tasks)
        self.assertNotIn("network_mode: host", tasks)
        self.assertNotIn("sk-", tasks)
        self.assertIn("no-new-privileges:true", tasks)
        self.assertIn("cap_drop:", tasks)
        self.assertIn("- ALL", tasks)
        self.assertIn("home-agent-internal", (ROLE_ROOT / "defaults/main.yml").read_text())
        self.assertIn("home-agent-frontend", tasks)

    def test_frontend_network_allows_loopback_publish_without_external_egress(self) -> None:
        home_agent_tasks = (
            PROJECT_ROOT / "ansible/roles/home_agent/tasks/main.yml"
        ).read_text(encoding="utf-8")
        open_webui_tasks = (ROLE_ROOT / "tasks/main.yml").read_text(encoding="utf-8")

        self.assertIn("internal: false", home_agent_tasks)
        self.assertIn(
            'com.docker.network.bridge.enable_ip_masquerade: "false"',
            home_agent_tasks,
        )
        self.assertIn("home_agent_frontend_network_current.exists", home_agent_tasks)
        self.assertIn("not open_webui_frontend_network.network.Internal", open_webui_tasks)

    def test_unneeded_execution_and_upload_features_are_disabled(self) -> None:
        tasks = (ROLE_ROOT / "tasks/main.yml").read_text(encoding="utf-8")

        disabled_settings = [
            "ENABLE_OLLAMA_API",
            "ENABLE_PLUGINS",
            "ENABLE_CODE_EXECUTION",
            "ENABLE_CODE_INTERPRETER",
            "ENABLE_WEB_SEARCH",
            "ENABLE_IMAGE_GENERATION",
            "ENABLE_SUBAGENTS",
            "USER_PERMISSIONS_CHAT_FILE_UPLOAD",
            "USER_PERMISSIONS_CHAT_WEB_UPLOAD",
            "USER_PERMISSIONS_CHAT_SYSTEM_PROMPT",
            "RAG_EMBEDDING_MODEL_AUTO_UPDATE",
            "RAG_RERANKING_MODEL_AUTO_UPDATE",
        ]
        for setting in disabled_settings:
            self.assertIn(f'{setting}: "False"', tasks)

    def test_dedicated_playbook_keeps_model_access_in_home_agent(self) -> None:
        playbook = (
            PROJECT_ROOT / "ansible/playbooks/open-webui.yml"
        ).read_text(encoding="utf-8")

        self.assertLess(playbook.index("role: home_agent"), playbook.index("role: open_webui"))
        self.assertIn("home_agent_frontend_network_enabled: true", playbook)


if __name__ == "__main__":
    unittest.main()
