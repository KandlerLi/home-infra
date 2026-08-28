"""Shared HTTP-over-Unix-socket call used by every tool client below.

home_tools.py and nextcloud_tools.py each talk to a locked-down local
service over a Unix socket: send one request, stream a size-capped
response, and require it to decode as a JSON object. This module holds
that one pattern so it's implemented once instead of copy-pasted per
client (the two services' own server-side code already share their
equivalent request helper -- see nextcloud_tools_service.py's
_send_http_request).
"""

from __future__ import annotations

import json
from typing import Any

import httpx2


def call_unix_socket_json(
    *,
    socket_path: str,
    base_url: str,
    method: str,
    path: str,
    timeout: float,
    max_response_bytes: int,
    error_cls: type[Exception],
    body: bytes | None = None,
    headers: dict[str, str] | None = None,
) -> dict[str, Any]:
    """Send one request over a Unix socket and return its decoded JSON object.

    Raises error_cls (with a short, caller-safe message) for a connection
    failure, a non-200 status, an oversized response, or a body that isn't
    a JSON object.
    """
    transport = httpx2.HTTPTransport(uds=socket_path)
    try:
        with httpx2.Client(
            transport=transport, base_url=base_url, timeout=timeout
        ) as client:
            with client.stream(
                method, path, content=body, headers=headers
            ) as response:
                response_body = bytearray()
                for chunk in response.iter_bytes():
                    response_body.extend(chunk)
                    if len(response_body) > max_response_bytes:
                        raise error_cls("tool response exceeded the size limit")
                if response.status_code != 200:
                    raise error_cls("tool is unavailable")
        payload = json.loads(bytes(response_body))
        if not isinstance(payload, dict):
            raise error_cls("tool returned an unexpected response")
        return payload
    except (httpx2.HTTPError, json.JSONDecodeError) as error:
        raise error_cls("tool request failed") from error
