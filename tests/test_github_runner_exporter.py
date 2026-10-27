from __future__ import annotations

import unittest
from pathlib import Path
from unittest import mock

from _load_module import load_module_from_path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
ROLE_ROOT = PROJECT_ROOT / "ansible/roles/monitoring"

exporter = load_module_from_path(
    "github_runner_exporter", "ansible/roles/monitoring/files/github_runner_exporter.py"
)


class GithubRunnerExporterTests(unittest.TestCase):
    def test_runner_state_follows_githubs_status_and_busy_flag(self) -> None:
        self.assertEqual(exporter.runner_state({"status": "online", "busy": False}), "online_idle")
        self.assertEqual(exporter.runner_state({"status": "online", "busy": True}), "online_busy")
        # GitHub can leave busy set on a runner that went offline mid-job.
        self.assertEqual(exporter.runner_state({"status": "offline", "busy": True}), "offline")

    def test_collect_counts_runners_and_reads_queued_total(self) -> None:
        responses = {
            "/repos/KandlerLi/dyndns/actions/runners?per_page=100": {
                "runners": [
                    {"status": "online", "busy": True},
                    {"status": "online", "busy": False},
                    {"status": "offline", "busy": False},
                ]
            },
            "/repos/KandlerLi/dyndns/actions/runs?status=queued&per_page=1": {"total_count": 2},
        }
        with mock.patch.object(exporter, "OWNER", "KandlerLi"), mock.patch.object(
            exporter, "get", side_effect=lambda path, token: responses[path]
        ):
            result = exporter.collect("dyndns", "t")

        self.assertEqual(
            result,
            {"runners": {"online_idle": 1, "online_busy": 1, "offline": 1}, "queued": 2},
        )

    def test_failed_repo_reports_only_up_zero(self) -> None:
        # Stale counts would read as healthy; a failed poll drops them.
        rendered = exporter.render(
            {
                "dyndns": {"runners": {"online_idle": 2, "online_busy": 0, "offline": 0}, "queued": 0},
                "website": None,
            }
        )

        self.assertIn('github_runners{repo="dyndns",state="online_idle"} 2', rendered)
        self.assertIn('github_actions_queued_runs{repo="dyndns"} 0', rendered)
        self.assertIn('github_runner_exporter_up{repo="dyndns"} 1', rendered)
        self.assertIn('github_runner_exporter_up{repo="website"} 0', rendered)
        self.assertNotIn('github_runners{repo="website"', rendered)

    def test_poll_failure_logs_no_exception_detail(self) -> None:
        # urllib errors can carry the request, whose headers hold the token.
        state = exporter.State()
        with mock.patch.object(exporter, "REPOSITORIES", ["dyndns"]), mock.patch.object(
            exporter, "read_token", return_value="secret-token"
        ), mock.patch.object(exporter, "collect", side_effect=RuntimeError("secret-token")), mock.patch.object(
            exporter.time, "sleep", side_effect=StopIteration
        ), self.assertLogs(exporter.LOGGER, "WARNING") as logs:
            with self.assertRaises(StopIteration):
                state.poll_forever()

        self.assertNotIn("secret-token", "\n".join(logs.output))
        self.assertIsNone(state.results["dyndns"])

    def test_listens_on_loopback_only(self) -> None:
        source = (ROLE_ROOT / "files/github_runner_exporter.py").read_text(encoding="utf-8")

        self.assertIn('ThreadingHTTPServer(("127.0.0.1", LISTEN_PORT)', source)


if __name__ == "__main__":
    unittest.main()
