#!/usr/bin/env python3
# ==============================================================================
# custom_tool.py — Pyrmethus AIChat / llm-functions Master Template v5.0.0
# AIChat/llm-functions compatible · Typed Python run() API · Safe JSON · Neon CLI
#
# Compatibility-first template:
#   - The public `run()` function is the LLM-facing contract.
#   - `run()` returns a JSON-serializable Python object and NEVER prints.
#   - Human/CLI output goes to stderr; machine output goes to stdout/LLM_OUTPUT.
#   - No third-party Python dependencies are required.
#
# @describe Master Python tool template for Sigoden llm-functions. Replace the
# @tool-purpose section and the implementation inside execute_tool() while
# preserving the public run() contract and CLI bridge.
#
# @meta require-tools aichat
# @meta python-entrypoint run
# @meta read-only
#
# @option --target! <PATH>               Target file or directory path (required)
# @option --mode <MODE>                  Execution mode: summary/detailed
# @option --limit <NUM>                  Maximum items to process
# @option --timeout <SEC>                Maximum execution time
# @option --file-pattern <PATTERN>       Filename glob filter
# @option --max-size <BYTES>             Maximum accepted file size
# @option --cache-ttl <SEC>              Cache TTL
# @option --cache-dir <PATH>             Cache directory
# @option --env-var <KEY=VALUE>          Safe environment override (repeatable)
# @flag   --recursive                    Scan directories recursively
# @flag   --include-hidden               Include hidden entries
# @flag   --dry-run                      Do not write cache/state
# @flag   --use-cache                    Enable deterministic result caching
# @flag   --clear-cache                  Clear this tool's cache
# @flag   --schema                       Print the LLM-facing JSON schema
# @flag   --self-test                    Run dependency-free template tests
# @flag   --json-only                    Suppress human CLI UI
# @flag   --quiet                        Suppress progress/UI
# @flag   --no-color                     Disable ANSI output
# @flag   --verbose                      Enable debug logging
#
# @env LLM_OUTPUT=/dev/stdout            Optional llm-functions output destination
# @env LLM_TOOL_MODE=summary             CLI default override
# @env LLM_TOOL_LIMIT=100                CLI default override
# @env LLM_TOOL_CACHE_DIR                Cache directory override
# ==============================================================================

from __future__ import annotations

import argparse
import contextlib
import dataclasses
import fnmatch
import hashlib
import ipaddress
import json
import logging
import math
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

# ==============================================================================
# TOOL IDENTITY
# ==============================================================================

__version__ = "5.0.0"
__tool_name__ = "custom_tool"
__tool_description__ = (
    "Safely inspect a file or directory and return structured, "
    "LLM-friendly metadata and matched paths."
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
    "get_agent_var",
    "get_builtin_var",
    "get_execution_context",
    "__version__",
]

# ==============================================================================
# SECTION 1: Constants, Exit Codes & Models
# ==============================================================================

EXIT_SUCCESS = 0
EXIT_ERROR = 1
EXIT_FILE_NOT_FOUND = 2
EXIT_TIMEOUT = 124
EXIT_PERMISSION_DENIED = 126
EXIT_INVALID_INPUT = 127
EXIT_INTERRUPTED = 130

MAX_LIMIT = 100_000
MAX_TIMEOUT = 86_400.0
MAX_CACHE_TTL = 31_536_000
MAX_CACHE_SIZE_MB = 512
DEFAULT_LIMIT = 100
DEFAULT_CACHE_TTL = 3_600
DEFAULT_CACHE_SIZE_MB = 512
SUMMARY_PREVIEW = 8

VALID_MODES = frozenset({"summary", "detailed"})
VALID_OUTPUT_FORMATS = frozenset({"json", "jsonl"})

# Environment variables that can change code loading, command resolution, or
# privilege behavior must never be overridden by a tool argument.
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

# ANSI is presentation-only. It must never enter the LLM result.
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

# ==============================================================================
# SECTION 2: Enums & Structured Errors
# ==============================================================================


class ExecutionMode(str, Enum):
    SUMMARY = "summary"
    DETAILED = "detailed"


class OutputFormat(str, Enum):
    JSON = "json"
    JSONL = "jsonl"


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
# SECTION 3: JSON Serialization / Normalization
# ==============================================================================


class ToolJSONEncoder(json.JSONEncoder):
    """Serialize common stdlib/dynamic objects without crashing the bridge."""

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
    """Round-trip a value through the same JSON contract used by the bridge."""
    return json.loads(json.dumps(value, ensure_ascii=False, cls=ToolJSONEncoder))


# ==============================================================================
# SECTION 4: Terminal UI / Logging
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
    width = 28
    filled = min(width, int(width * percent / 100.0))
    bar = "█" * filled + "░" * (width - filled)
    _cprint(
        f"\r{NEON_CYAN}Progress{RESET} "
        f"[{NEON_GREEN}{bar}{RESET}] {percent:5.1f}% "
        f"{DIM}{str(message)[:40]}{RESET}",
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
    """Human UI is deliberately stderr-only and never part of the LLM payload."""
    if quiet or not _is_tty(no_color):
        return

    success = bool(data.get("success"))
    color = NEON_GREEN if success else NEON_RED
    symbol = "✓" if success else "✗"
    status = "SUCCESS" if success else "FAILED"
    border = "─" * 70

    _cprint(f"{NEON_PURPLE}╭{border}╮{RESET}", no_color=no_color)
    _cprint(
        f"{NEON_PURPLE}│{RESET} {NEON_PINK}{BOLD}⚡ AIChat TOOL v{__version__}{RESET} "
        f"{color}{BOLD}{symbol} {status}{RESET}",
        no_color=no_color,
    )
    _cprint(f"{NEON_PURPLE}├{border}┤{RESET}", no_color=no_color)
    _cprint(
        f"{NEON_PURPLE}│{RESET} {NEON_CYAN}Request:{RESET}  "
        f"{data.get('request_id', 'N/A')}",
        no_color=no_color,
    )
    _cprint(
        f"{NEON_PURPLE}│{RESET} {NEON_CYAN}Target:{RESET}   "
        f"{data.get('target', 'N/A')}",
        no_color=no_color,
    )
    _cprint(
        f"{NEON_PURPLE}│{RESET} {NEON_CYAN}Mode:{RESET}     "
        f"{data.get('mode', 'N/A')}",
        no_color=no_color,
    )
    _cprint(
        f"{NEON_PURPLE}│{RESET} {NEON_CYAN}Count:{RESET}    "
        f"{NEON_YELLOW}{data.get('count', 0)}{RESET}",
        no_color=no_color,
    )
    _cprint(
        f"{NEON_PURPLE}│{RESET} {NEON_CYAN}Cached:{RESET}   "
        f"{NEON_YELLOW}{data.get('cached', False)}{RESET}",
        no_color=no_color,
    )
    _cprint(
        f"{NEON_PURPLE}│{RESET} {NEON_CYAN}Duration:{RESET} "
        f"{DIM}{data.get('duration_ms', 0)} ms{RESET}",
        no_color=no_color,
    )

    warnings = data.get("warnings") or []
    if warnings:
        _cprint(f"{NEON_PURPLE}├{border}┤{RESET}", no_color=no_color)
        for warning in warnings[:8]:
            _cprint(
                f"{NEON_PURPLE}│{RESET} {NEON_YELLOW}⚠{RESET} {warning}",
                no_color=no_color,
            )

    if not success:
        _cprint(f"{NEON_PURPLE}├{border}┤{RESET}", no_color=no_color)
        _cprint(
            f"{NEON_PURPLE}│{RESET} {NEON_RED}{data.get('type', 'Error')}:{RESET} "
            f"{data.get('error', 'Unknown error')}",
            no_color=no_color,
        )

    items = data.get("items") or []
    if items:
        _cprint(f"{NEON_PURPLE}├{border}┤{RESET}", no_color=no_color)
        _cprint(
            f"{NEON_PURPLE}│{RESET} {BOLD}Preview:{RESET}",
            no_color=no_color,
        )
        for item in list(items)[:SUMMARY_PREVIEW]:
            _cprint(
                f"{NEON_PURPLE}│{RESET}   {NEON_CYAN}›{RESET} {item}",
                no_color=no_color,
            )
        if len(items) > SUMMARY_PREVIEW:
            _cprint(
                f"{NEON_PURPLE}│{RESET}   {DIM}… and "
                f"{len(items) - SUMMARY_PREVIEW} more{RESET}",
                no_color=no_color,
            )

    _cprint(f"{NEON_PURPLE}╰{border}╯{RESET}", no_color=no_color)


# ==============================================================================
# SECTION 5: AIChat / Agent Environment Helpers
# ==============================================================================


def get_agent_var(name: str, default: str = "") -> str:
    """Read LLM_AGENT_VAR_<NAME> supplied by AIChat/agent execution."""
    if not name:
        return default
    return os.environ.get(f"LLM_AGENT_VAR_{name.upper()}", default)


def get_builtin_var(name: str) -> Optional[str]:
    """Read AIChat built-in agent variables with tolerant casing."""
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
    """Return useful runtime metadata without exposing secrets."""
    prefix = os.environ.get("PREFIX", "")
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
            "is_termux": "com.termux" in prefix or Path("/data/data/com.termux").exists(),
        },
    }


def resolve_agent_path(target: str) -> Path:
    """Resolve relative paths against AIChat's __cwd__ when available."""
    if "\x00" in target:
        raise ToolError(
            "Target contains a null byte.",
            EXIT_INVALID_INPUT,
            "ValidationError",
        )

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
# SECTION 6: Security & Input Validation
# ==============================================================================


class SecurityValidator:
    """Validation helpers for a read-only, dependency-free tool template."""

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
        """Parse env overrides without permitting loader/path hijacking."""
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
    mode: str = "summary",
    limit: Optional[int] = DEFAULT_LIMIT,
    timeout: Optional[float] = None,
    file_pattern: Optional[str] = None,
    max_size: Optional[int] = None,
    output_format: str = "jsonl",
) -> Optional[dict[str, Any]]:
    """Strictly validate public/CLI parameters."""
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

    if max_size is not None and max_size < 0:
        return ToolError(
            "max_size must be >= 0.",
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
# SECTION 7: Safe Cache Engine
# ==============================================================================


def build_cache_key(
    target_path: Path,
    mode: str,
    limit_val: int,
    file_pattern: Optional[str],
    recursive: bool,
    max_size: Optional[int],
    include_hidden: bool,
) -> str:
    """Build a deterministic cache key from all result-affecting inputs."""
    try:
        stat = target_path.stat()
        fingerprint = (
            f"{stat.st_mtime_ns}:{stat.st_size}:{getattr(stat, 'st_ino', 0)}"
        )
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
        str(max_size if max_size is not None else ""),
        str(bool(include_hidden)),
    )
    return "|".join(parts)


class ToolCache:
    """JSON cache with atomic writes, TTL, bounded storage, and corruption cleanup."""

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
                raise ValueError("Cache payload is not an object.")

            # Touch only after successful parsing.
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
                payload = json.dumps(
                    value,
                    ensure_ascii=False,
                    separators=(",", ":"),
                    cls=ToolJSONEncoder,
                )
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

        self._evict_if_needed()

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

    def _evict_if_needed(self) -> None:
        try:
            files = [p for p in self.cache_dir.glob("*.json") if p.is_file()]
            total = sum(p.stat().st_size for p in files)
            if total <= self.max_size_bytes:
                return

            files.sort(key=lambda p: p.stat().st_mtime)
            for path in files:
                if total <= self.max_size_bytes:
                    break
                try:
                    size = path.stat().st_size
                    path.unlink()
                    total -= size
                except OSError:
                    pass
        except OSError:
            pass


def invalidate_cache(cache: Optional[ToolCache] = None) -> int:
    return (cache or ToolCache()).clear()


# ==============================================================================
# SECTION 8: Graceful Cancellation
# ==============================================================================


class GracefulShutdown:
    """Signal-aware cancellation that is safe in main-thread and embedded use."""

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
# SECTION 9: Filesystem Scanner — REPLACEABLE TOOL-SPECIFIC CORE
# ==============================================================================


class FileScanner:
    """
    Example core implementation.

    When creating a real tool, this class/function is the primary section to
    replace. Keep the public run() signature stable unless the tool genuinely
    needs different LLM arguments.
    """

    def __init__(
        self,
        base_path: Path,
        recursive: bool,
        pattern: Optional[str],
        max_size: Optional[int],
        include_hidden: bool,
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
        self.max_size = max_size
        self.include_hidden = include_hidden
        self.limit = limit
        self.timeout = timeout
        self.start_time = start_time
        self.shutdown = shutdown
        self.progress = progress
        self.no_color = no_color
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

    def _is_candidate(self, path: Path, explicit: bool = False) -> bool:
        # An explicitly requested hidden file is allowed; hidden children are
        # filtered unless --include-hidden is enabled.
        if not explicit and not self.include_hidden and path.name.startswith("."):
            return False

        if self.pattern and not fnmatch.fnmatchcase(path.name, self.pattern):
            return False

        if self.max_size is not None and path.is_file():
            try:
                if path.stat().st_size > self.max_size:
                    return False
            except OSError:
                return False

        return True

    def scan(self) -> list[str]:
        # limit=0 is a valid request and must always produce zero items.
        if self.limit == 0:
            return []

        if not self.base_path.is_dir():
            return [str(self.base_path)] if self._is_candidate(self.base_path, True) else []

        results: list[str] = []
        self._walk_dir(self.base_path, results)
        return results

    def _walk_dir(self, current_dir: Path, results: list[str]) -> None:
        self._check_stop()

        try:
            real_dir = str(current_dir.resolve(strict=False))
        except OSError:
            return

        # Never recurse through the same resolved directory twice.
        if real_dir in self.visited_dirs:
            return
        self.visited_dirs.add(real_dir)

        try:
            entries = sorted(os.scandir(current_dir), key=lambda item: item.name)
        except PermissionError:
            return
        except OSError:
            return

        subdirs: list[Path] = []

        for entry in entries:
            if len(results) >= self.limit:
                return

            self._check_stop()

            if not self.include_hidden and entry.name.startswith("."):
                continue

            path = Path(entry.path)

            try:
                if entry.is_dir(follow_symlinks=False):
                    if self.recursive:
                        subdirs.append(path)
                    continue

                if entry.is_file(follow_symlinks=True) and self._is_candidate(path):
                    results.append(str(path))
                    if self.progress and len(results) % 10 == 0:
                        print_progress(
                            len(results),
                            self.limit,
                            path.name,
                            no_color=self.no_color,
                        )
            except OSError:
                continue

        if self.recursive:
            for subdir in subdirs:
                if len(results) >= self.limit:
                    break
                self._walk_dir(subdir, results)


# ==============================================================================
# SECTION 10: Core Execution Engine
# ==============================================================================


def execute_tool(
    target: str,
    mode: str = "summary",
    limit: Optional[int] = DEFAULT_LIMIT,
    timeout: Optional[float] = None,
    file_pattern: Optional[str] = None,
    env_vars: Optional[list[str]] = None,
    recursive: bool = False,
    use_cache: bool = False,
    no_color: bool = True,
    verbose: bool = False,
    cache_ttl: int = DEFAULT_CACHE_TTL,
    cache_dir: Optional[str] = None,
    max_size: Optional[int] = None,
    output_format: str = "jsonl",
    include_hidden: bool = False,
    dry_run: bool = False,
    quiet: bool = True,
) -> dict[str, Any]:
    """
    Shared engine used by the typed AIChat API and standalone CLI.

    IMPORTANT FOR NEW TOOLS:
      Keep the result envelope stable and replace only the tool-specific core.
    """
    started = time.monotonic()
    request_id = f"req_{uuid.uuid4().hex[:12]}"
    warnings: list[str] = []

    setup_tool_logging(verbose, request_id)

    if not 0 <= cache_ttl <= MAX_CACHE_TTL:
        return ToolError(
            f"cache_ttl must be between 0 and {MAX_CACHE_TTL}.",
            EXIT_INVALID_INPUT,
            "ValidationError",
            trace_id=request_id,
        ).to_dict()

    bad = validate_inputs(
        target=target,
        mode=mode,
        limit=limit,
        timeout=timeout,
        file_pattern=file_pattern,
        max_size=max_size,
        output_format=output_format,
    )
    if bad:
        bad["request_id"] = request_id
        bad["timestamp"] = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
        return bad

    safe_env, env_warnings = SecurityValidator.sanitize_env_vars(env_vars)
    warnings.extend(env_warnings)

    # Apply only to this process. A child process is not spawned by this
    # template, so these values cannot escape to a subprocess.
    # Real tools that spawn commands should pass an explicit env mapping rather
    # than mutating os.environ.
    previous_env: dict[str, Optional[str]] = {}
    for key, value in safe_env.items():
        previous_env[key] = os.environ.get(key)
        os.environ[key] = value

    limit_val = DEFAULT_LIMIT if limit is None else limit

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
            max_size=max_size,
            include_hidden=include_hidden,
        )

        if use_cache and not dry_run:
            cached = cache.get(cache_key)
            if cached is not None:
                cached = dict(cached)
                cached["cached"] = True
                cached["request_id"] = request_id
                cached["duration_ms"] = round(
                    (time.monotonic() - started) * 1000,
                    2,
                )
                return cached

        with GracefulShutdown() as shutdown:
            scanner = FileScanner(
                base_path=target_path,
                recursive=recursive,
                pattern=file_pattern,
                max_size=max_size,
                include_hidden=include_hidden,
                limit=limit_val,
                timeout=timeout,
                start_time=started,
                shutdown=shutdown,
                progress=verbose and not quiet,
                no_color=no_color,
            )
            processed_items = scanner.scan()

        duration_ms = round((time.monotonic() - started) * 1000, 2)

        result: dict[str, Any] = {
            "success": True,
            "request_id": request_id,
            "timestamp": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
            "tool": __tool_name__,
            "tool_version": __version__,
            "target": str(target_path),
            "mode": mode,
            "count": len(processed_items),
            "items": (
                processed_items
                if mode == "detailed"
                else processed_items[:SUMMARY_PREVIEW]
            ),
            "truncated": mode == "summary" and len(processed_items) > SUMMARY_PREVIEW,
            "limit": limit_val,
            "recursive": recursive,
            "include_hidden": include_hidden,
            "file_pattern": file_pattern,
            "max_size": max_size,
            "dry_run": dry_run,
            "cached": False,
            "warnings": warnings,
            "duration_ms": duration_ms,
            "execution_ms": duration_ms,
            "context": get_execution_context(),
            "exit_code": EXIT_SUCCESS,
        }

        # Ensure the actual LLM bridge contract is JSON-safe before caching or
        # returning it.
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
            details={
                "duration_ms": duration_ms,
                "warnings": warnings,
            },
        ).to_dict(verbose=verbose)

    except Exception as exc:
        duration_ms = round((time.monotonic() - started) * 1000, 2)
        return ToolError(
            f"Unexpected tool error: {exc}",
            EXIT_ERROR,
            "UnhandledException",
            trace_id=request_id,
            details={
                "duration_ms": duration_ms,
                "warnings": warnings,
            },
        ).to_dict(verbose=verbose)

    finally:
        for key, old_value in previous_env.items():
            if old_value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = old_value


# ==============================================================================
# SECTION 11: LLM-FACING Schema
# ==============================================================================


def generate_tool_schema() -> dict[str, Any]:
    """
    Generate a JSON-schema-like declaration for the canonical run() function.

    Only task arguments belong here. CLI infrastructure flags such as
    --verbose, --no-color, --cache-dir, and --schema are intentionally excluded
    from the LLM-facing contract.
    """
    return {
        "name": "run",
        "description": __tool_description__,
        "parameters": {
            "type": "object",
            "properties": {
                "target": {
                    "type": "string",
                    "description": "File or directory path to inspect.",
                },
                "mode": {
                    "type": "string",
                    "enum": ["summary", "detailed"],
                    "description": "Summary preview or all matching items up to limit.",
                    "default": "summary",
                },
                "limit": {
                    "type": "integer",
                    "minimum": 0,
                    "maximum": MAX_LIMIT,
                    "description": "Maximum number of matching filesystem items.",
                    "default": DEFAULT_LIMIT,
                },
                "timeout": {
                    "type": ["number", "null"],
                    "exclusiveMinimum": 0,
                    "maximum": MAX_TIMEOUT,
                    "description": "Optional maximum execution time in seconds.",
                    "default": None,
                },
                "file_pattern": {
                    "type": ["string", "null"],
                    "description": "Optional filename glob, for example '*.py'.",
                    "default": None,
                },
                "recursive": {
                    "type": "boolean",
                    "description": "Recursively scan child directories.",
                    "default": False,
                },
                "include_hidden": {
                    "type": "boolean",
                    "description": "Include hidden files/directories.",
                    "default": False,
                },
                "max_size": {
                    "type": ["integer", "null"],
                    "minimum": 0,
                    "description": "Exclude files larger than this many bytes.",
                    "default": None,
                },
                "use_cache": {
                    "type": "boolean",
                    "description": "Reuse a matching cached result.",
                    "default": False,
                },
                "dry_run": {
                    "type": "boolean",
                    "description": "Compatibility flag; this example tool is read-only.",
                    "default": False,
                },
            },
            # Critical fix: only truly required LLM arguments are required.
            "required": ["target"],
            "additionalProperties": False,
        },
    }


# ==============================================================================
# SECTION 12: CANONICAL AIChat / llm-functions ENTRYPOINT
# ==============================================================================


def run(
    target: str,
    mode: Literal["summary", "detailed"] = "summary",
    limit: int = DEFAULT_LIMIT,
    timeout: Optional[float] = None,
    file_pattern: Optional[str] = None,
    recursive: bool = False,
    include_hidden: bool = False,
    max_size: Optional[int] = None,
    use_cache: bool = False,
    dry_run: bool = False,
) -> dict[str, Any]:
    """
    Inspect a file or directory and return structured JSON-serializable results.

    This is the canonical LLM-facing entrypoint for Sigoden llm-functions.

    Args:
        target: File or directory path to inspect. Relative paths are resolved
            against AIChat's agent working directory when __cwd__ is available.
        mode: "summary" returns a short preview; "detailed" returns all matches
            up to limit.
        limit: Maximum number of filesystem items to inspect/return.
        timeout: Optional maximum execution time in seconds.
        file_pattern: Optional filename glob such as "*.py" or "*.json".
        recursive: Recursively inspect child directories when true.
        include_hidden: Include dot-files and dot-directories when true.
        max_size: Optional maximum file size in bytes. Larger files are excluded.
        use_cache: Reuse a deterministic cached result when available.
        dry_run: Compatibility flag. This example scanner never modifies targets.

    Returns:
        A JSON-serializable dictionary containing success status, matched items,
        warnings, timing, request metadata, and execution context.

    IMPORTANT:
        Do not print from run(). Return the result. The llm-functions Python
        adapter/function-calling layer owns serialization of the return value.
    """
    return execute_tool(
        target=target,
        mode=mode,
        limit=limit,
        timeout=timeout,
        file_pattern=file_pattern,
        recursive=recursive,
        include_hidden=include_hidden,
        max_size=max_size,
        use_cache=use_cache,
        dry_run=dry_run,
        # LLM calls are machine-readable and silent.
        no_color=True,
        verbose=False,
        quiet=True,
    )


# ==============================================================================
# SECTION 13: CLI OUTPUT ROUTING
# ==============================================================================


def write_llm_output(
    data: Mapping[str, Any],
    output_format: str = "jsonl",
) -> None:
    """
    Standalone CLI bridge.

    stdout remains machine-readable. Human UI/logging is stderr-only.
    LLM_OUTPUT can redirect the machine payload to a file expected by the
    llm-functions/argc execution bridge.
    """
    indent = 2 if output_format == "json" else None
    payload = json.dumps(
        data,
        ensure_ascii=False,
        cls=ToolJSONEncoder,
        indent=indent,
    )

    out_path = os.environ.get("LLM_OUTPUT", "/dev/stdout")
    if out_path in {"/dev/stdout", "/dev/fd/1", "-"}:
        sys.stdout.write(payload + "\n")
        sys.stdout.flush()
        return

    try:
        destination = Path(out_path).expanduser()
        destination.parent.mkdir(parents=True, exist_ok=True)
        # Append preserves the common LLM_OUTPUT file contract.
        with destination.open("a", encoding="utf-8") as handle:
            handle.write(payload + "\n")
    except OSError as exc:
        _cprint(
            f"{NEON_RED}LLM_OUTPUT write failed:{RESET} {exc}",
            no_color=False,
        )
        sys.stdout.write(payload + "\n")
        sys.stdout.flush()


# ==============================================================================
# SECTION 14: Self-Test / Validation Harness
# ==============================================================================


def self_test() -> int:
    """Run dependency-free regression checks for template authors."""
    failures: list[str] = []

    def check(name: str, condition: bool) -> None:
        if not condition:
            failures.append(name)

    with tempfile.TemporaryDirectory(prefix="aichat-tool-test-") as tmp:
        root = Path(tmp)
        (root / "a.py").write_text("print('a')\n", encoding="utf-8")
        (root / "b.txt").write_text("hello\n", encoding="utf-8")
        hidden = root / ".hidden"
        hidden.write_text("hidden\n", encoding="utf-8")
        sub = root / "sub"
        sub.mkdir()
        (sub / "c.py").write_text("print('c')\n", encoding="utf-8")

        result = run(str(root), mode="detailed", recursive=True, file_pattern="*.py")
        check("run() succeeds", result.get("success") is True)
        check("glob finds both Python files", result.get("count") == 2)
        check("run() returns dict", isinstance(result, dict))
        check("run() JSON-safe", json_safe(result) == result)

        zero = run(str(root), limit=0, mode="detailed", recursive=True)
        check("limit=0 returns zero items", zero.get("count") == 0)

        hidden_result = run(str(root), mode="detailed", include_hidden=False)
        check("hidden children excluded", all(".hidden" not in x for x in hidden_result.get("items", [])))

        explicit_hidden = run(str(hidden), mode="detailed")
        check("explicit hidden target allowed", explicit_hidden.get("count") == 1)

        schema = generate_tool_schema()
        required = schema["parameters"]["required"]
        check("schema requires only target", required == ["target"])

        cache_dir = root / "cache"
        cache = ToolCache(cache_dir=cache_dir, ttl=60)
        cache.set("abc", {"success": True})
        check("cache round-trip", cache.get("abc") == {"success": True})
        check("cache clear", cache.clear() >= 1)

    if failures:
        for failure in failures:
            print(f"FAIL: {failure}", file=sys.stderr)
        return EXIT_ERROR

    print("PASS: AIChat / llm-functions Python template self-test")
    return EXIT_SUCCESS


# ==============================================================================
# SECTION 15: CLI Parser / Main
# ==============================================================================


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog=__tool_name__,
        description=f"{__tool_description__} Template v{__version__}",
    )

    parser.add_argument("--target", "-t", metavar="PATH")
    parser.add_argument(
        "--mode",
        choices=sorted(VALID_MODES),
        **env_default("LLM_TOOL_MODE", "summary"),
    )
    parser.add_argument(
        "--limit",
        type=int,
        **env_default("LLM_TOOL_LIMIT", DEFAULT_LIMIT),
        metavar="NUM",
    )
    parser.add_argument("--timeout", type=float, metavar="SEC")
    parser.add_argument("--file-pattern", metavar="PATTERN")
    parser.add_argument("--max-size", type=int, metavar="BYTES")
    parser.add_argument(
        "--output-format",
        choices=sorted(VALID_OUTPUT_FORMATS),
        default="jsonl",
    )
    parser.add_argument("--cache-ttl", type=int, default=DEFAULT_CACHE_TTL, metavar="SEC")
    parser.add_argument(
        "--cache-dir",
        **env_default("LLM_TOOL_CACHE_DIR"),
        metavar="PATH",
    )
    parser.add_argument(
        "--env-var",
        action="append",
        dest="env_vars",
        metavar="KEY=VALUE",
    )
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
            _cprint(
                f"{NEON_RED}Error:{RESET} --target is required.",
                no_color=args.no_color,
            )
        return EXIT_INVALID_INPUT

    result = execute_tool(
        target=args.target,
        mode=args.mode,
        limit=args.limit,
        timeout=args.timeout,
        file_pattern=args.file_pattern,
        env_vars=args.env_vars,
        recursive=args.recursive,
        use_cache=args.use_cache,
        no_color=args.no_color,
        verbose=args.verbose,
        cache_ttl=args.cache_ttl,
        cache_dir=args.cache_dir,
        max_size=args.max_size,
        output_format=args.output_format,
        include_hidden=args.include_hidden,
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


# ==============================================================================
# SECTION 16: Standalone Entry Point
# ==============================================================================

if __name__ == "__main__":
    raise SystemExit(main())
