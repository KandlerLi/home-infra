# home_agent

Host prerequisites for `home_agent`: just `home_tools_service` now.
`home_agent` itself -- the OpenAI-backed HTTP API, the Docker container
that used to run here -- is retired, superseded by `infra/k3s-apps`'
`modules/home_agent/` (cut over, together with `open_webui`, to
`ai.jkandler.de` on 2026-08-30). Always applied (no enable flag), same
as before.

- `home_tools_service.py` is the one dependency that couldn't move
  into the k3s Pod as a sidecar -- it reports this homeserver's own
  hardware (disks, systemd units, Docker containers), so it has to
  keep running here. Restricted, argument-free, read-only, reached
  over a Unix socket by anything on this host and, when
  `home_agent_tools_tcp_bind_address` is set, over TCP too -- the
  mechanism the k3s Pod actually uses, since it can't reach a Unix
  socket on this host.
- `files/agent/` (`home_agent`'s own Python application) stays in this
  repo even though it's no longer built or run here -- it's the
  canonical source `build-home-agent.yml`'s CI workflow (on the
  self-hosted runner, never the homeserver) builds the k3s Pod's GHCR
  image from.
