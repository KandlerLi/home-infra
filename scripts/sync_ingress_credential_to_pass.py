#!/usr/bin/env python3
"""Sync the shared_ingress Basic Auth credential from sops into pass.

Source of truth is always secrets.sops.yml (what Ansible actually applies
to Traefik) -- this is one-way, sops -> pass, never the other direction.
pass is a personal convenience copy (e.g. for grabbing the credential to
paste into an iOS Shortcut), not something Ansible reads.

    .venv/bin/python scripts/sync_ingress_credential_to_pass.py [--check]

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
ROLE_DEFAULTS_FILE = REPO_ROOT / "ansible" / "roles" / "shared_ingress" / "defaults" / "main.yml"

PASS_USER_ENTRY = "ingress/user"
PASS_PASSWORD_ENTRY = "ingress/password"


def decrypt_password(secrets_file: Path) -> str:
    """Return shared_ingress_auth_password, decrypted from sops."""
    result = subprocess.run(
        ["sops", "-d", str(secrets_file)],
        check=True,
        capture_output=True,
        text=True,
    )
    secrets = yaml.safe_load(result.stdout) or {}
    password = secrets.get("shared_ingress_auth_password")
    if not isinstance(password, str) or not password:
        raise ValueError("shared_ingress_auth_password missing or empty in secrets.sops.yml")
    return password


def resolve_username(inventory_vars_file: Path, role_defaults_file: Path) -> str:
    """Return the effective shared_ingress_auth_username.

    Ansible's real precedence is broader than this (host_vars, group_vars
    on other groups, -e overrides), but this repo only ever sets it in one
    of these two places -- checked directly rather than assumed.
    """
    inventory_vars = yaml.safe_load(inventory_vars_file.read_text()) or {}
    if "shared_ingress_auth_username" in inventory_vars:
        return str(inventory_vars["shared_ingress_auth_username"])

    role_defaults = yaml.safe_load(role_defaults_file.read_text()) or {}
    username = role_defaults.get("shared_ingress_auth_username")
    if not isinstance(username, str) or not username:
        raise ValueError("shared_ingress_auth_username not found in inventory or role defaults")
    return username


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
        username = resolve_username(INVENTORY_VARS_FILE, ROLE_DEFAULTS_FILE)
        password = decrypt_password(SECRETS_FILE)
    except (subprocess.CalledProcessError, ValueError) as error:
        print(f"error: {error}", file=sys.stderr)
        return 1

    changed = False
    changed |= sync_entry(PASS_USER_ENTRY, username, check_only=args.check)
    changed |= sync_entry(PASS_PASSWORD_ENTRY, password, check_only=args.check)

    if args.check and changed:
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
