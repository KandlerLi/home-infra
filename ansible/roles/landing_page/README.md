# landing_page

Opt-in static homepage linking to the other home services
(`landing_page_enabled`, default `false`). Published at `home.jkandler.de`
via `shared_ingress` (`shared_ingress_home_enabled`), behind the shared
Basic Auth credential.

- Runs `nginxinc/nginx-unprivileged` (not plain nginx), digest-pinned,
  read-only, capabilities dropped -- the unprivileged image can bind its
  port as a non-root user, so it doesn't need the `NET_BIND_SERVICE`
  capability `shared_ingress` itself needs for ports 80/443.
- Content is pure HTML/CSS, no JavaScript, bind-mounted read-only from a
  host directory (`landing_page_content_dir`) and templated from
  `landing_page_links` (each entry an inline SVG icon, no external font/
  CDN requests).
- Must be registered in `site.yml` before `shared_ingress`: its own
  "verify before publishing" health check needs the loopback container
  already running.
