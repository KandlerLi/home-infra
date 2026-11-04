#!/usr/bin/env bash
# After merging a PR: fresh main and local Ansible dependencies, then the roll-out script. See README.

set -euo pipefail

usage() {
  echo "usage: $(basename "$0") dry-run|apply [k3s]" >&2
  exit 1
}

[ $# -ge 1 ] && [ $# -le 2 ] || usage
mode="$1"
case "$mode" in
  dry-run | apply) ;;
  *) usage ;;
esac
case "${2:-site}" in
  site) roll_out=scripts/roll-out.sh ;;
  k3s) roll_out=scripts/roll-out-k3s.sh ;;
  *) usage ;;
esac

script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$(dirname "${script_dir}")"

# Would otherwise roll out unmerged changes.
if [ -n "$(git status --porcelain --untracked-files=no)" ]; then
  echo "update.sh: uncommitted changes -- commit or stash them first" >&2
  exit 1
fi

git checkout main
git pull --ff-only origin main

.venv/bin/pip install --quiet -r requirements.txt
.venv/bin/ansible-galaxy collection install -r requirements.yml

exec "${roll_out}" "${mode}"
