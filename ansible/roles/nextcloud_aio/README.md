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
- `nextcloud_aio_oidc_enabled` (off by default, same "opt-in" shape as
  `nextcloud_aio_mount_dir` above) registers Authelia as a "Sign in
  with Authelia" option on Nextcloud's own login page, via the
  official `user_oidc` app -- see `tasks/oidc.yml`. Needs
  `authelia_oidc_nextcloud_client_secret` filled in via `sops
  ansible/inventory/group_vars/all/secrets.sops.yml` first (matching
  the client secret `infra/k3s-apps`' own `modules/authelia` hashes
  for its `nextcloud` client). Deliberately additive, not a
  replacement -- native login stays enabled, unlike Grafana/Open
  WebUI's own Authelia integration (`infra/k3s-apps#30`), since
  Nextcloud may have other real accounts not necessarily tied to this
  same Authelia identity.

Two clients depend on a dedicated Nextcloud account bootstrapped against
this instance, both now running as k3s workloads rather than home-infra
roles: `home_agent`'s own `nextcloud_tools` sidecar and the `sankey_export`
CronJob -- see `infra/k3s-apps`' `modules/home_agent/`/`modules/sankey_export/`.

- A systemd oneshot (`nextcloud-aio-apache-network-fix.service`, runs at
  boot and on every apply) automatically reconnects
  `nextcloud-aio-apache` to the `nextcloud-aio` Docker network if it's
  missing -- confirmed live on two independent reboots (2026-09-01,
  2026-09-04) that Apache silently drops off that network, deadlocking
  with `nextcloud-aio-nextcloud`. See
  `files/reconnect_apache_network.sh`'s own header comment and
  `PARKED.md`'s "Homeserver: make a reboot a complete non-event" -- this
  automates the known recovery, it doesn't fix the still-unknown root
  cause (owned by AIO's own mastercontainer, not this repo).
