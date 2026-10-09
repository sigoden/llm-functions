#!/usr/bin/env python3
"""Format, minify, or validate JSON — llm-functions tool."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Tuple


def _load_json(source: str) -> Tuple[Any, str]:
    s = source.strip()
    if not s:
        raise ValueError("empty input")
    path = Path(s)
    if path.is_file():
        text = path.read_text(encoding="utf-8")
        return json.loads(text), str(path)
    return json.loads(s), "<inline>"


def run(
    input: str,
    action: str = "format",
    indent: int = 2,
    sort_keys: bool = False,
) -> str:
    """Format, minify, or validate JSON from a file path or inline string.

    Args:
        input: Path to a .json file, or a JSON string.
        action: format (pretty print), minify (one line), or validate (no output body).
        indent: Spaces per level when action is format.
        sort_keys: Sort object keys when formatting or minifying.
    """
    act = (action or "format").lower().strip()
    if act not in ("format", "minify", "validate"):
        return "ERROR: action must be format, minify, or validate"

    try:
        data, label = _load_json(input)
    except ValueError as e:
        return f"ERROR: {e}"
    except json.JSONDecodeError as e:
        return f"ERROR: invalid JSON: {e}"
    except OSError as e:
        return f"ERROR: {e}"

    if act == "validate":
        return f"valid: true\nsource: {label}"

    if act == "minify":
        body = json.dumps(
            data, ensure_ascii=False, separators=(",", ":"), sort_keys=sort_keys
        )
    else:
        body = json.dumps(
            data,
            ensure_ascii=False,
            indent=max(0, int(indent)),
            sort_keys=sort_keys,
        )

    return body


def _cli() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--input", required=True)
    p.add_argument(
        "--action", default="format", choices=("format", "minify", "validate")
    )
    p.add_argument("--indent", type=int, default=2)
    p.add_argument("--sort-keys", action="store_true")
    args = p.parse_args()
    out = run(args.input, args.action, args.indent, args.sort_keys)
    print(out)
    return 0 if not out.startswith("ERROR:") else 1


if __name__ == "__main__":
    sys.exit(_cli())
