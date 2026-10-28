# stalwart_backup

The destination side of Stalwart's mail backup: a service account and
the NFS-exported directory `/mnt/red-hdd/stalwart-backup`, like
`authelia_backup`.

The backup itself runs as a k3s CronJob,
[`k3s-apps`](https://github.com/KandlerLi/k3s-apps)' `modules/stalwart/backup.tf`.
Stalwart keeps its mailboxes in RocksDB, which can only be copied
consistently while Stalwart is stopped, so the job scales Stalwart to 0,
tars its volume here and scales it back to 1, about a minute of mail
downtime a night (senders retry). 14 days are kept.

The account's uid/gid (982/977) are set explicitly, because the
CronJob runs as exactly these: NFS `root_squash` maps every other
writer to `nobody`, which can't write into this `0700` directory.

- Repo: [`home-infra`](https://github.com/KandlerLi/home-infra) (this role, `nfs_server_exports`),
  [`k3s-apps`](https://github.com/KandlerLi/k3s-apps) (`modules/stalwart/backup.tf`),
  [`k3s-bootstrap`](https://github.com/KandlerLi/k3s-bootstrap) (the NFS PersistentVolume and the
  CronJob's ServiceAccount)
