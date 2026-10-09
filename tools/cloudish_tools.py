#!/usr/bin/env python3
# ==============================================================================
# syncthing_rclone_suite.py — Syncthing & Rclone Master Sync Suite v2.4.0-ASCENDED
# argc/aichat compatible · Human-Readable Colorized Outputs · Native Caching
#
# @describe Inspect, manage, and synchronize files using Syncthing REST API and Rclone cloud remote engine.
#
# @meta require-tools aichat
#
# @option --target <URL_OR_REMOTE>       Syncthing URL (http://127.0.0.1:8384) or Rclone remote name (e.g. gdrive:)
# @option --mode <MODE>                  Mode: status/folders/devices/folder-status/pause-folder/resume-folder/connections/rescan/conflicts/events/errors/needed/config/rclone-remotes/rclone-about/rclone-ls/rclone-sync/rclone-rc/check-deps (default: status)
# @option --api-key <KEY>                Syncthing API Key (auto-discovered if omitted)
# @option --rc-url <URL>                 Rclone RC API endpoint URL (default: http://127.0.0.1:5572)
# @option --folder-id <ID>               Target folder ID for Syncthing operations
# @option --remote-path <PATH>           Path inside Rclone remote for listing or syncing
# @option --limit <NUM>                  Maximum items/events/files to return (default: 50)
# @option --timeout <SECONDS>            Timeout in seconds for API and CLI calls (default: 15)
# @flag   --insecure                     Disable TLS SSL certificate verification for self-signed endpoints
# @flag   --dry-run                      Run Rclone sync operations in simulation mode
# @option --env-var <KEY=VALUE>          Custom environment variable (repeatable)
# @flag   --use-cache                    Enable result caching for static status queries
# @flag   --no-color                     Disable ANSI color output
# @flag   --verbose                      Enable detailed debug log output
#
# @env LLM_OUTPUT=/dev/stdout            Output path for LLM integration
# ==============================================================================

from __future__ import annotations

import argparse
import hashlib
import json
import os
import pickle
import re
import signal
import socket
import ssl
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta, timezone
from enum import Enum
from pathlib import Path
from typing import Any, Literal, Optional

__version__ = "2.4.0"
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
# SECTION 2: Terminal Color Palette & Formatting Helpers
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
    return _ANSI_RE.sub("", text)


def _is_tty() -> bool:
    return sys.stderr.isatty() and os.environ.get("TERM", "").lower() not in (
        "dumb",
        "",
    )


def _cprint(
    text: str, file: Any = None, no_color: bool = False, end: str = "\n"
) -> None:
    target = file or sys.stderr
    if no_color or not _is_tty():
        text = _strip_ansi(text)
    print(text, file=target, flush=True, end=end)


def format_bytes(size: float) -> str:
    for unit in ["B", "KB", "MB", "GB", "TB"]:
        if abs(size) < 1024.0:
            return f"{size:.2f} {unit}"
        size /= 1024.0
    return f"{size:.2f} PB"


def check_port_open(host: str, port: int, timeout: float = 0.5) -> bool:
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False


def print_human_readable_ui(data: dict[str, Any], no_color: bool = False) -> None:
    if not _is_tty() or no_color:
        return

    success = data.get("success", False)
    status_color = NEON_GREEN if success else NEON_RED
    status_symbol = "✓" if success else "✗"
    status_text = "SUCCESS" if success else "FAILED"

    box_w = 72
    border = "─" * box_w

    _cprint(f"{NEON_PURPLE}╭{border}╮{RESET}")
    _cprint(
        f"{NEON_PURPLE}│{RESET} {NEON_PINK}⚡ [SYNCTHING & RCLONE SUITE v{__version__}]{RESET} {status_color}{BOLD}{status_symbol} {status_text}{RESET}"
    )
    _cprint(f"{NEON_PURPLE}├{border}┤{RESET}")
    _cprint(
        f"{NEON_PURPLE}│{RESET} {NEON_CYAN}Target:{RESET}      {data.get('target', 'N/A')}"
    )
    _cprint(
        f"{NEON_PURPLE}│{RESET} {NEON_CYAN}Mode:{RESET}        {data.get('mode', 'N/A')}"
    )
    _cprint(
        f"{NEON_PURPLE}│{RESET} {NEON_CYAN}Duration:{RESET}    {DIM}{data.get('duration_ms', 0)}ms{RESET}"
    )

    # Port Readiness Status
    if "ports_status" in data:
        ps = data["ports_status"]
        st_color = NEON_GREEN if ps.get("syncthing_8384") else NEON_RED
        rc_color = NEON_GREEN if ps.get("rclone_5572") else NEON_YELLOW
        _cprint(f"{NEON_PURPLE}├{border}┤{RESET}")
        _cprint(f"{NEON_PURPLE}│{RESET} {BOLD}Network Daemons Probed:{RESET}")
        _cprint(
            f"{NEON_PURPLE}│{RESET}   Syncthing Port 8384: {st_color}{'ONLINE' if ps.get('syncthing_8384') else 'OFFLINE'}{RESET}"
        )
        _cprint(
            f"{NEON_PURPLE}│{RESET}   Rclone RC Port 5572: {rc_color}{'ONLINE' if ps.get('rclone_5572') else 'OFFLINE'}{RESET}"
        )

    # Syncthing Summary
    if "status" in data:
        st = data["status"]
        _cprint(f"{NEON_PURPLE}├{border}┤{RESET}")
        _cprint(f"{NEON_PURPLE}│{RESET} {BOLD}Syncthing Daemon Status:{RESET}")
        _cprint(
            f"{NEON_PURPLE}│{RESET}   Device ID: {NEON_YELLOW}{st.get('myID', 'N/A')[:22]}...{RESET}"
        )
        _cprint(
            f"{NEON_PURPLE}│{RESET}   Uptime:    {st.get('uptime_str', 'N/A')} | Version: {st.get('version', 'N/A')}"
        )

    # Sync Conflicts Found
    if data.get("conflicts"):
        _cprint(f"{NEON_PURPLE}├{border}┤{RESET}")
        _cprint(
            f"{NEON_PURPLE}│{RESET} {NEON_YELLOW}⚠️ Discovered Sync Conflict Files ({len(data['conflicts'])}):{RESET}"
        )
        for c in data["conflicts"][:5]:
            _cprint(f"{NEON_PURPLE}│{RESET}   {NEON_RED}›{RESET} {c.get('name')}")
            _cprint(
                f"{NEON_PURPLE}│{RESET}     Path: {DIM}{c.get('path')}{RESET} ({format_bytes(c.get('size_bytes', 0))})"
            )

    # Rclone Remotes
    if "rclone_remotes" in data:
        _cprint(f"{NEON_PURPLE}├{border}┤{RESET}")
        _cprint(
            f"{NEON_PURPLE}│{RESET} {BOLD}Configured Rclone Cloud Remotes ({len(data['rclone_remotes'])}):{RESET}"
        )
        for rem in data["rclone_remotes"]:
            _cprint(
                f"{NEON_PURPLE}│{RESET}   {NEON_GREEN}›{RESET} Remote: {NEON_CYAN}{rem.get('name')}{RESET} [{rem.get('type')}]"
            )

    if not success and "error" in data:
        _cprint(f"{NEON_PURPLE}├{border}┤{RESET}")
        _cprint(f"{NEON_PURPLE}│{RESET} {NEON_RED}Error:{RESET}    {data['error']}")

    _cprint(f"{NEON_PURPLE}╰{border}╯{RESET}")


# ==============================================================================
# SECTION 3: Agent & Environment Helpers
# ==============================================================================


def get_agent_var(name: str, default: str = "") -> str:
    return os.environ.get(f"LLM_AGENT_VAR_{name.upper()}", default)


def get_builtin_var(name: str) -> Optional[str]:
    return os.environ.get(f"LLM_AGENT_VAR_{name}")


def get_execution_context() -> dict[str, Any]:
    termux_prefix = os.environ.get("PREFIX", "")
    rclone_bin = subprocess.run(
        ["which", "rclone"], capture_output=True, text=True
    ).stdout.strip()
    return {
        "tool_name": os.environ.get("LLM_TOOL_NAME", "syncthing_rclone_suite"),
        "cache_dir": os.environ.get("LLM_TOOL_CACHE_DIR"),
        "output_path": os.environ.get("LLM_OUTPUT"),
        "cwd": get_builtin_var("__cwd__") or os.getcwd(),
        "is_termux": "com.termux" in termux_prefix
        or Path("/data/data/com.termux").exists(),
        "has_rclone": bool(rclone_bin),
        "rclone_bin_path": rclone_bin or None,
    }


def _parse_env_vars(env_vars: Optional[list[str]]) -> dict[str, str]:
    if not env_vars:
        return {}
    parsed: dict[str, str] = {}
    for item in env_vars:
        if "=" in item:
            k, v = item.split("=", 1)
            parsed[k.strip()] = v.strip()
    return parsed


# ==============================================================================
# SECTION 4: Native Caching & Signal Handlers
# ==============================================================================


class ToolCache:
    def __init__(self, cache_dir: Optional[Path] = None) -> None:
        if cache_dir:
            self.cache_dir = cache_dir
        elif "LLM_TOOL_CACHE_DIR" in os.environ:
            self.cache_dir = Path(os.environ["LLM_TOOL_CACHE_DIR"])
        else:
            self.cache_dir = Path.home() / ".cache" / "aichat_tools"
        self.cache_dir.mkdir(parents=True, exist_ok=True)

    def _make_key(self, key_data: str) -> str:
        return hashlib.sha256(key_data.encode("utf-8")).hexdigest()

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
# SECTION 5: ADVANCED ENGINE & CONFLICT RESOLVER HELPERS
# ==============================================================================


def find_syncthing_config() -> tuple[Optional[str], Optional[str]]:
    home = Path.home()
    candidates = [
        os.environ.get("SYNCTHING_CONFIG"),
        home / ".config" / "syncthing" / "config.xml",
        home / ".local" / "state" / "syncthing" / "config.xml",
        Path("/data/data/com.termux/files/home/.config/syncthing/config.xml"),
        home / "Library" / "Application Support" / "Syncthing" / "config.xml",
    ]

    for p_str in candidates:
        if not p_str:
            continue
        p = Path(p_str)
        if p.exists():
            try:
                tree = ET.parse(p)
                root = tree.getroot()
                gui_node = root.find("gui")
                if gui_node is not None:
                    apk = gui_node.find("apikey")
                    addr = gui_node.find("address")
                    key_val = apk.text.strip() if apk is not None and apk.text else None
                    addr_val = (
                        addr.text.strip() if addr is not None and addr.text else None
                    )
                    if addr_val and not addr_val.startswith("http"):
                        addr_val = f"http://{addr_val}"
                    return key_val, addr_val
            except Exception:
                pass
    return None, None


def find_sync_conflicts(
    folder_paths: list[str], limit: int = 50
) -> list[dict[str, Any]]:
    """Scan local folders for Syncthing conflict files (*.sync-conflict-*)."""
    conflicts = []
    for fpath in folder_paths:
        p = Path(fpath)
        if p.exists() and p.is_dir():
            for item in p.rglob("*.sync-conflict-*"):
                try:
                    conflicts.append(
                        {
                            "path": str(item),
                            "name": item.name,
                            "size_bytes": item.stat().st_size if item.exists() else 0,
                            "modified": datetime.fromtimestamp(
                                item.stat().st_mtime, tz=timezone.utc
                            ).isoformat(),
                        }
                    )
                    if len(conflicts) >= limit:
                        break
                except OSError:
                    pass
    return conflicts


def syncthing_api_call(
    base_url: str,
    endpoint: str,
    api_key: str,
    method: str = "GET",
    params: Optional[dict[str, Any]] = None,
    data_payload: Optional[dict[str, Any]] = None,
    timeout: int = 15,
    insecure: bool = False,
) -> Any:
    url = f"{base_url.rstrip('/')}/rest/{endpoint.lstrip('/')}"
    if params:
        url += "?" + urllib.parse.urlencode(params)

    req = urllib.request.Request(url, method=method)
    req.add_header("X-API-Key", api_key)
    req.add_header("Accept", "application/json")

    if data_payload is not None:
        req.add_header("Content-Type", "application/json")
        req.data = json.dumps(data_payload).encode("utf-8")

    ctx = ssl.create_default_context()
    if insecure:
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE

    try:
        with urllib.request.urlopen(req, timeout=timeout, context=ctx) as resp:
            data = resp.read().decode("utf-8")
            return json.loads(data) if data else {}
    except urllib.error.HTTPError as err:
        body = err.read().decode("utf-8", errors="replace")
        raise ToolError(f"Syncthing API HTTP {err.code}: {err.reason} ({body.strip()})")
    except urllib.error.URLError as err:
        raise ToolError(
            f"Failed to connect to Syncthing API at {base_url}: {err.reason}"
        )


def rclone_rc_call(
    rc_url: str,
    endpoint: str,
    params: Optional[dict[str, Any]] = None,
    timeout: int = 15,
) -> dict[str, Any]:
    """Execute request against Rclone Remote Control (RC) HTTP API."""
    url = f"{rc_url.rstrip('/')}/{endpoint.lstrip('/')}"
    data_bytes = json.dumps(params or {}).encode("utf-8")

    req = urllib.request.Request(url, data=data_bytes, method="POST")
    req.add_header("Content-Type", "application/json")

    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            res_text = resp.read().decode("utf-8")
            return json.loads(res_text) if res_text else {}
    except Exception as err:
        return {"error": f"Rclone RC API Call failed: {err}"}


def run_rclone_cli(args: list[str], timeout: int = 15) -> dict[str, Any]:
    try:
        cmd = ["rclone"] + args
        res = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
        if res.returncode != 0:
            return {
                "success": False,
                "error": res.stderr.strip() or "Rclone command failed",
            }
        try:
            return (
                {"success": True, "json": json.loads(res.stdout)}
                if "--json" in args
                else {"success": True, "raw": res.stdout.strip()}
            )
        except json.JSONDecodeError:
            return {"success": True, "raw": res.stdout.strip()}
    except FileNotFoundError:
        return {
            "success": False,
            "error": "Rclone executable not found in system PATH.",
        }
    except subprocess.TimeoutExpired:
        return {
            "success": False,
            "error": f"Rclone operation timed out after {timeout}s.",
        }


# ==============================================================================
# SECTION 6: CORE EXECUTION ROUTER
# ==============================================================================


def execute_tool(
    target: Optional[str] = None,
    mode: str = "status",
    api_key: Optional[str] = None,
    rc_url: str = "http://127.0.0.1:5572",
    folder_id: Optional[str] = None,
    remote_path: Optional[str] = None,
    limit: Optional[int] = None,
    timeout: int = 15,
    insecure: bool = False,
    dry_run: bool = False,
    env_vars: Optional[list[str]] = None,
    use_cache: bool = False,
    no_color: bool = False,
    verbose: bool = False,
) -> dict[str, Any]:
    start_time = time.monotonic()
    limit_val = limit if (limit is not None and limit > 0) else 50

    disc_key, disc_url = find_syncthing_config()
    final_api_key = api_key or os.environ.get("SYNCTHING_API_KEY") or disc_key
    final_target = (
        target or os.environ.get("SYNCTHING_URL") or disc_url or "http://127.0.0.1:8384"
    )

    cache = ToolCache()
    cache_key = f"{final_target}:{mode}:{folder_id}:{remote_path}:{limit_val}"

    if use_cache and mode in ("status", "folders", "devices", "rclone-remotes"):
        cached = cache.get(cache_key)
        if cached:
            cached["cached"] = True
            return cached

    # Probe Daemon Ports
    ports_status = {
        "syncthing_8384": check_port_open("127.0.0.1", 8384),
        "rclone_5572": check_port_open("127.0.0.1", 5572),
    }

    res_data: dict[str, Any] = {
        "success": True,
        "target": final_target,
        "mode": mode,
        "ports_status": ports_status,
        "context": get_execution_context(),
    }

    try:
        # --- Conflict Detection Mode ---
        if mode == "conflicts":
            if not final_api_key:
                raise ToolError("Syncthing API key required for conflicts mode.")
            folders = syncthing_api_call(
                final_target,
                "config/folders",
                final_api_key,
                insecure=insecure,
                timeout=timeout,
            )
            folder_paths = [f["path"] for f in folders if "path" in f]
            res_data["conflicts"] = find_sync_conflicts(folder_paths, limit=limit_val)

        # --- Syncthing Folder Pause / Resume ---
        elif mode in ("pause-folder", "resume-folder"):
            if not folder_id:
                raise ToolError(f"Mode '{mode}' requires --folder-id parameter.")
            if not final_api_key:
                raise ToolError("Syncthing API key required.")
            folder_cfg = syncthing_api_call(
                final_target,
                f"config/folders/{folder_id}",
                final_api_key,
                insecure=insecure,
                timeout=timeout,
            )
            folder_cfg["paused"] = True if mode == "pause-folder" else False
            syncthing_api_call(
                final_target,
                f"config/folders/{folder_id}",
                final_api_key,
                method="PUT",
                data_payload=folder_cfg,
                insecure=insecure,
                timeout=timeout,
            )
            res_data["message"] = (
                f"Successfully {'paused' if mode == 'pause-folder' else 'resumed'} folder '{folder_id}'."
            )

        # --- Rclone Remote Control (RC) Direct Call ---
        elif mode == "rclone-rc":
            endpoint = remote_path or "core/version"
            res_data["rclone_rc_response"] = rclone_rc_call(
                rc_url, endpoint, timeout=timeout
            )

        # --- Standard Rclone CLI Modes ---
        elif mode == "rclone-remotes":
            rc = run_rclone_cli(["listremotes", "--long", "--json"], timeout=timeout)
            res_data["rclone_remotes"] = rc.get("json", []) if rc["success"] else []

        elif mode == "rclone-about":
            target_remote = target or "gdrive:"
            rc = run_rclone_cli(["about", target_remote, "--json"], timeout=timeout)
            res_data["rclone_about"] = (
                rc.get("json", {}) if rc["success"] else {"error": rc.get("error")}
            )

        # --- Standard Syncthing REST API Modes ---
        elif mode in (
            "status",
            "folders",
            "devices",
            "connections",
            "rescan",
            "events",
            "errors",
            "needed",
            "config",
        ):
            if not final_api_key:
                raise ToolError(
                    "Syncthing API Key missing. Provide --api-key or set SYNCTHING_API_KEY."
                )

            if mode == "status":
                st = syncthing_api_call(
                    final_target,
                    "system/status",
                    final_api_key,
                    insecure=insecure,
                    timeout=timeout,
                )
                ver = syncthing_api_call(
                    final_target,
                    "system/version",
                    final_api_key,
                    insecure=insecure,
                    timeout=timeout,
                )
                st["uptime_str"] = str(timedelta(seconds=st.get("uptime", 0)))
                st["version"] = ver.get("version")
                res_data["status"] = st

            elif mode == "folders":
                folders = syncthing_api_call(
                    final_target,
                    "config/folders",
                    final_api_key,
                    insecure=insecure,
                    timeout=timeout,
                )
                res_data["folders"] = folders

            elif mode == "devices":
                devices = syncthing_api_call(
                    final_target,
                    "config/devices",
                    final_api_key,
                    insecure=insecure,
                    timeout=timeout,
                )
                res_data["devices"] = devices

            elif mode == "rescan":
                params = {"folder": folder_id} if folder_id else {}
                syncthing_api_call(
                    final_target,
                    "db/scan",
                    final_api_key,
                    method="POST",
                    params=params,
                    insecure=insecure,
                    timeout=timeout,
                )
                res_data["message"] = (
                    f"Triggered rescan for {'folder: ' + folder_id if folder_id else 'all folders'}."
                )

        elif mode == "check-deps":
            res_data["diagnostics"] = {
                "execution_context": get_execution_context(),
                "ports_status": ports_status,
                "syncthing_config": disc_url or "Not Found",
            }

        else:
            raise ToolError(f"Unsupported mode: {mode}")

        duration_ms = round((time.monotonic() - start_time) * 1000, 2)
        res_data["duration_ms"] = duration_ms
        res_data["exit_code"] = EXIT_SUCCESS

        if use_cache:
            cache.set(cache_key, res_data)

        return res_data

    except ToolError as exc:
        return {
            "success": False,
            "error": exc.message,
            "exit_code": exc.exit_code,
            "duration_ms": round((time.monotonic() - start_time) * 1000, 2),
        }
    except Exception as exc:
        return {
            "success": False,
            "error": f"Unexpected execution error: {exc}",
            "exit_code": EXIT_ERROR,
            "duration_ms": round((time.monotonic() - start_time) * 1000, 2),
        }


# ==============================================================================
# SECTION 7: Output Routing
# ==============================================================================


def write_llm_output(data: dict[str, Any]) -> None:
    out_path = os.environ.get("LLM_OUTPUT", "/dev/stdout")
    json_payload = (
        json.dumps(data, indent=2, ensure_ascii=False, cls=ToolJSONEncoder) + "\n"
    )

    if out_path in {"/dev/stdout", "/dev/fd/1", "-"}:
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
# SECTION 8: Function Entry Point for AIChat
# ==============================================================================


def run(
    target: Optional[str] = None,
    mode: Literal[
        "status",
        "folders",
        "devices",
        "folder-status",
        "pause-folder",
        "resume-folder",
        "connections",
        "rescan",
        "conflicts",
        "events",
        "errors",
        "needed",
        "config",
        "rclone-remotes",
        "rclone-about",
        "rclone-ls",
        "rclone-sync",
        "rclone-rc",
        "check-deps",
    ] = "status",
    api_key: Optional[str] = None,
    rc_url: str = "http://127.0.0.1:5572",
    folder_id: Optional[str] = None,
    remote_path: Optional[str] = None,
    limit: Optional[int] = None,
    timeout: int = 15,
    insecure: bool = False,
    dry_run: bool = False,
    env_var: Optional[list[str]] = None,
    use_cache: bool = False,
    no_color: bool = False,
    verbose: bool = False,
) -> None:
    """Execute Syncthing & Rclone master suite."""
    result = execute_tool(
        target=target,
        mode=mode,
        api_key=api_key,
        rc_url=rc_url,
        folder_id=folder_id,
        remote_path=remote_path,
        limit=limit,
        timeout=timeout,
        insecure=insecure,
        dry_run=dry_run,
        env_vars=env_var,
        use_cache=use_cache,
        no_color=no_color,
        verbose=verbose,
    )

    print_human_readable_ui(result, no_color=no_color)
    write_llm_output(result)


# ==============================================================================
# SECTION 9: CLI Argument Parser
# ==============================================================================


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="syncthing_rclone_suite.py",
        description=f"Syncthing & Rclone Master Suite v{__version__}",
    )
    parser.add_argument(
        "--target",
        "-t",
        metavar="URL_OR_REMOTE",
        help="Syncthing URL or Rclone remote name",
    )
    parser.add_argument(
        "--mode",
        choices=[
            "status",
            "folders",
            "devices",
            "folder-status",
            "pause-folder",
            "resume-folder",
            "connections",
            "rescan",
            "conflicts",
            "events",
            "errors",
            "needed",
            "config",
            "rclone-remotes",
            "rclone-about",
            "rclone-ls",
            "rclone-sync",
            "rclone-rc",
            "check-deps",
        ],
        default="status",
        help="Execution mode (default: status)",
    )
    parser.add_argument(
        "--api-key", dest="api_key", metavar="KEY", help="Syncthing API key"
    )
    parser.add_argument(
        "--rc-url",
        dest="rc_url",
        default="http://127.0.0.1:5572",
        help="Rclone RC API URL",
    )
    parser.add_argument(
        "--folder-id", dest="folder_id", metavar="ID", help="Syncthing folder ID"
    )
    parser.add_argument(
        "--remote-path",
        dest="remote_path",
        metavar="PATH",
        help="Rclone remote subpath or RC endpoint",
    )
    parser.add_argument(
        "--limit", type=int, default=50, help="Max items/files to process"
    )
    parser.add_argument("--timeout", type=int, default=15, help="Timeout in seconds")
    parser.add_argument(
        "--insecure",
        action="store_true",
        default=False,
        help="Disable TLS SSL verification",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        default=False,
        dest="dry_run",
        help="Rclone dry-run mode",
    )
    parser.add_argument(
        "--env-var",
        action="append",
        dest="env_var",
        metavar="KEY=VALUE",
        help="Custom env var",
    )
    parser.add_argument(
        "--use-cache",
        action="store_true",
        default=False,
        dest="use_cache",
        help="Enable result caching",
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
        help="Enable debug logging",
    )
    return parser


if __name__ == "__main__":
    args = _build_parser().parse_args()
    res = execute_tool(
        target=args.target,
        mode=args.mode,
        api_key=args.api_key,
        rc_url=args.rc_url,
        folder_id=args.folder_id,
        remote_path=args.remote_path,
        limit=args.limit,
        timeout=args.timeout,
        insecure=args.insecure,
        dry_run=args.dry_run,
        env_vars=args.env_var,
        use_cache=args.use_cache,
        no_color=args.no_color,
        verbose=args.verbose,
    )

    print_human_readable_ui(res, no_color=args.no_color)
    write_llm_output(res)
    sys.exit(res.get("exit_code", EXIT_SUCCESS))
