#!/usr/bin/env python3
# ==============================================================================
# file_lint.py — AIChat / llm-functions File Lint Tool
# Compatible with Sigoden AIChat & llm-functions · Zero Dependencies · Stdlib Only
#
# @describe Multi-format file linter and syntax validator for code and data
# @describe files (Python, JSON, TOML, INI, Markdown, and general text).
#
# @meta require-tools aichat
# @meta python-entrypoint run
# @meta read-only
#
# @option --target! <PATH>               File or directory path to lint (required)
# @option --mode <MODE>                  Execution mode: summary/detailed
# @option --limit <NUM>                  Maximum items/issues to report
# @option --timeout <SEC>                Maximum execution time in seconds
# @option --file-pattern <PATTERN>       Filename glob filter (e.g., '*.py', '*.json')
# @option --max-line-length <NUM>        Maximum allowed line length (default: 100)
# @option --severity <LEVEL>             Minimum severity: all/warning/error
# @option --cache-ttl <SEC>              Cache TTL in seconds
# @option --cache-dir <PATH>             Cache directory path
# @option --env-var <KEY=VALUE>          Safe environment override (repeatable)
# @flag   --recursive                    Scan directories recursively
# @flag   --include-hidden               Include hidden files and directories
# @flag   --dry-run                      Compatibility flag; tool is read-only
# @flag   --use-cache                    Enable deterministic result caching
# @flag   --clear-cache                  Clear this tool's cache
# @flag   --schema                       Print the LLM-facing JSON schema
# @flag   --self-test                    Run dependency-free linter self-tests
# @flag   --json-only                    Suppress human CLI UI (machine output only)
# @flag   --quiet                        Suppress progress and UI output
# @flag   --no-color                     Disable ANSI color output
# @flag   --verbose                      Enable debug logging
#
# @env LLM_OUTPUT=/dev/stdout            llm-functions output destination
# @env LLM_TOOL_MODE=detailed            Default tool mode
# @env LLM_TOOL_LIMIT=100                Default issue limit
# @env LLM_TOOL_CACHE_DIR                Cache directory override
# ==============================================================================

from __future__ import annotations

import argparse
import ast
import configparser
import contextlib
import dataclasses
import fnmatch
import hashlib
import ipaddress
import json
import logging
import os
import platform
import re
import signal
import sys
import tempfile
import threading
import time
import traceback
import uuid
from collections.abc import Iterator, Mapping, Sequence
from datetime import date, datetime, timedelta, timezone
from enum import Enum
from pathlib import Path
from typing import Any, Literal, Optional

# Conditional import for Python 3.11+ stdlib TOML support
try:
    import tomllib  # type: ignore[import-not-found]
except ImportError:
    tomllib = None  # type: ignore[assignment]

# ==============================================================================
# TOOL IDENTITY
# ==============================================================================

__version__ = "1.0.0"
__tool_name__ = "file_lint"
__tool_description__ = (
    "Lint and validate files (Python, JSON, TOML, INI, Markdown, Text) for "
    "syntax errors, formatting defects, trailing whitespace, and line lengths."
)

__all__ = [
    "run",
    "execute_tool",
    "main",
    "validate_inputs",
    "build_cache_key",
    "generate_tool_schema",
    "invalidate_cache",
    "ToolCache",
    "ToolError",
    "GracefulShutdown",
    "SecurityValidator",
    "FileLinter",
    "get_agent_var",
    "get_builtin_var",
    "get_execution_context",
    "__version__",
]

# ==============================================================================
# SECTION 1: Constants, Exit Codes & Formatting
# ==============================================================================

EXIT_SUCCESS = 0
EXIT_ERROR = 1
EXIT_FILE_NOT_FOUND = 2
EXIT_LINT_ISSUES = 3
EXIT_TIMEOUT = 124
EXIT_PERMISSION_DENIED = 126
EXIT_INVALID_INPUT = 127
EXIT_INTERRUPTED = 130

MAX_LIMIT = 10_000
MAX_TIMEOUT = 86_400.0
MAX_CACHE_TTL = 31_536_000
MAX_CACHE_SIZE_MB = 512
DEFAULT_LIMIT = 100
DEFAULT_CACHE_TTL = 3_600
DEFAULT_CACHE_SIZE_MB = 512
DEFAULT_MAX_LINE_LENGTH = 100
SUMMARY_PREVIEW = 10

VALID_MODES = frozenset({"summary", "detailed"})
VALID_OUTPUT_FORMATS = frozenset({"json", "jsonl"})
VALID_SEVERITIES = frozenset({"all", "warning", "error"})

BLOCKED_ENV_VARS = frozenset(
    {
        "LD_PRELOAD",
        "LD_LIBRARY_PATH",
        "DYLD_INSERT_LIBRARIES",
        "DYLD_LIBRARY_PATH",
        "PYTHONPATH",
        "PYTHONHOME",
        "PATH",
        "SHELL",
        "BASH_ENV",
        "ENV",
        "CDPATH",
        "SUDO_COMMAND",
        "SUDO_USER",
        "SUDO_UID",
        "SUDO_GID",
    }
)

NEON_CYAN = "\033[38;5;51m"
NEON_GREEN = "\033[38;5;46m"
NEON_RED = "\033[38;5;196m"
NEON_YELLOW = "\033[38;5;226m"
NEON_PURPLE = "\033[38;5;129m"
NEON_PINK = "\033[38;5;198m"
RESET = "\033[0m"
BOLD = "\033[1m"
DIM = "\033[2m"

_ANSI_RE = re.compile(
    r"\x1b(?:[@-Z\\-_]|\[[0-?]*[ -/]*[@-~])|\033\[[0-9;?]*[a-zA-Z]"
)

# Common binary & artifact ignore extensions
IGNORED_EXTENSIONS = frozenset(
    {
        ".pyc", ".pyo", ".pyd", ".so", ".dll", ".dylib", ".exe", ".bin",
        ".png", ".jpg", ".jpeg", ".gif", ".ico", ".webp", ".svg",
        ".zip", ".tar", ".gz", ".bz2", ".xz", ".7z", ".rar",
        ".pdf", ".woff", ".woff2", ".ttf", ".eot", ".db", ".sqlite",
    }
)

# ==============================================================================
# SECTION 2: Enums & Structured Errors
# ==============================================================================


class ExecutionMode(str, Enum):
    SUMMARY = "summary"
    DETAILED = "detailed"


class SeverityLevel(str, Enum):
    ALL = "all"
    WARNING = "warning"
    ERROR = "error"


class ToolError(Exception):
    """Predictable, serializable tool error."""

    def __init__(
        self,
        message: str,
        exit_code: int = EXIT_ERROR,
        error_type: str = "ExecutionError",
        recoverable: bool = False,
        details: Optional[dict[str, Any]] = None,
        trace_id: Optional[str] = None,
    ) -> None:
        super().__init__(message)
        self.message = message
        self.exit_code = exit_code
        self.error_type = error_type
        self.recoverable = recoverable
        self.details = details or {}
        self.trace_id = trace_id or f"err_{uuid.uuid4().hex[:12]}"

    def to_dict(self, verbose: bool = False) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "success": False,
            "error": self.message,
            "type": self.error_type,
            "exit_code": self.exit_code,
            "recoverable": self.recoverable,
            "trace_id": self.trace_id,
            **self.details,
        }
        if verbose:
            payload["traceback"] = traceback.format_exc()
        return payload


# ==============================================================================
# SECTION 3: JSON Normalization
# ==============================================================================


class ToolJSONEncoder(json.JSONEncoder):
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
            if isinstance(obj, (uuid.UUID, ipaddress.IPv4Address, ipaddress.IPv6Address)):
                return str(obj)
            if isinstance(obj, bytes):
                return obj.decode("utf-8", errors="replace")
            if isinstance(obj, (set, frozenset)):
                return sorted(obj, key=str)
            if dataclasses.is_dataclass(obj):
                return dataclasses.asdict(obj)
            if hasattr(obj, "model_dump") and callable(obj.model_dump):
                return obj.model_dump()
            if hasattr(obj, "dict") and callable(obj.dict):
                return obj.dict()
            if isinstance(obj, Mapping):
                return dict(obj)
            if isinstance(obj, Sequence) and not isinstance(obj, (str, bytes, bytearray)):
                return list(obj)
        except Exception:
            pass
        return repr(obj)


def json_safe(value: Any) -> Any:
    return json.loads(json.dumps(value, ensure_ascii=False, cls=ToolJSONEncoder))


# ==============================================================================
# SECTION 4: Terminal UI / Presentation (STDERR Only)
# ==============================================================================


def _strip_ansi(text: str) -> str:
    return _ANSI_RE.sub("", str(text))


def _is_tty(no_color: bool = False) -> bool:
    if no_color or os.environ.get("NO_COLOR") is not None:
        return False
    if os.environ.get("FORCE_COLOR"):
        return True
    return sys.stderr.isatty() and os.environ.get("TERM", "").lower() not in {"", "dumb"}


def _cprint(
    text: str,
    file: Any = None,
    no_color: bool = False,
    end: str = "\n",
) -> None:
    target = file or sys.stderr
    rendered = text if _is_tty(no_color) else _strip_ansi(text)
    print(rendered, file=target, flush=True, end=end)


def setup_tool_logging(verbose: bool, request_id: str) -> None:
    if not verbose:
        return
    logging.basicConfig(
        level=logging.DEBUG,
        format=f"[%(asctime)s] [{request_id}] [%(levelname)s] %(message)s",
        stream=sys.stderr,
        force=True,
    )


def print_progress(
    current: int,
    total: int,
    message: str = "",
    no_color: bool = False,
) -> None:
    if not _is_tty(no_color):
        return
    percent = 100.0 if total <= 0 else min(100.0, current / total * 100.0)
    width = 24
    filled = min(width, int(width * percent / 100.0))
    bar = "█" * filled + "░" * (width - filled)
    _cprint(
        f"\r{NEON_CYAN}Linting{RESET} [{NEON_GREEN}{bar}{RESET}] {percent:5.1f}% "
        f"{DIM}{str(message)[:35]}{RESET}",
        no_color=no_color,
        end="",
    )
    if current >= total:
        _cprint("", no_color=no_color)


def print_human_readable_ui(
    data: Mapping[str, Any],
    no_color: bool = False,
    quiet: bool = False,
) -> None:
    if quiet or not _is_tty(no_color):
        return

    success = bool(data.get("success"))
    issue_count = data.get("issue_count", 0)
    files_scanned = data.get("files_scanned", 0)

    if not success:
        color = NEON_RED
        status = f"FAILED: {data.get('type', 'Error')}"
    elif issue_count == 0:
        color = NEON_GREEN
        status = "PASSED (Clean)"
    else:
        color = NEON_YELLOW
        status = f"ISSUES FOUND ({issue_count})"

    border = "─" * 70
    _cprint(f"{NEON_PURPLE}╭{border}╮{RESET}", no_color=no_color)
    _cprint(
        f"{NEON_PURPLE}│{RESET} {NEON_PINK}{BOLD}⚡ FILE LINTER v{__version__}{RESET} "
        f"{color}{BOLD}› {status}{RESET}",
        no_color=no_color,
    )
    _cprint(f"{NEON_PURPLE}├{border}┤{RESET}", no_color=no_color)
    _cprint(
        f"{NEON_PURPLE}│{RESET} {NEON_CYAN}Target:{RESET}       {data.get('target', 'N/A')}",
        no_color=no_color,
    )
    _cprint(
        f"{NEON_PURPLE}│{RESET} {NEON_CYAN}Files Scanned:{RESET}{NEON_YELLOW} {files_scanned}{RESET}",
        no_color=no_color,
    )
    _cprint(
        f"{NEON_PURPLE}│{RESET} {NEON_CYAN}Total Issues:{RESET} {NEON_RED if issue_count > 0 else NEON_GREEN}{issue_count}{RESET}",
        no_color=no_color,
    )
    _cprint(
        f"{NEON_PURPLE}│{RESET} {NEON_CYAN}Duration:{RESET}     {DIM}{data.get('duration_ms', 0)} ms{RESET}",
        no_color=no_color,
    )

    issues = data.get("issues") or []
    if issues:
        _cprint(f"{NEON_PURPLE}├{border}┤{RESET}", no_color=no_color)
        _cprint(f"{NEON_PURPLE}│{RESET} {BOLD}Lint Diagnostics:{RESET}", no_color=no_color)
        for issue in list(issues)[:SUMMARY_PREVIEW]:
            sev = issue.get("severity", "error").upper()
            sev_color = NEON_RED if sev == "ERROR" else NEON_YELLOW
            file_loc = f"{issue.get('file')}:{issue.get('line', 1)}"
            if issue.get("column"):
                file_loc += f":{issue.get('column')}"
            _cprint(
                f"{NEON_PURPLE}│{RESET}  {sev_color}[{sev}]{RESET} {DIM}{file_loc}{RESET} "
                f"{NEON_CYAN}{issue.get('code', '')}{RESET}: {issue.get('message', '')}",
                no_color=no_color,
            )
        if len(issues) > SUMMARY_PREVIEW:
            _cprint(
                f"{NEON_PURPLE}│{RESET}  {DIM}… and {len(issues) - SUMMARY_PREVIEW} more issues{RESET}",
                no_color=no_color,
            )

    _cprint(f"{NEON_PURPLE}╰{border}╯{RESET}", no_color=no_color)


# ==============================================================================
# SECTION 5: AIChat / Agent Helpers
# ==============================================================================


def get_agent_var(name: str, default: str = "") -> str:
    if not name:
        return default
    return os.environ.get(f"LLM_AGENT_VAR_{name.upper()}", default)


def get_builtin_var(name: str) -> Optional[str]:
    if not name:
        return None
    for candidate in (
        f"LLM_AGENT_VAR_{name}",
        f"LLM_AGENT_VAR_{name.lower()}",
        f"LLM_AGENT_VAR_{name.upper()}",
    ):
        value = os.environ.get(candidate)
        if value is not None:
            return value
    return None


def get_execution_context() -> dict[str, Any]:
    return {
        "tool_name": os.environ.get("LLM_TOOL_NAME", __tool_name__),
        "root_dir": os.environ.get("LLM_ROOT_DIR"),
        "cache_dir": os.environ.get("LLM_TOOL_CACHE_DIR"),
        "output_path": os.environ.get("LLM_OUTPUT", "/dev/stdout"),
        "cwd": get_builtin_var("__cwd__") or os.getcwd(),
        "host": {
            "os": platform.system().lower(),
            "arch": platform.machine(),
            "python_version": platform.python_version(),
            "toml_support": tomllib is not None,
        },
    }


def resolve_agent_path(target: str) -> Path:
    if "\x00" in target:
        raise ToolError("Target contains a null byte.", EXIT_INVALID_INPUT, "ValidationError")
    raw = Path(target).expanduser()
    if raw.is_absolute():
        return raw.resolve(strict=False)
    agent_cwd = get_builtin_var("__cwd__")
    if agent_cwd:
        return (Path(agent_cwd).expanduser() / raw).resolve(strict=False)
    return raw.resolve(strict=False)


def env_default(name: str, fallback: Any = None) -> dict[str, Any]:
    value = os.getenv(name)
    return {"default": value if value is not None else fallback}


# ==============================================================================
# SECTION 6: Input Validation & Security
# ==============================================================================


class SecurityValidator:
    @staticmethod
    def is_safe_glob(pattern: Optional[str]) -> bool:
        if not pattern:
            return True
        if "\x00" in pattern or len(pattern) > 1024:
            return False
        try:
            re.compile(fnmatch.translate(pattern))
            return True
        except re.error:
            return False

    @staticmethod
    def sanitize_env_vars(
        env_vars: Optional[list[str]],
    ) -> tuple[dict[str, str], list[str]]:
        parsed: dict[str, str] = {}
        warnings: list[str] = []
        for entry in env_vars or []:
            if "=" not in entry:
                warnings.append(f"Ignored malformed env var: {entry!r}")
                continue
            key, value = entry.split("=", 1)
            key = key.strip()
            if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", key):
                warnings.append(f"Ignored invalid environment variable name: {key!r}")
                continue
            if key.upper() in BLOCKED_ENV_VARS:
                warnings.append(f"Blocked unsafe environment override: {key}")
                continue
            parsed[key] = value
        return parsed, warnings


def validate_inputs(
    target: Optional[str],
    mode: str = "detailed",
    limit: Optional[int] = DEFAULT_LIMIT,
    timeout: Optional[float] = None,
    file_pattern: Optional[str] = None,
    max_line_length: Optional[int] = DEFAULT_MAX_LINE_LENGTH,
    severity: str = "all",
    output_format: str = "jsonl",
) -> Optional[dict[str, Any]]:
    trace_id = f"val_{uuid.uuid4().hex[:12]}"

    if target is None or not str(target).strip():
        return ToolError(
            "Target path is required.",
            EXIT_INVALID_INPUT,
            "ValidationError",
            trace_id=trace_id,
        ).to_dict()

    if "\x00" in str(target):
        return ToolError(
            "Target path contains a null byte.",
            EXIT_INVALID_INPUT,
            "ValidationError",
            trace_id=trace_id,
        ).to_dict()

    if mode not in VALID_MODES:
        return ToolError(
            f"Invalid mode {mode!r}. Allowed: {sorted(VALID_MODES)}.",
            EXIT_INVALID_INPUT,
            "ValidationError",
            trace_id=trace_id,
        ).to_dict()

    if severity not in VALID_SEVERITIES:
        return ToolError(
            f"Invalid severity {severity!r}. Allowed: {sorted(VALID_SEVERITIES)}.",
            EXIT_INVALID_INPUT,
            "ValidationError",
            trace_id=trace_id,
        ).to_dict()

    if output_format not in VALID_OUTPUT_FORMATS:
        return ToolError(
            f"Invalid output format {output_format!r}.",
            EXIT_INVALID_INPUT,
            "ValidationError",
            trace_id=trace_id,
        ).to_dict()

    if limit is not None and not 0 <= limit <= MAX_LIMIT:
        return ToolError(
            f"limit must be between 0 and {MAX_LIMIT}.",
            EXIT_INVALID_INPUT,
            "ValidationError",
            trace_id=trace_id,
        ).to_dict()

    if timeout is not None and not 0 < timeout <= MAX_TIMEOUT:
        return ToolError(
            f"timeout must be > 0 and <= {MAX_TIMEOUT} seconds.",
            EXIT_INVALID_INPUT,
            "ValidationError",
            trace_id=trace_id,
        ).to_dict()

    if max_line_length is not None and max_line_length <= 0:
        return ToolError(
            "max_line_length must be a positive integer.",
            EXIT_INVALID_INPUT,
            "ValidationError",
            trace_id=trace_id,
        ).to_dict()

    if not SecurityValidator.is_safe_glob(file_pattern):
        return ToolError(
            f"Invalid or unsafe glob pattern: {file_pattern!r}.",
            EXIT_INVALID_INPUT,
            "ValidationError",
            trace_id=trace_id,
        ).to_dict()

    return None


# ==============================================================================
# SECTION 7: Deterministic Cache Engine
# ==============================================================================


def build_cache_key(
    target_path: Path,
    mode: str,
    limit_val: int,
    file_pattern: Optional[str],
    recursive: bool,
    max_line_length: int,
    severity: str,
    include_hidden: bool,
) -> str:
    try:
        stat = target_path.stat()
        fingerprint = f"{stat.st_mtime_ns}:{stat.st_size}:{getattr(stat, 'st_ino', 0)}"
    except OSError:
        fingerprint = "unstatable"

    parts = (
        __tool_name__,
        __version__,
        str(target_path),
        "dir" if target_path.is_dir() else "file",
        fingerprint,
        mode,
        str(limit_val),
        file_pattern or "",
        str(bool(recursive)),
        str(max_line_length),
        severity,
        str(bool(include_hidden)),
    )
    return "|".join(parts)


class ToolCache:
    def __init__(
        self,
        cache_dir: Optional[str | Path] = None,
        ttl: int = DEFAULT_CACHE_TTL,
        max_size_mb: int = DEFAULT_CACHE_SIZE_MB,
    ) -> None:
        selected = cache_dir or os.environ.get("LLM_TOOL_CACHE_DIR")
        self.cache_dir = (
            Path(selected).expanduser().resolve()
            if selected
            else Path.home() / ".cache" / "aichat_tools" / __tool_name__
        )
        self.ttl = max(0, min(int(ttl), MAX_CACHE_TTL))
        self.max_size_bytes = max(1, int(max_size_mb)) * 1024 * 1024
        self._lock = threading.RLock()
        with contextlib.suppress(OSError):
            self.cache_dir.mkdir(parents=True, exist_ok=True)

    def _hash_key(self, key: str) -> str:
        return hashlib.sha256(key.encode("utf-8")).hexdigest()

    def _cache_file(self, key: str) -> Path:
        return self.cache_dir / f"{self._hash_key(key)}.json"

    def _lock_file(self, key: str) -> Path:
        return self.cache_dir / f"{self._hash_key(key)}.lock"

    @contextlib.contextmanager
    def _file_lock(self, lock_file: Path) -> Iterator[None]:
        acquired = False
        deadline = time.monotonic() + 3.0
        while time.monotonic() < deadline:
            try:
                fd = os.open(str(lock_file), os.O_CREAT | os.O_EXCL | os.O_RDWR, 0o600)
                os.close(fd)
                acquired = True
                break
            except FileExistsError:
                time.sleep(0.025)
            except OSError:
                break
        try:
            yield
        finally:
            if acquired:
                with contextlib.suppress(OSError):
                    lock_file.unlink()

    def get(self, key: str) -> Optional[dict[str, Any]]:
        path = self._cache_file(key)
        if not path.is_file():
            return None
        try:
            if self.ttl == 0:
                path.unlink(missing_ok=True)
                return None
            age = time.time() - path.stat().st_mtime
            if age > self.ttl:
                path.unlink(missing_ok=True)
                return None
            with path.open("r", encoding="utf-8") as handle:
                value = json.load(handle)
            if not isinstance(value, dict):
                raise ValueError("Corrupt cache")
            with contextlib.suppress(OSError):
                os.utime(path, None)
            return value
        except Exception:
            with contextlib.suppress(OSError):
                path.unlink()
            return None

    def set(self, key: str, value: Mapping[str, Any]) -> None:
        if self.ttl == 0:
            return
        path = self._cache_file(key)
        tmp: Optional[Path] = None
        with self._lock, self._file_lock(self._lock_file(key)):
            try:
                payload = json.dumps(value, ensure_ascii=False, cls=ToolJSONEncoder)
                fd, tmp_name = tempfile.mkstemp(
                    prefix=f".{path.name}.",
                    suffix=".tmp",
                    dir=str(self.cache_dir),
                    text=True,
                )
                os.close(fd)
                tmp = Path(tmp_name)
                tmp.write_text(payload, encoding="utf-8")
                tmp.replace(path)
            except Exception:
                if tmp:
                    with contextlib.suppress(OSError):
                        tmp.unlink()

    def clear(self) -> int:
        removed = 0
        if not self.cache_dir.exists():
            return removed
        for path in self.cache_dir.glob("*.json"):
            try:
                path.unlink()
                removed += 1
            except OSError:
                pass
        for path in self.cache_dir.glob("*.lock"):
            with contextlib.suppress(OSError):
                path.unlink()
        return removed


def invalidate_cache(cache: Optional[ToolCache] = None) -> int:
    return (cache or ToolCache()).clear()


# ==============================================================================
# SECTION 8: Graceful Cancellation
# ==============================================================================


class GracefulShutdown:
    def __init__(self) -> None:
        self.interrupted = False
        self._old_sigint: Any = None
        self._old_sigterm: Any = None

    def __enter__(self) -> "GracefulShutdown":
        self.interrupted = False
        if threading.current_thread() is threading.main_thread():
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

    def should_stop(self) -> bool:
        return self.interrupted

    def restore(self) -> None:
        if threading.current_thread() is not threading.main_thread():
            return
        if self._old_sigint is not None:
            with contextlib.suppress(ValueError, AttributeError):
                signal.signal(signal.SIGINT, self._old_sigint)
        if self._old_sigterm is not None:
            with contextlib.suppress(ValueError, AttributeError):
                signal.signal(signal.SIGTERM, self._old_sigterm)


# ==============================================================================
# SECTION 9: Multi-Format File Linter Engine
# ==============================================================================


@dataclasses.dataclass
class LintIssue:
    file: str
    line: int
    column: Optional[int]
    code: str
    severity: str  # "error", "warning", "info"
    message: str
    snippet: Optional[str] = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "file": self.file,
            "line": self.line,
            "column": self.column,
            "code": self.code,
            "severity": self.severity,
            "message": self.message,
            "snippet": self.snippet,
        }


class FileLinter:
    """Multi-format linting engine for Python, JSON, TOML, INI, and plain text."""

    def __init__(
        self,
        base_path: Path,
        recursive: bool,
        pattern: Optional[str],
        include_hidden: bool,
        max_line_length: int,
        severity_filter: str,
        limit: int,
        timeout: Optional[float],
        start_time: float,
        shutdown: GracefulShutdown,
        progress: bool = False,
        no_color: bool = True,
    ) -> None:
        self.base_path = base_path
        self.recursive = recursive
        self.pattern = pattern
        self.include_hidden = include_hidden
        self.max_line_length = max_line_length
        self.severity_filter = severity_filter
        self.limit = limit
        self.timeout = timeout
        self.start_time = start_time
        self.shutdown = shutdown
        self.progress = progress
        self.no_color = no_color
        self.files_scanned = 0
        self.visited_dirs: set[str] = set()

    def _check_stop(self) -> None:
        if self.shutdown.should_stop():
            raise ToolError(
                "Execution interrupted by user signal.",
                EXIT_INTERRUPTED,
                "InterruptError",
                recoverable=True,
            )
        if self.timeout is not None:
            elapsed = time.monotonic() - self.start_time
            if elapsed >= self.timeout:
                raise ToolError(
                    f"Execution timed out after {self.timeout:g}s.",
                    EXIT_TIMEOUT,
                    "TimeoutError",
                    recoverable=True,
                )

    def _should_include_file(self, path: Path) -> bool:
        if path.suffix.lower() in IGNORED_EXTENSIONS:
            return False
        if not self.include_hidden and path.name.startswith("."):
            return False
        if self.pattern and not fnmatch.fnmatchcase(path.name, self.pattern):
            return False
        return True

    def _collect_files(self) -> list[Path]:
        if not self.base_path.is_dir():
            return [self.base_path]

        files: list[Path] = []
        self._walk_and_collect(self.base_path, files)
        return files

    def _walk_and_collect(self, current_dir: Path, target_list: list[Path]) -> None:
        self._check_stop()
        try:
            real_dir = str(current_dir.resolve(strict=False))
        except OSError:
            return

        if real_dir in self.visited_dirs:
            return
        self.visited_dirs.add(real_dir)

        try:
            entries = sorted(os.scandir(current_dir), key=lambda x: x.name)
        except OSError:
            return

        subdirs: list[Path] = []
        for entry in entries:
            self._check_stop()
            if not self.include_hidden and entry.name.startswith("."):
                continue
            path = Path(entry.path)
            try:
                if entry.is_dir(follow_symlinks=False):
                    if self.recursive:
                        subdirs.append(path)
                elif entry.is_file(follow_symlinks=True):
                    if self._should_include_file(path):
                        target_list.append(path)
            except OSError:
                continue

        if self.recursive:
            for subdir in subdirs:
                self._walk_and_collect(subdir, target_list)

    def lint_all(self) -> tuple[list[dict[str, Any]], int]:
        files = self._collect_files()
        total_files = len(files)
        all_issues: list[dict[str, Any]] = []

        for idx, file_path in enumerate(files, start=1):
            self._check_stop()
            self.files_scanned += 1

            if self.progress and total_files > 0:
                print_progress(
                    idx,
                    total_files,
                    file_path.name,
                    no_color=self.no_color,
                )

            issues = self._lint_file(file_path)
            for issue in issues:
                if self._matches_severity(issue.severity):
                    if len(all_issues) < self.limit:
                        all_issues.append(issue.to_dict())
                    else:
                        break

            if len(all_issues) >= self.limit:
                break

        return all_issues, self.files_scanned

    def _matches_severity(self, severity: str) -> bool:
        if self.severity_filter == "all":
            return True
        if self.severity_filter == "warning":
            return severity in {"warning", "error"}
        if self.severity_filter == "error":
            return severity == "error"
        return True

    def _lint_file(self, file_path: Path) -> list[LintIssue]:
        issues: list[LintIssue] = []
        rel_str = str(file_path)

        try:
            content_bytes = file_path.read_bytes()
        except PermissionError:
            issues.append(
                LintIssue(
                    file=rel_str,
                    line=1,
                    column=None,
                    code="E999",
                    severity="error",
                    message="Permission denied reading file.",
                )
            )
            return issues
        except OSError as exc:
            issues.append(
                LintIssue(
                    file=rel_str,
                    line=1,
                    column=None,
                    code="E999",
                    severity="error",
                    message=f"OS error reading file: {exc}",
                )
            )
            return issues

        # Check for UTF-8 decode issues
        try:
            content = content_bytes.decode("utf-8")
        except UnicodeDecodeError as exc:
            issues.append(
                LintIssue(
                    file=rel_str,
                    line=1,
                    column=exc.start + 1,
                    code="E902",
                    severity="error",
                    message=f"Unicode decode error (non-UTF8 characters): {exc.reason}",
                )
            )
            return issues

        # Universal whitespace / structure checks
        self._check_generic_formatting(rel_str, content, issues)

        suffix = file_path.suffix.lower()
        if suffix == ".py":
            self._lint_python(rel_str, content, issues)
        elif suffix == ".json":
            self._lint_json(rel_str, content, issues)
        elif suffix == ".toml":
            self._lint_toml(rel_str, content, issues)
        elif suffix in {".ini", ".cfg"}:
            self._lint_ini(rel_str, content, issues)
        elif suffix in {".md", ".markdown"}:
            self._lint_markdown(rel_str, content, issues)

        return issues

    def _check_generic_formatting(
        self,
        rel_path: str,
        content: str,
        issues: list[LintIssue],
    ) -> None:
        if not content:
            return

        lines = content.splitlines(keepends=True)
        has_crlf = False
        has_tabs = False
        has_spaces = False

        for line_num, line in enumerate(lines, start=1):
            # Check mixed line endings
            if line.endswith("\r\n"):
                has_crlf = True

            stripped_line = line.rstrip("\r\n")

            # Check trailing whitespace
            if stripped_line != stripped_line.rstrip(" \t"):
                issues.append(
                    LintIssue(
                        file=rel_path,
                        line=line_num,
                        column=len(stripped_line.rstrip(" \t")) + 1,
                        code="W291",
                        severity="warning",
                        message="Trailing whitespace detected.",
                        snippet=stripped_line[-30:] if len(stripped_line) > 30 else stripped_line,
                    )
                )

            # Check max line length
            if len(stripped_line) > self.max_line_length:
                issues.append(
                    LintIssue(
                        file=rel_path,
                        line=line_num,
                        column=self.max_line_length + 1,
                        code="W505",
                        severity="warning",
                        message=f"Line exceeds max length ({len(stripped_line)} > {self.max_line_length}).",
                    )
                )

            # Detect indentation style
            leading = len(stripped_line) - len(stripped_line.lstrip())
            indent = stripped_line[:leading]
            if "\t" in indent:
                has_tabs = True
            if " " in indent:
                has_spaces = True

        # Check missing trailing newline at EOF
        if lines and not lines[-1].endswith(("\n", "\r")):
            issues.append(
                LintIssue(
                    file=rel_path,
                    line=len(lines),
                    column=len(lines[-1]) + 1,
                    code="W292",
                    severity="warning",
                    message="Missing newline at end of file (EOF).",
                )
            )

        # Check mixed line endings
        if has_crlf and any(l.endswith("\n") and not l.endswith("\r\n") for l in lines):
            issues.append(
                LintIssue(
                    file=rel_path,
                    line=1,
                    column=None,
                    code="W503",
                    severity="warning",
                    message="Mixed line endings (CRLF and LF) detected in file.",
                )
            )

        # Check mixed indentation in file
        if has_tabs and has_spaces and not rel_path.endswith((".tsv", ".tab")):
            issues.append(
                LintIssue(
                    file=rel_path,
                    line=1,
                    column=None,
                    code="E101",
                    severity="warning",
                    message="Mixed indentation types (tabs and spaces) detected.",
                )
            )

    def _lint_python(
        self,
        rel_path: str,
        content: str,
        issues: list[LintIssue],
    ) -> None:
        # 1. AST Parsing
        try:
            tree = ast.parse(content, filename=rel_path)
        except SyntaxError as exc:
            issues.append(
                LintIssue(
                    file=rel_path,
                    line=exc.lineno or 1,
                    column=exc.offset or 1,
                    code="E901",
                    severity="error",
                    message=f"Python syntax error: {exc.msg}",
                    snippet=exc.text.strip() if exc.text else None,
                )
            )
            return

        # 2. AST Visitor for logical lint issues
        class PyASTVisitor(ast.NodeVisitor):
            def __init__(self, visitor_issues: list[LintIssue]) -> None:
                self.visitor_issues = visitor_issues

            def visit_ExceptHandler(self, node: ast.ExceptHandler) -> None:
                if node.type is None:
                    self.visitor_issues.append(
                        LintIssue(
                            file=rel_path,
                            line=node.lineno,
                            column=node.col_offset + 1,
                            code="W701",
                            severity="warning",
                            message="Bare 'except:' clause caught; specify exception class.",
                        )
                    )
                self.generic_visit(node)

            def visit_Dict(self, node: ast.Dict) -> None:
                seen_keys = set()
                for key_node in node.keys:
                    if isinstance(key_node, ast.Constant):
                        if key_node.value in seen_keys:
                            self.visitor_issues.append(
                                LintIssue(
                                    file=rel_path,
                                    line=key_node.lineno,
                                    column=key_node.col_offset + 1,
                                    code="W601",
                                    severity="warning",
                                    message=f"Duplicate dictionary key: {key_node.value!r}.",
                                )
                            )
                        seen_keys.add(key_node.value)
                self.generic_visit(node)

        PyASTVisitor(issues).visit(tree)

    def _lint_json(
        self,
        rel_path: str,
        content: str,
        issues: list[LintIssue],
    ) -> None:
        if not content.strip():
            issues.append(
                LintIssue(
                    file=rel_path,
                    line=1,
                    column=1,
                    code="E270",
                    severity="error",
                    message="JSON file is completely empty.",
                )
            )
            return
        try:
            json.loads(content)
        except json.JSONDecodeError as exc:
            issues.append(
                LintIssue(
                    file=rel_path,
                    line=exc.lineno,
                    column=exc.colno,
                    code="E271",
                    severity="error",
                    message=f"JSON syntax error: {exc.msg}",
                )
            )

    def _lint_toml(
        self,
        rel_path: str,
        content: str,
        issues: list[LintIssue],
    ) -> None:
        if tomllib is None:
            return  # Toml parsing unavailable on Python < 3.11 without third-party
        try:
            tomllib.loads(content)
        except Exception as exc:
            issues.append(
                LintIssue(
                    file=rel_path,
                    line=1,
                    column=None,
                    code="E301",
                    severity="error",
                    message=f"TOML parsing error: {exc}",
                )
            )

    def _lint_ini(
        self,
        rel_path: str,
        content: str,
        issues: list[LintIssue],
    ) -> None:
        parser = configparser.ConfigParser()
        try:
            parser.read_string(content, source=rel_path)
        except configparser.Error as exc:
            line_no = getattr(exc, "lineno", 1) or 1
            issues.append(
                LintIssue(
                    file=rel_path,
                    line=line_no,
                    column=None,
                    code="E401",
                    severity="error",
                    message=f"INI/Config format error: {exc.message if hasattr(exc, 'message') else str(exc)}",
                )
            )

    def _lint_markdown(
        self,
        rel_path: str,
        content: str,
        issues: list[LintIssue],
    ) -> None:
        # Check unmatched code fences (```)
        lines = content.splitlines()
        fence_open = False
        fence_line = 0

        for line_num, line in enumerate(lines, start=1):
            if line.strip().startswith("```"):
                if not fence_open:
                    fence_open = True
                    fence_line = line_num
                else:
                    fence_open = False

        if fence_open:
            issues.append(
                LintIssue(
                    file=rel_path,
                    line=fence_line,
                    column=1,
                    code="W801",
                    severity="warning",
                    message="Unclosed code fence (```) in Markdown document.",
                )
            )


# ==============================================================================
# SECTION 10: Tool Execution Engine
# ==============================================================================


def execute_tool(
    target: str,
    mode: str = "detailed",
    limit: Optional[int] = DEFAULT_LIMIT,
    timeout: Optional[float] = None,
    file_pattern: Optional[str] = None,
    max_line_length: Optional[int] = DEFAULT_MAX_LINE_LENGTH,
    severity: str = "all",
    env_vars: Optional[list[str]] = None,
    recursive: bool = False,
    include_hidden: bool = False,
    use_cache: bool = False,
    no_color: bool = True,
    verbose: bool = False,
    cache_ttl: int = DEFAULT_CACHE_TTL,
    cache_dir: Optional[str] = None,
    output_format: str = "jsonl",
    dry_run: bool = False,
    quiet: bool = True,
) -> dict[str, Any]:
    started = time.monotonic()
    request_id = f"req_{uuid.uuid4().hex[:12]}"
    warnings: list[str] = []

    setup_tool_logging(verbose, request_id)

    bad = validate_inputs(
        target=target,
        mode=mode,
        limit=limit,
        timeout=timeout,
        file_pattern=file_pattern,
        max_line_length=max_line_length,
        severity=severity,
        output_format=output_format,
    )
    if bad:
        bad["request_id"] = request_id
        bad["timestamp"] = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
        return bad

    safe_env, env_warnings = SecurityValidator.sanitize_env_vars(env_vars)
    warnings.extend(env_warnings)

    previous_env: dict[str, Optional[str]] = {}
    for key, value in safe_env.items():
        previous_env[key] = os.environ.get(key)
        os.environ[key] = value

    limit_val = DEFAULT_LIMIT if limit is None else limit
    max_line_len = DEFAULT_MAX_LINE_LENGTH if max_line_length is None else max_line_length

    try:
        try:
            target_path = resolve_agent_path(target)
        except ToolError as exc:
            exc.trace_id = request_id
            return exc.to_dict(verbose=verbose)

        if not target_path.exists():
            return ToolError(
                f"Target path does not exist: '{target}' (resolved: '{target_path}').",
                EXIT_FILE_NOT_FOUND,
                "FileNotFoundError",
                recoverable=True,
                trace_id=request_id,
            ).to_dict(verbose=verbose)

        cache = ToolCache(cache_dir=cache_dir, ttl=cache_ttl)
        cache_key = build_cache_key(
            target_path=target_path,
            mode=mode,
            limit_val=limit_val,
            file_pattern=file_pattern,
            recursive=recursive,
            max_line_length=max_line_len,
            severity=severity,
            include_hidden=include_hidden,
        )

        if use_cache and not dry_run:
            cached = cache.get(cache_key)
            if cached is not None:
                cached = dict(cached)
                cached["cached"] = True
                cached["request_id"] = request_id
                cached["duration_ms"] = round((time.monotonic() - started) * 1000, 2)
                return cached

        with GracefulShutdown() as shutdown:
            linter = FileLinter(
                base_path=target_path,
                recursive=recursive,
                pattern=file_pattern,
                include_hidden=include_hidden,
                max_line_length=max_line_len,
                severity_filter=severity,
                limit=limit_val,
                timeout=timeout,
                start_time=started,
                shutdown=shutdown,
                progress=verbose and not quiet,
                no_color=no_color,
            )
            issues, files_scanned = linter.lint_all()

        duration_ms = round((time.monotonic() - started) * 1000, 2)
        total_issues = len(issues)
        has_errors = any(i.get("severity") == "error" for i in issues)

        result: dict[str, Any] = {
            "success": True,
            "passed": total_issues == 0,
            "has_errors": has_errors,
            "request_id": request_id,
            "timestamp": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
            "tool": __tool_name__,
            "tool_version": __version__,
            "target": str(target_path),
            "files_scanned": files_scanned,
            "issue_count": total_issues,
            "mode": mode,
            "issues": issues if mode == "detailed" else issues[:SUMMARY_PREVIEW],
            "truncated": mode == "summary" and total_issues > SUMMARY_PREVIEW,
            "limit": limit_val,
            "max_line_length": max_line_len,
            "severity_filter": severity,
            "recursive": recursive,
            "cached": False,
            "warnings": warnings,
            "duration_ms": duration_ms,
            "context": get_execution_context(),
            "exit_code": EXIT_LINT_ISSUES if total_issues > 0 else EXIT_SUCCESS,
        }

        result = json_safe(result)

        if use_cache and not dry_run:
            cache.set(cache_key, result)

        return result

    except ToolError as exc:
        duration_ms = round((time.monotonic() - started) * 1000, 2)
        payload = exc.to_dict(verbose=verbose)
        payload.update(
            {
                "request_id": request_id,
                "timestamp": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
                "tool": __tool_name__,
                "tool_version": __version__,
                "duration_ms": duration_ms,
                "warnings": warnings,
            }
        )
        return json_safe(payload)

    except PermissionError as exc:
        duration_ms = round((time.monotonic() - started) * 1000, 2)
        return ToolError(
            f"Permission denied: {exc}",
            EXIT_PERMISSION_DENIED,
            "PermissionError",
            recoverable=True,
            trace_id=request_id,
            details={"duration_ms": duration_ms, "warnings": warnings},
        ).to_dict(verbose=verbose)

    except Exception as exc:
        duration_ms = round((time.monotonic() - started) * 1000, 2)
        return ToolError(
            f"Unexpected tool error: {exc}",
            EXIT_ERROR,
            "UnhandledException",
            trace_id=request_id,
            details={"duration_ms": duration_ms, "warnings": warnings},
        ).to_dict(verbose=verbose)

    finally:
        for key, old_value in previous_env.items():
            if old_value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = old_value


# ==============================================================================
# SECTION 11: LLM Schema Declaration
# ==============================================================================


def generate_tool_schema() -> dict[str, Any]:
    return {
        "name": "run",
        "description": __tool_description__,
        "parameters": {
            "type": "object",
            "properties": {
                "target": {
                    "type": "string",
                    "description": "File or directory path to lint.",
                },
                "mode": {
                    "type": "string",
                    "enum": ["summary", "detailed"],
                    "description": "Detailed list or summary preview of lint issues.",
                    "default": "detailed",
                },
                "limit": {
                    "type": "integer",
                    "minimum": 0,
                    "maximum": MAX_LIMIT,
                    "description": "Maximum number of lint issues to return.",
                    "default": DEFAULT_LIMIT,
                },
                "timeout": {
                    "type": ["number", "null"],
                    "exclusiveMinimum": 0,
                    "maximum": MAX_TIMEOUT,
                    "description": "Maximum allowed execution time in seconds.",
                    "default": None,
                },
                "file_pattern": {
                    "type": ["string", "null"],
                    "description": "Optional glob filter (e.g. '*.py', '*.json').",
                    "default": None,
                },
                "max_line_length": {
                    "type": "integer",
                    "minimum": 1,
                    "description": "Maximum allowed line length threshold.",
                    "default": DEFAULT_MAX_LINE_LENGTH,
                },
                "severity": {
                    "type": "string",
                    "enum": ["all", "warning", "error"],
                    "description": "Minimum issue severity to report.",
                    "default": "all",
                },
                "recursive": {
                    "type": "boolean",
                    "description": "Recursively lint directories.",
                    "default": False,
                },
                "include_hidden": {
                    "type": "boolean",
                    "description": "Include hidden files and folders.",
                    "default": False,
                },
                "use_cache": {
                    "type": "boolean",
                    "description": "Reuse deterministic cached result if available.",
                    "default": False,
                },
            },
            "required": ["target"],
            "additionalProperties": False,
        },
    }


# ==============================================================================
# SECTION 12: Canonical Entrypoint (run)
# ==============================================================================


def run(
    target: str,
    mode: Literal["summary", "detailed"] = "detailed",
    limit: int = DEFAULT_LIMIT,
    timeout: Optional[float] = None,
    file_pattern: Optional[str] = None,
    max_line_length: int = DEFAULT_MAX_LINE_LENGTH,
    severity: Literal["all", "warning", "error"] = "all",
    recursive: bool = False,
    include_hidden: bool = False,
    use_cache: bool = False,
    dry_run: bool = False,
) -> dict[str, Any]:
    """
    Lint and validate code/data files for syntax errors, formatting, and style.

    Args:
        target: Target file or directory path.
        mode: 'summary' or 'detailed'.
        limit: Maximum number of lint issues to report.
        timeout: Execution timeout in seconds.
        file_pattern: Optional glob filter pattern (e.g. '*.py').
        max_line_length: Maximum allowed line length.
        severity: Filter issues by severity: 'all', 'warning', or 'error'.
        recursive: Whether to scan child directories recursively.
        include_hidden: Include hidden files/folders.
        use_cache: Enable deterministic result caching.
        dry_run: Read-only compatibility flag.

    Returns:
        JSON-serializable dictionary with pass/fail status and issue diagnostics.
    """
    return execute_tool(
        target=target,
        mode=mode,
        limit=limit,
        timeout=timeout,
        file_pattern=file_pattern,
        max_line_length=max_line_length,
        severity=severity,
        recursive=recursive,
        include_hidden=include_hidden,
        use_cache=use_cache,
        dry_run=dry_run,
        no_color=True,
        verbose=False,
        quiet=True,
    )


# ==============================================================================
# SECTION 13: CLI Output Routing
# ==============================================================================


def write_llm_output(
    data: Mapping[str, Any],
    output_format: str = "jsonl",
) -> None:
    indent = 2 if output_format == "json" else None
    payload = json.dumps(data, ensure_ascii=False, cls=ToolJSONEncoder, indent=indent)
    out_path = os.environ.get("LLM_OUTPUT", "/dev/stdout")

    if out_path in {"/dev/stdout", "/dev/fd/1", "-"}:
        sys.stdout.write(payload + "\n")
        sys.stdout.flush()
        return

    try:
        destination = Path(out_path).expanduser()
        destination.parent.mkdir(parents=True, exist_ok=True)
        with destination.open("a", encoding="utf-8") as handle:
            handle.write(payload + "\n")
    except OSError as exc:
        _cprint(f"{NEON_RED}LLM_OUTPUT write failed:{RESET} {exc}", no_color=False)
        sys.stdout.write(payload + "\n")
        sys.stdout.flush()


# ==============================================================================
# SECTION 14: Self-Test Harness
# ==============================================================================


def self_test() -> int:
    failures: list[str] = []

    def check(name: str, condition: bool) -> None:
        if not condition:
            failures.append(name)

    with tempfile.TemporaryDirectory(prefix="file-lint-test-") as tmp:
        root = Path(tmp)

        # 1. Clean Python file
        clean_py = root / "clean.py"
        clean_py.write_text("def add(a: int, b: int) -> int:\n    return a + b\n", encoding="utf-8")

        # 2. Syntax-error Python file
        bad_py = root / "bad_syntax.py"
        bad_py.write_text("def broken(\n", encoding="utf-8")

        # 3. Bad JSON file
        bad_json = root / "bad.json"
        bad_json.write_text('{"key": "value",}\n', encoding="utf-8")

        # 4. Trailing whitespace & long line file
        messy_txt = root / "messy.txt"
        messy_txt.write_text("hello world   \n" + "x" * 120 + "\nno_eof_newline", encoding="utf-8")

        # Test Clean file
        res_clean = run(str(clean_py))
        check("Clean file passes", res_clean.get("passed") is True)
        check("Clean file zero issues", res_clean.get("issue_count") == 0)

        # Test Bad Python
        res_bad_py = run(str(bad_py))
        check("Bad syntax detected", res_bad_py.get("passed") is False)
        check("Syntax error code present", any(i.get("code") == "E901" for i in res_bad_py.get("issues", [])))

        # Test Bad JSON
        res_bad_json = run(str(bad_json))
        check("Bad JSON detected", res_bad_json.get("passed") is False)
        check("JSON error code present", any(i.get("code") == "E271" for i in res_bad_json.get("issues", [])))

        # Test Formatting (Whitespace & Length)
        res_messy = run(str(messy_txt), max_line_length=100)
        check("Messy text flags issues", res_messy.get("issue_count", 0) >= 3)

        # Test Schema Generation
        schema = generate_tool_schema()
        check("Schema requires target only", schema["parameters"]["required"] == ["target"])

        # Test Caching
        cache_dir = root / "cache"
        cache = ToolCache(cache_dir=cache_dir, ttl=60)
        cache.set("k1", {"success": True, "passed": True})
        check("Cache read-back", cache.get("k1") == {"success": True, "passed": True})
        check("Cache clear", cache.clear() >= 1)

    if failures:
        for f in failures:
            print(f"FAIL: {f}", file=sys.stderr)
        return EXIT_ERROR

    print("PASS: file_lint self-tests passed successfully.")
    return EXIT_SUCCESS


# ==============================================================================
# SECTION 15: CLI Parser & Main
# ==============================================================================


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog=__tool_name__,
        description=f"{__tool_description__} (v{__version__})",
    )
    parser.add_argument("--target", "-t", metavar="PATH")
    parser.add_argument(
        "--mode",
        choices=sorted(VALID_MODES),
        **env_default("LLM_TOOL_MODE", "detailed"),
    )
    parser.add_argument(
        "--limit",
        type=int,
        **env_default("LLM_TOOL_LIMIT", DEFAULT_LIMIT),
        metavar="NUM",
    )
    parser.add_argument("--timeout", type=float, metavar="SEC")
    parser.add_argument("--file-pattern", metavar="PATTERN")
    parser.add_argument("--max-line-length", type=int, default=DEFAULT_MAX_LINE_LENGTH, metavar="NUM")
    parser.add_argument("--severity", choices=sorted(VALID_SEVERITIES), default="all")
    parser.add_argument("--output-format", choices=sorted(VALID_OUTPUT_FORMATS), default="jsonl")
    parser.add_argument("--cache-ttl", type=int, default=DEFAULT_CACHE_TTL, metavar="SEC")
    parser.add_argument("--cache-dir", **env_default("LLM_TOOL_CACHE_DIR"), metavar="PATH")
    parser.add_argument("--env-var", action="append", dest="env_vars", metavar="KEY=VALUE")
    parser.add_argument("--recursive", action="store_true")
    parser.add_argument("--include-hidden", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--use-cache", action="store_true")
    parser.add_argument("--clear-cache", action="store_true")
    parser.add_argument("--schema", action="store_true")
    parser.add_argument("--self-test", action="store_true")
    parser.add_argument("--json-only", action="store_true")
    parser.add_argument("--quiet", "-q", action="store_true")
    parser.add_argument("--no-color", action="store_true")
    parser.add_argument("--verbose", "-v", action="store_true")

    return parser


def main(argv: Optional[list[str]] = None) -> int:
    args = _build_parser().parse_args(argv)

    if args.self_test:
        return self_test()

    if args.schema:
        sys.stdout.write(
            json.dumps(
                generate_tool_schema(),
                indent=2,
                ensure_ascii=False,
                cls=ToolJSONEncoder,
            )
            + "\n"
        )
        return EXIT_SUCCESS

    if args.clear_cache:
        cache = ToolCache(cache_dir=args.cache_dir, ttl=args.cache_ttl)
        removed = invalidate_cache(cache)
        if not args.quiet and not args.json_only:
            _cprint(
                f"{NEON_GREEN}✓ Cleared {removed} cache record(s).{RESET}",
                no_color=args.no_color,
            )
        return EXIT_SUCCESS

    if not args.target:
        payload = ToolError(
            "--target is required.",
            EXIT_INVALID_INPUT,
            "ValidationError",
        ).to_dict()
        if args.json_only:
            write_llm_output(payload, args.output_format)
        else:
            _cprint(f"{NEON_RED}Error:{RESET} --target is required.", no_color=args.no_color)
        return EXIT_INVALID_INPUT

    result = execute_tool(
        target=args.target,
        mode=args.mode,
        limit=args.limit,
        timeout=args.timeout,
        file_pattern=args.file_pattern,
        max_line_length=args.max_line_length,
        severity=args.severity,
        env_vars=args.env_vars,
        recursive=args.recursive,
        include_hidden=args.include_hidden,
        use_cache=args.use_cache,
        no_color=args.no_color,
        verbose=args.verbose,
        cache_ttl=args.cache_ttl,
        cache_dir=args.cache_dir,
        output_format=args.output_format,
        dry_run=args.dry_run,
        quiet=args.quiet,
    )

    if not args.json_only:
        print_human_readable_ui(
            result,
            no_color=args.no_color,
            quiet=args.quiet,
        )

    write_llm_output(result, args.output_format)
    return int(result.get("exit_code", EXIT_SUCCESS))


if __name__ == "__main__":
    raise SystemExit(main())
