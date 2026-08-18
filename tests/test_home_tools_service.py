from __future__ import annotations

import importlib.util
import unittest
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
MODULE_PATH = PROJECT_ROOT / "ansible/roles/home_agent/files/home_tools_service.py"
SPEC = importlib.util.spec_from_file_location("home_tools_service", MODULE_PATH)
assert SPEC and SPEC.loader
home_tools_service = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(home_tools_service)


class HomeToolsServiceTests(unittest.TestCase):
    def test_docker_response_is_strictly_sanitized(self) -> None:
        result = home_tools_service.sanitize_containers(
            [
                {
                    "Id": "secret-internal-id",
                    "Names": ["/nextcloud-aio-nextcloud"],
                    "Image": "example/nextcloud:latest",
                    "State": "running",
                    "Status": "Up 2 hours (healthy)",
                    "Labels": {"secret": "must-not-leak"},
                    "Env": ["TOKEN=must-not-leak"],
                    "Mounts": [{"Source": "/private"}],
                }
            ]
        )

        self.assertEqual(
            result,
            {
                "containers": [
                    {
                        "name": "nextcloud-aio-nextcloud",
                        "image": "example/nextcloud:latest",
                        "state": "running",
                        "status": "Up 2 hours (healthy)",
                    }
                ]
            },
        )
        self.assertNotIn("must-not-leak", repr(result))

    def test_system_health_has_only_expected_top_level_fields(self) -> None:
        result = home_tools_service.get_system_health()
        self.assertEqual(
            set(result), {"hostname", "uptime_seconds", "cpu_count", "load"}
        )


if __name__ == "__main__":
    unittest.main()
