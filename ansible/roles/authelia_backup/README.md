# authelia_backup

Creates the destination side of a backup path for Authelia's own
`db.sqlite3` (sessions, TOTP registrations, the reset-password JWT
denylist) -- a service account and an NFS-exported directory on this
host, matching the same pattern `open_webui`'s own role already uses
for a k3s Pod that needs to write here as a specific non-root uid.

This role does **not** run the backup itself. Authelia's real data
lives on a `local-path` (hostPath-backed) k3s PersistentVolumeClaim,
physically inside `k3s-node-1`'s own guest filesystem -- never visible
to this host directly, unlike Deluge's config or Open WebUI's data
(both plain NFS-exported directories `aux_backup` can read straight off
disk). The actual backup logic -- SQLite's own online backup API for a
consistent snapshot of a live database, matching how `aux_backup`
already handles Open WebUI's own `webui.db` -- runs inside a k3s
CronJob instead, `infra/k3s-apps`' own
`modules/authelia/cronjob.tf`, which mounts this directory over NFS
(see `nfs_server_exports` in `group_vars/all/main.yml`) as its write
target.

`authelia_backup_uid`'s expected value (979) is hardcoded into that
CronJob's `run_as_user`/`run_as_group` -- this role's own validate task
fails loudly if the real assigned uid ever drifts from that, the same
safety net `open_webui`'s role already relies on.

- Repo: `infra/home-infra` (this role, `nfs_server_exports`),
  `infra/k3s-apps` (`modules/authelia/cronjob.tf`),
  `bootstrap/k3s-bootstrap` (the cluster-scoped NFS-backed
  PersistentVolume this directory is exported through)
