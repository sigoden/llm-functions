#!/usr/bin/env python3
# ==============================================================================
# pdf_intel_tool.py — Pyrmethus AIChat Tool: PDF Reader & Inspector v2.7.0
# argc/aichat compatible · Human-Readable Box UI · Metadata & Text Extraction
#
# @describe Extracts page text, metadata, table previews, and structural information from PDF documents.
#
# @meta require-tools aichat
#
# @option --target! <PATH>                Target PDF file path (required)
# @option --pages <RANGE>                 Specific page numbers or ranges (e.g. 1-5, 8, 10)
# @option --max-characters <NUM>          Maximum characters to extract per page (default: 4000)
# @flag   --metadata-only                 Only inspect document metadata without extracting body text
# @flag   --no-color                      Disable ANSI color output
# @flag   --verbose                       Enable detailed debug logging
#
# @env LLM_OUTPUT=/dev/stdout            Output path for LLM integration
# ==============================================================================

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Set

try:
    import pypdf
except ImportError:
    print(
        "\033[31mError: Missing dependency 'pypdf'. Please run: pip install pypdf\033[0m",
        file=sys.stderr,
    )
    sys.exit(127)

__version__ = "2.7.0"

EXIT_SUCCESS = 0
EXIT_ERROR = 1
EXIT_FILE_NOT_FOUND = 2


class ToolJSONEncoder(json.JSONEncoder):
    def default(self, obj: Any) -> Any:
        if isinstance(obj, Path):
            return str(obj)
        if isinstance(obj, datetime):
            return obj.isoformat()
        return super().default(obj)


NEON_CYAN = "\033[38;5;51m"
NEON_GREEN = "\033[38;5;46m"
NEON_RED = "\033[38;5;196m"
NEON_YELLOW = "\033[38;5;226m"
NEON_PURPLE = "\033[38;5;129m"
NEON_PINK = "\033[38;5;198m"
RESET = "\033[0m"
BOLD = "\033[1m"
DIM = "\033[2m"

_ANSI_RE = re.compile(r"\033\[[0-9;]*[a-zA-Z]")


def _strip_ansi(text: str) -> str:
    return _ANSI_RE.sub("", text)


def _is_tty() -> bool:
    return sys.stderr.isatty() and os.environ.get("TERM", "").lower() not in (
        "dumb",
        "",
    )


def _cprint(
    text: str, file: Any = None, no_color: bool = False, end: str = "\n"
) -> None:
    target = file or sys.stderr
    if no_color or not _is_tty():
        text = _strip_ansi(text)
    print(text, file=target, flush=True, end=end)


def _human_bytes(num: int) -> str:
    for unit in ["B", "KB", "MB", "GB"]:
        if abs(num) < 1024.0:
            return f"{num:.1f}{unit}"
        num /= 1024.0
    return f"{num:.1f}TB"


def _parse_page_range(range_str: str, max_pages: int) -> Set[int]:
    """Parse string range like '1-5,8,10-12' into zero-indexed page numbers."""
    selected_pages: Set[int] = set()
    for part in range_str.split(","):
        part = part.strip()
        if "-" in part:
            try:
                start_s, end_s = part.split("-", 1)
                start = max(1, int(start_s))
                end = min(max_pages, int(end_s))
                for p in range(start, end + 1):
                    selected_pages.add(p - 1)
            except ValueError:
                continue
        elif part.isdigit():
            p_num = int(part)
            if 1 <= p_num <= max_pages:
                selected_pages.add(p_num - 1)
    return selected_pages


def print_human_readable_ui(data: dict[str, Any], no_color: bool = False) -> None:
    if not _is_tty() or no_color:
        return

    success = data.get("success", False)
    status_color = NEON_GREEN if success else NEON_RED
    status_symbol = "✓" if success else "✗"
    box_w = 64
    border = "─" * box_w

    _cprint(f"{NEON_PURPLE}╭{border}╮{RESET}")
    _cprint(
        f"{NEON_PURPLE}│{RESET} {NEON_PINK}⚡ [PDF DOCUMENT READER v{__version__}]{RESET} "
        f"{status_color}{BOLD}{status_symbol} {'SUCCESS' if success else 'FAILED'}{RESET}"
    )
    _cprint(f"{NEON_PURPLE}├{border}┤{RESET}")
    _cprint(
        f"{NEON_PURPLE}│{RESET} {NEON_CYAN}File Path:{RESET}    {data.get('file_path', 'N/A')}"
    )
    _cprint(
        f"{NEON_PURPLE}│{RESET} {NEON_CYAN}Total Pages:{RESET}  {NEON_YELLOW}{data.get('total_pages', 0)}{RESET}"
    )
    _cprint(
        f"{NEON_PURPLE}│{RESET} {NEON_CYAN}File Size:{RESET}    {data.get('file_size_fmt', '0B')}"
    )
    _cprint(
        f"{NEON_PURPLE}│{RESET} {NEON_CYAN}Duration:{RESET}     {DIM}{data.get('duration_ms', 0)}ms{RESET}"
    )

    pages = data.get("pages", [])
    if pages:
        _cprint(f"{NEON_PURPLE}├{border}┤{RESET}")
        _cprint(f"{NEON_PURPLE}│{RESET} {BOLD}Extracted Pages ({len(pages)}):{RESET}")
        for p in pages[:3]:
            preview = p.get("snippet", "").replace("\n", " ")[:50]
            _cprint(
                f"{NEON_PURPLE}│{RESET}   {NEON_CYAN}›{RESET} Page {NEON_GREEN}{p['page_number']}{RESET} ({p['word_count']} words): {DIM}{preview}...{RESET}"
            )

    _cprint(f"{NEON_PURPLE}╰{border}╯{RESET}")


def execute_tool(
    target: str,
    pages: Optional[str] = None,
    max_characters: int = 4000,
    metadata_only: bool = False,
    no_color: bool = False,
    verbose: bool = False,
) -> dict[str, Any]:
    start_time = time.monotonic()
    target_path = Path(target).expanduser().resolve()

    if not target_path.exists():
        return {
            "success": False,
            "error": f"Target PDF file does not exist: {target}",
            "exit_code": EXIT_FILE_NOT_FOUND,
            "duration_ms": 0.0,
        }

    try:
        reader = pypdf.PdfReader(str(target_path))
        total_pages = len(reader.pages)

        raw_meta = reader.metadata or {}
        doc_meta = {
            "title": raw_meta.title or "Unknown",
            "author": raw_meta.author or "Unknown",
            "creator": raw_meta.creator or "Unknown",
            "producer": raw_meta.producer or "Unknown",
        }

        if metadata_only:
            return {
                "success": True,
                "file_path": str(target_path),
                "file_size_fmt": _human_bytes(target_path.stat().st_size),
                "total_pages": total_pages,
                "metadata": doc_meta,
                "duration_ms": round((time.monotonic() - start_time) * 1000, 2),
                "exit_code": EXIT_SUCCESS,
            }

        target_indexes = set(range(total_pages))
        if pages:
            parsed_indexes = _parse_page_range(pages, total_pages)
            if parsed_indexes:
                target_indexes = parsed_indexes

        extracted_pages: List[Dict[str, Any]] = []
        for idx in sorted(target_indexes):
            page = reader.pages[idx]
            raw_text = page.extract_text() or ""
            clean_text = re.sub(r"[ \t]+", " ", raw_text).strip()
            truncated_text = clean_text[:max_characters]

            extracted_pages.append(
                {
                    "page_number": idx + 1,
                    "character_count": len(clean_text),
                    "word_count": len(clean_text.split()),
                    "snippet": truncated_text[:200],
                    "text": truncated_text,
                }
            )

        return {
            "success": True,
            "file_path": str(target_path),
            "file_size_fmt": _human_bytes(target_path.stat().st_size),
            "total_pages": total_pages,
            "extracted_pages_count": len(extracted_pages),
            "metadata": doc_meta,
            "pages": extracted_pages,
            "duration_ms": round((time.monotonic() - start_time) * 1000, 2),
            "exit_code": EXIT_SUCCESS,
        }

    except Exception as exc:
        return {
            "success": False,
            "error": f"Failed reading PDF document: {exc}",
            "exit_code": EXIT_ERROR,
            "duration_ms": round((time.monotonic() - start_time) * 1000, 2),
        }


def write_llm_output(data: dict[str, Any]) -> None:
    out_path = os.environ.get("LLM_OUTPUT", "/dev/stdout")
    json_payload = (
        json.dumps(data, indent=2, ensure_ascii=False, cls=ToolJSONEncoder) + "\n"
    )
    if out_path in {"/dev/stdout", "/dev/fd/1", "-"}:
        sys.stdout.write(json_payload)
        sys.stdout.flush()
    else:
        try:
            Path(out_path).parent.mkdir(parents=True, exist_ok=True)
            with open(out_path, "a", encoding="utf-8") as fp:
                fp.write(json_payload)
        except OSError:
            sys.stdout.write(json_payload)


def run(
    target: str,
    pages: Optional[str] = None,
    max_characters: int = 4000,
    metadata_only: bool = False,
    no_color: bool = False,
    verbose: bool = False,
) -> None:
    res = execute_tool(
        target=target,
        pages=pages,
        max_characters=max_characters,
        metadata_only=metadata_only,
        no_color=no_color,
        verbose=verbose,
    )
    print_human_readable_ui(res, no_color=no_color)
    write_llm_output(res)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Pyrmethus PDF Inspector Tool")
    parser.add_argument("--target", "-t", required=True, dest="target")
    parser.add_argument("--pages", dest="pages")
    parser.add_argument(
        "--max-characters", type=int, default=4000, dest="max_characters"
    )
    parser.add_argument("--metadata-only", action="store_true", dest="metadata_only")
    parser.add_argument("--no-color", action="store_true", dest="no_color")
    parser.add_argument("--verbose", "-v", action="store_true", dest="verbose")

    args = parser.parse_args()
    res = execute_tool(
        target=args.target,
        pages=args.pages,
        max_characters=args.max_characters,
        metadata_only=args.metadata_only,
        no_color=args.no_color,
        verbose=args.verbose,
    )
    print_human_readable_ui(res, no_color=args.no_color)
    write_llm_output(res)
    sys.exit(res.get("exit_code", EXIT_SUCCESS))
