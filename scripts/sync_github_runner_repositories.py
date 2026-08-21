#!/usr/bin/env python3
"""Generate the github_runner_github_repositories Ansible variable.

Source of truth is repo-infra's config.yml: any repository entry with a
truthy `runner` key gets a self-hosted GitHub Actions runner. This script
reads that file and writes the generated Ansible variable file consumed by
the github_runner role.
"""

from __future__ import annotations

import argparse
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


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG, help="Path to repo-infra's config.yml")
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT, help="Path to the generated variable file")
    args = parser.parse_args(argv)

    if not args.config.exists():
        print(f"error: config file not found: {args.config}", file=sys.stderr)
        return 1

    config = yaml.safe_load(args.config.read_text()) or {}
    repositories = runner_repositories(config)
    args.output.write_text(render(repositories))
    print(f"wrote {len(repositories)} runner repositories to {args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
