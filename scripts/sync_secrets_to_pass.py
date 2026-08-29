#!/usr/bin/env python3
"""Sync human-facing login credentials from sops into pass.

Source of truth is always secrets.sops.yml and the Ansible vars that
back it (what's actually applied to the homeserver) -- this is one-way,
sops/vars -> pass, never the other direction. pass is a personal
convenience copy for grabbing a credential outside a terminal (e.g. to
log into a web UI, or paste into an iOS Shortcut), not something Ansible
reads.

Scope is deliberately narrow: only credentials a human actually types
into a login prompt or browser. Service-to-service secrets a container
reads on its own (blocky_postgres_password, the SES SMTP creds, the
GitHub runner token, the OpenAI API key, shared_ingress's derived bcrypt
hash) stay out of pass -- there's no login flow they'd ever get pasted
into, so mirroring them would just be more places for the same secret to
leak from with no real convenience benefit.

    .venv/bin/python scripts/sync_secrets_to_pass.py [--check]

Requires `sops` and `pass` on PATH, and the same GPG key
(6D8B16CB662983A54B4AF1466F0B5C2AB1509600) usable by both -- confirmed
true for this workspace's pass store.
"""

from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

import yaml

REPO_ROOT = Path(__file__).resolve().parents[1]
SECRETS_FILE = REPO_ROOT / "ansible" / "inventory" / "group_vars" / "all" / "secrets.sops.yml"
INVENTORY_VARS_FILE = REPO_ROOT / "ansible" / "inventory" / "group_vars" / "all" / "main.yml"
SHARED_INGRESS_DEFAULTS_FILE = REPO_ROOT / "ansible" / "roles" / "shared_ingress" / "defaults" / "main.yml"
MONITORING_DEFAULTS_FILE = REPO_ROOT / "ansible" / "roles" / "monitoring" / "defaults" / "main.yml"

# Each entry is (sops key in secrets.sops.yml, pass path).
SECRET_MAPPINGS = [
    ("shared_ingress_auth_password", "ingress/password"),
    ("deluge_web_password", "deluge/password"),
    ("monitoring_grafana_admin_password", "grafana/password"),
]

# Each entry is (Ansible var name, pass path, role defaults.yml fallback).
# Not secrets themselves (usernames), but worth having alongside the
# password they pair with in pass.
VAR_MAPPINGS = [
    ("shared_ingress_auth_username", "ingress/user", SHARED_INGRESS_DEFAULTS_FILE),
    ("monitoring_grafana_admin_user", "grafana/user", MONITORING_DEFAULTS_FILE),
]


def decrypt_secrets(secrets_file: Path) -> dict[str, object]:
    """Return every key in secrets.sops.yml, decrypted."""
    result = subprocess.run(
        ["sops", "-d", str(secrets_file)],
        check=True,
        capture_output=True,
        text=True,
    )
    return yaml.safe_load(result.stdout) or {}


def resolve_var(var_name: str, inventory_vars_file: Path, role_defaults_file: Path) -> str:
    """Return the effective value of an Ansible var: inventory override, else role default.

    Ansible's real precedence is broader than this (host_vars, group_vars
    on other groups, -e overrides), but this repo only ever sets these
    vars in one of these two places -- checked directly rather than
    assumed.
    """
    inventory_vars = yaml.safe_load(inventory_vars_file.read_text()) or {}
    if var_name in inventory_vars:
        return str(inventory_vars[var_name])

    role_defaults = yaml.safe_load(role_defaults_file.read_text()) or {}
    value = role_defaults.get(var_name)
    if not isinstance(value, str) or not value:
        raise ValueError(f"{var_name} not found in inventory or role defaults")
    return value


def pass_show(entry: str) -> str | None:
    """Return the current value of a pass entry, or None if it doesn't exist."""
    result = subprocess.run(
        ["pass", "show", entry],
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        return None
    return result.stdout.splitlines()[0] if result.stdout else ""


def pass_insert(entry: str, value: str) -> None:
    """Overwrite a pass entry non-interactively."""
    subprocess.run(
        ["pass", "insert", "--force", "--multiline", entry],
        input=value,
        check=True,
        capture_output=True,
        text=True,
    )


def sync_entry(entry: str, desired_value: str, *, check_only: bool) -> bool:
    """Sync one pass entry to desired_value. Returns True if it was (or would be) changed."""
    current_value = pass_show(entry)
    if current_value == desired_value:
        print(f"{entry}: already in sync")
        return False

    state = "missing" if current_value is None else "drifted"
    if check_only:
        print(f"{entry}: {state}, would update")
        return True

    pass_insert(entry, desired_value)
    print(f"{entry}: {state}, updated")
    return True


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument(
        "--check",
        action="store_true",
        help="Report drift without writing to pass. Exits 1 if anything is out of sync.",
    )
    args = parser.parse_args(argv)

    try:
        secrets = decrypt_secrets(SECRETS_FILE)
        desired_values: list[tuple[str, str]] = []
        for key, entry in SECRET_MAPPINGS:
            value = secrets.get(key)
            if not isinstance(value, str) or not value:
                raise ValueError(f"{key} missing or empty in secrets.sops.yml")
            desired_values.append((entry, value))
        for var_name, entry, role_defaults_file in VAR_MAPPINGS:
            desired_values.append((entry, resolve_var(var_name, INVENTORY_VARS_FILE, role_defaults_file)))
    except (subprocess.CalledProcessError, ValueError) as error:
        print(f"error: {error}", file=sys.stderr)
        return 1

    changed = False
    for entry, desired_value in desired_values:
        changed |= sync_entry(entry, desired_value, check_only=args.check)

    if args.check and changed:
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
