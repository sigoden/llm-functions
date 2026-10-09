#!/usr/bin/env python3
# ==============================================================================
# sys_unified.py — Unified System Information & Notification Tool v3.2.0-ASCENDED
# Combines: cpu, disk, mem, uptime, pkg, notify, and deep system diagnostics.
# argc/aichat compatible · Colorized UI · Safe Caching · Agent CWD Resolution
#
# @describe Unified system information tool for inspecting CPU, RAM, Disk, Uptime, Packages, System Diagnostics, and dispatching Telegram notifications.
#
# @meta require-tools aichat
#
# @option --action! <ACTION>            Execution action: all, cpu, disk, mem, uptime, pkg, notify, system (default: all)
# @option --message <TEXT>              Notification message content (for action=notify)
# @option --title <TEXT>                Notification title or category header
# @option --file <PATH>                 File/media attachment path (for action=notify)
# @option --media-type <TYPE>           Media type filter: auto, photo, document, audio, video (default: auto)
# @option --pkg-filter <QUERY>          Package search query filter (for action=pkg)
# @option --limit <NUM>                 Maximum items/packages to process (default: 50)
# @option --timeout <SEC>               Maximum execution timeout in seconds
# @option --token <TOKEN>               Telegram Bot API Token (overrides env)
# @option --chat-id <ID>                Telegram Chat ID (overrides env)
# @option --parse-mode <MODE>           Parse mode: html, markdown, markdownv2, plain (default: html)
# @option --env-var <KEY=VALUE>         Custom environment variable (repeatable)
# @flag   --silent                      Send Telegram notification silently without sound
# @flag   --disable-preview             Disable link previews in notifications
# @flag   --use-cache                   Enable result caching for expensive operations
# @flag   --clear-cache                 Clear cache directory and exit
# @flag   --schema                      Print JSON Tool Schema for LLM registration and exit
# @flag   --no-color                    Disable ANSI color output
# @flag   --verbose                     Enable detailed debug log output
#
# @env LLM_OUTPUT=/dev/stdout           Output path for LLM integration
# @env TELEGRAM_BOT_TOKEN               Default Telegram Bot API Token
# @env TELEGRAM_CHAT_ID                 Default Telegram Chat ID
# ==============================================================================

from __future__ import annotations

import argparse
import dataclasses
import hashlib
import html
import json
import logging
import mimetypes
import os
import platform
import re
import shutil
import signal
import subprocess
import sys
import time
import urllib.error
import urllib.request
import uuid
from datetime import date, datetime, timedelta
from enum import Enum
from pathlib import Path
from typing import Any, Literal, Optional, Tuple, Union

__version__ = "3.2.0"
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

# Standard Notification Defaults
DEFAULT_BOT_TOKEN = "8811508626:AAG4Ii7bN6X_qUqdAzq4GsdpkDmbmsYAw-0"
DEFAULT_CHAT_ID = "1864234012"
MAX_TELEGRAM_MSG_LEN = 4000
MAX_TELEGRAM_CAPTION_LEN = 1000

PHOTO_EXTS = {".jpg", ".jpeg", ".png", ".gif", ".webp", ".bmp"}
AUDIO_EXTS = {".mp3", ".wav", ".m4a", ".ogg", ".flac", ".aac"}
VIDEO_EXTS = {".mp4", ".mkv", ".mov", ".avi", ".webm"}

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


class ActionType(str, Enum):
    ALL = "all"
    CPU = "cpu"
    DISK = "disk"
    MEM = "mem"
    UPTIME = "uptime"
    PKG = "pkg"
    NOTIFY = "notify"
    SYSTEM = "system"


VALID_ACTIONS = {e.value for e in ActionType}
VALID_MEDIA_TYPES = {"auto", "photo", "document", "audio", "video"}
VALID_PARSE_MODES = {"html", "markdown", "markdownv2", "plain"}


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
    action: str,
    media_type: str,
    parse_mode: str,
    limit: Optional[int],
    timeout: Optional[float] = None,
) -> Optional[dict[str, Any]]:
    """Validate input parameters strictly before processing."""
    if action not in VALID_ACTIONS:
        return {
            "success": False,
            "error": f"Invalid action '{action}'. Allowed choices: {sorted(list(VALID_ACTIONS))}",
            "exit_code": EXIT_INVALID_INPUT,
            "duration_ms": 0.0,
        }
    if media_type not in VALID_MEDIA_TYPES:
        return {
            "success": False,
            "error": f"Invalid media-type '{media_type}'. Allowed choices: {sorted(list(VALID_MEDIA_TYPES))}",
            "exit_code": EXIT_INVALID_INPUT,
            "duration_ms": 0.0,
        }
    if parse_mode not in VALID_PARSE_MODES:
        return {
            "success": False,
            "error": f"Invalid parse-mode '{parse_mode}'. Allowed choices: {sorted(list(VALID_PARSE_MODES))}",
            "exit_code": EXIT_INVALID_INPUT,
            "duration_ms": 0.0,
        }
    if limit is not None and limit < 0:
        return {
            "success": False,
            "error": f"Invalid limit '{limit}'. Limit must be >= 0.",
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


def _truncate_str(text: str, max_len: int = 50) -> str:
    """Truncate text for UI alignment."""
    return text if len(text) <= max_len else text[: max_len - 3] + "..."


def print_human_readable_ui(data: dict[str, Any], no_color: bool = False) -> None:
    """Render a human-friendly box UI to stderr."""
    if not _is_tty(no_color=no_color):
        return

    success = data.get("success", False)
    action = data.get("action", "unknown")
    status_color = NEON_GREEN if success else NEON_RED
    status_symbol = "✓" if success else "✗"
    status_text = "SUCCESS" if success else "FAILED"

    box_w = 66
    border = "─" * box_w

    _cprint(f"{NEON_PURPLE}╭{border}╮{RESET}", no_color=no_color)
    _cprint(f"{NEON_PURPLE}│{RESET} {NEON_PINK}⚡ [SYS UNIFIED v{__version__}]{RESET} {NEON_CYAN}Action: {action.upper()}{RESET} {status_color}{BOLD}{status_symbol} {status_text}{RESET}", no_color=no_color)
    _cprint(f"{NEON_PURPLE}├{border}┤{RESET}", no_color=no_color)

    if not success and "error" in data:
        _cprint(f"{NEON_PURPLE}│{RESET} {NEON_RED}Error:{RESET}     {_truncate_str(str(data['error']), 52)}", no_color=no_color)
    else:
        results = data.get("results", {})

        if "cpu" in results:
            cpu = results["cpu"]
            _cprint(f"{NEON_PURPLE}│{RESET} {BOLD}CPU:{RESET} {_truncate_str(str(cpu.get('model', 'N/A')), 35)} ({cpu.get('cores')} cores, {cpu.get('arch')})", no_color=no_color)
            _cprint(f"{NEON_PURPLE}│{RESET}      Load Avg: {NEON_YELLOW}{cpu.get('load_avg')}{RESET}", no_color=no_color)

        if "memory" in results:
            mem = results["memory"]
            _cprint(f"{NEON_PURPLE}│{RESET} {BOLD}RAM:{RESET} Used {NEON_YELLOW}{mem.get('used_mb')} MB{RESET} / {mem.get('total_mb')} MB ({NEON_PINK}{mem.get('used_percent')}%{RESET})", no_color=no_color)

        if "disk" in results:
            disk = results["disk"]
            _cprint(f"{NEON_PURPLE}│{RESET} {BOLD}Disk:{RESET} Used {NEON_YELLOW}{disk.get('used_gb')} GB{RESET} / {disk.get('total_gb')} GB ({NEON_PINK}{disk.get('used_percent')}%{RESET})", no_color=no_color)

        if "uptime" in results:
            up = results["uptime"]
            _cprint(f"{NEON_PURPLE}│{RESET} {BOLD}Uptime:{RESET} {NEON_GREEN}{up.get('uptime_formatted')}{RESET}", no_color=no_color)

        if "packages" in results:
            pkg = results["packages"]
            _cprint(f"{NEON_PURPLE}│{RESET} {BOLD}Packages:{RESET} {pkg.get('total_found')} installed via {pkg.get('manager')}", no_color=no_color)
            for item in pkg.get("packages", [])[:5]:
                pkg_line = f"{item['name']} ({item['version']})"
                _cprint(f"{NEON_PURPLE}│{RESET}   {NEON_CYAN}›{RESET} {_truncate_str(pkg_line, 55)}", no_color=no_color)

        if "notify" in results:
            notif = results["notify"]
            _cprint(f"{NEON_PURPLE}│{RESET} {BOLD}Telegram Notification:{RESET} Sent {notif.get('chunks_sent')} message chunk(s)", no_color=no_color)

        if "system" in results:
            _cprint(f"{NEON_PURPLE}│{RESET} {BOLD}System Diagnostics:{RESET} Complete (see JSON payload)", no_color=no_color)

    _cprint(f"{NEON_PURPLE}├{border}┤{RESET}", no_color=no_color)
    _cprint(f"{NEON_PURPLE}│{RESET} {NEON_CYAN}Duration:{RESET} {DIM}{data.get('duration_ms', 0)}ms{RESET}  |  {NEON_CYAN}Cached:{RESET} {data.get('cached', False)}", no_color=no_color)
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
        "tool_name": os.environ.get("LLM_TOOL_NAME", "sys_unified.py"),
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


def resolve_credentials(token: Optional[str], chat_id: Optional[str]) -> Tuple[str, str]:
    """Resolve Telegram token and chat ID across options, env vars, agent variables, and defaults."""
    res_token = (
        token
        or get_agent_var("TELEGRAM_BOT_TOKEN")
        or os.environ.get("TELEGRAM_BOT_TOKEN")
        or DEFAULT_BOT_TOKEN
    )
    res_chat_id = (
        chat_id
        or get_agent_var("TELEGRAM_CHAT_ID")
        or os.environ.get("TELEGRAM_CHAT_ID")
        or DEFAULT_CHAT_ID
    )
    return res_token.strip(), str(res_chat_id).strip()


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

def build_cache_key(action: str, pkg_filter: Optional[str], limit: int, message: Optional[str], file: Optional[str]) -> str:
    """Construct pipe-delimited cache key."""
    return "|".join([
        __version__,
        action,
        pkg_filter or "",
        str(limit),
        message or "",
        file or "",
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

    def get(self, key_str: str, ttl_seconds: int = 60) -> Optional[dict[str, Any]]:
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
        "name": "sys_unified",
        "description": "Unified system information and notification tool for inspection and dispatching notifications.",
        "parameters": {
            "type": "object",
            "properties": {
                "action": {
                    "type": "string",
                    "enum": [e.value for e in ActionType],
                    "description": "Execution action (default: all)"
                },
                "message": {
                    "type": "string",
                    "description": "Notification message content (for action=notify)"
                },
                "title": {
                    "type": "string",
                    "description": "Notification title or category header"
                },
                "file": {
                    "type": "string",
                    "description": "File/media attachment path (for action=notify)"
                },
                "media_type": {
                    "type": "string",
                    "enum": list(VALID_MEDIA_TYPES),
                    "description": "Media type filter (default: auto)"
                },
                "pkg_filter": {
                    "type": "string",
                    "description": "Package search query filter (for action=pkg)"
                },
                "limit": {
                    "type": "integer",
                    "description": "Maximum items/packages to process (default: 50)"
                },
                "timeout": {
                    "type": "number",
                    "description": "Maximum execution timeout in seconds"
                },
                "token": {
                    "type": "string",
                    "description": "Telegram Bot API Token"
                },
                "chat_id": {
                    "type": "string",
                    "description": "Telegram Chat ID"
                },
                "parse_mode": {
                    "type": "string",
                    "enum": list(VALID_PARSE_MODES),
                    "description": "Parse mode (default: html)"
                },
                "silent": {
                    "type": "boolean",
                    "description": "Send notification silently"
                },
                "disable_preview": {
                    "type": "boolean",
                    "description": "Disable link previews"
                },
                "env_vars": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Custom environment variable array (KEY=VALUE)"
                },
                "use_cache": {
                    "type": "boolean",
                    "description": "Enable result caching"
                }
            },
            "required": []
        }
    }


# ==============================================================================
# SECTION 5: System Inspectors & Subsystem Engine
# ==============================================================================

def _safe_getloadavg() -> list[float]:
    """Retrieve load averages safely across all OS platforms."""
    try:
        if hasattr(os, "getloadavg"):
            return [round(x, 2) for x in os.getloadavg()]
    except (OSError, AttributeError):
        pass
    return [0.0, 0.0, 0.0]


def inspect_cpu() -> dict[str, Any]:
    """Gather CPU information."""
    info = {
        "cores": os.cpu_count() or 1,
        "load_avg": _safe_getloadavg(),
        "model": "Generic CPU",
        "arch": os.uname().machine if hasattr(os, "uname") else "Unknown",
    }
    proc_cpu = Path("/proc/cpuinfo")
    if proc_cpu.exists():
        try:
            for line in proc_cpu.read_text(errors="ignore").splitlines():
                if ":" in line:
                    k, v = [x.strip() for x in line.split(":", 1)]
                    k_lower = k.lower()
                    if k_lower in ("model name", "processor", "hardware", "vendor_id", "cpu model"):
                        if v and not v.isdigit():
                            info["model"] = v
                            break
        except Exception:
            pass
    return info


def inspect_memory() -> dict[str, Any]:
    """Gather memory information with legacy kernel fallbacks."""
    mem = {"total_mb": 0.0, "free_mb": 0.0, "available_mb": 0.0, "used_mb": 0.0, "used_percent": 0.0}
    proc_mem = Path("/proc/meminfo")
    if proc_mem.exists():
        try:
            data = {}
            for line in proc_mem.read_text(errors="ignore").splitlines():
                if ":" in line:
                    k, v = line.split(":", 1)
                    val = v.strip().split()[0]
                    if val.isdigit():
                        data[k.strip()] = int(val)
            total_kb = data.get("MemTotal", 0)
            free_kb = data.get("MemFree", 0)
            buffers_kb = data.get("Buffers", 0)
            cached_kb = data.get("Cached", 0)

            avail_kb = data.get("MemAvailable", free_kb + buffers_kb + cached_kb)
            used_kb = max(0, total_kb - avail_kb)

            mem["total_mb"] = round(total_kb / 1024, 2)
            mem["free_mb"] = round(free_kb / 1024, 2)
            mem["available_mb"] = round(avail_kb / 1024, 2)
            mem["used_mb"] = round(used_kb / 1024, 2)
            mem["used_percent"] = round((used_kb / total_kb) * 100, 1) if total_kb > 0 else 0.0
        except Exception:
            pass
    return mem


def inspect_disk(mount_point: str = "/") -> dict[str, Any]:
    """Gather filesystem storage information with path permission fallbacks."""
    target_path = mount_point
    try:
        usage = shutil.disk_usage(target_path)
    except Exception:
        target_path = str(Path.home())
        try:
            usage = shutil.disk_usage(target_path)
        except Exception as exc:
            return {"mount": mount_point, "error": str(exc)}

    total_gb = round(usage.total / (1024**3), 2)
    used_gb = round(usage.used / (1024**3), 2)
    free_gb = round(usage.free / (1024**3), 2)
    percent = round((usage.used / usage.total) * 100, 1) if usage.total > 0 else 0.0

    return {
        "mount": target_path,
        "total_gb": total_gb,
        "used_gb": used_gb,
        "free_gb": free_gb,
        "used_percent": percent,
    }


def inspect_uptime() -> dict[str, Any]:
    """Gather system uptime duration and idle metrics."""
    uptime_secs = 0.0
    idle_secs = 0.0
    proc_uptime = Path("/proc/uptime")
    if proc_uptime.exists():
        try:
            parts = proc_uptime.read_text().split()
            uptime_secs = float(parts[0])
            if len(parts) > 1:
                idle_secs = float(parts[1])
        except Exception:
            pass

    days = int(uptime_secs // 86400)
    hours = int((uptime_secs % 86400) // 3600)
    minutes = int((uptime_secs % 3600) // 60)
    seconds = int(uptime_secs % 60)

    formatted = f"{days}d {hours}h {minutes}m {seconds}s" if days > 0 else f"{hours}h {minutes}m {seconds}s"
    loads = _safe_getloadavg()

    return {
        "uptime_seconds": round(uptime_secs, 2),
        "idle_seconds": round(idle_secs, 2),
        "uptime_formatted": formatted,
        "load_1m": loads[0],
        "load_5m": loads[1],
        "load_15m": loads[2],
    }


def inspect_packages(query: Optional[str] = None, limit: int = 50) -> dict[str, Any]:
    """Gather installed software package details with subprocess timeout protection."""
    pkgs = []
    pkg_manager = "unknown"

    try:
        if shutil.which("dpkg-query"):
            pkg_manager = "dpkg"
            res = subprocess.run(
                ["dpkg-query", "-W", "-f=${Package}\\t${Version}\\n"],
                capture_output=True,
                text=True,
                timeout=8,
                stderr=subprocess.DEVNULL,
            )
            if res.returncode == 0:
                for line in res.stdout.splitlines():
                    if "\t" in line:
                        n, v = line.split("\t", 1)
                        pkgs.append({"name": n, "version": v})
        elif shutil.which("pkg"):
            pkg_manager = "pkg"
            res = subprocess.run(
                ["pkg", "list-installed"],
                capture_output=True,
                text=True,
                timeout=8,
                stderr=subprocess.DEVNULL,
            )
            if res.returncode == 0:
                for line in res.stdout.splitlines():
                    if "/" in line:
                        parts = line.split("/")
                        pkgs.append({"name": parts[0], "version": parts[1] if len(parts) > 1 else "installed"})
        elif shutil.which("rpm"):
            pkg_manager = "rpm"
            res = subprocess.run(
                ["rpm", "-qa", "--queryformat", "%{NAME}\\t%{VERSION}\\n"],
                capture_output=True,
                text=True,
                timeout=8,
                stderr=subprocess.DEVNULL,
            )
            if res.returncode == 0:
                for line in res.stdout.splitlines():
                    if "\t" in line:
                        n, v = line.split("\t", 1)
                        pkgs.append({"name": n, "version": v})
        elif shutil.which("pacman"):
            pkg_manager = "pacman"
            res = subprocess.run(
                ["pacman", "-Q"],
                capture_output=True,
                text=True,
                timeout=8,
                stderr=subprocess.DEVNULL,
            )
            if res.returncode == 0:
                for line in res.stdout.splitlines():
                    parts = line.split()
                    if len(parts) >= 2:
                        pkgs.append({"name": parts[0], "version": parts[1]})
    except subprocess.TimeoutExpired:
        return {"manager": pkg_manager, "error": "Package query timed out", "total_found": 0, "packages": []}
    except Exception as exc:
        return {"manager": pkg_manager, "error": str(exc), "total_found": 0, "packages": []}

    if query:
        q_lower = query.lower()
        pkgs = [p for p in pkgs if q_lower in p["name"].lower()]

    return {
        "manager": pkg_manager,
        "query": query,
        "total_found": len(pkgs),
        "packages": pkgs[:limit],
    }


def _safe_termux(cmd: list[str], timeout: float = 4.0) -> str:
    """Invoke a Termux-API command if available."""
    if not shutil.which(cmd[0]):
        return "termux-api not found"
    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
        if result.returncode != 0 or not result.stdout.strip():
            return "unavailable"
        return result.stdout.strip()
    except (subprocess.TimeoutExpired, OSError, Exception):
        return "unavailable"


def _read_meminfo() -> dict[str, int]:
    info: dict[str, int] = {}
    try:
        with open("/proc/meminfo", encoding="utf-8") as f:
            for line in f:
                if ":" not in line:
                    continue
                key, value = line.split(":", 1)
                key = key.strip()
                value = value.strip().split()[0]
                if value.isdigit():
                    info[key] = int(value)
    except (OSError, ValueError):
        pass
    return info


def _read_vmstat() -> dict[str, int]:
    info: dict[str, int] = {}
    try:
        with open("/proc/vmstat", encoding="utf-8") as f:
            for line in f:
                parts = line.split()
                if len(parts) >= 2 and parts[1].isdigit():
                    info[parts[0]] = int(parts[1])
    except (OSError, ValueError):
        pass
    return info


def _fmt_bytes(kb: int) -> str:
    if kb <= 0:
        return "Unknown"
    mb = kb / 1024
    if mb < 1024:
        return f"{mb:.1f} MB"
    gb = mb / 1024
    return f"{gb:.2f} GB"


def _live_samples(interval: float = 0.7) -> tuple[dict, dict, dict, dict, float]:
    m1 = _read_meminfo()
    v1 = _read_vmstat()
    time.sleep(interval)
    m2 = _read_meminfo()
    v2 = _read_vmstat()
    return m1, m2, v1, v2, interval


def inspect_system() -> dict[str, Any]:
    """Gather expanded system diagnostic information."""
    os_name = platform.system() or "Unknown"
    release = platform.release() or "Unknown"
    version = platform.version() or "Unknown"
    machine = platform.machine() or "Unknown"
    processor = platform.processor() or machine
    node = platform.node() or "Unknown"
    py_ver = platform.python_version()
    cpu_count = os.cpu_count() or "Unknown"
    cwd = os.getcwd()
    user = os.getenv("USER") or os.getenv("LOGNAME") or "Unknown"

    mem1, mem2, vm1, vm2, interval = _live_samples()

    total = mem2.get("MemTotal", 0)
    available = mem2.get("MemAvailable", 0)
    free = mem2.get("MemFree", 0)
    buffers = mem2.get("Buffers", 0)
    cached = mem2.get("Cached", 0)
    used = total - available if total and available else 0

    swap_total = mem2.get("SwapTotal", 0)
    swap_free = mem2.get("SwapFree", 0)
    swap_cached = mem2.get("SwapCached", 0)
    swap_used = swap_total - swap_free if swap_total else 0
    anon = mem2.get("AnonPages", 0)
    mapped = mem2.get("Mapped", 0)
    shmem = mem2.get("Shmem", 0)
    commit_limit = mem2.get("CommitLimit", 0)
    committed = mem2.get("Committed_AS", 0)
    vmalloc_total = mem2.get("VmallocTotal", 0)
    vmalloc_used = mem2.get("VmallocUsed", 0)

    PAGE_KB = 4
    pswpin1 = vm1.get("pswpin", 0)
    pswpin2 = vm2.get("pswpin", 0)
    pswpout1 = vm1.get("pswpout", 0)
    pswpout2 = vm2.get("pswpout", 0)
    pgfault1 = vm1.get("pgfault", 0)
    pgfault2 = vm2.get("pgfault", 0)
    pgmajfault1 = vm1.get("pgmajfault", 0)
    pgmajfault2 = vm2.get("pgmajfault", 0)

    swap_in_rate = ((pswpin2 - pswpin1) * PAGE_KB) / interval if interval else 0
    swap_out_rate = ((pswpout2 - pswpout1) * PAGE_KB) / interval if interval else 0
    fault_rate = (pgfault2 - pgfault1) / interval if interval else 0
    majfault_rate = (pgmajfault2 - pgmajfault1) / interval if interval else 0

    battery_raw = _safe_termux(["termux-battery-status"])
    device_raw = _safe_termux(["termux-telephony-deviceinfo"])
    wifi_raw = _safe_termux(["termux-wifi-connectioninfo"])
    termux_info = _safe_termux(["termux-info"], timeout=6.0)

    battery_line = battery_raw
    if battery_raw.startswith("{"):
        try:
            b = json.loads(battery_raw)
            pct = b.get("percentage", "?")
            status = b.get("status", "?")
            health = b.get("health", "?")
            temp = b.get("temperature", "?")
            battery_line = f"{pct}% | {status} | health:{health} | {temp}°C"
        except json.JSONDecodeError:
            pass

    device_line = device_raw
    if device_raw.startswith("{"):
        try:
            d = json.loads(device_raw)
            device_line = f"{d.get('manufacturer', '?')} {d.get('model', '?')} (API {d.get('sdk_version', '?')})"
        except json.JSONDecodeError:
            pass

    wifi_line = wifi_raw
    if wifi_raw.startswith("{"):
        try:
            w = json.loads(wifi_raw)
            ssid = w.get("ssid", "n/a")
            ip = w.get("ip", "n/a")
            wifi_line = f"SSID:{ssid}  IP:{ip}"
        except json.JSONDecodeError:
            pass

    return {
        "os_name": os_name,
        "release": release,
        "version": version,
        "machine": machine,
        "processor": processor,
        "node": node,
        "py_ver": py_ver,
        "cpu_count": cpu_count,
        "cwd": cwd,
        "user": user,
        "ram_total": _fmt_bytes(total),
        "ram_used": _fmt_bytes(used),
        "ram_available": _fmt_bytes(available),
        "ram_free": _fmt_bytes(free),
        "buffers": _fmt_bytes(buffers),
        "cached": _fmt_bytes(cached),
        "swap_total": _fmt_bytes(swap_total),
        "swap_used": _fmt_bytes(swap_used),
        "swap_free": _fmt_bytes(swap_free),
        "swap_cached": _fmt_bytes(swap_cached),
        "anon_pages": _fmt_bytes(anon),
        "mapped": _fmt_bytes(mapped),
        "shmem": _fmt_bytes(shmem),
        "commit_limit": _fmt_bytes(commit_limit),
        "committed_as": _fmt_bytes(committed),
        "vmalloc_used": _fmt_bytes(vmalloc_used),
        "vmalloc_total": _fmt_bytes(vmalloc_total),
        "swap_in_rate": swap_in_rate,
        "swap_out_rate": swap_out_rate,
        "fault_rate": fault_rate,
        "majfault_rate": majfault_rate,
        "sample_window": interval,
        "battery": battery_line,
        "device": device_line,
        "wifi": wifi_line,
        "termux_info": termux_info,
    }


def _chunk_text(text: str, max_len: int = MAX_TELEGRAM_MSG_LEN) -> list[str]:
    if len(text) <= max_len:
        return [text]
    chunks = []
    while text:
        if len(text) <= max_len:
            chunks.append(text)
            break
        split_idx = text.rfind("\n", 0, max_len)
        if split_idx == -1 or split_idx < max_len // 2:
            split_idx = text.rfind(" ", 0, max_len)
        if split_idx == -1:
            split_idx = max_len
        chunks.append(text[:split_idx])
        text = text[split_idx:].lstrip("\n")
    return chunks


def _build_multipart_payload(fields: dict[str, Any], file_field_name: str, file_path: Path) -> Tuple[bytes, str]:
    boundary = f"----WebKitFormBoundary{uuid.uuid4().hex}"
    body = bytearray()
    for name, value in fields.items():
        if value is None or value == "":
            continue
        body.extend(f"--{boundary}\r\n".encode("utf-8"))
        body.extend(f'Content-Disposition: form-data; name="{name}"\r\n\r\n'.encode("utf-8"))
        body.extend(f"{value}\r\n".encode("utf-8"))

    mime_type, _ = mimetypes.guess_type(str(file_path))
    body.extend(f"--{boundary}\r\n".encode("utf-8"))
    body.extend(f'Content-Disposition: form-data; name="{file_field_name}"; filename="{file_path.name}"\r\n'.encode("utf-8"))
    body.extend(f"Content-Type: {mime_type or 'application/octet-stream'}\r\n\r\n".encode("utf-8"))

    with open(file_path, "rb") as fp:
        body.extend(fp.read())
    body.extend(b"\r\n")

    body.extend(f"--{boundary}--\r\n".encode("utf-8"))
    return bytes(body), f"multipart/form-data; boundary={boundary}"


def _send_telegram_notification(
    token: str,
    chat_id: str,
    message: str,
    title: Optional[str] = None,
    file_path: Optional[Path] = None,
    media_type: str = "auto",
    parse_mode: str = "html",
    silent: bool = False,
    disable_preview: bool = False,
) -> dict[str, Any]:
    pm_norm = parse_mode.lower()
    tg_pm = "HTML" if pm_norm in ("html", "default") else ("Markdown" if "markdown" in pm_norm else None)

    if tg_pm == "HTML":
        header = f"<b>{html.escape(title.strip())}</b>\n\n" if title and title.strip() else ""
        body = html.escape(message.strip()) if message and message.strip() else ""
    else:
        header = f"[{title.strip()}]\n\n" if title and title.strip() else ""
        body = message.strip() if message and message.strip() else ""

    full_text = f"{header}{body}".strip()
    chunks_sent = 0

    if file_path:
        ext = file_path.suffix.lower()
        if media_type == "photo" or (media_type == "auto" and ext in PHOTO_EXTS):
            method, field = "sendPhoto", "photo"
        elif media_type == "audio" or (media_type == "auto" and ext in AUDIO_EXTS):
            method, field = "sendAudio", "audio"
        elif media_type == "video" or (media_type == "auto" and ext in VIDEO_EXTS):
            method, field = "sendVideo", "video"
        else:
            method, field = "sendDocument", "document"

        if len(full_text) <= MAX_TELEGRAM_CAPTION_LEN:
            cap = full_text
            rem = ""
        else:
            cap = (header or file_path.name)[:MAX_TELEGRAM_CAPTION_LEN]
            rem = full_text

        fields = {"chat_id": chat_id, "caption": cap, "disable_notification": silent}
        if tg_pm:
            fields["parse_mode"] = tg_pm

        data_bytes, ctype = _build_multipart_payload(fields, field, file_path)
        req = urllib.request.Request(f"https://api.telegram.org/bot{token}/{method}", data=data_bytes, headers={"Content-Type": ctype})
        with urllib.request.urlopen(req, timeout=30) as resp:
            pass
        chunks_sent += 1
        full_text = rem

    if full_text:
        for chunk in _chunk_text(full_text):
            fields = {"chat_id": chat_id, "text": chunk, "disable_notification": silent, "disable_web_page_preview": disable_preview}
            if tg_pm:
                fields["parse_mode"] = tg_pm
            data_bytes = json.dumps(fields).encode("utf-8")
            req = urllib.request.Request(f"https://api.telegram.org/bot{token}/sendMessage", data=data_bytes, headers={"Content-Type": "application/json"})
            with urllib.request.urlopen(req, timeout=15) as resp:
                pass
            chunks_sent += 1

    return {"chunks_sent": chunks_sent, "chat_id": chat_id}


# ==============================================================================
# SECTION 6: Core Execution Engine
# ==============================================================================

def execute_tool(
    action: str = "all",
    message: Optional[str] = None,
    title: Optional[str] = None,
    file: Optional[str] = None,
    media_type: str = "auto",
    pkg_filter: Optional[str] = None,
    limit: Optional[int] = 50,
    timeout: Optional[float] = None,
    token: Optional[str] = None,
    chat_id: Optional[str] = None,
    parse_mode: str = "html",
    silent: bool = False,
    disable_preview: bool = False,
    env_vars: Optional[list[str]] = None,
    use_cache: bool = False,
    no_color: bool = False,
    verbose: bool = False,
) -> dict[str, Any]:
    """Execute main tool logic and return structured result dictionary."""
    start_time = time.monotonic()
    action_norm = action.lower().strip()
    limit_val = limit if (limit is not None and limit > 0) else 50

    # Step 1: Input Validation
    bad_inputs = validate_inputs(action_norm, media_type, parse_mode, limit_val, timeout)
    if bad_inputs:
        return bad_inputs

    if verbose:
        try:
            logging.basicConfig(level=logging.DEBUG, format="[DEBUG] %(message)s", force=True)
        except TypeError:
            logging.basicConfig(level=logging.DEBUG, format="[DEBUG] %(message)s")
        logging.debug(f"Starting execution for action: {action_norm}")

    parsed_env = _parse_env_vars(env_vars)
    _apply_env_vars(parsed_env)

    file_target = resolve_agent_path(file) if file else None
    if file_target and not file_target.exists():
        return {
            "success": False,
            "action": action_norm,
            "error": f"Target attachment file not found: {file} (Resolved: {file_target})",
            "exit_code": EXIT_FILE_NOT_FOUND,
            "duration_ms": 0.0,
        }

    cache = ToolCache()
    cache_key = build_cache_key(action_norm, pkg_filter, limit_val, message, file)

    if use_cache:
        cached_result = cache.get(cache_key)
        if cached_result is not None:
            cached_result["cached"] = True
            return cached_result

    results: dict[str, Any] = {}

    try:
        with GracefulShutdown() as shutdown:
            if action_norm in ("all", "cpu"):
                if timeout and (time.monotonic() - start_time) > timeout:
                    raise ToolError(f"Execution timed out after {timeout}s", EXIT_TIMEOUT)
                if shutdown.should_stop():
                    raise ToolError("Execution interrupted by user signal.", EXIT_INTERRUPTED)
                results["cpu"] = inspect_cpu()

            if action_norm in ("all", "mem"):
                if timeout and (time.monotonic() - start_time) > timeout:
                    raise ToolError(f"Execution timed out after {timeout}s", EXIT_TIMEOUT)
                if shutdown.should_stop():
                    raise ToolError("Execution interrupted by user signal.", EXIT_INTERRUPTED)
                results["memory"] = inspect_memory()

            if action_norm in ("all", "disk"):
                if timeout and (time.monotonic() - start_time) > timeout:
                    raise ToolError(f"Execution timed out after {timeout}s", EXIT_TIMEOUT)
                if shutdown.should_stop():
                    raise ToolError("Execution interrupted by user signal.", EXIT_INTERRUPTED)
                results["disk"] = inspect_disk("/")

            if action_norm in ("all", "uptime"):
                if timeout and (time.monotonic() - start_time) > timeout:
                    raise ToolError(f"Execution timed out after {timeout}s", EXIT_TIMEOUT)
                if shutdown.should_stop():
                    raise ToolError("Execution interrupted by user signal.", EXIT_INTERRUPTED)
                results["uptime"] = inspect_uptime()

            if action_norm in ("all", "pkg"):
                if timeout and (time.monotonic() - start_time) > timeout:
                    raise ToolError(f"Execution timed out after {timeout}s", EXIT_TIMEOUT)
                if shutdown.should_stop():
                    raise ToolError("Execution interrupted by user signal.", EXIT_INTERRUPTED)
                results["packages"] = inspect_packages(query=pkg_filter, limit=limit_val)

            if action_norm in ("all", "system"):
                if timeout and (time.monotonic() - start_time) > timeout:
                    raise ToolError(f"Execution timed out after {timeout}s", EXIT_TIMEOUT)
                if shutdown.should_stop():
                    raise ToolError("Execution interrupted by user signal.", EXIT_INTERRUPTED)
                results["system"] = inspect_system()

            if action_norm == "notify":
                if not message and not file_target:
                    raise ToolError("Notification action requires --message or --file.", EXIT_INVALID_INPUT)
                if timeout and (time.monotonic() - start_time) > timeout:
                    raise ToolError(f"Execution timed out after {timeout}s", EXIT_TIMEOUT)
                if shutdown.should_stop():
                    raise ToolError("Execution interrupted by user signal.", EXIT_INTERRUPTED)
                
                bot_token, target_chat_id = resolve_credentials(token, chat_id)
                results["notify"] = _send_telegram_notification(
                    token=bot_token,
                    chat_id=target_chat_id,
                    message=message or "",
                    title=title,
                    file_path=file_target,
                    media_type=media_type,
                    parse_mode=parse_mode,
                    silent=silent,
                    disable_preview=disable_preview,
                )

        duration_ms = round((time.monotonic() - start_time) * 1000, 2)

        response: dict[str, Any] = {
            "success": True,
            "action": action_norm,
            "results": results,
            "parsed_env_vars": parsed_env,
            "context": get_execution_context(),
            "cached": False,
            "duration_ms": duration_ms,
            "exit_code": EXIT_SUCCESS,
        }

        if use_cache:
            cache.set(cache_key, response)

        return response

    except ToolError as exc:
        duration_ms = round((time.monotonic() - start_time) * 1000, 2)
        return {
            "success": False,
            "action": action_norm,
            "error": exc.message,
            "exit_code": exc.exit_code,
            "duration_ms": duration_ms,
        }
    except Exception as exc:
        duration_ms = round((time.monotonic() - start_time) * 1000, 2)
        return {
            "success": False,
            "action": action_norm,
            "error": f"Tool execution error: {exc}",
            "exit_code": EXIT_ERROR,
            "duration_ms": duration_ms,
        }


# ==============================================================================
# SECTION 7: Output Routing (JSON Lines Formatting)
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
# SECTION 8: Function Entry Point for AIChat
# ==============================================================================

def run(
    action: Literal["all", "cpu", "disk", "mem", "uptime", "pkg", "notify", "system"] = "all",
    message: Optional[str] = None,
    title: Optional[str] = None,
    file: Optional[str] = None,
    media_type: Literal["auto", "photo", "document", "audio", "video"] = "auto",
    pkg_filter: Optional[str] = None,
    limit: Optional[int] = 50,
    timeout: Optional[float] = None,
    token: Optional[str] = None,
    chat_id: Optional[str] = None,
    parse_mode: Literal["html", "markdown", "markdownv2", "plain"] = "html",
    silent: bool = False,
    disable_preview: bool = False,
    env_vars: Optional[list[str]] = None,
    use_cache: bool = False,
    no_color: bool = False,
    verbose: bool = False,
) -> None:
    """AIChat Tool entrypoint function."""
    result = execute_tool(
        action=action,
        message=message,
        title=title,
        file=file,
        media_type=media_type,
        pkg_filter=pkg_filter,
        limit=limit,
        timeout=timeout,
        token=token,
        chat_id=chat_id,
        parse_mode=parse_mode,
        silent=silent,
        disable_preview=disable_preview,
        env_vars=env_vars,
        use_cache=use_cache,
        no_color=no_color,
        verbose=verbose,
    )

    print_human_readable_ui(result, no_color=no_color)
    write_llm_output(result)


# ==============================================================================
# SECTION 9: CLI Parser & Main Entrypoint
# ==============================================================================

def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="sys_unified.py",
        description=f"Unified System Information & Notification Tool v{__version__}",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--action", "-a",
        choices=[e.value for e in ActionType],
        **env_default("LLM_TOOL_ACTION", "all"),
        help="Execution action: all, cpu, disk, mem, uptime, pkg, notify, system (default: all)",
    )
    parser.add_argument(
        "--message", "-m",
        help="Notification message content (for action=notify)",
    )
    parser.add_argument(
        "--title", "-t",
        help="Notification title or category header",
    )
    parser.add_argument(
        "--file", "-f",
        help="File/media attachment path (for action=notify)",
    )
    parser.add_argument(
        "--media-type",
        choices=list(VALID_MEDIA_TYPES),
        default="auto",
        help="Media type filter: auto, photo, document, audio, video (default: auto)",
    )
    parser.add_argument(
        "--pkg-filter", "-q",
        help="Package search query filter (for action=pkg)",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=50,
        help="Maximum items/packages to process (default: 50)",
    )
    parser.add_argument(
        "--timeout",
        type=float,
        help="Maximum execution timeout in seconds",
    )
    parser.add_argument(
        "--token",
        help="Telegram Bot API Token (overrides env)",
    )
    parser.add_argument(
        "--chat-id",
        help="Telegram Chat ID (overrides env)",
    )
    parser.add_argument(
        "--parse-mode",
        choices=list(VALID_PARSE_MODES),
        default="html",
        help="Parse mode: html, markdown, markdownv2, plain (default: html)",
    )
    parser.add_argument(
        "--silent",
        action="store_true",
        default=False,
        help="Send notification silently without sound",
    )
    parser.add_argument(
        "--disable-preview",
        action="store_true",
        default=False,
        help="Disable link previews in notifications",
    )
    parser.add_argument(
        "--env-var",
        action="append",
        dest="env_vars",
        metavar="KEY=VALUE",
        help="Custom environment variable (repeatable)",
    )
    parser.add_argument(
        "--use-cache",
        action="store_true",
        default=False,
        dest="use_cache",
        help="Enable result caching for expensive operations",
    )
    parser.add_argument(
        "--clear-cache",
        action="store_true",
        default=False,
        dest="clear_cache",
        help="Clear tool cache directory and exit",
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

    res = execute_tool(
        action=args.action,
        message=args.message,
        title=args.title,
        file=args.file,
        media_type=args.media_type,
        pkg_filter=args.pkg_filter,
        limit=args.limit,
        timeout=args.timeout,
        token=args.token,
        chat_id=args.chat_id,
        parse_mode=args.parse_mode,
        silent=args.silent,
        disable_preview=args.disable_preview,
        env_vars=args.env_vars,
        use_cache=args.use_cache,
        no_color=args.no_color,
        verbose=args.verbose,
    )

    print_human_readable_ui(res, no_color=args.no_color)
    write_llm_output(res)
    return res.get("exit_code", EXIT_SUCCESS)


if __name__ == "__main__":
    sys.exit(main())
