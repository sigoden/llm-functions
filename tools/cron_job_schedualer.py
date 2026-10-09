#!/usr/bin/env python3
# ==============================================================================
# cron_scheduler_tool.py — Pyrmethus AIChat Tool v2.2.0-ASCENDED
# argc/aichat compatible · Human-Readable Colorized Outputs · Native Caching
#
# @describe Manages background cron jobs and scheduled tasks within Termux, allowing inspecting active jobs, setting new timers, and auditing execution logs.
#
# @meta require-tools aichat
#
# @option --target! <PATH>                Target file or directory path (default: ~/)
# @option --action <ACTION>               Action to perform: list/add/remove/audit (default: list)
# @option --schedule <CRON_EXPR>          Standard 5-part cron schedule expression
# @option --command <CMD>                 Shell command to execute or search
# @option --log-limit <NUM>               Maximum log entries to audit (default: 50)
# @option --mode <MODE>                   Execution mode: summary/detailed (default: summary)
# @option --limit <NUM>                   Maximum items to process (default: 100)
# @option --file-pattern <PATTERN>        File glob pattern filter (e.g., *.log)
# @option --env-var <KEY=VALUE>          Custom environment variable (repeatable)
# @flag   --recursive                     Process directories recursively
# @flag   --use-cache                     Enable result caching for expensive operations
# @flag   --no-color                      Disable ANSI color output
# @flag   --verbose                       Enable detailed debug log output
#
# @env LLM_OUTPUT=/dev/stdout            Output path for LLM integration
# ==============================================================================

from __future__ import annotations

import argparse
import hashlib
import json
import logging
import os
import pickle
import re
import signal
import subprocess
import sys
import time
from datetime import datetime, timedelta
from enum import Enum
from pathlib import Path
from typing import Any, List, Literal, Optional, Tuple

__version__ = "2.2.0"
__all__ = [
    "ToolCache",
    "ToolError",
    "__version__",
    "execute_tool",
    "get_agent_var",
    "get_builtin_var",
    "get_execution_context",
    "run",
]

# ==============================================================================
# SECTION 1: Exit Codes & Exception Models
# ==============================================================================

EXIT_SUCCESS = 0
EXIT_ERROR = 1
EXIT_FILE_NOT_FOUND = 2
EXIT_TIMEOUT = 124
EXIT_PERMISSION_DENIED = 126
EXIT_INVALID_INPUT = 127
EXIT_INTERRUPTED = 130


class ExecutionMode(str, Enum):
    SUMMARY = "summary"
    DETAILED = "detailed"


class CronAction(str, Enum):
    LIST = "list"
    ADD = "add"
    REMOVE = "remove"
    AUDIT = "audit"


class ToolError(Exception):
    """Structured exception model for tool operations."""

    def __init__(
        self,
        message: str,
        exit_code: int = EXIT_ERROR,
        details: Optional[dict[str, Any]] = None,
    ) -> None:
        super().__init__(message)
        self.message = message
        self.exit_code = exit_code
        self.details = details or {}

    def to_dict(self) -> dict[str, Any]:
        return {
            "success": False,
            "error": self.message,
            "exit_code": self.exit_code,
            **self.details,
        }


class ToolJSONEncoder(json.JSONEncoder):
    """Custom JSON encoder handling Path, Enum, datetime, timedelta, bytes, and sets safely."""

    def default(self, obj: Any) -> Any:
        if isinstance(obj, Path):
            return str(obj)
        if isinstance(obj, Enum):
            return obj.value
        if isinstance(obj, datetime):
            return obj.isoformat()
        if isinstance(obj, timedelta):
            return obj.total_seconds()
        if isinstance(obj, bytes):
            return obj.decode("utf-8", errors="replace")
        if isinstance(obj, (set, frozenset)):
            return list(obj)
        return super().default(obj)


# ==============================================================================
# SECTION 2: Terminal Color Palette & UI Helpers
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

# Advanced ANSI escape sequence stripping regex
_ANSI_RE = re.compile(r"\x1b(?:[@-Z\\-_]|\[[0-?]*[ -/]*[@-~])|\033\[[0-9;?]*[a-zA-Z]")


def _strip_ansi(text: str) -> str:
    """Remove all ANSI escape sequences from text."""
    return _ANSI_RE.sub("", text)


def _is_tty() -> bool:
    """Return True if stderr is attached to an interactive, non-dumb terminal."""
    return sys.stderr.isatty() and os.environ.get("TERM", "").lower() not in (
        "dumb",
        "",
    )


def _cprint(
    text: str, file: Any = None, no_color: bool = False, end: str = "\n"
) -> None:
    """Print pre-formatted ANSI text, stripping colors if stream is not a TTY or --no-color is set."""
    target = file or sys.stderr
    if no_color or not _is_tty():
        text = _strip_ansi(text)
    print(text, file=target, flush=True, end=end)


def print_progress(
    current: int, total: int, message: str = "", no_color: bool = False
) -> None:
    """Render a visual progress bar for long-running batch operations."""
    if not _is_tty() or no_color:
        return
    percent = (current / total) * 100.0 if total > 0 else 100.0
    bar_width = 30
    filled = int(bar_width * percent / 100.0)
    bar = "█" * filled + "░" * (bar_width - filled)

    _cprint(
        f"\r{NEON_CYAN}Progress:{RESET} [{NEON_GREEN}{bar}{RESET}] {percent:.1f}% {message}",
        end="",
        no_color=no_color,
    )
    if current >= total:
        _cprint("", no_color=no_color)


def print_human_readable_ui(data: dict[str, Any], no_color: bool = False) -> None:
    """
    Render a human-friendly, colorized box UI for terminal users to stderr.
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
        f"{NEON_PURPLE}│{RESET} {NEON_PINK}⚡ [CRON SCHEDULER v{__version__}]{RESET} {status_color}{BOLD}{status_symbol} {status_text}{RESET}"
    )
    _cprint(f"{NEON_PURPLE}├{border}┤{RESET}")
    _cprint(
        f"{NEON_PURPLE}│{RESET} {NEON_CYAN}Action:{RESET}    {NEON_YELLOW}{data.get('action', 'N/A')}{RESET}"
    )
    _cprint(
        f"{NEON_PURPLE}│{RESET} {NEON_CYAN}Target:{RESET}    {data.get('target', 'N/A')}"
    )
    _cprint(
        f"{NEON_PURPLE}│{RESET} {NEON_CYAN}Count:{RESET}     {NEON_YELLOW}{data.get('count', 0)}{RESET}"
    )
    _cprint(
        f"{NEON_PURPLE}│{RESET} {NEON_CYAN}Cached:{RESET}    {NEON_YELLOW}{data.get('cached', False)}{RESET}"
    )
    _cprint(
        f"{NEON_PURPLE}│{RESET} {NEON_CYAN}Duration:{RESET}  {DIM}{data.get('duration_ms', 0)}ms{RESET}"
    )

    if not success and "error" in data:
        _cprint(f"{NEON_PURPLE}├{border}┤{RESET}")
        _cprint(f"{NEON_PURPLE}│{RESET} {NEON_RED}Error:{RESET}     {data['error']}")

    items = data.get("items", [])
    if items:
        _cprint(f"{NEON_PURPLE}├{border}┤{RESET}")
        _cprint(
            f"{NEON_PURPLE}│{RESET} {BOLD}Cron Records / Entries ({len(items)}):{RESET}"
        )
        for item in items[:10]:
            _cprint(f"{NEON_PURPLE}│{RESET}    {NEON_CYAN}›{RESET} {item}")
        if len(items) > 10:
            _cprint(
                f"{NEON_PURPLE}│{RESET}    {DIM}... and {len(items) - 10} more items{RESET}"
            )

    _cprint(f"{NEON_PURPLE}╰{border}╯{RESET}")


# ==============================================================================
# SECTION 3: Agent & Environment Helpers
# ==============================================================================


def get_agent_var(name: str, default: str = "") -> str:
    """Access agent user-defined variables (LLM_AGENT_VAR_<NAME>)."""
    env_name = f"LLM_AGENT_VAR_{name.upper()}"
    return os.environ.get(env_name, default)


def get_builtin_var(name: str) -> Optional[str]:
    """Access agent built-in environment variables (e.g., __cwd__, __os__)."""
    env_name = f"LLM_AGENT_VAR_{name}"
    return os.environ.get(env_name)


def get_execution_context() -> dict[str, Any]:
    """Extract complete execution context from the llm-functions and Termux environment."""
    termux_prefix = os.environ.get("PREFIX", "")
    return {
        "tool_name": os.environ.get("LLM_TOOL_NAME"),
        "cache_dir": os.environ.get("LLM_TOOL_CACHE_DIR"),
        "root_dir": os.environ.get("LLM_ROOT_DIR"),
        "output_path": os.environ.get("LLM_OUTPUT"),
        "cwd": get_builtin_var("__cwd__") or os.getcwd(),
        "termux_prefix": termux_prefix,
        "is_termux": "com.termux" in termux_prefix
        or Path("/data/data/com.termux").exists(),
    }


def _parse_env_vars(env_vars: Optional[list[str]]) -> dict[str, str]:
    """Parse environment variables provided in KEY=VALUE format."""
    if not env_vars:
        return {}
    parsed: dict[str, str] = {}
    for item in env_vars:
        if "=" in item:
            key, val = item.split("=", 1)
            parsed[key.strip()] = val.strip()
    return parsed


# ==============================================================================
# SECTION 4: Native Caching & Signal Handlers
# ==============================================================================


class ToolCache:
    """Caching utility with TTL support for expensive operations."""

    def __init__(self, cache_dir: Optional[Path] = None) -> None:
        if cache_dir:
            self.cache_dir = cache_dir
        elif "LLM_TOOL_CACHE_DIR" in os.environ:
            self.cache_dir = Path(os.environ["LLM_TOOL_CACHE_DIR"])
        else:
            self.cache_dir = Path.home() / ".cache" / "aichat_tools"

        try:
            self.cache_dir.mkdir(parents=True, exist_ok=True)
        except OSError:
            pass

    def _make_key(self, key_data: str) -> str:
        return hashlib.sha256(key_data.encode("utf-8")).hexdigest()

    def get(self, key_data: str, ttl_seconds: int = 3600) -> Optional[Any]:
        cache_file = self.cache_dir / f"{self._make_key(key_data)}.cache"
        if not cache_file.exists():
            return None
        try:
            mtime = cache_file.stat().st_mtime
            if time.time() - mtime > ttl_seconds:
                cache_file.unlink(missing_ok=True)
                return None
            with open(cache_file, "rb") as fp:
                return pickle.load(fp)
        except Exception:
            return None

    def set(self, key_data: str, value: Any) -> None:
        cache_file = self.cache_dir / f"{self._make_key(key_data)}.cache"
        tmp_file = cache_file.with_suffix(".tmp")
        try:
            with open(tmp_file, "wb") as fp:
                pickle.dump(value, fp)
            tmp_file.replace(cache_file)
        except Exception:
            if tmp_file.exists():
                tmp_file.unlink(missing_ok=True)


class GracefulShutdown:
    """Signal handler for graceful cancellation of batch operations."""

    def __init__(self) -> None:
        self.interrupted = False
        self._old_sigint = signal.signal(signal.SIGINT, self._handle_signal)
        self._old_sigterm = signal.signal(signal.SIGTERM, self._handle_signal)

    def _handle_signal(self, signum: int, frame: Any) -> None:
        self.interrupted = True

    def restore(self) -> None:
        """Restore previous signal handlers."""
        signal.signal(signal.SIGINT, self._old_sigint)
        signal.signal(signal.SIGTERM, self._old_sigterm)

    def should_stop(self) -> bool:
        return self.interrupted


# ==============================================================================
# SECTION 5: Core Logic Implementation
# ==============================================================================

CRON_REGEX = re.compile(
    r"^(\*|[0-5]?\d)(/[0-5]?\d)?\s+(\*|[01]?\d|2[0-3])\s+(\*|[012]?\d|3[01])\s+(\*|[01]?\d)\s+(\*|[0-6])"
)


def _get_crontab_entries() -> Tuple[List[str], Optional[str]]:
    """Fetch current user's crontab lines via subprocess."""
    try:
        proc = subprocess.run(
            ["crontab", "-l"],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            timeout=10,
        )
        if proc.returncode != 0:
            stderr = proc.stderr.strip()
            if "no crontab for" in stderr.lower():
                return [], None
            return [], stderr
        lines = [line.strip() for line in proc.stdout.splitlines() if line.strip()]
        return lines, None
    except FileNotFoundError:
        return [], "crontab utility is not installed or available in PATH."
    except subprocess.TimeoutExpired:
        return [], "Timed out querying crontab."


def _set_crontab_entries(lines: List[str]) -> Tuple[bool, Optional[str]]:
    """Write updated crontab lines via standard input to crontab."""
    content = "\n".join(lines) + ("\n" if lines else "")
    try:
        proc = subprocess.run(
            ["crontab", "-"],
            input=content,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            timeout=10,
        )
        if proc.returncode != 0:
            return False, proc.stderr.strip() or "Failed to update crontab."
        return True, None
    except Exception as err:
        return False, str(err)


def execute_tool(
    target: str = "~/",
    action: str = "list",
    schedule: Optional[str] = None,
    command: Optional[str] = None,
    log_limit: int = 50,
    mode: str = "summary",
    limit: Optional[int] = None,
    file_pattern: Optional[str] = None,
    env_vars: Optional[list[str]] = None,
    recursive: bool = False,
    use_cache: bool = False,
    no_color: bool = False,
    verbose: bool = False,
) -> dict[str, Any]:
    """
    Core execution logic shared by run() and CLI parser.
    """
    start_time = time.monotonic()

    if verbose:
        logging.basicConfig(level=logging.DEBUG, format="[DEBUG] %(message)s")
        logging.debug(
            f"Starting cron_scheduler execution with action '{action}' on target: {target}"
        )

    parsed_env = _parse_env_vars(env_vars)
    limit_val = limit if (limit is not None and limit >= 0) else 100
    target_path = Path(target).expanduser().resolve()

    cache = ToolCache()
    cache_key = f"cron:{action}:{schedule}:{command}:{target_path}:{log_limit}:{mode}:{limit_val}"
    if use_cache and action in ("list", "audit"):
        cached_result = cache.get(cache_key)
        if cached_result is not None:
            if verbose:
                logging.debug("Cache hit! Returning cached result.")
            cached_result["cached"] = True
            return cached_result

    shutdown = GracefulShutdown()
    processed_items: list[str] = []

    try:
        if action == CronAction.LIST.value:
            lines, err = _get_crontab_entries()
            if err:
                return {
                    "success": False,
                    "error": err,
                    "exit_code": EXIT_ERROR,
                    "duration_ms": round((time.monotonic() - start_time) * 1000, 2),
                }
            processed_items = lines

        elif action == CronAction.ADD.value:
            if not schedule or not command:
                return {
                    "success": False,
                    "error": "Both --schedule and --command are required when adding a cron task.",
                    "exit_code": EXIT_INVALID_INPUT,
                    "duration_ms": round((time.monotonic() - start_time) * 1000, 2),
                }

            schedule_clean = schedule.strip()
            if not CRON_REGEX.match(schedule_clean):
                return {
                    "success": False,
                    "error": f"Invalid cron schedule format: '{schedule}'. Expected standard 5-part syntax.",
                    "exit_code": EXIT_INVALID_INPUT,
                    "duration_ms": round((time.monotonic() - start_time) * 1000, 2),
                }

            new_entry = f"{schedule_clean} {command.strip()}"
            lines, err = _get_crontab_entries()
            if err and "no crontab for" not in err.lower():
                return {
                    "success": False,
                    "error": err,
                    "exit_code": EXIT_ERROR,
                    "duration_ms": round((time.monotonic() - start_time) * 1000, 2),
                }

            if new_entry not in lines:
                lines.append(new_entry)
                success_set, set_err = _set_crontab_entries(lines)
                if not success_set:
                    return {
                        "success": False,
                        "error": set_err or "Failed to append entry to crontab.",
                        "exit_code": EXIT_ERROR,
                        "duration_ms": round((time.monotonic() - start_time) * 1000, 2),
                    }
            processed_items = [new_entry]

        elif action == CronAction.REMOVE.value:
            if not command:
                return {
                    "success": False,
                    "error": "Parameter --command (or pattern) is required to identify the cron entry for removal.",
                    "exit_code": EXIT_INVALID_INPUT,
                    "duration_ms": round((time.monotonic() - start_time) * 1000, 2),
                }

            lines, err = _get_crontab_entries()
            if err:
                return {
                    "success": False,
                    "error": err,
                    "exit_code": EXIT_ERROR,
                    "duration_ms": round((time.monotonic() - start_time) * 1000, 2),
                }

            cmd_pattern = command.strip()
            filtered_lines = [line for line in lines if cmd_pattern not in line]
            removed_count = len(lines) - len(filtered_lines)

            if removed_count > 0:
                success_set, set_err = _set_crontab_entries(filtered_lines)
                if not success_set:
                    return {
                        "success": False,
                        "error": set_err or "Failed to rewrite crontab after removal.",
                        "exit_code": EXIT_ERROR,
                        "duration_ms": round((time.monotonic() - start_time) * 1000, 2),
                    }
            processed_items = [
                f"Removed {removed_count} job(s) matching '{cmd_pattern}'"
            ]

        elif action == CronAction.AUDIT.value:
            log_files: list[Path] = []
            if target_path.is_file():
                log_files = [target_path]
            elif target_path.is_dir():
                pattern = file_pattern or "*cron*.log"
                iterator = (
                    target_path.rglob(pattern)
                    if recursive
                    else target_path.glob(pattern)
                )
                log_files = [p for p in iterator if p.is_file()]

            if not log_files:
                # Attempt standard system log fallback paths
                for fallback in [
                    Path("/var/log/syslog"),
                    Path("/var/log/cron.log"),
                    Path.home() / ".cron.log",
                ]:
                    if fallback.exists():
                        log_files.append(fallback)
                        break

            audit_lines: list[str] = []
            for lfile in log_files:
                try:
                    content = lfile.read_text(
                        encoding="utf-8", errors="replace"
                    ).splitlines()
                    cron_matches = [
                        line for line in content if "CRON" in line or "cron" in line
                    ]
                    audit_lines.extend(cron_matches[-log_limit:])
                except Exception as read_err:
                    if verbose:
                        logging.debug(f"Error reading log file {lfile}: {read_err}")

            processed_items = audit_lines[-log_limit:]

        else:
            return {
                "success": False,
                "error": f"Unsupported action '{action}'. Valid choices: list, add, remove, audit.",
                "exit_code": EXIT_INVALID_INPUT,
                "duration_ms": round((time.monotonic() - start_time) * 1000, 2),
            }

        duration_ms = round((time.monotonic() - start_time) * 1000, 2)

        result: dict[str, Any] = {
            "success": True,
            "action": action,
            "target": str(target_path),
            "mode": mode,
            "count": len(processed_items),
            "items": processed_items
            if mode == "detailed"
            else processed_items[:limit_val],
            "parsed_env_vars": parsed_env,
            "context": get_execution_context(),
            "cached": False,
            "duration_ms": duration_ms,
            "exit_code": EXIT_SUCCESS,
        }

        if use_cache and action in ("list", "audit"):
            cache.set(cache_key, result)

        return result

    except PermissionError as exc:
        duration_ms = round((time.monotonic() - start_time) * 1000, 2)
        return {
            "success": False,
            "error": f"Permission denied during operation: {exc}",
            "exit_code": EXIT_PERMISSION_DENIED,
            "duration_ms": duration_ms,
        }
    except Exception as exc:
        duration_ms = round((time.monotonic() - start_time) * 1000, 2)
        return {
            "success": False,
            "error": f"Tool execution error: {exc}",
            "exit_code": EXIT_ERROR,
            "duration_ms": duration_ms,
        }
    finally:
        shutdown.restore()


# ==============================================================================
# SECTION 6: Output Routing (LLM vs Human Terminal)
# ==============================================================================


def write_llm_output(data: dict[str, Any]) -> None:
    """Format and write clean JSON output to LLM_OUTPUT destination safely."""
    out_path = os.environ.get("LLM_OUTPUT", "/dev/stdout")
    json_payload = (
        json.dumps(data, indent=2, ensure_ascii=False, cls=ToolJSONEncoder) + "\n"
    )

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
# SECTION 7: Function Entry Point for AIChat
# ==============================================================================


def run(
    target: str = "~/",
    action: Literal["list", "add", "remove", "audit"] = "list",
    schedule: Optional[str] = None,
    command: Optional[str] = None,
    log_limit: int = 50,
    mode: Literal["summary", "detailed"] = "summary",
    limit: Optional[int] = None,
    file_pattern: Optional[str] = None,
    env_var: Optional[list[str]] = None,
    recursive: bool = False,
    use_cache: bool = False,
    no_color: bool = False,
    verbose: bool = False,
) -> None:
    """Execute cron scheduler tool operations with specified parameters.

    Args:
        target: Target directory or file path for operations (default: ~/)
        action: Action to execute: list, add, remove, audit (default: list)
        schedule: Standard 5-part cron schedule pattern (e.g. '*/5 * * * *')
        command: Command string to add or search for removal
        log_limit: Maximum lines to audit in cron execution logs (default: 50)
        mode: Execution mode: summary or detailed (default: summary)
        limit: Maximum items to return in response
        file_pattern: Optional log file pattern filter
        env_var: Custom environment variable in KEY=VALUE format (repeatable)
        recursive: Search directories recursively for logs
        use_cache: Enable result caching
        no_color: Disable ANSI color output
        verbose: Enable detailed debug log output
    """
    result = execute_tool(
        target=target,
        action=action,
        schedule=schedule,
        command=command,
        log_limit=log_limit,
        mode=mode,
        limit=limit,
        file_pattern=file_pattern,
        env_vars=env_var,
        recursive=recursive,
        use_cache=use_cache,
        no_color=no_color,
        verbose=verbose,
    )

    print_human_readable_ui(result, no_color=no_color)
    write_llm_output(result)


# ==============================================================================
# SECTION 8: CLI Argument Parser
# ==============================================================================


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="cron_scheduler_tool.py",
        description=f"Termux Task & Cron Automation Tool v{__version__}",
    )
    parser.add_argument(
        "--target",
        "-t",
        default="~/",
        metavar="PATH",
        help="Target file or directory path (default: ~/)",
    )
    parser.add_argument(
        "--action",
        "-a",
        choices=["list", "add", "remove", "audit"],
        default="list",
        help="Action to perform: list, add, remove, audit (default: list)",
    )
    parser.add_argument(
        "--schedule",
        "-s",
        metavar="CRON_EXPR",
        help="Standard 5-part cron schedule string (e.g. '*/15 * * * *')",
    )
    parser.add_argument(
        "--command",
        "-c",
        metavar="CMD",
        help="Shell command string to schedule or identify for removal",
    )
    parser.add_argument(
        "--log-limit",
        type=int,
        default=50,
        dest="log_limit",
        help="Maximum lines to inspect during log audit (default: 50)",
    )
    parser.add_argument(
        "--mode",
        choices=["summary", "detailed"],
        default="summary",
        help="Execution output mode (default: summary)",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=None,
        help="Maximum items to return in response",
    )
    parser.add_argument(
        "--file-pattern",
        dest="file_pattern",
        metavar="PATTERN",
        help="File glob pattern filter (e.g. *.log)",
    )
    parser.add_argument(
        "--env-var",
        action="append",
        dest="env_var",
        metavar="KEY=VALUE",
        help="Custom environment variable (repeatable)",
    )
    parser.add_argument(
        "--recursive",
        action="store_true",
        default=False,
        help="Process log directories recursively",
    )
    parser.add_argument(
        "--use-cache",
        action="store_true",
        default=False,
        dest="use_cache",
        help="Enable result caching",
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
        "-v",
        action="store_true",
        default=False,
        help="Enable detailed debug logging",
    )
    return parser


if __name__ == "__main__":
    args = _build_parser().parse_args()
    res = execute_tool(
        target=args.target,
        action=args.action,
        schedule=args.schedule,
        command=args.command,
        log_limit=args.log_limit,
        mode=args.mode,
        limit=args.limit,
        file_pattern=args.file_pattern,
        env_vars=args.env_var,
        recursive=args.recursive,
        use_cache=args.use_cache,
        no_color=args.no_color,
        verbose=args.verbose,
    )

    print_human_readable_ui(res, no_color=args.no_color)
    write_llm_output(res)
    sys.exit(res.get("exit_code", EXIT_SUCCESS))
