from __future__ import annotations

import socket
import struct
import unittest
from pathlib import Path
from unittest import mock

import yaml
from jinja2 import Environment, FileSystemLoader

from _load_module import load_module_from_path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
ROLE_ROOT = PROJECT_ROOT / "ansible/roles/blocky_ipv6_relay"

relay = load_module_from_path(
    "blocky_ipv6_relay", "ansible/roles/blocky_ipv6_relay/files/blocky_ipv6_relay.py"
)


def render(**overrides: object) -> str:
    env = Environment(loader=FileSystemLoader(str(ROLE_ROOT / "templates")))
    defaults = yaml.safe_load((ROLE_ROOT / "defaults/main.yml").read_text())
    ctx = {**defaults, **overrides}
    return env.get_template("blocky-ipv6-relay.service.j2").render(**ctx)


def make_query(*, qr: int = 0, rcode: int = 0) -> bytes:
    # Minimal well-formed-enough DNS header (12 bytes) + a throwaway
    # question section, matching real query shape closely enough for
    # build_servfail's own byte-flipping logic to exercise correctly.
    flags = (qr << 15) | rcode
    header = struct.pack("!HHHHHH", 0x1234, flags, 1, 0, 0, 0)
    question = b"\x07example\x03com\x00" + struct.pack("!HH", 1, 1)
    return header + question


class BuildServfailTests(unittest.TestCase):
    def test_sets_the_response_bit_and_servfail_rcode(self) -> None:
        query = make_query()

        reply = relay.build_servfail(query)

        flags = struct.unpack("!H", reply[2:4])[0]
        self.assertTrue(flags & 0x8000, "QR bit not set")
        self.assertEqual(flags & 0x000F, 2, "RCODE is not SERVFAIL (2)")

    def test_preserves_the_question_section_untouched(self) -> None:
        query = make_query()

        reply = relay.build_servfail(query)

        self.assertEqual(reply[12:], query[12:])
        self.assertEqual(len(reply), len(query))

    def test_preserves_the_query_id(self) -> None:
        query = make_query()

        reply = relay.build_servfail(query)

        self.assertEqual(reply[0:2], query[0:2])

    def test_short_input_is_returned_unchanged_not_crashed_on(self) -> None:
        short = b"\x01\x02\x03"

        self.assertEqual(relay.build_servfail(short), short)


class RecvExactTests(unittest.TestCase):
    def test_assembles_chunks_until_count_is_reached(self) -> None:
        sock = mock.Mock()
        sock.recv.side_effect = [b"ab", b"cd", b"e"]

        result = relay._recv_exact(sock, 5)

        self.assertEqual(result, b"abcde")

    def test_raises_connection_error_on_early_close(self) -> None:
        sock = mock.Mock()
        sock.recv.side_effect = [b"ab", b""]

        with self.assertRaises(ConnectionError):
            relay._recv_exact(sock, 5)


class ForwardUdpTests(unittest.TestCase):
    def test_sends_query_verbatim_and_returns_the_reply(self) -> None:
        query = make_query()
        reply = make_query(qr=1)
        fake_socket = mock.MagicMock()
        fake_socket.__enter__.return_value = fake_socket
        fake_socket.recvfrom.return_value = (reply, ("192.168.101.10", 53))

        with mock.patch.object(relay.socket, "socket", return_value=fake_socket):
            result = relay.forward_udp(query)

        fake_socket.sendto.assert_called_once_with(
            query, (relay.BACKEND_HOST, relay.BACKEND_PORT)
        )
        self.assertEqual(result, reply)

    def test_sets_a_bounded_timeout(self) -> None:
        fake_socket = mock.MagicMock()
        fake_socket.__enter__.return_value = fake_socket
        fake_socket.recvfrom.return_value = (make_query(qr=1), ("x", 53))

        with mock.patch.object(relay.socket, "socket", return_value=fake_socket):
            relay.forward_udp(make_query())

        fake_socket.settimeout.assert_called_once_with(relay.FORWARD_TIMEOUT_SECONDS)


class ForwardTcpTests(unittest.TestCase):
    def test_frames_the_query_and_returns_the_unframed_reply(self) -> None:
        query = make_query()
        reply = make_query(qr=1)
        fake_socket = mock.MagicMock()
        fake_socket.__enter__.return_value = fake_socket
        # First recv call in _recv_exact reads the 2-byte length
        # prefix, subsequent call(s) read the reply body.
        fake_socket.recv.side_effect = [struct.pack("!H", len(reply)), reply]

        with mock.patch.object(
            relay.socket, "create_connection", return_value=fake_socket
        ) as create_connection:
            result = relay.forward_tcp(query)

        create_connection.assert_called_once_with(
            (relay.BACKEND_HOST, relay.BACKEND_PORT),
            timeout=relay.FORWARD_TIMEOUT_SECONDS,
        )
        fake_socket.sendall.assert_called_once_with(
            struct.pack("!H", len(query)) + query
        )
        self.assertEqual(result, reply)


class UdpHandlerTests(unittest.TestCase):
    def test_relays_a_successful_backend_reply_verbatim(self) -> None:
        query = make_query()
        reply = make_query(qr=1)
        client_socket = mock.Mock()
        handler = object.__new__(relay.UdpHandler)
        handler.request = (query, client_socket)
        handler.client_address = ("fd00::1", 54321)

        with mock.patch.object(relay, "forward_udp", return_value=reply):
            handler.handle()

        client_socket.sendto.assert_called_once_with(reply, ("fd00::1", 54321))

    def test_replies_servfail_when_the_backend_forward_fails(self) -> None:
        query = make_query()
        client_socket = mock.Mock()
        handler = object.__new__(relay.UdpHandler)
        handler.request = (query, client_socket)
        handler.client_address = ("fd00::1", 54321)

        with mock.patch.object(relay, "forward_udp", side_effect=OSError("timed out")):
            handler.handle()

        client_socket.sendto.assert_called_once_with(
            relay.build_servfail(query), ("fd00::1", 54321)
        )

    def test_replies_servfail_without_forwarding_once_the_concurrency_limit_is_hit(
        self,
    ) -> None:
        query = make_query()
        client_socket = mock.Mock()
        handler = object.__new__(relay.UdpHandler)
        handler.request = (query, client_socket)
        handler.client_address = ("fd00::1", 54321)

        held = [
            relay.FORWARD_SLOTS.acquire(blocking=False)
            for _ in range(relay.MAX_CONCURRENT_FORWARDS)
        ]
        try:
            with mock.patch.object(relay, "forward_udp") as forward_udp:
                handler.handle()
            forward_udp.assert_not_called()
        finally:
            for acquired in held:
                if acquired:
                    relay.FORWARD_SLOTS.release()

        client_socket.sendto.assert_called_once_with(
            relay.build_servfail(query), ("fd00::1", 54321)
        )

    def test_the_slot_is_released_after_every_forward_so_it_never_leaks(self) -> None:
        query = make_query()
        reply = make_query(qr=1)
        client_socket = mock.Mock()

        with mock.patch.object(relay, "forward_udp", return_value=reply):
            for _ in range(relay.MAX_CONCURRENT_FORWARDS + 1):
                handler = object.__new__(relay.UdpHandler)
                handler.request = (query, client_socket)
                handler.client_address = ("fd00::1", 54321)
                handler.handle()

        self.assertEqual(
            client_socket.sendto.call_count, relay.MAX_CONCURRENT_FORWARDS + 1
        )
        for call in client_socket.sendto.call_args_list:
            self.assertEqual(call.args[0], reply)


class TcpHandlerTests(unittest.TestCase):
    def test_relays_a_successful_backend_reply_framed(self) -> None:
        query = make_query()
        reply = make_query(qr=1)
        client_conn = mock.Mock()
        client_conn.recv.side_effect = [struct.pack("!H", len(query)), query]
        handler = object.__new__(relay.TcpHandler)
        handler.request = client_conn

        with mock.patch.object(relay, "forward_tcp", return_value=reply):
            handler.handle()

        client_conn.sendall.assert_called_once_with(
            struct.pack("!H", len(reply)) + reply
        )

    def test_replies_servfail_when_the_backend_forward_fails(self) -> None:
        query = make_query()
        client_conn = mock.Mock()
        client_conn.recv.side_effect = [struct.pack("!H", len(query)), query]
        handler = object.__new__(relay.TcpHandler)
        handler.request = client_conn

        with mock.patch.object(relay, "forward_tcp", side_effect=OSError("refused")):
            handler.handle()

        expected = relay.build_servfail(query)
        client_conn.sendall.assert_called_once_with(
            struct.pack("!H", len(expected)) + expected
        )

    def test_replies_servfail_without_forwarding_once_the_concurrency_limit_is_hit(
        self,
    ) -> None:
        query = make_query()
        client_conn = mock.Mock()
        client_conn.recv.side_effect = [struct.pack("!H", len(query)), query]
        handler = object.__new__(relay.TcpHandler)
        handler.request = client_conn

        held = [
            relay.FORWARD_SLOTS.acquire(blocking=False)
            for _ in range(relay.MAX_CONCURRENT_FORWARDS)
        ]
        try:
            with mock.patch.object(relay, "forward_tcp") as forward_tcp:
                handler.handle()
            forward_tcp.assert_not_called()
        finally:
            for acquired in held:
                if acquired:
                    relay.FORWARD_SLOTS.release()

        expected = relay.build_servfail(query)
        client_conn.sendall.assert_called_once_with(
            struct.pack("!H", len(expected)) + expected
        )

    def test_a_malformed_request_is_dropped_not_crashed_on(self) -> None:
        client_conn = mock.Mock()
        # Early close mid-length-prefix -- _recv_exact raises
        # ConnectionError, which TcpHandler must catch, not propagate.
        client_conn.recv.side_effect = [b""]
        handler = object.__new__(relay.TcpHandler)
        handler.request = client_conn

        handler.handle()  # must not raise

        client_conn.sendall.assert_not_called()


class Ipv6OnlyBindingTests(unittest.TestCase):
    # A real, live integration check, not a mock -- binds an actual
    # ephemeral port on ::1 (no root needed) and confirms the socket
    # genuinely refuses IPv4-mapped connections, the exact property
    # blocky_ipv6_relay's own ip6tables restriction depends on (see
    # Ipv6OnlyMixin's own docstring).
    def test_tcp_server_rejects_ipv4_mapped_connections(self) -> None:
        server = relay.Ipv6ThreadingTCPServer(("::1", 0), relay.TcpHandler)
        try:
            self.assertEqual(
                server.socket.getsockopt(socket.IPPROTO_IPV6, socket.IPV6_V6ONLY),
                1,
            )
        finally:
            server.server_close()

    def test_udp_server_is_ipv6_only(self) -> None:
        server = relay.Ipv6ThreadingUDPServer(("::1", 0), relay.UdpHandler)
        try:
            self.assertEqual(
                server.socket.getsockopt(socket.IPPROTO_IPV6, socket.IPV6_V6ONLY),
                1,
            )
        finally:
            server.server_close()


class MainListenerTests(unittest.TestCase):
    def test_starts_both_udp_and_tcp_listeners_on_the_wildcard_address(self) -> None:
        with (
            mock.patch.object(relay, "Ipv6ThreadingUDPServer") as udp_cls,
            mock.patch.object(relay, "Ipv6ThreadingTCPServer") as tcp_cls,
            mock.patch.object(relay.threading, "Thread") as thread_cls,
        ):
            relay.main()

        udp_cls.assert_called_once_with(("::", relay.LISTEN_PORT), relay.UdpHandler)
        tcp_cls.assert_called_once_with(("::", relay.LISTEN_PORT), relay.TcpHandler)
        # TCP runs in a background thread so UDP owns the main thread
        # via its own blocking serve_forever() -- matching
        # ntfy_relay.py's own dual-listener shape.
        thread_cls.assert_called_once()
        self.assertTrue(thread_cls.call_args.kwargs.get("daemon"))
        udp_cls.return_value.serve_forever.assert_called_once()


class DeploymentTests(unittest.TestCase):
    def test_disabled_and_no_lan_prefix_by_default(self) -> None:
        defaults = (ROLE_ROOT / "defaults/main.yml").read_text(encoding="utf-8")

        self.assertIn("blocky_ipv6_relay_enabled: false", defaults)
        self.assertIn('blocky_ipv6_relay_lan_prefix: ""', defaults)

    def test_validation_requires_a_real_prefix_when_enabled(self) -> None:
        tasks = (ROLE_ROOT / "tasks/main.yml").read_text(encoding="utf-8")

        self.assertIn("blocky_ipv6_relay_lan_prefix | trim | length > 0", tasks)
        self.assertIn('blocky_ipv6_relay_lan_prefix | trim != "::/0"', tasks)

    def test_rendered_unit_grants_only_net_bind_service(self) -> None:
        rendered = render(
            blocky_ipv6_relay_backend_host="192.168.101.10",
            blocky_ipv6_relay_backend_port=53,
            blocky_ipv6_relay_service_user="blocky-ipv6-relay",
            blocky_ipv6_relay_install_dir="/usr/local/lib/blocky-ipv6-relay",
        )

        self.assertIn("CapabilityBoundingSet=CAP_NET_BIND_SERVICE", rendered)
        self.assertIn("AmbientCapabilities=CAP_NET_BIND_SERVICE", rendered)

    def test_rendered_unit_keeps_the_full_hardening_stack(self) -> None:
        rendered = render(
            blocky_ipv6_relay_backend_host="192.168.101.10",
            blocky_ipv6_relay_backend_port=53,
            blocky_ipv6_relay_service_user="blocky-ipv6-relay",
            blocky_ipv6_relay_install_dir="/usr/local/lib/blocky-ipv6-relay",
        )

        for directive in (
            "NoNewPrivileges=true",
            "ProtectSystem=strict",
            "PrivateDevices=true",
            "RestrictNamespaces=true",
            "MemoryDenyWriteExecute=true",
            "RestrictAddressFamilies=AF_INET AF_INET6",
        ):
            with self.subTest(directive=directive):
                self.assertIn(directive, rendered)

    def test_rendered_unit_has_no_address_allowlist(self) -> None:
        # Deliberately absent as an active directive -- the ip6tables
        # INPUT rule (tasks/main.yml) is the real access boundary for
        # this service, not a systemd address allowlist. The template's
        # own comment explains this by name, so check for an active
        # directive line specifically, not just the substring anywhere.
        rendered = render(
            blocky_ipv6_relay_backend_host="192.168.101.10",
            blocky_ipv6_relay_backend_port=53,
            blocky_ipv6_relay_service_user="blocky-ipv6-relay",
            blocky_ipv6_relay_install_dir="/usr/local/lib/blocky-ipv6-relay",
        )

        directive_lines = [
            line
            for line in rendered.splitlines()
            if not line.strip().startswith("#")
        ]
        self.assertFalse(
            any("IPAddressAllow" in line for line in directive_lines)
        )

    def test_ip6tables_task_targets_ipv6_and_the_configured_prefix(self) -> None:
        tasks = (ROLE_ROOT / "tasks/main.yml").read_text(encoding="utf-8")

        firewall_task = tasks.split(
            "Allow blocky_ipv6_relay's own DNS port through ip6tables", 1
        )[1]
        self.assertIn("ip_version: ipv6", firewall_task)
        self.assertIn("chain: INPUT", firewall_task)
        self.assertIn('source: "{{ blocky_ipv6_relay_lan_prefix }}"', firewall_task)

    def test_firewall_rule_only_persists_when_actually_changed(self) -> None:
        tasks = (ROLE_ROOT / "tasks/main.yml").read_text(encoding="utf-8")

        persist_task = tasks.split(
            "Persist blocky_ipv6_relay's own ip6tables rule across reboots", 1
        )[1]
        self.assertIn("netfilter-persistent", persist_task)
        self.assertIn(
            "when: blocky_ipv6_relay_firewall_result is changed", persist_task
        )

    def test_disabling_actually_stops_the_service_not_just_skips_creation(self) -> None:
        # A real toggle, not a skip-guard -- the unit is always
        # installed regardless of enabled, and this task's own state:
        # ternary resolves to 'stopped' when disabled, so an
        # already-running relay is genuinely stopped, not left running
        # because a later apply merely skipped re-creating it.
        tasks = (ROLE_ROOT / "tasks/main.yml").read_text(encoding="utf-8")

        toggle_task = tasks.split(
            "Ensure blocky_ipv6_relay is in its desired running state", 1
        )[1]
        self.assertIn("'stopped'", toggle_task)
        self.assertIn("blocky_ipv6_relay_enabled | bool", toggle_task)

    def test_site_yml_includes_the_role(self) -> None:
        site = (PROJECT_ROOT / "ansible/playbooks/site.yml").read_text(
            encoding="utf-8"
        )

        self.assertIn("- blocky_ipv6_relay", site)

    def test_iptables_persistent_prompts_are_preseeded(self) -> None:
        tasks = (ROLE_ROOT / "tasks/main.yml").read_text(encoding="utf-8")

        self.assertIn("ansible.builtin.debconf", tasks)
        self.assertIn("iptables-persistent/autosave_", tasks)


if __name__ == "__main__":
    unittest.main()
