# libvirt_host

Installs KVM/libvirt on the homeserver so it can host the GitHub Actions
runner VM. Always applied when included -- only used by
`ansible/playbooks/github-runner.yml`, not `site.yml`.

- Installs the libvirt/QEMU packages and ensures the `libvirtd` service is
  running.
- Fails fast, with a clear message, if `/dev/kvm` doesn't exist -- usually
  means hardware virtualization is disabled in the BIOS/UEFI.
- Verifies the QEMU runtime user/group exist via `ansible.builtin.getent`
  (which fails the task itself on a missing key -- no separate assert
  needed) and creates the cloud image cache directory.

See the `github_runner` role for the VM this makes possible.
