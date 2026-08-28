# Home Infrastructure

Ansible configuration for a Debian home server, its application services,
and an isolated GitHub Actions runner VM.

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
- Opt-in Prometheus/Grafana monitoring stack (host health, container
  health, service reachability, certificate expiry)
- KVM/QEMU and libvirt
- Debian 13 GitHub Actions runner VM on a private NAT network

Each role has its own short `README.md` under `ansible/roles/<name>/`
with its enable flag, key variables, and how it's invoked.

The runner disk is stored at
`/mnt/black-hdd/github-runner/github-runner.qcow2`. The role never recursively
changes permissions below `/mnt/black-hdd` and never overwrites an orphaned
VM disk automatically.

The VM also keeps a generated NoCloud seed image at
`/mnt/black-hdd/github-runner/github-runner-cloud-init.iso`. Keeping this
read-only image attached makes first-boot identity, SSH, and network setup
independent of virt-install's temporary cloud-init media lifecycle. It
contains the controller's public SSH key, but no GitHub token.

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

Set `github_runner_github_owner` and `github_runner_github_repositories` in
`ansible/inventory/group_vars/all/main.yml`. Each repository gets its own
runner installation, Unix service account, and root-owned systemd service
below `/opt/actions-runner`, while sharing the VM and downloaded runner archive.
Repository entries use a stable local identifier and the GitHub repository
name:

```yaml
github_runner_github_repositories:
  - id: dyndns
    repository: dyndns
```

Repository-level runners are registered separately, so every entry starts its
own runner service and can execute one job at a time. Increase the VM CPU and
memory settings if several repositories will run jobs concurrently.

The encrypted GitHub API token
must be stored as `github_runner_github_token` in
`ansible/inventory/group_vars/all/secrets.sops.yml`.

The fine-grained token should be limited to the listed repositories and have
repository `Administration: write` permission for each one. Its long-lived
value remains on the Ansible controller; the VM receives only short-lived
registration tokens.

Use this persistent self-hosted runner only for mutually trusted workflows,
preferably in private repositories. Repository runners use separate Unix
accounts, but they still share one kernel and VM; this reduces accidental
cross-repository access but is not a strong security boundary. They are
isolated from the physical host. The VM intentionally receives no host SSH
key, Docker socket, or mount from `/mnt/black-hdd`.

To add another repository, grant the fine-grained token access to it and add
another `id`/`repository` entry to `github_runner_github_repositories`.
Removing an entry does not automatically unregister or delete that runner,
because doing so would remove credentials and remote state; decommission it
explicitly first.

## Usage

Check connectivity:

```bash
.venv/bin/ansible home_servers -m ansible.builtin.ping
```

Apply all infrastructure:

```bash
.venv/bin/ansible-playbook ansible/playbooks/site.yml --ask-become-pass
```

Apply only the GitHub runner VM and guest configuration:

```bash
.venv/bin/ansible-playbook ansible/playbooks/github-runner.yml \
  --ask-become-pass
```

Edit the encrypted secret with the existing GPG key:

```bash
sops ansible/inventory/group_vars/all/secrets.sops.yml
```

## Private homeserver agent

The `home_agent` role (always applied) installs two deliberately separate
components:

- `home-tools`, a hardened host systemd service that exposes only fixed,
  read-only JSON checks over `/run/home-tools/home-tools.sock`
- `home-agent`, an unprivileged Docker container that uses the OpenAI Responses
  API and can call only those checks

The model-facing container does not receive the Docker socket, shell access,
root privileges, host environment variables, or write tools. Docker data is
sanitized by `home-tools` to container name, image, state, and status. The
agent API is published only on `127.0.0.1:8090` unless the guarded shared
ingress migration is deliberately completed.

Add the API key to the encrypted SOPS file:

```yaml
home_agent_openai_api_key: "sk-..."
```

Then deploy just the agent stack (faster than a full `site.yml` run, e.g.
after an image or code change):

```bash
.venv/bin/ansible-playbook ansible/playbooks/home-agent.yml \
  --ask-become-pass
```

A normal `site.yml` run applies it too -- the role has no enable flag.
The model defaults to `gpt-5.4-mini` and can be changed with
`home_agent_model`.

After deployment, make a local request from the homeserver:

```bash
curl --fail-with-body \
  --header 'Content-Type: application/json' \
  --data '{"message":"Check my homeserver."}' \
  http://127.0.0.1:8090/v1/chat
```

Host data selected by the tools, including container names and health metrics,
is sent to the configured cloud model when needed. Nextcloud data remains
unavailable unless the separate read-only tool role is explicitly configured
and deployed.

The agent also exposes an OpenAI-compatible API on its container port. This
compatibility layer accepts bounded conversation history, ignores caller
system/developer messages, and advertises only the synthetic `home-agent`
model. The existing `/healthz` and `/v1/chat` endpoints remain available.

## Read-only Nextcloud tools

The disabled-by-default `nextcloud_tools` role prepares a separate hardened
systemd service. It holds one dedicated Nextcloud app password and talks only
to AIO Apache on `127.0.0.1:11000`. The `home-agent` container receives a
read-only mount of the service's Unix socket, never the credential or direct
WebDAV access. Open WebUI remains only a chat frontend.

The first milestone exposes three bounded operations below one configured
folder: list files, search names and paths, and read UTF-8 text files up to
256 KiB. Search scans at most 500 entries to depth four and returns at most 50
matches. Metadata includes file type, size, modification time, content type,
preview availability, and identifiers. The service implements no WebDAV write
method. Images and binary documents can be found by name and metadata but their
contents cannot be read or analysed.

The guarded bootstrap playbook creates the non-admin `home-agent` Nextcloud
account with an unrevealed generated login password, creates a limited
`home-agent-readonly` app password non-interactively, and installs that token
directly as a service-owned `0400` file. The token is protected by Ansible
`no_log` and is never written to the controller, SOPS, Docker environment, or
chat. If the account or token file already exists, the play reuses it; it
refuses to silently orphan a token after partial state loss.

Use the dedicated playbook only after separately approving both the Nextcloud
account/token mutation and production service deployment:

```bash
.venv/bin/ansible-playbook ansible/playbooks/nextcloud-tools.yml \
  --ask-become-pass \
  --extra-vars \
  nextcloud_tools_bootstrap_confirmation=BOOTSTRAP_NEXTCLOUD_TOOLS
```

After the play creates the account, create or select `AI Workspace` under your
normal Nextcloud account and share it with `home-agent` with editing disabled.
That one share remains manual so Ansible never needs a credential for the
account that owns your personal files.

After private list/search/text-read checks and an Open WebUI acceptance passed,
`nextcloud_tools_enabled` was persisted in inventory so aggregate applies
retain the validated socket integration. File metadata or
text content selected by these tools is sent to the configured cloud model only
when the user explicitly requests a Nextcloud operation. Writes, bulk indexing,
PDF/Office extraction, and image-content recognition require separate reviewed
milestones.

Token rotation is separately guarded. It validates the replacement token over
loopback WebDAV before installing it and revokes only older tokens with the
managed name:

```bash
.venv/bin/ansible-playbook \
  ansible/playbooks/rotate-nextcloud-tools-token.yml \
  --ask-become-pass \
  --extra-vars \
  nextcloud_tools_rotation_confirmation=ROTATE_NEXTCLOUD_TOOLS_TOKEN
```

To detach the socket and stop the credential-bearing service without deleting
its configuration, use the dedicated rollback playbook after explicit approval:

```bash
.venv/bin/ansible-playbook ansible/playbooks/disable-nextcloud-tools.yml \
  --ask-become-pass
```

## Open WebUI frontend

The `open_webui` role provides the deployed chat window at
`ai.jkandler.de`. It is enabled by ordinary `site.yml` runs. Open WebUI
connects only to the restricted `home-agent` compatibility API over an
isolated, non-masqueraded Docker bridge; it receives neither the real OpenAI
key nor host access. The bridge blocks external egress while still allowing
the container to publish only `127.0.0.1:8091`.

The role pins Open WebUI by version and image digest, drops all Linux
capabilities, uses a non-login host identity, and disables uploads, workspace
tools, plugins, code execution, web search, image generation, API keys, and
community sharing. Chat and account state persists under
`/var/lib/open-webui`. The image root filesystem must remain writable because
the upstream startup script rewrites bundled static assets; this is a known
residual risk, mitigated by the other container restrictions and isolated
network. Offline and RAG-bypass settings prevent startup from attempting to
download local embedding models.

Deploy the private frontend without changing the public route:

```bash
.venv/bin/ansible-playbook ansible/playbooks/open-webui.yml \
  --ask-become-pass
```

The initial administrator setup and authenticated public chat validation are
complete. The separately guarded publication playbook requires the exact
confirmation `PUBLISH_OPEN_WEBUI`; the rollback playbook requires
`ROLL_BACK_OPEN_WEBUI` and restores the existing direct agent API route.
Follow the documentation repository's Open WebUI runbook before either
operation. The UI route uses Open WebUI authentication because its Bearer token
and HTTP Basic Auth cannot share one `Authorization` header. The retained direct
`/healthz` and `/v1/chat` routes continue to require Basic Auth.

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
