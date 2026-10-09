#!/usr/bin/env python3
# ==============================================================================
# exec_command.py — Execute Command v0.1.0
# argc/aichat compatible · Termux & Linux
#
# Executes a shell command with a wall-clock timeout and captures
# stdout/stderr, returning a structured JSON result.
#
# Modes:
#   run    (default) Execute and capture; success=true unless the tool itself
#                    fails or times out. The command's exit code is in
#                    `returncode`.
#   check            Like run, but success=false when the command exits non-zero.
#
# @describe Execute a shell command with timeout and capture stdout/stderr
#
# @meta require-tools aichat
#
# @option --input! <STRING>       Shell command to execute (required — note the !)
# @option --mode <STRING>         Operation mode: run|check (default: run)
# @option --shell <PATH>          Shell binary for execution (default: /bin/sh)
# @option --cwd <PATH>            Working directory for the command
# @option --timeout <DURATION>   Wall-clock timeout, e.g. 60s, 1m, 2h (default: 5m)
# @option --env <KEY=VALUE>      Extra environment variable (repeatable)
# @flag   --dry-run              Simulate the action without making changes
# @flag   --stdin                Read primary input from stdin when --input is absent
# @flag   --no-color             Disable ANSI color output
# @flag   --verbose              Enable detailed debug logging
# @flag   --validate             Cross-check header/argparse/schema sync and exit
#
# @env LLM_OUTPUT=/dev/stdout    Output path for LLM integration
# @env LLM_OUTPUT_MODE=append    File write mode for LLM_OUTPUT: append or write
# @env LLM_TOOL_UI=auto          Show human UI on TTY; 0/false disables, 1/true forces
# ==============================================================================

from __future__ import annotations

import argparse
import enum
import functools
import json
import logging
import math
import os
import re
import select
import signal
import subprocess
import sys
import threading
import time
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any, Callable, Optional

# ==============================================================================
# SECTION 1: Identity, Exit Codes & Exception Models
# ==============================================================================

TOOL_NAME = "Execute Command"
TOOL_SLUG = "exec_command"          # function name aichat calls
TOOL_DESCRIPTION = "Execute a shell command with timeout and capture stdout/stderr"
__version__ = "0.1.0"
SCHEMA_VERSION = "1.0"

EXIT_SUCCESS = 0
EXIT_ERROR = 1
EXIT_FILE_NOT_FOUND = 2
EXIT_INVALID_INPUT = 3
EXIT_TIMEOUT = 124
EXIT_PERMISSION_DENIED = 126
EXIT_COMMAND_NOT_FOUND = 127
EXIT_INTERRUPTED = 130


class ToolError(Exception):
    """Structured exception carrying an exit code."""

    def __init__(self, message: str, exit_code: int = EXIT_ERROR) -> None:
        super().__init__(message)
        self.message = message
        self.exit_code = exit_code

    def to_dict(self) -> dict[str, Any]:
        return {"success": False, "error": self.message, "exit_code": self.exit_code}


class ToolJSONEncoder(json.JSONEncoder):
    """Custom JSON encoder: Path, Enum, datetime, timedelta, bytes, sets."""

    def default(self, obj: Any) -> Any:
        if isinstance(obj, Path):
            return str(obj)
        if isinstance(obj, enum.Enum):
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
# SECTION 2: Color Palette & Terminal UI Helpers
# Contract: human UI goes to STDERR; STDOUT stays pure JSON for the LLM.
# ==============================================================================

NEON_CYAN = "\033[38;5;51m"
NEON_GREEN = "\033[38;5;46m"
NEON_RED = "\033[38;5;196m"
NEON_YELLOW = "\033[38;5;226m"
NEON_PURPLE = "\033[38;5;129m"
NEON_PINK = "\033[38;5;198m"
NEON_BLUE = "\033[38;5;39m"
NEON_ORANGE = "\033[38;5;208m"
RESET = "\033[0m"
BOLD = "\033[1m"
DIM = "\033[2m"

_ANSI_RE = re.compile(r"\x1b(?:[@-Z\\-_]|\[[0-9;]*[ -/]*[@-~])")


def _strip_ansi(text: str) -> str:
    return _ANSI_RE.sub("", text)


def _env_truth(name: str, default: str = "") -> bool:
    val = os.environ.get(name, default).strip().lower()
    return val not in ("", "0", "false", "no", "off", "none")


def _is_tty() -> bool:
    try:
        return sys.stderr.isatty() and os.environ.get("TERM", "").lower() not in ("dumb", "")
    except Exception:
        return False


def _use_color(no_color: bool = False) -> bool:
    if no_color or os.environ.get("NO_COLOR") is not None:
        return False
    if _env_truth("FORCE_COLOR"):
        return True
    return _is_tty()


def _display_ui(no_color: bool = False) -> bool:
    """True when the human-readable box UI should render on stderr."""
    if no_color:
        return False
    ui_env = os.environ.get("LLM_TOOL_UI")
    if ui_env is not None:
        return _env_truth("LLM_TOOL_UI")
    if _env_truth("FORCE_COLOR"):
        return True
    return _is_tty()


def _cprint(text: str, no_color: bool = False, file: Any = None) -> None:
    target = file or sys.stderr
    if not _use_color(no_color):
        text = _strip_ansi(text)
    try:
        print(text, file=target, flush=True)
    except (BrokenPipeError, OSError):
        pass


# ==============================================================================
# SECTION 3: Agent & Environment Helpers
# ==============================================================================

def get_agent_var(name: str, default: str = "") -> str:
    """Access agent user-defined variables (LLM_AGENT_VAR_<NAME>)."""
    return os.environ.get(f"LLM_AGENT_VAR_{name.upper()}", default)


def get_builtin_var(name: str) -> Optional[str]:
    """Access agent built-in variables (e.g. __cwd__)."""
    return os.environ.get(f"LLM_AGENT_VAR_{name}")


def get_execution_context() -> dict[str, Any]:
    termux_prefix = os.environ.get("PREFIX", "")
    return {
        "tool_name": os.environ.get("LLM_TOOL_NAME", TOOL_SLUG),
        "output_path": os.environ.get("LLM_OUTPUT"),
        "cwd": get_builtin_var("__cwd__") or os.getcwd(),
        "platform": sys.platform,
        "is_termux": "com.termux" in termux_prefix or Path("/data/data/com.termux").exists(),
        "pid": os.getpid(),
        "version": __version__,
    }


# ==============================================================================
# SECTION 4: Small Shared Utilities (keep or delete per tool)
# ==============================================================================

def _timed(fn: Callable[..., dict[str, Any]]) -> Callable[..., dict[str, Any]]:
    """Stamp result with duration_ms / tool_version / schema_version."""
    @functools.wraps(fn)
    def wrapper(*args: Any, **kwargs: Any) -> dict[str, Any]:
        t0 = time.perf_counter()
        try:
            result = fn(*args, **kwargs)
        except ToolError as exc:
            result = exc.to_dict()
        except Exception as exc:
            result = {"success": False, "error": str(exc)}
        if not isinstance(result, dict):
            result = {"success": False, "error": "Operation returned invalid response"}
        result["duration_ms"] = round((time.perf_counter() - t0) * 1000, 3)
        result.setdefault("tool_version", __version__)
        result.setdefault("schema_version", SCHEMA_VERSION)
        return result
    return wrapper


_DURATION_RE = re.compile(r"^([0-9]+(?:\.[0-9]+)?)\s*([a-zA-Z]*)$")
_DURATION_UNITS: dict[str, float] = {
    "": 1.0, "s": 1.0, "sec": 1.0, "secs": 1.0, "second": 1.0, "seconds": 1.0,
    "ms": 0.001, "msec": 0.001, "msecs": 0.001, "millisecond": 0.001, "milliseconds": 0.001,
    "us": 1e-6, "usec": 1e-6, "microsecond": 1e-6, "microseconds": 1e-6,
    "m": 60.0, "min": 60.0, "mins": 60.0, "minute": 60.0, "minutes": 60.0,
    "h": 3600.0, "hr": 3600.0, "hrs": 3600.0, "hour": 3600.0, "hours": 3600.0,
    "d": 86400.0, "day": 86400.0, "days": 86400.0,
    "w": 604800.0, "week": 604800.0, "weeks": 604800.0,
}


def parse_duration(raw: Any, default: Optional[float] = None) -> Optional[float]:
    """Parse '60s' / '1m' / '2h' / 90 into seconds. Raises ValueError on bad input."""
    if raw is None:
        return default
    if isinstance(raw, bool):
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
        raise ValueError(f"Invalid duration: {raw!r}")
    unit = m.group(2).lower()
    if unit not in _DURATION_UNITS:
        raise ValueError(f"Unknown duration unit in: {raw!r}")
    value = float(m.group(1)) * _DURATION_UNITS[unit]
    if not math.isfinite(value):
        raise ValueError(f"Invalid duration: {raw!r}")
    return value


def seconds_to_human(sec: float) -> str:
    if sec < 0.001:
        return f"{sec * 1_000_000:.0f}µs"
    if sec < 1.0:
        return f"{sec * 1000:.0f}ms"
    if sec < 60:
        return f"{sec:.1f}s"
    if sec < 3600:
        return f"{sec / 60:.1f}m"
    return f"{sec / 3600:.2f}h"


_ENV_KEY_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
STDIN_TIMEOUT = 30.0


def _read_stdin(timeout: float = STDIN_TIMEOUT) -> str:
    """Read piped stdin with a timeout; returns "" when interactive or on timeout."""
    try:
        if sys.stdin.isatty():
            return ""
        if hasattr(select, "select"):
            ready, _, _ = select.select([sys.stdin], [], [], timeout)
            if not ready:
                return ""
        return sys.stdin.read()
    except Exception:
        return ""


def _call_with_timeout(
    fn: Callable[..., dict[str, Any]],
    timeout_sec: Optional[float],
    *args: Any,
    **kwargs: Any,
) -> dict[str, Any]:
    """Run fn in a daemon worker thread and enforce a wall-clock timeout.

    NOTE: an expired worker cannot be killed — it keeps running detached while
    the tool returns EXIT_TIMEOUT. Design ops to be idempotent / re-runnable.
    Pass timeout_sec=None (or <= 0) to disable.
    """
    if not timeout_sec or timeout_sec <= 0:
        return fn(*args, **kwargs)
    box: dict[str, Any] = {}

    def _target() -> None:
        try:
            box["result"] = fn(*args, **kwargs)
        except Exception as exc:
            box["error"] = exc

    worker = threading.Thread(target=_target, daemon=True)
    worker.start()
    worker.join(timeout_sec)
    if worker.is_alive():
        return {
            "success": False,
            "error": f"Operation timed out after {timeout_sec:g}s",
            "exit_code": EXIT_TIMEOUT,
            "timed_out": True,
        }
    if "error" in box:
        raise box["error"]
    result = box.get("result")
    if not isinstance(result, dict):
        return {"success": False, "error": "Operation returned invalid response"}
    return result


# ==============================================================================
# SECTION 5: Signal-Safe Execution (optional — delete if the tool is fast/sync)
# ==============================================================================

class GracefulShutdown:
    """SIGINT/SIGTERM flag for long-running operations."""

    def __init__(self) -> None:
        self.interrupted = False
        self._lock = threading.Lock()
        self._handlers: dict[int, Any] = {}

    def __enter__(self) -> "GracefulShutdown":
        try:
            if threading.current_thread() is threading.main_thread():
                self._handlers[signal.SIGINT] = signal.signal(signal.SIGINT, self._handle)
                self._handlers[signal.SIGTERM] = signal.signal(signal.SIGTERM, self._handle)
        except (ValueError, OSError):
            pass
        return self

    def __exit__(self, *exc: Any) -> None:
        for sig, h in self._handlers.items():
            try:
                signal.signal(sig, h)
            except (ValueError, OSError):
                pass

    def _handle(self, signum: int, frame: Any) -> None:
        with self._lock:
            self.interrupted = True

    def check(self) -> bool:
        with self._lock:
            return self.interrupted


# ==============================================================================
# SECTION 6: Core Operations — IMPLEMENT YOUR LOGIC HERE
# Every op returns {"success": bool, ...}. Use @_timed for duration stamps.
# ==============================================================================

@_timed
def op_run_command(
    command: str,
    mode: str = "run",
    shell: str = "/bin/sh",
    cwd: Optional[str] = None,
    cmd_timeout_sec: Optional[float] = None,
) -> dict[str, Any]:
    """Execute a shell command, capture stdout/stderr, and report the outcome.

    The tool-level success is True when the command was launched and awaited
    without incident — the *command's* own exit status lives in `returncode`.
    In `check` mode a non-zero returncode also fails the tool result.
    """
    if not command or not command.strip():
        return {"success": False, "error": "input is required",
                "exit_code": EXIT_INVALID_INPUT}

    if cwd is not None:
        cwd_path = Path(cwd).expanduser()
        if not cwd_path.is_dir():
            return {"success": False,
                    "error": f"--cwd is not a directory: {cwd!r}",
                    "exit_code": EXIT_INVALID_INPUT}

    try:
        completed = subprocess.run(
            [shell, "-c", command],
            cwd=str(cwd_path) if cwd is not None else None,
            capture_output=True,
            text=True,
            timeout=cmd_timeout_sec if (cmd_timeout_sec and cmd_timeout_sec > 0) else None,
        )
    except FileNotFoundError:
        return {"success": False, "error": f"shell not found: {shell!r}",
                "exit_code": EXIT_ERROR}
    except subprocess.TimeoutExpired as exc:
        out_tail = (exc.stdout or "") if isinstance(exc.stdout, str) else ""
        err_tail = (exc.stderr or "") if isinstance(exc.stderr, str) else ""
        return {
            "success": False,
            "error": f"command timed out after {cmd_timeout_sec}s",
            "exit_code": EXIT_TIMEOUT,
            "command": command,
            "shell": shell,
            "cwd": cwd,
            "stdout": out_tail,
            "stderr": err_tail,
            "returncode": None,
            "timed_out": True,
            "detail": "timed out — the process was killed",
        }

    returncode = completed.returncode
    success = returncode == 0 if mode == "check" else True
    return {
        "success": success,
        "error": None if success else f"command exited with code {returncode}",
        "exit_code": EXIT_SUCCESS if success else EXIT_ERROR,
        "command": command,
        "shell": shell,
        "cwd": cwd,
        "stdout": completed.stdout or "",
        "stderr": completed.stderr or "",
        "returncode": returncode,
        "timed_out": False,
        "detail": f"command exited {returncode} — {len(completed.stdout or '')} stdout bytes, "
                  f"{len(completed.stderr or '')} stderr bytes",
    }


def op_example(*args: Any, **kwargs: Any) -> dict[str, Any]:
    """Removed during forging — kept as a no-op shim so stale references fail loudly."""
    return {"success": False, "error": "op_example is not implemented",
            "exit_code": EXIT_ERROR}


# ==============================================================================
# SECTION 7: Master Execution Logic
# ==============================================================================

def execute_tool(
    input: Optional[str] = None,      # noqa: A002 - mirrors CLI/LLM param name
    mode: str = "run",
    shell: str = "/bin/sh",
    cwd: Optional[str] = None,
    timeout: Optional[str] = None,
    env: Optional[list[str]] = None,
    no_color: bool = False,
    verbose: bool = False,
    dry_run: bool = False,
    stdin: bool = False,
) -> dict[str, Any]:
    """
    Core execution shared by run() and main(). Validates inputs, applies
    env overrides, runs the work, and builds the final result dict.
    """
    logger = logging.getLogger(TOOL_SLUG)
    if verbose:
        logger.setLevel(logging.DEBUG)
        if not logger.handlers:
            h = logging.StreamHandler(sys.stderr)
            h.setFormatter(logging.Formatter("[%(levelname)s] %(message)s"))
            logger.addHandler(h)
            logger.propagate = False

    started_at = datetime.now().astimezone().isoformat()
    warnings: list[str] = []

    # --- validate durations ---
    try:
        timeout_sec = parse_duration(timeout, default=300.0)
    except ValueError as exc:
        return _error_result(str(exc), EXIT_INVALID_INPUT, warnings=warnings,
                             started_at=started_at)

    # --- stdin fallback ---
    if stdin and not input:
        input = _read_stdin() or None

    # --- env overrides (KEY=VALUE) ---
    for item in env or []:
        if "=" in item:
            k, v = item.split("=", 1)
            k = k.strip()
            if _ENV_KEY_RE.match(k):
                os.environ[k] = v
            else:
                warnings.append(f"Ignoring invalid env name: {k!r}")

    # --- dry run ---
    if dry_run:
        return {
            "success": True,
            "dry_run": True,
            "input": input,
            "mode": mode,
            "shell": shell,
            "cwd": cwd,
            "output": f"[DRY RUN] Would execute {input!r} via {shell!r}"
                      + (f" in {cwd!r}" if cwd else "") + "\n",
            "exit_code": EXIT_SUCCESS,
            "duration_ms": 0.0,
            "warnings": warnings,
            "started_at": started_at,
            "finished_at": datetime.now().astimezone().isoformat(),
            "context": get_execution_context(),
            "tool_version": __version__,
            "schema_version": SCHEMA_VERSION,
        }

    # --- run the work (timeout enforced; expired worker is detached) ---
    norm_mode = (mode or "run").strip().lower()
    if norm_mode not in ("run", "check"):
        warnings.append(f"Unknown mode {mode!r}; falling back to 'run'")
        norm_mode = "run"
    result = _call_with_timeout(
        op_run_command, timeout_sec, input or "",
        mode=norm_mode, shell=shell, cwd=cwd, cmd_timeout_sec=timeout_sec)

    result.setdefault("exit_code", EXIT_SUCCESS if result.get("success") else EXIT_ERROR)
    result["input"] = input
    result["mode"] = norm_mode
    result.setdefault("shell", shell)
    result.setdefault("cwd", cwd)
    result["timeout_sec"] = timeout_sec
    result["warnings"] = warnings
    result["started_at"] = started_at
    result["finished_at"] = datetime.now().astimezone().isoformat()
    result["context"] = get_execution_context()
    return result


def _error_result(
    message: str,
    exit_code: int,
    warnings: Optional[list[str]] = None,
    started_at: Optional[str] = None,
) -> dict[str, Any]:
    now = datetime.now().astimezone().isoformat()
    return {
        "success": False,
        "error": message,
        "output": f"{message}\n",
        "exit_code": exit_code,
        "duration_ms": 0.0,
        "warnings": warnings or [],
        "started_at": started_at or now,
        "finished_at": now,
        "context": get_execution_context(),
        "tool_version": __version__,
        "schema_version": SCHEMA_VERSION,
    }


# ==============================================================================
# SECTION 8: Tool Schema (aichat / OpenAI function registration)
# ==============================================================================

def generate_tool_schema() -> dict[str, Any]:
    """Emit the OpenAI-style function schema. Keep in sync with SECTION 7 + 10."""
    return {
        "name": TOOL_SLUG,
        "description": f"{TOOL_DESCRIPTION} (v{__version__})",
        "parameters": {
            "type": "object",
            "properties": {
                "input": {"type": "string",
                          "description": "Shell command to execute (required)"},
                "mode": {"type": "string",
                         "description": "Operation mode: 'run' (default) or 'check' (fail on non-zero exit)"},
                "shell": {"type": "string",
                          "description": "Shell binary for execution (default: /bin/sh)"},
                "cwd": {"type": "string",
                        "description": "Working directory for the command"},
                "timeout": {"type": "string", "description": "Wall-clock timeout (e.g. 60s, 1m)"},
                "env": {"type": "array", "items": {"type": "string"},
                        "description": "Extra env vars as KEY=VALUE (repeatable)"},
                "dry_run": {"type": "boolean", "description": "Simulate without making changes"},
                "stdin": {"type": "boolean", "description": "Read primary input from stdin when input is absent"},
                "no_color": {"type": "boolean", "description": "Disable ANSI color output"},
                "verbose": {"type": "boolean", "description": "Enable debug logging"},
            },
            "required": ["input"],
        },
    }


# --- Forge-time spec sync check -------------------------------------------------

_SPEC_ANNOTATION_RE = re.compile(r"^#\s*@(option|flag)\s+(--[\w-]+)", re.MULTILINE)

# Parser-only switches that never reach the LLM schema.
_SPEC_META_ARGS = {"no_color", "verbose", "schema", "validate", "version", "input_pos"}


def validate_spec() -> dict[str, Any]:
    """Cross-check header @option/@flag annotations vs argparse vs schema.

    Catches the #1 forging mistake: the three parameter declarations drifting
    out of sync. Returns a result dict; success=False lists every mismatch.
    """
    problems: list[str] = []
    try:
        src = Path(__file__).read_text(encoding="utf-8")
    except OSError as exc:
        return {"success": False, "error": f"Cannot read own source: {exc}",
                "exit_code": EXIT_ERROR}

    annotated = {m.group(2).lstrip("-").replace("-", "_")
                 for m in _SPEC_ANNOTATION_RE.finditer(src)}

    parser = _build_parser()
    cli = {a.dest for a in parser._actions if a.dest != "help"}

    schema = generate_tool_schema()
    schema_props = set(schema.get("parameters", {}).get("properties", {}).keys())

    for name in sorted(annotated - cli):
        problems.append(f"@{name}: annotated in header but missing from argparse")
    for name in sorted(cli - annotated - _SPEC_META_ARGS):
        problems.append(f"--{name}: in argparse but missing from header annotations")
    for name in sorted((cli - _SPEC_META_ARGS) - schema_props):
        problems.append(f"--{name}: in argparse but missing from generate_tool_schema()")
    for name in sorted(schema_props - cli):
        problems.append(f"{name}: in schema but missing from argparse")

    ok = not problems
    return {
        "success": ok,
        "error": None if ok else f"{len(problems)} spec mismatch(es) found",
        "exit_code": EXIT_SUCCESS if ok else EXIT_INVALID_INPUT,
        "annotated": sorted(annotated),
        "cli_args": sorted(cli),
        "schema_params": sorted(schema_props),
        "problems": problems,
        "tool_version": __version__,
        "schema_version": SCHEMA_VERSION,
    }


# ==============================================================================
# SECTION 9: Output Routing — JSON to stdout/LLM_OUTPUT, box UI to stderr
# ==============================================================================

_DIRECT_STDOUT = {"/dev/stdout", "/dev/fd/1", "-", "stdout"}
_DIRECT_STDERR = {"/dev/stderr", "/dev/fd/2", "stderr"}


def write_llm_output(data: dict[str, Any]) -> None:
    """Write the JSON payload for the LLM. stdout stays pure — never UI text."""
    out_path = (os.environ.get("LLM_OUTPUT", "/dev/stdout") or "/dev/stdout").strip()
    try:
        payload = json.dumps(data, ensure_ascii=False, cls=ToolJSONEncoder) + "\n"
    except (TypeError, ValueError) as exc:
        payload = json.dumps({"success": False, "error": "JSON serialization failed",
                              "detail": str(exc)}) + "\n"

    if out_path in _DIRECT_STDOUT:
        try:
            sys.stdout.buffer.write(payload.encode("utf-8"))
            sys.stdout.buffer.flush()
        except (OSError, BrokenPipeError):
            pass
        return
    if out_path in _DIRECT_STDERR:
        try:
            sys.stderr.write(payload)
        except (OSError, BrokenPipeError):
            pass
        return
    mode = os.environ.get("LLM_OUTPUT_MODE", "append").strip().lower()
    try:
        target = Path(out_path)
        target.parent.mkdir(parents=True, exist_ok=True)
        if mode == "write":
            # Atomic overwrite: temp file + rename, never a torn write.
            tmp = target.with_name(f".{target.name}.{os.getpid()}.{time.time_ns()}.tmp")
            with open(tmp, "w", encoding="utf-8") as fp:
                fp.write(payload)
            tmp.replace(target)
        else:
            with open(target, "a", encoding="utf-8") as fp:
                fp.write(payload)
    except OSError as err:
        sys.stderr.write(f"Failed writing to LLM_OUTPUT '{out_path}': {err}\n")
        try:
            sys.stdout.write(payload)
        except (OSError, BrokenPipeError):
            pass


def print_human_readable_ui(data: dict[str, Any], no_color: bool = False) -> None:
    """Neon box summary on stderr — human eyes only, never the LLM."""
    if not _display_ui(no_color):
        return

    success = bool(data.get("success", False))
    status_color = NEON_GREEN if success else NEON_RED
    symbol = "✓" if success else "✗"
    box_w = 72
    border = "─" * box_w

    _cprint(f"{NEON_PURPLE}╭{border}╮{RESET}", no_color=no_color)
    _cprint(
        f"{NEON_PURPLE}│{RESET} {NEON_PINK}⚡ [{TOOL_NAME} v{__version__}]{RESET} "
        f"{status_color}{BOLD}{symbol} {'SUCCESS' if success else 'FAILED'}{RESET}",
        no_color=no_color,
    )
    _cprint(f"{NEON_PURPLE}├{border}┤{RESET}", no_color=no_color)

    # --- customize these lines for your result fields ---
    _cprint(f"{NEON_PURPLE}│{RESET} {NEON_CYAN}Command:{RESET}   {_short(data.get('input'))}",
            no_color=no_color)
    _cprint(f"{NEON_PURPLE}│{RESET} {NEON_CYAN}Mode:{RESET}      {data.get('mode', 'N/A')}",
            no_color=no_color)
    if data.get("returncode") is not None:
        rc = data.get("returncode")
        rc_color = NEON_GREEN if rc == 0 else NEON_RED
        _cprint(f"{NEON_PURPLE}│{RESET} {NEON_CYAN}Return:{RESET}    {rc_color}{BOLD}{rc}{RESET}"
                + (f"  {NEON_YELLOW}(timed out){RESET}" if data.get("timed_out") else ""),
                no_color=no_color)
    if data.get("stdout"):
        _cprint(f"{NEON_PURPLE}│{RESET} {NEON_CYAN}Stdout:{RESET}    {_short(data.get('stdout'), 54)}",
                no_color=no_color)
    if data.get("stderr"):
        _cprint(f"{NEON_PURPLE}│{RESET} {NEON_ORANGE}Stderr:{RESET}    {_short(data.get('stderr'), 54)}",
                no_color=no_color)
    _cprint(f"{NEON_PURPLE}│{RESET} {NEON_CYAN}Duration:{RESET}  {DIM}{data.get('duration_ms', 0)}ms{RESET}",
            no_color=no_color)

    if data.get("dry_run"):
        _cprint(f"{NEON_PURPLE}│{RESET} {NEON_YELLOW}DRY-RUN:{RESET}    Simulation only — no changes made",
                no_color=no_color)

    if not success and data.get("error"):
        _cprint(f"{NEON_PURPLE}├{border}┤{RESET}", no_color=no_color)
        _cprint(f"{NEON_PURPLE}│{RESET} {NEON_RED}Error:{RESET}      {data['error']}", no_color=no_color)

    if data.get("warnings"):
        _cprint(f"{NEON_PURPLE}├{border}┤{RESET}", no_color=no_color)
        for w in data["warnings"][:5]:
            _cprint(f"{NEON_PURPLE}│{RESET} {NEON_ORANGE}⚠ {w}{RESET}", no_color=no_color)

    _cprint(f"{NEON_PURPLE}╰{border}╯{RESET}", no_color=no_color)


def _short(value: Any, limit: int = 60) -> str:
    text = repr("" if value is None else value)
    return text if len(text) <= limit else text[: limit - 1] + "…"


# ==============================================================================
# SECTION 10: aichat Entrypoint
# ==============================================================================

def run(**kwargs: Any) -> dict[str, Any]:
    """Programmatic entrypoint. Mirrors execute_tool's signature via kwargs."""
    try:
        res = execute_tool(**kwargs)
    except KeyboardInterrupt:
        res = _error_result("Interrupted by user", EXIT_INTERRUPTED)
    print_human_readable_ui(res, no_color=bool(kwargs.get("no_color", False)))
    write_llm_output(res)
    return res


# ==============================================================================
# SECTION 11: CLI Parser & Main — keep in sync with the header annotations
# ==============================================================================

def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog=f"{TOOL_SLUG}.py",
        description=f"{TOOL_NAME} v{__version__} — {TOOL_DESCRIPTION}",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p.add_argument("--input", "-i", required=False, default=None,
                   help="Shell command to execute (required unless --schema)")
    p.add_argument("input_pos", nargs="?", help="Positional command input")
    p.add_argument("--mode", default="run", help="Operation mode: run|check (default: run)")
    p.add_argument("--shell", default="/bin/sh", help="Shell binary for execution (default: /bin/sh)")
    p.add_argument("--cwd", default=None, help="Working directory for the command")
    p.add_argument("--timeout", default=None, help="Wall-clock timeout, e.g. 60s, 1m, 2h")
    p.add_argument("--env", action="append", default=None, metavar="KEY=VALUE",
                   help="Extra environment variable (repeatable)")
    p.add_argument("--dry-run", action="store_true", help="Simulate without making changes")
    p.add_argument("--stdin", action="store_true",
                   help="Read primary input from stdin when --input is absent")
    p.add_argument("--no-color", action="store_true", help="Disable ANSI color output")
    p.add_argument("--verbose", "-v", action="store_true", help="Enable debug logging")
    p.add_argument("--schema", action="store_true",
                   help="Print JSON tool schema for LLM registration and exit")
    p.add_argument("--validate", action="store_true",
                   help="Cross-check header/argparse/schema sync and exit")
    p.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    return p


def main() -> int:
    parser = _build_parser()
    args = parser.parse_args()

    if args.schema:
        sys.stdout.write(json.dumps(generate_tool_schema(), indent=2) + "\n")
        sys.stdout.flush()
        return EXIT_SUCCESS

    if args.validate:
        res = validate_spec()
        write_llm_output(res)
        return int(res.get("exit_code", EXIT_ERROR))

    if args.verbose:
        logging.basicConfig(level=logging.DEBUG, format="[%(levelname)s] %(message)s",
                            force=True)

    cli_input = args.input or args.input_pos
    if cli_input is None and not args.stdin:
        # argparse can't mark --input required because --schema/--validate must work standalone
        sys.stderr.write("error: --input/-i is required (or use --stdin)\n")
        return EXIT_INVALID_INPUT

    try:
        res = execute_tool(
            input=cli_input,
            mode=args.mode,
            shell=args.shell,
            cwd=args.cwd,
            timeout=args.timeout,
            env=args.env,
            no_color=args.no_color,
            verbose=args.verbose,
            dry_run=args.dry_run,
            stdin=args.stdin,
        )
    except KeyboardInterrupt:
        res = _error_result("Interrupted by user", EXIT_INTERRUPTED)
    except Exception as exc:  # never let the tool die without structured output
        res = _error_result(f"Unexpected tool failure: {exc}", EXIT_ERROR)

    print_human_readable_ui(res, no_color=args.no_color)
    write_llm_output(res)

    exit_code = res.get("exit_code")
    if exit_code is not None:
        return int(exit_code)
    return EXIT_SUCCESS if res.get("success") else EXIT_ERROR


if __name__ == "__main__":
    sys.exit(main())
