# k3s_ingress_forward

Opt-in (`k3s_ingress_forward_enabled`, default `false`) iptables DNAT
relay on the homeserver, forwarding its own public/LAN-facing ports
straight through to a k3s Service at `192.168.101.10`, unmodified at
the packet level -- k3s's own Traefik (`infra/k3s-apps`' own
`modules/ingress/`) originally, now also Blocky's own DNS Service
(`infra/k3s-apps`' own `modules/blocky/`). The one iptables/firewall
role in this repo.

- **A relay, not a proxy.** The k3s VM's own network
  (`192.168.101.0/24`) is deliberately unreachable from the LAN --
  isolated libvirt NAT, `k3s_node`'s own README explains why. Rather
  than bridging it onto the LAN, this role makes the homeserver a
  thin, invisible packet forward: the FRITZ!Box's own port-forward
  (manual, out-of-band, not in any repo) keeps targeting the
  homeserver's LAN IP exactly as it does today; this role is the only
  thing that changes what happens after that.
- **DNAT, not a TLS-terminating proxy** -- deliberately, because
  ACME's TLS-ALPN-01 challenge (`tlsChallenge` in k3s's own Traefik
  config) needs the real public TLS handshake to reach whatever's
  actually issuing the certificate. A relay below the TLS layer keeps
  that end-to-end; anything that terminated TLS itself here would
  break it.
- **A separate concern from `shared_ingress`.** This role manages
  packet forwarding, not reverse-proxy config, and outlives
  `shared_ingress` regardless of that migration's own outcome --
  deliberately not folded into it.
- **The `FORWARD`-chain rule is the real substance here, not the
  DNAT rule.** Confirmed live on the homeserver: its `FORWARD` chain's
  own default policy is `ACCEPT`, but libvirt's own `LIBVIRT_FWI`
  chain (auto-managed for every NAT-mode network it runs, `k3s_network`
  included) only accepts `RELATED,ESTABLISHED` traffic into the
  network by default -- the standard libvirt firewall pattern, which
  REJECTs a fresh externally-initiated connection otherwise. This role
  inserts its own narrowly-scoped `ACCEPT` at the very top of the base
  `FORWARD` chain (ahead of Docker's own `DOCKER-USER`/`DOCKER-FORWARD`
  chains and libvirt's own `LIBVIRT_FWX`/`FWI`/`FWO` chains alike), so
  it terminates before ever reaching either vendor's own chains and
  doesn't depend on their internal contents.
- **`k3s_ingress_forward_rules` is a list**, not a fixed pair of
  ports, specifically so a rehearsal can override it at apply time
  (e.g. `-e '{"k3s_ingress_forward_rules": [{"public_port": 8443,
  "target_port": 443}]}'`) without touching this role's own defaults
  -- forwarding a throwaway external port straight to Traefik's real
  `websecure` entryPoint (443) internally, so Traefik's own config
  needs no separate test entryPoint of its own.
- **Each rule has its own `protocol`**, defaulting to `tcp` (every
  rule needed only tcp until DNS) -- DNS needs both `tcp` and `udp`
  forwarded to the same port, so Blocky's own entry is two rules, not
  one.
- **Persisted via `iptables-persistent`** (`netfilter-persistent
  save`, only when a rule actually changed) so the relay survives a
  reboot -- debconf-preseeded to skip its install-time interactive
  prompt.
- **A real toggle, not just a skip-guard**: `state` on both iptables
  tasks flips between `present`/`absent` off
  `k3s_ingress_forward_enabled` directly, so disabling this role
  actually removes the rules again (the plan's own rollback step: flip
  this off, start `shared_ingress`'s container back up) rather than
  merely skipping their creation on a fresh host.
