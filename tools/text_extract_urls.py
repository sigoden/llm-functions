#!/usr/bin/env python3
# ==============================================================================
# text_extract_urls.py
#
# @describe Extract all unique URLs from arbitrary text.
# @option --text! <TEXT> The text to search for URLs.
# @option --limit[=500] <INT> Max number of unique URLs to return (1-5000).
# @option --pretty  Emit rich neon boxed human UI on stderr.
# ==============================================================================

from __future__ import annotations

import json
import os
import re
import sys
import time
from typing import Any
from urllib.parse import urlparse

TOOL_NAME = "text_extract_urls"
VERSION = "1.1.0"

# Neon palette (only used on stderr / human path)
NEON = {
    "c": "\033[38;5;51m",   # cyan
    "m": "\033[38;5;198m",  # neon pink
    "g": "\033[38;5;46m",   # green
    "y": "\033[38;5;226m",  # yellow
    "r": "\033[38;5;196m",  # red
    "w": "\033[1;97m",      # bright white
    "d": "\033[2;37m",      # dim
    "p": "\033[38;5;129m",  # purple
    "0": "\033[0m",
}

# More robust URL pattern (scheme + authority + optional path/query/fragment)
URL_RE = re.compile(
    r"""https?://                          # scheme
        (?:[a-zA-Z0-9\-._\~%]+(?::[a-zA-Z0-9\-._\~%]*)?@)?  # optional userinfo
        (?:[a-zA-Z0-9\-]+\.)+[a-zA-Z]{2,}  # domain
        (?::\d{1,5})?                      # optional port
        (?:/[^\s<>"'{}|\\^`\[\]]*)?        # optional path
        (?:\?[^\s<>"'{}|\\^`\[\]]*)?       # optional query
        (?:\#[^\s<>"'{}|\\^`\[\]]*)?       # optional fragment
    """,
    re.VERBOSE | re.IGNORECASE,
)


def _c(text: str, color: str = "c") -> str:
    if not sys.stderr.isatty() or os.environ.get("NO_COLOR"):
        return text
    return f"{NEON.get(color, '')}{text}{NEON['0']}"


def pretty_box(
    name: str,
    version: str,
    success: bool,
    summary: str,
    rows: list[tuple[str, str]] | None = None,
    extra_lines: list[str] | None = None,
) -> None:
    """Rich neon boxed card → stderr only. Never pollutes machine JSON."""
    width = min(max(60, 72), 100)
    glyph, status, sc = ("✓", "SUCCESS", "g") if success else ("✗", "FAILED", "r")
    top = "╭" + "─" * (width - 2) + "╮"
    mid = "├" + "─" * (width - 2) + "┤"
    bot = "╰" + "─" * (width - 2) + "╯"

    print(_c(top, "p"), file=sys.stderr)
    header = (
        f"│ {_c('⚡', 'c')} [{_c(name, 'c')} {_c('v' + version, 'd')}] "
        f"{_c(glyph, sc)} {_c(status, sc)} › {_c(summary, 'w')}"
    )
    print(header, file=sys.stderr)
    print(_c(mid, "p"), file=sys.stderr)

    if rows:
        for k, v in rows:
            label = _c(f"{k}:", "m")
            # crude padding that still looks decent with ANSI
            print(f"│ {label} {_c(str(v), 'w')}", file=sys.stderr)

    if extra_lines:
        print(_c(mid, "p"), file=sys.stderr)
        for line in extra_lines[:12]:  # hard cap
            print(f"│ {_c(line, 'd')}", file=sys.stderr)
        if len(extra_lines) > 12:
            print(f"│ {_c(f'… {len(extra_lines) - 12} more', 'd')}", file=sys.stderr)

    print(_c(bot, "p"), file=sys.stderr)


def run(
    text: str,
    limit: int = 500,
) -> dict[str, Any]:
    """Extract all unique URLs from text.

    Args:
        text: The raw text to scan for URLs.
        limit: Maximum number of unique URLs to return (1-5000).
    """
    # ---------- validation ----------
    if not isinstance(text, str) or not text.strip():
        return {
            "success": False,
            "error": {
                "code": "INVALID_INPUT",
                "message": "text is required and must be non-empty",
            },
            "data": None,
            "warnings": [],
        }

    if not isinstance(limit, int) or not (1 <= limit <= 5000):
        return {
            "success": False,
            "error": {
                "code": "INVALID_INPUT",
                "message": "limit must be an integer between 1 and 5000",
            },
            "data": None,
            "warnings": [],
        }

    # ---------- core logic ----------
    found = URL_RE.findall(text)
    # normalize + dedupe while preserving order
    seen: set[str] = set()
    unique: list[str] = []
    warnings: list[str] = []

    for raw in found:
        # light cleanup
        url = raw.rstrip(".,;:!?)\"'")
        if not url:
            continue
        # basic sanity
        try:
            parsed = urlparse(url)
            if not parsed.scheme or not parsed.netloc:
                continue
        except Exception:
            continue

        if url not in seen:
            seen.add(url)
            unique.append(url)
            if len(unique) >= limit:
                warnings.append(f"truncated at limit={limit}")
                break

    return {
        "success": True,
        "data": {
            "version": VERSION,
            "count": len(unique),
            "limit": limit,
            "urls": unique,
            "truncated": len(unique) >= limit and len(found) > limit,
        },
        "warnings": warnings,
        "error": None,
    }


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(
        description="Extract unique URLs from text (AIChat / llm-functions tool)"
    )
    parser.add_argument("--text", required=True, help="Text to scan")
    parser.add_argument(
        "--limit", type=int, default=500, help="Max unique URLs (1-5000)"
    )
    parser.add_argument(
        "--pretty",
        action="store_true",
        help="Force rich neon boxed output on stderr",
    )
    args = parser.parse_args()

    t0 = time.perf_counter()
    result = run(text=args.text, limit=args.limit)
    elapsed_ms = (time.perf_counter() - t0) * 1000

    # Machine path — pure JSON, zero ANSI
    print(json.dumps(result, ensure_ascii=False, indent=None))

    # Human path — neon box on stderr only
    if args.pretty or (sys.stderr.isatty() and not os.environ.get("NO_COLOR")):
        data = result.get("data") or {}
        urls = data.get("urls") or []
        extra = urls[:8] if urls else ["(no urls found)"]
        pretty_box(
            TOOL_NAME,
            VERSION,
            bool(result.get("success")),
            f"extracted {data.get('count', 0)} unique URL(s)"
            if result.get("success")
            else "failed",
            rows=[
                ("Count", str(data.get("count", 0))),
                ("Limit", str(data.get("limit", ""))),
                ("Truncated", str(data.get("truncated", False))),
                ("Duration", f"{elapsed_ms:.1f}ms"),
            ],
            extra_lines=extra,
        )