# libvirt_host

Installs KVM/libvirt on the homeserver so it can host VMs. Always
applied when included -- only used by `ansible/playbooks/k3s.yml`, not
`site.yml`.

- Installs the libvirt/QEMU packages and ensures the `libvirtd` service is
  running.
- Fails fast, with a clear message, if `/dev/kvm` doesn't exist -- usually
  means hardware virtualization is disabled in the BIOS/UEFI.
- Verifies the QEMU runtime user/group exist via `ansible.builtin.getent`
  (which fails the task itself on a missing key -- no separate assert
  needed) and creates the cloud image cache directory.

See the `k3s_node` role for the VMs this makes possible (`k3s-node-1`
and `k3s-node-2`). A now-deleted `github_runner` role used to be this
role's other consumer, its own standalone isolated VM -- gone as of
2026-08-31, moved to a k3s-native replacement entirely (see
`k3s_node`'s own README and `infra/k3s-apps`' `modules/github_runner/`).
