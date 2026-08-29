# nfs_server

Exports specific directories under `/mnt/black-hdd` over NFS to specific
clients only -- never a whole parent directory, never a subnet. First
use: sharing Deluge's `downloads`/`deluge-config` directories with the
k3s learning VM, so a Kubernetes-native copy of Deluge can be a real
drop-in replacement (Nextcloud integration included), not just a
disconnected sandbox copy.

Each entry in `nfs_server_exports` needs `path`, `client` (a single IP,
validated to reject CIDR ranges and `0.0.0.0`), and `options`
(validated to reject `no_root_squash` -- client root always maps down to
an unprivileged user server-side). The port itself isn't bound to a
specific interface the way Docker's `published_ports` are elsewhere in
this repo -- NFS doesn't offer that -- so access control here is
entirely the per-export client allowlist in `/etc/exports.d/`, enforced
by `mountd`/`nfsd` checking the connecting IP against it.
