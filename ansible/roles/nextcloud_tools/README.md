# nextcloud_tools

Not deployed on the homeserver any more -- home_agent's own Docker
container (the only thing that ever talked to this over a Unix socket)
is retired, superseded by `infra/k3s-apps`' `modules/home_agent/`,
which runs its own copy of `nextcloud_tools` as a sidecar in the same
Pod, authenticating to Nextcloud with its own independently-revocable
app password rather than this role's.

`files/nextcloud_tools_service.py` stays here as the **canonical
source** `infra/k3s-apps` vendors its own copy from (`modules/
home_agent/files/nextcloud_tools_service.py`) -- the same reason
`home_agent`'s own `files/agent/` Python package stays in this repo
even though its Docker deployment is retired too (the self-hosted CI
runner builds `home_agent`'s image straight from that source; this
file has no equivalent build pipeline yet, so it's synced by hand --
confirmed identical via `diff` each time it changes). Keep both copies
in sync deliberately, the same way, until/unless this file gets a real
build pipeline of its own.

`tests/test_nextcloud_tools_service.py` still exercises this file's
own logic directly (WebDAV client behavior, shopping-list resolution,
the endpoint-host allowlist, request/response validation) --
independent of whichever role or Kubernetes Pod actually runs it.

Bootstrapping a fresh app password, rotating one, or disabling the
service are all `infra/k3s-apps`-side concerns now (see that repo's
own `modules/home_agent/secret.tf` and its comment on how the current
token was generated) -- this repo no longer has dedicated playbooks
for any of that.
