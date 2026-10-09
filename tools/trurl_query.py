#!/usr/bin/env python3
# ==============================================================================
# trurl_query.py — Pyrmethus AIChat Tool Master Template v2.6.0-ASCENDED
# argc/aichat compatible · Colorized UI · Safe Caching · Agent CWD Resolution
#
# @describe Manipulate, clean, extract, format, and transform URL query parameters using trurl or native engine.
#
# @meta require-tools aichat
#
# @option --url -u <URL>                  Target URL to manipulate or query (required)
# @option --append -a <KEY=VAL>           Append a query parameter (repeatable)
# @option --replace <KEY=VAL>             Replace an existing query parameter (repeatable)
# @option --replace-append <KEY=VAL>      Replace parameter if present, otherwise append (repeatable)
# @option --qtrim <PATTERN>               Remove query parameters matching exact name or wildcard (repeatable)
# @option --get -g <FORMAT>               Extract component or parameter value (e.g., '{query:key}', '{query-all:key}', '{url}')
# @option --query-separator <CHAR>        Query parameter separator character (default: &)
# @option --mode <MODE>                  Execution mode: summary/detailed (default: summary)
# @option --timeout <SEC>                Maximum execution timeout in seconds
# @flag   --sort-query                   Alphabetically sort query parameters
# @flag   --json                         Output result in trurl-compatible structured JSON format
# @flag   --force-native                 Force native Python engine instead of system trurl binary
# @flag   --use-cache                    Enable result caching for expensive operations
# @flag   --clear-cache                  Clear tool cache directory and exit
# @flag   --schema                       Print JSON Tool Schema for LLM registration and exit
# @flag   --no-color                     Disable ANSI color output
# @flag   --verbose                      Enable detailed debug log output
#
# @env LLM_OUTPUT=/dev/stdout            Output path for LLM integration
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
import shutil
import subprocess
import sys
import time
import urllib.parse
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
    url: Optional[str],
    mode: str = "summary",
    timeout: Optional[float] = None,
) -> Optional[dict[str, Any]]:
    """Validate input parameters strictly before processing."""
    if not url or not url.strip():
        return {
            "success": False,
            "error": "URL parameter (--url) is required.",
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


def print_human_readable_ui(data: dict[str, Any], no_color: bool = False) -> None:
    """Render human-friendly box UI to stderr."""
    if not _is_tty(no_color=no_color):
        return

    success = data.get("success", False)
    status_color = NEON_GREEN if success else NEON_RED
    status_symbol = "✓" if success else "✗"
    status_text = "SUCCESS" if success else "FAILED"

    box_w = 68
    border = "─" * box_w

    _cprint(f"{NEON_PURPLE}╭{border}╮{RESET}", no_color=no_color)
    _cprint(f"{NEON_PURPLE}│{RESET} {NEON_PINK}⚡ [TRURL QUERY PROCESSOR v{__version__}]{RESET} {status_color}{BOLD}{status_symbol} {status_text}{RESET}", no_color=no_color)
    _cprint(f"{NEON_PURPLE}├{border}┤{RESET}", no_color=no_color)
    _cprint(f"{NEON_PURPLE}│{RESET} {NEON_CYAN}Engine:{RESET}     {NEON_YELLOW}{data.get('engine', 'N/A')}{RESET}", no_color=no_color)
    _cprint(f"{NEON_PURPLE}│{RESET} {NEON_CYAN}Input URL:{RESET}  {data.get('input_url', 'N/A')}", no_color=no_color)

    if success:
        if data.get("output_result") is not None:
            _cprint(f"{NEON_PURPLE}│{RESET} {NEON_CYAN}Result:{RESET}     {NEON_GREEN}{BOLD}{data.get('output_result')}{RESET}", no_color=no_color)
        if data.get("json_data") is not None and data.get("mode") == "detailed":
            _cprint(f"{NEON_PURPLE}├{border}┤{RESET}", no_color=no_color)
            _cprint(f"{NEON_PURPLE}│{RESET} {BOLD}Parsed Parameters:{RESET}", no_color=no_color)
            for item in data.get("json_data", []):
                params = item.get("params", []) if isinstance(item, dict) else []
                for p in params:
                    k, v = p.get("key", ""), p.get("value", "")
                    _cprint(f"{NEON_PURPLE}│{RESET}   {NEON_CYAN}›{RESET} {k} = {NEON_YELLOW}{v}{RESET}", no_color=no_color)

    _cprint(f"{NEON_PURPLE}├{border}┤{RESET}", no_color=no_color)
    _cprint(f"{NEON_PURPLE}│{RESET} {NEON_CYAN}Cached:{RESET}     {NEON_YELLOW}{data.get('cached', False)}{RESET}", no_color=no_color)
    _cprint(f"{NEON_PURPLE}│{RESET} {NEON_CYAN}Duration:{RESET}   {DIM}{data.get('duration_ms', 0)}ms{RESET}", no_color=no_color)

    if not success and "error" in data:
        _cprint(f"{NEON_PURPLE}├{border}┤{RESET}", no_color=no_color)
        _cprint(f"{NEON_PURPLE}│{RESET} {NEON_RED}Error:{RESET}      {data['error']}", no_color=no_color)

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
        "tool_name": os.environ.get("LLM_TOOL_NAME", "trurl_query.py"),
        "cache_dir": os.environ.get("LLM_TOOL_CACHE_DIR"),
        "root_dir": os.environ.get("LLM_ROOT_DIR"),
        "output_path": os.environ.get("LLM_OUTPUT"),
        "cwd": get_builtin_var("__cwd__") or os.getcwd(),
        "platform": platform.system(),
        "python_version": platform.python_version(),
        "termux_prefix": termux_prefix,
        "is_termux": "com.termux" in termux_prefix or Path("/data/data/com.termux").exists(),
    }


# ==============================================================================
# SECTION 4: Cache Management, Signal Handling & Tool Schema
# ==============================================================================

def build_cache_key(
    url: str,
    append: Optional[list[str]],
    replace: Optional[list[str]],
    replace_append: Optional[list[str]],
    qtrim: Optional[list[str]],
    get_format: Optional[str],
    query_separator: str,
    sort_query: bool,
    json_output: bool,
    force_native: bool,
) -> str:
    """Construct pipe-delimited cache key."""
    return "|".join([
        __version__,
        url,
        json.dumps(append or []),
        json.dumps(replace or []),
        json.dumps(replace_append or []),
        json.dumps(qtrim or []),
        get_format or "",
        query_separator,
        str(sort_query),
        str(json_output),
        str(force_native),
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
        "name": "trurl_query",
        "description": "Manipulate, clean, extract, format, and transform URL query parameters using trurl or native engine.",
        "parameters": {
            "type": "object",
            "properties": {
                "url": {
                    "type": "string",
                    "description": "Target URL to manipulate or query (required)"
                },
                "append": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Append query parameter(s) (key=value)"
                },
                "replace": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Replace existing query parameter(s) (key=value)"
                },
                "replace_append": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Replace if present, otherwise append (key=value)"
                },
                "qtrim": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Remove parameters matching string or wildcard (e.g. 'utm_*')"
                },
                "get": {
                    "type": "string",
                    "description": "Extract specific component/value (e.g., '{query:key}', '{query-all:key}')"
                },
                "query_separator": {
                    "type": "string",
                    "description": "Query parameter separator character (default: &)"
                },
                "sort_query": {
                    "type": "boolean",
                    "description": "Alphabetically sort query parameters"
                },
                "json": {
                    "type": "boolean",
                    "description": "Output result in trurl-compatible JSON format"
                },
                "force_native": {
                    "type": "boolean",
                    "description": "Force native Python engine instead of system trurl binary"
                },
                "timeout": {
                    "type": "number",
                    "description": "Maximum execution timeout in seconds"
                }
            },
            "required": ["url"]
        }
    }


# ==============================================================================
# SECTION 5: Core Processing Logic (trurl binary & Python Fallback Engine)
# ==============================================================================

def _parse_kv_pair(item: str) -> Tuple[str, str]:
    """Parse 'key=value' or 'query=key=value' into key and value tuple."""
    text = item.strip()
    if text.startswith("query="):
        text = text[6:]
    if "=" in text:
        k, v = text.split("=", 1)
        return k.strip(), v.strip()
    return text.strip(), ""


def _process_native_url_query(
    url_str: str,
    append_list: Optional[list[str]] = None,
    replace_list: Optional[list[str]] = None,
    replace_append_list: Optional[list[str]] = None,
    qtrim_list: Optional[list[str]] = None,
    get_format: Optional[str] = None,
    query_separator: str = "&",
    sort_query: bool = False,
    json_output: bool = False,
) -> dict[str, Any]:
    """Pure Python URL Query Parameter Processing Engine mimicking trurl semantics."""
    parsed = urllib.parse.urlparse(url_str)
    
    # Parse query parameters keeping duplicate order
    raw_query = parsed.query
    pairs: list[Tuple[str, str]] = []
    
    if raw_query:
        sep = query_separator if query_separator in ("&", ";", ":") else "&"
        for part in raw_query.split(sep):
            if part:
                if "=" in part:
                    k, v = part.split("=", 1)
                    pairs.append((urllib.parse.unquote(k), urllib.parse.unquote(v)))
                else:
                    pairs.append((urllib.parse.unquote(part), ""))

    # 1. Replace parameters (if key present)
    if replace_list:
        for item in replace_list:
            rk, rv = _parse_kv_pair(item)
            if any(k == rk for k, _ in pairs):
                pairs = [(rk, rv) if k == rk else (k, v) for k, v in pairs]

    # 2. Replace or Append
    if replace_append_list:
        for item in replace_append_list:
            rk, rv = _parse_kv_pair(item)
            if any(k == rk for k, _ in pairs):
                pairs = [(rk, rv) if k == rk else (k, v) for k, v in pairs]
            else:
                pairs.append((rk, rv))

    # 3. Append parameters
    if append_list:
        for item in append_list:
            ak, av = _parse_kv_pair(item)
            pairs.append((ak, av))

    # 4. Trim parameters (--qtrim wildcard match)
    if qtrim_list:
        filtered_pairs = []
        for k, v in pairs:
            matched = False
            for pat in qtrim_list:
                pat_clean = pat.strip()
                if fnmatch.fnmatch(k, pat_clean):
                    matched = True
                    break
            if not matched:
                filtered_pairs.append((k, v))
        pairs = filtered_pairs

    # 5. Sort query parameters alphabetically
    if sort_query:
        pairs.sort(key=lambda x: x[0].lower())

    # Build final re-encoded query string
    sep = query_separator
    encoded_query_parts = []
    for k, v in pairs:
        ek = urllib.parse.quote(k, safe="")
        ev = urllib.parse.quote(v, safe="")
        if ev or "=" in raw_query:
            encoded_query_parts.append(f"{ek}={ev}")
        else:
            encoded_query_parts.append(ek)
    new_query = sep.join(encoded_query_parts)

    new_parsed = parsed._replace(query=new_query)
    final_url = urllib.parse.urlunparse(new_parsed)

    # 6. Extraction or JSON formatting
    extracted_text: Optional[str] = None
    json_data: Optional[list[dict[str, Any]]] = None

    if get_format:
        fmt = get_format.strip()
        if fmt.startswith("{query:") and fmt.endswith("}"):
            target_key = fmt[7:-1]
            extracted_text = next((v for k, v in pairs if k == target_key), "")
        elif fmt.startswith("{query-all:") and fmt.endswith("}"):
            target_key = fmt[11:-1]
            all_vals = [v for k, v in pairs if k == target_key]
            extracted_text = " ".join(all_vals)
        elif fmt == "{url}":
            extracted_text = final_url
        elif fmt == "{query}":
            extracted_text = new_query
        elif fmt == "{path}":
            extracted_text = parsed.path or "/"
        elif fmt == "{host}":
            extracted_text = parsed.netloc.split(":")[0]
        elif fmt == "{scheme}":
            extracted_text = parsed.scheme
        else:
            extracted_text = final_url
    else:
        extracted_text = final_url

    if json_output:
        json_params = [{"key": k, "value": v} for k, v in pairs]
        json_data = [{
            "url": final_url,
            "parts": {
                "scheme": parsed.scheme,
                "host": parsed.netloc.split(":")[0],
                "path": parsed.path or "/",
                "query": new_query,
            },
            "params": json_params,
        }]

    return {
        "output_result": extracted_text,
        "json_data": json_data,
        "final_url": final_url,
        "param_count": len(pairs),
    }


def _run_trurl_binary(
    url_str: str,
    append_list: Optional[list[str]] = None,
    replace_list: Optional[list[str]] = None,
    replace_append_list: Optional[list[str]] = None,
    qtrim_list: Optional[list[str]] = None,
    get_format: Optional[str] = None,
    query_separator: str = "&",
    sort_query: bool = False,
    json_output: bool = False,
    timeout: Optional[float] = None,
) -> dict[str, Any]:
    """Execute installed system trurl binary directly."""
    cmd = ["trurl", url_str]

    if append_list:
        for item in append_list:
            val = item if item.startswith("query=") else f"query={item}"
            cmd.extend(["--append", val])
    if replace_list:
        for item in replace_list:
            cmd.extend(["--replace", item])
    if replace_append_list:
        for item in replace_append_list:
            cmd.extend(["--replace-append", item])
    if qtrim_list:
        for item in qtrim_list:
            cmd.extend(["--qtrim", item])
    if sort_query:
        cmd.append("--sort-query")
    if query_separator != "&":
        cmd.extend(["--query-separator", query_separator])
    if get_format:
        cmd.extend(["--get", get_format])
    if json_output:
        cmd.append("--json")

    proc = subprocess.run(
        cmd,
        capture_output=True,
        text=True,
        timeout=timeout or 30,
    )

    if proc.returncode != 0:
        raise ToolError(f"trurl binary execution failed: {proc.stderr.strip()}", EXIT_ERROR)

    output_str = proc.stdout.strip()
    json_data = None

    if json_output:
        try:
            json_data = json.loads(output_str)
        except Exception:
            pass

    return {
        "output_result": output_str,
        "json_data": json_data,
        "final_url": output_str if not json_output else "",
    }


def execute_tool(
    url: str,
    append: Optional[list[str]] = None,
    replace: Optional[list[str]] = None,
    replace_append: Optional[list[str]] = None,
    qtrim: Optional[list[str]] = None,
    get: Optional[str] = None,
    query_separator: str = "&",
    mode: str = "summary",
    timeout: Optional[float] = None,
    sort_query: bool = False,
    json_output: bool = False,
    force_native: bool = False,
    use_cache: bool = False,
    no_color: bool = False,
    verbose: bool = False,
) -> dict[str, Any]:
    """Execute main tool logic and return structured result dictionary."""
    start_time = time.monotonic()

    # Step 1: Input Validation
    bad_inputs = validate_inputs(url, mode, timeout)
    if bad_inputs:
        return bad_inputs

    if verbose:
        try:
            logging.basicConfig(level=logging.DEBUG, format="[DEBUG] %(message)s", force=True)
        except TypeError:
            logging.basicConfig(level=logging.DEBUG, format="[DEBUG] %(message)s")
        logging.debug(f"Processing URL: {url}")

    url_clean = url.strip()
    trurl_binary = shutil.which("trurl")
    use_binary = (trurl_binary is not None) and not force_native
    engine_name = "trurl (binary)" if use_binary else "python (native)"

    cache = ToolCache()
    cache_key = build_cache_key(
        url_clean,
        append,
        replace,
        replace_append,
        qtrim,
        get,
        query_separator,
        sort_query,
        json_output,
        force_native,
    )

    if use_cache:
        cached_result = cache.get(cache_key)
        if cached_result is not None:
            cached_result["cached"] = True
            return cached_result

    try:
        with GracefulShutdown() as shutdown:
            if shutdown.should_stop():
                raise ToolError("Execution interrupted by user signal.", EXIT_INTERRUPTED)

            if use_binary:
                res_data = _run_trurl_binary(
                    url_str=url_clean,
                    append_list=append,
                    replace_list=replace,
                    replace_append_list=replace_append,
                    qtrim_list=qtrim,
                    get_format=get,
                    query_separator=query_separator,
                    sort_query=sort_query,
                    json_output=json_output,
                    timeout=timeout,
                )
            else:
                res_data = _process_native_url_query(
                    url_str=url_clean,
                    append_list=append,
                    replace_list=replace,
                    replace_append_list=replace_append,
                    qtrim_list=qtrim,
                    get_format=get,
                    query_separator=query_separator,
                    sort_query=sort_query,
                    json_output=json_output,
                )

        duration_ms = round((time.monotonic() - start_time) * 1000, 2)

        result: dict[str, Any] = {
            "success": True,
            "engine": engine_name,
            "input_url": url_clean,
            "mode": mode,
            "output_result": res_data.get("output_result"),
            "json_data": res_data.get("json_data"),
            "final_url": res_data.get("final_url"),
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
    except Exception as exc:
        duration_ms = round((time.monotonic() - start_time) * 1000, 2)
        return {
            "success": False,
            "error": f"URL query processing error: {exc}",
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
    url: str,
    append: Optional[list[str]] = None,
    replace: Optional[list[str]] = None,
    replace_append: Optional[list[str]] = None,
    qtrim: Optional[list[str]] = None,
    get: Optional[str] = None,
    query_separator: str = "&",
    mode: Literal["summary", "detailed"] = "summary",
    timeout: Optional[float] = None,
    sort_query: bool = False,
    json_output: bool = False,
    force_native: bool = False,
    use_cache: bool = False,
    no_color: bool = False,
    verbose: bool = False,
) -> None:
    """AIChat Tool entrypoint function."""
    result = execute_tool(
        url=url,
        append=append,
        replace=replace,
        replace_append=replace_append,
        qtrim=qtrim,
        get=get,
        query_separator=query_separator,
        mode=mode,
        timeout=timeout,
        sort_query=sort_query,
        json_output=json_output,
        force_native=force_native,
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
        prog="trurl_query.py",
        description=f"AIChat trurl URL Query Processing Custom Tool v{__version__}",
    )
    parser.add_argument(
        "--url", "-u",
        required=False,
        metavar="URL",
        help="Target URL to manipulate or query",
    )
    parser.add_argument(
        "--append", "-a",
        action="append",
        dest="append",
        metavar="KEY=VAL",
        help="Append a query parameter (repeatable)",
    )
    parser.add_argument(
        "--replace",
        action="append",
        dest="replace",
        metavar="KEY=VAL",
        help="Replace an existing query parameter (repeatable)",
    )
    parser.add_argument(
        "--replace-append",
        action="append",
        dest="replace_append",
        metavar="KEY=VAL",
        help="Replace parameter if present, otherwise append (repeatable)",
    )
    parser.add_argument(
        "--qtrim",
        action="append",
        dest="qtrim",
        metavar="PATTERN",
        help="Remove query parameters matching exact name or wildcard (repeatable)",
    )
    parser.add_argument(
        "--get", "-g",
        dest="get",
        metavar="FORMAT",
        help="Extract component or parameter value (e.g., '{query:key}', '{query-all:key}')",
    )
    parser.add_argument(
        "--query-separator",
        dest="query_separator",
        default="&",
        metavar="CHAR",
        help="Query parameter separator character (default: &)",
    )
    parser.add_argument(
        "--mode",
        choices=["summary", "detailed"],
        default="summary",
        help="Execution mode (default: summary)",
    )
    parser.add_argument(
        "--timeout",
        type=float,
        help="Maximum execution timeout in seconds",
    )
    parser.add_argument(
        "--sort-query",
        action="store_true",
        default=False,
        dest="sort_query",
        help="Alphabetically sort query parameters",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        default=False,
        dest="json_output",
        help="Output result in trurl-compatible JSON format",
    )
    parser.add_argument(
        "--force-native",
        action="store_true",
        default=False,
        dest="force_native",
        help="Force native Python engine instead of system trurl binary",
    )
    parser.add_argument(
        "--use-cache",
        action="store_true",
        default=False,
        dest="use_cache",
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

    if not args.url:
        _cprint(f"{NEON_RED}Error: --url parameter is required for execution.{RESET}", no_color=args.no_color)
        return EXIT_INVALID_INPUT

    res = execute_tool(
        url=args.url,
        append=args.append,
        replace=args.replace,
        replace_append=args.replace_append,
        qtrim=args.qtrim,
        get=args.get,
        query_separator=args.query_separator,
        mode=args.mode,
        timeout=args.timeout,
        sort_query=args.sort_query,
        json_output=args.json_output,
        force_native=args.force_native,
        use_cache=args.use_cache,
        no_color=args.no_color,
        verbose=args.verbose,
    )

    print_human_readable_ui(res, no_color=args.no_color)
    write_llm_output(res)
    return res.get("exit_code", EXIT_SUCCESS)


if __name__ == "__main__":
    sys.exit(main())
