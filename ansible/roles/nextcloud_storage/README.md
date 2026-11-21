# nextcloud_storage

Host side of Nextcloud on k3s (ADR 0033), during the migration from AIO.

For now one directory, NFS-exported to `k3s-node-1`
(`nfs_server_exports` in `group_vars/all/main.yml`):

- `/mnt/black-hdd/nextcloud-k3s-test` -- empty data directory of the
  parallel test instance (migration step 2). Owned by `www-data` (33),
  the uid the Pod runs as. The live data directory
  `/mnt/black-hdd/nextcloud` stays AIO's until the cutover.

Nextcloud itself runs in [`k3s-apps`](https://github.com/KandlerLi/k3s-apps)' `argocd/nextcloud` (deployed by
Argo CD) as uid/gid 33.

Runs before `nfs_server` in `site.yml`: `exportfs -ra` fails if an
exported directory doesn't exist yet.

- Repo: [`home-infra`](https://github.com/KandlerLi/home-infra) (this role, `nfs_server_exports`),
  [`k3s-bootstrap`](https://github.com/KandlerLi/k3s-bootstrap) (the PersistentVolumes),
  [`k3s-apps`](https://github.com/KandlerLi/k3s-apps) (`argocd/nextcloud`)
