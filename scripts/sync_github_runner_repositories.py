#!/usr/bin/env python3
"""Generate the k3s-bootstrap repo's own github_runner_repositories Terraform variable.

Source of truth is repo-infra's config.yml: any repository entry with a
truthy `runner` key gets a self-hosted GitHub Actions runner. This script
reads that file and writes a generated, do-not-hand-edit Terraform
auto.tfvars.json file the k3s-bootstrap repo's own modules/github_runner/
reads (Terraform auto-loads any *.auto.tfvars.json file in the root module
directory, so nothing there needs to reference it explicitly).

Used to also generate an Ansible variable file too, back when
github_runner ran as a VM-based Ansible role here -- that role is gone
now (moved to infra/k3s-apps entirely, see that migration's own plan),
so this script's only remaining consumer is the Terraform side.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import yaml

DEFAULT_CONFIG = Path(__file__).resolve().parents[3] / "bootstrap" / "repo-infra" / "config.yml"
# A sibling repo, not a subdirectory of this one. In the standalone
# k3s-bootstrap repo (extracted 2026-09-03 from what was originally a
# bootstrap/ subdirectory inside infra/k3s-apps itself -- see that
# migration's own plan) since this file feeds a variable
# modules/github_runner/variables.tf declares, and that module now
# lives there, alongside repo-infra/terraform-state, not inside
# k3s-apps' own repo at all.
DEFAULT_K3S_BOOTSTRAP_OUTPUT = (
    Path(__file__).resolve().parents[3] / "bootstrap" / "k3s-bootstrap" / "repositories.auto.tfvars.json"
)


def runner_repositories(config: dict) -> list[dict]:
    """Return the sorted list of {id, repository} entries with runner: true."""
    repositories = []
    for name in sorted(config):
        entry = config[name] or {}
        if entry.get("runner"):
            repositories.append({"id": name, "repository": name})
    return repositories


def render_tfvars(repositories: list[dict]) -> str:
    return json.dumps({"github_runner_repositories": repositories}, indent=2) + "\n"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG, help="Path to repo-infra's config.yml")
    parser.add_argument(
        "--k3s-bootstrap-output",
        type=Path,
        default=DEFAULT_K3S_BOOTSTRAP_OUTPUT,
        help="Path to the generated Terraform auto.tfvars.json file",
    )
    args = parser.parse_args(argv)

    if not args.config.exists():
        print(f"error: config file not found: {args.config}", file=sys.stderr)
        return 1

    if not args.k3s_bootstrap_output.parent.is_dir():
        print(
            f"error: k3s-bootstrap output directory not found: {args.k3s_bootstrap_output.parent} "
            "(check out bootstrap/k3s-bootstrap as a sibling of bootstrap/repo-infra, "
            "or pass --k3s-bootstrap-output)",
            file=sys.stderr,
        )
        return 1

    config = yaml.safe_load(args.config.read_text()) or {}
    repositories = runner_repositories(config)
    args.k3s_bootstrap_output.write_text(render_tfvars(repositories))
    print(f"wrote {len(repositories)} runner repositories to {args.k3s_bootstrap_output}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
