"""Reformat Alertmanager webhook payloads into human-readable ntfy pushes.

Alertmanager's webhook_configs POST its own fixed JSON schema with no
templating support; ntfy has no native understanding of that schema, so
posting straight to ntfy.sh shows the raw JSON as the notification text.
This sits between the two: Alertmanager posts here instead, this extracts
a readable title/message/priority/tag from the payload and republishes
that to ntfy's own JSON publish API.
"""

from __future__ import annotations

import json
import logging
import os
import threading
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

LOGGER = logging.getLogger(__name__)

NTFY_URL = os.environ.get("NTFY_URL", "https://ntfy.sh")
NTFY_TOPIC_FILE = os.environ.get("NTFY_TOPIC_FILE", "")
LISTEN_PORT = int(os.environ.get("LISTEN_PORT", "9096"))
# Off by default (loopback only). When set, a second listener binds
# this address additionally, so the k3s-native Alertmanager can reach
# this too without breaking the loopback listener every other consumer
# on this host still uses. 192.168.101.1 is the only value
# ansible/roles/monitoring's own validation allows.
LISTEN_HOST_K3S = os.environ.get("LISTEN_HOST_K3S", "")
MAX_REQUEST_BYTES = 262144

SEVERITY_TAG = {
    "critical": "rotating_light",
    "warning": "warning",
    "info": "information_source",
}
SEVERITY_PRIORITY = {
    "critical": 5,
    "warning": 4,
    "info": 3,
}
RESOLVED_TAG = "white_check_mark"
RESOLVED_PRIORITY = 3


def read_topic() -> str:
    with open(NTFY_TOPIC_FILE, "r", encoding="utf-8") as handle:
        return handle.read().strip()


def format_notification(payload: dict) -> tuple[str, str, str, int]:
    """Return (title, message, tag, priority) for an Alertmanager webhook payload."""
    status = payload.get("status", "firing")
    alerts = payload.get("alerts", [])
    resolved = status == "resolved"

    lines = []
    severities = set()
    for alert in alerts:
        labels = alert.get("labels", {})
        annotations = alert.get("annotations", {})
        name = labels.get("alertname", "unknown")
        severity = labels.get("severity", "info")
        severities.add(severity)
        summary = annotations.get("summary") or annotations.get("description") or ""
        marker = "RESOLVED" if alert.get("status") == "resolved" else "FIRING"
        lines.append(f"[{marker}] {name} ({severity}): {summary}".rstrip(": "))

    count = len(alerts)
    noun = "alert" if count == 1 else "alerts"

    if resolved:
        title = f"{count} {noun} resolved"
        tag = RESOLVED_TAG
        priority = RESOLVED_PRIORITY
    else:
        title = f"{count} {noun} firing"
        worst = next(
            (level for level in ("critical", "warning", "info") if level in severities),
            "info",
        )
        tag = SEVERITY_TAG.get(worst, "information_source")
        priority = SEVERITY_PRIORITY.get(worst, 3)

    message = "\n".join(lines) if lines else "(no alert detail in payload)"
    return title, message, tag, priority


def publish(title: str, message: str, tag: str, priority: int) -> None:
    body = json.dumps(
        {
            "topic": read_topic(),
            "title": title,
            "message": message,
            "priority": priority,
            "tags": [tag],
        }
    ).encode("utf-8")
    request = urllib.request.Request(
        NTFY_URL,
        data=body,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=10) as response:
        response.read()


class WebhookHandler(BaseHTTPRequestHandler):
    def do_GET(self) -> None:
        if self.path == "/healthz":
            self.send_response(200)
            self.end_headers()
            self.wfile.write(b"ok")
            return
        self.send_response(404)
        self.end_headers()

    def do_POST(self) -> None:
        content_length = int(self.headers.get("Content-Length", 0))
        if content_length <= 0 or content_length > MAX_REQUEST_BYTES:
            self.send_response(400)
            self.end_headers()
            return

        body = self.rfile.read(content_length)
        try:
            payload = json.loads(body)
            title, message, tag, priority = format_notification(payload)
            publish(title, message, tag, priority)
        except Exception:
            LOGGER.exception("Failed to relay Alertmanager webhook to ntfy")
            self.send_response(502)
            self.end_headers()
            return

        self.send_response(200)
        self.end_headers()

    def log_message(self, format: str, *args: object) -> None:  # noqa: A002
        LOGGER.info("%s - %s", self.address_string(), format % args)


def main() -> None:
    logging.basicConfig(level=logging.INFO)
    server = ThreadingHTTPServer(("127.0.0.1", LISTEN_PORT), WebhookHandler)
    if LISTEN_HOST_K3S:
        # A second, independent server instance -- not the same one
        # bound twice, since ThreadingHTTPServer/socket.bind only takes
        # one address. Runs in a background thread so the primary
        # (loopback) server below can still own the main thread the
        # same way it always has.
        k3s_server = ThreadingHTTPServer((LISTEN_HOST_K3S, LISTEN_PORT), WebhookHandler)
        threading.Thread(target=k3s_server.serve_forever, daemon=True).start()
    server.serve_forever()


if __name__ == "__main__":
    main()
