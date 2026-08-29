# k3s_node

Provisions an isolated Debian VM as the first node of a personal k3s
learning cluster, then installs k3s on it. This is a **learning
project**, kept deliberately separate from the production services
`site.yml` manages -- it lives in its own playbook
(`ansible/playbooks/k3s.yml`) and is not imported into `site.yml`, so it
never runs as a side effect of a normal homelab apply.

Two modes, run as two separate plays in `ansible/playbooks/k3s.yml`:

- `k3s_node_role_mode: provision_vm` -- runs on the homeserver itself
  (needs `libvirt_host` first). Mirrors `github_runner`'s own
  `provision_vm` mode almost exactly (same storage-mount validation,
  same VM/disk/network recovery-state safety checks, same cloud-init
  seed pattern) since that's already a proven pattern in this repo for
  "isolated Debian VM behind libvirt" -- no need to invent a new one.
  Uses its own isolated NAT network (`k3s_network`, 192.168.101.0/24,
  `virbr11`) distinct from both the home LAN and the `github_runner`
  VM's network, and its own MAC/disk path so it can never collide with
  the runner VM. Reuses the same cached Debian cloud image as
  `github_runner` (same URL/checksum/path) so the image is only ever
  downloaded once. Registers the running VM into the `k3s_nodes`
  inventory group (SSH via `ProxyJump` through the homeserver, matching
  `github_runner_vms`).
- `k3s_node_role_mode: configure_guest` -- runs *inside* the VM
  (`hosts: k3s_nodes`). Downloads a specific, checksum-verified k3s
  release binary (same `get_url` + remote-checksum-file idiom
  `provision_vm` already uses for the Debian cloud image -- not the
  `get.k3s.io` install script piped through a shell) and installs it
  under a systemd unit hand-templated from k3s's own upstream unit file,
  so nothing runs unverified. Everything else is left at k3s's own
  defaults -- embedded SQLite datastore, bundled Traefik ingress,
  ServiceLB, local-path-provisioner -- deliberately, so what gets
  learned via `kubectl` is k3s's real out-of-the-box behavior, not a
  customized one.
