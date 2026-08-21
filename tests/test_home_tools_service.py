from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from _load_module import load_module_from_path

home_tools_service = load_module_from_path(
    "home_tools_service", "ansible/roles/home_agent/files/home_tools_service.py"
)


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
