# open_webui

Host prerequisites for Open WebUI: a dedicated service account and its
real data directory. Open WebUI itself -- the Docker container that
used to run here -- is retired, superseded by `infra/k3s-apps`'
`modules/open_webui/` (cut over, together with `home_agent`, to
`ai.jkandler.de` on 2026-08-30). Always applied (no enable flag any
more) -- the k3s-native copy still depends on this account and
directory existing identically, the same reason `deluge`'s own role
was reshaped this way rather than deleted outright.

- `open_webui_data_dir` (`/var/lib/open-webui`) holds the real,
  already-live data (accounts, chat history, `WEBUI_SECRET_KEY_FILE`)
  -- migrated over a new NFS export to the k3s cluster rather than
  started fresh, see `nfs_server_exports` in `home-infra`'s own
  `group_vars` and `infra/k3s-apps`' `modules/open_webui/storage.tf`.
- The service account's uid (995) is validated against what
  `infra/k3s-apps`' Deployment hardcodes as `run_as_user` -- see the
  task's own comment for why (and why its *group* is deliberately
  `root`, not this account's own).
