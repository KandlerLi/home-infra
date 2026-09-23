# base

Baseline OS setup every other role depends on. Always applied (no enable
flag) -- first role in `site.yml`.

- Disables PCIe ASPM (`pcie_aspm=off` kernel boot parameter) -- fixes a
  well-documented Intel `e1000e`/82579LM bug (this NIC's chipset,
  onboard on the homeserver only). Only takes effect after a reboot.
  See `docs/home-infra-docs/docs/runbooks/homeserver-e1000e-nic-watchdog-hang.md`
  for the full incident history, including why this alone wasn't
  sufficient (TSO/GSO/GRO offloading, Energy Efficient Ethernet, and a
  `e1000e-watchdog-recovery.service` PCI-level unbind/rebind all layer
  on top).
- **Root-cause-agnostic backstop**: enables this hardware's own
  watchdog timer via `RuntimeWatchdogSec` in `/etc/systemd/system.conf`
  (applied immediately via `systemctl daemon-reexec`). See
  `docs/home-infra-docs/docs/runbooks/homeserver-silent-freeze.md` for
  the outage that motivated this -- a fundamentally different, harder
  freeze than the e1000e bug above, with zero kernel-level log entries
  at all.
- Installs core apt packages (curl, git, htop, vim) plus the interactive
  shell stack (zsh, zsh-autosuggestions, zsh-syntax-highlighting).
- Installs Oh My Zsh and the Powerlevel10k theme, both pinned to a fixed
  git ref -- not a moving branch -- so a prompt update is a deliberate SHA
  bump here, not a surprise on the next apply.
- Adds `admin_user` to the `www-data` group and sets zsh as its login
  shell.

Requires running as root (`become: true`); asserts this explicitly rather
than failing with a confusing permission error partway through.
