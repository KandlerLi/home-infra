#!/usr/bin/env python3
"""Generate infra/k3s-apps' own github_runner_repositories Terraform variable.

Source of truth is repo-infra's config.yml: any repository entry with a
truthy `runner` key gets a self-hosted GitHub Actions runner. This script
reads that file and writes a generated, do-not-hand-edit Terraform
auto.tfvars.json file infra/k3s-apps' own modules/github_runner/ reads
(Terraform auto-loads any *.auto.tfvars.json file in the root module
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
# A sibling repo, not a subdirectory of this one.
DEFAULT_K3S_APPS_OUTPUT = (
    Path(__file__).resolve().parents[2] / "k3s-apps" / "repositories.auto.tfvars.json"
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
        "--k3s-apps-output",
        type=Path,
        default=DEFAULT_K3S_APPS_OUTPUT,
        help="Path to the generated Terraform auto.tfvars.json file",
    )
    args = parser.parse_args(argv)

    if not args.config.exists():
        print(f"error: config file not found: {args.config}", file=sys.stderr)
        return 1

    if not args.k3s_apps_output.parent.is_dir():
        print(
            f"error: k3s-apps output directory not found: {args.k3s_apps_output.parent} "
            "(check out infra/k3s-apps as a sibling directory, or pass --k3s-apps-output)",
            file=sys.stderr,
        )
        return 1

    config = yaml.safe_load(args.config.read_text()) or {}
    repositories = runner_repositories(config)
    args.k3s_apps_output.write_text(render_tfvars(repositories))
    print(f"wrote {len(repositories)} runner repositories to {args.k3s_apps_output}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
