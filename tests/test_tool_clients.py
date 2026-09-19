from __future__ import annotations

import json
import socketserver
import sys
import tempfile
import unittest
from http.server import BaseHTTPRequestHandler
from pathlib import Path
from threading import Thread

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "ansible/roles/home_agent/files/agent"))

from home_agent.home_tools import HomeToolsClient, HomeToolsError
from home_agent.nextcloud_tools import NextcloudToolsClient, NextcloudToolsError


class _ScriptedUnixServer(socketserver.ThreadingMixIn, socketserver.UnixStreamServer):
    daemon_threads = True


def _fake_socket_server(status: int, body: bytes):
    """Start a Unix-socket HTTP server that always answers with (status, body)."""

    class Handler(BaseHTTPRequestHandler):
        def _respond(self) -> None:
            if self.command == "POST":
                length = int(self.headers.get("Content-Length", 0))
                self.rfile.read(length)
            self.send_response(status)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self) -> None:  # noqa: N802 (BaseHTTPRequestHandler API)
            self._respond()

        def do_POST(self) -> None:  # noqa: N802 (BaseHTTPRequestHandler API)
            self._respond()

        def log_message(self, *args: object) -> None:
            pass

    socket_dir = tempfile.mkdtemp()
    socket_path = str(Path(socket_dir) / "test.sock")
    server = _ScriptedUnixServer(socket_path, Handler)
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return server, thread, socket_path


class HomeToolsClientTests(unittest.TestCase):
    def _serve(self, status: int, body: bytes):
        server, thread, socket_path = _fake_socket_server(status, body)
        self.addCleanup(server.shutdown)
        self.addCleanup(server.server_close)
        self.addCleanup(thread.join, timeout=2)
        return socket_path

    def test_returns_the_decoded_payload_on_success(self) -> None:
        socket_path = self._serve(200, b'{"hostname": "homeserver"}')

        result = HomeToolsClient(socket_path).call("get_system_health")

        self.assertEqual(result, {"hostname": "homeserver"})

    def test_rejects_an_unknown_tool_without_connecting(self) -> None:
        with self.assertRaises(HomeToolsError):
            HomeToolsClient("/nonexistent/socket").call("delete_everything")

    def test_wraps_a_non_200_status(self) -> None:
        socket_path = self._serve(503, b"{}")

        with self.assertRaises(HomeToolsError):
            HomeToolsClient(socket_path).call("get_system_health")

    def test_does_not_pass_a_404_through_even_with_an_error_body(self) -> None:
        socket_path = self._serve(404, b'{"error":"not_found"}')

        with self.assertRaises(HomeToolsError):
            HomeToolsClient(socket_path).call("get_system_health")

    def test_wraps_an_oversized_response(self) -> None:
        from home_agent import home_tools

        oversized = b"x" * (home_tools.MAX_TOOL_RESPONSE_BYTES + 1)
        socket_path = self._serve(200, json.dumps({"padding": oversized.decode()}).encode())

        with self.assertRaises(HomeToolsError):
            HomeToolsClient(socket_path).call("get_system_health")

    def test_wraps_a_non_dict_payload(self) -> None:
        socket_path = self._serve(200, b"[1, 2, 3]")

        with self.assertRaises(HomeToolsError):
            HomeToolsClient(socket_path).call("get_system_health")

    def test_wraps_a_connection_failure(self) -> None:
        with self.assertRaises(HomeToolsError):
            HomeToolsClient("/nonexistent/socket", timeout=1.0).call("get_system_health")


class NextcloudToolsClientTests(unittest.TestCase):
    def _serve(self, status: int, body: bytes):
        server, thread, socket_path = _fake_socket_server(status, body)
        self.addCleanup(server.shutdown)
        self.addCleanup(server.server_close)
        self.addCleanup(thread.join, timeout=2)
        return socket_path

    def test_returns_the_decoded_payload_on_success(self) -> None:
        socket_path = self._serve(200, b'{"entries": []}')

        result = NextcloudToolsClient(socket_path).call(
            "list_nextcloud_files", {"path": ""}
        )

        self.assertEqual(result, {"entries": []})

    def test_rejects_an_unknown_tool_without_connecting(self) -> None:
        with self.assertRaises(NextcloudToolsError):
            NextcloudToolsClient("/nonexistent/socket").call("delete_everything", {})

    def test_rejects_oversized_arguments_without_connecting(self) -> None:
        with self.assertRaises(NextcloudToolsError):
            NextcloudToolsClient("/nonexistent/socket").call(
                "search_nextcloud_files", {"query": "x" * 5000, "path": ""}
            )

    def test_wraps_a_non_200_status(self) -> None:
        socket_path = self._serve(404, b"{}")

        with self.assertRaises(NextcloudToolsError):
            NextcloudToolsClient(socket_path).call("list_nextcloud_files", {"path": ""})

    def test_a_structured_not_found_reaches_the_model_as_data(self) -> None:
        socket_path = self._serve(
            404, b'{"error":"not_found","message":"not shared with this account"}'
        )

        result = NextcloudToolsClient(socket_path).call(
            "list_nextcloud_files", {"path": "Nope"}
        )

        self.assertEqual(result["error"], "not_found")

    def test_a_503_stays_an_outage_even_with_an_error_body(self) -> None:
        socket_path = self._serve(503, b'{"error":"tool_unavailable"}')

        with self.assertRaises(NextcloudToolsError):
            NextcloudToolsClient(socket_path).call("list_nextcloud_files", {"path": ""})

    def test_wraps_a_connection_failure(self) -> None:
        with self.assertRaises(NextcloudToolsError):
            NextcloudToolsClient("/nonexistent/socket", timeout=1.0).call(
                "list_nextcloud_files", {"path": ""}
            )

    def test_write_tool_is_wired_to_its_endpoint(self) -> None:
        from home_agent import nextcloud_tools

        self.assertEqual(
            nextcloud_tools.TOOL_PATHS["write_nextcloud_file"], "/v1/write"
        )

    def test_write_tool_reaches_the_write_endpoint(self) -> None:
        socket_path = self._serve(
            200, b'{"operation": "create", "path": "note.txt"}'
        )

        result = NextcloudToolsClient(socket_path).call(
            "write_nextcloud_file",
            {
                "operation": "create",
                "path": "note.txt",
                "content": "hi",
                "destination_path": None,
            },
        )

        self.assertEqual(result["operation"], "create")

    def test_write_content_bypasses_the_small_argument_cap(self) -> None:
        # 5000 bytes exceeds the 4096-byte cap other tools use, but must
        # not be rejected locally for write_nextcloud_file -- it should
        # get far enough to attempt a (failing) connection instead.
        with self.assertRaises(NextcloudToolsError) as raised:
            NextcloudToolsClient("/nonexistent/socket").call(
                "write_nextcloud_file",
                {
                    "operation": "create",
                    "path": "note.txt",
                    "content": "x" * 5000,
                    "destination_path": None,
                },
            )

        self.assertEqual(str(raised.exception), "tool request failed")

    def test_shopping_list_tools_are_wired_to_their_endpoints(self) -> None:
        from home_agent import nextcloud_tools

        self.assertEqual(
            nextcloud_tools.TOOL_PATHS["list_shopping_lists"], "/v1/shopping/lists"
        )
        self.assertEqual(
            nextcloud_tools.TOOL_PATHS["list_shopping_list_items"],
            "/v1/shopping/items",
        )
        self.assertEqual(
            nextcloud_tools.TOOL_PATHS["update_shopping_list"], "/v1/shopping/write"
        )

    def test_document_tool_allows_a_larger_response_than_other_tools(self) -> None:
        big = json.dumps({"document_type": "pdf", "data_base64": "A" * (2 * 1024 * 1024)})
        socket_path = self._serve(200, big.encode("utf-8"))

        result = NextcloudToolsClient(socket_path).call(
            "read_nextcloud_document", {"path": "a.pdf"}
        )
        self.assertEqual(result["document_type"], "pdf")

        with self.assertRaises(NextcloudToolsError):
            NextcloudToolsClient(socket_path).call(
                "read_nextcloud_text_file", {"path": "a.md"}
            )

    def test_update_shopping_list_reaches_the_write_endpoint(self) -> None:
        socket_path = self._serve(
            200, b'{"operation": "add", "list": "Groceries", "item": "Milk"}'
        )

        result = NextcloudToolsClient(socket_path).call(
            "update_shopping_list",
            {"operation": "add", "list": None, "item": "Milk", "quantity": None},
        )

        self.assertEqual(result["operation"], "add")

    def test_rejects_oversized_write_content_without_connecting(self) -> None:
        from home_agent import nextcloud_tools

        with self.assertRaises(NextcloudToolsError) as raised:
            NextcloudToolsClient("/nonexistent/socket").call(
                "write_nextcloud_file",
                {
                    "operation": "create",
                    "path": "note.txt",
                    "content": "x" * nextcloud_tools.MAX_WRITE_ARGUMENT_BYTES,
                    "destination_path": None,
                },
            )

        self.assertEqual(
            str(raised.exception), "tool arguments exceeded the size limit"
        )


if __name__ == "__main__":
    unittest.main()
