# monitoring

Opt-in Prometheus stack for host health, container health, service
reachability, and TLS/DNS expiry (`monitoring_enabled`, default `false`;
see ADR 0017). Prometheus itself and its exporters are loopback-only,
not published anywhere.

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
  SES SMTP credentials from [`ses-relay`](https://github.com/KandlerLi/ses-relay)) -- one channel being
  misconfigured shouldn't mean silence.

**Grafana and Alertmanager no longer run here.** Both are k3s-native
copies instead ([`k3s-apps`](https://github.com/KandlerLi/k3s-apps), `modules/grafana` and `modules/
alertmanager`). Grafana is reached at `grafana.jkandler.de` through
[`k3s-apps`](https://github.com/KandlerLi/k3s-apps)' own Traefik (`modules/ingress`); Alertmanager is
reached by Prometheus over `monitoring_alertmanager_upstream`.
Prometheus and the exporters stay on this host permanently -- they
report *this physical host's* own hardware/Docker daemon, so moving them
into the k3s VM would monitor the wrong thing entirely. (Blocky moved to
k3s on 2026-09-01 after all; Prometheus scrapes it over
`monitoring_blocky_upstream`.) `files/dashboards/*.json` stay here as the canonical
source [`k3s-apps`](https://github.com/KandlerLi/k3s-apps)' own `modules/grafana` vendors a synced copy
from, even though nothing here installs them any more. See
[`home-infra-docs`](https://github.com/KandlerLi/home-infra-docs)' history.md ("k3s learning
cluster") for the full cutover history.

Two listeners exist purely so the k3s-native pieces can reach what
stays on this host: `monitoring_prometheus_k3s_bind_address` and
`monitoring_ntfy_relay_k3s_bind_address` (both this role) -- additive
(loopback keeps working; each only ever adds a second bind), both
locked to `192.168.101.1` by their own validation, never `0.0.0.0`.
`monitoring_alertmanager_upstream` and `monitoring_blocky_upstream`
are the other direction: a clean **switch**, not an addition -- see
each variable's own comment in `defaults/main.yml`.

`monitoring_k3s_node_health_enabled` (default `false`) is the reverse
direction of the two listeners above: this Prometheus reaching *into*
the k3s cluster, to scrape [`k3s-apps`](https://github.com/KandlerLi/k3s-apps)' own `modules/node_exporter`
(a node-exporter DaemonSet + kube-state-metrics, both reached the same
way Blocky/Alertmanager already are -- k3s's bundled ServiceLB). Off
until that module is confirmed applied, so this doesn't spend a scrape
interval alerting on a target that doesn't exist yet. `k3s-node-health.json`
(one of the canonical dashboards above) and the `k3s_node_health` alert
group (`alert_rules.yml.j2`) both filter on `job="k3s_node_exporter"`
specifically, since that job's metrics share names with this host's own
`node_exporter` job above but must never be aggregated together.
`github-runners.json` builds on the same two jobs: the CI runner pods'
state from kube-state-metrics, `k3s-node-2`'s disk and IO pressure, and
this host's HDD busy time, plus CPU, memory, memory against the limit
and OOM kills per runner container from `k3s_kubelet_cadvisor`. It has
no GitHub-side online/busy status, since that isn't scraped.

`monitoring_k3s_kubelet_metrics_enabled` adds the `k3s_kubelet_cadvisor`
job: each kubelet's `/metrics/cadvisor` over HTTPS, verified against the
k3s server CA (`files/k3s-server-ca.crt`, public, valid until 2036), with
a ServiceAccount token that may only `get` `nodes/metrics`
([`k3s-bootstrap`](https://github.com/KandlerLi/k3s-bootstrap)'s `kubelet_metrics_rbac.tf`). The token comes from
`home-infra/monitoring` in Secrets Manager (`monitoring_k3s_kubelet_token`)
into a `0400` file only Prometheus reads. Its metric names match this
host's own `cadvisor` job, so every query filters on `job`, and an
allowlist keeps only the series the dashboards use (no
`container_last_seen`, which `ContainerDown` reads).

**Backup freshness** (`backups` alert group, `backups.json`): every
backup becomes one `backup:last_success_timestamp_seconds{backup=...}`
series. Host-side backups (`monitoring_backup_markers`) are the mtime of
a file each run only writes on success, exported by the
`backup-freshness` timer through node_exporter's textfile collector;
k3s CronJob backups (`monitoring_backup_k3s_cronjobs`) come from
kube-state-metrics. `BackupStale` fires past
`monitoring_backup_max_age_hours`, `BackupMissing` for a listed backup
with no success at all. A new backup needs adding to one of the two
lists.

Needs `monitoring_ntfy_topic` and the SES SMTP credentials set in the
`home-infra/monitoring` AWS Secrets Manager group before first
enabling. Grafana's own admin password is no longer a shared secret at
all -- its k3s copy generates a throwaway `random_password` since
native login is disabled at the protocol level (Authelia OIDC only).
