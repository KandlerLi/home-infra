# monitoring

Opt-in Prometheus/Grafana stack for host health, container health, service
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
- **Grafana** is provisioned entirely from files (datasource + dashboard
  JSON in `files/dashboards/`), not click-through UI setup.

Needs `monitoring_grafana_admin_password`, `monitoring_ntfy_topic`, and
the SES SMTP credentials set through SOPS before first enabling. A
personal copy of the Grafana admin login lives in `pass` (`grafana/user`,
`grafana/password`) for convenience -- sops is always the source of
truth; keep `pass` in sync with `scripts/sync_secrets_to_pass.py` after
rotating it.
