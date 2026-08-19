# Home Infrastructure

Ansible configuration for a Debian home server, its application services,
and an isolated GitHub Actions runner VM.

## Managed infrastructure

- Base operating system packages
- `/mnt/black-hdd` storage mount
- Docker and Nextcloud AIO
- Opt-in private homeserver health agent
- Opt-in shared Traefik ingress for Nextcloud and the agent
- KVM/QEMU and libvirt
- Debian 13 GitHub Actions runner VM on a private NAT network

The runner disk is stored at
`/mnt/black-hdd/github-runner/github-runner.qcow2`. The role never recursively
changes permissions below `/mnt/black-hdd` and never overwrites an orphaned
VM disk automatically.

The VM also keeps a generated NoCloud seed image at
`/mnt/black-hdd/github-runner/github-runner-cloud-init.iso`. Keeping this
read-only image attached makes first-boot identity, SSH, and network setup
independent of virt-install's temporary cloud-init media lifecycle. It
contains the controller's public SSH key, but no GitHub token.

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

The opt-in `home_agent` role installs two deliberately separate components:

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

Then deploy only the agent stack:

```bash
.venv/bin/ansible-playbook ansible/playbooks/home-agent.yml \
  --ask-become-pass
```

The main `site.yml` includes the role but leaves it disabled by default, so
existing infrastructure runs are unchanged until the dedicated playbook is
used. The model defaults to `gpt-5.4-mini` and can be changed with
`home_agent_model`.

After deployment, make a local request from the homeserver:

```bash
curl --fail-with-body \
  --header 'Content-Type: application/json' \
  --data '{"message":"Check my homeserver."}' \
  http://127.0.0.1:8090/v1/chat
```

Host data selected by the tools, including container names and health metrics,
is sent to the configured cloud model when needed. No Nextcloud documents are
indexed or sent by this milestone.

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
hardware work, verify the detected memory, failed units, containers, and the
public Nextcloud status.

If the qcow2 disk and libvirt domain get out of sync, the runner role stops
with a recovery message. Inspect and archive or restore the orphaned resource
manually before rerunning; normal automation intentionally uses neither
`force_disk` nor domain recreation.
