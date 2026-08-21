"""Minimal loopback HTTP API for the homeserver agent."""

from __future__ import annotations

import json
import logging
import os
import time
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from .agent import OpenAIResponsesProvider
from .audio import MAX_AUDIO_BYTES, OpenAIAudioTranscriber
from .home_tools import HomeToolsClient
from .nextcloud_tools import NextcloudToolsClient

MODEL_ID = "home-agent"
LEGACY_MAX_REQUEST_BYTES = 16 * 1024
COMPAT_MAX_REQUEST_BYTES = 64 * 1024
MAX_MESSAGE_CHARACTERS = 4_000
MAX_CONVERSATION_MESSAGES = 24
MAX_CONVERSATION_CHARACTERS = 24_000
LOGGER = logging.getLogger(__name__)


class _ChunkedBodyTooLarge(Exception):
    """Indicate that a chunked request body exceeded its size cap."""


def read_secret(path: str) -> str:
    value = Path(path).read_text(encoding="utf-8").strip()
    if len(value) < 20 or value == "CHANGE_ME":
        raise RuntimeError("OpenAI API key file is not configured")
    return value


def create_provider() -> OpenAIResponsesProvider:
    key_path = os.environ.get("OPENAI_API_KEY_FILE", "/run/secrets/openai_api_key")
    model = os.environ.get("HOME_AGENT_MODEL", "gpt-5.4-mini")
    socket_path = os.environ.get("HOME_TOOLS_SOCKET", "/run/home-tools/home-tools.sock")
    nextcloud_socket_path = os.environ.get("NEXTCLOUD_TOOLS_SOCKET")
    return OpenAIResponsesProvider(
        api_key=read_secret(key_path),
        model=model,
        home_tools=HomeToolsClient(socket_path),
        nextcloud_tools=(
            NextcloudToolsClient(nextcloud_socket_path)
            if nextcloud_socket_path
            else None
        ),
    )


def create_transcriber() -> OpenAIAudioTranscriber:
    key_path = os.environ.get("OPENAI_API_KEY_FILE", "/run/secrets/openai_api_key")
    model = os.environ.get("HOME_AGENT_STT_MODEL", "whisper-1")
    return OpenAIAudioTranscriber(api_key=read_secret(key_path), model=model)


class AgentHTTPServer(ThreadingHTTPServer):
    provider: OpenAIResponsesProvider
    transcriber: OpenAIAudioTranscriber


class AgentRequestHandler(BaseHTTPRequestHandler):
    server_version = "home-agent/1"

    def do_GET(self) -> None:
        path = urlsplit(self.path).path
        if path == "/healthz":
            self._send_json(200, {"status": "ok"})
        elif path == "/v1/models":
            self._send_json(
                200,
                {
                    "object": "list",
                    "data": [
                        {
                            "id": MODEL_ID,
                            "object": "model",
                            "created": 0,
                            "owned_by": "home-infra",
                        }
                    ],
                },
            )
        else:
            self._send_json(404, {"error": "not_found"})

    def do_POST(self) -> None:
        path = urlsplit(self.path).path
        if path == "/v1/chat":
            self._handle_legacy_chat()
        elif path == "/v1/chat/completions":
            self._handle_chat_completions()
        elif path == "/v1/audio/transcriptions":
            self._handle_audio_transcriptions()
        else:
            self._send_json(404, {"error": "not_found"})

    def _handle_legacy_chat(self) -> None:
        request = self._read_json_request(LEGACY_MAX_REQUEST_BYTES)
        if request is None:
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
            self._log_provider_failure(error)
            self._send_json(502, {"error": "agent_unavailable"})
            return

        self._send_json(200, {"answer": answer})

    def _handle_chat_completions(self) -> None:
        request = self._read_json_request(COMPAT_MAX_REQUEST_BYTES)
        if request is None:
            return

        if not isinstance(request, dict) or request.get("model") != MODEL_ID:
            self._send_openai_error(400, "invalid_model")
            return

        messages = normalize_conversation(request.get("messages"))
        if messages is None:
            self._send_openai_error(400, "invalid_messages")
            return

        stream = request.get("stream", False)
        if not isinstance(stream, bool):
            self._send_openai_error(400, "invalid_stream")
            return

        try:
            answer = self.server.provider.respond(messages)
        except Exception as error:  # noqa: BLE001
            self._log_provider_failure(error)
            self._send_openai_error(502, "agent_unavailable")
            return

        completion_id = f"chatcmpl-{uuid.uuid4().hex}"
        created = int(time.time())
        if stream:
            self._send_chat_completion_stream(completion_id, created, answer)
            return

        self._send_json(
            200,
            {
                "id": completion_id,
                "object": "chat.completion",
                "created": created,
                "model": MODEL_ID,
                "choices": [
                    {
                        "index": 0,
                        "message": {"role": "assistant", "content": answer},
                        "finish_reason": "stop",
                    }
                ],
            },
        )

    def _read_chunked_body(self, max_bytes: int) -> bytes:
        """Read an HTTP/1.1 chunked-encoded request body, capped at max_bytes.

        Raises _ChunkedBodyTooLarge if the body would exceed max_bytes, or
        ValueError for anything malformed (bad chunk-size line, a chunk
        shorter than declared, a missing chunk terminator).
        """
        body = bytearray()
        while True:
            size_line = self.rfile.readline(64)
            if not size_line:
                raise ValueError("truncated chunked body")
            chunk_size = int(size_line.split(b";", 1)[0].strip(), 16)
            if chunk_size == 0:
                # Consume any trailer headers up to the terminating blank line.
                while True:
                    trailer_line = self.rfile.readline(64)
                    if not trailer_line or trailer_line in (b"\r\n", b"\n"):
                        break
                return bytes(body)
            if len(body) + chunk_size > max_bytes:
                raise _ChunkedBodyTooLarge()
            chunk = self.rfile.read(chunk_size)
            if len(chunk) != chunk_size or self.rfile.read(2) != b"\r\n":
                raise ValueError("malformed chunk")
            body.extend(chunk)

    def _handle_audio_transcriptions(self) -> None:
        content_type = self.headers.get("Content-Type", "")

        # Open WebUI streams the recorded file through aiohttp from an
        # async generator, so it can't know the body's total size upfront
        # and sends Transfer-Encoding: chunked with no Content-Length at
        # all (confirmed against its actual FormData behavior) -- handle
        # both shapes rather than assuming a fixed Content-Length always
        # exists like the JSON endpoints can.
        if self.headers.get("Transfer-Encoding", "").lower() == "chunked":
            try:
                body = self._read_chunked_body(MAX_AUDIO_BYTES)
            except _ChunkedBodyTooLarge:
                self._send_openai_error(413, "invalid_request_size")
                return
            except ValueError:
                self._send_openai_error(400, "invalid_request")
                return
        else:
            try:
                content_length = int(self.headers.get("Content-Length", "0"))
            except ValueError:
                self._send_openai_error(400, "invalid_content_length")
                return
            if content_length <= 0 or content_length > MAX_AUDIO_BYTES:
                self._send_openai_error(413, "invalid_request_size")
                return
            body = self.rfile.read(content_length)

        try:
            result = self.server.transcriber.transcribe(body, content_type)
        except Exception as error:  # noqa: BLE001
            self._log_provider_failure(error)
            self._send_openai_error(502, "agent_unavailable")
            return

        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(result)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(result)

    def _read_json_request(self, max_request_bytes: int) -> Any | None:
        content_length_header = self.headers.get("Content-Length", "0")

        try:
            content_length = int(content_length_header)
        except ValueError:
            self._send_json(400, {"error": "invalid_content_length"})
            return None

        if content_length <= 0 or content_length > max_request_bytes:
            self._send_json(413, {"error": "invalid_request_size"})
            return None

        try:
            return json.loads(self.rfile.read(content_length))
        except (UnicodeDecodeError, json.JSONDecodeError):
            self._send_json(400, {"error": "invalid_json"})
            return None

    def _send_chat_completion_stream(
        self, completion_id: str, created: int, answer: str
    ) -> None:
        chunks = [
            {
                "id": completion_id,
                "object": "chat.completion.chunk",
                "created": created,
                "model": MODEL_ID,
                "choices": [
                    {
                        "index": 0,
                        "delta": {"role": "assistant", "content": answer},
                        "finish_reason": None,
                    }
                ],
            },
            {
                "id": completion_id,
                "object": "chat.completion.chunk",
                "created": created,
                "model": MODEL_ID,
                "choices": [
                    {"index": 0, "delta": {}, "finish_reason": "stop"}
                ],
            },
        ]
        body = b"".join(
            b"data: "
            + json.dumps(chunk, separators=(",", ":")).encode("utf-8")
            + b"\n\n"
            for chunk in chunks
        ) + b"data: [DONE]\n\n"
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-cache, no-store")
        self.send_header("X-Accel-Buffering", "no")
        self.end_headers()
        self.wfile.write(body)

    def _send_openai_error(self, status: int, code: str) -> None:
        self._send_json(
            status,
            {
                "error": {
                    "message": code,
                    "type": "invalid_request_error"
                    if status < 500
                    else "server_error",
                    "code": code,
                }
            },
        )

    def _log_provider_failure(self, error: Exception) -> None:
        # This HTTP boundary deliberately logs only the exception type so SDK
        # details, request contents, and credentials never leak.
        LOGGER.error("Agent request failed: %s", type(error).__name__)

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


def normalize_conversation(value: Any) -> list[dict[str, str]] | None:
    if not isinstance(value, list) or not value:
        return None
    if len(value) > MAX_CONVERSATION_MESSAGES:
        return None

    messages: list[dict[str, str]] = []
    total_characters = 0
    for item in value:
        if not isinstance(item, dict):
            return None
        role = item.get("role")
        content = item.get("content")

        # Open WebUI may prepend its own system text. The home-agent keeps its
        # fixed server-side instructions and never accepts a caller-supplied
        # replacement for that safety boundary.
        if role in {"system", "developer"}:
            continue
        if role not in {"user", "assistant"} or not isinstance(content, str):
            return None

        normalized_content = content.strip()
        if not normalized_content or len(normalized_content) > MAX_MESSAGE_CHARACTERS:
            return None
        total_characters += len(normalized_content)
        if total_characters > MAX_CONVERSATION_CHARACTERS:
            return None
        messages.append({"role": role, "content": normalized_content})

    if not messages or messages[-1]["role"] != "user":
        return None
    return messages


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    server = AgentHTTPServer(("0.0.0.0", 8000), AgentRequestHandler)
    server.provider = create_provider()
    server.transcriber = create_transcriber()
    server.serve_forever()


if __name__ == "__main__":
    main()
