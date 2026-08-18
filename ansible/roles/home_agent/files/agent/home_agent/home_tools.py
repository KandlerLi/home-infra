"""Client for the restricted home-tools Unix socket API."""

from __future__ import annotations

import http.client
import json
import socket
from typing import Any

MAX_TOOL_RESPONSE_BYTES = 4 * 1024 * 1024

TOOL_PATHS = {
    "get_system_health": "/v1/health",
    "get_cpu_and_load": "/v1/system",
    "get_memory_usage": "/v1/memory",
    "get_disk_usage": "/v1/disks",
    "get_systemd_failures": "/v1/systemd/failed",
    "get_docker_status": "/v1/docker/containers",
}


class HomeToolsError(RuntimeError):
    """Indicate that the restricted host service could not answer."""


class UnixHTTPConnection(http.client.HTTPConnection):
    def __init__(self, socket_path: str, timeout: float = 5.0) -> None:
        super().__init__("home-tools", timeout=timeout)
        self.socket_path = socket_path

    def connect(self) -> None:
        self.sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self.sock.settimeout(self.timeout)
        self.sock.connect(self.socket_path)


class HomeToolsClient:
    """Call only predeclared, argument-free home-tools endpoints."""

    def __init__(self, socket_path: str) -> None:
        self.socket_path = socket_path

    def call(self, tool_name: str) -> dict[str, Any]:
        path = TOOL_PATHS.get(tool_name)
        if path is None:
            raise HomeToolsError("unknown tool")

        connection = UnixHTTPConnection(self.socket_path)
        try:
            connection.request("GET", path)
            response = connection.getresponse()
            body = response.read(MAX_TOOL_RESPONSE_BYTES + 1)
            if len(body) > MAX_TOOL_RESPONSE_BYTES:
                raise HomeToolsError("tool response exceeded the size limit")
            if response.status != 200:
                raise HomeToolsError("tool is unavailable")
            payload = json.loads(body)
            if not isinstance(payload, dict):
                raise HomeToolsError("tool returned an unexpected response")
            return payload
        except (OSError, http.client.HTTPException, json.JSONDecodeError) as error:
            raise HomeToolsError("tool request failed") from error
        finally:
            connection.close()
