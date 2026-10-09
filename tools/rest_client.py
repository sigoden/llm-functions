#!/usr/bin/env python3
# ==============================================================================
# rest_client.py — Pyrmethus AIChat REST API & Web Request Tool
# argc/aichat compatible · Colorized UI · Safe Caching · Agent CWD Resolution
#
# @describe Structured REST API client supporting custom headers, JSON payloads, and response truncation.
#
# @meta require-tools aichat
#
# @option --target! <URL>                Target URL endpoint (required)
# @option --method <METHOD>              HTTP Method: GET/POST/PUT/PATCH/DELETE (default: GET)
# @option --header <KEY=VAL>             Custom HTTP request header (repeatable)
# @option --data <BODY>                  Request payload string or JSON body
# @option --param <KEY=VAL>              Query parameter (repeatable)
# @option --mode <MODE>                  Execution mode: summary/detailed (default: summary)
# @option --timeout <SEC>                HTTP request timeout in seconds (default: 15.0)
# @option --save-to <PATH>               Save full response body to disk
# @flag   --use-cache                    Enable caching for GET requests
# @flag   --clear-cache                  Clear cache directory and exit
# @flag   --schema                       Print JSON Tool Schema for LLM registration and exit
# @flag   --no-color                     Disable ANSI color output
# @flag   --verbose                      Enable detailed debug log output
#
# @env LLM_OUTPUT=/dev/stdout            Output path for LLM integration
# @env LLM_TOOL_MODE=summary             Default execution mode override
# ==============================================================================

from __future__ import annotations

import argparse
import hashlib
import json
import logging
import os
import pickle
import re
import signal
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timedelta
from enum import Enum
from pathlib import Path
from typing import Any, Literal, Optional

__version__ = "2.5.0"
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
VALID_METHODS = {"GET", "POST", "PUT", "PATCH", "DELETE", "HEAD", "OPTIONS"}


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
    method: str,
    mode: str,
    timeout: Optional[float] = None,
) -> Optional[dict[str, Any]]:
    """Validate input parameters strictly before processing."""
    if not target or not target.strip():
        return {
            "success": False,
            "error": "Target URL endpoint is required.",
            "exit_code": EXIT_INVALID_INPUT,
            "duration_ms": 0.0,
        }
    if method.upper() not in VALID_METHODS:
        return {
            "success": False,
            "error": f"Invalid method '{method}'. Allowed choices: {sorted(list(VALID_METHODS))}",
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
    """Resilient JSON encoder handling Path, Enum, datetime, timedelta, bytes, and sets."""

    def default(self, obj: Any) -> Any:
        try:
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
    return _ANSI_RE.sub("", text)


def _is_tty() -> bool:
    return sys.stderr.isatty() and os.environ.get("TERM", "").lower() not in ("dumb", "")


def _cprint(text: str, file: Any = None, no_color: bool = False, end: str = "\n") -> None:
    target = file or sys.stderr
    if no_color or not _is_tty():
        text = _strip_ansi(text)
    print(text, file=target, flush=True, end=end)


def print_human_readable_ui(data: dict[str, Any], no_color: bool = False) -> None:
    if not _is_tty() or no_color:
        return

    success = data.get("success", False)
    status_code = data.get("status_code", 0)
    
    if success and 200 <= status_code < 300:
        status_color = NEON_GREEN
        status_symbol = "✓"
    elif status_code >= 400:
        status_color = NEON_RED
        status_symbol = "✗"
    else:
        status_color = NEON_YELLOW
        status_symbol = "⚡"

    status_text = f"HTTP {status_code}" if status_code else ("SUCCESS" if success else "FAILED")

    box_w = 68
    border = "─" * box_w

    _cprint(f"{NEON_PURPLE}╭{border}╮{RESET}")
    _cprint(f"{NEON_PURPLE}│{RESET} {NEON_PINK}⚡ [REST CLIENT v{__version__}]{RESET} {status_color}{BOLD}{status_symbol} {status_text}{RESET}")
    _cprint(f"{NEON_PURPLE}├{border}┤{RESET}")
    _cprint(f"{NEON_PURPLE}│{RESET} {NEON_CYAN}URL:{RESET}      {data.get('url', 'N/A')}")
    _cprint(f"{NEON_PURPLE}│{RESET} {NEON_CYAN}Method:{RESET}   {NEON_YELLOW}{data.get('method', 'GET')}{RESET}")
    _cprint(f"{NEON_PURPLE}│{RESET} {NEON_CYAN}Size:{RESET}     {data.get('content_length', 0)} bytes")
    _cprint(f"{NEON_PURPLE}│{RESET} {NEON_CYAN}Type:{RESET}     {data.get('content_type', 'unknown')}")
    _cprint(f"{NEON_PURPLE}│{RESET} {NEON_CYAN}Cached:{RESET}   {NEON_YELLOW}{data.get('cached', False)}{RESET}")
    _cprint(f"{NEON_PURPLE}│{RESET} {NEON_CYAN}Duration:{RESET} {DIM}{data.get('duration_ms', 0)}ms{RESET}")

    if not success and "error" in data:
        _cprint(f"{NEON_PURPLE}├{border}┤{RESET}")
        _cprint(f"{NEON_PURPLE}│{RESET} {NEON_RED}Error:{RESET}    {data['error']}")

    save_path = data.get("saved_to")
    if save_path:
        _cprint(f"{NEON_PURPLE}├{border}┤{RESET}")
        _cprint(f"{NEON_PURPLE}│{RESET} {NEON_GREEN}Saved Body To:{RESET} {save_path}")

    preview = data.get("body_preview")
    if preview:
        _cprint(f"{NEON_PURPLE}├{border}┤{RESET}")
        _cprint(f"{NEON_PURPLE}│{RESET} {BOLD}Response Payload Preview:{RESET}")
        lines = str(preview).splitlines()[:6]
        for line in lines:
            if len(line) > 64:
                line = line[:61] + "..."
            _cprint(f"{NEON_PURPLE}│{RESET}   {DIM}{line}{RESET}")

    _cprint(f"{NEON_PURPLE}╰{border}╯{RESET}")


# ==============================================================================
# SECTION 3: Agent Environment & Path Resolution Helpers
# ==============================================================================

def get_agent_var(name: str, default: str = "") -> str:
    return os.environ.get(f"LLM_AGENT_VAR_{name.upper()}", default)


def get_builtin_var(name: str) -> Optional[str]:
    return os.environ.get(f"LLM_AGENT_VAR_{name}")


def get_execution_context() -> dict[str, Any]:
    termux_prefix = os.environ.get("PREFIX", "")
    return {
        "tool_name": os.environ.get("LLM_TOOL_NAME", "rest_client"),
        "cache_dir": os.environ.get("LLM_TOOL_CACHE_DIR"),
        "root_dir": os.environ.get("LLM_ROOT_DIR"),
        "output_path": os.environ.get("LLM_OUTPUT"),
        "cwd": get_builtin_var("__cwd__") or os.getcwd(),
        "termux_prefix": termux_prefix,
        "is_termux": "com.termux" in termux_prefix or Path("/data/data/com.termux").exists(),
    }


def resolve_agent_path(target_str: str) -> Path:
    raw_path = Path(target_str).expanduser()
    if raw_path.is_absolute():
        return raw_path.resolve(strict=False)

    agent_cwd = get_builtin_var("__cwd__")
    if agent_cwd:
        return (Path(agent_cwd) / raw_path).resolve(strict=False)

    return raw_path.resolve(strict=False)


def env_default(env_name: str, fallback: Any = None) -> dict[str, Any]:
    val = os.getenv(env_name)
    if val is not None:
        return {"default": val}
    if fallback is not None:
        return {"default": fallback}
    return {}


def _parse_key_val_list(items: Optional[list[str]]) -> dict[str, str]:
    if not items:
        return {}
    parsed: dict[str, str] = {}
    for item in items:
        if ":" in item:
            k, v = item.split(":", 1)
            parsed[k.strip()] = v.strip()
        elif "=" in item:
            k, v = item.split("=", 1)
            parsed[k.strip()] = v.strip()
    return parsed


# ==============================================================================
# SECTION 4: Cache Management, Signal Handling & Tool Schema
# ==============================================================================

def build_cache_key(
    url: str,
    method: str,
    headers: dict[str, str],
    params: dict[str, str],
    body_str: Optional[str],
) -> str:
    return "|".join([
        __version__,
        url,
        method.upper(),
        json.dumps(headers, sort_keys=True),
        json.dumps(params, sort_keys=True),
        body_str or "",
    ])


class ToolCache:
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

    def get(self, key_str: str, ttl_seconds: int = 1800) -> Optional[Any]:
        cache_file = self.cache_dir / f"{self._hash_key(key_str)}.cache"
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

    def set(self, key_str: str, value: Any) -> None:
        cache_file = self.cache_dir / f"{self._hash_key(key_str)}.cache"
        tmp_file = cache_file.with_suffix(".tmp")
        try:
            with open(tmp_file, "wb") as fp:
                pickle.dump(value, fp)
            tmp_file.replace(cache_file)
        except Exception:
            if tmp_file.exists():
                tmp_file.unlink(missing_ok=True)


def invalidate_cache(cache: Optional[ToolCache] = None, prefix: str = "") -> int:
    cache_obj = cache or ToolCache()
    removed = 0
    if not cache_obj.cache_dir.exists():
        return removed
    for file in cache_obj.cache_dir.glob("*.cache"):
        if not prefix or file.name.startswith(prefix):
            try:
                file.unlink(missing_ok=True)
                removed += 1
            except OSError:
                pass
    return removed


class GracefulShutdown:
    def __init__(self) -> None:
        self.interrupted = False
        self._old_sigint = None
        self._old_sigterm = None

    def __enter__(self) -> GracefulShutdown:
        self.interrupted = False
        try:
            self._old_sigint = signal.signal(signal.SIGINT, self._handle_signal)
            self._old_sigterm = signal.signal(signal.SIGTERM, self._handle_signal)
        except ValueError:
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
            except ValueError:
                pass
        if getattr(self, "_old_sigterm", None) is not None:
            try:
                signal.signal(signal.SIGTERM, self._old_sigterm)
            except ValueError:
                pass

    def should_stop(self) -> bool:
        return getattr(self, "interrupted", False)


def generate_tool_schema() -> dict[str, Any]:
    return {
        "name": "rest_client",
        "description": "Execute REST/HTTP requests with custom headers, JSON payloads, query parameters, and response truncation.",
        "parameters": {
            "type": "object",
            "properties": {
                "target": {
                    "type": "string",
                    "description": "Target HTTP/HTTPS URL"
                },
                "method": {
                    "type": "string",
                    "enum": ["GET", "POST", "PUT", "PATCH", "DELETE", "HEAD", "OPTIONS"],
                    "description": "HTTP method (default: GET)"
                },
                "header": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Custom HTTP request headers (KEY: VALUE)"
                },
                "data": {
                    "type": "string",
                    "description": "Request body payload string or JSON object string"
                },
                "param": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Query parameters (KEY=VALUE)"
                },
                "mode": {
                    "type": "string",
                    "enum": ["summary", "detailed"],
                    "description": "Execution mode (default: summary)"
                },
                "timeout": {
                    "type": "number",
                    "description": "Timeout in seconds (default: 15.0)"
                },
                "save_to": {
                    "type": "string",
                    "description": "Save raw response payload to output file path"
                },
                "use_cache": {
                    "type": "boolean",
                    "description": "Enable result caching for GET requests"
                }
            },
            "required": ["target"]
        }
    }


# ==============================================================================
# SECTION 5: Core Execution Engine
# ==============================================================================

def execute_tool(
    target: str,
    method: str = "GET",
    headers: Optional[list[str]] = None,
    data: Optional[str] = None,
    params: Optional[list[str]] = None,
    mode: str = "summary",
    timeout: Optional[float] = None,
    save_to: Optional[str] = None,
    use_cache: bool = False,
    no_color: bool = False,
    verbose: bool = False,
) -> dict[str, Any]:
    start_time = time.monotonic()

    bad_inputs = validate_inputs(target, method, mode, timeout)
    if bad_inputs:
        return bad_inputs

    req_method = method.upper()
    req_timeout = timeout if timeout is not None else 15.0

    parsed_headers = _parse_key_val_list(headers)
    parsed_params = _parse_key_val_list(params)

    # Attach User-Agent if not specified
    if not any(k.lower() == "user-agent" for k in parsed_headers):
        parsed_headers["User-Agent"] = f"Pyrmethus-RestClient/{__version__}"

    # Build URL with query parameters
    url_parts = urllib.parse.urlparse(target)
    existing_query = urllib.parse.parse_qs(url_parts.query)
    for pk, pv in parsed_params.items():
        existing_query[pk] = [pv]
    
    new_query = urllib.parse.urlencode(existing_query, doseq=True)
    final_url = urllib.parse.urlunparse((
        url_parts.scheme,
        url_parts.netloc,
        url_parts.path,
        url_parts.params,
        new_query,
        url_parts.fragment,
    ))

    cache = ToolCache()
    cache_key = build_cache_key(final_url, req_method, parsed_headers, parsed_params, data)

    if use_cache and req_method == "GET":
        cached_result = cache.get(cache_key)
        if cached_result is not None:
            cached_result["cached"] = True
            return cached_result

    # Format request body payload
    encoded_body: Optional[bytes] = None
    if data:
        if isinstance(data, str):
            encoded_body = data.encode("utf-8")
        if not any(k.lower() == "content-type" for k in parsed_headers):
            # Auto detect JSON body
            try:
                json.loads(data)
                parsed_headers["Content-Type"] = "application/json"
            except (ValueError, TypeError):
                parsed_headers["Content-Type"] = "text/plain"

    try:
        with GracefulShutdown() as shutdown:
            req = urllib.request.Request(
                url=final_url,
                data=encoded_body,
                headers=parsed_headers,
                method=req_method,
            )

            status_code = 0
            resp_headers: dict[str, str] = {}
            raw_body = b""

            try:
                with urllib.request.urlopen(req, timeout=req_timeout) as resp:
                    status_code = resp.status
                    resp_headers = dict(resp.headers.items())
                    raw_body = resp.read()
            except urllib.error.HTTPError as http_err:
                status_code = http_err.code
                resp_headers = dict(http_err.headers.items())
                raw_body = http_err.read()

            if shutdown.should_stop():
                raise ToolError("Execution interrupted by signal.", EXIT_INTERRUPTED)

            content_type = resp_headers.get("Content-Type", "unknown")
            content_length = len(raw_body)

            # Try parsing response as JSON
            parsed_json: Optional[Any] = None
            if "application/json" in content_type.lower():
                try:
                    parsed_json = json.loads(raw_body.decode("utf-8", errors="replace"))
                except Exception:
                    pass

            text_preview = raw_body.decode("utf-8", errors="replace")
            if len(text_preview) > 2000 and mode == "summary":
                preview_str = text_preview[:2000] + f"\n... [Truncated {len(text_preview) - 2000} bytes]"
            else:
                preview_str = text_preview

            saved_path_str: Optional[str] = None
            if save_to:
                out_path = resolve_agent_path(save_to)
                out_path.parent.mkdir(parents=True, exist_ok=True)
                with open(out_path, "wb") as fp:
                    fp.write(raw_body)
                saved_path_str = str(out_path)

        duration_ms = round((time.monotonic() - start_time) * 1000, 2)
        is_success = 200 <= status_code < 400

        result: dict[str, Any] = {
            "success": is_success,
            "url": final_url,
            "method": req_method,
            "status_code": status_code,
            "content_type": content_type,
            "content_length": content_length,
            "response_headers": resp_headers if mode == "detailed" else None,
            "data_json": parsed_json,
            "body_preview": preview_str,
            "saved_to": saved_path_str,
            "context": get_execution_context(),
            "cached": False,
            "duration_ms": duration_ms,
            "exit_code": EXIT_SUCCESS if is_success else EXIT_ERROR,
        }

        if use_cache and req_method == "GET" and is_success:
            cache.set(cache_key, result)

        return result

    except urllib.error.URLError as exc:
        duration_ms = round((time.monotonic() - start_time) * 1000, 2)
        return {
            "success": False,
            "url": final_url,
            "method": req_method,
            "status_code": 0,
            "error": f"Network URL error: {exc.reason}",
            "exit_code": EXIT_ERROR,
            "duration_ms": duration_ms,
        }
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
# SECTION 6: Output Routing
# ==============================================================================

def write_llm_output(data: dict[str, Any]) -> None:
    out_path = os.environ.get("LLM_OUTPUT", "/dev/stdout")
    payload = json.dumps(data, ensure_ascii=False, cls=ToolJSONEncoder)

    if out_path in {"/dev/stdout", "/dev/fd/1", "-"}:
        sys.stdout.write(payload + "\n")
        sys.stdout.flush()
        return

    try:
        Path(out_path).parent.mkdir(parents=True, exist_ok=True)
        with open(out_path, "a", encoding="utf-8") as fp:
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
    method: Literal["GET", "POST", "PUT", "PATCH", "DELETE", "HEAD", "OPTIONS"] = "GET",
    headers: Optional[list[str]] = None,
    data: Optional[str] = None,
    params: Optional[list[str]] = None,
    mode: Literal["summary", "detailed"] = "summary",
    timeout: Optional[float] = None,
    save_to: Optional[str] = None,
    use_cache: bool = False,
    no_color: bool = False,
    verbose: bool = False,
) -> None:
    """AIChat Tool entrypoint function."""
    result = execute_tool(
        target=target,
        method=method,
        headers=headers,
        data=data,
        params=params,
        mode=mode,
        timeout=timeout,
        save_to=save_to,
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
        prog="rest_client.py",
        description=f"AIChat REST API Tool v{__version__}",
    )
    parser.add_argument(
        "--target", "-t",
        required=False,
        metavar="URL",
        help="Target HTTP/HTTPS URL endpoint",
    )
    parser.add_argument(
        "--method", "-m",
        choices=["GET", "POST", "PUT", "PATCH", "DELETE", "HEAD", "OPTIONS"],
        default="GET",
        help="HTTP method (default: GET)",
    )
    parser.add_argument(
        "--header", "-H",
        action="append",
        dest="headers",
        metavar="KEY:VAL",
        help="Custom HTTP header (repeatable)",
    )
    parser.add_argument(
        "--data", "-d",
        help="Request payload body string or JSON payload",
    )
    parser.add_argument(
        "--param", "-p",
        action="append",
        dest="params",
        metavar="KEY=VAL",
        help="Query parameter (repeatable)",
    )
    parser.add_argument(
        "--mode",
        choices=["summary", "detailed"],
        **env_default("LLM_TOOL_MODE", "summary"),
        help="Execution mode (default: summary)",
    )
    parser.add_argument(
        "--timeout",
        type=float,
        help="Maximum HTTP request timeout in seconds",
    )
    parser.add_argument(
        "--save-to",
        dest="save_to",
        metavar="PATH",
        help="Save raw response payload to file path",
    )
    parser.add_argument(
        "--use-cache",
        action="store_true",
        dest="use_cache",
        help="Enable result caching for GET requests",
    )
    parser.add_argument(
        "--clear-cache",
        action="store_true",
        dest="clear_cache",
        help="Clear tool cache directory and exit",
    )
    parser.add_argument(
        "--schema",
        action="store_true",
        help="Print JSON Tool Schema for LLM registration and exit",
    )
    parser.add_argument(
        "--no-color",
        action="store_true",
        dest="no_color",
        help="Disable ANSI color output",
    )
    parser.add_argument(
        "--verbose", "-v",
        action="store_true",
        help="Enable detailed debug logging",
    )
    return parser


def main() -> int:
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
        method=args.method,
        headers=args.headers,
        data=args.data,
        params=args.params,
        mode=args.mode,
        timeout=args.timeout,
        save_to=args.save_to,
        use_cache=args.use_cache,
        no_color=args.no_color,
        verbose=args.verbose,
    )

    print_human_readable_ui(res, no_color=args.no_color)
    write_llm_output(res)
    return res.get("exit_code", EXIT_SUCCESS)


if __name__ == "__main__":
    sys.exit(main())
