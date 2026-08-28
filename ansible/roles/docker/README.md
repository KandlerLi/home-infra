# docker

Installs Docker Engine from Docker's own apt repository (pinned to Debian
trixie). Always applied (no enable flag) -- every containerized role in
this repo depends on it.

- Adds Docker's apt key and repository, installs the Docker packages, and
  ensures the `docker` service is enabled and running.
- `docker_daemon_config` (empty by default) lets a caller override
  `/etc/docker/daemon.json` -- e.g. to adjust the default bridge network
  MTU for a guest sitting behind an extra NAT hop, if that's ever needed
  again (was applied for the runner VM once, removed after live testing
  showed the underlying PMTUD path works correctly end-to-end).

Every other role's containers are created through `community.docker`
modules, not shelled-out `docker` CLI calls.
