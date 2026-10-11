#!/usr/bin/env bash
# Runs the tests and syntax check, then site.yml with the right flags. See the README.

set -Eeuo pipefail

usage() {
  echo "usage: $(basename "$0") dry-run|apply" >&2
  exit 1
}

[ $# -eq 1 ] || usage
mode="$1"
case "$mode" in
  dry-run | apply) ;;
  *) usage ;;
esac

script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
repo_root="$(dirname "${script_dir}")"
cd "${repo_root}"

fail() {
  echo "roll-out.sh: $1" >&2
  exit 1
}

access_key_id="$(pass show aws/home-infra-local/access-key-id 2>/dev/null | head -1 || true)"
[ -n "${access_key_id}" ] || fail "pass entry aws/home-infra-local/access-key-id is empty or missing -- see KandlerLi/terraform-state/README.md's 'home-infra-local Identity' section"

secret_access_key="$(pass show aws/home-infra-local/secret-access-key 2>/dev/null | head -1 || true)"
[ -n "${secret_access_key}" ] || fail "pass entry aws/home-infra-local/secret-access-key is empty or missing -- see KandlerLi/terraform-state/README.md's 'home-infra-local Identity' section"

export AWS_ACCESS_KEY_ID="${access_key_id}"
export AWS_SECRET_ACCESS_KEY="${secret_access_key}"

[ -x .venv/bin/ansible-playbook ] || {
  echo "roll-out.sh: .venv/bin/ansible-playbook not found -- set up the venv first (see README.md)" >&2
  exit 1
}

echo "roll-out.sh: running the test suite"
.venv/bin/python3 -m unittest discover -s tests

echo "roll-out.sh: syntax-checking the playbook"
.venv/bin/ansible-playbook ansible/playbooks/site.yml --syntax-check

if [ "${mode}" = "dry-run" ]; then
  echo "roll-out.sh: dry run (--check --diff) -- will prompt for the sudo password"
  .venv/bin/ansible-playbook ansible/playbooks/site.yml --check --diff --ask-become-pass
else
  echo "roll-out.sh: applying for real -- will prompt for the sudo password"
  .venv/bin/ansible-playbook ansible/playbooks/site.yml --ask-become-pass
fi
