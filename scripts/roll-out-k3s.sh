#!/usr/bin/env bash
# The k3s.yml equivalent of scripts/roll-out.sh -- deliberately a
# separate script, not a mode on that one: k3s.yml itself is
# deliberately NOT imported into site.yml ("a learning project, kept
# separate from the production apply so it never runs as a side effect
# of a normal homelab change" -- see that playbook's own header
# comment), and this script keeping the same separation means running
# the routine roll-out.sh can never accidentally reach a k3s VM
# provision/resize either.
#
# Same no-AWS-creds/no-tunnel/no-secrets-export shape as roll-out.sh --
# this playbook only ever touches the homeserver itself via libvirt,
# nothing external. --ask-become-pass stays interactive on purpose.
#
#   scripts/roll-out-k3s.sh dry-run
#   scripts/roll-out-k3s.sh apply

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
  echo "roll-out-k3s.sh: .venv/bin/ansible-playbook not found -- set up the venv first (see README.md)" >&2
  exit 1
}

echo "roll-out-k3s.sh: running the test suite"
.venv/bin/python3 -m unittest discover -s tests

echo "roll-out-k3s.sh: syntax-checking the playbook"
.venv/bin/ansible-playbook ansible/playbooks/k3s.yml --syntax-check

# k3s_node's own vcpu/memory resize path (added 2026-09-08) genuinely
# stops and restarts the target VM when its allocation changes --
# --check only simulates that, so a dry-run here can't fully prove a
# pending resize is safe the way it can for an ordinary config change.
echo "roll-out-k3s.sh: heads up -- if a vCPU/memory change is pending for either node, applying this WILL shut down and restart that VM (and everything running on it)"

if [ "${mode}" = "dry-run" ]; then
  echo "roll-out-k3s.sh: dry run (--check --diff) -- will prompt for the sudo password"
  .venv/bin/ansible-playbook ansible/playbooks/k3s.yml --check --diff --ask-become-pass
else
  echo "roll-out-k3s.sh: applying for real -- will prompt for the sudo password"
  .venv/bin/ansible-playbook ansible/playbooks/k3s.yml --ask-become-pass
fi
