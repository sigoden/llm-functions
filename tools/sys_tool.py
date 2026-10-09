#!/usr/bin/env python3
# ==============================================================================
# sys_tool.py — Pyrmethus AIChat Tool Master Template v2.4.0-ASCENDED
# argc/aichat compatible · Human-Readable Colorized Outputs · Native Caching
#
# @describe Unified system information and notification tool (CPU, Disk, Memory, Packages, Uptime, Telegram Notifications).
#
# @meta require-tools aichat
#
# @option --action -a <ACTION>            Execution action: all, cpu, disk, mem, uptime, pkg, notify (default: all)
# @option --message -m <MESSAGE>          Notification message content (for action=notify)
# @option --title -t <TITLE>              Title or category header
# @option --file -f <PATH>                File/media attachment path (for action=notify)
# @option --media-type <TYPE>             Media type: auto, photo, document, audio, video (default: auto)
# @option --pkg-filter -q <QUERY>         Package search query filter (for action=pkg)
# @option --limit <NUM>                  Maximum items/packages to process (default: 50)
# @option --token <TOKEN>                 Telegram Bot API Token (overrides env)
# @option --chat-id <CHAT_ID>             Telegram Chat ID (overrides env)
# @option --parse-mode <MODE>             Parse mode: html, markdown, markdownv2, plain (default: html)
# @flag   --silent                        Send notification silently without sound
# @flag   --disable-preview               Disable link previews in notifications
# @flag   --use-cache                    Enable result caching for expensive operations
# @flag   --no-color                     Disable ANSI color output
# @flag   --verbose                      Enable detailed debug log output
#
# @env LLM_OUTPUT=/dev/stdout            Output path for LLM integration
# ==============================================================================

from __future__ import annotations

import argparse
import hashlib
import html
import json
import logging
import mimetypes
import os
import pickle
import re
import shutil
import signal
import subprocess
import sys
import time
import urllib.error
import urllib.request
import uuid
from datetime import datetime, timedelta
from enum import Enum
from pathlib import Path
from typing import Any, Dict, List, Literal, Optional, Tuple, Union

__version__ = "2.4.0"
__all__ = [
    "run",
    "execute_tool",
    "ToolCache",
    "ToolError",
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
# SECTION 1: Exit Codes & Exception Models
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

NEON_CYAN    = "\033[38;5;51m"
NEON_GREEN   = "\033[38;5;46m"
NEON_RED     = "\033[38;5;196m"
NEON_YELLOW  = "\033[38;5;226m"
NEON_PURPLE  = "\033[38;5;129m"
NEON_PINK    = "\033[38;5;198m"
RESET        = "\033[0m"
BOLD         = "\033[1m"
DIM          = "\033[2m"

_ANSI_RE = re.compile(
    r"\x1b(?:[@-Z\\-_]|\[[0-?]*[ -/]*[@-~])|\033\[[0-9;?]*[a-zA-Z]"
)


def _strip_ansi(text: str) -> str:
    """Remove ANSI escape sequences from text."""
    return _ANSI_RE.sub("", text)


def _is_tty() -> bool:
    """Return True if stderr is attached to an interactive terminal and NO_COLOR is not set."""
    if "NO_COLOR" in os.environ:
        return False
    return sys.stderr.isatty() and os.environ.get("TERM", "").lower() not in ("dumb", "")


def _cprint(text: str, file: Any = None, no_color: bool = False, end: str = "\n") -> None:
    """Print ANSI text, stripping colors if stream is not a TTY or --no-color is set."""
    target = file or sys.stderr
    if no_color or not _is_tty():
        text = _strip_ansi(text)
    print(text, file=target, flush=True, end=end)


def _truncate_str(text: str, max_len: int = 50) -> str:
    """Truncate text for UI alignment."""
    return text if len(text) <= max_len else text[: max_len - 3] + "..."


def print_human_readable_ui(data: dict[str, Any], no_color: bool = False) -> None:
    """Render a human-friendly box UI to stderr."""
    if not _is_tty() or no_color:
        return

    success = data.get("success", False)
    action = data.get("action", "unknown")
    status_color = NEON_GREEN if success else NEON_RED
    status_symbol = "✓" if success else "✗"
    status_text = "SUCCESS" if success else "FAILED"

    box_w = 66
    border = "─" * box_w

    _cprint(f"{NEON_PURPLE}╭{border}╮{RESET}")
    _cprint(f"{NEON_PURPLE}│{RESET} {NEON_PINK}⚡ [SYSTEM TOOL v{__version__}]{RESET} {NEON_CYAN}Action: {action.upper()}{RESET} {status_color}{BOLD}{status_symbol} {status_text}{RESET}")
    _cprint(f"{NEON_PURPLE}├{border}┤{RESET}")

    if not success and "error" in data:
        _cprint(f"{NEON_PURPLE}│{RESET} {NEON_RED}Error:{RESET}     {_truncate_str(str(data['error']), 52)}")
    else:
        results = data.get("results", {})
        
        if "cpu" in results:
            cpu = results["cpu"]
            _cprint(f"{NEON_PURPLE}│{RESET} {BOLD}CPU:{RESET} {_truncate_str(cpu.get('model', 'N/A'), 35)} ({cpu.get('cores')} cores, {cpu.get('arch')})")
            _cprint(f"{NEON_PURPLE}│{RESET}      Load Avg: {NEON_YELLOW}{cpu.get('load_avg')}{RESET}")

        if "memory" in results:
            mem = results["memory"]
            _cprint(f"{NEON_PURPLE}│{RESET} {BOLD}RAM:{RESET} Used {NEON_YELLOW}{mem.get('used_mb')} MB{RESET} / {mem.get('total_mb')} MB ({NEON_PINK}{mem.get('used_percent')}%{RESET})")

        if "disk" in results:
            disk = results["disk"]
            _cprint(f"{NEON_PURPLE}│{RESET} {BOLD}Disk:{RESET} Used {NEON_YELLOW}{disk.get('used_gb')} GB{RESET} / {disk.get('total_gb')} GB ({NEON_PINK}{disk.get('used_percent')}%{RESET})")

        if "uptime" in results:
            up = results["uptime"]
            _cprint(f"{NEON_PURPLE}│{RESET} {BOLD}Uptime:{RESET} {NEON_GREEN}{up.get('uptime_formatted')}{RESET}")

        if "packages" in results:
            pkg = results["packages"]
            _cprint(f"{NEON_PURPLE}│{RESET} {BOLD}Packages:{RESET} {pkg.get('total_found')} installed via {pkg.get('manager')}")
            for item in pkg.get("packages", [])[:5]:
                pkg_line = f"{item['name']} ({item['version']})"
                _cprint(f"{NEON_PURPLE}│{RESET}   {NEON_CYAN}›{RESET} {_truncate_str(pkg_line, 55)}")

        if "notify" in results:
            notif = results["notify"]
            _cprint(f"{NEON_PURPLE}│{RESET} {BOLD}Telegram Notification:{RESET} Sent {notif.get('chunks_sent')} message chunk(s)")

    _cprint(f"{NEON_PURPLE}├{border}┤{RESET}")
    _cprint(f"{NEON_PURPLE}│{RESET} {NEON_CYAN}Duration:{RESET} {DIM}{data.get('duration_ms', 0)}ms{RESET}  |  {NEON_CYAN}Cached:{RESET} {data.get('cached', False)}")
    _cprint(f"{NEON_PURPLE}╰{border}╯{RESET}")


# ==============================================================================
# SECTION 3: Agent & Environment Helpers
# ==============================================================================

def get_agent_var(name: str, default: str = "") -> str:
    """Access agent user-defined variables."""
    return os.environ.get(f"LLM_AGENT_VAR_{name.upper()}", default)


def get_builtin_var(name: str) -> Optional[str]:
    """Access agent built-in environment variables."""
    return os.environ.get(f"LLM_AGENT_VAR_{name}")


def get_execution_context() -> dict[str, Any]:
    """Extract complete execution context."""
    termux_prefix = os.environ.get("PREFIX", "")
    return {
        "tool_name": os.environ.get("LLM_TOOL_NAME", "sys_tool.py"),
        "cache_dir": os.environ.get("LLM_TOOL_CACHE_DIR"),
        "root_dir": os.environ.get("LLM_ROOT_DIR"),
        "output_path": os.environ.get("LLM_OUTPUT"),
        "cwd": get_builtin_var("__cwd__") or os.getcwd(),
        "termux_prefix": termux_prefix,
        "is_termux": "com.termux" in termux_prefix or Path("/data/data/com.termux").exists(),
    }


def resolve_credentials(token: Optional[str], chat_id: Optional[str]) -> Tuple[str, str]:
    """Resolve Telegram token and chat ID across options, env vars, and defaults."""
    res_token = token or get_agent_var("TELEGRAM_BOT_TOKEN") or os.environ.get("TELEGRAM_BOT_TOKEN") or DEFAULT_BOT_TOKEN
    res_chat_id = chat_id or get_agent_var("TELEGRAM_CHAT_ID") or os.environ.get("TELEGRAM_CHAT_ID") or DEFAULT_CHAT_ID
    return res_token.strip(), str(res_chat_id).strip()


# ==============================================================================
# SECTION 4: Native Caching & Signal Handlers
# ==============================================================================

class ToolCache:
    """Caching utility with TTL support."""

    def __init__(self, cache_dir: Optional[Path] = None) -> None:
        self.cache_dir = cache_dir or Path(os.environ.get("LLM_TOOL_CACHE_DIR", Path.home() / ".cache" / "aichat_tools"))
        try:
            self.cache_dir.mkdir(parents=True, exist_ok=True)
        except OSError:
            pass

    def _make_key(self, key_data: str) -> str:
        versioned_data = f"{__version__}:{key_data}"
        return hashlib.sha256(versioned_data.encode("utf-8")).hexdigest()

    def get(self, key_data: str, ttl_seconds: int = 60) -> Optional[Any]:
        cache_file = self.cache_dir / f"{self._make_key(key_data)}.cache"
        if not cache_file.exists():
            return None
        try:
            if time.time() - cache_file.stat().st_mtime > ttl_seconds:
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
# SECTION 5: System Inspectors & Subsystem Engine
# ==============================================================================

def _safe_getloadavg() -> List[float]:
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
            
            # MemAvailable fallback formula for Linux kernels < 3.14
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
                ["dpkg-query", "-W", "-f=${Package}\t${Version}\n"],
                capture_output=True,
                text=True,
                timeout=8,
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
            )
            if res.returncode == 0:
                for line in res.stdout.splitlines():
                    if "/" in line:
                        parts = line.split("/")
                        pkgs.append({"name": parts[0], "version": parts[1] if len(parts) > 1 else "installed"})
        elif shutil.which("rpm"):
            pkg_manager = "rpm"
            res = subprocess.run(
                ["rpm", "-qa", "--queryformat", "%{NAME}\t%{VERSION}\n"],
                capture_output=True,
                text=True,
                timeout=8,
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


# Telegram Notification Engine Sub-functions
def _chunk_text(text: str, max_len: int = MAX_TELEGRAM_MSG_LEN) -> List[str]:
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
# SECTION 6: Execution Engine Router
# ==============================================================================

def execute_tool(
    action: str = "all",
    message: Optional[str] = None,
    title: Optional[str] = None,
    file: Optional[str] = None,
    media_type: str = "auto",
    pkg_filter: Optional[str] = None,
    limit: Optional[int] = 50,
    token: Optional[str] = None,
    chat_id: Optional[str] = None,
    parse_mode: str = "html",
    silent: bool = False,
    disable_preview: bool = False,
    use_cache: bool = False,
    no_color: bool = False,
    verbose: bool = False,
) -> dict[str, Any]:
    """Execute system tools based on target action."""
    start_time = time.monotonic()
    action_norm = action.lower().strip()
    limit_val = limit if (limit is not None and limit > 0) else 50

    if verbose:
        logging.basicConfig(level=logging.DEBUG, format="[DEBUG] %(message)s")
        logging.debug(f"Executing action: {action_norm}")

    file_target = Path(file).expanduser().resolve() if file else None
    if file_target and not file_target.exists():
        return {
            "success": False,
            "action": action_norm,
            "error": f"Target attachment file not found: {file}",
            "exit_code": EXIT_FILE_NOT_FOUND,
            "duration_ms": 0.0,
        }

    cache = ToolCache()
    cache_key = f"{action_norm}:{pkg_filter}:{limit_val}:{message}:{file}:{title}:{parse_mode}"
    if use_cache:
        cached_res = cache.get(cache_key)
        if cached_res:
            cached_res["cached"] = True
            return cached_res

    shutdown = GracefulShutdown()
    results: dict[str, Any] = {}
    timings_ms: dict[str, float] = {}

    try:
        if action_norm in ("all", "cpu"):
            if shutdown.should_stop():
                raise ToolError("Interrupted by user signal.", exit_code=EXIT_INTERRUPTED)
            t0 = time.monotonic()
            results["cpu"] = inspect_cpu()
            timings_ms["cpu"] = round((time.monotonic() - t0) * 1000, 2)

        if action_norm in ("all", "mem"):
            if shutdown.should_stop():
                raise ToolError("Interrupted by user signal.", exit_code=EXIT_INTERRUPTED)
            t0 = time.monotonic()
            results["memory"] = inspect_memory()
            timings_ms["mem"] = round((time.monotonic() - t0) * 1000, 2)

        if action_norm in ("all", "disk"):
            if shutdown.should_stop():
                raise ToolError("Interrupted by user signal.", exit_code=EXIT_INTERRUPTED)
            t0 = time.monotonic()
            results["disk"] = inspect_disk("/")
            timings_ms["disk"] = round((time.monotonic() - t0) * 1000, 2)

        if action_norm in ("all", "uptime"):
            if shutdown.should_stop():
                raise ToolError("Interrupted by user signal.", exit_code=EXIT_INTERRUPTED)
            t0 = time.monotonic()
            results["uptime"] = inspect_uptime()
            timings_ms["uptime"] = round((time.monotonic() - t0) * 1000, 2)

        if action_norm in ("all", "pkg"):
            if shutdown.should_stop():
                raise ToolError("Interrupted by user signal.", exit_code=EXIT_INTERRUPTED)
            t0 = time.monotonic()
            results["packages"] = inspect_packages(query=pkg_filter, limit=limit_val)
            timings_ms["pkg"] = round((time.monotonic() - t0) * 1000, 2)

        if action_norm == "notify":
            if shutdown.should_stop():
                raise ToolError("Interrupted by user signal.", exit_code=EXIT_INTERRUPTED)
            if not message and not file_target:
                raise ToolError("Notification action requires --message or --file.", exit_code=EXIT_INVALID_INPUT)
            t0 = time.monotonic()
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
            timings_ms["notify"] = round((time.monotonic() - t0) * 1000, 2)

        if action_norm == "all":
            results["timings_ms"] = timings_ms
            results["actions_completed"] = [
                k for k in ("cpu", "memory", "disk", "uptime", "packages") if k in results
            ]
            if "notify" in results:
                results["actions_completed"].append("notify")

        return results

    except ToolError:
        raise
    except KeyboardInterrupt:
        raise ToolError("Interrupted by user signal.", exit_code=EXIT_INTERRUPTED)
    except Exception as exc:
        raise ToolError(f"Unexpected failure during '{action_norm}': {exc}", exit_code=EXIT_ERROR)

