# blocky_ipv6_relay

Opt-in (`blocky_ipv6_relay_enabled`, default `false`) IPv6-to-Blocky
DNS relay on the homeserver. Bridges IPv6-transport DNS queries from
the LAN to Blocky (`infra/k3s-apps`, `modules/blocky/`), which only
has an IPv4 address inside the k3s VM's isolated network.

- **Why this exists at all**: a FRITZ!Box's own IPv6 DNS setting
  ("Andere DNSv6-Server") can point LAN clients at a *different*
  resolver than its IPv4 DNS setting does. Left on "ISP-assigned" (the
  default), any device that prefers IPv6 transport for its DNS queries
  gets the ISP's own resolver instead of Blocky, bypassing every bit of
  ad-blocking Blocky would otherwise provide -- even on a device
  that's otherwise correctly configured to use the homeserver for
  IPv4 DNS.
- **Why a relay, not extending `k3s_ingress_forward`**: plain DNAT
  never translates between address families -- it can forward an IPv6
  packet to another IPv6 destination, or an IPv4 packet to another
  IPv4 destination, but not one to the other. The k3s VM's own
  isolated libvirt network (`virbr11`, `192.168.101.0/24`) has no IPv6
  configured anywhere, and giving it one would mean a real libvirt
  network redefine (IPv6 forwarding/NAT for libvirt is its own
  research problem, not just an XML addition) touching an
  already-running node. A small relay process that receives IPv6
  queries and forwards them to Blocky's existing IPv4 address over a
  fresh IPv4 connection sidesteps all of that -- no new libvirt
  network, no VM changes, no `k3s_ingress_forward` changes.
- **stdlib-only Python, systemd unit, no Docker container** -- the
  same idiom `monitoring`'s own `ntfy_relay.py` and `home_agent`'s own
  `home_tools_service.py` already use for small, standalone bridging
  logic. Two independent listeners (UDP and TCP, both DNS's own
  requirement -- DNS falls back to TCP for responses too large for a
  single UDP datagram), both bound to `::` with `IPV6_V6ONLY` set
  explicitly (Linux's dual-stack default would otherwise let this
  service also silently answer IPv4 queries the `ip6tables` rule below
  was never scoped to restrict). A backend failure gets a real SERVFAIL
  reply, not a silently dropped query.
- **`CAP_NET_BIND_SERVICE` via `AmbientCapabilities=`**, not root --
  the systemd-native equivalent of the now-deleted Docker `blocky`
  role's own `cap_drop: ALL` + `capabilities: [NET_BIND_SERVICE]`
  shape for the same problem (binding port 53 as a non-root user).
  Every other hardening directive `ntfy-relay.service.j2` already
  established (`ProtectSystem=strict`, `RestrictNamespaces`,
  `MemoryDenyWriteExecute`, etc.) applies here unchanged.
- **`blocky_ipv6_relay_lan_prefix` is not optional hardening, it's the
  actual access boundary.** This service listens on `::` -- reachable
  from wherever the homeserver's own firewall lets it be reached from.
  `docs/home-infra-ai-context` carries a standing, explicitly-stated
  rule against exposing IPv6 services without end-to-end proof (a past
  incident: an accidental AAAA record exposed the FRITZ!Box's own
  self-signed cert to the public internet). An open DNS relay reachable
  from the public internet is a real abuse vector (DNS amplification),
  not a hypothetical one. This role's own validation refuses to do
  anything at all if `blocky_ipv6_relay_enabled: true` and the prefix
  is still empty or `::/0` -- there is no safe default, it has to be
  the LAN's own real IPv6 prefix.
- **A real toggle, not just a skip-guard**: disabling this role
  actually stops the running relay and removes its `ip6tables` rule
  (matching the plan's own rollback discipline), not just skips
  creating them on a host that never had them.

## Two manual steps this role does not do

Both need real values from your own live FRITZ!Box/ISP setup that
can't be discovered or safely guessed at from this repo:

1. **Give the homeserver a stable, non-rotating IPv6 address.** Its
   current global IPv6 is very likely a SLAAC privacy/temporary
   address with only a couple of hours' lifetime -- not usable as a
   fixed target. On a FRITZ!Box, this is typically a per-device
   setting in the device list (Home Network -> Network -> the
   homeserver's own entry -> "always use this IPv6 interface ID for
   this device" or similar wording depending on firmware version).
2. **Determine the LAN's real IPv6 prefix** (FRITZ!Box: Home Network ->
   Network -> IPv6 settings) and set `blocky_ipv6_relay_lan_prefix` to
   it in inventory, alongside `blocky_ipv6_relay_enabled: true` --
   prefer the ULA range if the FRITZ!Box has "also support unique
   local addresses" enabled (a ULA prefix never rotates the way an
   ISP-delegated GUA prefix can, so it stays correct even if the ISP
   changes what it hands out later).

Only once both are done and this role is live: point the FRITZ!Box's
own "Andere DNSv6-Server" setting at the homeserver's new stable IPv6
address, verified internally first (a real `dig AAAA`/`dig A` against
that address from an IPv6-capable LAN client) before relying on it.
