#!/usr/bin/env python3
# ==============================================================================
# git_manager.py — Pyrmethus AIChat / llm-functions Master Tool v5.1.0
# AIChat/llm-functions compatible · Typed Python run() API · Safe JSON · Neon CLI
#
# Compatibility-first template:
#   - The public `run()` function is the LLM-facing contract.
#   - `run()` returns a JSON-serializable Python object and NEVER prints.
#   - Human/CLI output goes to stderr; machine output goes to stdout/LLM_OUTPUT.
#   - No third-party Python dependencies are required (relies on git & gh CLI).
#
# @describe Advanced Git & GitHub repository tool: pulls, pushes, branches, merges,
# @describe rebases, commits, diffs, blame, tags, stashes, remotes, conflict checks,
# @describe restores, and GitHub PR / Issue workflows.
#
# @meta require-tools aichat git
# @meta python-entrypoint run
#
# @option --target! <PATH>               Repository path (defaults to current dir)
# @option --action <ACTION>              Operation: status/info/log/show/diff/blame/commit/push/pull/fetch/checkout/branch/merge/rebase/cherry_pick/revert/reset/restore/stash/tag/remote/clean/conflicts/pr/issue
# @option --subcommand <CMD>             Action subcommand (e.g., pr: list/view/create/checkout/merge; stash: pop/list; rebase: abort)
# @option --branch <NAME>                Branch, ref, or tag target
# @option --commit-hash <SHA>            Commit hash for show, cherry-pick, revert, reset, or blame
# @option --message <TEXT>               Commit message, tag description, or PR/Issue title
# @option --body <TEXT>                  Long-form description or PR/Issue body
# @option --file <PATH>                  Target file path (repeatable)
# @option --remote <NAME>                Remote repository name (default: origin)
# @option --limit <NUM>                  Max items for logs, PRs, or issues (default: 20)
# @option --line-start <NUM>             Starting line for blame inspection
# @option --line-end <NUM>             Ending line for blame inspection
# @option --timeout <SEC>                Command timeout in seconds
# @option --cache-ttl <SEC>              Cache TTL for read-only actions
# @option --cache-dir <PATH>             Cache directory
# @option --env-var <KEY=VALUE>          Safe environment override (repeatable)
# @flag   --all-files                    Stage all modified/untracked files (git add -A)
# @flag   --create-branch                Create branch on checkout (-b / switch -c)
# @flag   --delete-branch                Delete specified branch or tag (-d)
# @flag   --set-upstream                 Set upstream on push (-u)
# @flag   --dry-run                      Simulate mutating operations without changes
# @flag   --use-cache                    Enable result caching for read queries
# @flag   --clear-cache                  Clear tool cache
# @flag   --schema                       Print the LLM-facing JSON schema
# @flag   --self-test                    Run dependency-free automated tests
# @flag   --json-only                    Suppress human CLI UI
# @flag   --quiet                        Suppress progress/UI
# @flag   --no-color                     Disable ANSI output
# @flag   --verbose                      Enable debug logging
#
# @env LLM_OUTPUT=/dev/stdout            Optional llm-functions output destination
# @env LLM_TOOL_LIMIT=20                 CLI default override
# @env LLM_TOOL_CACHE_DIR                Cache directory override
# ==============================================================================

from __future__ import annotations

import argparse
import contextlib
import dataclasses
import hashlib
import json
import logging
import os
import platform
import re
import shutil
import signal
import subprocess
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

__version__ = "5.1.0"
__tool_name__ = "git_manager"
__tool_description__ = (
    "Comprehensive Git and GitHub engine to manage repository state, commits, "
    "branches, merges, rebases, tags, cherry-picks, stashes, conflict resolution, "
    "blame, remotes, and GitHub Pull Requests / Issues."
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
    "GitEngine",
    "get_agent_var",
    "get_builtin_var",
    "get_execution_context",
    "__version__",
]

# ==============================================================================
# CONSTANTS & EXIT CODES
# ==============================================================================

EXIT_SUCCESS = 0
EXIT_ERROR = 1
EXIT_FILE_NOT_FOUND = 2
EXIT_TIMEOUT = 124
EXIT_PERMISSION_DENIED = 126
EXIT_INVALID_INPUT = 127
EXIT_INTERRUPTED = 130

MAX_LIMIT = 500
MAX_TIMEOUT = 900.0
MAX_CACHE_TTL = 86_400
DEFAULT_LIMIT = 20
DEFAULT_TIMEOUT = 60.0
DEFAULT_CACHE_TTL = 300
DEFAULT_CACHE_SIZE_MB = 128
SUMMARY_PREVIEW = 10

VALID_ACTIONS = frozenset(
    {
        "status",
        "info",
        "log",
        "show",
        "diff",
        "blame",
        "commit",
        "push",
        "pull",
        "fetch",
        "checkout",
        "branch",
        "merge",
        "rebase",
        "cherry_pick",
        "revert",
        "reset",
        "restore",
        "stash",
        "tag",
        "remote",
        "clean",
        "conflicts",
        "pr",
        "issue",
    }
)

READ_ONLY_ACTIONS = frozenset(
    {
        "status",
        "info",
        "log",
        "show",
        "diff",
        "blame",
        "conflicts",
    }
)

VALID_OUTPUT_FORMATS = frozenset({"json", "jsonl"})

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
_TOKEN_RE = re.compile(r"(ghp_[a-zA-Z0-9]{36}|github_pat_[a-zA-Z0-9_]{82}|https://[^:@\s]+:[^:@\s]+@)")


def _sanitize_output(text: str) -> str:
    if not text:
        return ""
    return _TOKEN_RE.sub("[REDACTED_CREDENTIAL]", text)


# ==============================================================================
# STRUCTURED ERRORS
# ==============================================================================


class ToolError(Exception):
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
        self.message = _sanitize_output(message)
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
# JSON SERIALIZATION
# ==============================================================================


class ToolJSONEncoder(json.JSONEncoder):
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
                return sorted(obj, key=str)
            if dataclasses.is_dataclass(obj):
                return dataclasses.asdict(obj)
            if isinstance(obj, Mapping):
                return dict(obj)
            if isinstance(obj, Sequence) and not isinstance(obj, (str, bytes, bytearray)):
                return list(obj)
        except Exception:
            pass
        return repr(obj)


def json_safe(value: Any) -> Any:
    return json.loads(json.dumps(value, ensure_ascii=False, cls=ToolJSONEncoder))


# ==============================================================================
# UI & LOGGING
# ==============================================================================


def _strip_ansi(text: str) -> str:
    return _ANSI_RE.sub("", str(text))


def _is_tty(no_color: bool = False) -> bool:
    if no_color or os.environ.get("NO_COLOR") is not None:
        return False
    if os.environ.get("FORCE_COLOR"):
        return True
    return sys.stderr.isatty() and os.environ.get("TERM", "").lower() not in {"", "dumb"}


def _cprint(text: str, file: Any = None, no_color: bool = False, end: str = "\n") -> None:
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


def print_human_readable_ui(data: Mapping[str, Any], no_color: bool = False, quiet: bool = False) -> None:
    if quiet or not _is_tty(no_color):
        return

    success = bool(data.get("success"))
    color = NEON_GREEN if success else NEON_RED
    symbol = "✓" if success else "✗"
    status = "SUCCESS" if success else "FAILED"
    border = "─" * 70

    _cprint(f"{NEON_PURPLE}╭{border}╮{RESET}", no_color=no_color)
    _cprint(
        f"{NEON_PURPLE}│{RESET} {NEON_PINK}{BOLD}⚡ GIT ENGINE v{__version__}{RESET} "
        f"{color}{BOLD}{symbol} {status}{RESET}",
        no_color=no_color,
    )
    _cprint(f"{NEON_PURPLE}├{border}┤{RESET}", no_color=no_color)
    _cprint(f"{NEON_PURPLE}│{RESET} {NEON_CYAN}Action:{RESET}     {data.get('action', 'N/A')}", no_color=no_color)
    if data.get("subcommand"):
        _cprint(f"{NEON_PURPLE}│{RESET} {NEON_CYAN}Subcommand:{RESET} {data.get('subcommand')}", no_color=no_color)
    _cprint(f"{NEON_PURPLE}│{RESET} {NEON_CYAN}Target:{RESET}     {data.get('target', 'N/A')}", no_color=no_color)
    if "branch" in data:
        _cprint(f"{NEON_PURPLE}│{RESET} {NEON_CYAN}Branch:{RESET}     {NEON_YELLOW}{data.get('branch')}{RESET}", no_color=no_color)
    _cprint(f"{NEON_PURPLE}│{RESET} {NEON_CYAN}Duration:{RESET}   {DIM}{data.get('duration_ms', 0)} ms{RESET}", no_color=no_color)

    if not success:
        _cprint(f"{NEON_PURPLE}├{border}┤{RESET}", no_color=no_color)
        _cprint(
            f"{NEON_PURPLE}│{RESET} {NEON_RED}{data.get('type', 'Error')}:{RESET} {data.get('error', 'Unknown error')}",
            no_color=no_color,
        )

    output = data.get("raw_output") or data.get("summary") or data.get("output")
    if output:
        _cprint(f"{NEON_PURPLE}├{border}┤{RESET}", no_color=no_color)
        lines = str(output).strip().splitlines()
        for line in lines[:SUMMARY_PREVIEW]:
            _cprint(f"{NEON_PURPLE}│{RESET}   {line}", no_color=no_color)
        if len(lines) > SUMMARY_PREVIEW:
            _cprint(f"{NEON_PURPLE}│{RESET}   {DIM}… and {len(lines) - SUMMARY_PREVIEW} more lines{RESET}", no_color=no_color)

    _cprint(f"{NEON_PURPLE}╰{border}╯{RESET}", no_color=no_color)


# ==============================================================================
# ENVIRONMENT & SECURITY HELPERS
# ==============================================================================


def get_agent_var(name: str, default: str = "") -> str:
    if not name:
        return default
    return os.environ.get(f"LLM_AGENT_VAR_{name.upper()}", default)


def get_builtin_var(name: str) -> Optional[str]:
    if not name:
        return None
    for cand in (
        f"LLM_AGENT_VAR_{name}",
        f"LLM_AGENT_VAR_{name.lower()}",
        f"LLM_AGENT_VAR_{name.upper()}",
    ):
        val = os.environ.get(cand)
        if val is not None:
            return val
    return None


def get_execution_context() -> dict[str, Any]:
    return {
        "tool_name": os.environ.get("LLM_TOOL_NAME", __tool_name__),
        "output_path": os.environ.get("LLM_OUTPUT", "/dev/stdout"),
        "cwd": get_builtin_var("__cwd__") or os.getcwd(),
        "has_git": bool(shutil.which("git")),
        "has_gh": bool(shutil.which("gh")),
        "host": {
            "os": platform.system().lower(),
            "arch": platform.machine(),
            "python_version": platform.python_version(),
        },
    }


def resolve_agent_path(target: str) -> Path:
    if "\x00" in target:
        raise ToolError("Target path contains a null byte.", EXIT_INVALID_INPUT, "ValidationError")
    raw = Path(target).expanduser()
    if raw.is_absolute():
        return raw.resolve(strict=False)
    agent_cwd = get_builtin_var("__cwd__")
    if agent_cwd:
        return (Path(agent_cwd).expanduser() / raw).resolve(strict=False)
    return raw.resolve(strict=False)


class SecurityValidator:
    @staticmethod
    def is_safe_ref(ref: Optional[str]) -> bool:
        if not ref:
            return True
        if "\x00" in ref or "\n" in ref or "\r" in ref or " " in ref:
            return False
        if ref.startswith("-") or ".." in ref or "~" in ref or "^" in ref or ":" in ref:
            return False
        return True

    @staticmethod
    def sanitize_env_vars(env_vars: Optional[list[str]]) -> tuple[dict[str, str], list[str]]:
        parsed: dict[str, str] = {}
        warnings: list[str] = []
        for entry in env_vars or []:
            if "=" not in entry:
                warnings.append(f"Ignored malformed env var: {entry!r}")
                continue
            k, v = entry.split("=", 1)
            k = k.strip()
            if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", k):
                warnings.append(f"Ignored invalid env name: {k!r}")
                continue
            if k.upper() in BLOCKED_ENV_VARS:
                warnings.append(f"Blocked unsafe env override: {k}")
                continue
            parsed[k] = v
        return parsed, warnings


def validate_inputs(
    target: Optional[str],
    action: str,
    branch: Optional[str] = None,
    commit_hash: Optional[str] = None,
    limit: Optional[int] = DEFAULT_LIMIT,
    timeout: Optional[float] = None,
) -> Optional[dict[str, Any]]:
    trace_id = f"val_{uuid.uuid4().hex[:12]}"
    if not target or not str(target).strip():
        return ToolError("Target repository path is required.", EXIT_INVALID_INPUT, "ValidationError", trace_id=trace_id).to_dict()

    if action not in VALID_ACTIONS:
        return ToolError(
            f"Invalid action {action!r}. Allowed: {sorted(VALID_ACTIONS)}.",
            EXIT_INVALID_INPUT,
            "ValidationError",
            trace_id=trace_id,
        ).to_dict()

    if branch and not SecurityValidator.is_safe_ref(branch):
        return ToolError(
            f"Invalid or unsafe branch/ref: {branch!r}.",
            EXIT_INVALID_INPUT,
            "ValidationError",
            trace_id=trace_id,
        ).to_dict()

    if commit_hash and not re.fullmatch(r"[A-Fa-f0-9]{4,64}", commit_hash):
        return ToolError(
            f"Invalid commit hash format: {commit_hash!r}.",
            EXIT_INVALID_INPUT,
            "ValidationError",
            trace_id=trace_id,
        ).to_dict()

    if limit is not None and not 1 <= limit <= MAX_LIMIT:
        return ToolError(f"limit must be between 1 and {MAX_LIMIT}.", EXIT_INVALID_INPUT, "ValidationError", trace_id=trace_id).to_dict()

    if timeout is not None and not 0 < timeout <= MAX_TIMEOUT:
        return ToolError(f"timeout must be > 0 and <= {MAX_TIMEOUT}s.", EXIT_INVALID_INPUT, "ValidationError", trace_id=trace_id).to_dict()

    return None


# ==============================================================================
# CACHE ENGINE
# ==============================================================================


def build_cache_key(target_path: Path, action: str, branch: Optional[str], limit: int) -> str:
    try:
        git_dir = target_path / ".git"
        head_file = git_dir / "HEAD"
        head_sig = head_file.read_text(encoding="utf-8").strip() if head_file.exists() else "nohead"
    except Exception:
        head_sig = "unreadable"
    return f"{__tool_name__}|{target_path}|{action}|{branch or ''}|{limit}|{head_sig}"


class ToolCache:
    def __init__(self, cache_dir: Optional[str | Path] = None, ttl: int = DEFAULT_CACHE_TTL) -> None:
        sel = cache_dir or os.environ.get("LLM_TOOL_CACHE_DIR")
        self.cache_dir = Path(sel).expanduser().resolve() if sel else Path.home() / ".cache" / "aichat_tools" / __tool_name__
        self.ttl = max(0, min(int(ttl), MAX_CACHE_TTL))
        with contextlib.suppress(OSError):
            self.cache_dir.mkdir(parents=True, exist_ok=True)

    def _hash(self, key: str) -> str:
        return hashlib.sha256(key.encode("utf-8")).hexdigest()

    def get(self, key: str) -> Optional[dict[str, Any]]:
        path = self.cache_dir / f"{self._hash(key)}.json"
        if not path.is_file():
            return None
        try:
            if self.ttl == 0 or (time.time() - path.stat().st_mtime) > self.ttl:
                path.unlink(missing_ok=True)
                return None
            with path.open("r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            with contextlib.suppress(OSError):
                path.unlink()
            return None

    def set(self, key: str, value: Mapping[str, Any]) -> None:
        if self.ttl == 0:
            return
        path = self.cache_dir / f"{self._hash(key)}.json"
        with contextlib.suppress(Exception):
            payload = json.dumps(value, ensure_ascii=False, cls=ToolJSONEncoder)
            fd, tmp_name = tempfile.mkstemp(prefix=".git_c_", dir=str(self.cache_dir), text=True)
            os.close(fd)
            tmp = Path(tmp_name)
            tmp.write_text(payload, encoding="utf-8")
            tmp.replace(path)

    def clear(self) -> int:
        removed = 0
        if not self.cache_dir.exists():
            return 0
        for p in self.cache_dir.glob("*.json"):
            with contextlib.suppress(OSError):
                p.unlink()
                removed += 1
        return removed


def invalidate_cache(cache: Optional[ToolCache] = None) -> int:
    return (cache or ToolCache()).clear()


# ==============================================================================
# GRACEFUL CANCELLATION
# ==============================================================================


class GracefulShutdown:
    def __init__(self) -> None:
        self.interrupted = False
        self._old_sigint: Any = None
        self._old_sigterm: Any = None

    def __enter__(self) -> "GracefulShutdown":
        if threading.current_thread() is threading.main_thread():
            with contextlib.suppress(ValueError, AttributeError):
                self._old_sigint = signal.signal(signal.SIGINT, self._handle)
                self._old_sigterm = signal.signal(signal.SIGTERM, self._handle)
        return self

    def __exit__(self, exc_type: Any, exc_val: Any, exc_tb: Any) -> None:
        if threading.current_thread() is threading.main_thread():
            if self._old_sigint:
                with contextlib.suppress(ValueError, AttributeError):
                    signal.signal(signal.SIGINT, self._old_sigint)
            if self._old_sigterm:
                with contextlib.suppress(ValueError, AttributeError):
                    signal.signal(signal.SIGTERM, self._old_sigterm)

    def _handle(self, signum: int, frame: Any) -> None:
        self.interrupted = True

    def should_stop(self) -> bool:
        return self.interrupted


# ==============================================================================
# EXPANDED GIT & GITHUB ENGINE
# ==============================================================================


class GitEngine:
    """Safe, non-shell command executor for Git and GitHub CLI."""

    def __init__(self, repo_dir: Path, timeout: float = DEFAULT_TIMEOUT) -> None:
        self.repo_dir = repo_dir
        self.timeout = timeout
        self.git_bin = shutil.which("git")
        self.gh_bin = shutil.which("gh")

        if not self.git_bin:
            raise ToolError("Git executable ('git') not found in PATH.", EXIT_ERROR, "DependencyError")

    def _run_cmd(
        self,
        args: list[str],
        allow_error: bool = False,
        extra_env: Optional[dict[str, str]] = None,
    ) -> tuple[int, str, str]:
        env = os.environ.copy()
        env["LC_ALL"] = "C"
        env["GIT_TERMINAL_PROMPT"] = "0"
        if extra_env:
            env.update(extra_env)

        try:
            res = subprocess.run(
                args,
                cwd=str(self.repo_dir),
                capture_output=True,
                text=True,
                timeout=self.timeout,
                check=False,
                shell=False,
                env=env,
            )
            stdout = _sanitize_output(res.stdout)
            stderr = _sanitize_output(res.stderr)

            if res.returncode != 0 and not allow_error:
                err_msg = stderr.strip() or stdout.strip() or f"Command failed with code {res.returncode}"
                raise ToolError(
                    f"Command '{' '.join(args[:3])}...' failed: {err_msg}",
                    exit_code=EXIT_ERROR,
                    error_type="GitCommandError",
                    recoverable=True,
                    details={"args": args, "stdout": stdout, "stderr": stderr, "code": res.returncode},
                )
            return res.returncode, stdout, stderr
        except subprocess.TimeoutExpired:
            raise ToolError(f"Command timed out after {self.timeout}s.", EXIT_TIMEOUT, "TimeoutError", recoverable=True)
        except OSError as exc:
            raise ToolError(f"Process execution failure: {exc}", EXIT_ERROR, "SystemError")

    def is_repo(self) -> bool:
        code, _, _ = self._run_cmd(["git", "rev-parse", "--is-inside-work-tree"], allow_error=True)
        return code == 0

    # --------------------------------------------------------------------------
    # REPO STATE & DIAGNOSTICS
    # --------------------------------------------------------------------------

    def status(self) -> dict[str, Any]:
        _, branch_out, _ = self._run_cmd(["git", "branch", "--show-current"])
        current_branch = branch_out.strip() or "HEAD (detached)"

        _, raw_status, _ = self._run_cmd(["git", "status", "--porcelain=v1", "-b"])
        lines = raw_status.splitlines()

        branch_tracking = lines[0] if lines else ""
        staged: list[str] = []
        unstaged: list[str] = []
        untracked: list[str] = []
        conflicts: list[str] = []

        for line in lines[1:]:
            if len(line) < 3:
                continue
            idx, wt, path = line[0], line[1], line[3:].strip()
            if idx in "U" or wt in "U" or (idx == "A" and wt == "A") or (idx == "D" and wt == "D"):
                conflicts.append(path)
            elif idx in "MADRC":
                staged.append(path)
            if wt in "MD":
                unstaged.append(path)
            if idx == "?" and wt == "?":
                untracked.append(path)

        return {
            "current_branch": current_branch,
            "tracking": branch_tracking.replace("## ", ""),
            "clean": len(staged) == 0 and len(unstaged) == 0 and len(untracked) == 0 and len(conflicts) == 0,
            "has_conflicts": len(conflicts) > 0,
            "conflicts": conflicts,
            "staged_count": len(staged),
            "staged_files": staged,
            "unstaged_count": len(unstaged),
            "unstaged_files": unstaged,
            "untracked_count": len(untracked),
            "untracked_files": untracked,
            "raw_status": raw_status.strip(),
        }

    def info(self) -> dict[str, Any]:
        """High-level repository diagnostics and overview."""
        st = self.status()
        _, root_path, _ = self._run_cmd(["git", "rev-parse", "--show-toplevel"])
        _, remotes_out, _ = self._run_cmd(["git", "remote", "-v"], allow_error=True)
        _, last_commit_out, _ = self._run_cmd(["git", "log", "-1", "--format=%H|%an|%cr|%s"], allow_error=True)
        _, stash_out, _ = self._run_cmd(["git", "stash", "list"], allow_error=True)

        last_commit = {}
        if last_commit_out.strip():
            parts = last_commit_out.strip().split("|")
            if len(parts) >= 4:
                last_commit = {"hash": parts[0][:8], "author": parts[1], "date": parts[2], "subject": parts[3]}

        # Check in-progress operations
        git_dir = Path(root_path.strip()) / ".git"
        rebase_merge = (git_dir / "rebase-merge").exists() or (git_dir / "rebase-apply").exists()
        merge_head = (git_dir / "MERGE_HEAD").exists()
        cherry_pick_head = (git_dir / "CHERRY_PICK_HEAD").exists()

        return {
            "root_path": root_path.strip(),
            "branch": st["current_branch"],
            "clean": st["clean"],
            "last_commit": last_commit,
            "remotes": [r.strip() for r in remotes_out.splitlines() if "(fetch)" in r],
            "stashes_count": len(stash_out.splitlines()),
            "in_progress": {
                "merge": merge_head,
                "rebase": rebase_merge,
                "cherry_pick": cherry_pick_head,
            },
            "status_summary": f"Staged: {st['staged_count']}, Unstaged: {st['unstaged_count']}, Untracked: {st['untracked_count']}",
        }

    def conflicts(self) -> dict[str, Any]:
        """Detect and inspect merge, rebase, or cherry-pick conflict files."""
        st = self.status()
        conflict_files = st.get("conflicts", [])
        previews: dict[str, str] = {}
        for f in conflict_files[:5]:
            path = self.repo_dir / f
            if path.is_file():
                try:
                    content = path.read_text(encoding="utf-8", errors="replace")
                    markers = [line for line in content.splitlines() if any(line.startswith(m) for m in ("<<<<<<<", "=======", ">>>>>>>"))]
                    previews[f] = f"{len(markers) // 3} conflict marker block(s) detected"
                except Exception:
                    pass
        return {
            "has_conflicts": len(conflict_files) > 0,
            "conflicting_files": conflict_files,
            "previews": previews,
            "help": "Resolve manually by editing files and committing, or abort with action='merge'/'rebase' and subcommand='abort'.",
        }

    # --------------------------------------------------------------------------
    # LOG, SHOW, DIFF & BLAME
    # --------------------------------------------------------------------------

    def log(self, limit: int = 20, branch: Optional[str] = None) -> list[dict[str, Any]]:
        fmt = "%H%x1f%an%x1f%ae%x1f%at%x1f%s"
        cmd = ["git", "log", f"-n{limit}", f"--format={fmt}"]
        if branch:
            cmd.append(branch)
        code, out, _ = self._run_cmd(cmd, allow_error=True)
        if code != 0 or not out.strip():
            return []

        commits = []
        for line in out.strip().splitlines():
            parts = line.split("\x1f")
            if len(parts) >= 5:
                commit_time = datetime.fromtimestamp(int(parts[3]), tz=timezone.utc).isoformat()
                commits.append(
                    {
                        "hash": parts[0],
                        "short_hash": parts[0][:7],
                        "author": f"{parts[1]} <{parts[2]}>",
                        "timestamp": commit_time,
                        "subject": parts[4],
                    }
                )
        return commits

    def show(self, commit_hash: Optional[str] = None) -> dict[str, Any]:
        ref = commit_hash or "HEAD"
        _, out, _ = self._run_cmd(["git", "show", "--stat", "--patch-with-stat", ref])
        return {"commit": ref, "details": out}

    def diff(self, files: Optional[list[str]] = None, cached: bool = False) -> str:
        cmd = ["git", "diff"]
        if cached:
            cmd.append("--cached")
        if files:
            cmd.append("--")
            cmd.extend(files)
        _, out, _ = self._run_cmd(cmd)
        return out

    def blame(self, file_path: str, line_start: Optional[int] = None, line_end: Optional[int] = None) -> dict[str, Any]:
        cmd = ["git", "blame", "-s"]
        if line_start and line_end:
            cmd.extend([f"-L{line_start},{line_end}"])
        cmd.extend(["--", file_path])
        _, out, _ = self._run_cmd(cmd)
        return {"file": file_path, "blame": out}

    # --------------------------------------------------------------------------
    # COMMITS & BRANCHING
    # --------------------------------------------------------------------------

    def commit(self, message: str, all_files: bool = True, files: Optional[list[str]] = None) -> dict[str, Any]:
        if not message or not message.strip():
            raise ToolError("Commit message is required.", EXIT_INVALID_INPUT, "ValidationError")

        if all_files:
            self._run_cmd(["git", "add", "-A"])
        elif files:
            self._run_cmd(["git", "add", "--"] + files)

        code, out, err = self._run_cmd(["git", "commit", "-m", message.strip()], allow_error=True)
        if code != 0:
            if "nothing to commit" in out or "nothing to commit" in err:
                return {"committed": False, "reason": "Nothing to commit, working tree clean."}
            raise ToolError(f"Commit failed: {err.strip() or out.strip()}", EXIT_ERROR, "GitCommitError")

        return {"committed": True, "output": out.strip()}

    def checkout(self, branch: str, create: bool = False) -> dict[str, Any]:
        if not branch:
            raise ToolError("Target branch or ref name required for checkout.", EXIT_INVALID_INPUT, "ValidationError")
        cmd = ["git", "checkout"]
        if create:
            cmd.append("-b")
        cmd.append(branch)
        _, out, err = self._run_cmd(cmd)
        return {"branch": branch, "created": create, "output": (out + "\n" + err).strip()}

    def branch(self, name: Optional[str] = None, delete: bool = False) -> dict[str, Any]:
        if delete:
            if not name:
                raise ToolError("Branch name required to delete.", EXIT_INVALID_INPUT, "ValidationError")
            _, out, err = self._run_cmd(["git", "branch", "-D" if delete else "-d", name])
            return {"deleted": True, "branch": name, "output": (out + "\n" + err).strip()}

        if name:
            _, out, err = self._run_cmd(["git", "branch", name])
            return {"created": True, "branch": name, "output": (out + "\n" + err).strip()}

        _, out, _ = self._run_cmd(["git", "branch", "--list", "-a"])
        branches = [line.strip().replace("* ", "") for line in out.splitlines()]
        return {"branches": branches}

    # --------------------------------------------------------------------------
    # MERGE, REBASE, CHERRY-PICK & REVERT
    # --------------------------------------------------------------------------

    def merge(self, branch: Optional[str] = None, subcommand: Optional[str] = None, message: Optional[str] = None) -> dict[str, Any]:
        if subcommand in {"abort", "continue"}:
            cmd = ["git", "merge", f"--{subcommand}"]
            _, out, err = self._run_cmd(cmd)
            return {"merge_op": subcommand, "output": (out + "\n" + err).strip()}

        if not branch:
            raise ToolError("Target branch name required for merge.", EXIT_INVALID_INPUT, "ValidationError")

        cmd = ["git", "merge", branch]
        if message:
            cmd.extend(["-m", message.strip()])
        code, out, err = self._run_cmd(cmd, allow_error=True)
        if code != 0:
            if "CONFLICT" in out or "CONFLICT" in err:
                return {"merged": False, "conflict": True, "output": (out + "\n" + err).strip()}
            raise ToolError(f"Merge failed: {err.strip() or out.strip()}", EXIT_ERROR, "GitMergeError")
        return {"merged": True, "branch": branch, "output": (out + "\n" + err).strip()}

    def rebase(self, branch: Optional[str] = None, subcommand: Optional[str] = None) -> dict[str, Any]:
        if subcommand in {"abort", "continue", "skip"}:
            cmd = ["git", "rebase", f"--{subcommand}"]
            _, out, err = self._run_cmd(cmd)
            return {"rebase_op": subcommand, "output": (out + "\n" + err).strip()}

        if not branch:
            raise ToolError("Upstream target branch required to start rebase.", EXIT_INVALID_INPUT, "ValidationError")

        code, out, err = self._run_cmd(["git", "rebase", branch], allow_error=True)
        if code != 0:
            if "CONFLICT" in out or "CONFLICT" in err:
                return {"rebased": False, "conflict": True, "output": (out + "\n" + err).strip()}
            raise ToolError(f"Rebase failed: {err.strip() or out.strip()}", EXIT_ERROR, "GitRebaseError")
        return {"rebased": True, "branch": branch, "output": (out + "\n" + err).strip()}

    def cherry_pick(self, commit_hash: Optional[str] = None, subcommand: Optional[str] = None) -> dict[str, Any]:
        if subcommand in {"abort", "continue", "skip"}:
            cmd = ["git", "cherry-pick", f"--{subcommand}"]
            _, out, err = self._run_cmd(cmd)
            return {"cherry_pick_op": subcommand, "output": (out + "\n" + err).strip()}

        if not commit_hash:
            raise ToolError("Commit hash required for cherry-pick.", EXIT_INVALID_INPUT, "ValidationError")

        code, out, err = self._run_cmd(["git", "cherry-pick", commit_hash], allow_error=True)
        if code != 0:
            if "CONFLICT" in out or "CONFLICT" in err:
                return {"cherry_picked": False, "conflict": True, "output": (out + "\n" + err).strip()}
            raise ToolError(f"Cherry-pick failed: {err.strip() or out.strip()}", EXIT_ERROR, "GitCherryPickError")
        return {"cherry_picked": True, "commit": commit_hash, "output": (out + "\n" + err).strip()}

    def revert(self, commit_hash: str) -> dict[str, Any]:
        if not commit_hash:
            raise ToolError("Commit hash required for revert.", EXIT_INVALID_INPUT, "ValidationError")
        _, out, err = self._run_cmd(["git", "revert", "--no-edit", commit_hash])
        return {"reverted": True, "commit": commit_hash, "output": (out + "\n" + err).strip()}

    # --------------------------------------------------------------------------
    # RESTORE, RESET & CLEAN
    # --------------------------------------------------------------------------

    def restore(self, files: Optional[list[str]] = None, staged: bool = False) -> dict[str, Any]:
        cmd = ["git", "restore"]
        if staged:
            cmd.append("--staged")
        if files:
            cmd.append("--")
            cmd.extend(files)
        else:
            cmd.append(".")
        _, out, err = self._run_cmd(cmd)
        return {"restored": True, "files": files or ["."], "output": (out + "\n" + err).strip()}

    def reset(self, mode: str = "mixed", commit_hash: Optional[str] = None) -> dict[str, Any]:
        ref = commit_hash or "HEAD~1"
        flag = f"--{mode}" if mode in {"soft", "mixed", "hard"} else "--mixed"
        _, out, err = self._run_cmd(["git", "reset", flag, ref])
        return {"reset": True, "mode": mode, "target": ref, "output": (out + "\n" + err).strip()}

    def clean(self, dry_run: bool = True) -> dict[str, Any]:
        flag = "-n" if dry_run else "-f"
        _, out, _ = self._run_cmd(["git", "clean", flag, "-d"])
        return {"clean_executed": not dry_run, "items": [l.replace("Would remove ", "").strip() for l in out.splitlines()]}

    # --------------------------------------------------------------------------
    # STASH & TAGS
    # --------------------------------------------------------------------------

    def stash(self, subcommand: str = "save", message: Optional[str] = None) -> dict[str, Any]:
        cmd = ["git", "stash"]
        if subcommand in {"save", "push"}:
            cmd.append("push")
            if message:
                cmd.extend(["-m", message.strip()])
        elif subcommand in {"pop", "list", "drop", "clear", "apply"}:
            cmd.append(subcommand)
        else:
            raise ToolError(f"Unsupported stash operation '{subcommand}'", EXIT_INVALID_INPUT, "ValidationError")

        _, out, err = self._run_cmd(cmd)
        return {"stash_op": subcommand, "output": (out + "\n" + err).strip()}

    def tag(self, name: Optional[str] = None, subcommand: str = "list", message: Optional[str] = None) -> dict[str, Any]:
        if subcommand == "list" or (not name and subcommand != "create"):
            _, out, _ = self._run_cmd(["git", "tag", "-l"])
            return {"tags": out.splitlines()}

        if subcommand == "create":
            if not name:
                raise ToolError("Tag name is required to create a tag.", EXIT_INVALID_INPUT, "ValidationError")
            cmd = ["git", "tag"]
            if message:
                cmd.extend(["-a", name, "-m", message.strip()])
            else:
                cmd.append(name)
            _, out, err = self._run_cmd(cmd)
            return {"created_tag": name, "output": (out + "\n" + err).strip()}

        if subcommand == "delete":
            if not name:
                raise ToolError("Tag name is required to delete.", EXIT_INVALID_INPUT, "ValidationError")
            _, out, err = self._run_cmd(["git", "tag", "-d", name])
            return {"deleted_tag": name, "output": (out + "\n" + err).strip()}

        raise ToolError(f"Unsupported tag operation '{subcommand}'", EXIT_INVALID_INPUT, "ValidationError")

    # --------------------------------------------------------------------------
    # NETWORK: PUSH, PULL, FETCH, REMOTE
    # --------------------------------------------------------------------------

    def push(self, remote: str = "origin", branch: Optional[str] = None, set_upstream: bool = True, push_tags: bool = False) -> dict[str, Any]:
        cmd = ["git", "push"]
        if push_tags:
            cmd.append("--tags")
        if set_upstream and not push_tags:
            cmd.append("-u")
        cmd.append(remote)
        if branch and not push_tags:
            cmd.append(branch)
        _, out, err = self._run_cmd(cmd)
        return {"pushed": True, "remote": remote, "branch": branch, "output": (out + "\n" + err).strip()}

    def pull(self, remote: str = "origin", branch: Optional[str] = None) -> dict[str, Any]:
        cmd = ["git", "pull", remote]
        if branch:
            cmd.append(branch)
        _, out, err = self._run_cmd(cmd)
        return {"pulled": True, "output": (out + "\n" + err).strip()}

    def fetch(self, remote: str = "origin", prune: bool = True) -> dict[str, Any]:
        cmd = ["git", "fetch", remote]
        if prune:
            cmd.append("--prune")
        _, out, err = self._run_cmd(cmd)
        return {"fetched": True, "remote": remote, "output": (out + "\n" + err).strip()}

    def remote(self, subcommand: str = "list", name: Optional[str] = None, url: Optional[str] = None) -> dict[str, Any]:
        if subcommand == "list":
            _, out, _ = self._run_cmd(["git", "remote", "-v"])
            return {"remotes": [line.strip() for line in out.splitlines()]}
        if subcommand == "add":
            if not name or not url:
                raise ToolError("Remote name and URL required for remote add.", EXIT_INVALID_INPUT, "ValidationError")
            _, out, err = self._run_cmd(["git", "remote", "add", name, url])
            return {"added_remote": name, "url": url, "output": (out + "\n" + err).strip()}
        if subcommand == "remove":
            if not name:
                raise ToolError("Remote name required to remove.", EXIT_INVALID_INPUT, "ValidationError")
            _, out, err = self._run_cmd(["git", "remote", "remove", name])
            return {"removed_remote": name, "output": (out + "\n" + err).strip()}
        raise ToolError(f"Unsupported remote subcommand: {subcommand}", EXIT_INVALID_INPUT, "ValidationError")

    # --------------------------------------------------------------------------
    # GITHUB PR & ISSUE WORKFLOWS (via gh CLI)
    # --------------------------------------------------------------------------

    def github_pr(
        self,
        subcommand: str = "list",
        title: Optional[str] = None,
        body: Optional[str] = None,
        branch: Optional[str] = None,
        limit: int = 20,
    ) -> dict[str, Any]:
        if not self.gh_bin:
            raise ToolError("GitHub CLI ('gh') is not installed or not in PATH.", EXIT_ERROR, "DependencyError")

        if subcommand == "list":
            cmd = ["gh", "pr", "list", "--limit", str(limit), "--json", "number,title,state,headRefName,author,url"]
            _, out, _ = self._run_cmd(cmd)
            try:
                prs = json.loads(out)
                return {"pull_requests": prs, "count": len(prs)}
            except json.JSONDecodeError:
                return {"raw_output": out}

        if subcommand == "create":
            if not title:
                raise ToolError("Title required to create a Pull Request.", EXIT_INVALID_INPUT, "ValidationError")
            cmd = ["gh", "pr", "create", "--title", title.strip(), "--body", (body or "").strip()]
            if branch:
                cmd.extend(["--base", branch])
            _, out, err = self._run_cmd(cmd)
            return {"created": True, "output": (out + "\n" + err).strip()}

        if subcommand == "view":
            cmd = ["gh", "pr", "view"]
            if branch:
                cmd.append(branch)
            cmd.extend(["--json", "number,title,state,body,url,reviews,mergeable"])
            _, out, _ = self._run_cmd(cmd)
            try:
                return {"pr_details": json.loads(out)}
            except json.JSONDecodeError:
                return {"raw_output": out}

        if subcommand == "checkout":
            if not branch:
                raise ToolError("PR number or branch name required to checkout.", EXIT_INVALID_INPUT, "ValidationError")
            _, out, err = self._run_cmd(["gh", "pr", "checkout", branch])
            return {"checked_out_pr": branch, "output": (out + "\n" + err).strip()}

        if subcommand == "merge":
            if not branch:
                raise ToolError("PR number or branch required to merge.", EXIT_INVALID_INPUT, "ValidationError")
            _, out, err = self._run_cmd(["gh", "pr", "merge", branch, "--merge", "--auto"])
            return {"merged_pr": branch, "output": (out + "\n" + err).strip()}

        if subcommand == "checks":
            cmd = ["gh", "pr", "checks"]
            if branch:
                cmd.append(branch)
            _, out, err = self._run_cmd(cmd)
            return {"checks": (out + "\n" + err).strip()}

        raise ToolError(f"Unsupported PR action: {subcommand}", EXIT_INVALID_INPUT, "ValidationError")

    def github_issue(
        self,
        subcommand: str = "list",
        title: Optional[str] = None,
        body: Optional[str] = None,
        issue_id: Optional[str] = None,
        limit: int = 20,
    ) -> dict[str, Any]:
        if not self.gh_bin:
            raise ToolError("GitHub CLI ('gh') is not installed or not in PATH.", EXIT_ERROR, "DependencyError")

        if subcommand == "list":
            cmd = ["gh", "issue", "list", "--limit", str(limit), "--json", "number,title,state,author,url,labels"]
            _, out, _ = self._run_cmd(cmd)
            try:
                issues = json.loads(out)
                return {"issues": issues, "count": len(issues)}
            except json.JSONDecodeError:
                return {"raw_output": out}

        if subcommand == "create":
            if not title:
                raise ToolError("Title required to create an Issue.", EXIT_INVALID_INPUT, "ValidationError")
            cmd = ["gh", "issue", "create", "--title", title.strip(), "--body", (body or "").strip()]
            _, out, err = self._run_cmd(cmd)
            return {"created": True, "output": (out + "\n" + err).strip()}

        if subcommand == "view":
            target = issue_id or title
            if not target:
                raise ToolError("Issue number or title required to view.", EXIT_INVALID_INPUT, "ValidationError")
            cmd = ["gh", "issue", "view", target, "--json", "number,title,state,body,comments,url"]
            _, out, _ = self._run_cmd(cmd)
            try:
                return {"issue_details": json.loads(out)}
            except json.JSONDecodeError:
                return {"raw_output": out}

        raise ToolError(f"Unsupported Issue action: {subcommand}", EXIT_INVALID_INPUT, "ValidationError")


# ==============================================================================
# CORE EXECUTION ROUTER
# ==============================================================================


def execute_tool(
    target: str,
    action: str = "status",
    subcommand: Optional[str] = None,
    branch: Optional[str] = None,
    commit_hash: Optional[str] = None,
    message: Optional[str] = None,
    body: Optional[str] = None,
    files: Optional[list[str]] = None,
    remote: str = "origin",
    create_branch: bool = False,
    delete_branch: bool = False,
    all_files: bool = True,
    set_upstream: bool = True,
    limit: int = DEFAULT_LIMIT,
    line_start: Optional[int] = None,
    line_end: Optional[int] = None,
    timeout: Optional[float] = DEFAULT_TIMEOUT,
    use_cache: bool = False,
    cache_ttl: int = DEFAULT_CACHE_TTL,
    cache_dir: Optional[str] = None,
    dry_run: bool = False,
    verbose: bool = False,
    no_color: bool = True,
    quiet: bool = True,
    output_format: str = "jsonl",
    env_vars: Optional[list[str]] = None,
) -> dict[str, Any]:
    started = time.monotonic()
    request_id = f"req_{uuid.uuid4().hex[:12]}"
    warnings: list[str] = []

    setup_tool_logging(verbose, request_id)

    bad = validate_inputs(
        target=target,
        action=action,
        branch=branch,
        commit_hash=commit_hash,
        limit=limit,
        timeout=timeout,
    )
    if bad:
        bad["request_id"] = request_id
        bad["timestamp"] = datetime.now(timezone.utc).isoformat()
        return bad

    safe_env, env_warnings = SecurityValidator.sanitize_env_vars(env_vars)
    warnings.extend(env_warnings)

    try:
        repo_path = resolve_agent_path(target)
        if not repo_path.exists():
            return ToolError(
                f"Directory does not exist: '{target}' (resolved: '{repo_path}').",
                EXIT_FILE_NOT_FOUND,
                "FileNotFoundError",
                trace_id=request_id,
            ).to_dict(verbose=verbose)

        engine = GitEngine(repo_dir=repo_path, timeout=timeout or DEFAULT_TIMEOUT)
        if not engine.is_repo():
            return ToolError(
                f"Path '{repo_path}' is not inside a Git working tree.",
                EXIT_INVALID_INPUT,
                "NotAGitRepository",
                trace_id=request_id,
            ).to_dict(verbose=verbose)

        cache = ToolCache(cache_dir=cache_dir, ttl=cache_ttl)
        cache_key = build_cache_key(repo_path, action, branch, limit)

        if use_cache and action in READ_ONLY_ACTIONS and not dry_run:
            cached = cache.get(cache_key)
            if cached is not None:
                cached["cached"] = True
                cached["duration_ms"] = round((time.monotonic() - started) * 1000, 2)
                return cached

        if dry_run and action not in READ_ONLY_ACTIONS:
            return {
                "success": True,
                "request_id": request_id,
                "dry_run": True,
                "action": action,
                "subcommand": subcommand,
                "target": str(repo_path),
                "summary": f"[DRY-RUN] Simulated execution for '{action}' ({subcommand or 'default'}) on branch '{branch or 'current'}'. No changes applied.",
                "duration_ms": round((time.monotonic() - started) * 1000, 2),
                "exit_code": EXIT_SUCCESS,
            }

        with GracefulShutdown() as shutdown:
            if shutdown.should_stop():
                raise ToolError("Execution cancelled by user signal.", EXIT_INTERRUPTED, "InterruptError", recoverable=True)

            action_data: dict[str, Any] = {}

            # Actions Dispatcher
            if action == "status":
                action_data = engine.status()
            elif action == "info":
                action_data = engine.info()
            elif action == "conflicts":
                action_data = engine.conflicts()
            elif action == "log":
                commits = engine.log(limit=limit, branch=branch)
                action_data = {"count": len(commits), "commits": commits}
            elif action == "show":
                action_data = engine.show(commit_hash=commit_hash or branch)
            elif action == "diff":
                diff_out = engine.diff(files=files, cached=(subcommand == "staged"))
                action_data = {"diff": diff_out, "has_diff": bool(diff_out.strip())}
            elif action == "blame":
                target_file = (files[0] if files else branch) or ""
                if not target_file:
                    raise ToolError("File path is required for blame inspection.", EXIT_INVALID_INPUT, "ValidationError")
                action_data = engine.blame(file_path=target_file, line_start=line_start, line_end=line_end)
            elif action == "commit":
                action_data = engine.commit(message=message or "Update", all_files=all_files, files=files)
            elif action == "push":
                action_data = engine.push(remote=remote, branch=branch, set_upstream=set_upstream, push_tags=(subcommand == "tags"))
            elif action == "pull":
                action_data = engine.pull(remote=remote, branch=branch)
            elif action == "fetch":
                action_data = engine.fetch(remote=remote)
            elif action == "checkout":
                action_data = engine.checkout(branch=branch or "", create=create_branch)
            elif action == "branch":
                action_data = engine.branch(name=branch, delete=delete_branch)
            elif action == "merge":
                action_data = engine.merge(branch=branch, subcommand=subcommand, message=message)
            elif action == "rebase":
                action_data = engine.rebase(branch=branch, subcommand=subcommand)
            elif action == "cherry_pick":
                action_data = engine.cherry_pick(commit_hash=commit_hash or branch, subcommand=subcommand)
            elif action == "revert":
                action_data = engine.revert(commit_hash=commit_hash or branch or "")
            elif action == "restore":
                action_data = engine.restore(files=files, staged=(subcommand == "staged"))
            elif action == "reset":
                action_data = engine.reset(mode=subcommand or "mixed", commit_hash=commit_hash or branch)
            elif action == "clean":
                action_data = engine.clean(dry_run=(subcommand != "force"))
            elif action == "stash":
                action_data = engine.stash(subcommand=subcommand or "save", message=message)
            elif action == "tag":
                action_data = engine.tag(name=branch, subcommand=subcommand or "list", message=message)
            elif action == "remote":
                action_data = engine.remote(subcommand=subcommand or "list", name=branch or remote, url=message)
            elif action == "pr":
                action_data = engine.github_pr(subcommand=subcommand or "list", title=message, body=body, branch=branch, limit=limit)
            elif action == "issue":
                action_data = engine.github_issue(subcommand=subcommand or "list", title=message, body=body, issue_id=branch, limit=limit)

        duration_ms = round((time.monotonic() - started) * 1000, 2)
        payload = {
            "success": True,
            "request_id": request_id,
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "tool": __tool_name__,
            "version": __version__,
            "target": str(repo_path),
            "action": action,
            "subcommand": subcommand,
            "cached": False,
            "dry_run": dry_run,
            "duration_ms": duration_ms,
            "warnings": warnings,
            "exit_code": EXIT_SUCCESS,
            **action_data,
        }

        res = json_safe(payload)
        if use_cache and action in READ_ONLY_ACTIONS:
            cache.set(cache_key, res)

        return res

    except ToolError as exc:
        duration_ms = round((time.monotonic() - started) * 1000, 2)
        err_res = exc.to_dict(verbose=verbose)
        err_res.update(
            {
                "request_id": request_id,
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "duration_ms": duration_ms,
                "warnings": warnings,
            }
        )
        return json_safe(err_res)
    except Exception as exc:
        duration_ms = round((time.monotonic() - started) * 1000, 2)
        return ToolError(
            f"Unexpected failure: {exc}",
            EXIT_ERROR,
            "UnhandledException",
            trace_id=request_id,
            details={"duration_ms": duration_ms, "warnings": warnings},
        ).to_dict(verbose=verbose)


# ==============================================================================
# SCHEMA GENERATOR
# ==============================================================================


def generate_tool_schema() -> dict[str, Any]:
    return {
        "name": "run",
        "description": __tool_description__,
        "parameters": {
            "type": "object",
            "properties": {
                "target": {
                    "type": "string",
                    "description": "Path to local repository root (default '.').",
                    "default": ".",
                },
                "action": {
                    "type": "string",
                    "enum": sorted(VALID_ACTIONS),
                    "description": (
                        "Primary Git operation:\n"
                        "- 'status': working tree state & staged/unstaged counts\n"
                        "- 'info': high-level repository health, remote overview & branch summary\n"
                        "- 'log': commit history with author, date, and hash\n"
                        "- 'show': detailed patch and stats for a commit\n"
                        "- 'diff': unstaged or staged file differences\n"
                        "- 'blame': line-by-line file blame inspection\n"
                        "- 'commit': stage files and commit with message\n"
                        "- 'push': push local commits to remote (use subcommand='tags' for tags)\n"
                        "- 'pull': fetch and merge updates from remote\n"
                        "- 'fetch': retrieve remote refs and prune stale branches\n"
                        "- 'checkout': switch branches or create with create_branch=True\n"
                        "- 'branch': list, create, or delete branches\n"
                        "- 'merge': merge branches or abort/continue conflicts\n"
                        "- 'rebase': rebase commits onto upstream or abort/continue\n"
                        "- 'cherry_pick': apply commit by SHA or abort/continue\n"
                        "- 'revert': record a reverse commit to undo changes safely\n"
                        "- 'restore': discard uncommitted changes in files or tree\n"
                        "- 'reset': soft, mixed, or hard HEAD reset\n"
                        "- 'clean': inspect or clean untracked files\n"
                        "- 'conflicts': inspect files with conflict markers and status\n"
                        "- 'stash': save, pop, list, apply, or drop changes\n"
                        "- 'tag': list, create, or delete repository tags\n"
                        "- 'remote': list, add, or remove remotes\n"
                        "- 'pr': GitHub Pull Request workflows (list, view, create, checkout, merge, checks)\n"
                        "- 'issue': GitHub Issue management (list, view, create)"
                    ),
                    "default": "status",
                },
                "subcommand": {
                    "type": ["string", "null"],
                    "description": (
                        "Specific subcommand modifier:\n"
                        "- for pr: 'list', 'view', 'create', 'checkout', 'merge', 'checks'\n"
                        "- for issue: 'list', 'view', 'create'\n"
                        "- for stash: 'save', 'pop', 'list', 'drop', 'apply'\n"
                        "- for tag: 'list', 'create', 'delete'\n"
                        "- for merge/rebase/cherry_pick: 'abort', 'continue', 'skip'\n"
                        "- for diff/restore: 'staged'\n"
                        "- for reset: 'soft', 'mixed', 'hard'\n"
                        "- for clean: 'force' (otherwise defaults to dry-run)\n"
                        "- for push: 'tags'"
                    ),
                    "default": None,
                },
                "branch": {
                    "type": ["string", "null"],
                    "description": "Branch name, ref, tag, or PR number/ID.",
                    "default": None,
                },
                "commit_hash": {
                    "type": ["string", "null"],
                    "description": "Commit hash for show, cherry_pick, revert, reset, or blame.",
                    "default": None,
                },
                "message": {
                    "type": ["string", "null"],
                    "description": "Commit message, tag description, or PR/Issue title.",
                    "default": None,
                },
                "body": {
                    "type": ["string", "null"],
                    "description": "Long-form description for Pull Requests or GitHub Issues.",
                    "default": None,
                },
                "files": {
                    "type": ["array", "null"],
                    "items": {"type": "string"},
                    "description": "File paths for commit, diff, restore, or blame.",
                    "default": None,
                },
                "remote": {
                    "type": "string",
                    "description": "Remote name (defaults to 'origin').",
                    "default": "origin",
                },
                "create_branch": {
                    "type": "boolean",
                    "description": "Create new branch when checking out (-b).",
                    "default": False,
                },
                "delete_branch": {
                    "type": "boolean",
                    "description": "Delete specified branch or tag.",
                    "default": False,
                },
                "all_files": {
                    "type": "boolean",
                    "description": "Stage all modified and untracked files on commit (git add -A).",
                    "default": True,
                },
                "set_upstream": {
                    "type": "boolean",
                    "description": "Set upstream tracking automatically on push (-u).",
                    "default": True,
                },
                "limit": {
                    "type": "integer",
                    "minimum": 1,
                    "maximum": MAX_LIMIT,
                    "description": "Max entries to return for log, PR, or issue lists.",
                    "default": DEFAULT_LIMIT,
                },
                "line_start": {
                    "type": ["integer", "null"],
                    "description": "Start line for blame inspection.",
                    "default": None,
                },
                "line_end": {
                    "type": ["integer", "null"],
                    "description": "End line for blame inspection.",
                    "default": None,
                },
                "timeout": {
                    "type": ["number", "null"],
                    "description": "Command execution timeout in seconds.",
                    "default": DEFAULT_TIMEOUT,
                },
                "dry_run": {
                    "type": "boolean",
                    "description": "Simulate git actions safely without making changes.",
                    "default": False,
                },
            },
            "required": ["target"],
            "additionalProperties": False,
        },
    }


# ==============================================================================
# CANONICAL AIChat ENTRYPOINT
# ==============================================================================


def run(
    target: str = ".",
    action: Literal[
        "status",
        "info",
        "log",
        "show",
        "diff",
        "blame",
        "commit",
        "push",
        "pull",
        "fetch",
        "checkout",
        "branch",
        "merge",
        "rebase",
        "cherry_pick",
        "revert",
        "reset",
        "restore",
        "stash",
        "tag",
        "remote",
        "clean",
        "conflicts",
        "pr",
        "issue",
    ] = "status",
    subcommand: Optional[str] = None,
    branch: Optional[str] = None,
    commit_hash: Optional[str] = None,
    message: Optional[str] = None,
    body: Optional[str] = None,
    files: Optional[list[str]] = None,
    remote: str = "origin",
    create_branch: bool = False,
    delete_branch: bool = False,
    all_files: bool = True,
    set_upstream: bool = True,
    limit: int = DEFAULT_LIMIT,
    line_start: Optional[int] = None,
    line_end: Optional[int] = None,
    timeout: Optional[float] = DEFAULT_TIMEOUT,
    dry_run: bool = False,
) -> dict[str, Any]:
    """
    Execute Git and GitHub repository operations smoothly.

    This function never prints to stdout; it returns a fully JSON-serializable dictionary.
    """
    return execute_tool(
        target=target,
        action=action,
        subcommand=subcommand,
        branch=branch,
        commit_hash=commit_hash,
        message=message,
        body=body,
        files=files,
        remote=remote,
        create_branch=create_branch,
        delete_branch=delete_branch,
        all_files=all_files,
        set_upstream=set_upstream,
        limit=limit,
        line_start=line_start,
        line_end=line_end,
        timeout=timeout,
        dry_run=dry_run,
        no_color=True,
        verbose=False,
        quiet=True,
    )


# ==============================================================================
# CLI OUTPUT ROUTING
# ==============================================================================


def write_llm_output(data: Mapping[str, Any], output_format: str = "jsonl") -> None:
    indent = 2 if output_format == "json" else None
    payload = json.dumps(data, ensure_ascii=False, cls=ToolJSONEncoder, indent=indent)
    out_path = os.environ.get("LLM_OUTPUT", "/dev/stdout")
    if out_path in {"/dev/stdout", "/dev/fd/1", "-"}:
        sys.stdout.write(payload + "\n")
        sys.stdout.flush()
        return

    try:
        dest = Path(out_path).expanduser()
        dest.parent.mkdir(parents=True, exist_ok=True)
        with dest.open("a", encoding="utf-8") as handle:
            handle.write(payload + "\n")
    except OSError as exc:
        _cprint(f"{NEON_RED}LLM_OUTPUT write failed:{RESET} {exc}", no_color=False)
        sys.stdout.write(payload + "\n")
        sys.stdout.flush()


# ==============================================================================
# SELF TEST SUITE
# ==============================================================================


def self_test() -> int:
    """Run automated regression test suite on an isolated temporary repository."""
    failures: list[str] = []

    def check(name: str, cond: bool) -> None:
        if not cond:
            failures.append(name)

    with tempfile.TemporaryDirectory(prefix="aichat-git-fulltest-") as tmp:
        repo = Path(tmp)

        # 1. Initialize
        subprocess.run(["git", "init", "-b", "main"], cwd=str(repo), check=True, capture_output=True)
        subprocess.run(["git", "config", "user.name", "Tester"], cwd=str(repo), check=True)
        subprocess.run(["git", "config", "user.email", "tester@repo.local"], cwd=str(repo), check=True)

        # 2. Status & Info
        st = run(target=str(repo), action="status")
        check("status initial", st.get("clean") is True)
        inf = run(target=str(repo), action="info")
        check("info branch detected", inf.get("branch") == "main")

        # 3. Create file & commit
        test_file = repo / "code.py"
        test_file.write_text("def hello():\n    print('world')\n", encoding="utf-8")
        cm = run(target=str(repo), action="commit", message="feat: add hello world")
        check("commit succeeds", cm.get("committed") is True)

        # 4. Log & Show
        lg = run(target=str(repo), action="log", limit=5)
        check("log has commit", lg.get("count") == 1)
        commit_sha = lg["commits"][0]["hash"]
        sh = run(target=str(repo), action="show", commit_hash=commit_sha)
        check("show commit", commit_sha[:7] in sh.get("details", ""))

        # 5. Branch & Checkout
        br = run(target=str(repo), action="checkout", branch="feature-ai", create_branch=True)
        check("checkout new branch", br.get("branch") == "feature-ai")

        # 6. Tags
        tg = run(target=str(repo), action="tag", subcommand="create", branch="v1.0.0", message="First release")
        check("create tag", tg.get("created_tag") == "v1.0.0")
        tg_l = run(target=str(repo), action="tag", subcommand="list")
        check("list tag contains v1.0.0", "v1.0.0" in tg_l.get("tags", []))

        # 7. Stash
        test_file.write_text("def hello():\n    print('modified')\n", encoding="utf-8")
        st_save = run(target=str(repo), action="stash", subcommand="save", message="temp changes")
        check("stash save", st_save.get("stash_op") == "save")
        st_pop = run(target=str(repo), action="stash", subcommand="pop")
        check("stash pop", st_pop.get("stash_op") == "pop")

        # 8. Restore
        rst = run(target=str(repo), action="restore")
        check("restore clean", rst.get("restored") is True)

        # 9. Blame
        blm = run(target=str(repo), action="blame", files=["code.py"], line_start=1, line_end=2)
        check("blame returns lines", "hello" in blm.get("blame", ""))

        # 10. Conflict detection
        conf = run(target=str(repo), action="conflicts")
        check("conflicts is false", conf.get("has_conflicts") is False)

        # 11. Dry-Run
        dr = run(target=str(repo), action="commit", message="test dry run", dry_run=True)
        check("dry run respected", dr.get("dry_run") is True)

    if failures:
        for f in failures:
            print(f"FAIL: {f}", file=sys.stderr)
        return EXIT_ERROR

    print("PASS: Git manager comprehensive test suite passed cleanly.")
    return EXIT_SUCCESS


# ==============================================================================
# CLI PARSER
# ==============================================================================


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog=__tool_name__,
        description=f"{__tool_description__} (v{__version__})",
    )
    parser.add_argument("--target", "-t", default=".", metavar="PATH")
    parser.add_argument("--action", "-a", choices=sorted(VALID_ACTIONS), default="status")
    parser.add_argument("--subcommand", metavar="CMD")
    parser.add_argument("--branch", "-b", metavar="NAME")
    parser.add_argument("--commit-hash", metavar="SHA")
    parser.add_argument("--message", "-m", metavar="TEXT")
    parser.add_argument("--body", metavar="TEXT")
    parser.add_argument("--file", action="append", dest="files", metavar="PATH")
    parser.add_argument("--remote", default="origin", metavar="NAME")
    parser.add_argument("--create-branch", action="store_true")
    parser.add_argument("--delete-branch", action="store_true")
    parser.add_argument("--all-files", action="store_true", default=True)
    parser.add_argument("--set-upstream", action="store_true", default=True)
    parser.add_argument("--limit", type=int, default=DEFAULT_LIMIT, metavar="NUM")
    parser.add_argument("--line-start", type=int, metavar="NUM")
    parser.add_argument("--line-end", type=int, metavar="NUM")
    parser.add_argument("--timeout", type=float, default=DEFAULT_TIMEOUT, metavar="SEC")
    parser.add_argument("--cache-ttl", type=int, default=DEFAULT_CACHE_TTL, metavar="SEC")
    parser.add_argument("--cache-dir", metavar="PATH")
    parser.add_argument("--env-var", action="append", dest="env_vars", metavar="KEY=VALUE")
    parser.add_argument("--output-format", choices=sorted(VALID_OUTPUT_FORMATS), default="jsonl")
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
        sys.stdout.write(json.dumps(generate_tool_schema(), indent=2, ensure_ascii=False, cls=ToolJSONEncoder) + "\n")
        return EXIT_SUCCESS

    if args.clear_cache:
        cache = ToolCache(cache_dir=args.cache_dir, ttl=args.cache_ttl)
        removed = invalidate_cache(cache)
        if not args.quiet and not args.json_only:
            _cprint(f"{NEON_GREEN}✓ Cleared {removed} cache records.{RESET}", no_color=args.no_color)
        return EXIT_SUCCESS

    result = execute_tool(
        target=args.target,
        action=args.action,
        subcommand=args.subcommand,
        branch=args.branch,
        commit_hash=args.commit_hash,
        message=args.message,
        body=args.body,
        files=args.files,
        remote=args.remote,
        create_branch=args.create_branch,
        delete_branch=args.delete_branch,
        all_files=args.all_files,
        set_upstream=args.set_upstream,
        limit=args.limit,
        line_start=args.line_start,
        line_end=args.line_end,
        timeout=args.timeout,
        use_cache=args.use_cache,
        cache_ttl=args.cache_ttl,
        cache_dir=args.cache_dir,
        dry_run=args.dry_run,
        verbose=args.verbose,
        no_color=args.no_color,
        quiet=args.quiet,
        output_format=args.output_format,
        env_vars=args.env_vars,
    )

    if not args.json_only:
        print_human_readable_ui(result, no_color=args.no_color, quiet=args.quiet)

    write_llm_output(result, args.output_format)
    return int(result.get("exit_code", EXIT_SUCCESS))


if __name__ == "__main__":
    raise SystemExit(main())
