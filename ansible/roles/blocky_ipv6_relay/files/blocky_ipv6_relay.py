"""Bridge IPv6 DNS queries to Blocky's IPv4-only address inside k3s.

Blocky (infra/k3s-apps, modules/blocky/) only has an IPv4 address
inside the k3s VM's own isolated network -- plain DNAT can't bridge
address families, so this small stdlib-only relay does it directly:
listens on IPv6 (both UDP and TCP, matching DNS's own protocol needs --
DNS falls back to TCP for responses too large for a single UDP
datagram), forwards each query byte-for-byte to Blocky's fixed IPv4
target over a fresh IPv4 connection, and relays the reply straight
back. On a backend failure, replies SERVFAIL rather than dropping the
query silently.
"""

from __future__ import annotations

import logging
import os
import socket
import socketserver
import struct
import threading

LOGGER = logging.getLogger(__name__)

BACKEND_HOST = os.environ.get("BACKEND_HOST", "192.168.101.10")
BACKEND_PORT = int(os.environ.get("BACKEND_PORT", "53"))
LISTEN_PORT = int(os.environ.get("LISTEN_PORT", "53"))
FORWARD_TIMEOUT_SECONDS = float(os.environ.get("FORWARD_TIMEOUT_SECONDS", "5"))


def build_servfail(query: bytes) -> bytes:
    """Flip a DNS query's own header into a minimal SERVFAIL reply.

    No DNS-parsing library needed for this -- the 12-byte header is a
    fixed prefix regardless of the question section that follows it.
    Byte 2 holds QR/Opcode/AA/TC/RD; setting the high bit (0x80) turns
    a query into a response. Byte 3 holds RA/Z/RCODE; RCODE occupies
    the low nibble, and SERVFAIL is RCODE 2.
    """
    if len(query) < 12:
        return query
    header = bytearray(query[:12])
    header[2] |= 0x80
    header[3] = (header[3] & 0xF0) | 0x02
    return bytes(header) + query[12:]


def _recv_exact(sock: socket.socket, count: int) -> bytes:
    chunks = []
    remaining = count
    while remaining > 0:
        chunk = sock.recv(remaining)
        if not chunk:
            raise ConnectionError("backend closed connection early")
        chunks.append(chunk)
        remaining -= len(chunk)
    return b"".join(chunks)


def forward_udp(query: bytes) -> bytes:
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as backend:
        backend.settimeout(FORWARD_TIMEOUT_SECONDS)
        backend.sendto(query, (BACKEND_HOST, BACKEND_PORT))
        reply, _ = backend.recvfrom(65535)
        return reply


def forward_tcp(query: bytes) -> bytes:
    # DNS-over-TCP length-prefixes every message with a 2-byte
    # big-endian length (RFC 1035 4.2.2) -- framing is this function's
    # own job, independent of TcpHandler's own framing on the client
    # side.
    with socket.create_connection(
        (BACKEND_HOST, BACKEND_PORT), timeout=FORWARD_TIMEOUT_SECONDS
    ) as backend:
        backend.sendall(struct.pack("!H", len(query)) + query)
        (reply_length,) = struct.unpack("!H", _recv_exact(backend, 2))
        return _recv_exact(backend, reply_length)


class UdpHandler(socketserver.BaseRequestHandler):
    def handle(self) -> None:
        query, client_socket = self.request
        try:
            reply = forward_udp(query)
        except Exception:
            LOGGER.warning(
                "UDP forward to backend failed, replying SERVFAIL", exc_info=True
            )
            reply = build_servfail(query)
        client_socket.sendto(reply, self.client_address)


class TcpHandler(socketserver.BaseRequestHandler):
    def handle(self) -> None:
        try:
            (query_length,) = struct.unpack("!H", _recv_exact(self.request, 2))
            query = _recv_exact(self.request, query_length)
        except (ConnectionError, struct.error, OSError):
            LOGGER.warning("Malformed DNS-over-TCP request, dropping", exc_info=True)
            return

        try:
            reply = forward_tcp(query)
        except Exception:
            LOGGER.warning(
                "TCP forward to backend failed, replying SERVFAIL", exc_info=True
            )
            reply = build_servfail(query)

        self.request.sendall(struct.pack("!H", len(reply)) + reply)


class Ipv6OnlyMixin:
    """Disable the Linux default of dual-stack (v4-mapped) binding.

    Without this, a socket bound to "::" would also silently answer
    IPv4 queries -- this service's whole reason to exist is the IPv6
    side specifically (IPv4 DNS already goes through
    k3s_ingress_forward's own DNAT relay directly), and the ip6tables
    INPUT rule this role installs only restricts IPv6 traffic. An
    accidental dual-stack listener would bypass that restriction
    entirely for IPv4 clients.
    """

    def server_bind(self) -> None:
        self.socket.setsockopt(socket.IPPROTO_IPV6, socket.IPV6_V6ONLY, 1)
        super().server_bind()


class Ipv6ThreadingUDPServer(Ipv6OnlyMixin, socketserver.ThreadingUDPServer):
    address_family = socket.AF_INET6


class Ipv6ThreadingTCPServer(Ipv6OnlyMixin, socketserver.ThreadingTCPServer):
    address_family = socket.AF_INET6
    allow_reuse_address = True


def main() -> None:
    logging.basicConfig(level=logging.INFO)
    udp_server = Ipv6ThreadingUDPServer(("::", LISTEN_PORT), UdpHandler)
    tcp_server = Ipv6ThreadingTCPServer(("::", LISTEN_PORT), TcpHandler)
    # Two independent server instances, not one socket serving both
    # protocols -- UDP and TCP DNS servers are different socket types
    # entirely. TCP runs in a background thread so UDP (the common
    # case for ordinary queries) owns the main thread via its own
    # blocking serve_forever(), matching ntfy_relay.py's own
    # dual-listener shape.
    threading.Thread(target=tcp_server.serve_forever, daemon=True).start()
    udp_server.serve_forever()


if __name__ == "__main__":
    main()
