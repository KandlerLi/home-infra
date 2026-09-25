# paperless_storage

Host side of Paperless-ngx (ADR 0023): a `paperless` service account
with a pinned uid/gid (983/978) and two directories, both NFS-exported
to `k3s-node-1` (`nfs_server_exports` in `group_vars/all/main.yml`):

- `/mnt/black-hdd/paperless-media` -- originals, OCR'd archive PDFs,
  thumbnails. Live data.
- `/mnt/red-hdd/paperless-export` -- write target for the nightly
  `document_exporter` CronJob. The backup.

Paperless itself (app, PostgreSQL, Redis, the export CronJob) runs in
`infra/k3s-apps`' `modules/paperless`, as this uid/gid. Changing the
pinned numbers here means changing them there too.

Runs before `nfs_server` in `site.yml`: `exportfs -ra` fails if an
exported directory doesn't exist yet.

- Repo: `infra/home-infra` (this role, `nfs_server_exports`),
  `bootstrap/k3s-bootstrap` (the NFS PersistentVolumes),
  `infra/k3s-apps` (`modules/paperless`)
