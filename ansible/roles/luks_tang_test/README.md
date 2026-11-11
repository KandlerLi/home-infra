# luks_tang_test

A small LUKS2 test volume on the homeserver's SSD, unlocked at boot
through Tang on `rasp` (Clevis). It rehearses the setup the HDDs will get
(tracker #41) without touching real data, and is removed once they are
done.

- An image file (`/var/lib/luks-tang-test.img`, 64 MiB), never a disk:
  the role only formats a file that isn't LUKS yet.
- Two ways in: a recovery passphrase (in `pass` as
  `hosts/homeserver/luks-test`) and a Clevis Tang binding. The binding
  pins Tang's signing key thumbprint (`luks_tang_test_tang_thp`), so a
  different server can't stand in for it. After a Tang key rotation,
  update the thumbprint and rebind.
- At boot, `/etc/crypttab` (`_netdev,nofail`) asks for the passphrase
  and `clevis-luks-askpass` answers it through Tang. `nofail` keeps the
  boot going if Tang can't be reached; the volume then stays locked.
- Mounted at `/mnt/luks-tang-test` with a `marker.txt` written on apply.

## Apply

The first run formats and binds, so it needs the passphrase. Create it
once, then pass it in through the environment (not as an argument):

```bash
pass generate -n hosts/homeserver/luks-test 32
LUKS_TANG_TEST_PASSPHRASE="$(pass show hosts/homeserver/luks-test)" \
  .venv/bin/ansible-playbook ansible/playbooks/luks-test.yml --ask-become-pass
```

Later runs need no passphrase unless the binding is gone.

## Prove it

After a reboot, without typing anything:

```bash
sudo cat /mnt/luks-tang-test/marker.txt
sudo clevis luks list -d /var/lib/luks-tang-test.img
```

Failure cases worth one test each: Tang stopped (volume stays locked,
boot continues, unlock with the passphrase), and a cold start of both
machines at once.

## Remove

```bash
sudo umount /mnt/luks-tang-test
sudo cryptsetup close luks-tang-test
sudo sed -i '/^luks-tang-test /d' /etc/crypttab
sudo sed -i '\#/mnt/luks-tang-test #d' /etc/fstab
sudo rm /var/lib/luks-tang-test.img
pass rm hosts/homeserver/luks-test
```

Then delete this role and `ansible/playbooks/luks-test.yml`.
