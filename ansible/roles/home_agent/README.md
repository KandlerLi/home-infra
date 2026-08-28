# home_agent

The private homeserver assistant. Always applied (no enable flag). A
small OpenAI-backed HTTP API, loopback-only, exposing an OpenAI-compatible
`/v1/chat/completions` endpoint plus a legacy `/v1/chat` and
`/v1/audio/transcriptions` (so Open WebUI can use it as both a chat
backend and its speech-to-text engine).

- Runs a Docker container built from `files/agent/` (its own Python
  package), read-only, capabilities dropped, non-root, with a fixed
  server-side system prompt the caller can never override.
- Tool access is deliberately narrow and read-mostly: a host-side
  `home_tools_service.py` (system/Docker health, no arguments accepted)
  plus, when `nextcloud_tools_enabled` is set, the Nextcloud file and
  shopping-list tools from that role -- both reached over Unix sockets,
  never given the container broader access.
- The model and tool round/call counts are capped in code
  (`max_tool_rounds`, `max_tool_calls`); the caller-stated `model` field
  is always ignored in favor of `HOME_AGENT_MODEL`.
- The OpenAI API key is mounted as a file (`OPENAI_API_KEY_FILE`), not a
  plain env var, so it doesn't show up in `docker inspect`.

Set `home_agent_frontend_network_enabled: true` to let `open_webui` reach
it by container name instead of only the host loopback.
