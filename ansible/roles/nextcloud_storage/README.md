# nextcloud_storage

Host side of Nextcloud on k3s (ADR 0033), during the migration from AIO.

Directories, NFS-exported to `k3s-node-1`
(`nfs_server_exports` in `group_vars/all/main.yml`), owned by `www-data`
(33), the uid the Pod runs as:

- `/mnt/black-hdd/nextcloud-k3s-test` -- empty data directory of the
  parallel test instance (migration step 2). The live data directory
  `/mnt/black-hdd/nextcloud` stays AIO's until the cutover.
- `/mnt/red-hdd/nextcloud-backup` -- write target of `k3s-apps`'
  `nextcloud-backup` CronJob: nightly `pg_dump` and `config.php`.

## restic (`nextcloud_storage_restic_enabled`)

The files are backed up on this host, not through NFS: the
`nextcloud-restic` service (timer at 03:45 UTC, after the 03:30 UTC
database dump) runs `restic backup`, `forget --prune` (AIO's Borg
retention) and `check` as `www-data` into
`/mnt/red-hdd/nextcloud-restic`. It backs up the data directory and the
dump directory; previews are excluded. Only after all three succeed does
it touch `/var/lib/nextcloud-restic/last-success`, which the monitoring
role turns into `backup:last_success_timestamp_seconds{backup="nextcloud-restic"}`
(`BackupStale`/`BackupMissing`).

The repository password comes from Secrets Manager's
`home-infra/nextcloud-backup` (`restic_password`, see that runbook in
[`secrets-manager`](https://github.com/KandlerLi/secrets-manager)): without it the repository can't be read. The role
initialises the repository only when restic reports it missing (exit
10), never on a wrong password, and runs the first backup when no run
has succeeded yet.

At the cutover `nextcloud_storage_restic_paths` and the exclude switch
from the test directory to `/mnt/black-hdd/nextcloud`.

Runs before `nfs_server` in `site.yml`: `exportfs -ra` fails if an
exported directory doesn't exist yet.

- Repo: [`home-infra`](https://github.com/KandlerLi/home-infra) (this role, `nfs_server_exports`, the
  `monitoring` role's backup markers),
  [`k3s-bootstrap`](https://github.com/KandlerLi/k3s-bootstrap) (the PersistentVolumes),
  [`k3s-apps`](https://github.com/KandlerLi/k3s-apps) (`argocd/nextcloud`, its backup CronJob)
