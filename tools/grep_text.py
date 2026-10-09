#!/usr/bin/env python3
"""Search for lines matching a pattern in a text file — llm-functions tool."""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path
from typing import List


def run(
    input: str,
    pattern: str,
    ignore_case: bool = True,
    max_matches: int = 50,
    context_lines: int = 0,
    use_regex: bool = False,
    encoding: str = "utf-8",
) -> str:
    """Find lines in a text file that contain a string or regex pattern.

    Args:
        input: Path to a readable text file.
        pattern: Text to search for, or a regular expression if use_regex is true.
        ignore_case: Case-insensitive matching when true.
        max_matches: Maximum number of matching lines to return.
        context_lines: Number of lines before and after each match to include.
        use_regex: Treat pattern as a regular expression instead of literal text.
        encoding: File encoding when reading.
    """
    path_str = (input or "").strip()
    pat = (pattern or "").strip()
    if not path_str:
        return "ERROR: input is empty"
    if not pat:
        return "ERROR: pattern is empty"

    path = Path(path_str)
    if not path.is_file():
        return f"ERROR: not a file: {path}"

    max_matches = max(1, min(int(max_matches), 500))
    context_lines = max(0, min(int(context_lines), 10))

    try:
        lines = path.read_text(encoding=encoding, errors="replace").splitlines()
    except OSError as e:
        return f"ERROR: {e}"

    flags = re.IGNORECASE if ignore_case else 0
    if use_regex:
        try:
            rx = re.compile(pat, flags)
        except re.error as e:
            return f"ERROR: invalid regex: {e}"

        def matches_line(line: str) -> bool:
            return rx.search(line) is not None
    else:
        needle = pat.casefold() if ignore_case else pat

        def matches_line(line: str) -> bool:
            hay = line.casefold() if ignore_case else line
            return needle in hay

    hit_indices: List[int] = []
    for i, line in enumerate(lines):
        if matches_line(line):
            hit_indices.append(i)
            if len(hit_indices) >= max_matches:
                break

    if not hit_indices:
        return f"path: {path}\npattern: {pat}\nmatches: 0"

    out: List[str] = [
        f"path: {path}",
        f"pattern: {pat}",
        f"matches: {len(hit_indices)}",
        "",
    ]

    shown: set[int] = set()
    for idx in hit_indices:
        start = max(0, idx - context_lines)
        end = min(len(lines), idx + context_lines + 1)
        block: List[str] = []
        for j in range(start, end):
            if j in shown:
                continue
            shown.add(j)
            prefix = ">" if j == idx else " "
            block.append(f"{prefix} {j + 1}: {lines[j]}")
        if block:
            out.extend(block)
            out.append("")

    return "\n".join(out).rstrip()


def _cli() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--input", required=True)
    p.add_argument("--pattern", required=True)
    p.add_argument("--ignore-case", action="store_true", default=True)
    p.add_argument("--no-ignore-case", action="store_false", dest="ignore_case")
    p.add_argument("--max-matches", type=int, default=50)
    p.add_argument("--context-lines", type=int, default=0)
    p.add_argument("--regex", action="store_true", dest="use_regex")
    p.add_argument("--encoding", default="utf-8")
    args = p.parse_args()
    out = run(
        input=args.input,
        pattern=args.pattern,
        ignore_case=args.ignore_case,
        max_matches=args.max_matches,
        context_lines=args.context_lines,
        use_regex=args.use_regex,
        encoding=args.encoding,
    )
    print(out)
    return 0 if not out.startswith("ERROR:") else 1


if __name__ == "__main__":
    sys.exit(_cli())
