# unattended_upgrades

Daily unattended **security** updates (Debian's `trixie-security` only)
on the homeserver (`site.yml`) and both k3s VMs (`k3s.yml`). Tracker #42.

- Point releases and third-party repositories (Docker, speedtest) stay
  manual: Docker updates restart every container, Nextcloud AIO
  included. `52unattended-upgrades-local` `#clear`s the origin list from
  Debian's own `50unattended-upgrades`, which also takes point releases.
- **No automatic reboots**, and on the homeserver no automatic service
  restarts either: a systemd drop-in sets `NEEDRESTART_MODE=l` for
  `apt-daily-upgrade.service`, so needrestart only lists what needs a
  restart (manual `apt` runs keep its interactive default). An
  automatic restart of `nfs-server` or `libvirtd` would interrupt k3s.
  Restarts and reboots stay deliberate; see #34 for a clean reboot.
- Runs via Debian's `apt-daily.timer`/`apt-daily-upgrade.timer` (around
  06:00 with a random delay). Log: `/var/log/unattended-upgrades/`.

Check what a run would install:
```bash
sudo unattended-upgrade --dry-run --debug 2>&1 | grep -E "Allowed origins|Packages that will be upgraded"
```
