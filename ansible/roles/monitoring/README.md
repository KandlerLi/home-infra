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
  SES SMTP credentials from `aws/ses-relay`) -- one channel being
  misconfigured shouldn't mean silence.

**Grafana and Alertmanager no longer run here.** Both are k3s-native
copies instead (`infra/k3s-apps`, `modules/grafana` and `modules/
alertmanager`). Grafana is reached at `grafana.jkandler.de` through
`infra/k3s-apps`' own Traefik (`modules/ingress`); Alertmanager is
reached by Prometheus over `monitoring_alertmanager_upstream`.
Prometheus, the exporters, and Blocky stay on this host permanently --
they report *this physical host's* own hardware/Docker daemon, or are
LAN-facing, so moving them into the k3s VM would monitor the wrong
thing entirely. `files/dashboards/*.json` stay here as the canonical
source `infra/k3s-apps`' own `modules/grafana` vendors a synced copy
from, even though nothing here installs them any more. See
`docs/home-infra-ai-context`'s current-state.md ("k3s learning
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
the k3s cluster, to scrape `infra/k3s-apps`' own `modules/node_exporter`
(a node-exporter DaemonSet + kube-state-metrics, both reached the same
way Blocky/Alertmanager already are -- k3s's bundled ServiceLB). Off
until that module is confirmed applied, so this doesn't spend a scrape
interval alerting on a target that doesn't exist yet. `k3s-node-health.json`
(one of the canonical dashboards above) and the `k3s_node_health` alert
group (`alert_rules.yml.j2`) both filter on `job="k3s_node_exporter"`
specifically, since that job's metrics share names with this host's own
`node_exporter` job above but must never be aggregated together.

Needs `monitoring_ntfy_topic` and the SES SMTP credentials set in the
`home-infra/monitoring` AWS Secrets Manager group before first
enabling. Grafana's own admin password is no longer a shared secret at
all -- its k3s copy generates a throwaway `random_password` since
native login is disabled at the protocol level (Authelia OIDC only).
