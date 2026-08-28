# docker

Installs Docker Engine from Docker's own apt repository (pinned to Debian
trixie). Always applied (no enable flag) -- every containerized role in
this repo depends on it.

- Adds Docker's apt key and repository, installs the Docker packages, and
  ensures the `docker` service is enabled and running.
- `docker_daemon_config` (empty by default) lets a caller override
  `/etc/docker/daemon.json` -- used by `github-runner.yml` to lower the
  bridge network MTU to 1400 for the runner VM specifically, whose guest
  networking sits behind an extra libvirt NAT hop that caused real TLS
  resets on job containers (confirmed live). The bare-metal host's own
  invocation in `site.yml` doesn't need this.

Every other role's containers are created through `community.docker`
modules, not shelled-out `docker` CLI calls.
