"""Client for the restricted home-tools Unix socket API."""

from __future__ import annotations

import json
from typing import Any

import httpx2

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

        transport = httpx2.HTTPTransport(uds=self.socket_path)
        try:
            with httpx2.Client(
                transport=transport, base_url="http://home-tools", timeout=self.timeout
            ) as client:
                with client.stream("GET", path) as response:
                    body = bytearray()
                    for chunk in response.iter_bytes():
                        body.extend(chunk)
                        if len(body) > MAX_TOOL_RESPONSE_BYTES:
                            raise HomeToolsError("tool response exceeded the size limit")
                    if response.status_code != 200:
                        raise HomeToolsError("tool is unavailable")
            payload = json.loads(bytes(body))
            if not isinstance(payload, dict):
                raise HomeToolsError("tool returned an unexpected response")
            return payload
        except (httpx2.HTTPError, json.JSONDecodeError) as error:
            raise HomeToolsError("tool request failed") from error
