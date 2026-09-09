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
  into **Alertmanager** (now a k3s-native copy, see below), which
  notifies both ntfy (via a small local relay service, since
  Alertmanager can't template ntfy's payload shape) and email (via AWS
  SES SMTP credentials from `aws/ses-relay`) -- one channel being
  misconfigured shouldn't mean silence.

**Grafana and Alertmanager no longer run here.** Both are k3s-native
copies instead (`infra/k3s-apps`, `modules/grafana` and `modules/
alertmanager`) -- Phases 1 and 2 of moving this stack into the k3s
cluster. Grafana is reached at `grafana.jkandler.de` through
`shared_ingress_grafana_upstream`, confirmed live 2026-08-30 (real
admin login, both datasources healthy, a real PromQL query returning
real scrape data). Alertmanager is reached by Prometheus over
`monitoring_alertmanager_upstream`, confirmed live 2026-08-31 (a
synthetic test alert posted straight to its own API reached both ntfy
and the real SES inbox, and Prometheus's own `/api/v1/alertmanagers`
showed exactly that target, healthy) before either role's own Docker
deployment was removed. Prometheus, the exporters, and Blocky stay on
this host permanently -- they report *this physical host's* own
hardware/Docker daemon, or are LAN-facing, so moving them into the k3s
VM would monitor the wrong thing entirely. `files/dashboards/*.json`
stay here as the canonical source `infra/k3s-apps`' own `modules/
grafana` vendors a synced copy from (same pattern as
`nextcloud_tools_service.py`, kept at the top-level `nextcloud_tools/`
directory for the same reason), even though nothing here installs
them any more.

Two listeners exist purely so the k3s-native pieces can reach what
stays on this host: `monitoring_prometheus_k3s_bind_address` and
`monitoring_ntfy_relay_k3s_bind_address` (both this role) -- additive
(loopback keeps working; each only ever adds a second bind), both
locked to `192.168.101.1` (this homeserver's own address on the k3s
VM's isolated network) by their own validation, never `0.0.0.0`. A
third, `blocky_postgres_k3s_bind_address` (the `blocky` role), existed
here too until Blocky itself moved fully into k3s and that role was
deleted entirely (2026-09-01) -- `monitoring_blocky_upstream`
(defaults/main.yml) is its replacement, but as a clean switch, not an
addition, since Blocky no longer runs on this host at all. The other
exception is
`monitoring_alertmanager_upstream` itself: a clean **switch**, not an
addition -- Prometheus's own `alerting.alertmanagers`
target list notifies every address in it for the same firing alert
(unlike a scrape target list), so listing both Alertmanagers at once
would double-fire every real notification.

Needs `monitoring_ntfy_topic` and the SES SMTP credentials set in the
`home-infra/monitoring` AWS Secrets Manager group before first
enabling. `monitoring_grafana_admin_password` lives in the separate
`home-infra/grafana` group (nothing in this role reads it directly --
`infra/k3s-apps`' own `secrets.tf` reads it from there, and so does
the `pass` sync below) -- same real login, same real password, just
served from the k3s cluster now. A personal copy of the Grafana admin
login lives in `pass` (`grafana/user`, `grafana/password`) for
convenience -- AWS Secrets Manager is always the source of truth; keep
`pass` in sync with `scripts/sync_secrets_to_pass.py` after rotating it.
