# luks_tang_test

A small LUKS2 test volume on the homeserver's SSD, unlocked at boot
through Tang on `rasp` (Clevis). It rehearses the setup the HDDs will get
(tracker #41) without touching real data, and is removed once they are
done.

- An image file (`/var/lib/luks-tang-test.img`, 64 MiB), never a disk:
  the role only formats a volume that isn't LUKS yet.
- The image is attached as `/dev/loop7` by `luks-tang-test-loop.service`,
  which a drop-in on the volume's `systemd-cryptsetup@` unit pulls in
  (crypttab itself has no `x-systemd.requires=`). Two reasons:
  `clevis-luks-askpass` only answers for block devices (it skipped the
  bare file at boot), and at shutdown systemd now unmounts, closes LUKS
  and detaches the loop device in order.
- Two ways in: a recovery passphrase (in `pass` as
  `hosts/homeserver/luks-test`) and a Clevis Tang binding. The binding
  pins Tang's signing key thumbprint (`luks_tang_test_tang_thp`), so a
  different server can't stand in for it. After a Tang key rotation,
  update the thumbprint and rebind.
- At boot, `/etc/crypttab` (`_netdev,nofail`) asks for the passphrase
  and `clevis-luks-askpass` answers it through Tang. `nofail` keeps the
  boot going if Tang can't be reached; the volume then stays locked.
- The volume is only ever opened by its systemd unit, also on apply,
  so systemd owns the mapping and tears it down in order.
- Mounted at `/mnt/luks-tang-test` with a `marker.txt` written on apply.

## Paused

`luks_tang_test_active` is `false` by default: the role closes the
volume, detaches the loop device and marks crypttab and fstab `noauto`,
so the volume stays closed at boot. Reason: every reboot after dm-crypt
had been used in that boot hung after `systemd-shutdown` took over
(three of three, 2026-11-11/12), even with the volume torn down cleanly
before; the one reboot without dm-crypt was fine. Tracker #41.

To open it again for a test, pass `-e luks_tang_test_active=true`.

## Apply

The first run formats and binds, so it needs the passphrase. Create it
once, then pass it in through the environment (not as an argument):

```bash
pass generate -n hosts/homeserver/luks-test 32
LUKS_TANG_TEST_PASSPHRASE="$(pass show hosts/homeserver/luks-test)" \
  .venv/bin/ansible-playbook ansible/playbooks/luks-test.yml --ask-become-pass \
  -e luks_tang_test_active=true
```

Later runs need no passphrase unless the binding is gone. Without
`-e luks_tang_test_active=true` a run closes the volume again.

## Prove it

After a reboot, without typing anything:

```bash
sudo cat /mnt/luks-tang-test/marker.txt
sudo clevis luks list -d /dev/loop7
```

Failure cases worth one test each: Tang stopped (volume stays locked,
boot continues, unlock with the passphrase), and a cold start of both
machines at once.

## Remove

```bash
sudo umount /mnt/luks-tang-test
sudo systemctl stop 'systemd-cryptsetup@luks\x2dtang\x2dtest.service' luks-tang-test-loop.service
sudo sed -i '/^luks-tang-test /d' /etc/crypttab
sudo sed -i '\#/mnt/luks-tang-test #d' /etc/fstab
sudo rm -r /etc/systemd/system/luks-tang-test-loop.service '/etc/systemd/system/systemd-cryptsetup@luks\x2dtang\x2dtest.service.d' /var/lib/luks-tang-test.img
sudo systemctl daemon-reload
pass rm hosts/homeserver/luks-test
```

Then delete this role and `ansible/playbooks/luks-test.yml`.
