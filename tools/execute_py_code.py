#!/usr/bin/env python3
# ==============================================================================
# python_eval.py — Pyrmethus AIChat Tool Python Code Executor v2.2.0-ASCENDED
# argc/aichat compatible · Human-Readable Colorized Outputs · Native Caching
#
# @describe Execute Python code with output capturing, timing, timeout safety, and context management.
#
# @meta require-tools python3
#
# @option --code! <TEXT>                 Python code to execute (required)
# @option --timeout <NUM>                Execution timeout limit in seconds (default: 30)
# @option --cwd <PATH>                   Working directory for code execution
# @option --env-var <KEY=VALUE>          Custom environment variable (repeatable)
# @flag   --use-cache                    Enable result caching for idempotent operations
# @flag   --no-color                     Disable ANSI color output
# @flag   --verbose                      Enable detailed debug log output
#
# @env LLM_OUTPUT=/dev/stdout            Output path for LLM integration
# ==============================================================================

from __future__ import annotations

import argparse
import contextlib
import hashlib
import io
import json
import logging
import os
import pickle
import re
import signal
import sys
import time
import traceback
from datetime import datetime, timedelta
from enum import Enum
from pathlib import Path
from typing import Any, Optional

__version__ = "2.2.0"
__all__ = [
    "ToolCache",
    "ToolError",
    "__version__",
    "execute_tool",
    "get_agent_var",
    "get_builtin_var",
    "get_execution_context",
    "run",
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

NEON_CYAN = "\033[38;5;51m"
NEON_GREEN = "\033[38;5;46m"
NEON_RED = "\033[38;5;196m"
NEON_YELLOW = "\033[38;5;226m"
NEON_PURPLE = "\033[38;5;129m"
NEON_PINK = "\033[38;5;198m"
RESET = "\033[0m"
BOLD = "\033[1m"
DIM = "\033[2m"

_ANSI_RE = re.compile(r"\x1b(?:[@-Z\\-_]|\[[0-?]*[ -/]*[@-~])|\033\[[0-9;?]*[a-zA-Z]")


def _strip_ansi(text: str) -> str:
    """Remove ANSI escape sequences from text."""
    return _ANSI_RE.sub("", text)


def _is_tty() -> bool:
    """Return True if stderr is attached to an interactive, non-dumb terminal."""
    return sys.stderr.isatty() and os.environ.get("TERM", "").lower() not in (
        "dumb",
        "",
    )


def _cprint(
    text: str, file: Any = None, no_color: bool = False, end: str = "\n"
) -> None:
    """Print pre-formatted ANSI text, stripping colors if non-TTY or --no-color is set."""
    target = file or sys.stderr
    if no_color or not _is_tty():
        text = _strip_ansi(text)
    print(text, file=target, flush=True, end=end)


def print_human_readable_ui(data: dict[str, Any], no_color: bool = False) -> None:
    """Render a human-friendly box UI to stderr for interactive users."""
    if not _is_tty() or no_color:
        return

    success = data.get("success", False)
    status_color = NEON_GREEN if success else NEON_RED
    status_symbol = "✓" if success else "✗"
    status_text = "SUCCESS" if success else "FAILED"

    box_w = 64
    border = "─" * box_w

    _cprint(f"{NEON_PURPLE}╭{border}╮{RESET}")
    _cprint(
        f"{NEON_PURPLE}│{RESET} {NEON_PINK}⚡ [PYTHON CODE EXECUTOR v{__version__}]{RESET} {status_color}{BOLD}{status_symbol} {status_text}{RESET}"
    )
    _cprint(f"{NEON_PURPLE}├{border}┤{RESET}")
    _cprint(
        f"{NEON_PURPLE}│{RESET} {NEON_CYAN}Cached:{RESET}   {NEON_YELLOW}{data.get('cached', False)}{RESET}"
    )
    _cprint(
        f"{NEON_PURPLE}│{RESET} {NEON_CYAN}Duration:{RESET} {DIM}{data.get('duration_ms', 0)}ms{RESET}"
    )

    stdout_val = data.get("stdout", "").strip()
    stderr_val = data.get("stderr", "").strip()

    if stdout_val:
        _cprint(f"{NEON_PURPLE}├{border}┤{RESET}")
        _cprint(f"{NEON_PURPLE}│{RESET} {NEON_GREEN}{BOLD}STDOUT Output:{RESET}")
        for line in stdout_val.splitlines()[:10]:
            _cprint(f"{NEON_PURPLE}│{RESET}   {line}")
        if len(stdout_val.splitlines()) > 10:
            _cprint(f"{NEON_PURPLE}│{RESET}   {DIM}... (output truncated){RESET}")

    if stderr_val:
        _cprint(f"{NEON_PURPLE}├{border}┤{RESET}")
        _cprint(f"{NEON_PURPLE}│{RESET} {NEON_YELLOW}{BOLD}STDERR Output:{RESET}")
        for line in stderr_val.splitlines()[:5]:
            _cprint(f"{NEON_PURPLE}│{RESET}   {line}")

    if not success and "error" in data:
        _cprint(f"{NEON_PURPLE}├{border}┤{RESET}")
        _cprint(f"{NEON_PURPLE}│{RESET} {NEON_RED}{BOLD}Traceback / Error:{RESET}")
        for line in str(data["error"]).strip().splitlines()[-8:]:
            _cprint(f"{NEON_PURPLE}│{RESET}   {NEON_RED}{line}{RESET}")

    _cprint(f"{NEON_PURPLE}╰{border}╯{RESET}")


# ==============================================================================
# SECTION 3: Agent & Environment Helpers
# ==============================================================================


def get_agent_var(name: str, default: str = "") -> str:
    """Access agent user-defined variables (LLM_AGENT_VAR_<NAME>)."""
    return os.environ.get(f"LLM_AGENT_VAR_{name.upper()}", default)


def get_builtin_var(name: str) -> Optional[str]:
    """Access agent built-in environment variables (e.g., __cwd__, __os__)."""
    return os.environ.get(f"LLM_AGENT_VAR_{name}")


def get_execution_context() -> dict[str, Any]:
    """Extract complete execution context."""
    termux_prefix = os.environ.get("PREFIX", "")
    return {
        "tool_name": os.environ.get("LLM_TOOL_NAME", "python_eval"),
        "cache_dir": os.environ.get("LLM_TOOL_CACHE_DIR"),
        "root_dir": os.environ.get("LLM_ROOT_DIR"),
        "output_path": os.environ.get("LLM_OUTPUT"),
        "cwd": get_builtin_var("__cwd__") or os.getcwd(),
        "is_termux": "com.termux" in termux_prefix
        or Path("/data/data/com.termux").exists(),
    }


def _parse_env_vars(env_vars: Optional[list[str]]) -> dict[str, str]:
    """Parse KEY=VALUE environment variable strings."""
    if not env_vars:
        return {}
    parsed: dict[str, str] = {}
    for item in env_vars:
        if "=" in item:
            key, val = item.split("=", 1)
            parsed[key.strip()] = val.strip()
    return parsed


# ==============================================================================
# SECTION 4: Native Caching & Signal Handlers
# ==============================================================================


class ToolCache:
    """Caching utility with TTL support."""

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

    def _make_key(self, key_data: str) -> str:
        return hashlib.sha256(key_data.encode("utf-8")).hexdigest()

    def get(self, key_data: str, ttl_seconds: int = 3600) -> Optional[Any]:
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


class TimeoutException(Exception):
    """Exception raised when execution exceeds timeout limit."""

    pass


def _alarm_handler(signum: int, frame: Any) -> None:
    raise TimeoutException("Code execution timed out.")


# ==============================================================================
# SECTION 5: Core Logic Implementation
# ==============================================================================


def execute_tool(
    code: str,
    timeout: int = 30,
    cwd: Optional[str] = None,
    env_vars: Optional[list[str]] = None,
    use_cache: bool = False,
    no_color: bool = False,
    verbose: bool = False,
) -> dict[str, Any]:
    """Execute Python code and return structured evaluation results."""
    start_time = time.monotonic()

    if verbose:
        logging.basicConfig(level=logging.DEBUG, format="[DEBUG] %(message)s")
        logging.debug("Starting Python code execution.")

    if not code or not code.strip():
        return {
            "success": False,
            "error": "No Python code provided for execution.",
            "stdout": "",
            "stderr": "",
            "exit_code": EXIT_INVALID_INPUT,
            "duration_ms": 0.0,
        }

    # Setup caching key
    cache = ToolCache()
    cache_key = f"py_exec:{hashlib.sha256(code.encode()).hexdigest()}:{cwd}:{env_vars}"
    if use_cache:
        cached_result = cache.get(cache_key)
        if cached_result is not None:
            if verbose:
                logging.debug("Cache hit! Returning cached execution output.")
            cached_result["cached"] = True
            return cached_result

    # Working Directory context
    orig_cwd = os.getcwd()
    target_cwd = Path(cwd).expanduser().resolve() if cwd else Path(orig_cwd)
    if not target_cwd.exists():
        return {
            "success": False,
            "error": f"Specified working directory does not exist: {cwd}",
            "exit_code": EXIT_FILE_NOT_FOUND,
            "duration_ms": 0.0,
        }

    # Custom environment variables
    parsed_env = _parse_env_vars(env_vars)
    orig_env = dict(os.environ)
    os.environ.update(parsed_env)

    stdout_buf = io.StringIO()
    stderr_buf = io.StringIO()

    exec_globals = {
        "__name__": "__main__",
        "__doc__": None,
        "sys": sys,
        "os": os,
    }

    # Set up signal timeout if supported (POSIX systems)
    has_alarm = hasattr(signal, "SIGALRM") and hasattr(signal, "alarm")
    if has_alarm and timeout > 0:
        try:
            signal.signal(signal.SIGALRM, _alarm_handler)
            signal.alarm(timeout)
        except (ValueError, OSError):
            has_alarm = False

    success = False
    error_msg = None
    exit_code = EXIT_SUCCESS

    try:
        os.chdir(target_cwd)
        with contextlib.redirect_stdout(stdout_buf), contextlib.redirect_stderr(
            stderr_buf
        ):
            exec(code, exec_globals)
        success = True
    except TimeoutException as exc:
        exit_code = EXIT_TIMEOUT
        error_msg = str(exc)
    except Exception:
        exit_code = EXIT_ERROR
        error_msg = traceback.format_exc()
    finally:
        if has_alarm:
            signal.alarm(0)
        os.chdir(orig_cwd)
        os.environ.clear()
        os.environ.update(orig_env)

    duration_ms = round((time.monotonic() - start_time) * 1000, 2)

    result: dict[str, Any] = {
        "success": success,
        "stdout": stdout_buf.getvalue(),
        "stderr": stderr_buf.getvalue(),
        "error": error_msg,
        "duration_ms": duration_ms,
        "cached": False,
        "exit_code": exit_code,
        "context": get_execution_context(),
    }

    if success and use_cache:
        cache.set(cache_key, result)

    return result


# ==============================================================================
# SECTION 6: Output Routing (LLM vs Human Terminal)
# ==============================================================================


def write_llm_output(data: dict[str, Any]) -> None:
    """Format and write JSON output to LLM_OUTPUT destination."""
    out_path = os.environ.get("LLM_OUTPUT", "/dev/stdout")
    json_payload = (
        json.dumps(data, indent=2, ensure_ascii=False, cls=ToolJSONEncoder) + "\n"
    )

    if out_path in ("/dev/stdout", "/dev/fd/1", "-"):
        sys.stdout.write(json_payload)
        sys.stdout.flush()
    else:
        try:
            Path(out_path).parent.mkdir(parents=True, exist_ok=True)
            with open(out_path, "a", encoding="utf-8") as fp:
                fp.write(json_payload)
        except OSError as err:
            sys.stderr.write(f"Failed writing to LLM_OUTPUT '{out_path}': {err}\n")
            sys.stdout.write(json_payload)
            sys.stdout.flush()


# ==============================================================================
# SECTION 7: Function Entry Point for AIChat
# ==============================================================================


def run(
    code: str,
    timeout: int = 30,
    cwd: Optional[str] = None,
    env_var: Optional[list[str]] = None,
    use_cache: bool = False,
    no_color: bool = False,
    verbose: bool = False,
) -> None:
    """Execute Python code and output structured results for AIChat."""
    result = execute_tool(
        code=code,
        timeout=timeout,
        cwd=cwd,
        env_vars=env_var,
        use_cache=use_cache,
        no_color=no_color,
        verbose=verbose,
    )

    print_human_readable_ui(result, no_color=no_color)
    write_llm_output(result)


# ==============================================================================
# SECTION 8: CLI Argument Parser
# ==============================================================================


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python_eval.py",
        description=f"AIChat Python Executor v{__version__}",
    )
    parser.add_argument(
        "--code",
        "-c",
        required=True,
        metavar="TEXT",
        help="Python code snippet to execute (required)",
    )
    parser.add_argument(
        "--timeout",
        type=int,
        default=30,
        metavar="NUM",
        help="Execution timeout limit in seconds (default: 30)",
    )
    parser.add_argument(
        "--cwd",
        metavar="PATH",
        help="Working directory for code execution",
    )
    parser.add_argument(
        "--env-var",
        action="append",
        dest="env_var",
        metavar="KEY=VALUE",
        help="Custom environment variable (repeatable)",
    )
    parser.add_argument(
        "--use-cache",
        action="store_true",
        default=False,
        dest="use_cache",
        help="Enable result caching for identical executions",
    )
    parser.add_argument(
        "--no-color",
        action="store_true",
        default=False,
        dest="no_color",
        help="Disable ANSI color output",
    )
    parser.add_argument(
        "--verbose",
        "-v",
        action="store_true",
        default=False,
        help="Enable detailed debug logging",
    )
    return parser


if __name__ == "__main__":
    # Check if code is passed via argc environment variable when called by argc
    argc_code = os.environ.get("argc_code")

    if argc_code and "--code" not in sys.argv and "-c" not in sys.argv:
        res = execute_tool(
            code=argc_code,
            timeout=int(os.environ.get("argc_timeout", "30")),
            cwd=os.environ.get("argc_cwd"),
            use_cache="argc_use_cache" in os.environ,
            no_color="argc_no_color" in os.environ,
            verbose="argc_verbose" in os.environ,
        )
    else:
        args = _build_parser().parse_args()
        res = execute_tool(
            code=args.code,
            timeout=args.timeout,
            cwd=args.cwd,
            env_vars=args.env_var,
            use_cache=args.use_cache,
            no_color=args.no_color,
            verbose=args.verbose,
        )

    print_human_readable_ui(res, no_color=res.get("no_color", False))
    write_llm_output(res)
    sys.exit(res.get("exit_code", EXIT_SUCCESS))
