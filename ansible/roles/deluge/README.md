# deluge

Opt-in BitTorrent client (`deluge_enabled`, default `false`). Runs
`linuxserver/deluge`, digest-pinned, published only on loopback --
`shared_ingress` is what actually exposes it, at `torrent.jkandler.de`
behind Basic Auth.

- Downloads and config live on `/mnt/black-hdd` (`deluge_downloads_dir`,
  `deluge_config_dir`), owned by a dedicated `deluge` service account.
- The web UI password is provided through SOPS, not generated here --
  set it before first enabling the role. A personal copy lives in `pass`
  (`deluge/password`) for convenience -- sops is always the source of
  truth; keep `pass` in sync with `scripts/sync_secrets_to_pass.py`
  after rotating it.
- Recreates the container when the image or config changes
  (`deluge_force_recreate` forces it manually).

Enable this role, then set `shared_ingress_deluge_enabled: true` to
actually publish it.
