# Home Infrastructure

Ansible configuration for a Debian home server and its application
services.

## Managed infrastructure

- Base operating system packages
- `/mnt/black-hdd` storage mount
- Docker and Nextcloud AIO
- Private homeserver health agent
- Opt-in restricted Nextcloud file and shopping-list tools for the agent
- Opt-in Finanzfluss Sankey budget exporter (planned for a future
  restructure away from Finanzfluss)
- Opt-in Open WebUI chat frontend for the agent
- Opt-in BitTorrent client (Deluge)
- Opt-in static landing page linking the other home services
- Opt-in shared Traefik ingress fronting the services above
- Opt-in Prometheus monitoring stack (host health, container health,
  service reachability, certificate expiry), with Grafana itself now a
  k3s-native copy (`infra/k3s-apps`) instead of a Docker container here
- Opt-in network-wide DNS ad-blocking (Blocky), with a Pi-hole-style
  query-log dashboard in Grafana
- KVM/QEMU and libvirt (needed for the k3s learning cluster below;
  the isolated GitHub Actions runner VM that used to live here too is
  gone -- see "Continuous integration")

Each role has its own short `README.md` under `ansible/roles/<name>/`
with its enable flag, key variables, and how it's invoked.

## Current physical host

The managed host is a Fujitsu ESPRIMO P900 with an Intel Core i5-2400 and
16 GiB DDR3 RAM. After the 2026-08-19 memory upgrade, Debian reported all
16 GiB online and approximately 15.5 GiB usable after hardware reservations.

## Prerequisites

Create a Python virtual environment and install the dependencies:

```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
.venv/bin/ansible-galaxy collection install -r requirements.yml
```

## Usage

Check connectivity:

```bash
.venv/bin/ansible home_servers -m ansible.builtin.ping
```

Apply all infrastructure:

```bash
.venv/bin/ansible-playbook ansible/playbooks/site.yml --ask-become-pass
```

Edit the encrypted secret with the existing GPG key:

```bash
sops ansible/inventory/group_vars/all/secrets.sops.yml
```

## Private homeserver agent, Nextcloud tools, and the chat frontend

`home_agent` (the OpenAI-backed HTTP API), `nextcloud_tools` (restricted
Nextcloud file/shopping-list access), and `open_webui` (the chat
frontend) all used to run as Docker containers managed by this repo.
As of 2026-08-30 they run as a personal, single-node k3s cluster
instead (`infra/k3s-apps`, a sibling repository, Terraform-managed) --
see `docs/home-infra-ai-context/context/current-state.md`'s "k3s
learning cluster" section for the full cutover history. This repo now
only owns the host-level prerequisites those k3s Pods still depend on:

- **`home_agent` role** (always applied): `home-tools`, a hardened
  host systemd service exposing fixed, read-only JSON checks (host
  disks, systemd units, Docker containers) over
  `/run/home-tools/home-tools.sock` -- the one dependency that
  couldn't move into the k3s Pod as a sidecar, since it reports this
  homeserver's own hardware. Reached by the k3s Pod over an opt-in TCP
  listener, scoped to only the k3s VM's isolated network address. The
  role also keeps `home_agent`'s own Python application source
  (`files/agent/`) as the canonical copy a self-hosted CI workflow
  (`.github/workflows/build-home-agent.yml`) builds the k3s Pod's GHCR
  image from -- never built on the homeserver itself.
- **`nextcloud_tools` role**: retired outright, not reshaped -- its
  only consumer (home_agent's own Docker container) is gone. Its
  Python service source (`files/nextcloud_tools_service.py`) stays on
  as the canonical original `infra/k3s-apps` vendors its own Pod
  sidecar's copy from; see that role's own README.
- **`open_webui` role** (always applied): a dedicated service account
  and `open_webui_data_dir` (`/var/lib/open-webui`), holding the real
  accounts and chat history the k3s Pod reads over a new NFS export
  rather than starting fresh. The service account's uid is validated
  against what `infra/k3s-apps`' Deployment hardcodes.

`ai.jkandler.de` is split by path between the two (`home_agent`
answers `/healthz` and `/v1/chat`, `open_webui` gets everything else)
-- see `shared_ingress` below, and `infra/k3s-apps`' own
`ai_ingress.tf` for how that split is replicated on the cluster side.

## Shared HTTPS ingress

The opt-in `shared_ingress` role prepares a pinned Traefik container using
file-based routing. It receives no Docker socket and provides automatic TLS,
Basic Auth, rate limiting, request-size limits, and security headers for
`ai.jkandler.de`. Nextcloud remains unauthenticated by Traefik and is routed by
its existing hostname, `nextcloud.jkandler.de`.

Preparation does not start Traefik or change production ports:

```bash
.venv/bin/ansible-playbook \
  ansible/playbooks/shared-ingress-prepare.yml \
  --ask-become-pass
```

The cutover is intentionally guarded. It refuses to run while
`nextcloud-aio-apache` is active and requires the exact extra-variable
confirmation `MIGRATE_NEXTCLOUD_INGRESS`. Follow the dedicated documentation
runbook before invoking `ansible/playbooks/shared-ingress.yml`; the migration
moves Nextcloud Apache to `127.0.0.1:11000` and transfers public ports 80/443
to Traefik.

The main `site.yml` keeps `shared_ingress` disabled by default. Do not create
the `ai.jkandler.de` DNS record or run the cutover until authentication,
ACME email, backups, and rollback steps have been verified.

After a successful Nextcloud cutover, persist the reverse-proxy and shared
ingress enable flags in group variables before the next normal `site.yml` run.
Use `shared-ingress-agent.yml` only after the agent DNS record resolves, and
use the separately guarded `shared-ingress-rollback.yml` if Nextcloud
validation fails.

## DNS ad-blocking

The opt-in `blocky` role (ADR 0020) runs Blocky for network-wide DNS
ad-blocking, plus a dedicated Postgres container just to hold its query
log. Blocky has no web UI of its own -- the dashboard is Grafana,
already deployed by the `monitoring` role above, fed by Blocky's native
Prometheus metrics and its Postgres query log.

Set the Postgres password in the encrypted SOPS file before the first
deploy:

```yaml
blocky_postgres_password: "..."
```

Then apply it (it's included in a normal `site.yml` run once
`blocky_enabled: true` is set in inventory, same as every other opt-in
role):

```bash
.venv/bin/ansible-playbook ansible/playbooks/site.yml --ask-become-pass
```

Enabling the role alone changes nothing for your devices -- they keep
using whatever DNS they already have. To actually route traffic through
it, point your router at the homeserver for DNS -- there's no way to
automate this from Ansible. On a FRITZ!Box: Home Network -> Network ->
Network Settings -> IPv4 Settings -> the custom/"other" DNS server
field, set to the homeserver's LAN IP. This keeps the FRITZ!Box itself
as the DHCP-advertised DNS server for every device (so its own
local-hostname resolution keeps working), while the FRITZ!Box's own
lookups route through Blocky. Verify the exact field name in your own
FRITZ!Box firmware -- menu wording can drift between versions.

Confirm it's working from the homeserver itself:

```bash
dig @<homeserver-lan-ip> example.com
```

Run the local unit tests with:

```bash
python3 -m unittest discover -s tests -v
```

## Continuous integration

A GitHub Actions workflow (`.github/workflows/checks.yml`) runs an Ansible
syntax check and the full unit test suite on every push to `main`, on the
self-hosted home runner (`[self-hosted, home, debian]`). It's checks-only,
deliberately: it never applies anything and never touches the real
homeserver, since that has no public API to reach safely the way
`dyndns`/`website`'s AWS deploys do. Applying changes still always means
running `ansible-playbook site.yml --ask-become-pass` yourself, on your
own machine, over the local network, exactly as before. Unlike
`website`'s deploy pipeline, running these particular checks on the home
runner has no real availability tradeoff — nothing here ever needs to run
while the homeserver itself is down.

That self-hosted runner itself used to be a standalone libvirt VM
managed by this repo's own (now-deleted) `github_runner` role. As of
2026-08-31 it's a k3s-native replacement instead
(`infra/k3s-apps`' own `modules/github_runner/`), confirmed live across
every repository with a real successful `Checks` run, `home-infra`
itself included, before the old VM was deregistered and torn down.
`bootstrap/repo-infra/config.yml`'s `runner: true` flag on a repository
entry is still the source of truth for which repositories get one; only
where that list now gets synced to changed (see
`scripts/sync_github_runner_repositories.py`, which now emits only
`infra/k3s-apps`' own Terraform variable, not an Ansible one).

## Hardware maintenance shutdown

The guarded shutdown helper stops the Nextcloud AIO application containers,
verifies that they are down, flushes filesystem buffers, and powers off the
host. It refuses to continue if `/mnt/black-hdd` is not mounted or a libvirt VM
is running. It does not create a backup; verify a suitable backup separately.

Copy the script to the homeserver and run its read-only check from the
controller:

```bash
scp scripts/shutdown-homeserver-for-maintenance.sh \
  julian@192.168.178.100:/tmp/

ssh -t julian@192.168.178.100 \
  'sudo bash /tmp/shutdown-homeserver-for-maintenance.sh --check'
```

When ready for the outage, run it without `--check` and type `POWER OFF` at
the prompt:

```bash
ssh -t julian@192.168.178.100 \
  'sudo bash /tmp/shutdown-homeserver-for-maintenance.sh'
```

Wait for the host to turn off completely before disconnecting power. After the
hardware work, the AIO application containers may remain stopped. Start them
through the AIO interface at `https://192.168.178.100:8080`, then verify the
detected memory, failed units, containers, and public Nextcloud status.

If the qcow2 disk and libvirt domain get out of sync, the runner role stops
with a recovery message. Inspect and archive or restore the orphaned resource
manually before rerunning; normal automation intentionally uses neither
`force_disk` nor domain recreation.
