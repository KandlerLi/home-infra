"""Client for the restricted home-tools Unix socket API."""

from __future__ import annotations

from typing import Any

from .unix_socket_client import call_unix_socket_json

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


class HomeToolsClient:
    """Call only predeclared, argument-free home-tools endpoints."""

    def __init__(self, socket_path: str, timeout: float = 5.0) -> None:
        self.socket_path = socket_path
        self.timeout = timeout

    def call(self, tool_name: str) -> dict[str, Any]:
        path = TOOL_PATHS.get(tool_name)
        if path is None:
            raise HomeToolsError("unknown tool")

        return call_unix_socket_json(
            socket_path=self.socket_path,
            base_url="http://home-tools",
            method="GET",
            path=path,
            timeout=self.timeout,
            max_response_bytes=MAX_TOOL_RESPONSE_BYTES,
            error_cls=HomeToolsError,
        )
