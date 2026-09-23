# aux_backup

Opt-in (`aux_backup_enabled`, default `false`) daily backup of the
small, real host directories Nextcloud AIO's own Borg backup was
never going to cover: Deluge's config and Open WebUI's real data
(`aux_backup_sources`, see `defaults/main.yml`). Plain dated
`tar.gz` archives written to `aux_backup_dest_dir` (the same disk
Nextcloud's own backup uses, a separate subdirectory) via a systemd
timer, not another Borg repository -- these sources are small enough
that Borg's own dedup/compression/encryption machinery would be
disproportionate.

- **Why root, not a dedicated service user**: this job reads across
  several *different* dedicated-host-uid directories in one pass
  (each source role's own convention keeps its data under its own
  UID) -- a single non-root user can't do that without being added to
  every one of those groups, which only grows more fragile as sources
  are added. Every systemd hardening directive that doesn't touch
  DAC/capabilities is still applied (`ProtectSystem=strict` with an
  explicit `ReadWritePaths` for the destination only, no network, no
  new privileges, locked-down namespaces/syscalls); none of it is
  loosened to compensate for running as root.
- **Fails loud on a missing or empty source directory**, rather than
  silently writing an empty or partial archive -- a real backup job
  succeeding when it backed up nothing is worse than it failing
  loudly.
- **Live SQLite databases get SQLite's own online backup, not a raw
  copy** (`aux_backup_sources[].sqlite_files`, currently just Open
  WebUI's `webui.db`) -- a raw copy of a live WAL-mode database isn't
  guaranteed to be a consistent point-in-time snapshot. Any configured
  `sqlite_files` entry gets excluded from the plain `rsync -a` copy
  and snapshotted separately via Python's stdlib `sqlite3` module's
  `Connection.backup()`, the same approach `infra/k3s-apps`' own
  `modules/authelia` uses.
- **A real toggle**: disabling this role stops the timer, not just
  skips creating it on a host that never had it. The service/timer
  units are always installed regardless of `aux_backup_enabled`, so
  there's always a real timer to act on even the first apply.

## Rollout

Matches this repo's own private-validation-before-persisting
convention (`deluge`/`monitoring`'s own history): apply with
`aux_backup_enabled: true` passed at apply time first, confirm a real
run (`systemctl start aux-backup.service` to trigger one immediately
rather than waiting for the schedule, then check
`journalctl -u aux-backup.service` and that dated archives actually
landed in `aux_backup_dest_dir`), only then persist
`aux_backup_enabled: true` in inventory.
