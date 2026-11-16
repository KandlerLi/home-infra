# authelia_backup

Creates the destination side of a backup path for Authelia's own
`db.sqlite3` (sessions, TOTP registrations, the reset-password JWT
denylist) -- a service account and an NFS-exported directory on this
host, for a k3s Pod that needs to write here as a specific non-root uid.

This role does **not** run the backup itself. Authelia's real data
lives on a `local-path` (hostPath-backed) k3s PersistentVolumeClaim,
physically inside `k3s-node-1`'s own guest filesystem -- never visible
to this host directly. The actual backup logic -- SQLite's own online backup API for a
consistent snapshot of a live database -- runs inside a k3s
CronJob instead, [`k3s-apps`](https://github.com/KandlerLi/k3s-apps)' own
`modules/authelia/cronjob.tf`, which mounts this directory over NFS
(see `nfs_server_exports` in `group_vars/all/main.yml`) as its write
target.

This account's real assigned uid/gid (984/979 -- two *different*
numbers) are hardcoded into
that CronJob's
`run_as_user`/`run_as_group`. This role's own validate task fails
loudly if either ever drifts from that.

- Repo: [`home-infra`](https://github.com/KandlerLi/home-infra) (this role, `nfs_server_exports`),
  [`k3s-apps`](https://github.com/KandlerLi/k3s-apps) (`modules/authelia/cronjob.tf`),
  [`k3s-bootstrap`](https://github.com/KandlerLi/k3s-bootstrap) (the cluster-scoped NFS-backed
  PersistentVolume this directory is exported through)
