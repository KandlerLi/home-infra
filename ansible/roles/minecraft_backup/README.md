# minecraft_backup

The destination side of the Minecraft world backup: a service account
and the NFS-exported directory `/mnt/red-hdd/minecraft-backup`, like
`vaultwarden_backup`.

The backup itself runs as the `backup` sidecar (`itzg/mc-backup`) of
[`k3s-apps`](https://github.com/KandlerLi/k3s-apps)' `argocd/minecraft/deployment.yaml`. Every 24 hours it
pauses world saving over the Pod-internal RCON, tars the world here and
resumes; the server keeps running. 14 days are kept.

The account's uid/gid (980/975) are set explicitly, because the whole
Minecraft Pod runs as exactly these: NFS `root_squash` maps every other
writer to `nobody`, which can't write into this `0700` directory. The
same ids own the world directory on `k3s-node-1` (`k3s_node_host_path_dirs`
in `ansible/playbooks/k3s.yml`).

- Repo: [`home-infra`](https://github.com/KandlerLi/home-infra) (this role, `nfs_server_exports`),
  [`k3s-apps`](https://github.com/KandlerLi/k3s-apps) (`argocd/minecraft/`),
  [`k3s-bootstrap`](https://github.com/KandlerLi/k3s-bootstrap) (the NFS PersistentVolume)
