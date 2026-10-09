#!/usr/bin/env python3
# BEGIN HUMAN READABLE UI PATCH
import json, sys
def _print_human_readable_ui(res):
    if not isinstance(res, dict):
        return
    if res.get('success'):
        data = res.get('data', {})
        if isinstance(data, dict):
            for k, v in data.items():
                print(f'>>> {k}: {v}', file=sys.stderr)
    else:
        err = res.get('error')
        if isinstance(err, dict):
            print(f'!!! Error: {err.get("message", err)}', file=sys.stderr)
        else:
            print(f'!!! Error: {err}', file=sys.stderr)
# END HUMAN READABLE UI PATCH

"""Cron Job Scheduler Tool

Provides management of system cron jobs: list existing jobs, add a new job, or remove a job by its line number.
The implementation uses the system `crontab` command and works without external Python dependencies.
"""
# __future__ import (annotations) removed - not required
import argparse
import subprocess
import sys
import json
from typing import Any, List, Dict

__version__ = "1.0.0"

def _run_crontab(args: List[str]) -> str:
    """Execute the `crontab` command and return its stdout.
    Raises RuntimeError on failure.
    """
    result = subprocess.run(["crontab"] + args, capture_output=True, text=True)
    if result.returncode != 0:
        raise RuntimeError(f"crontab error: {result.stderr.strip()}")
    return result.stdout.strip()

def _list_jobs() -> List[str]:
    """Return the current user's crontab lines (excluding blanks and comments)."""
    try:
        output = _run_crontab(["-l"])
    except RuntimeError:
        return []  # No crontab yet
    jobs = []
    for line in output.splitlines():
        stripped = line.strip()
        if stripped and not stripped.startswith("#"):
            jobs.append(stripped)
    return jobs

def _write_jobs(jobs: List[str]) -> None:
    """Replace the current crontab with the supplied job lines."""
    content = "\n".join(jobs) + "\n"
    proc = subprocess.run(["crontab", "-"], input=content, text=True, capture_output=True)
    if proc.returncode != 0:
        raise RuntimeError(f"Failed to write crontab: {proc.stderr.strip()}")

def run(action: str = "list", schedule: str = "", command: str = "", line: int = -1, **_: Any) -> Dict[str, Any]:
    """Programmatic entrypoint.

    Args:
        action: "list", "add", or "remove".
        schedule: Cron timing string (e.g. "0 * * * *") required for "add".
        command: Shell command to schedule, required for "add".
        line: 1‑based line number to remove, required for "remove".
    """
    try:
        if action == "list":
            return {"success": True, "data": {"jobs": _list_jobs()}}
        if action == "add":
            if not schedule or not command:
                return {"success": False, "error": {"code": "INVALID_INPUT", "message": "schedule and command are required for add"}}
            jobs = _list_jobs()
            jobs.append(f"{schedule} {command}")
            _write_jobs(jobs)
            return {"success": True, "data": {"message": "Job added", "jobs": jobs}}
        if action == "remove":
            if line <= 0:
                return {"success": False, "error": {"code": "INVALID_INPUT", "message": "line must be > 0 for remove"}}
            jobs = _list_jobs()
            if line > len(jobs):
                return {"success": False, "error": {"code": "OUT_OF_RANGE", "message": f"Line {line} out of range (1‑{len(jobs)})"}}
            removed = jobs.pop(line - 1)
            _write_jobs(jobs)
            return {"success": True, "data": {"message": f"Removed job: {removed}", "jobs": jobs}}
        return {"success": False, "error": {"code": "INVALID_ACTION", "message": f"Unknown action '{action}'"}}
    except Exception as e:
        return {"success": False, "error": {"code": "EXCEPTION", "message": str(e)}}

def _cli() -> int:
    parser = argparse.ArgumentParser(description="Cron job scheduler")
    parser.add_argument("--action", default="list", choices=["list", "add", "remove"])
    parser.add_argument("--schedule", help="Cron schedule expression (required for add)")
    parser.add_argument("--command", help="Command to run (required for add)")
    parser.add_argument("--line", type=int, default=-1, help="Line number to delete (required for remove)")
    args = parser.parse_args()
    res = run(action=args.action, schedule=args.schedule or "", command=args.command or "", line=args.line)
    _print_human_readable_ui(res)
    _print_human_readable_ui(res)
    _print_human_readable_ui(res)
    print(json.dumps(res, indent=2))
    return 0 if res.get("success") else 1

if __name__ == "__main__":
    sys.exit(_cli())
