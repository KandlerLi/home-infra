"""Minimal loopback HTTP API for the homeserver agent."""

from __future__ import annotations

import json
import logging
import os
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

from .agent import OpenAIResponsesProvider
from .home_tools import HomeToolsClient

MAX_REQUEST_BYTES = 16 * 1024
MAX_MESSAGE_CHARACTERS = 4_000
LOGGER = logging.getLogger(__name__)


def read_secret(path: str) -> str:
    value = Path(path).read_text(encoding="utf-8").strip()
    if len(value) < 20 or value == "CHANGE_ME":
        raise RuntimeError("OpenAI API key file is not configured")
    return value


def create_provider() -> OpenAIResponsesProvider:
    key_path = os.environ.get("OPENAI_API_KEY_FILE", "/run/secrets/openai_api_key")
    model = os.environ.get("HOME_AGENT_MODEL", "gpt-5.4-mini")
    socket_path = os.environ.get("HOME_TOOLS_SOCKET", "/run/home-tools/home-tools.sock")
    return OpenAIResponsesProvider(
        api_key=read_secret(key_path),
        model=model,
        home_tools=HomeToolsClient(socket_path),
    )


class AgentHTTPServer(ThreadingHTTPServer):
    provider: OpenAIResponsesProvider


class AgentRequestHandler(BaseHTTPRequestHandler):
    server_version = "home-agent/1"

    def do_GET(self) -> None:
        if self.path == "/healthz":
            self._send_json(200, {"status": "ok"})
        else:
            self._send_json(404, {"error": "not_found"})

    def do_POST(self) -> None:
        if self.path != "/v1/chat":
            self._send_json(404, {"error": "not_found"})
            return

        try:
            content_length = int(self.headers.get("Content-Length", "0"))
        except ValueError:
            self._send_json(400, {"error": "invalid_content_length"})
            return

        if content_length <= 0 or content_length > MAX_REQUEST_BYTES:
            self._send_json(413, {"error": "invalid_request_size"})
            return

        try:
            request = json.loads(self.rfile.read(content_length))
        except (UnicodeDecodeError, json.JSONDecodeError):
            self._send_json(400, {"error": "invalid_json"})
            return

        message = request.get("message") if isinstance(request, dict) else None
        if (
            not isinstance(message, str)
            or not message.strip()
            or len(message) > MAX_MESSAGE_CHARACTERS
        ):
            self._send_json(400, {"error": "invalid_message"})
            return

        try:
            answer = self.server.provider.respond(message.strip())
        except Exception as error:  # noqa: BLE001
            # This HTTP boundary deliberately turns every provider failure into
            # one generic response so SDK details and credentials never leak.
            LOGGER.error("Agent request failed: %s", type(error).__name__)
            self._send_json(502, {"error": "agent_unavailable"})
            return

        self._send_json(200, {"answer": answer})

    def _send_json(self, status: int, payload: dict[str, Any]) -> None:
        body = json.dumps(payload, separators=(",", ":")).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, message_format: str, *args: Any) -> None:
        LOGGER.info("home-agent request: " + message_format, *args)


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    server = AgentHTTPServer(("0.0.0.0", 8000), AgentRequestHandler)
    server.provider = create_provider()
    server.serve_forever()


if __name__ == "__main__":
    main()
