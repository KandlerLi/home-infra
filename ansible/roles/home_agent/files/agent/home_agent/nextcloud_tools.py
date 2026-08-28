"""Client for the restricted read-only Nextcloud tools Unix socket API."""

from __future__ import annotations

import json
from typing import Any

from .unix_socket_client import call_unix_socket_json

MAX_TOOL_RESPONSE_BYTES = 1024 * 1024
MAX_ARGUMENT_BYTES = 4096
# write_nextcloud_file can carry file content; matches the service's own
# MAX_WRITE_BYTES + JSON overhead allowance (nextcloud_tools_service.py).
MAX_WRITE_ARGUMENT_BYTES = 256 * 1024 + 4096

TOOL_PATHS = {
    "list_nextcloud_files": "/v1/list",
    "search_nextcloud_files": "/v1/search",
    "read_nextcloud_text_file": "/v1/read",
    "write_nextcloud_file": "/v1/write",
    "list_shopping_lists": "/v1/shopping/lists",
    "list_shopping_list_items": "/v1/shopping/items",
    "update_shopping_list": "/v1/shopping/write",
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
    {
        "type": "function",
        "name": "write_nextcloud_file",
        "description": (
            "Create, update, delete, or move one approved text file or "
            "folder below the approved Nextcloud root. This writes "
            "immediately -- tell the user what you did after it succeeds, "
            "don't ask for permission first unless the user's own request "
            "was ambiguous about what to write."
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
        "name": "list_shopping_lists",
        "description": (
            "List the names of Nextcloud Shopping List lists shared with "
            "the agent. Use this when the user has more than one list and "
            "it's unclear which one they mean."
        ),
        "parameters": {
            "type": "object",
            "properties": {},
            "required": [],
            "additionalProperties": False,
        },
        "strict": True,
    },
    {
        "type": "function",
        "name": "list_shopping_list_items",
        "description": (
            "List the items currently on one Nextcloud Shopping List, "
            "including whether each is checked off. This never changes "
            "the list."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "list": {
                    "type": ["string", "null"],
                    "description": (
                        "List title. Omit (null) if the user has only one "
                        "shopping list."
                    ),
                }
            },
            "required": ["list"],
            "additionalProperties": False,
        },
        "strict": True,
    },
    {
        "type": "function",
        "name": "update_shopping_list",
        "description": (
            "Add an item to a Nextcloud Shopping List, mark an existing "
            "item bought/not bought, or remove an item. This writes "
            "immediately -- tell the user what you did after it succeeds, "
            "don't ask for permission first unless the request was "
            "ambiguous about which item or list was meant."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "operation": {
                    "type": "string",
                    "enum": ["add", "check", "uncheck", "remove"],
                },
                "list": {
                    "type": ["string", "null"],
                    "description": (
                        "List title. Omit (null) if the user has only one "
                        "shopping list."
                    ),
                },
                "item": {
                    "type": "string",
                    "description": "Item name, e.g. 'milk'.",
                },
                "quantity": {
                    "type": ["string", "null"],
                    "description": (
                        "Optional free-text quantity, e.g. '2' or '1 dozen'. "
                        "Only valid for add; omit (null) otherwise."
                    ),
                },
            },
            "required": ["operation", "list", "item", "quantity"],
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
            if tool_name == "write_nextcloud_file"
            else MAX_ARGUMENT_BYTES
        )
        if len(body) > max_bytes:
            raise NextcloudToolsError("tool arguments exceeded the size limit")

        return call_unix_socket_json(
            socket_path=self.socket_path,
            base_url="http://nextcloud-tools",
            method="POST",
            path=path,
            timeout=self.timeout,
            max_response_bytes=MAX_TOOL_RESPONSE_BYTES,
            error_cls=NextcloudToolsError,
            body=body,
            headers={"Content-Type": "application/json"},
        )
