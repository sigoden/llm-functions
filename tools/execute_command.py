#!/usr/bin/env python3
# ==============================================================================
# execute_command.py — Pyrmethus Command Executor v2.4.0-ASCENDED
# argc/aichat compatible · Termux · Secure shell command execution · Native Caching
#
# @describe Execute arbitrary shell command and return full output with complete runtime metadata.
#
# @meta require-tools aichat
#
# @option --command! <STRING>            Command to run (required)
# @option --timeout <DURATION>           Duration for the command (e.g. 60s, 1m, 2h)
# @option --connect-timeout <DURATION>   Connection timeout for curl commands (default: 10s)
# @option --max-time <DURATION>          Max transfer time for curl commands (default: matches --timeout)
# @option --working-dir <PATH>           Working directory for the command (default: current dir)
# @option --env <KEY=VALUE>              Extra environment variable (repeatable)
# @option --shell <SHELL>                Shell to use: bash/sh/zsh (default: bash)
# @option --stdin <STRING>               Optional stdin content to pipe to command
# @option --retry <INT>                  Number of retries on failure (default: 0)
# @option --retry-delay <DURATION>       Delay between retries (default: 1s)
# @flag   --use-cache                    Enable result caching for identical command operations
# @flag   --no-color                     Disable ANSI colour output
# @flag   --strip-ansi                   Strip ANSI codes from command output before returning
# @flag   --verbose                      Show extra debug info (PATH, shell, env vars)
# @flag   --dry-run                      Show what would be executed without running
#
# @env LLM_OUTPUT=/dev/stdout            Output path for LLM integration
# @env LLM_TOOL_CACHE_TTL=1h             Optional cache TTL override when --use-cache is enabled
# @env EXECUTE_COMMAND_UI_LINES=20       Maximum lines shown in the interactive stderr preview
# @env EXECUTE_COMMAND_MAX_OUTPUT_BYTES  Maximum output bytes to capture (default: 20MB)
# ==============================================================================

from __future__ import annotations

import argparse
import hashlib
import json
import logging
import math
import os
import re
import shlex
import shutil
import signal
import subprocess
import sys
import tempfile
import threading
import time
from contextlib import contextmanager
from datetime import date, datetime, timedelta
from enum import Enum
from pathlib import Path
from typing import Any, Iterator, Optional, Union

__version__ = "2.4.0"
__all__ = [
    "ToolCache",
    "ToolError",
    "__version__",
    "duration_to_seconds",
    "parse_duration",
    "execute_tool",
    "get_agent_var",
    "get_builtin_var",
    "get_execution_context",
    "inject_curl_timeouts",
    "interpret_exit_code",
    "run",
    "run_command",
    "run_command_full",
    "sanitize_path",
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

_DEFAULT_MAX_OUTPUT_BYTES = 20 * 1024 * 1024


def _positive_int_env(name: str, default: int, minimum: int = 0) -> int:
    """Parse a positive int env var with fallback."""
    try:
        value = int(os.environ.get(name, default))
        return max(minimum, value)
    except (TypeError, ValueError):
        return default


MAX_OUTPUT_BYTES = _positive_int_env(
    "EXECUTE_COMMAND_MAX_OUTPUT_BYTES", _DEFAULT_MAX_OUTPUT_BYTES
)


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
    """Custom JSON encoder handling Path, Enum, datetime, timedelta, bytes, sets, and objects safely."""

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
            return sorted(obj, key=str)
        if hasattr(obj, "to_dict") and callable(obj.to_dict):
            return obj.to_dict()
        try:
            return super().default(obj)
        except TypeError:
            return str(obj)


# ==============================================================================
# SECTION 2: Terminal Color Palette & UI Helpers
# ==============================================================================

NEON_PINK = "\033[38;5;198m"
NEON_CYAN = "\033[38;5;51m"
NEON_GREEN = "\033[38;5;46m"
NEON_ORANGE = "\033[38;5;202m"
NEON_PURPLE = "\033[38;5;129m"
NEON_YELLOW = "\033[38;5;226m"
NEON_RED = "\033[38;5;196m"
NEON_BLUE = "\033[38;5;33m"
NEON_MAGENTA = "\033[38;5;201m"
NEON_LIME = "\033[38;5;82m"
RESET = "\033[0m"
BOLD = "\033[1m"
DIM = "\033[2m"

GLOW_CYAN = NEON_CYAN + BOLD
GLOW_GREEN = NEON_GREEN + BOLD
GLOW_RED = NEON_RED + BOLD
GLOW_YELLOW = NEON_YELLOW + BOLD
GLOW_PINK = NEON_PINK + BOLD

BOX_TL = "╭"
BOX_TR = "╮"
BOX_BL = "╰"
BOX_BR = "╯"
BOX_V = "│"
BOX_H = "─"
BOX_LT = "├"
BOX_RT = "┤"

_COLOR_OVERRIDE: Optional[bool] = None

# Combined ANSI regex (compiled once, reused)
_ANSI_RE = re.compile(
    r"(?:"
    r"\x1b\[[0-?]*[ -/]*[@-~]"           # CSI sequences
    r"|"
    r"\x1b\][^\x07\x1b]*(?:\x07|\x1b\\)"  # OSC sequences
    r"|"
    r"\x1b[@-Z]"                          # Single-char controls
    r"|"
    r"\x1b\\"                             # ST
    r")"
)


def _strip_ansi(text: str) -> str:
    """Remove all ANSI escape sequences from text."""
    if not text or "\x1b" not in text:
        return text
    return _ANSI_RE.sub("", text)


def _is_tty() -> bool:
    """Return True if stderr is attached to an interactive, non-dumb terminal."""
    try:
        return sys.stderr.isatty() and os.environ.get("TERM", "").lower() not in (
            "dumb",
            "",
        )
    except (AttributeError, ValueError):
        return False


def _env_truthy(name: str) -> bool:
    """Return True if an environment variable is set to a truthy value."""
    return os.environ.get(name, "").strip().lower() in {
        "1",
        "true",
        "yes",
        "y",
        "on",
    }


def _color_enabled() -> bool:
    """Determine whether ANSI colour output should be used."""
    if _COLOR_OVERRIDE is not None:
        return _COLOR_OVERRIDE
    if "NO_COLOR" in os.environ:
        return False
    if _env_truthy("FORCE_COLOR"):
        return True
    return _is_tty()


def _cprint(text: str, end: str = "\n", file: Any = None) -> None:
    """Print pre-formatted ANSI text to stderr by default to keep stdout pure for LLM JSON."""
    target = file or sys.stderr
    if not _color_enabled():
        text = _strip_ansi(text)
    try:
        print(text, end=end, flush=True, file=target)
    except (BrokenPipeError, OSError):
        pass


def get_width() -> int:
    """Return current terminal column count based on stderr, constrained to reasonable bounds."""
    try:
        cols = os.get_terminal_size(sys.stderr.fileno()).columns
        return max(40, min(cols, 120))
    except (OSError, AttributeError, ValueError):
        return 80


@contextmanager
def _color_context(no_color: bool) -> Iterator[None]:
    """Context manager to temporarily override color settings."""
    global _COLOR_OVERRIDE
    previous = _COLOR_OVERRIDE
    _COLOR_OVERRIDE = False if no_color else None
    try:
        yield
    finally:
        _COLOR_OVERRIDE = previous


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
        "python_version": sys.version.split()[0],
        "platform": sys.platform,
        "os_name": os.name,
        "pid": os.getpid(),
    }


_ENV_KEY_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


def _parse_env_vars(
    env_vars: Optional[list[str]], warnings: Optional[list[str]] = None
) -> dict[str, str]:
    """Parse environment variables provided in KEY=VALUE format."""
    parsed: dict[str, str] = {}
    if not env_vars:
        return parsed

    for item in env_vars:
        if not item or "=" not in item:
            if warnings is not None:
                warnings.append(
                    f"Ignoring invalid --env value: {item!r} (expected KEY=VALUE)."
                )
            continue

        key, val = item.split("=", 1)
        key = key.strip()

        if not _ENV_KEY_RE.match(key):
            if warnings is not None:
                warnings.append(f"Ignoring invalid environment variable name: {key!r}.")
            continue

        parsed[key] = val  # Preserve value whitespace intentionally

    return parsed


def sanitize_path() -> None:
    """Remove llm-functions/bin entries from PATH to prevent recursive shadowing."""
    raw = os.environ.get("PATH", "")
    parts = []
    seen = set()
    for p in raw.split(os.pathsep):
        if not p:
            continue
        norm = os.path.normpath(p)
        if norm in seen:
            continue
        if (
            norm.endswith(os.path.join("llm-functions", "bin"))
            or os.path.basename(norm) == "llm-functions-bin"
        ):
            continue
        seen.add(norm)
        parts.append(p)
    os.environ["PATH"] = os.pathsep.join(parts)


# ==============================================================================
# SECTION 4: Native Caching & Signal Handlers
# ==============================================================================


class ToolCache:
    """JSON-backed caching utility with TTL support for expensive operations."""

    SCHEMA_VERSION = 2

    def __init__(self, cache_dir: Optional[Path] = None) -> None:
        if cache_dir:
            self.cache_dir = Path(cache_dir)
        elif "LLM_TOOL_CACHE_DIR" in os.environ:
            self.cache_dir = Path(os.environ["LLM_TOOL_CACHE_DIR"])
        else:
            self.cache_dir = Path.home() / ".cache" / "aichat_tools"

        self._disabled = False
        try:
            self.cache_dir.mkdir(parents=True, exist_ok=True)
        except OSError:
            self._disabled = True

    def _make_key(self, key_data: str) -> str:
        return hashlib.sha256(key_data.encode("utf-8")).hexdigest()

    def _cache_path(self, key_data: str) -> Path:
        return self.cache_dir / f"{self._make_key(key_data)}.json"

    def get(self, key_data: str, ttl_seconds: float = 3600.0) -> Optional[Any]:
        """Return cached value if present and fresh, otherwise None."""
        if self._disabled:
            return None

        cache_file = self._cache_path(key_data)
        try:
            st = cache_file.stat()
        except OSError:
            return None

        if time.time() - st.st_mtime > ttl_seconds:
            try:
                cache_file.unlink()
            except OSError:
                pass
            return None

        try:
            with open(cache_file, "r", encoding="utf-8") as fp:
                payload = json.load(fp)
            if not isinstance(payload, dict):
                return None
            if payload.get("schema") != self.SCHEMA_VERSION:
                return None
            return payload.get("value")
        except (OSError, json.JSONDecodeError, ValueError):
            return None

    def set(self, key_data: str, value: Any) -> None:
        """Atomically persist a cache entry as JSON."""
        if self._disabled:
            return

        cache_file = self._cache_path(key_data)
        tmp_file = cache_file.with_name(
            f"{cache_file.name}.tmp.{os.getpid()}.{time.time_ns()}"
        )
        try:
            payload = {
                "schema": self.SCHEMA_VERSION,
                "created": time.time(),
                "value": value,
            }
            with open(tmp_file, "w", encoding="utf-8") as fp:
                json.dump(payload, fp, ensure_ascii=False, cls=ToolJSONEncoder)
            tmp_file.replace(cache_file)
        except (OSError, TypeError, ValueError):
            try:
                tmp_file.unlink()
            except OSError:
                pass

    def clear(self) -> int:
        """Remove all cache entries. Returns count of removed files."""
        if self._disabled:
            return 0
        count = 0
        try:
            for f in self.cache_dir.glob("*.json"):
                try:
                    f.unlink()
                    count += 1
                except OSError:
                    pass
        except OSError:
            pass
        return count


# ==============================================================================
# SECTION 5: Duration Helpers & Parsing
# ==============================================================================

_DURATION_RE = re.compile(r"^([0-9]+(?:\.[0-9]+)?)\s*([a-zA-Z]*)$")
_UNIT_MULTIPLIERS: dict[str, float] = {
    "": 1.0, "s": 1.0, "sec": 1.0, "secs": 1.0, "second": 1.0, "seconds": 1.0,
    "ms": 0.001, "msec": 0.001, "msecs": 0.001, "millisecond": 0.001, "milliseconds": 0.001,
    "us": 1e-6, "usec": 1e-6, "microsecond": 1e-6, "microseconds": 1e-6,
    "m": 60.0, "min": 60.0, "mins": 60.0, "minute": 60.0, "minutes": 60.0,
    "h": 3600.0, "hr": 3600.0, "hrs": 3600.0, "hour": 3600.0, "hours": 3600.0,
    "d": 86400.0, "day": 86400.0, "days": 86400.0,
    "w": 604800.0, "week": 604800.0, "weeks": 604800.0,
}


def parse_duration(raw: Any, default: Optional[float] = None) -> Optional[float]:
    """Parse a duration value into seconds, raising ValueError on invalid input."""
    if raw is None:
        return default
    if isinstance(raw, bool):  # Guard against bool being int subclass
        raise ValueError(f"Invalid duration: {raw!r}")
    if isinstance(raw, (int, float)):
        value = float(raw)
        if not math.isfinite(value):
            raise ValueError(f"Invalid duration: {raw!r}")
        return value

    text = str(raw).strip()
    if not text:
        return default

    m = _DURATION_RE.match(text)
    if not m:
        try:
            value = float(text)
        except ValueError as exc:
            raise ValueError(f"Invalid duration: {raw!r}") from exc
        if not math.isfinite(value):
            raise ValueError(f"Invalid duration: {raw!r}")
        return value

    unit = m.group(2).lower()
    if unit not in _UNIT_MULTIPLIERS:
        raise ValueError(f"Unknown duration unit in: {raw!r}")

    value = float(m.group(1)) * _UNIT_MULTIPLIERS[unit]
    if not math.isfinite(value):
        raise ValueError(f"Invalid duration: {raw!r}")
    return value


def duration_to_seconds(raw: Union[str, int, float, None]) -> float:
    """Convert duration string (e.g. '30s', '100ms', '1m', '2h') or number to seconds float."""
    try:
        parsed = parse_duration(raw, default=0.0)
        return float(parsed if parsed is not None else 0.0)
    except (ValueError, TypeError):
        return 0.0


def seconds_to_human(sec: float) -> str:
    """Return compact human-readable duration representation."""
    if sec < 0.001:
        return f"{sec * 1_000_000:.0f}µs"
    if sec < 1.0:
        return f"{sec * 1000:.0f}ms"
    if sec < 60:
        return f"{sec:.1f}s"
    if sec < 3600:
        return f"{sec / 60:.1f}m"
    if sec < 86400:
        return f"{sec / 3600:.2f}h"
    return f"{sec / 86400:.2f}d"


# ==============================================================================
# SECTION 6: Curl / Wget Timeout Injection & Command Icons
# ==============================================================================


def _find_binary(name: str) -> str:
    """Locate absolute binary path, dynamically checking Termux environment directories first."""
    prefix = os.environ.get("PREFIX", "/data/data/com.termux/files/usr")
    candidates = [
        os.path.join(prefix, "bin", name),
        f"/usr/bin/{name}",
        f"/usr/local/bin/{name}",
    ]
    for c in candidates:
        try:
            if Path(c).is_file() and os.access(c, os.X_OK):
                return c
        except OSError:
            pass
    return shutil.which(name) or name


def _ceil_seconds(value: float) -> int:
    """Return a safe positive integer second count for network timeout flags."""
    try:
        return max(1, int(math.ceil(float(value))))
    except (ValueError, TypeError):
        return 1


def _safe_tokens(text: str) -> list[str]:
    """Best-effort tokenizer for flag detection."""
    try:
        return shlex.split(text, posix=True)
    except ValueError:
        return text.split()


def _has_long_flag(tokens: list[str], *flags: str) -> bool:
    """Return True if any long flag is present as --flag or --flag=value."""
    for token in tokens:
        for flag in flags:
            if token == flag or token.startswith(flag + "="):
                return True
    return False


def _has_short_flag(tokens: list[str], letter: str) -> bool:
    """Return True if a short flag letter appears in a clustered short option."""
    for token in tokens:
        if not token.startswith("-") or token.startswith("--") or len(token) <= 1:
            continue
        body = token[1:]
        if body.lstrip("0123456789.") == "":
            continue
        if letter in body:
            return True
    return False


def _split_shell_operators(cmd: str) -> list[tuple[str, bool]]:
    """
    Split command text into segments and operators while respecting quotes and escapes.

    Returns a list of (text, is_operator) tuples.
    """
    segments: list[tuple[str, bool]] = []
    current: list[str] = []
    i = 0
    n = len(cmd)
    in_single = False
    in_double = False
    escaped = False

    while i < n:
        ch = cmd[i]

        if escaped:
            current.append(ch)
            escaped = False
            i += 1
            continue

        if ch == "\\" and not in_single:
            current.append(ch)
            escaped = True
            i += 1
            continue

        if ch == "'" and not in_double:
            in_single = not in_single
            current.append(ch)
            i += 1
            continue

        if ch == '"' and not in_single:
            in_double = not in_double
            current.append(ch)
            i += 1
            continue

        if not in_single and not in_double:
            op: Optional[str] = None

            if ch == "&":
                if i + 1 < n and cmd[i + 1] == "&":
                    op = "&&"
                elif i + 1 < n and cmd[i + 1] == ">":
                    op = None  # preserve &> redirection as literal text
                else:
                    op = "&"
            elif ch == "|":
                if i + 1 < n and cmd[i + 1] == "|":
                    op = "||"
                else:
                    op = "|"
            elif ch in (";", "\n"):
                op = ch

            if op is not None:
                segments.append(("".join(current), False))
                segments.append((op, True))
                current = []
                i += len(op)
                continue

        current.append(ch)
        i += 1

    segments.append(("".join(current), False))
    return segments


_CURL_BIN_RE = re.compile(r"^(?P<bin>(?:[^\s]+/)?curl)(?P<sep>\s|$)")
_WGET_BIN_RE = re.compile(r"^(?P<bin>(?:[^\s]+/)?wget)(?P<sep>\s|$)")


def _inject_curl_timeouts_single(
    cmd_segment: str, connect_timeout: float, max_time: float
) -> str:
    """Inject timeouts into a single command segment."""
    if not cmd_segment.strip():
        return cmd_segment

    stripped = cmd_segment.lstrip()
    leading = cmd_segment[: len(cmd_segment) - len(stripped)]
    trailing = cmd_segment[len(cmd_segment.rstrip()) :]
    tokens = _safe_tokens(stripped)

    m = _CURL_BIN_RE.match(stripped)
    if m:
        bin_token = m.group("bin")
        curl_bin = bin_token if "/" in bin_token else _find_binary("curl")
        rest = stripped[m.end() :].lstrip()

        flags: list[str] = []
        if not _has_long_flag(tokens, "--connect-timeout"):
            flags += ["--connect-timeout", str(_ceil_seconds(connect_timeout))]
        if not _has_long_flag(tokens, "--max-time"):
            flags += ["--max-time", str(_ceil_seconds(max_time))]
        if not _has_long_flag(tokens, "--retry"):
            flags += ["--retry", "3", "--retry-delay", "2"]

        silent_present = _has_long_flag(tokens, "--silent") or _has_short_flag(
            tokens, "s"
        )
        if not silent_present:
            flags.append("--silent")

        if flags:
            return f"{leading}{curl_bin} {' '.join(flags)} {rest}".rstrip() + trailing
        return f"{leading}{curl_bin} {rest}".rstrip() + trailing

    m = _WGET_BIN_RE.match(stripped)
    if m:
        bin_token = m.group("bin")
        wget_bin = bin_token if "/" in bin_token else _find_binary("wget")
        rest = stripped[m.end() :].lstrip()

        extra: list[str] = []
        if not (_has_long_flag(tokens, "--timeout") or _has_short_flag(tokens, "T")):
            extra.append(f"--timeout={_ceil_seconds(max_time)}")

        verbose_present = _has_long_flag(tokens, "--verbose") or _has_short_flag(
            tokens, "v"
        )
        no_verbose_present = (
            _has_long_flag(tokens, "--no-verbose") or "-nv" in tokens
        )
        if not no_verbose_present and not verbose_present:
            extra.append("--no-verbose")

        if extra:
            return f"{leading}{wget_bin} {' '.join(extra)} {rest}".rstrip() + trailing
        return f"{leading}{wget_bin} {rest}".rstrip() + trailing

    return cmd_segment


def inject_curl_timeouts(cmd: str, connect_timeout: float, max_time: float) -> str:
    """Prepend missing network timeouts and retry settings into curl or wget across shell pipelines."""
    if not cmd:
        return cmd

    # Fast-path: skip parsing if neither tool is referenced
    if "curl" not in cmd and "wget" not in cmd:
        return cmd

    segments = _split_shell_operators(cmd)
    modified: list[str] = []
    for text, is_operator in segments:
        if is_operator:
            modified.append(text)
        else:
            modified.append(
                _inject_curl_timeouts_single(text, connect_timeout, max_time)
            )
    return "".join(modified)


_ICON_PATTERNS: list[tuple[re.Pattern, str]] = [
    (re.compile(r"^(git|hg|svn)(\s|$)"), "📦"),
    (re.compile(r"^(npm|yarn|pnpm|apt|apt-get|yum|dnf|pacman|brew|pip|uv|bun|deno)(\s|$)"), "📦"),
    (re.compile(r"^(curl|wget|http|aria2c)(\s|$)"), "🌐"),
    (re.compile(r"^(python[0-9.]*|node|ruby|perl|php|lua|rustc|zig|go)(\s|$)"), "🐍"),
    (re.compile(r"^(docker|kubectl|helm|podman|k3s|terraform|ansible)(\s|$)"), "🐳"),
    (re.compile(r"^(ls|ll|la|dir|pwd|mkdir|rm|cp|mv|touch|cat|grep|rg|fd|bat|eza|find|awk|sed|tr|sort|uniq|wc|jq|yq)(\s|$)"), "📁"),
    (re.compile(r"^(ffmpeg|ffprobe|convert|magick|sox)(\s|$)"), "🎬"),
    (re.compile(r"^(ffuf|gobuster|nmap|nikto|sqlmap|hydra)(\s|$)"), "🔍"),
    (re.compile(r"^(ssh|scp|rsync|sftp|ftp)(\s|$)"), "🔐"),
    (re.compile(r"^(systemctl|service|journalctl|launchctl)(\s|$)"), "⚙️ "),
    (re.compile(r"^(make|cmake|ninja|gcc|g\+\+|clang|cargo)(\s|$)"), "🔨"),
    (re.compile(r"^(tar|zip|unzip|gzip|bzip2|xz|7z)(\s|$)"), "🗜️ "),
    (re.compile(r"^(mysql|psql|sqlite3|mongo|redis-cli)(\s|$)"), "🗄️ "),
    (re.compile(r"^(vi|vim|nvim|nano|emacs|code)(\s|$)"), "✏️ "),
]


def get_cmd_icon(cmd: str) -> str:
    """Return an emoji icon representing the leading command."""
    stripped = cmd.lstrip()
    for pattern, icon in _ICON_PATTERNS:
        if pattern.match(stripped):
            return icon
    return "⚡"


# ==============================================================================
# SECTION 7: Exit Code Interpretation & Shadow Hints
# ==============================================================================

_EXIT_CODES: dict[int, str] = {
    0: "Success",
    1: "General error",
    2: "Misuse of shell builtins or permission error",
    3: "No such process",
    13: "Permission denied",
    17: "File exists",
    28: "No space left on device",
    111: "Connection refused",
    124: "Command timed out",
    125: "timeout binary itself failed",
    126: "Permission denied (cannot execute)",
    127: "Command not found",
    128: "Invalid exit argument",
    129: "Received SIGHUP",
    130: "Terminated by Ctrl-C (SIGINT)",
    131: "Quit (SIGQUIT)",
    132: "Illegal instruction (SIGILL)",
    133: "Trace/breakpoint trap (SIGTRAP)",
    134: "Aborted (SIGABRT)",
    135: "Bus error (SIGBUS)",
    136: "Floating point exception (SIGFPE)",
    137: "Killed (SIGKILL) — often OOM killer",
    138: "User defined signal 1 (SIGUSR1)",
    139: "Segmentation fault (SIGSEGV)",
    140: "User defined signal 2 (SIGUSR2)",
    141: "Broken pipe (SIGPIPE)",
    142: "Alarm clock (SIGALRM)",
    143: "Terminated (SIGTERM)",
    152: "CPU time limit exceeded (SIGXCPU)",
    153: "File size limit exceeded (SIGXFSZ)",
    255: "Exit status out of range / SSH error",
}


def interpret_exit_code(code: int) -> str:
    """Return a human-readable description of a process exit code."""
    if code in _EXIT_CODES:
        return _EXIT_CODES[code]
    if code > 128:
        return f"Killed by signal {code - 128}"
    return f"Unknown exit code {code}"


_SHADOW_HINTS: list[tuple[str, str]] = [
    (
        "invalid json data",
        "⚠️  HINT: Output contains 'invalid JSON data'. Command may be shadowed by an AIChat tool symlink. Use 'command <cmd>' or full path.",
    ),
    (
        "function not found",
        "⚠️  HINT: 'function not found' — command intercepted by function/alias. Use absolute path.",
    ),
    (
        "command not found: aichat",
        "⚠️  HINT: 'aichat' missing or shadowed. Verify installation or PATH environment variable.",
    ),
    (
        "permission denied: /llm-functions",
        "⚠️  HINT: Intercepted by wrapper script in /llm-functions. Use direct binary path.",
    ),
]


def detect_shadowing_hint(output: str) -> Optional[str]:
    """Detect tool shadowing based on standard output strings."""
    if not output:
        return None
    text = _strip_ansi(output).lower()
    for trigger, hint in _SHADOW_HINTS:
        if trigger in text:
            return hint
    return None


# ==============================================================================
# SECTION 8: Terminal UI & Header/Footer Rendering
# ==============================================================================


def _border(width: int) -> str:
    return BOX_H * max(width, 10)


def _truncate(text: str, max_len: int) -> str:
    if max_len <= 1:
        return text[:max_len]
    return text if len(text) <= max_len else text[: max_len - 1] + "…"


def _ui_max_lines() -> int:
    return _positive_int_env("EXECUTE_COMMAND_UI_LINES", 20, minimum=0)


def print_human_readable_ui(data: dict[str, Any], no_color: bool = False) -> None:
    """Render a human-friendly box UI to stderr for interactive user sessions."""
    if not _is_tty():
        return

    with _color_context(no_color):
        success = bool(data.get("success", False))
        exit_code = int(data.get("exit_code", EXIT_ERROR))
        cmd = str(data.get("command") or "")
        duration_ms = data.get("duration_ms", 0.0)
        output = str(data.get("output") or "")
        hint = data.get("hint")
        cached = bool(data.get("cached", False))
        retries = int(data.get("retries_attempted", 0))

        warnings = data.get("warnings")
        if isinstance(warnings, str):
            warnings = [warnings]
        elif not isinstance(warnings, list):
            warnings = []

        bw = max(get_width() - 4, 20)
        border_str = _border(bw)
        icon = get_cmd_icon(cmd)
        display_cmd = _truncate(cmd.replace("\n", " ⏎ "), bw - 12)

        status_color = NEON_GREEN if success else NEON_RED
        status_symbol = "✓" if success else "✗"
        status_text = "SUCCESS" if success else "FAILED"

        _cprint(f"{NEON_PURPLE}{BOX_TL}{border_str}{BOX_TR}{RESET}")
        _cprint(
            f"{NEON_PINK} {icon} {GLOW_CYAN}[EXEC v{__version__}]{RESET} "
            f"{status_color}{BOLD}{status_symbol} {status_text}{RESET} "
            f"{NEON_YELLOW}›{RESET} {BOLD}{display_cmd}{RESET}"
        )

        # Duration line with optional retry info
        duration_str = seconds_to_human(duration_ms / 1000.0)
        stats_line = (
            f"{NEON_PURPLE}{BOX_V}{RESET} "
            f"{NEON_CYAN}Duration:{RESET} {NEON_LIME}{duration_str}{RESET}  "
            f"{NEON_CYAN}Exit:{RESET} {status_color}{exit_code}{RESET}  "
            f"{NEON_CYAN}Cached:{RESET} {NEON_YELLOW}{cached}{RESET}"
        )
        if retries > 0:
            stats_line += f"  {NEON_CYAN}Retries:{RESET} {NEON_ORANGE}{retries}{RESET}"
        _cprint(stats_line)

        if warnings:
            _cprint(f"{NEON_PURPLE}{BOX_LT}{border_str}{BOX_RT}{RESET}")
            for warning in warnings[:5]:
                _cprint(f"{NEON_PURPLE}{BOX_V}{RESET} {NEON_ORANGE}{warning}{RESET}")
            if len(warnings) > 5:
                _cprint(
                    f"{NEON_PURPLE}{BOX_V}{RESET} {DIM}… {len(warnings) - 5} more warnings{RESET}"
                )

        _cprint(f"{NEON_PURPLE}{BOX_LT}{border_str}{BOX_RT}{RESET}")

        if hint:
            _cprint(f"{NEON_PURPLE}{BOX_V}{RESET} {NEON_RED}{hint}{RESET}")

        if output.strip():
            lines = output.rstrip("\n").splitlines()
            limit = _ui_max_lines()
            if limit <= 0:
                _cprint(
                    f"{NEON_PURPLE}{BOX_V}{RESET} {DIM}… {len(lines)} output lines hidden by EXECUTE_COMMAND_UI_LINES{RESET}"
                )
            elif len(lines) > limit:
                # Show first portion and last portion for context
                head_lines = max(1, limit - 3)
                for line in lines[:head_lines]:
                    _cprint(f"{NEON_PURPLE}{BOX_V}{RESET} {line}")
                _cprint(
                    f"{NEON_PURPLE}{BOX_V}{RESET} {DIM}… {len(lines) - head_lines - 3} lines omitted …{RESET}"
                )
                for line in lines[-3:]:
                    _cprint(f"{NEON_PURPLE}{BOX_V}{RESET} {line}")
            else:
                for line in lines:
                    _cprint(f"{NEON_PURPLE}{BOX_V}{RESET} {line}")
        else:
            _cprint(
                f"{NEON_PURPLE}{BOX_V}{RESET} {DIM}(Command produced no stdout/stderr){RESET}"
            )

        if exit_code != 0:
            _cprint(f"{NEON_PURPLE}{BOX_LT}{border_str}{BOX_RT}{RESET}")
            _cprint(
                f"{NEON_PURPLE}{BOX_V}{RESET} {NEON_RED}Error Info:{RESET} {interpret_exit_code(exit_code)}"
            )

        _cprint(f"{NEON_PURPLE}{BOX_BL}{border_str}{BOX_BR}{RESET}")


# ==============================================================================
# SECTION 9: Shell Resolution & Core Execution Engine
# ==============================================================================

logger = logging.getLogger("execute_command")


def _configure_logging(verbose: bool) -> None:
    """Configure tool-local logging without polluting global basicConfig."""
    if verbose:
        logger.setLevel(logging.DEBUG)
        if not logger.handlers:
            handler = logging.StreamHandler(sys.stderr)
            handler.setFormatter(logging.Formatter("[DEBUG %(asctime)s] %(message)s", "%H:%M:%S"))
            logger.addHandler(handler)
            logger.propagate = False
    else:
        logger.setLevel(logging.WARNING)


def _termux_bin() -> str:
    return os.path.join(
        os.environ.get("PREFIX", "/data/data/com.termux/files/usr"), "bin"
    )


def _shell_candidates(shell: str) -> list[str]:
    key = (shell or "").lower()
    if key in {"bash", "sh", "zsh", "dash", "ksh", "fish"}:
        return [
            os.path.join(_termux_bin(), key),
            f"/bin/{key}",
            f"/usr/local/bin/{key}",
            f"/usr/bin/{key}",
            key,
        ]
    return [shell]


def _resolve_shell(shell: str) -> tuple[list[str], Optional[str]]:
    """Return ([executable, flag], warning) for requested shell string."""
    requested = shell or "bash"

    for candidate in _shell_candidates(requested):
        if not candidate:
            continue
        try:
            expanded = Path(candidate).expanduser()
            if expanded.is_file() and os.access(expanded, os.X_OK):
                return [str(expanded), "-c"], None
        except OSError:
            pass

        which = shutil.which(candidate)
        if which:
            return [which, "-c"], None

    fallback = "/bin/sh"
    for candidate in (os.path.join(_termux_bin(), "sh"), "/bin/sh", "sh"):
        try:
            expanded = Path(candidate).expanduser()
            if expanded.is_file() and os.access(expanded, os.X_OK):
                fallback = str(expanded)
                break
        except OSError:
            pass
        which = shutil.which(candidate)
        if which:
            fallback = which
            break

    return [fallback, "-c"], f"Shell '{requested}' not found; falling back to {fallback}."


def _signal_process_group(process: Optional[subprocess.Popen], sig: Optional[int]) -> None:
    """Send a signal to a process group when possible, with process-level fallback."""
    if process is None or sig is None or process.poll() is not None:
        return

    try:
        if hasattr(os, "killpg") and hasattr(os, "getpgid"):
            os.killpg(os.getpgid(process.pid), sig)
            return
    except (ProcessLookupError, PermissionError, OSError):
        pass

    try:
        if sig == getattr(signal, "SIGTERM", None):
            process.terminate()
        elif sig in (getattr(signal, "SIGKILL", None), 9):
            process.kill()
        elif hasattr(process, "send_signal"):
            process.send_signal(sig)
        else:
            process.kill()
    except (ProcessLookupError, OSError):
        try:
            process.kill()
        except (ProcessLookupError, OSError):
            pass


def _kill_process_group(process: Optional[subprocess.Popen]) -> None:
    """Immediately kill process group with fallback."""
    if process is None or process.poll() is not None:
        return
    sigkill = getattr(signal, "SIGKILL", None)
    if sigkill is not None:
        _signal_process_group(process, sigkill)
    else:
        try:
            process.kill()
        except (ProcessLookupError, OSError):
            pass


def _terminate_process_group(process: Optional[subprocess.Popen]) -> None:
    """Gracefully terminate process group, escalating to kill if needed."""
    if process is None or process.poll() is not None:
        return

    sigterm = getattr(signal, "SIGTERM", None)
    if sigterm is None:
        _kill_process_group(process)
        return

    _signal_process_group(process, sigterm)
    try:
        process.wait(timeout=0.4)
    except subprocess.TimeoutExpired:
        _kill_process_group(process)


def _install_signal_handlers(handler: Any) -> dict[int, Any]:
    """Install signal handlers when running in the main thread."""
    old_handlers: dict[int, Any] = {}
    try:
        if threading.current_thread() is not threading.main_thread():
            return old_handlers
    except Exception:
        return old_handlers

    for sig_name in ("SIGINT", "SIGTERM"):
        sig = getattr(signal, sig_name, None)
        if sig is None:
            continue
        try:
            old_handlers[sig] = signal.signal(sig, handler)
        except (ValueError, OSError):
            pass
    return old_handlers


def _restore_signal_handlers(old_handlers: dict[int, Any]) -> None:
    """Restore previously installed signal handlers."""
    for sig, old_handler in old_handlers.items():
        try:
            signal.signal(sig, old_handler)
        except (ValueError, OSError):
            pass


def run_command_full(
    cmd: str,
    timeout_sec: Optional[float],
    shell: str = "bash",
    cwd: Optional[str] = None,
    extra_env: Optional[dict[str, str]] = None,
    strip_ansi: bool = False,
    stdin_input: Optional[str] = None,
) -> dict[str, Any]:
    """
    Execute cmd via requested shell and return full low-level execution metadata.
    """
    warnings: list[str] = []
    shell_cmd, shell_warning = _resolve_shell(shell)
    if shell_warning:
        warnings.append(shell_warning)

    timeout_val: Optional[float]
    if timeout_sec is None:
        timeout_val = None
    else:
        try:
            timeout_val = float(timeout_sec)
            if not math.isfinite(timeout_val) or timeout_val <= 0:
                warnings.append(
                    "Timeout value is zero, negative, or non-finite; running without timeout."
                )
                timeout_val = None
        except (TypeError, ValueError):
            warnings.append(f"Invalid timeout value {timeout_sec!r}; running without timeout.")
            timeout_val = None

    env = {**os.environ, **(extra_env or {})}

    output = ""
    exit_code = EXIT_ERROR
    truncated = False
    bytes_count = 0
    process: Optional[subprocess.Popen] = None
    received_signal: Optional[int] = None
    timed_out = False

    def _signal_handler(signum: int, frame: Any) -> None:
        nonlocal received_signal
        received_signal = signum
        if process is not None:
            _kill_process_group(process)

    old_handlers = _install_signal_handlers(_signal_handler)

    try:
        with tempfile.TemporaryFile(prefix=".execute_command_", suffix=".out") as out_file:
            popen_kwargs: dict[str, Any] = {
                "stdout": out_file,
                "stderr": subprocess.STDOUT,
                "stdin": subprocess.PIPE if stdin_input is not None else subprocess.DEVNULL,
                "cwd": cwd or None,
                "env": env,
            }
            if os.name == "posix":
                popen_kwargs["start_new_session"] = True

            try:
                process = subprocess.Popen([*shell_cmd, cmd], **popen_kwargs)
            except FileNotFoundError:
                output = f"[Shell binary not found: {shell_cmd[0]}]\n"
                exit_code = EXIT_INVALID_INPUT
            except PermissionError as exc:
                output = f"[Permission denied executing shell: {shell_cmd[0]}: {exc}]\n"
                exit_code = EXIT_PERMISSION_DENIED
            except OSError as exc:
                output = f"[Executor OS error: {exc}]\n"
                exit_code = EXIT_ERROR
            except Exception as exc:
                output = f"[Executor failure: {exc}]\n"
                exit_code = EXIT_ERROR

            if process is not None:
                # Write stdin if provided
                if stdin_input is not None and process.stdin is not None:
                    try:
                        process.stdin.write(stdin_input.encode("utf-8", errors="replace"))
                        process.stdin.close()
                    except (BrokenPipeError, OSError) as exc:
                        warnings.append(f"Failed to write stdin: {exc}")

                try:
                    process.wait(timeout=timeout_val)
                except subprocess.TimeoutExpired:
                    timed_out = True
                    _terminate_process_group(process)
                    try:
                        process.wait(timeout=2.0)
                    except subprocess.TimeoutExpired:
                        _kill_process_group(process)
                        try:
                            process.wait(timeout=2.0)
                        except subprocess.TimeoutExpired:
                            pass
                except KeyboardInterrupt:
                    received_signal = getattr(signal, "SIGINT", 2)
                    _kill_process_group(process)
                    try:
                        process.wait(timeout=2.0)
                    except subprocess.TimeoutExpired:
                        pass
                finally:
                    if process.poll() is None:
                        _kill_process_group(process)
                        try:
                            process.wait(timeout=1.0)
                        except subprocess.TimeoutExpired:
                            pass

                    returncode = process.returncode
                    try:
                        out_file.seek(0)
                        raw_bytes = out_file.read(MAX_OUTPUT_BYTES + 1)
                    except OSError as exc:
                        raw_bytes = f"[Failed reading output buffer: {exc}]\n".encode("utf-8")

                    truncated = len(raw_bytes) > MAX_OUTPUT_BYTES
                    if truncated:
                        raw_bytes = (
                            raw_bytes[:MAX_OUTPUT_BYTES]
                            + b"\n... [Output truncated at output limit]\n"
                        )

                    output = raw_bytes.decode("utf-8", errors="replace")
                    output = output.replace("\r\n", "\n").replace("\r", "\n")
                    bytes_count = len(raw_bytes)

                    if returncode is None:
                        exit_code = EXIT_ERROR
                    elif returncode < 0:
                        exit_code = 128 + abs(returncode)
                    else:
                        exit_code = returncode

                    if timed_out:
                        suffix = timeout_val if timeout_val is not None else 0.0
                        output += f"\n[Timed out after {suffix:.1f}s]\n"
                        exit_code = EXIT_TIMEOUT

                    if received_signal == getattr(signal, "SIGINT", None):
                        output += "\n[Command execution interrupted by user]\n"
                        exit_code = EXIT_INTERRUPTED
                    elif received_signal == getattr(signal, "SIGTERM", None):
                        output += "\n[Command execution terminated]\n"
                        exit_code = 143

    except Exception as exc:
        output = f"[Executor failure: {exc}]\n"
        exit_code = EXIT_ERROR
    finally:
        _restore_signal_handlers(old_handlers)

    if strip_ansi:
        output = _strip_ansi(output)

    if output:
        lines_count = output.count("\n") + (0 if output.endswith("\n") else 1)
        if strip_ansi or bytes_count == 0:
            bytes_count = len(output.encode("utf-8", errors="ignore"))
    else:
        lines_count = 0
        bytes_count = 0

    return {
        "output": output,
        "exit_code": exit_code,
        "warnings": warnings,
        "truncated": truncated,
        "bytes_count": bytes_count,
        "lines_count": lines_count,
        "shell_cmd": shell_cmd,
        "timed_out": timed_out,
    }


def run_command(
    cmd: str,
    timeout_sec: Optional[float],
    shell: str = "bash",
    cwd: Optional[str] = None,
    extra_env: Optional[dict[str, str]] = None,
    strip_ansi: bool = False,
) -> tuple[str, int]:
    """
    Execute cmd via requested shell returning (output, exit_code).
    Kept for backward compatibility.
    """
    result = run_command_full(
        cmd=cmd,
        timeout_sec=timeout_sec,
        shell=shell,
        cwd=cwd,
        extra_env=extra_env,
        strip_ansi=strip_ansi,
    )
    return result["output"], result["exit_code"]


# ==============================================================================
# SECTION 10: Primary Master Tool Execution Logic
# ==============================================================================


def _error_result(
    message: str,
    exit_code: int,
    raw_command: str = "",
    warnings: Optional[list[str]] = None,
    shell: Optional[str] = None,
    cwd: Optional[str] = None,
) -> dict[str, Any]:
    """Construct a standardized error result."""
    now = datetime.now().astimezone().isoformat()
    output_text = f"{message}\n"
    return {
        "success": False,
        "error": message,
        "command": raw_command,
        "raw_command": raw_command,
        "output": output_text,
        "exit_code": exit_code,
        "duration_ms": 0.0,
        "lines_count": 1 if message else 0,
        "bytes_count": len(output_text.encode("utf-8", errors="ignore")),
        "truncated": False,
        "hint": None,
        "cached": False,
        "shell": shell,
        "shell_command": None,
        "cwd": cwd or os.getcwd(),
        "timeout_sec": None,
        "connect_timeout_sec": None,
        "max_time_sec": None,
        "retries_attempted": 0,
        "warnings": warnings or [],
        "started_at": now,
        "finished_at": now,
        "context": get_execution_context(),
    }


def execute_tool(
    command: str,
    timeout: Optional[str] = None,
    connect_timeout: Optional[str] = None,
    max_time: Optional[str] = None,
    working_dir: Optional[str] = None,
    env: Optional[list[str]] = None,
    shell: str = "bash",
    stdin: Optional[str] = None,
    retry: int = 0,
    retry_delay: Optional[str] = None,
    use_cache: bool = False,
    no_color: bool = False,
    strip_ansi: bool = False,
    verbose: bool = False,
    dry_run: bool = False,
) -> dict[str, Any]:
    """
    Core master tool execution context shared between API run() and CLI.
    """
    global _COLOR_OVERRIDE
    _COLOR_OVERRIDE = False if no_color else None
    _configure_logging(verbose)

    start_time = time.monotonic()
    started_at = datetime.now().astimezone().isoformat()
    warnings: list[str] = []

    if command is None:
        raw_command = ""
    elif isinstance(command, str):
        raw_command = command
    else:
        raw_command = str(command)

    if not raw_command.strip():
        return _error_result(
            "Command must not be empty.",
            EXIT_INVALID_INPUT,
            raw_command=raw_command,
            warnings=warnings,
            shell=shell,
        )

    if verbose:
        logger.debug(f"Executing command: {raw_command!r}")

    sanitize_path()

    # Parse and validate all duration values
    try:
        timeout_sec = parse_duration(timeout, default=None)
    except ValueError as exc:
        return _error_result(
            str(exc),
            EXIT_INVALID_INPUT,
            raw_command=raw_command,
            warnings=warnings,
            shell=shell,
        )

    try:
        ct_sec = parse_duration(connect_timeout, default=10.0)
    except ValueError as exc:
        warnings.append(f"{exc}; using default 10s connect timeout.")
        ct_sec = 10.0

    try:
        mt_sec = parse_duration(max_time, default=None)
    except ValueError as exc:
        warnings.append(f"{exc}; using default max-time.")
        mt_sec = None

    try:
        retry_delay_sec = parse_duration(retry_delay, default=1.0) or 1.0
    except ValueError as exc:
        warnings.append(f"{exc}; using 1s retry delay.")
        retry_delay_sec = 1.0

    # Validate ranges
    if timeout_sec is not None and (not math.isfinite(timeout_sec) or timeout_sec <= 0):
        warnings.append("Timeout value is zero or negative; running without timeout.")
        timeout_sec = None

    if ct_sec is None or not math.isfinite(ct_sec) or ct_sec <= 0:
        warnings.append("Connect timeout must be positive; using 10s.")
        ct_sec = 10.0

    if mt_sec is None or not math.isfinite(mt_sec) or mt_sec <= 0:
        if timeout_sec is not None and math.isfinite(timeout_sec) and timeout_sec > 0:
            mt_sec = timeout_sec
        else:
            mt_sec = 30.0

    # Validate retry count
    try:
        retry_count = max(0, int(retry))
    except (TypeError, ValueError):
        warnings.append(f"Invalid retry value {retry!r}; using 0.")
        retry_count = 0
    if retry_count > 10:
        warnings.append(f"Retry count {retry_count} capped at 10.")
        retry_count = 10

    # Parse cache TTL
    cache_ttl = 3600.0
    try:
        ttl_raw = os.environ.get(
            "LLM_TOOL_CACHE_TTL", os.environ.get("EXECUTE_COMMAND_CACHE_TTL", "1h")
        )
        parsed_ttl = parse_duration(ttl_raw, default=3600.0)
        if parsed_ttl is not None and math.isfinite(parsed_ttl) and parsed_ttl > 0:
            cache_ttl = parsed_ttl
    except ValueError:
        warnings.append("Invalid cache TTL override; using 1h.")

    extra_env = _parse_env_vars(env, warnings)
    cmd = inject_curl_timeouts(raw_command, ct_sec, mt_sec)

    # Resolve working directory
    cwd: Optional[str] = None
    if working_dir:
        base_dir = get_builtin_var("__cwd__") or os.getcwd()
        try:
            wd = (Path(base_dir) / working_dir).expanduser().resolve()
            if wd.is_dir():
                cwd = str(wd)
            else:
                warnings.append(
                    f"Working directory {working_dir!r} not found; using {os.getcwd()}."
                )
        except (OSError, ValueError) as exc:
            warnings.append(f"Could not resolve working directory {working_dir!r}: {exc}")

    effective_cwd = cwd or os.getcwd()

    # Dry-run: return preview without executing
    if dry_run:
        finished_at = datetime.now().astimezone().isoformat()
        return {
            "success": True,
            "command": cmd,
            "raw_command": raw_command,
            "output": f"[DRY RUN] Would execute: {cmd}\n[DRY RUN] Shell: {shell}\n[DRY RUN] CWD: {effective_cwd}\n",
            "exit_code": EXIT_SUCCESS,
            "duration_ms": 0.0,
            "lines_count": 3,
            "bytes_count": 0,
            "truncated": False,
            "hint": None,
            "cached": False,
            "dry_run": True,
            "shell": shell,
            "shell_command": None,
            "cwd": effective_cwd,
            "timeout_sec": timeout_sec,
            "connect_timeout_sec": ct_sec,
            "max_time_sec": mt_sec,
            "retries_attempted": 0,
            "warnings": warnings,
            "started_at": started_at,
            "finished_at": finished_at,
            "context": get_execution_context(),
        }

    # Build cache key
    cache = ToolCache()
    cache_key_data = json.dumps(
        {
            "cmd": cmd,
            "cwd": effective_cwd,
            "shell": shell,
            "timeout": timeout_sec,
            "strip_ansi": strip_ansi,
            "env": extra_env,
            "stdin": stdin,
        },
        sort_keys=True,
        ensure_ascii=False,
        cls=ToolJSONEncoder,
    )

    if use_cache:
        cached_result_raw = cache.get(cache_key_data, ttl_seconds=cache_ttl)
        if cached_result_raw is not None:
            if verbose:
                logger.debug("Cache hit for command execution.")
            cached_result = (
                dict(cached_result_raw)
                if isinstance(cached_result_raw, dict)
                else {"success": True, "output": str(cached_result_raw)}
            )
            cached_result["cached"] = True
            cached_warnings = cached_result.get("warnings", [])
            if isinstance(cached_warnings, str):
                cached_warnings = [cached_warnings]
            elif not isinstance(cached_warnings, list):
                cached_warnings = []
            if "Served from cache." not in cached_warnings:
                cached_warnings.append("Served from cache.")
            cached_result["warnings"] = cached_warnings
            return cached_result

    # Execute with retry loop
    full: dict[str, Any] = {}
    retries_attempted = 0
    for attempt in range(retry_count + 1):
        if attempt > 0:
            if verbose:
                logger.debug(f"Retry attempt {attempt}/{retry_count} after {retry_delay_sec}s")
            warnings.append(f"Retry attempt {attempt}/{retry_count}")
            try:
                time.sleep(retry_delay_sec)
            except KeyboardInterrupt:
                warnings.append("Retry loop interrupted.")
                break
            retries_attempted = attempt

        full = run_command_full(
            cmd=cmd,
            timeout_sec=timeout_sec,
            shell=shell,
            cwd=cwd,
            extra_env=extra_env,
            strip_ansi=strip_ansi,
            stdin_input=stdin,
        )

        exit_code = int(full.get("exit_code", EXIT_ERROR))
        # Don't retry on interrupts or invalid input
        if exit_code == EXIT_SUCCESS or exit_code in (EXIT_INTERRUPTED, EXIT_INVALID_INPUT):
            break

    # Merge warnings from run_command_full
    run_warnings = full.get("warnings", [])
    if isinstance(run_warnings, str):
        run_warnings = [run_warnings]
    elif not isinstance(run_warnings, list):
        run_warnings = [str(run_warnings)]

    all_warnings = warnings + run_warnings
    output = str(full.get("output", ""))
    exit_code = int(full.get("exit_code", EXIT_ERROR))
    duration_ms = round((time.monotonic() - start_time) * 1000, 2)
    finished_at = datetime.now().astimezone().isoformat()

    hint = detect_shadowing_hint(output)
    lines_count = int(full.get("lines_count", 0)) or (
        output.count("\n") + (0 if not output or output.endswith("\n") else 1)
    )
    bytes_count = int(
        full.get("bytes_count", len(output.encode("utf-8", errors="ignore")))
    )

    result: dict[str, Any] = {
        "success": exit_code == EXIT_SUCCESS,
        "command": cmd,
        "raw_command": raw_command,
        "output": output,
        "exit_code": exit_code,
        "duration_ms": duration_ms,
        "duration_human": seconds_to_human(duration_ms / 1000.0),
        "lines_count": lines_count,
        "bytes_count": bytes_count,
        "truncated": bool(full.get("truncated", False)),
        "timed_out": bool(full.get("timed_out", False)),
        "hint": hint,
        "cached": False,
        "shell": shell,
        "shell_command": full.get("shell_cmd"),
        "cwd": effective_cwd,
        "timeout_sec": timeout_sec,
        "connect_timeout_sec": ct_sec,
        "max_time_sec": mt_sec,
        "retries_attempted": retries_attempted,
        "warnings": all_warnings,
        "started_at": started_at,
        "finished_at": finished_at,
        "context": get_execution_context(),
    }

    if verbose:
        logger.debug(
            f"Finished command with exit_code={exit_code} duration_ms={duration_ms} retries={retries_attempted}"
        )

    if use_cache and result["success"]:
        cache.set(cache_key_data, result)

    return result


# ==============================================================================
# SECTION 11: Output Routing (LLM vs Terminal)
# ==============================================================================


def _safe_stream_write(stream: Any, payload: str) -> None:
    """Write to a stream while tolerating broken pipes."""
    try:
        stream.write(payload)
        stream.flush()
    except BrokenPipeError:
        pass
    except OSError as exc:
        try:
            sys.stderr.write(f"Failed writing output stream: {exc}\n")
        except (OSError, ValueError):
            pass


def write_llm_output(data: dict[str, Any]) -> None:
    """Format and write structured execution output to LLM_OUTPUT destination."""
    out_path = os.environ.get("LLM_OUTPUT", "/dev/stdout").strip() or "/dev/stdout"

    try:
        json_payload = (
            json.dumps(data, indent=2, ensure_ascii=False, cls=ToolJSONEncoder) + "\n"
        )
    except (TypeError, ValueError) as exc:
        json_payload = (
            json.dumps(
                {
                    "success": False,
                    "error": "JSON serialization failed",
                    "detail": str(exc),
                },
                ensure_ascii=False,
            )
            + "\n"
        )

    if out_path in {"/dev/stdout", "/dev/fd/1", "-"}:
        _safe_stream_write(sys.stdout, json_payload)
        return

    if out_path in {"/dev/stderr", "/dev/fd/2"}:
        _safe_stream_write(sys.stderr, json_payload)
        return

    try:
        p = Path(out_path).expanduser().resolve()
        p.parent.mkdir(parents=True, exist_ok=True)

        mode = os.environ.get("LLM_OUTPUT_MODE", "a").lower()
        if mode == "w":
            tmp = p.with_name(f".{p.name}.{os.getpid()}.{time.time_ns()}.tmp")
            with open(tmp, "w", encoding="utf-8") as fp:
                fp.write(json_payload)
            tmp.replace(p)
        else:
            with open(p, "a", encoding="utf-8") as fp:
                fp.write(json_payload)
    except OSError as err:
        try:
            sys.stderr.write(f"Failed writing to LLM_OUTPUT '{out_path}': {err}\n")
        except (OSError, ValueError):
            pass
        _safe_stream_write(sys.stdout, json_payload)


# ==============================================================================
# SECTION 12: Function Entry Point for AIChat
# ==============================================================================


def run(
    command: str,
    timeout: Optional[str] = None,
    connect_timeout: Optional[str] = None,
    max_time: Optional[str] = None,
    working_dir: Optional[str] = None,
    env: Optional[list[str]] = None,
    shell: str = "bash",
    stdin: Optional[str] = None,
    retry: int = 0,
    retry_delay: Optional[str] = None,
    use_cache: bool = False,
    no_color: bool = False,
    strip_ansi: bool = False,
    verbose: bool = False,
    dry_run: bool = False,
) -> None:
    """Execute the shell command with specified parameters.

    Args:
        command: Command string to run (required)
        timeout: Wall-clock timeout for command execution
        connect_timeout: Connection timeout injected into curl
        max_time: Max transfer time injected into curl commands
        working_dir: Working directory context
        env: Extra environment variables in KEY=VALUE format (repeatable)
        shell: Shell binary to execute command (bash/sh/zsh)
        stdin: Optional stdin content to pipe to command
        retry: Number of retries on failure (0-10)
        retry_delay: Delay between retries
        use_cache: Enable result caching
        no_color: Disable ANSI color output
        strip_ansi: Strip ANSI sequences from process output
        verbose: Enable detailed debug logging
        dry_run: Preview command without executing
    """
    try:
        res = execute_tool(
            command=command,
            timeout=timeout,
            connect_timeout=connect_timeout,
            max_time=max_time,
            working_dir=working_dir,
            env=env,
            shell=shell,
            stdin=stdin,
            retry=retry,
            retry_delay=retry_delay,
            use_cache=use_cache,
            no_color=no_color,
            strip_ansi=strip_ansi,
            verbose=verbose,
            dry_run=dry_run,
        )
    except Exception as exc:
        res = _error_result(
            f"Unexpected tool failure: {exc}",
            EXIT_ERROR,
            raw_command=str(command or ""),
            shell=shell,
        )

    print_human_readable_ui(res, no_color=no_color)
    write_llm_output(res)


# ==============================================================================
# SECTION 13: CLI Argument Parser & Runner
# ==============================================================================


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="execute_command.py",
        description=f"Pyrmethus Command Executor v{__version__}-ASCENDED",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--command",
        "-c",
        required=True,
        metavar="STRING",
        help="Command to run (required)",
    )
    parser.add_argument(
        "--timeout",
        metavar="DURATION",
        default=None,
        help="Wall-clock timeout for the command (e.g. 60s, 1m, 2h)",
    )
    parser.add_argument(
        "--connect-timeout",
        metavar="DURATION",
        default=None,
        dest="connect_timeout",
        help="Connection timeout injected into curl commands (default: 10s)",
    )
    parser.add_argument(
        "--max-time",
        metavar="DURATION",
        default=None,
        dest="max_time",
        help="Max transfer time injected into curl commands",
    )
    parser.add_argument(
        "--working-dir",
        metavar="PATH",
        default=None,
        dest="working_dir",
        help="Working directory for command execution",
    )
    parser.add_argument(
        "--env",
        metavar="KEY=VALUE",
        action="append",
        default=None,
        help="Extra environment variable (repeatable)",
    )
    parser.add_argument(
        "--shell",
        metavar="SHELL",
        default="bash",
        help="Shell interpreter to use (default: bash)",
    )
    parser.add_argument(
        "--stdin",
        metavar="STRING",
        default=None,
        help="Optional stdin content to pipe to the command",
    )
    parser.add_argument(
        "--retry",
        metavar="INT",
        type=int,
        default=0,
        help="Number of retries on failure (0-10, default: 0)",
    )
    parser.add_argument(
        "--retry-delay",
        metavar="DURATION",
        default=None,
        dest="retry_delay",
        help="Delay between retries (default: 1s)",
    )
    parser.add_argument(
        "--use-cache",
        action="store_true",
        default=False,
        dest="use_cache",
        help="Enable caching for repeated commands",
    )
    parser.add_argument(
        "--no-color",
        action="store_true",
        default=False,
        dest="no_color",
        help="Disable ANSI colour output",
    )
    parser.add_argument(
        "--strip-ansi",
        action="store_true",
        default=False,
        dest="strip_ansi",
        help="Strip ANSI codes from output before returning",
    )
    parser.add_argument(
        "--verbose",
        "-v",
        action="store_true",
        default=False,
        help="Enable detailed debug logging",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        default=False,
        dest="dry_run",
        help="Preview command without executing",
    )
    parser.add_argument(
        "--version",
        action="version",
        version=f"%(prog)s {__version__}",
    )
    return parser


def main() -> int:
    """CLI entry point returning exit code."""
    args = _build_parser().parse_args()

    try:
        res = execute_tool(
            command=args.command,
            timeout=args.timeout,
            connect_timeout=args.connect_timeout,
            max_time=args.max_time,
            working_dir=args.working_dir,
            env=args.env,
            shell=args.shell,
            stdin=args.stdin,
            retry=args.retry,
            retry_delay=args.retry_delay,
            use_cache=args.use_cache,
            no_color=args.no_color,
            strip_ansi=args.strip_ansi,
            verbose=args.verbose,
            dry_run=args.dry_run,
        )
    except Exception as exc:
        res = _error_result(
            f"Unexpected tool failure: {exc}",
            EXIT_ERROR,
            raw_command=str(args.command or ""),
            shell=args.shell,
        )

    print_human_readable_ui(res, no_color=args.no_color)
    write_llm_output(res)
    return int(res.get("exit_code", EXIT_ERROR))


if __name__ == "__main__":
    sys.exit(main())
