# wireguard

**Teardown only.** The first version of this role ran a WireGuard
server on the homeserver (ADR 0024). The admin VPN moved to the
FRITZ!Box's built-in WireGuard instead (ADR 0025, runbook
`fritzbox-wireguard-vpn.md` in `docs/home-infra-docs`).

This role now removes what that version installed: stops and disables
`wg-quick@wg0`, deletes its three iptables rules (FORWARD in/out and
the MASQUERADE for `10.13.13.0/24`), `/etc/wireguard` including the
server key, `/etc/sysctl.d/60-wireguard.conf` and `wireguard-tools`.

Once `site.yml` has been applied with this version, delete the role,
its `site.yml` entry and `tests/test_wireguard.py`.
