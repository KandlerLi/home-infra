# k3s_ingress_forward

Opt-in (`k3s_ingress_forward_enabled`, default `false`, set `true` in
`ansible/inventory/group_vars/all/main.yml` -- see that variable's own
comment there for why it's persisted, not passed via `-e`) iptables
DNAT relay on the homeserver, forwarding its own public/LAN-facing ports
straight through to a k3s Service at `192.168.101.10`, unmodified at
the packet level -- k3s's own Traefik (`infra/k3s-apps`' own
`modules/ingress/`) originally, now also Blocky's own DNS Service
(`infra/k3s-apps`' own `modules/blocky/`). The `iptables` (IPv4) role
in this repo -- `blocky_ipv6_relay`'s own `ip6tables` INPUT rule is a
materially different concern (protecting a locally-listening process
from the LAN, not DNAT-relaying real inbound traffic through to a k3s
Service), so it lives in that role instead of here.

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
- **Port 25 (inbound SMTP)** is forwarded tcp-only to `infra/k3s-apps`'
  own `modules/stalwart/`. The FRITZ!Box's own port-forward for 25 is
  manual and out-of-band, IPv4 only, targeting the homeserver's LAN IP
  -- redo it from a runbook if the router is ever reset. Only 25 is
  exposed publicly; submission and IMAP are not.
- **Each rule has its own `protocol`**, defaulting to `tcp` (every
  rule needed only tcp until DNS) -- DNS needs both `tcp` and `udp`
  forwarded to the same port, so Blocky's own entry is two rules, not
  one.
- **The DNAT rule excludes traffic arriving via the k3s VM's own
  bridge** (`in_interface: "!virbr11"`). `PREROUTING`/`nat` sees every
  packet entering any interface, including the VM's own outbound
  connections as they transit that bridge on their way out to the
  internet -- without this exclusion, an outbound packet using source
  port 80/443 (any HTTPS image pull, for instance) matches the DNAT
  rule's own `destination_port` just as well as real inbound traffic
  does, and gets hairpinned straight back to the VM's own address
  instead of ever leaving. Confirmed live (2026-09-01): this exact bug
  silently broke every outbound HTTPS/HTTP connection from
  `k3s-node-1`, surfacing as `ImagePullBackOff` with no other visible
  cause -- DNS and other ports were unaffected since they never
  matched the rule's own `destination_port` in the first place.
- **The DNAT rule is also restricted to this host's own address**
  (`destination: "{{ ansible_host }}"`). Same class of bug as the
  `in_interface` exclusion above, just a different source of traffic:
  without this, the rule matched *any* packet with a matching
  `destination_port` regardless of where it was actually addressed --
  including this host's own locally-originated outbound traffic (any
  Docker container, or a process on the host itself) making a real
  connection to the real internet on port 80/443/53. Confirmed live
  (2026-09-05): Nextcloud AIO's own mastercontainer, trying to reach
  the real `ghcr.io` on port 443 to validate connectivity as part of
  its own startup, got silently hairpinned into this cluster's own
  Traefik instead -- a real TLS handshake completed, with a real but
  wrong certificate, surfacing as `SSL: no alternative certificate
  subject name matches target hostname 'ghcr.io'` and an indefinite
  crash-restart loop, for hours, surviving even a full host reboot
  since nothing about the DNAT rule itself changed. `ansible_host` is
  the same address inventory already uses to reach this host, not a
  separate literal that could drift out of sync with it.
- **Persisted via `iptables-persistent`** (`netfilter-persistent
  save`, only when a rule actually changed) so the relay survives a
  reboot -- debconf-preseeded to skip its install-time interactive
  prompt.
- **A real toggle, not just a skip-guard**: `state` on both iptables
  tasks flips between `present`/`absent` off
  `k3s_ingress_forward_enabled` directly, so disabling this role
  actually removes the rules again (the plan's own rollback step: flip
  this off, start `shared_ingress`'s container back up) rather than
  merely skipping their creation on a fresh host. This cuts both ways,
  confirmed the hard way (2026-09-01): before
  `k3s_ingress_forward_enabled` was persisted in inventory, one
  ordinary `ansible-playbook` invocation that forgot the `-e
  k3s_ingress_forward_enabled=true` flag defaulted to `false` and
  actively tore out the live 80/443/53 DNAT rules -- a real production
  outage for every public `*.jkandler.de` service, not a no-op. A real
  toggle needs its own real, persisted state; relying on a
  manually-remembered `-e` flag every single apply was the actual bug.
