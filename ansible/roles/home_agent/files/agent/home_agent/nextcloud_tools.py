"""Client for the restricted read-only Nextcloud tools Unix socket API."""

from __future__ import annotations

import http.client
import json
import socket
from typing import Any

MAX_TOOL_RESPONSE_BYTES = 1024 * 1024

TOOL_PATHS = {
    "list_nextcloud_files": "/v1/list",
    "search_nextcloud_files": "/v1/search",
    "read_nextcloud_text_file": "/v1/read",
}

TOOL_DEFINITIONS = [
    {
        "type": "function",
        "name": "list_nextcloud_files",
        "description": (
            "List files and folders below the approved Nextcloud root. "
            "Use an empty path for the root. This never changes files."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "path": {
                    "type": "string",
                    "description": "Relative folder path, or empty for root.",
                }
            },
            "required": ["path"],
            "additionalProperties": False,
        },
        "strict": True,
    },
    {
        "type": "function",
        "name": "search_nextcloud_files",
        "description": (
            "Search file and folder names below an approved Nextcloud folder. "
            "Returns bounded metadata, not file contents."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "Case-insensitive filename or path fragment.",
                },
                "path": {
                    "type": "string",
                    "description": "Relative folder to search, or empty for root.",
                },
            },
            "required": ["query", "path"],
            "additionalProperties": False,
        },
        "strict": True,
    },
    {
        "type": "function",
        "name": "read_nextcloud_text_file",
        "description": (
            "Read one small approved UTF-8 text file from Nextcloud. "
            "Binary documents, images, and writes are not supported."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "path": {
                    "type": "string",
                    "description": "Relative path of the text file to read.",
                }
            },
            "required": ["path"],
            "additionalProperties": False,
        },
        "strict": True,
    },
]


class NextcloudToolsError(RuntimeError):
    """Indicate that the restricted Nextcloud service could not answer."""


class UnixHTTPConnection(http.client.HTTPConnection):
    def __init__(self, socket_path: str, timeout: float = 15.0) -> None:
        super().__init__("nextcloud-tools", timeout=timeout)
        self.socket_path = socket_path

    def connect(self) -> None:
        self.sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self.sock.settimeout(self.timeout)
        self.sock.connect(self.socket_path)


class NextcloudToolsClient:
    """Call only predeclared read-only Nextcloud tool endpoints."""

    def __init__(self, socket_path: str) -> None:
        self.socket_path = socket_path

    def call(self, tool_name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        path = TOOL_PATHS.get(tool_name)
        if path is None:
            raise NextcloudToolsError("unknown tool")
        if not isinstance(arguments, dict):
            raise NextcloudToolsError("invalid tool arguments")

        body = json.dumps(arguments, separators=(",", ":")).encode("utf-8")
        if len(body) > 4096:
            raise NextcloudToolsError("tool arguments exceeded the size limit")
        connection = UnixHTTPConnection(self.socket_path)
        try:
            connection.request(
                "POST",
                path,
                body=body,
                headers={"Content-Type": "application/json"},
            )
            response = connection.getresponse()
            response_body = response.read(MAX_TOOL_RESPONSE_BYTES + 1)
            if len(response_body) > MAX_TOOL_RESPONSE_BYTES:
                raise NextcloudToolsError("tool response exceeded the size limit")
            if response.status != 200:
                raise NextcloudToolsError("tool is unavailable")
            payload = json.loads(response_body)
            if not isinstance(payload, dict):
                raise NextcloudToolsError("tool returned an unexpected response")
            return payload
        except (OSError, http.client.HTTPException, json.JSONDecodeError) as error:
            raise NextcloudToolsError("tool request failed") from error
        finally:
            connection.close()
