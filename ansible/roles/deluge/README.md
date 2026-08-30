# deluge

Host prerequisites Deluge needs on the homeserver: a dedicated
`deluge` service account (uid/gid 993/986) and the two directories it
reads/writes on `/mnt/black-hdd` (`deluge_downloads_dir`,
`deluge_config_dir`), plus the ACL grant that lets Nextcloud's own
process (`www-data`) see and delete files in the downloads directory.

Deluge itself now runs as a k3s-native copy (`infra/k3s-apps`,
Terraform-managed), reachable at `torrent.jkandler.de` behind Basic
Auth via `shared_ingress` -- this role no longer runs a Docker
container or manages a `deluge_enabled` flag; its tasks always run.

- The service account's uid/gid are validated against `993`/`986`,
  which `infra/k3s-apps`' Deployment hardcodes as `PUID`/`PGID` --
  catches drift instead of letting the k3s copy silently write as the
  wrong uid after a homeserver rebuild.
- Downloads is `0755` (world-readable/executable) plus an ACL granting
  `www-data` write access; config stays `0750`, private session
  state.
- The Deluge web UI password lives only in `infra/k3s-apps` now
  (`TF_VAR_deluge_web_password`, sourced from this repo's
  `deluge_web_password` SOPS secret) -- nothing here manages it.
