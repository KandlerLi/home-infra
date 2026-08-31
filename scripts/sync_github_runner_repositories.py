#!/usr/bin/env python3
"""Generate the github_runner_github_repositories Ansible variable, and
infra/k3s-apps' own Terraform equivalent.

Source of truth is repo-infra's config.yml: any repository entry with a
truthy `runner` key gets a self-hosted GitHub Actions runner. This script
reads that file and writes two generated, do-not-hand-edit consumers of
the same list: the Ansible variable file the (VM-based) github_runner
role reads, and a Terraform auto.tfvars.json file infra/k3s-apps' own
modules/github_runner/ reads (Terraform auto-loads any *.auto.tfvars.json
file in the root module directory, so nothing there needs to reference it
explicitly).
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import yaml

GENERATED_HEADER = """\
# GENERATED FILE -- do not hand-edit.
#
# Source of truth: bootstrap/repo-infra/config.yml (the `runner: true` key
# on each repository entry). Regenerate with:
#
#   .venv/bin/python scripts/sync_github_runner_repositories.py
#
# Then review the diff, commit it, and run the github-runner.yml playbook.
"""

DEFAULT_CONFIG = Path(__file__).resolve().parents[3] / "bootstrap" / "repo-infra" / "config.yml"
DEFAULT_OUTPUT = (
    Path(__file__).resolve().parents[1]
    / "ansible"
    / "inventory"
    / "group_vars"
    / "all"
    / "github_runner_repositories.yml"
)
# A sibling repo, not a subdirectory of this one -- see
# infra/k3s-apps/modules/github_runner/README (or its own file header)
# for why it consumes the exact same repository list rather than
# maintaining an independent one.
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


def render(repositories: list[dict]) -> str:
    body = {"github_runner_github_repositories": repositories}
    return GENERATED_HEADER + "\n" + yaml.safe_dump(body, sort_keys=False, default_flow_style=False)


def render_tfvars(repositories: list[dict]) -> str:
    return json.dumps({"github_runner_repositories": repositories}, indent=2) + "\n"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG, help="Path to repo-infra's config.yml")
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT, help="Path to the generated Ansible variable file")
    parser.add_argument(
        "--k3s-apps-output",
        type=Path,
        default=DEFAULT_K3S_APPS_OUTPUT,
        help="Path to the generated Terraform auto.tfvars.json file",
    )
    parser.add_argument(
        "--skip-k3s-apps",
        action="store_true",
        help="Only write the Ansible variable file -- for checkouts without a sibling k3s-apps repo",
    )
    args = parser.parse_args(argv)

    if not args.config.exists():
        print(f"error: config file not found: {args.config}", file=sys.stderr)
        return 1

    config = yaml.safe_load(args.config.read_text()) or {}
    repositories = runner_repositories(config)
    args.output.write_text(render(repositories))
    print(f"wrote {len(repositories)} runner repositories to {args.output}")

    if not args.skip_k3s_apps:
        if not args.k3s_apps_output.parent.is_dir():
            print(
                f"error: k3s-apps output directory not found: {args.k3s_apps_output.parent} "
                "(pass --skip-k3s-apps if you don't have that sibling repo checked out)",
                file=sys.stderr,
            )
            return 1
        args.k3s_apps_output.write_text(render_tfvars(repositories))
        print(f"wrote {len(repositories)} runner repositories to {args.k3s_apps_output}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
