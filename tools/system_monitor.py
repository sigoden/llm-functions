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

"""System Monitor Tool

Provides basic system metrics (CPU load, memory usage) without external dependencies.
"""
from __future__ import annotations
import argparse
import json
import sys
import os
from typing import Any, Dict

__version__ = "1.0.0"

def _cpu_load() -> float:
    # Read /proc/loadavg if available (Linux)
    try:
        with open('/proc/loadavg') as f:
            return float(f.read().split()[0])
    except Exception:
        return 0.0

def _mem_usage() -> Dict[str, int]:
    # Return total and available memory in kB from /proc/meminfo
    info = {}
    try:
        with open('/proc/meminfo') as f:
            for line in f:
                parts = line.split(':')
                if len(parts) != 2:
                    continue
                key, value = parts
                if key in ('MemTotal', 'MemAvailable'):
                    info[key] = int(value.strip().split()[0])
    except Exception:
        pass
    return {"total_kb": info.get('MemTotal', 0), "available_kb": info.get('MemAvailable', 0)}

def run(metrics: str = "cpu,mem", **_: Any) -> Dict[str, Any]:
    """Return selected system metrics.

    Args:
        metrics: comma‑separated list containing any of "cpu" or "mem".
    """
    result: Dict[str, Any] = {}
    for m in (m.strip().lower() for m in metrics.split(',')):
        if m == 'cpu':
            result['cpu_load'] = _cpu_load()
        elif m == 'mem':
            result['mem'] = _mem_usage()
        else:
            return {"success": False, "error": {"code": "UNKNOWN_METRIC", "message": f"Unsupported metric {m}"}}
    return {"success": True, "data": result}

def _cli() -> int:
    parser = argparse.ArgumentParser(description="System monitor tool")
    parser.add_argument("--metrics", default="cpu,mem", help="Comma‑separated metrics to report (cpu,mem)")
    args = parser.parse_args()
    res = run(metrics=args.metrics)
    _print_human_readable_ui(res)
    _print_human_readable_ui(res)
    _print_human_readable_ui(res)
    print(json.dumps(res, indent=2))
    return 0 if res.get('success') else 1

if __name__ == "__main__":
    sys.exit(_cli())
