# Home Infrastructure

Ansible configuration for a Debian home server, its application services,
and an isolated GitHub Actions runner VM.

## Managed infrastructure

- Base operating system packages
- `/mnt/black-hdd` storage mount
- Docker and Nextcloud AIO
- Opt-in private homeserver health agent
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
agent API is published only on `127.0.0.1:8090`; do not expose it through a
reverse proxy until authentication and HTTPS have been designed.

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

Run the local unit tests with:

```bash
python3 -m unittest discover -s tests -v
```

If the qcow2 disk and libvirt domain get out of sync, the runner role stops
with a recovery message. Inspect and archive or restore the orphaned resource
manually before rerunning; normal automation intentionally uses neither
`force_disk` nor domain recreation.
