# blocky

Opt-in network-wide DNS ad-blocker (`blocky_enabled`, default `false`).
Runs two containers: Blocky itself, and a dedicated Postgres instance
feeding its query log to Grafana (see the `monitoring` role for the
dashboards).

- Blocky has no web UI of its own -- unlike Pi-hole, it's a single Go
  binary, YAML-configured. The dashboard comes from Grafana, fed by
  Blocky's native Prometheus metrics and its Postgres-backed query log.
- `network_mode: host`, DNS bound to `blocky_bind_address` (the host's
  real LAN IP, auto-discovered, not loopback) -- required, not just
  convenient: other devices on the network need to actually reach it.
  Hardened with `cap_drop: ALL` + `capabilities: [NET_BIND_SERVICE]`
  (the one capability needed to bind port 53), the same shape
  `shared_ingress` already uses for Traefik's ports 80/443.
- `upstreams`/`bootstrapDns` are static IPs, never hostnames -- avoids a
  chicken-and-egg problem once Blocky becomes the network's only
  resolver (it would otherwise depend on itself to resolve its own
  upstream).
- Postgres runs as its own default image user (not a mapped host uid
  like most containers here) -- its entrypoint needs to start as root to
  set up its data directory on first run. Loopback-only by its own
  `listen_addresses=127.0.0.1`, not by network isolation (it shares
  Blocky's host network namespace). Data lives on
  `blocky_postgres_data_dir` (defaults to `/mnt/black-hdd`, easy to point
  at the root SSD instead via that one variable -- see the tradeoff note
  in the role's defaults).
- Query log retention is capped (`blocky_query_log_retention_days`,
  default 7) -- a small, rolling dataset regardless of where it lives.

Enabling this role alone doesn't change anything for your devices --
they keep using whatever DNS they already have. Actually routing traffic
through Blocky needs one manual step outside Ansible's reach: pointing
your router at the homeserver for DNS (see the top-level README).
