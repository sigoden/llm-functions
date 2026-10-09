#!/usr/bin/env python3
# ==============================================================================
# memory_manager.py — Pyrmethus AIChat Tool Template v1.1.0
# argc/aichat compatible · Human-Readable Colorized Outputs
#
# @describe Manage persistent memory for AIChat conversations and context (store, retrieve, search, clear, export, import, cleanup).
#
# @option --action! <ACTION>             Action to perform: store, retrieve, search, clear, export, import, cleanup (required)
# @option --key <KEY>                    Memory key or identifier
# @option --value <VALUE>                Value to store, query string, or import file path
# @option --type <TYPE>                  Memory type: conversation, preference, context, knowledge (default: context)
# @option --session <SESSION>            Session identifier (default: default)
# @option --tags <TAGS>                  Comma-separated tags for categorization
# @option --days <DAYS>                  Retention period in days for cleanup (default: 30)
# @flag   --no-color                     Disable ANSI color output
# @flag   --verbose                      Enable detailed debug log output
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
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Optional

__version__ = "1.1.0"

# ==============================================================================
# SECTION 1: Color Palette & Formatting Helpers
# ==============================================================================

NEON_CYAN = "\033[38;5;51m"
NEON_GREEN = "\033[38;5;46m"
NEON_RED = "\033[38;5;196m"
NEON_YELLOW = "\033[38;5;226m"
NEON_PURPLE = "\033[38;5;129m"
NEON_PINK = "\033[38;5;198m"
RESET = "\033[0m"
BOLD = "\033[1m"
DIM = "\033[2m"

_ANSI_RE = re.compile(r"\033\[[0-9;]*[mGKHF]")


def _strip_ansi(text: str) -> str:
    """Remove all ANSI escape sequences from text."""
    return _ANSI_RE.sub("", text)


def _is_tty() -> bool:
    """Return True if stdout is attached to an interactive terminal."""
    return sys.stdout.isatty()


def _cprint(text: str, file: Any = None, no_color: bool = False) -> None:
    """Print pre-formatted ANSI text, stripping colors if stdout is not a TTY or --no-color is set."""
    target = file or sys.stdout
    if no_color or not _is_tty():
        text = _strip_ansi(text)
    print(text, file=target, flush=True)


def print_human_readable_ui(data: dict[str, Any], no_color: bool = False) -> None:
    """
    Render a human-friendly, colorized box UI for terminal users.
    Only executes if running in an interactive TTY.
    """
    if not _is_tty() or no_color:
        return

    success = data.get("success", False)
    status_color = NEON_GREEN if success else NEON_RED
    status_symbol = "✓" if success else "✗"
    status_text = "SUCCESS" if success else "FAILED"

    box_w = 64
    border = "─" * box_w

    _cprint(f"{NEON_PURPLE}╭{border}╮{RESET}")
    _cprint(
        f"{NEON_PURPLE}│{RESET} {NEON_PINK}⚡ [PERSISTENT MEMORY MANAGER]{RESET} {status_color}{BOLD}{status_symbol} {status_text}{RESET}"
    )
    _cprint(f"{NEON_PURPLE}├{border}┤{RESET}")
    _cprint(
        f"{NEON_PURPLE}│{RESET} {NEON_CYAN}Action:{RESET}       {NEON_YELLOW}{data.get('action', 'N/A')}{RESET}"
    )
    _cprint(
        f"{NEON_PURPLE}│{RESET} {NEON_CYAN}Type:{RESET}         {data.get('type', 'context')}"
    )
    _cprint(
        f"{NEON_PURPLE}│{RESET} {NEON_CYAN}Session:{RESET}      {data.get('session', 'default')}"
    )
    _cprint(
        f"{NEON_PURPLE}│{RESET} {NEON_CYAN}Count:{RESET}        {NEON_GREEN}{data.get('count', 0):,}{RESET}"
    )
    _cprint(
        f"{NEON_PURPLE}│{RESET} {NEON_CYAN}Duration:{RESET}     {DIM}{data.get('duration_ms', 0)}ms{RESET}"
    )

    if not success and "error" in data:
        _cprint(f"{NEON_PURPLE}├{border}┤{RESET}")
        _cprint(f"{NEON_PURPLE}│{RESET} {NEON_RED}Error:{RESET}        {data['error']}")

    results = data.get("results", [])
    if results:
        _cprint(f"{NEON_PURPLE}├{border}┤{RESET}")
        _cprint(f"{NEON_PURPLE}│{RESET} {BOLD}Memory Entries ({len(results)}):{RESET}")
        for item in results[:5]:
            k = item.get("key", "N/A")
            v = str(item.get("value", ""))[:45]
            _cprint(
                f"{NEON_PURPLE}│{RESET}   {NEON_CYAN}›{RESET} {BOLD}{k}{RESET}: {DIM}{v}...{RESET}"
                if len(str(item.get("value", ""))) > 45
                else f"{NEON_PURPLE}│{RESET}   {NEON_CYAN}›{RESET} {BOLD}{k}{RESET}: {v}"
            )

    _cprint(f"{NEON_PURPLE}╰{border}╯{RESET}")


# ==============================================================================
# SECTION 2: Core Logic Implementation
# ==============================================================================


def execute_tool(
    action: str,
    key: Optional[str] = None,
    value: Optional[str] = None,
    type: str = "context",
    session: str = "default",
    tags: Optional[str] = None,
    days: int = 30,
    no_color: bool = False,
    verbose: bool = False,
) -> dict[str, Any]:
    """
    Core memory management execution logic.
    """
    start_time = time.perf_counter()
    action_clean = action.lower().strip()

    root_dir = Path(os.environ.get("LLM_ROOT_DIR", os.getcwd())).resolve()
    memory_dir = root_dir / "memory"
    memory_dir.mkdir(parents=True, exist_ok=True)

    type_clean = (type or "context").lower().strip()
    session_clean = (session or "default").strip()
    memory_file = memory_dir / f"{type_clean}.jsonl"

    tag_list = [t.strip() for t in tags.split(",") if t.strip()] if tags else []

    try:
        results: list[dict[str, Any]] = []

        # ----------------------------------------------------------------------
        # ACTION 1: STORE
        # ----------------------------------------------------------------------
        if action_clean == "store":
            if not key:
                return {
                    "success": False,
                    "error": "Missing required argument: --key",
                    "exit_code": 1,
                }
            if not value:
                return {
                    "success": False,
                    "error": "Missing required argument: --value",
                    "exit_code": 1,
                }

            entry = {
                "key": key.strip(),
                "value": value.strip(),
                "type": type_clean,
                "session": session_clean,
                "tags": tag_list,
                "timestamp": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            }

            with open(memory_file, "a", encoding="utf-8") as f:
                f.write(json.dumps(entry, ensure_ascii=False) + "\n")

            results.append(entry)

        # ----------------------------------------------------------------------
        # ACTION 2: RETRIEVE
        # ----------------------------------------------------------------------
        elif action_clean == "retrieve":
            if not key:
                return {
                    "success": False,
                    "error": "Missing required argument: --key",
                    "exit_code": 1,
                }
            if not memory_file.exists():
                return {
                    "success": False,
                    "error": f"No memory store found for type '{type_clean}'",
                    "exit_code": 1,
                }

            with open(memory_file, encoding="utf-8") as f:
                for line in f:
                    if not line.strip():
                        continue
                    item = json.loads(line)
                    if item.get("key") == key.strip():
                        results.append(item)

        # ----------------------------------------------------------------------
        # ACTION 3: SEARCH
        # ----------------------------------------------------------------------
        elif action_clean == "search":
            query = (value or key or "").lower().strip()
            if not query:
                return {
                    "success": False,
                    "error": "Search query required in --value or --key",
                    "exit_code": 1,
                }
            if not memory_file.exists():
                return {
                    "success": False,
                    "error": f"No memory store found for type '{type_clean}'",
                    "exit_code": 1,
                }

            with open(memory_file, encoding="utf-8") as f:
                for line in f:
                    if not line.strip():
                        continue
                    item = json.loads(line)
                    item_val = str(item.get("value", "")).lower()
                    item_key = str(item.get("key", "")).lower()
                    item_tags = [str(t).lower() for t in item.get("tags", [])]

                    if (
                        query in item_val
                        or query in item_key
                        or any(query in t for t in item_tags)
                    ):
                        results.append(item)

        # ----------------------------------------------------------------------
        # ACTION 4: CLEAR
        # ----------------------------------------------------------------------
        elif action_clean == "clear":
            if memory_file.exists():
                memory_file.write_text("", encoding="utf-8")

        # ----------------------------------------------------------------------
        # ACTION 5: EXPORT
        # ----------------------------------------------------------------------
        elif action_clean == "export":
            if not memory_file.exists():
                return {
                    "success": False,
                    "error": f"No memory store found for type '{type_clean}'",
                    "exit_code": 1,
                }

            entries = []
            with open(memory_file, encoding="utf-8") as f:
                for line in f:
                    if line.strip():
                        entries.append(json.loads(line))

            export_filename = (
                f"{type_clean}_export_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
            )
            export_path = memory_dir / export_filename
            export_path.write_text(
                json.dumps(entries, indent=2, ensure_ascii=False), encoding="utf-8"
            )
            results.append(
                {"export_path": str(export_path), "exported_count": len(entries)}
            )

        # ----------------------------------------------------------------------
        # ACTION 6: IMPORT
        # ----------------------------------------------------------------------
        elif action_clean == "import":
            if not value:
                return {
                    "success": False,
                    "error": "Missing import file path in --value",
                    "exit_code": 1,
                }

            import_path = Path(value).expanduser().resolve()
            if not import_path.exists():
                return {
                    "success": False,
                    "error": f"Import file not found: {import_path}",
                    "exit_code": 1,
                }

            raw_text = import_path.read_text(encoding="utf-8")
            imported_entries = []

            # Handle JSON array or JSONL
            try:
                data = json.loads(raw_text)
                if isinstance(data, list):
                    imported_entries = data
                elif isinstance(data, dict):
                    imported_entries = [data]
            except json.JSONDecodeError:
                for line in raw_text.splitlines():
                    if line.strip():
                        imported_entries.append(json.loads(line))

            with open(memory_file, "a", encoding="utf-8") as f:
                for entry in imported_entries:
                    f.write(json.dumps(entry, ensure_ascii=False) + "\n")

            results = imported_entries

        # ----------------------------------------------------------------------
        # ACTION 7: CLEANUP
        # ----------------------------------------------------------------------
        elif action_clean == "cleanup":
            retention_days = int(days)
            cutoff = datetime.now(timezone.utc) - timedelta(days=retention_days)
            retained_entries = []

            for jsonl_file in memory_dir.glob("*.jsonl"):
                retained_local = []
                with open(jsonl_file, encoding="utf-8") as f:
                    for line in f:
                        if not line.strip():
                            continue
                        item = json.loads(line)
                        ts_str = item.get("timestamp")
                        if ts_str:
                            try:
                                item_dt = datetime.fromisoformat(
                                    ts_str.replace("Z", "+00:00")
                                )
                                if item_dt >= cutoff:
                                    retained_local.append(item)
                            except ValueError:
                                retained_local.append(item)
                        else:
                            retained_local.append(item)

                with open(jsonl_file, "w", encoding="utf-8") as f:
                    for item in retained_local:
                        f.write(json.dumps(item, ensure_ascii=False) + "\n")

                retained_entries.extend(retained_local)

            results = retained_entries

        else:
            return {
                "success": False,
                "error": f"Unknown action: '{action}'",
                "exit_code": 1,
            }

        duration_ms = round((time.perf_counter() - start_time) * 1000, 2)

        return {
            "success": True,
            "action": action_clean,
            "type": type_clean,
            "session": session_clean,
            "count": len(results),
            "results": results,
            "duration_ms": duration_ms,
            "exit_code": 0,
        }

    except Exception as exc:
        return {
            "success": False,
            "error": f"Memory management failed: {exc}",
            "exit_code": 1,
        }


# ==============================================================================
# SECTION 3: Output Routing (LLM vs Human Terminal)
# ==============================================================================


def write_llm_output(data: dict[str, Any]) -> None:
    """Format and write clean JSON output to LLM_OUTPUT destination."""
    out_path = os.environ.get("LLM_OUTPUT", "/dev/stdout")
    json_payload = json.dumps(data, indent=2, ensure_ascii=False) + "\n"

    direct_targets = {"/dev/stdout", "/dev/fd/1", "-"}
    if out_path in direct_targets:
        sys.stdout.write(json_payload)
        sys.stdout.flush()
    else:
        try:
            Path(out_path).parent.mkdir(parents=True, exist_ok=True)
            with open(out_path, "a", encoding="utf-8") as fp:
                fp.write(json_payload)
        except OSError as err:
            sys.stderr.write(f"Failed writing to LLM_OUTPUT '{out_path}': {err}\n")
            sys.stdout.write(json_payload)
            sys.stdout.flush()


# ==============================================================================
# SECTION 4: Function Entry Point for AIChat
# ==============================================================================


def run(
    action: str,
    key: Optional[str] = None,
    value: Optional[str] = None,
    type: str = "context",
    session: str = "default",
    tags: Optional[str] = None,
    days: int = 30,
    no_color: bool = False,
    verbose: bool = False,
) -> None:
    """
    AIChat Programmatic Entrypoint.
    Parameter names match option/flag slugs (with underscores).
    """
    result = execute_tool(
        action=action,
        key=key,
        value=value,
        type=type,
        session=session,
        tags=tags,
        days=days,
        no_color=no_color,
        verbose=verbose,
    )

    print_human_readable_ui(result, no_color=no_color)
    write_llm_output(result)


# ==============================================================================
# SECTION 5: CLI Argument Parser
# ==============================================================================


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="memory_manager.py",
        description=f"AIChat Persistent Memory Manager v{__version__}",
    )
    parser.add_argument(
        "--action",
        "-a",
        required=True,
        choices=["store", "retrieve", "search", "clear", "export", "import", "cleanup"],
        help="Action to perform (required)",
    )
    parser.add_argument(
        "--key",
        "-k",
        type=str,
        default=None,
        help="Memory key or identifier",
    )
    parser.add_argument(
        "--value",
        "-v",
        type=str,
        default=None,
        help="Value to store, query string, or import file path",
    )
    parser.add_argument(
        "--type",
        "-t",
        default="context",
        choices=["conversation", "preference", "context", "knowledge"],
        help="Memory type (default: context)",
    )
    parser.add_argument(
        "--session",
        "-s",
        default="default",
        help="Session identifier (default: default)",
    )
    parser.add_argument(
        "--tags",
        type=str,
        default=None,
        help="Comma-separated tags for categorization",
    )
    parser.add_argument(
        "--days",
        type=int,
        default=30,
        help="Retention period in days for cleanup (default: 30)",
    )
    parser.add_argument(
        "--no-color",
        action="store_true",
        default=False,
        dest="no_color",
        help="Disable ANSI color output",
    )
    parser.add_argument(
        "--verbose",
        action="store_true",
        default=False,
        help="Enable detailed debug logging",
    )
    return parser


if __name__ == "__main__":
    args = _build_parser().parse_args()
    res = execute_tool(
        action=args.action,
        key=args.key,
        value=args.value,
        type=args.type,
        session=args.session,
        tags=args.tags,
        days=args.days,
        no_color=args.no_color,
        verbose=args.verbose,
    )

    print_human_readable_ui(res, no_color=args.no_color)
    write_llm_output(res)
    sys.exit(res.get("exit_code", 0))
