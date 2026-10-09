#!/usr/bin/env python3
# ==============================================================================
# edit_file.py — Pyrmethus File Weaver v2.9.0
# argc/aichat compatible · Termux · Full 33-operation suite
# Upgraded by assistant
#
# @describe File editing and manipulation tool with security restrictions.
#
# @option --operation! <OPERATION>        Operation to perform (required)
# @option --file-path <PATH>              Primary file or directory path
# @option --target-path <PATH>            Secondary path (copy/move/diff target)
# @option --content <TEXT>                Content to write/append/insert
# @option --search-text <TEXT>            Text to search for (replace/search ops)
# @option --replacement <TEXT>            Replacement text
# @option --pattern <PATTERN>             Search pattern (search/grep_dir)
# @option --line-number <NUM>             Line number for insert/delete
# @option --start-line <NUM>              Start line for range operations
# @option --end-line <NUM>                End line for range operations
# @option --encoding <ENC>                File encoding (default: utf-8)
# @option --max-size <NUM>                Max read size in bytes
# @option --max-write-size <NUM>          Max write size in bytes
# @option --line-context <NUM>            Context lines for search results
# @option --sort-by <FIELD>               Sort field for list_dir (name/size/modified)
# @option --context-lines <NUM>           Context lines for diff
# @option --truncate-size <NUM>           Size for truncate operation
# @option --max-backups <NUM>             Maximum backup count
# @option --max-matches <NUM>             Maximum search matches
# @option --mode <MODE>                   Permission mode (octal) or compare mode
# @option --to-type <TYPE>                Line ending type: lf or crlf
# @option --backup-timestamp <TS>         Backup timestamp for revert
# @option --algorithm <ALG>               Hash algorithm (sha256/sha1/sha512/md5/blake2b)
# @option --n-lines <NUM>                 Lines for head/tail operations
# @option --compare-mode <MODE>           Compare mode: bytes or text
# @option --compression <TYPE>            Archive compression type
# @option --password <PASS>               Archive password
# @option --undefined-var <MODE>          Template undefined var: error/keep/empty
# @option --file-pattern <GLOB>           File pattern for find/grep
# @option --min-size <NUM>                Minimum file size filter
# @option --max-size-filter <NUM>         Maximum file size filter
# @option --modified-after <TIMESTAMP>    Modified after (unix timestamp)
# @option --modified-before <TIMESTAMP>   Modified before (unix timestamp)
# @option --file-type <TYPE>              File type filter: any/file/dir
# @option --max-results <NUM>             Maximum find results
# @option --var <KEY=VALUE>               Template variable (repeatable)
# @option --ops <JSON>                    JSON array of operations for batch mode
# @option --edits <JSON>                  JSON array of edits for batch_edit mode
# @flag   --use-regex                     Use regex for pattern matching
# @flag   --no-global                     Only replace first occurrence
# @flag   --case-insensitive              Case-insensitive matching
# @flag   --show-lines                    Show line numbers in output
# @flag   --add-newline                   Append newline to written content
# @flag   --create-parents                Create parent directories
# @flag   --preserve-metadata             Preserve file metadata on copy
# @flag   --include-hidden                Include hidden files
# @flag   --descending                    Sort in descending order
# @flag   --parents                       Create parent dirs for create_dir
# @flag   --recursive                     Recursive operation
# @flag   --verbose                       Enable verbose/debug logging
# @flag   --continue-on-error             Continue batch on error instead of stopping
# @flag   --dry-run                       Preview changes without modifying filesystem
#
# @env LLM_OUTPUT=/dev/fd/1              Output path for LLM integration
# ==============================================================================

from __future__ import annotations

import argparse
import difflib
import fnmatch
import functools
import glob as _glob_module
import hashlib
import json
import logging
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
import zipfile
from collections import deque
from pathlib import Path

# Ensure the tools directory is in sys.path
sys.path.insert(0, str(Path(__file__).resolve().parent))

# ═══════════════════════════════════════════════════════════════════════════
# PATH SANITIZATION
# Remove any llm-functions/bin from PATH to avoid recursive shadowing.
# ═══════════════════════════════════════════════════════════════════════════
_raw_path = os.environ.get("PATH", "")
_path_parts = _raw_path.split(os.pathsep)
_clean_path_parts = [p for p in _path_parts if not p.endswith("/llm-functions/bin")]
os.environ["PATH"] = os.pathsep.join(_clean_path_parts)

from typing import Any, Callable

from edit_options import EditOptions

__all__ = [
    "append",
    "archive",
    "batch",
    "batch_edit",
    "compare_files",
    "copy",
    "create_dir",
    "delete",
    "delete_line",
    "diff",
    "extract",
    "file_hash",
    "file_search",
    "find_files",
    "grep_dir",
    "head",
    "info",
    "insert_line",
    "list_dir",
    "move",
    "normalize_line_endings",
    "read",
    "read_lines",
    "replace",
    "replace_lines",
    "revert_to_backup",
    "run",
    "set_permissions",
    "tail",
    "template_write",
    "truncate",
    "word_count",
    "write",
]

__version__ = "2.9.0"

# ==============================================================================
# SECTION 1: Logger & Color Support
# ==============================================================================

logger = logging.getLogger(__name__)
if not logger.handlers:
    logger.addHandler(logging.NullHandler())

try:
    from colorama import Fore, Style
    from colorama import init as colorama_init

    colorama_init(autoreset=True)
    COLORAMA_AVAILABLE = True
except ImportError:
    COLORAMA_AVAILABLE = False

    class _DummyColor:
        """Fallback when colorama is not installed."""

        def __getattr__(self, name: str) -> str:
            return ""

    Fore = _DummyColor()  # type: ignore[assignment]
    Style = _DummyColor()  # type: ignore[assignment]


# SYNTHWAVE RETRO-NEON PALETTE
CYBER_MAGENTA = "\033[38;5;201m"
CYBER_LIME = "\033[38;5;82m"
CYBER_CYAN = "\033[38;5;51m"
RESET = "\033[0m"


def _cprint(text: str, color: str = CYBER_CYAN, style: str = "") -> None:
    """Print with Cyber-Neon palette."""
    print(f"{style}{color}{text}{RESET}")


# ==============================================================================
# SECTION 2: Constants
# ==============================================================================

DEFAULT_MAX_READ: int = 10_485_760  # 10 MiB
DEFAULT_MAX_WRITE: int = 104_857_600  # 100 MiB
DEFAULT_ENCODING: str = "utf-8"
MAX_BACKUPS: int = 15
BINARY_CHECK_BYTES: int = 32_768
STDIN_TIMEOUT: float = 30.0
MAX_PATH_LENGTH: int = 4096
MAX_ARCHIVE_SIZE: int = 524_288_000  # 500 MiB
MAX_FIND_RESULTS: int = 10_000

# Paths that should never be written to even if they resolve inside home
PSEUDO_FS_PREFIXES: tuple[str, ...] = (
    "/proc",
    "/sys",
    "/dev",
    "/system",
    "/vendor",
    "/data/data/com.termux",
)

# Sort key lambdas for list_dir
_SORT_KEYS: dict[str, Callable[[dict[str, Any]], Any]] = {
    "name": lambda x: x["name"].lower(),
    "size": lambda x: x.get("size", 0),
    "modified": lambda x: x.get("modified", 0.0),
    "type": lambda x: (not x.get("is_dir", False), x["name"].lower()),
}

_HASH_ALGORITHMS: frozenset[str] = frozenset(
    {"sha256", "sha1", "sha512", "md5", "blake2b"}
)

# Template variable pattern: {{ var_name }} with optional whitespace
_TEMPLATE_RE = re.compile(r"\{\{\s*(\w+)\s*\}\}")

# MIME types that should be treated as text (not binary)
_TEXT_MIME_PREFIXES: tuple[str, ...] = (
    "text/",
    "application/json",
    "application/xml",
    "application/javascript",
    "application/ecmascript",
    "application/x-shellscript",
    "application/x-python",
    "application/x-perl",
    "application/x-ruby",
    "application/x-httpd-php",
    "application/xhtml+xml",
    "application/x-empty",
    "inode/x-empty",
    "application/toml",
    "application/yaml",
    "application/x-yaml",
    "application/svg+xml",
    "application/csv",
    "application/sql",
    "application/x-subrip",
    "application/x-tex",
    "application/rtf",
    "application/x-ndjson",
    "application/graphql",
    "application/x-typescript",
)

# Cache the `file` command location once at import time
_FILE_CMD: str | None = shutil.which("file")

# Stale lock threshold
STALE_LOCK_SECONDS: float = 30.0

OperationName = str


def _glob_escape(name: str) -> str:
    """Escape special glob/fnmatch characters for use with Path.glob()."""
    return _glob_module.escape(name)


# ==============================================================================
# SECTION 3: Timing decorator
# ==============================================================================


def _timed(fn: Callable[..., dict[str, Any]]) -> Callable[..., dict[str, Any]]:
    """Wrap any operation function to inject ``duration_ms`` into its result."""

    @functools.wraps(fn)
    def wrapper(*args: Any, **kwargs: Any) -> dict[str, Any]:
        t0 = time.perf_counter()
        result = fn(*args, **kwargs)
        if not isinstance(result, dict):
            result = {"success": False, "error": "Operation returned non-dict"}
        result["duration_ms"] = round((time.perf_counter() - t0) * 1000, 3)
        return result

    return wrapper


# ==============================================================================
# SECTION 4: FileEditor — sandboxed core
# ==============================================================================


class FileEditor:
    """
    Secure sandboxed file editor.

    All paths are confined to the user's home directory or the system temp
    directory.  Symlink escapes, path traversal, and pseudo-filesystem access
    are all blocked at the path-validation layer.
    """

    def __init__(self) -> None:
        self.home: Path = Path.home().resolve()
        self.temp: Path = Path(tempfile.gettempdir()).resolve()
        self._home_str: str = str(self.home) + os.sep
        self._temp_str: str = str(self.temp) + os.sep
        self._termux_home: str = "/data/data/com.termux/files/home"

    # -------------------------------------------------------------------------
    # Path validation
    # -------------------------------------------------------------------------

    def _validate_path(self, file_path: str, allow_write: bool = True) -> Path | None:
        """
        Return a resolved, sandbox-confined Path or None on any violation.

        Checks (in order):
        1. Type and emptiness
        2. Null-byte injection
        3. Path length limit
        4. Directory traversal (..)
        5. Sandbox boundary (home or temp)
        6. Symlink escape
        7. Read permission (read-only ops)
        8. Parent creation (write ops)
        """
        if not file_path or not isinstance(file_path, str):
            return None
        if "\x00" in file_path:
            logger.warning("Null byte in path rejected: %r", file_path)
            return None
        if len(file_path) > MAX_PATH_LENGTH:
            logger.warning("Path too long (%d chars), rejected", len(file_path))
            return None

        raw = Path(file_path)
        if ".." in raw.parts:
            logger.warning("Path traversal attempt rejected: %s", file_path)
            return None

        try:
            path = raw.expanduser().resolve(strict=False)
        except (ValueError, OSError):
            return None

        if not self._is_allowed(path):
            logger.warning("Path outside allowed realm: %s", path)
            return None

        # Symlink safety — resolve the link target relative to link's parent
        if path.is_symlink():
            try:
                raw_target = os.readlink(path)
                target = (path.parent / raw_target).resolve()
                if not self._is_allowed(target):
                    logger.warning("Symlink escape blocked: %s → %s", path, target)
                    return None
            except OSError:
                pass

        if allow_write:
            parent = path.parent
            if not parent.exists():
                try:
                    parent.mkdir(parents=True, exist_ok=True)
                except OSError:
                    return None
        elif path.exists() and not os.access(path, os.R_OK):
            return None

        return path

    def _is_allowed(self, path: Path) -> bool:
        """True if path is inside the home or temp sandbox."""
        s = str(path)
        return (
            s.startswith(self._home_str)
            or path == self.home
            or s.startswith(self._temp_str)
            or path == self.temp
            or s.startswith(self._termux_home)
        )

    # -------------------------------------------------------------------------
    # Binary detection
    # -------------------------------------------------------------------------

    def _is_binary(self, path: Path, check_bytes: int = BINARY_CHECK_BYTES) -> bool:
        """
        Return True if path appears to be a binary file.

        Strategy:
        1. Pseudo-filesystems → always text.
        2. Null-byte presence in first chunk → binary.
        3. `file --mime-type` subprocess when available.
        4. Fallback: non-binary.
        """
        path_str = str(path)
        if any(path_str.startswith(pfx) for pfx in PSEUDO_FS_PREFIXES):
            return False
        try:
            with open(path, "rb") as f:
                chunk = f.read(check_bytes)
            if b"\x00" in chunk:
                return True
            if _FILE_CMD:
                try:
                    r = subprocess.run(
                        [_FILE_CMD, "-b", "--mime-type", path_str],
                        capture_output=True,
                        text=True,
                        timeout=2.0,
                    )
                    if r.returncode == 0:
                        mime = r.stdout.strip()
                        if not mime:
                            return False
                        return not mime.startswith(_TEXT_MIME_PREFIXES)
                except Exception:
                    pass
            return False
        except OSError:
            return True

    # -------------------------------------------------------------------------
    # Atomic write
    # -------------------------------------------------------------------------

    def _atomic_write(
        self, path: Path, content: str, encoding: str = DEFAULT_ENCODING
    ) -> None:
        """
        Write content to path atomically using O_TMPFILE (Linux) or
        mkstemp + rename (portable).
        """
        dir_ = path.parent
        _O_TMPFILE = getattr(os, "O_TMPFILE", None)
        tmp_link: str | None = None
        fd: int | None = None

        if _O_TMPFILE is not None:
            try:
                fd = os.open(str(dir_), _O_TMPFILE | os.O_RDWR | os.O_CLOEXEC, 0o600)
                raw = content.encode(encoding, errors="surrogateescape")
                os.write(fd, raw)
                os.fsync(fd)
                proc_link = f"/proc/self/fd/{fd}"
                tmp_link = str(dir_ / f".~tmp_{os.getpid()}_{time.time_ns()}")
                os.link(proc_link, tmp_link)
                os.close(fd)
                fd = None
                os.replace(tmp_link, str(path))
                tmp_link = None
                return
            except Exception:
                if fd is not None:
                    try:
                        os.close(fd)
                    except OSError:
                        pass
                if tmp_link is not None:
                    try:
                        os.unlink(tmp_link)
                    except OSError:
                        pass

        # Portable mkstemp fallback
        fd2, tmp_name = tempfile.mkstemp(dir=dir_, prefix=".~tmp_")
        try:
            os.chmod(tmp_name, 0o600)
            with os.fdopen(fd2, "w", encoding=encoding) as f:
                f.write(content)
                f.flush()
                os.fsync(f.fileno())
            os.replace(tmp_name, str(path))
        except Exception:
            try:
                os.unlink(tmp_name)
            except OSError:
                pass
            raise

    # -------------------------------------------------------------------------
    # Backup system
    # -------------------------------------------------------------------------

    def _make_backup(self, path: Path, max_backups: int = MAX_BACKUPS) -> Path:
        """Copy path to a timestamped .bak file; prune excess backups."""
        lock = path.parent / f".~bklock_{path.name}"
        self._acquire_lock(lock)
        try:
            ts = time.time_ns()
            backup = path.parent / f"{path.stem}{path.suffix}.{ts}.bak"
            shutil.copy2(path, backup)
            self._prune_backups(path, max_backups)
            return backup
        finally:
            self._release_lock(lock)

    @staticmethod
    def _acquire_lock(lock_path: Path, timeout: float = 3.0) -> None:
        """Spin-acquire a simple lock file; break stale locks."""
        deadline = time.monotonic() + timeout
        while True:
            try:
                fd = os.open(
                    str(lock_path), os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600
                )
                os.write(fd, str(os.getpid()).encode())
                os.close(fd)
                return
            except FileExistsError:
                try:
                    lock_age = time.time() - lock_path.stat().st_mtime
                    if lock_age > STALE_LOCK_SECONDS:
                        logger.warning(
                            "Breaking stale lock %s (age=%.1fs)", lock_path, lock_age
                        )
                        lock_path.unlink(missing_ok=True)
                        continue
                except OSError:
                    pass
            if time.monotonic() >= deadline:
                logger.warning("Lock acquisition timed out: %s", lock_path)
                return
            time.sleep(0.03)

    @staticmethod
    def _release_lock(lock_path: Path) -> None:
        """Remove the lock file created by _acquire_lock."""
        lock_path.unlink(missing_ok=True)

    def _prune_backups(self, path: Path, max_backups: int) -> None:
        """Delete oldest backups beyond max_backups."""
        pattern = f"{_glob_escape(path.stem)}{_glob_escape(path.suffix)}.*.bak"
        try:
            backups = sorted(
                path.parent.glob(pattern),
                key=lambda p: p.stat().st_mtime_ns,
            )
            while len(backups) > max_backups:
                backups.pop(0).unlink(missing_ok=True)
        except OSError:
            pass

    def _list_backups(self, path: Path) -> list[Path]:
        """Return all backups for path, newest first."""
        pattern = f"{_glob_escape(path.stem)}{_glob_escape(path.suffix)}.*.bak"
        try:
            return sorted(
                path.parent.glob(pattern),
                key=lambda p: p.stat().st_mtime_ns,
                reverse=True,
            )
        except OSError:
            return []

    # -------------------------------------------------------------------------
    # Shared content reader
    # -------------------------------------------------------------------------

    def _read_content(
        self,
        file_path: str,
        encoding: str = DEFAULT_ENCODING,
        allow_binary: bool = False,
    ) -> dict[str, Any]:
        """
        Validate path and read text content.

        Returns dict with keys:
          success, content, path  — on success
          success, error          — on failure
        """
        path = self._validate_path(file_path, allow_write=False)
        if not path:
            return {"success": False, "error": "Invalid or disallowed file path"}
        if not path.exists():
            return {"success": False, "error": f"File not found: {file_path}"}
        if not path.is_file():
            return {
                "success": False,
                "error": f"Path is not a regular file: {file_path}",
            }
        if not allow_binary and self._is_binary(path):
            return {
                "success": False,
                "error": "Binary file detected; operation refused",
            }
        try:
            content = path.read_text(encoding=encoding, errors="surrogateescape")
            return {"success": True, "content": content, "path": path}
        except Exception as exc:
            return {"success": False, "error": str(exc)}


# Module-level singleton — all public functions share one editor instance
_editor = FileEditor()


# ==============================================================================
# SECTION 5: Helpers
# ==============================================================================


def _ends_with_newline(path: Path) -> bool:
    """Check if a file ends with a newline character."""
    try:
        with open(path, "rb") as f:
            f.seek(0, os.SEEK_END)
            if f.tell() == 0:
                return False
            f.seek(-1, os.SEEK_END)
            return f.read(1) == b"\n"
    except OSError:
        return False


def _normalize_lf(text: str) -> str:
    """Normalize CRLF/CR line endings to LF."""
    return text.replace("\r\n", "\n").replace("\r", "\n")


def _build_regex(
    pattern: str,
    use_regex: bool,
    case_sensitive: bool,
) -> re.Pattern[str] | None:
    """
    Compile a search pattern.  Returns None on re.error (caller handles).
    Raises re.error so caller can surface a clean message.
    """
    flags = 0 if case_sensitive else re.IGNORECASE
    if use_regex:
        return re.compile(pattern, flags)
    return re.compile(re.escape(pattern), flags)


# ==============================================================================
# SECTION 6: Original Operations
# ==============================================================================


@_timed
def read(
    file_path: str,
    max_size: int = DEFAULT_MAX_READ,
    encoding: str = DEFAULT_ENCODING,
    show_lines: bool = True,
    start_line: int | None = None,
    end_line: int | None = None,
) -> dict[str, Any]:
    """
    Read file contents with optional line slicing.

    Returns content as a string plus per-line list when show_lines=True.
    Slicing is 1-based inclusive on both ends.
    """
    path = _editor._validate_path(file_path, allow_write=False)
    if not path:
        return {"success": False, "error": "Invalid or disallowed file path"}
    if not path.exists():
        return {"success": False, "error": f"File not found: {file_path}"}
    if not path.is_file():
        return {"success": False, "error": f"Path is not a regular file: {file_path}"}
    try:
        stat = path.stat()
        if stat.st_size > max_size:
            return {
                "success": False,
                "error": (
                    f"File too large: {stat.st_size:,} bytes (limit: {max_size:,})"
                ),
            }
        if _editor._is_binary(path):
            return {"success": False, "error": "Binary file detected; refusing to read"}

        # Delegate to read_lines for range requests
        if start_line is not None or end_line is not None:
            return read_lines(
                file_path=file_path,
                start_line=start_line or 1,
                end_line=end_line or sys.maxsize,
                encoding=encoding,
            )

        content = path.read_text(encoding=encoding, errors="surrogateescape")
        all_lines = content.splitlines()

        return {
            "success": True,
            "content": content,
            "lines": all_lines if show_lines else None,
            "line_count": len(all_lines),
            "total_lines": len(all_lines),
            "path": str(path),
            "size": stat.st_size,
            "encoding": encoding,
        }
    except Exception as exc:
        return {"success": False, "error": str(exc)}


@_timed
def read_lines(
    file_path: str,
    start_line: int,
    end_line: int,
    encoding: str = DEFAULT_ENCODING,
) -> dict[str, Any]:
    """
    Efficiently read a contiguous line range without loading the whole file.

    Lines are 1-based and inclusive.  Streaming read for memory efficiency.
    """
    try:
        start_line = int(start_line) if start_line is not None else 1
        end_line = int(end_line) if end_line is not None else 1
    except (ValueError, TypeError):
        return {
            "success": False,
            "error": "start_line and end_line must be integers",
        }

    if start_line < 1 or end_line < start_line:
        return {
            "success": False,
            "error": "Invalid line range: start_line must be >= 1 and <= end_line",
        }
    path = _editor._validate_path(file_path, allow_write=False)
    if not path or not path.exists() or not path.is_file():
        return {"success": False, "error": "Invalid or missing file"}
    if _editor._is_binary(path):
        return {"success": False, "error": "Binary file detected"}
    try:
        selected: list[str] = []
        last = 0
        with open(path, encoding=encoding, errors="surrogateescape") as f:
            for lineno, line in enumerate(f, 1):
                last = lineno
                if lineno > end_line:
                    break
                if lineno >= start_line:
                    selected.append(line.rstrip("\n"))
        if not selected and start_line > last:
            return {
                "success": False,
                "error": f"start_line {start_line} beyond file end ({last} lines)",
            }
        return {
            "success": True,
            "path": str(path),
            "lines": selected,
            "content": "\n".join(selected),
            "start_line": start_line,
            "end_line": start_line + len(selected) - 1 if selected else start_line,
            "line_count": len(selected),
        }
    except Exception as exc:
        return {"success": False, "error": str(exc)}


@_timed
def write(options: EditOptions) -> dict[str, Any]:
    """
    Atomically write (create or overwrite) a file.

    Uses O_TMPFILE + rename on Linux; mkstemp + rename elsewhere.
    No partial writes are ever visible.
    """
    if options.content is None:
        return {"success": False, "error": "content cannot be None"}

    file_path = options.file_path
    if not file_path:
        return {"success": False, "error": "file_path is required"}

    path = _editor._validate_path(file_path, allow_write=True)
    if not path:
        return {"success": False, "error": "Invalid or disallowed file path"}

    content = (
        str(options.content)
        if not isinstance(options.content, str)
        else options.content
    )
    content = _normalize_lf(content)

    if options.add_newline and content and not content.endswith("\n"):
        content += "\n"

    encoding = options.encoding
    encoded_size = len(content.encode(encoding, errors="surrogateescape"))
    if encoded_size > options.max_write_size:
        return {
            "success": False,
            "error": f"Content exceeds max write size ({options.max_write_size:,} bytes)",
        }

    if options.dry_run:
        return {
            "success": True,
            "path": str(path),
            "mode": "dry-run",
            "message": "Write operation would proceed (dry-run)",
            "size_would_be": encoded_size,
        }

    try:
        original_bytes = path.stat().st_size if path.exists() else 0
        mode_label = "overwrite" if path.exists() else "create"
        _editor._atomic_write(path, content, encoding)
        new_bytes = path.stat().st_size
        return {
            "success": True,
            "path": str(path),
            "size": new_bytes,
            "bytes_delta": new_bytes - original_bytes,
            "original_bytes": original_bytes,
            "new_bytes": new_bytes,
            "mode": mode_label,
            "encoding": encoding,
        }
    except Exception as exc:
        return {"success": False, "error": str(exc)}


@_timed
def append(
    file_path: str,
    content: str,
    encoding: str = DEFAULT_ENCODING,
    add_newline: bool = True,
    max_size: int = DEFAULT_MAX_WRITE,
) -> dict[str, Any]:
    """
    Append text to an existing file.

    When add_newline=True (default), ensures a newline separates existing
    content from the new content even if the file didn't end with one.
    Uses atomic read-modify-write to avoid partial writes.
    """
    if content is None:
        return {"success": False, "error": "content cannot be None"}
    if not isinstance(content, str):
        content = str(content)

    path = _editor._validate_path(file_path, allow_write=True)
    if not path:
        return {"success": False, "error": "Invalid or disallowed file path"}
    if not path.exists():
        return {"success": False, "error": "File not found for append operation"}
    if not path.is_file():
        return {"success": False, "error": "Path is not a regular file"}
    if _editor._is_binary(path):
        return {"success": False, "error": "Binary file detected; append refused"}

    content = _normalize_lf(content)
    encoded_size = len(content.encode(encoding, errors="surrogateescape"))

    if encoded_size > max_size:
        return {
            "success": False,
            "error": f"Content too large ({encoded_size:,} bytes, limit: {max_size:,})",
        }

    try:
        original_bytes = path.stat().st_size
        needs_sep = add_newline and original_bytes > 0 and not _ends_with_newline(path)
        newline_size = 1 if needs_sep else 0
        combined = original_bytes + encoded_size + newline_size

        if combined > max_size:
            return {
                "success": False,
                "error": (
                    f"Combined file size would be {combined:,} bytes, "
                    f"exceeding limit of {max_size:,} bytes"
                ),
            }

        # Atomic read-modify-write
        current = path.read_text(encoding=encoding, errors="surrogateescape")
        if needs_sep:
            current += "\n"
        _editor._atomic_write(path, current + content, encoding)

        new_size = path.stat().st_size
        return {
            "success": True,
            "path": str(path),
            "size": new_size,
            "bytes_delta": new_size - original_bytes,
            "original_bytes": original_bytes,
            "new_bytes": new_size,
            "encoding": encoding,
        }
    except Exception as exc:
        return {"success": False, "error": str(exc)}


@_timed
def replace(options: EditOptions) -> dict[str, Any]:
    """
    Replace text or regex pattern in a file.

    A timestamped backup is created before any modification.
    Reports count of replacements made.
    Supports capture-group back-references in replacement strings.
    """
    file_path = options.file_path
    search_text = options.search_text or options.pattern

    if not search_text:
        return {"success": False, "error": "search_text / pattern cannot be empty"}
    if not isinstance(search_text, str):
        search_text = str(search_text)

    # 'content' acts as fallback for 'replacement'
    if options.replacement is not None:
        replacement = options.replacement
    elif options.content is not None:
        replacement = str(options.content)
    else:
        replacement = ""
    if not isinstance(replacement, str):
        replacement = str(replacement)

    res = _editor._read_content(file_path, options.encoding)
    if not res["success"]:
        return res

    content: str = _normalize_lf(res["content"])
    path: Path = res["path"]

    try:
        compiled = _build_regex(search_text, options.use_regex, options.case_sensitive)
    except re.error as exc:
        return {"success": False, "error": f"Invalid regex: {exc}"}

    try:
        if options.global_replace:
            new_content, count = compiled.subn(replacement, content)
        else:
            new_content, count = compiled.subn(replacement, content, count=1)

        if new_content == content:
            return {
                "success": True,
                "path": str(path),
                "replacements": 0,
                "message": "No replacements made — pattern not found",
            }

        if options.dry_run:
            return {
                "success": True,
                "path": str(path),
                "mode": "dry-run",
                "message": "Replace would proceed (dry-run)",
                "replacements_would_be": count,
            }

        original_bytes = path.stat().st_size
        backup = _editor._make_backup(path, options.max_backups)
        _editor._atomic_write(path, new_content, options.encoding)
        new_bytes = path.stat().st_size

        return {
            "success": True,
            "path": str(path),
            "replacements": count,
            "bytes_delta": new_bytes - original_bytes,
            "original_bytes": original_bytes,
            "new_bytes": new_bytes,
            "backup_path": str(backup),
            "size": new_bytes,
        }
    except re.error as exc:
        return {"success": False, "error": f"Invalid regex: {exc}"}
    except Exception as exc:
        return {"success": False, "error": str(exc)}


@_timed
def insert_line(
    file_path: str,
    line_number: int,
    content: str,
    encoding: str = DEFAULT_ENCODING,
    max_backups: int = MAX_BACKUPS,
) -> dict[str, Any]:
    """
    Insert a line at position line_number (1-based).

    line_number is clamped to [1, len(lines)+1].
    """
    if content is None:
        return {"success": False, "error": "content cannot be None"}

    res = _editor._read_content(file_path, encoding)
    if not res["success"]:
        return res
    original_content: str = res["content"]
    path: Path = res["path"]

    lines = original_content.splitlines(keepends=True)
    line_number = max(1, min(int(line_number), len(lines) + 1))
    if not content.endswith("\n"):
        content += "\n"
    new_lines = lines[: line_number - 1] + [content] + lines[line_number - 1 :]
    new_content = "".join(new_lines)

    original_bytes = len(original_content.encode(encoding, errors="surrogateescape"))
    try:
        backup = _editor._make_backup(path, max_backups)
        _editor._atomic_write(path, new_content, encoding)
        stat = path.stat()
        return {
            "success": True,
            "path": str(path),
            "line_number": line_number,
            "bytes_delta": stat.st_size - original_bytes,
            "original_bytes": original_bytes,
            "new_bytes": stat.st_size,
            "original_lines": len(lines),
            "new_lines": len(new_lines),
            "backup_path": str(backup),
        }
    except Exception as exc:
        return {"success": False, "error": str(exc)}


@_timed
def delete_line(
    file_path: str,
    line_number: int,
    encoding: str = DEFAULT_ENCODING,
    max_backups: int = MAX_BACKUPS,
) -> dict[str, Any]:
    """
    Delete line line_number (1-based) from a file.

    Returns the deleted line's content in 'deleted_content'.
    """
    res = _editor._read_content(file_path, encoding)
    if not res["success"]:
        return res
    content: str = res["content"]
    path: Path = res["path"]

    lines = content.splitlines(keepends=True)
    ln = int(line_number)
    if ln < 1 or ln > len(lines):
        return {
            "success": False,
            "error": f"Invalid line number {ln} (file has {len(lines)} lines)",
        }
    deleted = lines.pop(ln - 1)
    new_content = "".join(lines)

    original_bytes = len(content.encode(encoding, errors="surrogateescape"))
    try:
        backup = _editor._make_backup(path, max_backups)
        _editor._atomic_write(path, new_content, encoding)
        stat = path.stat()
        return {
            "success": True,
            "path": str(path),
            "deleted_line": ln,
            "deleted_content": deleted.rstrip("\n"),
            "bytes_delta": stat.st_size - original_bytes,
            "original_bytes": original_bytes,
            "new_bytes": stat.st_size,
            "original_lines": len(lines) + 1,
            "new_lines": len(lines),
            "backup_path": str(backup),
        }
    except Exception as exc:
        return {"success": False, "error": str(exc)}


@_timed
def replace_lines(
    file_path: str,
    start_line: int,
    end_line: int,
    content: str,
    encoding: str = DEFAULT_ENCODING,
    max_backups: int = MAX_BACKUPS,
    dry_run: bool = False,
) -> dict[str, Any]:
    """
    Replace a range of lines [start_line, end_line] with new content.

    Both bounds are 1-based and inclusive.
    """
    if content is None:
        return {"success": False, "error": "content cannot be None"}
    if not isinstance(content, str):
        content = str(content)

    try:
        start_line = int(start_line)
        end_line = int(end_line)
    except (ValueError, TypeError):
        return {"success": False, "error": "start_line and end_line must be integers"}

    res = _editor._read_content(file_path, encoding)
    if not res["success"]:
        return res
    original: str = res["content"]
    path: Path = res["path"]

    lines = original.splitlines(keepends=True)
    total = len(lines)
    if start_line < 1 or end_line < start_line or end_line > total:
        return {
            "success": False,
            "error": f"Invalid range {start_line}–{end_line} (file has {total} lines)",
        }

    content_lines = content.splitlines()
    replacement_lines = [ln + "\n" for ln in content_lines] if content_lines else []
    new_lines = lines[: start_line - 1] + replacement_lines + lines[end_line:]
    new_content = "".join(new_lines)

    original_bytes = len(original.encode(encoding, errors="surrogateescape"))
    new_bytes = len(new_content.encode(encoding, errors="surrogateescape"))

    if dry_run:
        return {
            "success": True,
            "path": str(path),
            "mode": "dry-run",
            "message": "Replace-lines would proceed (dry-run)",
            "start_line": start_line,
            "end_line": end_line,
            "original_lines": total,
            "new_lines": len(new_lines),
            "bytes_delta_would_be": new_bytes - original_bytes,
            "original_bytes": original_bytes,
            "new_bytes_would_be": new_bytes,
            "lines_removed": end_line - start_line + 1,
            "lines_added": len(replacement_lines),
        }

    try:
        backup = _editor._make_backup(path, max_backups)
        _editor._atomic_write(path, new_content, encoding)
        stat = path.stat()
        return {
            "success": True,
            "path": str(path),
            "start_line": start_line,
            "end_line": end_line,
            "bytes_delta": stat.st_size - original_bytes,
            "original_bytes": original_bytes,
            "new_bytes": stat.st_size,
            "original_lines": total,
            "new_lines": len(new_lines),
            "backup_path": str(backup),
        }
    except Exception as exc:
        return {"success": False, "error": str(exc)}


@_timed
def file_search(
    file_path: str,
    pattern: str,
    use_regex: bool = False,
    case_sensitive: bool = True,
    line_context: int = 0,
    encoding: str = DEFAULT_ENCODING,
    max_matches: int = 1000,
) -> dict[str, Any]:
    """
    Search for a pattern inside a single file.

    Returns a list of match dicts each containing line number and content.
    """
    if not pattern or not pattern.strip():
        return {"success": False, "error": "pattern cannot be empty"}
    res = _editor._read_content(file_path, encoding)
    if not res["success"]:
        return res
    content: str = res["content"]
    path: Path = res["path"]

    lines = content.splitlines()
    matches: list[dict[str, Any]] = []
    truncated = False

    try:
        compiled = _build_regex(pattern, use_regex, case_sensitive)
    except re.error as exc:
        return {"success": False, "error": f"Invalid regex: {exc}"}

    def match_fn(line: str) -> bool:
        return bool(compiled.search(line))

    for i, line in enumerate(lines):
        if len(matches) >= max_matches:
            truncated = True
            break
        if match_fn(line):
            entry: dict[str, Any] = {"line": i + 1, "content": line}
            if line_context > 0:
                ctx_start = max(0, i - line_context)
                ctx_end = min(len(lines), i + line_context + 1)
                entry["context"] = lines[ctx_start:ctx_end]
                entry["context_start_line"] = ctx_start + 1
            matches.append(entry)

    return {
        "success": True,
        "path": str(path),
        "pattern": pattern,
        "matches": matches,
        "match_count": len(matches),
        "truncated": truncated,
    }


@_timed
def copy(
    file_path: str,
    target_path: str,
    preserve_metadata: bool = True,
    recursive: bool = False,
) -> dict[str, Any]:
    """
    Copy a file or directory.  Directories require recursive=True.
    """
    src = _editor._validate_path(file_path, allow_write=False)
    dst = _editor._validate_path(target_path, allow_write=True)
    if not src or not dst:
        return {"success": False, "error": "Invalid or disallowed path(s)"}
    if not src.exists():
        return {"success": False, "error": f"Source not found: {file_path}"}
    if src == dst:
        return {"success": False, "error": "Source and target are identical"}
    try:
        if src.is_dir():
            if not recursive:
                return {
                    "success": False,
                    "error": "Use recursive=True to copy directories",
                }
            if dst.exists():
                return {
                    "success": False,
                    "error": f"Destination already exists: {target_path}",
                }
            copy_fn = shutil.copy2 if preserve_metadata else shutil.copy
            shutil.copytree(src, dst, copy_function=copy_fn)
            size = sum(f.stat().st_size for f in dst.rglob("*") if f.is_file())
        else:
            if preserve_metadata:
                shutil.copy2(src, dst)
            else:
                shutil.copy(src, dst)
            size = dst.stat().st_size
        return {
            "success": True,
            "source": str(src),
            "target": str(dst),
            "size": size,
            "preserve_metadata": preserve_metadata,
            "recursive": recursive,
        }
    except Exception as exc:
        return {"success": False, "error": str(exc)}


@_timed
def move(file_path: str, target_path: str) -> dict[str, Any]:
    """Move or rename a file or directory."""
    src = _editor._validate_path(file_path, allow_write=True)
    dst = _editor._validate_path(target_path, allow_write=True)
    if not src or not dst:
        return {"success": False, "error": "Invalid or disallowed path(s)"}
    if not src.exists():
        return {"success": False, "error": f"Source not found: {file_path}"}
    if src == dst:
        return {"success": False, "error": "Source and target are identical"}
    try:
        shutil.move(str(src), str(dst))
        size = dst.stat().st_size if dst.is_file() else 0
        return {"success": True, "source": str(src), "target": str(dst), "size": size}
    except Exception as exc:
        return {"success": False, "error": str(exc)}


@_timed
def delete(file_path: str, recursive: bool = False) -> dict[str, Any]:
    """
    Delete a file or directory.  Directories require recursive=True.
    """
    path = _editor._validate_path(file_path, allow_write=True)
    if not path or not path.exists():
        return {"success": False, "error": f"Path not found: {file_path}"}
    try:
        if path.is_dir():
            if not recursive:
                return {
                    "success": False,
                    "error": "Use recursive=True to delete directories",
                }
            size = sum(f.stat().st_size for f in path.rglob("*") if f.is_file())
            shutil.rmtree(path)
        else:
            size = path.stat().st_size
            path.unlink()
        return {
            "success": True,
            "path": str(path),
            "size": size,
            "recursive": recursive,
        }
    except Exception as exc:
        return {"success": False, "error": str(exc)}


@_timed
def info(file_path: str) -> dict[str, Any]:
    """
    Return detailed metadata for a file or directory.

    Includes MIME type detection and line count for text files.
    """
    path = _editor._validate_path(file_path, allow_write=False)
    if not path or not path.exists():
        return {"success": False, "error": f"Path not found: {file_path}"}

    symlink_target: str | None = None
    if path.is_symlink():
        try:
            symlink_target = str(os.readlink(path))
        except OSError:
            symlink_target = "<unreadable>"

    base: dict[str, Any] = {
        "success": True,
        "path": str(path),
        "name": path.name,
        "stem": path.stem,
        "extension": path.suffix,
        "is_file": path.is_file(),
        "is_dir": path.is_dir(),
        "is_symlink": path.is_symlink(),
        "symlink_target": symlink_target,
    }
    try:
        st = path.stat()
        lst = path.lstat()
        base.update(
            {
                "size": st.st_size,
                "modified": st.st_mtime,
                "created": st.st_ctime,
                "accessed": st.st_atime,
                "permissions": oct(st.st_mode)[-3:],
                "permissions_octal": oct(lst.st_mode),
                "inode": st.st_ino,
            }
        )
        if path.is_file() and _FILE_CMD:
            try:
                r = subprocess.run(
                    [_FILE_CMD, "-b", "--mime-type", str(path)],
                    capture_output=True,
                    text=True,
                    timeout=2.0,
                )
                if r.returncode == 0 and r.stdout.strip():
                    base["mime_type"] = r.stdout.strip()
            except Exception:
                pass
        if path.is_file() and not _editor._is_binary(path):
            try:
                with open(path, "rb") as f:
                    base["line_count"] = sum(1 for _ in f)
            except OSError:
                pass
    except OSError as exc:
        base.update({"stat_error": str(exc), "size": 0})
    return base


@_timed
def create_dir(file_path: str, parents: bool = True) -> dict[str, Any]:
    """Create a directory, optionally creating all intermediate parents."""
    path = _editor._validate_path(file_path, allow_write=True)
    if not path:
        return {"success": False, "error": "Invalid or disallowed directory path"}
    if path.exists():
        return {
            "success": False,
            "error": f"Path already exists: {file_path}",
            "is_dir": path.is_dir(),
        }
    try:
        path.mkdir(parents=parents, exist_ok=False)
        return {"success": True, "path": str(path), "is_dir": True}
    except Exception as exc:
        return {"success": False, "error": str(exc)}


@_timed
def list_dir(
    file_path: str,
    include_hidden: bool = False,
    sort_by: str = "name",
    descending: bool = False,
) -> dict[str, Any]:
    """
    List directory contents with per-entry metadata.

    sort_by: 'name' | 'size' | 'modified' | 'type'
    """
    path = _editor._validate_path(file_path, allow_write=False)
    if not path or not path.exists() or not path.is_dir():
        return {"success": False, "error": "Invalid or missing directory"}

    sort_key = _SORT_KEYS.get(sort_by, _SORT_KEYS["name"])
    items: list[dict[str, Any]] = []
    try:
        for item in path.iterdir():
            if not include_hidden and item.name.startswith("."):
                continue
            try:
                st = item.stat()
                lst = item.lstat()
                items.append(
                    {
                        "name": item.name,
                        "path": str(item),
                        "is_file": item.is_file(),
                        "is_dir": item.is_dir(),
                        "is_symlink": item.is_symlink(),
                        "size": st.st_size,
                        "modified": st.st_mtime,
                        "permissions": oct(lst.st_mode)[-3:],
                        "extension": item.suffix if item.is_file() else "",
                    }
                )
            except OSError:
                items.append(
                    {
                        "name": item.name,
                        "path": str(item),
                        "is_file": False,
                        "is_dir": False,
                        "is_symlink": item.is_symlink(),
                        "size": 0,
                        "modified": 0.0,
                        "permissions": "???",
                        "extension": "",
                        "error": "stat failed",
                    }
                )
        items.sort(key=sort_key, reverse=descending)
        return {
            "success": True,
            "path": str(path),
            "items": items,
            "item_count": len(items),
            "dir_count": sum(1 for i in items if i.get("is_dir")),
            "file_count": sum(1 for i in items if i.get("is_file")),
        }
    except OSError as exc:
        return {"success": False, "error": str(exc)}


@_timed
def diff(
    file_path: str,
    target_path: str | None = None,
    encoding: str = DEFAULT_ENCODING,
    context_lines: int = 3,
) -> dict[str, Any]:
    """
    Produce a unified diff between two files or a file and its newest backup.

    If target_path is omitted the most recent timestamped backup is used.
    """
    path = _editor._validate_path(file_path, allow_write=False)
    if not path:
        return {"success": False, "error": "Invalid or disallowed file path"}
    if not path.exists():
        return {"success": False, "error": f"File not found: {file_path}"}
    if not path.is_file():
        return {"success": False, "error": f"Path is not a regular file: {file_path}"}

    cmp_path: Path | None = None
    if target_path:
        cmp_path = _editor._validate_path(target_path, allow_write=False)
        if not cmp_path:
            return {"success": False, "error": "Invalid or disallowed target path"}
        if not cmp_path.exists():
            return {"success": False, "error": f"Target file not found: {target_path}"}
    else:
        backups = _editor._list_backups(path)
        if not backups:
            return {
                "success": False,
                "error": "No backup found; provide target_path or run a mutating op first.",
            }
        cmp_path = backups[0]

    try:
        old_lines = cmp_path.read_text(
            encoding=encoding, errors="surrogateescape"
        ).splitlines(keepends=True)
        new_lines = path.read_text(
            encoding=encoding, errors="surrogateescape"
        ).splitlines(keepends=True)
    except UnicodeDecodeError:
        return {"success": False, "error": f"Encoding error reading files ({encoding})"}
    except OSError as exc:
        return {"success": False, "error": str(exc)}

    diff_lines = list(
        difflib.unified_diff(
            old_lines,
            new_lines,
            fromfile=str(cmp_path),
            tofile=str(path),
            n=context_lines,
        )
    )
    additions = sum(
        1 for ln in diff_lines if ln.startswith("+") and not ln.startswith("+++")
    )
    deletions = sum(
        1 for ln in diff_lines if ln.startswith("-") and not ln.startswith("---")
    )
    return {
        "success": True,
        "path": str(path),
        "target": str(cmp_path),
        "diff": "".join(diff_lines),
        "additions": additions,
        "deletions": deletions,
        "changed": bool(diff_lines),
    }


@_timed
def truncate(
    file_path: str,
    size: int = 0,
    encoding: str = DEFAULT_ENCODING,
    max_backups: int = MAX_BACKUPS,
) -> dict[str, Any]:
    """
    Truncate a file to exactly size bytes (default 0 → empty the file).

    Backup created before truncation.
    """
    try:
        size = int(size)
    except (ValueError, TypeError):
        return {"success": False, "error": f"Invalid size '{size}': must be an integer"}
    if size < 0:
        return {"success": False, "error": "size must be >= 0"}

    path = _editor._validate_path(file_path, allow_write=True)
    if not path:
        return {"success": False, "error": "Invalid or disallowed file path"}
    if not path.exists():
        return {"success": False, "error": f"File not found: {file_path}"}
    if not path.is_file():
        return {"success": False, "error": f"Path is not a regular file: {file_path}"}
    try:
        original_bytes = path.stat().st_size
        backup_path = _editor._make_backup(path, max_backups=max_backups)
        with open(path, "r+b") as fh:
            fh.truncate(size)
        new_bytes = path.stat().st_size
        return {
            "success": True,
            "path": str(path),
            "original_bytes": original_bytes,
            "new_bytes": new_bytes,
            "bytes_delta": new_bytes - original_bytes,
            "backup_path": str(backup_path),
            "encoding": encoding,
        }
    except OSError as exc:
        return {"success": False, "error": str(exc)}


@_timed
def set_permissions(file_path: str, mode: str) -> dict[str, Any]:
    """
    Set POSIX permissions on a file or directory.

    mode accepts: '755', '0o644', '644', etc.  Maximum allowed: 0o7777.
    """
    path = _editor._validate_path(file_path, allow_write=True)
    if not path:
        return {"success": False, "error": "Invalid or disallowed file path"}
    if not path.exists():
        return {"success": False, "error": f"Path not found: {file_path}"}

    mode_str = mode.strip()
    if mode_str.lower().startswith("0o"):
        mode_str = mode_str[2:]
    try:
        mode_int = int(mode_str, 8)
    except ValueError:
        return {
            "success": False,
            "error": f"Invalid mode '{mode}'. Use octal such as '755' or '0o644'.",
        }
    if mode_int > 0o7777:
        return {
            "success": False,
            "error": f"Mode {oct(mode_int)} exceeds maximum 0o7777",
        }
    try:
        original_mode = oct(path.lstat().st_mode)[-4:]
        os.chmod(path, mode_int)
        new_mode = oct(path.lstat().st_mode)[-4:]
        return {
            "success": True,
            "path": str(path),
            "mode": new_mode,
            "original_mode": original_mode,
        }
    except OSError as exc:
        return {"success": False, "error": str(exc)}


@_timed
def normalize_line_endings(
    file_path: str,
    to_type: str,
    encoding: str = DEFAULT_ENCODING,
    max_backups: int = MAX_BACKUPS,
) -> dict[str, Any]:
    """
    Convert all line endings to 'lf' or 'crlf'.

    Uses binary write to prevent Python from applying OS line-end translation.
    """
    if to_type not in ("lf", "crlf"):
        return {
            "success": False,
            "error": f"Invalid to_type '{to_type}'. Must be 'lf' or 'crlf'.",
        }

    res = _editor._read_content(file_path, encoding=encoding)
    if not res["success"]:
        return res
    content: str = res["content"]
    path: Path = res["path"]

    original_bytes = path.stat().st_size
    unified = _normalize_lf(content)
    new_content = unified.replace("\n", "\r\n") if to_type == "crlf" else unified

    if new_content == content:
        return {
            "success": True,
            "path": str(path),
            "to_type": to_type,
            "lines_changed": 0,
            "original_bytes": original_bytes,
            "new_bytes": original_bytes,
            "bytes_delta": 0,
            "backup_path": None,
            "message": "File already uses the target line ending style",
        }

    crlf_count = content.count("\r\n")
    bare_lf = content.count("\n") - crlf_count
    lines_changed = max(0, bare_lf) if to_type == "crlf" else max(0, crlf_count)

    try:
        backup_path = _editor._make_backup(path, max_backups=max_backups)
        raw = new_content.encode(encoding, errors="surrogateescape")
        raw_fd, tmp_name = tempfile.mkstemp(dir=path.parent, prefix=".~tmp_")
        try:
            os.chmod(tmp_name, 0o600)
            with os.fdopen(raw_fd, "wb") as fh:
                fh.write(raw)
                fh.flush()
                os.fsync(fh.fileno())
            os.replace(tmp_name, str(path))
        except Exception:
            try:
                os.unlink(tmp_name)
            except OSError:
                pass
            raise
        new_bytes = path.stat().st_size
        return {
            "success": True,
            "path": str(path),
            "to_type": to_type,
            "lines_changed": lines_changed,
            "original_bytes": original_bytes,
            "new_bytes": new_bytes,
            "bytes_delta": new_bytes - original_bytes,
            "backup_path": str(backup_path),
        }
    except OSError as exc:
        return {"success": False, "error": str(exc)}


@_timed
def revert_to_backup(
    file_path: str,
    backup_timestamp: str | None = None,
    max_backups: int = MAX_BACKUPS,
) -> dict[str, Any]:
    """
    Restore a file from one of its timestamped backups.

    With backup_timestamp=None the most recent backup is used.
    The current file is itself backed up before restoration.
    """
    path = _editor._validate_path(file_path, allow_write=True)
    if not path:
        return {"success": False, "error": "Invalid or disallowed file path"}
    if not path.exists():
        return {"success": False, "error": f"File not found: {file_path}"}
    if not path.is_file():
        return {"success": False, "error": f"Path is not a regular file: {file_path}"}

    backups = _editor._list_backups(path)
    if not backups:
        return {
            "success": False,
            "error": "No backups found; run a mutating operation first.",
        }

    chosen: Path | None = None
    if backup_timestamp is not None:
        ts_clean = backup_timestamp.strip()
        for bk in backups:
            if ts_clean in bk.name:
                chosen = bk
                break
        if chosen is None:
            return {
                "success": False,
                "error": (
                    f"No backup found with timestamp '{backup_timestamp}'. "
                    f"Available: {[bk.name for bk in backups]}"
                ),
            }
    else:
        chosen = backups[0]

    try:
        previous_backup = _editor._make_backup(path, max_backups=max_backups)
        shutil.copy2(chosen, path)
        return {
            "success": True,
            "path": str(path),
            "restored_from": str(chosen),
            "previous_backup": str(previous_backup),
            "available_backups": [str(b) for b in _editor._list_backups(path)],
        }
    except OSError as exc:
        return {"success": False, "error": str(exc)}


# ==============================================================================
# SECTION 7: Extended Operations
# ==============================================================================


@_timed
def grep_dir(
    dir_path: str,
    pattern: str,
    use_regex: bool = False,
    case_sensitive: bool = True,
    include_hidden: bool = False,
    file_pattern: str = "*",
    max_matches: int = 1000,
    line_context: int = 0,
    encoding: str = DEFAULT_ENCODING,
    recursive: bool = True,
) -> dict[str, Any]:
    """
    Recursively search all text files in a directory for a pattern.

    Stops collecting new matches once max_matches is reached.
    """
    if not pattern or not isinstance(pattern, str) or not pattern.strip():
        return {"success": False, "error": "pattern cannot be empty"}

    pattern = pattern.strip()
    root = _editor._validate_path(dir_path, allow_write=False)
    if not root or not root.exists() or not root.is_dir():
        return {"success": False, "error": "Invalid or missing directory"}

    try:
        compiled = _build_regex(pattern, use_regex, case_sensitive)
    except re.error as exc:
        return {"success": False, "error": f"Invalid regex: {exc}"}

    def match_fn(line: str) -> bool:
        return bool(compiled.search(line))

    glob_method = root.rglob if recursive else root.glob
    file_matches: list[dict[str, Any]] = []
    total_matches = 0
    files_searched = 0
    truncated = False

    for fpath in sorted(glob_method(file_pattern)):
        if truncated:
            break
        if not fpath.is_file():
            continue
        if not include_hidden and any(
            part.startswith(".") for part in fpath.relative_to(root).parts
        ):
            continue
        if _editor._is_binary(fpath):
            continue
        files_searched += 1
        file_hits: list[dict[str, Any]] = []
        try:
            lines = fpath.read_text(
                encoding=encoding, errors="surrogateescape"
            ).splitlines()
        except OSError:
            continue
        for i, line in enumerate(lines):
            if match_fn(line):
                if total_matches >= max_matches:
                    truncated = True
                    break
                entry: dict[str, Any] = {"line": i + 1, "content": line}
                if line_context > 0:
                    ctx_s = max(0, i - line_context)
                    ctx_e = min(len(lines), i + line_context + 1)
                    entry["context"] = lines[ctx_s:ctx_e]
                    entry["context_start_line"] = ctx_s + 1
                file_hits.append(entry)
                total_matches += 1
        if file_hits:
            file_matches.append(
                {
                    "file": str(fpath),
                    "match_count": len(file_hits),
                    "matches": file_hits,
                }
            )

    return {
        "success": True,
        "path": str(root),
        "pattern": pattern,
        "file_matches": file_matches,
        "total_matches": total_matches,
        "files_searched": files_searched,
        "truncated": truncated,
    }


@_timed
def file_hash(
    file_path: str,
    algorithm: str = "sha256",
    chunk_size: int = 65_536,
) -> dict[str, Any]:
    """
    Compute a cryptographic hash of a file (streaming, safe for large files).

    algorithm: sha256 | sha1 | sha512 | md5 | blake2b
    """
    algo = algorithm.lower()
    if algo not in _HASH_ALGORITHMS:
        return {
            "success": False,
            "error": f"Unsupported algorithm '{algorithm}'. Choose from: {', '.join(sorted(_HASH_ALGORITHMS))}",
        }
    path = _editor._validate_path(file_path, allow_write=False)
    if not path:
        return {"success": False, "error": "Invalid or disallowed file path"}
    if not path.exists():
        return {"success": False, "error": f"File not found: {file_path}"}
    if not path.is_file():
        return {"success": False, "error": f"Path is not a regular file: {file_path}"}
    try:
        h = hashlib.new(algo)
        size = 0
        with open(path, "rb") as f:
            while True:
                chunk = f.read(chunk_size)
                if not chunk:
                    break
                h.update(chunk)
                size += len(chunk)
        return {
            "success": True,
            "path": str(path),
            "algorithm": algo,
            "hash": h.hexdigest(),
            "size": size,
        }
    except OSError as exc:
        return {"success": False, "error": str(exc)}


@_timed
def word_count(
    file_path: str,
    encoding: str = DEFAULT_ENCODING,
) -> dict[str, Any]:
    """Count lines, words, characters, and bytes in a text file (streaming)."""
    path = _editor._validate_path(file_path, allow_write=False)
    if not path:
        return {"success": False, "error": "Invalid or disallowed file path"}
    if not path.exists():
        return {"success": False, "error": f"File not found: {file_path}"}
    if not path.is_file():
        return {"success": False, "error": f"Path is not a regular file: {file_path}"}
    if _editor._is_binary(path):
        return {"success": False, "error": "Binary file detected"}
    try:
        byte_count = path.stat().st_size
        lines = words = chars = 0
        with open(path, encoding=encoding, errors="surrogateescape") as f:
            for line in f:
                lines += 1
                words += len(line.split())
                chars += len(line)
        return {
            "success": True,
            "path": str(path),
            "lines": lines,
            "words": words,
            "characters": chars,
            "bytes": byte_count,
        }
    except Exception as exc:
        return {"success": False, "error": str(exc)}


@_timed
def find_files(
    dir_path: str,
    name_pattern: str = "*",
    use_regex: bool = False,
    include_hidden: bool = False,
    min_size: int | None = None,
    max_size_filter: int | None = None,
    modified_after: float | None = None,
    modified_before: float | None = None,
    file_type: str = "any",
    recursive: bool = True,
    max_results: int = MAX_FIND_RESULTS,
) -> dict[str, Any]:
    """
    Discover files/directories matching flexible criteria.

    file_type: 'any' | 'file' | 'dir'
    Size filters apply only to files (not directories).
    """
    root = _editor._validate_path(dir_path, allow_write=False)
    if not root or not root.exists() or not root.is_dir():
        return {"success": False, "error": "Invalid or missing directory"}
    if file_type not in ("any", "file", "dir"):
        return {"success": False, "error": "file_type must be 'any', 'file', or 'dir'"}

    if use_regex:
        try:
            name_re = re.compile(name_pattern)
        except re.error as exc:
            return {"success": False, "error": f"Invalid regex: {exc}"}

        def name_match(n: str) -> bool:
            return bool(name_re.search(n))
    else:

        def name_match(n: str) -> bool:  # type: ignore[misc]
            return fnmatch.fnmatch(n, name_pattern)

    glob_method = root.rglob if recursive else root.glob
    results: list[dict[str, Any]] = []
    truncated = False

    for entry in sorted(glob_method("*")):
        if not include_hidden and any(
            part.startswith(".") for part in entry.relative_to(root).parts
        ):
            continue
        if not name_match(entry.name):
            continue
        if file_type == "file" and not entry.is_file():
            continue
        if file_type == "dir" and not entry.is_dir():
            continue
        try:
            st = entry.stat()
        except OSError:
            continue
        if entry.is_file():
            if min_size is not None and st.st_size < min_size:
                continue
            if max_size_filter is not None and st.st_size > max_size_filter:
                continue
        if modified_after is not None and st.st_mtime < modified_after:
            continue
        if modified_before is not None and st.st_mtime > modified_before:
            continue
        if len(results) >= max_results:
            truncated = True
            break
        results.append(
            {
                "path": str(entry),
                "name": entry.name,
                "is_file": entry.is_file(),
                "is_dir": entry.is_dir(),
                "size": st.st_size,
                "modified": st.st_mtime,
            }
        )

    return {
        "success": True,
        "path": str(root),
        "results": results,
        "result_count": len(results),
        "truncated": truncated,
    }


@_timed
def head(
    file_path: str,
    n: int = 10,
    encoding: str = DEFAULT_ENCODING,
) -> dict[str, Any]:
    """Read the first n lines of a file (streaming, memory efficient)."""
    if n < 1:
        return {"success": False, "error": "n must be >= 1"}
    path = _editor._validate_path(file_path, allow_write=False)
    if not path or not path.exists() or not path.is_file():
        return {"success": False, "error": "Invalid or missing file"}
    if _editor._is_binary(path):
        return {"success": False, "error": "Binary file detected"}
    try:
        selected: list[str] = []
        with open(path, encoding=encoding, errors="surrogateescape") as f:
            for i, line in enumerate(f):
                if i >= n:
                    break
                selected.append(line.rstrip("\n"))
        return {
            "success": True,
            "path": str(path),
            "lines": selected,
            "content": "\n".join(selected),
            "line_count": len(selected),
            "n": n,
        }
    except Exception as exc:
        return {"success": False, "error": str(exc)}


@_timed
def tail(
    file_path: str,
    n: int = 10,
    encoding: str = DEFAULT_ENCODING,
) -> dict[str, Any]:
    """Read the last n lines of a file using a circular buffer (O(n) memory)."""
    if n < 1:
        return {"success": False, "error": "n must be >= 1"}
    path = _editor._validate_path(file_path, allow_write=False)
    if not path or not path.exists() or not path.is_file():
        return {"success": False, "error": "Invalid or missing file"}
    if _editor._is_binary(path):
        return {"success": False, "error": "Binary file detected"}
    try:
        buf: deque[str] = deque(maxlen=n)
        with open(path, encoding=encoding, errors="surrogateescape") as f:
            for line in f:
                buf.append(line.rstrip("\n"))
        selected = list(buf)
        return {
            "success": True,
            "path": str(path),
            "lines": selected,
            "content": "\n".join(selected),
            "line_count": len(selected),
            "n": n,
        }
    except Exception as exc:
        return {"success": False, "error": str(exc)}


@_timed
def compare_files(
    file_path: str,
    target_path: str,
    mode: str = "bytes",
    encoding: str = DEFAULT_ENCODING,
) -> dict[str, Any]:
    """
    Compare two files for equality.

    mode='bytes' → byte-exact comparison, reports first differing byte offset.
    mode='text'  → normalized line-ending comparison, reports first differing line.
    """
    if mode not in ("bytes", "text"):
        return {"success": False, "error": "mode must be 'bytes' or 'text'"}
    src = _editor._validate_path(file_path, allow_write=False)
    dst = _editor._validate_path(target_path, allow_write=False)
    if not src or not dst:
        return {"success": False, "error": "Invalid or disallowed path(s)"}
    if not src.exists() or not src.is_file():
        return {"success": False, "error": f"File not found: {file_path}"}
    if not dst.exists() or not dst.is_file():
        return {"success": False, "error": f"File not found: {target_path}"}
    try:
        size_a = src.stat().st_size
        size_b = dst.stat().st_size
        if mode == "bytes":
            chunk = 65_536
            offset = 0
            equal = True
            first_diff: int | None = None
            with open(src, "rb") as fa, open(dst, "rb") as fb:
                while True:
                    ca = fa.read(chunk)
                    cb = fb.read(chunk)
                    if ca != cb:
                        equal = False
                        for i, (ba, bb) in enumerate(zip(ca, cb)):
                            if ba != bb:
                                first_diff = offset + i
                                break
                        else:
                            first_diff = offset + min(len(ca), len(cb))
                        break
                    offset += len(ca)
                    if not ca:
                        break
            result: dict[str, Any] = {
                "success": True,
                "path": str(src),
                "target": str(dst),
                "equal": equal,
                "size_a": size_a,
                "size_b": size_b,
                "mode": "bytes",
            }
            if not equal:
                result["first_difference_byte"] = first_diff
            return result
        else:
            text_a = src.read_text(encoding=encoding, errors="surrogateescape")
            text_b = dst.read_text(encoding=encoding, errors="surrogateescape")
            norm_a = _normalize_lf(text_a)
            norm_b = _normalize_lf(text_b)
            equal = norm_a == norm_b
            result = {
                "success": True,
                "path": str(src),
                "target": str(dst),
                "equal": equal,
                "size_a": size_a,
                "size_b": size_b,
                "mode": "text",
            }
            if not equal:
                lines_a = norm_a.splitlines()
                lines_b = norm_b.splitlines()
                for i, (la, lb) in enumerate(zip(lines_a, lines_b), 1):
                    if la != lb:
                        result["first_difference_line"] = i
                        break
                else:
                    result["first_difference_line"] = (
                        min(len(lines_a), len(lines_b)) + 1
                    )
            return result
    except Exception as exc:
        return {"success": False, "error": str(exc)}


@_timed
def archive(
    source_path: str,
    archive_path: str,
    compression: str = "deflate",
    recursive: bool = True,
) -> dict[str, Any]:
    """
    Create a ZIP archive from a file or directory.

    compression: 'deflate' | 'store' | 'bz2' | 'lzma'
    '.zip' extension appended automatically if absent.
    """
    _COMPRESS_MAP: dict[str, int] = {
        "deflate": zipfile.ZIP_DEFLATED,
        "store": zipfile.ZIP_STORED,
        "bz2": zipfile.ZIP_BZIP2,
        "lzma": zipfile.ZIP_LZMA,
    }
    if compression not in _COMPRESS_MAP:
        return {
            "success": False,
            "error": f"Unknown compression '{compression}'. Choose: {', '.join(_COMPRESS_MAP)}",
        }
    src = _editor._validate_path(source_path, allow_write=False)
    arc = _editor._validate_path(archive_path, allow_write=True)
    if not src or not arc:
        return {"success": False, "error": "Invalid or disallowed path(s)"}
    if not src.exists():
        return {"success": False, "error": f"Source not found: {source_path}"}
    if not (src.is_file() or src.is_dir()):
        return {
            "success": False,
            "error": f"Source is not a file or directory: {source_path}",
        }
    if not str(arc).endswith(".zip"):
        arc = arc.parent / (arc.name + ".zip")

    method = _COMPRESS_MAP[compression]
    files_added = 0
    uncompressed = 0
    try:
        with zipfile.ZipFile(arc, "w", compression=method) as zf:
            if src.is_file():
                zf.write(src, src.name)
                files_added = 1
                uncompressed = src.stat().st_size
            else:
                glob_fn = src.rglob("*") if recursive else src.glob("*")
                for fpath in sorted(glob_fn):
                    if fpath.is_file():
                        arcname = fpath.relative_to(src.parent)
                        zf.write(fpath, arcname)
                        files_added += 1
                        uncompressed += fpath.stat().st_size
        compressed = arc.stat().st_size
        ratio = round(1 - compressed / uncompressed, 4) if uncompressed > 0 else 0.0
        return {
            "success": True,
            "source": str(src),
            "archive": str(arc),
            "files_added": files_added,
            "uncompressed_bytes": uncompressed,
            "compressed_bytes": compressed,
            "ratio": ratio,
        }
    except Exception as exc:
        return {"success": False, "error": str(exc)}


@_timed
def extract(
    archive_path: str,
    target_path: str,
    password: str | None = None,
) -> dict[str, Any]:
    """
    Extract a ZIP archive to a target directory.

    Validates all member paths to prevent zip-slip attacks.
    Enforces MAX_ARCHIVE_SIZE against decompression bombs.
    """
    arc = _editor._validate_path(archive_path, allow_write=False)
    dst = _editor._validate_path(target_path, allow_write=True)
    if not arc or not dst:
        return {"success": False, "error": "Invalid or disallowed path(s)"}
    if not arc.exists() or not arc.is_file():
        return {"success": False, "error": f"Archive not found: {archive_path}"}
    if not zipfile.is_zipfile(arc):
        return {"success": False, "error": f"Not a valid ZIP file: {archive_path}"}
    try:
        with zipfile.ZipFile(arc, "r") as zf:
            total_uncompressed = sum(i.file_size for i in zf.infolist())
            if total_uncompressed > MAX_ARCHIVE_SIZE:
                return {
                    "success": False,
                    "error": (
                        f"Archive uncompressed size {total_uncompressed:,} bytes "
                        f"exceeds limit {MAX_ARCHIVE_SIZE:,} bytes"
                    ),
                }
            # Zip-slip protection
            dst_resolved = dst.resolve()
            for member in zf.namelist():
                member_path = (dst / member).resolve()
                try:
                    is_safe = member_path.is_relative_to(dst_resolved)
                except AttributeError:
                    # Python < 3.9
                    is_safe = (
                        str(member_path).startswith(str(dst_resolved) + os.sep)
                        or member_path == dst_resolved
                    )
                if not is_safe:
                    return {
                        "success": False,
                        "error": f"Unsafe archive entry blocked: {member}",
                    }
            pwd = password.encode() if password else None
            dst.mkdir(parents=True, exist_ok=True)
            zf.extractall(dst, pwd=pwd)
            files_extracted = len(
                [i for i in zf.infolist() if not i.filename.endswith("/")]
            )
    except RuntimeError as exc:
        if "Bad password" in str(exc) or "password required" in str(exc):
            return {"success": False, "error": "Incorrect archive password"}
        raise
    except Exception as exc:
        return {"success": False, "error": str(exc)}
    return {
        "success": True,
        "archive": str(arc),
        "target": str(dst),
        "files_extracted": files_extracted,
        "total_bytes": total_uncompressed,
    }


@_timed
def template_write(
    file_path: str,
    template: str,
    variables: dict[str, str],
    encoding: str = DEFAULT_ENCODING,
    create_parents: bool = True,
    undefined_var: str = "error",
) -> dict[str, Any]:
    """
    Render a {{variable}} template and write it to a file.

    undefined_var controls behaviour for missing keys:
      'error' → fail with list of missing variables (default)
      'keep'  → leave {{ placeholder }} unchanged
      'empty' → replace with empty string
    """
    if template is None:
        return {"success": False, "error": "template cannot be None"}
    if not isinstance(variables, dict):
        return {"success": False, "error": "variables must be a dict"}
    if undefined_var not in ("error", "keep", "empty"):
        return {
            "success": False,
            "error": "undefined_var must be 'error', 'keep', or 'empty'",
        }

    path = _editor._validate_path(file_path, allow_write=True)
    if not path:
        return {"success": False, "error": "Invalid or disallowed file path"}

    found_vars = set(_TEMPLATE_RE.findall(template))
    missing = sorted(found_vars - set(variables.keys()))
    used = sorted(found_vars & set(variables.keys()))

    if missing and undefined_var == "error":
        return {"success": False, "error": f"Undefined template variable(s): {missing}"}

    def _replacer(m: re.Match) -> str:  # type: ignore[type-arg]
        key = m.group(1)
        if key in variables:
            return str(variables[key])
        return "" if undefined_var == "empty" else m.group(0)

    rendered = _TEMPLATE_RE.sub(_replacer, template)
    try:
        _editor._atomic_write(path, rendered, encoding)
        size = path.stat().st_size
        return {
            "success": True,
            "path": str(path),
            "variables_used": used,
            "variables_missing": missing,
            "size": size,
            "encoding": encoding,
        }
    except Exception as exc:
        return {"success": False, "error": str(exc)}


# ==============================================================================
# SECTION 8: Operation registry
# ==============================================================================

_ALL_OPERATIONS: frozenset[str] = frozenset(
    {
        "read",
        "read_lines",
        "write",
        "append",
        "replace",
        "insert_line",
        "delete_line",
        "replace_lines",
        "search",
        "copy",
        "move",
        "delete",
        "info",
        "create_dir",
        "list_dir",
        "diff",
        "truncate",
        "set_permissions",
        "normalize_line_endings",
        "revert_to_backup",
        "grep_dir",
        "file_hash",
        "word_count",
        "find_files",
        "head",
        "tail",
        "compare_files",
        "archive",
        "extract",
        "template_write",
        "batch",
        "batch_edit",
    }
)


@_timed
def batch(
    operations: list[dict[str, Any]],
    continue_on_error: bool = False,
) -> dict[str, Any]:
    """
    Execute multiple file operations in sequence.

    With continue_on_error=False (default) the batch stops on first failure.
    With continue_on_error=True all operations are attempted regardless.
    """
    if not operations or not isinstance(operations, list):
        return {"success": False, "error": "operations must be a non-empty list"}

    results: list[dict[str, Any]] = []
    had_error = False

    for i, op_data in enumerate(operations):
        op_name = op_data.get("operation")
        if not op_name:
            err = {
                "success": False,
                "error": f"Operation at index {i} missing 'operation' field",
            }
            results.append({"index": i, "operation": None, "result": err})
            if not continue_on_error:
                return {"success": False, "error": err["error"], "completed": results}
            had_error = True
            continue

        if op_name == "batch":
            err = {"success": False, "error": "Nested batch operations are not allowed"}
            results.append({"index": i, "operation": op_name, "result": err})
            if not continue_on_error:
                return {"success": False, "error": err["error"], "completed": results}
            had_error = True
            continue

        try:
            op_options = EditOptions(**op_data)
            res = _run(op_options)
        except TypeError as exc:
            res = {
                "success": False,
                "error": f"Invalid arguments for '{op_name}': {exc}",
            }

        results.append({"index": i, "operation": op_name, "result": res})

        if not res.get("success"):
            had_error = True
            if not continue_on_error:
                return {
                    "success": False,
                    "error": f"Batch failed at index {i} ({op_name}): {res.get('error')}",
                    "completed": results,
                }

    exit_code = 0 if all(r.get("success") for r in results) else 1
    return {
        "success": not had_error,
        "results": results,
        "count": len(results),
        "failed": sum(1 for r in results if not r["result"].get("success")),
        "exit_code": exit_code,
    }


@_timed
def batch_edit(
    file_path: str,
    edits: list[dict[str, Any]],
    encoding: str = DEFAULT_ENCODING,
    max_backups: int = MAX_BACKUPS,
    continue_on_error: bool = False,
) -> dict[str, Any]:
    """
    Apply multiple sequential edits to a single file atomically.

    All edits operate on an in-memory copy.  A single backup is created before
    any changes; the final result is written atomically in one operation.

    Supported edit operations: replace, insert_line, delete_line,
    replace_lines, append.
    """
    if not edits or not isinstance(edits, list):
        return {"success": False, "error": "edits must be a non-empty list"}

    _ALLOWED_EDIT_OPS = frozenset(
        {"replace", "insert_line", "delete_line", "replace_lines", "append"}
    )

    res = _editor._read_content(file_path, encoding)
    if not res["success"]:
        return res
    content: str = res["content"]
    path: Path = res["path"]
    original_content: str = content

    edit_results: list[dict[str, Any]] = []
    total_replacements = lines_inserted = lines_deleted = 0

    for i, edit in enumerate(edits):
        op = edit.get("operation", "replace")
        if op not in _ALLOWED_EDIT_OPS:
            err_msg = (
                f"Edit {i}: unsupported operation '{op}'. "
                f"Allowed: {sorted(_ALLOWED_EDIT_OPS)}"
            )
            if not continue_on_error:
                return {"success": False, "error": err_msg, "completed": edit_results}
            edit_results.append(
                {"index": i, "operation": op, "success": False, "error": err_msg}
            )
            continue

        try:
            if op == "replace":
                search_text = edit.get("search_text", "")
                replacement = edit.get("replacement", "")
                use_regex = edit.get("use_regex", False)
                global_replace = edit.get("global_replace", True)
                case_sensitive = edit.get("case_sensitive", True)
                if not search_text:
                    raise ValueError("search_text is required for replace")
                compiled = _build_regex(search_text, use_regex, case_sensitive)
                if global_replace:
                    content, count = compiled.subn(replacement, content)
                else:
                    new = compiled.sub(replacement, content, count=1)
                    count = 1 if new != content else 0
                    content = new
                total_replacements += count
                edit_results.append(
                    {
                        "index": i,
                        "operation": op,
                        "success": True,
                        "replacements": count,
                    }
                )

            elif op == "insert_line":
                ln = edit.get("line_number")
                ins_content = edit.get("content", "")
                if ln is None:
                    raise ValueError("line_number is required for insert_line")
                ln = int(ln)
                lines = content.splitlines(keepends=True)
                ln = max(1, min(ln, len(lines) + 1))
                if not ins_content.endswith("\n"):
                    ins_content += "\n"
                lines.insert(ln - 1, ins_content)
                content = "".join(lines)
                lines_inserted += 1
                edit_results.append(
                    {"index": i, "operation": op, "success": True, "line_number": ln}
                )

            elif op == "delete_line":
                ln = edit.get("line_number")
                if ln is None:
                    raise ValueError("line_number is required for delete_line")
                ln = int(ln)
                lines = content.splitlines(keepends=True)
                if ln < 1 or ln > len(lines):
                    raise ValueError(
                        f"Invalid line number {ln} (file has {len(lines)} lines)"
                    )
                deleted = lines.pop(ln - 1)
                content = "".join(lines)
                lines_deleted += 1
                edit_results.append(
                    {
                        "index": i,
                        "operation": op,
                        "success": True,
                        "deleted_line": ln,
                        "deleted_content": deleted.rstrip("\n"),
                    }
                )

            elif op == "replace_lines":
                start_line = edit.get("start_line")
                end_line = edit.get("end_line")
                repl_content = edit.get("content", "")
                if start_line is None or end_line is None:
                    raise ValueError(
                        "start_line and end_line required for replace_lines"
                    )
                start_line = int(start_line)
                end_line = int(end_line)
                lines = content.splitlines(keepends=True)
                total_ln = len(lines)
                if start_line < 1 or end_line < start_line or end_line > total_ln:
                    raise ValueError(
                        f"Invalid range {start_line}-{end_line} (file has {total_ln} lines)"
                    )
                repl_lines = repl_content.splitlines()
                replacement_lines = (
                    [ln + "\n" for ln in repl_lines] if repl_lines else []
                )
                lines = lines[: start_line - 1] + replacement_lines + lines[end_line:]
                content = "".join(lines)
                edit_results.append(
                    {
                        "index": i,
                        "operation": op,
                        "success": True,
                        "start_line": start_line,
                        "end_line": end_line,
                        "lines_replaced": end_line - start_line + 1,
                        "lines_inserted": len(replacement_lines),
                    }
                )

            elif op == "append":
                app_content = edit.get("content", "")
                add_newline = edit.get("add_newline", True)
                if add_newline and content and not content.endswith("\n"):
                    content += "\n"
                content += app_content
                edit_results.append({"index": i, "operation": op, "success": True})

        except (ValueError, re.error) as exc:
            err_msg = f"Edit {i} ({op}): {exc}"
            if not continue_on_error:
                return {"success": False, "error": err_msg, "completed": edit_results}
            edit_results.append(
                {"index": i, "operation": op, "success": False, "error": str(exc)}
            )

    # Only write if content actually changed
    if content == original_content:
        return {
            "success": True,
            "path": str(path),
            "message": "No changes made",
            "edits": edit_results,
            "edits_applied": sum(1 for e in edit_results if e.get("success")),
            "edits_failed": sum(1 for e in edit_results if not e.get("success")),
        }

    try:
        backup = _editor._make_backup(path, max_backups)
        _editor._atomic_write(path, content, encoding)
        original_bytes = len(
            original_content.encode(encoding, errors="surrogateescape")
        )
        new_bytes = path.stat().st_size
        return {
            "success": True,
            "path": str(path),
            "edits": edit_results,
            "edits_applied": sum(1 for e in edit_results if e.get("success")),
            "edits_failed": sum(1 for e in edit_results if not e.get("success")),
            "total_replacements": total_replacements,
            "lines_inserted": lines_inserted,
            "lines_deleted": lines_deleted,
            "original_bytes": original_bytes,
            "new_bytes": new_bytes,
            "bytes_delta": new_bytes - original_bytes,
            "backup_path": str(backup),
        }
    except Exception as exc:
        return {"success": False, "error": f"Failed to write changes: {exc}"}


# ==============================================================================
# SECTION 9: Main dispatcher — run()
# ==============================================================================


def run(options: EditOptions | None = None, **kwargs: Any) -> dict[str, Any]:
    """
    Primary entry point.

    Accepts either a pre-built EditOptions object or keyword arguments that
    will be forwarded to EditOptions.  The 'path' key is accepted as an alias
    for 'file_path'.
    """
    if options is None:
        # Resolve 'path' alias
        if "path" in kwargs and "file_path" not in kwargs:
            kwargs["file_path"] = kwargs.pop("path")
        try:
            options = EditOptions(**kwargs)
        except TypeError as exc:
            return {"success": False, "error": f"Invalid arguments: {exc}"}
    return _run(options)


def _run(options: EditOptions) -> dict[str, Any]:
    """Execute any named file operation using a validated EditOptions object."""
    file_path = options.file_path

    # ------------------------------------------------------------------
    # Validate operation name
    # ------------------------------------------------------------------
    if options.operation not in _ALL_OPERATIONS:
        return {
            "success": False,
            "error": (
                f"Unknown operation '{options.operation}'. "
                f"Valid: {', '.join(sorted(_ALL_OPERATIONS))}"
            ),
        }

    # ------------------------------------------------------------------
    # Special-case: batch (no file_path required, uses ops JSON)
    # ------------------------------------------------------------------
    if options.operation == "batch":
        if not options.ops:
            return {
                "success": False,
                "error": "'ops' (JSON array) is required for 'batch'",
            }
        return batch(options.ops, continue_on_error=options.continue_on_error)

    # ------------------------------------------------------------------
    # Special-case: batch_edit
    # ------------------------------------------------------------------
    if options.operation == "batch_edit":
        if not file_path:
            return {"success": False, "error": "file_path is required for 'batch_edit'"}
        if not options.edits:
            return {
                "success": False,
                "error": "'edits' (JSON array) is required for 'batch_edit'",
            }
        return batch_edit(
            file_path=file_path,
            edits=options.edits,
            encoding=options.encoding,
            max_backups=options.max_backups,
            continue_on_error=options.continue_on_error,
        )

    if not file_path:
        return {"success": False, "error": "file_path (or --file-path) is required"}

    # Unify search_text / pattern
    effective_search: str | None = options.search_text or options.pattern

    # ------------------------------------------------------------------
    # Dispatch table
    # ------------------------------------------------------------------
    operation_dispatch: dict[str, Callable[[], dict[str, Any]]] = {
        "read": lambda: read(
            file_path,
            options.max_size,
            options.encoding,
            options.show_lines,
            options.start_line,
            options.end_line,
        ),
        "read_lines": lambda: read_lines(
            file_path,
            int(options.start_line) if options.start_line is not None else 1,
            int(options.end_line) if options.end_line is not None else 1,
            options.encoding,
        ),
        "write": lambda: write(options),
        "append": lambda: append(
            file_path,
            options.content,  # type: ignore[arg-type]
            options.encoding,
            options.add_newline,
            options.max_write_size,
        ),
        "replace": lambda: replace(options),
        "insert_line": lambda: insert_line(
            file_path,
            options.line_number,  # type: ignore[arg-type]
            options.content,  # type: ignore[arg-type]
            options.encoding,
            options.max_backups,
        ),
        "delete_line": lambda: delete_line(
            file_path,
            options.line_number,  # type: ignore[arg-type]
            options.encoding,
            options.max_backups,
        ),
        "replace_lines": lambda: replace_lines(
            file_path,
            int(options.start_line) if options.start_line is not None else 1,  # type: ignore[arg-type]
            int(options.end_line) if options.end_line is not None else 1,  # type: ignore[arg-type]
            options.content,  # type: ignore[arg-type]
            options.encoding,
            options.max_backups,
            options.dry_run,
        ),
        "search": lambda: file_search(
            file_path,
            effective_search,  # type: ignore[arg-type]
            options.use_regex,
            options.case_sensitive,
            options.line_context,
            options.encoding,
            options.max_matches,
        ),
        "copy": lambda: copy(
            file_path,
            options.target_path,  # type: ignore[arg-type]
            options.preserve_metadata,
            options.recursive,
        ),
        "move": lambda: move(file_path, options.target_path),  # type: ignore[arg-type]
        "delete": lambda: delete(file_path, options.recursive),
        "info": lambda: info(file_path),
        "create_dir": lambda: create_dir(file_path, options.parents),
        "list_dir": lambda: list_dir(
            file_path,
            options.include_hidden,
            options.sort_by,
            options.descending,
        ),
        "diff": lambda: diff(
            file_path,
            options.target_path,
            options.encoding,
            options.context_lines,
        ),
        "truncate": lambda: truncate(
            file_path,
            options.truncate_size,
            options.encoding,
            options.max_backups,
        ),
        "set_permissions": lambda: set_permissions(
            file_path,
            options.mode,  # type: ignore[arg-type]
        ),
        "normalize_line_endings": lambda: normalize_line_endings(
            file_path,
            options.to_type,  # type: ignore[arg-type]
            options.encoding,
            options.max_backups,
        ),
        "revert_to_backup": lambda: revert_to_backup(
            file_path,
            options.backup_timestamp,
            options.max_backups,
        ),
        "grep_dir": lambda: grep_dir(
            file_path,
            effective_search,  # type: ignore[arg-type]
            options.use_regex,
            options.case_sensitive,
            options.include_hidden,
            options.file_pattern,
            options.max_matches,
            options.line_context,
            options.encoding,
            options.recursive,
        ),
        "file_hash": lambda: file_hash(file_path, options.algorithm),
        "word_count": lambda: word_count(file_path, options.encoding),
        "find_files": lambda: find_files(
            file_path,
            options.file_pattern,
            options.use_regex,
            options.include_hidden,
            options.min_size,
            options.max_size_filter,
            options.modified_after,
            options.modified_before,
            options.file_type,
            options.recursive,
            options.max_results,
        ),
        "head": lambda: head(file_path, options.n_lines, options.encoding),
        "tail": lambda: tail(file_path, options.n_lines, options.encoding),
        "compare_files": lambda: compare_files(
            file_path,
            options.target_path,  # type: ignore[arg-type]
            options.compare_mode,
            options.encoding,
        ),
        "archive": lambda: archive(
            file_path,
            options.target_path,  # type: ignore[arg-type]
            options.compression,
            options.recursive,
        ),
        "extract": lambda: extract(
            file_path,
            options.target_path,  # type: ignore[arg-type]
            options.password,
        ),
        "template_write": lambda: template_write(
            file_path,
            options.content,  # type: ignore[arg-type]
            options.variables or {},
            options.encoding,
            options.create_parents,
            options.undefined_var,
        ),
        # batch_edit is already handled above; keep entry for completeness
        "batch_edit": lambda: batch_edit(
            file_path,
            options.edits or [],
            options.encoding,
            options.max_backups,
            options.continue_on_error,
        ),
    }

    try:
        return operation_dispatch[options.operation]()
    except Exception as exc:
        logger.exception("Dispatcher error for '%s'", options.operation)
        return {"success": False, "error": f"Dispatcher error: {exc}"}


# ==============================================================================
# SECTION 10: CLI
# ==============================================================================


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="edit_file.py",
        description=f"Pyrmethus File Weaver v{__version__}",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=f"""
Operations: {", ".join(sorted(_ALL_OPERATIONS))}

Examples:
  python edit_file.py read myfile.txt
  python edit_file.py head myfile.txt --n-lines 20
  python edit_file.py tail myfile.txt --n-lines 5
  python edit_file.py search myfile.txt --search "TODO"
  python edit_file.py replace myfile.txt --search "foo" --replacement "bar"
  python edit_file.py replace myfile.txt --search "(foo)" --replacement "\\\\1_bar" --regex
  python edit_file.py write myfile.txt --content "Hello, world!"
  python edit_file.py write myfile.txt --content "preview" --dry-run
  python edit_file.py insert_line myfile.txt --line 3 --content "new line"
  python edit_file.py delete_line myfile.txt --line 3
  python edit_file.py replace_lines myfile.txt --start-line 2 --end-line 4 --content "replacement"
  python edit_file.py diff myfile.txt --target other.txt
  python edit_file.py copy myfile.txt --target copy.txt
  python edit_file.py move myfile.txt --target newname.txt
  python edit_file.py delete myfile.txt
  python edit_file.py info myfile.txt
  python edit_file.py list_dir ~/mydir
  python edit_file.py create_dir ~/newdir
  python edit_file.py grep_dir ~/projects --search "TODO" --file-pattern "*.py"
  python edit_file.py file_hash myfile.txt --algorithm sha256
  python edit_file.py word_count myfile.txt
  python edit_file.py find_files ~/docs --file-pattern "*.md"
  python edit_file.py compare_files a.txt b.txt
  python edit_file.py archive ~/myproject --target archive.zip
  python edit_file.py extract archive.zip --target ~/output
  python edit_file.py template_write out.txt --content "Hello {{{{name}}}}!" --var name=World
  python edit_file.py truncate myfile.txt --truncate-size 0
  python edit_file.py set_permissions myfile.txt --mode 644
  python edit_file.py normalize_line_endings myfile.txt --to-type lf
  python edit_file.py revert_to_backup myfile.txt
  python edit_file.py batch_edit myfile.txt --edits '[{{"operation":"replace","search_text":"foo","replacement":"bar"}}]'
        """,
    )
    parser.add_argument(
        "operation", choices=sorted(_ALL_OPERATIONS), help="Operation to perform"
    )
    parser.add_argument("file_path", nargs="?", help="Primary file or directory path")
    parser.add_argument("--target", "-t", dest="target_path", help="Secondary path")
    parser.add_argument(
        "--content", "-c", help="Content string (use '-' to read from stdin)"
    )
    parser.add_argument(
        "--search", "-s", dest="search_text", help="Search text or pattern"
    )
    parser.add_argument("--replacement", "-r", help="Replacement text")
    parser.add_argument("--pattern", "-p", help="Search pattern (alias for --search)")
    parser.add_argument(
        "--line", "-n", dest="line_number", type=int, help="Line number (1-based)"
    )
    parser.add_argument(
        "--start-line", type=int, help="Start line (1-based, inclusive)"
    )
    parser.add_argument("--end-line", type=int, help="End line (1-based, inclusive)")
    parser.add_argument(
        "--regex", action="store_true", dest="use_regex", help="Use regex matching"
    )
    parser.add_argument(
        "--no-global", dest="global_replace", action="store_false", default=True
    )
    parser.add_argument(
        "--case-insensitive", dest="case_sensitive", action="store_false", default=True
    )
    parser.add_argument("--encoding", default=DEFAULT_ENCODING)
    parser.add_argument("--context", dest="line_context", type=int, default=0)
    parser.add_argument("--include-hidden", action="store_true")
    parser.add_argument(
        "--sort", dest="sort_by", default="name", choices=list(_SORT_KEYS)
    )
    parser.add_argument("--descending", action="store_true")
    parser.add_argument(
        "--no-lines", dest="show_lines", action="store_false", default=True
    )
    parser.add_argument("--add-newline", action="store_true", default=False)
    parser.add_argument("--diff-context", dest="context_lines", type=int, default=3)
    parser.add_argument("--truncate-size", type=int, default=0)
    parser.add_argument("--max-backups", type=int, default=MAX_BACKUPS)
    parser.add_argument("--max-matches", type=int, default=1000)
    parser.add_argument("--stdin-timeout", type=float, default=STDIN_TIMEOUT)
    parser.add_argument(
        "--mode", default=None, help="Octal permission mode or compare mode"
    )
    parser.add_argument(
        "--to-type", dest="to_type", choices=["lf", "crlf"], default=None
    )
    parser.add_argument("--backup-timestamp", dest="backup_timestamp", default=None)
    parser.add_argument("--recursive", action="store_true", default=False)
    parser.add_argument("--verbose", "-v", action="store_true")
    parser.add_argument(
        "--algorithm", default="sha256", choices=sorted(_HASH_ALGORITHMS)
    )
    parser.add_argument("--n-lines", type=int, default=10, dest="n_lines")
    parser.add_argument(
        "--compare-mode",
        dest="compare_mode",
        choices=["bytes", "text"],
        default="bytes",
    )
    parser.add_argument(
        "--compression", choices=["deflate", "store", "bz2", "lzma"], default="deflate"
    )
    parser.add_argument("--password", default=None)
    parser.add_argument(
        "--var",
        dest="variables",
        action="append",
        metavar="KEY=VALUE",
        help="Template variable (repeatable)",
    )
    parser.add_argument(
        "--undefined-var",
        dest="undefined_var",
        choices=["error", "keep", "empty"],
        default="error",
    )
    parser.add_argument("--file-pattern", dest="file_pattern", default="*")
    parser.add_argument("--min-size", type=int, default=None, dest="min_size")
    parser.add_argument(
        "--max-size-filter", type=int, default=None, dest="max_size_filter"
    )
    parser.add_argument(
        "--modified-after", type=float, default=None, dest="modified_after"
    )
    parser.add_argument(
        "--modified-before", type=float, default=None, dest="modified_before"
    )
    parser.add_argument(
        "--file-type", dest="file_type", choices=["any", "file", "dir"], default="any"
    )
    parser.add_argument(
        "--max-results", type=int, default=MAX_FIND_RESULTS, dest="max_results"
    )
    parser.add_argument(
        "--edits", default=None, help="JSON array of edits for batch_edit mode"
    )
    parser.add_argument(
        "--continue-on-error",
        action="store_true",
        default=False,
        dest="continue_on_error",
        help="Continue batch/batch_edit on error instead of stopping",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        default=False,
        help="Preview changes without modifying the filesystem",
    )
    return parser


def _parse_variables(raw: list[str] | None) -> dict[str, str]:
    """Parse a list of 'KEY=VALUE' strings into a dict."""
    if not raw:
        return {}
    result: dict[str, str] = {}
    for item in raw:
        if "=" not in item:
            raise ValueError(f"Invalid --var format '{item}'. Expected KEY=VALUE.")
        k, _, v = item.partition("=")
        result[k.strip()] = v
    return result


# ==============================================================================
# SECTION 11: Entry point
# ==============================================================================

if __name__ == "__main__":
    _parser = _build_parser()
    cli = _parser.parse_args()

    # Verbose logging
    if cli.verbose:
        logging.basicConfig(
            level=logging.DEBUG,
            format="%(levelname)s %(name)s: %(message)s",
            stream=sys.stderr,
        )

    # stdin content ingestion
    content_value: str | None = cli.content
    if content_value == "-":
        try:
            import select as _select

            if _select.select([sys.stdin], [], [], cli.stdin_timeout)[0]:
                content_value = sys.stdin.read()
            else:
                print(
                    json.dumps(
                        {
                            "success": False,
                            "error": f"Timed out waiting for stdin ({cli.stdin_timeout}s)",
                        }
                    ),
                    file=sys.stderr,
                )
                sys.exit(1)
        except (ImportError, OSError):
            content_value = sys.stdin.read()

    # Parse --var KEY=VALUE pairs
    try:
        variables = _parse_variables(getattr(cli, "variables", None))
    except ValueError as exc:
        print(json.dumps({"success": False, "error": str(exc)}))
        sys.exit(1)

    # Termux greeting (only when running interactively in Termux)
    if (
        os.environ.get("TERMUX_VERSION")
        or "termux" in os.environ.get("SHELL", "").lower()
        or "com.termux" in os.environ.get("PREFIX", "")
    ):
        _cprint(
            f"✨ Pyrmethus File Weaver v{__version__} — Awakened ✨",
            Fore.CYAN,
            Style.BRIGHT,  # type: ignore[arg-type]
        )
        _cprint(f"   Sanctuary: {Path.home()}", Fore.BLUE)  # type: ignore[arg-type]

    # Load batch ops from file_path if operation is 'batch'
    ops_data: list[dict[str, Any]] = []
    if cli.operation == "batch" and cli.file_path:
        try:
            with open(cli.file_path, encoding="utf-8") as f:
                ops_data = json.load(f)
        except Exception as exc:
            print(
                json.dumps(
                    {"success": False, "error": f"Failed to load batch JSON: {exc}"}
                )
            )
            sys.exit(1)

    # Convert CLI arguments to EditOptions
    cli_dict = vars(cli)
    # The CLI uses `operation` but EditOptions expects `action`
    if "operation" in cli_dict:
        cli_dict["action"] = cli_dict.pop("operation")
    # Build the EditOptions instance
    options = EditOptions(**cli_dict)
    result = _run(options)

    # Human-readable status line
    if result.get("success"):
        _cprint(
            f"✓ Done  ({result.get('duration_ms', 0):.1f} ms)",
            Fore.GREEN,
            Style.BRIGHT,  # type: ignore[arg-type]
        )
    else:
        _cprint(f"✗ {result.get('error', 'Unknown error')}", Fore.RED)  # type: ignore[arg-type]

    print(json.dumps(result, indent=2, ensure_ascii=False))
    sys.exit(0 if result.get("success") else 1)
