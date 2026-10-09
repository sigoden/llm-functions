#!/usr/bin/env python3
# ==============================================================================
# github_tool.py — Pyrmethus Termux GitHub Operations Tool v2.2.0-ASCENDED
# argc/aichat compatible · Termux · Native Caching · Full GitHub API Engine
#
# @describe Complete GitHub operations tool for Termux (repos, issues, PRs, gists, search, clone, workflows).
#
# @meta require-tools aichat
#
# @option --action <ACTION>             Operation: repo-info/repo-list/repo-clone/issue-list/issue-create/pr-list/gist-list/gist-create/search/user-info/workflow-list (default: user-info)
# @option --repo <OWNER/REPO>           Target repository (e.g. octocat/Hello-World)
# @option --query <TEXT>                Search query or title for issue/gist
# @option --body <TEXT>                 Body text for issue/PR/gist creation
# @option --state <STATE>               Filter state: open/closed/all (default: open)
# @option --limit <NUM>                 Maximum results to fetch (default: 30)
# @option --token <TOKEN>               GitHub Personal Access Token (or set GITHUB_TOKEN env)
# @option --target <PATH>               Local target directory path for git clone
# @option --mode <MODE>                 Execution mode: summary/detailed (default: summary)
# @flag   --use-cache                   Enable result caching for GitHub API requests
# @flag   --no-color                    Disable ANSI color output
# @flag   --verbose                     Enable detailed debug log output
#
# @env LLM_OUTPUT=/dev/stdout            Output path for LLM integration
# ==============================================================================

from __future__ import annotations

import argparse
import hashlib
import json
import logging
import os
import pickle
import re
import shutil
import signal
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import date, datetime, timedelta
from enum import Enum
from pathlib import Path
from typing import Any, Literal, Optional, Tuple

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
        if isinstance(obj, (datetime, date)):
            return obj.isoformat()
        if isinstance(obj, timedelta):
            return obj.total_seconds()
        if isinstance(obj, bytes):
            return obj.decode("utf-8", errors="replace")
        if isinstance(obj, (set, frozenset)):
            return list(obj)
        if hasattr(obj, "to_dict") and callable(obj.to_dict):
            return obj.to_dict()
        try:
            return super().default(obj)
        except TypeError:
            return str(obj)


# ==============================================================================
# SECTION 2: Terminal Color Palette & UI Helpers
# ==============================================================================

NEON_CYAN = "\033[38;5;51m"
NEON_GREEN = "\033[38;5;46m"
NEON_RED = "\033[38;5;196m"
NEON_YELLOW = "\033[38;5;226m"
NEON_PURPLE = "\033[38;5;129m"
NEON_PINK = "\033[38;5;198m"
NEON_LIME = "\033[38;5;82m"
RESET = "\033[0m"
BOLD = "\033[1m"
DIM = "\033[2m"

BOX_TL = "╭"
BOX_TR = "╮"
BOX_BL = "╰"
BOX_BR = "╯"
BOX_V = "│"
BOX_H = "─"
BOX_LT = "├"
BOX_RT = "┤"

_ANSI_RE = re.compile(r"\x1b(?:[@-Z\\-_]|\[[0-?]*[ -/]*[@-~])|\033\[[0-9;?]*[a-zA-Z]")

_ACTION_ICONS = {
    "repo-info": "📦",
    "repo-list": "📁",
    "repo-clone": "📥",
    "issue-list": "🐛",
    "issue-create": "➕",
    "pr-list": "🔀",
    "gist-list": "📜",
    "gist-create": "📝",
    "search": "🔍",
    "user-info": "👤",
    "workflow-list": "⚙️",
}


def _strip_ansi(text: str) -> str:
    """Remove ANSI escape sequences."""
    return _ANSI_RE.sub("", text)


def _is_tty() -> bool:
    """Return True if stderr is attached to an interactive terminal."""
    return sys.stderr.isatty() and os.environ.get("TERM", "").lower() not in (
        "dumb",
        "",
    )


def get_width() -> int:
    """Return current terminal column count based on stderr, constrained to reasonable bounds."""
    try:
        cols = os.get_terminal_size(sys.stderr.fileno()).columns
        return max(40, min(cols, 120))
    except (OSError, AttributeError):
        return 68


def _cprint(
    text: str, file: Any = None, no_color: bool = False, end: str = "\n"
) -> None:
    """Print pre-formatted ANSI text to stderr by default."""
    target = file or sys.stderr
    if no_color or not _is_tty():
        text = _strip_ansi(text)
    print(text, file=target, flush=True, end=end)


def print_human_readable_ui(data: dict[str, Any], no_color: bool = False) -> None:
    """Render colorized box UI for human terminal sessions to stderr."""
    if not _is_tty() or no_color:
        return

    success = data.get("success", False)
    status_color = NEON_GREEN if success else NEON_RED
    status_symbol = "✓" if success else "✗"
    status_text = "SUCCESS" if success else "FAILED"
    action = data.get("action", "user-info")
    icon = _ACTION_ICONS.get(action, "🐙")

    box_w = get_width() - 4
    border = BOX_H * box_w

    _cprint(f"{NEON_PURPLE}{BOX_TL}{border}{BOX_TR}{RESET}")
    _cprint(
        f"{NEON_PURPLE}{BOX_V}{RESET} {NEON_PINK}{icon} [GITHUB TERMUX TOOL v{__version__}]{RESET} {status_color}{BOLD}{status_symbol} {status_text}{RESET}"
    )
    _cprint(f"{NEON_PURPLE}{BOX_LT}{border}{BOX_RT}{RESET}")
    _cprint(f"{NEON_PURPLE}{BOX_V}{RESET} {NEON_CYAN}Action:{RESET}   {action}")
    if data.get("repo"):
        _cprint(
            f"{NEON_PURPLE}{BOX_V}{RESET} {NEON_CYAN}Repo:{RESET}     {data.get('repo')}"
        )
    if data.get("query"):
        _cprint(
            f"{NEON_PURPLE}{BOX_V}{RESET} {NEON_CYAN}Query:{RESET}    {data.get('query')}"
        )
    _cprint(
        f"{NEON_PURPLE}{BOX_V}{RESET} {NEON_CYAN}Count:{RESET}    {NEON_YELLOW}{data.get('count', 0)}{RESET}"
    )

    rl = data.get("rate_limit", {})
    if rl.get("remaining") is not None:
        _cprint(
            f"{NEON_PURPLE}{BOX_V}{RESET} {NEON_CYAN}API Limit:{RESET} {NEON_LIME}{rl.get('remaining')}/{rl.get('limit')}{RESET}"
        )

    _cprint(
        f"{NEON_PURPLE}{BOX_V}{RESET} {NEON_CYAN}Cached:{RESET}   {NEON_YELLOW}{data.get('cached', False)}{RESET}"
    )
    _cprint(
        f"{NEON_PURPLE}{BOX_V}{RESET} {NEON_CYAN}Duration:{RESET} {DIM}{data.get('duration_ms', 0)}ms{RESET}"
    )

    if not success and "error" in data:
        _cprint(f"{NEON_PURPLE}{BOX_LT}{border}{BOX_RT}{RESET}")
        _cprint(
            f"{NEON_PURPLE}{BOX_V}{RESET} {NEON_RED}Error:{RESET}    {data['error']}"
        )

    items = data.get("items", [])
    if items:
        _cprint(f"{NEON_PURPLE}{BOX_LT}{border}{BOX_RT}{RESET}")
        _cprint(
            f"{NEON_PURPLE}{BOX_V}{RESET} {BOLD}Processed Results ({len(items)}):{RESET}"
        )
        for idx, item in enumerate(items[:5], 1):
            if isinstance(item, dict):
                label = (
                    item.get("full_name")
                    or item.get("title")
                    or item.get("description")
                    or item.get("login")
                    or item.get("id")
                    or str(item)
                )
                url = item.get("html_url") or item.get("url", "")
                _cprint(
                    f"{NEON_PURPLE}{BOX_V}{RESET}   {NEON_CYAN}{idx:02d}.{RESET} {label}"
                )
                if url:
                    _cprint(f"{NEON_PURPLE}{BOX_V}{RESET}       {DIM}↳ {url}{RESET}")
            else:
                _cprint(f"{NEON_PURPLE}{BOX_V}{RESET}   {NEON_CYAN}›{RESET} {item}")
        if len(items) > 5:
            _cprint(
                f"{NEON_PURPLE}{BOX_V}{RESET}   {DIM}... and {len(items) - 5} more items{RESET}"
            )

    _cprint(f"{NEON_PURPLE}{BOX_BL}{border}{BOX_BR}{RESET}")


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
    """Extract complete execution context from standard and Termux environment."""
    termux_prefix = os.environ.get("PREFIX", "")
    return {
        "tool_name": os.environ.get("LLM_TOOL_NAME", "github_tool"),
        "cache_dir": os.environ.get("LLM_TOOL_CACHE_DIR"),
        "root_dir": os.environ.get("LLM_ROOT_DIR"),
        "output_path": os.environ.get("LLM_OUTPUT"),
        "cwd": get_builtin_var("__cwd__") or os.getcwd(),
        "termux_prefix": termux_prefix,
        "is_termux": "com.termux" in termux_prefix
        or Path("/data/data/com.termux").exists(),
    }


def sanitize_path() -> None:
    """Remove llm-functions/bin entries from PATH to prevent recursive shadowing."""
    raw = os.environ.get("PATH", "")
    parts = []
    for p in raw.split(os.pathsep):
        if not p:
            continue
        norm = os.path.normpath(p)
        if (
            norm.endswith(os.path.join("llm-functions", "bin"))
            or os.path.basename(norm) == "llm-functions-bin"
        ):
            continue
        parts.append(p)
    os.environ["PATH"] = os.pathsep.join(parts)


def _redact_token(text: str, token: str) -> str:
    """Redact GitHub Personal Access Tokens from command outputs or URLs."""
    if not text:
        return ""
    if token and len(token) > 4:
        text = text.replace(token, f"{token[:4]}****")
    text = re.sub(
        r"https://x-access-token:[^@]+@", "https://x-access-token:****@", text
    )
    return text


def _resolve_github_token(user_token: Optional[str] = None) -> str:
    """Resolve GitHub token from argument -> env vars -> gh CLI authentication."""
    if user_token and user_token.strip():
        return user_token.strip()

    for env_key in ("GITHUB_TOKEN", "GH_TOKEN", "LLM_AGENT_VAR_GITHUB_TOKEN"):
        val = os.environ.get(env_key, "").strip()
        if val:
            return val

    # Fallback to gh CLI if authenticated in Termux
    gh_bin = shutil.which("gh")
    if gh_bin:
        try:
            res = subprocess.run(
                [gh_bin, "auth", "token"], capture_output=True, text=True, timeout=5
            )
            if res.returncode == 0 and res.stdout.strip():
                return res.stdout.strip()
        except Exception:
            pass

    return ""


# ==============================================================================
# SECTION 4: Native Caching & Signal Handlers
# ==============================================================================


class ToolCache:
    """Caching utility with TTL support for GitHub API requests."""

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
            mtime = cache_file.stat().st_mtime
            if time.time() - mtime > ttl_seconds:
                cache_file.unlink(missing_ok=True)
                return None
            with open(cache_file, "rb") as fp:
                return pickle.load(fp)
        except Exception:
            return None

    def set(self, key_data: str, value: Any) -> None:
        cache_file = self.cache_dir / f"{self._make_key(key_data)}.cache"
        tmp_file = cache_file.with_suffix(f".tmp.{os.getpid()}_{time.time_ns()}")
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
# SECTION 5: Native GitHub API Client Engine
# ==============================================================================


def _parse_link_header(link_header: Optional[str]) -> dict[str, str]:
    """Parse standard GitHub Link headers for pagination URLs."""
    links = {}
    if not link_header:
        return links
    parts = link_header.split(",")
    for part in parts:
        section = part.split(";")
        if len(section) < 2:
            continue
        url = section[0].strip().strip("<>")
        rel_match = re.search(r'rel="([^"]+)"', section[1])
        if rel_match:
            links[rel_match.group(1)] = url
    return links


def _github_api_request(
    endpoint: str,
    method: str = "GET",
    data: Optional[dict[str, Any]] = None,
    token: str = "",
    timeout: int = 15,
) -> Tuple[bool, Any, int, dict[str, Any]]:
    """Execute native GitHub REST API request with header parsing and error diagnostics."""
    url = (
        endpoint if endpoint.startswith("http") else f"https://api.github.com{endpoint}"
    )
    headers = {
        "User-Agent": "Termux-Pyrmethus-GitHub-Tool/2.2.0",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
    }
    if token:
        headers["Authorization"] = f"Bearer {token}"

    encoded_data = None
    if data is not None:
        headers["Content-Type"] = "application/json"
        encoded_data = json.dumps(data).encode("utf-8")

    req = urllib.request.Request(
        url, data=encoded_data, headers=headers, method=method.upper()
    )

    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            status_code = resp.getcode()
            body = resp.read().decode("utf-8", errors="replace")
            parsed = json.loads(body) if body.strip() else {}

            meta = {
                "rate_limit": {
                    "limit": resp.headers.get("X-RateLimit-Limit"),
                    "remaining": resp.headers.get("X-RateLimit-Remaining"),
                    "reset": resp.headers.get("X-RateLimit-Reset"),
                },
                "links": _parse_link_header(resp.headers.get("Link")),
            }
            return True, parsed, status_code, meta

    except urllib.error.HTTPError as err:
        error_body = err.read().decode("utf-8", errors="replace")
        try:
            parsed_err = json.loads(error_body)
        except Exception:
            parsed_err = {"message": error_body or err.reason}

        meta = {
            "rate_limit": {
                "limit": err.headers.get("X-RateLimit-Limit") if err.headers else None,
                "remaining": err.headers.get("X-RateLimit-Remaining")
                if err.headers
                else None,
                "reset": err.headers.get("X-RateLimit-Reset") if err.headers else None,
            }
        }
        return False, parsed_err, err.code, meta

    except urllib.error.URLError as err:
        msg = str(err.reason if hasattr(err, "reason") else err)
        if "CERTIFICATE_VERIFY_FAILED" in msg or "ssl" in msg.lower():
            msg += " [Termux Fix: Run 'pkg install ca-certificates' to install root CA certificates]"
        return False, {"message": msg}, EXIT_ERROR, {}

    except Exception as exc:
        return False, {"message": str(exc)}, EXIT_ERROR, {}


# ==============================================================================
# SECTION 6: GitHub Action Handlers
# ==============================================================================


def _handle_user_info(
    query: Optional[str], token: str
) -> Tuple[bool, Any, dict[str, Any]]:
    endpoint = f"/users/{urllib.parse.quote(query)}" if query else "/user"
    ok, data, code, meta = _github_api_request(endpoint, token=token)
    if not ok:
        if code == 401:
            return (
                False,
                "Unauthorized: Provide a valid GitHub Personal Access Token (--token or GITHUB_TOKEN).",
                meta,
            )
        return False, data.get("message", "Failed to fetch user info."), meta
    return True, data, meta


def _handle_repo_info(repo: str, token: str) -> Tuple[bool, Any, dict[str, Any]]:
    if not repo or "/" not in repo:
        return False, "Action 'repo-info' requires --repo in 'OWNER/REPO' format.", {}
    ok, data, code, meta = _github_api_request(
        f"/repos/{urllib.parse.quote(repo, safe='/')}", token=token
    )
    if not ok:
        return (
            False,
            data.get("message", f"Failed to fetch repo info for '{repo}'."),
            meta,
        )
    return True, data, meta


def _handle_repo_list(
    query: Optional[str], limit: int, token: str
) -> Tuple[bool, Any, dict[str, Any]]:
    if query:
        endpoint = (
            f"/users/{urllib.parse.quote(query)}/repos?per_page={limit}&sort=updated"
        )
    else:
        endpoint = (
            f"/user/repos?per_page={limit}&sort=updated"
            if token
            else f"/repositories?per_page={limit}"
        )

    ok, data, code, meta = _github_api_request(endpoint, token=token)
    if not ok:
        return False, data.get("message", "Failed to list repositories."), meta
    return True, data, meta


def _handle_repo_clone(
    repo: str, target: Optional[str], token: str
) -> Tuple[bool, Any, dict[str, Any]]:
    if not repo:
        return False, "Action 'repo-clone' requires --repo parameter.", {}

    sanitize_path()
    git_bin = shutil.which("git")
    if not git_bin:
        return (
            False,
            "Git binary not found in Termux environment. Install with: pkg install git",
            {},
        )

    clone_url = f"https://github.com/{repo}.git" if "/" in repo else repo
    if token and "github.com" in clone_url and "@" not in clone_url:
        clone_url = clone_url.replace("https://", f"https://x-access-token:{token}@")

    base_cwd = get_builtin_var("__cwd__") or os.getcwd()
    dest = str(
        (
            Path(base_cwd)
            / (target or repo.rsplit("/", maxsplit=1)[-1].replace(".git", ""))
        )
        .expanduser()
        .resolve()
    )
    cmd = [git_bin, "clone", clone_url, dest]

    preexec = os.setsid if hasattr(os, "setsid") else None

    try:
        proc = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            preexec_fn=preexec,
            text=True,
        )
        try:
            stdout, stderr = proc.communicate(timeout=120)
            if proc.returncode == 0:
                return (
                    True,
                    {"cloned_to": dest, "output": _redact_token(stdout.strip(), token)},
                    {},
                )
            return False, _redact_token(stderr.strip(), token), {}
        except subprocess.TimeoutExpired:
            if hasattr(os, "killpg") and preexec is not None:
                try:
                    os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
                except Exception:
                    pass
            else:
                proc.kill()
            return False, "Git clone operation timed out after 120 seconds.", {}

    except Exception as exc:
        return False, f"Clone process error: {exc}", {}


def _handle_issue_list(
    repo: str, state: str, limit: int, token: str
) -> Tuple[bool, Any, dict[str, Any]]:
    if not repo or "/" not in repo:
        return False, "Action 'issue-list' requires --repo in 'OWNER/REPO' format.", {}
    endpoint = f"/repos/{urllib.parse.quote(repo, safe='/')}/issues?state={urllib.parse.quote(state)}&per_page={limit}"
    ok, data, code, meta = _github_api_request(endpoint, token=token)
    if not ok:
        return False, data.get("message", "Failed to list issues."), meta
    return True, data, meta


def _handle_issue_create(
    repo: str, title: str, body: str, token: str
) -> Tuple[bool, Any, dict[str, Any]]:
    if not repo or "/" not in repo:
        return (
            False,
            "Action 'issue-create' requires --repo in 'OWNER/REPO' format.",
            {},
        )
    if not title:
        return (
            False,
            "Action 'issue-create' requires --query to specify the issue title.",
            {},
        )
    if not token:
        return (
            False,
            "Creating issues requires GitHub authentication (--token or GITHUB_TOKEN).",
            {},
        )

    payload = {"title": title, "body": body or ""}
    ok, data, code, meta = _github_api_request(
        f"/repos/{urllib.parse.quote(repo, safe='/')}/issues",
        method="POST",
        data=payload,
        token=token,
    )
    if not ok:
        return False, data.get("message", "Failed to create issue."), meta
    return True, data, meta


def _handle_pr_list(
    repo: str, state: str, limit: int, token: str
) -> Tuple[bool, Any, dict[str, Any]]:
    if not repo or "/" not in repo:
        return False, "Action 'pr-list' requires --repo in 'OWNER/REPO' format.", {}
    endpoint = f"/repos/{urllib.parse.quote(repo, safe='/')}/pulls?state={urllib.parse.quote(state)}&per_page={limit}"
    ok, data, code, meta = _github_api_request(endpoint, token=token)
    if not ok:
        return False, data.get("message", "Failed to list pull requests."), meta
    return True, data, meta


def _handle_gist_list(
    query: Optional[str], limit: int, token: str
) -> Tuple[bool, Any, dict[str, Any]]:
    endpoint = (
        f"/users/{urllib.parse.quote(query)}/gists?per_page={limit}"
        if query
        else f"/gists?per_page={limit}"
    )
    ok, data, code, meta = _github_api_request(endpoint, token=token)
    if not ok:
        return False, data.get("message", "Failed to list gists."), meta
    return True, data, meta


def _handle_gist_create(
    query: Optional[str], body: str, token: str
) -> Tuple[bool, Any, dict[str, Any]]:
    if not body:
        return False, "Action 'gist-create' requires --body content.", {}
    if not token:
        return (
            False,
            "Creating gists requires GitHub authentication (--token or GITHUB_TOKEN).",
            {},
        )

    filename = query or "snippet.txt"
    payload = {
        "description": f"Created via Pyrmethus Termux Tool ({filename})",
        "public": True,
        "files": {filename: {"content": body}},
    }
    ok, data, code, meta = _github_api_request(
        "/gists", method="POST", data=payload, token=token
    )
    if not ok:
        return False, data.get("message", "Failed to create gist."), meta
    return True, data, meta


def _handle_search(
    query: str, limit: int, token: str
) -> Tuple[bool, Any, dict[str, Any]]:
    if not query:
        return False, "Action 'search' requires a query string (--query).", {}
    encoded_q = urllib.parse.quote(query)
    endpoint = f"/search/repositories?q={encoded_q}&per_page={limit}"
    ok, data, code, meta = _github_api_request(endpoint, token=token)
    if not ok:
        return False, data.get("message", "Search operation failed."), meta
    return True, data.get("items", []), meta


def _handle_workflow_list(
    repo: str, limit: int, token: str
) -> Tuple[bool, Any, dict[str, Any]]:
    if not repo or "/" not in repo:
        return (
            False,
            "Action 'workflow-list' requires --repo in 'OWNER/REPO' format.",
            {},
        )
    endpoint = f"/repos/{urllib.parse.quote(repo, safe='/')}/actions/workflows?per_page={limit}"
    ok, data, code, meta = _github_api_request(endpoint, token=token)
    if not ok:
        return False, data.get("message", "Failed to list workflows."), meta
    return True, data.get("workflows", []), meta


# ==============================================================================
# SECTION 7: Primary Master Tool Execution Logic
# ==============================================================================


def execute_tool(
    action: str = "user-info",
    repo: Optional[str] = None,
    query: Optional[str] = None,
    body: Optional[str] = None,
    state: str = "open",
    limit: Optional[int] = 30,
    token: Optional[str] = None,
    target: Optional[str] = None,
    mode: str = "summary",
    use_cache: bool = False,
    no_color: bool = False,
    verbose: bool = False,
) -> dict[str, Any]:
    """
    Core execution logic shared by CLI and run() entry points.
    """
    start_time = time.monotonic()

    if verbose:
        logging.basicConfig(level=logging.DEBUG, format="[DEBUG] %(message)s")
        logging.debug(f"Starting GitHub execution: action='{action}', repo='{repo}'")

    resolved_token = _resolve_github_token(token)
    limit_val = limit if (limit is not None and limit > 0) else 30
    action_key = action.lower().strip()

    cache = ToolCache()
    cache_key = (
        f"github:{action_key}:{repo}:{query}:{state}:{limit_val}:{resolved_token[:10]}"
    )
    if use_cache and action_key not in ("issue-create", "gist-create", "repo-clone"):
        cached_result = cache.get(cache_key)
        if cached_result is not None:
            if verbose:
                logging.debug("Cache hit! Returning cached GitHub API payload.")
            cached_result["cached"] = True
            return cached_result

    shutdown = GracefulShutdown()

    try:
        ok = False
        payload: Any = None
        meta: dict[str, Any] = {}

        if action_key == "user-info":
            ok, payload, meta = _handle_user_info(query, resolved_token)
        elif action_key == "repo-info":
            ok, payload, meta = _handle_repo_info(repo or "", resolved_token)
        elif action_key == "repo-list":
            ok, payload, meta = _handle_repo_list(query, limit_val, resolved_token)
        elif action_key == "repo-clone":
            ok, payload, meta = _handle_repo_clone(repo or "", target, resolved_token)
        elif action_key == "issue-list":
            ok, payload, meta = _handle_issue_list(
                repo or "", state, limit_val, resolved_token
            )
        elif action_key == "issue-create":
            ok, payload, meta = _handle_issue_create(
                repo or "", query or "", body or "", resolved_token
            )
        elif action_key == "pr-list":
            ok, payload, meta = _handle_pr_list(
                repo or "", state, limit_val, resolved_token
            )
        elif action_key == "gist-list":
            ok, payload, meta = _handle_gist_list(query, limit_val, resolved_token)
        elif action_key == "gist-create":
            ok, payload, meta = _handle_gist_create(query, body or "", resolved_token)
        elif action_key == "search":
            ok, payload, meta = _handle_search(query or "", limit_val, resolved_token)
        elif action_key == "workflow-list":
            ok, payload, meta = _handle_workflow_list(
                repo or "", limit_val, resolved_token
            )
        else:
            return {
                "success": False,
                "error": f"Unknown action '{action}'. Choose from: repo-info/repo-list/repo-clone/issue-list/issue-create/pr-list/gist-list/gist-create/search/user-info/workflow-list",
                "exit_code": EXIT_INVALID_INPUT,
                "duration_ms": 0.0,
            }

        duration_ms = round((time.monotonic() - start_time) * 1000, 2)

        if not ok:
            return {
                "success": False,
                "action": action_key,
                "error": str(payload),
                "exit_code": EXIT_ERROR,
                "duration_ms": duration_ms,
            }

        items = payload if isinstance(payload, list) else [payload]
        raw_output_json = json.dumps(items, cls=ToolJSONEncoder)

        result: dict[str, Any] = {
            "success": True,
            "action": action_key,
            "repo": repo,
            "query": query,
            "count": len(items),
            "lines_count": len(raw_output_json.splitlines()),
            "bytes_count": len(raw_output_json.encode("utf-8")),
            "items": items if mode == "detailed" else items[:10],
            "rate_limit": meta.get("rate_limit", {}),
            "pagination": meta.get("links", {}),
            "authenticated": bool(resolved_token),
            "context": get_execution_context(),
            "cached": False,
            "duration_ms": duration_ms,
            "exit_code": EXIT_SUCCESS,
        }

        if shutdown.should_stop():
            result["success"] = False
            result["error"] = "Operation interrupted by user signal."
            result["exit_code"] = EXIT_INTERRUPTED

        if use_cache and result["success"]:
            cache.set(cache_key, result)

        return result

    except Exception as exc:
        duration_ms = round((time.monotonic() - start_time) * 1000, 2)
        return {
            "success": False,
            "error": f"Tool execution failure: {exc}",
            "exit_code": EXIT_ERROR,
            "duration_ms": duration_ms,
        }
    finally:
        shutdown.restore()


# ==============================================================================
# SECTION 8: Output Routing (LLM vs Human Terminal)
# ==============================================================================


def write_llm_output(data: dict[str, Any]) -> None:
    """Format and write JSON output to LLM_OUTPUT destination safely."""
    out_path = os.environ.get("LLM_OUTPUT", "/dev/stdout")
    json_payload = (
        json.dumps(data, indent=2, ensure_ascii=False, cls=ToolJSONEncoder) + "\n"
    )

    direct_targets = {"/dev/stdout", "/dev/fd/1", "-"}
    if out_path in direct_targets:
        sys.stdout.write(json_payload)
        sys.stdout.flush()
    else:
        try:
            p = Path(out_path).expanduser().resolve()
            p.parent.mkdir(parents=True, exist_ok=True)
            with open(p, "a", encoding="utf-8") as fp:
                fp.write(json_payload)
        except OSError as err:
            sys.stderr.write(f"Failed writing to LLM_OUTPUT '{out_path}': {err}\n")
            sys.stdout.write(json_payload)
            sys.stdout.flush()


# ==============================================================================
# SECTION 9: Function Entry Point for AIChat
# ==============================================================================


def run(
    action: Literal[
        "repo-info",
        "repo-list",
        "repo-clone",
        "issue-list",
        "issue-create",
        "pr-list",
        "gist-list",
        "gist-create",
        "search",
        "user-info",
        "workflow-list",
    ] = "user-info",
    repo: Optional[str] = None,
    query: Optional[str] = None,
    body: Optional[str] = None,
    state: Literal["open", "closed", "all"] = "open",
    limit: Optional[int] = 30,
    token: Optional[str] = None,
    target: Optional[str] = None,
    mode: Literal["summary", "detailed"] = "summary",
    use_cache: bool = False,
    no_color: bool = False,
    verbose: bool = False,
) -> None:
    """Execute GitHub API operation.

    Args:
        action: Target operation name
        repo: Repository identifier in 'OWNER/REPO' format
        query: Query string or issue/gist title
        body: Body text for creating issues or gists
        state: State filter: open, closed, or all
        limit: Maximum number of records to return
        token: GitHub Personal Access Token
        target: Target destination directory for clone
        mode: Result detail mode (summary/detailed)
        use_cache: Enable API caching
        no_color: Disable ANSI color output
        verbose: Enable debug log output
    """
    result = execute_tool(
        action=action,
        repo=repo,
        query=query,
        body=body,
        state=state,
        limit=limit,
        token=token,
        target=target,
        mode=mode,
        use_cache=use_cache,
        no_color=no_color,
        verbose=verbose,
    )

    print_human_readable_ui(result, no_color=no_color)
    write_llm_output(result)


# ==============================================================================
# SECTION 10: CLI Argument Parser
# ==============================================================================


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="github_tool.py",
        description=f"Pyrmethus Termux GitHub Tool v{__version__}",
    )
    parser.add_argument(
        "--action",
        "-a",
        default="user-info",
        choices=[
            "repo-info",
            "repo-list",
            "repo-clone",
            "issue-list",
            "issue-create",
            "pr-list",
            "gist-list",
            "gist-create",
            "search",
            "user-info",
            "workflow-list",
        ],
        help="GitHub operation action (default: user-info)",
    )
    parser.add_argument(
        "--repo",
        "-r",
        metavar="OWNER/REPO",
        help="Target repository (e.g. octocat/Hello-World)",
    )
    parser.add_argument(
        "--query",
        "-q",
        metavar="TEXT",
        help="Search query or title for issue/gist",
    )
    parser.add_argument(
        "--body",
        "-b",
        metavar="TEXT",
        help="Body text for issue/PR/gist creation",
    )
    parser.add_argument(
        "--state",
        choices=["open", "closed", "all"],
        default="open",
        help="Filter state for issues/PRs (default: open)",
    )
    parser.add_argument(
        "--limit",
        "-l",
        type=int,
        default=30,
        help="Maximum results to return (default: 30)",
    )
    parser.add_argument(
        "--token",
        "-t",
        metavar="TOKEN",
        help="GitHub Personal Access Token",
    )
    parser.add_argument(
        "--target",
        metavar="PATH",
        help="Local target directory path for repo clone",
    )
    parser.add_argument(
        "--mode",
        choices=["summary", "detailed"],
        default="summary",
        help="Output mode detail level (default: summary)",
    )
    parser.add_argument(
        "--use-cache",
        action="store_true",
        default=False,
        dest="use_cache",
        help="Enable result caching for API requests",
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
    args = _build_parser().parse_args()
    res = execute_tool(
        action=args.action,
        repo=args.repo,
        query=args.query,
        body=args.body,
        state=args.state,
        limit=args.limit,
        token=args.token,
        target=args.target,
        mode=args.mode,
        use_cache=args.use_cache,
        no_color=args.no_color,
        verbose=args.verbose,
    )

    print_human_readable_ui(res, no_color=args.no_color)
    write_llm_output(res)
    sys.exit(res.get("exit_code", EXIT_SUCCESS))
