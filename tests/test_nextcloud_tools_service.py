from __future__ import annotations

import http.client
import json
import shutil
import socket
import sys
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent))

from _load_module import load_module_from_path

nextcloud_tools = load_module_from_path(
    "nextcloud_tools_service",
    "nextcloud_tools/nextcloud_tools_service.py",
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

    def read_document(self, path: str):
        return {"path": path, "document_type": "xlsx", "content": "## Sheet: A"}

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


def _shopping_list_client() -> "nextcloud_tools.NextcloudShoppingList":
    return nextcloud_tools.NextcloudShoppingList(
        "127.0.0.1",
        11000,
        "nextcloud.jkandler.de",
        "agent",
        "unused-app-password",
    )


class FakeShoppingListClient:
    def __init__(self) -> None:
        self.lists_result = [{"id": 1, "title": "Groceries"}]
        self.items_result = [
            {"id": 10, "name": "Milk", "quantity": "1", "unit": None, "checked": False}
        ]
        self.creates: list = []
        self.checks: list = []
        self.deletes: list = []

    def list_lists(self):
        return self.lists_result

    def list_items(self, list_id: int):
        return self.items_result

    def create_item(self, list_id: int, name: str, quantity):
        self.creates.append((list_id, name, quantity))
        return {"id": 99, "name": name}

    def set_item_checked(self, list_id: int, item_id: int, checked: bool):
        self.checks.append((list_id, item_id, checked))
        return {"id": item_id, "checked": checked}

    def delete_item(self, list_id: int, item_id: int) -> None:
        self.deletes.append((list_id, item_id))


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

        with patch.object(client, "_request", return_value=(500, {}, b"")):
            with self.assertRaises(nextcloud_tools.ToolUnavailable) as raised:
                client.list_directory("private-name-must-not-be-logged")

        self.assertNotIsInstance(raised.exception, nextcloud_tools.ToolNotFound)
        self.assertEqual(
            str(raised.exception),
            "Nextcloud directory listing returned HTTP 500",
        )
        self.assertEqual(
            nextcloud_tools.safe_unavailable_reason(raised.exception),
            "upstream_http_500",
        )

    def test_missing_directory_is_not_found_and_does_not_echo_the_path(self) -> None:
        client = _webdav_client()

        with patch.object(client, "_request", return_value=(404, {}, b"")):
            with self.assertRaises(nextcloud_tools.ToolNotFound) as raised:
                client.list_directory("private-name-must-not-be-logged")

        self.assertNotIn("private-name-must-not-be-logged", str(raised.exception))

    def test_missing_file_metadata_is_not_found_but_a_server_error_is_not(self) -> None:
        client = _webdav_client()

        with patch.object(client, "_request", return_value=(404, {}, b"")):
            with self.assertRaises(nextcloud_tools.ToolNotFound):
                client.stat("Photos/sunset.jpg")

        with patch.object(client, "_request", return_value=(500, {}, b"")):
            with self.assertRaises(nextcloud_tools.ToolUnavailable) as raised:
                client.stat("Photos/sunset.jpg")
        self.assertNotIsInstance(raised.exception, nextcloud_tools.ToolNotFound)

    def test_unavailable_reason_rejects_unstructured_detail(self) -> None:
        reason = nextcloud_tools.safe_unavailable_reason(
            nextcloud_tools.ToolUnavailable("private/path must not escape")
        )

        self.assertEqual(reason, "upstream_response_invalid")


def _xlsx_bytes(
    *,
    sheet_xml: str | None = None,
    extra_parts: dict[str, str] | None = None,
) -> bytes:
    import io
    import zipfile

    ns = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"
    rel_ns = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
    workbook = (
        f'<workbook xmlns="{ns}" xmlns:r="{rel_ns}"><sheets>'
        '<sheet name="Budget" sheetId="1" r:id="rId1"/>'
        '<sheet name="Old" sheetId="2" state="hidden" r:id="rId2"/>'
        "</sheets></workbook>"
    )
    rels = (
        '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
        '<Relationship Id="rId1" Target="worksheets/sheet1.xml"/>'
        '<Relationship Id="rId2" Target="/xl/worksheets/sheet2.xml"/>'
        "</Relationships>"
    )
    shared = (
        f'<sst xmlns="{ns}"><si><t>Item</t></si>'
        "<si><r><t>Ren</t></r><r><t>t</t></r></si></sst>"
    )
    sheet1 = sheet_xml or (
        f'<worksheet xmlns="{ns}"><sheetData>'
        '<row r="1"><c r="A1" t="s"><v>0</v></c><c r="C1" t="s"><v>1</v></c></row>'
        '<row r="2"><c r="A2" t="inlineStr"><is><t>Food</t></is></c>'
        '<c r="B2"><v>12.5</v></c><c r="C2" t="b"><v>1</v></c></row>'
        '<row r="3"/>'
        "</sheetData></worksheet>"
    )
    sheet2 = (
        f'<worksheet xmlns="{ns}"><sheetData><row r="1">'
        '<c r="A1" t="str"><f>1+1</f><v>two</v></c></row></sheetData></worksheet>'
    )
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("xl/workbook.xml", workbook)
        archive.writestr("xl/_rels/workbook.xml.rels", rels)
        archive.writestr("xl/sharedStrings.xml", shared)
        archive.writestr("xl/worksheets/sheet1.xml", sheet1)
        archive.writestr("xl/worksheets/sheet2.xml", sheet2)
        for name, content in (extra_parts or {}).items():
            archive.writestr(name, content)
    return buffer.getvalue()


class DocumentReadingTests(unittest.TestCase):
    def test_xlsx_becomes_tab_separated_text_per_sheet(self) -> None:
        text, truncated = nextcloud_tools.xlsx_to_text(_xlsx_bytes())

        self.assertFalse(truncated)
        self.assertEqual(
            text.split("\n"),
            [
                "## Sheet: Budget",
                "Item\t\tRent",
                "Food\t12.5\tTRUE",
                "## Sheet: Old (hidden)",
                "two",
            ],
        )

    def test_xlsx_with_a_doctype_is_refused(self) -> None:
        evil = '<!DOCTYPE x [<!ENTITY a "aaaa">]><worksheet><sheetData/></worksheet>'

        with self.assertRaises(nextcloud_tools.InvalidToolRequest):
            nextcloud_tools.xlsx_to_text(_xlsx_bytes(sheet_xml=evil))

    def test_xlsx_part_over_the_declared_size_limit_is_refused(self) -> None:
        with patch.object(nextcloud_tools, "MAX_XLSX_PART_BYTES", 100):
            with self.assertRaises(nextcloud_tools.InvalidToolRequest):
                nextcloud_tools.xlsx_to_text(_xlsx_bytes())

    def test_xlsx_total_uncompressed_size_is_capped(self) -> None:
        with patch.object(nextcloud_tools, "MAX_XLSX_TOTAL_BYTES", 500):
            with self.assertRaises(nextcloud_tools.InvalidToolRequest):
                nextcloud_tools.xlsx_to_text(_xlsx_bytes())

    def test_xlsx_output_is_truncated_at_the_read_limit(self) -> None:
        with patch.object(nextcloud_tools, "MAX_READ_BYTES", 20):
            text, truncated = nextcloud_tools.xlsx_to_text(_xlsx_bytes())

        self.assertTrue(truncated)
        self.assertLessEqual(len(text.encode("utf-8")), 20)

    def test_a_non_zip_file_is_refused(self) -> None:
        with self.assertRaises(nextcloud_tools.InvalidToolRequest):
            nextcloud_tools.xlsx_to_text(b"not a spreadsheet")

    def test_extra_zip_parts_are_never_opened(self) -> None:
        # A part outside the named spreadsheet parts (even a huge one) must
        # not count against the budget or be read.
        with patch.object(nextcloud_tools, "MAX_XLSX_TOTAL_BYTES", 5_000):
            text, _ = nextcloud_tools.xlsx_to_text(
                _xlsx_bytes(extra_parts={"junk.bin": "x" * 1_000_000})
            )

        self.assertIn("## Sheet: Budget", text)

    def test_pdf_is_returned_as_base64_bytes(self) -> None:
        import base64

        client = _webdav_client()
        pdf = b"%PDF-1.7\n%payload"
        entry = _entry(path="Finance/statement.pdf")

        with patch.object(client, "stat", return_value=entry), patch.object(
            client, "_request", return_value=(200, {}, pdf)
        ):
            result = client.read_document("Finance/statement.pdf")

        self.assertEqual(result["document_type"], "pdf")
        self.assertEqual(base64.b64decode(result["data_base64"]), pdf)

    def test_a_file_named_pdf_without_the_pdf_magic_is_refused(self) -> None:
        client = _webdav_client()

        with patch.object(client, "stat", return_value=_entry(path="a.pdf")), patch.object(
            client, "_request", return_value=(200, {}, b"<html>not a pdf</html>")
        ):
            with self.assertRaises(nextcloud_tools.InvalidToolRequest):
                client.read_document("a.pdf")

    def test_other_extensions_are_refused_by_read_document(self) -> None:
        client = _webdav_client()

        for path in ("a.docx", "a.xls", "a.exe", "a.txt", "a"):
            with self.assertRaises(nextcloud_tools.InvalidToolRequest):
                client.read_document(path)

    def test_oversized_documents_are_refused_before_download(self) -> None:
        client = _webdav_client()
        too_big = _entry(path="big.pdf")
        object.__setattr__(too_big, "size_bytes", nextcloud_tools.MAX_DOCUMENT_BYTES + 1)

        with patch.object(client, "stat", return_value=too_big), patch.object(
            client, "_request"
        ) as request:
            with self.assertRaises(nextcloud_tools.InvalidToolRequest):
                client.read_document("big.pdf")

        request.assert_not_called()

    def test_handle_tool_routes_read_document_with_exactly_one_path(self) -> None:
        result = nextcloud_tools.handle_tool(
            "/v1/read_document", {"path": "Budget.xlsx"}, FakeClient()
        )

        self.assertEqual(result["document_type"], "xlsx")
        with self.assertRaises(nextcloud_tools.InvalidToolRequest):
            nextcloud_tools.handle_tool(
                "/v1/read_document", {"path": "a.pdf", "extra": 1}, FakeClient()
            )


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


class NextcloudShoppingListRequestTests(unittest.TestCase):
    """OCS JSON envelope handling, independent of any specific route."""

    def test_successful_request_returns_the_ocs_data_payload(self) -> None:
        client = _shopping_list_client()
        body = b'{"ocs":{"meta":{"statuscode":200},"data":[{"id":1,"title":"Groceries"}]}}'

        with patch.object(
            nextcloud_tools, "_send_http_request", return_value=(200, {}, body)
        ):
            data = client.list_lists()

        self.assertEqual(data, [{"id": 1, "title": "Groceries"}])

    def test_forbidden_status_maps_to_a_clear_permission_error(self) -> None:
        client = _shopping_list_client()

        with patch.object(
            nextcloud_tools, "_send_http_request", return_value=(403, {}, b"{}")
        ):
            with self.assertRaises(nextcloud_tools.ToolUnavailable) as raised:
                client.list_lists()

        self.assertIn("forbidden", str(raised.exception))

    def test_not_found_status_maps_to_a_clear_error(self) -> None:
        client = _shopping_list_client()

        with patch.object(
            nextcloud_tools, "_send_http_request", return_value=(404, {}, b"{}")
        ):
            with self.assertRaises(nextcloud_tools.ToolUnavailable) as raised:
                client.list_lists()

        self.assertIn("not found", str(raised.exception))

    def test_malformed_envelope_is_rejected(self) -> None:
        client = _shopping_list_client()

        with patch.object(
            nextcloud_tools, "_send_http_request", return_value=(200, {}, b"not json")
        ):
            with self.assertRaises(nextcloud_tools.ToolUnavailable) as raised:
                client.list_lists()

        self.assertIn("invalid data", str(raised.exception))

    def test_create_item_sends_a_json_body_to_the_items_path(self) -> None:
        client = _shopping_list_client()
        captured: dict = {}

        def fake_send(host, port, timeout, method, request_path, headers, body, max_bytes):
            captured["request_path"] = request_path
            captured["body"] = body
            return (
                201,
                {},
                b'{"ocs":{"meta":{"statuscode":201},"data":{"id":5,"name":"Milk"}}}',
            )

        with patch.object(nextcloud_tools, "_send_http_request", side_effect=fake_send):
            item = client.create_item(1, "Milk", "2")

        self.assertEqual(item, {"id": 5, "name": "Milk"})
        self.assertIn("/lists/1/items", captured["request_path"])
        self.assertEqual(json.loads(captured["body"]), {"name": "Milk", "quantity": "2"})


class ShoppingListResolutionTests(unittest.TestCase):
    def test_resolves_the_sole_list_when_none_is_named(self) -> None:
        client = FakeShoppingListClient()

        list_id, title = nextcloud_tools.resolve_shopping_list(client, None)

        self.assertEqual((list_id, title), (1, "Groceries"))

    def test_rejects_when_no_lists_are_shared(self) -> None:
        client = FakeShoppingListClient()
        client.lists_result = []

        with self.assertRaises(nextcloud_tools.InvalidToolRequest):
            nextcloud_tools.resolve_shopping_list(client, None)

    def test_rejects_ambiguous_selection_among_multiple_lists(self) -> None:
        client = FakeShoppingListClient()
        client.lists_result = [
            {"id": 1, "title": "Groceries"},
            {"id": 2, "title": "Hardware store"},
        ]

        with self.assertRaises(nextcloud_tools.InvalidToolRequest):
            nextcloud_tools.resolve_shopping_list(client, None)

    def test_resolves_an_explicit_list_name_case_insensitively(self) -> None:
        client = FakeShoppingListClient()
        client.lists_result = [
            {"id": 1, "title": "Groceries"},
            {"id": 2, "title": "Hardware store"},
        ]

        list_id, title = nextcloud_tools.resolve_shopping_list(client, "groceries")

        self.assertEqual((list_id, title), (1, "Groceries"))

    def test_rejects_an_unknown_list_name(self) -> None:
        client = FakeShoppingListClient()

        with self.assertRaises(nextcloud_tools.InvalidToolRequest):
            nextcloud_tools.resolve_shopping_list(client, "Nonexistent")

    def test_resolves_an_exact_item_name_match(self) -> None:
        client = FakeShoppingListClient()

        item = nextcloud_tools.resolve_shopping_list_item(client, 1, "milk")

        self.assertEqual(item["id"], 10)

    def test_resolves_a_unique_substring_item_match(self) -> None:
        client = FakeShoppingListClient()
        client.items_result = [
            {"id": 10, "name": "Oat Milk", "quantity": None, "unit": None, "checked": False}
        ]

        item = nextcloud_tools.resolve_shopping_list_item(client, 1, "milk")

        self.assertEqual(item["id"], 10)

    def test_rejects_an_item_name_with_no_match(self) -> None:
        client = FakeShoppingListClient()

        with self.assertRaises(nextcloud_tools.InvalidToolRequest):
            nextcloud_tools.resolve_shopping_list_item(client, 1, "eggs")

    def test_rejects_an_ambiguous_item_name(self) -> None:
        # Neither item is an exact match for "milk", so both substring
        # matches are genuinely ambiguous (an exact match, if one existed,
        # would short-circuit this and win instead).
        client = FakeShoppingListClient()
        client.items_result = [
            {"id": 10, "name": "Oat Milk", "quantity": None, "unit": None, "checked": False},
            {
                "id": 11,
                "name": "Chocolate Milk",
                "quantity": None,
                "unit": None,
                "checked": False,
            },
        ]

        with self.assertRaises(nextcloud_tools.InvalidToolRequest):
            nextcloud_tools.resolve_shopping_list_item(client, 1, "milk")


class ShoppingListHandlerTests(unittest.TestCase):
    """/v1/shopping/* executes immediately, matching /v1/write (ADR 0013/0014)."""

    def test_list_shopping_lists_returns_bounded_titles(self) -> None:
        result = nextcloud_tools.list_shopping_lists({}, FakeShoppingListClient())

        self.assertEqual(result["lists"], [{"title": "Groceries"}])

    def test_list_shopping_lists_rejects_unexpected_arguments(self) -> None:
        with self.assertRaises(nextcloud_tools.InvalidToolRequest):
            nextcloud_tools.list_shopping_lists({"list": "Groceries"}, FakeShoppingListClient())

    def test_list_shopping_list_items_reports_checked_state(self) -> None:
        result = nextcloud_tools.list_shopping_list_items({}, FakeShoppingListClient())

        self.assertEqual(result["list"], "Groceries")
        self.assertEqual(result["items"][0]["name"], "Milk")
        self.assertFalse(result["items"][0]["checked"])

    def test_add_creates_the_item_immediately(self) -> None:
        client = FakeShoppingListClient()

        result = nextcloud_tools.update_shopping_list(
            {"operation": "add", "list": None, "item": "Eggs", "quantity": None}, client
        )

        self.assertEqual(client.creates, [(1, "Eggs", None)])
        self.assertEqual(result["operation"], "add")
        self.assertIn("Eggs", result["summary"])

    def test_add_passes_through_an_explicit_quantity(self) -> None:
        client = FakeShoppingListClient()

        nextcloud_tools.update_shopping_list(
            {"operation": "add", "list": None, "item": "Milk", "quantity": "2"}, client
        )

        self.assertEqual(client.creates, [(1, "Milk", "2")])

    def test_quantity_is_rejected_for_non_add_operations(self) -> None:
        client = FakeShoppingListClient()

        with self.assertRaises(nextcloud_tools.InvalidToolRequest):
            nextcloud_tools.update_shopping_list(
                {"operation": "check", "list": None, "item": "Milk", "quantity": "2"}, client
            )

    def test_check_marks_the_resolved_item_bought(self) -> None:
        client = FakeShoppingListClient()

        result = nextcloud_tools.update_shopping_list(
            {"operation": "check", "list": None, "item": "milk", "quantity": None}, client
        )

        self.assertEqual(client.checks, [(1, 10, True)])
        self.assertIn("Milk", result["summary"])

    def test_uncheck_marks_the_resolved_item_not_bought(self) -> None:
        client = FakeShoppingListClient()

        nextcloud_tools.update_shopping_list(
            {"operation": "uncheck", "list": None, "item": "milk", "quantity": None}, client
        )

        self.assertEqual(client.checks, [(1, 10, False)])

    def test_remove_deletes_the_resolved_item(self) -> None:
        client = FakeShoppingListClient()

        result = nextcloud_tools.update_shopping_list(
            {"operation": "remove", "list": None, "item": "milk", "quantity": None}, client
        )

        self.assertEqual(client.deletes, [(1, 10)])
        self.assertIn("Milk", result["summary"])

    def test_rejects_an_unknown_operation(self) -> None:
        client = FakeShoppingListClient()

        with self.assertRaises(nextcloud_tools.InvalidToolRequest):
            nextcloud_tools.update_shopping_list(
                {"operation": "rename", "list": None, "item": "Milk", "quantity": None}, client
            )

    def test_rejects_an_oversized_item_name(self) -> None:
        client = FakeShoppingListClient()

        with self.assertRaises(nextcloud_tools.InvalidToolRequest):
            nextcloud_tools.update_shopping_list(
                {
                    "operation": "add",
                    "list": None,
                    "item": "x" * (nextcloud_tools.MAX_ITEM_NAME_CHARS + 1),
                    "quantity": None,
                },
                client,
            )

    def test_handle_tool_dispatches_the_three_shopping_routes(self) -> None:
        client = FakeShoppingListClient()

        listed = nextcloud_tools.handle_tool(
            "/v1/shopping/lists", {}, FakeClient(), client
        )
        items = nextcloud_tools.handle_tool(
            "/v1/shopping/items", {}, FakeClient(), client
        )
        written = nextcloud_tools.handle_tool(
            "/v1/shopping/write",
            {"operation": "add", "list": None, "item": "Eggs", "quantity": None},
            FakeClient(),
            client,
        )

        self.assertEqual(listed["lists"], [{"title": "Groceries"}])
        self.assertEqual(items["list"], "Groceries")
        self.assertEqual(written["operation"], "add")

    def test_handle_tool_rejects_shopping_routes_without_a_client(self) -> None:
        with self.assertRaises(nextcloud_tools.ToolUnavailable):
            nextcloud_tools.handle_tool("/v1/shopping/lists", {}, FakeClient())


class EndpointHostAllowlistTests(unittest.TestCase):
    """main() guards ENDPOINT_HOST against an arbitrary host, not just
    loopback -- defense in depth for an AI-agent-facing tool, widened to
    also admit the k3s VM's isolated-network address the k3s-native copy
    of this service (infra/k3s-apps) reaches Nextcloud AIO's Apache at.
    Confirmed live: the original bare loopback-only check broke that
    copy outright ("Nextcloud endpoint must remain on loopback").
    """

    def test_allowed_hosts_are_exactly_loopback_and_the_k3s_vm_address(self) -> None:
        self.assertEqual(
            nextcloud_tools.ALLOWED_ENDPOINT_HOSTS,
            frozenset({"127.0.0.1", "192.168.101.1"}),
        )

    def test_main_rejects_a_host_outside_the_allowlist(self) -> None:
        with patch.object(nextcloud_tools, "ENDPOINT_HOST", "10.0.0.1"):
            with self.assertRaisesRegex(RuntimeError, "allowlisted host"):
                nextcloud_tools.main()

    def test_main_accepts_the_k3s_vm_address_and_proceeds_past_the_guard(
        self,
    ) -> None:
        # Patched to a path that can't exist, so main() fails at the next
        # step (reading the app password) rather than actually starting a
        # server -- proves the endpoint guard let this host through
        # without asserting on anything past its own responsibility.
        with (
            patch.object(nextcloud_tools, "ENDPOINT_HOST", "192.168.101.1"),
            patch.object(
                nextcloud_tools, "APP_PASSWORD_FILE", "/nonexistent/app-password"
            ),
        ):
            with self.assertRaises(FileNotFoundError):
                nextcloud_tools.main()


if __name__ == "__main__":
    unittest.main()


class _UnixHTTPConnection(http.client.HTTPConnection):
    def __init__(self, socket_path: str) -> None:
        super().__init__("nextcloud-tools")
        self._socket_path = socket_path

    def connect(self) -> None:
        self.sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self.sock.connect(self._socket_path)


class MissingPathHttpTests(unittest.TestCase):
    """Run the real sidecar server: a missing path must not look like an outage."""

    def _post(self, client, path: str, payload: dict) -> tuple[int, dict]:
        tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, tmp, True)
        socket_path = f"{tmp}/s.sock"
        server = nextcloud_tools.ThreadingUnixServer(
            socket_path, nextcloud_tools.NextcloudToolsRequestHandler
        )
        server.client = client
        server.shopping_list_client = FakeShoppingListClient()
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        self.addCleanup(thread.join, 5)
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)

        connection = _UnixHTTPConnection(socket_path)
        connection.request(
            "POST",
            path,
            body=json.dumps(payload),
            headers={"Content-Type": "application/json"},
        )
        response = connection.getresponse()
        body = json.loads(response.read())
        connection.close()
        return response.status, body

    def test_missing_path_answers_404_not_found_with_a_hint(self) -> None:
        class Missing(FakeClient):
            def list_directory(self, path: str):
                raise nextcloud_tools.ToolNotFound("Nextcloud directory not found")

        status, body = self._post(Missing(), "/v1/list", {"path": "Nope"})

        self.assertEqual(status, 404)
        self.assertEqual(body["error"], "not_found")
        self.assertIn("not shared", body["message"])
        self.assertNotIn("Nope", json.dumps(body))

    def test_real_outage_still_answers_503_tool_unavailable(self) -> None:
        class Down(FakeClient):
            def list_directory(self, path: str):
                raise nextcloud_tools.ToolUnavailable(
                    "Nextcloud directory listing returned HTTP 500"
                )

        status, body = self._post(Down(), "/v1/list", {"path": "Photos"})

        self.assertEqual(status, 503)
        self.assertEqual(body["error"], "tool_unavailable")
        self.assertEqual(body["reason"], "upstream_http_500")
