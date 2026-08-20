"""Expose bounded, read-only Nextcloud WebDAV operations over a Unix socket."""

from __future__ import annotations

import base64
import http.client
import json
import logging
import os
import posixpath
import socket
import socketserver
import xml.etree.ElementTree as ET
from collections import deque
from dataclasses import dataclass
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
        authorization = base64.b64encode(
            f"{self.username}:{self.app_password}".encode("utf-8")
        ).decode("ascii")
        request_headers = {
            "Authorization": f"Basic {authorization}",
            "Connection": "close",
            "Host": self.http_host,
            "User-Agent": "home-infra-nextcloud-tools/1",
        }
        if headers:
            request_headers.update(headers)
        connection = http.client.HTTPConnection(
            self.host, self.port, timeout=self.timeout
        )
        try:
            connection.request(
                method,
                self._webdav_path(relative_path),
                body=body,
                headers=request_headers,
            )
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


def handle_tool(path: str, payload: Any, client: NextcloudWebDAV) -> dict[str, Any]:
    if not isinstance(payload, dict):
        raise InvalidToolRequest("request must be an object")
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
        if content_length <= 0 or content_length > MAX_REQUEST_BYTES:
            self._send_json(413, {"error": "invalid_request_size"})
            return
        try:
            payload = json.loads(self.rfile.read(content_length))
            result = handle_tool(self.path, payload, self.server.client)
        except (UnicodeDecodeError, json.JSONDecodeError, InvalidToolRequest):
            self._send_json(400, {"error": "invalid_request"})
            return
        except ToolUnavailable as error:
            LOGGER.warning("Nextcloud tool request failed: %s", error)
            self._send_json(503, {"error": "tool_unavailable"})
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


def read_app_password(path: str) -> str:
    value = Path(path).read_text(encoding="utf-8").strip()
    if len(value) < 16 or value == "CHANGE_ME":
        raise RuntimeError("Nextcloud app password is not configured")
    return value


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    if ENDPOINT_HOST != "127.0.0.1":
        raise RuntimeError("Nextcloud endpoint must remain on loopback")
    client = NextcloudWebDAV(
        ENDPOINT_HOST,
        ENDPOINT_PORT,
        HTTP_HOST,
        USERNAME,
        read_app_password(APP_PASSWORD_FILE),
        ALLOWED_ROOT,
    )
    socket_path = Path(SOCKET_PATH)
    socket_path.parent.mkdir(parents=True, exist_ok=True)
    socket_path.unlink(missing_ok=True)
    with ThreadingUnixServer(str(socket_path), NextcloudToolsRequestHandler) as server:
        server.client = client
        os.chmod(socket_path, 0o660)
        server.serve_forever()


if __name__ == "__main__":
    main()
