# nextcloud_aio

The core Nextcloud install (All-in-One mastercontainer). Always applied
(no enable flag) -- this is the personal cloud everything else in this
repo builds around.

- Runs Nextcloud AIO's mastercontainer, which in turn manages its own
  sub-containers (app, database, etc.) through AIO's own internal
  mechanism.
- Deliberately pinned to `:latest`, not a digest, unlike every other
  image in this repo -- Nextcloud's own AIO docs recommend this, since
  the sub-containers self-update independently of Ansible; pinning an
  old digest here risks the compatibility mismatch digest-pinning
  elsewhere is meant to prevent.
- Data lives on `/mnt/black-hdd/nextcloud`. An optional host directory
  mount (`nextcloud_aio_mount_dir`) can be exposed as external storage
  to one named Nextcloud account -- off by default, since a wrong
  `nextcloud_aio_mount_applicable_user` would grant unintended
  visibility.
- `nextcloud_aio_reverse_proxy_enabled` switches its published ports for
  the cutover to `shared_ingress` fronting it instead of talking to AIO's
  own built-in proxy directly.

`nextcloud_tools` and `sankey_export` both depend on a dedicated Nextcloud
account bootstrapped against this instance; see their own READMEs.
