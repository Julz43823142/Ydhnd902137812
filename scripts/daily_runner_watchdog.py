"""Fail-closed GitHub Actions watchdog for the Discord bot's single controller.

Runs in a separate workflow; never runs Discord or reads Discord credentials.
"""
from __future__ import annotations

import json
import os
import sys
from datetime import datetime, timezone
import urllib.error
import urllib.request

WORKFLOW = "daily_puzzle_and_answer.yml"
ACTIVE = {"queued", "in_progress", "waiting", "requested", "pending"}


def should_dispatch(runs):
    """Dispatch only if the API returned a valid, nonempty run history and no active run."""
    if not isinstance(runs, list) or not runs:
        raise ValueError("No reliable Daily Puzzle run history; refusing to dispatch")
    for run in runs:
        if not isinstance(run, dict) or not isinstance(run.get("status"), str):
            raise ValueError("Incomplete run state; refusing to dispatch")
        if run["status"] in ACTIVE:
            return False
        if run["status"] != "completed":
            raise ValueError("Unknown run state; refusing to dispatch")
    last_start = runs[0].get("created_at")
    if last_start:
        started = datetime.fromisoformat(last_start.replace("Z", "+00:00"))
        if (datetime.now(timezone.utc) - started).total_seconds() < 600:
            return False
    return True


def github_request(method, url, token, data=None):
    payload = json.dumps(data).encode() if data is not None else None
    req = urllib.request.Request(
        url, data=payload, method=method,
        headers={
            "Authorization": f"Bearer {token}",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
            "User-Agent": "sharkbot-daily-runner-watchdog",
            **({"Content-Type": "application/json"} if payload is not None else {}),
        },
    )
    with urllib.request.urlopen(req, timeout=20) as response:
        return json.load(response) if method == "GET" else None


def main():
    repository = os.environ["GITHUB_REPOSITORY"]
    token = os.environ["GITHUB_TOKEN"]
    if not repository or len(repository.split("/")) != 2:
        raise ValueError("Invalid repository")
    root = f"https://api.github.com/repos/{repository}/actions"
    # The target workflow's own concurrency group (daily-puzzle) guarantees
    # that an accidentally duplicated dispatch cannot start two bot controllers.
    response = github_request(
        "GET", f"{root}/workflows/{WORKFLOW}/runs?per_page=50", token)
    if not isinstance(response, dict):
        raise ValueError("Invalid workflow response")
    runs = response.get("workflow_runs")
    if not should_dispatch(runs):
        print("Daily Puzzle has an active or pending run; no restart.")
        return
    print("No active or queued Daily Puzzle run. Dispatching recovery.", flush=True)
    github_request("POST", f"{root}/workflows/{WORKFLOW}/dispatches", token,
                   {"ref": "main"})
    print("Recovery workflow dispatch accepted.", flush=True)


if __name__ == "__main__":
    try:
        main()
    except (OSError, ValueError, KeyError, urllib.error.HTTPError) as error:
        print(f"Daily Puzzle watchdog check failed safely: {type(error).__name__}", file=sys.stderr)
        sys.exit(1)
