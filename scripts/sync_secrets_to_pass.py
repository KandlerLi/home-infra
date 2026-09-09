#!/usr/bin/env python3
"""Sync human-facing login credentials from AWS Secrets Manager into pass.

Source of truth is AWS Secrets Manager (this workspace's own move off
SOPS, PARKED.md's own writeup) -- this is one-way, Secrets Manager ->
pass, never the other direction. pass is a personal convenience copy
for grabbing a credential outside a terminal (e.g. to log into a web
UI, or paste into an iOS Shortcut), not something Ansible reads.

Scope is deliberately narrow: only credentials a human actually types
into a login prompt or browser. Service-to-service secrets a container
reads on its own (blocky_postgres_password, the SES SMTP creds, the
GitHub runner token, the OpenAI API key, shared_ingress's derived bcrypt
hash) stay out of pass -- there's no login flow they'd ever get pasted
into, so mirroring them would just be more places for the same secret to
leak from with no real convenience benefit.

    .venv/bin/python scripts/sync_secrets_to_pass.py [--check]

Requires `aws` and `pass` on PATH, and AWS credentials with
secretsmanager:GetSecretValue on the two secrets below (julian's own
operator policy, bootstrap/terraform-state/operator.tf, already grants
this).
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path

import yaml

REPO_ROOT = Path(__file__).resolve().parents[1]
MONITORING_DEFAULTS_FILE = REPO_ROOT / "ansible" / "roles" / "monitoring" / "defaults" / "main.yml"
INVENTORY_VARS_FILE = REPO_ROOT / "ansible" / "inventory" / "group_vars" / "all" / "main.yml"

SECRETS_MANAGER_REGION = "eu-central-1"

# Each entry is (Secrets Manager secret id, key within that secret's
# JSON value, pass path). deluge_web_password/deluge/password is
# deliberately gone -- Deluge's k3s copy hardcodes a blank password now
# that Authelia gates torrent.jkandler.de, so this pass entry has had no
# real consumer since (PARKED.md's own writeup on this cutover).
SECRET_MAPPINGS = [
    ("home-infra/ingress", "shared_ingress_auth_password", "ingress/password"),
    ("home-infra/grafana", "monitoring_grafana_admin_password", "grafana/password"),
]

# Each entry is (Ansible var name, pass path, role defaults.yml
# fallback). Not secrets themselves (usernames), but worth having
# alongside the password they pair with in pass.
VAR_MAPPINGS = [
    ("monitoring_grafana_admin_user", "grafana/user", MONITORING_DEFAULTS_FILE),
]

# ingress/user has no Ansible variable behind it any more --
# shared_ingress_auth_username's own role (ansible/roles/shared_ingress)
# was deleted once shared_ingress's own move to k3s completed, and
# infra/k3s-apps' own modules/ingress/secret.tf never read a username
# variable at all -- it hardcodes "julian" directly in its Basic Auth
# users string. A literal here, matching that hardcode, is more honest
# than resolving a variable that no longer exists anywhere (found live
# 2026-09-09: the old resolve_var() path for this one crashed outright,
# FileNotFoundError against the already-deleted role's defaults file).
STATIC_MAPPINGS = [
    ("ingress/user", "julian"),
]


def fetch_secret(secret_id: str, *, retries: int = 2) -> dict[str, object]:
    """Return the decoded JSON value of one AWS Secrets Manager secret.

    Retries once on failure -- found live 2026-09-09, repeatedly, across
    three different tools (this script, a raw `aws` CLI call, and an
    Ansible lookup) hitting this exact workspace's own `aws login`
    credential flow: a `CreateOAuth2Token`/"authorization grant is
    invalid" error that a bare retry a moment later reliably clears, not
    a real, persistent auth failure. Only the final attempt's error
    propagates.
    """
    command = [
        "aws",
        "secretsmanager",
        "get-secret-value",
        "--region",
        SECRETS_MANAGER_REGION,
        "--secret-id",
        secret_id,
        "--query",
        "SecretString",
        "--output",
        "text",
    ]
    last_error: subprocess.CalledProcessError | None = None
    for attempt in range(retries):
        result = subprocess.run(command, capture_output=True, text=True)
        if result.returncode == 0:
            return json.loads(result.stdout)
        last_error = subprocess.CalledProcessError(
            result.returncode, command, output=result.stdout, stderr=result.stderr
        )
    assert last_error is not None
    raise RuntimeError(f"aws secretsmanager get-secret-value for {secret_id} failed: {last_error.stderr.strip()}") from last_error


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
        desired_values: list[tuple[str, str]] = []
        secret_groups: dict[str, dict[str, object]] = {}
        for secret_id, key, entry in SECRET_MAPPINGS:
            if secret_id not in secret_groups:
                secret_groups[secret_id] = fetch_secret(secret_id)
            value = secret_groups[secret_id].get(key)
            if not isinstance(value, str) or not value:
                raise ValueError(f"{key} missing or empty in Secrets Manager secret {secret_id}")
            desired_values.append((entry, value))
        for var_name, entry, role_defaults_file in VAR_MAPPINGS:
            desired_values.append((entry, resolve_var(var_name, INVENTORY_VARS_FILE, role_defaults_file)))
        desired_values.extend(STATIC_MAPPINGS)
    except (RuntimeError, ValueError, json.JSONDecodeError) as error:
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
