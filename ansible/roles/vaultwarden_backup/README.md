# vaultwarden_backup

The destination side of Vaultwarden's backup: a service account and the
NFS-exported directory `/mnt/red-hdd/vaultwarden-backup`, like
`authelia_backup`.

The backup itself runs as a k3s CronJob,
[`k3s-apps`](https://github.com/KandlerLi/k3s-apps)' `modules/vaultwarden/backup.tf`. It takes an
online SQLite snapshot of `db.sqlite3` (Vaultwarden keeps running) and
gzips it here; 14 days are kept. Only the database is copied:
attachments and Sends are disabled, and a lost `rsa_key.pem` is
regenerated at start, which only signs every client out.

The vault inside the snapshot is encrypted with each user's master
password; the file is still `0700`-protected like every backup here.

The account's uid/gid (981/976) are set explicitly, because the
CronJob runs as exactly these: NFS `root_squash` maps every other
writer to `nobody`, which can't write into this `0700` directory.

- Repo: [`home-infra`](https://github.com/KandlerLi/home-infra) (this role, `nfs_server_exports`),
  [`k3s-apps`](https://github.com/KandlerLi/k3s-apps) (`modules/vaultwarden/backup.tf`),
  [`k3s-bootstrap`](https://github.com/KandlerLi/k3s-bootstrap) (the NFS PersistentVolume)
