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

    def test_wraps_a_connection_failure(self) -> None:
        with self.assertRaises(NextcloudToolsError):
            NextcloudToolsClient("/nonexistent/socket", timeout=1.0).call(
                "list_nextcloud_files", {"path": ""}
            )


if __name__ == "__main__":
    unittest.main()
