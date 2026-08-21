"""Client for the restricted read-only Nextcloud tools Unix socket API."""

from __future__ import annotations

import json
from typing import Any

import httpx2

MAX_TOOL_RESPONSE_BYTES = 1024 * 1024
MAX_ARGUMENT_BYTES = 4096
# propose_nextcloud_write can carry file content; matches the service's own
# MAX_WRITE_BYTES + JSON overhead allowance (nextcloud_tools_service.py).
MAX_WRITE_ARGUMENT_BYTES = 256 * 1024 + 4096

TOOL_PATHS = {
    "list_nextcloud_files": "/v1/list",
    "search_nextcloud_files": "/v1/search",
    "read_nextcloud_text_file": "/v1/read",
    "propose_nextcloud_write": "/v1/propose_write",
    "confirm_nextcloud_write": "/v1/confirm_write",
}

# Tools whose execution must be locally verified against the raw human
# message before being forwarded to the socket -- see agent.py's dispatch
# loop. Never trust the model's own claim that a human approved something.
CONFIRMATION_GATED_TOOLS = frozenset({"confirm_nextcloud_write"})

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
    {
        "type": "function",
        "name": "propose_nextcloud_write",
        "description": (
            "Propose creating, updating, deleting, or moving one approved "
            "text file or folder below the approved Nextcloud root. Does "
            "NOT write anything yet -- it returns a short confirmation code "
            "and a human-readable summary (a diff for updates). You must "
            "show that summary and code to the user verbatim and wait for "
            "them to explicitly respond before calling "
            "confirm_nextcloud_write. Never call confirm_nextcloud_write "
            "without the user having done so in their own message."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "operation": {
                    "type": "string",
                    "enum": ["create", "update", "delete", "move"],
                },
                "path": {
                    "type": "string",
                    "description": "Relative path of the file or folder.",
                },
                "content": {
                    "type": ["string", "null"],
                    "description": (
                        "New file content for create/update. Omit (null) "
                        "when creating a folder, or for delete/move."
                    ),
                },
                "destination_path": {
                    "type": ["string", "null"],
                    "description": "New relative path. Required for move only.",
                },
            },
            "required": ["operation", "path", "content", "destination_path"],
            "additionalProperties": False,
        },
        "strict": True,
    },
    {
        "type": "function",
        "name": "confirm_nextcloud_write",
        "description": (
            "Execute a previously proposed Nextcloud write. Only call this "
            "after the user has explicitly confirmed, in their own message, "
            "the exact confirmation code returned by propose_nextcloud_write. "
            "Calling this without a genuine user confirmation will be "
            "rejected."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "confirmation_code": {
                    "type": "string",
                    "description": (
                        "The code returned by propose_nextcloud_write, as "
                        "confirmed by the user."
                    ),
                }
            },
            "required": ["confirmation_code"],
            "additionalProperties": False,
        },
        "strict": True,
    },
]


class NextcloudToolsError(RuntimeError):
    """Indicate that the restricted Nextcloud service could not answer."""


class NextcloudToolsClient:
    """Call only predeclared Nextcloud tool endpoints."""

    def __init__(self, socket_path: str, timeout: float = 15.0) -> None:
        self.socket_path = socket_path
        self.timeout = timeout

    def call(self, tool_name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        path = TOOL_PATHS.get(tool_name)
        if path is None:
            raise NextcloudToolsError("unknown tool")
        if not isinstance(arguments, dict):
            raise NextcloudToolsError("invalid tool arguments")

        body = json.dumps(arguments, separators=(",", ":")).encode("utf-8")
        max_bytes = (
            MAX_WRITE_ARGUMENT_BYTES
            if tool_name == "propose_nextcloud_write"
            else MAX_ARGUMENT_BYTES
        )
        if len(body) > max_bytes:
            raise NextcloudToolsError("tool arguments exceeded the size limit")

        transport = httpx2.HTTPTransport(uds=self.socket_path)
        try:
            with httpx2.Client(
                transport=transport,
                base_url="http://nextcloud-tools",
                timeout=self.timeout,
            ) as client:
                with client.stream(
                    "POST",
                    path,
                    content=body,
                    headers={"Content-Type": "application/json"},
                ) as response:
                    response_body = bytearray()
                    for chunk in response.iter_bytes():
                        response_body.extend(chunk)
                        if len(response_body) > MAX_TOOL_RESPONSE_BYTES:
                            raise NextcloudToolsError(
                                "tool response exceeded the size limit"
                            )
                    if response.status_code != 200:
                        raise NextcloudToolsError("tool is unavailable")
            payload = json.loads(bytes(response_body))
            if not isinstance(payload, dict):
                raise NextcloudToolsError("tool returned an unexpected response")
            return payload
        except (httpx2.HTTPError, json.JSONDecodeError) as error:
            raise NextcloudToolsError("tool request failed") from error
