# open_webui

Opt-in chat frontend for `home_agent` (`open_webui_enabled`, default
`false`). Runs upstream Open WebUI, digest-pinned, loopback-only --
`shared_ingress` is what actually exposes it, at `ai.jkandler.de`.

- Pre-wired to `home_agent` as its only OpenAI-compatible provider
  (`open_webui_provider_base_url`), including its transcription endpoint
  for voice input -- `open_webui_stt_model` is cosmetic only, since
  `home_agent` always ignores the caller-stated model and uses its own
  configured one.
- Joins the shared `home-agent-frontend` Docker network (see
  `home_agent_frontend_network_enabled`) to reach the agent by container
  name rather than the host loopback.
- User data lives on `open_webui_data_dir`; enable
  `shared_ingress_open_webui_enabled` (alongside `shared_ingress_agent_enabled`)
  to publish it -- both share `ai.jkandler.de`, split by path: `home_agent`
  answers `/healthz` and `/v1/chat` directly, everything else goes to
  Open WebUI's own UI.
