#!/usr/bin/env python3
# ==============================================================================
# apk_tool.py — Pyrmethus Android APK Operations Tool v2.2.0-ASCENDED
# argc/aichat compatible · Termux · Decompile, Build, Sign, Inspect & Create APKs
#
# @describe Complete Android APK tool for Termux (decompile, build/compile, sign, inspect/info, and create templates).
#
# @meta require-tools aichat apktool apksigner aapt2
#
# @option --action <ACTION>             Operation: decompile/build/sign/info/create (default: info)
# @option --target! <PATH>              Target APK file or project directory (required)
# @option --output <PATH>               Output APK file or destination directory
# @option --keystore <PATH>             Custom keystore path for signing
# @option --ks-pass <PASS>              Keystore password (default: android)
# @option --package-name <NAME>         Package name for 'create' action (default: com.example.myapp)
# @option --app-name <NAME>             App name for 'create' action (default: MyApp)
# @option --mode <MODE>                 Execution mode: summary/detailed (default: summary)
# @flag   --align                       Run zipalign prior to signing
# @flag   --use-cache                   Enable result caching for static APK inspections
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
import zipfile
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
    "decompile": "🔓",
    "build": "🔨",
    "sign": "🔑",
    "info": "🔍",
    "create": "✨",
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
    """Return current terminal column count based on stderr."""
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
    action = data.get("action", "info")
    icon = _ACTION_ICONS.get(action, "📱")

    box_w = get_width() - 4
    border = BOX_H * box_w

    _cprint(f"{NEON_PURPLE}{BOX_TL}{border}{BOX_TR}{RESET}")
    _cprint(
        f"{NEON_PURPLE}{BOX_V}{RESET} {NEON_PINK}{icon} [APK TOOL v{__version__}]{RESET} {status_color}{BOLD}{status_symbol} {status_text}{RESET}"
    )
    _cprint(f"{NEON_PURPLE}{BOX_LT}{border}{BOX_RT}{RESET}")
    _cprint(f"{NEON_PURPLE}{BOX_V}{RESET} {NEON_CYAN}Action:{RESET}   {action}")
    _cprint(
        f"{NEON_PURPLE}{BOX_V}{RESET} {NEON_CYAN}Target:{RESET}   {data.get('target', 'N/A')}"
    )
    if data.get("output"):
        _cprint(
            f"{NEON_PURPLE}{BOX_V}{RESET} {NEON_CYAN}Output:{RESET}   {NEON_GREEN}{data.get('output')}{RESET}"
        )

    if action == "info" and success and "package_info" in data:
        pkg = data["package_info"]
        _cprint(f"{NEON_PURPLE}{BOX_LT}{border}{BOX_RT}{RESET}")
        _cprint(
            f"{NEON_PURPLE}{BOX_V}{RESET} {NEON_CYAN}App Name:{RESET} {NEON_YELLOW}{pkg.get('label', 'N/A')}{RESET}"
        )
        _cprint(
            f"{NEON_PURPLE}{BOX_V}{RESET} {NEON_CYAN}Package:{RESET}  {NEON_LIME}{pkg.get('package_name', 'N/A')}{RESET}"
        )
        _cprint(
            f"{NEON_PURPLE}{BOX_V}{RESET} {NEON_CYAN}Version:{RESET}  {pkg.get('version_name', 'N/A')} (Code: {pkg.get('version_code', 'N/A')})"
        )
        _cprint(
            f"{NEON_PURPLE}{BOX_V}{RESET} {NEON_CYAN}SDK Bounds:{RESET} Min {pkg.get('min_sdk', 'N/A')} | Target {pkg.get('target_sdk', 'N/A')}"
        )
        if pkg.get("native_abis"):
            _cprint(
                f"{NEON_PURPLE}{BOX_V}{RESET} {NEON_CYAN}Native ABIs:{RESET} {', '.join(pkg.get('native_abis'))}"
            )

    _cprint(
        f"{NEON_PURPLE}{BOX_V}{RESET} {NEON_CYAN}Duration:{RESET} {DIM}{data.get('duration_ms', 0)}ms{RESET}"
    )

    if not success and "error" in data:
        _cprint(f"{NEON_PURPLE}{BOX_LT}{border}{BOX_RT}{RESET}")
        _cprint(
            f"{NEON_PURPLE}{BOX_V}{RESET} {NEON_RED}Error:{RESET}    {data['error']}"
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
    """Access agent built-in environment variables."""
    env_name = f"LLM_AGENT_VAR_{name}"
    return os.environ.get(env_name)


def get_execution_context() -> dict[str, Any]:
    """Extract execution context."""
    termux_prefix = os.environ.get("PREFIX", "")
    return {
        "tool_name": os.environ.get("LLM_TOOL_NAME", "apk_tool"),
        "cache_dir": os.environ.get("LLM_TOOL_CACHE_DIR"),
        "root_dir": os.environ.get("LLM_ROOT_DIR"),
        "output_path": os.environ.get("LLM_OUTPUT"),
        "cwd": get_builtin_var("__cwd__") or os.getcwd(),
        "termux_prefix": termux_prefix,
        "is_termux": "com.termux" in termux_prefix
        or Path("/data/data/com.termux").exists(),
    }


def sanitize_path() -> None:
    """Remove llm-functions/bin entries from PATH to prevent wrapper loops."""
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


def _redact_passwords(text: str, pass_val: str) -> str:
    """Redact passwords from process logs and exception strings."""
    if not text:
        return ""
    if pass_val and len(pass_val) > 1:
        text = text.replace(pass_val, "****")
    text = re.sub(r"(--ks-pass\s+pass:)[^\s]+", r"\1****", text)
    text = re.sub(r"(-storepass\s+)[^\s]+", r"\1****", text)
    text = re.sub(r"(-keypass\s+)[^\s]+", r"\1****", text)
    return text


def _find_binary(name: str) -> Optional[str]:
    """Find binary path in Termux/Android or standard Linux PATH."""
    sanitize_path()
    prefix = os.environ.get("PREFIX", "/data/data/com.termux/files/usr")
    candidates = [
        os.path.join(prefix, "bin", name),
        f"/usr/bin/{name}",
        f"/usr/local/bin/{name}",
    ]
    for c in candidates:
        try:
            if Path(c).is_file() and os.access(c, os.X_OK):
                return c
        except Exception:
            pass
    return shutil.which(name)


def _run_cmd(
    cmd: list[str],
    cwd: Optional[str] = None,
    timeout: int = 300,
    pass_to_redact: str = "",
) -> tuple[int, str, str]:
    """Run a subprocess cleanly with timeout, process group isolation, and credential redaction."""
    preexec = os.setsid if hasattr(os, "setsid") else None
    try:
        proc = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            preexec_fn=preexec,
            cwd=cwd,
            text=True,
        )
        try:
            stdout, stderr = proc.communicate(timeout=timeout)
            return (
                proc.returncode,
                _redact_passwords(stdout.strip(), pass_to_redact),
                _redact_passwords(stderr.strip(), pass_to_redact),
            )
        except subprocess.TimeoutExpired:
            if hasattr(os, "killpg") and preexec is not None:
                try:
                    os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
                except Exception:
                    pass
            else:
                proc.kill()
            return EXIT_TIMEOUT, "", f"Command timed out after {timeout} seconds."
    except FileNotFoundError:
        return EXIT_INVALID_INPUT, "", f"Binary not found: {cmd[0]}"
    except Exception as exc:
        return EXIT_ERROR, "", _redact_passwords(str(exc), pass_to_redact)


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
# SECTION 5: Core APK Action Handlers
# ==============================================================================


def _action_info(target_path: Path) -> Tuple[bool, Any]:
    """Inspect APK manifest badging and structure using aapt2/aapt or fallback inspection."""
    if not target_path.is_file():
        return False, f"Target path is not a file: {target_path}"

    aapt_bin = _find_binary("aapt2") or _find_binary("aapt")
    pkg_info: dict[str, Any] = {
        "file_size": target_path.stat().st_size,
        "file_name": target_path.name,
    }

    if aapt_bin:
        cmd = [aapt_bin, "dump", "badging", str(target_path)]
        code, stdout, stderr = _run_cmd(cmd)
        if code == 0:
            for line in stdout.splitlines():
                if line.startswith("package:"):
                    m_pkg = re.search(r"name='([^']+)'", line)
                    m_ver_code = re.search(r"versionCode='([^']+)'", line)
                    m_ver_name = re.search(r"versionName='([^']+)'", line)
                    if m_pkg:
                        pkg_info["package_name"] = m_pkg.group(1)
                    if m_ver_code:
                        pkg_info["version_code"] = m_ver_code.group(1)
                    if m_ver_name:
                        pkg_info["version_name"] = m_ver_name.group(1)
                elif line.startswith("application-label:"):
                    m_label = re.search(r"application-label:'([^']+)'", line)
                    if m_label:
                        pkg_info["label"] = m_label.group(1)
                elif line.startswith("sdkVersion:"):
                    m_sdk = re.search(r"sdkVersion:'([^']+)'", line)
                    if m_sdk:
                        pkg_info["min_sdk"] = m_sdk.group(1)
                elif line.startswith("targetSdkVersion:"):
                    m_target = re.search(r"targetSdkVersion:'([^']+)'", line)
                    if m_target:
                        pkg_info["target_sdk"] = m_target.group(1)

            permissions = re.findall(r"uses-permission: name='([^']+)'", stdout)
            pkg_info["permissions_count"] = len(permissions)
            pkg_info["permissions"] = permissions[:15]

    # Fallback/Supplemental ZIP extraction for Native Libraries ABIs & DEX structure
    try:
        with zipfile.ZipFile(target_path, "r") as z:
            files = z.namelist()
            pkg_info["has_dex"] = any(f.endswith(".dex") for f in files)
            pkg_info["dex_count"] = len([f for f in files if f.endswith(".dex")])
            pkg_info["has_resources"] = "resources.arsc" in files
            pkg_info["has_manifest"] = "AndroidManifest.xml" in files
            pkg_info["total_files"] = len(files)

            # Native ABI architecture inspection (.so files)
            abis = set()
            for f in files:
                if f.startswith("lib/") and f.endswith(".so"):
                    parts = f.split("/")
                    if len(parts) >= 3:
                        abis.add(parts[1])
            pkg_info["native_abis"] = sorted(list(abis))
            pkg_info["has_native_libs"] = len(abis) > 0

            if not aapt_bin:
                pkg_info["notice"] = (
                    "Basic inspection (Install 'aapt2' or 'aapt' via 'pkg install aapt' for full badging)"
                )
            return True, pkg_info
    except Exception as exc:
        return False, f"Failed to inspect APK file: {exc}"


def _action_decompile(
    target_path: Path, output_path: Optional[Path]
) -> Tuple[bool, Any]:
    """Decompile an APK file into a project directory using apktool."""
    if not target_path.is_file():
        return False, f"Decompile target must be an APK file: {target_path}"

    apktool_bin = _find_binary("apktool")
    if not apktool_bin:
        return (
            False,
            "apktool binary not found in Termux. Install with: pkg install apktool",
        )

    out_dir = output_path or (target_path.parent / f"{target_path.stem}_src")
    cmd = [apktool_bin, "d", "-f", "-o", str(out_dir), str(target_path)]

    code, stdout, stderr = _run_cmd(cmd, timeout=300)
    if code == 0 and out_dir.is_dir():
        return True, {
            "decompiled_to": str(out_dir),
            "output": stdout or "Decompiled successfully.",
        }
    return False, stderr or stdout or "Apktool decompilation failed."


def _action_build(target_path: Path, output_path: Optional[Path]) -> Tuple[bool, Any]:
    """Build/compile a decompiled directory structure into an APK using apktool."""
    if not target_path.is_dir():
        return (
            False,
            f"Build target must be a decompiled project directory: {target_path}",
        )

    if not (target_path / "apktool.yml").is_file():
        return (
            False,
            f"Target directory is missing 'apktool.yml'. Is this a valid apktool project? {target_path}",
        )

    apktool_bin = _find_binary("apktool")
    if not apktool_bin:
        return (
            False,
            "apktool binary not found in Termux. Install with: pkg install apktool",
        )

    out_apk = output_path or (target_path.parent / f"{target_path.name}_unsigned.apk")
    cmd = [apktool_bin, "b", str(target_path), "-o", str(out_apk)]

    code, stdout, stderr = _run_cmd(cmd, timeout=300)
    if code == 0 and out_apk.is_file():
        return True, {"built_apk": str(out_apk), "file_size": out_apk.stat().st_size}
    return False, stderr or stdout or "Apktool build failed."


def _generate_debug_keystore(ks_path: Path) -> bool:
    """Generate a standard debug keystore if none exists."""
    keytool_bin = _find_binary("keytool")
    if not keytool_bin:
        return False

    ks_path.parent.mkdir(parents=True, exist_ok=True)
    cmd = [
        keytool_bin,
        "-genkey",
        "-v",
        "-keystore",
        str(ks_path),
        "-alias",
        "androiddebugkey",
        "-keyalg",
        "RSA",
        "-keysize",
        "2048",
        "-validity",
        "10000",
        "-storepass",
        "android",
        "-keypass",
        "android",
        "-dname",
        "CN=Android Debug,O=Android,C=US",
    ]
    code, _, _ = _run_cmd(cmd, pass_to_redact="android")
    return code == 0 and ks_path.is_file()


def _action_sign(
    target_path: Path,
    output_path: Optional[Path],
    keystore: Optional[Path],
    ks_pass: str,
    align: bool,
) -> Tuple[bool, Any]:
    """Sign (and optionally zipalign) an APK file using apksigner."""
    if not target_path.is_file():
        return False, f"Signing target must be an APK file: {target_path}"

    apksigner_bin = _find_binary("apksigner")
    if not apksigner_bin:
        return (
            False,
            "apksigner binary not found in Termux. Install with: pkg install apksigner",
        )

    out_apk = output_path or (target_path.parent / f"{target_path.stem}_signed.apk")
    working_apk = target_path

    # Optional zipalign pass
    if align:
        zipalign_bin = _find_binary("zipalign")
        if zipalign_bin:
            aligned_apk = target_path.parent / f"{target_path.stem}_aligned.apk"
            cmd_align = [
                zipalign_bin,
                "-f",
                "-v",
                "4",
                str(target_path),
                str(aligned_apk),
            ]
            c_align, _, err_align = _run_cmd(cmd_align)
            if c_align == 0 and aligned_apk.is_file():
                working_apk = aligned_apk
            else:
                return False, f"Zipalign failed: {err_align}"

    # Keystore resolution or debug keystore generation
    ks_file = keystore or (Path.home() / ".android" / "debug.keystore")
    if not ks_file.is_file():
        if not _generate_debug_keystore(ks_file):
            return (
                False,
                f"Keystore not found and generation failed: {ks_file} (Install 'openjdk-17' for keytool)",
            )

    cmd_sign = [
        apksigner_bin,
        "sign",
        "--ks",
        str(ks_file),
        "--ks-pass",
        f"pass:{ks_pass}",
        "--out",
        str(out_apk),
        str(working_apk),
    ]

    code, stdout, stderr = _run_cmd(cmd_sign, pass_to_redact=ks_pass)
    if code == 0 and out_apk.is_file():
        return True, {
            "signed_apk": str(out_apk),
            "keystore_used": str(ks_file),
            "aligned": align,
        }
    return False, stderr or stdout or "APKSigner signing failed."


def _action_create(
    target_path: Path,
    package_name: str,
    app_name: str,
) -> Tuple[bool, Any]:
    """Bootstrap a minimal, compilable Android APK project template directory."""
    if target_path.exists() and any(target_path.iterdir()):
        return False, f"Target project directory is not empty: {target_path}"

    target_path.mkdir(parents=True, exist_ok=True)
    pkg_dir = package_name.replace(".", "/")

    # Create directory tree
    (target_path / "res" / "values").mkdir(parents=True, exist_ok=True)
    (target_path / "smali" / pkg_dir).mkdir(parents=True, exist_ok=True)

    # AndroidManifest.xml
    manifest_content = f"""<?xml version="1.0" encoding="utf-8"?>
<manifest xmlns:android="http://schemas.android.com/apk/res/android"
    package="{package_name}"
    android:versionCode="1"
    android:versionName="1.0">

    <uses-sdk android:minSdkVersion="21" android:targetSdkVersion="33" />

    <application
        android:label="@string/app_name"
        android:allowBackup="true">
        
        <activity
            android:name=".MainActivity"
            android:exported="true">
            <intent-filter>
                <action android:name="android.intent.action.MAIN" />
                <category android:name="android.intent.category.LAUNCHER" />
            </intent-filter>
        </activity>
    </application>
</manifest>
"""
    (target_path / "AndroidManifest.xml").write_text(manifest_content, encoding="utf-8")

    # res/values/strings.xml
    strings_content = f"""<?xml version="1.0" encoding="utf-8"?>
<resources>
    <string name="app_name">{app_name}</string>
</resources>
"""
    (target_path / "res" / "values" / "strings.xml").write_text(
        strings_content, encoding="utf-8"
    )

    # apktool.yml
    apktool_yaml = f"""!!brut.androlib.meta.MetaInfo
apkFileName: {app_name}.apk
isFrameworkApk: false
usesFramework:
  ids:
  - 1
sdkInfo:
  minSdkVersion: '21'
  targetSdkVersion: '33'
packageInfo:
  forcedPackageId: '127'
  renameManifestPackage: null
versionInfo:
  versionCode: '1'
  versionName: '1.0'
"""
    (target_path / "apktool.yml").write_text(apktool_yaml, encoding="utf-8")

    # Valid MainActivity smali
    smali_content = f""".class public L{pkg_dir}/MainActivity;
.super Landroid/app/Activity;
.source "MainActivity.java"

.method public constructor <init>()V
    .registers 1
    invoke-direct {{p0}}, Landroid/app/Activity;-><init>()V
    return-void
.end method

.method protected onCreate(Landroid/os/Bundle;)V
    .registers 2
    invoke-super {{p0, p1}}, Landroid/app/Activity;->onCreate(Landroid/os/Bundle;)V
    return-void
.end method
"""
    (target_path / "smali" / pkg_dir / "MainActivity.smali").write_text(
        smali_content, encoding="utf-8"
    )

    return True, {
        "created_project": str(target_path),
        "package_name": package_name,
        "app_name": app_name,
        "structure": [
            "AndroidManifest.xml",
            "res/values/strings.xml",
            "apktool.yml",
            f"smali/{pkg_dir}/MainActivity.smali",
        ],
    }


# ==============================================================================
# SECTION 6: Primary Master Tool Execution Logic
# ==============================================================================


def execute_tool(
    target: str,
    action: str = "info",
    output: Optional[str] = None,
    keystore: Optional[str] = None,
    ks_pass: str = "android",
    package_name: str = "com.example.myapp",
    app_name: str = "MyApp",
    mode: str = "summary",
    align: bool = False,
    use_cache: bool = False,
    no_color: bool = False,
    verbose: bool = False,
) -> dict[str, Any]:
    """
    Core execution logic shared between CLI and run() entry points.
    """
    start_time = time.monotonic()

    if verbose:
        logging.basicConfig(level=logging.DEBUG, format="[DEBUG] %(message)s")
        logging.debug(f"Executing APK tool: action='{action}', target='{target}'")

    base_cwd = get_builtin_var("__cwd__") or os.getcwd()
    target_path = (Path(base_cwd) / target).expanduser().resolve()
    output_path = (Path(base_cwd) / output).expanduser().resolve() if output else None
    keystore_path = (
        (Path(base_cwd) / keystore).expanduser().resolve() if keystore else None
    )
    action_key = action.lower().strip()

    cache = ToolCache()
    cache_key = f"apk:{action_key}:{target_path}:{output_path}"
    if use_cache and action_key == "info":
        cached_result = cache.get(cache_key)
        if cached_result is not None:
            cached_result["cached"] = True
            return cached_result

    shutdown = GracefulShutdown()

    try:
        ok = False
        payload: Any = None

        if action_key == "info":
            ok, payload = _action_info(target_path)
        elif action_key == "decompile":
            ok, payload = _action_decompile(target_path, output_path)
        elif action_key in ("build", "compile"):
            ok, payload = _action_build(target_path, output_path)
        elif action_key == "sign":
            ok, payload = _action_sign(
                target_path, output_path, keystore_path, ks_pass, align
            )
        elif action_key == "create":
            ok, payload = _action_create(target_path, package_name, app_name)
        else:
            return {
                "success": False,
                "error": f"Unknown action '{action}'. Choose from: decompile/build/sign/info/create",
                "exit_code": EXIT_INVALID_INPUT,
                "duration_ms": 0.0,
            }

        duration_ms = round((time.monotonic() - start_time) * 1000, 2)

        if not ok:
            return {
                "success": False,
                "action": action_key,
                "target": str(target_path),
                "error": str(payload),
                "exit_code": EXIT_ERROR,
                "duration_ms": duration_ms,
            }

        raw_json_str = json.dumps(payload, cls=ToolJSONEncoder)

        result: dict[str, Any] = {
            "success": True,
            "action": action_key,
            "target": str(target_path),
            "output": str(output_path)
            if output_path
            else (
                payload.get("decompiled_to")
                or payload.get("built_apk")
                or payload.get("signed_apk")
                or payload.get("created_project")
            ),
            "details": payload,
            "lines_count": len(raw_json_str.splitlines()),
            "bytes_count": len(raw_json_str.encode("utf-8")),
            "context": get_execution_context(),
            "cached": False,
            "duration_ms": duration_ms,
            "exit_code": EXIT_SUCCESS,
        }

        if action_key == "info" and isinstance(payload, dict):
            result["package_info"] = payload

        if shutdown.should_stop():
            result["success"] = False
            result["error"] = "Operation interrupted by user signal."
            result["exit_code"] = EXIT_INTERRUPTED

        if use_cache and result["success"] and action_key == "info":
            cache.set(cache_key, result)

        return result

    except Exception as exc:
        duration_ms = round((time.monotonic() - start_time) * 1000, 2)
        return {
            "success": False,
            "error": f"Tool execution failure: {_redact_passwords(str(exc), ks_pass)}",
            "exit_code": EXIT_ERROR,
            "duration_ms": duration_ms,
        }
    finally:
        shutdown.restore()


# ==============================================================================
# SECTION 7: Output Routing (LLM vs Human Terminal)
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
# SECTION 8: Function Entry Point for AIChat
# ==============================================================================


def run(
    target: str,
    action: Literal["decompile", "build", "sign", "info", "create"] = "info",
    output: Optional[str] = None,
    keystore: Optional[str] = None,
    ks_pass: str = "android",
    package_name: str = "com.example.myapp",
    app_name: str = "MyApp",
    mode: Literal["summary", "detailed"] = "summary",
    align: bool = False,
    use_cache: bool = False,
    no_color: bool = False,
    verbose: bool = False,
) -> None:
    """Execute Android APK operations.

    Args:
        target: Target APK file or project directory
        action: Operation action name (decompile, build, sign, info, create)
        output: Destination APK file or directory
        keystore: Path to custom signing keystore
        ks_pass: Keystore password
        package_name: Package identifier for 'create' action
        app_name: Application label for 'create' action
        mode: Result detail mode (summary/detailed)
        align: Run zipalign before signing
        use_cache: Enable result caching
        no_color: Disable ANSI color output
        verbose: Enable debug log output
    """
    result = execute_tool(
        target=target,
        action=action,
        output=output,
        keystore=keystore,
        ks_pass=ks_pass,
        package_name=package_name,
        app_name=app_name,
        mode=mode,
        align=align,
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
        prog="apk_tool.py",
        description=f"Pyrmethus Termux APK Operations Tool v{__version__}",
    )
    parser.add_argument(
        "--target",
        "-t",
        required=True,
        metavar="PATH",
        help="Target APK file or project directory (required)",
    )
    parser.add_argument(
        "--action",
        "-a",
        default="info",
        choices=["decompile", "build", "sign", "info", "create"],
        help="APK operation action (default: info)",
    )
    parser.add_argument(
        "--output",
        "-o",
        metavar="PATH",
        help="Output APK file or destination directory",
    )
    parser.add_argument(
        "--keystore",
        metavar="PATH",
        help="Custom keystore path for signing",
    )
    parser.add_argument(
        "--ks-pass",
        default="android",
        metavar="PASS",
        help="Keystore password (default: android)",
    )
    parser.add_argument(
        "--package-name",
        default="com.example.myapp",
        metavar="NAME",
        help="Package name for 'create' action",
    )
    parser.add_argument(
        "--app-name",
        default="MyApp",
        metavar="NAME",
        help="App name for 'create' action",
    )
    parser.add_argument(
        "--mode",
        choices=["summary", "detailed"],
        default="summary",
        help="Output mode detail level (default: summary)",
    )
    parser.add_argument(
        "--align",
        action="store_true",
        default=False,
        help="Run zipalign prior to signing",
    )
    parser.add_argument(
        "--use-cache",
        action="store_true",
        default=False,
        dest="use_cache",
        help="Enable result caching for inspections",
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
        target=args.target,
        action=args.action,
        output=args.output,
        keystore=args.keystore,
        ks_pass=args.ks_pass,
        package_name=args.package_name,
        app_name=args.app_name,
        mode=args.mode,
        align=args.align,
        use_cache=args.use_cache,
        no_color=args.no_color,
        verbose=args.verbose,
    )

    print_human_readable_ui(res, no_color=args.no_color)
    write_llm_output(res)
    sys.exit(res.get("exit_code", EXIT_SUCCESS))
