from __future__ import annotations

import unittest

from _load_module import load_module_from_path

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


if __name__ == "__main__":
    unittest.main()
