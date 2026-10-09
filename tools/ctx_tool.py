#!/usr/bin/env python3
# ==============================================================================
# ctx_tool.py — Project Context Librarian v1.0.0
# Structural alignment with edit.py & maps_tool.py
# ==============================================================================

from __future__ import annotations

import argparse
import functools
import json
import logging
import sys
import time
from pathlib import Path
from typing import Any, Callable, Dict

# ==============================================================================
# SECTION 1: Logger & Setup
# ==============================================================================
logger = logging.getLogger(__name__)
if not logger.handlers:
    logger.addHandler(logging.StreamHandler(sys.stderr))

# ==============================================================================
# SECTION 2: Decorators & Helpers
# ==============================================================================


def _timed(fn: Callable[..., dict[str, Any]]) -> Callable[..., dict[str, Any]]:
    @functools.wraps(fn)
    def wrapper(*args: Any, **kwargs: Any) -> dict[str, Any]:
        t0 = time.perf_counter()
        result = fn(*args, **kwargs)
        if not isinstance(result, dict):
            result = {"success": False, "error": "Operation returned non-dict"}
        result["duration_ms"] = round((time.perf_counter() - t0) * 1000, 3)
        return result

    return wrapper


# ==============================================================================
# SECTION 3: Operations
# ==============================================================================


@_timed
def map_project() -> dict[str, Any]:
    """Generates a tree structure of the project."""
    tree = {}
    root = Path.cwd()
    for path in root.rglob("*"):
        if (
            ".git" in path.parts
            or ".pytest_cache" in path.parts
            or "__pycache__" in path.parts
        ):
            continue
        if path.is_file():
            rel_path = path.relative_to(root)
            # Simple nested dict construction
            parts = rel_path.parts
            d = tree
            for part in parts[:-1]:
                d = d.setdefault(part, {})
            d[parts[-1]] = "file"

    return {"success": True, "tree": tree}


@_timed
def summarize_project() -> dict[str, Any]:
    """Extracts key information from project metadata."""
    summary = {}
    for md_file in ["README.md", "GEMINI.md", "MEMORY.md"]:
        path = Path.cwd() / md_file
        if path.exists():
            summary[md_file] = path.read_text(encoding="utf-8")[
                :2000
            ]  # Truncate for context
    return {"success": True, "summary": summary}


# ==============================================================================
# SECTION 4: Dispatcher
# ==============================================================================


def run(operation: Optional[str] = None, **kwargs: Any) -> dict[str, Any]:
    """Dispatcher entry point."""
    if operation is None:
        operation = kwargs.get("operation", "map")

    dispatch: Dict[str, Callable[[], dict[str, Any]]] = {
        "map": map_project,
        "summarize": summarize_project,
    }

    if operation not in dispatch:
        return {"success": False, "error": f"Unknown operation: {operation}"}

    try:
        return dispatch[operation]()
    except Exception as e:
        logger.exception("Error in operation %s", operation)
        return {"success": False, "error": str(e)}


# ==============================================================================
# SECTION 5: CLI
# ==============================================================================


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="ctx_tool.py", description="Project Context Librarian"
    )
    subparsers = parser.add_subparsers(dest="operation", required=True)

    subparsers.add_parser("map", help="Generate project tree")
    subparsers.add_parser("summarize", help="Extract project summary")

    return parser


if __name__ == "__main__":
    parser = _build_parser()
    args = parser.parse_args()

    result = run(**vars(args))
    print(json.dumps(result, indent=2))
    sys.exit(0 if result.get("success") else 1)
