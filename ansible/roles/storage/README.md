# storage

Mounts the data drives at `/mnt/black-hdd` and `/mnt/red-hdd`. Always
applied (no enable flag) -- runs early in `site.yml`, before any role that
stores data there.

- Both mounts are `nofail`, so a missing or locked disk can't drop the
  boot into emergency mode before SSH is up. The services in
  `storage_hdd_dependent_services` get a `RequiresMountsFor` drop-in in
  exchange: without either disk they stay down instead of writing into
  the empty mount point on the SSD. Applying this only reloads systemd;
  the dependency takes effect the next time a service starts.

- Mounts the filesystem by UUID (`black_hdd_device_uuid`), not device path
  -- device names like `/dev/sdb` aren't stable across reboots.
- Uses `state: mounted` (not just an fstab entry), so the mount is also
  active immediately, not only after a future reboot.
- Sets ownership to `www-data:www-data` with mode `02770` -- the setgid
  bit means every file *created* under the mount inherits the group, so
  services that share this drive (deluge, nextcloud_aio's optional
  mount, the GitHub runner VM's disk) stay consistently group-writable
  without each one managing permissions itself.

See `tests/test_storage.py` for the invariants this role is expected to
hold (mount-before-use ordering, UUID-based mounting, the setgid bit).
