"""Expose bounded Nextcloud WebDAV file and Shopping List operations over a Unix socket."""

from __future__ import annotations

import base64
import http.client
import json
import logging
import os
import posixpath
import re
import socket
import socketserver
import xml.etree.ElementTree as ET
from collections import deque
from dataclasses import dataclass
from difflib import unified_diff
from http.server import BaseHTTPRequestHandler
from pathlib import Path, PurePosixPath
from typing import Any
from urllib.parse import quote, unquote, urlsplit

LOGGER = logging.getLogger(__name__)
SOCKET_PATH = os.environ.get(
    "NEXTCLOUD_TOOLS_SOCKET", "/run/nextcloud-tools/nextcloud-tools.sock"
)
ENDPOINT_HOST = os.environ.get("NEXTCLOUD_ENDPOINT_HOST", "127.0.0.1")
ENDPOINT_PORT = int(os.environ.get("NEXTCLOUD_ENDPOINT_PORT", "11000"))
HTTP_HOST = os.environ.get("NEXTCLOUD_HTTP_HOST", "nextcloud.jkandler.de")
USERNAME = os.environ.get("NEXTCLOUD_USERNAME", "")
APP_PASSWORD_FILE = os.environ.get(
    "NEXTCLOUD_APP_PASSWORD_FILE", "/etc/nextcloud-tools/app-password"
)
ALLOWED_ROOT = os.environ.get("NEXTCLOUD_ALLOWED_ROOT", "AI Workspace")
MAX_REQUEST_BYTES = int(os.environ.get("NEXTCLOUD_MAX_REQUEST_BYTES", "8192"))
MAX_RESPONSE_BYTES = int(
    os.environ.get("NEXTCLOUD_MAX_RESPONSE_BYTES", str(2 * 1024 * 1024))
)
MAX_READ_BYTES = int(os.environ.get("NEXTCLOUD_MAX_READ_BYTES", str(256 * 1024)))
MAX_RESULTS = int(os.environ.get("NEXTCLOUD_MAX_RESULTS", "50"))
MAX_SCAN_ENTRIES = int(os.environ.get("NEXTCLOUD_MAX_SCAN_ENTRIES", "500"))
MAX_SCAN_DEPTH = int(os.environ.get("NEXTCLOUD_MAX_SCAN_DEPTH", "4"))
MAX_WRITE_BYTES = int(os.environ.get("NEXTCLOUD_MAX_WRITE_BYTES", str(256 * 1024)))
MAX_SUMMARY_CHARS = int(os.environ.get("NEXTCLOUD_MAX_SUMMARY_CHARS", "4000"))
MAX_ITEM_NAME_CHARS = int(os.environ.get("NEXTCLOUD_MAX_ITEM_NAME_CHARS", "200"))
MAX_QUANTITY_CHARS = int(os.environ.get("NEXTCLOUD_MAX_QUANTITY_CHARS", "32"))

# Scope for the Shopping List app is enforced entirely by Nextcloud's own
# list sharing (the dedicated account only ever sees lists shared with it),
# unlike WebDAV, which needs the ALLOWED_ROOT restriction above because an
# account's DAV namespace always exposes its whole home directory tree. So
# there is no allowed-list config to add here -- see ADR 0014.
SHOPPING_LIST_APP_ID = "shopping_list"

DAV = "DAV:"
NC = "http://nextcloud.org/ns"
OC = "http://owncloud.org/ns"
PROPFIND_BODY = b"""<?xml version="1.0" encoding="UTF-8"?>
<d:propfind xmlns:d="DAV:" xmlns:oc="http://owncloud.org/ns" xmlns:nc="http://nextcloud.org/ns">
  <d:prop>
    <d:getlastmodified/><d:getcontentlength/><d:getcontenttype/>
    <d:resourcetype/><d:getetag/><oc:fileid/><oc:permissions/>
    <nc:has-preview/>
  </d:prop>
</d:propfind>
"""
READABLE_EXTENSIONS = frozenset(
    {".txt", ".md", ".csv", ".json", ".yaml", ".yml", ".log"}
)


class ToolUnavailable(RuntimeError):
    """Indicate that Nextcloud could not safely answer a tool request."""


def safe_unavailable_reason(error: ToolUnavailable) -> str:
    """Return only allowlisted diagnostic categories to the socket client."""
    message = str(error)
    status = re.fullmatch(
        r"Nextcloud directory listing returned HTTP ([1-5][0-9]{2})",
        message,
    )
    if status:
        return f"upstream_http_{status.group(1)}"
    transport = re.fullmatch(
        r"Nextcloud request failed \(([A-Za-z][A-Za-z0-9_]{0,63})\)",
        message,
    )
    if transport:
        return f"upstream_transport_{transport.group(1)}"
    return "upstream_response_invalid"


class InvalidToolRequest(ValueError):
    """Indicate invalid caller-controlled tool arguments."""


@dataclass(frozen=True)
class FileEntry:
    path: str
    name: str
    kind: str
    size_bytes: int | None
    modified: str | None
    content_type: str | None
    etag: str | None
    file_id: str | None
    permissions: str | None
    has_preview: bool

    def as_dict(self) -> dict[str, Any]:
        return {
            "path": self.path,
            "name": self.name,
            "kind": self.kind,
            "size_bytes": self.size_bytes,
            "modified": self.modified,
            "content_type": self.content_type,
            "etag": self.etag,
            "file_id": self.file_id,
            "permissions": self.permissions,
            "has_preview": self.has_preview,
        }


def normalize_relative_path(value: Any, *, allow_empty: bool = True) -> str:
    """Accept only a bounded relative POSIX path below the configured root."""
    if not isinstance(value, str):
        raise InvalidToolRequest("path must be a string")
    if len(value) > 1024 or "\x00" in value or "\\" in value:
        raise InvalidToolRequest("path is invalid")
    stripped = value.strip().strip("/")
    if not stripped:
        if allow_empty:
            return ""
        raise InvalidToolRequest("path is required")
    path = PurePosixPath(stripped)
    if path.is_absolute() or any(part in {"", ".", ".."} for part in path.parts):
        raise InvalidToolRequest("path must stay below the allowed root")
    return path.as_posix()


def require_string(payload: dict[str, Any], key: str, *, max_length: int) -> str:
    value = payload.get(key)
    if not isinstance(value, str) or not value.strip() or len(value) > max_length:
        raise InvalidToolRequest(f"{key} is invalid")
    return value.strip()


def _basic_auth_header(username: str, app_password: str) -> str:
    encoded = base64.b64encode(f"{username}:{app_password}".encode("utf-8")).decode(
        "ascii"
    )
    return f"Basic {encoded}"


def _send_http_request(
    host: str,
    port: int,
    timeout: float,
    method: str,
    request_path: str,
    headers: dict[str, str],
    body: bytes | None,
    max_bytes: int,
) -> tuple[int, dict[str, str], bytes]:
    """Send one request and read a size-capped response, wrapping transport errors."""
    connection = http.client.HTTPConnection(host, port, timeout=timeout)
    try:
        connection.request(method, request_path, body=body, headers=headers)
        response = connection.getresponse()
        response_headers = {key.lower(): value for key, value in response.getheaders()}
        response_body = response.read(max_bytes + 1)
        if len(response_body) > max_bytes:
            raise ToolUnavailable("Nextcloud response exceeded the size limit")
        return response.status, response_headers, response_body
    except (OSError, http.client.HTTPException) as error:
        raise ToolUnavailable(
            f"Nextcloud request failed ({type(error).__name__})"
        ) from error
    finally:
        connection.close()


class NextcloudWebDAV:
    """Minimal WebDAV client fixed to one user and one allowed root folder."""

    def __init__(
        self,
        host: str,
        port: int,
        http_host: str,
        username: str,
        app_password: str,
        allowed_root: str,
        *,
        timeout: float = 10.0,
    ) -> None:
        self.host = host
        self.port = port
        self.http_host = http_host
        self.username = username
        self.app_password = app_password
        self.allowed_root = normalize_relative_path(allowed_root, allow_empty=False)
        self.timeout = timeout
        self._decoded_root_path = (
            f"/remote.php/dav/files/{username}/{self.allowed_root}"
        )

    def _webdav_path(self, relative_path: str) -> str:
        segments = [self.username, *self.allowed_root.split("/")]
        if relative_path:
            segments.extend(relative_path.split("/"))
        return "/remote.php/dav/files/" + "/".join(quote(part, safe="") for part in segments)

    def _request(
        self,
        method: str,
        relative_path: str,
        *,
        body: bytes | None = None,
        headers: dict[str, str] | None = None,
        max_bytes: int = MAX_RESPONSE_BYTES,
    ) -> tuple[int, dict[str, str], bytes]:
        request_headers = {
            "Authorization": _basic_auth_header(self.username, self.app_password),
            "Connection": "close",
            "Host": self.http_host,
            "User-Agent": "home-infra-nextcloud-tools/1",
        }
        if headers:
            request_headers.update(headers)
        return _send_http_request(
            self.host,
            self.port,
            self.timeout,
            method,
            self._webdav_path(relative_path),
            request_headers,
            body,
            max_bytes,
        )

    def list_directory(self, relative_path: str) -> list[FileEntry]:
        relative_path = normalize_relative_path(relative_path)
        status, _, body = self._request(
            "PROPFIND",
            relative_path,
            body=PROPFIND_BODY,
            headers={"Content-Type": "application/xml", "Depth": "1"},
        )
        if status != 207:
            raise ToolUnavailable(
                f"Nextcloud directory listing returned HTTP {status}"
            )
        entries = parse_multistatus(body, self._decoded_root_path)
        requested = relative_path.rstrip("/")
        return [entry for entry in entries if entry.path.rstrip("/") != requested]

    def stat(self, relative_path: str) -> FileEntry:
        relative_path = normalize_relative_path(relative_path, allow_empty=False)
        status, _, body = self._request(
            "PROPFIND",
            relative_path,
            body=PROPFIND_BODY,
            headers={"Content-Type": "application/xml", "Depth": "0"},
        )
        if status != 207:
            raise ToolUnavailable("Nextcloud file metadata is unavailable")
        entries = parse_multistatus(body, self._decoded_root_path)
        if len(entries) != 1:
            raise ToolUnavailable("Nextcloud returned unexpected file metadata")
        return entries[0]

    def read_text_file(self, relative_path: str) -> dict[str, Any]:
        relative_path = normalize_relative_path(relative_path, allow_empty=False)
        extension = PurePosixPath(relative_path).suffix.lower()
        if extension not in READABLE_EXTENSIONS:
            raise InvalidToolRequest("file type is not approved for text reading")
        entry = self.stat(relative_path)
        if entry.kind != "file":
            raise InvalidToolRequest("path is not a file")
        if entry.size_bytes is not None and entry.size_bytes > MAX_READ_BYTES:
            raise InvalidToolRequest("file exceeds the read size limit")
        status, headers, body = self._request(
            "GET", relative_path, max_bytes=MAX_READ_BYTES
        )
        if status != 200:
            raise ToolUnavailable("Nextcloud file read failed")
        content_type = headers.get("content-type", entry.content_type or "")
        if not (
            content_type.startswith("text/")
            or content_type.split(";", 1)[0]
            in {"application/json", "application/yaml", "application/x-yaml"}
        ):
            raise InvalidToolRequest("file content type is not approved")
        try:
            content = body.decode("utf-8")
        except UnicodeDecodeError as error:
            raise InvalidToolRequest("file is not valid UTF-8 text") from error
        result = entry.as_dict()
        result["content"] = content
        return result

    def search(self, query: str, relative_path: str = "") -> dict[str, Any]:
        query = query.strip().casefold()
        if not query or len(query) > 200:
            raise InvalidToolRequest("query is invalid")
        start_path = normalize_relative_path(relative_path)
        queue: deque[tuple[str, int]] = deque([(start_path, 0)])
        matches: list[dict[str, Any]] = []
        scanned = 0
        truncated = False

        while queue:
            folder, depth = queue.popleft()
            for entry in self.list_directory(folder):
                scanned += 1
                if scanned > MAX_SCAN_ENTRIES:
                    truncated = True
                    queue.clear()
                    break
                if query in entry.path.casefold():
                    matches.append(entry.as_dict())
                    if len(matches) >= MAX_RESULTS:
                        truncated = True
                        queue.clear()
                        break
                if entry.kind == "folder" and depth + 1 < MAX_SCAN_DEPTH:
                    queue.append((entry.path, depth + 1))
            if truncated:
                break

        return {
            "query": query,
            "root": start_path,
            "matches": matches,
            "scanned_entries": min(scanned, MAX_SCAN_ENTRIES),
            "truncated": truncated,
        }

    def exists(self, relative_path: str) -> bool:
        """Check existence without conflating "not found" with a real error."""
        relative_path = normalize_relative_path(relative_path, allow_empty=False)
        status, _, _ = self._request(
            "PROPFIND",
            relative_path,
            body=PROPFIND_BODY,
            headers={"Content-Type": "application/xml", "Depth": "0"},
        )
        if status == 404:
            return False
        if status == 207:
            return True
        raise ToolUnavailable(f"Nextcloud existence check returned HTTP {status}")

    def _absolute_url(self, relative_path: str) -> str:
        return f"http://{self.http_host}{self._webdav_path(relative_path)}"

    def create_file(self, relative_path: str, content: str) -> FileEntry:
        relative_path = normalize_relative_path(relative_path, allow_empty=False)
        extension = PurePosixPath(relative_path).suffix.lower()
        if extension not in READABLE_EXTENSIONS:
            raise InvalidToolRequest("file type is not approved for writing")
        body = content.encode("utf-8")
        if len(body) > MAX_WRITE_BYTES:
            raise InvalidToolRequest("content exceeds the write size limit")
        status, _, _ = self._request(
            "PUT",
            relative_path,
            body=body,
            headers={
                "Content-Type": "text/plain; charset=utf-8",
                "If-None-Match": "*",
            },
        )
        if status == 412:
            raise ToolUnavailable("Nextcloud write conflict: file already exists")
        if status not in (200, 201, 204):
            raise ToolUnavailable(f"Nextcloud file create returned HTTP {status}")
        return self.stat(relative_path)

    def mkcol(self, relative_path: str) -> FileEntry:
        relative_path = normalize_relative_path(relative_path, allow_empty=False)
        status, _, _ = self._request("MKCOL", relative_path)
        if status == 405:
            raise ToolUnavailable("Nextcloud write conflict: folder already exists")
        if status not in (200, 201):
            raise ToolUnavailable(f"Nextcloud folder create returned HTTP {status}")
        return self.stat(relative_path)

    def update_file(
        self, relative_path: str, content: str, expected_etag: str | None
    ) -> FileEntry:
        relative_path = normalize_relative_path(relative_path, allow_empty=False)
        extension = PurePosixPath(relative_path).suffix.lower()
        if extension not in READABLE_EXTENSIONS:
            raise InvalidToolRequest("file type is not approved for writing")
        body = content.encode("utf-8")
        if len(body) > MAX_WRITE_BYTES:
            raise InvalidToolRequest("content exceeds the write size limit")
        headers = {"Content-Type": "text/plain; charset=utf-8"}
        if expected_etag:
            headers["If-Match"] = expected_etag
        status, _, _ = self._request("PUT", relative_path, body=body, headers=headers)
        if status == 412:
            raise ToolUnavailable(
                "Nextcloud write conflict: file changed since it was proposed"
            )
        if status not in (200, 201, 204):
            raise ToolUnavailable(f"Nextcloud file update returned HTTP {status}")
        return self.stat(relative_path)

    def delete(self, relative_path: str, expected_etag: str | None) -> None:
        relative_path = normalize_relative_path(relative_path, allow_empty=False)
        entry = self.stat(relative_path)
        if entry.kind == "folder" and self.list_directory(relative_path):
            raise InvalidToolRequest(
                "folder is not empty; delete its contents first"
            )
        headers = {}
        if expected_etag:
            headers["If-Match"] = expected_etag
        status, _, _ = self._request("DELETE", relative_path, headers=headers)
        if status == 412:
            raise ToolUnavailable(
                "Nextcloud write conflict: item changed since it was proposed"
            )
        if status not in (200, 204):
            raise ToolUnavailable(f"Nextcloud delete returned HTTP {status}")

    def move(
        self,
        relative_path: str,
        destination_path: str,
        expected_etag: str | None,
    ) -> FileEntry:
        relative_path = normalize_relative_path(relative_path, allow_empty=False)
        destination_path = normalize_relative_path(destination_path, allow_empty=False)
        destination_extension = PurePosixPath(destination_path).suffix.lower()
        if destination_extension and destination_extension not in READABLE_EXTENSIONS:
            raise InvalidToolRequest("destination file type is not approved")
        headers = {
            "Destination": self._absolute_url(destination_path),
            "Overwrite": "F",
        }
        if expected_etag:
            headers["If-Match"] = expected_etag
        status, _, _ = self._request("MOVE", relative_path, headers=headers)
        if status == 412:
            raise ToolUnavailable(
                "Nextcloud write conflict: source changed or destination exists"
            )
        if status not in (200, 201, 204):
            raise ToolUnavailable(f"Nextcloud move returned HTTP {status}")
        return self.stat(destination_path)


def parse_multistatus(body: bytes, decoded_root_path: str) -> list[FileEntry]:
    """Parse only the approved WebDAV metadata fields."""
    try:
        root = ET.fromstring(body)
    except ET.ParseError as error:
        raise ToolUnavailable("Nextcloud returned invalid WebDAV metadata") from error

    root_prefix = decoded_root_path.rstrip("/")
    entries: list[FileEntry] = []
    for response in root.findall(f"{{{DAV}}}response"):
        href_element = response.find(f"{{{DAV}}}href")
        if href_element is None or not href_element.text:
            continue
        decoded_path = unquote(urlsplit(href_element.text).path).rstrip("/")
        if decoded_path != root_prefix and not decoded_path.startswith(root_prefix + "/"):
            raise ToolUnavailable("Nextcloud returned a path outside the allowed root")
        relative_path = decoded_path[len(root_prefix) :].lstrip("/")
        prop = None
        for propstat in response.findall(f"{{{DAV}}}propstat"):
            status = propstat.findtext(f"{{{DAV}}}status", "")
            if " 200 " in status:
                prop = propstat.find(f"{{{DAV}}}prop")
                break
        if prop is None:
            continue
        resource_type = prop.find(f"{{{DAV}}}resourcetype")
        is_folder = (
            resource_type is not None
            and resource_type.find(f"{{{DAV}}}collection") is not None
        )
        size_text = prop.findtext(f"{{{DAV}}}getcontentlength")
        try:
            size = int(size_text) if size_text is not None else None
        except ValueError:
            size = None
        entries.append(
            FileEntry(
                path=relative_path,
                name=(
                    posixpath.basename(relative_path)
                    if relative_path
                    else posixpath.basename(root_prefix)
                ),
                kind="folder" if is_folder else "file",
                size_bytes=size,
                modified=prop.findtext(f"{{{DAV}}}getlastmodified"),
                content_type=prop.findtext(f"{{{DAV}}}getcontenttype"),
                etag=prop.findtext(f"{{{DAV}}}getetag"),
                file_id=prop.findtext(f"{{{OC}}}fileid"),
                permissions=prop.findtext(f"{{{OC}}}permissions"),
                has_preview=prop.findtext(f"{{{NC}}}has-preview", "false").lower()
                == "true",
            )
        )
    return entries


class NextcloudShoppingList:
    """Minimal OCS JSON client for the Shopping List app, fixed to one user."""

    def __init__(
        self,
        host: str,
        port: int,
        http_host: str,
        username: str,
        app_password: str,
        *,
        timeout: float = 10.0,
    ) -> None:
        self.host = host
        self.port = port
        self.http_host = http_host
        self.username = username
        self.app_password = app_password
        self.timeout = timeout
        self._base_path = f"/ocs/v2.php/apps/{SHOPPING_LIST_APP_ID}/api/v1"

    def _request(
        self,
        method: str,
        path: str,
        *,
        json_body: dict[str, Any] | None = None,
        max_bytes: int = MAX_RESPONSE_BYTES,
    ) -> Any:
        headers = {
            "Authorization": _basic_auth_header(self.username, self.app_password),
            "Connection": "close",
            "Host": self.http_host,
            "User-Agent": "home-infra-nextcloud-tools/1",
            "OCS-APIREQUEST": "true",
            "Accept": "application/json",
        }
        body = None
        if json_body is not None:
            body = json.dumps(json_body).encode("utf-8")
            headers["Content-Type"] = "application/json"
        separator = "&" if "?" in path else "?"
        request_path = f"{self._base_path}{path}{separator}format=json"
        status, _, response_body = _send_http_request(
            self.host,
            self.port,
            self.timeout,
            method,
            request_path,
            headers,
            body,
            max_bytes,
        )
        if status == 403:
            raise ToolUnavailable(
                "Nextcloud shopping list write was forbidden "
                "(check the list share's edit permission)"
            )
        if status == 404:
            raise ToolUnavailable("Nextcloud shopping list or item was not found")
        if status not in (200, 201, 204):
            raise ToolUnavailable(
                f"Nextcloud shopping list request returned HTTP {status}"
            )
        if status == 204 or not response_body:
            return None
        try:
            envelope = json.loads(response_body)
            return envelope["ocs"]["data"]
        except (json.JSONDecodeError, KeyError, TypeError) as error:
            raise ToolUnavailable(
                "Nextcloud shopping list returned invalid data"
            ) from error

    def list_lists(self) -> list[dict[str, Any]]:
        data = self._request("GET", "/lists")
        if not isinstance(data, list):
            raise ToolUnavailable(
                "Nextcloud returned an unexpected list of shopping lists"
            )
        return data

    def list_items(self, list_id: int) -> list[dict[str, Any]]:
        data = self._request("GET", f"/lists/{list_id}/items")
        if not isinstance(data, list):
            raise ToolUnavailable(
                "Nextcloud returned an unexpected list of shopping list items"
            )
        return data

    def create_item(
        self, list_id: int, name: str, quantity: str | None
    ) -> dict[str, Any]:
        body: dict[str, Any] = {"name": name}
        if quantity is not None:
            body["quantity"] = quantity
        data = self._request("POST", f"/lists/{list_id}/items", json_body=body)
        if not isinstance(data, dict):
            raise ToolUnavailable("Nextcloud returned an unexpected shopping list item")
        return data

    def set_item_checked(
        self, list_id: int, item_id: int, checked: bool
    ) -> dict[str, Any]:
        data = self._request(
            "PUT",
            f"/lists/{list_id}/items/{item_id}/check",
            json_body={"checked": checked},
        )
        if not isinstance(data, dict):
            raise ToolUnavailable("Nextcloud returned an unexpected shopping list item")
        return data

    def delete_item(self, list_id: int, item_id: int) -> None:
        self._request("DELETE", f"/lists/{list_id}/items/{item_id}")


def resolve_shopping_list(
    client: NextcloudShoppingList, name: Any
) -> tuple[int, str]:
    """Resolve a caller-supplied list name to (id, title), or the sole list."""
    if name is not None and not isinstance(name, str):
        raise InvalidToolRequest("list must be a string")
    lists = client.list_lists()
    titles = [
        (entry.get("id"), entry.get("title", ""))
        for entry in lists
        if isinstance(entry, dict)
    ]

    if name:
        name = name.strip()
        if not name or len(name) > MAX_ITEM_NAME_CHARS:
            raise InvalidToolRequest("list is invalid")
        matches = [
            (list_id, title)
            for list_id, title in titles
            if isinstance(title, str) and title.casefold() == name.casefold()
        ]
        if not matches:
            available = ", ".join(repr(title) for _, title in titles[:MAX_RESULTS])
            raise InvalidToolRequest(
                f"no shopping list named {name!r} is shared with the agent"
                + (f"; available: {available}" if available else "")
            )
        list_id, title = matches[0]
    elif not titles:
        raise InvalidToolRequest("no shopping lists are shared with the agent yet")
    elif len(titles) > 1:
        available = ", ".join(repr(title) for _, title in titles[:MAX_RESULTS])
        raise InvalidToolRequest(
            f"multiple shopping lists are shared with the agent; "
            f"specify list: {available}"
        )
    else:
        list_id, title = titles[0]

    if not isinstance(list_id, int):
        raise ToolUnavailable("Nextcloud returned a shopping list without an id")
    return list_id, title


def resolve_shopping_list_item(
    client: NextcloudShoppingList, list_id: int, name: str
) -> dict[str, Any]:
    """Resolve a caller-supplied item name to one item on the list."""
    items = client.list_items(list_id)
    candidates = [
        entry
        for entry in items
        if isinstance(entry, dict) and isinstance(entry.get("name"), str)
    ]
    folded = name.casefold()

    exact = [entry for entry in candidates if entry["name"].casefold() == folded]
    if len(exact) == 1:
        return exact[0]
    if len(exact) > 1:
        raise InvalidToolRequest(f"multiple items named {name!r} are on the list")

    partial = [entry for entry in candidates if folded in entry["name"].casefold()]
    if len(partial) == 1:
        return partial[0]
    if len(partial) > 1:
        names = ", ".join(repr(entry["name"]) for entry in partial[:MAX_RESULTS])
        raise InvalidToolRequest(f"multiple items match {name!r}: {names}")

    available = ", ".join(repr(entry["name"]) for entry in candidates[:MAX_RESULTS])
    raise InvalidToolRequest(
        f"no item named {name!r} was found on the list"
        + (f"; items on the list: {available}" if available else "")
    )


def list_shopping_lists(
    payload: dict[str, Any], client: NextcloudShoppingList
) -> dict[str, Any]:
    if payload:
        raise InvalidToolRequest("unexpected arguments")
    lists = client.list_lists()
    result = [
        {"title": entry.get("title")}
        for entry in lists
        if isinstance(entry, dict)
    ]
    return {"lists": result[:MAX_RESULTS]}


def list_shopping_list_items(
    payload: dict[str, Any], client: NextcloudShoppingList
) -> dict[str, Any]:
    if set(payload) - {"list"}:
        raise InvalidToolRequest("unexpected arguments")
    list_id, list_title = resolve_shopping_list(client, payload.get("list"))
    items = client.list_items(list_id)

    entries = []
    truncated = False
    for entry in items:
        if not isinstance(entry, dict):
            continue
        if len(entries) >= MAX_RESULTS:
            truncated = True
            break
        entries.append(
            {
                "name": entry.get("name"),
                "quantity": entry.get("quantity"),
                "unit": entry.get("unit"),
                "checked": bool(entry.get("checked")),
            }
        )
    return {"list": list_title, "items": entries, "truncated": truncated}


def update_shopping_list(
    payload: dict[str, Any], client: NextcloudShoppingList
) -> dict[str, Any]:
    """Validate and execute one shopping-list item change immediately.

    Mirrors write_file() below: no confirmation round-trip (ADR 0013/0014),
    just bounded validation, name resolution, the write, and audit logging.
    """
    if set(payload) - {"operation", "list", "item", "quantity"}:
        raise InvalidToolRequest("unexpected arguments")
    operation = payload.get("operation")
    if operation not in {"add", "check", "uncheck", "remove"}:
        raise InvalidToolRequest("operation must be add, check, uncheck, or remove")
    item_name = require_string(payload, "item", max_length=MAX_ITEM_NAME_CHARS)

    quantity = payload.get("quantity")
    if quantity is not None:
        if operation != "add":
            raise InvalidToolRequest("quantity is only valid for add")
        if (
            not isinstance(quantity, str)
            or not quantity.strip()
            or len(quantity) > MAX_QUANTITY_CHARS
        ):
            raise InvalidToolRequest("quantity is invalid")
        quantity = quantity.strip()

    list_id, list_title = resolve_shopping_list(client, payload.get("list"))

    try:
        if operation == "add":
            client.create_item(list_id, item_name, quantity)
            resolved_name = item_name
            summary = f"Added {item_name!r} to {list_title!r}."
        else:
            item = resolve_shopping_list_item(client, list_id, item_name)
            item_id = item.get("id")
            resolved_name = item.get("name", item_name)
            if not isinstance(item_id, int):
                raise ToolUnavailable(
                    "Nextcloud returned a shopping list item without an id"
                )
            if operation == "check":
                client.set_item_checked(list_id, item_id, True)
                summary = f"Marked {resolved_name!r} as bought on {list_title!r}."
            elif operation == "uncheck":
                client.set_item_checked(list_id, item_id, False)
                summary = f"Marked {resolved_name!r} as not bought on {list_title!r}."
            else:  # remove
                client.delete_item(list_id, item_id)
                summary = f"Removed {resolved_name!r} from {list_title!r}."
    except ToolUnavailable as error:
        LOGGER.info(
            "Nextcloud shopping list write failed: operation=%s list=%s item=%s reason=%s",
            operation,
            list_title,
            item_name,
            error,
        )
        raise

    LOGGER.info(
        "Nextcloud shopping list write completed: operation=%s list=%s item=%s",
        operation,
        list_title,
        item_name,
    )
    return {
        "operation": operation,
        "list": list_title,
        "item": resolved_name,
        "summary": summary,
    }


def truncate_summary(text: str) -> str:
    if len(text) <= MAX_SUMMARY_CHARS:
        return text
    return text[:MAX_SUMMARY_CHARS] + "\n... (truncated)"


def write_file(payload: dict[str, Any], client: NextcloudWebDAV) -> dict[str, Any]:
    """Validate and execute one Nextcloud write immediately.

    No confirmation round-trip: the model requests a write and it happens.
    Safety still comes from scope (one bounded folder), the extension/size
    allowlist, conflict protection (conditional WebDAV headers against the
    freshly-fetched current state), the empty-folder-only delete guard, and
    content-free audit logging -- just not from a human approving each one.
    """
    if set(payload) - {"operation", "path", "content", "destination_path"}:
        raise InvalidToolRequest("unexpected arguments")
    operation = payload.get("operation")
    if operation not in {"create", "update", "delete", "move"}:
        raise InvalidToolRequest("operation must be create, update, delete, or move")
    relative_path = normalize_relative_path(payload.get("path"), allow_empty=False)
    content = payload.get("content")
    destination_path = payload.get("destination_path")

    if content is not None and operation not in {"create", "update"}:
        raise InvalidToolRequest("content is only valid for create or update")
    if content is not None and not isinstance(content, str):
        raise InvalidToolRequest("content must be a string")
    if operation == "move":
        if not isinstance(destination_path, str):
            raise InvalidToolRequest("move requires destination_path")
        destination_path = normalize_relative_path(destination_path, allow_empty=False)
    elif destination_path is not None:
        raise InvalidToolRequest("destination_path is only valid for move")

    try:
        if operation == "create":
            if client.exists(relative_path):
                raise InvalidToolRequest("path already exists; use update instead")
            if content is None:
                entry = client.mkcol(relative_path)
                summary = f"Created empty folder at {relative_path!r}."
            else:
                if len(content.encode("utf-8")) > MAX_WRITE_BYTES:
                    raise InvalidToolRequest("content exceeds the write size limit")
                entry = client.create_file(relative_path, content)
                summary = f"Created file at {relative_path!r} ({len(content)} characters)."
        elif operation == "update":
            if not isinstance(content, str):
                raise InvalidToolRequest("update requires content")
            if len(content.encode("utf-8")) > MAX_WRITE_BYTES:
                raise InvalidToolRequest("content exceeds the write size limit")
            current = client.read_text_file(relative_path)
            diff = "\n".join(
                unified_diff(
                    current["content"].splitlines(),
                    content.splitlines(),
                    fromfile=relative_path,
                    tofile=relative_path,
                    lineterm="",
                )
            )
            entry = client.update_file(relative_path, content, current["etag"])
            summary = (
                f"Updated {relative_path!r}:\n{diff}"
                if diff
                else f"Updated {relative_path!r} (no content change)."
            )
        elif operation == "delete":
            stat_entry = client.stat(relative_path)
            if stat_entry.kind == "folder" and client.list_directory(relative_path):
                raise InvalidToolRequest(
                    "folder is not empty; delete its contents first"
                )
            client.delete(relative_path, stat_entry.etag)
            entry = None
            summary = f"Deleted {stat_entry.kind} at {relative_path!r}."
        else:  # move
            stat_entry = client.stat(relative_path)
            entry = client.move(relative_path, destination_path, stat_entry.etag)
            summary = f"Moved {relative_path!r} to {destination_path!r}."
    except ToolUnavailable as error:
        LOGGER.info(
            "Nextcloud write failed: operation=%s path=%s reason=%s",
            operation,
            relative_path,
            error,
        )
        raise

    LOGGER.info(
        "Nextcloud write completed: operation=%s path=%s", operation, relative_path
    )
    return {
        "operation": operation,
        "path": relative_path,
        "summary": truncate_summary(summary),
        "result": entry.as_dict() if entry is not None else None,
    }


def handle_tool(
    path: str,
    payload: Any,
    client: NextcloudWebDAV,
    shopping_list_client: NextcloudShoppingList | None = None,
) -> dict[str, Any]:
    if not isinstance(payload, dict):
        raise InvalidToolRequest("request must be an object")
    if path == "/v1/shopping/lists":
        if shopping_list_client is None:
            raise ToolUnavailable("Shopping list tool is unavailable")
        return list_shopping_lists(payload, shopping_list_client)
    if path == "/v1/shopping/items":
        if shopping_list_client is None:
            raise ToolUnavailable("Shopping list tool is unavailable")
        return list_shopping_list_items(payload, shopping_list_client)
    if path == "/v1/shopping/write":
        if shopping_list_client is None:
            raise ToolUnavailable("Shopping list tool is unavailable")
        return update_shopping_list(payload, shopping_list_client)
    if path == "/v1/list":
        if set(payload) - {"path"}:
            raise InvalidToolRequest("unexpected arguments")
        folder = normalize_relative_path(payload.get("path", ""))
        entries = client.list_directory(folder)
        return {"root": folder, "entries": [entry.as_dict() for entry in entries]}
    if path == "/v1/search":
        if set(payload) - {"query", "path"}:
            raise InvalidToolRequest("unexpected arguments")
        query = require_string(payload, "query", max_length=200)
        folder = normalize_relative_path(payload.get("path", ""))
        return client.search(query, folder)
    if path == "/v1/read":
        if set(payload) != {"path"}:
            raise InvalidToolRequest("read requires exactly one path")
        file_path = normalize_relative_path(payload["path"], allow_empty=False)
        return client.read_text_file(file_path)
    if path == "/v1/write":
        return write_file(payload, client)
    raise InvalidToolRequest("unknown tool")


class NextcloudToolsRequestHandler(BaseHTTPRequestHandler):
    """Serve only the exact bounded Nextcloud endpoints."""

    server_version = "nextcloud-tools/1"

    def do_GET(self) -> None:
        if self.path == "/v1/health":
            self._send_json(200, {"status": "ok"})
        else:
            self._send_json(404, {"error": "not_found"})

    def do_POST(self) -> None:
        if self.headers.get("Transfer-Encoding"):
            self._send_json(400, {"error": "invalid_transfer_encoding"})
            return
        try:
            content_length = int(self.headers.get("Content-Length", "0"))
        except ValueError:
            self._send_json(400, {"error": "invalid_content_length"})
            return
        # /v1/write can carry up to MAX_WRITE_BYTES of file content, far
        # beyond the small fixed-shape arguments every other tool takes.
        max_bytes = (
            MAX_WRITE_BYTES + 4096 if self.path == "/v1/write" else MAX_REQUEST_BYTES
        )
        if content_length <= 0 or content_length > max_bytes:
            self._send_json(413, {"error": "invalid_request_size"})
            return
        try:
            payload = json.loads(self.rfile.read(content_length))
            result = handle_tool(
                self.path,
                payload,
                self.server.client,
                self.server.shopping_list_client,
            )
        except (UnicodeDecodeError, json.JSONDecodeError, InvalidToolRequest):
            self._send_json(400, {"error": "invalid_request"})
            return
        except ToolUnavailable as error:
            LOGGER.warning("Nextcloud tool request failed: %s", error)
            self._send_json(
                503,
                {
                    "error": "tool_unavailable",
                    "reason": safe_unavailable_reason(error),
                },
            )
            return
        self._send_json(200, result)

    def _send_json(self, status: int, payload: dict[str, Any]) -> None:
        body = json.dumps(payload, separators=(",", ":")).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, message_format: str, *args: Any) -> None:
        LOGGER.info("nextcloud-tools request completed")


class ThreadingUnixServer(socketserver.ThreadingMixIn, socketserver.UnixStreamServer):
    daemon_threads = True
    client: NextcloudWebDAV
    shopping_list_client: NextcloudShoppingList


def read_app_password(path: str) -> str:
    value = Path(path).read_text(encoding="utf-8").strip()
    if len(value) < 16 or value == "CHANGE_ME":
        raise RuntimeError("Nextcloud app password is not configured")
    return value


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    if ENDPOINT_HOST != "127.0.0.1":
        raise RuntimeError("Nextcloud endpoint must remain on loopback")
    app_password = read_app_password(APP_PASSWORD_FILE)
    client = NextcloudWebDAV(
        ENDPOINT_HOST,
        ENDPOINT_PORT,
        HTTP_HOST,
        USERNAME,
        app_password,
        ALLOWED_ROOT,
    )
    shopping_list_client = NextcloudShoppingList(
        ENDPOINT_HOST,
        ENDPOINT_PORT,
        HTTP_HOST,
        USERNAME,
        app_password,
    )
    socket_path = Path(SOCKET_PATH)
    socket_path.parent.mkdir(parents=True, exist_ok=True)
    socket_path.unlink(missing_ok=True)
    with ThreadingUnixServer(str(socket_path), NextcloudToolsRequestHandler) as server:
        server.client = client
        server.shopping_list_client = shopping_list_client
        os.chmod(socket_path, 0o660)
        server.serve_forever()


if __name__ == "__main__":
    main()
