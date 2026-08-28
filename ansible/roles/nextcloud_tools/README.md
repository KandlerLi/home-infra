# nextcloud_tools

Opt-in restricted Nextcloud file/shopping-list access for `home_agent`
(`nextcloud_tools_enabled`, default `false`).

- Runs a hardened Python service (not a container) on a Unix socket,
  authenticating to Nextcloud as one dedicated, non-admin account
  (`nextcloud_tools_username`) via an app password.
- WebDAV access is bounded to a single allowed root folder
  (`nextcloud_tools_allowed_root`) -- an account's DAV namespace always
  exposes its whole home directory, so this restriction is enforced in
  the service itself, not by Nextcloud. Shopping List access needs no
  such restriction: scope there comes entirely from which lists are
  shared with the account.
- Writes (create/update/delete/move) execute immediately once validated
  -- no confirmation round-trip (superseded by ADR 0013) -- protected
  instead by scope, an extension/size allowlist, and conditional WebDAV
  headers against conflicting changes.
- `home_agent` receives only the Unix socket, never the Nextcloud
  credentials themselves.

Bootstrap with `ansible-playbook ansible/playbooks/nextcloud-tools.yml`
(creates the dedicated account and its first app password); rotate the
password with `rotate-nextcloud-tools-token.yml`; both playbooks require
an exact typed confirmation string. Disable with
`disable-nextcloud-tools.yml`.
