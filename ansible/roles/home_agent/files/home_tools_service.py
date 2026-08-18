"""Expose a fixed set of sanitized, read-only host checks over a Unix socket."""

from __future__ import annotations

import http.client
import json
import logging
import os
import shutil
import socket
import socketserver
import subprocess
from collections.abc import Callable
from http.server import BaseHTTPRequestHandler
from pathlib import Path
from typing import Any

SOCKET_PATH = os.environ.get("HOME_TOOLS_SOCKET", "/run/home-tools/home-tools.sock")
DOCKER_SOCKET_PATH = os.environ.get("HOME_TOOLS_DOCKER_SOCKET", "/var/run/docker.sock")
FILESYSTEMS = tuple(
    path for path in os.environ.get("HOME_TOOLS_FILESYSTEMS", "/").split(",") if path
)
MAX_DOCKER_RESPONSE_BYTES = 4 * 1024 * 1024
LOGGER = logging.getLogger(__name__)


class ToolUnavailable(RuntimeError):
    """Indicate that a fixed local information source is unavailable."""


class UnixHTTPConnection(http.client.HTTPConnection):
    """Use HTTP over a configured Unix domain socket."""

    def __init__(self, socket_path: str, timeout: float = 5.0) -> None:
        super().__init__("localhost", timeout=timeout)
        self.socket_path = socket_path

    def connect(self) -> None:
        self.sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self.sock.settimeout(self.timeout)
        self.sock.connect(self.socket_path)


def get_system_health() -> dict[str, Any]:
    """Return uptime, processor count, and load averages."""
    try:
        uptime_seconds = int(float(Path("/proc/uptime").read_text().split()[0]))
        load_1m, load_5m, load_15m = os.getloadavg()
    except (OSError, ValueError) as error:
        raise ToolUnavailable("system metrics are unavailable") from error

    return {
        "hostname": socket.gethostname(),
        "uptime_seconds": uptime_seconds,
        "cpu_count": os.cpu_count(),
        "load": {
            "1m": round(load_1m, 2),
            "5m": round(load_5m, 2),
            "15m": round(load_15m, 2),
        },
    }


def get_memory_usage() -> dict[str, int]:
    """Return selected values from procfs without exposing other process data."""
    wanted = {"MemTotal", "MemAvailable", "SwapTotal", "SwapFree"}
    values: dict[str, int] = {}
    try:
        for line in Path("/proc/meminfo").read_text().splitlines():
            key, raw_value = line.split(":", 1)
            if key in wanted:
                values[key] = int(raw_value.strip().split()[0]) * 1024
    except (OSError, ValueError) as error:
        raise ToolUnavailable("memory metrics are unavailable") from error

    if set(values) != wanted:
        raise ToolUnavailable("memory metrics are incomplete")

    return {
        "total_bytes": values["MemTotal"],
        "available_bytes": values["MemAvailable"],
        "used_bytes": values["MemTotal"] - values["MemAvailable"],
        "swap_total_bytes": values["SwapTotal"],
        "swap_used_bytes": values["SwapTotal"] - values["SwapFree"],
    }


def get_disk_usage() -> dict[str, list[dict[str, Any]]]:
    """Return usage only for explicitly configured filesystems."""
    filesystems = []
    for mount_point in FILESYSTEMS:
        try:
            usage = shutil.disk_usage(mount_point)
        except OSError as error:
            raise ToolUnavailable(f"filesystem unavailable: {mount_point}") from error

        used_percent = round((usage.used / usage.total) * 100, 1)
        filesystems.append(
            {
                "mount": mount_point,
                "total_bytes": usage.total,
                "used_bytes": usage.used,
                "available_bytes": usage.free,
                "used_percent": used_percent,
            }
        )
    return {"filesystems": filesystems}


def get_systemd_failures() -> dict[str, list[dict[str, str]]]:
    """Return failed unit names and descriptions from a fixed systemctl query."""
    try:
        result = subprocess.run(
            [
                "/usr/bin/systemctl",
                "list-units",
                "--state=failed",
                "--no-legend",
                "--plain",
                "--no-pager",
            ],
            check=True,
            capture_output=True,
            text=True,
            timeout=5,
        )
    except (OSError, subprocess.SubprocessError) as error:
        raise ToolUnavailable("systemd status is unavailable") from error

    units = []
    for line in result.stdout.splitlines():
        columns = line.split(None, 4)
        if len(columns) >= 4:
            units.append(
                {
                    "unit": columns[0],
                    "active": columns[2],
                    "sub": columns[3],
                    "description": columns[4][:200] if len(columns) == 5 else "",
                }
            )
    return {"failed_units": units}


def sanitize_containers(containers: Any) -> dict[str, list[dict[str, str]]]:
    """Extract only the Docker fields approved for model consumption."""
    if not isinstance(containers, list):
        raise ToolUnavailable("Docker returned an unexpected response")

    sanitized = []
    for container in containers:
        if not isinstance(container, dict):
            continue
        names = container.get("Names", [])
        name = names[0].lstrip("/") if isinstance(names, list) and names else "unknown"
        sanitized.append(
            {
                "name": str(name)[:128],
                "image": str(container.get("Image", "unknown"))[:256],
                "state": str(container.get("State", "unknown"))[:32],
                "status": str(container.get("Status", "unknown"))[:256],
            }
        )
    return {"containers": sanitized}


def get_docker_status() -> dict[str, list[dict[str, str]]]:
    """Query Docker locally and discard labels, mounts, ports, and environment."""
    connection = UnixHTTPConnection(DOCKER_SOCKET_PATH)
    try:
        connection.request("GET", "/v1.41/containers/json?all=1")
        response = connection.getresponse()
        if response.status != 200:
            raise ToolUnavailable("Docker status query failed")
        body = response.read(MAX_DOCKER_RESPONSE_BYTES + 1)
        if len(body) > MAX_DOCKER_RESPONSE_BYTES:
            raise ToolUnavailable("Docker response exceeded the size limit")
        return sanitize_containers(json.loads(body))
    except (OSError, http.client.HTTPException, json.JSONDecodeError) as error:
        raise ToolUnavailable("Docker status is unavailable") from error
    finally:
        connection.close()


TOOLS: dict[str, Callable[[], dict[str, Any]]] = {
    "/v1/system": get_system_health,
    "/v1/memory": get_memory_usage,
    "/v1/disks": get_disk_usage,
    "/v1/systemd/failed": get_systemd_failures,
    "/v1/docker/containers": get_docker_status,
}


def get_combined_health() -> dict[str, Any]:
    """Run every approved check and keep individual failures sanitized."""
    checks: dict[str, Any] = {}
    overall_status = "ok"
    for path, tool in TOOLS.items():
        name = path.removeprefix("/v1/").replace("/", "_")
        try:
            checks[name] = {"status": "ok", "data": tool()}
        except ToolUnavailable:
            overall_status = "degraded"
            checks[name] = {"status": "unavailable"}
    return {"status": overall_status, "checks": checks}


class HomeToolsRequestHandler(BaseHTTPRequestHandler):
    """Serve only exact, argument-free GET endpoints."""

    server_version = "home-tools/1"

    def do_GET(self) -> None:
        path = self.path.split("?", 1)[0]
        if path == "/v1/health":
            self._send_json(200, get_combined_health())
            return

        tool = TOOLS.get(path)
        if tool is None:
            self._send_json(404, {"error": "not_found"})
            return

        try:
            self._send_json(200, tool())
        except ToolUnavailable:
            self._send_json(503, {"error": "tool_unavailable"})

    def do_POST(self) -> None:
        self._send_json(405, {"error": "method_not_allowed"})

    def _send_json(self, status: int, payload: dict[str, Any]) -> None:
        body = json.dumps(payload, separators=(",", ":")).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, message_format: str, *args: Any) -> None:
        LOGGER.info("home-tools request: " + message_format, *args)


class ThreadingUnixServer(socketserver.ThreadingMixIn, socketserver.UnixStreamServer):
    daemon_threads = True


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    socket_path = Path(SOCKET_PATH)
    socket_path.parent.mkdir(parents=True, exist_ok=True)
    socket_path.unlink(missing_ok=True)
    try:
        with ThreadingUnixServer(str(socket_path), HomeToolsRequestHandler) as server:
            os.chmod(socket_path, 0o660)
            server.serve_forever()
    finally:
        socket_path.unlink(missing_ok=True)


if __name__ == "__main__":
    main()
