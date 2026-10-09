#!/usr/bin/env python3
# ==============================================================================
# file_patch_editor.py — Pyrmethus AIChat Tool v2.1.0
# argc/aichat compatible · Human-Readable Colorized Outputs · Native Caching
#
# @describe High-performance file editor supporting line viewing, safe literal string replacement, match counting, and dependency-free unified diff patching.
#
# @meta require-tools aichat
#
# @option --action! <ACTION>             Operation to perform: view, replace, count, patch (required)
# @option --file-path! <PATH>            Path to the target file or directory (required)
# @option --search <TEXT>                Search string (literal matching; required for replace/count)
# @option --replacement <TEXT>           Replacement string (literal replacement; required for replace)
# @option --contents <TEXT>              Unified diff patch contents to apply (required for patch)
# @option --start-line <NUM>             Starting line number for view operation (1-based index)
# @option --end-line <NUM>               Ending line number for view operation
# @flag   --dry-run                      Preview changes without modifying target files
# @flag   --backup                       Create backup files (.bak) before modifying
# @flag   --use-cache                    Enable result caching for read-only operations
# @flag   --no-color                     Disable ANSI color output
# @flag   --verbose                      Enable detailed debug log output
#
# @env LLM_OUTPUT=/dev/stdout            Output path for LLM integration
# ==============================================================================

from __future__ import annotations

import argparse
import atexit
import difflib
import hashlib
import json
import logging
import os
import pickle
import re
import signal
import sys
import time
from datetime import datetime, timedelta
from enum import Enum
from pathlib import Path
from typing import Any, Literal, Optional

try:
    import fcntl

    HAS_FCNTL = True
except ImportError:
    HAS_FCNTL = False

__version__ = "2.1.0"
__all__ = [
    "ToolCache",
    "ToolError",
    "__version__",
    "apply_file_patch",
    "execute_tool",
    "get_agent_var",
    "get_builtin_var",
    "get_execution_context",
    "parse_unified_diff",
    "run",
]

# Track temporary files globally for signal-safe cleanup
_ACTIVE_TEMP_FILES: set[Path] = set()


def _cleanup_temp_files() -> None:
    """Remove any orphan temporary swap files created during atomic operations."""
    for tmp_path in list(_ACTIVE_TEMP_FILES):
        try:
            if tmp_path.exists():
                tmp_path.unlink()
        except Exception:
            pass
    _ACTIVE_TEMP_FILES.clear()


atexit.register(_cleanup_temp_files)

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


class EditorAction(str, Enum):
    VIEW = "view"
    REPLACE = "replace"
    COUNT = "count"
    PATCH = "patch"
    WRITE = "write"
    CREATE = "create"


class ToolError(Exception):
    """Structured exception model for editor operations."""

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
    """Custom JSON encoder handling Path, Enum, datetime, timedelta, and sets safely."""

    def default(self, obj: Any) -> Any:
        if isinstance(obj, Path):
            return str(obj)
        if isinstance(obj, Enum):
            return obj.value
        if isinstance(obj, (datetime, timedelta)):
            return str(obj)
        if isinstance(obj, set):
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

# Comprehensive ANSI escape sequence stripping regex
_ANSI_RE = re.compile(r"\033\[[0-9;]*[a-zA-Z]|\033\[?[0-9;]*[0-9;]*[0-9;]*[a-zA-Z]")


def _strip_ansi(text: str) -> str:
    """Remove all ANSI escape sequences from text."""
    return _ANSI_RE.sub("", text)


def _is_tty() -> bool:
    """Return True if stdout is attached to an interactive, non-dumb terminal."""
    return sys.stdout.isatty() and os.environ.get("TERM", "").lower() not in (
        "dumb",
        "",
    )


def _cprint(
    text: str, file: Any = None, no_color: bool = False, end: str = "\n"
) -> None:
    """Print pre-formatted ANSI text, stripping colors if stdout is not a TTY or --no-color is set."""
    target = file or sys.stdout
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
        f"{NEON_PURPLE}│{RESET} {NEON_PINK}⚡ [FILE PATCH & STR EDITOR v{__version__}]{RESET} {status_color}{BOLD}{status_symbol} {status_text}{RESET}"
    )
    _cprint(f"{NEON_PURPLE}├{border}┤{RESET}")
    _cprint(
        f"{NEON_PURPLE}│{RESET} {NEON_CYAN}Target Path:{RESET} {data.get('file_path', 'N/A')}"
    )
    _cprint(
        f"{NEON_PURPLE}│{RESET} {NEON_CYAN}Action:{RESET}      {NEON_YELLOW}{data.get('action', 'N/A')}{RESET}"
    )

    action = data.get("action")
    if action == "view":
        _cprint(
            f"{NEON_PURPLE}│{RESET} {NEON_CYAN}Lines View:{RESET}  Line {data.get('view_start_line')} to {data.get('view_end_line')} (Total: {data.get('total_lines')})"
        )
    elif action in {"replace", "count"}:
        _cprint(
            f"{NEON_PURPLE}│{RESET} {NEON_CYAN}Search:{RESET}      '{data.get('search', '')}'"
        )
        if action == "replace":
            _cprint(
                f"{NEON_PURPLE}│{RESET} {NEON_CYAN}Replacement:{RESET} '{data.get('replacement', '')}'"
            )
            _cprint(
                f"{NEON_PURPLE}│{RESET} {NEON_CYAN}Replaced:{RESET}    {NEON_GREEN}{data.get('replacements_made', 0)}{RESET} match(es)"
            )
        elif action == "count":
            _cprint(
                f"{NEON_PURPLE}│{RESET} {NEON_CYAN}Matches:{RESET}     {NEON_GREEN}{data.get('match_count', 0)}{RESET}"
            )
    elif action == "patch":
        _cprint(
            f"{NEON_PURPLE}│{RESET} {NEON_CYAN}Target Type:{RESET} {data.get('target_type', 'file')}"
        )
        _cprint(
            f"{NEON_PURPLE}│{RESET} {NEON_CYAN}Dry Run:{RESET}     {NEON_YELLOW}{data.get('dry_run', False)}{RESET}"
        )
        _cprint(
            f"{NEON_PURPLE}│{RESET} {NEON_CYAN}Patched Files:{RESET}{NEON_GREEN}{len(data.get('patched_files', []))}{RESET}"
        )
        if "lines_added" in data or "lines_removed" in data:
            _cprint(
                f"{NEON_PURPLE}│{RESET} {NEON_CYAN}Changes:{RESET}     {NEON_GREEN}+{data.get('lines_added', 0)}{RESET} / {NEON_RED}-{data.get('lines_removed', 0)}{RESET} lines"
            )
    elif action in {"write", "create"}:
        _cprint(
            f"{NEON_PURPLE}│{RESET} {NEON_CYAN}Written:{RESET}     {NEON_GREEN}{data.get('written_bytes', 0)}{RESET} bytes"
        )
        _cprint(
            f"{NEON_PURPLE}│{RESET} {NEON_CYAN}Dry Run:{RESET}     {NEON_YELLOW}{data.get('dry_run', False)}{RESET}"
        )

    if data.get("backup_created"):
        bak_str = (
            f"Created (.bak) [{data.get('backup_sha256', '')[:8]}]"
            if data.get("backup_sha256")
            else "Created (.bak)"
        )
        _cprint(
            f"{NEON_PURPLE}│{RESET} {NEON_CYAN}Backup:{RESET}      {NEON_GREEN}{bak_str}{RESET}"
        )

    if data.get("encoding_used"):
        _cprint(
            f"{NEON_PURPLE}│{RESET} {NEON_CYAN}Encoding:{RESET}    {DIM}{data.get('encoding_used')}{RESET}"
        )

    _cprint(
        f"{NEON_PURPLE}│{RESET} {NEON_CYAN}Cached:{RESET}      {NEON_YELLOW}{data.get('cached', False)}{RESET}"
    )
    _cprint(
        f"{NEON_PURPLE}│{RESET} {NEON_CYAN}Duration:{RESET}    {DIM}{data.get('duration_ms', 0)}ms{RESET}"
    )

    if not success and "error" in data:
        _cprint(f"{NEON_PURPLE}├{border}┤{RESET}")
        _cprint(f"{NEON_PURPLE}│{RESET} {NEON_RED}Error:{RESET}       {data['error']}")

    if action == "view" and "content" in data:
        _cprint(f"{NEON_PURPLE}├{border}┤{RESET}")
        _cprint(f"{NEON_PURPLE}│{RESET} {BOLD}File Content Preview:{RESET}")
        lines = data["content"].splitlines()
        start_ln = data.get("view_start_line", 1)
        for idx, line in enumerate(lines[:25]):
            _cprint(f"{NEON_PURPLE}│{RESET}   {DIM}{start_ln + idx:4d} │{RESET} {line}")
        if len(lines) > 25:
            _cprint(
                f"{NEON_PURPLE}│{RESET}   {DIM}... and {len(lines) - 25} more lines{RESET}"
            )

    if action == "patch" and "diff_preview" in data and data["diff_preview"]:
        _cprint(f"{NEON_PURPLE}├{border}┤{RESET}")
        _cprint(f"{NEON_PURPLE}│{RESET} {BOLD}Diff Preview:{RESET}")
        diff_lines = data["diff_preview"].splitlines()
        for line in diff_lines[:25]:
            if line.startswith("+") and not line.startswith("+++"):
                _cprint(f"{NEON_PURPLE}│{RESET}   {NEON_GREEN}{line}{RESET}")
            elif line.startswith("-") and not line.startswith("---"):
                _cprint(f"{NEON_PURPLE}│{RESET}   {NEON_RED}{line}{RESET}")
            else:
                _cprint(f"{NEON_PURPLE}│{RESET}   {DIM}{line}{RESET}")
        if len(diff_lines) > 25:
            _cprint(
                f"{NEON_PURPLE}│{RESET}   {DIM}... and {len(diff_lines) - 25} more diff lines{RESET}"
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
    """Extract complete execution context from the llm-functions environment."""
    return {
        "tool_name": os.environ.get("LLM_TOOL_NAME"),
        "cache_dir": os.environ.get("LLM_TOOL_CACHE_DIR"),
        "root_dir": os.environ.get("LLM_ROOT_DIR"),
        "output_path": os.environ.get("LLM_OUTPUT"),
        "cwd": get_builtin_var("__cwd__") or os.getcwd(),
        "is_termux": "TERMUX_VERSION" in os.environ
        or "/com.termux/" in os.environ.get("PATH", ""),
        "pid": os.getpid(),
    }


def _read_file_with_fallback(file_path: Path) -> tuple[str, str, str]:
    """
    Read file text using an encoding fallback matrix.
    Returns (content, encoding_used, newline_type).
    """
    raw_bytes = file_path.read_bytes()

    # Detect newline style
    if b"\r\n" in raw_bytes:
        newline_type = "\r\n"
    else:
        newline_type = "\n"

    for enc in ("utf-8", "utf-8-sig", "latin-1", "cp1252"):
        try:
            content = raw_bytes.decode(enc)
            return content, enc, newline_type
        except UnicodeDecodeError:
            continue

    return raw_bytes.decode("utf-8", errors="replace"), "utf-8-lossy", newline_type


def _acquire_file_lock(file_obj: Any) -> None:
    """Acquire a non-blocking exclusive lock if supported by OS platform."""
    if HAS_FCNTL and hasattr(file_obj, "fileno"):
        try:
            fcntl.flock(file_obj.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            pass


def _release_file_lock(file_obj: Any) -> None:
    """Release file lock if supported by OS platform."""
    if HAS_FCNTL and hasattr(file_obj, "fileno"):
        try:
            fcntl.flock(file_obj.fileno(), fcntl.LOCK_UN)
        except OSError:
            pass


# ==============================================================================
# SECTION 4: Native Caching & Signal Handlers
# ==============================================================================


class ToolCache:
    """Caching utility with TTL support and file modification hash validation."""

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

    def get(
        self, key_data: str, target_file: Optional[Path] = None, ttl_seconds: int = 3600
    ) -> Optional[Any]:
        cache_file = self.cache_dir / f"{self._make_key(key_data)}.cache"
        if not cache_file.exists():
            return None
        try:
            mtime = cache_file.stat().st_mtime
            if time.time() - mtime > ttl_seconds:
                cache_file.unlink(missing_ok=True)
                return None

            with open(cache_file, "rb") as fp:
                cached_obj = pickle.load(fp)

            # High-precision TTL cache invalidation check against target file stat
            if target_file and target_file.exists() and isinstance(cached_obj, dict):
                target_stat = target_file.stat()
                recorded_mtime = cached_obj.get("_target_mtime")
                recorded_size = cached_obj.get("_target_size")
                if (
                    recorded_mtime != target_stat.st_mtime
                    or recorded_size != target_stat.st_size
                ):
                    cache_file.unlink(missing_ok=True)
                    return None

            return cached_obj
        except Exception:
            return None

    def set(
        self, key_data: str, value: Any, target_file: Optional[Path] = None
    ) -> None:
        cache_file = self.cache_dir / f"{self._make_key(key_data)}.cache"
        try:
            if target_file and target_file.exists() and isinstance(value, dict):
                stat_info = target_file.stat()
                value["_target_mtime"] = stat_info.st_mtime
                value["_target_size"] = stat_info.st_size

            with open(cache_file, "wb") as fp:
                pickle.dump(value, fp)
        except Exception:
            pass


class GracefulShutdown:
    """Signal handler for graceful cancellation of multi-file batch operations."""

    def __init__(self) -> None:
        self.interrupted = False
        signal.signal(signal.SIGINT, self._handle_signal)
        signal.signal(signal.SIGTERM, self._handle_signal)

    def _handle_signal(self, signum: int, frame: Any) -> None:
        self.interrupted = True
        _cleanup_temp_files()

    def should_stop(self) -> bool:
        return self.interrupted


# ==============================================================================
# SECTION 5: Dependency-Free Unified Diff Engine
# ==============================================================================


class PatchHunk:
    def __init__(
        self, old_start: int, old_len: int, new_start: int, new_len: int
    ) -> None:
        self.old_start = old_start
        self.old_len = old_len
        self.new_start = new_start
        self.new_len = new_len
        self.lines: list[tuple[str, str]] = []  # List of (op, line_text)


class FilePatch:
    def __init__(self, old_file: str, new_file: str) -> None:
        self.old_file = old_file
        self.new_file = new_file
        self.hunks: list[PatchHunk] = []


def _clean_patch_path(raw_path: str) -> str:
    """Extract clean relative path from unified diff header line."""
    p = raw_path.strip()
    if p.startswith("--- ") or p.startswith("+++ "):
        p = p[4:].strip()
    p = p.split("\t")[0].strip()
    if p == "/dev/null":
        return ""
    if p.startswith("a/") or p.startswith("b/"):
        p = p[2:]
    return p


def parse_unified_diff(patch_text: str) -> list[FilePatch]:
    """Parse raw unified diff patch string into FilePatch objects."""
    patches: list[FilePatch] = []
    lines = patch_text.splitlines()
    i = 0
    curr_patch: Optional[FilePatch] = None
    curr_hunk: Optional[PatchHunk] = None

    while i < len(lines):
        line = lines[i]
        if line.startswith("--- "):
            old_f = _clean_patch_path(line)
            i += 1
            if i < len(lines) and lines[i].startswith("+++ "):
                new_f = _clean_patch_path(lines[i])
                curr_patch = FilePatch(old_f, new_f)
                patches.append(curr_patch)
                curr_hunk = None
                i += 1
                continue
        elif line.startswith("@@ ") and curr_patch is not None:
            m = re.match(r"^@@\s+-(\d+)(?:,(\d+))?\s+\+(\d+)(?:,(\d+))?\s+@@", line)
            if m:
                o_start = int(m.group(1))
                o_len = int(m.group(2)) if m.group(2) is not None else 1
                n_start = int(m.group(3))
                n_len = int(m.group(4)) if m.group(4) is not None else 1
                curr_hunk = PatchHunk(o_start, o_len, n_start, n_len)
                curr_patch.hunks.append(curr_hunk)
            i += 1
            continue
        elif curr_hunk is not None and (
            line.startswith(" ") or line.startswith("-") or line.startswith("+")
        ):
            op = line[0]
            text = line[1:]
            curr_hunk.lines.append((op, text))
            i += 1
            continue
        elif curr_hunk is not None and line == "":
            curr_hunk.lines.append((" ", ""))
            i += 1
            continue
        else:
            i += 1

    return patches


def apply_file_patch(
    file_lines: list[str], file_patch: FilePatch
) -> tuple[bool, list[str], str]:
    """
    Apply patch hunks to file_lines using a multi-pass fuzzy alignment algorithm.
    Returns (success, patched_lines, error_message).
    """
    lines = list(file_lines)
    line_offset = 0

    for idx, hunk in enumerate(file_patch.hunks):
        old_lines = [text for op, text in hunk.lines if op in (" ", "-")]
        new_lines = [text for op, text in hunk.lines if op in (" ", "+")]

        expected_idx = hunk.old_start - 1 + line_offset
        match_idx = -1

        # Pass 1: Check exact expected position first
        if 0 <= expected_idx <= len(lines):
            if lines[expected_idx : expected_idx + len(old_lines)] == old_lines:
                match_idx = expected_idx

        # Pass 2: Search full file for exact line matches
        if match_idx == -1:
            for candidate in range(len(lines) + 1):
                if lines[candidate : candidate + len(old_lines)] == old_lines:
                    match_idx = candidate
                    break

        # Pass 3: Fuzzy matching allowing stripped whitespace differences
        if match_idx == -1:
            stripped_old = [l.strip() for l in old_lines]
            for candidate in range(len(lines) - len(old_lines) + 1):
                window = [
                    l.strip() for l in lines[candidate : candidate + len(old_lines)]
                ]
                if window == stripped_old:
                    match_idx = candidate
                    break

        # Pass 4: Robust fuzzy matching via difflib (Similarity > 0.85)
        if match_idx == -1 and old_lines:
            best_ratio = 0.0
            best_candidate = -1
            old_text = "\n".join(old_lines)

            # Try searching around expected index first
            search_start = max(0, expected_idx - 50)
            search_end = min(len(lines) - len(old_lines) + 1, expected_idx + 50)

            # Fallback to full file if not found nearby
            search_ranges = [
                (search_start, search_end),
                (0, len(lines) - len(old_lines) + 1),
            ]

            for start, end in search_ranges:
                if match_idx != -1:
                    break
                for candidate in range(start, end):
                    window = "\n".join(lines[candidate : candidate + len(old_lines)])
                    ratio = difflib.SequenceMatcher(None, old_text, window).ratio()
                    if ratio > best_ratio:
                        best_ratio = ratio
                        best_candidate = candidate

                if best_ratio > 0.85:
                    match_idx = best_candidate

        if match_idx == -1:
            sample = old_lines[:3]
            sample_str = "\n".join(f"  | {l}" for l in sample)
            if len(old_lines) > 3:
                sample_str += f"\n  | ... ({len(old_lines) - 3} more lines)"
            return (
                False,
                file_lines,
                (
                    f"Patch hunk #{idx + 1}/{len(file_patch.hunks)} failed to match any content in file "
                    f"({len(lines)} lines). Searched for {len(old_lines)}-line block starting with:\n{sample_str}"
                ),
            )

        # Replace matching lines
        lines[match_idx : match_idx + len(old_lines)] = new_lines
        shift = len(new_lines) - len(old_lines)
        line_offset += (match_idx - (hunk.old_start - 1)) + shift

    return True, lines, ""


# ==============================================================================
# SECTION 6: Core Tool Execution Logic
# ==============================================================================


def execute_tool(
    action: str,
    file_path: str,
    search: Optional[str] = None,
    replacement: Optional[str] = None,
    contents: Optional[str] = None,
    start_line: Optional[int] = None,
    end_line: Optional[int] = None,
    dry_run: bool = False,
    backup: bool = False,
    use_cache: bool = False,
    no_color: bool = False,
    verbose: bool = False,
    content: Optional[str] = None,
    patch: Optional[str] = None,
    **kwargs: Any,
) -> dict[str, Any]:
    """
    Core execution logic shared by run() and CLI parser.
    """
    start_time = time.monotonic()

    if contents is None:
        contents = content if content is not None else patch

    if verbose:
        logging.basicConfig(level=logging.DEBUG, format="[DEBUG] %(message)s")
        logging.debug(
            f"Initializing editor tool action '{action}' on path: {file_path}"
        )

    # Path traversal & sandbox validation
    target_path = Path(file_path).expanduser().resolve()
    action_lower = action.lower().strip()

    if action_lower not in {"view", "replace", "count", "patch", "write", "create"}:
        return {
            "success": False,
            "error": f"Unknown action '{action}'. Allowed actions: view, replace, count, patch, write, create.",
            "exit_code": EXIT_INVALID_INPUT,
            "duration_ms": 0.0,
        }

    # write/create actions can target non-existent files; all others require existence
    if action_lower not in {"write", "create"} and not target_path.exists():
        return {
            "success": False,
            "error": f"Target path does not exist: {file_path}",
            "exit_code": EXIT_FILE_NOT_FOUND,
            "duration_ms": 0.0,
        }

    # Optional Caching for read-only operations
    cache = ToolCache()
    cache_key = (
        f"{action_lower}:{target_path}:{search}:{start_line}:{end_line}:{dry_run}"
    )
    if use_cache and (
        action_lower in {"view", "count"} or (action_lower == "patch" and dry_run)
    ):
        cached_res = cache.get(cache_key, target_file=target_path)
        if cached_res is not None:
            if verbose:
                logging.debug("Cache hit! Returning cached editor response.")
            cached_res["cached"] = True
            return cached_res

    shutdown = GracefulShutdown()

    try:
        # --- ACTION: VIEW ---
        if action_lower == "view":
            if not target_path.is_file():
                return {
                    "success": False,
                    "error": f"Path is not a regular file: {file_path}",
                    "exit_code": EXIT_INVALID_INPUT,
                }

            content, enc_used, _ = _read_file_with_fallback(target_path)
            lines = content.splitlines()
            total_lines = len(lines)

            s_idx = (start_line - 1) if (start_line and start_line > 0) else 0
            e_idx = end_line if (end_line and end_line > 0) else total_lines

            sliced_lines = lines[s_idx:e_idx]
            view_content = "\n".join(sliced_lines)

            duration_ms = round((time.monotonic() - start_time) * 1000, 2)
            res = {
                "success": True,
                "action": "view",
                "file_path": str(target_path),
                "total_lines": total_lines,
                "view_start_line": s_idx + 1,
                "view_end_line": min(e_idx, total_lines),
                "content": view_content,
                "line_count": len(sliced_lines),
                "encoding_used": enc_used,
                "context": get_execution_context(),
                "cached": False,
                "exit_code": EXIT_SUCCESS,
                "duration_ms": duration_ms,
            }

            if use_cache:
                cache.set(cache_key, res, target_file=target_path)
            return res

        # --- ACTION: COUNT ---
        elif action_lower == "count":
            if not target_path.is_file():
                return {
                    "success": False,
                    "error": f"Path is not a regular file: {file_path}",
                    "exit_code": EXIT_INVALID_INPUT,
                }

            if not search:
                return {
                    "success": False,
                    "error": "Option '--search' is required for count action.",
                    "exit_code": EXIT_INVALID_INPUT,
                }

            content, enc_used, _ = _read_file_with_fallback(target_path)
            match_count = content.count(search)
            duration_ms = round((time.monotonic() - start_time) * 1000, 2)

            res = {
                "success": True,
                "action": "count",
                "file_path": str(target_path),
                "search": search,
                "match_count": match_count,
                "encoding_used": enc_used,
                "context": get_execution_context(),
                "cached": False,
                "exit_code": EXIT_SUCCESS,
                "duration_ms": duration_ms,
            }

            if use_cache:
                cache.set(cache_key, res, target_file=target_path)
            return res

        # --- ACTION: REPLACE ---
        elif action_lower == "replace":
            if not target_path.is_file():
                return {
                    "success": False,
                    "error": f"Path is not a regular file: {file_path}",
                    "exit_code": EXIT_INVALID_INPUT,
                }

            if not search:
                return {
                    "success": False,
                    "error": "Option '--search' is required for replace action.",
                    "exit_code": EXIT_INVALID_INPUT,
                }
            if replacement is None:
                return {
                    "success": False,
                    "error": "Option '--replacement' is required for replace action.",
                    "exit_code": EXIT_INVALID_INPUT,
                }

            content, encoding_used, newline_style = _read_file_with_fallback(
                target_path
            )

            match_count = content.count(search)
            if match_count == 0:
                duration_ms = round((time.monotonic() - start_time) * 1000, 2)
                return {
                    "success": True,
                    "action": "replace",
                    "file_path": str(target_path),
                    "search": search,
                    "replacement": replacement,
                    "replacements_made": 0,
                    "encoding_used": encoding_used,
                    "message": "Search string not found in target file; 0 replacements made.",
                    "cached": False,
                    "exit_code": EXIT_SUCCESS,
                    "duration_ms": duration_ms,
                }

            # Fast literal string substitution preserving line ending style
            new_content = content.replace(search, replacement)

            backup_sha256 = None
            if not dry_run:
                if backup:
                    backup_path = target_path.with_suffix(target_path.suffix + ".bak")
                    backup_bytes = content.encode(encoding_used)
                    backup_path.write_bytes(backup_bytes)
                    backup_sha256 = hashlib.sha256(backup_bytes).hexdigest()

                # Atomic swap write with sync and temp tracker
                temp_path = target_path.with_name(
                    f".{target_path.name}.tmp_{os.getpid()}_{int(time.time())}"
                )
                _ACTIVE_TEMP_FILES.add(temp_path)

                try:
                    with open(temp_path, "wb") as fh:
                        _acquire_file_lock(fh)
                        fh.write(new_content.encode(encoding_used))
                        fh.flush()
                        os.fdatasync(fh.fileno()) if hasattr(
                            os, "fdatasync"
                        ) else os.fsync(fh.fileno())
                        _release_file_lock(fh)

                    temp_path.replace(target_path)
                finally:
                    _ACTIVE_TEMP_FILES.discard(temp_path)

            duration_ms = round((time.monotonic() - start_time) * 1000, 2)

            return {
                "success": True,
                "action": "replace",
                "file_path": str(target_path),
                "search": search,
                "replacement": replacement,
                "replacements_made": match_count,
                "encoding_used": encoding_used,
                "dry_run": dry_run,
                "backup_created": backup and not dry_run,
                "backup_sha256": backup_sha256,
                "message": f"Successfully performed {match_count} replacement(s).",
                "cached": False,
                "exit_code": EXIT_SUCCESS,
                "duration_ms": duration_ms,
            }

        # --- ACTION: WRITE ---
        elif action_lower == "write":
            if contents is None:
                return {
                    "success": False,
                    "error": "Option '--contents' is required for write action.",
                    "exit_code": EXIT_INVALID_INPUT,
                    "duration_ms": 0.0,
                }

            target_path.parent.mkdir(parents=True, exist_ok=True)

            backup_sha256 = None
            if not dry_run:
                if backup and target_path.exists():
                    bak = target_path.with_suffix(target_path.suffix + ".bak")
                    bak_bytes = target_path.read_bytes()
                    bak.write_bytes(bak_bytes)
                    backup_sha256 = hashlib.sha256(bak_bytes).hexdigest()

                tmp = target_path.with_name(
                    f".{target_path.name}.tmp_{os.getpid()}_{int(time.time())}"
                )
                _ACTIVE_TEMP_FILES.add(tmp)
                try:
                    with open(tmp, "wb") as fh:
                        _acquire_file_lock(fh)
                        fh.write(contents.encode("utf-8"))
                        fh.flush()
                        os.fdatasync(fh.fileno()) if hasattr(
                            os, "fdatasync"
                        ) else os.fsync(fh.fileno())
                        _release_file_lock(fh)
                    tmp.replace(target_path)
                finally:
                    _ACTIVE_TEMP_FILES.discard(tmp)

            duration_ms = round((time.monotonic() - start_time) * 1000, 2)
            return {
                "success": True,
                "action": "write",
                "file_path": str(target_path),
                "written_bytes": len(contents.encode("utf-8")),
                "dry_run": dry_run,
                "backup_created": backup and not dry_run,
                "backup_sha256": backup_sha256,
                "cached": False,
                "exit_code": EXIT_SUCCESS,
                "duration_ms": duration_ms,
            }

        # --- ACTION: CREATE ---
        elif action_lower == "create":
            if target_path.exists():
                return {
                    "success": False,
                    "error": f"File already exists: {file_path}. Use 'write' to overwrite.",
                    "exit_code": EXIT_INVALID_INPUT,
                    "duration_ms": 0.0,
                }
            if contents is None:
                return {
                    "success": False,
                    "error": "Option '--contents' is required for create action.",
                    "exit_code": EXIT_INVALID_INPUT,
                    "duration_ms": 0.0,
                }

            target_path.parent.mkdir(parents=True, exist_ok=True)

            if not dry_run:
                tmp = target_path.with_name(
                    f".{target_path.name}.tmp_{os.getpid()}_{int(time.time())}"
                )
                _ACTIVE_TEMP_FILES.add(tmp)
                try:
                    with open(tmp, "wb") as fh:
                        _acquire_file_lock(fh)
                        fh.write(contents.encode("utf-8"))
                        fh.flush()
                        os.fdatasync(fh.fileno()) if hasattr(
                            os, "fdatasync"
                        ) else os.fsync(fh.fileno())
                        _release_file_lock(fh)
                    tmp.replace(target_path)
                finally:
                    _ACTIVE_TEMP_FILES.discard(tmp)

            duration_ms = round((time.monotonic() - start_time) * 1000, 2)
            return {
                "success": True,
                "action": "create",
                "file_path": str(target_path),
                "written_bytes": len(contents.encode("utf-8")),
                "dry_run": dry_run,
                "cached": False,
                "exit_code": EXIT_SUCCESS,
                "duration_ms": duration_ms,
            }

        # --- ACTION: PATCH ---
        elif action_lower == "patch":
            if not contents:
                return {
                    "success": False,
                    "error": "Option '--contents' (unified diff string) is required for patch action.",
                    "exit_code": EXIT_INVALID_INPUT,
                }

            file_patches = parse_unified_diff(contents)
            if not file_patches:
                return {
                    "success": False,
                    "error": "No valid unified diff hunks found in '--contents'.",
                    "exit_code": EXIT_INVALID_INPUT,
                }

            target_is_dir = target_path.is_dir()
            patched_files: list[str] = []
            diff_previews: list[str] = []
            total_added = 0
            total_removed = 0
            backup_sha256 = None

            for idx, fp in enumerate(file_patches):
                if shutdown.should_stop():
                    duration_ms = round((time.monotonic() - start_time) * 1000, 2)
                    return {
                        "success": False,
                        "error": "Patch operation cancelled by user signal.",
                        "exit_code": EXIT_INTERRUPTED,
                        "duration_ms": duration_ms,
                    }

                rel_file = fp.new_file or fp.old_file
                if target_is_dir:
                    actual_file_path = target_path / rel_file
                else:
                    actual_file_path = target_path

                if actual_file_path.exists():
                    orig_text, enc, newline_style = _read_file_with_fallback(
                        actual_file_path
                    )
                    orig_lines = orig_text.splitlines()
                else:
                    orig_lines = []
                    enc = "utf-8"
                    newline_style = "\n"

                ok, patched_lines, err_msg = apply_file_patch(orig_lines, fp)
                if not ok:
                    return {
                        "success": False,
                        "error": f"Failed patching {rel_file}: {err_msg}",
                        "exit_code": EXIT_ERROR,
                        "duration_ms": round((time.monotonic() - start_time) * 1000, 2),
                    }

                diff_seq = list(
                    difflib.unified_diff(
                        orig_lines,
                        patched_lines,
                        fromfile=f"a/{rel_file}",
                        tofile=f"b/{rel_file}",
                        lineterm="",
                    )
                )
                diff_str = "\n".join(diff_seq)
                if diff_str:
                    diff_previews.append(diff_str)
                    for d_line in diff_seq:
                        if d_line.startswith("+") and not d_line.startswith("+++"):
                            total_added += 1
                        elif d_line.startswith("-") and not d_line.startswith("---"):
                            total_removed += 1

                if not dry_run:
                    if backup and actual_file_path.exists():
                        bak = actual_file_path.with_suffix(
                            actual_file_path.suffix + ".bak"
                        )
                        bak_bytes = (
                            newline_style.join(orig_lines) + newline_style
                        ).encode(enc)
                        bak.write_bytes(bak_bytes)
                        backup_sha256 = hashlib.sha256(bak_bytes).hexdigest()

                    actual_file_path.parent.mkdir(parents=True, exist_ok=True)
                    tmp = actual_file_path.with_name(
                        f".{actual_file_path.name}.tmp_{os.getpid()}_{int(time.time())}"
                    )
                    _ACTIVE_TEMP_FILES.add(tmp)

                    try:
                        out_bytes = (
                            newline_style.join(patched_lines)
                            + (newline_style if patched_lines else "")
                        ).encode(enc)
                        with open(tmp, "wb") as fh:
                            _acquire_file_lock(fh)
                            fh.write(out_bytes)
                            fh.flush()
                            os.fdatasync(fh.fileno()) if hasattr(
                                os, "fdatasync"
                            ) else os.fsync(fh.fileno())
                            _release_file_lock(fh)

                        tmp.replace(actual_file_path)
                    finally:
                        _ACTIVE_TEMP_FILES.discard(tmp)

                patched_files.append(str(actual_file_path))

                if verbose and len(file_patches) > 1:
                    print_progress(
                        idx + 1,
                        len(file_patches),
                        f"Patched {rel_file}",
                        no_color=no_color,
                    )

            duration_ms = round((time.monotonic() - start_time) * 1000, 2)

            res = {
                "success": True,
                "action": "patch",
                "file_path": str(target_path),
                "target_type": "directory" if target_is_dir else "file",
                "patched_files": patched_files,
                "lines_added": total_added,
                "lines_removed": total_removed,
                "diff_preview": "\n".join(diff_previews),
                "dry_run": dry_run,
                "backup_created": backup and not dry_run,
                "backup_sha256": backup_sha256,
                "cached": False,
                "exit_code": EXIT_SUCCESS,
                "duration_ms": duration_ms,
            }

            if use_cache and dry_run:
                cache.set(cache_key, res, target_file=target_path)
            return res

    except PermissionError as exc:
        duration_ms = round((time.monotonic() - start_time) * 1000, 2)
        return {
            "success": False,
            "error": f"Permission denied accessing path: {exc}",
            "exit_code": EXIT_PERMISSION_DENIED,
            "duration_ms": duration_ms,
        }
    except Exception as exc:
        duration_ms = round((time.monotonic() - start_time) * 1000, 2)
        return {
            "success": False,
            "error": f"Operation failed: {exc}",
            "exit_code": EXIT_ERROR,
            "duration_ms": duration_ms,
        }


# ==============================================================================
# SECTION 7: Output Routing (LLM vs Human Terminal)
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
# SECTION 8: Function Entry Point for AIChat
# ==============================================================================


def run(
    action: Literal["view", "replace", "count", "patch", "write", "create"],
    file_path: str,
    search: Optional[str] = None,
    replacement: Optional[str] = None,
    contents: Optional[str] = None,
    start_line: Optional[int] = None,
    end_line: Optional[int] = None,
    dry_run: bool = False,
    backup: bool = False,
    use_cache: bool = False,
    no_color: bool = False,
    verbose: bool = False,
    patch: Optional[str] = None,
    content: Optional[str] = None,
    **kwargs: Any,
) -> dict[str, Any]:
    """Execute the file patch & string editor tool with specified parameters."""
    effective_contents = contents if contents is not None else patch
    if effective_contents is None and content is not None and action == "patch":
        effective_contents = content

    result = execute_tool(
        action=action,
        file_path=file_path,
        search=search,
        replacement=replacement,
        contents=contents,
        start_line=start_line,
        end_line=end_line,
        dry_run=dry_run,
        backup=backup,
        use_cache=use_cache,
        no_color=no_color,
        verbose=verbose,
        content=content,
        patch=patch,
        **kwargs,
    )

    # 1. Render interactive colorized UI for terminal users
    print_human_readable_ui(result, no_color=no_color)

    # 2. Write structured JSON to LLM_OUTPUT
    write_llm_output(result)

    return result


# ==============================================================================
# SECTION 9: CLI Argument Parser
# ==============================================================================


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="file_patch_editor.py",
        description=f"AIChat File Patch & String Editor Tool v{__version__}",
    )
    parser.add_argument(
        "--action",
        "-a",
        required=True,
        choices=["view", "replace", "count", "patch", "write", "create"],
        help="Operation to perform: view, replace, count, patch, write, create (required)",
    )
    parser.add_argument(
        "--file-path",
        "-f",
        required=True,
        dest="file_path",
        metavar="PATH",
        help="Path to the target file or directory (required)",
    )
    parser.add_argument(
        "--search",
        "-s",
        metavar="TEXT",
        help="Search string (literal string matching; required for replace/count)",
    )
    parser.add_argument(
        "--replacement",
        "-r",
        metavar="TEXT",
        help="Replacement string (literal replacement; required for replace)",
    )
    parser.add_argument(
        "--contents",
        "-c",
        metavar="TEXT",
        help="Unified diff patch contents to apply (required for patch)",
    )
    parser.add_argument(
        "--start-line",
        type=int,
        dest="start_line",
        metavar="NUM",
        help="Starting line number for view operation (1-based index)",
    )
    parser.add_argument(
        "--end-line",
        type=int,
        dest="end_line",
        metavar="NUM",
        help="Ending line number for view operation",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        default=False,
        dest="dry_run",
        help="Preview changes without modifying target files",
    )
    parser.add_argument(
        "--backup",
        action="store_true",
        default=False,
        help="Create backup files (.bak) before modifying files",
    )
    parser.add_argument(
        "--use-cache",
        action="store_true",
        default=False,
        dest="use_cache",
        help="Enable result caching for read-only operations",
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
        action=args.action,
        file_path=args.file_path,
        search=args.search,
        replacement=args.replacement,
        contents=args.contents,
        start_line=args.start_line,
        end_line=args.end_line,
        dry_run=args.dry_run,
        backup=args.backup,
        use_cache=args.use_cache,
        no_color=args.no_color,
        verbose=args.verbose,
    )

    # Output rendering
    print_human_readable_ui(res, no_color=args.no_color)
    write_llm_output(res)
    sys.exit(res.get("exit_code", EXIT_SUCCESS))
