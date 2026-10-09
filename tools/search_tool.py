#!/usr/bin/env python3
# ==============================================================================
# search_tool.py — Pyrmethus AIChat Search Tool (Find & Grep Engine)
# argc/aichat compatible · Colorized UI · Safe Caching · Agent CWD Resolution
#
# @describe Fast, full-featured CLI search tool combining find and grep capabilities with regex pattern matching, context lines, depth controls, and LLM output routing.
#
# @meta require-tools aichat
#
# @option --target! <PATH>               Target file or directory path (required)
# @option --query <PATTERN>              Regex pattern or text query to search inside file contents
# @option --file-pattern <PATTERN>       Filename glob filter (e.g. *.py, *.md, config_*.json)
# @option --max-depth <NUM>              Maximum directory recursion depth
# @option --limit <NUM>                  Maximum matching lines/files to return (default: 100)
# @option --context-lines <NUM>          Number of context lines before/after match (default: 0)
# @option --timeout <SEC>                Maximum execution timeout in seconds
# @option --mode <MODE>                  Output detail mode: summary/detailed (default: summary)
# @option --env-var <KEY=VALUE>          Custom environment variable (repeatable)
# @flag   --case-sensitive               Enable case-sensitive regex matching
# @flag   --include-hidden               Include hidden files and dot-directories (.git, .env, etc.)
# @flag   --files-only                   Return matching file paths only (skip line matches)
# @flag   --use-cache                    Enable result caching for expensive search queries
# @flag   --clear-cache                  Clear cache directory and exit
# @flag   --schema                       Print JSON Tool Schema for LLM registration and exit
# @flag   --no-color                     Disable ANSI color output
# @flag   --verbose                      Enable detailed debug log output
#
# @env LLM_OUTPUT=/dev/stdout            Output path for LLM integration
# @env LLM_TOOL_MODE=summary             Default execution mode override
# @env LLM_TOOL_LIMIT=100                Default processing limit override
# ==============================================================================

from __future__ import annotations

import argparse
import dataclasses
import fnmatch
import hashlib
import json
import logging
import os
import platform
import re
import signal
import sys
import time
from datetime import date, datetime, timedelta
from enum import Enum
from pathlib import Path
from typing import Any, Literal, Optional

__version__ = "2.6.0"
__all__ = [
    "run",
    "execute_tool",
    "main",
    "validate_inputs",
    "build_cache_key",
    "invalidate_cache",
    "generate_tool_schema",
    "ToolCache",
    "ToolError",
    "GracefulShutdown",
    "get_agent_var",
    "get_builtin_var",
    "get_execution_context",
    "__version__",
]

# ==============================================================================
# SECTION 1: Exit Codes, Validation Rules & Exception Models
# ==============================================================================

EXIT_SUCCESS = 0
EXIT_ERROR = 1
EXIT_FILE_NOT_FOUND = 2
EXIT_TIMEOUT = 124
EXIT_PERMISSION_DENIED = 126
EXIT_INVALID_INPUT = 127
EXIT_INTERRUPTED = 130

VALID_MODES = {"summary", "detailed"}


class ExecutionMode(str, Enum):
    SUMMARY = "summary"
    DETAILED = "detailed"


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


def validate_inputs(
    target: Optional[str],
    mode: str,
    limit: Optional[int],
    query: Optional[str] = None,
    max_depth: Optional[int] = None,
    timeout: Optional[float] = None,
) -> Optional[dict[str, Any]]:
    """Validate input parameters strictly before processing."""
    if not target or not target.strip():
        return {
            "success": False,
            "error": "Target path is required.",
            "exit_code": EXIT_INVALID_INPUT,
            "duration_ms": 0.0,
        }
    if mode not in VALID_MODES:
        return {
            "success": False,
            "error": f"Invalid mode '{mode}'. Allowed choices: {sorted(list(VALID_MODES))}",
            "exit_code": EXIT_INVALID_INPUT,
            "duration_ms": 0.0,
        }
    if limit is not None and limit <= 0:
        return {
            "success": False,
            "error": f"Invalid limit '{limit}'. Limit must be > 0.",
            "exit_code": EXIT_INVALID_INPUT,
            "duration_ms": 0.0,
        }
    if max_depth is not None and max_depth < 0:
        return {
            "success": False,
            "error": f"Invalid max_depth '{max_depth}'. Depth must be >= 0.",
            "exit_code": EXIT_INVALID_INPUT,
            "duration_ms": 0.0,
        }
    if timeout is not None and timeout <= 0:
        return {
            "success": False,
            "error": f"Invalid timeout '{timeout}'. Timeout must be > 0 seconds.",
            "exit_code": EXIT_INVALID_INPUT,
            "duration_ms": 0.0,
        }
    if query:
        try:
            re.compile(query)
        except re.error as err:
            return {
                "success": False,
                "error": f"Invalid search query regex '{query}': {err}",
                "exit_code": EXIT_INVALID_INPUT,
                "duration_ms": 0.0,
            }
    return None


class ToolJSONEncoder(json.JSONEncoder):
    """Resilient JSON encoder handling Path, Enum, datetime, timedelta, bytes, sets, and dataclasses."""

    def default(self, obj: Any) -> Any:
        try:
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
            if dataclasses.is_dataclass(obj):
                return dataclasses.asdict(obj)
        except Exception:
            pass
        return repr(obj)


# ==============================================================================
# SECTION 2: Terminal Colors & UI Display Helpers
# ==============================================================================

NEON_CYAN    = "\033[38;5;51m"
NEON_GREEN   = "\033[38;5;46m"
NEON_RED     = "\033[38;5;196m"
NEON_YELLOW  = "\033[38;5;226m"
NEON_PURPLE  = "\033[38;5;129m"
NEON_PINK    = "\033[38;5;198m"
RESET        = "\033[0m"
BOLD         = "\033[1m"
DIM          = "\033[2m"

_ANSI_RE = re.compile(r"\x1b(?:[@-Z\\-_]|\[[0-?]*[ -/]*[@-~])|\033\[[0-9;?]*[a-zA-Z]")


def _strip_ansi(text: str) -> str:
    """Remove ANSI color escape sequences."""
    return _ANSI_RE.sub("", text)


def _is_tty(no_color: bool = False) -> bool:
    """Return True if colors should be enabled (stderr is TTY and NO_COLOR is not set)."""
    if no_color or os.environ.get("NO_COLOR"):
        return False
    return sys.stderr.isatty() and os.environ.get("TERM", "").lower() not in ("dumb", "")


def _cprint(text: str, file: Any = None, no_color: bool = False, end: str = "\n") -> None:
    """Print pre-formatted ANSI text safely."""
    target = file or sys.stderr
    if not _is_tty(no_color=no_color):
        text = _strip_ansi(text)
    print(text, file=target, flush=True, end=end)


def print_progress(current: int, total: int, message: str = "", no_color: bool = False) -> None:
    """Render interactive progress bar on stderr."""
    if not _is_tty(no_color=no_color):
        return
    percent = (current / total) * 100.0 if total > 0 else 100.0
    bar_width = 30
    filled = int(bar_width * percent / 100.0)
    bar = "█" * filled + "░" * (bar_width - filled)

    _cprint(
        f"\r{NEON_CYAN}Search Progress:{RESET} [{NEON_GREEN}{bar}{RESET}] {percent:.1f}% {message}",
        end="",
        no_color=no_color,
    )
    if current >= total:
        _cprint("", no_color=no_color)


def print_human_readable_ui(data: dict[str, Any], no_color: bool = False) -> None:
    """Render human-friendly box UI with match highlights to stderr."""
    if not _is_tty(no_color=no_color):
        return

    success = data.get("success", False)
    status_color = NEON_GREEN if success else NEON_RED
    status_symbol = "✓" if success else "✗"
    status_text = "SUCCESS" if success else "FAILED"

    box_w = 72
    border = "─" * box_w

    _cprint(f"{NEON_PURPLE}╭{border}╮{RESET}", no_color=no_color)
    _cprint(f"{NEON_PURPLE}│{RESET} {NEON_PINK}🔍 [SEARCH TOOL v{__version__}]{RESET} {status_color}{BOLD}{status_symbol} {status_text}{RESET}", no_color=no_color)
    _cprint(f"{NEON_PURPLE}├{border}┤{RESET}", no_color=no_color)
    _cprint(f"{NEON_PURPLE}│{RESET} {NEON_CYAN}Target Path:{RESET} {data.get('target', 'N/A')}", no_color=no_color)
    _cprint(f"{NEON_PURPLE}│{RESET} {NEON_CYAN}Text Query:{RESET}  {NEON_YELLOW}{data.get('query') or '<Filename Find Only>'}{RESET}", no_color=no_color)
    _cprint(f"{NEON_PURPLE}│{RESET} {NEON_CYAN}Pattern:{RESET}     {data.get('file_pattern', '*')}", no_color=no_color)
    _cprint(f"{NEON_PURPLE}│{RESET} {NEON_CYAN}Files Read:{RESET}  {data.get('files_scanned', 0)} | {NEON_CYAN}Total Matches:{RESET} {NEON_YELLOW}{data.get('match_count', 0)}{RESET}", no_color=no_color)
    _cprint(f"{NEON_PURPLE}│{RESET} {NEON_CYAN}Cached:{RESET}      {data.get('cached', False)} | {NEON_CYAN}Duration:{RESET} {DIM}{data.get('duration_ms', 0)}ms{RESET}", no_color=no_color)

    if not success and "error" in data:
        _cprint(f"{NEON_PURPLE}├{border}┤{RESET}", no_color=no_color)
        _cprint(f"{NEON_PURPLE}│{RESET} {NEON_RED}Error:{RESET} {data['error']}", no_color=no_color)

    matches = data.get("matches", [])
    if matches:
        _cprint(f"{NEON_PURPLE}├{border}┤{RESET}", no_color=no_color)
        _cprint(f"{NEON_PURPLE}│{RESET} {BOLD}Search Snippets ({len(matches)} preview items):{RESET}", no_color=no_color)
        for idx, item in enumerate(matches[:15]):
            if isinstance(item, dict):
                fpath = item.get("file", "unknown")
                lno = item.get("line_num", "?")
                text = item.get("line", "").strip()
                _cprint(f"{NEON_PURPLE}│{RESET} {NEON_GREEN}{fpath}{RESET}:{NEON_YELLOW}{lno}{RESET} ── {text[:80]}", no_color=no_color)
            else:
                _cprint(f"{NEON_PURPLE}│{RESET} {NEON_CYAN}›{RESET} {item}", no_color=no_color)

        if len(matches) > 15:
            _cprint(f"{NEON_PURPLE}│{RESET}   {DIM}... and {len(matches) - 15} more matches{RESET}", no_color=no_color)

    _cprint(f"{NEON_PURPLE}╰{border}╯{RESET}", no_color=no_color)


# ==============================================================================
# SECTION 3: Agent Environment & Path Resolution Helpers
# ==============================================================================

def get_agent_var(name: str, default: str = "") -> str:
    """Access agent user-defined variables (LLM_AGENT_VAR_<NAME>)."""
    return os.environ.get(f"LLM_AGENT_VAR_{name.upper()}", default)


def get_builtin_var(name: str) -> Optional[str]:
    """Access agent built-in environment variables (e.g., __cwd__, __os__)."""
    return os.environ.get(f"LLM_AGENT_VAR_{name}")


def get_execution_context() -> dict[str, Any]:
    """Gather complete agent execution context."""
    termux_prefix = os.environ.get("PREFIX", "")
    return {
        "tool_name": os.environ.get("LLM_TOOL_NAME", "search_tool"),
        "cache_dir": os.environ.get("LLM_TOOL_CACHE_DIR"),
        "root_dir": os.environ.get("LLM_ROOT_DIR"),
        "output_path": os.environ.get("LLM_OUTPUT"),
        "cwd": get_builtin_var("__cwd__") or os.getcwd(),
        "platform": platform.system(),
        "python_version": platform.python_version(),
        "termux_prefix": termux_prefix,
        "is_termux": "com.termux" in termux_prefix or Path("/data/data/com.termux").exists(),
    }


def resolve_agent_path(target_str: str) -> Path:
    """Resolve target path relative to Agent CWD (__cwd__) if relative."""
    raw_path = Path(target_str).expanduser()
    if raw_path.is_absolute():
        return raw_path.resolve(strict=False)

    agent_cwd = get_builtin_var("__cwd__")
    if agent_cwd:
        return (Path(agent_cwd) / raw_path).resolve(strict=False)

    return raw_path.resolve(strict=False)


def env_default(env_name: str, fallback: Any = None) -> dict[str, Any]:
    """Provide argparse default options from environment variables."""
    val = os.getenv(env_name)
    if val is not None:
        return {"default": val}
    if fallback is not None:
        return {"default": fallback}
    return {}


def _parse_env_vars(env_vars: Optional[list[str]]) -> dict[str, str]:
    """Parse KEY=VALUE strings into dictionary."""
    if not env_vars:
        return {}
    parsed: dict[str, str] = {}
    for item in env_vars:
        if "=" in item:
            k, v = item.split("=", 1)
            parsed[k.strip()] = v.strip()
    return parsed


def _apply_env_vars(env_vars: dict[str, str]) -> dict[str, str]:
    """Return runtime environment dictionary containing overrides."""
    merged = os.environ.copy()
    merged.update(env_vars)
    return merged


# ==============================================================================
# SECTION 4: Cache Management, Signal Handling & Tool Schema
# ==============================================================================

def build_cache_key(
    target_path: Path,
    mtime: float,
    query: Optional[str],
    file_pattern: Optional[str],
    case_sensitive: bool,
    max_depth: Optional[int],
    limit_val: int,
) -> str:
    """Construct pipe-delimited cache key."""
    return "|".join([
        __version__,
        str(target_path),
        str(mtime),
        query or "",
        file_pattern or "",
        str(case_sensitive),
        str(max_depth or -1),
        str(limit_val),
    ])


class ToolCache:
    """Safe, JSON file-backed caching utility with TTL support."""

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

    def _hash_key(self, key_str: str) -> str:
        return hashlib.sha256(key_str.encode("utf-8")).hexdigest()

    def get(self, key_str: str, ttl_seconds: int = 3600) -> Optional[dict[str, Any]]:
        cache_file = self.cache_dir / f"{self._hash_key(key_str)}.json"
        if not cache_file.exists():
            return None
        try:
            if time.time() - cache_file.stat().st_mtime > ttl_seconds:
                cache_file.unlink(missing_ok=True)
                return None
            with open(cache_file, "r", encoding="utf-8") as fp:
                data = json.load(fp)
                if isinstance(data, dict):
                    return data
                return None
        except Exception:
            return None

    def set(self, key_str: str, value: Any) -> None:
        cache_file = self.cache_dir / f"{self._hash_key(key_str)}.json"
        tmp_file = cache_file.with_suffix(".tmp")
        try:
            payload = json.dumps(value, cls=ToolJSONEncoder, ensure_ascii=False)
            with open(tmp_file, "w", encoding="utf-8") as fp:
                fp.write(payload)
            tmp_file.replace(cache_file)
        except Exception:
            if tmp_file.exists():
                tmp_file.unlink(missing_ok=True)


def invalidate_cache(cache: Optional[ToolCache] = None, prefix: str = "") -> int:
    """Remove cached results from disk."""
    cache_obj = cache or ToolCache()
    removed = 0
    if not cache_obj.cache_dir.exists():
        return removed
    for file in cache_obj.cache_dir.glob("*.json"):
        if not prefix or file.name.startswith(prefix):
            try:
                file.unlink(missing_ok=True)
                removed += 1
            except OSError:
                pass
    return removed


class GracefulShutdown:
    """Context manager for intercepting termination signals cleanly."""

    def __init__(self) -> None:
        self.interrupted = False
        self._old_sigint = None
        self._old_sigterm = None

    def __enter__(self) -> GracefulShutdown:
        self.interrupted = False
        try:
            self._old_sigint = signal.signal(signal.SIGINT, self._handle_signal)
            self._old_sigterm = signal.signal(signal.SIGTERM, self._handle_signal)
        except (ValueError, AttributeError):
            pass
        return self

    def __exit__(self, exc_type: Any, exc_val: Any, exc_tb: Any) -> None:
        self.restore()

    def _handle_signal(self, signum: int, frame: Any) -> None:
        self.interrupted = True

    def restore(self) -> None:
        if getattr(self, "_old_sigint", None) is not None:
            try:
                signal.signal(signal.SIGINT, self._old_sigint)
            except (ValueError, AttributeError):
                pass
        if getattr(self, "_old_sigterm", None) is not None:
            try:
                signal.signal(signal.SIGTERM, self._old_sigterm)
            except (ValueError, AttributeError):
                pass

    def should_stop(self) -> bool:
        return getattr(self, "interrupted", False)


def generate_tool_schema() -> dict[str, Any]:
    """Generate OpenAI/AIChat Function Tool Schema definition."""
    return {
        "name": "search_tool",
        "description": "Powerful CLI search tool (Find + Grep Engine) supporting regex text search, filename patterns, context lines, and depth limits.",
        "parameters": {
            "type": "object",
            "properties": {
                "target": {
                    "type": "string",
                    "description": "Target file or directory path (required)"
                },
                "query": {
                    "type": "string",
                    "description": "Regex pattern or text string to search inside files (if omitted, performs filename search)"
                },
                "file_pattern": {
                    "type": "string",
                    "description": "Filename glob pattern filter (e.g. *.py, *.md, config_*.json)"
                },
                "max_depth": {
                    "type": "integer",
                    "description": "Maximum directory traversal depth"
                },
                "limit": {
                    "type": "integer",
                    "description": "Maximum match items to return (default: 100)"
                },
                "context_lines": {
                    "type": "integer",
                    "description": "Number of context lines before and after match (default: 0)"
                },
                "timeout": {
                    "type": "number",
                    "description": "Maximum execution timeout in seconds"
                },
                "mode": {
                    "type": "string",
                    "enum": ["summary", "detailed"],
                    "description": "Detail mode for JSON output (default: summary)"
                },
                "case_sensitive": {
                    "type": "boolean",
                    "description": "Enable case-sensitive regex matching"
                },
                "include_hidden": {
                    "type": "boolean",
                    "description": "Include hidden files and dot-directories (.git, .env, etc.)"
                },
                "files_only": {
                    "type": "boolean",
                    "description": "Return matching file paths only, hiding line snippets"
                },
                "use_cache": {
                    "type": "boolean",
                    "description": "Enable result caching for expensive searches"
                }
            },
            "required": ["target"]
        }
    }


# ==============================================================================
# SECTION 5: Core Search Engine (Find & Grep Helper)
# ==============================================================================

def _is_binary_file(file_path: Path) -> bool:
    """Fast check if file is binary by inspecting first 1024 bytes."""
    try:
        with open(file_path, "rb") as fp:
            chunk = fp.read(1024)
            return b"\x00" in chunk
    except OSError:
        return True


def _search_file_content(
    file_path: Path,
    regex: re.Pattern[str],
    context_lines: int = 0,
    files_only: bool = False,
    max_matches: int = 100,
) -> tuple[int, list[dict[str, Any]]]:
    """Search file contents for regex matches and extract context lines."""
    matches: list[dict[str, Any]] = []
    if _is_binary_file(file_path):
        return 0, matches

    try:
        with open(file_path, "r", encoding="utf-8", errors="replace") as fp:
            lines = fp.readlines()
    except OSError:
        return 0, matches

    file_matches = 0
    total_lines = len(lines)

    for i, line in enumerate(lines):
        if regex.search(line):
            file_matches += 1
            if files_only:
                matches.append({"file": str(file_path), "matched": True})
                break

            start_ctx = max(0, i - context_lines)
            end_ctx = min(total_lines, i + context_lines + 1)

            ctx_before = [lines[idx].rstrip("\r\n") for idx in range(start_ctx, i)]
            ctx_after = [lines[idx].rstrip("\r\n") for idx in range(i + 1, end_ctx)]

            matches.append({
                "file": str(file_path),
                "line_num": i + 1,
                "line": line.rstrip("\r\n"),
                "context_before": ctx_before,
                "context_after": ctx_after,
            })

            if len(matches) >= max_matches:
                break

    return file_matches, matches


def execute_tool(
    target: str,
    query: Optional[str] = None,
    file_pattern: Optional[str] = None,
    max_depth: Optional[int] = None,
    limit: Optional[int] = None,
    context_lines: int = 0,
    timeout: Optional[float] = None,
    mode: str = "summary",
    env_vars: Optional[list[str]] = None,
    case_sensitive: bool = False,
    include_hidden: bool = False,
    files_only: bool = False,
    use_cache: bool = False,
    no_color: bool = False,
    verbose: bool = False,
) -> dict[str, Any]:
    """Execute find/grep search engine and return structured result dictionary."""
    start_time = time.monotonic()

    # Step 1: Input Validation
    bad_inputs = validate_inputs(target, mode, limit, query=query, max_depth=max_depth, timeout=timeout)
    if bad_inputs:
        return bad_inputs

    if verbose:
        try:
            logging.basicConfig(level=logging.DEBUG, format="[DEBUG] %(message)s", force=True)
        except TypeError:
            logging.basicConfig(level=logging.DEBUG, format="[DEBUG] %(message)s")
        logging.debug(f"Starting search operation on target: {target}")

    parsed_env = _parse_env_vars(env_vars)
    _apply_env_vars(parsed_env)
    limit_val = limit if limit is not None else 100

    # Resolve target path relative to Agent CWD if relative
    target_path = resolve_agent_path(target)

    if not target_path.exists():
        return {
            "success": False,
            "error": f"Target path does not exist: {target} (Resolved: {target_path})",
            "exit_code": EXIT_FILE_NOT_FOUND,
            "duration_ms": 0.0,
        }

    try:
        mtime = target_path.stat().st_mtime
    except OSError:
        mtime = 0.0

    cache = ToolCache()
    cache_key = build_cache_key(target_path, mtime, query, file_pattern, case_sensitive, max_depth, limit_val)

    if use_cache:
        cached_result = cache.get(cache_key)
        if cached_result is not None:
            cached_result["cached"] = True
            return cached_result

    # Compile regex if content search query is given
    compiled_regex: Optional[re.Pattern[str]] = None
    if query:
        flags = 0 if case_sensitive else re.IGNORECASE
        compiled_regex = re.compile(query, flags)

    matched_items: list[Any] = []
    files_scanned = 0
    total_matches = 0

    try:
        with GracefulShutdown() as shutdown:
            if target_path.is_file():
                files_scanned += 1
                if compiled_regex:
                    count, file_results = _search_file_content(
                        target_path,
                        compiled_regex,
                        context_lines=context_lines,
                        files_only=files_only,
                        max_matches=limit_val,
                    )
                    total_matches += count
                    matched_items.extend(file_results)
                else:
                    total_matches += 1
                    matched_items.append(str(target_path))
            else:
                # Directory Traversal (Find + Grep Engine)
                pattern = file_pattern or "*"
                
                for root, dirs, files in os.walk(target_path):
                    if timeout and (time.monotonic() - start_time) > timeout:
                        raise ToolError(f"Search operation timed out after {timeout}s", EXIT_TIMEOUT)

                    if shutdown.should_stop():
                        raise ToolError("Search interrupted by user signal.", EXIT_INTERRUPTED)

                    current_root = Path(root)

                    # Hidden directory filtering
                    if not include_hidden:
                        dirs[:] = [d for d in dirs if not d.startswith(".")]

                    # Depth calculation & enforcement
                    try:
                        rel_parts = current_root.relative_to(target_path).parts
                        curr_depth = len(rel_parts)
                    except ValueError:
                        curr_depth = 0

                    if max_depth is not None and curr_depth > max_depth:
                        dirs.clear()
                        continue

                    # Process files in directory
                    for fname in files:
                        if not include_hidden and fname.startswith("."):
                            continue

                        if not fnmatch.fnmatch(fname, pattern):
                            continue

                        fpath = current_root / fname
                        files_scanned += 1

                        if compiled_regex:
                            count, file_results = _search_file_content(
                                fpath,
                                compiled_regex,
                                context_lines=context_lines,
                                files_only=files_only,
                                max_matches=limit_val - len(matched_items),
                            )
                            if count > 0:
                                total_matches += count
                                if files_only:
                                    matched_items.append(str(fpath))
                                else:
                                    matched_items.extend(file_results)
                        else:
                            total_matches += 1
                            matched_items.append(str(fpath))

                        if len(matched_items) >= limit_val:
                            break

                    if len(matched_items) >= limit_val:
                        break

                    if verbose and files_scanned % 50 == 0:
                        print_progress(len(matched_items), limit_val, f"Scanned {files_scanned} files...", no_color=no_color)

        duration_ms = round((time.monotonic() - start_time) * 1000, 2)

        result: dict[str, Any] = {
            "success": True,
            "target": str(target_path),
            "query": query,
            "file_pattern": file_pattern or "*",
            "files_scanned": files_scanned,
            "match_count": total_matches,
            "returned_count": len(matched_items),
            "matches": matched_items if mode == "detailed" else matched_items[:20],
            "mode": mode,
            "context": get_execution_context(),
            "cached": False,
            "duration_ms": duration_ms,
            "exit_code": EXIT_SUCCESS,
        }

        if use_cache:
            cache.set(cache_key, result)

        return result

    except ToolError as exc:
        duration_ms = round((time.monotonic() - start_time) * 1000, 2)
        return {
            "success": False,
            "error": exc.message,
            "exit_code": exc.exit_code,
            "duration_ms": duration_ms,
        }
    except PermissionError as exc:
        duration_ms = round((time.monotonic() - start_time) * 1000, 2)
        return {
            "success": False,
            "error": f"Permission denied searching path: {exc}",
            "exit_code": EXIT_PERMISSION_DENIED,
            "duration_ms": duration_ms,
        }
    except Exception as exc:
        duration_ms = round((time.monotonic() - start_time) * 1000, 2)
        return {
            "success": False,
            "error": f"Search engine execution error: {exc}",
            "exit_code": EXIT_ERROR,
            "duration_ms": duration_ms,
        }


# ==============================================================================
# SECTION 6: Output Routing (JSON Lines Formatting)
# ==============================================================================

def write_llm_output(data: dict[str, Any]) -> None:
    """Write single-line JSON payload to stdout or specified LLM_OUTPUT path."""
    out_path = os.environ.get("LLM_OUTPUT", "/dev/stdout")
    payload = json.dumps(data, ensure_ascii=False, cls=ToolJSONEncoder)

    if out_path in {"/dev/stdout", "/dev/fd/1", "-"}:
        sys.stdout.write(payload + "\n")
        sys.stdout.flush()
        return

    try:
        target_file = Path(out_path)
        target_file.parent.mkdir(parents=True, exist_ok=True)
        with open(target_file, "a", encoding="utf-8") as fp:
            fp.write(payload + "\n")
    except OSError as err:
        sys.stderr.write(f"Failed writing to LLM_OUTPUT '{out_path}': {err}\n")
        sys.stdout.write(payload + "\n")
        sys.stdout.flush()


# ==============================================================================
# SECTION 7: Function Entry Point for AIChat
# ==============================================================================

def run(
    target: str,
    query: Optional[str] = None,
    file_pattern: Optional[str] = None,
    max_depth: Optional[int] = None,
    limit: Optional[int] = None,
    context_lines: int = 0,
    timeout: Optional[float] = None,
    mode: Literal["summary", "detailed"] = "summary",
    env_vars: Optional[list[str]] = None,
    case_sensitive: bool = False,
    include_hidden: bool = False,
    files_only: bool = False,
    use_cache: bool = False,
    no_color: bool = False,
    verbose: bool = False,
) -> None:
    """AIChat Tool entrypoint function."""
    result = execute_tool(
        target=target,
        query=query,
        file_pattern=file_pattern,
        max_depth=max_depth,
        limit=limit,
        context_lines=context_lines,
        timeout=timeout,
        mode=mode,
        env_vars=env_vars,
        case_sensitive=case_sensitive,
        include_hidden=include_hidden,
        files_only=files_only,
        use_cache=use_cache,
        no_color=no_color,
        verbose=verbose,
    )

    print_human_readable_ui(result, no_color=no_color)
    write_llm_output(result)


# ==============================================================================
# SECTION 8: CLI Parser & Main Entrypoint
# ==============================================================================

def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="search_tool.py",
        description=f"AIChat CLI Find & Grep Search Tool v{__version__}",
    )
    parser.add_argument(
        "--target", "-t",
        required=False,
        metavar="PATH",
        help="Target file or directory path",
    )
    parser.add_argument(
        "--query", "-q",
        metavar="PATTERN",
        help="Regex pattern or text string to search inside file contents",
    )
    parser.add_argument(
        "--file-pattern", "-f",
        dest="file_pattern",
        metavar="PATTERN",
        help="Filename glob pattern filter (e.g. *.py, *.md)",
    )
    parser.add_argument(
        "--max-depth", "-d",
        type=int,
        dest="max_depth",
        metavar="NUM",
        help="Maximum directory traversal depth",
    )
    parser.add_argument(
        "--limit", "-l",
        type=int,
        **env_default("LLM_TOOL_LIMIT", 100),
        help="Maximum match items to return (default: 100)",
    )
    parser.add_argument(
        "--context-lines", "-c",
        type=int,
        default=0,
        dest="context_lines",
        metavar="NUM",
        help="Number of context lines before/after match (default: 0)",
    )
    parser.add_argument(
        "--timeout",
        type=float,
        help="Maximum execution timeout in seconds",
    )
    parser.add_argument(
        "--mode",
        choices=["summary", "detailed"],
        **env_default("LLM_TOOL_MODE", "summary"),
        help="Execution mode (default: summary)",
    )
    parser.add_argument(
        "--env-var",
        action="append",
        dest="env_vars",
        metavar="KEY=VALUE",
        help="Custom environment variable (repeatable)",
    )
    parser.add_argument(
        "--case-sensitive", "-s",
        action="store_true",
        default=False,
        dest="case_sensitive",
        help="Enable case-sensitive matching",
    )
    parser.add_argument(
        "--include-hidden", "-a",
        action="store_true",
        default=False,
        dest="include_hidden",
        help="Include hidden files and dot-directories",
    )
    parser.add_argument(
        "--files-only",
        action="store_true",
        default=False,
        dest="files_only",
        help="Return matching file paths only",
    )
    parser.add_argument(
        "--use-cache",
        action="store_true",
        default=False,
        dest="use_cache",
        help="Enable result caching for expensive searches",
    )
    parser.add_argument(
        "--clear-cache",
        action="store_true",
        default=False,
        dest="clear_cache",
        help="Clear search tool cache directory and exit",
    )
    parser.add_argument(
        "--schema",
        action="store_true",
        default=False,
        help="Print JSON Tool Schema for LLM registration and exit",
    )
    parser.add_argument(
        "--no-color",
        action="store_true",
        default=False,
        dest="no_color",
        help="Disable ANSI color output",
    )
    parser.add_argument(
        "--verbose", "-v",
        action="store_true",
        default=False,
        help="Enable detailed debug logging",
    )
    return parser


def main() -> int:
    """CLI execution entrypoint."""
    args = _build_parser().parse_args()

    if args.schema:
        schema = generate_tool_schema()
        sys.stdout.write(json.dumps(schema, indent=2) + "\n")
        sys.stdout.flush()
        return EXIT_SUCCESS

    if args.clear_cache:
        removed = invalidate_cache()
        _cprint(f"{NEON_GREEN}Cleared {removed} cache file(s).{RESET}", no_color=args.no_color)
        return EXIT_SUCCESS

    if not args.target:
        _cprint(f"{NEON_RED}Error: --target parameter is required for execution.{RESET}", no_color=args.no_color)
        return EXIT_INVALID_INPUT

    res = execute_tool(
        target=args.target,
        query=args.query,
        file_pattern=args.file_pattern,
        max_depth=args.max_depth,
        limit=args.limit,
        context_lines=args.context_lines,
        timeout=args.timeout,
        mode=args.mode,
        env_vars=args.env_vars,
        case_sensitive=args.case_sensitive,
        include_hidden=args.include_hidden,
        files_only=args.files_only,
        use_cache=args.use_cache,
        no_color=args.no_color,
        verbose=args.verbose,
    )

    print_human_readable_ui(res, no_color=args.no_color)
    write_llm_output(res)
    return res.get("exit_code", EXIT_SUCCESS)


if __name__ == "__main__":
    sys.exit(main())
