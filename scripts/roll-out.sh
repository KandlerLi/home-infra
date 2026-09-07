#!/usr/bin/env bash
# Mimics what a real CI check would do for this repo before a real
# local apply: run the test suite + syntax-check, then the actual
# ansible-playbook invocation with the right flags -- so there's one
# command to run instead of remembering the venv path, the playbook
# path, and which flags mean "dry run" vs. "for real".
#
# Unlike github/repo-infra's and bootstrap/k3s-bootstrap's own
# roll-out.sh, this one needs no AWS credentials, no SSH tunnel, and no
# manual secrets export -- Ansible's own community.sops.sops vars
# plugin (ansible.cfg) already decrypts secrets.sops.yml automatically
# at playbook-run time. --ask-become-pass stays interactive on
# purpose: sudo password entry is exactly the kind of credential ADR
# 0018's own principle says shouldn't be scripted away.
#
#   scripts/roll-out.sh dry-run
#   scripts/roll-out.sh apply

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
