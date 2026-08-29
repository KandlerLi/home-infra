# k3s_node

Provisions an isolated Debian VM as the first node of a personal k3s
learning cluster. This is a **learning project**, kept deliberately
separate from the production services `site.yml` manages -- it lives in
its own playbook (`ansible/playbooks/k3s.yml`) and is not imported into
`site.yml`, so it never runs as a side effect of a normal homelab apply.

Mirrors `github_runner`'s own `provision_vm` mode almost exactly (same
storage-mount validation, same VM/disk/network recovery-state safety
checks, same cloud-init seed pattern) since that's already a
proven pattern in this repo for "isolated Debian VM behind libvirt" --
no need to invent a new one. The two differences: no GitHub-specific
configuration, and this role only stands up the bare VM. Installing k3s
itself is intentionally a separate, later step -- the point of this
project is learning Kubernetes, not learning VM provisioning (which this
repo already has solved).

Needs `libvirt_host` to have run first (same as `github_runner`). Uses
its own isolated NAT network (`k3s_network`, 192.168.101.0/24,
`virbr11`) distinct from both the home LAN and the `github_runner` VM's
network, and its own MAC/disk path so it can never collide with the
runner VM. Reuses the same cached Debian cloud image as `github_runner`
(same URL/checksum/path) so the image is only ever downloaded once.

Registers the running VM into the `k3s_nodes` inventory group (SSH via
`ProxyJump` through the homeserver, matching `github_runner_vms`) for
whatever role configures k3s on it next.
