# base

Baseline OS setup every other role depends on. Always applied (no enable
flag) -- first role in `site.yml`.

- Disables PCIe ASPM (`pcie_aspm=off` kernel boot parameter) -- fixes a
  well-documented Intel `e1000e`/82579LM bug (this NIC's chipset,
  onboard on the homeserver only) that causes the network interface to
  periodically hang and reset under a `NETDEV WATCHDOG` loop; caused a
  real ~14min production outage on 2026-09-01. Only takes effect after
  a reboot -- this role writes the parameter and regenerates
  `/boot/grub/grub.cfg`, it does not reboot on its own.
- **Not sufficient on its own**: the same bug recurred on 2026-09-06
  with `pcie_aspm=off` confirmed active the whole time, this time
  stuck failing its own internal reset for almost 8 hours before a
  manual reboot. Two more fixes layer on top: TSO/GSO/GRO offloading
  and Energy Efficient Ethernet are disabled on the NIC (`ethtool`,
  applied immediately and persisted via an `ifupdown` post-up hook,
  since this host uses `/etc/network/interfaces`), and a small
  `e1000e-watchdog-recovery.service` watches the kernel log for a
  `NETDEV WATCHDOG` event and forces a full PCI-level unbind/rebind of
  the device -- a safety net regardless of whether the root trigger is
  ever fully eliminated, turning a recurrence into a few seconds of
  disruption instead of an all-night outage.
- **Root-cause-agnostic backstop**: enables this hardware's own
  watchdog timer via `RuntimeWatchdogSec` in `/etc/systemd/system.conf`
  (applied immediately via `systemctl daemon-reexec`, not just on next
  boot). Added after a real ~8h53m outage on 2026-09-11/12 with a
  fundamentally different signature from the e1000e bug above: zero
  kernel-level log entries of any kind in the entire dead window (no
  watchdog message, no panic, no OOM), meaning the kernel itself
  stopped scheduling entirely rather than one specific, logged hardware
  fault -- the e1000e-specific recovery service above can't help here,
  it only reacts to a message that never appeared. If the kernel ever
  fully freezes again, for any reason, the hardware watchdog forces a
  real reset with no software involved, converting an outage requiring
  physical intervention into an automatic reboot within seconds. Root
  cause of that specific outage is still unknown.
- Installs core apt packages (curl, git, htop, vim) plus the interactive
  shell stack (zsh, zsh-autosuggestions, zsh-syntax-highlighting).
- Installs Oh My Zsh and the Powerlevel10k theme, both pinned to a fixed
  git ref -- not a moving branch -- so a prompt update is a deliberate SHA
  bump here, not a surprise on the next apply.
- Adds `admin_user` to the `www-data` group and sets zsh as its login
  shell.

Requires running as root (`become: true`); asserts this explicitly rather
than failing with a confusing permission error partway through.
