#!/usr/bin/env bash
# Restore drill for the homeserver's backups, run from the laptop: see home-infra-docs' runbooks/verify-backups.md.

set -Eeuo pipefail

host="julian@192.168.178.100"
borg_pass_entry="${BORG_PASS_ENTRY:-nc/backup}"
region="eu-central-1"

fail() {
  echo "verify-backups: $1" >&2
  exit 1
}

script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
remote_script="${script_dir}/verify-backups-remote.sh"
[ -f "${remote_script}" ] || fail "${remote_script} not found"

borg_pass="$(pass show "${borg_pass_entry}" 2>/dev/null | head -1)" || true
[ -n "${borg_pass}" ] || fail "pass entry ${borg_pass_entry} is empty or missing"

# Only the one key, read in memory; without an AWS session the key check is skipped, not failed.
authelia_key="$(aws secretsmanager get-secret-value --secret-id home-infra/authelia --region "${region}" \
  --query SecretString --output text 2>/dev/null | jq -r '.authelia_storage_encryption_key // empty')" || true
[ -n "${authelia_key}" ] || echo "verify-backups: no AWS session, skipping the Authelia key check ('aws login' to include it)" >&2

read -rsp "sudo password homeserver: " sudo_pw
echo

# Secrets travel on stdin only: sudo takes line 1, the remote script reads the next two.
remote_b64="$(base64 -w0 "${remote_script}")"
printf '%s\n%s\n%s\n' "${sudo_pw}" "${borg_pass}" "${authelia_key}" \
  | ssh "${host}" "sudo -S -p '' bash -c \"\$(echo ${remote_b64} | base64 -d)\""
