# monitoring

Opt-in Prometheus stack for host health, container health, service
reachability, and TLS/DNS expiry (`monitoring_enabled`, default `false`;
see ADR 0017). Loopback-only, not published through `shared_ingress`.

- **node_exporter** (host stats) and **cAdvisor** (container stats) feed
  **Prometheus**, which also runs `blackbox_exporter` probes against the
  public services (`monitoring_probe_targets` /
  `_targets_authenticated`) -- its HTTPS probe reports certificate
  expiry for free, so no separate exporter is needed for that.
- Every container uses `network_mode: host` uniformly, even where not
  strictly required, so Prometheus can scrape everything as a plain
  `127.0.0.1:<port>` without a host/bridge networking mismatch.
- Alerting is dual-channel on purpose: Prometheus's own alert rules fire
  into **Alertmanager**, which notifies both ntfy (via a small local
  relay service, since Alertmanager can't template ntfy's payload shape)
  and email (via AWS SES SMTP credentials from `infra/ses-relay`) -- one
  channel being misconfigured shouldn't mean silence.

**Grafana itself no longer runs here.** It's a k3s-native copy instead
(`infra/k3s-apps`, `modules/grafana`), reached at `grafana.jkandler.de`
through `shared_ingress_grafana_upstream` -- Phase 1 of moving this
stack into the k3s cluster, confirmed live 2026-08-30 (real admin
login, both datasources healthy against the two additive listeners
below, a real PromQL query returning real scrape data) before this
role's own Docker deployment of it was removed. Prometheus/Alertmanager/
the exporters/Blocky all stay on this host permanently -- they report
*this physical host's* own hardware/Docker daemon, or are LAN-facing,
so moving them into the k3s VM would monitor the wrong thing entirely.
`files/dashboards/*.json` stay here as the canonical source
`infra/k3s-apps`' own module vendors a synced copy from (same pattern
as `nextcloud_tools_service.py`'s own role), even though nothing here
installs them any more.

Two listeners exist purely so the k3s-native Grafana can reach its
datasources: `monitoring_prometheus_k3s_bind_address` (this role) and
`blocky_postgres_k3s_bind_address` (the `blocky` role) -- both additive
(loopback keeps working; this only ever adds a second bind), both
locked to `192.168.101.1` (this homeserver's own address on the k3s
VM's isolated network) by their own validation, never `0.0.0.0`.

Needs `monitoring_ntfy_topic` and the SES SMTP credentials set through
SOPS before first enabling. `monitoring_grafana_admin_password` still
lives in `secrets.sops.yml` too (nothing in this role reads it any
more, but `infra/k3s-apps`' own `scripts/export-tf-vars.sh` does, and so
does the `pass` sync below) -- same real login, same real password, just
served from the k3s cluster now. A personal copy of the Grafana admin
login lives in `pass` (`grafana/user`, `grafana/password`) for
convenience -- sops is always the source of truth; keep `pass` in sync
with `scripts/sync_secrets_to_pass.py` after rotating it.
