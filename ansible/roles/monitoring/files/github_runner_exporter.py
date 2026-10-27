"""Expose GitHub's own view of the self-hosted runners as Prometheus metrics.

kube-state-metrics only sees the runner Pods. Whether GitHub counts a
runner as online or busy, and whether runs sit queued with no runner
taking them, only the GitHub API knows. This polls it read-only for each
runner repo and serves the last result on /metrics.
"""

from __future__ import annotations

import json
import logging
import os
import threading
import time
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

LOGGER = logging.getLogger(__name__)

API_URL = "https://api.github.com"
OWNER = os.environ.get("GITHUB_OWNER", "")
REPOSITORIES = [r for r in os.environ.get("GITHUB_REPOSITORIES", "").split(",") if r]
TOKEN_FILE = os.environ.get("GITHUB_TOKEN_FILE", "")
LISTEN_PORT = int(os.environ.get("LISTEN_PORT", "9097"))
POLL_SECONDS = int(os.environ.get("POLL_SECONDS", "60"))
MAX_RESPONSE_BYTES = 1048576

STATES = ("online_idle", "online_busy", "offline")


def read_token() -> str:
    # Reread on every poll, so a rotated token needs no restart.
    with open(TOKEN_FILE, "r", encoding="utf-8") as handle:
        return handle.read().strip()


def get(path: str, token: str) -> dict:
    request = urllib.request.Request(
        API_URL + path,
        headers={
            "Accept": "application/vnd.github+json",
            "Authorization": f"Bearer {token}",
            "X-GitHub-Api-Version": "2022-11-28",
            "User-Agent": "home-infra-github-runner-exporter",
        },
    )
    with urllib.request.urlopen(request, timeout=10) as response:
        return json.loads(response.read(MAX_RESPONSE_BYTES))


def runner_state(runner: dict) -> str:
    if runner.get("status") != "online":
        return "offline"
    return "online_busy" if runner.get("busy") else "online_idle"


def collect(repo: str, token: str) -> dict:
    """Return {"runners": {state: count}, "queued": n} for one repo."""
    runners = get(f"/repos/{OWNER}/{repo}/actions/runners?per_page=100", token)["runners"]
    counts = dict.fromkeys(STATES, 0)
    for runner in runners:
        counts[runner_state(runner)] += 1
    queued = get(f"/repos/{OWNER}/{repo}/actions/runs?status=queued&per_page=1", token)
    return {"runners": counts, "queued": queued["total_count"]}


def render(results: dict[str, dict | None]) -> str:
    """Prometheus text format; a repo whose last poll failed only reports up 0."""
    lines = [
        "# HELP github_runners Self-hosted runners per repo, by GitHub's own status.",
        "# TYPE github_runners gauge",
    ]
    for repo, result in sorted(results.items()):
        if result is not None:
            for state in STATES:
                lines.append(f'github_runners{{repo="{repo}",state="{state}"}} {result["runners"][state]}')
    lines += [
        "# HELP github_actions_queued_runs Workflow runs GitHub reports as queued.",
        "# TYPE github_actions_queued_runs gauge",
    ]
    for repo, result in sorted(results.items()):
        if result is not None:
            lines.append(f'github_actions_queued_runs{{repo="{repo}"}} {result["queued"]}')
    lines += [
        "# HELP github_runner_exporter_up Whether the last poll of this repo succeeded.",
        "# TYPE github_runner_exporter_up gauge",
    ]
    for repo, result in sorted(results.items()):
        lines.append(f'github_runner_exporter_up{{repo="{repo}"}} {0 if result is None else 1}')
    return "\n".join(lines) + "\n"


class State:
    def __init__(self) -> None:
        self.lock = threading.Lock()
        self.results: dict[str, dict | None] = dict.fromkeys(REPOSITORIES)

    def poll_forever(self) -> None:
        while True:
            for repo in REPOSITORIES:
                try:
                    result = collect(repo, read_token())
                except Exception as error:
                    # Never log the request itself: its headers hold the token.
                    LOGGER.warning(
                        "poll of %s failed: %s %s", repo, type(error).__name__, getattr(error, "code", "")
                    )
                    result = None
                with self.lock:
                    self.results[repo] = result
            time.sleep(POLL_SECONDS)

    def metrics(self) -> str:
        with self.lock:
            return render(dict(self.results))


STATE = State()


class MetricsHandler(BaseHTTPRequestHandler):
    def do_GET(self) -> None:
        if self.path == "/healthz":
            body, content_type = b"ok", "text/plain"
        elif self.path == "/metrics":
            body, content_type = STATE.metrics().encode("utf-8"), "text/plain; version=0.0.4"
        else:
            self.send_response(404)
            self.end_headers()
            return
        self.send_response(200)
        self.send_header("Content-Type", content_type)
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, format: str, *args: object) -> None:  # noqa: A002
        pass


def main() -> None:
    logging.basicConfig(level=logging.INFO)
    threading.Thread(target=STATE.poll_forever, daemon=True).start()
    ThreadingHTTPServer(("127.0.0.1", LISTEN_PORT), MetricsHandler).serve_forever()


if __name__ == "__main__":
    main()
