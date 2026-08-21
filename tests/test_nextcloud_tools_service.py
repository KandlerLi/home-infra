from __future__ import annotations

import sys
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent))

from _load_module import load_module_from_path

nextcloud_tools = load_module_from_path(
    "nextcloud_tools_service",
    "ansible/roles/nextcloud_tools/files/nextcloud_tools_service.py",
)


MULTISTATUS = b"""<?xml version="1.0"?>
<d:multistatus xmlns:d="DAV:" xmlns:oc="http://owncloud.org/ns" xmlns:nc="http://nextcloud.org/ns">
  <d:response>
    <d:href>/remote.php/dav/files/agent/AI%20Workspace/</d:href>
    <d:propstat><d:prop><d:resourcetype><d:collection/></d:resourcetype></d:prop>
      <d:status>HTTP/1.1 200 OK</d:status></d:propstat>
  </d:response>
  <d:response>
    <d:href>/remote.php/dav/files/agent/AI%20Workspace/Photos/sunset.jpg</d:href>
    <d:propstat><d:prop>
      <d:getlastmodified>Wed, 19 Aug 2026 10:00:00 GMT</d:getlastmodified>
      <d:getcontentlength>1234</d:getcontentlength>
      <d:getcontenttype>image/jpeg</d:getcontenttype>
      <d:resourcetype/><d:getetag>etag-safe</d:getetag>
      <oc:fileid>42</oc:fileid><oc:permissions>RGDNVCK</oc:permissions>
      <nc:has-preview>true</nc:has-preview>
      <oc:owner-display-name>private-owner</oc:owner-display-name>
    </d:prop><d:status>HTTP/1.1 200 OK</d:status></d:propstat>
  </d:response>
</d:multistatus>
"""


def _entry(path: str = "Photos/sunset.jpg", kind: str = "file", etag: str = "etag-safe"):
    return nextcloud_tools.FileEntry(
        path=path,
        name=path.rsplit("/", 1)[-1],
        kind=kind,
        size_bytes=1234,
        modified="Wed, 19 Aug 2026 10:00:00 GMT",
        content_type="image/jpeg",
        etag=etag,
        file_id="42",
        permissions="RGDNVCK",
        has_preview=True,
    )


class FakeClient:
    def __init__(self) -> None:
        self.creates = []
        self.mkcols = []
        self.updates = []
        self.deletes = []
        self.moves = []
        self.exists_result = False
        self.stat_kind = "file"

    def list_directory(self, path: str):
        return [_entry()]

    def search(self, query: str, path: str):
        return {"query": query, "root": path, "matches": [], "truncated": False}

    def read_text_file(self, path: str):
        return {"path": path, "content": "safe text", "etag": "etag-safe"}

    def stat(self, path: str):
        return _entry(path=path, kind=self.stat_kind)

    def exists(self, path: str) -> bool:
        return self.exists_result

    def create_file(self, path: str, content: str):
        self.creates.append((path, content))
        return _entry(path=path)

    def mkcol(self, path: str):
        self.mkcols.append(path)
        return _entry(path=path, kind="folder")

    def update_file(self, path: str, content: str, expected_etag):
        self.updates.append((path, content, expected_etag))
        return _entry(path=path)

    def delete(self, path: str, expected_etag) -> None:
        self.deletes.append((path, expected_etag))

    def move(self, path: str, destination_path: str, expected_etag):
        self.moves.append((path, destination_path, expected_etag))
        return _entry(path=destination_path)


def _webdav_client() -> "nextcloud_tools.NextcloudWebDAV":
    return nextcloud_tools.NextcloudWebDAV(
        "127.0.0.1",
        11000,
        "nextcloud.jkandler.de",
        "agent",
        "unused-app-password",
        "AI Workspace",
    )


class NextcloudToolsServiceTests(unittest.TestCase):
    def test_relative_paths_cannot_escape_allowed_root(self) -> None:
        invalid_paths = ["../secret", "folder/../../secret", "/../secret", "a\\b"]
        for path in invalid_paths:
            with self.subTest(path=path), self.assertRaises(
                nextcloud_tools.InvalidToolRequest
            ):
                nextcloud_tools.normalize_relative_path(path)

    def test_multistatus_returns_only_approved_metadata(self) -> None:
        entries = nextcloud_tools.parse_multistatus(
            MULTISTATUS, "/remote.php/dav/files/agent/AI Workspace"
        )

        self.assertEqual(len(entries), 2)
        file_entry = entries[1].as_dict()
        self.assertEqual(file_entry["path"], "Photos/sunset.jpg")
        self.assertEqual(file_entry["content_type"], "image/jpeg")
        self.assertTrue(file_entry["has_preview"])
        self.assertNotIn("private-owner", repr(file_entry))

    def test_multistatus_rejects_paths_outside_allowed_root(self) -> None:
        malicious = MULTISTATUS.replace(
            b"/remote.php/dav/files/agent/AI%20Workspace/Photos/sunset.jpg",
            b"/remote.php/dav/files/agent/Private/secret.txt",
        )

        with self.assertRaises(nextcloud_tools.ToolUnavailable):
            nextcloud_tools.parse_multistatus(
                malicious, "/remote.php/dav/files/agent/AI Workspace"
            )

    def test_tool_dispatch_rejects_unknown_arguments(self) -> None:
        with self.assertRaises(nextcloud_tools.InvalidToolRequest):
            nextcloud_tools.handle_tool(
                "/v1/search",
                {"query": "sunset", "path": "", "include_content": True},
                FakeClient(),
            )

    def test_list_exposes_bounded_metadata(self) -> None:
        result = nextcloud_tools.handle_tool(
            "/v1/list", {"path": "Photos"}, FakeClient()
        )

        self.assertEqual(result["root"], "Photos")
        self.assertEqual(result["entries"][0]["name"], "sunset.jpg")

    def test_binary_file_extensions_cannot_be_read(self) -> None:
        client = nextcloud_tools.NextcloudWebDAV(
            "127.0.0.1",
            11000,
            "nextcloud.jkandler.de",
            "agent",
            "unused-app-password",
            "AI Workspace",
        )

        with self.assertRaises(nextcloud_tools.InvalidToolRequest):
                client.read_text_file("Photos/sunset.jpg")

    def test_upstream_status_error_does_not_echo_request_path(self) -> None:
        client = nextcloud_tools.NextcloudWebDAV(
            "127.0.0.1",
            11000,
            "nextcloud.jkandler.de",
            "agent",
            "unused-app-password",
            "AI Workspace",
        )

        with patch.object(client, "_request", return_value=(404, {}, b"")):
            with self.assertRaises(nextcloud_tools.ToolUnavailable) as raised:
                client.list_directory("private-name-must-not-be-logged")

        self.assertEqual(
            str(raised.exception),
            "Nextcloud directory listing returned HTTP 404",
        )
        self.assertEqual(
            nextcloud_tools.safe_unavailable_reason(raised.exception),
            "upstream_http_404",
        )

    def test_unavailable_reason_rejects_unstructured_detail(self) -> None:
        reason = nextcloud_tools.safe_unavailable_reason(
            nextcloud_tools.ToolUnavailable("private/path must not escape")
        )

        self.assertEqual(reason, "upstream_response_invalid")


class NextcloudWebDAVWriteTests(unittest.TestCase):
    def test_create_file_maps_a_conflict_to_a_clear_error(self) -> None:
        client = _webdav_client()

        with patch.object(client, "_request", return_value=(412, {}, b"")):
            with self.assertRaises(nextcloud_tools.ToolUnavailable) as raised:
                client.create_file("note.txt", "hello")

        self.assertIn("already exists", str(raised.exception))

    def test_create_file_rejects_unapproved_extensions(self) -> None:
        client = _webdav_client()

        with self.assertRaises(nextcloud_tools.InvalidToolRequest):
            client.create_file("note.exe", "hello")

    def test_create_file_rejects_oversized_content(self) -> None:
        client = _webdav_client()

        with self.assertRaises(nextcloud_tools.InvalidToolRequest):
            client.create_file("note.txt", "x" * (nextcloud_tools.MAX_WRITE_BYTES + 1))

    def test_update_file_sends_if_match_and_maps_conflict(self) -> None:
        client = _webdav_client()
        captured: dict = {}

        def fake_request(method, path, *, body=None, headers=None, max_bytes=None):
            captured["headers"] = headers
            return (412, {}, b"")

        with patch.object(client, "_request", side_effect=fake_request):
            with self.assertRaises(nextcloud_tools.ToolUnavailable):
                client.update_file("note.txt", "new text", "old-etag")

        self.assertEqual(captured["headers"]["If-Match"], "old-etag")

    def test_mkcol_maps_405_to_a_conflict_error(self) -> None:
        client = _webdav_client()

        with patch.object(client, "_request", return_value=(405, {}, b"")):
            with self.assertRaises(nextcloud_tools.ToolUnavailable) as raised:
                client.mkcol("NewFolder")

        self.assertIn("already exists", str(raised.exception))

    def test_move_rejects_an_unapproved_destination_extension(self) -> None:
        client = _webdav_client()

        with self.assertRaises(nextcloud_tools.InvalidToolRequest):
            client.move("note.txt", "note.exe", "etag")

    def test_move_sends_overwrite_false_and_the_destination_header(self) -> None:
        client = _webdav_client()
        captured: dict = {}

        def fake_request(method, path, *, body=None, headers=None, max_bytes=None):
            captured["method"] = method
            captured["headers"] = headers
            return (201, {}, b"")

        with patch.object(client, "_request", side_effect=fake_request), patch.object(
            client, "stat", return_value=_entry(path="renamed.txt")
        ):
            client.move("note.txt", "renamed.txt", "etag-value")

        self.assertEqual(captured["method"], "MOVE")
        self.assertEqual(captured["headers"]["Overwrite"], "F")
        self.assertEqual(captured["headers"]["If-Match"], "etag-value")
        self.assertIn("renamed.txt", captured["headers"]["Destination"])


class WriteFileHandlerTests(unittest.TestCase):
    """/v1/write executes immediately -- no confirmation round-trip.

    Safety here comes from scope, the extension/size allowlist, conflict
    protection, and the empty-folder-only delete guard, not from a human
    approving each write (a deliberate simplification -- see ADR 0013).
    """

    def test_create_writes_the_file_immediately(self) -> None:
        client = FakeClient()

        result = nextcloud_tools.handle_tool(
            "/v1/write",
            {
                "operation": "create",
                "path": "note.txt",
                "content": "hello",
                "destination_path": None,
            },
            client,
        )

        self.assertEqual(client.creates, [("note.txt", "hello")])
        self.assertEqual(result["operation"], "create")
        self.assertIn("note.txt", result["summary"])

    def test_create_rejects_an_existing_path(self) -> None:
        client = FakeClient()
        client.exists_result = True

        with self.assertRaises(nextcloud_tools.InvalidToolRequest):
            nextcloud_tools.handle_tool(
                "/v1/write",
                {
                    "operation": "create",
                    "path": "note.txt",
                    "content": "hello",
                    "destination_path": None,
                },
                client,
            )

    def test_create_with_no_content_makes_a_folder(self) -> None:
        client = FakeClient()

        nextcloud_tools.handle_tool(
            "/v1/write",
            {
                "operation": "create",
                "path": "NewFolder",
                "content": None,
                "destination_path": None,
            },
            client,
        )

        self.assertEqual(client.mkcols, ["NewFolder"])
        self.assertEqual(client.creates, [])

    def test_update_writes_immediately_and_summarizes_the_diff(self) -> None:
        client = FakeClient()

        result = nextcloud_tools.handle_tool(
            "/v1/write",
            {
                "operation": "update",
                "path": "note.txt",
                "content": "new text",
                "destination_path": None,
            },
            client,
        )

        self.assertEqual(client.updates, [("note.txt", "new text", "etag-safe")])
        self.assertIn("safe text", result["summary"])
        self.assertIn("new text", result["summary"])

    def test_delete_rejects_a_nonempty_folder(self) -> None:
        client = FakeClient()
        client.stat_kind = "folder"

        with self.assertRaises(nextcloud_tools.InvalidToolRequest):
            nextcloud_tools.handle_tool(
                "/v1/write",
                {
                    "operation": "delete",
                    "path": "Photos",
                    "content": None,
                    "destination_path": None,
                },
                client,
            )

    def test_delete_removes_the_item_immediately(self) -> None:
        client = FakeClient()

        result = nextcloud_tools.handle_tool(
            "/v1/write",
            {
                "operation": "delete",
                "path": "note.txt",
                "content": None,
                "destination_path": None,
            },
            client,
        )

        self.assertEqual(client.deletes, [("note.txt", "etag-safe")])
        self.assertIsNone(result["result"])

    def test_move_requires_a_destination(self) -> None:
        client = FakeClient()

        with self.assertRaises(nextcloud_tools.InvalidToolRequest):
            nextcloud_tools.handle_tool(
                "/v1/write",
                {
                    "operation": "move",
                    "path": "note.txt",
                    "content": None,
                    "destination_path": None,
                },
                client,
            )

    def test_move_relocates_the_item_immediately(self) -> None:
        client = FakeClient()

        result = nextcloud_tools.handle_tool(
            "/v1/write",
            {
                "operation": "move",
                "path": "note.txt",
                "content": None,
                "destination_path": "renamed.txt",
            },
            client,
        )

        self.assertEqual(client.moves, [("note.txt", "renamed.txt", "etag-safe")])
        self.assertEqual(result["operation"], "move")


if __name__ == "__main__":
    unittest.main()
