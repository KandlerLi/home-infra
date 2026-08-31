from __future__ import annotations

import unittest
from pathlib import Path
from unittest import mock

from _load_module import load_module_from_path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
ROLE_ROOT = PROJECT_ROOT / "ansible/roles/monitoring"

ntfy_relay = load_module_from_path(
    "ntfy_relay", "ansible/roles/monitoring/files/ntfy_relay.py"
)


class NtfyRelayFormattingTests(unittest.TestCase):
    def test_single_firing_alert_is_readable(self) -> None:
        payload = {
            "status": "firing",
            "alerts": [
                {
                    "status": "firing",
                    "labels": {"alertname": "ContainerDown", "severity": "critical"},
                    "annotations": {"description": "cAdvisor hasn't seen this container"},
                }
            ],
        }

        title, message, tag, priority = ntfy_relay.format_notification(payload)

        self.assertEqual(title, "1 alert firing")
        self.assertIn("ContainerDown", message)
        self.assertIn("critical", message)
        self.assertIn("cAdvisor hasn't seen this container", message)
        self.assertNotIn("{", message)  # never leaks raw JSON into the message
        self.assertEqual(tag, "rotating_light")
        self.assertEqual(priority, 5)

    def test_resolved_alert_uses_the_resolved_tag_regardless_of_severity(self) -> None:
        payload = {
            "status": "resolved",
            "alerts": [
                {
                    "status": "resolved",
                    "labels": {"alertname": "ServiceUnreachable", "severity": "critical"},
                    "annotations": {"summary": "back up"},
                }
            ],
        }

        title, _message, tag, priority = ntfy_relay.format_notification(payload)

        self.assertEqual(title, "1 alert resolved")
        self.assertEqual(tag, "white_check_mark")
        self.assertEqual(priority, ntfy_relay.RESOLVED_PRIORITY)

    def test_multiple_alerts_pick_the_worst_severity_for_the_notification_tag(self) -> None:
        payload = {
            "status": "firing",
            "alerts": [
                {
                    "status": "firing",
                    "labels": {"alertname": "DiskAlmostFull", "severity": "warning"},
                    "annotations": {"summary": "85% full"},
                },
                {
                    "status": "firing",
                    "labels": {"alertname": "ContainerDown", "severity": "critical"},
                    "annotations": {"summary": "grafana missing"},
                },
            ],
        }

        title, message, tag, priority = ntfy_relay.format_notification(payload)

        self.assertEqual(title, "2 alerts firing")
        self.assertIn("DiskAlmostFull", message)
        self.assertIn("ContainerDown", message)
        self.assertEqual(tag, "rotating_light")
        self.assertEqual(priority, 5)

    def test_missing_annotations_do_not_crash_formatting(self) -> None:
        payload = {
            "status": "firing",
            "alerts": [
                {
                    "status": "firing",
                    "labels": {"alertname": "Test", "severity": "info"},
                    "annotations": {},
                }
            ],
        }

        title, message, tag, priority = ntfy_relay.format_notification(payload)

        self.assertEqual(title, "1 alert firing")
        self.assertIn("Test", message)
        self.assertEqual(tag, "information_source")
        self.assertEqual(priority, 3)

    def test_empty_alerts_list_still_produces_a_sane_message(self) -> None:
        title, message, _tag, _priority = ntfy_relay.format_notification(
            {"status": "firing", "alerts": []}
        )

        self.assertEqual(title, "0 alerts firing")
        self.assertEqual(message, "(no alert detail in payload)")


class NtfyRelayK3sListenerTests(unittest.TestCase):
    def test_only_the_loopback_listener_starts_by_default(self) -> None:
        with (
            mock.patch.object(ntfy_relay, "LISTEN_HOST_K3S", ""),
            mock.patch.object(ntfy_relay, "ThreadingHTTPServer") as server_cls,
            mock.patch.object(ntfy_relay.threading, "Thread") as thread_cls,
        ):
            ntfy_relay.main()

        server_cls.assert_called_once_with(
            ("127.0.0.1", ntfy_relay.LISTEN_PORT), ntfy_relay.WebhookHandler
        )
        thread_cls.assert_not_called()

    def test_second_listener_starts_additionally_when_k3s_bind_address_is_set(
        self,
    ) -> None:
        # Additive, not a switch -- the loopback server still has to
        # start too, so the still-Docker-based Alertmanager on this same
        # host keeps working throughout the migration (see this
        # constant's own comment).
        with (
            mock.patch.object(ntfy_relay, "LISTEN_HOST_K3S", "192.168.101.1"),
            mock.patch.object(ntfy_relay, "ThreadingHTTPServer") as server_cls,
            mock.patch.object(ntfy_relay.threading, "Thread") as thread_cls,
        ):
            ntfy_relay.main()

        addresses = [call.args[0] for call in server_cls.call_args_list]
        self.assertIn(("127.0.0.1", ntfy_relay.LISTEN_PORT), addresses)
        self.assertIn(("192.168.101.1", ntfy_relay.LISTEN_PORT), addresses)
        # The k3s listener runs in a background thread so the loopback
        # server can still own the main thread the same way it always
        # has -- daemon=True so it doesn't block process shutdown.
        thread_cls.assert_called_once()
        self.assertTrue(thread_cls.call_args.kwargs.get("daemon"))


class MonitoringDataRootPermissionTests(unittest.TestCase):
    def test_data_root_is_created_world_traversable_before_service_subdirs(self) -> None:
        # ansible.builtin.file stamps an implicitly-created parent
        # directory with whatever owner/mode the *first* task needing it
        # specifies. Without an explicit, world-traversable
        # monitoring_data_dir task ahead of the per-service directory
        # loop, /var/lib/monitoring would end up owned by whichever
        # service happened to be first in that loop (0750) -- silently
        # blocking every other account from traversing into it. Never
        # surfaced for the Docker-based services (a bind mount doesn't
        # walk the host's real parent-directory chain), only for
        # ntfy_relay.service's native, non-container file read --
        # confirmed live via a real EACCES.
        tasks = (ROLE_ROOT / "tasks/main.yml").read_text(encoding="utf-8")

        root_task_index = tasks.index("Create monitoring data root directory")
        loop_task_index = tasks.index("Create monitoring directories")
        self.assertLess(root_task_index, loop_task_index)

        root_task = tasks[root_task_index : loop_task_index + 200]
        self.assertIn('owner: root', root_task)
        self.assertIn('mode: "0755"', root_task)


if __name__ == "__main__":
    unittest.main()
