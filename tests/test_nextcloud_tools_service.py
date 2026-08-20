from __future__ import annotations

import importlib.util
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

PROJECT_ROOT = Path(__file__).resolve().parents[1]
MODULE_PATH = (
    PROJECT_ROOT
    / "ansible/roles/nextcloud_tools/files/nextcloud_tools_service.py"
)
SPEC = importlib.util.spec_from_file_location("nextcloud_tools_service", MODULE_PATH)
assert SPEC and SPEC.loader
nextcloud_tools = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = nextcloud_tools
SPEC.loader.exec_module(nextcloud_tools)


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


class FakeClient:
    def list_directory(self, path: str):
        return [
            nextcloud_tools.FileEntry(
                path="Photos/sunset.jpg",
                name="sunset.jpg",
                kind="file",
                size_bytes=1234,
                modified="Wed, 19 Aug 2026 10:00:00 GMT",
                content_type="image/jpeg",
                etag="etag-safe",
                file_id="42",
                permissions="RGDNVCK",
                has_preview=True,
            )
        ]

    def search(self, query: str, path: str):
        return {"query": query, "root": path, "matches": [], "truncated": False}

    def read_text_file(self, path: str):
        return {"path": path, "content": "safe text"}


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


if __name__ == "__main__":
    unittest.main()
