#!/usr/bin/env python3
"""
mcp_tool_server.py - Enhanced local MCP tool server (Production Hardened v4.1)

v4.1 highlights
---------------
* Tightened execution and filesystem boundaries: shell path traversal and nested
  interpreter escape routes are rejected; child process groups are terminated on
  timeout; archive extraction has entry and expanded-size limits.
* Correctness and resilience: argument coercion rejects malformed/non-finite values,
  oversized HTTP bodies close cleanly, DNS record types are honored, and tool
  failures are reflected consistently in telemetry and MCP responses.
* Maintains the multi-engine web search, parsing, conversion, filesystem, memory,
  diagnostics, and system tools introduced in v4.0, with expanded offline coverage.

Backwards compatible: existing tool names and parameters retain their defaults.
"""

# ---------------------------------------------------------------------------
# Imports & Global Settings
# ---------------------------------------------------------------------------
import ast
import base64
import binascii
import contextlib
import csv
import datetime as _dt
import difflib
import fnmatch
import hashlib
import hmac
import http.client
import html as html_module
import io
import ipaddress
import itertools
import json
import logging
import mimetypes
import math
import ntpath
import os
import platform
import random
import re
import shlex
import shutil
import signal
import socket
import stat
import string
import subprocess
import sys
import tarfile
import tempfile
import textwrap
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
import xml.etree.ElementTree as ET
import zipfile
from collections import Counter, defaultdict
from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, as_completed, wait
from contextlib import contextmanager
from html.parser import HTMLParser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any, Callable, Dict, Generator, List, Optional, Sequence, Set, Tuple, Type

SERVER_NAME = "mcp-enhanced-wizard"
SERVER_VERSION = "4.1.0"
USER_AGENT = f"MCP-Tool-Server/{SERVER_VERSION} (+compatible; robust-fetcher)"
BROWSER_UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
              "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36")

# Terminal Colors for Termux / Visual Feedback
GREEN = "\033[92m"
RED = "\033[91m"
YELLOW = "\033[93m"
RESET = "\033[0m"

# Optional psutil - falls back gracefully if missing
try:
    import psutil
    HAS_PSUTIL = True
except ImportError:  # pragma: no cover - environment dependent
    HAS_PSUTIL = False

# Optional PyYAML - only used by the yaml conversion tool
try:
    import yaml as _yaml
    HAS_YAML = True
except ImportError:  # pragma: no cover - environment dependent
    HAS_YAML = False

# Optional resource module (POSIX) - used to harden sandboxed execution
try:
    import resource as _resource
    HAS_RESOURCE = True
except ImportError:  # pragma: no cover - non POSIX
    HAS_RESOURCE = False

# ---------------------------------------------------------------------------
# Configuration & Constants
# ---------------------------------------------------------------------------
_TRUTHY = {"1", "true", "yes", "on", "y", "t"}
_FALSY = {"0", "false", "no", "off", "n", "f", ""}


def _to_bool(value: Any, default: bool = False) -> bool:
    """Parse booleans coming from JSON, env vars or MCP clients (strings included)."""
    if isinstance(value, bool):
        return value
    if value is None:
        return default
    if isinstance(value, (int, float)):
        if isinstance(value, float) and not math.isfinite(value):
            return default
        return bool(value)
    text = str(value).strip().lower()
    if text in _TRUTHY:
        return True
    if text in _FALSY:
        return False
    return default


def _pick_sandbox_root() -> str:
    """Resolve the sandbox root, falling back when the configured path is unusable."""
    candidates = [os.getenv("MCP_SANDBOX_ROOT") or "/data/data/com.termux/files/home",
                  os.path.expanduser("~/.mcp_sandbox"),
                  tempfile.gettempdir()]
    for candidate in candidates:
        path = os.path.realpath(os.path.abspath(os.path.expanduser(candidate)))
        try:
            os.makedirs(path, exist_ok=True)
            if os.access(path, os.W_OK):
                return path
        except OSError:
            continue
    return os.path.realpath(tempfile.gettempdir())


SANDBOX_ROOT = _pick_sandbox_root()
CONFIG_PATH = os.path.join(SANDBOX_ROOT, "mts_config.json")

# Load optional external config file (JSON) - allows users to tweak limits dynamically
_user_cfg: Dict[str, Any] = {}
_config_error: str = ""
if os.path.exists(CONFIG_PATH):
    try:
        if os.path.getsize(CONFIG_PATH) > 1024 * 1024:
            raise ValueError("config file exceeds the 1 MiB size limit")
        with open(CONFIG_PATH, "r", encoding="utf-8") as f:
            raw_config = f.read(1024 * 1024 + 1)
        if len(raw_config.encode("utf-8")) > 1024 * 1024:
            raise ValueError("config file exceeds the 1 MiB size limit")
        loaded = json.loads(raw_config)
        _user_cfg = loaded if isinstance(loaded, dict) else {}
        if not isinstance(loaded, dict):
            _config_error = "config file must contain a JSON object"
    except Exception as exc:  # pragma: no cover - depends on user file
        _user_cfg = {}
        _config_error = f"{type(exc).__name__}: {exc}"


def _cfg_val(key: str, env_name: str, default: Any, cast_type: Callable = str) -> Any:
    """Config precedence: JSON config file -> environment variable -> default.

    Booleans are parsed from strings in *both* sources (the v3.8 version used
    ``bool("false")`` for the config-file branch, which is always True).
    """
    for raw in (_user_cfg.get(key, None), os.getenv(env_name)):
        if raw is None:
            continue
        try:
            if cast_type is bool:
                return _to_bool(raw, bool(default))
            if cast_type is int and isinstance(raw, str):
                return int(raw.strip(), 0)
            return cast_type(raw)
        except Exception:
            continue
    return default


# Core operational limits & settings
API_TOKEN = str(_cfg_val("api_token", "MCP_API_TOKEN", ""))
REQUIRE_AUTH = _cfg_val("require_auth", "MCP_REQUIRE_AUTH", False, bool)
HTTP_PORT = _cfg_val("http_port", "MCP_HTTP_PORT", 8000, int)
BIND_HOST = str(_cfg_val("bind_host", "MCP_BIND_HOST", "0.0.0.0"))
LOG_LEVEL = str(_cfg_val("log_level", "MCP_LOG_LEVEL", "INFO"))
ENABLE_DANGEROUS = _cfg_val("enable_dangerous", "MCP_ENABLE_DANGEROUS", False, bool)
ALLOW_TOKEN_IN_QUERY = _cfg_val("allow_token_in_query", "MCP_ALLOW_TOKEN_IN_QUERY", False, bool)

# Search & request limits
MCP_MAX_SEARCH_RESULTS = _cfg_val("max_search_results", "MCP_MAX_SEARCH_RESULTS", 50, int)
MCP_MAX_REQUEST_BYTES = _cfg_val("max_request_bytes", "MCP_MAX_REQUEST_BYTES", 2 * 1024 * 1024, int)
MCP_MAX_RETRY_ATTEMPTS = _cfg_val("max_retry_attempts", "MCP_MAX_RETRY_ATTEMPTS", 3, int)
MCP_MAX_LINKS = _cfg_val("max_links", "MCP_MAX_LINKS", 500, int)
MCP_MAX_FEED_ITEMS = _cfg_val("max_feed_items", "MCP_MAX_FEED_ITEMS", 100, int)
MAX_HTTP_BYTES = _cfg_val("max_http_bytes", "MCP_MAX_HTTP_BYTES", 2 * 1024 * 1024, int)
MAX_DOWNLOAD_BYTES = _cfg_val("max_download_bytes", "MCP_MAX_DOWNLOAD_BYTES", 64 * 1024 * 1024, int)
MAX_ARCHIVE_ENTRIES = _cfg_val("max_archive_entries", "MCP_MAX_ARCHIVE_ENTRIES", 5000, int)
MAX_ARCHIVE_UNPACKED_BYTES = _cfg_val("max_archive_unpacked_bytes", "MCP_MAX_ARCHIVE_UNPACKED_BYTES",
                                      256 * 1024 * 1024, int)
MAX_TEXT_CHARS = _cfg_val("max_text_chars", "MCP_MAX_TEXT_CHARS", 64000, int)
MAX_FILE_SCAN_BYTES = 32 * 1024 * 1024
HTTP_TIMEOUT = max(2, min(_cfg_val("http_timeout", "MCP_HTTP_TIMEOUT", 15, int), 120))
ALLOW_PRIVATE_NETWORKS = _cfg_val("allow_private_networks", "MCP_ALLOW_PRIVATE_NETWORKS", False, bool)
CORS_ORIGIN = str(_cfg_val("cors_origin", "MCP_CORS_ORIGIN", "*"))
RETRY_BACKOFF = _cfg_val("retry_backoff", "MCP_RETRY_BACKOFF", 0.5, float)
MEMORY_FILE = os.path.expanduser(str(_cfg_val("memory_file", "MCP_MEMORY_FILE", "~/.mcp_memory.json")))
MAX_MEMORY_BYTES = _cfg_val("max_memory_bytes", "MCP_MAX_MEMORY_BYTES", 8 * 1024 * 1024, int)

# Search stack configuration
SEARCH_CACHE_TTL = _cfg_val("search_cache_ttl", "MCP_SEARCH_CACHE_TTL", 300, int)
FETCH_CACHE_TTL = _cfg_val("fetch_cache_ttl", "MCP_FETCH_CACHE_TTL", 120, int)
CACHE_MAX_ENTRIES = _cfg_val("cache_max_entries", "MCP_CACHE_MAX_ENTRIES", 512, int)
SEARCH_PARALLELISM = _cfg_val("search_parallelism", "MCP_SEARCH_PARALLELISM", 6, int)
DEFAULT_SEARCH_ENGINES = str(_cfg_val("search_engines", "MCP_SEARCH_ENGINES", "auto"))
BRAVE_API_KEY = str(_cfg_val("brave_api_key", "BRAVE_SEARCH_API_KEY", ""))
TAVILY_API_KEY = str(_cfg_val("tavily_api_key", "TAVILY_API_KEY", ""))
SERPAPI_KEY = str(_cfg_val("serpapi_key", "SERPAPI_KEY", ""))
GOOGLE_API_KEY = str(_cfg_val("google_api_key", "GOOGLE_API_KEY", ""))
GOOGLE_CSE_ID = str(_cfg_val("google_cse_id", "GOOGLE_CSE_ID", ""))
SEARXNG_URL = str(_cfg_val("searxng_url", "SEARXNG_URL", ""))
GITHUB_TOKEN = str(_cfg_val("github_token", "GITHUB_TOKEN", ""))

# Command execution allow-list (configurable). Destructive verbs are opt-in.
_DEFAULT_BASH_CMDS = ["echo", "cat", "head", "tail", "wc", "grep", "sed", "awk", "sort", "uniq",
                      "cut", "tr", "date", "sleep", "ls", "pwd", "basename", "dirname", "stat",
                      "find", "diff", "du", "df", "file", "which", "mkdir", "cp", "mv"]
_DANGEROUS_BASH_CMDS = ["rm", "rmdir", "chmod", "chown", "ln", "truncate", "dd"]


def _cfg_list(key: str, env_name: str, default: Sequence[str]) -> List[str]:
    raw = _user_cfg.get(key, None)
    if raw is None:
        raw = os.getenv(env_name)
    if raw is None:
        return list(default)
    if isinstance(raw, (list, tuple, set)):
        return [str(x).strip() for x in raw if str(x).strip()]
    return [x.strip() for x in str(raw).split(",") if x.strip()]


ALLOWED_BASH_CMDS: Set[str] = set(_cfg_list("allowed_bash_commands", "MCP_ALLOWED_BASH_COMMANDS",
                                            _DEFAULT_BASH_CMDS))
if ENABLE_DANGEROUS and _cfg_val("allow_destructive_bash", "MCP_ALLOW_DESTRUCTIVE_BASH", False, bool):
    ALLOWED_BASH_CMDS |= set(_DANGEROUS_BASH_CMDS)

# Hosts that must never be reached even when private networks are allowed.
BLOCKED_HOSTS: Set[str] = {h.lower() for h in _cfg_list(
    "blocked_hosts", "MCP_BLOCKED_HOSTS",
    ["metadata.google.internal", "metadata.goog", "instance-data", "169.254.169.254"])}

# Environment variables that can never be read back or overwritten.
_SECRET_HINTS = ("token", "key", "secret", "auth", "pwd", "passwd", "password", "credential",
                 "session", "cookie", "private", "proxy", "database", "dsn", "connection_string")
_PROTECTED_ENV = {"PATH", "LD_PRELOAD", "LD_LIBRARY_PATH", "LD_AUDIT", "DYLD_INSERT_LIBRARIES",
                  "DYLD_LIBRARY_PATH", "PYTHONPATH", "PYTHONSTARTUP", "PYTHONHOME",
                  "PYTHONEXECUTABLE", "PYTHONINSPECT", "PYTHONWARNINGS", "PYTHONUSERBASE",
                  "BASH_ENV", "ENV", "IFS", "SHELL", "HOME", "TMPDIR", "NODE_OPTIONS",
                  "PERL5LIB", "PERL5OPT", "RUBYOPT", "GIT_SSH", "GIT_SSH_COMMAND",
                  "GIT_EXTERNAL_DIFF", "GIT_PAGER", "GIT_CONFIG", "GIT_CONFIG_GLOBAL",
                  "GIT_CONFIG_SYSTEM", "GIT_CONFIG_COUNT", "GIT_INDEX_FILE", "GIT_DIR",
                  "GIT_WORK_TREE", "PAGER", "LESSOPEN", "LESSCLOSE", "HTTP_PROXY",
                  "HTTPS_PROXY", "ALL_PROXY", "NO_PROXY", "SSL_CERT_FILE", "SSL_CERT_DIR",
                  "SSLKEYLOGFILE", "REQUESTS_CA_BUNDLE", "CURL_CA_BUNDLE"}
_PROTECTED_ENV_PREFIXES = ("GIT_", "LD_", "DYLD_", "PYTHON", "NODE_", "BASH_", "PERL",
                           "RUBY", "HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "NO_PROXY",
                           "SSL_", "REQUESTS_", "CURL_")

# ---------------------------------------------------------------------------
# Logging Setup - Structured, Color-Aware
# ---------------------------------------------------------------------------
_TOKEN_IN_URL_RE = re.compile(r"((?:token|api_key|apikey|access_token|key)=)([^&\s\"']+)", re.I)
_BEARER_RE = re.compile(r"(Bearer\s+)([A-Za-z0-9._\-]+)", re.I)


def _redact(text: str) -> str:
    """Strip obvious secrets out of anything that reaches the logs."""
    out = _TOKEN_IN_URL_RE.sub(r"\1<redacted>", str(text))
    return _BEARER_RE.sub(r"\1<redacted>", out)


class _ColourFormatter(logging.Formatter):
    def format(self, record):
        msg = _redact(super().format(record))
        if record.levelno == logging.INFO:
            return f"{GREEN}{msg}{RESET}"
        if record.levelno == logging.WARNING:
            return f"{YELLOW}{msg}{RESET}"
        if record.levelno >= logging.ERROR:
            return f"{RED}{msg}{RESET}"
        return msg


log = logging.getLogger("mcp")
log.setLevel(getattr(logging, LOG_LEVEL.upper(), logging.INFO))
for _h in list(log.handlers):
    log.removeHandler(_h)
_handler = logging.StreamHandler()
_handler.setFormatter(_ColourFormatter("%(asctime)s %(levelname)s %(message)s", "%H:%M:%S"))
log.addHandler(_handler)
log.propagate = False

if _config_error:
    log.warning("Ignoring unreadable config %s (%s)", CONFIG_PATH, _config_error)

# ---------------------------------------------------------------------------
# Telemetry & Server Diagnostics State Tracking
# ---------------------------------------------------------------------------
_server_stats_lock = threading.Lock()
_SERVER_STATS: Dict[str, Any] = {
    "start_time": time.time(),
    "total_requests": 0,
    "successful_calls": 0,
    "failed_calls": 0,
    "rejected_auth": 0,
    "bytes_downloaded": 0,
}

_tool_stats: Dict[str, Dict[str, Any]] = defaultdict(lambda: {
    "calls": 0,
    "success": 0,
    "failed": 0,
    "total_time": 0.0,
    "avg_time": 0.0,
    "last_error": None,
    "last_called": None,
})


def _record_tool(name: str, success: bool, duration: float, error: Optional[str] = None) -> None:
    with _server_stats_lock:
        stats = _tool_stats[name]
        stats["calls"] += 1
        if success:
            stats["success"] += 1
        else:
            stats["failed"] += 1
            if error:
                stats["last_error"] = str(error)[:500]
        stats["total_time"] += max(0.0, float(duration))
        stats["avg_time"] = round(stats["total_time"] / max(1, stats["calls"]), 4)
        stats["last_called"] = time.time()


def _bump(counter: str, amount: int = 1) -> None:
    with _server_stats_lock:
        _SERVER_STATS[counter] = _SERVER_STATS.get(counter, 0) + amount


# ---------------------------------------------------------------------------
# In-Memory Cache with Expiration (TTL) + bounded size
# ---------------------------------------------------------------------------
_cache_lock = threading.Lock()
_response_cache: "Dict[str, Tuple[float, Any]]" = {}
_cache_sizes: Dict[str, int] = {}
_cache_size_bytes = 0
CACHE_MAX_BYTES = 32 * 1024 * 1024
CACHE_MAX_ENTRY_BYTES = 1024 * 1024
_cache_counters = {"hits": 0, "misses": 0, "evictions": 0, "sets": 0}


def _cache_key(*parts: Any) -> str:
    raw = "\x1f".join(repr(p) for p in parts)
    return hashlib.sha256(raw.encode("utf-8", "replace")).hexdigest()


def _cache_get(key: str) -> Optional[Any]:
    with _cache_lock:
        entry = _response_cache.get(key)
        if entry is not None:
            expires, val = entry
            if time.time() < expires:
                _cache_counters["hits"] += 1
                return val
            _response_cache.pop(key, None)
            global _cache_size_bytes
            _cache_size_bytes -= _cache_sizes.pop(key, 0)
        _cache_counters["misses"] += 1
    return None


def _cache_set(key: str, val: Any, ttl_seconds: float = 60.0) -> None:
    global _cache_size_bytes
    try:
        ttl = float(ttl_seconds)
        if not math.isfinite(ttl) or ttl <= 0:
            return
        ttl = min(ttl, 24 * 60 * 60)
        if isinstance(val, str):
            value_size = len(val.encode("utf-8"))
        elif isinstance(val, bytes):
            value_size = len(val)
        else:
            value_size = len(json.dumps(val, ensure_ascii=False, default=_json_default).encode("utf-8"))
    except (TypeError, ValueError, OverflowError, UnicodeError):
        return
    if value_size > CACHE_MAX_ENTRY_BYTES:
        return
    max_entries = _bounded_int(CACHE_MAX_ENTRIES, 512, 16, 10000)
    with _cache_lock:
        old_size = _cache_sizes.pop(key, 0)
        _response_cache.pop(key, None)
        _cache_size_bytes -= old_size
        while _response_cache and (len(_response_cache) >= max_entries
                                   or _cache_size_bytes + value_size > CACHE_MAX_BYTES):
            stale_key = min(_response_cache, key=lambda cache_key: _response_cache[cache_key][0])
            _response_cache.pop(stale_key, None)
            _cache_size_bytes -= _cache_sizes.pop(stale_key, 0)
            _cache_counters["evictions"] += 1
        _response_cache[key] = (time.time() + ttl, val)
        _cache_sizes[key] = value_size
        _cache_size_bytes += value_size
        _cache_counters["sets"] += 1


def _cache_clear() -> int:
    global _cache_size_bytes
    with _cache_lock:
        count = len(_response_cache)
        _response_cache.clear()
        _cache_sizes.clear()
        _cache_size_bytes = 0
    return count


# ---------------------------------------------------------------------------
# JSON Serialisation Helpers
# ---------------------------------------------------------------------------
def _json_default(obj: Any) -> str:
    if isinstance(obj, (set, frozenset)):
        return sorted(str(x) for x in obj)  # type: ignore[return-value]
    if isinstance(obj, (_dt.datetime, _dt.date)):
        return obj.isoformat()
    if isinstance(obj, bytes):
        return base64.b64encode(obj).decode("ascii")
    return str(obj)


def _json_result(payload: Any) -> str:
    return json.dumps(payload, ensure_ascii=False, indent=2, default=_json_default)


def _error(message: str, **extra: Any) -> str:
    payload = {"error": str(message), "ok": False}
    payload.update(extra)
    return _json_result(payload)


def _ok(payload: Optional[Dict[str, Any]] = None, **extra: Any) -> str:
    data: Dict[str, Any] = {"ok": True}
    if payload:
        data.update(payload)
    data.update(extra)
    return _json_result(data)


def _bounded_int(value: Any, default: int, minimum: int, maximum: int) -> int:
    """Convert an integer-like value and clamp it, falling back on invalid input."""
    try:
        if isinstance(value, str):
            value = value.strip()
            if not value:
                return default
        if isinstance(value, float) and not math.isfinite(value):
            return default
        number = int(float(value))
        return max(minimum, min(number, maximum))
    except (TypeError, ValueError, OverflowError):
        return default


# Clamp operator-configurable values once, so every tool and HTTP path shares
# conservative resource ceilings even when the local config is malformed.
MCP_MAX_SEARCH_RESULTS = _bounded_int(MCP_MAX_SEARCH_RESULTS, 50, 1, 500)
MCP_MAX_REQUEST_BYTES = _bounded_int(MCP_MAX_REQUEST_BYTES, 2 * 1024 * 1024,
                                     1024, 32 * 1024 * 1024)
MCP_MAX_RETRY_ATTEMPTS = _bounded_int(MCP_MAX_RETRY_ATTEMPTS, 3, 1, 10)
MCP_MAX_LINKS = _bounded_int(MCP_MAX_LINKS, 500, 1, 2000)
MCP_MAX_FEED_ITEMS = _bounded_int(MCP_MAX_FEED_ITEMS, 100, 1, 1000)
MAX_HTTP_BYTES = _bounded_int(MAX_HTTP_BYTES, 2 * 1024 * 1024,
                              1024, 64 * 1024 * 1024)
MAX_HTML_BYTES = min(MAX_HTTP_BYTES, 4 * 1024 * 1024)
MAX_DOWNLOAD_BYTES = _bounded_int(MAX_DOWNLOAD_BYTES, 64 * 1024 * 1024,
                                  1024, 1024 * 1024 * 1024)
MAX_ARCHIVE_ENTRIES = _bounded_int(MAX_ARCHIVE_ENTRIES, 5000, 1, 10000)
MAX_ARCHIVE_UNPACKED_BYTES = _bounded_int(MAX_ARCHIVE_UNPACKED_BYTES, 256 * 1024 * 1024,
                                          1024, 1024 * 1024 * 1024)
MAX_TEXT_CHARS = _bounded_int(MAX_TEXT_CHARS, 64000, 1024, 1_000_000)
MAX_MEMORY_BYTES = _bounded_int(MAX_MEMORY_BYTES, 8 * 1024 * 1024, 1024, 64 * 1024 * 1024)
CACHE_MAX_ENTRIES = _bounded_int(CACHE_MAX_ENTRIES, 512, 16, 10000)
SEARCH_PARALLELISM = _bounded_int(SEARCH_PARALLELISM, 6, 1, 20)
SEARCH_CACHE_TTL = _bounded_int(SEARCH_CACHE_TTL, 300, 0, 86400)
FETCH_CACHE_TTL = _bounded_int(FETCH_CACHE_TTL, 120, 0, 86400)
DEFAULT_SEARCH_ENGINES = str(DEFAULT_SEARCH_ENGINES)[:200]
try:
    RETRY_BACKOFF = float(RETRY_BACKOFF)
    if not math.isfinite(RETRY_BACKOFF):
        raise ValueError
    RETRY_BACKOFF = max(0.0, min(RETRY_BACKOFF, 10.0))
except (TypeError, ValueError, OverflowError):
    RETRY_BACKOFF = 0.5


def _clean_text(value: Any, limit: int = 0) -> str:
    text = re.sub(r"\s+", " ", html_module.unescape(str(value or ""))).strip()
    return text[:limit] if limit else text


def _strip_tags(value: Any) -> str:
    return _clean_text(re.sub(r"<[^>]+>", " ", str(value or "")))


# ---------------------------------------------------------------------------
# Path Safety Verification
# ---------------------------------------------------------------------------
def _safe_path(filepath: str, must_exist: bool = False) -> str:
    """Resolve *filepath* inside SANDBOX_ROOT and reject path traversal attempts."""
    if not isinstance(filepath, str) or not filepath.strip():
        raise ValueError("filepath must be a non-empty string")
    if "\x00" in filepath:
        raise ValueError("filepath contains a null byte")
    root = os.path.realpath(SANDBOX_ROOT)
    expanded = os.path.expanduser(filepath)
    if not os.path.isabs(expanded):
        expanded = os.path.join(root, expanded)
    candidate = os.path.realpath(os.path.abspath(expanded))
    try:
        if os.path.commonpath((root, candidate)) != root:
            raise ValueError
    except ValueError:
        raise ValueError(f"Access denied: path '{filepath}' is outside sandbox root '{SANDBOX_ROOT}'")
    if must_exist and not os.path.exists(candidate):
        raise FileNotFoundError(f"Path '{filepath}' does not exist")
    return candidate


def _ensure_parent(path: str) -> None:
    parent = os.path.dirname(path)
    if parent:
        os.makedirs(parent, exist_ok=True)


def _atomic_write(path: str, data: str, encoding: str = "utf-8") -> int:
    """Write via a temp file + os.replace so readers never see a half-written file."""
    _ensure_parent(path)
    directory = os.path.dirname(path) or "."
    fd, tmp = tempfile.mkstemp(prefix=".mts-", dir=directory)
    try:
        with os.fdopen(fd, "w", encoding=encoding) as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp, path)
    except Exception:
        with contextlib.suppress(OSError):
            os.unlink(tmp)
        raise
    return len(data.encode(encoding, "replace"))


# ---------------------------------------------------------------------------
# URL Safety Helpers (SSRF defence)
# ---------------------------------------------------------------------------
def _validated_http_url(url: str) -> str:
    """Validate that *url* is a well-formed http/https URL without credentials."""
    if not isinstance(url, str) or not url.strip():
        raise ValueError("URL must be a non-empty string")
    parsed = urllib.parse.urlsplit(url.strip())
    if parsed.scheme.lower() not in ("http", "https") or not parsed.hostname:
        raise ValueError("Only valid http:// or https:// URLs are allowed")
    if parsed.username or parsed.password:
        raise ValueError("URLs containing embedded credentials are not allowed")
    try:
        port = parsed.port
    except ValueError:
        raise ValueError("Invalid URL port")
    if port is not None and not (1 <= port <= 65535):
        raise ValueError("Invalid URL port")
    return urllib.parse.urlunsplit(parsed)


def _ip_is_blocked(ip: "ipaddress._BaseAddress") -> bool:
    if isinstance(ip, ipaddress.IPv6Address):
        if ip.ipv4_mapped is not None:
            ip = ip.ipv4_mapped
        elif getattr(ip, "sixtofour", None):
            ip = ip.sixtofour
        elif getattr(ip, "teredo", None):
            ip = ip.teredo[1]
    return bool(ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved
                or ip.is_multicast or ip.is_unspecified)


def _resolve_host(host: str) -> List[str]:
    infos = socket.getaddrinfo(host, None, type=socket.SOCK_STREAM)
    return sorted({info[4][0] for info in infos})


def _host_is_private(host: str) -> bool:
    """Detect private/loopback/link-local/reserved/multicast/metadata hosts.

    Fails *closed*: an unresolvable host is treated as unsafe (v3.8 returned
    False here, which let DNS failures bypass the guard).
    """
    host = (host or "").strip().strip("[]").lower().rstrip(".")
    if not host:
        return True
    if host in BLOCKED_HOSTS or host in {"localhost", "localhost.localdomain"}:
        return True
    if host.endswith(".local") or host.endswith(".internal") or host.endswith(".localhost"):
        return True
    with contextlib.suppress(ValueError):
        return _ip_is_blocked(ipaddress.ip_address(host))
    try:
        addresses = _resolve_host(host)
    except (OSError, ValueError):
        return True
    if not addresses:
        return True
    for address in addresses:
        with contextlib.suppress(ValueError):
            if _ip_is_blocked(ipaddress.ip_address(address)):
                return True
    return False


def _assert_safe_remote(url: str) -> str:
    """Run security checks on a remote URL prior to establishing a request."""
    normalized = _validated_http_url(url)
    host = (urllib.parse.urlsplit(normalized).hostname or "").lower().rstrip(".")
    if host in BLOCKED_HOSTS:
        raise ValueError(f"Destination host '{host}' is blocked by policy")
    if not ALLOW_PRIVATE_NETWORKS and _host_is_private(host):
        raise ValueError("Private, loopback, link-local, reserved, multicast, metadata or "
                         "unresolvable destinations are blocked")
    return normalized


def _resolved_connection_addresses(host: str, port: int,
                                  allow_private: Optional[bool] = None):
    """Resolve once per socket and validate the exact addresses we will connect to."""
    normalized = str(host or "").strip().strip("[]").lower().rstrip(".")
    if normalized in BLOCKED_HOSTS:
        raise ValueError(f"Destination host '{normalized}' is blocked by policy")
    addresses = socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)
    if not addresses:
        raise OSError(f"No addresses found for {host}")
    if allow_private is None:
        allow_private = ALLOW_PRIVATE_NETWORKS
    if not allow_private:
        for _family, _socktype, _proto, _canonname, sockaddr in addresses:
            try:
                address = ipaddress.ip_address(str(sockaddr[0]).split("%", 1)[0])
            except (ValueError, IndexError):
                continue
            if _ip_is_blocked(address):
                raise ValueError("Connection blocked: DNS resolved to a private or reserved address")
    return addresses


def _create_pinned_socket(host: str, port: int, timeout, source_address=None,
                          allow_private: Optional[bool] = None):
    """Connect to a validated getaddrinfo result without a second DNS lookup."""
    addresses = _resolved_connection_addresses(host, port, allow_private=allow_private)
    last_error = None
    for family, socktype, proto, _canonname, sockaddr in addresses:
        sock = None
        try:
            sock = socket.socket(family, socktype, proto)
            if timeout is not socket._GLOBAL_DEFAULT_TIMEOUT:
                sock.settimeout(timeout)
            if source_address:
                sock.bind(source_address)
            sock.connect(sockaddr)
            return sock
        except OSError as exc:
            last_error = exc
            if sock is not None:
                with contextlib.suppress(OSError):
                    sock.close()
    if last_error is not None:
        raise last_error
    raise OSError(f"Could not connect to {host}:{port}")


class _PinnedConnectionMixin:
    def _connect_pinned(self):
        self.sock = _create_pinned_socket(self.host, self.port, self.timeout, self.source_address)


class _SafeHTTPConnection(_PinnedConnectionMixin, http.client.HTTPConnection):
    def connect(self):
        self._connect_pinned()
        if self._tunnel_host:
            self._tunnel()


class _SafeHTTPSConnection(_PinnedConnectionMixin, http.client.HTTPSConnection):
    def connect(self):
        self._connect_pinned()
        if self._tunnel_host:
            self._tunnel()
        server_hostname = self._tunnel_host or self.host
        ctx = getattr(self, "_context", None)
        if ctx is not None:
            self.sock = ctx.wrap_socket(self.sock, server_hostname=server_hostname)


class _SafeHTTPHandler(urllib.request.HTTPHandler):
    def http_open(self, req):
        return self.do_open(_SafeHTTPConnection, req)


class _SafeHTTPSHandler(urllib.request.HTTPSHandler):
    def https_open(self, req):
        return self.do_open(_SafeHTTPSConnection, req)





class _SafeRedirectHandler(urllib.request.HTTPRedirectHandler):
    """Validate *every* redirect hop instead of only the first/last URL."""

    max_redirections = 6

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        redirects = getattr(req, "redirect_dict", {})
        if len(redirects) >= self.max_redirections:
            raise urllib.error.HTTPError(req.full_url, code,
                                          f"Redirect limit ({self.max_redirections}) exceeded",
                                          headers, fp)
        try:
            _assert_safe_remote(newurl)
        except ValueError as exc:
            raise urllib.error.HTTPError(newurl, code, f"Blocked redirect: {exc}", headers, fp)
        new_request = super().redirect_request(req, fp, code, msg, headers, newurl)
        if new_request is not None:
            # never leak credentials/cookies across hosts
            for header in ("Authorization", "Cookie", "Proxy-Authorization"):
                new_request.headers.pop(header, None)
                new_request.unredirected_hdrs.pop(header, None)
        return new_request


_opener_lock = threading.Lock()
_shared_opener: Optional[urllib.request.OpenerDirector] = None


def _build_opener(max_redirects: int = 6) -> urllib.request.OpenerDirector:
    handler = _SafeRedirectHandler()
    handler.max_redirections = max(0, int(max_redirects))
    # Bypass proxy env vars so the pinned connection is the actual destination.
    # Every HTTP/TLS socket validates and connects to the same resolved address.
    return urllib.request.build_opener(urllib.request.ProxyHandler({}), handler,
                                       _SafeHTTPHandler(), _SafeHTTPSHandler())


def _get_opener() -> urllib.request.OpenerDirector:
    global _shared_opener
    with _opener_lock:
        if _shared_opener is None:
            _shared_opener = _build_opener()
        return _shared_opener


# ---------------------------------------------------------------------------
# Retry Decorator with Exponential Back-off
# ---------------------------------------------------------------------------
def _retry_on_exception(max_attempts: int = MCP_MAX_RETRY_ATTEMPTS,
                        backoff: float = RETRY_BACKOFF,
                        allowed_exceptions: Tuple[Type[BaseException], ...] = (
                            urllib.error.URLError, socket.timeout, TimeoutError,
                            subprocess.TimeoutExpired, ConnectionError)):
    def decorator(func: Callable) -> Callable:
        def wrapper(*args, **kwargs):
            attempt = 0
            while True:
                try:
                    return func(*args, **kwargs)
                except urllib.error.HTTPError:
                    raise  # a real HTTP status is an answer, not a transport failure
                except allowed_exceptions as exc:
                    attempt += 1
                    if attempt >= max(1, max_attempts):
                        raise
                    sleep = backoff * (2 ** (attempt - 1)) * (0.75 + random.random() * 0.5)
                    log.warning("Retry %d/%d after %.2fs due to %s", attempt, max_attempts, sleep, exc)
                    time.sleep(sleep)
        wrapper.__name__ = getattr(func, "__name__", "wrapped")
        wrapper.__doc__ = func.__doc__
        return wrapper
    return decorator


# ---------------------------------------------------------------------------
# Temporary File Context Manager
# ---------------------------------------------------------------------------
@contextmanager
def temporary_file(suffix: str = "", delete: bool = True) -> Generator[Any, None, None]:
    # Keep the securely-created descriptor open; closing and reopening the path
    # would introduce a symlink/race window. NamedTemporaryFile exposes a path in
    # ``.name`` for subprocess callers while retaining mkstemp's exclusive create.
    file_obj = tempfile.NamedTemporaryFile(mode="w+", suffix=suffix, delete=False,
                                            encoding="utf-8")
    path = file_obj.name
    try:
        yield file_obj
    finally:
        with contextlib.suppress(Exception):
            file_obj.close()
        if delete:
            with contextlib.suppress(Exception):
                os.unlink(path)


# ---------------------------------------------------------------------------
# Chunked, Size-Limited Stream Readers
# ---------------------------------------------------------------------------
def _read_stream_limited(resp, limit: int = MAX_HTTP_BYTES) -> Tuple[bytes, bool]:
    """Read at most *limit* bytes. Returns ``(data, truncated)``.

    v3.8 could never detect truncation (it only ever read ``limit`` bytes and then
    tested ``total > limit``), so oversized downloads were silently corrupted.
    """
    limit = max(1024, int(limit))
    chunks: List[bytes] = []
    total = 0
    truncated = False
    while total < limit:
        remaining = limit - total
        chunk = resp.read(min(65536, remaining))
        if not chunk:
            break
        if len(chunk) > remaining:
            chunks.append(chunk[:remaining])
            total += remaining
            truncated = True
            break
        chunks.append(chunk)
        total += len(chunk)
    if total >= limit and not truncated:
        # Probe one extra byte to learn whether the body continued.
        with contextlib.suppress(Exception):
            extra = resp.read(1)
            if extra:
                truncated = True
    _bump("bytes_downloaded", total)
    return b"".join(chunks), truncated


def _read_limited_response(resp, limit: int = MAX_HTTP_BYTES, strict: bool = True) -> bytes:
    data, truncated = _read_stream_limited(resp, limit)
    if truncated and strict:
        raise ValueError(f"Response exceeded configured limit of {max(1024, int(limit))} bytes")
    return data


def _decode_response(data: bytes, content_type: str = "") -> str:
    charset = None
    match = re.search(r"charset\s*=\s*['\"]?([A-Za-z0-9._-]+)", content_type or "", re.I)
    if match:
        charset = match.group(1)
    if not charset:
        head = data[:4096]
        meta = re.search(br"""charset=["']?([A-Za-z0-9._-]+)""", head, re.I)
        if meta:
            charset = meta.group(1).decode("ascii", "ignore")
    try:
        return data.decode(charset or "utf-8", errors="replace")
    except (LookupError, TypeError):
        return data.decode("utf-8", errors="replace")


# ---------------------------------------------------------------------------
# Robust HTTP Fetching Engine
# ---------------------------------------------------------------------------
def _http_fetch(url: str,
                method: str = "GET",
                headers: Optional[Dict[str, str]] = None,
                data: Optional[bytes] = None,
                timeout: Optional[int] = None,
                max_bytes: Optional[int] = None,
                strict_size: bool = False,
                max_redirects: int = 6) -> Dict[str, Any]:
    """Single low-level fetch used by every network tool.

    Validates the target (and every redirect hop), caps the body and reports
    truncation explicitly.
    """
    target = _assert_safe_remote(url)
    timeout = max(2, min(int(timeout or HTTP_TIMEOUT), 300))
    max_bytes = (_bounded_int(max_bytes, MAX_HTTP_BYTES, 1024, MAX_HTTP_BYTES)
                 if max_bytes else MAX_HTTP_BYTES)
    request_headers = {"User-Agent": USER_AGENT, "Accept-Encoding": "identity"}
    for key, value in (headers or {}).items():
        request_headers[str(key)] = str(value)
    req = urllib.request.Request(target, data=data, headers=request_headers,
                                 method=str(method or "GET").upper())
    opener = _get_opener() if max_redirects == 6 else _build_opener(max_redirects)
    started = time.time()
    with opener.open(req, timeout=timeout) as resp:
        final_url = _assert_safe_remote(resp.geturl())
        body, truncated = _read_stream_limited(resp, max_bytes)
        if truncated and strict_size:
            raise ValueError(f"Response exceeded configured limit of {max_bytes} bytes")
        content_type = resp.headers.get("Content-Type", "")
        return {
            "status": getattr(resp, "status", None) or resp.getcode(),
            "url": final_url,
            "headers": {k.lower(): v for k, v in resp.headers.items()},
            "content_type": content_type,
            "body": body,
            "text": _decode_response(body, content_type),
            "truncated": truncated,
            "elapsed_ms": round((time.time() - started) * 1000, 2),
        }


@_retry_on_exception()
def _http_fetch_retrying(url: str, **kwargs) -> Dict[str, Any]:
    return _http_fetch(url, **kwargs)


def _fetch_url(url: str, timeout: int = HTTP_TIMEOUT, max_bytes: int = MAX_HTTP_BYTES) -> str:
    """Backwards-compatible text fetch (retries transient failures, SSRF-safe)."""
    try:
        result = _http_fetch_retrying(url, timeout=timeout, max_bytes=max_bytes)
    except urllib.error.HTTPError as exc:
        raise RuntimeError(f"HTTP {exc.code} while fetching {url}") from exc
    except urllib.error.URLError as exc:
        raise RuntimeError(f"Network error while fetching {url}: {exc.reason}") from exc
    return result["text"]


def _safe_parse_xml(text: str) -> ET.Element:
    """Parse bounded XML without DTDs or entity declarations."""
    if len(text) > 4 * 1024 * 1024:
        raise ValueError("XML exceeds the 4 MiB parsing limit")
    if re.search(r"<!\s*(?:DOCTYPE|ENTITY)\b", text, re.I):
        raise ValueError("XML document types and entity declarations are not allowed")
    return ET.fromstring(text)


def _http_get_text(url: str,
                   headers: Optional[Dict[str, str]] = None,
                   timeout: Optional[int] = None,
                   max_bytes: Optional[int] = None,
                   data: Optional[bytes] = None,
                   cache_ttl: float = 0.0) -> str:
    """Convenience helper for scrapers: returns decoded text ('' on HTTP error)."""
    if max_bytes is None:
        max_bytes = min(MAX_HTTP_BYTES, 2 * 1024 * 1024)
    else:
        max_bytes = _bounded_int(max_bytes, min(MAX_HTTP_BYTES, 2 * 1024 * 1024),
                                 1024, MAX_HTTP_BYTES)
    key = ""
    if cache_ttl > 0:
        key = _cache_key("get", url, sorted((headers or {}).items()), data)
        cached = _cache_get(key)
        if cached is not None:
            return cached
    result = _http_fetch(url, method="POST" if data else "GET", headers=headers,
                         data=data, timeout=timeout, max_bytes=max_bytes)
    text = result["text"]
    if key:
        _cache_set(key, text, cache_ttl)
    return text


# ---------------------------------------------------------------------------
# Advanced Rich HTML Parser
# ---------------------------------------------------------------------------
class _RichHTMLParser(HTMLParser):
    SKIP = {"script", "style", "noscript", "template", "svg", "canvas", "nav", "footer", "header", "form"}
    BLOCK = {"p", "div", "article", "section", "main", "li", "blockquote", "pre", "br",
             "h1", "h2", "h3", "h4", "h5", "h6", "tr"}
    HEADINGS = {"h1", "h2", "h3", "h4", "h5", "h6"}

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.parts: List[str] = []
        self.links: List[Dict[str, str]] = []
        self.meta: Dict[str, str] = {}
        self.headings: List[Dict[str, str]] = []
        self.canonical = ""
        self.lang = ""
        self._skip = 0
        self._text_chars = 0
        self._title_depth = 0
        self._title_parts: List[str] = []
        self._title_chars = 0
        self._anchor_depth = 0
        self._anchor: Optional[Dict[str, str]] = None
        self._heading: Optional[str] = None
        self._heading_parts: List[str] = []
        self._heading_chars = 0

    def handle_starttag(self, tag, attrs):
        tag = tag.lower()
        attr_dict = {str(k).lower(): (v or "") for k, v in attrs}
        if tag in self.SKIP:
            self._skip += 1
            return
        if self._skip:
            return
        if tag == "html" and attr_dict.get("lang"):
            self.lang = attr_dict["lang"][:100]
        if tag == "title":
            self._title_depth += 1
        elif tag == "meta":
            key = attr_dict.get("name") or attr_dict.get("property") or attr_dict.get("itemprop")
            if key:
                meta_key = key.lower()[:200]
                if meta_key in self.meta or len(self.meta) < 500:
                    self.meta[meta_key] = attr_dict.get("content", "")[:2000]
        elif tag == "link" and "canonical" in attr_dict.get("rel", "").lower().split():
            self.canonical = attr_dict.get("href", "")[:2048]
        elif tag == "a":
            self._anchor_depth = 1
            self._anchor = {"url": attr_dict.get("href", "")[:2048], "text": "",
                            "rel": attr_dict.get("rel", "")[:200],
                            "title": attr_dict.get("title", "")[:500]}
        elif self._anchor_depth:
            self._anchor_depth += 1
        if tag in self.HEADINGS:
            self._heading = tag if len(self.headings) < 200 else None
            self._heading_parts = []
            self._heading_chars = 0
        if tag in self.BLOCK:
            self._append_text("\n")

    def handle_startendtag(self, tag, attrs):
        self.handle_starttag(tag, attrs)
        if tag.lower() in self.SKIP and self._skip:
            self._skip -= 1

    def handle_endtag(self, tag):
        tag = tag.lower()
        if tag in self.SKIP and self._skip:
            self._skip -= 1
            return
        if self._skip:
            return
        if tag == "title" and self._title_depth:
            self._title_depth -= 1
        if tag in self.HEADINGS and self._heading:
            text = " ".join(self._heading_parts).strip()
            if text and len(self.headings) < 200:
                self.headings.append({"level": self._heading, "text": text[:300]})
            self._heading, self._heading_parts = None, []
            self._heading_chars = 0
        if tag == "a" and self._anchor_depth:
            if self._anchor and self._anchor.get("url") and len(self.links) < MCP_MAX_LINKS:
                self.links.append(self._anchor)
            self._anchor = None
            self._anchor_depth = 0
        elif self._anchor_depth:
            self._anchor_depth = max(0, self._anchor_depth - 1)
        if tag in self.BLOCK:
            self._append_text("\n")

    def _append_text(self, text: str):
        remaining = max(0, MAX_TEXT_CHARS * 2 - self._text_chars)
        if remaining:
            piece = text[:remaining]
            self.parts.append(piece)
            self._text_chars += len(piece)

    def handle_data(self, data):
        if self._skip:
            return
        clean = re.sub(r"\s+", " ", data).strip()
        if not clean:
            return
        self._append_text(clean)
        if self._title_depth and self._title_chars < 2000:
            title_piece = clean[:2000 - self._title_chars]
            self._title_parts.append(title_piece)
            self._title_chars += len(title_piece)
        if self._heading is not None and self._heading_chars < 5000:
            heading_piece = clean[:5000 - self._heading_chars]
            self._heading_parts.append(heading_piece)
            self._heading_chars += len(heading_piece)
        if self._anchor_depth and self._anchor is not None:
            remaining = 500 - len(self._anchor["text"])
            if remaining > 0:
                separator = " " if self._anchor["text"] else ""
                self._anchor["text"] += (separator + clean)[:remaining]

    def text(self) -> str:
        value = " ".join(self.parts)
        if len(value) > MAX_TEXT_CHARS * 2:
            value = value[:MAX_TEXT_CHARS * 2]
        value = re.sub(r"[ \t]+", " ", value)
        value = re.sub(r"\n\s*", "\n", value)
        return value.strip()


def _extract_html_details(html: str, base_url: str = "") -> dict:
    parser = _RichHTMLParser()
    source = str(html or "")
    input_truncated = len(source) > MAX_HTML_BYTES
    if input_truncated:
        source = source[:MAX_HTML_BYTES]
    try:
        parser.feed(source)
        parser.close()
    except Exception as exc:  # malformed markup should degrade, not explode
        log.debug("HTML parse warning: %s", exc)
    title = " ".join(parser._title_parts).strip()
    canonical = ""
    if parser.canonical:
        with contextlib.suppress(ValueError):
            canonical = _validated_http_url(urllib.parse.urljoin(base_url, parser.canonical))
    links, seen = [], set()
    for link in parser.links:
        try:
            absolute = _validated_http_url(urllib.parse.urljoin(base_url, link["url"]))
        except ValueError:
            continue
        if absolute in seen:
            continue
        seen.add(absolute)
        links.append({"text": _clean_text(link["text"], 500), "url": absolute,
                      "rel": link.get("rel", "")})
        if len(links) >= MCP_MAX_LINKS:
            break
    image = ""
    if parser.meta.get("og:image"):
        with contextlib.suppress(ValueError):
            image = _validated_http_url(urllib.parse.urljoin(base_url, parser.meta["og:image"]))
    return {
        "title": title or parser.meta.get("og:title", ""),
        "text": parser.text(),
        "description": parser.meta.get("description") or parser.meta.get("og:description") or "",
        "image": image,
        "canonical_url": canonical,
        "language": parser.lang,
        "site_name": parser.meta.get("og:site_name", ""),
        "headings": parser.headings[:200],
        "meta_tags": parser.meta,
        "links": links,
        "input_truncated": input_truncated,
    }


def _html_to_markdown(html: str, base_url: str = "") -> str:
    """Convert bounded HTML to Markdown without regex-driven tag matching."""
    source = str(html or "")[:MAX_HTML_BYTES]
    max_output = max(1024, min(MAX_TEXT_CHARS * 4, 4 * 1024 * 1024))

    class MarkdownParser(HTMLParser):
        SKIP = {"script", "style", "noscript", "template", "svg", "canvas"}
        BLOCK = {"p", "div", "article", "section", "main", "header", "footer", "nav",
                 "li", "blockquote", "pre", "br", "hr", "table", "tr", "ul", "ol",
                 "h1", "h2", "h3", "h4", "h5", "h6"}

        def __init__(self):
            super().__init__(convert_charrefs=True)
            self.parts: List[str] = []
            self.output_chars = 0
            self.truncated = False
            self.skip_depth = 0
            self.anchors: List[str] = []
            self.anchor_overflow = 0
            self.pre_depth = 0
            self.pre_parts: List[str] = []
            self.pre_chars = 0
            self.code_blocks: List[str] = []
            self.marker = "MTS_CODE_" + uuid.uuid4().hex + "_"

        def append(self, value: str):
            if not value:
                return
            remaining = max_output - self.output_chars
            if remaining > 0:
                piece = value[:remaining]
                self.parts.append(piece)
                self.output_chars += len(piece)
            if len(value) > max(0, remaining):
                self.truncated = True

        def _safe_url(self, value: str) -> str:
            try:
                candidate = urllib.parse.urljoin(base_url, html_module.unescape(value))
                return _validated_http_url(candidate)
            except (TypeError, ValueError):
                return ""

        def handle_starttag(self, tag, attrs):
            tag = tag.lower()
            attributes = {str(key).lower(): (value or "") for key, value in attrs}
            if tag in self.SKIP:
                self.skip_depth += 1
                return
            if self.skip_depth:
                return
            if tag == "pre":
                if self.pre_depth == 0:
                    self.pre_parts = []
                    self.pre_chars = 0
                self.pre_depth += 1
                return
            if self.pre_depth:
                return
            if tag in self.BLOCK:
                self.append("\n\n")
            if tag.startswith("h") and len(tag) == 2 and tag[1] in "123456":
                self.append("#" * int(tag[1]) + " ")
            elif tag == "a":
                href = self._safe_url(attributes.get("href", ""))
                if self.anchor_overflow == 0 and len(self.anchors) < 1000:
                    self.anchors.append(href)
                    if href:
                        self.append("[")
                else:
                    self.anchor_overflow += 1
                    self.truncated = True
            elif tag == "img":
                url = self._safe_url(attributes.get("src", ""))
                if url:
                    alt = html_module.unescape(attributes.get("alt", ""))[:500]
                    self.append(f"![{alt}]({url})")
            elif tag in ("strong", "b"):
                self.append("**")
            elif tag in ("em", "i"):
                self.append("*")
            elif tag == "code":
                self.append("`")
            elif tag == "li":
                self.append("- ")
            elif tag == "blockquote":
                self.append("> ")
            elif tag in ("td", "th"):
                self.append(" | ")
            elif tag == "br":
                self.append("\n")
            elif tag == "hr":
                self.append("\n---\n")

        def handle_endtag(self, tag):
            tag = tag.lower()
            if tag in self.SKIP and self.skip_depth:
                self.skip_depth -= 1
                return
            if self.skip_depth:
                return
            if tag == "pre" and self.pre_depth:
                self.pre_depth -= 1
                if self.pre_depth == 0:
                    code = "".join(self.pre_parts).strip("\n")
                    if len(code) > max_output:
                        code = code[:max_output]
                        self.truncated = True
                    if len(self.code_blocks) < 1000:
                        marker = f"{self.marker}{len(self.code_blocks)}__"
                        self.code_blocks.append("\n\n```\n" + code + "\n```\n\n")
                        self.append(marker)
                    else:
                        self.truncated = True
                    self.pre_parts = []
                    self.pre_chars = 0
                return
            if self.pre_depth:
                return
            if tag == "a" and self.anchor_overflow:
                self.anchor_overflow -= 1
            elif tag == "a" and self.anchors:
                href = self.anchors.pop()
                if href:
                    self.append(f"]({href})")
            elif tag in ("strong", "b"):
                self.append("**")
            elif tag in ("em", "i"):
                self.append("*")
            elif tag == "code":
                self.append("`")
            if tag in self.BLOCK:
                self.append("\n\n")

        def handle_data(self, data):
            if self.skip_depth:
                return
            if self.pre_depth:
                remaining = max(0, max_output - self.pre_chars)
                if remaining:
                    piece = data[:remaining]
                    self.pre_parts.append(piece)
                    self.pre_chars += len(piece)
                if len(data) > remaining:
                    self.truncated = True
                return
            self.append(re.sub(r"\s+", " ", data))

    try:
        parser = MarkdownParser()
        parser.feed(source)
        parser.close()
        markdown = "".join(parser.parts)
        markdown = re.sub(r"[ \t\r\f\v]+", " ", markdown)
        markdown = re.sub(r" *\n *", "\n", markdown)
        markdown = re.sub(r"\n{3,}", "\n\n", markdown).strip()
        for index, code in enumerate(parser.code_blocks):
            markdown = markdown.replace(f"{parser.marker}{index}__", code.strip())
        return markdown[:max_output]
    except Exception as exc:
        log.debug("Markdown conversion failed: %s", exc)
        return _extract_html_details(source, base_url).get("text", "")[:max_output]


# ---------------------------------------------------------------------------
# Persistent Memory Store (atomic, size-bounded)
# ---------------------------------------------------------------------------
_memory_lock = threading.RLock()


def _memory_size_limit() -> int:
    return _bounded_int(MAX_MEMORY_BYTES, 8 * 1024 * 1024, 1024, 64 * 1024 * 1024)


def _load_memory() -> dict:
    if not os.path.exists(MEMORY_FILE):
        return {}
    limit = _memory_size_limit()
    if os.path.getsize(MEMORY_FILE) > limit:
        raise ValueError(f"Memory file exceeds the configured {limit} byte limit")
    with open(MEMORY_FILE, "r", encoding="utf-8") as f:
        raw = f.read(limit + 1)
    if len(raw.encode("utf-8")) > limit:
        raise ValueError(f"Memory file exceeds the configured {limit} byte limit")
    data = json.loads(raw)
    return data if isinstance(data, dict) else {}


def _save_memory(data: dict) -> int:
    limit = _memory_size_limit()
    payload = json.dumps(data, indent=2, ensure_ascii=False, default=_json_default)
    if len(payload.encode("utf-8")) > limit:
        raise ValueError(f"Memory store would exceed {limit} bytes")
    return _atomic_write(MEMORY_FILE, payload)


def memory_store(key: str, value: str, tags: str = "") -> str:
    """Persist a value under *key* with optional comma-separated tags."""
    if not key or not isinstance(key, str):
        return _error("Key must be a non-empty string")
    if len(key) > 1024:
        return _error("Key exceeds the 1,024 character limit")
    value = "" if value is None else str(value)
    if len(value.encode("utf-8")) > MCP_MAX_REQUEST_BYTES:
        return _error("Value exceeds configured request size limit")
    tags_text = str(tags or "")
    if len(tags_text) > 4096:
        return _error("Tags exceed the 4,096 character limit")
    try:
        with _memory_lock:
            mem = _load_memory()
            mem[key] = {
                "value": value,
                "tags": [t.strip()[:200] for t in tags_text.split(",") if t.strip()][:50],
                "updated": time.time(),
                "updated_iso": _dt.datetime.now(_dt.timezone.utc).isoformat(),
            }
            size = _save_memory(mem)
        return _ok({"status": "stored", "key": key, "entries": len(mem), "store_bytes": size})
    except Exception as exc:
        return _error(f"memory_store failed: {exc}")


def memory_recall(key: str = "", tag: str = "") -> str:
    """Recall one memory entry by key, or a bounded set of entries carrying a tag."""
    try:
        key, tag = str(key or ""), str(tag or "")
        if len(key) > 1024 or len(tag) > 200:
            return _error("key exceeds 1,024 characters or tag exceeds 200 characters")
        with _memory_lock:
            mem = _load_memory()
            if key:
                item = mem.get(key)
                if not item:
                    return _error(f"Key '{key}' not found", key=key)
                return _ok({"key": key, **item})
            ordered = sorted(mem.items(), key=lambda kv: kv[1].get("updated", 0), reverse=True)
            if tag:
                ordered = [(k, v) for k, v in ordered if tag in v.get("tags", [])]
            total = len(ordered)
            bounded = dict(ordered[:500])
            return _ok({"tag": tag, "count": total, "truncated": total > len(bounded),
                        "items": bounded})
    except Exception as exc:
        return _error(str(exc))


def memory_list() -> str:
    """List up to 500 stored memory keys with their tags and update times."""
    try:
        with _memory_lock:
            mem = _load_memory()
            ordered = sorted(mem.items(), key=lambda kv: kv[1].get("updated", 0), reverse=True)
            summary = [{"key": k, "tags": v.get("tags", []), "updated": v.get("updated"),
                        "value_preview": _clean_text(v.get("value", ""), 120)}
                       for k, v in ordered[:500]]
            return _ok({"count": len(ordered), "returned": len(summary),
                        "truncated": len(ordered) > len(summary), "keys": summary})
    except Exception as exc:
        return _error(str(exc))


def memory_delete(key: str = "", tag: str = "", confirm_all: bool = False) -> str:
    """Delete a memory entry by key, every entry with a tag, or the whole store."""
    try:
        with _memory_lock:
            mem = _load_memory()
            removed: List[str] = []
            if key:
                if key not in mem:
                    return _error(f"Key '{key}' not found")
                mem.pop(key)
                removed = [key]
            elif tag:
                removed = [k for k, v in mem.items() if tag in v.get("tags", [])]
                for k in removed:
                    mem.pop(k, None)
            elif _to_bool(confirm_all):
                removed = list(mem.keys())
                mem = {}
            else:
                return _error("Provide key=, tag=, or confirm_all=true")
            _save_memory(mem)
        return _ok({"deleted": removed[:500], "count": len(removed),
                    "deleted_truncated": len(removed) > 500, "remaining": len(mem)})
    except Exception as exc:
        return _error(f"memory_delete failed: {exc}")


def memory_search(query: str, search_values: bool = True, max_results: int = 25) -> str:
    """Substring search across memory keys, tags and (optionally) values."""
    try:
        query = str(query or "").strip()
        if not query:
            return _error("query must be a non-empty string")
        if len(query) > 2000:
            return _error("query exceeds the 2,000 character memory search limit")
        needle = query.lower()
        limit = _bounded_int(max_results, 25, 1, 500)
        search_values = _to_bool(search_values, True)
        hits = []
        with _memory_lock:
            mem = _load_memory()
        for k, v in mem.items():
            haystacks = [k.lower(), " ".join(v.get("tags", [])).lower()]
            if search_values:
                haystacks.append(str(v.get("value", "")).lower())
            if any(needle in h for h in haystacks):
                hits.append({"key": k, "tags": v.get("tags", []), "updated": v.get("updated"),
                             "value_preview": _clean_text(v.get("value", ""), 300)})
            if len(hits) >= limit:
                break
        return _ok({"query": query, "count": len(hits), "matches": hits})
    except Exception as exc:
        return _error(f"memory_search failed: {exc}")


# ---------------------------------------------------------------------------
# Web Search Stack - multi-engine, parallel, rank-fused
# ---------------------------------------------------------------------------
_TRACKING_PARAMS = {"utm_source", "utm_medium", "utm_campaign", "utm_term", "utm_content",
                    "utm_id", "utm_name", "fbclid", "gclid", "gclsrc", "dclid", "msclkid",
                    "mc_cid", "mc_eid", "igshid", "ref", "ref_src", "spm", "_ga", "yclid",
                    "si", "s_kwcid", "vero_id", "wickedid", "oly_enc_id"}

_SEARCH_STOPWORDS = {
    "the", "a", "an", "and", "or", "but", "if", "then", "than", "that", "this", "these", "those",
    "is", "are", "was", "were", "be", "been", "being", "am", "of", "to", "in", "on", "for", "with",
    "as", "by", "at", "from", "it", "its", "into", "about", "over", "after", "before", "between",
    "out", "up", "down", "off", "again", "further", "how", "what", "when", "where", "who", "whom",
    "why", "which", "you", "your", "we", "our", "they", "their", "he", "she", "his", "her", "i",
    "not", "no", "do", "does", "did", "can", "could", "should", "would", "will", "just", "so",
    "there", "here", "have", "has", "had", "more", "most", "some", "such", "only", "own", "same",
    "too", "very", "s", "t", "don", "now", "also", "may", "any", "all", "one", "two", "use", "used",
}

_TIME_RANGE_ALIASES = {
    "": "", "any": "", "all": "", "none": "",
    "d": "day", "day": "day", "24h": "day", "today": "day", "past_day": "day",
    "w": "week", "week": "week", "7d": "week", "past_week": "week",
    "m": "month", "month": "month", "30d": "month", "past_month": "month",
    "y": "year", "year": "year", "365d": "year", "past_year": "year",
}


def _normalize_time_range(value: Any) -> str:
    return _TIME_RANGE_ALIASES.get(str(value or "").strip().lower(), "")


def _normalize_url_for_dedupe(url: str) -> str:
    """Canonical form used for cross-engine de-duplication."""
    try:
        parts = urllib.parse.urlsplit(url)
        host = (parts.hostname or "").lower()
        if host.startswith("www."):
            host = host[4:]
        if host.startswith("m.") and len(host) > 6:
            host = host[2:]
        if host.endswith("."):
            host = host[:-1]
        query = [(k, v) for k, v in urllib.parse.parse_qsl(
            parts.query, keep_blank_values=False, max_num_fields=2000)
                 if k.lower() not in _TRACKING_PARAMS]
        query.sort()
        path = re.sub(r"/+$", "", parts.path) or "/"
        if path.endswith("/index.html"):
            path = path[: -len("index.html")].rstrip("/") or "/"
        default_port = 80 if parts.scheme.lower() == "http" else 443
        port = f":{parts.port}" if parts.port and parts.port != default_port else ""
        return urllib.parse.urlunsplit(("https", host + port, path,
                                        urllib.parse.urlencode(query), ""))
    except Exception:
        return (url or "").strip().rstrip("/").lower()


def _clean_search_url(href: str) -> str:
    """Unwrap redirector links and normalise scheme-relative URLs."""
    href = html_module.unescape(str(href or "")).strip()
    if not href:
        return ""
    if href.startswith("//"):
        href = "https:" + href
    if href.startswith("/"):
        return ""
    def safe(candidate: str) -> str:
        try:
            return _validated_http_url(candidate)
        except ValueError:
            return ""

    try:
        parsed = urllib.parse.urlsplit(href)
        host = (parsed.hostname or "").lower()
        qs = urllib.parse.parse_qs(parsed.query, max_num_fields=2000)
    except ValueError:
        return ""
    # DuckDuckGo / Bing / Google style redirectors
    for param in ("uddg", "u3", "url", "q", "u"):
        if host.endswith("duckduckgo.com") and param == "uddg" and qs.get(param):
            return safe(urllib.parse.unquote(qs[param][0]))
        if host.endswith(("google.com", "bing.com", "yandex.com")) and param in ("url", "q", "u") and qs.get(param):
            candidate = urllib.parse.unquote(qs[param][0])
            if candidate.startswith("http"):
                return safe(candidate)
    # Bing base64 `u=a1...` wrapper
    match = re.search(r"(?:[?&]|&amp;|^)u=a1([A-Za-z0-9_\-]+)", href)
    if match:
        raw = match.group(1).replace("-", "+").replace("_", "/")
        raw += "=" * (-len(raw) % 4)
        with contextlib.suppress(Exception):
            decoded = base64.b64decode(raw).decode("utf-8")
            if decoded.startswith("http"):
                return safe(decoded)
    return safe(href)


def _mk_result(title: Any, url: Any, snippet: Any, engine: str, rank: int,
               extra: Optional[Dict[str, Any]] = None) -> Optional[Dict[str, Any]]:
    url = _clean_search_url(url)
    if not re.match(r"^https?://", url or "", re.I):
        return None
    title = _strip_tags(title)[:500]
    snippet = _strip_tags(snippet)[:2000]
    if not title and not snippet:
        return None
    host = (urllib.parse.urlsplit(url).hostname or "").lower()
    item = {"title": title or host, "url": url, "snippet": snippet, "engine": engine,
            "rank": rank, "domain": host}
    if extra:
        item.update(extra)
    return item


_ANCHOR_RE = re.compile(
    r"<a\b((?:[^\"'>]|\"[^\"]*\"|'[^']*')*)>(.*?)</a\s*>", re.I | re.S)
_ATTR_RE = re.compile(r"""([\w:-]+)\s*=\s*(?:"([^"]*)"|'([^']*)'|([^\s>]+))""")


def _parse_attrs(raw: str) -> Dict[str, str]:
    return {m.group(1).lower(): html_module.unescape(m.group(2) or m.group(3) or m.group(4) or "")
            for m in _ATTR_RE.finditer(raw or "")}


def _html_anchors(html: str) -> List[Dict[str, Any]]:
    """Attribute-order independent anchor extraction (scrapers must not assume order)."""
    anchors = []
    for match in _ANCHOR_RE.finditer(html or ""):
        attrs = _parse_attrs(match.group(1))
        if not attrs.get("href"):
            continue
        anchors.append({"attrs": attrs, "inner": match.group(2), "end": match.end(),
                        "classes": set(attrs.get("class", "").lower().split())})
    return anchors


def _snippet_after(html: str, position: int, class_hint: str, window: int = 2500) -> str:
    chunk = html[position: position + window]
    match = re.search(rf'class="[^"]*{class_hint}[^"]*"[^>]*>(.*?)</(?:a|div|td|p|span)>',
                      chunk, re.S | re.I)
    return match.group(1) if match else ""


# --- individual engines -----------------------------------------------------
def _engine_duckduckgo(query: str, count: int, page: int, safe: bool, time_range: str,
                       region: str) -> List[Dict[str, Any]]:
    params = {
        "q": query,
        "kl": region or "us-en",
        "kp": "1" if safe else "-2",
        "s": str((max(1, page) - 1) * 30),
    }
    if time_range:
        params["df"] = {"day": "d", "week": "w", "month": "m", "year": "y"}[time_range]
    body = _http_get_text(
        "https://html.duckduckgo.com/html/",
        headers={"User-Agent": BROWSER_UA, "Accept-Language": "en-US,en;q=0.9",
                 "Content-Type": "application/x-www-form-urlencoded"},
        data=urllib.parse.urlencode(params).encode("utf-8"),
        timeout=15,
    )
    out: List[Dict[str, Any]] = []
    for anchor in _html_anchors(body):
        if "result__a" not in anchor["classes"]:
            continue
        snippet = _snippet_after(body, anchor["end"], "result__snippet")
        item = _mk_result(anchor["inner"], anchor["attrs"]["href"], snippet,
                          "duckduckgo", len(out) + 1)
        if item:
            out.append(item)
        if len(out) >= count:
            break
    return out


def _engine_duckduckgo_lite(query: str, count: int, page: int, safe: bool, time_range: str,
                            region: str) -> List[Dict[str, Any]]:
    params = {"q": query, "kl": region or "us-en", "kp": "1" if safe else "-2",
              "s": str((max(1, page) - 1) * 30)}
    if time_range:
        params["df"] = {"day": "d", "week": "w", "month": "m", "year": "y"}[time_range]
    body = _http_get_text(
        "https://lite.duckduckgo.com/lite/",
        headers={"User-Agent": BROWSER_UA, "Accept-Language": "en-US,en;q=0.9",
                 "Content-Type": "application/x-www-form-urlencoded"},
        data=urllib.parse.urlencode(params).encode("utf-8"),
        timeout=15,
    )
    out: List[Dict[str, Any]] = []
    for anchor in _html_anchors(body):
        if "result-link" not in anchor["classes"]:
            continue
        snippet = _snippet_after(body, anchor["end"], "result-snippet")
        item = _mk_result(anchor["inner"], anchor["attrs"]["href"], snippet,
                          "duckduckgo_lite", len(out) + 1)
        if item:
            out.append(item)
        if len(out) >= count:
            break
    return out


def _engine_bing(query: str, count: int, page: int, safe: bool, time_range: str,
                 region: str) -> List[Dict[str, Any]]:
    params = {
        "q": query,
        "first": (max(1, page) - 1) * 10 + 1,
        "count": max(10, min(count * 2, 50)),
        "adlt": "strict" if safe else "off",
        "setlang": "en-us",
    }
    if time_range:
        params["filters"] = {"day": 'ex1:"ez1"', "week": 'ex1:"ez2"',
                             "month": 'ex1:"ez3"', "year": 'ex1:"ez5"'}[time_range]
    body = _http_get_text(
        "https://www.bing.com/search?" + urllib.parse.urlencode(params),
        headers={"User-Agent": BROWSER_UA,
                 "Cookie": f"SRCHHPGUSR=ADLT={'STRICT' if safe else 'OFF'}",
                 "Accept-Language": "en-US,en;q=0.9"},
        timeout=15,
    )
    out: List[Dict[str, Any]] = []
    blocks = re.split(r'<li[^>]+class="[^"]*\bb_algo\b[^"]*"[^>]*>', body, flags=re.I)
    for block in blocks[1:]:
        item_html = re.split(r"</li>", block, maxsplit=1, flags=re.I)[0]
        heading = re.search(r"<h2[^>]*>(.*?)</h2>", item_html, re.S | re.I)
        anchors = _html_anchors(heading.group(1) if heading else item_html)
        anchors = [a for a in anchors if a["attrs"]["href"].startswith(("http", "//"))]
        if not anchors:
            continue
        snippet = re.search(r'<p[^>]*>(.*?)</p>', item_html, re.S | re.I)
        item = _mk_result(anchors[0]["inner"], anchors[0]["attrs"]["href"],
                          snippet.group(1) if snippet else "", "bing", len(out) + 1)
        if item:
            out.append(item)
        if len(out) >= count:
            break
    return out


def _engine_mojeek(query: str, count: int, page: int, safe: bool, time_range: str,
                   region: str) -> List[Dict[str, Any]]:
    params = {"q": query, "s": (max(1, page) - 1) * 10, "safe": "1" if safe else "0"}
    if time_range:
        params["since"] = {"day": "d", "week": "w", "month": "m", "year": "y"}[time_range]
    body = _http_get_text("https://www.mojeek.com/search?" + urllib.parse.urlencode(params),
                          headers={"User-Agent": BROWSER_UA,
                                   "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
                                   "Accept-Language": "en-US,en;q=0.9"}, timeout=15)

    out: List[Dict[str, Any]] = []
    for block in re.split(r"<li[^>]*>", body, flags=re.I)[1:]:
        chunk = re.split(r"</li>", block, maxsplit=1, flags=re.I)[0]
        heading = re.search(r"<h2[^>]*>(.*?)</h2>", chunk, re.S | re.I)
        if not heading:
            continue
        anchors = [a for a in _html_anchors(heading.group(1))
                   if a["attrs"]["href"].startswith(("http", "//"))]
        if not anchors:
            continue
        snippet = re.search(r'<p[^>]*class="[^"]*\bs\b[^"]*"[^>]*>(.*?)</p>', chunk, re.S | re.I) \
            or re.search(r"<p[^>]*>(.*?)</p>", chunk, re.S | re.I)
        item = _mk_result(anchors[0]["inner"], anchors[0]["attrs"]["href"],
                          snippet.group(1) if snippet else "", "mojeek", len(out) + 1)
        if item:
            out.append(item)
        if len(out) >= count:
            break
    return out


def _engine_wikipedia(query: str, count: int, page: int, safe: bool, time_range: str,
                      region: str) -> List[Dict[str, Any]]:
    lang = (region or "us-en").split("-")[-1][:2] or "en"
    params = {"action": "query", "list": "search", "srsearch": query, "format": "json",
              "srlimit": min(count, 20), "sroffset": (max(1, page) - 1) * min(count, 20)}
    body = _http_get_text(f"https://{lang}.wikipedia.org/w/api.php?" + urllib.parse.urlencode(params),
                          headers={"User-Agent": USER_AGENT}, timeout=12)
    data = json.loads(body)
    out = []
    for hit in data.get("query", {}).get("search", []):
        title = hit.get("title", "")
        url = f"https://{lang}.wikipedia.org/wiki/{urllib.parse.quote(title.replace(' ', '_'))}"
        item = _mk_result(title, url, hit.get("snippet", ""), "wikipedia", len(out) + 1)
        if item:
            out.append(item)
        if len(out) >= count:
            break
    return out


def _engine_brave_api(query: str, count: int, page: int, safe: bool, time_range: str,
                      region: str) -> List[Dict[str, Any]]:
    if not BRAVE_API_KEY:
        raise RuntimeError("BRAVE_SEARCH_API_KEY not configured")
    params = {"q": query, "count": min(count, 20), "offset": max(0, page - 1),
              "safesearch": "strict" if safe else "off"}
    if time_range:
        params["freshness"] = {"day": "pd", "week": "pw", "month": "pm", "year": "py"}[time_range]
    body = _http_get_text("https://api.search.brave.com/res/v1/web/search?" + urllib.parse.urlencode(params),
                          headers={"X-Subscription-Token": BRAVE_API_KEY,
                                   "Accept": "application/json"}, timeout=15)
    data = json.loads(body)
    out = []
    for hit in data.get("web", {}).get("results", []):
        item = _mk_result(hit.get("title"), hit.get("url"), hit.get("description"), "brave", len(out) + 1,
                          {"published": hit.get("age", "")})
        if item:
            out.append(item)
    return out


def _engine_google_cse(query: str, count: int, page: int, safe: bool, time_range: str,
                       region: str) -> List[Dict[str, Any]]:
    if not (GOOGLE_API_KEY and GOOGLE_CSE_ID):
        raise RuntimeError("GOOGLE_API_KEY / GOOGLE_CSE_ID not configured")
    params = {"key": GOOGLE_API_KEY, "cx": GOOGLE_CSE_ID, "q": query,
              "num": min(count, 10), "start": (max(1, page) - 1) * 10 + 1,
              "safe": "active" if safe else "off"}
    if time_range:
        params["dateRestrict"] = {"day": "d1", "week": "w1", "month": "m1", "year": "y1"}[time_range]
    body = _http_get_text("https://www.googleapis.com/customsearch/v1?" + urllib.parse.urlencode(params),
                          headers={"Accept": "application/json"}, timeout=15)
    data = json.loads(body)
    out = []
    for hit in data.get("items", []):
        item = _mk_result(hit.get("title"), hit.get("link"), hit.get("snippet"), "google_cse", len(out) + 1)
        if item:
            out.append(item)
    return out


def _engine_tavily(query: str, count: int, page: int, safe: bool, time_range: str,
                   region: str) -> List[Dict[str, Any]]:
    if not TAVILY_API_KEY:
        raise RuntimeError("TAVILY_API_KEY not configured")
    payload = json.dumps({"api_key": TAVILY_API_KEY, "query": query,
                          "max_results": min(count, 20), "search_depth": "basic"}).encode("utf-8")
    body = _http_get_text("https://api.tavily.com/search",
                          headers={"Content-Type": "application/json"}, data=payload, timeout=25)
    data = json.loads(body)
    out = []
    for hit in data.get("results", []):
        item = _mk_result(hit.get("title"), hit.get("url"), hit.get("content"), "tavily", len(out) + 1,
                          {"score": hit.get("score")})
        if item:
            out.append(item)
    if data.get("answer"):
        out.insert(0, {"title": "Tavily answer", "url": data.get("results", [{}])[0].get("url", ""),
                       "snippet": str(data["answer"])[:2000], "engine": "tavily", "rank": 0,
                       "domain": "tavily.com"})
    return [r for r in out if r.get("url")]


def _engine_serpapi(query: str, count: int, page: int, safe: bool, time_range: str,
                    region: str) -> List[Dict[str, Any]]:
    if not SERPAPI_KEY:
        raise RuntimeError("SERPAPI_KEY not configured")
    params = {"api_key": SERPAPI_KEY, "q": query, "num": min(count, 20),
              "start": (max(1, page) - 1) * 10, "safe": "active" if safe else "off"}
    if time_range:
        params["tbs"] = {"day": "qdr:d", "week": "qdr:w", "month": "qdr:m", "year": "qdr:y"}[time_range]
    body = _http_get_text("https://serpapi.com/search.json?" + urllib.parse.urlencode(params), timeout=25)
    data = json.loads(body)
    out = []
    for hit in data.get("organic_results", []):
        item = _mk_result(hit.get("title"), hit.get("link"), hit.get("snippet"), "serpapi", len(out) + 1)
        if item:
            out.append(item)
    return out


def _engine_searxng(query: str, count: int, page: int, safe: bool, time_range: str,
                    region: str) -> List[Dict[str, Any]]:
    if not SEARXNG_URL:
        raise RuntimeError("SEARXNG_URL not configured")
    params = {"q": query, "format": "json", "pageno": max(1, page),
              "safesearch": "2" if safe else "0"}
    if time_range:
        params["time_range"] = time_range
    base = SEARXNG_URL.rstrip("/")
    body = _http_get_text(f"{base}/search?" + urllib.parse.urlencode(params),
                          headers={"Accept": "application/json"}, timeout=20)
    data = json.loads(body)
    out = []
    for hit in data.get("results", []):
        item = _mk_result(hit.get("title"), hit.get("url"), hit.get("content"), "searxng", len(out) + 1)
        if item:
            out.append(item)
        if len(out) >= count:
            break
    return out


SEARCH_ENGINES: Dict[str, Callable[..., List[Dict[str, Any]]]] = {
    "duckduckgo": _engine_duckduckgo,
    "duckduckgo_lite": _engine_duckduckgo_lite,
    "bing": _engine_bing,
    "mojeek": _engine_mojeek,
    "wikipedia": _engine_wikipedia,
    "brave": _engine_brave_api,
    "google_cse": _engine_google_cse,
    "tavily": _engine_tavily,
    "serpapi": _engine_serpapi,
    "searxng": _engine_searxng,
}

# Engines that need no credentials, ordered by general result quality.
_FREE_ENGINES = ["duckduckgo", "bing", "duckduckgo_lite", "wikipedia"]

_ENGINE_WEIGHTS = {"tavily": 1.35, "brave": 1.3, "google_cse": 1.3, "serpapi": 1.25,
                   "searxng": 1.15, "bing": 1.1, "duckduckgo": 1.0, "duckduckgo_lite": 0.9,
                   "mojeek": 0.8, "wikipedia": 0.7}


def available_engines() -> List[str]:
    """Engines that are usable with the current configuration."""
    engines = list(_FREE_ENGINES)
    if BRAVE_API_KEY:
        engines.insert(0, "brave")
    if TAVILY_API_KEY:
        engines.insert(0, "tavily")
    if GOOGLE_API_KEY and GOOGLE_CSE_ID:
        engines.insert(0, "google_cse")
    if SERPAPI_KEY:
        engines.append("serpapi")
    if SEARXNG_URL:
        engines.append("searxng")
    return engines


def _select_engines(requested: str) -> List[str]:
    """Resolve an engine selector, honoring the configured default and rejecting typos."""
    raw = str(requested or "").strip().lower()
    if len(raw) > 1000:
        return []
    if raw in ("", "auto", "default"):
        configured = str(DEFAULT_SEARCH_ENGINES or "auto").strip().lower()
        if configured not in ("", "auto", "default"):
            raw = configured
        else:
            return available_engines()[:4]
    if raw == "all":
        return available_engines()
    names = [name for name in re.split(r"[,\s]+", raw) if name]
    if not names or any(name not in SEARCH_ENGINES for name in names):
        return []
    return list(dict.fromkeys(names))


def _run_engines(engines: Sequence[str], query: str, per_engine: int, page: int, safe: bool,
                 time_range: str, region: str, timeout: float = 25.0
                 ) -> Tuple[Dict[str, List[Dict[str, Any]]], Dict[str, str]]:
    """Run selected engines concurrently with a real overall deadline and fault isolation."""
    buckets: Dict[str, List[Dict[str, Any]]] = {}
    errors: Dict[str, str] = {}
    if not engines:
        return buckets, {"engines": "no engines available"}

    valid_engines = []
    for name in engines:
        if name in SEARCH_ENGINES and name not in valid_engines:
            valid_engines.append(name)
        elif name not in SEARCH_ENGINES:
            errors[name] = "unknown search engine"
    if not valid_engines:
        return buckets, errors or {"engines": "no engines available"}

    try:
        timeout_value = float(timeout)
        if not math.isfinite(timeout_value):
            timeout_value = 25.0
    except (TypeError, ValueError, OverflowError):
        timeout_value = 25.0
    timeout_value = max(0.0, min(timeout_value, 300.0))
    workers = max(1, min(len(valid_engines), max(1, SEARCH_PARALLELISM)))
    pool = ThreadPoolExecutor(max_workers=workers)
    futures = {pool.submit(SEARCH_ENGINES[name], query, per_engine, page, safe,
                           time_range, region): name for name in valid_engines}
    pending = set(futures)
    deadline = time.monotonic() + timeout_value
    try:
        while pending:
            remaining = max(0.0, deadline - time.monotonic())
            completed, pending = wait(pending, timeout=remaining, return_when=FIRST_COMPLETED)
            if not completed:
                break
            for future in completed:
                name = futures[future]
                try:
                    results = future.result() or []
                    if not isinstance(results, (list, tuple)):
                        raise TypeError("engine returned a non-list result")
                    safe_results = []
                    for result in results[:max(per_engine * 3, 20)]:
                        if not isinstance(result, dict):
                            continue
                        safe_url = _clean_search_url(result.get("url", ""))
                        if not safe_url:
                            continue
                        item = dict(result)
                        item["url"] = safe_url
                        item["title"] = _clean_text(item.get("title", ""), 500)
                        item["snippet"] = _clean_text(item.get("snippet", ""), 2000)
                        item["domain"] = (urllib.parse.urlsplit(safe_url).hostname or "").lower()
                        safe_results.append(item)
                    buckets[name] = safe_results
                    if not buckets[name]:
                        errors.setdefault(name, "no results")
                except Exception as exc:
                    errors[name] = f"{type(exc).__name__}: {exc}"[:300]
        for future in pending:
            name = futures[future]
            errors[name] = f"TimeoutError: engine exceeded {timeout_value:g}s deadline"
            future.cancel()
    finally:
        # Do not block the caller on a misbehaving engine. Running HTTP requests
        # retain their own socket deadlines and are isolated in these worker threads.
        pool.shutdown(wait=not pending)
    return buckets, errors


def _fuse_results(buckets: Dict[str, List[Dict[str, Any]]], limit: int,
                  k: float = 60.0) -> List[Dict[str, Any]]:
    """Reciprocal-rank fusion: agreement across engines beats any single ranking."""
    merged: Dict[str, Dict[str, Any]] = {}
    for engine, results in buckets.items():
        weight = _ENGINE_WEIGHTS.get(engine, 1.0)
        for rank, item in enumerate(results, 1):
            key = _normalize_url_for_dedupe(item["url"])
            entry = merged.get(key)
            if entry is None:
                entry = dict(item)
                entry["engines"] = []
                entry["score"] = 0.0
                merged[key] = entry
            entry["score"] += weight / (k + rank)
            if engine not in entry["engines"]:
                entry["engines"].append(engine)
            # prefer the longest snippet / most informative title seen
            if len(item.get("snippet", "")) > len(entry.get("snippet", "")):
                entry["snippet"] = item["snippet"]
            if len(item.get("title", "")) > len(entry.get("title", "")):
                entry["title"] = item["title"]
    ordered = sorted(merged.values(),
                     key=lambda r: (-len(r["engines"]), -r["score"], r.get("rank", 99)))
    final = []
    for position, item in enumerate(ordered[:limit], 1):
        item.pop("rank", None)
        item["engine"] = ", ".join(item["engines"])
        item["score"] = round(item["score"], 5)
        item["position"] = position
        final.append(item)
    return final


def _keywords(text: str, limit: int = 12) -> List[str]:
    words = re.findall(r"[A-Za-z][A-Za-z0-9'\-]{2,}", str(text or "").lower())
    counts = Counter(w for w in words if w not in _SEARCH_STOPWORDS and len(w) > 2)
    return [w for w, _ in counts.most_common(limit)]


def _summarize_text(text: str, max_sentences: int = 5, query: str = "") -> str:
    """Dependency-free extractive summary (word-frequency + query-term boosting)."""
    clean = re.sub(r"\s+", " ", str(text or "")).strip()
    if not clean:
        return ""
    sentences = [s.strip() for s in re.split(r"(?<=[.!?])\s+(?=[A-Z0-9\"'(])", clean) if len(s.strip()) > 30]
    if not sentences:
        return clean[:800]
    freq = Counter(w for w in re.findall(r"[a-z][a-z0-9'\-]+", clean.lower())
                   if w not in _SEARCH_STOPWORDS)
    if not freq:
        return " ".join(sentences[:max_sentences])
    top = freq.most_common(200)
    weights = {w: c / top[0][1] for w, c in top}
    query_terms = {w for w in re.findall(r"[a-z0-9]+", str(query).lower()) if w not in _SEARCH_STOPWORDS}
    scored = []
    for index, sentence in enumerate(sentences[:400]):
        words = re.findall(r"[a-z][a-z0-9'\-]+", sentence.lower())
        if not words:
            continue
        score = sum(weights.get(w, 0.0) for w in words) / (len(words) ** 0.65)
        score += 0.35 * len(query_terms & set(words))
        score *= 1.15 if index < 3 else 1.0
        scored.append((score, index, sentence))
    scored.sort(reverse=True)
    picked = sorted(scored[:max(1, max_sentences)], key=lambda item: item[1])
    return " ".join(sentence for _, _, sentence in picked)


def _fetch_page_content(url: str, max_chars: int = 4000) -> Dict[str, Any]:
    try:
        result = _http_fetch(url, timeout=min(HTTP_TIMEOUT, 20), max_bytes=min(MAX_HTTP_BYTES, 1_500_000))
        details = _extract_html_details(result["text"], result["url"])
        text = details["text"] or result["text"]
        return {"url": url, "final_url": result["url"], "title": details["title"],
                "status": result["status"], "chars": len(text),
                "truncated": result["truncated"] or len(text) > max_chars,
                "content": text[:max_chars]}
    except Exception as exc:
        return {"url": url, "error": f"{type(exc).__name__}: {exc}"[:300], "content": ""}


def _fetch_pages_parallel(urls: Sequence[str], max_chars: int = 4000,
                          workers: int = 5) -> List[Dict[str, Any]]:
    urls = list(dict.fromkeys([u for u in urls if u]))
    if not urls:
        return []
    out: List[Dict[str, Any]] = []
    with ThreadPoolExecutor(max_workers=max(1, min(len(urls), workers))) as pool:
        futures = {pool.submit(_fetch_page_content, u, max_chars): u for u in urls}
        for future in as_completed(futures):
            with contextlib.suppress(Exception):
                out.append(future.result())
    order = {u: i for i, u in enumerate(urls)}
    out.sort(key=lambda item: order.get(item.get("url", ""), 999))
    return out


def web_search(query: str,
               max_results: int = 5,
               page: int = 1,
               domain: str = "",
               safe_search: bool = False,
               engines: str = "auto",
               time_range: str = "",
               region: str = "us-en",
               fetch_content: bool = False,
               content_chars: int = 2000,
               use_cache: bool = True) -> str:
    """Multi-engine web search with rank fusion, de-duplication and optional page fetching."""
    start = time.time()
    try:
        max_results = _bounded_int(max_results, 5, 1, MCP_MAX_SEARCH_RESULTS)
        page = _bounded_int(page, 1, 1, 100)
        content_chars = _bounded_int(content_chars, 2000, 200, MAX_TEXT_CHARS)
        safe_search = _to_bool(safe_search, False)
        fetch_content = _to_bool(fetch_content, False)
        use_cache = _to_bool(use_cache, True)
        time_range = _normalize_time_range(time_range)
        region = str(region or "us-en").strip().lower()[:64] or "us-en"

        query = str(query or "").replace(r"\"", '"').replace(r"\:", ":").strip()
        if not query:
            return _error("query must be a non-empty string")
        if len(query) > 2000:
            return _error("query exceeds 2,000 characters")
        domain = str(domain or "").strip().lstrip("@").replace("https://", "").replace("http://", "").strip("/")
        if len(domain) > 253:
            return _error("domain exceeds 253 characters")
        search_query = query
        if domain and not re.search(r"(?:^|\s)site:", search_query, re.I):
            search_query = f"site:{domain} {search_query}".strip()

        requested_engines = engines if str(engines or "").strip() else DEFAULT_SEARCH_ENGINES
        selected = _select_engines(requested_engines)
        if not selected:
            return _error("No valid search engines selected", requested=str(requested_engines),
                          available=available_engines(), supported=sorted(SEARCH_ENGINES))
        cache_id = _cache_key("search", search_query, max_results, page, safe_search,
                              tuple(selected), time_range, region)
        if use_cache:
            cached = _cache_get(cache_id)
            if cached is not None:
                payload = json.loads(cached)
                payload["cached"] = True
                if fetch_content:
                    payload["pages"] = _fetch_pages_parallel(
                        [r["url"] for r in payload.get("results", [])[:5]], content_chars)
                return _json_result(payload)

        per_engine = max(max_results, min(max_results * 2, 25))
        buckets, errors = _run_engines(selected, search_query, per_engine, page,
                                       safe_search, time_range, region)
        results = _fuse_results(buckets, max_results)

        # Fallback: if the preferred engines all failed, try whatever is left.
        if not results:
            fallback = [e for e in available_engines() if e not in selected]
            if fallback:
                extra_buckets, extra_errors = _run_engines(fallback[:3], search_query, per_engine,
                                                           page, safe_search, time_range, region)
                errors.update(extra_errors)
                buckets.update(extra_buckets)
                results = _fuse_results(buckets, max_results)

        payload: Dict[str, Any] = {
            "ok": bool(results),
            "query": query,
            "search_query": search_query,
            "domain": domain,
            "page": page,
            "count": len(results),
            "safe_search": safe_search,
            "time_range": time_range or "any",
            "region": region,
            "engines_used": sorted(buckets.keys()),
            "engine_result_counts": {k: len(v) for k, v in sorted(buckets.items())},
            "elapsed_ms": round((time.time() - start) * 1000, 1),
            "cached": False,
            "results": results,
        }
        if errors:
            payload["engine_errors"] = errors
        if not results:
            payload["error"] = f"No search results found for: {query}"
        elif use_cache:
            _cache_set(cache_id, _json_result(payload), SEARCH_CACHE_TTL)
        if fetch_content and results:
            payload["pages"] = _fetch_pages_parallel([r["url"] for r in results[:5]], content_chars)
        return _json_result(payload)
    except Exception as exc:
        return _error(f"web_search failed: {exc}")


def web_search_news(query: str, max_results: int = 10, time_range: str = "week",
                    region: str = "US:en", safe_search: bool = False) -> str:
    """Search recent news via Google News RSS and Bing News, merged and de-duplicated."""
    try:
        max_results = _bounded_int(max_results, 10, 1, MCP_MAX_SEARCH_RESULTS)
        time_range = _normalize_time_range(time_range) or "week"
        safe_search = _to_bool(safe_search, False)
        query = str(query or "").strip()
        if not query:
            return _error("query must be a non-empty string")
        if len(query) > 2000:
            return _error("query exceeds 2,000 characters")
        region = str(region or "US:en")[:64]
        country, _, lang = region.partition(":")
        lang = lang or "en"
        items: List[Dict[str, Any]] = []
        errors: Dict[str, str] = {}

        def google_news():
            when = {"day": "1d", "week": "7d", "month": "30d", "year": "1y"}.get(time_range, "7d")
            params = {"q": f"{query} when:{when}", "hl": f"{lang}-{country}",
                      "gl": country, "ceid": f"{country}:{lang}",
                      "safe": "active" if safe_search else "off"}
            body = _http_get_text("https://news.google.com/rss/search?" + urllib.parse.urlencode(params),
                                  headers={"User-Agent": BROWSER_UA}, timeout=15)
            root = _safe_parse_xml(body)
            out = []
            for node in itertools.islice(root.iter("item"), max_results * 2):
                title = (node.findtext("title") or "").strip()[:400]
                link = (node.findtext("link") or "").strip()[:2048]
                source = (node.findtext("{*}source") or node.findtext("source") or "")[:200]
                out.append({"title": title, "url": link,
                            "snippet": _strip_tags(node.findtext("description") or "")[:1000],
                            "published": (node.findtext("pubDate") or "")[:200],
                            "source": source, "engine": "google_news"})
            return out

        def bing_news():
            params = {"q": query, "format": "rss", "adlt": "strict" if safe_search else "off",
                      "qft": {"day": "interval=\"4\"", "week": "interval=\"7\"",
                              "month": "interval=\"9\""}.get(time_range, ""),
                      "setlang": lang}
            params = {k: v for k, v in params.items() if v}
            body = _http_get_text("https://www.bing.com/news/search?" + urllib.parse.urlencode(params),
                                  headers={"User-Agent": BROWSER_UA}, timeout=15)
            root = _safe_parse_xml(body)
            out = []
            for node in itertools.islice(root.iter("item"), max_results * 2):
                out.append({"title": (node.findtext("title") or "").strip()[:400],
                            "url": (node.findtext("link") or "").strip()[:2048],
                            "snippet": _strip_tags(node.findtext("description") or "")[:1000],
                            "published": (node.findtext("pubDate") or "")[:200],
                            "source": "", "engine": "bing_news"})
            return out

        pool = ThreadPoolExecutor(max_workers=2)
        futures = {pool.submit(google_news): "google_news", pool.submit(bing_news): "bing_news"}
        pending = set(futures)
        deadline = time.monotonic() + 30.0
        try:
            while pending:
                completed, pending = wait(pending,
                                          timeout=max(0.0, deadline - time.monotonic()),
                                          return_when=FIRST_COMPLETED)
                if not completed:
                    break
                for future in completed:
                    name = futures[future]
                    try:
                        items.extend(future.result() or [])
                    except Exception as exc:
                        errors[name] = f"{type(exc).__name__}: {exc}"[:300]
            for future in pending:
                errors[futures[future]] = "TimeoutError: news engine exceeded 30s deadline"
                future.cancel()
        finally:
            pool.shutdown(wait=not pending)

        seen, merged = set(), []
        for item in items:
            url = _clean_search_url(item.get("url", ""))
            if not url:
                continue
            key = _normalize_url_for_dedupe(url)
            title_key = _clean_text(item.get("title", "")).lower()[:90]
            if key in seen or (title_key and title_key in seen):
                continue
            seen.add(key)
            if title_key:
                seen.add(title_key)
            merged.append({"title": _clean_text(item.get("title"), 400), "url": url,
                           "snippet": _clean_text(item.get("snippet"), 1000),
                           "published": item.get("published", ""),
                           "source": item.get("source") or (urllib.parse.urlsplit(url).hostname or ""),
                           "engine": item.get("engine", "")})
            if len(merged) >= max_results:
                break
        payload = {"ok": bool(merged), "query": query, "time_range": time_range,
                   "safe_search": safe_search, "count": len(merged), "articles": merged}
        if errors:
            payload["engine_errors"] = errors
        if not merged:
            payload["error"] = f"No news results for: {query}"
        return _json_result(payload)
    except Exception as exc:
        return _error(f"web_search_news failed: {exc}")


def web_search_images(query: str, max_results: int = 10, safe_search: bool = True) -> str:
    """Image search through DuckDuckGo's i.js endpoint (with a Bing HTML fallback)."""
    try:
        max_results = _bounded_int(max_results, 10, 1, MCP_MAX_SEARCH_RESULTS)
        safe_search = _to_bool(safe_search, True)
        query = str(query or "").strip()
        if not query:
            return _error("query must be a non-empty string")
        if len(query) > 2000:
            return _error("query exceeds 2,000 characters")
        images: List[Dict[str, Any]] = []
        errors: Dict[str, str] = {}
        try:
            token_page = _http_get_text("https://duckduckgo.com/?" + urllib.parse.urlencode({"q": query, "iax": "images", "ia": "images"}),
                                        headers={"User-Agent": BROWSER_UA}, timeout=15)
            vqd = re.search(r"vqd=[\"']?([\d-]+)[\"']?", token_page)
            if not vqd:
                raise RuntimeError("could not obtain DuckDuckGo vqd token")
            params = {"l": "us-en", "o": "json", "q": query, "vqd": vqd.group(1),
                      "f": ",,,", "p": "1" if safe_search else "-1"}
            body = _http_get_text("https://duckduckgo.com/i.js?" + urllib.parse.urlencode(params),
                                  headers={"User-Agent": BROWSER_UA, "Referer": "https://duckduckgo.com/",
                                           "Accept": "application/json"}, timeout=15)
            for hit in json.loads(body).get("results", []):
                image_url = _clean_search_url(hit.get("image", ""))
                if image_url:
                    images.append({"title": _clean_text(hit.get("title"), 300),
                                   "image_url": image_url,
                                   "thumbnail": _clean_search_url(hit.get("thumbnail", "")),
                                   "source_url": _clean_search_url(hit.get("url", "")),
                                   "width": hit.get("width"), "height": hit.get("height"),
                                   "engine": "duckduckgo_images"})
                if len(images) >= max_results:
                    break
        except Exception as exc:
            errors["duckduckgo_images"] = f"{type(exc).__name__}: {exc}"[:300]

        if not images:
            try:
                params = {"q": query, "adlt": "strict" if safe_search else "off", "form": "HDRSC2"}
                body = _http_get_text("https://www.bing.com/images/search?" + urllib.parse.urlencode(params),
                                      headers={"User-Agent": BROWSER_UA}, timeout=15)
                for match in re.finditer(r'm=["\']({&quot;.*?})["\']', body):
                    with contextlib.suppress(Exception):
                        meta = json.loads(html_module.unescape(match.group(1)))
                        image_url = _clean_search_url(meta.get("murl", ""))
                        if image_url:
                            images.append({"title": _clean_text(meta.get("t"), 300),
                                           "image_url": image_url,
                                           "thumbnail": _clean_search_url(meta.get("turl", "")),
                                           "source_url": _clean_search_url(meta.get("purl", "")),
                                           "engine": "bing_images"})
                    if len(images) >= max_results:
                        break
            except Exception as exc:
                errors["bing_images"] = f"{type(exc).__name__}: {exc}"[:300]

        payload = {"ok": bool(images), "query": query, "safe_search": safe_search,
                   "count": len(images), "images": images[:max_results]}
        if errors:
            payload["engine_errors"] = errors
        if not images:
            payload["error"] = f"No image results for: {query}"
        return _json_result(payload)
    except Exception as exc:
        return _error(f"web_search_images failed: {exc}")


def web_search_answer(query: str, include_wikipedia: bool = True) -> str:
    """Instant answer lookup: DuckDuckGo IA + Wikipedia summary + top organic hits."""
    try:
        query = str(query or "").strip()
        if not query:
            return _error("query must be a non-empty string")
        if len(query) > 2000:
            return _error("query exceeds 2,000 characters")
        answer: Dict[str, Any] = {"ok": True, "query": query, "answers": []}
        with contextlib.suppress(Exception):
            params = {"q": query, "format": "json", "no_html": "1", "skip_disambig": "1", "t": "mcp"}
            data = json.loads(_http_get_text("https://api.duckduckgo.com/?" + urllib.parse.urlencode(params),
                                             headers={"User-Agent": USER_AGENT}, timeout=12,
                                             cache_ttl=SEARCH_CACHE_TTL))
            if data.get("AbstractText"):
                answer["answers"].append({"source": "duckduckgo", "type": data.get("Type", "")[:50],
                                          "text": str(data["AbstractText"])[:4000],
                                          "url": _clean_search_url(data.get("AbstractURL", "")),
                                          "heading": str(data.get("Heading", ""))[:500]})
            if data.get("Answer"):
                answer["answers"].append({"source": "duckduckgo_instant",
                                          "text": str(data["Answer"])[:2000],
                                          "url": _clean_search_url(data.get("AbstractURL", ""))})
            related = [_clean_text(t.get("Text"), 1000)
                       for t in data.get("RelatedTopics", [])
                       if isinstance(t, dict) and t.get("Text")]
            if related:
                answer["related_topics"] = related[:8]
        if _to_bool(include_wikipedia, True):
            with contextlib.suppress(Exception):
                summary = json.loads(wikipedia_lookup(query, sentences=3))
                if summary.get("ok") and summary.get("extract"):
                    answer["answers"].append({"source": "wikipedia", "text": summary["extract"],
                                              "url": summary.get("url", ""),
                                              "heading": summary.get("title", "")})
        with contextlib.suppress(Exception):
            search = json.loads(web_search(query, max_results=5))
            answer["top_results"] = search.get("results", [])
        answer["count"] = len(answer["answers"])
        if not answer["answers"] and not answer.get("top_results"):
            return _error(f"No answer found for: {query}")
        return _json_result(answer)
    except Exception as exc:
        return _error(f"web_search_answer failed: {exc}")


def web_search_suggestions(query: str, max_results: int = 10) -> str:
    """Autocomplete/related-query suggestions from DuckDuckGo and Bing."""
    try:
        query = str(query or "").strip()
        if not query:
            return _error("query must be a non-empty string")
        if len(query) > 2000:
            return _error("query exceeds 2,000 characters")
        limit = _bounded_int(max_results, 10, 1, 50)
        suggestions: List[str] = []
        with contextlib.suppress(Exception):
            data = json.loads(_http_get_text("https://duckduckgo.com/ac/?" + urllib.parse.urlencode({"q": query, "type": "list"}),
                                             headers={"User-Agent": BROWSER_UA}, timeout=10,
                                             cache_ttl=SEARCH_CACHE_TTL))
            if isinstance(data, list) and len(data) > 1 and isinstance(data[1], list):
                suggestions.extend(str(s)[:500] for s in data[1][:limit * 3])
            elif isinstance(data, list):
                suggestions.extend(str(d.get("phrase", ""))[:500]
                                   for d in data[:limit * 3] if isinstance(d, dict))
        with contextlib.suppress(Exception):
            body = _http_get_text("https://api.bing.com/osjson.aspx?" + urllib.parse.urlencode({"query": query}),
                                  headers={"User-Agent": BROWSER_UA}, timeout=10, cache_ttl=SEARCH_CACHE_TTL)
            data = json.loads(body)
            if isinstance(data, list) and len(data) > 1 and isinstance(data[1], list):
                suggestions.extend(str(s)[:500] for s in data[1][:limit * 3])
        unique = [s for s in dict.fromkeys(x.strip() for x in suggestions if x.strip())][:limit]
        return _json_result({"ok": bool(unique), "query": query, "count": len(unique),
                             "suggestions": unique})
    except Exception as exc:
        return _error(f"web_search_suggestions failed: {exc}")


def wikipedia_lookup(query: str, language: str = "en", sentences: int = 5) -> str:
    """Fetch a Wikipedia summary (with search fallback) for *query*."""
    try:
        query = str(query or "").strip()
        if not query:
            return _error("query must be a non-empty string")
        if len(query) > 2000:
            return _error("query exceeds 2,000 characters")
        lang = re.sub(r"[^a-z]", "", str(language or "en").lower())[:5] or "en"
        sentences = _bounded_int(sentences, 5, 1, 20)
        title = query

        def summary_for(page_title: str) -> Optional[Dict[str, Any]]:
            url = f"https://{lang}.wikipedia.org/api/rest_v1/page/summary/{urllib.parse.quote(page_title.replace(' ', '_'))}"
            body = _http_get_text(url, headers={"User-Agent": USER_AGENT, "Accept": "application/json"},
                                  timeout=12, cache_ttl=SEARCH_CACHE_TTL)
            data = json.loads(body)
            if data.get("type") == "disambiguation" or not data.get("extract"):
                return None
            return data

        data = None
        with contextlib.suppress(Exception):
            data = summary_for(title)
        if data is None:
            params = {"action": "query", "list": "search", "srsearch": query, "format": "json", "srlimit": 1}
            body = _http_get_text(f"https://{lang}.wikipedia.org/w/api.php?" + urllib.parse.urlencode(params),
                                  headers={"User-Agent": USER_AGENT}, timeout=12)
            hits = json.loads(body).get("query", {}).get("search", [])
            if not hits:
                return _error(f"No Wikipedia article found for: {query}")
            title = hits[0]["title"]
            data = summary_for(title)
        if not data:
            return _error(f"No Wikipedia summary available for: {query}")
        extract = str(data.get("extract", ""))
        parts = re.split(r"(?<=[.!?])\s+", extract[:200000])
        return _ok({
            "query": query,
            "title": str(data.get("title", title))[:500],
            "description": str(data.get("description", ""))[:1000],
            "extract": " ".join(parts[:sentences]).strip()[:5000],
            "full_extract": extract[:20000],
            "extract_truncated": len(extract) > 20000,
            "url": _clean_search_url(data.get("content_urls", {}).get("desktop", {}).get("page", "")),
            "thumbnail": _clean_search_url(data.get("thumbnail", {}).get("source", "")),
            "language": lang,
        })
    except Exception as exc:
        return _error(f"wikipedia_lookup failed: {exc}")


def arxiv_search(query: str, max_results: int = 10, sort_by: str = "relevance") -> str:
    """Search arXiv preprints (title, authors, abstract, PDF link)."""
    try:
        query = str(query or "").strip()
        if not query:
            return _error("query must be a non-empty string")
        if len(query) > 2000:
            return _error("query exceeds 2,000 characters")
        limit = _bounded_int(max_results, 10, 1, 50)
        sort_map = {"relevance": "relevance", "recent": "submittedDate", "updated": "lastUpdatedDate"}
        params = {"search_query": f"all:{query}" if ":" not in query else query,
                  "start": 0, "max_results": limit,
                  "sortBy": sort_map.get(str(sort_by).lower(), "relevance"),
                  "sortOrder": "descending"}
        body = _http_get_text("http://export.arxiv.org/api/query?" + urllib.parse.urlencode(params),
                              headers={"User-Agent": USER_AGENT}, timeout=20, cache_ttl=SEARCH_CACHE_TTL)
        root = _safe_parse_xml(body)
        ns = {"a": "http://www.w3.org/2005/Atom"}
        papers = []
        for entry in itertools.islice(root.iter("{http://www.w3.org/2005/Atom}entry"), limit):
            links = {link.get("title") or link.get("rel"): link.get("href")
                     for link in itertools.islice(entry.iter("{http://www.w3.org/2005/Atom}link"), 20)}
            papers.append({
                "title": _clean_text(entry.findtext("a:title", "", ns), 500),
                "authors": [_clean_text(a.findtext("a:name", "", ns), 200)
                            for a in itertools.islice(entry.iter("{http://www.w3.org/2005/Atom}author"), 20)],
                "summary": _clean_text(entry.findtext("a:summary", "", ns), 3000),
                "published": entry.findtext("a:published", "", ns)[:100],
                "updated": entry.findtext("a:updated", "", ns)[:100],
                "url": _clean_search_url(entry.findtext("a:id", "", ns)),
                "pdf_url": _clean_search_url(links.get("pdf", "")),
                "categories": [c.get("term", "")[:200]
                               for c in itertools.islice(entry.iter("{http://www.w3.org/2005/Atom}category"), 10)],
            })
        return _json_result({"ok": bool(papers), "query": query, "count": len(papers), "papers": papers})
    except Exception as exc:
        return _error(f"arxiv_search failed: {exc}")


def github_search(query: str, search_type: str = "repositories", max_results: int = 10,
                  sort: str = "") -> str:
    """Search GitHub repositories, code, issues or users via the public API."""
    try:
        query = str(query or "").strip()
        if not query:
            return _error("query must be a non-empty string")
        if len(query) > 2000:
            return _error("query exceeds 2,000 characters")
        kind = str(search_type or "repositories").lower().strip()
        if kind in ("repo", "repos", "repository"):
            kind = "repositories"
        if kind not in {"repositories", "code", "issues", "users", "commits", "topics"}:
            return _error("search_type must be one of repositories, code, issues, users, commits, topics")
        if kind == "code" and not GITHUB_TOKEN:
            return _error("GitHub code search requires GITHUB_TOKEN to be configured")
        limit = _bounded_int(max_results, 10, 1, 50)
        params = {"q": query, "per_page": limit}
        if sort:
            params["sort"] = str(sort)[:50]
        headers = {"Accept": "application/vnd.github+json", "User-Agent": USER_AGENT,
                   "X-GitHub-Api-Version": "2022-11-28"}
        if GITHUB_TOKEN:
            headers["Authorization"] = f"Bearer {GITHUB_TOKEN}"
        body = _http_get_text(f"https://api.github.com/search/{kind}?" + urllib.parse.urlencode(params),
                              headers=headers, timeout=20, cache_ttl=SEARCH_CACHE_TTL)
        data = json.loads(body)
        items = []
        for hit in data.get("items", [])[:limit]:
            if kind == "repositories":
                topics = hit.get("topics", [])
                topics = topics if isinstance(topics, list) else []
                items.append({"name": _clean_text(hit.get("full_name"), 300),
                              "url": _clean_search_url(hit.get("html_url", "")),
                              "description": _clean_text(hit.get("description"), 2000),
                              "stars": hit.get("stargazers_count"), "forks": hit.get("forks_count"),
                              "language": _clean_text(hit.get("language"), 100),
                              "updated": str(hit.get("updated_at", ""))[:100],
                              "topics": [_clean_text(topic, 100) for topic in topics[:10]]})
            elif kind == "code":
                items.append({"name": _clean_text(hit.get("name"), 300),
                              "path": _clean_text(hit.get("path"), 1000),
                              "repository": _clean_text((hit.get("repository") or {}).get("full_name"), 300),
                              "url": _clean_search_url(hit.get("html_url", ""))})
            elif kind == "issues":
                items.append({"title": _clean_text(hit.get("title"), 500),
                              "url": _clean_search_url(hit.get("html_url", "")),
                              "state": _clean_text(hit.get("state"), 30),
                              "comments": hit.get("comments"),
                              "created": str(hit.get("created_at", ""))[:100],
                              "body": _clean_text(hit.get("body"), 800)})
            elif kind == "users":
                items.append({"login": _clean_text(hit.get("login"), 200),
                              "url": _clean_search_url(hit.get("html_url", "")),
                              "type": _clean_text(hit.get("type"), 50), "score": hit.get("score")})
            else:
                item = {k: hit.get(k) for k in ("name", "html_url", "url", "description",
                                                  "sha", "score") if hit.get(k) is not None}
                for url_key in ("html_url", "url"):
                    if url_key in item:
                        item[url_key] = _clean_search_url(item[url_key])
                for text_key in ("name", "description", "sha"):
                    if text_key in item:
                        item[text_key] = _clean_text(item[text_key], 1000)
                items.append(item)
        return _json_result({"ok": bool(items), "query": query, "search_type": kind,
                             "total_available": data.get("total_count", 0),
                             "count": len(items), "items": items,
                             "authenticated": bool(GITHUB_TOKEN)})
    except Exception as exc:
        return _error(f"github_search failed: {exc}")


def stackexchange_search(query: str, site: str = "stackoverflow", max_results: int = 10,
                         tagged: str = "") -> str:
    """Search Stack Exchange (Stack Overflow by default) for questions and answers."""
    try:
        query = str(query or "").strip()
        if not query:
            return _error("query must be a non-empty string")
        if len(query) > 2000:
            return _error("query exceeds 2,000 characters")
        limit = _bounded_int(max_results, 10, 1, 50)
        site = str(site or "stackoverflow")[:100]
        tagged = str(tagged or "")[:500]
        params = {"order": "desc", "sort": "relevance", "q": query, "site": site,
                  "pagesize": limit, "filter": "withbody"}
        if tagged:
            params["tagged"] = tagged
        body = _http_get_text("https://api.stackexchange.com/2.3/search/advanced?" + urllib.parse.urlencode(params),
                              headers={"User-Agent": USER_AGENT, "Accept": "application/json",
                                       "Accept-Encoding": "identity"},
                              timeout=20, cache_ttl=SEARCH_CACHE_TTL)
        data = json.loads(body)
        items = []
        for hit in data.get("items", [])[:limit]:
            tags = hit.get("tags", [])
            tags = tags if isinstance(tags, list) else []
            items.append({"title": _clean_text(hit.get("title"), 400),
                          "url": _clean_search_url(hit.get("link", "")),
                          "score": hit.get("score"), "answers": hit.get("answer_count"),
                          "is_answered": hit.get("is_answered"),
                          "tags": [_clean_text(tag, 100) for tag in tags[:10]],
                          "created": hit.get("creation_date"),
                          "excerpt": _strip_tags(hit.get("body", ""))[:1200]})
        return _json_result({"ok": bool(items), "query": query, "site": params["site"],
                             "count": len(items), "quota_remaining": data.get("quota_remaining"),
                             "questions": items})
    except Exception as exc:
        return _error(f"stackexchange_search failed: {exc}")


def research_topic(query: str, max_sources: int = 5, max_chars_per_source: int = 4000,
                   summary_sentences: int = 6, include_news: bool = False) -> str:
    """One-shot research pipeline: search -> parallel fetch -> extractive brief + sources."""
    start = time.time()
    try:
        query = str(query or "").strip()
        if not query:
            return _error("query must be a non-empty string")
        max_sources = _bounded_int(max_sources, 5, 1, 12)
        max_chars_per_source = _bounded_int(max_chars_per_source, 4000, 500, 20000)
        summary_sentences = _bounded_int(summary_sentences, 6, 1, 25)

        search_payload = json.loads(web_search(query, max_results=max_sources * 2, use_cache=True))
        results = search_payload.get("results", [])
        if not results:
            return _error(f"No sources found for: {query}",
                          engine_errors=search_payload.get("engine_errors"))
        chosen, seen_domains = [], set()
        for item in results:
            domain = item.get("domain", "")
            if domain in seen_domains and len(chosen) >= max_sources // 2:
                continue
            seen_domains.add(domain)
            chosen.append(item)
            if len(chosen) >= max_sources:
                break

        pages = _fetch_pages_parallel([c["url"] for c in chosen], max_chars_per_source)
        sources, corpus = [], []
        for item in chosen:
            page = next((p for p in pages if p.get("url") == item["url"]), {})
            content = page.get("content", "")
            if content:
                corpus.append(content)
            sources.append({
                "title": page.get("title") or item.get("title"),
                "url": item["url"],
                "domain": item.get("domain"),
                "engines": item.get("engines", []),
                "snippet": item.get("snippet", ""),
                "fetched": bool(content),
                "error": page.get("error"),
                "key_points": _summarize_text(content, 3, query) if content else "",
                "chars": page.get("chars", 0),
            })

        combined = "\n\n".join(corpus)
        brief = _summarize_text(combined, summary_sentences, query) if combined else \
            _summarize_text(" ".join(i.get("snippet", "") for i in chosen), summary_sentences, query)
        answer = json.loads(web_search_answer(query)) if not combined else {}
        payload = {
            "ok": True,
            "query": query,
            "summary": brief,
            "keywords": _keywords(combined or query, 15),
            "source_count": len(sources),
            "sources": sources,
            "fetched_chars": len(combined),
            "elapsed_ms": round((time.time() - start) * 1000, 1),
        }
        if answer.get("answers"):
            payload["instant_answers"] = answer["answers"]
        if _to_bool(include_news, False):
            with contextlib.suppress(Exception):
                news = json.loads(web_search_news(query, max_results=5))
                payload["news"] = news.get("articles", [])
        return _json_result(payload)
    except Exception as exc:
        return _error(f"research_topic failed: {exc}")


def fetch_many_urls(urls: str, max_chars: int = 4000, as_markdown: bool = False) -> str:
    """Fetch several URLs in parallel (comma/newline/JSON-array separated) and extract text."""
    try:
        raw = urls
        url_list: List[str] = []
        if isinstance(raw, (list, tuple)):
            url_list = [str(u) for u in raw]
        else:
            text = str(raw or "").strip()
            if text.startswith("["):
                with contextlib.suppress(Exception):
                    url_list = [str(u) for u in json.loads(text)]
            if not url_list:
                url_list = [u.strip() for u in re.split(r"[\s,]+", text) if u.strip()]
        url_list = url_list[:20]
        if not url_list:
            return _error("Provide at least one URL")
        max_chars = _bounded_int(max_chars, 4000, 200, MAX_TEXT_CHARS)
        if _to_bool(as_markdown, False):
            def grab(url: str) -> Dict[str, Any]:
                try:
                    result = _http_fetch(url, timeout=HTTP_TIMEOUT,
                                         max_bytes=min(MAX_HTTP_BYTES, MAX_HTML_BYTES))
                    markdown = _html_to_markdown(result["text"], result["url"])
                    return {"url": url, "final_url": result["url"], "status": result["status"],
                            "truncated": result["truncated"] or len(markdown) > max_chars,
                            "markdown": markdown[:max_chars]}
                except Exception as exc:
                    return {"url": url, "error": f"{type(exc).__name__}: {exc}"[:300]}
            with ThreadPoolExecutor(max_workers=min(len(url_list), 6)) as pool:
                pages = list(pool.map(grab, url_list))
        else:
            pages = _fetch_pages_parallel(url_list, max_chars, workers=6)
        ok_count = sum(1 for p in pages if not p.get("error"))
        return _json_result({"ok": ok_count > 0, "requested": len(url_list), "succeeded": ok_count,
                             "failed": len(pages) - ok_count, "pages": pages})
    except Exception as exc:
        return _error(f"fetch_many_urls failed: {exc}")


def url_to_markdown(url: str, max_chars: int = 20000, include_links: bool = True) -> str:
    """Fetch a page and convert it to readable Markdown."""
    try:
        max_chars = _bounded_int(max_chars, 20000, 200, MAX_TEXT_CHARS)
        result = _http_fetch(url, timeout=HTTP_TIMEOUT,
                             max_bytes=min(MAX_HTTP_BYTES, MAX_HTML_BYTES))
        markdown = _html_to_markdown(result["text"], result["url"])
        if not _to_bool(include_links, True):
            markdown = re.sub(r"\[([^\]]*)\]\([^)]*\)", r"\1", markdown)
        details = _extract_html_details(result["text"], result["url"])
        return _json_result({"ok": True, "url": url, "final_url": result["url"],
                             "title": details["title"], "description": details["description"],
                             "length": len(markdown),
                             "truncated": result["truncated"] or len(markdown) > max_chars,
                             "markdown": markdown[:max_chars]})
    except Exception as exc:
        return _error(f"url_to_markdown failed: {exc}")


def fetch_webpage_text(url: str, max_chars: int = 8000, summarize: bool = False) -> str:
    """Fetch a URL and return the extracted readable text (optionally summarised)."""
    try:
        max_chars = _bounded_int(max_chars, 8000, 256, MAX_TEXT_CHARS)
        result = _http_fetch_retrying(url, timeout=HTTP_TIMEOUT,
                                      max_bytes=min(MAX_HTTP_BYTES, MAX_HTML_BYTES))
        details = _extract_html_details(result["text"], result["url"])
        text = details["text"] or result["text"]
        payload = {
            "ok": True,
            "url": url,
            "final_url": result["url"],
            "status": result["status"],
            "title": details["title"],
            "description": details["description"],
            "length": min(len(text), max_chars),
            "total_length": len(text),
            "truncated": len(text) > max_chars or result["truncated"],
            "text": text[:max_chars],
        }
        if _to_bool(summarize, False):
            payload["summary"] = _summarize_text(text, 6)
        return _json_result(payload)
    except Exception as exc:
        return _error(f"Failed to fetch webpage: {exc}")


# ---------------------------------------------------------------------------
# Filesystem Tools
# ---------------------------------------------------------------------------
_BINARY_EXTS = {".png", ".jpg", ".jpeg", ".gif", ".bmp", ".ico", ".webp", ".pdf", ".zip", ".gz",
                ".bz2", ".xz", ".7z", ".rar", ".tar", ".exe", ".dll", ".so", ".dylib", ".class",
                ".pyc", ".pyo", ".o", ".a", ".bin", ".dat", ".db", ".sqlite", ".mp3", ".mp4",
                ".avi", ".mov", ".mkv", ".wav", ".flac", ".woff", ".woff2", ".ttf", ".otf"}


def _looks_binary(path: str) -> bool:
    if os.path.splitext(path)[1].lower() in _BINARY_EXTS:
        return True
    try:
        with open(path, "rb") as handle:
            return b"\x00" in handle.read(4096)
    except OSError:
        return True


def read_file(filepath: str, start_line: int = 1, line_count: int = 500) -> str:
    """Read a bounded text range from inside the sandbox."""
    try:
        path = _safe_path(filepath)
        if os.path.isdir(path):
            return _error(f"'{filepath}' is a directory; use list_directory")
        if not os.path.exists(path):
            return _error(f"File '{filepath}' not found")
        start_line = _bounded_int(start_line, 1, 1, 10_000_000)
        line_count = _bounded_int(line_count, 500, 1, 20_000)
        file_size = os.path.getsize(path)
        total_lines: Optional[int] = None
        with open(path, "r", encoding="utf-8", errors="replace") as stream:
            if file_size <= MAX_FILE_SCAN_BYTES:
                total_lines = sum(1 for _ in stream)
                stream.seek(0)

            skipped = 0
            scanned_chars = 0
            while skipped < start_line - 1:
                chunk = stream.readline(MAX_TEXT_CHARS + 1)
                if not chunk:
                    break
                scanned_chars += len(chunk)
                while not chunk.endswith("\n"):
                    chunk = stream.readline(MAX_TEXT_CHARS + 1)
                    if not chunk:
                        break
                    scanned_chars += len(chunk)
                    if scanned_chars > MAX_FILE_SCAN_BYTES:
                        return _error("Requested line range requires scanning more than 32 MiB")
                if scanned_chars > MAX_FILE_SCAN_BYTES:
                    return _error("Requested line range requires scanning more than 32 MiB")
                skipped += 1

            sliced_lines: List[str] = []
            content_chars = 0
            content_truncated = False
            while len(sliced_lines) < line_count and content_chars < MAX_TEXT_CHARS:
                remaining = MAX_TEXT_CHARS - content_chars
                chunk = stream.readline(remaining + 1)
                if not chunk:
                    break
                if len(chunk) > remaining:
                    chunk = chunk[:remaining]
                    content_truncated = True
                sliced_lines.append(chunk)
                content_chars += len(chunk)
                if content_truncated or not chunk.endswith("\n"):
                    break

        content = "".join(sliced_lines)
        has_more = (start_line - 1 + len(sliced_lines) < total_lines
                    if total_lines is not None else None)
        if total_lines is None and (content_truncated or len(sliced_lines) >= line_count):
            has_more = True
        return _ok({
            "filepath": filepath,
            "resolved_path": path,
            "total_lines": total_lines,
            "total_lines_known": total_lines is not None,
            "start_line": start_line,
            "returned_lines": len(sliced_lines),
            "has_more": has_more,
            "content_truncated": content_truncated,
            "content": content,
        })
    except Exception as exc:
        return _error(f"Error reading file: {exc}")


def write_file(filepath: str, content: str, create_backup: bool = False) -> str:
    """Write (overwrite) a UTF-8 text file atomically inside the sandbox."""
    try:
        path = _safe_path(filepath)
        content = "" if content is None else str(content)
        if len(content.encode("utf-8")) > MCP_MAX_REQUEST_BYTES:
            raise ValueError("Content exceeds configured file write limit")
        backup = ""
        if _to_bool(create_backup, False) and os.path.exists(path):
            if not os.path.isfile(path):
                raise ValueError("Backups are supported only for regular files")
            backup = f"{path}.bak"
            _safe_path(backup)
            backup_fd, backup_tmp = tempfile.mkstemp(prefix=".mts-backup-",
                                                     dir=os.path.dirname(path) or SANDBOX_ROOT)
            os.close(backup_fd)
            try:
                shutil.copyfile(path, backup_tmp)
                shutil.copystat(path, backup_tmp)
                os.replace(backup_tmp, backup)
            except Exception:
                with contextlib.suppress(OSError):
                    os.unlink(backup_tmp)
                raise
        written = _atomic_write(path, content)
        return _ok({"status": "success", "filepath": filepath, "resolved_path": path,
                    "bytes_written": written, "backup": backup})
    except Exception as exc:
        return _error(f"Error writing file: {exc}")


def edit_file_replace(filepath: str, target_snippet: str, replacement_snippet: str,
                      replace_all: bool = False) -> str:
    """Replace an exact snippet in a file; refuses ambiguous matches unless replace_all."""
    try:
        path = _safe_path(filepath)
        if not os.path.exists(path):
            return _error(f"File '{filepath}' does not exist.")
        if os.path.getsize(path) > MAX_FILE_SCAN_BYTES:
            return _error("Target file exceeds the 32 MiB edit limit")
        snippet_bytes = (len(str(target_snippet or "").encode("utf-8"))
                         + len(str(replacement_snippet or "").encode("utf-8")))
        if snippet_bytes > MCP_MAX_REQUEST_BYTES:
            return _error("Edit snippets exceed configured request size limit")
        replace_all = _to_bool(replace_all, False)
        with open(path, "r", encoding="utf-8", errors="replace") as f:
            content = f.read(MAX_FILE_SCAN_BYTES + 1)
        if len(content.encode("utf-8")) > MAX_FILE_SCAN_BYTES:
            return _error("Target file exceeds the 32 MiB edit limit")
        if not target_snippet:
            return _error("target_snippet must be a non-empty string")
        count = content.count(target_snippet)
        if count == 0:
            return _error(f"Target snippet not found in {filepath}.")
        if not replace_all and count > 1:
            return _error(f"Target snippet appears {count} times. Set replace_all=true.", occurrences=count)
        updated = content.replace(target_snippet, replacement_snippet) if replace_all \
            else content.replace(target_snippet, replacement_snippet, 1)
        if len(updated.encode("utf-8")) > MAX_FILE_SCAN_BYTES * 2:
            return _error("Edited file would exceed the 64 MiB write limit")
        _atomic_write(path, updated)
        return _ok({"status": "success", "filepath": filepath,
                    "replacements_made": count if replace_all else 1,
                    "bytes_written": len(updated.encode("utf-8"))})
    except Exception as exc:
        return _error(f"Error editing file: {exc}")


def append_to_file(filepath: str, content: str) -> str:
    """Append text to a file, creating it (and parents) when required."""
    try:
        path = _safe_path(filepath)
        content = "" if content is None else str(content)
        content_bytes = len(content.encode("utf-8"))
        if content_bytes > MCP_MAX_REQUEST_BYTES:
            raise ValueError("Content exceeds configured write limit")
        current_size = os.path.getsize(path) if os.path.exists(path) else 0
        if current_size + content_bytes > 64 * 1024 * 1024:
            raise ValueError("Appended file would exceed the 64 MiB file write limit")
        _ensure_parent(path)
        with open(path, "a", encoding="utf-8") as f:
            f.write(content)
        return _ok({"status": "success", "filepath": filepath,
                    "bytes_appended": len(content.encode("utf-8")),
                    "size_bytes": os.path.getsize(path)})
    except Exception as exc:
        return _error(f"Error appending: {exc}")


def list_directory(path: str = ".", show_hidden: bool = True, sort_by: str = "name") -> str:
    """List a directory with sizes, types and modification times."""
    try:
        target = _safe_path(path)
        if not os.path.isdir(target):
            return _error(f"'{path}' is not a directory")
        show_hidden = _to_bool(show_hidden, True)
        items = []
        truncated = False
        entry_limit = 2000
        scanned = 0
        with os.scandir(target) as scan:
            for entry in scan:
                scanned += 1
                if scanned > entry_limit * 10:
                    truncated = True
                    break
                if not show_hidden and entry.name.startswith("."):
                    continue
                if len(items) >= entry_limit:
                    truncated = True
                    break
                with contextlib.suppress(OSError):
                    is_symlink = entry.is_symlink()
                    stat_info = entry.stat(follow_symlinks=False)
                    is_dir = not is_symlink and stat.S_ISDIR(stat_info.st_mode)
                    items.append({
                        "name": entry.name,
                        "type": "symlink" if is_symlink else ("directory" if is_dir else "file"),
                        "size_bytes": 0 if is_dir else stat_info.st_size,
                        "modified": stat_info.st_mtime,
                        "modified_time": time.ctime(stat_info.st_mtime),
                        "mode": oct(stat_info.st_mode & 0o777),
                    })
        key = str(sort_by or "name").lower()
        if key in ("size", "size_bytes"):
            items.sort(key=lambda i: i["size_bytes"], reverse=True)
        elif key in ("modified", "mtime", "time"):
            items.sort(key=lambda i: i["modified"], reverse=True)
        elif key == "type":
            items.sort(key=lambda i: (i["type"], i["name"]))
        return _ok({"path": target, "count": len(items), "truncated": truncated, "items": items})
    except Exception as exc:
        return _error(f"Error listing directory: {exc}")


def file_stat(filepath: str) -> str:
    """Detailed metadata for a file or directory (size, times, mode, mime type)."""
    try:
        path = _safe_path(filepath, must_exist=True)
        stat = os.stat(path)
        mime, encoding = mimetypes.guess_type(path)
        info = {
            "path": path,
            "name": os.path.basename(path),
            "type": "directory" if os.path.isdir(path) else "file",
            "size_bytes": stat.st_size,
            "size_human": _human_bytes(stat.st_size),
            "mode": oct(stat.st_mode & 0o777),
            "uid": stat.st_uid,
            "gid": stat.st_gid,
            "created": stat.st_ctime,
            "modified": stat.st_mtime,
            "accessed": stat.st_atime,
            "modified_iso": _dt.datetime.fromtimestamp(stat.st_mtime).isoformat(),
            "is_symlink": os.path.islink(path),
            "mime_type": mime or "",
            "encoding": encoding or "",
        }
        if os.path.isfile(path):
            info["binary"] = _looks_binary(path)
            if not info["binary"] and stat.st_size < 5_000_000:
                with open(path, "r", encoding="utf-8", errors="replace") as f:
                    info["line_count"] = sum(1 for _ in f)
        elif os.path.isdir(path):
            with contextlib.suppress(OSError):
                entries_seen = 0
                with os.scandir(path) as directory:
                    for _entry in directory:
                        entries_seen += 1
                        if entries_seen > 10000:
                            break
                info["entry_count"] = min(entries_seen, 10000)
                info["entry_count_truncated"] = entries_seen > 10000
        return _ok(info)
    except Exception as exc:
        return _error(f"file_stat failed: {exc}")


def _human_bytes(num: float) -> str:
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if abs(num) < 1024.0:
            return f"{num:.1f}{unit}" if unit != "B" else f"{int(num)}B"
        num /= 1024.0
    return f"{num:.1f}PB"


def delete_file(filepath: str, recursive: bool = False) -> str:
    """Delete a file (or a directory when recursive=true) inside the sandbox."""
    try:
        path = _safe_path(filepath)
        if not os.path.lexists(path):
            return _error(f"File '{filepath}' does not exist")
        if path == os.path.realpath(SANDBOX_ROOT):
            return _error("Refusing to delete the sandbox root")
        if os.path.isdir(path) and not os.path.islink(path):
            if not _to_bool(recursive, False):
                return _error(f"'{filepath}' is a directory; pass recursive=true to remove it")
            shutil.rmtree(path)
            return _ok({"status": "success", "deleted": filepath, "type": "directory"})
        os.remove(path)
        return _ok({"status": "success", "deleted": filepath, "type": "file"})
    except Exception as exc:
        return _error(f"delete_file failed: {exc}")


def make_directory(path: str) -> str:
    """Create a directory (and any missing parents) inside the sandbox."""
    try:
        target = _safe_path(path)
        os.makedirs(target, exist_ok=True)
        return _ok({"status": "success", "created": target})
    except Exception as exc:
        return _error(str(exc))


def copy_file(source: str, destination: str) -> str:
    """Copy a file or directory tree within the sandbox."""
    try:
        src = _safe_path(source, must_exist=True)
        dst = _safe_path(destination)
        if os.path.isdir(src):
            common = os.path.commonpath((src, dst))
            if common in (src, dst):
                return _error("Directory copies cannot overlap the source and destination paths")
            _archive_manifest(
                src, dst,
                _bounded_int(MAX_ARCHIVE_ENTRIES, 5000, 1, 10000),
                _bounded_int(MAX_ARCHIVE_UNPACKED_BYTES, 256 * 1024 * 1024,
                             1024, 1024 * 1024 * 1024))
            _ensure_parent(dst)
            shutil.copytree(src, dst, dirs_exist_ok=True, symlinks=False)
        else:
            size = os.path.getsize(src)
            if size > MAX_ARCHIVE_UNPACKED_BYTES:
                return _error("Source file exceeds the configured copy size limit")
            _ensure_parent(dst)
            shutil.copy2(src, dst)
        return _ok({"status": "success", "source": src, "destination": dst})
    except Exception as exc:
        return _error(str(exc))


def move_file(source: str, destination: str) -> str:
    """Move or rename a file/directory within the sandbox."""
    try:
        src = _safe_path(source, must_exist=True)
        dst = _safe_path(destination)
        if os.path.isdir(src):
            common = os.path.commonpath((src, dst))
            if common in (src, dst):
                return _error("Directory moves cannot overlap the source and destination paths")
        _ensure_parent(dst)
        cross_device = os.stat(src).st_dev != os.stat(os.path.dirname(dst) or SANDBOX_ROOT).st_dev
        if os.path.isdir(src):
            if cross_device:
                _archive_manifest(
                    src, dst,
                    _bounded_int(MAX_ARCHIVE_ENTRIES, 5000, 1, 10000),
                    _bounded_int(MAX_ARCHIVE_UNPACKED_BYTES, 256 * 1024 * 1024,
                                 1024, 1024 * 1024 * 1024))
        elif cross_device and os.path.getsize(src) > MAX_ARCHIVE_UNPACKED_BYTES:
            return _error("Cross-device file move exceeds the configured copy size limit")
        shutil.move(src, dst)
        return _ok({"status": "success", "source": src, "destination": dst})
    except Exception as exc:
        return _error(str(exc))


def file_checksum(filepath: str, algorithm: str = "sha256") -> str:
    """Compute a file checksum with a validated hashlib algorithm."""
    try:
        path = _safe_path(filepath)
        if not os.path.isfile(path):
            return _error(f"File '{filepath}' not found")
        size_bytes = os.path.getsize(path)
        checksum_limit = _bounded_int(MAX_ARCHIVE_UNPACKED_BYTES, 256 * 1024 * 1024,
                                     1024, 1024 * 1024 * 1024)
        if size_bytes > checksum_limit:
            return _error(f"File exceeds the {checksum_limit} byte checksum limit")
        alg = str(algorithm or "sha256").lower().strip().replace("-", "_")
        if alg not in hashlib.algorithms_available:
            return _error(f"Unsupported algorithm '{algorithm}'",
                          supported=sorted(hashlib.algorithms_guaranteed))
        try:
            hasher = hashlib.new(alg)
        except ValueError as exc:
            return _error(f"Unsupported algorithm '{algorithm}': {exc}")
        with open(path, "rb") as f:
            while chunk := f.read(1024 * 1024):
                hasher.update(chunk)
        digest = hasher.hexdigest() if hasattr(hasher, "hexdigest") else ""
        return _ok({"filepath": filepath, "algorithm": alg, "checksum": digest,
                    "size_bytes": size_bytes})
    except Exception as exc:
        return _error(str(exc))


def search_files(directory: str = ".", pattern: str = "*", max_depth: int = 5,
                 max_results: int = 200, include_dirs: bool = False) -> str:
    """Find files by glob pattern with a bounded depth and result count."""
    try:
        base = _safe_path(directory, must_exist=True)
        if not os.path.isdir(base):
            return _error(f"'{directory}' is not a directory")
        max_depth = _bounded_int(max_depth, 5, 0, 32)
        max_results = _bounded_int(max_results, 200, 1, 2000)
        include_dirs = _to_bool(include_dirs, False)
        pattern = str(pattern or "*")
        if len(pattern) > 1024:
            return _error("pattern exceeds 1,024 characters")
        matches: List[Dict[str, Any]] = []
        truncated = False
        scanned = 0
        scan_limit = 50000
        pending = [(base, 0)]
        while pending and not truncated:
            root, depth = pending.pop()
            child_directories = []
            try:
                with os.scandir(root) as scan:
                    entries = []
                    for entry in scan:
                        scanned += 1
                        if scanned > scan_limit:
                            truncated = True
                            break
                        entries.append(entry)
            except OSError:
                continue
            for entry in sorted(entries, key=lambda item: item.name):
                try:
                    if entry.is_symlink():
                        continue
                    is_dir = entry.is_dir(follow_symlinks=False)
                    info = entry.stat(follow_symlinks=False)
                    if is_dir:
                        if include_dirs and fnmatch.fnmatchcase(entry.name, pattern):
                            matches.append({"path": entry.path, "name": entry.name,
                                            "type": "directory", "size_bytes": 0})
                        if depth < max_depth:
                            child_directories.append((entry.path, depth + 1))
                    elif stat.S_ISREG(info.st_mode) and fnmatch.fnmatchcase(entry.name, pattern):
                        matches.append({"path": entry.path, "name": entry.name,
                                        "type": "file", "size_bytes": info.st_size})
                except OSError:
                    continue
                if len(matches) >= max_results:
                    truncated = True
                    break
            pending.extend(reversed(child_directories))
        return _ok({"directory": base, "pattern": pattern, "count": len(matches),
                    "truncated": truncated, "files": matches})
    except Exception as exc:
        return _error(str(exc))


def search_file_content(directory: str = ".", query: str = "", file_extension: str = "",
                        case_insensitive: bool = False, is_regex: bool = False,
                        max_results: int = 200, max_depth: int = 12,
                        context_chars: int = 240) -> str:
    """Grep-like search with bounded traversal and isolated regex evaluation."""
    try:
        base = _safe_path(directory, must_exist=True)
        query = str(query or "")
        if not query.strip():
            return _error("query must be a non-empty string")
        if len(query) > 2000:
            return _error("query is too long (max 2000 chars)")
        case_insensitive = _to_bool(case_insensitive, False)
        is_regex = _to_bool(is_regex, False)
        max_results = _bounded_int(max_results, 200, 1, 2000)
        max_depth = _bounded_int(max_depth, 12, 0, 32)
        context_chars = _bounded_int(context_chars, 240, 40, 2000)

        if is_regex:
            if _regex_has_nested_repeats(query):
                return _error("Pattern contains nested/ambiguous repetitions that may cause excessive backtracking")
            try:
                re.compile(query, re.I if case_insensitive else 0)
            except re.error as exc:
                return _error(f"Invalid regular expression: {exc}")
        needle = query.lower() if case_insensitive else query

        results: List[Dict[str, Any]] = []
        files_scanned = 0
        files_considered = 0
        bytes_scanned = 0
        scan_byte_limit = 64 * 1024 * 1024
        truncated = False
        regex_timed_out = False
        regex_error = ""
        regex_deadline = time.monotonic() + 20.0 if is_regex else None
        base_depth = base.rstrip(os.sep).count(os.sep)
        ext = ""
        if file_extension:
            ext = file_extension if str(file_extension).startswith(".") else f".{file_extension}"

        entries_scanned = 0
        pending_directories = [(base, 0)]
        while pending_directories and not truncated:
            root, depth = pending_directories.pop()
            child_directories = []
            files = []
            try:
                with os.scandir(root) as scan:
                    for entry in scan:
                        entries_scanned += 1
                        if entries_scanned > 50000:
                            truncated = True
                            break
                        if entry.is_symlink():
                            continue
                        if entry.is_dir(follow_symlinks=False):
                            if (depth < max_depth
                                    and entry.name not in {".git", "node_modules", "__pycache__", ".venv", "venv"}):
                                child_directories.append(entry.path)
                        elif entry.is_file(follow_symlinks=False):
                            files.append(entry.name)
            except OSError:
                continue
            for filename in sorted(files):
                if ext and not filename.endswith(ext):
                    continue
                files_considered += 1
                if files_considered > 10000:
                    truncated = True
                    break
                path = os.path.join(root, filename)
                try:
                    # Never follow a symlink while recursively scanning. A link inside
                    # the sandbox may point at a host secret or a huge external file.
                    if os.path.islink(path):
                        continue
                    file_size = os.path.getsize(path)
                    if file_size > 8 * 1024 * 1024 or _looks_binary(path):
                        continue
                    if bytes_scanned + file_size > scan_byte_limit:
                        truncated = True
                        break
                    bytes_scanned += file_size
                    files_scanned += 1
                    if is_regex:
                        remaining_time = max(0.0, regex_deadline - time.monotonic())
                        if not remaining_time:
                            regex_timed_out = truncated = True
                            break
                        with open(path, "r", encoding="utf-8", errors="ignore") as stream:
                            content = stream.read(8 * 1024 * 1024 + 1)
                        if len(content) > 8 * 1024 * 1024:
                            truncated = True
                            break
                        worker_result = _regex_worker_search(
                            query, content, "i" if case_insensitive else "", max_results - len(results),
                            line_mode=True, context_chars=context_chars,
                            timeout=min(2.0, remaining_time))
                        for match in worker_result.get("matches", []):
                            results.append({"filepath": path, **match})
                        if worker_result.get("truncated") or worker_result.get("output_limited"):
                            truncated = True
                            break
                    else:
                        with open(path, "r", encoding="utf-8", errors="ignore") as stream:
                            for line_number, line in enumerate(stream, 1):
                                line = line[:20000]
                                hit = needle in line.lower() if case_insensitive else needle in line
                                if hit:
                                    results.append({"filepath": path, "line_number": line_number,
                                                    "content": line.strip()[:context_chars]})
                                    if len(results) >= max_results:
                                        truncated = True
                                        break
                except subprocess.TimeoutExpired:
                    regex_timed_out = truncated = True
                    break
                except Exception as exc:
                    if is_regex:
                        regex_error = f"{type(exc).__name__}: {exc}"[:300]
                        truncated = True
                        break
                    continue
                if len(results) >= max_results:
                    truncated = True
                    break
            if not truncated:
                pending_directories.extend((child, depth + 1)
                                           for child in reversed(child_directories))
        payload = {"query": query, "directory": base, "files_scanned": files_scanned,
                   "match_count": len(results), "truncated": truncated, "matches": results}
        if regex_timed_out:
            payload["regex_timed_out"] = True
        if regex_error:
            payload["regex_error"] = regex_error
        return _ok(payload)
    except Exception as exc:
        return _error(str(exc))


def file_tree(path: str = ".", max_depth: int = 4,
              ignore: str = ".git,__pycache__,node_modules,.venv",
              max_entries: int = 1000) -> str:
    """Render an ASCII tree of a directory with correct branch glyphs and bounded output."""
    try:
        base = _safe_path(path, must_exist=True)
        max_depth = _bounded_int(max_depth, 4, 0, 16)
        max_entries = _bounded_int(max_entries, 1000, 10, 20000)
        ignore_set = {p.strip() for p in str(ignore or "").split(",") if p.strip()}
        lines: List[str] = [os.path.basename(base.rstrip(os.sep)) or base]
        state = {"count": 0, "truncated": False, "dirs": 0, "files": 0}

        def walk(current: str, prefix: str = "", depth: int = 0):
            if depth > max_depth or state["truncated"]:
                return
            remaining = max(1, max_entries - state["count"] + 1)
            try:
                with os.scandir(current) as scan:
                    entries = []
                    for entry in scan:
                        if entry.name in ignore_set:
                            continue
                        entries.append(entry.name)
                        if len(entries) >= remaining:
                            break
                entries.sort()
            except (PermissionError, OSError):
                return
            # The scan is deliberately bounded; exceeding the output allowance is
            # signalled by the extra (max_entries + 1) sentinel entry.
            dirs = [name for name in entries if not os.path.islink(os.path.join(current, name))
                    and os.path.isdir(os.path.join(current, name))]
            files = [name for name in entries if name not in dirs]
            ordered = [(name, True) for name in dirs] + [(name, False) for name in files]
            for index, (name, is_dir) in enumerate(ordered):
                if state["count"] >= max_entries:
                    state["truncated"] = True
                    lines.append(f"{prefix}... (output truncated at {max_entries} entries)")
                    return
                state["count"] += 1
                last = index == len(ordered) - 1
                lines.append(f"{prefix}{'└── ' if last else '├── '}{name}{'/' if is_dir else ''}")
                if is_dir:
                    state["dirs"] += 1
                    walk(os.path.join(current, name), prefix + ("    " if last else "│   "), depth + 1)
                else:
                    state["files"] += 1
                if state["truncated"]:
                    return

        walk(base)
        return _ok({"path": base, "directories": state["dirs"], "files": state["files"],
                    "truncated": state["truncated"], "tree": "\n".join(lines)})
    except Exception as exc:
        return _error(str(exc))


def disk_usage(path: str = ".", top_n: int = 15) -> str:
    """Report filesystem usage plus the largest entries under *path*."""
    try:
        base = _safe_path(path, must_exist=True)
        total, used, free = shutil.disk_usage(base)
        top_n = _bounded_int(top_n, 15, 1, 100)
        sizes: List[Tuple[int, str]] = []
        scanned = 0
        entries_scanned = 0
        truncated = False
        pending_directories = [base]
        while pending_directories and not truncated:
            root = pending_directories.pop()
            child_directories = []
            try:
                with os.scandir(root) as scan:
                    for entry in scan:
                        entries_scanned += 1
                        if entries_scanned > 200000:
                            truncated = True
                            break
                        if entry.is_symlink():
                            continue
                        if entry.is_dir(follow_symlinks=False):
                            if entry.name not in {".git", "node_modules", "__pycache__"}:
                                child_directories.append(entry.path)
                            continue
                        if not entry.is_file(follow_symlinks=False):
                            continue
                        scanned += 1
                        with contextlib.suppress(OSError):
                            sizes.append((entry.stat(follow_symlinks=False).st_size, entry.path))
            except OSError:
                continue
            pending_directories.extend(reversed(child_directories))
        sizes.sort(reverse=True)
        return _ok({
            "path": base,
            "filesystem": {"total": _human_bytes(total), "used": _human_bytes(used),
                           "free": _human_bytes(free), "used_percent": round(used / total * 100, 1) if total else 0},
            "files_scanned": min(scanned, 200000),
            "truncated": truncated,
            "total_size": _human_bytes(sum(s for s, _ in sizes)),
            "largest": [{"path": p, "size": _human_bytes(s), "size_bytes": s} for s, p in sizes[:top_n]],
        })
    except Exception as exc:
        return _error(f"disk_usage failed: {exc}")


def _archive_manifest(source: str, destination: str, max_entries: int,
                      max_unpacked_bytes: int) -> Tuple[List[Tuple[str, str, bool, os.stat_result]], int]:
    """Collect a bounded manifest without following links or special files."""
    root = os.path.realpath(source)
    output = os.path.abspath(destination)
    pending = [root]
    entries: List[Tuple[str, str, bool, os.stat_result]] = []
    total_bytes = 0
    while pending:
        current = pending.pop()
        try:
            with os.scandir(current) as scan:
                for item in scan:
                    full_path = item.path
                    if os.path.abspath(full_path) == output:
                        continue
                    if item.is_symlink():
                        raise ValueError(f"Refusing to archive symlink: {full_path}")
                    info = item.stat(follow_symlinks=False)
                    is_directory = stat.S_ISDIR(info.st_mode)
                    if not is_directory and not stat.S_ISREG(info.st_mode):
                        raise ValueError(f"Refusing to archive non-regular file: {full_path}")
                    real_path = os.path.realpath(full_path)
                    if os.path.commonpath((root, real_path)) != root:
                        raise ValueError(f"Archive entry escapes source directory: {full_path}")
                    relative = os.path.relpath(full_path, root).replace(os.sep, "/")
                    normalized = relative.replace("\\", "/")
                    drive, _ = ntpath.splitdrive(normalized)
                    if drive or normalized.startswith("/") or any(
                            part in ("", ".", "..") for part in normalized.split("/")):
                        raise ValueError(f"Unsafe archive member path: {relative}")
                    if len(entries) >= max_entries:
                        raise ValueError(f"Source has more than {max_entries} archive entries")
                    entries.append((full_path, normalized, is_directory, info))
                    if is_directory:
                        pending.append(full_path)
                    else:
                        total_bytes += max(0, info.st_size)
                        if total_bytes > max_unpacked_bytes:
                            raise ValueError(f"Source exceeds the {max_unpacked_bytes} byte archive limit")
        except OSError as exc:
            raise ValueError(f"Could not scan archive source '{current}': {exc}") from exc
    entries.sort(key=lambda item: item[1])
    return entries, total_bytes


def _open_regular_nofollow(path: str):
    """Open a regular file without following symlinks where the OS supports it."""
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_BINARY", 0)
    fd = os.open(path, flags)
    try:
        info = os.fstat(fd)
        if not stat.S_ISREG(info.st_mode):
            raise ValueError(f"Archive source is no longer a regular file: {path}")
        return os.fdopen(fd, "rb"), info
    except Exception:
        os.close(fd)
        raise


def _write_archive_manifest(entries: Sequence[Tuple[str, str, bool, os.stat_result]],
                            destination: str, archive_format: str,
                            max_unpacked_bytes: int) -> int:
    """Write a previously checked manifest to zip or tar without recursive walks."""
    total_bytes = 0
    if archive_format == "zip":
        with zipfile.ZipFile(destination, "w", compression=zipfile.ZIP_DEFLATED,
                             allowZip64=True) as archive:
            for full_path, name, is_directory, original_stat in entries:
                timestamp = max(315532800, min(original_stat.st_mtime, 4354819198))
                date_time = time.localtime(timestamp)[:6]
                member_name = name.rstrip("/") + "/" if is_directory else name
                info = zipfile.ZipInfo(member_name, date_time)
                info.compress_type = zipfile.ZIP_DEFLATED
                info.external_attr = (original_stat.st_mode & 0xFFFF) << 16
                if is_directory:
                    info.external_attr |= 0x10
                    archive.writestr(info, b"")
                    continue
                source, _ = _open_regular_nofollow(full_path)
                with source, archive.open(info, "w") as target:
                    while True:
                        chunk = source.read(65536)
                        if not chunk:
                            break
                        total_bytes += len(chunk)
                        if total_bytes > max_unpacked_bytes:
                            raise ValueError(f"Source grew beyond the {max_unpacked_bytes} byte archive limit")
                        target.write(chunk)
        return total_bytes

    tar_modes = {"tar": "w", "gztar": "w:gz", "bztar": "w:bz2", "xztar": "w:xz"}
    with tarfile.open(destination, mode=tar_modes[archive_format]) as archive:
        for full_path, name, is_directory, original_stat in entries:
            info = tarfile.TarInfo(name.rstrip("/") + ("/" if is_directory else ""))
            info.mode = stat.S_IMODE(original_stat.st_mode)
            info.mtime = original_stat.st_mtime
            if is_directory:
                info.type = tarfile.DIRTYPE
                archive.addfile(info)
                continue
            source, current_stat = _open_regular_nofollow(full_path)
            with source:
                info.size = current_stat.st_size
                total_bytes += info.size
                if total_bytes > max_unpacked_bytes:
                    raise ValueError(f"Source grew beyond the {max_unpacked_bytes} byte archive limit")
                archive.addfile(info, source)
    return total_bytes


def compress_decompress_archive(archive_path: str, action: str = "extract",
                                target_directory: str = ".", format: str = "",
                                max_entries: int = 5000,
                                max_unpacked_bytes: int = 268435456) -> str:
    """Create or extract zip/tar archives with traversal and decompression-bomb limits."""
    try:
        action = str(action or "extract").lower().strip()
        configured_entries = _bounded_int(MAX_ARCHIVE_ENTRIES, 5000, 1, 10000)
        configured_bytes = _bounded_int(MAX_ARCHIVE_UNPACKED_BYTES, 268435456,
                                        1024, 1024 * 1024 * 1024)
        entry_limit = _bounded_int(max_entries, min(5000, configured_entries),
                                   1, configured_entries)
        byte_limit = _bounded_int(max_unpacked_bytes, min(268435456, configured_bytes),
                                  1024, configured_bytes)
        if action in ("extract", "unpack", "decompress"):
            arc = _safe_path(archive_path, must_exist=True)
            tgt = _safe_path(target_directory)
            os.makedirs(tgt, exist_ok=True)
            extracted = _safe_extract(arc, tgt, entry_limit, byte_limit)
            return _ok({"status": "success", "action": "extracted", "archive": arc,
                        "target": tgt, "members": len(extracted),
                        "max_entries": entry_limit, "max_unpacked_bytes": byte_limit,
                        "extracted": extracted[:200]})
        if action in ("create", "compress", "pack"):
            source = _safe_path(target_directory, must_exist=True)
            requested = str(format or "").lower().strip()
            lowered = str(archive_path).lower()
            if not requested:
                if lowered.endswith(".zip"):
                    requested = "zip"
                elif lowered.endswith((".tar.gz", ".tgz")):
                    requested = "gztar"
                elif lowered.endswith((".tar.bz2", ".tbz2")):
                    requested = "bztar"
                elif lowered.endswith((".tar.xz", ".txz")):
                    requested = "xztar"
                elif lowered.endswith(".tar"):
                    requested = "tar"
                else:
                    requested = "zip"
            suffixes = {"zip": ".zip", "gztar": ".tar.gz", "bztar": ".tar.bz2",
                        "xztar": ".tar.xz", "tar": ".tar"}
            accepted_suffixes = {"zip": (".zip",), "gztar": (".tar.gz", ".tgz"),
                                 "bztar": (".tar.bz2", ".tbz2"),
                                 "xztar": (".tar.xz", ".txz"), "tar": (".tar",)}
            if requested not in suffixes:
                return _error(f"Unsupported archive format '{requested}'", supported=sorted(suffixes))
            if not os.path.isdir(source):
                return _error("target_directory must be a directory when creating an archive")
            out_path = _safe_path(archive_path)
            suffix = suffixes[requested]
            if out_path.lower().endswith(accepted_suffixes[requested]):
                created = out_path
            else:
                created = _safe_path(out_path + suffixes[requested])
            if os.path.isdir(created):
                return _error("Archive destination is a directory")
            entries, source_bytes = _archive_manifest(source, created, entry_limit, byte_limit)
            _ensure_parent(created)
            fd, temporary = tempfile.mkstemp(prefix=".mts-archive-", suffix=suffix,
                                             dir=os.path.dirname(created) or SANDBOX_ROOT)
            os.close(fd)
            try:
                actual_bytes = _write_archive_manifest(entries, temporary, requested, byte_limit)
                os.replace(temporary, created)
            except Exception:
                with contextlib.suppress(OSError):
                    os.unlink(temporary)
                raise
            return _ok({"status": "success", "action": "created", "archive": created,
                        "format": requested, "entries": len(entries),
                        "source_bytes": max(source_bytes, actual_bytes),
                        "size_bytes": os.path.getsize(created)})
        return _error("action must be 'extract' or 'create'")
    except Exception as exc:
        return _error(f"compress_decompress_archive failed: {exc}")


def _preflight_zip(archive: str, max_entries: int,
                   max_directory_bytes: int = MAX_FILE_SCAN_BYTES) -> int:
    """Check ZIP directory bounds before ZipFile materializes every member record."""
    endrec_reader = getattr(zipfile, "_EndRecData", None)
    if endrec_reader is None:
        raise RuntimeError("ZIP archive preflight is unavailable in this Python version")
    with open(archive, "rb") as handle:
        endrec = endrec_reader(handle)
    if not endrec:
        raise ValueError("Invalid ZIP end-of-directory record")
    entry_count = int(endrec[zipfile._ECD_ENTRIES_TOTAL])
    directory_size = int(endrec[zipfile._ECD_SIZE])
    if entry_count > max_entries:
        raise ValueError(f"Archive has more than {max_entries} entries")
    if directory_size > max_directory_bytes:
        raise ValueError("ZIP central directory exceeds the inspection size limit")
    return entry_count


def _safe_extract(archive: str, target: str, max_entries: int = 5000,
                  max_unpacked_bytes: int = 268435456) -> List[str]:
    """Extract only regular files/dirs, rejecting traversal, links and archive bombs."""
    target_root = os.path.realpath(target)
    max_entries = _bounded_int(max_entries, 5000, 1, 10000)
    max_unpacked_bytes = _bounded_int(max_unpacked_bytes, 268435456, 1024, 1024 * 1024 * 1024)
    archive_size_limit = min(1024 * 1024 * 1024, max_unpacked_bytes + 64 * 1024 * 1024)
    if os.path.getsize(archive) > archive_size_limit:
        raise ValueError(f"Archive exceeds the {archive_size_limit} byte inspection limit")

    def _check(name: str) -> str:
        normalized = str(name or "").replace("\\", "/")
        drive, _ = ntpath.splitdrive(normalized)
        if drive or normalized.startswith("/") or any(part == ".." for part in normalized.split("/")):
            raise ValueError(f"Blocked path traversal in archive member: {name}")
        destination = os.path.realpath(os.path.join(target_root, *normalized.split("/")))
        try:
            if os.path.commonpath((target_root, destination)) != target_root:
                raise ValueError
        except ValueError:
            raise ValueError(f"Blocked path traversal in archive member: {name}")
        return destination

    if zipfile.is_zipfile(archive):
        _preflight_zip(archive, max_entries)
        with zipfile.ZipFile(archive) as zf:
            infos = zf.infolist()
            if len(infos) > max_entries:
                raise ValueError(f"Archive has {len(infos)} entries; limit is {max_entries}")
            expanded = 0
            for info in infos:
                _check(info.filename)
                mode = (info.external_attr >> 16) & 0xFFFF
                if stat.S_ISLNK(mode):
                    raise ValueError(f"Blocked symlink member in archive: {info.filename}")
                expanded += 0 if info.is_dir() else max(0, info.file_size)
                if expanded > max_unpacked_bytes:
                    raise ValueError(f"Archive expands beyond the {max_unpacked_bytes} byte limit")
            extracted = []
            for info in infos:
                zf.extract(info, target_root)
                if not info.is_dir():
                    extracted.append(info.filename)
            return extracted

    if tarfile.is_tarfile(archive):
        with tarfile.open(archive) as tf:
            # Validate incrementally: getmembers() would first load an unbounded
            # attacker-controlled member table before enforcing max_entries.
            members = []
            expanded = 0
            for member in tf:
                members.append(member)
                if len(members) > max_entries:
                    raise ValueError(f"Archive has more than {max_entries} entries")
                _check(member.name)
                if member.issym() or member.islnk():
                    raise ValueError(f"Blocked link member in archive: {member.name}")
                if member.isdev() or member.isfifo() or not (member.isdir() or member.isfile()):
                    raise ValueError(f"Blocked special member in archive: {member.name}")
                if member.isfile():
                    expanded += max(0, member.size)
                    if expanded > max_unpacked_bytes:
                        raise ValueError(f"Archive expands beyond the {max_unpacked_bytes} byte limit")
            if hasattr(tarfile, "data_filter"):
                tf.extractall(target_root, members=members, filter="data")
            else:  # pragma: no cover - older Python
                tf.extractall(target_root, members=members)
            return [member.name for member in members if member.isfile()]

    raise ValueError("Unsupported archive format; only zip and tar archives are accepted")


def archive_list(archive_path: str, max_entries: int = 500) -> str:
    """List the contents of a zip/tar archive without extracting it."""
    try:
        arc = _safe_path(archive_path, must_exist=True)
        limit = _bounded_int(max_entries, 500, 1, 10000)
        entries: List[Dict[str, Any]] = []
        kind = "unknown"
        truncated = False
        if zipfile.is_zipfile(arc):
            kind = "zip"
            total_entries = _preflight_zip(arc, _bounded_int(MAX_ARCHIVE_ENTRIES, 5000, 1, 10000))
            with zipfile.ZipFile(arc) as zf:
                for info in zf.infolist()[:limit]:
                    entries.append({"name": info.filename, "size": info.file_size,
                                    "compressed": info.compress_size,
                                    "is_dir": info.is_dir(),
                                    "modified": "%04d-%02d-%02d %02d:%02d" % info.date_time[:5]})
            truncated = total_entries > len(entries)
        elif tarfile.is_tarfile(arc):
            kind = "tar"
            expanded_bytes = 0
            expanded_limit = _bounded_int(MAX_ARCHIVE_UNPACKED_BYTES, 268435456,
                                          1024, 1024 * 1024 * 1024)
            with tarfile.open(arc) as tf:
                for member in itertools.islice(tf, limit + 1):
                    if len(entries) >= limit:
                        truncated = True
                        break
                    if member.isfile():
                        expanded_bytes += max(0, member.size)
                        if expanded_bytes > expanded_limit:
                            raise ValueError(f"Archive exceeds the {expanded_limit} byte inspection limit")
                    entries.append({"name": member.name, "size": member.size,
                                    "is_dir": member.isdir(), "mode": oct(member.mode),
                                    "modified": member.mtime})
        else:
            return _error("Not a recognised zip or tar archive")
        suspicious = [e["name"] for e in entries
                      if e["name"].startswith("/") or ".." in e["name"].split("/")]
        return _ok({"archive": arc, "type": kind, "count": len(entries),
                    "truncated": truncated, "suspicious_paths": suspicious[:20], "entries": entries})
    except Exception as exc:
        return _error(f"archive_list failed: {exc}")


def apply_patch(filepath: str, patch: str, mode: str = "auto") -> str:
    """Apply a unified diff to a file. mode=auto|patch|replace (replace overwrites)."""
    try:
        path = _safe_path(filepath)
        mode = str(mode or "auto").lower().strip()
        patch_text = str(patch or "")
        if len(patch_text.encode("utf-8")) > MCP_MAX_REQUEST_BYTES:
            return _error("Patch exceeds configured size limit")
        if not os.path.exists(path) and mode != "replace":
            return _error(f"File not found: {filepath}")
        if os.path.isfile(path) and os.path.getsize(path) > MCP_MAX_REQUEST_BYTES:
            return _error("Target file exceeds configured patch limit")
        stripped = patch_text.strip()
        looks_like_diff = stripped.startswith(("---", "diff ", "Index:", "@@"))
        if mode == "replace" or (mode == "auto" and not looks_like_diff and not os.path.exists(path)):
            _atomic_write(path, patch_text)
            return _ok({"status": "success", "mode": "full_replace", "filepath": filepath,
                        "bytes_written": len(patch_text.encode("utf-8"))})
        if not looks_like_diff:
            return _error("Input does not look like a unified diff. "
                          "Pass mode='replace' to overwrite the file instead.",
                          hint="v3.8 silently overwrote the file here; that behaviour is now opt-in.")
        if not shutil.which("patch"):
            return _error("The 'patch' utility is not installed on this system")
        patch_bin = shutil.which("patch")
        clean_env = {"PATH": os.getenv("PATH", "/usr/bin:/bin"), "HOME": SANDBOX_ROOT,
                     "LANG": "C.UTF-8", "LC_ALL": "C.UTF-8", "POSIXLY_CORRECT": "1"}
        backup_fd, backup = tempfile.mkstemp(prefix=".mts-patch-", suffix=".bak",
                                             dir=os.path.dirname(path) or SANDBOX_ROOT)
        os.close(backup_fd)
        try:
            shutil.copy2(path, backup)
        except Exception:
            with contextlib.suppress(OSError):
                os.unlink(backup)
            raise
        preserve_backup = False
        try:
            try:
                res = _run_subprocess([patch_bin, "-p0", "--forward", "--batch", path],
                                      timeout=20, cwd=SANDBOX_ROOT, env=clean_env,
                                      cpu_seconds=20, address_space_mb=512,
                                      file_size_mb=16, stdin_text=patch_text, max_output_bytes=50000)
            except Exception as exc:
                try:
                    os.replace(backup, path)
                    backup = ""
                except OSError as restore_exc:
                    preserve_backup = True
                    raise RuntimeError(f"Patch failed and the original could not be restored; "
                                       f"backup retained at {backup}: {restore_exc}") from exc
                raise
            if res["exit_code"] == 0:
                with contextlib.suppress(OSError):
                    os.unlink(backup)
                backup = ""
                return _ok({"status": "success", "mode": "patch", "filepath": filepath,
                            "stdout": res["stdout"][-2000:],
                            "truncated": res["stdout_truncated"] or res["stderr_truncated"]})
            try:
                os.replace(backup, path)
                backup = ""
            except OSError as restore_exc:
                preserve_backup = True
                raise RuntimeError(f"Patch failed and the original could not be restored; "
                                   f"backup retained at {backup}: {restore_exc}") from restore_exc
            return _error("patch failed and the file was restored", filepath=filepath,
                          stderr=(res["stderr"] or res["stdout"])[-2000:],
                          exit_code=res["exit_code"],
                          truncated=res["stdout_truncated"] or res["stderr_truncated"])
        finally:
            if backup and not preserve_backup:
                with contextlib.suppress(OSError):
                    os.unlink(backup)
    except Exception as exc:
        return _error(f"apply_patch failed: {exc}")


# ---------------------------------------------------------------------------
# Code Linting & Execution
# ---------------------------------------------------------------------------
def lint_code(language: str, code: str = "", filepath: str = "") -> str:
    """Syntax-check Python, JavaScript, JSON, YAML or shell source."""
    tmp_path = ""
    try:
        target_code = str(code or "")
        if filepath:
            source_path = _safe_path(filepath, must_exist=True)
            if os.path.getsize(source_path) > MCP_MAX_REQUEST_BYTES:
                return _error("Source file exceeds configured lint limit")
            with open(source_path, "r", encoding="utf-8", errors="replace") as f:
                target_code = f.read(MCP_MAX_REQUEST_BYTES + 1)
        if len(target_code.encode("utf-8")) > MCP_MAX_REQUEST_BYTES:
            return _error("Source exceeds configured lint limit")
        if not target_code.strip():
            return _error("Provide code= or filepath= with content to lint")
        lang = str(language or "").lower().strip()

        if lang in ("python", "py", "python3"):
            if len(target_code.encode("utf-8")) > 1024 * 1024:
                return _error("Python source exceeds the 1 MiB linting limit")
            try:
                tree = ast.parse(target_code)
            except SyntaxError as exc:
                return _json_result({"ok": False, "status": "syntax_error", "language": "python",
                                     "line": exc.lineno, "offset": exc.offset,
                                     "text": exc.text, "error": str(exc)})
            except (RecursionError, MemoryError, ValueError) as exc:
                return _error(f"Python source is too complex to lint safely ({type(exc).__name__})")
            pending = [tree]
            node_count = 0
            while pending:
                node = pending.pop()
                node_count += 1
                if node_count > 100000:
                    return _error("Python source exceeds the 100,000 AST node linting limit")
                pending.extend(ast.iter_child_nodes(node))
            warnings = []
            assigned = {n.id for n in ast.walk(tree) if isinstance(n, ast.Name) and isinstance(n.ctx, ast.Store)}
            imported = set()
            for node in ast.walk(tree):
                if isinstance(node, ast.Import):
                    imported.update(alias.asname or alias.name.split(".")[0] for alias in node.names)
                elif isinstance(node, ast.ImportFrom):
                    imported.update(alias.asname or alias.name for alias in node.names)
                elif isinstance(node, ast.ExceptHandler) and node.type is None:
                    warnings.append(f"line {node.lineno}: bare 'except:' catches everything")
                elif isinstance(node, ast.Compare):
                    for op, comparator in zip(node.ops, node.comparators):
                        if isinstance(op, (ast.Is, ast.IsNot)) and isinstance(comparator, ast.Constant) \
                                and isinstance(comparator.value, (int, str, float)):
                            warnings.append(f"line {node.lineno}: 'is' comparison with a literal")
            used = {n.id for n in ast.walk(tree) if isinstance(n, ast.Name) and isinstance(n.ctx, ast.Load)}
            used |= {n.value.id for n in ast.walk(tree)
                     if isinstance(n, ast.Attribute) and isinstance(n.value, ast.Name)}
            for name in sorted(imported - used - assigned):
                warnings.append(f"unused import: {name}")
            result = {"ok": True, "status": "ok", "language": "python",
                      "message": "Syntax OK", "warnings": warnings[:50],
                      "functions": [n.name for n in ast.walk(tree)
                                    if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))][:200],
                      "classes": [n.name for n in ast.walk(tree) if isinstance(n, ast.ClassDef)][:200]}
            with contextlib.suppress(Exception):
                compile(target_code, "<lint>", "exec")
                result["compiles"] = True
            return _json_result(result)

        if lang in ("javascript", "js", "node", "mjs"):
            node = shutil.which("node")
            if not node:
                return _error("Node.js is not installed; cannot lint JavaScript")
            with temporary_file(".js", delete=False) as tf:
                tf.write(target_code)
                tf.flush()
                tmp_path = tf.name
            clean_env = {"PATH": os.getenv("PATH", "/usr/bin:/bin"), "HOME": SANDBOX_ROOT,
                         "LANG": "C.UTF-8", "NODE_NO_WARNINGS": "1"}
            res = _run_subprocess([node, "--check", tmp_path], timeout=15,
                                  cwd=SANDBOX_ROOT, env=clean_env, cpu_seconds=15,
                                  address_space_mb=1024, file_size_mb=1, max_output_bytes=50000)
            if res["exit_code"] == 0:
                return _ok({"status": "ok", "language": "javascript", "message": "Syntax OK",
                            "truncated": res["stderr_truncated"]})
            return _json_result({"ok": False, "status": "syntax_error", "language": "javascript",
                                 "error": res["stderr"].strip(), "truncated": res["stderr_truncated"]})

        if lang == "json":
            try:
                parsed = json.loads(target_code)
            except json.JSONDecodeError as exc:
                return _json_result({"ok": False, "status": "syntax_error", "language": "json",
                                     "line": exc.lineno, "column": exc.colno, "error": str(exc)})
            return _ok({"status": "ok", "language": "json", "message": "Valid JSON",
                        "root_type": type(parsed).__name__,
                        "keys": list(parsed)[:50] if isinstance(parsed, dict) else None})

        if lang in ("yaml", "yml"):
            if not HAS_YAML:
                return _error("PyYAML is not installed; cannot lint YAML")
            try:
                _yaml.safe_load(target_code)
            except Exception as exc:
                return _json_result({"ok": False, "status": "syntax_error", "language": "yaml",
                                     "error": str(exc)})
            return _ok({"status": "ok", "language": "yaml", "message": "Valid YAML"})

        if lang in ("bash", "sh", "shell"):
            bash = shutil.which("bash")
            if not bash:
                return _error("bash is not installed; cannot lint shell scripts")
            # `bash -n` only parses - it never executes - so no command allow-list is needed.
            clean_env = {"PATH": os.getenv("PATH", "/usr/bin:/bin"), "HOME": SANDBOX_ROOT,
                         "LANG": "C.UTF-8"}
            res = _run_subprocess([bash, "-n"], timeout=10, cwd=SANDBOX_ROOT,
                                  env=clean_env, stdin_text=target_code,
                                  cpu_seconds=10, address_space_mb=512,
                                  file_size_mb=1, max_output_bytes=50000)
            warnings = []
            if re.search(r"\brm\s+-rf\s+/(?:\s|$)", target_code):
                warnings.append("destructive 'rm -rf /' detected")
            if re.search(r"\$\{?\w+\}?\s*\|\s*(?:bash|sh)\b", target_code):
                warnings.append("piping a variable into a shell interpreter")
            if res["exit_code"] == 0:
                return _ok({"status": "ok", "language": "bash", "message": "Syntax OK",
                            "warnings": warnings, "truncated": res["stderr_truncated"]})
            return _json_result({"ok": False, "status": "syntax_error", "language": "bash",
                                 "error": res["stderr"].strip(), "warnings": warnings,
                                 "truncated": res["stderr_truncated"]})

        return _error(f"Unsupported language: {language}",
                      supported=["python", "javascript", "json", "yaml", "bash"])
    except subprocess.TimeoutExpired:
        return _error("Linting timed out")
    except Exception as exc:
        return _error(f"Linting failed: {exc}")
    finally:
        if tmp_path:
            with contextlib.suppress(Exception):
                os.unlink(tmp_path)


def _rlimit_preexec(cpu_seconds: int = 10, address_space_mb: int = 8192, file_size_mb: int = 256):
    """Return a preexec_fn applying resource limits to a child process (POSIX only)."""
    if not HAS_RESOURCE:
        return None

    def _apply():  # pragma: no cover - runs in the child process
        with contextlib.suppress(Exception):
            _resource.setrlimit(_resource.RLIMIT_CPU, (cpu_seconds, cpu_seconds + 2))
        with contextlib.suppress(Exception):
            limit = address_space_mb * 1024 * 1024
            _resource.setrlimit(_resource.RLIMIT_AS, (limit, limit))
        with contextlib.suppress(Exception):
            limit = file_size_mb * 1024 * 1024
            _resource.setrlimit(_resource.RLIMIT_FSIZE, (limit, limit))
        with contextlib.suppress(Exception):
            _resource.setrlimit(_resource.RLIMIT_CORE, (0, 0))
    return _apply


def _run_subprocess(cmd: List[str], timeout: int, cwd: Optional[str] = None,
                    env: Optional[Dict[str, str]] = None, cpu_seconds: Optional[int] = None,
                    stdin_text: Optional[str] = None, address_space_mb: int = 8192,
                    file_size_mb: int = 256, max_output_bytes: int = 1_000_000,
                    stdout_mode: str = "tail") -> Dict[str, Any]:
    """Run without a shell, cap captured output, and kill the whole process group on timeout."""
    started = time.monotonic()
    output_limit = max(0, int(max_output_bytes))
    proc = subprocess.Popen(
        cmd,
        cwd=cwd or SANDBOX_ROOT,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        stdin=subprocess.PIPE if stdin_text is not None else subprocess.DEVNULL,
        bufsize=0,
        env=env,
        preexec_fn=_rlimit_preexec(cpu_seconds or timeout + 5, address_space_mb, file_size_mb),
        start_new_session=(os.name == "posix"),
        shell=False,
    )
    buffers = {"stdout": bytearray(), "stderr": bytearray()}
    totals = {"stdout": 0, "stderr": 0}

    def drain(pipe, name):
        try:
            while True:
                chunk = pipe.read(8192)
                if not chunk:
                    break
                totals[name] += len(chunk)
                if output_limit:
                    if name == "stdout" and stdout_mode == "head":
                        remaining = output_limit - len(buffers[name])
                        if remaining > 0:
                            buffers[name].extend(chunk[:remaining])
                    elif len(chunk) >= output_limit:
                        buffers[name][:] = chunk[-output_limit:]
                    else:
                        buffers[name].extend(chunk)
                        excess = len(buffers[name]) - output_limit
                        if excess > 0:
                            del buffers[name][:excess]
        except (OSError, ValueError):
            pass
        finally:
            with contextlib.suppress(Exception):
                pipe.close()

    readers = [threading.Thread(target=drain, args=(proc.stdout, "stdout"), daemon=True),
               threading.Thread(target=drain, args=(proc.stderr, "stderr"), daemon=True)]
    for reader in readers:
        reader.start()

    writer = None
    if stdin_text is not None:
        input_bytes = str(stdin_text).encode("utf-8")

        def write_stdin():
            try:
                view = memoryview(input_bytes)
                while view:
                    written = proc.stdin.write(view[:65536])
                    if not written:
                        break
                    view = view[written:]
            except (BrokenPipeError, OSError, ValueError):
                pass
            finally:
                with contextlib.suppress(Exception):
                    proc.stdin.close()

        writer = threading.Thread(target=write_stdin, daemon=True)
        writer.start()

    deadline = time.monotonic() + max(0.0, float(timeout))
    timed_out = False

    def kill_process_group():
        with contextlib.suppress(ProcessLookupError, PermissionError, OSError):
            if os.name == "posix":
                os.killpg(proc.pid, signal.SIGKILL)
            else:  # pragma: no cover - Windows fallback
                proc.kill()

    try:
        proc.wait(timeout=max(0.0, deadline - time.monotonic()))
    except subprocess.TimeoutExpired:
        timed_out = True
        kill_process_group()
        with contextlib.suppress(subprocess.TimeoutExpired):
            proc.wait(timeout=1.0)

    remaining = max(0.0, deadline - time.monotonic())
    if writer is not None:
        writer.join(timeout=remaining)
    for reader in readers:
        reader.join(timeout=max(0.0, deadline - time.monotonic()))
    if (writer is not None and writer.is_alive()) or any(reader.is_alive() for reader in readers):
        # A child may exit while a grandchild still holds an inherited pipe open.
        # Do not let those descriptors make a timed operation wait forever.
        timed_out = True
        kill_process_group()
        with contextlib.suppress(subprocess.TimeoutExpired):
            proc.wait(timeout=1.0)
        if writer is not None:
            writer.join(timeout=1.0)
        for reader in readers:
            reader.join(timeout=1.0)

    stdout = bytes(buffers["stdout"]).decode("utf-8", "replace")
    stderr = bytes(buffers["stderr"]).decode("utf-8", "replace")
    if timed_out:
        raise subprocess.TimeoutExpired(cmd, timeout, output=stdout, stderr=stderr)
    return {"stdout": stdout, "stderr": stderr, "exit_code": proc.returncode,
            "stdout_truncated": totals["stdout"] > output_limit,
            "stderr_truncated": totals["stderr"] > output_limit,
            "execution_time_sec": round(time.monotonic() - started, 3)}


def execute_python_code(code: str, timeout_seconds: int = 30) -> str:
    """Run unrestricted Python in a resource-limited subprocess (dangerous opt-in required)."""
    if not ENABLE_DANGEROUS:
        return _error("Dangerous tool execution disabled. Set MCP_ENABLE_DANGEROUS=true.")
    tmp = ""
    try:
        source = str(code or "")
        if len(source.encode("utf-8")) > MCP_MAX_REQUEST_BYTES:
            return _error("code exceeds configured request limit")
        timeout = _bounded_int(timeout_seconds, 30, 1, 300)
        with temporary_file(".py", delete=False) as tf:
            tf.write(source)
            tf.flush()
            tmp = tf.name
        clean_env = {"PATH": os.getenv("PATH", "/usr/bin:/bin"), "HOME": SANDBOX_ROOT,
                     "LANG": "C.UTF-8", "PYTHONIOENCODING": "utf-8",
                     "PYTHONDONTWRITEBYTECODE": "1"}
        result = _run_subprocess([sys.executable or "python3", tmp], timeout,
                                 cwd=SANDBOX_ROOT, env=clean_env, cpu_seconds=timeout,
                                 address_space_mb=2048, file_size_mb=64,
                                 max_output_bytes=100000)
        result["ok"] = result["exit_code"] == 0
        result["stdout"] = result["stdout"][-100000:]
        result["stderr"] = result["stderr"][-20000:]
        return _json_result(result)
    except subprocess.TimeoutExpired:
        return _error(f"Timed out after {timeout_seconds}s")
    except Exception as exc:
        return _error(str(exc))
    finally:
        if tmp:
            with contextlib.suppress(Exception):
                os.unlink(tmp)


def execute_javascript_code(code: str, timeout_seconds: int = 30) -> str:
    """Run unrestricted JavaScript in a resource-limited subprocess (dangerous opt-in required)."""
    if not ENABLE_DANGEROUS:
        return _error("Dangerous tool execution disabled. Set MCP_ENABLE_DANGEROUS=true.")
    node = shutil.which("node")
    if not node:
        return _error("Node.js not installed")
    tmp = ""
    try:
        source = str(code or "")
        if len(source.encode("utf-8")) > MCP_MAX_REQUEST_BYTES:
            return _error("code exceeds configured request limit")
        timeout = _bounded_int(timeout_seconds, 30, 1, 300)
        with temporary_file(".js", delete=False) as tf:
            tf.write(source)
            tf.flush()
            tmp = tf.name
        clean_env = {"PATH": os.getenv("PATH", "/usr/bin:/bin"), "HOME": SANDBOX_ROOT,
                     "LANG": "C.UTF-8", "NODE_NO_WARNINGS": "1"}
        result = _run_subprocess([node, tmp], timeout, cwd=SANDBOX_ROOT, env=clean_env,
                                 cpu_seconds=timeout, address_space_mb=4096, file_size_mb=64,
                                 max_output_bytes=100000)
        result["ok"] = result["exit_code"] == 0
        result["stdout"] = result["stdout"][-100000:]
        result["stderr"] = result["stderr"][-20000:]
        return _json_result(result)
    except subprocess.TimeoutExpired:
        return _error(f"Timed out after {timeout_seconds}s")
    except Exception as exc:
        return _error(str(exc))
    finally:
        if tmp:
            with contextlib.suppress(Exception):
                os.unlink(tmp)


_SHELL_METACHARS = (";", "&", "|", "`", "$", ">", "<", "\n", "\r", "(", ")", "{", "}", "\\", "!", "\x00")
_ARG_DENY_PATTERNS = [
    (re.compile(r"\bsystem\s*\(", re.I), "awk/sed system() calls are blocked"),
    (re.compile(r"\bexec\s*\(", re.I), "exec() in a script argument is blocked"),
    (re.compile(r"\|\s*(?:sh|bash|zsh|python\d?)\b", re.I), "piping into an interpreter is blocked"),
    (re.compile(r"(?<![A-Za-z0-9_])[0-9]*e[0-9]*\b.*/dev/"), "device access is blocked"),
]


_FIND_DANGEROUS_ACTIONS = {"-delete", "-exec", "-execdir", "-ok", "-okdir", "-fprint", "-fprint0",
                           "-fprintf", "-fls"}


def _safe_sed_substitution(script: str) -> bool:
    """Allow only sed's non-writing substitution form; block e/w/r script commands."""
    if len(script) < 4 or script[0] != "s" or script[1].isalnum() or script[1].isspace():
        return False
    delimiter = script[1]
    delimiters = []
    escaped = False
    for index, char in enumerate(script[2:], 2):
        if escaped:
            escaped = False
        elif char == "\\":
            escaped = True
        elif char == delimiter:
            delimiters.append(index)
            if len(delimiters) == 2:
                break
    if len(delimiters) != 2:
        return False
    return bool(re.fullmatch(r"[gipIM0-9]*", script[delimiters[-1] + 1:]))


def _validate_bash_path_arguments(tokens: Sequence[str], program: str) -> Optional[str]:
    """Reject paths outside the sandbox, including paths reached through symlinks."""
    options_ended = False
    root = os.path.realpath(SANDBOX_ROOT)
    for token in tokens[1:]:
        if token == "--" and not options_ended:
            options_ended = True
            continue
        candidates: List[str] = []
        if options_ended or not token.startswith("-") or token == "-":
            candidates.append(token)
        elif "=" in token:
            candidates.append(token.split("=", 1)[1])
        else:
            # Attached option values can themselves be paths, e.g. -I/etc/passwd.
            match = re.match(r"^-[A-Za-z]+(.+)$", token)
            if match:
                candidates.append(match.group(1))
        for candidate in candidates:
            if candidate in ("", "-"):
                continue
            try:
                resolved = _safe_path(candidate)
            except ValueError:
                return f"Argument '{candidate}' points outside the sandbox root"
            if program in _DANGEROUS_BASH_CMDS and resolved == root:
                return "Refusing to run a destructive command against the sandbox root"
    return None


def _validate_restricted_command(program: str, tokens: Sequence[str]) -> Optional[str]:
    """Reject command-specific sub-languages with file-write/exec capabilities."""
    if program == "awk":
        return "awk programs are not supported by the restricted runner; use search_file_content"
    if program == "find":
        for token in tokens[1:]:
            option = token.split("=", 1)[0].lower()
            if option in _FIND_DANGEROUS_ACTIONS:
                return f"find action '{option}' is blocked"
    if program == "sed":
        scripts: List[str] = []
        index = 1
        while index < len(tokens):
            token = tokens[index]
            if token in ("-f", "--file", "-i", "--in-place") or token.startswith("--in-place="):
                return "sed script files and in-place writes are blocked"
            if token in ("-e", "--expression"):
                index += 1
                if index >= len(tokens) or not _safe_sed_substitution(tokens[index]):
                    return "only non-writing sed substitution expressions are allowed"
                scripts.append(tokens[index])
            elif token.startswith("--expression="):
                script = token.split("=", 1)[1]
                if not _safe_sed_substitution(script):
                    return "only non-writing sed substitution expressions are allowed"
                scripts.append(script)
            elif token.startswith("-e") and len(token) > 2:
                script = token[2:]
                if not _safe_sed_substitution(script):
                    return "only non-writing sed substitution expressions are allowed"
                scripts.append(script)
            elif not token.startswith("-") and not scripts:
                if not _safe_sed_substitution(token):
                    return "only non-writing sed substitution expressions are allowed"
                scripts.append(token)
            index += 1
        if not scripts:
            return "a safe sed substitution expression is required"
    return None


def execute_bash_command(command: str, timeout_seconds: int = 60) -> str:
    """Run one allow-listed command WITHOUT a shell (no chaining, pipes or redirection)."""
    if not ENABLE_DANGEROUS:
        return _error("Dangerous tool execution disabled. Set MCP_ENABLE_DANGEROUS=true.")
    try:
        raw = str(command or "")
        if not raw.strip():
            return _error("Empty command")
        if len(raw) > MCP_MAX_REQUEST_BYTES:
            return _error("Command exceeds configured request limit")
        found = [ch for ch in _SHELL_METACHARS if ch in raw]
        if found:
            return _error("Shell metacharacters are not permitted (no chaining, pipes, "
                          "redirection, substitution or newlines).",
                          rejected_characters=[c.replace("\n", "\\n").replace("\r", "\\r") for c in found])
        try:
            tokens = shlex.split(raw)
        except ValueError as exc:
            return _error(f"Could not parse command: {exc}")
        if not tokens:
            return _error("Empty command")
        program = os.path.basename(tokens[0])
        if program not in ALLOWED_BASH_CMDS:
            return _error(f"Command '{program}' is not in the allowed whitelist.",
                          allowed=sorted(ALLOWED_BASH_CMDS))
        restricted_error = _validate_restricted_command(program, tokens)
        if restricted_error:
            return _error(restricted_error)
        for pattern, message in _ARG_DENY_PATTERNS:
            if any(pattern.search(tok) for tok in tokens[1:]):
                return _error(f"Rejected argument: {message}")
        resolved = shutil.which(program)
        if not resolved:
            return _error(f"Command '{program}' was not found on PATH")
        path_error = _validate_bash_path_arguments(tokens, program)
        if path_error:
            return _error(path_error)
        env = {"PATH": os.getenv("PATH", "/usr/bin:/bin"), "HOME": SANDBOX_ROOT,
               "LANG": os.getenv("LANG", "C.UTF-8"), "TERM": "dumb"}
        result = _run_subprocess([resolved] + tokens[1:],
                                 _bounded_int(timeout_seconds, 60, 1, 300), env=env)
        result["ok"] = result["exit_code"] == 0
        result["command"] = tokens
        result["stdout"] = result["stdout"][-100000:]
        result["stderr"] = result["stderr"][-20000:]
        if result["exit_code"] != 0:
            result["error"] = f"Command exited with status {result['exit_code']}"
        return _json_result(result)
    except subprocess.TimeoutExpired:
        return _error(f"Timed out after {timeout_seconds}s")
    except Exception as exc:
        return _error(str(exc))


# --- restricted python sandbox ---------------------------------------------
_SAFE_BUILTINS = sorted({
    "abs", "all", "any", "ascii", "bin", "bool", "bytearray", "bytes", "callable", "chr",
    "complex", "dict", "divmod", "enumerate", "filter", "float", "format", "frozenset",
    "hash", "hex", "int", "isinstance", "issubclass", "iter", "len", "list", "map", "max",
    "min", "next", "object", "oct", "ord", "pow", "print", "range", "repr", "reversed",
    "round", "set", "slice", "sorted", "str", "sum", "tuple", "zip", "True", "False", "None",
    "Exception", "ArithmeticError", "AssertionError", "AttributeError", "BaseException",
    "IndexError", "KeyError", "LookupError", "NameError", "NotImplementedError",
    "OverflowError", "RuntimeError", "StopIteration", "TypeError", "ValueError",
    "ZeroDivisionError", "RecursionError", "UnicodeError",
})

_SAFE_MODULES = sorted({
    "math", "cmath", "statistics", "random", "decimal", "fractions", "itertools", "functools",
    "collections", "heapq", "bisect", "string", "re", "json", "datetime", "time",
    "textwrap", "unicodedata", "uuid", "hashlib", "base64", "binascii", "struct", "array",
    "copy", "enum", "abc", "numbers", "pprint", "difflib", "csv",
})

_FORBIDDEN_NAMES = {"eval", "exec", "compile", "open", "input", "__import__", "globals", "locals",
                    "vars", "getattr", "setattr", "delattr", "hasattr", "breakpoint", "exit",
                    "quit", "help", "memoryview", "super", "type", "dir", "id", "license",
                    "credits", "copyright"}

_SANDBOX_RUNNER = '''
import builtins, sys, types

ALLOWED_BUILTINS = __ALLOWED_BUILTINS__
ALLOWED_MODULES = __ALLOWED_MODULES__

_real_import = builtins.__import__


class _SafeModuleProxy:
    """Expose public non-module exports without leaking a module's import graph."""
    __slots__ = ("_module", "_exports")

    def __init__(self, module):
        exports = tuple(name for name, value in vars(module).items()
                        if name and not name.startswith("_")
                        and not isinstance(value, types.ModuleType))
        object.__setattr__(self, "_module", module)
        object.__setattr__(self, "_exports", frozenset(exports))

    def __getattr__(self, name):
        if name == "__all__":
            return tuple(sorted(object.__getattribute__(self, "_exports")))
        if not name or name.startswith("_"):
            raise AttributeError(name)
        exports = object.__getattribute__(self, "_exports")
        if name not in exports:
            raise AttributeError(name)
        value = getattr(object.__getattribute__(self, "_module"), name)
        if isinstance(value, types.ModuleType):
            raise AttributeError(name)
        return value


def _guarded_import(name, globals=None, locals=None, fromlist=(), level=0):
    # Permit only explicitly listed root modules; never expose their attributes
    # that are modules (for example uuid.os or statistics.sys).
    if level != 0 or str(name) not in ALLOWED_MODULES:
        raise ImportError("import of '%s' is blocked in safe mode" % name)
    if any(str(item).startswith("_") for item in (fromlist or ())):
        raise ImportError("private imports are blocked in safe mode")
    module = _real_import(name, globals, locals, fromlist, level)
    return _SafeModuleProxy(module)


safe_builtins = {name: getattr(builtins, name) for name in ALLOWED_BUILTINS if hasattr(builtins, name)}
safe_builtins["__import__"] = _guarded_import
safe_builtins["__name__"] = "builtins"

sandbox_globals = {"__builtins__": safe_builtins, "__name__": "__main__", "__doc__": None,
                   "__package__": None, "__spec__": None, "__loader__": None, "__file__": "<sandbox>"}

with open(sys.argv[1], "r", encoding="utf-8") as handle:
    source = handle.read()

try:
    exec(compile(source, "<sandboxed>", "exec"), sandbox_globals)
except SystemExit:
    raise
except BaseException as exc:
    import traceback
    traceback.print_exc(limit=5)
    sys.exit(1)
'''


def _validate_sandbox_ast(code: str) -> Optional[str]:
    """Allow-list based static validation for safe_execute_python."""
    if len(code.encode("utf-8")) > 1024 * 1024:
        return "code exceeds the 1 MiB safe-execution limit"
    try:
        tree = ast.parse(code)
    except SyntaxError as exc:
        return f"Syntax error: {exc}"
    except (RecursionError, MemoryError, ValueError) as exc:
        return f"code is too complex to parse safely ({type(exc).__name__})"
    pending = [(tree, 0)]
    node_count = 0
    while pending:
        node, depth = pending.pop()
        node_count += 1
        if node_count > 100000:
            return "code exceeds the 100,000 AST node limit"
        if depth > 200:
            return "code exceeds the 200-level AST nesting limit"
        pending.extend((child, depth + 1) for child in ast.iter_child_nodes(node))
        if isinstance(node, ast.Attribute) and node.attr.startswith("_"):
            return f"access to private attribute '{node.attr}' is disallowed"
        if isinstance(node, ast.Name) and node.id in _FORBIDDEN_NAMES:
            return f"use of '{node.id}' is disallowed in safe mode"
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            if re.search(r"__[A-Za-z0-9_]+__", node.value):
                return "dunder attribute names in strings are disallowed in safe mode"
            if len(node.value) > 100000:
                return "string literal is too large"
        if isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name not in _SAFE_MODULES:
                    return f"import of '{alias.name}' is not on the safe-module allow-list"
        if isinstance(node, ast.ImportFrom):
            if node.level or node.module not in _SAFE_MODULES:
                return f"import from '{node.module}' is not on the safe-module allow-list"
            if any(alias.name.startswith("_") for alias in node.names):
                return "private imports are disallowed in safe mode"
        if isinstance(node, (ast.Global, ast.Nonlocal)):
            continue
    return None


def safe_execute_python(code: str, timeout_seconds: int = 15, memory_mb: int = 256) -> str:
    """Run Python in a restricted interpreter: allow-listed builtins/imports, rlimits, clean env."""
    if not ENABLE_DANGEROUS:
        return _error("Dangerous tool execution disabled. Set MCP_ENABLE_DANGEROUS=true.")
    code = str(code or "")
    if not code.strip():
        return _error("code must be a non-empty string")
    if len(code.encode("utf-8")) > MCP_MAX_REQUEST_BYTES:
        return _error("code exceeds configured request limit")
    violation = _validate_sandbox_ast(code)
    if violation:
        return _error(f"Security violation: {violation}")

    code_path = runner_path = ""
    try:
        timeout = _bounded_int(timeout_seconds, 15, 1, 120)
        memory_mb = _bounded_int(memory_mb, 256, 32, 2048)
        runner = (_SANDBOX_RUNNER
                  .replace("__ALLOWED_BUILTINS__", repr(_SAFE_BUILTINS))
                  .replace("__ALLOWED_MODULES__", repr(set(_SAFE_MODULES))))
        with temporary_file(".py", delete=False) as tf:
            tf.write(code)
            tf.flush()
            code_path = tf.name
        with temporary_file("_runner.py", delete=False) as tf:
            tf.write(runner)
            tf.flush()
            runner_path = tf.name

        clean_env = {"PYTHONDONTWRITEBYTECODE": "1", "PYTHONHASHSEED": "0",
                     "PATH": "/usr/bin:/bin", "LANG": "C.UTF-8", "HOME": SANDBOX_ROOT,
                     "PYTHONIOENCODING": "utf-8"}
        started = time.time()
        proc = _run_subprocess(
            [sys.executable or "python3", "-I", "-S", runner_path, code_path],
            timeout=timeout, cwd=tempfile.gettempdir(), env=clean_env,
            cpu_seconds=timeout, address_space_mb=memory_mb, file_size_mb=1,
        )
        payload = {
            "ok": proc["exit_code"] == 0,
            "stdout": proc["stdout"][-8000:],
            "stderr": proc["stderr"][-4000:],
            "exit_code": proc["exit_code"],
            "execution_time_sec": proc["execution_time_sec"],
            "restricted": True,
            "allowed_modules": _SAFE_MODULES,
        }
        if proc["exit_code"] != 0:
            payload["error"] = "Sandboxed execution exited with a non-zero status"
        return _json_result(payload)
    except subprocess.TimeoutExpired:
        return _error(f"Timed out after {timeout_seconds}s")
    except Exception as exc:
        return _error(str(exc))
    finally:
        for path in (code_path, runner_path):
            if path:
                with contextlib.suppress(Exception):
                    os.unlink(path)


def process_list(filter_name: str = "", max_results: int = 50) -> str:
    """List running processes (psutil when available, otherwise `ps aux`)."""
    try:
        limit = _bounded_int(max_results, 50, 1, 500)
        needle = str(filter_name or "").lower().strip()
        if HAS_PSUTIL:
            rows = []
            for proc in psutil.process_iter(["pid", "name", "username", "cpu_percent",
                                             "memory_percent", "status", "create_time"]):
                with contextlib.suppress(Exception):
                    info = proc.info
                    if needle and needle not in str(info.get("name", "")).lower():
                        continue
                    rows.append({"pid": info.get("pid"), "name": info.get("name"),
                                 "user": info.get("username"),
                                 "cpu_percent": info.get("cpu_percent"),
                                 "memory_percent": round(info.get("memory_percent") or 0, 2),
                                 "status": info.get("status")})
                if len(rows) >= limit:
                    break
            rows.sort(key=lambda r: r.get("memory_percent") or 0, reverse=True)
            return _ok({"source": "psutil", "count": len(rows), "processes": rows})
        if not shutil.which("ps"):
            return _error("Neither psutil nor `ps` is available")
        env = {"PATH": os.getenv("PATH", "/usr/bin:/bin"), "HOME": SANDBOX_ROOT,
               "LANG": "C.UTF-8", "LC_ALL": "C.UTF-8"}
        result = _run_subprocess(["ps", "-eo", "pid,comm,user,pcpu,pmem,state"],
                                 timeout=10, cwd=SANDBOX_ROOT, env=env,
                                 max_output_bytes=512000)
        if result["exit_code"] != 0:
            return _error(result["stderr"].strip() or "ps failed")
        lines = result["stdout"].splitlines()
        rows = lines[1:] if lines else []
        if needle:
            rows = [line for line in rows if needle in line.lower()]
        return _ok({"source": "ps", "count": len(rows), "output": rows[:limit],
                    "truncated": result["stdout_truncated"] or len(rows) > limit})
    except Exception as exc:
        return _error(str(exc))


# ---------------------------------------------------------------------------
# Networking & Web Tools
# ---------------------------------------------------------------------------
def _parse_headers_arg(headers: Any) -> Dict[str, str]:
    if not headers:
        return {}
    parsed: Dict[str, str] = {}
    if isinstance(headers, dict):
        parsed = {str(k): str(v) for k, v in headers.items()}
    else:
        text = str(headers).strip()
        if not text or text in ("{}", "null"):
            return {}
        try:
            data = json.loads(text)
            if isinstance(data, dict):
                parsed = {str(k): str(v) for k, v in data.items()}
        except json.JSONDecodeError:
            pass
        if not parsed:
            for line in text.splitlines():
                if ":" in line:
                    key, _, value = line.partition(":")
                    parsed[key.strip()] = value.strip()
            if not parsed:
                raise ValueError("headers must be a JSON object or 'Key: Value' lines")
    if len(parsed) > 100:
        raise ValueError("At most 100 request headers are allowed")
    validated: Dict[str, str] = {}
    seen_names = set()
    for key, value in parsed.items():
        if key.lower() in seen_names:
            raise ValueError(f"Duplicate HTTP header name: {key}")
        seen_names.add(key.lower())
        if (not key or len(key) > 256
                or not re.fullmatch(r"[!#$%&'*+.^_`|~0-9A-Za-z-]+", key)):
            raise ValueError(f"Invalid HTTP header name: {key[:80]}")
        if len(value) > 8192 or any(ord(char) < 32 and char != "\t" or ord(char) == 127
                                    for char in value):
            raise ValueError(f"Invalid or oversized value for HTTP header '{key}'")
        validated[key] = value
    return validated


def http_request(url: str, method: str = "GET", headers: str = "{}", data: str = "",
                 timeout_seconds: int = 0, max_response_chars: int = 5000) -> str:
    """Perform an SSRF-checked HTTP request and return status, headers and body."""
    try:
        method = str(method or "GET").upper()
        if method not in {"GET", "POST", "PUT", "PATCH", "DELETE", "HEAD", "OPTIONS"}:
            return _error("Unsupported HTTP method", allowed=["GET", "POST", "PUT", "PATCH",
                                                              "DELETE", "HEAD", "OPTIONS"])
        req_headers = _parse_headers_arg(headers)
        body = str(data).encode("utf-8") if data else None
        if body and len(body) > MCP_MAX_REQUEST_BYTES:
            return _error("Request body exceeds limit")
        max_chars = _bounded_int(max_response_chars, 5000, 100, MAX_TEXT_CHARS)
        response_byte_limit = min(MAX_HTTP_BYTES, max(1024, max_chars * 4))
        timeout = _bounded_int(timeout_seconds, HTTP_TIMEOUT, 1, 300) if timeout_seconds else HTTP_TIMEOUT
        try:
            result = _http_fetch(url, method=method, headers=req_headers, data=body,
                                 timeout=timeout, max_bytes=response_byte_limit)
            status, resp_headers = result["status"], result["headers"]
            text, final_url = result["text"], result["url"]
            truncated = result["truncated"]
            content_type = result["content_type"]
        except urllib.error.HTTPError as exc:
            if str(getattr(exc, "reason", "")).startswith("Blocked redirect:"):
                return _error(f"Blocked redirect: {exc.reason}", status=exc.code)
            raw, truncated = (_read_stream_limited(exc, response_byte_limit)
                              if hasattr(exc, "read") else (b"", False))
            status, resp_headers = exc.code, {k.lower(): v for k, v in (exc.headers or {}).items()}
            content_type = resp_headers.get("content-type", "")
            text = _decode_response(raw, content_type)
            final_url = getattr(exc, "url", None) or url
        payload: Dict[str, Any] = {
            "ok": 200 <= int(status) < 400,
            "status_code": status,
            "url": final_url,
            "method": method,
            "content_type": content_type,
            "headers": resp_headers,
            "body_truncated": truncated or len(text) > max_chars,
            "response": text[:max_chars],
        }
        if "json" in content_type.lower():
            if not truncated and len(text) <= 1024 * 1024:
                with contextlib.suppress(Exception):
                    payload["json"] = json.loads(text)
            else:
                payload["json_omitted"] = "response is truncated or exceeds the 1 MiB JSON preview limit"
        return _json_result(payload)
    except Exception as exc:
        return _error(str(exc))


def fetch_json_api(url: str, headers: str = "{}", method: str = "GET", data: str = "") -> str:
    """Call a JSON API and return the parsed document."""
    try:
        method = str(method or "GET").upper()
        if method not in {"GET", "POST", "PUT", "PATCH", "DELETE", "HEAD", "OPTIONS"}:
            return _error("Unsupported HTTP method", allowed=["GET", "POST", "PUT", "PATCH",
                                                               "DELETE", "HEAD", "OPTIONS"])
        req_headers = _parse_headers_arg(headers)
        req_headers.setdefault("User-Agent", USER_AGENT)
        req_headers.setdefault("Accept", "application/json")
        body = None
        if data:
            body = str(data).encode("utf-8")
            if len(body) > MCP_MAX_REQUEST_BYTES:
                return _error("Request body exceeds limit")
            req_headers.setdefault("Content-Type", "application/json")
        result = _http_fetch(url, method=method, headers=req_headers, data=body,
                             timeout=HTTP_TIMEOUT, max_bytes=min(MAX_HTTP_BYTES, 1024 * 1024),
                             strict_size=True)
        try:
            parsed = json.loads(result["text"])
        except json.JSONDecodeError as exc:
            return _error(f"Invalid JSON response: {exc}", status=result["status"],
                          content_type=result["content_type"],
                          body_preview=result["text"][:500])
        return _ok({"url": result["url"], "status": result["status"],
                    "content_type": result["content_type"], "data": parsed})
    except urllib.error.HTTPError as exc:
        return _error(f"HTTP {exc.code}: {exc.reason}", status=exc.code)
    except Exception as exc:
        return _error(f"fetch_json_api failed: {exc}")


def web_download_file(url: str, destination_filepath: str, max_bytes: int = 0,
                      overwrite: bool = True) -> str:
    """Stream a remote file to disk atomically; fail loudly instead of truncating."""
    temp_path = ""
    try:
        dst = _safe_path(destination_filepath)
        overwrite = _to_bool(overwrite, True)
        if os.path.exists(dst) and not overwrite:
            return _error(f"Destination '{destination_filepath}' already exists")
        _ensure_parent(dst)
        limit = _bounded_int(max_bytes, MAX_DOWNLOAD_BYTES, 1024, MAX_DOWNLOAD_BYTES) if max_bytes else MAX_DOWNLOAD_BYTES
        target = _assert_safe_remote(url)
        req = urllib.request.Request(target, headers={"User-Agent": USER_AGENT,
                                                       "Accept-Encoding": "identity"})
        fd, temp_path = tempfile.mkstemp(prefix=".mts-download-", dir=os.path.dirname(dst) or ".")
        digest = hashlib.sha256()
        total = 0
        with os.fdopen(fd, "wb") as out:
            with _get_opener().open(req, timeout=max(HTTP_TIMEOUT, 30)) as resp:
                _assert_safe_remote(resp.geturl())
                declared = resp.headers.get("Content-Length")
                if declared and declared.isdigit() and int(declared) > limit:
                    raise ValueError(f"Remote file is {int(declared)} bytes which exceeds the {limit} byte limit")
                while True:
                    chunk = resp.read(262144)
                    if not chunk:
                        break
                    total += len(chunk)
                    if total > limit:
                        raise ValueError(f"Download aborted: exceeded {limit} bytes (increase max_bytes to allow more)")
                    digest.update(chunk)
                    out.write(chunk)
                out.flush()
                os.fsync(out.fileno())
                content_type = resp.headers.get("Content-Type", "")
                final_url = resp.geturl()
        if overwrite:
            os.replace(temp_path, dst)
        else:
            # Linking is an atomic create-if-absent operation on the same filesystem.
            os.link(temp_path, dst)
            os.unlink(temp_path)
        temp_path = ""
        _bump("bytes_downloaded", total)
        return _ok({"status": "success", "url": url, "final_url": final_url, "saved_to": dst,
                    "size_bytes": total, "size_human": _human_bytes(total),
                    "sha256": digest.hexdigest(), "content_type": content_type})
    except Exception as exc:
        if temp_path:
            with contextlib.suppress(OSError):
                os.unlink(temp_path)
        return _error(f"web_download_file failed: {exc}")


def web_scrape_links(url: str, filter_domain: bool = False, max_links: int = 300) -> str:
    """Extract every hyperlink from a page, optionally same-domain only."""
    try:
        filter_domain = _to_bool(filter_domain, False)
        limit = _bounded_int(max_links, 300, 1, MCP_MAX_LINKS)
        result = _http_fetch_retrying(url, timeout=HTTP_TIMEOUT,
                                      max_bytes=min(MAX_HTTP_BYTES, MAX_HTML_BYTES))
        details = _extract_html_details(result["text"], result["url"])
        base_host = (urllib.parse.urlsplit(result["url"]).hostname or "").lower()
        links = []
        for item in details["links"]:
            host = (urllib.parse.urlsplit(item["url"]).hostname or "").lower()
            external = host != base_host
            if filter_domain and external:
                continue
            links.append({**item, "text": item["text"] or "[No Text]", "domain": host,
                          "is_external": external})
            if len(links) >= limit:
                break
        return _ok({"source_url": url, "final_url": result["url"], "count": len(links),
                    "internal": sum(1 for l in links if not l["is_external"]),
                    "external": sum(1 for l in links if l["is_external"]),
                    "truncated": (result["truncated"] or details["input_truncated"]
                                  or len(details["links"]) >= MCP_MAX_LINKS or len(links) >= limit),
                    "links": links})
    except Exception as exc:
        return _error(f"web_scrape_links failed: {exc}")


def extract_metadata(url: str) -> str:
    """Fetch a page and return its title, description, canonical URL and meta tags."""
    try:
        result = _http_fetch_retrying(url, timeout=HTTP_TIMEOUT,
                                      max_bytes=min(MAX_HTTP_BYTES, MAX_HTML_BYTES))
        details = _extract_html_details(result["text"], result["url"])
        return _ok({"url": url, "final_url": result["url"], "title": details["title"],
                    "description": details["description"], "image": details["image"],
                    "canonical_url": details["canonical_url"], "language": details["language"],
                    "site_name": details["site_name"], "headings": details["headings"][:30],
                    "truncated": result["truncated"] or details["input_truncated"],
                    "meta_tags": details["meta_tags"]})
    except Exception as exc:
        return _error(f"extract_metadata failed: {exc}")


def dns_lookup(domain: str, record_types: str = "A") -> str:
    """Resolve requested DNS record types and report reverse DNS where available."""
    try:
        host = str(domain or "").strip()
        if "://" in host:
            host = urllib.parse.urlsplit(host).hostname or ""
        host = host.split("/", 1)[0].strip("[]").rstrip(".")
        if not host or len(host) > 253:
            return _error("Invalid hostname")
        record_spec = str(record_types or "A")
        if len(record_spec) > 200:
            return _error("record_types is too long (max 200 characters)")
        requested = list(dict.fromkeys(t.strip().upper() for t in
                                       re.split(r"[,\s]+", record_spec) if t.strip()))
        if len(requested) > 10:
            return _error("At most 10 DNS record types may be requested at once")
        supported = {"A", "AAAA", "CNAME", "MX", "TXT", "NS", "SOA", "CAA", "SRV", "PTR"}
        unsupported = [kind for kind in requested if kind not in supported]
        if unsupported:
            return _error("Unsupported DNS record type", unsupported=unsupported,
                          supported=sorted(supported))
        if not requested:
            requested = ["A"]

        records: Dict[str, List[Any]] = {}
        errors: Dict[str, str] = {}
        query_names: Dict[str, str] = {}
        address_infos = []
        for kind in requested:
            if kind in ("A", "AAAA"):
                family = socket.AF_INET if kind == "A" else socket.AF_INET6
                try:
                    infos = socket.getaddrinfo(host, None, family=family, type=socket.SOCK_STREAM)
                    address_infos.extend(infos)
                    records[kind] = sorted({info[4][0] for info in infos})
                except OSError as exc:
                    records[kind] = []
                    errors[kind] = f"{type(exc).__name__}: {exc}"[:200]
                continue

            # The system resolver API in the standard library only exposes A/AAAA.
            # Use Google's fixed JSON DoH endpoint for other DNS record types.
            query_name = host
            if kind == "PTR":
                with contextlib.suppress(ValueError):
                    query_name = ipaddress.ip_address(host).reverse_pointer
            query_names[kind] = query_name
            query = urllib.parse.urlencode({"name": query_name, "type": kind})
            body = _http_get_text("https://dns.google/resolve?" + query,
                                  headers={"Accept": "application/dns-json", "User-Agent": USER_AGENT},
                                  timeout=HTTP_TIMEOUT, max_bytes=256 * 1024,
                                  cache_ttl=FETCH_CACHE_TTL)
            response = json.loads(body)
            answers = response.get("Answer", []) if isinstance(response, dict) else []
            values = [answer.get("data") for answer in answers
                      if isinstance(answer, dict) and answer.get("data") is not None]
            records[kind] = values
            if not values and isinstance(response, dict) and response.get("Status") not in (0, None):
                errors[kind] = f"DNS status {response.get('Status')}"

        addresses = sorted({info[4][0] for info in address_infos})
        ipv4 = sorted({info[4][0] for info in address_infos if info[0] == socket.AF_INET})
        ipv6 = sorted({info[4][0] for info in address_infos if info[0] == socket.AF_INET6})
        reverse = {}
        for address in addresses[:5]:
            with contextlib.suppress(Exception):
                reverse[address] = socket.gethostbyaddr(address)[0]
        private = [address for address in addresses
                   if _ip_is_blocked(ipaddress.ip_address(address))]
        cname = (records.get("CNAME") or [""])[0]
        found = any(records.get(kind) for kind in records)
        payload = {"ok": found, "domain": host, "record_types": requested, "records": records,
                   "query_names": query_names, "ip_addresses": addresses, "ipv4": ipv4, "ipv6": ipv6,
                   "count": len(addresses), "reverse_dns": reverse,
                   "canonical_name": cname or (address_infos[0][3] if address_infos else ""),
                   "private_addresses": private}
        if errors:
            payload["lookup_errors"] = errors
        if not found:
            payload["error"] = f"No DNS records found for {host} ({', '.join(requested)})"
        return _json_result(payload)
    except Exception as exc:
        return _error(f"dns_lookup failed: {exc}")


def check_url_status(url: str, follow_redirects: bool = True) -> str:
    """HEAD/GET a URL and report the status code, timing and final destination."""
    try:
        target = _assert_safe_remote(url)
        started = time.time()
        opener = _build_opener(6 if _to_bool(follow_redirects, True) else 0)

        def attempt(method: str):
            req = urllib.request.Request(target, headers={"User-Agent": USER_AGENT}, method=method)
            return opener.open(req, timeout=HTTP_TIMEOUT)

        status = None
        headers: Dict[str, str] = {}
        final_url = target
        try:
            with attempt("HEAD") as resp:
                status = getattr(resp, "status", None) or resp.getcode()
                headers = {k.lower(): v for k, v in resp.headers.items()}
                final_url = resp.geturl()
        except urllib.error.HTTPError as exc:
            if exc.code in (403, 405, 501):
                try:
                    with attempt("GET") as resp:
                        status = getattr(resp, "status", None) or resp.getcode()
                        headers = {k.lower(): v for k, v in resp.headers.items()}
                        final_url = resp.geturl()
                        resp.read(2048)
                except urllib.error.HTTPError as inner:
                    status = inner.code
                    headers = {k.lower(): v for k, v in (inner.headers or {}).items()}
                    final_url = inner.url or target
            else:
                status = exc.code
                headers = {k.lower(): v for k, v in (exc.headers or {}).items()}
                final_url = exc.url or target
        return _json_result({
            "ok": bool(status and 200 <= int(status) < 400),
            "url": url,
            "status_code": status,
            "status_class": f"{int(status) // 100}xx" if status else "unknown",
            "response_time_ms": round((time.time() - started) * 1000, 2),
            "final_url": final_url,
            "redirected": final_url.rstrip("/") != target.rstrip("/"),
            "content_type": headers.get("content-type", ""),
            "content_length": headers.get("content-length", ""),
            "server": headers.get("server", ""),
        })
    except Exception as exc:
        return _error(f"check_url_status failed: {exc}")


def parse_url_headers(url: str) -> str:
    """Return the response headers for a URL (HEAD with a GET fallback)."""
    try:
        target = _assert_safe_remote(url)
        try:
            req = urllib.request.Request(target, headers={"User-Agent": USER_AGENT}, method="HEAD")
            with _get_opener().open(req, timeout=HTTP_TIMEOUT) as resp:
                headers = {k.lower(): v for k, v in resp.headers.items()}
                status = getattr(resp, "status", None) or resp.getcode()
                final_url = resp.geturl()
        except urllib.error.HTTPError as exc:
            if exc.code not in (403, 405, 501):
                raise
            result = _http_fetch(target, max_bytes=8192)
            headers, status, final_url = result["headers"], result["status"], result["url"]
        security = {h: headers.get(h, "") for h in
                    ("strict-transport-security", "content-security-policy", "x-frame-options",
                     "x-content-type-options", "referrer-policy", "permissions-policy")}
        return _ok({"url": url, "final_url": final_url, "status_code": status,
                    "headers": headers, "security_headers": security})
    except Exception as exc:
        return _error(f"parse_url_headers failed: {exc}")


def read_url_hardened(url: str, max_chars: int = 64000, max_redirects: int = 5) -> str:
    """Fetch a URL with strict SSRF checks on every redirect hop and return its text."""
    try:
        max_chars = _bounded_int(max_chars, 64000, 256, MAX_TEXT_CHARS)
        max_redirects = _bounded_int(max_redirects, 5, 0, 10)
        target = _assert_safe_remote(url)
        req = urllib.request.Request(target, headers={"User-Agent": USER_AGENT}, method="GET")
        opener = _build_opener(max_redirects)
        with opener.open(req, timeout=HTTP_TIMEOUT) as resp:
            final_url = _assert_safe_remote(resp.geturl())
            data, truncated = _read_stream_limited(resp, min(MAX_HTTP_BYTES, MAX_HTML_BYTES))
            content_type = resp.headers.get("Content-Type", "")
            status = getattr(resp, "status", None) or resp.getcode()
        raw = _decode_response(data, content_type)
        is_html = "html" in content_type.lower() or bool(re.search(r"<html\b|<body\b", raw[:4000], re.I))
        if is_html:
            details = _extract_html_details(raw, final_url)
            text = details["text"]
            return _ok({"url": url, "final_url": final_url, "status": status,
                        "title": details["title"], "content_type": content_type,
                        "length": min(len(text), max_chars),
                        "truncated": truncated or len(text) > max_chars,
                        "text": text[:max_chars]})
        return _ok({"url": url, "final_url": final_url, "status": status,
                    "content_type": content_type, "length": min(len(raw), max_chars),
                    "truncated": truncated or len(raw) > max_chars, "text": raw[:max_chars]})
    except urllib.error.HTTPError as exc:
        return _error(f"HTTP {exc.code}: {exc.reason}", status=exc.code)
    except Exception as exc:
        return _error(f"read_url_hardened failed: {exc}")


def sitemap_parse(sitemap_url: str, max_urls: int = 500, recurse: bool = False) -> str:
    """Parse a sitemap.xml (optionally following nested sitemap indexes)."""
    try:
        limit = _bounded_int(max_urls, 500, 1, 5000)
        recurse = _to_bool(recurse, False)
        seen_sitemaps: Set[str] = set()
        urls: List[Dict[str, Any]] = []
        indexes: List[str] = []
        indexes_truncated = False
        urls_truncated = False

        def parse_one(target: str, depth: int = 0):
            nonlocal indexes_truncated, urls_truncated
            if target in seen_sitemaps:
                return
            if depth > 2 or len(seen_sitemaps) >= 25:
                if recurse:
                    urls_truncated = True
                return
            seen_sitemaps.add(target)
            fetched = _http_fetch_retrying(target, max_bytes=min(MAX_HTTP_BYTES, 4 * 1024 * 1024))
            if fetched["truncated"]:
                raise ValueError("Sitemap exceeds the 4 MiB parsing limit")
            target = fetched["url"]
            root = _safe_parse_xml(fetched["text"])
            is_index = root.tag.split("}")[-1].lower() == "sitemapindex"
            for node in root:
                if urls_truncated:
                    return
                name = node.tag.split("}")[-1].lower()
                if name not in {"url", "sitemap"}:
                    continue
                values = {child.tag.split("}")[-1].lower(): (child.text or "").strip() for child in node}
                loc = values.get("loc")
                if not loc:
                    continue
                try:
                    loc = _validated_http_url(urllib.parse.urljoin(target, loc))
                except ValueError:
                    continue
                if name == "sitemap" or is_index:
                    if len(indexes) < 200:
                        indexes.append(loc)
                    else:
                        indexes_truncated = True
                    if recurse:
                        with contextlib.suppress(Exception):
                            parse_one(loc, depth + 1)
                else:
                    if len(urls) >= limit:
                        urls_truncated = True
                        return
                    urls.append({"url": loc, "lastmod": values.get("lastmod"),
                                 "changefreq": values.get("changefreq"),
                                 "priority": values.get("priority")})

        parse_one(sitemap_url)
        return _ok({"sitemap_url": sitemap_url, "count": len(urls),
                    "truncated": urls_truncated,
                    "nested_sitemaps": indexes, "nested_sitemaps_truncated": indexes_truncated,
                    "urls": urls[:limit]})
    except Exception as exc:
        return _error(f"sitemap_parse failed: {exc}")


def rss_feed_parse(feed_url: str, max_items: int = 0) -> str:
    """Parse an RSS or Atom feed into normalised items (handles <link>text</link>)."""
    try:
        limit = _bounded_int(max_items, MCP_MAX_FEED_ITEMS, 1, 1000) if max_items else MCP_MAX_FEED_ITEMS
        fetched = _http_fetch_retrying(feed_url, max_bytes=min(MAX_HTTP_BYTES, 4 * 1024 * 1024))
        if fetched["truncated"]:
            raise ValueError("RSS/Atom feed exceeds the 4 MiB parsing limit")
        feed_base = fetched["url"]
        root = _safe_parse_xml(fetched["text"])
        channel_title = ""
        for tag in ("title", "{http://www.w3.org/2005/Atom}title"):
            node = root.find(f"./channel/{tag}") if tag == "title" else root.find(tag)
            if node is not None and node.text:
                channel_title = node.text.strip()[:1000]
                break
        items = []
        items_truncated = False
        for node in root.iter():
            name = node.tag.split("}")[-1].lower()
            if name not in {"item", "entry"}:
                continue
            if len(items) >= limit:
                items_truncated = True
                break
            values: Dict[str, str] = {}
            enclosures: List[str] = []
            categories: List[str] = []
            for child in node:
                key = child.tag.split("}")[-1].lower()
                if key == "link":
                    # Atom uses href attributes, RSS 2.0 puts the URL in the text node
                    value = (child.attrib.get("href") or (child.text or "")).strip()
                elif key == "enclosure":
                    enclosure_url = child.attrib.get("url", "")
                    if enclosure_url and len(enclosures) < 10 and len(enclosure_url) <= 2048:
                        enclosures.append(enclosure_url)
                    continue
                elif key == "category":
                    text = (child.text or child.attrib.get("term") or "").strip()
                    if text and len(categories) < 15:
                        categories.append(text[:500])
                    continue
                else:
                    value = " ".join("".join(child.itertext()).split())
                if key in {"title", "description", "summary", "content", "encoded", "pubdate",
                           "published", "updated", "link", "id", "guid", "author", "creator"}:
                    values.setdefault(key, value[:4096])
            link = values.get("link") or values.get("guid") or ""
            link_url = ""
            if link and len(link) <= 2048:
                with contextlib.suppress(ValueError):
                    link_url = _validated_http_url(urllib.parse.urljoin(feed_base, link))[:2048]
            safe_enclosures = []
            for enclosure in enclosures[:10]:
                with contextlib.suppress(ValueError):
                    safe_enclosures.append(_validated_http_url(urllib.parse.urljoin(feed_base, enclosure)))
            items.append({
                "title": values.get("title", "")[:1000],
                "link": link_url,
                "pub_date": (values.get("pubdate") or values.get("published")
                             or values.get("updated", ""))[:500],
                "author": (values.get("author") or values.get("creator") or "")[:500],
                "categories": categories[:15],
                "enclosures": safe_enclosures,
                "description": _strip_tags(values.get("description") or values.get("summary")
                                           or values.get("content") or values.get("encoded", ""))[:4000],
            })
            if len(items) >= limit:
                break
        return _ok({"feed_url": feed_url, "feed_title": channel_title,
                    "count": len(items), "truncated": items_truncated, "items": items})
    except ET.ParseError as exc:
        return _error(f"rss_feed_parse failed: malformed XML ({exc})")
    except Exception as exc:
        return _error(f"rss_feed_parse failed: {exc}")


def whois_lookup(domain: str, timeout_seconds: int = 10) -> str:
    """Query WHOIS over port 43 (IANA referral then the registry server)."""
    try:
        host = str(domain or "").strip().lower()
        if "://" in host:
            host = urllib.parse.urlsplit(host).hostname or ""
        host = host.split("/", 1)[0].strip().strip(".")
        if not host or len(host) > 253 or not re.fullmatch(r"[a-z0-9.-]+", host):
            return _error("Invalid domain name")
        timeout = _bounded_int(timeout_seconds, 10, 1, 30)

        def query(server: str, request: str) -> str:
            with _create_pinned_socket(server, 43, timeout, allow_private=False) as sock:
                sock.sendall((request + "\r\n").encode("utf-8"))
                chunks = []
                total = 0
                while total < 256000:
                    data = sock.recv(min(8192, 256000 - total))
                    if not data:
                        break
                    chunks.append(data)
                    total += len(data)
            return b"".join(chunks).decode("utf-8", "replace")

        iana = query("whois.iana.org", host)
        referral = re.search(r"(?im)^refer:\s*(\S+)", iana)
        registry_text = ""
        server = ""
        if referral:
            server = referral.group(1)
            with contextlib.suppress(Exception):
                registry_text = query(server, host)
        text = registry_text or iana
        fields = {}
        for key in ("registrar", "creation date", "created", "updated date", "expiry date",
                    "registry expiry date", "name server", "status", "org", "country"):
            matches = re.findall(rf"(?im)^{re.escape(key)}:\s*(.+)$", text)
            if matches:
                fields[key.replace(" ", "_")] = matches[:6] if len(matches) > 1 else matches[0].strip()
        return _ok({"domain": host, "whois_server": server or "whois.iana.org",
                    "fields": fields, "raw": text[:20000]})
    except Exception as exc:
        return _error(f"whois_lookup failed: {exc}")


def port_check(host: str, ports: str = "80,443", timeout_seconds: int = 3) -> str:
    """Test TCP connectivity to a host on one or more ports (public hosts only)."""
    try:
        target = str(host or "").strip()
        if "://" in target:
            target = urllib.parse.urlsplit(target).hostname or ""
        target = target.strip("[]")
        if not target or len(target) > 253:
            return _error("host must be a hostname or IP address no longer than 253 characters")
        if not ALLOW_PRIVATE_NETWORKS and _host_is_private(target):
            return _error("Scanning private, loopback or unresolvable hosts is blocked")
        ports_spec = str(ports or "")
        if len(ports_spec) > 1000:
            return _error("ports is too long (maximum 1,000 characters)")
        wanted = []
        for chunk in re.split(r"[,\s]+", ports_spec):
            if not chunk or len(wanted) >= 64:
                continue
            if "-" in chunk:
                low, _, high = chunk.partition("-")
                with contextlib.suppress(ValueError):
                    start_port, end_port = int(low), int(high)
                    wanted.extend(range(start_port, min(end_port, start_port + 63) + 1))
            else:
                with contextlib.suppress(ValueError):
                    wanted.append(int(chunk))
        wanted = [p for p in dict.fromkeys(wanted) if 1 <= p <= 65535][:64]
        if not wanted:
            return _error("No valid ports supplied")
        timeout = _bounded_int(timeout_seconds, 3, 1, 15)

        def probe(port: int) -> Dict[str, Any]:
            started = time.time()
            try:
                with _create_pinned_socket(target, port, timeout):
                    return {"port": port, "open": True,
                            "latency_ms": round((time.time() - started) * 1000, 2)}
            except Exception as exc:
                return {"port": port, "open": False, "error": type(exc).__name__}

        with ThreadPoolExecutor(max_workers=min(len(wanted), 16)) as pool:
            results = sorted(pool.map(probe, wanted), key=lambda r: r["port"])
        return _ok({"host": target, "checked": len(results),
                    "open_ports": [r["port"] for r in results if r["open"]], "results": results})
    except Exception as exc:
        return _error(f"port_check failed: {exc}")


# ---------------------------------------------------------------------------
# HTML/document parsing tools (operate on supplied markup)
# ---------------------------------------------------------------------------
def parse_html_document(html: str, base_url: str = "", max_chars: int = 64000) -> str:
    """Extract title, text, links, headings and meta tags from an HTML string."""
    try:
        max_chars = _bounded_int(max_chars, 64000, 256, MAX_TEXT_CHARS)
        details = _extract_html_details(html, base_url)
        details["total_text_length"] = len(details["text"])
        details["text_truncated"] = len(details["text"]) > max_chars
        details["text"] = details["text"][:max_chars]
        details["ok"] = True
        return _json_result(details)
    except Exception as exc:
        return _error(f"parse_html_document failed: {exc}")


def extract_structured_data(html: str, base_url: str = "") -> str:
    """Pull bounded JSON-LD, microdata-ish meta and OpenGraph/Twitter cards out of HTML."""
    class JSONLDParser(HTMLParser):
        def __init__(self):
            super().__init__(convert_charrefs=True)
            self.values: List[Any] = []
            self.parts: List[str] = []
            self.script_chars = 0
            self.total_chars = 0
            self.capturing = False
            self.truncated = False

        def handle_starttag(self, tag, attrs):
            if tag.lower() != "script" or self.capturing:
                return
            attributes = {str(key).lower(): (value or "") for key, value in attrs}
            media_type = attributes.get("type", "").split(";", 1)[0].strip().lower()
            if media_type != "application/ld+json":
                return
            if len(self.values) >= 100 or self.total_chars >= MAX_TEXT_CHARS * 8:
                self.truncated = True
                return
            self.capturing = True
            self.parts = []
            self.script_chars = 0

        def handle_data(self, data):
            if not self.capturing:
                return
            per_script_left = max(0, MAX_TEXT_CHARS * 4 - self.script_chars)
            total_left = max(0, MAX_TEXT_CHARS * 8 - self.total_chars - self.script_chars)
            remaining = min(per_script_left, total_left)
            if remaining:
                self.parts.append(data[:remaining])
                self.script_chars += min(len(data), remaining)
            if len(data) > remaining:
                self.truncated = True

        def handle_endtag(self, tag):
            if tag.lower() != "script" or not self.capturing:
                return
            raw = html_module.unescape("".join(self.parts)).strip()
            self.total_chars += self.script_chars
            try:
                self.values.append(json.loads(raw))
            except (json.JSONDecodeError, RecursionError, ValueError):
                pass
            self.parts = []
            self.capturing = False

    try:
        source_full = str(html or "")
        source = source_full[:MAX_HTML_BYTES]
        details = _extract_html_details(source, base_url)
        parser = JSONLDParser()
        parser.feed(source)
        parser.close()
        json_ld_truncated = len(source_full) > MAX_HTML_BYTES or parser.truncated or parser.capturing
        meta = details["meta_tags"]
        return _ok({
            "title": details["title"],
            "canonical_url": details["canonical_url"],
            "description": details["description"],
            "open_graph": {k: v for k, v in meta.items() if k.startswith("og:")},
            "twitter_card": {k: v for k, v in meta.items() if k.startswith("twitter:")},
            "json_ld": parser.values,
            "json_ld_types": [d.get("@type") for d in parser.values if isinstance(d, dict)],
            "json_ld_truncated": json_ld_truncated or details.get("input_truncated", False),
        })
    except Exception as exc:
        return _error(f"extract_structured_data failed: {exc}")


def parse_html_tables(html: str, max_tables: int = 20, max_rows: int = 200) -> str:
    """Convert HTML <table> elements into header/row structures."""
    class TableParser(HTMLParser):
        def __init__(self, table_limit, row_limit):
            super().__init__(convert_charrefs=True)
            self.tables: List[List[List[str]]] = []
            self.table: Optional[List[List[str]]] = None
            self.row: Optional[List[str]] = None
            self.cell: Optional[List[str]] = None
            self.cell_chars = 0
            self.table_limit = table_limit
            self.row_limit = row_limit
            self.total_rows = 0
            self.total_cells = 0
            self.truncated = False
            self.in_cell = False

        def handle_starttag(self, tag, attrs):
            tag = tag.lower()
            if tag == "table":
                if len(self.tables) < self.table_limit:
                    self.table = []
                else:
                    self.table = None
                    self.truncated = True
            elif self.table is not None and tag == "tr":
                if self.total_rows < 20000:
                    self.row = []
                    self.total_rows += 1
                else:
                    self.row = None
                    self.truncated = True
            elif self.table is not None and tag in ("td", "th") and self.row is not None:
                if self.total_cells < 100000:
                    self.cell = []
                    self.cell_chars = 0
                    self.in_cell = True
                    self.total_cells += 1
                else:
                    self.cell = None
                    self.in_cell = False
                    self.truncated = True

        def handle_data(self, data):
            if self.in_cell and self.cell is not None and self.cell_chars < 2000:
                value = re.sub(r"\s+", " ", data).strip()
                if value:
                    piece = value[:2000 - self.cell_chars]
                    self.cell.append(piece)
                    self.cell_chars += len(piece)

        def handle_endtag(self, tag):
            tag = tag.lower()
            if tag in ("td", "th") and self.in_cell and self.row is not None:
                if len(self.row) < 100:
                    self.row.append(" ".join(self.cell or [])[:2000])
                else:
                    self.truncated = True
                self.cell, self.in_cell = None, False
            elif tag == "tr" and self.table is not None and self.row is not None:
                if self.row and len(self.table) < self.row_limit:
                    self.table.append(self.row)
                elif self.row:
                    self.truncated = True
                self.row = None
            elif tag == "table" and self.table is not None:
                if self.table:
                    self.tables.append(self.table)
                self.table = None

    try:
        table_limit = _bounded_int(max_tables, 20, 1, 100)
        row_limit = _bounded_int(max_rows, 200, 1, 5000)
        parser = TableParser(table_limit, row_limit)
        html_source = str(html or "")
        input_truncated = len(html_source) > MAX_HTML_BYTES
        parser.feed(html_source[:MAX_HTML_BYTES])
        parser.close()
        tables = parser.tables[:table_limit]
        out = []
        for table in tables:
            rows = table[:row_limit]
            headers = rows[0] if rows else []
            data_rows = rows[1:] if headers else rows
            records = [dict(zip(headers, row)) for row in data_rows] if headers else []
            out.append({"headers": headers, "rows": data_rows, "row_count": len(data_rows),
                        "records": records[:row_limit]})
        return _ok({"table_count": len(out), "truncated": parser.truncated or input_truncated,
                    "tables": out})
    except Exception as exc:
        return _error(f"parse_html_tables failed: {exc}")


def extract_media_links(html: str, base_url: str = "", max_results: int = 500) -> str:
    """Collect image/audio/video/source URLs (src, data-src, srcset, poster) from HTML."""
    limit = _bounded_int(max_results, 500, 1, MCP_MAX_LINKS)

    class MediaParser(HTMLParser):
        def __init__(self):
            super().__init__(convert_charrefs=True)
            self.items: List[Dict[str, str]] = []
            self.truncated = False

        def _add(self, item):
            if len(self.items) < limit:
                self.items.append(item)
            else:
                self.truncated = True

        def handle_starttag(self, tag, attrs):
            attrs_dict = {str(k).lower(): (v or "") for k, v in attrs}
            tag = tag.lower()
            candidate_attrs = ("src", "data-src", "data-original", "poster")
            if tag == "source":
                candidate_attrs += ("href",)
            for key in candidate_attrs:
                value = attrs_dict.get(key)
                if value:
                    self._add({"type": tag, "attribute": key, "url": value[:2048],
                               "alt": attrs_dict.get("alt", "")[:500]})
            srcset = attrs_dict.get("srcset")
            if srcset:
                for candidate in srcset.split(","):
                    url = candidate.strip().split(" ")[0]
                    if url:
                        self._add({"type": tag, "attribute": "srcset", "url": url[:2048],
                                   "alt": attrs_dict.get("alt", "")[:500]})

        def handle_startendtag(self, tag, attrs):
            self.handle_starttag(tag, attrs)

    try:
        parser = MediaParser()
        html_source = str(html or "")
        input_truncated = len(html_source) > MAX_HTML_BYTES
        parser.feed(html_source[:MAX_HTML_BYTES])
        parser.close()
        out, seen = [], set()
        for item in parser.items:
            try:
                absolute = _validated_http_url(urllib.parse.urljoin(base_url, item["url"]))
            except ValueError:
                continue
            key = (item["type"], absolute)
            if key in seen:
                continue
            seen.add(key)
            out.append({**item, "url": absolute,
                        "extension": os.path.splitext(urllib.parse.urlsplit(absolute).path)[1].lower()})
            if len(out) >= limit:
                break
        return _ok({"count": len(out), "truncated": parser.truncated or input_truncated,
                    "media": out})
    except Exception as exc:
        return _error(f"extract_media_links failed: {exc}")


def parse_robots_txt(text: str = "", user_agent: str = "*", url: str = "") -> str:
    """Parse robots.txt rules for a user agent (pass text= or url= to fetch it)."""
    try:
        content = str(text or "")
        source = "inline"
        if not content.strip() and url:
            base = _assert_safe_remote(url)
            parts = urllib.parse.urlsplit(base)
            robots_url = urllib.parse.urlunsplit((parts.scheme, parts.netloc, "/robots.txt", "", ""))
            fetched = _http_fetch_retrying(robots_url, max_bytes=min(MAX_HTTP_BYTES, 1024 * 1024))
            if fetched["truncated"]:
                return _error("robots.txt exceeds the 1 MiB parsing limit")
            content = fetched["text"]
            source = fetched["url"]
        if not content.strip():
            return _error("Provide robots.txt content via text= or a site url=")
        content_truncated = len(content) > 1024 * 1024
        content = content[:1024 * 1024]
        groups: List[Dict[str, Any]] = []
        current: Optional[Dict[str, Any]] = None
        sitemaps: List[str] = []
        lines_seen = 0
        lines_truncated = False
        for raw in io.StringIO(content):
            lines_seen += 1
            if lines_seen > 5000:
                lines_truncated = True
                break
            line = raw[:4096].split("#", 1)[0].strip()
            if not line or ":" not in line:
                continue
            key, value = [x.strip() for x in line.split(":", 1)]
            key, value = key[:64], value[:512]
            low = key.lower()
            if low == "sitemap":
                candidate = urllib.parse.urljoin(source, value) if source != "inline" else value
                try:
                    candidate = _validated_http_url(candidate)
                except ValueError:
                    continue
                if len(sitemaps) < 100:
                    sitemaps.append(candidate)
                else:
                    content_truncated = True
                continue
            if low == "user-agent":
                if current is None or current.get("directives"):
                    current = {"agents": [], "directives": []}
                    groups.append(current)
                current["agents"].append(value)
            elif current is not None:
                current["directives"].append({"directive": low, "value": value})
        user_agent = str(user_agent or "*")[:500]
        ua = user_agent.lower()
        selected, matched_agents = [], []
        for group in groups:
            if any(a == "*" or a.lower() in ua or ua in a.lower() for a in group["agents"]):
                selected.extend(group["directives"])
                matched_agents.extend(group["agents"])
        return _ok({"source": source, "user_agent": user_agent,
                    "matched_agent_groups": matched_agents,
                    "disallow": [d["value"] for d in selected if d["directive"] == "disallow"],
                    "allow": [d["value"] for d in selected if d["directive"] == "allow"],
                    "crawl_delay": next((d["value"] for d in selected if d["directive"] == "crawl-delay"), None),
                    "sitemaps": sitemaps, "truncated": content_truncated or lines_truncated,
                    "directives": selected})
    except Exception as exc:
        return _error(f"parse_robots_txt failed: {exc}")


def discover_feed_links(html: str = "", base_url: str = "", url: str = "") -> str:
    """Find RSS/Atom/JSON feed links in a page (pass html= or url= to fetch it)."""
    class FeedParser(HTMLParser):
        def __init__(self):
            super().__init__(convert_charrefs=True)
            self.items: List[Dict[str, str]] = []
            self.truncated = False

        def handle_starttag(self, tag, attrs):
            if tag.lower() != "link":
                return
            a = {str(k).lower(): (v or "") for k, v in attrs}
            rel = a.get("rel", "").lower().split()
            typ = a.get("type", "").lower()
            if "alternate" in rel and any(x in typ for x in ("rss", "atom", "json", "feed")):
                if a.get("href"):
                    if len(self.items) < 100:
                        self.items.append({"type": typ[:200], "title": a.get("title", "")[:500],
                                           "url": a["href"][:2048]})
                    else:
                        self.truncated = True

        def handle_startendtag(self, tag, attrs):
            self.handle_starttag(tag, attrs)

    try:
        content = str(html or "")
        fetch_truncated = False
        if not content.strip() and url:
            result = _http_fetch_retrying(url, timeout=HTTP_TIMEOUT,
                                          max_bytes=min(MAX_HTTP_BYTES, MAX_HTML_BYTES))
            content = result["text"]
            fetch_truncated = result["truncated"]
            base_url = base_url or result["url"]
        if not content.strip():
            return _error("Provide page markup via html= or a url= to fetch")
        if base_url:
            try:
                base_url = _validated_http_url(str(base_url))
            except ValueError:
                base_url = ""
        parser = FeedParser()
        parser.feed(content[:MAX_HTML_BYTES])
        parser.close()
        seen, feeds = set(), []
        for item in parser.items:
            absolute = urllib.parse.urljoin(base_url, item["url"])
            try:
                absolute = _validated_http_url(absolute)
            except ValueError:
                continue
            if absolute in seen:
                continue
            seen.add(absolute)
            feeds.append({**item, "url": absolute})
        # common conventional locations
        if base_url:
            for guess in ("/feed", "/rss", "/atom.xml", "/index.xml", "/feed.xml"):
                candidate = urllib.parse.urljoin(base_url, guess)
                if candidate not in seen:
                    feeds.append({"type": "guess", "title": "conventional location", "url": candidate,
                                  "verified": False})
                    seen.add(candidate)
        return _ok({"count": len(feeds[:100]),
                    "truncated": (parser.truncated or len(feeds) > 100
                                  or len(content) > MAX_HTML_BYTES or fetch_truncated),
                    "feeds": feeds[:100]})
    except Exception as exc:
        return _error(f"discover_feed_links failed: {exc}")


# ---------------------------------------------------------------------------
# Data, Text & Encoding Utilities
# ---------------------------------------------------------------------------
def json_parse_validate(json_string: str, pretty: bool = False) -> str:
    """Validate a JSON string and report its structure."""
    try:
        data = json.loads(json_string)
    except json.JSONDecodeError as exc:
        return _json_result({"ok": False, "valid": False, "error": str(exc),
                             "line": exc.lineno, "column": exc.colno, "position": exc.pos})
    except Exception as exc:
        return _json_result({"ok": False, "valid": False, "error": str(exc)})
    info: Dict[str, Any] = {"ok": True, "valid": True, "type": type(data).__name__}
    if isinstance(data, dict):
        info["keys"] = list(data)[:200]
        info["key_count"] = len(data)
    elif isinstance(data, list):
        info["length"] = len(data)
        info["element_types"] = sorted({type(x).__name__ for x in data[:200]})
    info["parsed"] = data
    if _to_bool(pretty, False):
        info["formatted"] = json.dumps(data, indent=2, ensure_ascii=False)
    return _json_result(info)


def _parse_json_path(expression: str) -> List[Tuple[str, Any]]:
    """Parse a small, strict JSON path grammar without silently skipping bad syntax."""
    if len(expression) > 1024:
        raise ValueError("path exceeds 1024 characters")
    path = expression
    index = 0
    if path.startswith("$"):
        index = 1
        if index < len(path) and path[index] not in ".[":
            raise ValueError("'$' must be followed by '.' or '['")
    if index < len(path) and path[index] == ".":
        index += 1
    tokens: List[Tuple[str, Any]] = []
    need_segment = True
    while index < len(path):
        char = path[index]
        if char == ".":
            if need_segment:
                raise ValueError(f"empty path segment at position {index}")
            need_segment = True
            index += 1
            if index >= len(path):
                raise ValueError("path cannot end with '.'")
            continue
        if char == "[":
            end = index + 1
            in_string = False
            escaped = False
            while end < len(path):
                current_char = path[end]
                if in_string:
                    if escaped:
                        escaped = False
                    elif current_char == "\\":
                        escaped = True
                    elif current_char == '"':
                        in_string = False
                elif current_char == '"':
                    in_string = True
                elif current_char == "]":
                    break
                elif current_char == "[":
                    raise ValueError(f"nested '[' at position {end}")
                end += 1
            if end >= len(path):
                raise ValueError(f"unclosed '[' at position {index}")
            content = path[index + 1:end]
            if re.fullmatch(r"(?:0|[1-9][0-9]*)", content):
                tokens.append(("index", int(content)))
            elif content.startswith('"'):
                try:
                    key = json.loads(content)
                except json.JSONDecodeError as exc:
                    raise ValueError(f"invalid quoted key at position {index}: {exc.msg}") from exc
                if not isinstance(key, str):
                    raise ValueError("bracketed object keys must be strings")
                tokens.append(("key", key))
            else:
                raise ValueError(f"expected a non-negative index or JSON string key at position {index}")
            index = end + 1
            need_segment = False
            continue
        if char == "]":
            raise ValueError(f"unexpected ']' at position {index}")
        if not need_segment:
            raise ValueError(f"expected '.' or '[' at position {index}")
        end = index
        while end < len(path) and path[end] not in ".[ ]":
            if path[end] == "]":
                break
            end += 1
        segment = path[index:end]
        if not segment or any(ch.isspace() for ch in segment):
            raise ValueError(f"invalid path segment at position {index}")
        tokens.append(("segment", segment))
        index = end
        need_segment = False
    if need_segment:
        raise ValueError("path cannot end with an empty segment")
    return tokens


def json_query(json_string: str, path: str = "", default: str = "") -> str:
    """Query JSON using dotted keys, numeric indices, or bracketed indices/quoted keys."""
    try:
        data = json.loads(json_string) if isinstance(json_string, str) else json_string
    except Exception as exc:
        return _error(f"Invalid JSON: {exc}")
    expression = str(path or "").strip()
    if not expression or expression in (".", "$"):
        return _ok({"path": expression, "value": data})
    try:
        tokens = _parse_json_path(expression)
    except ValueError as exc:
        return _error(f"Invalid JSON path: {exc}")

    current: Any = data
    walked: List[str] = []
    for kind, value in tokens:
        token = f"[{value}]" if kind == "index" else str(value)
        walked.append(token)
        try:
            if isinstance(current, dict):
                key = str(value) if kind == "index" else value
                if key not in current:
                    raise KeyError(key)
                current = current[key]
            elif isinstance(current, (list, tuple)):
                if kind == "key":
                    raise TypeError("quoted object key cannot index an array")
                array_index = value if kind == "index" else int(value)
                current = current[array_index]
            else:
                raise TypeError(f"cannot index {type(current).__name__}")
        except Exception as exc:
            if default:
                return _ok({"path": expression, "value": default, "used_default": True,
                            "failed_at": ".".join(walked), "reason": str(exc)})
            return _error(f"Path not found at '{'.'.join(walked)}': {exc}")
    return _ok({"path": expression, "type": type(current).__name__, "value": current})


def _regex_has_nested_repeats(pattern: str) -> bool:
    """Conservatively flag nested/ambiguous repeats and repeated identical atoms."""
    # Three or more adjacent repetitions of the same simple atom can backtrack
    # combinatorially even without parentheses (for example, a*a*a*a*b).
    index = 0
    last_atom = None
    repeat_run = 0
    while index < len(pattern):
        start = index
        char = pattern[index]
        if char == "\\" and index + 1 < len(pattern):
            index += 2
        elif char == "[":
            index += 1
            escaped = False
            while index < len(pattern):
                current = pattern[index]
                index += 1
                if escaped:
                    escaped = False
                elif current == "\\":
                    escaped = True
                elif current == "]":
                    break
        elif char in "()|":
            last_atom, repeat_run = None, 0
            index += 1
            continue
        else:
            index += 1
        atom = pattern[start:index]
        if index < len(pattern) and pattern[index] in "*+?":
            index += 1
            if atom == last_atom:
                repeat_run += 1
            else:
                last_atom, repeat_run = atom, 1
            if repeat_run >= 3:
                return True
            if index < len(pattern) and pattern[index] == "?":
                index += 1
        elif index < len(pattern) and pattern[index] == "{":
            quantifier = re.match(r"\{[0-9]+,\d*\}", pattern[index:])
            if quantifier:
                index += len(quantifier.group(0))
                if atom == last_atom:
                    repeat_run += 1
                else:
                    last_atom, repeat_run = atom, 1
                if repeat_run >= 3:
                    return True
            else:
                last_atom, repeat_run = None, 0
        else:
            last_atom, repeat_run = None, 0

    stack = [{"quantifier": False, "alternation": False}]
    in_class = False
    escaped = False
    last_group = None
    previous = ""
    for char in pattern:
        if escaped:
            escaped = False
            last_group = None
            previous = char
            continue
        if char == "\\":
            escaped = True
            previous = char
            continue
        if in_class:
            if char == "]":
                in_class = False
            last_group = None
            previous = char
            continue
        if char == "[":
            in_class = True
            last_group = None
        elif char == "(":
            stack.append({"quantifier": False, "alternation": False})
            last_group = None
        elif char == "|":
            stack[-1]["alternation"] = True
            last_group = None
        elif char == ")" and len(stack) > 1:
            last_group = stack.pop()
            stack[-1]["quantifier"] |= last_group["quantifier"]
            stack[-1]["alternation"] |= last_group["alternation"]
        elif char in "*+?{" and not (char == "?" and previous == "("):
            if last_group and (last_group["quantifier"] or last_group["alternation"]):
                return True
            stack[-1]["quantifier"] = True
            last_group = None
        else:
            last_group = None
        previous = char
    return False


_REGEX_WORKER = r'''\
import io, itertools, json, re, sys
request = json.loads(sys.stdin.read())
try:
    flags = 0
    for flag in request["flags"]:
        flags |= {"i": re.I, "m": re.M, "s": re.S, "x": re.X, "a": re.A, "u": re.U}[flag]
    compiled = re.compile(request["pattern"], flags)
    if request.get("line_mode"):
        output = []
        used = 0
        truncated = False
        output_limited = False
        for line_number, line in enumerate(io.StringIO(request["text"]), 1):
            if not compiled.search(line):
                continue
            if len(output) >= request["limit"]:
                truncated = True
                break
            item = {"line_number": line_number,
                    "content": line.strip()[:request.get("context_chars", 240)]}
            serialized = json.dumps(item, ensure_ascii=False, separators=(",", ":"))
            if used + len(serialized) > 300000:
                truncated = output_limited = True
                break
            used += len(serialized)
            output.append(item)
        print(json.dumps({"count": len(output), "matches": output,
                          "truncated": truncated, "output_limited": output_limited}, ensure_ascii=False))
    else:
        found = list(itertools.islice(compiled.finditer(request["text"]), request["limit"] + 1))
        output = []
        used = 0
        output_limited = False
        for match in found[:request["limit"]]:
            item = {
                "match": match.group(0)[:2000],
                "start": match.start(),
                "end": match.end(),
                "groups": [None if value is None else value[:500] for value in match.groups()[:50]],
                "named_groups": {key: None if value is None else value[:500]
                                 for key, value in list(match.groupdict().items())[:50]},
                "groups_truncated": len(match.groups()) > 50 or len(match.groupdict()) > 50,
            }
            serialized = json.dumps(item, ensure_ascii=False, separators=(",", ":"))
            if used + len(serialized) > 300000:
                output_limited = True
                break
            used += len(serialized)
            output.append(item)
        print(json.dumps({"count": len(output), "matches": output,
                          "truncated": len(found) > request["limit"] or output_limited,
                          "output_limited": output_limited}, ensure_ascii=False))
except re.error as exc:
    print(json.dumps({"error": str(exc)}))
'''


def _regex_worker_search(pattern: str, text: str, flags: str, limit: int,
                         line_mode: bool = False, context_chars: int = 240,
                         timeout: float = 2.0) -> Dict[str, Any]:
    """Evaluate regex work out-of-process with CPU, memory, time and output limits."""
    clean_env = {"PATH": "/usr/bin:/bin", "LANG": "C.UTF-8", "PYTHONHASHSEED": "0",
                 "PYTHONIOENCODING": "utf-8", "PYTHONDONTWRITEBYTECODE": "1"}
    request = {"pattern": pattern, "text": text, "flags": flags, "limit": limit,
               "line_mode": line_mode, "context_chars": context_chars}
    proc = _run_subprocess(
        [sys.executable or "python3", "-I", "-S", "-c", _REGEX_WORKER],
        timeout=timeout, cwd=tempfile.gettempdir(), env=clean_env,
        cpu_seconds=max(1, int(timeout)), address_space_mb=256,
        file_size_mb=1, max_output_bytes=500000,
        stdin_text=json.dumps(request, ensure_ascii=False),
    )
    if proc["exit_code"] != 0:
        raise RuntimeError(f"Regex worker exited abnormally ({proc['exit_code']}): {proc['stderr'][-1000:]}")
    try:
        result = json.loads(proc["stdout"])
    except json.JSONDecodeError as exc:
        raise RuntimeError("Regex worker returned an invalid or oversized result") from exc
    if not isinstance(result, dict):
        raise RuntimeError("Regex worker returned a non-object result")
    return result


def regex_search(pattern: str, text: str, flags: str = "", max_matches: int = 200) -> str:
    """Run a regex in a resource-limited worker and return matches, groups and spans."""
    try:
        if not isinstance(pattern, str) or not pattern:
            return _error("pattern must be a non-empty string")
        if len(pattern) > 2000:
            return _error("pattern is too long (max 2000 chars)")
        flag_map = {"i": re.I, "m": re.M, "s": re.S, "x": re.X, "a": re.A, "u": re.U}
        normalized_flags = str(flags or "").lower()
        for char in normalized_flags:
            if char not in flag_map:
                return _error(f"Unsupported regex flag '{char}'", supported=sorted(flag_map))
        if _regex_has_nested_repeats(pattern):
            return _error("Pattern contains nested/ambiguous repetitions that may cause excessive backtracking")
        content = str(text or "")
        if len(content) > MAX_TEXT_CHARS:
            return _error(f"text exceeds the {MAX_TEXT_CHARS} character search limit")
        limit = _bounded_int(max_matches, 200, 1, 5000)
        result = _regex_worker_search(pattern, content, normalized_flags, limit, timeout=2)
        if result.get("error"):
            return _error(f"Invalid regular expression: {result['error']}")
        return _ok({"pattern": pattern, "flags": normalized_flags,
                    "count": result.get("count", 0), "truncated": result.get("truncated", False),
                    "output_limited": result.get("output_limited", False),
                    "matches": result.get("matches", [])})
    except subprocess.TimeoutExpired:
        return _error("Regex evaluation exceeded its 2 second time limit")
    except Exception as exc:
        return _error(f"regex_search failed: {exc}")


def base64_encode_decode(text: str, mode: str = "encode", url_safe: bool = False) -> str:
    """Base64 encode or decode a string (standard or URL-safe alphabet)."""
    try:
        mode = str(mode or "encode").lower().strip()
        url_safe = _to_bool(url_safe, False)
        if mode.startswith("enc"):
            raw = str(text).encode("utf-8")
            if len(raw) > MAX_TEXT_CHARS * 4:
                return _error("text exceeds the configured base64 conversion limit")
            encoded = (base64.urlsafe_b64encode if url_safe else base64.b64encode)(raw).decode("ascii")
            if len(encoded) > MAX_TEXT_CHARS * 4:
                return _error("encoded output exceeds the configured base64 conversion limit")
            return _ok({"mode": "encode", "result": encoded, "input_bytes": len(raw)})
        if not (mode.startswith("dec") or mode == "decode"):
            return _error("mode must be 'encode' or 'decode'")
        payload = str(text).strip()
        if len(payload) > MAX_TEXT_CHARS * 4:
            return _error("base64 input exceeds the configured conversion limit")
        payload += "=" * (-len(payload) % 4)
        raw = base64.b64decode(payload.encode("ascii"),
                                altchars=b"-_" if (url_safe or "-" in payload or "_" in payload) else None,
                                validate=True)
        if len(raw) > MAX_TEXT_CHARS * 2:
            return _error("decoded output exceeds the configured base64 conversion limit")
        try:
            decoded = raw.decode("utf-8")
            binary = False
        except UnicodeDecodeError:
            decoded = raw.hex()
            binary = True
        return _ok({"mode": "decode", "result": decoded, "is_binary": binary, "bytes": len(raw)})
    except (binascii.Error, ValueError) as exc:
        return _error(f"base64_encode_decode failed: invalid base64 ({exc})")
    except Exception as exc:
        return _error(f"base64_encode_decode failed: {exc}")


def url_encode_decode(text: str, mode: str = "encode", component: bool = True) -> str:
    """Percent-encode or decode a URL or query component."""
    try:
        mode = str(mode or "encode").lower().strip()
        text = str(text)
        if len(text.encode("utf-8")) > MAX_TEXT_CHARS * 4:
            return _error("text exceeds the configured URL conversion limit")
        if mode.startswith("enc"):
            safe = "" if _to_bool(component, True) else "/:?#[]@!$&'()*+,;="
            encoded = urllib.parse.quote(text, safe=safe)
            if len(encoded) > MAX_TEXT_CHARS * 4:
                return _error("encoded output exceeds the configured URL conversion limit")
            return _ok({"mode": "encode", "result": encoded})
        if mode.startswith("dec") or mode == "decode":
            return _ok({"mode": "decode", "result": urllib.parse.unquote_plus(text)})
        return _error("mode must be 'encode' or 'decode'")
    except Exception as exc:
        return _error(f"url_encode_decode failed: {exc}")


def hash_text(text: str, algorithm: str = "sha256", hmac_key: str = "") -> str:
    """Hash a string (optionally as an HMAC) with a validated algorithm."""
    try:
        alg = str(algorithm or "sha256").lower().strip().replace("-", "_")
        if alg not in hashlib.algorithms_available:
            return _error(f"Unsupported algorithm '{algorithm}'",
                          supported=sorted(hashlib.algorithms_guaranteed))
        raw = str(text).encode("utf-8")
        if hmac_key:
            digest = hmac.new(str(hmac_key).encode("utf-8"), raw, alg).hexdigest()
            return _ok({"algorithm": alg, "hmac": True, "digest": digest})
        hasher = hashlib.new(alg, raw)
        try:
            digest = hasher.hexdigest()
        except TypeError:  # shake_* need a length
            digest = hasher.hexdigest(32)  # type: ignore[call-arg]
        return _ok({"algorithm": alg, "hmac": False, "digest": digest, "input_bytes": len(raw)})
    except Exception as exc:
        return _error(f"hash_text failed: {exc}")


def uuid_generate(version: int = 4, count: int = 1, namespace_name: str = "") -> str:
    """Generate UUIDs (v1, v4, or v5 with a DNS namespace name)."""
    try:
        version = _bounded_int(version, 4, 1, 5)
        if version not in (1, 4, 5):
            return _error("version must be one of 1, 4 or 5", supported=[1, 4, 5])
        count = _bounded_int(count, 1, 1, 100)
        values = []
        for _ in range(count):
            if version == 1:
                values.append(str(uuid.uuid1()))
            elif version == 5:
                if not namespace_name:
                    return _error("version 5 requires namespace_name")
                values.append(str(uuid.uuid5(uuid.NAMESPACE_DNS, str(namespace_name))))
            else:
                values.append(str(uuid.uuid4()))
        return _ok({"version": version, "count": len(values), "uuids": values, "uuid": values[0]})
    except Exception as exc:
        return _error(f"uuid_generate failed: {exc}")


def random_string(length: int = 32, charset: str = "alphanumeric", count: int = 1) -> str:
    """Generate cryptographically strong random strings/tokens."""
    try:
        import secrets
        length = _bounded_int(length, 32, 1, 4096)
        count = _bounded_int(count, 1, 1, 100)
        charset = str(charset or "alphanumeric")
        if len(charset) > 4096:
            return _error("custom charset exceeds 4,096 characters")
        alphabets = {
            "alphanumeric": string.ascii_letters + string.digits,
            "alpha": string.ascii_letters,
            "digits": string.digits,
            "hex": string.hexdigits.lower()[:16],
            "urlsafe": string.ascii_letters + string.digits + "-_",
            "password": string.ascii_letters + string.digits + "!@#$%^&*()-_=+[]{}",
        }
        alphabet = alphabets.get(str(charset or "alphanumeric").lower())
        if alphabet is None:
            alphabet = str(charset)
            if len(set(alphabet)) < 2:
                return _error("charset must be a preset name or contain at least 2 characters",
                              presets=sorted(alphabets))
        values = ["".join(secrets.choice(alphabet) for _ in range(length)) for _ in range(count)]
        return _ok({"length": length, "count": count, "values": values, "value": values[0]})
    except Exception as exc:
        return _error(f"random_string failed: {exc}")


def current_datetime(timezone_offset_hours: float = 0.0, format: str = "") -> str:
    """Current date/time in UTC, local time and epoch seconds."""
    try:
        now_utc = _dt.datetime.now(_dt.timezone.utc)
        requested_offset = float(timezone_offset_hours or 0)
        if not math.isfinite(requested_offset):
            return _error("timezone_offset_hours must be a finite number")
        offset_hours = max(-14.0, min(requested_offset, 14.0))
        offset = _dt.timezone(_dt.timedelta(hours=offset_hours))
        shifted = now_utc.astimezone(offset)
        payload = {
            "epoch_seconds": now_utc.timestamp(),
            "epoch_ms": int(now_utc.timestamp() * 1000),
            "utc_iso": now_utc.isoformat(),
            "local_iso": _dt.datetime.now().astimezone().isoformat(),
            "offset_iso": shifted.isoformat(),
            "utc_date": now_utc.strftime("%Y-%m-%d"),
            "weekday": now_utc.strftime("%A"),
            "week_number": now_utc.isocalendar()[1],
            "timezone_offset_hours": offset_hours,
        }
        if format:
            with contextlib.suppress(Exception):
                payload["formatted"] = shifted.strftime(str(format))
        return _ok(payload)
    except Exception as exc:
        return _error(f"current_datetime failed: {exc}")


def timestamp_convert(value: str, to_format: str = "iso") -> str:
    """Convert between epoch seconds/milliseconds and ISO-8601 timestamps."""
    try:
        text = str(value or "").strip()
        if not text:
            return _error("value must be provided")
        dt_value: Optional[_dt.datetime] = None
        if re.fullmatch(r"-?\d+(\.\d+)?", text):
            number = float(text)
            if abs(number) > 1e11:  # milliseconds
                number /= 1000.0
            dt_value = _dt.datetime.fromtimestamp(number, _dt.timezone.utc)
        else:
            candidate = text.replace("Z", "+00:00")
            with contextlib.suppress(ValueError):
                dt_value = _dt.datetime.fromisoformat(candidate)
            if dt_value is None:
                for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d", "%d/%m/%Y", "%m/%d/%Y",
                            "%a, %d %b %Y %H:%M:%S %z", "%Y%m%d%H%M%S"):
                    with contextlib.suppress(ValueError):
                        dt_value = _dt.datetime.strptime(text, fmt)
                        break
        if dt_value is None:
            return _error(f"Could not parse timestamp: {value}")
        if dt_value.tzinfo is None:
            dt_value = dt_value.replace(tzinfo=_dt.timezone.utc)
        result = {
            "input": text,
            "iso": dt_value.isoformat(),
            "epoch_seconds": dt_value.timestamp(),
            "epoch_ms": int(dt_value.timestamp() * 1000),
            "utc": dt_value.astimezone(_dt.timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC"),
            "human": dt_value.strftime("%A, %d %B %Y %H:%M:%S %z"),
            "age_seconds": round(time.time() - dt_value.timestamp(), 3),
        }
        fmt = str(to_format or "iso").lower()
        if fmt not in {"iso", "epoch_seconds", "epoch_ms", "utc", "human"}:
            return _error("to_format must be one of iso, epoch_seconds, epoch_ms, utc or human")
        result["result"] = result[fmt]
        return _ok(result)
    except Exception as exc:
        return _error(f"timestamp_convert failed: {exc}")


def jwt_decode(token: str) -> str:
    """Decode a JWT's header and payload (signature is NOT verified)."""
    try:
        token_text = str(token or "").strip()
        if len(token_text) > MAX_TEXT_CHARS * 4:
            return _error("JWT exceeds the configured decode limit")
        if token_text.count(".") != 2:
            return _error("Not a JWT: expected exactly three header.payload.signature segments")
        parts = token_text.split(".", 2)

        def decode_part(segment: str) -> Any:
            if not re.fullmatch(r"[A-Za-z0-9_-]*={0,2}", segment) or "=" in segment.rstrip("="):
                raise ValueError("JWT segment is not valid base64url")
            padded = segment + "=" * (-len(segment) % 4)
            raw = base64.b64decode(padded.encode("ascii"), altchars=b"-_", validate=True)
            return json.loads(raw.decode("utf-8"))

        header = decode_part(parts[0])
        payload = decode_part(parts[1])
        if not isinstance(header, dict) or not isinstance(payload, dict):
            return _error("JWT header and payload must be JSON objects")
        info: Dict[str, Any] = {"header": header, "payload": payload,
                                "signature_present": len(parts) > 2 and bool(parts[2]),
                                "signature_verified": False}
        now = time.time()
        for claim in ("exp", "iat", "nbf"):
            if isinstance(payload.get(claim), (int, float)):
                info[f"{claim}_iso"] = _dt.datetime.fromtimestamp(payload[claim], _dt.timezone.utc).isoformat()
        if isinstance(payload.get("exp"), (int, float)):
            info["expired"] = now > payload["exp"]
            info["expires_in_seconds"] = round(payload["exp"] - now, 1)
        return _ok(info)
    except Exception as exc:
        return _error(f"jwt_decode failed: {exc}")


def csv_to_json(csv_text: str = "", filepath: str = "", delimiter: str = ",",
                max_rows: int = 5000) -> str:
    """Convert CSV text (or a sandbox CSV file) into JSON records."""
    try:
        text = str(csv_text or "")
        if filepath:
            source_path = _safe_path(filepath, must_exist=True)
            request_limit = _bounded_int(MCP_MAX_REQUEST_BYTES, 2 * 1024 * 1024,
                                         1024, 32 * 1024 * 1024)
            if os.path.getsize(source_path) > request_limit:
                return _error(f"CSV file exceeds the {request_limit} byte input limit")
            with open(source_path, "r", encoding="utf-8", errors="replace") as f:
                text = f.read(request_limit + 1)
        request_limit = _bounded_int(MCP_MAX_REQUEST_BYTES, 2 * 1024 * 1024,
                                     1024, 32 * 1024 * 1024)
        if len(text.encode("utf-8")) > request_limit:
            return _error(f"CSV input exceeds the {request_limit} byte limit")
        if not text.strip():
            return _error("Provide csv_text= or filepath=")
        limit = _bounded_int(max_rows, 5000, 1, 10000)
        sep = (delimiter or ",")[:1]
        if sep == "\\":
            sep = "\t"
        with contextlib.suppress(Exception):
            sep = csv.Sniffer().sniff(text[:4096], delimiters=",;\t|").delimiter if not delimiter else sep
        if text.count(sep) > 10000:
            return _error("CSV contains too many fields to parse safely (maximum 10,000 delimiters)")
        reader = csv.DictReader(io.StringIO(text), delimiter=sep)
        fieldnames = reader.fieldnames or []
        if len(fieldnames) > 1000:
            return _error("CSV contains too many columns (maximum 1,000)")
        sampled = [dict(row) for row in itertools.islice(reader, limit + 1)]
        truncated = len(sampled) > limit
        rows = sampled[:limit]
        return _ok({"delimiter": sep, "columns": reader.fieldnames or [], "row_count": len(rows),
                    "truncated": truncated, "rows": rows})
    except Exception as exc:
        return _error(f"csv_to_json failed: {exc}")


def json_to_csv(json_string: str, delimiter: str = ",", filepath: str = "") -> str:
    """Convert a JSON array of objects into CSV (optionally written to a file)."""
    try:
        data = json.loads(json_string) if isinstance(json_string, str) else json_string
        if isinstance(data, dict):
            for value in data.values():
                if isinstance(value, list):
                    data = value
                    break
        if not isinstance(data, list) or not data:
            return _error("Expected a non-empty JSON array of objects")
        if len(data) > 10000:
            return _error("JSON array exceeds the 10,000 row conversion limit")
        rows = [row if isinstance(row, dict) else {"value": row} for row in data]
        columns: List[str] = []
        column_set = set()
        for row in rows:
            for key in row:
                if key not in column_set:
                    column_set.add(key)
                    columns.append(key)
                    if len(columns) > 1000:
                        return _error("JSON rows exceed the 1,000 column conversion limit")
        buffer = io.StringIO()
        writer = csv.DictWriter(buffer, fieldnames=columns, delimiter=(delimiter or ",")[:1],
                                extrasaction="ignore")
        writer.writeheader()
        output_limit = _bounded_int(MCP_MAX_REQUEST_BYTES, 2 * 1024 * 1024,
                                    1024, 32 * 1024 * 1024)
        for row in rows:
            writer.writerow({k: (json.dumps(v, ensure_ascii=False) if isinstance(v, (dict, list)) else v)
                             for k, v in row.items()})
            if buffer.tell() > output_limit:
                return _error(f"CSV output exceeds the {output_limit} character limit")
        csv_text = buffer.getvalue()
        if len(csv_text.encode("utf-8")) > output_limit:
            return _error(f"CSV output exceeds the {output_limit} byte limit")
        saved = ""
        if filepath:
            saved = _safe_path(filepath)
            _atomic_write(saved, csv_text)
        return _ok({"columns": columns, "row_count": len(rows), "saved_to": saved,
                    "truncated": len(csv_text) > MAX_TEXT_CHARS,
                    "csv": csv_text[:MAX_TEXT_CHARS]})
    except Exception as exc:
        return _error(f"json_to_csv failed: {exc}")


def _validate_yaml_tree(value: Any, max_nodes: int = 20000,
                        max_chars: int = 1024 * 1024, max_depth: int = 64) -> None:
    """Reject cyclic or excessively expanded YAML aliases before JSON serialization."""
    stack = [(value, 0, False)]
    active = set()
    nodes = 0
    chars = 0
    while stack:
        current, depth, exiting = stack.pop()
        if exiting:
            active.discard(id(current))
            continue
        nodes += 1
        if nodes > max_nodes:
            raise ValueError(f"YAML document exceeds the {max_nodes} node expansion limit")
        if depth > max_depth:
            raise ValueError(f"YAML document exceeds the {max_depth} level nesting limit")
        if isinstance(current, (dict, list, tuple)):
            identity = id(current)
            if identity in active:
                raise ValueError("Cyclic YAML aliases cannot be converted to JSON")
            active.add(identity)
            stack.append((current, depth, True))
            if len(current) > max_nodes:
                raise ValueError(f"YAML collection exceeds the {max_nodes} item limit")
            if isinstance(current, dict):
                for key, item in current.items():
                    stack.append((key, depth + 1, False))
                    stack.append((item, depth + 1, False))
            else:
                stack.extend((item, depth + 1, False) for item in current)
        else:
            chars += len(current) if isinstance(current, str) else len(str(current))
            if chars > max_chars:
                raise ValueError(f"YAML scalar data exceeds the {max_chars} character limit")


def yaml_json_convert(text: str, direction: str = "yaml_to_json") -> str:
    """Convert bounded YAML to JSON or JSON to YAML (requires PyYAML)."""
    try:
        if not HAS_YAML:
            return _error("PyYAML is not installed (pip install pyyaml)")
        direction = str(direction or "yaml_to_json").lower()
        if direction.startswith("yaml"):
            data = _yaml.safe_load(str(text))
            _validate_yaml_tree(data)
            json_text = json.dumps(data, indent=2, ensure_ascii=False, default=_json_default)
            if len(json_text.encode("utf-8")) > 2 * 1024 * 1024:
                return _error("JSON output exceeds the 2 MiB conversion limit")
            return _ok({"direction": "yaml_to_json", "data": data, "json": json_text})
        data = json.loads(text) if isinstance(text, str) else text
        yaml_text = _yaml.safe_dump(data, sort_keys=False, allow_unicode=True)
        if len(yaml_text.encode("utf-8")) > 2 * 1024 * 1024:
            return _error("YAML output exceeds the 2 MiB conversion limit")
        return _ok({"direction": "json_to_yaml", "yaml": yaml_text})
    except Exception as exc:
        return _error(f"yaml_json_convert failed: {exc}")


def text_diff_compare(text1: str, text2: str, context_lines: int = 3, mode: str = "unified") -> str:
    """Diff bounded strings (unified, context, or HTML-free ndiff) with a similarity score."""
    try:
        left, right = str(text1 or ""), str(text2 or "")
        diff_input_limit = min(MAX_TEXT_CHARS * 4, 256 * 1024)
        if len(left) + len(right) > diff_input_limit:
            return _error(f"Combined diff input exceeds {diff_input_limit} characters")
        lines1 = left.splitlines(keepends=True)
        lines2 = right.splitlines(keepends=True)
        context = _bounded_int(context_lines, 3, 0, 50)
        mode = str(mode or "unified").lower()
        if mode not in {"unified", "context", "ndiff"}:
            return _error("mode must be 'unified', 'context' or 'ndiff'")
        if mode == "ndiff":
            diff = list(difflib.ndiff(lines1, lines2))
        elif mode == "context":
            diff = list(difflib.context_diff(lines1, lines2, "text1", "text2", n=context))
        else:
            diff = list(difflib.unified_diff(lines1, lines2, "text1", "text2", n=context))
        added = sum(1 for d in diff if d.startswith("+") and not d.startswith("+++"))
        removed = sum(1 for d in diff if d.startswith("-") and not d.startswith("---"))
        ratio = difflib.SequenceMatcher(None, left, right).ratio()
        diff_text = "".join(diff)
        return _ok({"mode": mode, "identical": left == right, "similarity": round(ratio, 4),
                    "lines_added": added, "lines_removed": removed,
                    "diff_lines_count": len(diff), "truncated": len(diff_text) > MAX_TEXT_CHARS,
                    "diff": diff_text[:MAX_TEXT_CHARS]})
    except Exception as exc:
        return _error(f"text_diff_compare failed: {exc}")


def text_stats(text: str, top_words: int = 15) -> str:
    """Word/character counts, reading time, and keyword frequency for a text."""
    try:
        content = str(text or "")
        if len(content) > 1024 * 1024:
            return _error("text exceeds the 1 MiB analysis limit")
        if not content.strip():
            return _error("text must be a non-empty string")
        words = re.findall(r"[A-Za-z0-9'\-]+", content)
        sentences = [s for s in re.split(r"(?<=[.!?])\s+", content.strip()) if s]
        paragraphs = [p for p in re.split(r"\n\s*\n", content.strip()) if p.strip()]
        syllables = sum(max(1, len(re.findall(r"[aeiouy]+", w.lower()))) for w in words[:20000])
        word_count = len(words)
        counts = Counter(w.lower() for w in words if w.lower() not in _SEARCH_STOPWORDS and len(w) > 2)
        flesch = None
        if word_count and sentences:
            flesch = round(206.835 - 1.015 * (word_count / len(sentences))
                           - 84.6 * (syllables / max(1, word_count)), 2)
        return _ok({
            "characters": len(content),
            "characters_no_spaces": len(re.sub(r"\s", "", content)),
            "words": word_count,
            "unique_words": len({w.lower() for w in words}),
            "sentences": len(sentences),
            "paragraphs": len(paragraphs),
            "lines": len(content.splitlines()),
            "avg_word_length": round(sum(len(w) for w in words) / word_count, 2) if word_count else 0,
            "avg_sentence_words": round(word_count / len(sentences), 2) if sentences else 0,
            "reading_time_minutes": round(word_count / 220, 2),
            "flesch_reading_ease": flesch,
            "top_words": [{"word": w, "count": c}
                          for w, c in counts.most_common(_bounded_int(top_words, 15, 1, 100))],
        })
    except Exception as exc:
        return _error(f"text_stats failed: {exc}")


def text_summarize(text: str, max_sentences: int = 5, query: str = "") -> str:
    """Extractive summary of a text, optionally biased toward a query."""
    try:
        content = str(text or "")
        if len(content) > 1024 * 1024:
            return _error("text exceeds the 1 MiB summarization limit")
        if len(content.strip()) < 40:
            return _error("text is too short to summarise")
        sentences = _bounded_int(max_sentences, 5, 1, 30)
        summary = _summarize_text(content, sentences, query)
        return _ok({"input_chars": len(content), "summary": summary,
                    "summary_chars": len(summary), "keywords": _keywords(content, 12),
                    "compression_ratio": round(len(summary) / max(1, len(content)), 4)})
    except Exception as exc:
        return _error(f"text_summarize failed: {exc}")


def parse_url(url: str) -> str:
    """Break a URL into its components and decode its query string."""
    try:
        parsed = urllib.parse.urlsplit(str(url))
        query_pairs = urllib.parse.parse_qsl(parsed.query, keep_blank_values=True, max_num_fields=2000)
        query_dict: Dict[str, List[str]] = {}
        for key, value in query_pairs:
            query_dict.setdefault(key, []).append(value)
        return _ok({
            "url": url,
            "scheme": parsed.scheme,
            "netloc": parsed.netloc,
            "hostname": parsed.hostname,
            "port": parsed.port,
            "path": parsed.path,
            "path_segments": [s for s in parsed.path.split("/") if s],
            "query": parsed.query,
            "query_dict": query_dict,
            "query_pairs": query_pairs,
            "fragment": parsed.fragment,
            "normalized": _normalize_url_for_dedupe(str(url)),
            "is_private_host": _host_is_private(parsed.hostname or ""),
        })
    except Exception as exc:
        return _error(f"parse_url failed: {exc}")


# ---------------------------------------------------------------------------
# Git Tools
# ---------------------------------------------------------------------------
def _git(args: List[str], cwd: str, timeout: int = 15) -> subprocess.CompletedProcess:
    """Run read-only Git inspection with config, paging, external tools and output bounded."""
    git_args = list(args)
    if git_args and git_args[0] == "diff":
        git_args[1:1] = ["--no-ext-diff", "--no-textconv"]
    env = {"PATH": os.getenv("PATH", "/usr/bin:/bin"), "HOME": SANDBOX_ROOT,
           "LANG": "C.UTF-8", "LC_ALL": "C.UTF-8", "GIT_CONFIG_NOSYSTEM": "1",
           "GIT_CONFIG_GLOBAL": os.devnull, "GIT_CONFIG_SYSTEM": os.devnull,
           "GIT_TERMINAL_PROMPT": "0", "GIT_PAGER": "cat", "PAGER": "cat",
           "GIT_OPTIONAL_LOCKS": "0"}
    command = ["git", "--no-pager", "-c", "core.fsmonitor=false",
               "-c", "core.pager=cat"] + git_args
    result = _run_subprocess(command, timeout=timeout, cwd=cwd, env=env,
                             cpu_seconds=timeout + 5, max_output_bytes=1_000_000,
                             stdout_mode="head")
    completed = subprocess.CompletedProcess(command, result["exit_code"],
                                            result["stdout"], result["stderr"])
    completed.stdout_truncated = result["stdout_truncated"]
    completed.stderr_truncated = result["stderr_truncated"]
    return completed


def git_status(directory: str = ".") -> str:
    """Show `git status --porcelain` plus the current branch for a sandbox repo."""
    try:
        target = _safe_path(directory, must_exist=True)
        if not shutil.which("git"):
            return _error("git is not installed")
        res = _git(["status", "--porcelain=v1", "-b"], target)
        if res.returncode != 0:
            return _error(res.stderr.strip() or "git status failed")
        lines = res.stdout.strip().split("\n") if res.stdout.strip() else []
        branch = lines[0][3:] if lines and lines[0].startswith("##") else ""
        entries = [{"status": line[:2].strip(), "path": line[3:]} for line in lines[1:]]
        return _ok({"directory": target, "branch": branch, "dirty": bool(entries),
                    "change_count": len(entries), "truncated": getattr(res, "stdout_truncated", False),
                    "changes": entries, "status": [f"{e['status']} {e['path']}" for e in entries]})
    except Exception as exc:
        return _error(str(exc))


def git_diff(directory: str = ".", staged: bool = False, max_chars: int = 20000) -> str:
    """Show the working-tree (or staged) diff for a sandbox repo."""
    try:
        target = _safe_path(directory, must_exist=True)
        if not shutil.which("git"):
            return _error("git is not installed")
        args = ["diff"]
        if _to_bool(staged, False):
            args.append("--cached")
        res = _git(args, target, timeout=30)
        if res.returncode != 0 and res.stderr.strip():
            return _error(res.stderr.strip())
        stat = _git(args + ["--stat"], target, timeout=30).stdout
        limit = _bounded_int(max_chars, 20000, 500, MAX_TEXT_CHARS)
        return _ok({"directory": target, "staged": _to_bool(staged, False),
                    "stat": stat.strip()[:5000],
                    "truncated": getattr(res, "stdout_truncated", False) or len(res.stdout) > limit,
                    "diff": res.stdout[:limit]})
    except Exception as exc:
        return _error(f"git_diff failed: {exc}")


def git_log(directory: str = ".", max_entries: int = 20, path_filter: str = "") -> str:
    """Recent commit history (hash, author, date, subject) for a sandbox repo."""
    try:
        target = _safe_path(directory, must_exist=True)
        if not shutil.which("git"):
            return _error("git is not installed")
        limit = _bounded_int(max_entries, 20, 1, 500)
        fmt = "%H%x1f%h%x1f%an%x1f%ae%x1f%aI%x1f%s%x1e"
        args = ["log", f"-{limit}", f"--pretty=format:{fmt}"]
        if path_filter:
            args += ["--", str(path_filter)]
        res = _git(args, target, timeout=30)
        if res.returncode != 0:
            return _error(res.stderr.strip() or "git log failed")
        commits = []
        for record in res.stdout.split("\x1e"):
            record = record.strip("\n")
            if not record:
                continue
            fields = record.split("\x1f")
            if len(fields) >= 6:
                commits.append({"hash": fields[0], "short": fields[1], "author": fields[2],
                                "email": fields[3], "date": fields[4], "subject": fields[5]})
        return _ok({"directory": target, "count": len(commits),
                    "truncated": getattr(res, "stdout_truncated", False), "commits": commits})
    except Exception as exc:
        return _error(f"git_log failed: {exc}")


# ---------------------------------------------------------------------------
# Environment, System & Diagnostics
# ---------------------------------------------------------------------------
def _is_secret_env(name: str) -> bool:
    lowered = str(name).lower()
    return any(hint in lowered for hint in _SECRET_HINTS)


def get_environment_variable(name: str = "", reveal_secrets: bool = False) -> str:
    """Read environment variables; secret-looking values are redacted by default."""
    try:
        reveal = _to_bool(reveal_secrets, False) and ENABLE_DANGEROUS
        name = str(name or "").strip()
        if len(name) > 255:
            return _error("Environment variable name exceeds the 255 character limit")
        if name:
            if not reveal and _is_secret_env(name):
                value = os.getenv(name)
                return _ok({name: "<redacted>", "present": value is not None, "redacted": True,
                            "hint": "set MCP_ENABLE_DANGEROUS=true and reveal_secrets=true to read"})
            return _ok({name: os.getenv(name), "present": os.getenv(name) is not None,
                        "redacted": False})
        env = {}
        redacted = 0
        for key, value in os.environ.items():
            if _is_secret_env(key) and not reveal:
                env[key] = "<redacted>"
                redacted += 1
            else:
                env[key] = value
        return _ok({"count": len(env), "redacted_count": redacted, "variables": env})
    except Exception as exc:
        return _error(str(exc))


def set_environment_variable(name: str, value: str) -> str:
    """Set a non-sensitive process variable only when dangerous tools are enabled."""
    try:
        if not ENABLE_DANGEROUS:
            return _error("Environment mutation is disabled. Set MCP_ENABLE_DANGEROUS=true to enable it.")
        key = str(name or "").strip()
        if len(key) > 255 or not key or not re.match(r"^[A-Za-z_][A-Za-z0-9_]*$", key):
            return _error("name must be a valid environment variable identifier")
        upper = key.upper()
        if upper in _PROTECTED_ENV or upper.startswith(_PROTECTED_ENV_PREFIXES):
            return _error(f"'{key}' is protected: changing it could alter subprocess or network behavior",
                          protected=sorted(_PROTECTED_ENV), protected_prefixes=list(_PROTECTED_ENV_PREFIXES))
        if upper.startswith("MCP_"):
            return _error("MCP_* server configuration variables cannot be changed at runtime")
        text = str(value)
        if "\x00" in text:
            return _error("environment variable values cannot contain null bytes")
        if len(text.encode("utf-8")) > min(MCP_MAX_REQUEST_BYTES, 100000):
            return _error("environment variable value exceeds the 100,000 byte process limit")
        os.environ[key] = text
        return _ok({"status": "success", "name": key,
                    "value": "<redacted>" if _is_secret_env(key) else text})
    except Exception as exc:
        return _error(str(exc))


def system_info() -> str:
    """Platform, Python, resource and sandbox information."""
    try:
        info: Dict[str, Any] = {
            "server": SERVER_NAME,
            "version": SERVER_VERSION,
            "platform": platform.platform(),
            "machine": platform.machine(),
            "python": sys.version,
            "python_executable": sys.executable,
            "processor": platform.processor(),
            "cpu_count": os.cpu_count(),
            "cwd": os.getcwd(),
            "sandbox_root": SANDBOX_ROOT,
            "config_path": CONFIG_PATH if os.path.exists(CONFIG_PATH) else "",
            "user": os.getenv("USER", os.getenv("USERNAME", "unknown")),
            "termux": os.path.exists("/data/data/com.termux"),
            "env_vars_count": len(os.environ),
            "optional_modules": {"psutil": HAS_PSUTIL, "yaml": HAS_YAML, "resource": HAS_RESOURCE},
            "tooling": {name: bool(shutil.which(name))
                        for name in ("git", "node", "patch", "bash", "ps", "curl")},
        }
        if HAS_PSUTIL:
            memory = psutil.virtual_memory()
            info["memory"] = {"total_mb": round(memory.total / 1048576, 1),
                              "used_mb": round(memory.used / 1048576, 1),
                              "percent": memory.percent}
            info["cpu_percent"] = psutil.cpu_percent(interval=0.1)
            with contextlib.suppress(Exception):
                info["open_files"] = len(psutil.Process().open_files())
        with contextlib.suppress(Exception):
            total, used, free = shutil.disk_usage(SANDBOX_ROOT)
            info["disk"] = {"total": _human_bytes(total), "free": _human_bytes(free)}
        return _ok(info)
    except Exception as exc:
        return _error(str(exc))


def ping() -> str:
    """Liveness probe with uptime and aggregate telemetry."""
    with _server_stats_lock:
        stats = dict(_SERVER_STATS)
        tools = {k: dict(v) for k, v in _tool_stats.items()}
    uptime = time.time() - stats["start_time"]
    return _ok({
        "status": "pong",
        "server": SERVER_NAME,
        "version": SERVER_VERSION,
        "time": time.time(),
        "uptime_seconds": round(uptime, 2),
        "uptime_human": str(_dt.timedelta(seconds=int(uptime))),
        "telemetry": {
            "total_requests": stats["total_requests"],
            "successful_calls": stats["successful_calls"],
            "failed_calls": stats["failed_calls"],
            "rejected_auth": stats.get("rejected_auth", 0),
            "bytes_downloaded": stats.get("bytes_downloaded", 0),
            "distinct_tools_used": len(tools),
        },
        "python_version": sys.version.split()[0],
        "platform": platform.platform(),
    })


def server_stats(top_n: int = 20) -> str:
    """Full telemetry: per-tool call counts, timings, failures and cache statistics."""
    try:
        with _server_stats_lock:
            stats = dict(_SERVER_STATS)
            tools = {k: dict(v) for k, v in _tool_stats.items()}
        ranked = sorted(tools.items(), key=lambda kv: kv[1]["calls"], reverse=True)
        limit = _bounded_int(top_n, 20, 1, 500)
        uptime = time.time() - stats["start_time"]
        with _cache_lock:
            cache = dict(_cache_counters)
            cache["entries"] = len(_response_cache)
        total = stats["successful_calls"] + stats["failed_calls"]
        return _ok({
            "uptime_seconds": round(uptime, 2),
            "requests": stats["total_requests"],
            "successful_calls": stats["successful_calls"],
            "failed_calls": stats["failed_calls"],
            "success_rate": round(stats["successful_calls"] / total, 4) if total else None,
            "rejected_auth": stats.get("rejected_auth", 0),
            "bytes_downloaded": stats.get("bytes_downloaded", 0),
            "cache": cache,
            "registered_tools": len(TOOL_MAP),
            "tool_stats": dict(ranked[:limit]),
            "slowest_tools": sorted(
                [{"tool": k, "avg_time": v["avg_time"], "calls": v["calls"]} for k, v in tools.items()],
                key=lambda item: item["avg_time"], reverse=True)[:10],
        })
    except Exception as exc:
        return _error(f"server_stats failed: {exc}")


def cache_stats() -> str:
    """Inspect the in-memory TTL cache (entries, hit rate, memory estimate)."""
    try:
        with _cache_lock:
            counters = dict(_cache_counters)
            entries = len(_response_cache)
            now = time.time()
            ttl_remaining = sorted(round(expires - now, 1) for expires, _ in _response_cache.values())
            approx_bytes = _cache_size_bytes
        lookups = counters["hits"] + counters["misses"]
        return _ok({"entries": entries, "max_entries": CACHE_MAX_ENTRIES,
                    "max_bytes": CACHE_MAX_BYTES, **counters,
                    "hit_rate": round(counters["hits"] / lookups, 4) if lookups else None,
                    "approx_bytes": approx_bytes,
                    "ttl_remaining_seconds": ttl_remaining[:50],
                    "search_cache_ttl": SEARCH_CACHE_TTL, "fetch_cache_ttl": FETCH_CACHE_TTL})
    except Exception as exc:
        return _error(f"cache_stats failed: {exc}")


def cache_clear() -> str:
    """Drop every cached search/fetch response."""
    try:
        removed = _cache_clear()
        return _ok({"status": "cleared", "entries_removed": removed})
    except Exception as exc:
        return _error(f"cache_clear failed: {exc}")


def list_tools(filter: str = "", category: str = "") -> str:
    """List every registered tool with its parameters, optionally filtered."""
    try:
        needle = str(filter or "").lower().strip()
        wanted = str(category or "").lower().strip()
        tools = []
        for name, schema in sorted(TOOL_SCHEMAS.items()):
            info = TOOL_CATEGORIES.get(name, "misc")
            if wanted and info != wanted:
                continue
            doc = (TOOL_MAP[name].__doc__ or "").strip().split("\n")[0]
            if needle and needle not in name.lower() and needle not in doc.lower():
                continue
            tools.append({"name": name, "category": info, "description": doc,
                          "parameters": schema})
        return _ok({"count": len(tools), "total_registered": len(TOOL_MAP),
                    "categories": sorted(set(TOOL_CATEGORIES.values())), "tools": tools})
    except Exception as exc:
        return _error(f"list_tools failed: {exc}")


def tool_help(name: str) -> str:
    """Full documentation and JSON schema for a single tool."""
    try:
        tool = TOOL_MAP.get(str(name or "").strip())
        if tool is None:
            close = difflib.get_close_matches(str(name or ""), list(TOOL_MAP), n=5, cutoff=0.4)
            return _error(f"Unknown tool '{name}'", did_you_mean=close)
        schema = TOOL_SCHEMAS.get(name, {})
        return _ok({"name": name, "category": TOOL_CATEGORIES.get(name, "misc"),
                    "description": textwrap.dedent(tool.__doc__ or "").strip(),
                    "parameters": schema, "input_schema": _build_mcp_input_schema(schema),
                    "example": {"tool": name,
                                "args": {k: v.split("=", 1)[1] if "=" in str(v) else f"<{v}>"
                                         for k, v in schema.items()}}})
    except Exception as exc:
        return _error(f"tool_help failed: {exc}")


def self_diagnostics(include_network: bool = True) -> str:
    """Run internal health checks: sandbox, config, tool registry, network, optional deps."""
    checks: List[Dict[str, Any]] = []

    def add(name: str, ok: bool, detail: Any = ""):
        checks.append({"check": name, "ok": bool(ok), "detail": detail})

    try:
        add("sandbox_root_writable", os.access(SANDBOX_ROOT, os.W_OK), SANDBOX_ROOT)
        probe = os.path.join(SANDBOX_ROOT, f".mts_probe_{os.getpid()}")
        try:
            _atomic_write(probe, "ok")
            with open(probe, "r", encoding="utf-8") as handle:
                add("atomic_write", handle.read() == "ok")
        finally:
            with contextlib.suppress(OSError):
                os.unlink(probe)
        add("config_file", not _config_error, _config_error or CONFIG_PATH)
        missing_schema = sorted(set(TOOL_MAP) - set(TOOL_SCHEMAS))
        orphan_schema = sorted(set(TOOL_SCHEMAS) - set(TOOL_MAP))
        add("tool_registry_consistent", not missing_schema and not orphan_schema,
            {"missing_schema": missing_schema, "orphan_schema": orphan_schema,
             "tool_count": len(TOOL_MAP)})
        uncategorised = sorted(set(TOOL_MAP) - set(TOOL_CATEGORIES))
        add("tools_categorised", not uncategorised, uncategorised)
        add("auth_configured", (not REQUIRE_AUTH) or bool(API_TOKEN),
            "REQUIRE_AUTH is on but no token is set" if REQUIRE_AUTH and not API_TOKEN else "ok")
        add("path_sandbox_enforced", _sandbox_escape_blocked(), "traversal attempts rejected")
        add("ssrf_guard", _host_is_private("127.0.0.1") and _host_is_private("169.254.169.254"))
        add("optional_psutil", True, HAS_PSUTIL)
        add("optional_yaml", True, HAS_YAML)
        add("resource_limits_available", True, HAS_RESOURCE)
        add("dangerous_execution_enabled", True, ENABLE_DANGEROUS)
        add("search_engines_available", bool(available_engines()), available_engines())
        if _to_bool(include_network, True):
            try:
                result = _http_fetch("https://duckduckgo.com/", timeout=8, max_bytes=65536)
                add("network_egress", 200 <= result["status"] < 400, result["status"])
            except Exception as exc:
                add("network_egress", False, f"{type(exc).__name__}: {exc}"[:200])
        failed = [c["check"] for c in checks if not c["ok"]]
        return _json_result({"ok": not failed, "checks": checks, "failed": failed,
                             "server": SERVER_NAME, "version": SERVER_VERSION})
    except Exception as exc:
        return _error(f"self_diagnostics failed: {exc}", checks=checks)


def _sandbox_escape_blocked() -> bool:
    for attempt in ("../../../../etc/passwd", "/etc/passwd", "~/../../etc/shadow"):
        try:
            _safe_path(attempt)
            return False
        except (ValueError, FileNotFoundError):
            continue
    return True


def health_check() -> str:
    """Compact readiness probe for monitoring systems."""
    with _server_stats_lock:
        uptime = time.time() - _SERVER_STATS["start_time"]
        failed = _SERVER_STATS["failed_calls"]
        total = _SERVER_STATS["successful_calls"] + failed
    return _ok({"status": "healthy", "server": SERVER_NAME, "version": SERVER_VERSION,
                "uptime_seconds": round(uptime, 2), "tools": len(TOOL_MAP),
                "error_rate": round(failed / total, 4) if total else 0.0,
                "auth_required": REQUIRE_AUTH, "dangerous_enabled": ENABLE_DANGEROUS})


# ---------------------------------------------------------------------------
# Tool Registry & Schemas
# ---------------------------------------------------------------------------
TOOL_MAP: Dict[str, Callable] = {
    # --- search & research ---
    "web_search": web_search,
    "web_search_news": web_search_news,
    "web_search_images": web_search_images,
    "web_search_answer": web_search_answer,
    "web_search_suggestions": web_search_suggestions,
    "wikipedia_lookup": wikipedia_lookup,
    "arxiv_search": arxiv_search,
    "github_search": github_search,
    "stackexchange_search": stackexchange_search,
    "research_topic": research_topic,
    "fetch_many_urls": fetch_many_urls,
    "url_to_markdown": url_to_markdown,
    "fetch_webpage_text": fetch_webpage_text,
    # --- networking ---
    "http_request": http_request,
    "fetch_json_api": fetch_json_api,
    "web_download_file": web_download_file,
    "web_scrape_links": web_scrape_links,
    "extract_metadata": extract_metadata,
    "dns_lookup": dns_lookup,
    "check_url_status": check_url_status,
    "parse_url_headers": parse_url_headers,
    "read_url_hardened": read_url_hardened,
    "sitemap_parse": sitemap_parse,
    "rss_feed_parse": rss_feed_parse,
    "whois_lookup": whois_lookup,
    "port_check": port_check,
    # --- html parsing ---
    "parse_html_document": parse_html_document,
    "extract_structured_data": extract_structured_data,
    "parse_html_tables": parse_html_tables,
    "extract_media_links": extract_media_links,
    "parse_robots_txt": parse_robots_txt,
    "discover_feed_links": discover_feed_links,
    # --- filesystem ---
    "read_file": read_file,
    "write_file": write_file,
    "edit_file_replace": edit_file_replace,
    "append_to_file": append_to_file,
    "list_directory": list_directory,
    "file_stat": file_stat,
    "delete_file": delete_file,
    "make_directory": make_directory,
    "copy_file": copy_file,
    "move_file": move_file,
    "file_checksum": file_checksum,
    "search_files": search_files,
    "search_file_content": search_file_content,
    "file_tree": file_tree,
    "disk_usage": disk_usage,
    "compress_decompress_archive": compress_decompress_archive,
    "archive_list": archive_list,
    "apply_patch": apply_patch,
    # --- execution ---
    "lint_code": lint_code,
    "execute_python_code": execute_python_code,
    "execute_javascript_code": execute_javascript_code,
    "execute_bash_command": execute_bash_command,
    "safe_execute_python": safe_execute_python,
    "process_list": process_list,
    # --- data & text ---
    "json_parse_validate": json_parse_validate,
    "json_query": json_query,
    "regex_search": regex_search,
    "base64_encode_decode": base64_encode_decode,
    "url_encode_decode": url_encode_decode,
    "hash_text": hash_text,
    "uuid_generate": uuid_generate,
    "random_string": random_string,
    "current_datetime": current_datetime,
    "timestamp_convert": timestamp_convert,
    "jwt_decode": jwt_decode,
    "csv_to_json": csv_to_json,
    "json_to_csv": json_to_csv,
    "yaml_json_convert": yaml_json_convert,
    "text_diff_compare": text_diff_compare,
    "text_stats": text_stats,
    "text_summarize": text_summarize,
    "parse_url": parse_url,
    # --- git ---
    "git_status": git_status,
    "git_diff": git_diff,
    "git_log": git_log,
    # --- memory ---
    "memory_store": memory_store,
    "memory_recall": memory_recall,
    "memory_list": memory_list,
    "memory_delete": memory_delete,
    "memory_search": memory_search,
    # --- server & system ---
    "ping": ping,
    "health_check": health_check,
    "system_info": system_info,
    "server_stats": server_stats,
    "self_diagnostics": self_diagnostics,
    "cache_stats": cache_stats,
    "cache_clear": cache_clear,
    "list_tools": list_tools,
    "tool_help": tool_help,
    "get_environment_variable": get_environment_variable,
    "set_environment_variable": set_environment_variable,
}

TOOL_SCHEMAS: Dict[str, Dict[str, str]] = {
    # --- search & research ---
    "web_search": {"query": "str", "max_results": "int=5", "page": "int=1", "domain": "str=",
                   "safe_search": "bool=false", "engines": "str=auto", "time_range": "str=",
                   "region": "str=us-en", "fetch_content": "bool=false",
                   "content_chars": "int=2000", "use_cache": "bool=true"},
    "web_search_news": {"query": "str", "max_results": "int=10", "time_range": "str=week",
                        "region": "str=US:en", "safe_search": "bool=false"},
    "web_search_images": {"query": "str", "max_results": "int=10", "safe_search": "bool=true"},
    "web_search_answer": {"query": "str", "include_wikipedia": "bool=true"},
    "web_search_suggestions": {"query": "str", "max_results": "int=10"},
    "wikipedia_lookup": {"query": "str", "language": "str=en", "sentences": "int=5"},
    "arxiv_search": {"query": "str", "max_results": "int=10", "sort_by": "str=relevance"},
    "github_search": {"query": "str", "search_type": "str=repositories", "max_results": "int=10",
                      "sort": "str="},
    "stackexchange_search": {"query": "str", "site": "str=stackoverflow", "max_results": "int=10",
                             "tagged": "str="},
    "research_topic": {"query": "str", "max_sources": "int=5", "max_chars_per_source": "int=4000",
                       "summary_sentences": "int=6", "include_news": "bool=false"},
    "fetch_many_urls": {"urls": "str", "max_chars": "int=4000", "as_markdown": "bool=false"},
    "url_to_markdown": {"url": "str", "max_chars": "int=20000", "include_links": "bool=true"},
    "fetch_webpage_text": {"url": "str", "max_chars": "int=8000", "summarize": "bool=false"},
    # --- networking ---
    "http_request": {"url": "str", "method": "str=GET", "headers": "str={}", "data": "str=",
                     "timeout_seconds": "int=0", "max_response_chars": "int=5000"},
    "fetch_json_api": {"url": "str", "headers": "str={}", "method": "str=GET", "data": "str="},
    "web_download_file": {"url": "str", "destination_filepath": "str", "max_bytes": "int=0",
                          "overwrite": "bool=true"},
    "web_scrape_links": {"url": "str", "filter_domain": "bool=false", "max_links": "int=300"},
    "extract_metadata": {"url": "str"},
    "dns_lookup": {"domain": "str", "record_types": "str=A"},
    "check_url_status": {"url": "str", "follow_redirects": "bool=true"},
    "parse_url_headers": {"url": "str"},
    "read_url_hardened": {"url": "str", "max_chars": "int=64000", "max_redirects": "int=5"},
    "sitemap_parse": {"sitemap_url": "str", "max_urls": "int=500", "recurse": "bool=false"},
    "rss_feed_parse": {"feed_url": "str", "max_items": "int=0"},
    "whois_lookup": {"domain": "str", "timeout_seconds": "int=10"},
    "port_check": {"host": "str", "ports": "str=80,443", "timeout_seconds": "int=3"},
    # --- html parsing ---
    "parse_html_document": {"html": "str", "base_url": "str=", "max_chars": "int=64000"},
    "extract_structured_data": {"html": "str", "base_url": "str="},
    "parse_html_tables": {"html": "str", "max_tables": "int=20", "max_rows": "int=200"},
    "extract_media_links": {"html": "str", "base_url": "str=", "max_results": "int=500"},
    "parse_robots_txt": {"text": "str=", "user_agent": "str=*", "url": "str="},
    "discover_feed_links": {"html": "str=", "base_url": "str=", "url": "str="},
    # --- filesystem ---
    "read_file": {"filepath": "str", "start_line": "int=1", "line_count": "int=500"},
    "write_file": {"filepath": "str", "content": "str", "create_backup": "bool=false"},
    "edit_file_replace": {"filepath": "str", "target_snippet": "str", "replacement_snippet": "str",
                          "replace_all": "bool=false"},
    "append_to_file": {"filepath": "str", "content": "str"},
    "list_directory": {"path": "str=.", "show_hidden": "bool=true", "sort_by": "str=name"},
    "file_stat": {"filepath": "str"},
    "delete_file": {"filepath": "str", "recursive": "bool=false"},
    "make_directory": {"path": "str"},
    "copy_file": {"source": "str", "destination": "str"},
    "move_file": {"source": "str", "destination": "str"},
    "file_checksum": {"filepath": "str", "algorithm": "str=sha256"},
    "search_files": {"directory": "str=.", "pattern": "str=*", "max_depth": "int=5",
                     "max_results": "int=200", "include_dirs": "bool=false"},
    "search_file_content": {"directory": "str=.", "query": "str", "file_extension": "str=",
                            "case_insensitive": "bool=false", "is_regex": "bool=false",
                            "max_results": "int=200", "max_depth": "int=12",
                            "context_chars": "int=240"},
    "file_tree": {"path": "str=.", "max_depth": "int=4",
                  "ignore": "str=.git,__pycache__,node_modules,.venv", "max_entries": "int=1000"},
    "disk_usage": {"path": "str=.", "top_n": "int=15"},
    "compress_decompress_archive": {"archive_path": "str", "action": "str=extract",
                                    "target_directory": "str=.", "format": "str=",
                                    "max_entries": "int=5000", "max_unpacked_bytes": "int=268435456"},
    "archive_list": {"archive_path": "str", "max_entries": "int=500"},
    "apply_patch": {"filepath": "str", "patch": "str", "mode": "str=auto"},
    # --- execution ---
    "lint_code": {"language": "str", "code": "str=", "filepath": "str="},
    "execute_python_code": {"code": "str", "timeout_seconds": "int=30"},
    "execute_javascript_code": {"code": "str", "timeout_seconds": "int=30"},
    "execute_bash_command": {"command": "str", "timeout_seconds": "int=60"},
    "safe_execute_python": {"code": "str", "timeout_seconds": "int=15", "memory_mb": "int=256"},
    "process_list": {"filter_name": "str=", "max_results": "int=50"},
    # --- data & text ---
    "json_parse_validate": {"json_string": "str", "pretty": "bool=false"},
    "json_query": {"json_string": "str", "path": "str=", "default": "str="},
    "regex_search": {"pattern": "str", "text": "str", "flags": "str=", "max_matches": "int=200"},
    "base64_encode_decode": {"text": "str", "mode": "str=encode", "url_safe": "bool=false"},
    "url_encode_decode": {"text": "str", "mode": "str=encode", "component": "bool=true"},
    "hash_text": {"text": "str", "algorithm": "str=sha256", "hmac_key": "str="},
    "uuid_generate": {"version": "int=4", "count": "int=1", "namespace_name": "str="},
    "random_string": {"length": "int=32", "charset": "str=alphanumeric", "count": "int=1"},
    "current_datetime": {"timezone_offset_hours": "number=0", "format": "str="},
    "timestamp_convert": {"value": "str", "to_format": "str=iso"},
    "jwt_decode": {"token": "str"},
    "csv_to_json": {"csv_text": "str=", "filepath": "str=", "delimiter": "str=,",
                    "max_rows": "int=5000"},
    "json_to_csv": {"json_string": "str", "delimiter": "str=,", "filepath": "str="},
    "yaml_json_convert": {"text": "str", "direction": "str=yaml_to_json"},
    "text_diff_compare": {"text1": "str", "text2": "str", "context_lines": "int=3",
                          "mode": "str=unified"},
    "text_stats": {"text": "str", "top_words": "int=15"},
    "text_summarize": {"text": "str", "max_sentences": "int=5", "query": "str="},
    "parse_url": {"url": "str"},
    # --- git ---
    "git_status": {"directory": "str=."},
    "git_diff": {"directory": "str=.", "staged": "bool=false", "max_chars": "int=20000"},
    "git_log": {"directory": "str=.", "max_entries": "int=20", "path_filter": "str="},
    # --- memory ---
    "memory_store": {"key": "str", "value": "str", "tags": "str="},
    "memory_recall": {"key": "str=", "tag": "str="},
    "memory_list": {},
    "memory_delete": {"key": "str=", "tag": "str=", "confirm_all": "bool=false"},
    "memory_search": {"query": "str", "search_values": "bool=true", "max_results": "int=25"},
    # --- server & system ---
    "ping": {},
    "health_check": {},
    "system_info": {},
    "server_stats": {"top_n": "int=20"},
    "self_diagnostics": {"include_network": "bool=true"},
    "cache_stats": {},
    "cache_clear": {},
    "list_tools": {"filter": "str=", "category": "str="},
    "tool_help": {"name": "str"},
    "get_environment_variable": {"name": "str=", "reveal_secrets": "bool=false"},
    "set_environment_variable": {"name": "str", "value": "str"},
}

TOOL_CATEGORIES: Dict[str, str] = {}
for _group, _names in {
    "search": ["web_search", "web_search_news", "web_search_images", "web_search_answer",
               "web_search_suggestions", "wikipedia_lookup", "arxiv_search", "github_search",
               "stackexchange_search", "research_topic"],
    "web": ["fetch_many_urls", "url_to_markdown", "fetch_webpage_text", "http_request",
            "fetch_json_api", "web_download_file", "web_scrape_links", "extract_metadata",
            "dns_lookup", "check_url_status", "parse_url_headers", "read_url_hardened",
            "sitemap_parse", "rss_feed_parse", "whois_lookup", "port_check",
            "parse_html_document", "extract_structured_data", "parse_html_tables",
            "extract_media_links", "parse_robots_txt", "discover_feed_links"],
    "files": ["read_file", "write_file", "edit_file_replace", "append_to_file", "list_directory",
              "file_stat", "delete_file", "make_directory", "copy_file", "move_file",
              "file_checksum", "search_files", "search_file_content", "file_tree", "disk_usage",
              "compress_decompress_archive", "archive_list", "apply_patch"],
    "execution": ["lint_code", "execute_python_code", "execute_javascript_code",
                  "execute_bash_command", "safe_execute_python", "process_list"],
    "data": ["json_parse_validate", "json_query", "regex_search", "base64_encode_decode",
             "url_encode_decode", "hash_text", "uuid_generate", "random_string",
             "current_datetime", "timestamp_convert", "jwt_decode", "csv_to_json", "json_to_csv",
             "yaml_json_convert", "text_diff_compare", "text_stats", "text_summarize", "parse_url"],
    "git": ["git_status", "git_diff", "git_log"],
    "memory": ["memory_store", "memory_recall", "memory_list", "memory_delete", "memory_search"],
    "system": ["ping", "health_check", "system_info", "server_stats", "self_diagnostics",
               "cache_stats", "cache_clear", "list_tools", "tool_help",
               "get_environment_variable", "set_environment_variable"],
}.items():
    for _name in _names:
        TOOL_CATEGORIES[_name] = _group

# Tools that are gated behind MCP_ENABLE_DANGEROUS
DANGEROUS_TOOLS = {"execute_python_code", "execute_javascript_code", "execute_bash_command",
                   "safe_execute_python"}


def _parse_default(param_type: str, raw: str) -> Any:
    if param_type == "int":
        with contextlib.suppress(ValueError):
            return int(raw)
        return None
    if param_type == "number":
        with contextlib.suppress(ValueError):
            return float(raw)
        return None
    if param_type == "bool":
        return _to_bool(raw, False)
    return raw


def _build_mcp_input_schema(tschema: dict) -> dict:
    """Convert the compact "int=5" spec strings into a JSON Schema object."""
    properties: Dict[str, Any] = {}
    required: List[str] = []
    for key, spec in (tschema or {}).items():
        spec_str = str(spec)
        has_default = "=" in spec_str
        param_type = (spec_str.split("=", 1)[0] if has_default else spec_str).strip() or "str"
        json_type = {"int": "integer", "number": "number", "bool": "boolean"}.get(param_type, "string")
        prop: Dict[str, Any] = {"type": json_type}
        if has_default:
            default_raw = spec_str.split("=", 1)[1]
            parsed = _parse_default(param_type, default_raw)
            if parsed is not None and not (json_type == "string" and parsed == ""):
                prop["default"] = parsed
            elif json_type == "string":
                prop["default"] = ""
        properties[key] = prop
        if not has_default:
            required.append(key)
    return {"type": "object", "properties": properties, "required": required,
            "additionalProperties": False}


def _coerce_args(tool_name: str, args: Dict[str, Any]) -> Tuple[Dict[str, Any], List[str]]:
    """Validate and coerce one JSON object against the registered tool schema."""
    schema = TOOL_SCHEMAS.get(tool_name, {})
    cleaned: Dict[str, Any] = {}
    problems: List[str] = []
    if not isinstance(args, dict):
        return cleaned, ["arguments must be a JSON object"]
    for key, value in args.items():
        if not isinstance(key, str) or key not in schema:
            close = difflib.get_close_matches(str(key), list(schema), n=1, cutoff=0.6)
            problems.append(f"unknown parameter '{key}'" + (f" (did you mean '{close[0]}'?)" if close else ""))
            continue
        spec = str(schema[key])
        param_type = (spec.split("=", 1)[0] if "=" in spec else spec).strip() or "str"
        if value is None:
            continue
        try:
            if param_type == "int":
                if isinstance(value, bool):
                    cleaned[key] = int(value)
                else:
                    number = float(value)
                    if not math.isfinite(number) or not number.is_integer():
                        raise ValueError
                    cleaned[key] = int(number)
            elif param_type == "number":
                number = float(value)
                if not math.isfinite(number):
                    raise ValueError
                cleaned[key] = number
            elif param_type == "bool":
                if isinstance(value, bool):
                    cleaned[key] = value
                elif isinstance(value, (int, float)):
                    if not math.isfinite(float(value)) or value not in (0, 1):
                        raise ValueError
                    cleaned[key] = bool(value)
                else:
                    text = str(value).strip().lower()
                    if text not in _TRUTHY | _FALSY:
                        raise ValueError
                    cleaned[key] = text in _TRUTHY
            else:
                if isinstance(value, str):
                    cleaned[key] = value
                elif isinstance(value, (dict, list)):
                    cleaned[key] = json.dumps(value, default=_json_default)
                else:
                    cleaned[key] = str(value)
        except (TypeError, ValueError, OverflowError):
            problems.append(f"parameter '{key}' must be of type {param_type}")
    missing = [k for k, spec in schema.items() if "=" not in str(spec) and k not in cleaned]
    for key in missing:
        problems.append(f"missing required parameter '{key}'")
    return cleaned, problems


def call_tool(name: str, args: Optional[Dict[str, Any]] = None) -> str:
    """Invoke a registered tool by name with validation, coercion and telemetry."""
    if not isinstance(name, str):
        return _error("tool name must be a string")
    func = TOOL_MAP.get(name)
    if func is None:
        close = difflib.get_close_matches(name, list(TOOL_MAP), n=5, cutoff=0.4)
        return _error(f"unknown tool '{name}'", did_you_mean=close)
    if args is None:
        args = {}
    if not isinstance(args, dict):
        _record_tool(name, False, 0.0, "arguments must be a JSON object")
        return _error("invalid arguments: arguments must be a JSON object",
                      tool=name, expected=TOOL_SCHEMAS.get(name, {}))
    try:
        args_size = len(json.dumps(args, ensure_ascii=False, default=_json_default).encode("utf-8"))
    except (TypeError, ValueError, RecursionError, UnicodeError) as exc:
        _record_tool(name, False, 0.0, str(exc))
        return _error(f"invalid arguments: {exc}", tool=name)
    if args_size > MCP_MAX_REQUEST_BYTES:
        _record_tool(name, False, 0.0, "arguments exceed configured request size limit")
        return _error(f"invalid arguments: arguments exceed {MCP_MAX_REQUEST_BYTES} bytes",
                      tool=name)
    cleaned, problems = _coerce_args(name, args)
    if problems:
        message = "; ".join(problems[:6])
        _record_tool(name, False, 0.0, message)
        return _error("invalid arguments: " + message,
                      tool=name, expected=TOOL_SCHEMAS.get(name, {}))
    started = time.perf_counter()
    try:
        result = func(**cleaned)
    except TypeError as exc:
        _record_tool(name, False, time.perf_counter() - started, str(exc))
        return _error(f"invalid arguments for '{name}': {exc}", expected=TOOL_SCHEMAS.get(name, {}))
    except Exception as exc:
        log.exception("Tool %s raised", name)
        _record_tool(name, False, time.perf_counter() - started, str(exc))
        return _error(f"{type(exc).__name__}: {exc}", tool=name)
    result_text = result if isinstance(result, str) else _json_result(result)
    failed = False
    with contextlib.suppress(Exception):
        parsed = json.loads(result_text)
        failed = isinstance(parsed, dict) and (bool(parsed.get("error")) or parsed.get("ok") is False)
    _record_tool(name, not failed, time.perf_counter() - started,
                 "tool returned ok=false" if failed else None)
    return result_text


# ---------------------------------------------------------------------------
# HTTP & JSON-RPC 2.0 Server Handler
# ---------------------------------------------------------------------------
PROTOCOL_VERSION = "2024-11-05"


class Handler(BaseHTTPRequestHandler):
    MAX_BODY_BYTES = _bounded_int(MCP_MAX_REQUEST_BYTES, 2 * 1024 * 1024,
                                  1024, 32 * 1024 * 1024)
    server_version = f"{SERVER_NAME}/{SERVER_VERSION}"
    sys_version = ""
    protocol_version = "HTTP/1.1"

    def setup(self):
        super().setup()
        # Bound slow/idle clients so an incomplete request cannot hold a handler
        # thread indefinitely.
        self.connection.settimeout(15)

    # -- helpers ------------------------------------------------------------
    def _set_cors(self):
        self.send_header("Access-Control-Allow-Origin", CORS_ORIGIN)
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type, Authorization, X-API-Key, Mcp-Session-Id")
        self.send_header("Access-Control-Max-Age", "86400")

    def _json(self, code: int, obj: Any):
        body = json.dumps(obj, ensure_ascii=False, indent=2, default=_json_default).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("X-Content-Type-Options", "nosniff")
        if self.close_connection:
            self.send_header("Connection", "close")
        self._set_cors()
        self.end_headers()
        with contextlib.suppress(Exception):
            self.wfile.write(body)

    def _check_auth(self) -> bool:
        if not REQUIRE_AUTH:
            return True
        if not API_TOKEN:
            log.error("REQUIRE_AUTH is enabled but no API token is configured - denying request")
            return False
        auth = self.headers.get("Authorization", "")
        if auth.lower().startswith("bearer "):
            token = auth.split(" ", 1)[1].strip()
            if token and hmac.compare_digest(token, API_TOKEN):
                return True
        header_token = (self.headers.get("X-API-Key") or "").strip()
        if header_token and hmac.compare_digest(header_token, API_TOKEN):
            return True
        if ALLOW_TOKEN_IN_QUERY:
            try:
                query = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query,
                                              max_num_fields=100)
            except ValueError:
                return False
            supplied = (query.get("token") or [""])[0]
            if supplied and hmac.compare_digest(supplied, API_TOKEN):
                return True
        return False

    def _deny(self):
        _bump("rejected_auth")
        _bump("failed_calls")
        self._json(401, {"error": "unauthorized - send 'Authorization: Bearer <token>'"})

    def _read_body(self) -> Optional[bytes]:
        # BaseHTTPRequestHandler does not decode chunked request bodies. Reject
        # them explicitly rather than leaving unread bytes on a keep-alive socket.
        if self.headers.get("Transfer-Encoding"):
            self.close_connection = True
            self._json(501, {"error": "Transfer-Encoding request bodies are not supported"})
            return None
        try:
            length = int(self.headers.get("Content-Length", 0) or 0)
        except (TypeError, ValueError):
            self.close_connection = True
            self._json(400, {"error": "invalid Content-Length"})
            return None
        if length < 0 or length > self.MAX_BODY_BYTES:
            self.close_connection = True
            self._json(413, {"error": f"request body exceeds limit of {self.MAX_BODY_BYTES} bytes"})
            return None
        if not length:
            return b"{}"
        chunks, remaining = [], length
        while remaining > 0:
            chunk = self.rfile.read(min(65536, remaining))
            if not chunk:
                break
            chunks.append(chunk)
            remaining -= len(chunk)
        if remaining:
            self.close_connection = True
            self._json(400, {"error": "incomplete request body"})
            return None
        return b"".join(chunks) or b"{}"

    @staticmethod
    def _tool_descriptor(name: str) -> Dict[str, Any]:
        doc = (TOOL_MAP[name].__doc__ or f"Tool {name}").strip().split("\n")[0]
        return {"name": name, "description": doc,
                "inputSchema": _build_mcp_input_schema(TOOL_SCHEMAS.get(name, {}))}

    # -- verbs --------------------------------------------------------------
    def do_OPTIONS(self):
        self.send_response(204)
        self.send_header("Content-Length", "0")
        self._set_cors()
        self.end_headers()

    def do_GET(self):
        path = self.path.split("?")[0].rstrip("/") or "/"
        if path in ("/health", "/healthz", "/ping"):
            self._json(200, json.loads(health_check()))
            return
        if not self._check_auth():
            self._deny()
            return
        if path in ("/", "/tools", "/mcp", "/mcp/v1/tools"):
            self._json(200, {
                "status": "online",
                "server": SERVER_NAME,
                "version": SERVER_VERSION,
                "protocolVersion": PROTOCOL_VERSION,
                "total_tools": len(TOOL_SCHEMAS),
                "categories": sorted(set(TOOL_CATEGORIES.values())),
                "search_engines": available_engines(),
                "tools": TOOL_SCHEMAS,
            })
            return
        if path == "/tools/detailed":
            self._json(200, {"tools": [self._tool_descriptor(n) for n in sorted(TOOL_MAP)]})
            return
        if path == "/stats":
            self._json(200, json.loads(server_stats()))
            return
        if path == "/diagnostics":
            self._json(200, json.loads(self_diagnostics(include_network=False)))
            return
        self._json(404, {"error": "not found",
                         "endpoints": ["/", "/health", "/tools", "/tools/detailed", "/stats",
                                       "/diagnostics", "POST /", "POST /<tool_name>"]})

    def do_POST(self):
        _bump("total_requests")
        if not self._check_auth():
            self._deny()
            return
        raw = self._read_body()
        if raw is None:
            return
        try:
            payload = json.loads(raw.decode("utf-8"))
        except Exception as exc:
            self._json(400, {"error": f"invalid JSON: {exc}"})
            return

        is_rpc = isinstance(payload, dict) and payload.get("jsonrpc") == "2.0"
        req_id = payload.get("id") if isinstance(payload, dict) else None

        # ---- JSON-RPC protocol methods ----
        if is_rpc:
            method = payload.get("method")
            if method == "initialize":
                self._json(200, {"jsonrpc": "2.0", "id": req_id, "result": {
                    "protocolVersion": PROTOCOL_VERSION,
                    "capabilities": {"tools": {"listChanged": False}, "logging": {}},
                    "serverInfo": {"name": SERVER_NAME, "version": SERVER_VERSION},
                }})
                return
            if method in ("notifications/initialized", "notifications/cancelled"):
                if req_id is None:
                    self.send_response(202)
                    self.send_header("Content-Length", "0")
                    self._set_cors()
                    self.end_headers()
                else:
                    self._json(200, {"jsonrpc": "2.0", "id": req_id, "result": {}})
                return
            if method == "ping":
                self._json(200, {"jsonrpc": "2.0", "id": req_id, "result": {}})
                return
            if method in ("tools/list", "list_tools"):
                self._json(200, {"jsonrpc": "2.0", "id": req_id,
                                 "result": {"tools": [self._tool_descriptor(n) for n in sorted(TOOL_MAP)]}})
                return

        # ---- resolve tool name + arguments from the many supported shapes ----
        func_name: Optional[str] = None
        args: Any = {}
        path_parts = [p for p in self.path.split("?")[0].split("/") if p]
        if path_parts and path_parts[-1] in TOOL_MAP:
            func_name = path_parts[-1]
            args = payload if isinstance(payload, dict) else {}

        if isinstance(payload, dict):
            if is_rpc:
                params = payload.get("params", {})
                if isinstance(params, dict):
                    func_name = func_name or params.get("name") or params.get("tool")
                    args = params.get("arguments") or params.get("args") or params.get("input") or args
                    if payload.get("method") in TOOL_MAP and not func_name:
                        func_name = payload["method"]
            if not func_name:
                tool_info = payload.get("tool") or payload.get("function") or payload.get("call")
                if isinstance(tool_info, dict):
                    func_name = tool_info.get("func") or tool_info.get("name")
                    args = tool_info.get("args") or tool_info.get("arguments") or {}
                elif isinstance(tool_info, str):
                    func_name = tool_info
                    args = payload.get("args") or payload.get("arguments") or {}
            if not func_name:
                func_name = payload.get("func") or payload.get("name") or payload.get("action")
                args = args or payload.get("args") or payload.get("arguments") or {}
            if isinstance(args, str):
                with contextlib.suppress(Exception):
                    args = json.loads(args)
            if isinstance(args, dict):
                args = {k: v for k, v in args.items()
                        if k not in {"jsonrpc", "id", "method", "params", "func", "tool", "name",
                                     "action", "args", "arguments"} or k in TOOL_SCHEMAS.get(str(func_name), {})}

        if not func_name or not isinstance(func_name, str):
            _bump("failed_calls")
            if is_rpc:
                self._json(200, {"jsonrpc": "2.0", "id": req_id,
                                 "error": {"code": -32600, "message": "Missing tool name"}})
            else:
                self._json(400, {"error": "missing tool name",
                                 "hint": "POST {\"tool\": \"web_search\", \"args\": {...}}"})
            return

        if func_name not in TOOL_MAP:
            _bump("failed_calls")
            close = difflib.get_close_matches(func_name, list(TOOL_MAP), n=5, cutoff=0.4)
            if is_rpc:
                self._json(200, {"jsonrpc": "2.0", "id": req_id,
                                 "error": {"code": -32601,
                                           "message": f"Tool '{func_name}' not found",
                                           "data": {"did_you_mean": close}}})
            else:
                self._json(404, {"error": f"unknown tool '{func_name}'", "did_you_mean": close,
                                 "available": sorted(TOOL_MAP)})
            return

        if not isinstance(args, dict):
            args = {}

        log.info("Calling tool: %s%s", func_name, f" {sorted(args)}" if args else "")
        result_text = call_tool(func_name, args)
        try:
            result_obj = json.loads(result_text)
        except Exception:
            result_obj = {"result": result_text}
        is_error = (isinstance(result_obj, dict)
                    and (bool(result_obj.get("error")) or result_obj.get("ok") is False))
        _bump("failed_calls" if is_error else "successful_calls")

        if is_rpc:
            content_text = json.dumps(result_obj, ensure_ascii=False, default=_json_default)
            self._json(200, {"jsonrpc": "2.0", "id": req_id,
                             "result": {"content": [{"type": "text", "text": content_text}],
                                        "isError": is_error}})
        else:
            self._json(200 if not is_error else 400, result_obj)

    def log_message(self, fmt, *args):  # noqa: A003 - BaseHTTPRequestHandler API
        log.info("%s - %s", self.client_address[0], _redact(fmt % args))

    def log_error(self, fmt, *args):
        log.warning("%s - %s", self.client_address[0], _redact(fmt % args))


# ---------------------------------------------------------------------------
# Server Launcher Entrypoint
# ---------------------------------------------------------------------------
def _startup_warnings() -> List[str]:
    warnings = []
    if REQUIRE_AUTH and not API_TOKEN:
        warnings.append("MCP_REQUIRE_AUTH is enabled but MCP_API_TOKEN is empty - all requests will be rejected")
    if not REQUIRE_AUTH and BIND_HOST not in ("127.0.0.1", "localhost", "::1"):
        warnings.append(f"Listening on {BIND_HOST} WITHOUT authentication - set MCP_REQUIRE_AUTH=true "
                        "and MCP_API_TOKEN, or bind to 127.0.0.1 (MCP_BIND_HOST)")
    if ENABLE_DANGEROUS:
        warnings.append("Dangerous execution tools are ENABLED (python/node/bash)")
    if ALLOW_PRIVATE_NETWORKS:
        warnings.append("SSRF protection is relaxed: private network destinations are allowed")
    if CORS_ORIGIN == "*" and REQUIRE_AUTH:
        warnings.append("CORS origin is '*'; browsers on any origin may call this server")
    return warnings


def run_server(port: Optional[int] = None, host: Optional[str] = None):
    """Start the threaded HTTP/JSON-RPC server."""
    ThreadingHTTPServer.allow_reuse_address = True
    ThreadingHTTPServer.daemon_threads = True
    bind_host = host or BIND_HOST
    bind_port = int(port or HTTP_PORT)
    server = ThreadingHTTPServer((bind_host, bind_port), Handler)
    log.info("=" * 68)
    log.info("%s%s (Production Hardened v%s)%s", GREEN, SERVER_NAME, SERVER_VERSION, RESET)
    log.info("Listening on http://%s:%s", bind_host, bind_port)
    log.info("Authentication required : %s", REQUIRE_AUTH)
    log.info("Sandbox root            : %s", SANDBOX_ROOT)
    log.info("Dangerous execution     : %s", ENABLE_DANGEROUS)
    log.info("Registered tools        : %d across %d categories",
             len(TOOL_MAP), len(set(TOOL_CATEGORIES.values())))
    log.info("Search engines          : %s", ", ".join(available_engines()) or "none")
    for warning in _startup_warnings():
        log.warning("! %s", warning)
    log.info("=" * 68)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        log.info("Shutting down tool server gracefully...")
    finally:
        with contextlib.suppress(Exception):
            server.shutdown()
        with contextlib.suppress(Exception):
            server.server_close()


def _cli(argv: List[str]) -> int:
    if len(argv) > 1 and argv[1] in ("--help", "-h"):
        print(f"{SERVER_NAME} v{SERVER_VERSION}\n"
              f"  python3 mts.py                     start the server on {BIND_HOST}:{HTTP_PORT}\n"
              "  python3 mts.py --port 9000         start on a specific port\n"
              "  python3 mts.py --list-tools        print the tool registry\n"
              "  python3 mts.py --self-test         run internal diagnostics and exit\n"
              "  python3 mts.py --call TOOL JSON    invoke one tool from the CLI\n")
        return 0
    if len(argv) > 1 and argv[1] == "--list-tools":
        print(list_tools())
        return 0
    if len(argv) > 1 and argv[1] == "--self-test":
        report = json.loads(self_diagnostics(include_network=False))
        print(_json_result(report))
        return 0 if report.get("ok") else 1
    if len(argv) > 2 and argv[1] == "--call":
        tool = argv[2]
        args = json.loads(argv[3]) if len(argv) > 3 else {}
        print(call_tool(tool, args))
        return 0
    port = HTTP_PORT
    if len(argv) > 2 and argv[1] == "--port":
        port = int(argv[2])
    run_server(port=port)
    return 0


if __name__ == "__main__":
    sys.exit(_cli(sys.argv))
