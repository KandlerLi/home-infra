# github_runner

Provisions an isolated Debian VM and registers per-repository, containerized
GitHub Actions self-hosted runners on it. Only used by
`ansible/playbooks/github-runner.yml`, which runs it in two modes:

- `github_runner_role_mode: provision_vm` -- runs on the homeserver itself
  (needs `libvirt_host` first): downloads/verifies the Debian cloud image,
  creates the VM's disk, an isolated NAT network
  (`github_runner_network_*`), and a cloud-init seed ISO with the
  controller's SSH key baked in (no GitHub token). Never recreates an
  already-provisioned disk, and never recursively touches permissions
  below `/mnt/black-hdd`.
- `github_runner_role_mode: configure_guest` -- runs *inside* the VM
  (`hosts: github_runner_vms`): installs one runner service per entry in
  `github_runner_github_repositories`, each under its own dedicated
  system account and systemd unit below `/opt/actions-runner`, sharing
  the VM and the downloaded runner archive.

Repository entries need a stable local `id` and the GitHub `repository`
name; add one via `bootstrap/repo-infra/config.yml`'s `runner: true` flag
and `scripts/sync_github_runner_repositories.py`, not by hand. Registering
a runner also needs a fine-grained GitHub PAT with `Administration: write`
on that repository -- a manual, per-PAT step, not something Terraform or
this role can do for you.
