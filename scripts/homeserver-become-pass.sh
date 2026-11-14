#!/usr/bin/env bash
# Ansible's --become-password-file runs this and reads the homeserver's sudo password from stdout (pass, ADR 0030).
set -euo pipefail
pass show hosts/homeserver/sudo | head -1
