#!/usr/bin/env python3
# ==============================================================================
# git_status_tool.py — Pyrmethus Git Repository Status Intelligence Tool v2.2.0-ASCENDED
# argc/aichat compatible · Termux · Git Repository Analysis · Native Caching
#
# @describe Detailed Git status summary including branch tracking, commit metadata, staged/unstaged files, and stash counts.
#
# @meta require-tools aichat git
#
# @option --target <PATH>                Target directory path (default: current working directory)
# @option --mode <MODE>                  Execution mode: summary/detailed (default: summary)
# @flag   --use-cache                    Enable result caching for static status checks
# @flag   --no-color                     Disable ANSI color output
# @flag   --verbose                      Enable detailed debug log output
#
# @env LLM_OUTPUT=/dev/stdout            Output path for LLM integration
# ==============================================================================

from __future__ import annotations

import argparse
import hashlib
import json
import os
import pickle
import re
import signal
import subprocess
import sys
import time
from datetime import date, datetime, timedelta
from enum import Enum
from pathlib import Path
from typing import Any, Literal, Optional

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
        if isinstance(obj, (datetime, date)):
            return obj.isoformat()
        if isinstance(obj, timedelta):
            return obj.total_seconds()
        if isinstance(obj, bytes):
            return obj.decode("utf-8", errors="replace")
        if isinstance(obj, (set, frozenset)):
            return list(obj)
        if hasattr(obj, "to_dict") and callable(obj.to_dict):
            return obj.to_dict()
        try:
            return super().default(obj)
        except TypeError:
            return str(obj)


# ==============================================================================
# SECTION 2: Terminal Color Palette & UI Helpers
# ==============================================================================

NEON_CYAN = "\033[38;5;51m"
NEON_GREEN = "\033[38;5;46m"
NEON_RED = "\033[38;5;196m"
NEON_YELLOW = "\033[38;5;226m"
NEON_PURPLE = "\033[38;5;129m"
NEON_PINK = "\033[38;5;198m"
NEON_LIME = "\033[38;5;82m"
RESET = "\033[0m"
BOLD = "\033[1m"
DIM = "\033[2m"

BOX_TL = "╭"
BOX_TR = "╮"
BOX_BL = "╰"
BOX_BR = "╯"
BOX_V = "│"
BOX_H = "─"
BOX_LT = "├"
BOX_RT = "┤"

_ANSI_RE = re.compile(r"\x1b(?:[@-Z\\-_]|\[[0-?]*[ -/]*[@-~])|\033\[[0-9;?]*[a-zA-Z]")


def _strip_ansi(text: str) -> str:
    """Remove ANSI escape sequences."""
    return _ANSI_RE.sub("", text)


def _is_tty() -> bool:
    """Return True if stderr is attached to an interactive terminal."""
    return sys.stderr.isatty() and os.environ.get("TERM", "").lower() not in (
        "dumb",
        "",
    )


def get_width() -> int:
    """Return current terminal column count based on stderr, constrained to reasonable bounds."""
    try:
        cols = os.get_terminal_size(sys.stderr.fileno()).columns
        return max(40, min(cols, 120))
    except (OSError, AttributeError):
        return 68


def _cprint(
    text: str, file: Any = None, no_color: bool = False, end: str = "\n"
) -> None:
    """Print pre-formatted ANSI text to stderr by default."""
    target = file or sys.stderr
    if no_color or not _is_tty():
        text = _strip_ansi(text)
    print(text, file=target, flush=True, end=end)


def print_human_readable_ui(data: dict[str, Any], no_color: bool = False) -> None:
    """Render colorized box UI for human terminal sessions to stderr."""
    if not _is_tty() or no_color:
        return

    success = data.get("success", False)
    status_color = NEON_GREEN if success else NEON_RED
    status_symbol = "✓" if success else "✗"
    status_text = "SUCCESS" if success else "FAILED"

    box_w = get_width() - 4
    border = BOX_H * box_w

    _cprint(f"{NEON_PURPLE}{BOX_TL}{border}{BOX_TR}{RESET}")
    _cprint(
        f"{NEON_PURPLE}{BOX_V}{RESET} {NEON_PINK}🌱 [GIT STATUS INTELLIGENCE v{__version__}]{RESET} {status_color}{BOLD}{status_symbol} {status_text}{RESET}"
    )
    _cprint(f"{NEON_PURPLE}{BOX_LT}{border}{BOX_RT}{RESET}")
    _cprint(
        f"{NEON_PURPLE}{BOX_V}{RESET} {NEON_CYAN}Target:{RESET}   {data.get('target', 'N/A')}"
    )

    if success:
        _cprint(
            f"{NEON_PURPLE}{BOX_V}{RESET} {NEON_CYAN}Branch:{RESET}   {NEON_GREEN}{BOLD}{data.get('branch', 'N/A')}{RESET} ({data.get('commit_hash', 'N/A')})"
        )
        if data.get("tracking"):
            _cprint(
                f"{NEON_PURPLE}{BOX_V}{RESET} {NEON_CYAN}Tracking:{RESET} {data.get('tracking')} {DIM}(Ahead: {data.get('ahead', 0)} | Behind: {data.get('behind', 0)}){RESET}"
            )

        changes = data.get("changes_summary", {})
        _cprint(
            f"{NEON_PURPLE}{BOX_V}{RESET} {NEON_CYAN}Changes:{RESET}  {NEON_LIME}Staged: {changes.get('staged', 0)}{RESET} | {NEON_YELLOW}Unstaged: {changes.get('unstaged', 0)}{RESET} | {NEON_RED}Untracked: {changes.get('untracked', 0)}{RESET}"
        )
        _cprint(
            f"{NEON_PURPLE}{BOX_V}{RESET} {NEON_CYAN}Stashes:{RESET}  {data.get('stash_count', 0)}"
        )

    _cprint(
        f"{NEON_PURPLE}{BOX_V}{RESET} {NEON_CYAN}Duration:{RESET} {DIM}{data.get('duration_ms', 0)}ms{RESET}"
    )

    if not success and "error" in data:
        _cprint(f"{NEON_PURPLE}{BOX_LT}{border}{BOX_RT}{RESET}")
        _cprint(
            f"{NEON_PURPLE}{BOX_V}{RESET} {NEON_RED}Error:{RESET}    {data['error']}"
        )

    items = data.get("files", [])
    if items:
        _cprint(f"{NEON_PURPLE}{BOX_LT}{border}{BOX_RT}{RESET}")
        _cprint(
            f"{NEON_PURPLE}{BOX_V}{RESET} {BOLD}Changed Files ({len(items)}):{RESET}"
        )
        for idx, item in enumerate(items[:10], 1):
            status_code = item.get("status", "??")
            path = item.get("path", "")
            _cprint(
                f"{NEON_PURPLE}{BOX_V}{RESET}   {NEON_CYAN}{idx:02d}.{RESET} [{NEON_YELLOW}{status_code}{RESET}] {path}"
            )
        if len(items) > 10:
            _cprint(
                f"{NEON_PURPLE}{BOX_V}{RESET}   {DIM}... and {len(items) - 10} more files{RESET}"
            )

    _cprint(f"{NEON_PURPLE}{BOX_BL}{border}{BOX_BR}{RESET}")


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
    """Extract complete execution context from standard and Termux environment."""
    termux_prefix = os.environ.get("PREFIX", "")
    return {
        "tool_name": os.environ.get("LLM_TOOL_NAME", "git_status_tool"),
        "cache_dir": os.environ.get("LLM_TOOL_CACHE_DIR"),
        "root_dir": os.environ.get("LLM_ROOT_DIR"),
        "output_path": os.environ.get("LLM_OUTPUT"),
        "cwd": get_builtin_var("__cwd__") or os.getcwd(),
        "termux_prefix": termux_prefix,
        "is_termux": "com.termux" in termux_prefix
        or Path("/data/data/com.termux").exists(),
    }


# ==============================================================================
# SECTION 4: Native Caching & Signal Handlers
# ==============================================================================


class ToolCache:
    """Caching utility with TTL support for status checks."""

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
        tmp_file = cache_file.with_suffix(f".tmp.{os.getpid()}_{time.time_ns()}")
        try:
            with open(tmp_file, "wb") as fp:
                pickle.dump(value, fp)
            tmp_file.replace(cache_file)
        except Exception:
            if tmp_file.exists():
                tmp_file.unlink(missing_ok=True)


class GracefulShutdown:
    """Signal handler for graceful cancellation."""

    def __init__(self) -> None:
        self.interrupted = False
        self._old_sigint = signal.signal(signal.SIGINT, self._handle_signal)
        self._old_sigterm = signal.signal(signal.SIGTERM, self._handle_signal)

    def _handle_signal(self, signum: int, frame: Any) -> None:
        self.interrupted = True

    def restore(self) -> None:
        signal.signal(signal.SIGINT, self._old_sigint)
        signal.signal(signal.SIGTERM, self._old_sigterm)

    def should_stop(self) -> bool:
        return self.interrupted


# ==============================================================================
# SECTION 5: Core Git Execution Engine
# ==============================================================================


def _run_git(args: list[str], cwd: str) -> tuple[int, str, str]:
    """Execute git command and return (returncode, stdout, stderr)."""
    git_bin = os.environ.get("GIT_BINARY") or "git"
    try:
        proc = subprocess.run(
            [git_bin, *args],
            cwd=cwd,
            capture_output=True,
            text=True,
            timeout=15,
        )
        return proc.returncode, proc.stdout.strip(), proc.stderr.strip()
    except FileNotFoundError:
        return EXIT_INVALID_INPUT, "", "Git binary not found in system PATH."
    except Exception as exc:
        return EXIT_ERROR, "", str(exc)


# ==============================================================================
# SECTION 6: Primary Master Tool Execution Logic
# ==============================================================================


def execute_tool(
    target: Optional[str] = None,
    mode: str = "summary",
    use_cache: bool = False,
    no_color: bool = False,
    verbose: bool = False,
) -> dict[str, Any]:
    """
    Core execution logic for Git status extraction.
    """
    start_time = time.monotonic()

    base_cwd = get_builtin_var("__cwd__") or os.getcwd()
    target_path = Path(target or base_cwd).expanduser().resolve()

    if not target_path.exists() or not target_path.is_dir():
        return {
            "success": False,
            "error": f"Target directory does not exist: {target_path}",
            "exit_code": EXIT_FILE_NOT_FOUND,
            "duration_ms": 0.0,
        }

    target_dir = str(target_path)

    # Check if target is inside a Git repository
    code, is_repo, err = _run_git(
        ["rev-parse", "--is-inside-work-tree"], cwd=target_dir
    )
    if code != 0 or is_repo != "true":
        return {
            "success": False,
            "error": f"Target directory is not a Git repository: {target_dir}",
            "exit_code": EXIT_INVALID_INPUT,
            "duration_ms": round((time.monotonic() - start_time) * 1000, 2),
        }

    cache = ToolCache()
    cache_key = f"git_status:{target_dir}:{mode}"
    if use_cache:
        cached_result = cache.get(cache_key)
        if cached_result is not None:
            cached_result["cached"] = True
            return cached_result

    shutdown = GracefulShutdown()

    try:
        # Branch & Commit Hash
        _, branch, _ = _run_git(["rev-parse", "--abbrev-ref", "HEAD"], cwd=target_dir)
        _, commit_hash, _ = _run_git(["rev-parse", "--short", "HEAD"], cwd=target_dir)

        # Tracking info
        _, tracking, _ = _run_git(
            ["rev-parse", "--abbrev-ref", "@{upstream}"], cwd=target_dir
        )
        ahead, behind = 0, 0
        if tracking:
            _, counts, _ = _run_git(
                ["rev-list", "--left-right", "--count", "HEAD...@{upstream}"],
                cwd=target_dir,
            )
            if counts and "\t" in counts:
                try:
                    ahead, behind = map(int, counts.split("\t"))
                except ValueError:
                    pass

        # Stash Count
        _, stash_raw, _ = _run_git(["stash", "list"], cwd=target_dir)
        stash_count = len(stash_raw.splitlines()) if stash_raw else 0

        # Detailed Status Parsing
        _, status_raw, _ = _run_git(["status", "--porcelain=v1"], cwd=target_dir)

        parsed_files = []
        staged_count = 0
        unstaged_count = 0
        untracked_count = 0

        for line in status_raw.splitlines():
            if len(line) < 4:
                continue
            index_status = line[0]
            work_status = line[1]
            file_path = line[3:].strip()

            if index_status != " " and index_status != "?":
                staged_count += 1
            if work_status != " " and work_status != "?":
                unstaged_count += 1
            if index_status == "?" and work_status == "?":
                untracked_count += 1

            parsed_files.append(
                {
                    "status": line[:2],
                    "staged": index_status != " " and index_status != "?",
                    "unstaged": work_status != " " and work_status != "?",
                    "untracked": index_status == "?",
                    "path": file_path,
                }
            )

        duration_ms = round((time.monotonic() - start_time) * 1000, 2)

        result: dict[str, Any] = {
            "success": True,
            "target": target_dir,
            "branch": branch or "HEAD",
            "commit_hash": commit_hash or "N/A",
            "tracking": tracking or None,
            "ahead": ahead,
            "behind": behind,
            "stash_count": stash_count,
            "changes_summary": {
                "staged": staged_count,
                "unstaged": unstaged_count,
                "untracked": untracked_count,
                "total": len(parsed_files),
            },
            "files": parsed_files if mode == "detailed" else parsed_files[:15],
            "context": get_execution_context(),
            "cached": False,
            "duration_ms": duration_ms,
            "exit_code": EXIT_SUCCESS,
        }

        if shutdown.should_stop():
            result["success"] = False
            result["error"] = "Execution interrupted by signal."
            result["exit_code"] = EXIT_INTERRUPTED

        if use_cache and result["success"]:
            cache.set(cache_key, result)

        return result

    except Exception as exc:
        duration_ms = round((time.monotonic() - start_time) * 1000, 2)
        return {
            "success": False,
            "error": f"Tool execution failure: {exc}",
            "exit_code": EXIT_ERROR,
            "duration_ms": duration_ms,
        }
    finally:
        shutdown.restore()


# ==============================================================================
# SECTION 7: Output Routing (LLM vs Human Terminal)
# ==============================================================================


def write_llm_output(data: dict[str, Any]) -> None:
    """Format and write JSON output to LLM_OUTPUT destination safely."""
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
            p = Path(out_path).expanduser().resolve()
            p.parent.mkdir(parents=True, exist_ok=True)
            with open(p, "a", encoding="utf-8") as fp:
                fp.write(json_payload)
        except OSError as err:
            sys.stderr.write(f"Failed writing to LLM_OUTPUT '{out_path}': {err}\n")
            sys.stdout.write(json_payload)
            sys.stdout.flush()


# ==============================================================================
# SECTION 8: Function Entry Point for AIChat
# ==============================================================================


def run(
    target: Optional[str] = None,
    mode: Literal["summary", "detailed"] = "summary",
    use_cache: bool = False,
    no_color: bool = False,
    verbose: bool = False,
) -> None:
    """Execute Git status analysis tool.

    Args:
        target: Target directory path (defaults to current working dir)
        mode: Result detail mode (summary/detailed)
        use_cache: Enable result caching
        no_color: Disable ANSI color output
        verbose: Enable debug log output
    """
    result = execute_tool(
        target=target,
        mode=mode,
        use_cache=use_cache,
        no_color=no_color,
        verbose=verbose,
    )

    print_human_readable_ui(result, no_color=no_color)
    write_llm_output(result)


# ==============================================================================
# SECTION 9: CLI Argument Parser
# ==============================================================================


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="git_status_tool.py",
        description=f"Pyrmethus Git Repository Status Intelligence Tool v{__version__}",
    )
    parser.add_argument(
        "--target",
        "-t",
        metavar="PATH",
        help="Target directory path (default: current working directory)",
    )
    parser.add_argument(
        "--mode",
        choices=["summary", "detailed"],
        default="summary",
        help="Output mode detail level (default: summary)",
    )
    parser.add_argument(
        "--use-cache",
        action="store_true",
        default=False,
        dest="use_cache",
        help="Enable status caching",
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
        mode=args.mode,
        use_cache=args.use_cache,
        no_color=args.no_color,
        verbose=args.verbose,
    )

    print_human_readable_ui(res, no_color=args.no_color)
    write_llm_output(res)
    sys.exit(res.get("exit_code", EXIT_SUCCESS))
