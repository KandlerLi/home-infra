# Home Infrastructure

Ansible configuration for a Debian home server and its application
services.

## Managed infrastructure

- Base operating system packages
- `/mnt/black-hdd` storage mount
- Docker and Nextcloud AIO
- Opt-in Finanzfluss Sankey budget exporter (planned for a future
  restructure away from Finanzfluss)
- Opt-in static landing page linking the other home services
- Opt-in shared Traefik ingress fronting the services above
- Opt-in Prometheus monitoring stack (host health, container health,
  service reachability, certificate expiry), with Grafana itself now a
  k3s-native copy ([`k3s-apps`](https://github.com/KandlerLi/k3s-apps)) instead of a Docker container here
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
scripts/roll-out.sh apply
```

(sources AWS credentials for the `home-infra-local` identity from
`pass` first -- see [`terraform-state/README.md`](https://github.com/KandlerLi/terraform-state/blob/main/README.md)'s
"home-infra-local Identity" section. Running the raw
`ansible-playbook` command below works too, but only if
`AWS_ACCESS_KEY_ID`/`AWS_SECRET_ACCESS_KEY` are already exported in
your shell -- `site.yml`'s own `pre_tasks` read 2 secret groups
straight from AWS Secrets Manager.)

```bash
.venv/bin/ansible-playbook ansible/playbooks/site.yml --ask-become-pass
```

Or `scripts/roll-out.sh dry-run`/`apply`, which runs the test suite and
`--syntax-check` first, then the same command above (`dry-run` adds
`--check --diff`). The scripts take the sudo password from `pass`
(`hosts/homeserver/sudo`) through `scripts/homeserver-become-pass.sh` as
`--become-password-file`; the raw command above still prompts.

After merging a PR, `scripts/update.sh dry-run`/`apply` switches to a fresh
`main`, refusing a dirty tree, installs `requirements.txt` and
`requirements.yml` into the local venv, and runs `roll-out.sh`. Add `k3s`
(`scripts/update.sh apply k3s`) to run `roll-out-k3s.sh` instead.

`ansible/playbooks/k3s.yml` (the k3s learning cluster's own VM
provisioning/resizing -- deliberately not part of `site.yml`, see that
playbook's own header comment) has its own equivalent,
`scripts/roll-out-k3s.sh dry-run`/`apply`, kept as a separate script on
purpose rather than a mode on `roll-out.sh` -- so a routine
`roll-out.sh` run can never accidentally reach a k3s VM.

Secrets live in AWS Secrets Manager now, not a SOPS-encrypted file in
this repo (the SOPS-to-Secrets-Manager cutover) -- edit
one with the AWS CLI, merging into the existing JSON blob rather than
overwriting it (each secret holds several related keys):

```bash
aws secretsmanager get-secret-value --secret-id home-infra/<group> --query SecretString --output text \
  | jq '.<key> = "<new-value>"' > /tmp/secret.json
aws secretsmanager put-secret-value --secret-id home-infra/<group> --secret-string file:///tmp/secret.json
rm /tmp/secret.json
```

## Private homeserver agent and the chat frontend

`home_agent`, `nextcloud_tools` and Open WebUI (`ai.jkandler.de`) were
retired outright on 2026-11-16, data included: their roles, the
`home-tools` host service, the `aux_backup` timer that only backed up
Open WebUI, and the image build. See
[`home-infra-docs/docs/decisions.md`](https://github.com/KandlerLi/home-infra-docs/blob/main/docs/decisions.md).

## Shared HTTPS ingress

**Moved to k3s, 2026-09-01.** The `shared_ingress` role that used to run
a pinned Traefik container directly on the homeserver (file-based
routing, automatic TLS, Basic Auth, rate limiting, security headers for
every public hostname) was deleted outright once its k3s-native
replacement was confirmed live -- [`k3s-apps`](https://github.com/KandlerLi/k3s-apps)' own
`modules/ingress/`, reached from the internet through this repo's own
`k3s_ingress_forward` role (opt-in iptables DNAT on the homeserver;
`192.168.101.0/24` is otherwise unreachable from the LAN). ACME now
uses DNS-01 against Route53 rather than the original role's own
TLS-ALPN-01. See `home-infra-docs`' `history.md`, "k3s
learning cluster" section, for the full migration writeup and the real
bugs found along the way.

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
scripts/roll-out.sh apply
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
syntax check and the full unit test suite on every push to `main`, on a
GitHub-hosted runner (`ubuntu-latest`). It's checks-only,
deliberately: it never applies anything and never touches the real
homeserver, since that has no public API to reach safely the way
`dyndns`/`website`'s AWS deploys do. Applying changes still always means
running `scripts/roll-out.sh apply` yourself, on your own machine, over
the local network, exactly as before.

That self-hosted runner itself used to be a standalone libvirt VM
managed by this repo's own (now-deleted) `github_runner` role. As of
2026-08-31 it's a k3s-native replacement instead
([`k3s-apps`](https://github.com/KandlerLi/k3s-apps)' own `modules/github_runner/`), confirmed live across
every repository with a real successful `Checks` run, `home-infra`
itself included, before the old VM was deregistered and torn down.
[`repo-infra/config.yml`](https://github.com/KandlerLi/repo-infra/blob/main/config.yml)'s `runner: true` flag on a repository
entry is still the source of truth for which repositories get one; only
where that list now gets synced to changed (see
`scripts/sync_github_runner_repositories.py`, which emits
[`k3s-bootstrap`](https://github.com/KandlerLi/k3s-bootstrap)'s Terraform variable and the monitoring role's
exporter repo list).

## Backup restore drill

`scripts/verify-backups.sh` checks from the laptop that every backup on
`/mnt/red-hdd` can actually be restored: the newest Authelia backup is a sound SQLite database that the current
storage key decrypts, every Paperless export original matches its manifest
checksum, and the Borg passphrase from `pass` (`nc/backup`, or
`BORG_PASS_ENTRY`) opens Nextcloud AIO's repository and gets a file back out
of the newest archive. It runs `scripts/verify-backups-remote.sh` as root on
the homeserver in a scratch directory under `/mnt/red-hdd` that it deletes
afterwards; the passphrase, the Authelia key (one key from Secrets Manager,
skipped without an AWS session) and the sudo password travel on stdin only,
and the output is PASS/FAIL lines, never contents. Restore procedures:
[`home-infra-docs`](https://github.com/KandlerLi/home-infra-docs)' `runbooks/verify-backups.md`.

## Hardware maintenance shutdown

The guarded shutdown helper shuts down the running libvirt VMs (`k3s-node-2`
before `k3s-node-1`, up to 5 minutes each), stops the Nextcloud AIO application
containers, verifies that they are down, flushes filesystem buffers, and powers
off the host. It refuses to continue if `/mnt/black-hdd` is not mounted, and
stops without powering off if a VM doesn't shut down in time. It does not
create a backup; verify a suitable backup separately.

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

Wait for the host to turn off completely, then disconnect power at once:
Wake-on-LAN stays enabled on purpose, and on 2026-10-31 the host powered
itself back on 40–50 s after each poweroff (most likely the FRITZ!Box waking
it for traffic to its port forwardings; tracker #49). Hardware must never be
changed while it can come back up. After the
hardware work, the VMs start by themselves (autostart), but the AIO
application containers may remain stopped. Start them
through the AIO interface at `https://192.168.178.100:8080`, then verify the
detected memory, failed units, containers, and public Nextcloud status.

If the qcow2 disk and libvirt domain get out of sync, the runner role stops
with a recovery message. Inspect and archive or restore the orphaned resource
manually before rerunning; normal automation intentionally uses neither
`force_disk` nor domain recreation.
