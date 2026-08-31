# k3s_node

Provisions isolated Debian VMs for a personal k3s learning cluster,
then installs k3s on them. This is a **learning project**, kept
deliberately separate from the production services `site.yml` manages
-- it lives in its own playbook (`ansible/playbooks/k3s.yml`) and is
not imported into `site.yml`, so it never runs as a side effect of a
normal homelab apply.

Two nodes share this one role, distinguished by `k3s_node_join_mode`:
`k3s-node-1` (`server`, the default -- everything else in the cluster
runs here) and `k3s-node-2` (`agent`, joined to `k3s-node-1`'s control
plane and tainted `ci=github-runner:NoSchedule` so only
`infra/k3s-apps`' own `modules/github_runner/` Pods ever land there --
see that repo's README). Both share the same isolated NAT network
(`k3s_network`, `virbr11`) rather than each getting their own: a
second DHCP reservation is added to the *live* network via `virsh
net-update ... --live --config` when a second node provisions
(`provision_vm.yml`), never a full redefine -- that would only touch
libvirt's persistent config, not the already-running dnsmasq instance,
and could otherwise interrupt `k3s-node-1`'s own active connections.
`k3s-node-2`'s own join token is read live off `k3s-node-1`'s
`/var/lib/rancher/k3s/server/node-token` in `ansible/playbooks/k3s.yml`
itself (`no_log: true` throughout) -- never written to the controller's
disk or committed anywhere.

Two modes, run as two separate plays in `ansible/playbooks/k3s.yml`:

- `k3s_node_role_mode: provision_vm` -- runs on the homeserver itself
  (needs `libvirt_host` first). Mirrors the shape of the old, now-
  deleted VM-based `github_runner` role's own `provision_vm` mode
  almost exactly (same storage-mount validation, same VM/disk/network
  recovery-state safety checks, same cloud-init seed pattern) since
  that was already a proven pattern in this repo for "isolated Debian
  VM behind libvirt." Uses its own isolated NAT network (`k3s_network`,
  192.168.101.0/24, `virbr11`) distinct from the home LAN, and its own
  MAC/disk path. Reuses the same cached Debian cloud image path
  `github_runner` used to (same URL/checksum/path convention) so the
  image is only ever downloaded once even with two nodes now sharing
  it. Registers the running VM into the `k3s_nodes` inventory group
  (SSH via `ProxyJump` through the homeserver). `k3s-node-2`'s own
  provisioning play overrides
  `k3s_node_vm_name`/`_disk_path`/`_ip`/`_mac`/`_ssh_host_key_alias`
  and `k3s_node_inventory_group: k3s_agent_nodes`, so it lands in a
  distinct inventory group from `k3s-node-1` rather than a generic
  loop over both.
- `k3s_node_role_mode: configure_guest` -- runs *inside* the VM
  (`hosts: k3s_nodes` for the server, `hosts: k3s_agent_nodes` for the
  agent). Downloads a specific, checksum-verified k3s release binary
  (same `get_url` + remote-checksum-file idiom `provision_vm` already
  uses for the Debian cloud image -- not the `get.k3s.io` install
  script piped through a shell) and installs it under a systemd unit
  hand-templated from k3s's own upstream unit file, so nothing runs
  unverified. `k3s_node_join_mode` picks which: `server` (default)
  renders `k3s server` with everything else left at k3s's own defaults
  -- embedded SQLite datastore, ServiceLB, local-path-provisioner --
  deliberately, so what gets learned via `kubectl` is k3s's real
  out-of-the-box behavior, not a customized one. Bundled Traefik is
  the one deliberate exception (`config.yaml`'s `disable: [traefik]`):
  `infra/k3s-apps`' own `modules/ingress/` Deployment replaces it
  entirely, since real production ACME/Basic-Auth/rate-limit config
  has no business living in a role that's meant to stay at defaults,
  and two Traefik instances can't both hold the LoadBalancer ports or
  usefully watch the same Ingress resources at once; `agent` renders
  `k3s agent --node-taint ... --node-label ...` instead, joining over
  `K3S_URL`/`K3S_TOKEN` in a mode-`0600` env file. An agent has no
  kubeconfig of its own (only a server generates one), so its own
  Ready-wait polls its systemd unit's own `ActiveState` rather than
  `kubectl get nodes` -- the real Ready check happens from
  `k3s-node-1`'s own side instead.
