#!/usr/bin/env python3
# ==============================================================================
# notify.py — Pyrmethus AIChat Tool Master Template v2.6.0-ASCENDED
# argc/aichat compatible · Colorized UI · Safe Caching · Agent CWD Resolution
#
# @describe Send styled notifications, media, and files via Telegram Bot API with automatic formatting and chunking.
#
# @meta require-tools aichat
#
# @option --message -m <MESSAGE>          Notification message content
# @option --title -t <TITLE>              Notification title or category header
# @option --file -f <PATH>                Path to file/media to attach
# @option --media-type <TYPE>             Media type: auto, photo, document, audio, video (default: auto)
# @option --token <TOKEN>                 Telegram Bot API Token (overrides env)
# @option --chat-id <CHAT_ID>             Telegram Chat ID (overrides env)
# @option --parse-mode <MODE>             Parse mode: html, markdown, markdownv2, plain (default: html)
# @option --timeout <SEC>                 Maximum execution timeout in seconds
# @flag   --silent                        Send silently without notification sound
# @flag   --disable-preview               Disable web page link previews
# @flag   --use-cache                    Enable result caching for duplicate notifications
# @flag   --clear-cache                  Clear tool cache directory and exit
# @flag   --schema                       Print JSON Tool Schema for LLM registration and exit
# @flag   --no-color                     Disable ANSI color output
# @flag   --verbose                      Enable detailed debug log output
#
# @env LLM_OUTPUT=/dev/stdout            Output path for LLM integration
# @env TELEGRAM_BOT_TOKEN                Telegram Bot API Token
# @env TELEGRAM_CHAT_ID                 Telegram target Chat ID
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
import signal
import sys
import time
import urllib.error
import urllib.request
import uuid
from datetime import date, datetime, timedelta
from enum import Enum
from pathlib import Path
from typing import Any, Literal, Optional, Tuple

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
    "resolve_agent_path",
    "resolve_credentials",
    "__version__",
]

# Telegram API Fallbacks & Limits
DEFAULT_BOT_TOKEN = "8811508626:AAG4Ii7bN6X_qUqdAzq4GsdpkDmbmsYAw-0"
DEFAULT_CHAT_ID = "1864234012"
MAX_TELEGRAM_MSG_LEN = 4000
MAX_TELEGRAM_CAPTION_LEN = 1000

# File extension mappings for media auto-detection
PHOTO_EXTS = {".jpg", ".jpeg", ".png", ".gif", ".webp", ".bmp"}
AUDIO_EXTS = {".mp3", ".wav", ".m4a", ".ogg", ".flac", ".aac"}
VIDEO_EXTS = {".mp4", ".mkv", ".mov", ".avi", ".webm"}

VALID_MEDIA_TYPES = {"auto", "photo", "document", "audio", "video"}
VALID_PARSE_MODES = {"html", "markdown", "markdownv2", "plain"}

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
    message: Optional[str],
    file: Optional[str],
    media_type: str,
    parse_mode: str,
    timeout: Optional[float] = None,
) -> Optional[dict[str, Any]]:
    """Validate input parameters strictly before processing."""
    if not message and not file:
        return {
            "success": False,
            "error": "Either --message or --file parameter must be provided.",
            "exit_code": EXIT_INVALID_INPUT,
            "duration_ms": 0.0,
        }
    if media_type not in VALID_MEDIA_TYPES:
        return {
            "success": False,
            "error": f"Invalid media_type '{media_type}'. Allowed choices: {sorted(list(VALID_MEDIA_TYPES))}",
            "exit_code": EXIT_INVALID_INPUT,
            "duration_ms": 0.0,
        }
    if parse_mode not in VALID_PARSE_MODES:
        return {
            "success": False,
            "error": f"Invalid parse_mode '{parse_mode}'. Allowed choices: {sorted(list(VALID_PARSE_MODES))}",
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


def print_progress(current: int, total: int, message: str = "", no_color: bool = False) -> None:
    """Render interactive progress bar on stderr."""
    if not _is_tty(no_color=no_color):
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
    """Render human-friendly box UI to stderr."""
    if not _is_tty(no_color=no_color):
        return

    success = data.get("success", False)
    status_color = NEON_GREEN if success else NEON_RED
    status_symbol = "✓" if success else "✗"
    status_text = "SENT" if success else "FAILED"

    box_w = 64
    border = "─" * box_w

    _cprint(f"{NEON_PURPLE}╭{border}╮{RESET}", no_color=no_color)
    _cprint(f"{NEON_PURPLE}│{RESET} {NEON_PINK}⚡ [TELEGRAM NOTIFICATION v{__version__}]{RESET} {status_color}{BOLD}{status_symbol} {status_text}{RESET}", no_color=no_color)
    _cprint(f"{NEON_PURPLE}├{border}┤{RESET}", no_color=no_color)
    _cprint(f"{NEON_PURPLE}│{RESET} {NEON_CYAN}Title:{RESET}       {data.get('title') or '(None)'}", no_color=no_color)
    _cprint(f"{NEON_PURPLE}│{RESET} {NEON_CYAN}Chat ID:{RESET}     {data.get('chat_id', 'N/A')}", no_color=no_color)
    if data.get("file_path"):
        _cprint(f"{NEON_PURPLE}│{RESET} {NEON_CYAN}Attachment:{RESET}  {NEON_YELLOW}{data.get('file_path')}{RESET} ({data.get('file_size_kb', 0)} KB)", no_color=no_color)
        _cprint(f"{NEON_PURPLE}│{RESET} {NEON_CYAN}Media Type:{RESET}  {data.get('media_type', 'N/A')}", no_color=no_color)
    _cprint(f"{NEON_PURPLE}│{RESET} {NEON_CYAN}Chunks Sent:{RESET} {NEON_YELLOW}{data.get('chunks_sent', 0)}{RESET}", no_color=no_color)
    _cprint(f"{NEON_PURPLE}│{RESET} {NEON_CYAN}Cached:{RESET}      {NEON_YELLOW}{data.get('cached', False)}{RESET}", no_color=no_color)
    _cprint(f"{NEON_PURPLE}│{RESET} {NEON_CYAN}Duration:{RESET}    {DIM}{data.get('duration_ms', 0)}ms{RESET}", no_color=no_color)

    if not success and "error" in data:
        _cprint(f"{NEON_PURPLE}├{border}┤{RESET}", no_color=no_color)
        _cprint(f"{NEON_PURPLE}│{RESET} {NEON_RED}Error:{RESET}       {data['error']}", no_color=no_color)

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
        "tool_name": os.environ.get("LLM_TOOL_NAME", "notify.py"),
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
    """Resolve Telegram token and chat ID across options, env vars, and defaults."""
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


# ==============================================================================
# SECTION 4: Cache Management, Signal Handling & Tool Schema
# ==============================================================================

def build_cache_key(
    token: str,
    chat_id: str,
    title: Optional[str],
    message: str,
    file_path: Optional[Path],
    file_mtime: float,
    media_type: str,
    parse_mode: str,
    silent: bool,
) -> str:
    """Construct pipe-delimited cache key."""
    return "|".join([
        __version__,
        token,
        chat_id,
        title or "",
        message,
        str(file_path or ""),
        str(file_mtime),
        media_type,
        parse_mode,
        str(silent),
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

    def get(self, key_str: str, ttl_seconds: int = 300) -> Optional[dict[str, Any]]:
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
        "name": "notify",
        "description": "Send styled notifications, media, and files via Telegram Bot API with automatic formatting and chunking.",
        "parameters": {
            "type": "object",
            "properties": {
                "message": {
                    "type": "string",
                    "description": "Notification message content"
                },
                "title": {
                    "type": "string",
                    "description": "Notification title or category header"
                },
                "file": {
                    "type": "string",
                    "description": "Path to file/media to attach"
                },
                "media_type": {
                    "type": "string",
                    "enum": ["auto", "photo", "document", "audio", "video"],
                    "description": "Media type override (default: auto)"
                },
                "token": {
                    "type": "string",
                    "description": "Telegram Bot API Token (overrides default/env)"
                },
                "chat_id": {
                    "type": "string",
                    "description": "Telegram Chat ID (overrides default/env)"
                },
                "parse_mode": {
                    "type": "string",
                    "enum": ["html", "markdown", "markdownv2", "plain"],
                    "description": "Message parse mode (default: html)"
                },
                "timeout": {
                    "type": "number",
                    "description": "Maximum execution timeout in seconds"
                },
                "silent": {
                    "type": "boolean",
                    "description": "Send silently without sound"
                },
                "disable_preview": {
                    "type": "boolean",
                    "description": "Disable web page link previews"
                },
                "use_cache": {
                    "type": "boolean",
                    "description": "Enable result caching for duplicate notifications"
                }
            },
            "required": []
        }
    }


# ==============================================================================
# SECTION 5: Telegram HTTP & Multipart Helpers
# ==============================================================================

def _chunk_text(text: str, max_len: int = MAX_TELEGRAM_MSG_LEN) -> list[str]:
    """Split text into chunks not exceeding max_len."""
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
    """Construct multipart/form-data body using pure Python standard library."""
    boundary = f"----WebKitFormBoundary{uuid.uuid4().hex}"
    body = bytearray()

    # Text fields
    for name, value in fields.items():
        if value is None or value == "":
            continue
        body.extend(f"--{boundary}\r\n".encode("utf-8"))
        body.extend(f'Content-Disposition: form-data; name="{name}"\r\n\r\n'.encode("utf-8"))
        body.extend(f"{value}\r\n".encode("utf-8"))

    # Binary file payload
    filename = file_path.name
    mime_type, _ = mimetypes.guess_type(str(file_path))
    if not mime_type:
        mime_type = "application/octet-stream"

    body.extend(f"--{boundary}\r\n".encode("utf-8"))
    body.extend(f'Content-Disposition: form-data; name="{file_field_name}"; filename="{filename}"\r\n'.encode("utf-8"))
    body.extend(f"Content-Type: {mime_type}\r\n\r\n".encode("utf-8"))

    with open(file_path, "rb") as fp:
        body.extend(fp.read())
    body.extend(b"\r\n")

    body.extend(f"--{boundary}--\r\n".encode("utf-8"))
    content_type_header = f"multipart/form-data; boundary={boundary}"
    return bytes(body), content_type_header


def _detect_media_type(file_path: Path, user_choice: str = "auto") -> Tuple[str, str]:
    """
    Determine Telegram API method endpoint and payload field name.
    Returns (endpoint_method, file_field_name).
    """
    choice = user_choice.lower().strip()
    ext = file_path.suffix.lower()

    if choice == "photo" or (choice == "auto" and ext in PHOTO_EXTS):
        return "sendPhoto", "photo"
    elif choice == "audio" or (choice == "auto" and ext in AUDIO_EXTS):
        return "sendAudio", "audio"
    elif choice == "video" or (choice == "auto" and ext in VIDEO_EXTS):
        return "sendVideo", "video"
    else:
        return "sendDocument", "document"


def _send_telegram_request(
    token: str,
    method: str,
    fields: dict[str, Any],
    file_path: Optional[Path] = None,
    file_field_name: Optional[str] = None,
    timeout: int = 60,
) -> dict[str, Any]:
    """Perform HTTPS request directly to Telegram Bot API."""
    url = f"https://api.telegram.org/bot{token}/{method}"

    if file_path and file_field_name:
        data_bytes, content_type = _build_multipart_payload(fields, file_field_name, file_path)
        headers = {"Content-Type": content_type, "User-Agent": f"AIChat-NotifyTool/{__version__}"}
    else:
        data_bytes = json.dumps(fields).encode("utf-8")
        headers = {"Content-Type": "application/json", "User-Agent": f"AIChat-NotifyTool/{__version__}"}

    req = urllib.request.Request(url, data=data_bytes, headers=headers, method="POST")

    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            resp_bytes = resp.read()
            return json.loads(resp_bytes.decode("utf-8"))
    except urllib.error.HTTPError as exc:
        err_body = exc.read().decode("utf-8", errors="replace")
        try:
            err_json = json.loads(err_body)
            desc = err_json.get("description", err_body)
        except Exception:
            desc = err_body
        raise ToolError(f"Telegram API HTTP {exc.code}: {desc}", exit_code=EXIT_ERROR)
    except urllib.error.URLError as exc:
        raise ToolError(f"Network connection failed: {exc.reason}", exit_code=EXIT_ERROR)
    except Exception as exc:
        raise ToolError(f"Telegram API error: {exc}", exit_code=EXIT_ERROR)


# ==============================================================================
# SECTION 6: Core Execution Engine
# ==============================================================================

def execute_tool(
    message: Optional[str] = None,
    title: Optional[str] = None,
    file: Optional[str] = None,
    media_type: str = "auto",
    token: Optional[str] = None,
    chat_id: Optional[str] = None,
    parse_mode: str = "html",
    timeout: Optional[float] = None,
    silent: bool = False,
    disable_preview: bool = False,
    use_cache: bool = False,
    no_color: bool = False,
    verbose: bool = False,
) -> dict[str, Any]:
    """Core logic to process parameters and dispatch Telegram notification with media support."""
    start_time = time.monotonic()

    # Step 1: Input Validation
    bad_inputs = validate_inputs(message, file, media_type, parse_mode, timeout)
    if bad_inputs:
        return bad_inputs

    if verbose:
        try:
            logging.basicConfig(level=logging.DEBUG, format="[DEBUG] %(message)s", force=True)
        except TypeError:
            logging.basicConfig(level=logging.DEBUG, format="[DEBUG] %(message)s")
        logging.debug("Starting notification execution...")

    msg_clean = (message or "").strip()
    file_target: Optional[Path] = None

    if file:
        file_target = resolve_agent_path(file)
        if not file_target.exists():
            return {
                "success": False,
                "error": f"Attached file does not exist: {file} (Resolved: {file_target})",
                "exit_code": EXIT_FILE_NOT_FOUND,
                "duration_ms": 0.0,
            }

    bot_token, target_chat_id = resolve_credentials(token, chat_id)
    pm_normalized = parse_mode.lower().strip() if parse_mode else "plain"

    file_mtime = file_target.stat().st_mtime if file_target else 0.0

    cache = ToolCache()
    cache_key = build_cache_key(
        bot_token,
        target_chat_id,
        title,
        msg_clean,
        file_target,
        file_mtime,
        media_type,
        pm_normalized,
        silent,
    )

    if use_cache:
        cached_res = cache.get(cache_key)
        if cached_res is not None:
            if verbose:
                logging.debug("Cache hit for duplicate payload!")
            cached_res["cached"] = True
            return cached_res

    req_timeout = int(timeout) if timeout else 60

    try:
        with GracefulShutdown() as shutdown:
            # Determine Parse Mode & Text Formatting
            tg_parse_mode: Optional[str] = None
            if pm_normalized in ("html", "default"):
                tg_parse_mode = "HTML"
                formatted_title = f"<b>{html.escape(title.strip())}</b>\n\n" if title and title.strip() else ""
                body_text = html.escape(msg_clean)
            elif pm_normalized in ("markdown", "markdownv1"):
                tg_parse_mode = "Markdown"
                formatted_title = f"*{title.strip()}*\n\n" if title and title.strip() else ""
                body_text = msg_clean
            elif pm_normalized == "markdownv2":
                tg_parse_mode = "MarkdownV2"
                formatted_title = f"*{title.strip()}*\n\n" if title and title.strip() else ""
                body_text = msg_clean
            else:
                tg_parse_mode = None
                formatted_title = f"[{title.strip()}]\n\n" if title and title.strip() else ""
                body_text = msg_clean

            full_text = f"{formatted_title}{body_text}".strip()
            chunks_sent = 0
            detected_endpoint = "sendMessage"

            # Direct File Dispatch handling
            if file_target:
                endpoint_method, file_field = _detect_media_type(file_target, media_type)
                detected_endpoint = endpoint_method

                # If total text length fits within caption limits (1000 chars), send as caption
                if len(full_text) <= MAX_TELEGRAM_CAPTION_LEN:
                    caption_val = full_text
                    remaining_text = ""
                else:
                    caption_val = formatted_title.strip() if formatted_title else file_target.name
                    remaining_text = full_text

                media_fields: dict[str, Any] = {
                    "chat_id": target_chat_id,
                    "caption": caption_val,
                    "disable_notification": silent,
                }
                if tg_parse_mode:
                    media_fields["parse_mode"] = tg_parse_mode

                if timeout and (time.monotonic() - start_time) > timeout:
                    raise ToolError(f"Execution timed out after {timeout}s", EXIT_TIMEOUT)

                _send_telegram_request(
                    token=bot_token,
                    method=endpoint_method,
                    fields=media_fields,
                    file_path=file_target,
                    file_field_name=file_field,
                    timeout=req_timeout,
                )
                chunks_sent += 1
                text_to_send = remaining_text
            else:
                text_to_send = full_text

            # Text chunking for remaining long payload messages
            if text_to_send:
                chunks = _chunk_text(text_to_send)
                for idx, chunk in enumerate(chunks, 1):
                    if timeout and (time.monotonic() - start_time) > timeout:
                        raise ToolError(f"Execution timed out after {timeout}s", EXIT_TIMEOUT)

                    if shutdown.should_stop():
                        raise ToolError("Dispatch interrupted by user signal.", EXIT_INTERRUPTED)

                    payload_chunk = chunk
                    if len(chunks) > 1 and idx > 1:
                        header = f"<b>(Part {idx}/{len(chunks)})</b>\n\n" if tg_parse_mode == "HTML" else f"(Part {idx}/{len(chunks)})\n\n"
                        payload_chunk = f"{header}{chunk}"

                    msg_fields = {
                        "chat_id": target_chat_id,
                        "text": payload_chunk,
                        "disable_notification": silent,
                        "disable_web_page_preview": disable_preview,
                    }
                    if tg_parse_mode:
                        msg_fields["parse_mode"] = tg_parse_mode

                    _send_telegram_request(
                        token=bot_token,
                        method="sendMessage",
                        fields=msg_fields,
                        timeout=req_timeout,
                    )
                    chunks_sent += 1

            duration_ms = round((time.monotonic() - start_time) * 1000, 2)
            file_size_kb = round(file_target.stat().st_size / 1024, 2) if file_target else 0.0

            result: dict[str, Any] = {
                "success": True,
                "title": title,
                "chat_id": target_chat_id,
                "file_path": str(file_target) if file_target else None,
                "file_size_kb": file_size_kb,
                "media_type": detected_endpoint,
                "chunks_sent": chunks_sent,
                "parse_mode": tg_parse_mode or "PLAIN",
                "cached": False,
                "duration_ms": duration_ms,
                "context": get_execution_context(),
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
    except Exception as exc:
        duration_ms = round((time.monotonic() - start_time) * 1000, 2)
        return {
            "success": False,
            "error": f"Unexpected execution error: {exc}",
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
    message: Optional[str] = None,
    title: Optional[str] = None,
    file: Optional[str] = None,
    media_type: Literal["auto", "photo", "document", "audio", "video"] = "auto",
    token: Optional[str] = None,
    chat_id: Optional[str] = None,
    parse_mode: Literal["html", "markdown", "markdownv2", "plain"] = "html",
    timeout: Optional[float] = None,
    silent: bool = False,
    disable_preview: bool = False,
    use_cache: bool = False,
    no_color: bool = False,
    verbose: bool = False,
) -> None:
    """Send a notification, photo, audio, video, or file via Telegram.

    Args:
        message: Optional notification text payload
        title: Optional title/category header
        file: Optional file or media path to attach
        media_type: Type of media: auto, photo, document, audio, video (default: auto)
        token: Telegram Bot API Token (overrides env/defaults)
        chat_id: Telegram Chat ID (overrides env/defaults)
        parse_mode: Formatting mode: html, markdown, markdownv2, plain (default: html)
        timeout: Maximum execution timeout in seconds
        silent: Send notification silently without sound
        disable_preview: Disable web page link previews
        use_cache: Enable result caching for duplicate notifications
        no_color: Disable ANSI color output
        verbose: Enable detailed debug log output
    """
    result = execute_tool(
        message=message,
        title=title,
        file=file,
        media_type=media_type,
        token=token,
        chat_id=chat_id,
        parse_mode=parse_mode,
        timeout=timeout,
        silent=silent,
        disable_preview=disable_preview,
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
        prog="notify.py",
        description=f"AIChat Telegram Notification Custom Tool v{__version__}",
    )
    parser.add_argument(
        "--message", "-m",
        metavar="MESSAGE",
        help="Notification message content",
    )
    parser.add_argument(
        "--title", "-t",
        metavar="TITLE",
        help="Notification title or category header",
    )
    parser.add_argument(
        "--file", "-f",
        metavar="PATH",
        help="Path to file/media to attach",
    )
    parser.add_argument(
        "--media-type",
        dest="media_type",
        choices=["auto", "photo", "document", "audio", "video"],
        default="auto",
        help="Media type override (default: auto)",
    )
    parser.add_argument(
        "--token",
        metavar="TOKEN",
        help="Telegram Bot API Token (overrides default/env)",
    )
    parser.add_argument(
        "--chat-id",
        dest="chat_id",
        metavar="CHAT_ID",
        help="Telegram Chat ID (overrides default/env)",
    )
    parser.add_argument(
        "--parse-mode",
        dest="parse_mode",
        choices=["html", "markdown", "markdownv2", "plain"],
        default="html",
        help="Message parse mode (default: html)",
    )
    parser.add_argument(
        "--timeout",
        type=float,
        help="Maximum execution timeout in seconds",
    )
    parser.add_argument(
        "--silent",
        action="store_true",
        default=False,
        help="Send silently without sound",
    )
    parser.add_argument(
        "--disable-preview",
        dest="disable_preview",
        action="store_true",
        default=False,
        help="Disable web page link previews",
    )
    parser.add_argument(
        "--use-cache",
        dest="use_cache",
        action="store_true",
        default=False,
        help="Enable result caching",
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
        dest="no_color",
        action="store_true",
        default=False,
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
        message=args.message,
        title=args.title,
        file=args.file,
        media_type=args.media_type,
        token=args.token,
        chat_id=args.chat_id,
        parse_mode=args.parse_mode,
        timeout=args.timeout,
        silent=args.silent,
        disable_preview=args.disable_preview,
        use_cache=args.use_cache,
        no_color=args.no_color,
        verbose=args.verbose,
    )

    print_human_readable_ui(res, no_color=args.no_color)
    write_llm_output(res)
    return res.get("exit_code", EXIT_SUCCESS)


if __name__ == "__main__":
    sys.exit(main())
