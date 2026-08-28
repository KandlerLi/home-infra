# shared_ingress

Opt-in Traefik reverse proxy fronting every publicly-reachable home
service (`shared_ingress_enabled`, default `false`), each behind its own
Let's Encrypt certificate.

- Digest-pinned `traefik` image -- the single most security-critical,
  internet-facing container in this repo, so this one gets the most
  scrutiny of any image pin here.
- Each service is published behind its own `shared_ingress_<service>_enabled`
  flag (`nextcloud`, `agent`, `open_webui`, `deluge`, `grafana`, `home`) plus
  a rate limit and max request body size, so enabling this role doesn't
  itself expose anything -- each route is opted in separately.
- One shared Basic Auth credential (`shared_ingress_auth_username`/
  `_password_hash`) covers every gated route -- a deliberate trade of
  per-service blast-radius isolation for one password to manage, accepted
  for a single-user homelab.
- `shared_ingress_apex_redirect_enabled` gives the bare `jkandler.de` its
  own router + cert that redirects to `www.jkandler.de`, without touching
  the apex DNS record `dyndns` manages.
- Cutting an already-live service (e.g. Nextcloud) over from its own
  direct port to routing through here is a guarded, confirmation-gated
  step (`shared_ingress_cutover_confirmation`, `shared_ingress_start`) --
  see `ansible/playbooks/shared-ingress-prepare.yml` and
  `shared-ingress.yml`, with `shared-ingress-rollback.yml` to back out.
