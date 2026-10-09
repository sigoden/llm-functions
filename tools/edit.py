#!/usr/bin/env python3
# ==============================================================================
# edit_file.py — Pyrmethus Universal File Weaver v4.0.0-MERGED
# argc/aichat compatible · Termux & Linux · Unified 38-Operation Suite
#
# Merged from edit_file.py v3.0.0-ASCENDED + str_replace_editor.py v2.2.0-ASCENDED
# Incorporates robust encoding/BOM/newline I/O, atomic writes, zip-slip
# protection, stdin support, timeout guards, and unified JSON output.
#
# @describe Unified file editing and directory manipulation tool supporting view,
#           read, write, append, prepend, replace, count, insert, delete, diff,
#           batch operations, regex search, archive, template rendering, line
#           operations, hash, word count, find, head, tail, compare, permissions,
#           line-ending normalization, backup/revert, grep, and truncate.
#
# @option --operation <OPERATION>         Operation: read, view, write, replace, append, prepend, insert_line, delete_line, replace_lines, search, count, copy, move, delete, info, create_dir, list_dir, diff, truncate, set_permissions, normalize_line_endings, revert_to_backup, list_backups, grep_dir, file_hash, word_count, find_files, head, tail, compare_files, archive, extract, template_write, batch, batch_edit, read_lines
# @option --action <ACTION>               Alias for --operation
# @option --file-path <PATH>              Primary target file or directory path
# @option --target <PATH>                 Alias for --file-path
# @option --target-path <PATH>            Secondary target path (copy/move/diff/archive target)
# @option --content <TEXT>                Content to write/append/prepend/insert
# @option --search <TEXT>                 Text or regex pattern to search for
# @option --search-text <TEXT>            Alias for --search
# @option --pattern <PATTERN>             Alias for --search
# @option --replacement <TEXT>            Replacement string for replace operations
# @option --edits <JSON_OR_FILE>          JSON array string or file path for batch_edit mode
# @option --ops <JSON_OR_FILE>            JSON array string or file path for batch mode
# @option --line <NUM>                    Line number (1-based) for line operations
# @option --line-number <NUM>             Alias for --line
# @option --start-line <NUM>              Start line (1-based) for range operations
# @option --end-line <NUM>                End line for range operations
# @option --encoding <ENC>                File encoding (default: auto-detect, fallback utf-8)
# @option --max-size <NUM>                Max read size in bytes
# @option --max-write-size <NUM>          Max write size in bytes
# @option --max-file-bytes <NUM>          Maximum allowed file size guard
# @option --max-replacements <NUM>        Limit replacements (0=none, negative=unlimited)
# @option --max-backups <NUM>             Maximum backup count (default: 15)
# @option --max-matches <NUM>             Maximum search matches (default: 1000)
# @option --context-lines <NUM>           Context lines for diff / search
# @option --line-context <NUM>            Alias for --context-lines
# @option --truncate-size <NUM>           Size for truncate operation
# @option --mode <MODE>                   Octal permission mode (e.g. 644, 755)
# @option --to-type <TYPE>                Line ending conversion target: lf or crlf
# @option --backup-timestamp <TS>         Backup timestamp for revert operation
# @option --algorithm <ALG>               Hash algorithm (sha256/sha1/sha512/md5/blake2b/sha3_256/sha3_512)
# @option --n-lines <NUM>                 Line count for head/tail operations
# @option --compare-mode <MODE>           Compare mode: bytes or text
# @option --compression <TYPE>            Archive compression: deflate, store, bz2, lzma
# @option --password <PASS>               Archive password for extraction
# @option --undefined-var <MODE>          Template undefined variable rule: error/keep/empty
# @option --file-pattern <GLOB>           File glob pattern filter (default: *)
# @option --exclude-pattern <GLOB>        Exclude glob pattern for grep/find
# @option --min-size <NUM>                Minimum file size filter
# @option --max-size-filter <NUM>         Maximum file size filter
# @option --modified-after <TIMESTAMP>    Modified after (Unix timestamp)
# @option --modified-before <TIMESTAMP>   Modified before (Unix timestamp)
# @option --file-type <TYPE>              File type filter: any/file/dir
# @option --max-results <NUM>             Maximum search results limit
# @option --max-depth <NUM>               Maximum recursion depth (0=unlimited)
# @option --sort-by <FIELD>               Directory sort field (name/size/modified/type)
# @option --var <KEY=VALUE>               Template variable (repeatable)
# @option --env-var <KEY=VALUE>           Custom environment variable override (repeatable)
# @option --timeout <SECONDS>             Operation timeout in seconds (default: 300)
# @flag   --regex                         Use regex for pattern matching
# @flag   --use-regex                     Alias for --regex
# @flag   --ignore-case                   Enable case-insensitive matching
# @flag   --case-insensitive              Alias for --ignore-case
# @flag   --no-global                     Replace only first match occurrence
# @flag   --decode-escapes                Decode escape sequences (\n, \t, \r, \xHH, \uXXXX)
# @flag   --dotall                        Treat . in regex as matching newlines (re.DOTALL)
# @flag   --preserve-newlines             Preserve original line ending style (default)
# @flag   --no-preserve-newlines          Do not preserve original line ending style
# @flag   --backup                        Create timestamped .bak backup file before editing
# @flag   --no-backup                     Disable automatic backup creation
# @flag   --dry-run                       Simulate mutations without writing to disk
# @flag   --show-lines                    Include line array in read output
# @flag   --no-lines                      Suppress line array in read output
# @flag   --add-newline                   Append newline to written content
# @flag   --no-add-newline                Do not append trailing newline
# @flag   --include-hidden                Include hidden files and dot-directories
# @flag   --descending                    Sort directory listing in descending order
# @flag   --parents                       Create parent directories when needed
# @flag   --recursive                     Process directories recursively
# @flag   --preserve-metadata             Preserve metadata on file copy
# @flag   --continue-on-error             Continue batch execution on error
# @flag   --schema                        Print JSON Tool Schema for LLM registration and exit
# @flag   --no-color                      Disable ANSI color output
# @flag   --verbose                       Enable detailed debug logging
# @flag   --stdin                         Read content from stdin
# @flag   --force                         Overwrite without confirmation
# @flag   --verify-after-write            Verify file after write operations
# @flag   --return-diff                    Include mutation diff when available
# @flag   --strict-mode                    Enable stricter validation
# @flag   --transactional                  Enable rollback protection
# @flag   --version                       Print version and exit
#
# @env LLM_OUTPUT=/dev/stdout             Output path for LLM integration
# @env LLM_TOOL_MAX_FILE_BYTES=268435456  Default maximum file size guard in bytes
# @env LLM_TOOL_MAX_DIFF_LINES=30         Maximum diff lines shown in JSON/UI output
# @env LLM_TOOL_ALLOW_ALL_PATHS=0         Allow paths outside sandbox when truthy
# @env LLM_TOOL_UI=auto                   Show human UI on TTY; set 0/false to disable, 1/true to force
# ==============================================================================
from __future__ import annotations

import argparse
import codecs
import difflib
import enum
import fnmatch
import functools
import glob as _glob_module
import hashlib
import io
import json
import logging
import os
import platform
import re
import select
import shutil
import signal
import stat
import subprocess
import sys
import tempfile
import threading
import time
import zipfile
from collections import OrderedDict, deque
from contextlib import contextmanager
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import (
    Any,
    Callable,
    Dict,
    List,
    Literal,
    Optional,
    Pattern,
    Tuple,
    Union,
)

__version__ = "4.2.0-ENTERPRISE"
SCHEMA_VERSION = "4.2"

# ==============================================================================
# SECTION 1: Exit Codes, Constants & Exception Models
# ==============================================================================

EXIT_SUCCESS = 0
EXIT_ERROR = 1
EXIT_FILE_NOT_FOUND = 2
EXIT_INVALID_INPUT = 3
EXIT_PERMISSION_DENIED = 126
EXIT_INTERRUPTED = 130
EXIT_TIMEOUT = 124

DEFAULT_MAX_READ = 10_485_760          # 10 MiB
DEFAULT_MAX_WRITE = 104_857_600        # 100 MiB
DEFAULT_MAX_FILE_BYTES = 256 * 1024 * 1024
DEFAULT_ENCODING = "utf-8"
DEFAULT_TIMEOUT = 300.0
MAX_BACKUPS = 15
BINARY_CHECK_BYTES = 32_768
STDIN_TIMEOUT = 30.0
MAX_PATH_LENGTH = 4096
MAX_ARCHIVE_SIZE = 524_288_000         # 500 MiB
MAX_FIND_RESULTS = 10_000
STALE_LOCK_SECONDS = 30.0
CHUNK_SIZE = 65_536

_SIMPLE_ESCAPES: Dict[str, str] = {
    "n": "\n", "r": "\r", "t": "\t", "b": "\b",
    "f": "\f", "v": "\v", "0": "\0", "\\": "\\",
    '"': '"', "'": "'",
}
_HEX_CHARS = set("0123456789abcdefABCDEF")
_OCTAL_CHARS = set("01234567")

_BOM_TABLE: Tuple[Tuple[bytes, str], ...] = (
    (b"\xff\xfe\x00\x00", "utf-32-le"),
    (b"\x00\x00\xfe\xff", "utf-32-be"),
    (b"\xef\xbb\xbf",     "utf-8-sig"),
    (b"\xff\xfe",         "utf-16-le"),
    (b"\xfe\xff",         "utf-16-be"),
)

PSEUDO_FS_PREFIXES: tuple[str, ...] = (
    "/proc", "/sys", "/dev", "/system", "/vendor",
)

_DIRECT_STDOUT_TARGETS = {"/dev/stdout", "/dev/fd/1", "-", "stdout"}
_DIRECT_STDERR_TARGETS = {"/dev/stderr", "/dev/fd/2", "stderr"}

_HASH_ALGORITHMS: frozenset[str] = frozenset(
    {"sha256", "sha1", "sha512", "md5", "blake2b", "sha3_256", "sha3_512"}
)

_TEMPLATE_RE = re.compile(r"\{\{\s*([a-zA-Z0-9_.-]+)\s*\}\}")

_TEXT_MIME_PREFIXES: tuple[str, ...] = (
    "text/", "application/json", "application/xml", "application/javascript",
    "application/ecmascript", "application/x-shellscript", "application/x-python",
    "application/x-perl", "application/x-ruby", "application/x-httpd-php",
    "application/xhtml+xml", "application/x-empty", "inode/x-empty",
    "application/toml", "application/yaml", "application/x-yaml",
    "application/svg+xml", "application/csv", "application/sql",
    "application/x-subrip", "application/x-tex", "application/rtf",
    "application/x-ndjson", "application/graphql", "application/x-typescript",
)

_FILE_CMD: Optional[str] = shutil.which("file")
_MIME_CACHE: dict[str, Optional[str]] = {}

VALID_OPERATIONS = frozenset({
    "read", "view", "write", "replace", "replace_all", "patch", "edit_file", "append", "prepend",
    "insert_line", "insert", "delete_line", "replace_lines",
    "search", "file_search", "count",
    "copy", "move", "delete",
    "info", "create_dir", "list_dir",
    "diff", "truncate", "set_permissions",
    "normalize_line_endings", "revert_to_backup", "list_backups",
    "grep_dir", "file_hash", "word_count", "find_files",
    "head", "tail", "compare_files",
    "archive", "extract", "template_write",
    "batch", "batch_edit", "read_lines",
})


class ToolError(Exception):
    """Base exception for tool errors with exit code."""
    def __init__(self, message: str, exit_code: int = EXIT_ERROR):
        super().__init__(message)
        self.exit_code = exit_code


class ToolJSONEncoder(json.JSONEncoder):
    """Custom JSON encoder handling Path, Enum, datetime, timedelta, bytes, and sets."""
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
            return list(obj)
        if isinstance(obj, float) and (obj != obj or obj == float("inf") or obj == float("-inf")):
            return None
        return super().default(obj)


# ==============================================================================
# SECTION 2: Color Palette & Formatting Helpers
# ==============================================================================

NEON_CYAN    = "\033[38;5;51m"
NEON_GREEN   = "\033[38;5;46m"
NEON_RED     = "\033[38;5;196m"
NEON_YELLOW  = "\033[38;5;226m"
NEON_PURPLE  = "\033[38;5;129m"
NEON_PINK    = "\033[38;5;198m"
NEON_BLUE    = "\033[38;5;39m"
NEON_ORANGE  = "\033[38;5;208m"
RESET        = "\033[0m"
BOLD         = "\033[1m"
DIM          = "\033[2m"

_ANSI_RE = re.compile(r"\x1b(?:[@-Z\\-_]|\[[0-9;]*[ -/]*[@-~])|\033\[[0-9;]*[a-zA-Z]")


def _strip_ansi(text: str) -> str:
    """Remove ANSI color escape sequences."""
    return _ANSI_RE.sub("", text)


def _env_truth(name: str, default: str = "") -> bool:
    """Return True when an environment variable has a truthy value."""
    val = os.environ.get(name, default).strip().lower()
    return val not in ("", "0", "false", "no", "off", "none")


def _is_tty() -> bool:
    """Return True if stderr is attached to an interactive terminal."""
    try:
        return sys.stderr.isatty() and os.environ.get("TERM", "").lower() not in ("dumb", "")
    except Exception:
        return False


def _llm_output_is_stderr() -> bool:
    """Return True when LLM_OUTPUT is routed directly to stderr."""
    out_path = (os.environ.get("LLM_OUTPUT", "/dev/stdout") or "/dev/stdout").strip().lower()
    return out_path in _DIRECT_STDERR_TARGETS


def _use_color(no_color: bool = False) -> bool:
    """Determine whether ANSI colors should be emitted."""
    if no_color or os.environ.get("NO_COLOR") is not None:
        return False
    if _env_truth("FORCE_COLOR"):
        return True
    return _is_tty()


def _display_ui(no_color: bool = False) -> bool:
    """Determine whether the human-readable UI should be displayed."""
    ui_env = os.environ.get("LLM_TOOL_UI")
    if ui_env is not None:
        return _env_truth("LLM_TOOL_UI")
    if _llm_output_is_stderr():
        return False
    if _env_truth("FORCE_COLOR"):
        return True
    return _is_tty()


def _cprint(text: str, file: Any = None, no_color: bool = False) -> None:
    """Print pre-formatted ANSI text to stderr safely."""
    target = file or sys.stderr
    if not _use_color(no_color):
        text = _strip_ansi(text)
    print(text, file=target, flush=True)


def _preview_text(value: Any, limit: int = 80) -> str:
    """Return a safe repr preview for UI output."""
    text = repr("" if value is None else value)
    if len(text) > limit:
        return text[: max(0, limit - 3)] + "..."
    return text


def _unescape_string(text: str) -> str:
    """Convert literal escape sequences into actual characters safely."""
    if not text:
        return text
    out: List[str] = []
    i = 0
    n = len(text)
    while i < n:
        ch = text[i]
        if ch != "\\":
            out.append(ch)
            i += 1
            continue
        if i + 1 >= n:
            out.append("\\")
            break
        nxt = text[i + 1]
        # Octal escapes
        if nxt in _OCTAL_CHARS:
            digits = ""
            j = i + 1
            while j < n and len(digits) < 3 and text[j] in _OCTAL_CHARS:
                digits += text[j]
                j += 1
            try:
                val = int(digits, 8)
                if 0 <= val <= 0o377:
                    out.append(chr(val))
                    i = j
                    continue
            except ValueError:
                pass
        if nxt in _SIMPLE_ESCAPES:
            out.append(_SIMPLE_ESCAPES[nxt])
            i += 2
            continue
        if nxt == "x" and i + 4 <= n:
            hex_digits = text[i + 2 : i + 4]
            if len(hex_digits) == 2 and all(c in _HEX_CHARS for c in hex_digits):
                try:
                    out.append(chr(int(hex_digits, 16)))
                    i += 4
                    continue
                except (ValueError, OverflowError):
                    pass
        if nxt == "u" and i + 6 <= n:
            hex_digits = text[i + 2 : i + 6]
            if len(hex_digits) == 4 and all(c in _HEX_CHARS for c in hex_digits):
                try:
                    out.append(chr(int(hex_digits, 16)))
                    i += 6
                    continue
                except (ValueError, OverflowError):
                    pass
        if nxt == "U" and i + 10 <= n:
            hex_digits = text[i + 2 : i + 10]
            if len(hex_digits) == 8 and all(c in _HEX_CHARS for c in hex_digits):
                try:
                    out.append(chr(int(hex_digits, 16)))
                    i += 10
                    continue
                except (ValueError, OverflowError):
                    pass
        out.append("\\")
        out.append(nxt)
        i += 2
    return "".join(out)


def _generate_diff(old_content: str, new_content: str, file_path: str, max_lines: int = 30) -> str:
    """Generate unified diff summary between old and new content."""
    if old_content == new_content:
        return ""
    try:
        max_lines = int(os.environ.get("LLM_TOOL_MAX_DIFF_LINES", max_lines))
    except Exception:
        pass
    old_lines = old_content.splitlines(keepends=True)
    new_lines = new_content.splitlines(keepends=True)
    diff_lines = list(
        difflib.unified_diff(
            old_lines, new_lines,
            fromfile=f"a/{file_path}", tofile=f"b/{file_path}", n=3
        )
    )
    if not diff_lines:
        return ""
    if max_lines > 0 and len(diff_lines) > max_lines:
        return (
            "".join(diff_lines[:max_lines])
            + f"\n... ({len(diff_lines) - max_lines} more diff lines truncated)\n"
        )
    return "".join(diff_lines)


def _as_bool(value: Any, default: bool = False) -> bool:
    """Best-effort boolean coercion."""
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    if isinstance(value, str):
        return value.strip().lower() in ("1", "true", "yes", "on", "y", "t")
    return default


def _as_int(value: Any, default: Optional[int] = None) -> Optional[int]:
    """Best-effort integer coercion."""
    if value is None:
        return default
    try:
        return int(value)
    except Exception:
        return default


# ==============================================================================
# SECTION 3: Encoding, BOM, Newline Detection & Robust I/O
# ==============================================================================

def _normalize_encoding_name(encoding: Optional[str]) -> str:
    if not encoding:
        return "utf-8"
    return str(encoding).strip().lower().replace("_", "-")


def _validate_encoding(encoding: Optional[str]) -> Optional[str]:
    if encoding is None:
        return None
    try:
        return codecs.lookup(encoding).name
    except LookupError as exc:
        raise ValueError(f"Unknown encoding: {encoding}") from exc


def _is_null_text_encoding(encoding: Optional[str]) -> bool:
    enc = _normalize_encoding_name(encoding)
    return enc.startswith("utf-16") or enc.startswith("utf-32")


def _is_utf_family(encoding: Optional[str]) -> bool:
    enc = _normalize_encoding_name(encoding)
    return enc.startswith(("utf-8", "utf-16", "utf-32"))


def _detect_bom_encoding(data: bytes) -> Tuple[Optional[str], int]:
    for bom, enc in _BOM_TABLE:
        if data.startswith(bom):
            return enc, len(bom)
    return None, 0


def _strip_bom_char(text: str) -> str:
    if text.startswith("\ufeff"):
        return text[1:]
    return text


def _should_prefer_bom(declared_encoding: Optional[str], bom_encoding: Optional[str]) -> bool:
    if not bom_encoding:
        return False
    if not declared_encoding:
        return True
    decl = _normalize_encoding_name(declared_encoding)
    bom = _normalize_encoding_name(bom_encoding)
    if decl == bom:
        return True
    if bom == "utf-8-sig" and decl in {"utf-8", "utf-8-sig"}:
        return True
    if bom.startswith("utf-16") and decl.startswith("utf-16"):
        return True
    if bom.startswith("utf-32") and decl.startswith("utf-32"):
        return True
    return False


def _detect_newline_style(data: bytes) -> str:
    if b"\r\n" in data:
        return "\r\n"
    if b"\r" in data:
        return "\r"
    if b"\n" in data:
        return "\n"
    return ""


def _detect_newline_style_text(text: str) -> str:
    if "\r\n" in text:
        return "\r\n"
    if "\r" in text:
        return "\r"
    if "\n" in text:
        return "\n"
    return ""


def _normalize_newlines(text: str) -> str:
    if not text:
        return text
    return text.replace("\r\n", "\n").replace("\r", "\n")


def _denormalize_newlines(text: str, style: str) -> str:
    if not text:
        return text
    text = _normalize_newlines(text)
    if style in ("\r\n", "\r"):
        return text.replace("\n", style)
    return text


def _prepare_normalized_write_text(normalized_text: str, newline_style: str, preserve_newlines: bool) -> str:
    if preserve_newlines and newline_style:
        return _denormalize_newlines(normalized_text, newline_style)
    return normalized_text


def _looks_binary_bytes(data: bytes) -> bool:
    if not data:
        return False
    sample = data[:8192]
    if b"\x00" in sample:
        return True
    control_chars = sum(1 for b in sample if b < 32 and b not in (9, 10, 11, 12, 13))
    return len(sample) > 0 and (control_chars / len(sample)) > 0.15


def _decode_bytes_strict(data: bytes, encoding: str) -> str:
    text = data.decode(encoding)
    return _strip_bom_char(text)


def _read_file_text(
    file_path: Path, declared_encoding: Optional[str] = None
) -> Tuple[str, str, bool, str, int, bool]:
    """Read a file as text with robust encoding detection.
    Returns: (normalized_text, encoding_used, is_binary, newline_style, file_size, bom_present)
    """
    data = file_path.read_bytes()
    file_size = len(data)
    byte_newline_style = _detect_newline_style(data)
    bom_encoding, bom_length = _detect_bom_encoding(data)
    raw_bom_present = bom_length > 0

    if not data:
        return (
            "", _normalize_encoding_name(declared_encoding or "utf-8"),
            False, byte_newline_style, file_size, False,
        )

    candidates: List[str] = []
    declared_norm = _normalize_encoding_name(declared_encoding) if declared_encoding else None
    if declared_norm:
        candidates.append(declared_norm)
    if bom_encoding:
        bom_norm = _normalize_encoding_name(bom_encoding)
        if _should_prefer_bom(declared_norm, bom_norm):
            if bom_norm not in candidates:
                candidates.insert(0, bom_norm)
        elif bom_norm not in candidates:
            candidates.append(bom_norm)
    for enc in ("utf-8", "utf-8-sig"):
        if enc not in candidates:
            candidates.append(enc)

    for enc in candidates:
        try:
            raw_text = _decode_bytes_strict(data, enc)
            binary = False
            if not _is_null_text_encoding(enc) and _looks_binary_bytes(data):
                binary = True
            newline_style = _detect_newline_style_text(raw_text) or byte_newline_style
            bom_present = raw_bom_present and _is_utf_family(enc)
            return (
                _normalize_newlines(raw_text),
                _normalize_encoding_name(enc),
                binary, newline_style, file_size, bom_present,
            )
        except (UnicodeDecodeError, LookupError, ValueError):
            continue

    binary = _looks_binary_bytes(data)
    if not binary:
        for enc in ("cp1252", "latin-1", "iso-8859-1"):
            try:
                raw_text = _decode_bytes_strict(data, enc)
                newline_style = _detect_newline_style_text(raw_text) or byte_newline_style
                return (
                    _normalize_newlines(raw_text),
                    _normalize_encoding_name(enc),
                    False, newline_style, file_size, False,
                )
            except (UnicodeDecodeError, LookupError):
                continue

    try:
        raw_text = data.decode("utf-8", errors="replace")
    except Exception:
        raw_text = data.decode("latin-1", errors="replace")
    newline_style = _detect_newline_style_text(raw_text) or byte_newline_style
    return (
        _normalize_newlines(raw_text), "utf-8", True, newline_style, file_size, False,
    )


def _encode_text(
    content: str,
    encoding: str,
    explicit_encoding: bool = False,
    bom_present: bool = False,
) -> Tuple[bytes, str, bool]:
    """Encode text for writing. Returns (bytes, encoding_used, fell_back_to_utf8)."""
    enc = _normalize_encoding_name(encoding or "utf-8")
    try:
        codecs.lookup(enc)
    except LookupError as exc:
        raise ValueError(f"Unknown encoding: {encoding}") from exc

    text = content
    if text.startswith("\ufeff"):
        text = text[1:]
    if bom_present and _is_utf_family(enc):
        text = "\ufeff" + text

    try:
        return text.encode(enc), enc, False
    except UnicodeEncodeError:
        if explicit_encoding:
            raise
        fallback_text = content
        if fallback_text.startswith("\ufeff"):
            fallback_text = fallback_text[1:]
        if bom_present:
            fallback_text = "\ufeff" + fallback_text
        return fallback_text.encode("utf-8"), "utf-8", True


# ==============================================================================
# SECTION 4: Sandboxed FileEditor & Atomic I/O Engine
# ==============================================================================

class FileEditor:
    """Secure sandboxed file editor with lock management and atomic writes."""

    def __init__(self) -> None:
        self.home: Path = Path.home().resolve()
        self.temp: Path = Path(tempfile.gettempdir()).resolve()
        self._home_str: str = str(self.home) + os.sep
        self._temp_str: str = str(self.temp) + os.sep
        self._termux_home: str = "/data/data/com.termux/files/home" + os.sep

    def _validate_path(self, file_path: str, allow_write: bool = True) -> Optional[Path]:
        """Validate, resolve, and confine path within agent sandbox."""
        if not file_path or not isinstance(file_path, str):
            return None
        if "\x00" in file_path or len(file_path) > MAX_PATH_LENGTH:
            return None
        raw = Path(file_path)
        if ".." in raw.parts:
            return None
        try:
            path = raw.expanduser().resolve(strict=False)
        except (ValueError, OSError):
            return None
        if not self._is_allowed(path):
            return None
        if path.is_symlink():
            try:
                raw_target = os.readlink(path)
                target = (path.parent / raw_target).resolve()
                if not self._is_allowed(target):
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
        if _env_truth("LLM_TOOL_ALLOW_ALL_PATHS"):
            return True
        s = str(path)
        s_dir = s + os.sep if not s.endswith(os.sep) else s
        return (
            s_dir.startswith(self._home_str)
            or path == self.home
            or s_dir.startswith(self._temp_str)
            or path == self.temp
            or s_dir.startswith(self._termux_home)
        )

    def _is_binary(self, path: Path, check_bytes: int = BINARY_CHECK_BYTES) -> bool:
        path_str = str(path)
        if path_str in _MIME_CACHE:
            return _MIME_CACHE[path_str] is None
        if any(path_str.startswith(pfx) for pfx in PSEUDO_FS_PREFIXES):
            return False
        try:
            with open(path, "rb") as f:
                chunk = f.read(check_bytes)
            if b"\x00" in chunk:
                _MIME_CACHE[path_str] = None
                return True
            if _FILE_CMD:
                try:
                    r = subprocess.run(
                        [_FILE_CMD, "-b", "--mime-type", path_str],
                        capture_output=True, text=True, timeout=2.0
                    )
                    if r.returncode == 0:
                        mime = r.stdout.strip()
                        is_binary = not (mime.startswith(_TEXT_MIME_PREFIXES) or not mime)
                        _MIME_CACHE[path_str] = None if is_binary else mime
                        return is_binary
                except Exception:
                    pass
            _MIME_CACHE[path_str] = "text/plain"
            return False
        except OSError:
            return True

    def _atomic_write(self, path: Path, content: str, encoding: str = DEFAULT_ENCODING) -> None:
        """Write text to target file atomically with fallback."""
        if not isinstance(content, str):
            content = str(content)
        raw = content.encode(encoding, errors="surrogateescape")
        self._atomic_write_bytes(path, raw)

    def _atomic_write_bytes(self, path: Path, payload: bytes) -> None:
        """Write bytes atomically using temp file + rename."""
        dir_ = path.parent
        # Try O_TMPFILE first (Linux 3.11+)
        _O_TMPFILE = getattr(os, "O_TMPFILE", None)
        if _O_TMPFILE is not None:
            try:
                fd = os.open(str(dir_), _O_TMPFILE | os.O_RDWR | os.O_CLOEXEC, 0o600)
                try:
                    os.write(fd, payload)
                    os.fsync(fd)
                    proc_link = f"/proc/self/fd/{fd}"
                    tmp_link = str(dir_ / f".~tmp_{os.getpid()}_{time.time_ns()}")
                    os.link(proc_link, tmp_link)
                    os.replace(tmp_link, str(path))
                    return
                finally:
                    os.close(fd)
            except Exception:
                pass

        # Fallback: mkstemp
        fd2, tmp_name = tempfile.mkstemp(dir=str(dir_), prefix=".~tmp_")
        try:
            os.fchmod(fd2, 0o600)
            with os.fdopen(fd2, "wb") as f:
                f.write(payload)
                f.flush()
                os.fsync(f.fileno())
            # Preserve metadata if target exists
            if path.exists():
                try:
                    shutil.copymode(str(path), tmp_name)
                except OSError:
                    pass
            os.replace(tmp_name, str(path))
        except Exception:
            try:
                os.unlink(tmp_name)
            except OSError:
                pass
            raise

    def _make_backup(self, path: Path, max_backups: int = MAX_BACKUPS) -> Optional[Path]:
        """Create a timestamped .bak copy of file and prune excess backups."""
        if not path.exists():
            return None
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
        deadline = time.monotonic() + timeout
        while True:
            try:
                fd = os.open(str(lock_path), os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
                os.write(fd, str(os.getpid()).encode())
                os.close(fd)
                return
            except FileExistsError:
                try:
                    if lock_path.exists():
                        lock_age = time.time() - lock_path.stat().st_mtime
                        if lock_age > STALE_LOCK_SECONDS:
                            lock_path.unlink(missing_ok=True)
                            continue
                except OSError:
                    pass
                if time.monotonic() >= deadline:
                    return
                time.sleep(0.03)

    @staticmethod
    def _release_lock(lock_path: Path) -> None:
        try:
            lock_path.unlink(missing_ok=True)
        except OSError:
            pass

    def _prune_backups(self, path: Path, max_backups: int) -> None:
        pattern = f"{_glob_module.escape(path.stem)}{_glob_module.escape(path.suffix)}.*.bak"
        try:
            backups = sorted(path.parent.glob(pattern), key=lambda p: p.stat().st_mtime_ns)
            while len(backups) > max_backups:
                backups.pop(0).unlink(missing_ok=True)
        except OSError:
            pass

    def _list_backups(self, path: Path) -> list[Path]:
        pattern = f"{_glob_module.escape(path.stem)}{_glob_module.escape(path.suffix)}.*.bak"
        try:
            return sorted(path.parent.glob(pattern), key=lambda p: p.stat().st_mtime_ns, reverse=True)
        except OSError:
            return []

    def _read_content(self, file_path: str, encoding: Optional[str] = None, allow_binary: bool = False) -> dict[str, Any]:
        """Read and validate file text content with robust encoding detection."""
        path = self._validate_path(file_path, allow_write=False)
        if not path:
            return {"success": False, "error": "Invalid or disallowed file path"}
        if not path.exists():
            return {"success": False, "error": f"File not found: {file_path}"}
        if not path.is_file():
            return {"success": False, "error": f"Path is not a regular file: {file_path}"}
        try:
            content, enc_used, is_binary, newline_style, file_size, bom_present = _read_file_text(path, encoding)
            if not allow_binary and is_binary:
                return {"success": False, "error": "Binary file detected; text operation refused"}
            return {
                "success": True,
                "content": content,
                "path": path,
                "encoding_used": enc_used,
                "is_binary": is_binary,
                "newline_style": newline_style,
                "file_size": file_size,
                "bom_present": bom_present,
            }
        except Exception as exc:
            return {"success": False, "error": str(exc)}


_editor = FileEditor()


# ==============================================================================
# SECTION 5: Options Normalizer & Agent Helpers
# ==============================================================================

class EditOptions:
    """Options container with attribute access and defaults."""

    DEFAULTS: Dict[str, Any] = {
        "operation": None, "action": None,
        "file_path": None, "target": None, "path": None,
        "target_path": None,
        "content": None,
        "search": None, "search_text": None, "pattern": None,
        "replacement": None,
        "edits": None, "ops": None,
        "line": None, "line_number": None,
        "start_line": None, "end_line": None,
        "encoding": None,
        "max_size": DEFAULT_MAX_READ,
        "max_write_size": DEFAULT_MAX_WRITE,
        "max_file_bytes": DEFAULT_MAX_FILE_BYTES,
        "max_replacements": None,
        "max_backups": MAX_BACKUPS,
        "max_matches": 1000,
        "context_lines": 3, "line_context": 0,
        "truncate_size": 0,
        "mode": None,
        "to_type": None,
        "backup_timestamp": None,
        "algorithm": "sha256",
        "n_lines": 10,
        "compare_mode": "bytes",
        "compression": "deflate",
        "password": None,
        "variables": {},
        "undefined_var": "error",
        "file_pattern": "*",
        "exclude_pattern": None,
        "min_size": None, "max_size_filter": None,
        "modified_after": None, "modified_before": None,
        "file_type": "any",
        "max_results": MAX_FIND_RESULTS,
        "max_depth": 0,
        "sort_by": "name",
        "timeout": DEFAULT_TIMEOUT,
        # Flags
        "regex": False, "use_regex": False,
        "ignore_case": False, "case_insensitive": False,
        "global_replace": True,
        "decode_escapes": False,
        "dotall": False,
        "preserve_newlines": True,
        "backup": False,
        "dry_run": False,
        "show_lines": True,
        "add_newline": True,
        "include_hidden": False,
        "descending": False,
        "parents": True,
        "recursive": False,
        "preserve_metadata": True,
        "continue_on_error": False,
        "no_color": False,
        "verbose": False,
        "stdin": False,
        "force": False,
        "verify_after_write": True,
        "return_diff": False,
        "strict_mode": False,
        "transactional": False,
    }

    def __init__(self, **kwargs: Any) -> None:
        data = dict(self.DEFAULTS)
        data.update({k: v for k, v in kwargs.items() if v is not None})

        # Normalize operation
        op = data.get("operation") or data.get("action")
        data["operation"] = str(op).strip().lower() if op else "read"

        # Compatibility aliases for LLM generated tool calls.
        operation_aliases = {
            "edit": "replace",
            "modify": "replace",
            "update": "replace",
            "cat": "read",
            "ls": "list_dir",
            "rm": "delete",
            "mkdir": "create_dir",
            "cp": "copy",
            "mv": "move",
            "grep": "grep_dir",
            "patch": "replace",
            "edit_file": "replace",
            "readfile": "read",
            "writefile": "write",
            "save": "write",
        }
        data["operation"] = operation_aliases.get(data["operation"], data["operation"])

        # Normalize file path
        fpath = data.get("file_path") or data.get("target") or data.get("path")
        data["file_path"] = str(fpath).strip() if fpath else None

        # Normalize search
        srch = data.get("search") or data.get("search_text") or data.get("pattern")
        data["search_text"] = str(srch) if srch is not None else None

        # Normalize line number
        ln = data.get("line_number") or data.get("line") or data.get("start_line")
        data["line_number"] = _as_int(ln)

        # Normalize flags
        data["regex"] = bool(data.get("regex") or data.get("use_regex"))
        data["case_sensitive"] = not (data.get("ignore_case") or data.get("case_insensitive"))

        self._data = data

    def __getattr__(self, name: str) -> Any:
        if name.startswith("_"):
            raise AttributeError(name)
        return self._data.get(name)

    def __setattr__(self, name: str, value: Any) -> None:
        if name.startswith("_"):
            super().__setattr__(name, value)
        else:
            self._data[name] = value

    def get(self, key: str, default: Any = None) -> Any:
        return self._data.get(key, default)

    def to_dict(self) -> dict:
        return dict(self._data)


def get_agent_var(name: str, default: str = "") -> str:
    return os.environ.get(f"LLM_AGENT_VAR_{name.upper()}", default)


def get_builtin_var(name: str) -> Optional[str]:
    return os.environ.get(f"LLM_AGENT_VAR_{name}")


def get_execution_context() -> dict[str, Any]:
    termux_prefix = os.environ.get("PREFIX", "")
    return {
        "tool_name": os.environ.get("LLM_TOOL_NAME", "edit_file"),
        "output_path": os.environ.get("LLM_OUTPUT"),
        "cwd": get_builtin_var("__cwd__") or os.getcwd(),
        "platform": platform.system(),
        "is_termux": "com.termux" in termux_prefix or Path("/data/data/com.termux").exists(),
        "pid": os.getpid(),
        "version": __version__,
    }


def _read_stdin(timeout: float = STDIN_TIMEOUT) -> str:
    """Read content from stdin with timeout."""
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


def _resolve_max_file_bytes(cli_value: Optional[int] = None) -> int:
    if cli_value is not None:
        try:
            return max(0, int(cli_value))
        except Exception:
            return DEFAULT_MAX_FILE_BYTES
    env_value = os.environ.get("LLM_TOOL_MAX_FILE_BYTES")
    if env_value is not None:
        try:
            return max(0, int(env_value))
        except Exception:
            pass
    return DEFAULT_MAX_FILE_BYTES


def _check_zip_slip(member_path: str, target_dir: Path) -> bool:
    """Validate that a zip member path doesn't escape target directory."""
    member_resolved = (target_dir / member_path).resolve()
    target_resolved = target_dir.resolve()
    return str(member_resolved).startswith(str(target_resolved) + os.sep) or member_resolved == target_resolved


# ==============================================================================
# SECTION 6: Graceful Shutdown & Regex Cache
# ==============================================================================

class GracefulShutdown:
    """Signal handler context manager for safe execution cancellation."""

    def __init__(self) -> None:
        self.interrupted = False
        self._lock = threading.Lock()
        self._handlers: Dict[int, Any] = {}

    def __enter__(self) -> "GracefulShutdown":
        try:
            if threading.current_thread() is threading.main_thread():
                self._handlers[signal.SIGINT] = signal.signal(signal.SIGINT, self._handle)
                self._handlers[signal.SIGTERM] = signal.signal(signal.SIGTERM, self._handle)
        except (ValueError, OSError):
            pass
        return self

    def __exit__(self, exc_type: Any, exc_val: Any, exc_tb: Any) -> None:
        for sig, h in self._handlers.items():
            try:
                signal.signal(sig, h)
            except (ValueError, OSError):
                pass

    def _handle(self, signum: int, frame: Any) -> None:
        with self._lock:
            self.interrupted = True

    def check_interrupted(self) -> bool:
        with self._lock:
            return self.interrupted


class RegexCache:
    """Thread-safe regex pattern cache with LRU eviction."""

    def __init__(self, max_size: int = 128):
        self._cache: OrderedDict[Tuple[str, int], Pattern[str]] = OrderedDict()
        self._max_size = max_size
        self._lock = threading.Lock()

    def get(self, pattern: str, flags: int) -> Pattern[str]:
        key = (pattern, flags)
        with self._lock:
            if key in self._cache:
                self._cache.move_to_end(key)
                return self._cache[key]
            if len(self._cache) >= self._max_size:
                self._cache.popitem(last=False)
            compiled = re.compile(pattern, flags)
            self._cache[key] = compiled
            return compiled


_regex_cache = RegexCache()


def _build_regex_flags(ignore_case: bool = False, dotall: bool = False) -> int:
    flags = re.MULTILINE
    if ignore_case:
        flags |= re.IGNORECASE
    if dotall:
        flags |= re.DOTALL
    return flags


def _normalize_max_replacements(max_replacements: Optional[int]) -> Optional[int]:
    if max_replacements is None:
        return None
    try:
        value = int(max_replacements)
    except Exception:
        return None
    if value < 0:
        return None
    return value


def _validate_text_operation_size(search: str, replacement: str = "") -> None:
    """Prevent accidental huge replacement/search payloads."""
    if len(search or "") > 1_000_000:
        raise ValueError("Search pattern exceeds maximum allowed size")
    if len(replacement or "") > 5_000_000:
        raise ValueError("Replacement content exceeds maximum allowed size")


def _replace_in_content(
    content: str,
    search: str,
    replacement: str,
    regex: bool = False,
    ignore_case: bool = False,
    dotall: bool = False,
    max_replacements: Optional[int] = None,
) -> Tuple[str, int]:
    """Perform search and replace on content cleanly."""
    _validate_text_operation_size(search, replacement)
    limit = _normalize_max_replacements(max_replacements)
    if limit == 0:
        return content, 0
    flags = _build_regex_flags(ignore_case=ignore_case, dotall=dotall)

    if regex:
        try:
            pattern = _regex_cache.get(search, flags)
            count_arg = 0 if limit is None else limit
            return pattern.subn(replacement, content, count=count_arg)
        except re.error as e:
            raise ValueError(f"Regex error: {e}") from e

    if ignore_case:
        escaped_search = re.escape(search)
        try:
            pattern = _regex_cache.get(escaped_search, flags)
            count_arg = 0 if limit is None else limit
            return pattern.subn(lambda _m: replacement, content, count=count_arg)
        except re.error as e:
            raise ValueError(f"Regex error: {e}") from e

    if limit is None:
        count = content.count(search)
        return content.replace(search, replacement), count
    count_all = content.count(search)
    count = min(count_all, limit)
    return content.replace(search, replacement, limit), count


def _count_matches(
    content: str,
    search: str,
    regex: bool = False,
    ignore_case: bool = False,
    dotall: bool = False,
) -> int:
    flags = _build_regex_flags(ignore_case=ignore_case, dotall=dotall)
    if regex:
        pattern = _regex_cache.get(search, flags)
        return sum(1 for _ in pattern.finditer(content))
    if ignore_case:
        pattern = _regex_cache.get(re.escape(search), flags)
        return sum(1 for _ in pattern.finditer(content))
    return content.count(search)


# ==============================================================================
# SECTION 7: Core Operations Suite (38 Operations)
# ==============================================================================

def _timed(fn: Callable[..., dict[str, Any]]) -> Callable[..., dict[str, Any]]:
    """Decorator to inject duration_ms into operation results."""
    @functools.wraps(fn)
    def wrapper(*args: Any, **kwargs: Any) -> dict[str, Any]:
        t0 = time.perf_counter()
        try:
            result = fn(*args, **kwargs)
        except ToolError as exc:
            result = {"success": False, "error": str(exc), "exit_code": exc.exit_code}
        except Exception as exc:
            result = {"success": False, "error": str(exc)}
        if not isinstance(result, dict):
            result = {"success": False, "error": "Operation returned invalid response"}
        result["duration_ms"] = round((time.perf_counter() - t0) * 1000, 3)
        result.setdefault("tool_version", __version__)
        result.setdefault("schema_version", SCHEMA_VERSION)
        result.setdefault("request_id", f"req_{time.time_ns()}")
        result.setdefault("runtime", {
            "python": platform.python_version(),
            "platform": platform.system(),
            "termux": "com.termux" in os.environ.get("PREFIX", "")
        })
        return result
    return wrapper


# ---------- READ / VIEW ----------

@_timed
def op_read(
    file_path: str,
    max_size: int = DEFAULT_MAX_READ,
    encoding: Optional[str] = None,
    show_lines: bool = True,
    start_line: Optional[int] = None,
    end_line: Optional[int] = None,
) -> dict[str, Any]:
    """Read text file contents with optional line range slicing."""
    path = _editor._validate_path(file_path, allow_write=False)
    if not path or not path.exists() or not path.is_file():
        return {"success": False, "error": f"File not found or unreadable: {file_path}", "exit_code": EXIT_FILE_NOT_FOUND}

    try:
        st = path.stat()
        if st.st_size > max_size:
            return {"success": False, "error": f"File size ({st.st_size} bytes) exceeds read limit ({max_size} bytes)", "exit_code": EXIT_INVALID_INPUT}

        if _editor._is_binary(path):
            return {"success": False, "error": "Binary file detected; text read refused", "exit_code": EXIT_INVALID_INPUT}

        # If line range requested, delegate to line reader (without double-timing)
        if start_line is not None or end_line is not None:
            s = max(1, start_line or 1)
            e = end_line if end_line and end_line >= s else sys.maxsize
            selected: list[str] = []
            with open(path, encoding=encoding or DEFAULT_ENCODING, errors="surrogateescape") as f:
                for lineno, line in enumerate(f, 1):
                    if lineno > e:
                        break
                    if lineno >= s:
                        selected.append(line.rstrip("\r\n"))
            return {
                "success": True,
                "path": str(path),
                "lines": selected,
                "content": "\n".join(selected),
                "start_line": s,
                "end_line": s + len(selected) - 1 if selected else s,
                "line_count": len(selected),
                "size": st.st_size,
            }

        content, enc_used, is_bin, nl_style, fsize, bom = _read_file_text(path, encoding)
        all_lines = content.splitlines()
        return {
            "success": True,
            "path": str(path),
            "content": content,
            "lines": all_lines if show_lines else None,
            "line_count": len(all_lines),
            "size": st.st_size,
            "encoding": enc_used,
            "newline_style": nl_style,
            "bom_present": bom,
        }
    except Exception as exc:
        return {"success": False, "error": str(exc)}


@_timed
def op_read_lines(
    file_path: str,
    start_line: int,
    end_line: int,
    encoding: Optional[str] = None,
) -> dict[str, Any]:
    """Read a specific 1-based line range from a text file."""
    if start_line < 1 or end_line < start_line:
        return {"success": False, "error": "Invalid line range bounds", "exit_code": EXIT_INVALID_INPUT}
    path = _editor._validate_path(file_path, allow_write=False)
    if not path or not path.exists() or not path.is_file():
        return {"success": False, "error": "Invalid or missing target file", "exit_code": EXIT_FILE_NOT_FOUND}
    if _editor._is_binary(path):
        return {"success": False, "error": "Binary file detected", "exit_code": EXIT_INVALID_INPUT}
    try:
        selected: list[str] = []
        with open(path, encoding=encoding or DEFAULT_ENCODING, errors="surrogateescape") as f:
            for lineno, line in enumerate(f, 1):
                if lineno > end_line:
                    break
                if lineno >= start_line:
                    selected.append(line.rstrip("\r\n"))
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


# ---------- WRITE ----------

@_timed
def op_write(options: EditOptions) -> dict[str, Any]:
    """Write text content to a file atomically."""
    content = options.content
    if options.stdin:
        content = _read_stdin()
    if content is None:
        return {"success": False, "error": "content parameter is required for write", "exit_code": EXIT_INVALID_INPUT}

    file_path = options.file_path
    if not file_path:
        return {"success": False, "error": "file_path is required", "exit_code": EXIT_INVALID_INPUT}

    path = _editor._validate_path(file_path, allow_write=True)
    if not path:
        return {"success": False, "error": "Disallowed or invalid path", "exit_code": EXIT_PERMISSION_DENIED}

    text = str(content)
    if options.decode_escapes:
        text = _unescape_string(text)

    # Detect existing file newline style for preservation
    newline_style = ""
    bom_present = False
    encoding_used = DEFAULT_ENCODING
    if path.exists() and options.preserve_newlines:
        try:
            data = path.read_bytes()[:BINARY_CHECK_BYTES]
            newline_style = _detect_newline_style(data)
            _, bom_len = _detect_bom_encoding(data)
            bom_present = bom_len > 0
        except OSError:
            pass

    text_norm = _normalize_newlines(text)
    if options.add_newline and text_norm and not text_norm.endswith("\n"):
        text_norm += "\n"

    write_text = _prepare_normalized_write_text(text_norm, newline_style, options.preserve_newlines)
    encoding = options.encoding or DEFAULT_ENCODING
    payload, enc_used, fell_back = _encode_text(write_text, encoding, bom_present=bom_present)

    if len(payload) > options.max_write_size:
        return {"success": False, "error": "Content exceeds max_write_size limit", "exit_code": EXIT_INVALID_INPUT}

    if options.dry_run:
        return {"success": True, "path": str(path), "mode": "dry-run", "bytes_would_write": len(payload)}

    try:
        original_bytes = path.stat().st_size if path.exists() else 0
        if options.backup and path.exists():
            _editor._make_backup(path, options.max_backups)
        _editor._atomic_write_bytes(path, payload)
        new_bytes = path.stat().st_size
        return {
            "success": True,
            "path": str(path),
            "size": new_bytes,
            "bytes_delta": new_bytes - original_bytes,
            "original_bytes": original_bytes,
            "new_bytes": new_bytes,
            "encoding": enc_used,
            "encoding_fallback": fell_back,
        }
    except Exception as exc:
        return {"success": False, "error": str(exc)}


# ---------- APPEND / PREPEND ----------

@_timed
def op_append(
    file_path: str,
    content: str,
    encoding: Optional[str] = None,
    add_newline: bool = True,
    max_size: int = DEFAULT_MAX_WRITE,
    decode_escapes: bool = False,
    preserve_newlines: bool = True,
) -> dict[str, Any]:
    if content is None:
        return {"success": False, "error": "content parameter is required", "exit_code": EXIT_INVALID_INPUT}
    path = _editor._validate_path(file_path, allow_write=True)
    if not path or not path.exists() or not path.is_file():
        return {"success": False, "error": "Target file for append does not exist", "exit_code": EXIT_FILE_NOT_FOUND}
    if _editor._is_binary(path):
        return {"success": False, "error": "Binary file detected; append refused", "exit_code": EXIT_INVALID_INPUT}

    text = _unescape_string(str(content)) if decode_escapes else str(content)
    try:
        file_content, enc_used, _, nl_style, fsize, bom = _read_file_text(path, encoding)
        incoming = _normalize_newlines(text)
        sep = "\n" if add_newline and file_content and not file_content.endswith("\n") else ""
        new_content = file_content + sep + incoming

        write_text = _prepare_normalized_write_text(new_content, nl_style, preserve_newlines)
        payload, enc_final, _ = _encode_text(write_text, enc_used, bom_present=bom)
        if len(payload) > max_size:
            return {"success": False, "error": "Combined content exceeds max size", "exit_code": EXIT_INVALID_INPUT}

        _editor._atomic_write_bytes(path, payload)
        new_size = path.stat().st_size
        return {
            "success": True,
            "path": str(path),
            "original_bytes": fsize,
            "new_bytes": new_size,
            "bytes_delta": new_size - fsize,
            "encoding": enc_final,
        }
    except Exception as exc:
        return {"success": False, "error": str(exc)}


@_timed
def op_prepend(
    file_path: str,
    content: str,
    encoding: Optional[str] = None,
    add_newline: bool = True,
    max_size: int = DEFAULT_MAX_WRITE,
    decode_escapes: bool = False,
    preserve_newlines: bool = True,
) -> dict[str, Any]:
    if content is None:
        return {"success": False, "error": "content parameter is required", "exit_code": EXIT_INVALID_INPUT}
    path = _editor._validate_path(file_path, allow_write=True)
    if not path or not path.exists() or not path.is_file():
        return {"success": False, "error": "Target file for prepend does not exist", "exit_code": EXIT_FILE_NOT_FOUND}
    if _editor._is_binary(path):
        return {"success": False, "error": "Binary file detected; prepend refused", "exit_code": EXIT_INVALID_INPUT}

    text = _unescape_string(str(content)) if decode_escapes else str(content)
    try:
        file_content, enc_used, _, nl_style, fsize, bom = _read_file_text(path, encoding)
        incoming = _normalize_newlines(text)
        sep = "\n" if add_newline and file_content and not incoming.endswith("\n") else ""
        new_content = incoming + sep + file_content

        write_text = _prepare_normalized_write_text(new_content, nl_style, preserve_newlines)
        payload, enc_final, _ = _encode_text(write_text, enc_used, bom_present=bom)
        if len(payload) > max_size:
            return {"success": False, "error": "Combined content exceeds max size", "exit_code": EXIT_INVALID_INPUT}

        _editor._atomic_write_bytes(path, payload)
        new_size = path.stat().st_size
        return {
            "success": True,
            "path": str(path),
            "original_bytes": fsize,
            "new_bytes": new_size,
            "bytes_delta": new_size - fsize,
            "encoding": enc_final,
        }
    except Exception as exc:
        return {"success": False, "error": str(exc)}


# ---------- REPLACE ----------

@_timed
def op_replace(options: EditOptions) -> dict[str, Any]:
    """Replace literal text or regex patterns in a file."""
    file_path = options.file_path
    search_text = options.search_text or options.pattern
    if not search_text:
        return {"success": False, "error": "search string or pattern is required", "exit_code": EXIT_INVALID_INPUT}

    replacement = str(options.replacement if options.replacement is not None else (options.content or ""))
    if options.decode_escapes:
        search_text = _unescape_string(search_text)
        replacement = _unescape_string(replacement)

    res = _editor._read_content(file_path, options.encoding)
    if not res["success"]:
        return res

    content: str = _normalize_newlines(res["content"])
    path: Path = res["path"]
    nl_style: str = res.get("newline_style", "")
    bom: bool = res.get("bom_present", False)
    enc_used: str = res.get("encoding_used", DEFAULT_ENCODING)

    search_norm = _normalize_newlines(search_text)
    repl_norm = _normalize_newlines(replacement)

    limit = _normalize_max_replacements(options.max_replacements)
    if not options.global_replace and limit is None:
        limit = 1

    try:
        new_content, count = _replace_in_content(
            content, search_norm, repl_norm,
            regex=options.regex,
            ignore_case=not options.case_sensitive,
            dotall=options.dotall,
            max_replacements=limit,
        )
    except ValueError as exc:
        return {"success": False, "error": str(exc), "exit_code": EXIT_INVALID_INPUT}

    if new_content == content or count == 0:
        return {"success": True, "path": str(path), "replacements": 0, "changed": False, "message": "No matches found"}

    diff_text = _generate_diff(content, new_content, str(path))
    if options.dry_run:
        return {"success": True, "path": str(path), "mode": "dry-run", "replacements": count, "changed": True, "diff": diff_text}

    backup_path = None
    if options.backup:
        bp = _editor._make_backup(path, options.max_backups)
        backup_path = str(bp) if bp else None

    write_text = _prepare_normalized_write_text(new_content, nl_style, options.preserve_newlines)
    payload, enc_final, _ = _encode_text(write_text, enc_used, bom_present=bom)
    _editor._atomic_write_bytes(path, payload)

    return {
        "success": True,
        "path": str(path),
        "replacements": count,
        "changed": True,
        "backup_path": backup_path,
        "diff": diff_text,
        "size": path.stat().st_size,
    }


# ---------- LINE OPERATIONS ----------

@_timed
def op_insert_line(
    file_path: str,
    line_number: int,
    content: str,
    encoding: Optional[str] = None,
    max_backups: int = MAX_BACKUPS,
    decode_escapes: bool = False,
) -> dict[str, Any]:
    if line_number is None or line_number < 1:
        return {"success": False, "error": "Valid 1-based line_number is required", "exit_code": EXIT_INVALID_INPUT}
    if content is None:
        return {"success": False, "error": "content parameter is required", "exit_code": EXIT_INVALID_INPUT}

    text = _unescape_string(str(content)) if decode_escapes else str(content)
    res = _editor._read_content(file_path, encoding)
    if not res["success"]:
        return res

    lines = res["content"].splitlines(keepends=True)
    idx = max(0, min(line_number - 1, len(lines)))
    if not text.endswith("\n"):
        text += "\n"
    lines.insert(idx, text)
    new_content = "".join(lines)

    path: Path = res["path"]
    try:
        backup = _editor._make_backup(path, max_backups)
        _editor._atomic_write(path, new_content, res.get("encoding_used", DEFAULT_ENCODING))
        return {"success": True, "path": str(path), "inserted_line": idx + 1, "backup_path": str(backup) if backup else None}
    except Exception as exc:
        return {"success": False, "error": str(exc)}


@_timed
def op_delete_line(
    file_path: str,
    line_number: int,
    encoding: Optional[str] = None,
    max_backups: int = MAX_BACKUPS,
) -> dict[str, Any]:
    if line_number is None or line_number < 1:
        return {"success": False, "error": "Valid 1-based line_number is required", "exit_code": EXIT_INVALID_INPUT}
    res = _editor._read_content(file_path, encoding)
    if not res["success"]:
        return res

    lines = res["content"].splitlines(keepends=True)
    if line_number > len(lines):
        return {"success": False, "error": f"line_number {line_number} exceeds file length ({len(lines)} lines)", "exit_code": EXIT_INVALID_INPUT}

    deleted = lines.pop(line_number - 1)
    new_content = "".join(lines)
    path: Path = res["path"]
    try:
        backup = _editor._make_backup(path, max_backups)
        _editor._atomic_write(path, new_content, res.get("encoding_used", DEFAULT_ENCODING))
        return {
            "success": True,
            "path": str(path),
            "deleted_line": line_number,
            "deleted_content": deleted.rstrip("\r\n"),
            "backup_path": str(backup) if backup else None,
        }
    except Exception as exc:
        return {"success": False, "error": str(exc)}


@_timed
def op_replace_lines(
    file_path: str,
    start_line: int,
    end_line: int,
    content: str,
    encoding: Optional[str] = None,
    max_backups: int = MAX_BACKUPS,
    dry_run: bool = False,
    decode_escapes: bool = False,
) -> dict[str, Any]:
    if start_line < 1 or end_line < start_line:
        return {"success": False, "error": "Invalid start_line/end_line range", "exit_code": EXIT_INVALID_INPUT}

    text = _unescape_string(str(content or "")) if decode_escapes else str(content or "")
    res = _editor._read_content(file_path, encoding)
    if not res["success"]:
        return res

    lines = res["content"].splitlines(keepends=True)
    s_idx = start_line - 1
    e_idx = min(end_line, len(lines))
    repl_lines = [l + "\n" for l in text.splitlines()] if text else []
    new_lines = lines[:s_idx] + repl_lines + lines[e_idx:]
    new_content = "".join(new_lines)

    path: Path = res["path"]
    diff_text = _generate_diff(res["content"], new_content, str(path))
    if dry_run:
        return {"success": True, "path": str(path), "mode": "dry-run", "diff": diff_text}

    try:
        backup = _editor._make_backup(path, max_backups)
        _editor._atomic_write(path, new_content, res.get("encoding_used", DEFAULT_ENCODING))
        return {"success": True, "path": str(path), "lines_replaced": e_idx - s_idx, "diff": diff_text, "backup_path": str(backup) if backup else None}
    except Exception as exc:
        return {"success": False, "error": str(exc)}


# ---------- SEARCH / COUNT ----------

@_timed
def op_file_search(
    file_path: str,
    pattern: str,
    use_regex: bool = False,
    case_sensitive: bool = True,
    context_lines: int = 0,
    encoding: Optional[str] = None,
    max_matches: int = 1000,
) -> dict[str, Any]:
    if not pattern or not pattern.strip():
        return {"success": False, "error": "pattern parameter is required", "exit_code": EXIT_INVALID_INPUT}

    res = _editor._read_content(file_path, encoding)
    if not res["success"]:
        return res

    lines = res["content"].splitlines()
    matches: list[dict[str, Any]] = []

    if use_regex:
        flags = 0 if case_sensitive else re.IGNORECASE
        try:
            comp = _regex_cache.get(pattern, flags)
            match_fn = lambda l: bool(comp.search(l))
        except re.error as exc:
            return {"success": False, "error": f"Invalid regex: {exc}", "exit_code": EXIT_INVALID_INPUT}
    elif case_sensitive:
        match_fn = lambda l: pattern in l
    else:
        _lower = pattern.lower()
        match_fn = lambda l: _lower in l.lower()

    for i, line in enumerate(lines):
        if len(matches) >= max_matches:
            break
        if match_fn(line):
            entry: dict[str, Any] = {"line": i + 1, "content": line}
            if context_lines > 0:
                ctx_s = max(0, i - context_lines)
                ctx_e = min(len(lines), i + context_lines + 1)
                entry["context"] = lines[ctx_s:ctx_e]
            matches.append(entry)

    return {"success": True, "path": str(res["path"]), "pattern": pattern, "matches": matches, "match_count": len(matches)}


# ---------- FILE SYSTEM OPERATIONS ----------

@_timed
def op_copy(
    file_path: str,
    target_path: str,
    preserve_metadata: bool = True,
    recursive: bool = False,
) -> dict[str, Any]:
    src = _editor._validate_path(file_path, allow_write=False)
    dst = _editor._validate_path(target_path, allow_write=True)
    if not src or not dst or not src.exists():
        return {"success": False, "error": "Invalid source/destination paths", "exit_code": EXIT_FILE_NOT_FOUND}
    try:
        if src.is_dir():
            if not recursive:
                return {"success": False, "error": "recursive flag required to copy directory", "exit_code": EXIT_INVALID_INPUT}
            copy_fn = shutil.copy2 if preserve_metadata else shutil.copy
            shutil.copytree(src, dst, copy_function=copy_fn, dirs_exist_ok=True)
        else:
            if preserve_metadata:
                shutil.copy2(src, dst)
            else:
                shutil.copy(src, dst)
        return {"success": True, "source": str(src), "target": str(dst)}
    except Exception as exc:
        return {"success": False, "error": str(exc)}


@_timed
def op_move(file_path: str, target_path: str) -> dict[str, Any]:
    src = _editor._validate_path(file_path, allow_write=True)
    dst = _editor._validate_path(target_path, allow_write=True)
    if not src or not dst or not src.exists():
        return {"success": False, "error": "Invalid source/destination paths", "exit_code": EXIT_FILE_NOT_FOUND}
    try:
        shutil.move(str(src), str(dst))
        return {"success": True, "source": str(src), "target": str(dst)}
    except Exception as exc:
        return {"success": False, "error": str(exc)}


@_timed
def op_delete(file_path: str, recursive: bool = False) -> dict[str, Any]:
    path = _editor._validate_path(file_path, allow_write=True)
    if not path or not path.exists():
        return {"success": False, "error": f"Path not found: {file_path}", "exit_code": EXIT_FILE_NOT_FOUND}
    try:
        if path.is_dir():
            if not recursive:
                return {"success": False, "error": "recursive flag required to delete directory", "exit_code": EXIT_INVALID_INPUT}
            shutil.rmtree(path)
        else:
            path.unlink()
        return {"success": True, "path": str(path)}
    except Exception as exc:
        return {"success": False, "error": str(exc)}


@_timed
def op_info(file_path: str) -> dict[str, Any]:
    path = _editor._validate_path(file_path, allow_write=False)
    if not path or not path.exists():
        return {"success": False, "error": f"Path not found: {file_path}", "exit_code": EXIT_FILE_NOT_FOUND}
    try:
        st = path.stat()
        res: dict[str, Any] = {
            "success": True,
            "path": str(path),
            "name": path.name,
            "is_file": path.is_file(),
            "is_dir": path.is_dir(),
            "is_symlink": path.is_symlink(),
            "size": st.st_size,
            "modified": datetime.fromtimestamp(st.st_mtime).isoformat(),
            "accessed": datetime.fromtimestamp(st.st_atime).isoformat(),
            "permissions": oct(st.st_mode)[-4:],
            "uid": st.st_uid,
            "gid": st.st_gid,
        }
        if path.is_file() and not _editor._is_binary(path):
            with open(path, "rb") as f:
                res["line_count"] = sum(1 for _ in f)
        return res
    except Exception as exc:
        return {"success": False, "error": str(exc)}


@_timed
def op_create_dir(file_path: str, parents: bool = True) -> dict[str, Any]:
    path = _editor._validate_path(file_path, allow_write=True)
    if not path:
        return {"success": False, "error": "Invalid directory path", "exit_code": EXIT_INVALID_INPUT}
    try:
        if path.exists() and path.is_file():
            return {"success": False, "error": f"Path exists as a file: {file_path}", "exit_code": EXIT_INVALID_INPUT}
        path.mkdir(parents=parents, exist_ok=True)
        return {"success": True, "path": str(path)}
    except Exception as exc:
        return {"success": False, "error": str(exc)}


@_timed
def op_list_dir(
    file_path: str,
    include_hidden: bool = False,
    sort_by: str = "name",
    descending: bool = False,
) -> dict[str, Any]:
    path = _editor._validate_path(file_path, allow_write=False)
    if not path or not path.exists() or not path.is_dir():
        return {"success": False, "error": "Invalid directory path", "exit_code": EXIT_FILE_NOT_FOUND}

    items: list[dict[str, Any]] = []
    try:
        for item in path.iterdir():
            if not include_hidden and item.name.startswith("."):
                continue
            try:
                st = item.stat()
            except OSError:
                continue
            items.append({
                "name": item.name,
                "path": str(item),
                "is_file": item.is_file(),
                "is_dir": item.is_dir(),
                "is_symlink": item.is_symlink(),
                "size": st.st_size,
                "modified": st.st_mtime,
                "permissions": oct(st.st_mode)[-4:],
            })

        if sort_by == "size":
            items.sort(key=lambda x: x["size"], reverse=descending)
        elif sort_by == "modified":
            items.sort(key=lambda x: x["modified"], reverse=descending)
        elif sort_by == "type":
            items.sort(key=lambda x: (not x["is_dir"], x["name"].lower()), reverse=descending)
        else:
            items.sort(key=lambda x: x["name"].lower(), reverse=descending)

        return {"success": True, "path": str(path), "items": items, "count": len(items)}
    except Exception as exc:
        return {"success": False, "error": str(exc)}


# ---------- DIFF / COMPARE ----------

@_timed
def op_diff(
    file_path: str,
    target_path: Optional[str] = None,
    encoding: Optional[str] = None,
    context_lines: int = 3,
) -> dict[str, Any]:
    path = _editor._validate_path(file_path, allow_write=False)
    if not path or not path.exists():
        return {"success": False, "error": "Source file not found", "exit_code": EXIT_FILE_NOT_FOUND}

    cmp_path: Optional[Path] = None
    if target_path:
        cmp_path = _editor._validate_path(target_path, allow_write=False)
    else:
        backups = _editor._list_backups(path)
        if backups:
            cmp_path = backups[0]

    if not cmp_path or not cmp_path.exists():
        return {"success": False, "error": "Target file or backup not found for diff", "exit_code": EXIT_FILE_NOT_FOUND}

    try:
        old_text = cmp_path.read_text(encoding=encoding or DEFAULT_ENCODING, errors="surrogateescape")
        new_text = path.read_text(encoding=encoding or DEFAULT_ENCODING, errors="surrogateescape")
        diff_text = _generate_diff(old_text, new_text, str(path), max_lines=context_lines * 20)
        return {"success": True, "path": str(path), "target": str(cmp_path), "diff": diff_text}
    except Exception as exc:
        return {"success": False, "error": str(exc)}


@_timed
def op_compare_files(file_path: str, target_path: str, compare_mode: str = "bytes", encoding: Optional[str] = None) -> dict[str, Any]:
    src = _editor._validate_path(file_path, allow_write=False)
    dst = _editor._validate_path(target_path, allow_write=False)
    if not src or not dst or not src.exists() or not dst.exists():
        return {"success": False, "error": "Invalid source or comparison file", "exit_code": EXIT_FILE_NOT_FOUND}
    try:
        if compare_mode == "bytes":
            equal = src.stat().st_size == dst.stat().st_size
            if equal:
                with open(src, "rb") as fa, open(dst, "rb") as fb:
                    while True:
                        chunk_a = fa.read(CHUNK_SIZE)
                        chunk_b = fb.read(CHUNK_SIZE)
                        if chunk_a != chunk_b:
                            equal = False
                            break
                        if not chunk_a:
                            break
            return {"success": True, "source": str(src), "target": str(dst), "equal": equal, "mode": "bytes"}
        else:
            t1 = src.read_text(encoding=encoding or DEFAULT_ENCODING, errors="surrogateescape")
            t2 = dst.read_text(encoding=encoding or DEFAULT_ENCODING, errors="surrogateescape")
            return {"success": True, "source": str(src), "target": str(dst), "equal": t1 == t2, "mode": "text"}
    except Exception as exc:
        return {"success": False, "error": str(exc)}


# ---------- TRUNCATE / PERMISSIONS / LINE ENDINGS ----------

@_timed
def op_truncate(
    file_path: str,
    truncate_size: int = 0,
    max_backups: int = MAX_BACKUPS,
) -> dict[str, Any]:
    path = _editor._validate_path(file_path, allow_write=True)
    if not path or not path.exists() or not path.is_file():
        return {"success": False, "error": "Invalid target file", "exit_code": EXIT_FILE_NOT_FOUND}
    try:
        backup = _editor._make_backup(path, max_backups)
        with open(path, "r+b") as fh:
            fh.truncate(truncate_size)
        return {"success": True, "path": str(path), "new_size": path.stat().st_size, "backup_path": str(backup) if backup else None}
    except Exception as exc:
        return {"success": False, "error": str(exc)}


@_timed
def op_set_permissions(file_path: str, mode: str) -> dict[str, Any]:
    path = _editor._validate_path(file_path, allow_write=True)
    if not path or not path.exists():
        return {"success": False, "error": "Invalid target path", "exit_code": EXIT_FILE_NOT_FOUND}
    try:
        mode_str = str(mode).strip().lstrip("0o")
        mode_int = int(mode_str, 8)
        if mode_int < 0 or mode_int > 0o7777:
            return {"success": False, "error": f"Invalid permission mode: {mode} (must be 0-7777 octal)", "exit_code": EXIT_INVALID_INPUT}
        os.chmod(path, mode_int)
        return {"success": True, "path": str(path), "mode": oct(path.stat().st_mode)[-4:]}
    except ValueError:
        return {"success": False, "error": f"Invalid octal mode: {mode}", "exit_code": EXIT_INVALID_INPUT}
    except Exception as exc:
        return {"success": False, "error": str(exc)}


@_timed
def op_normalize_line_endings(
    file_path: str,
    to_type: str = "lf",
    encoding: Optional[str] = None,
    max_backups: int = MAX_BACKUPS,
) -> dict[str, Any]:
    path = _editor._validate_path(file_path, allow_write=True)
    if not path or not path.exists() or not path.is_file():
        return {"success": False, "error": "Invalid target file", "exit_code": EXIT_FILE_NOT_FOUND}

    res = _editor._read_content(file_path, encoding)
    if not res["success"]:
        return res

    text = _normalize_newlines(res["content"])
    new_content = text.replace("\n", "\r\n") if to_type == "crlf" else text

    try:
        backup = _editor._make_backup(path, max_backups)
        raw = new_content.encode(res.get("encoding_used", DEFAULT_ENCODING), errors="surrogateescape")
        _editor._atomic_write_bytes(path, raw)
        return {"success": True, "path": str(path), "target_style": to_type, "backup_path": str(backup) if backup else None}
    except Exception as exc:
        return {"success": False, "error": str(exc)}


# ---------- BACKUP OPERATIONS ----------

@_timed
def op_revert_to_backup(
    file_path: str,
    backup_timestamp: Optional[str] = None,
    max_backups: int = MAX_BACKUPS,
) -> dict[str, Any]:
    path = _editor._validate_path(file_path, allow_write=True)
    if not path:
        return {"success": False, "error": "Invalid path", "exit_code": EXIT_INVALID_INPUT}

    backups = _editor._list_backups(path)
    if not backups:
        return {"success": False, "error": "No backup files available", "exit_code": EXIT_FILE_NOT_FOUND}

    chosen = backups[0]
    if backup_timestamp:
        for bk in backups:
            if backup_timestamp in bk.name:
                chosen = bk
                break

    try:
        current_bk = _editor._make_backup(path, max_backups)
        shutil.copy2(chosen, path)
        return {"success": True, "path": str(path), "restored_from": str(chosen), "backup_created": str(current_bk) if current_bk else None}
    except Exception as exc:
        return {"success": False, "error": str(exc)}


@_timed
def op_list_backups(file_path: str) -> dict[str, Any]:
    path = _editor._validate_path(file_path, allow_write=False)
    if not path:
        return {"success": False, "error": "Invalid path", "exit_code": EXIT_INVALID_INPUT}
    backups = _editor._list_backups(path)
    return {
        "success": True,
        "path": str(path),
        "backups": [{"path": str(b), "size": b.stat().st_size, "modified": datetime.fromtimestamp(b.stat().st_mtime).isoformat()} for b in backups if b.exists()],
        "count": len(backups),
    }


# ---------- GREP / HASH / WORD COUNT / FIND ----------

@_timed
def op_grep_dir(
    file_path: str,
    pattern: str,
    use_regex: bool = False,
    case_sensitive: bool = True,
    include_hidden: bool = False,
    file_pattern: str = "*",
    exclude_pattern: Optional[str] = None,
    max_matches: int = 1000,
    context_lines: int = 0,
    encoding: Optional[str] = None,
    recursive: bool = True,
    max_depth: int = 0,
) -> dict[str, Any]:
    root = _editor._validate_path(file_path, allow_write=False)
    if not root or not root.exists() or not root.is_dir():
        return {"success": False, "error": "Invalid search directory", "exit_code": EXIT_FILE_NOT_FOUND}

    if not pattern or not pattern.strip():
        return {"success": False, "error": "pattern is required", "exit_code": EXIT_INVALID_INPUT}

    glob_fn = root.rglob if recursive else root.glob
    results: list[dict[str, Any]] = []
    total_matches = 0

    for fpath in glob_fn(file_pattern):
        if total_matches >= max_matches:
            break
        if not fpath.is_file() or _editor._is_binary(fpath):
            continue
        if not include_hidden and any(part.startswith(".") for part in fpath.parts):
            continue
        if exclude_pattern and fnmatch.fnmatch(fpath.name, exclude_pattern):
            continue
        # Depth check
        if max_depth > 0:
            try:
                rel = fpath.relative_to(root)
                if len(rel.parts) > max_depth:
                    continue
            except ValueError:
                continue

        # Inline search (no double-timing)
        try:
            content = fpath.read_text(encoding=encoding or DEFAULT_ENCODING, errors="surrogateescape")
        except Exception:
            continue
        lines = content.splitlines()
        matches: list[dict[str, Any]] = []

        if use_regex:
            flags = 0 if case_sensitive else re.IGNORECASE
            try:
                comp = _regex_cache.get(pattern, flags)
                match_fn = lambda l: bool(comp.search(l))
            except re.error:
                continue
        elif case_sensitive:
            match_fn = lambda l: pattern in l
        else:
            _lower = pattern.lower()
            match_fn = lambda l: _lower in l.lower()

        for i, line in enumerate(lines):
            if total_matches >= max_matches:
                break
            if match_fn(line):
                entry: dict[str, Any] = {"line": i + 1, "content": line}
                if context_lines > 0:
                    ctx_s = max(0, i - context_lines)
                    ctx_e = min(len(lines), i + context_lines + 1)
                    entry["context"] = lines[ctx_s:ctx_e]
                matches.append(entry)
                total_matches += 1

        if matches:
            results.append({"file": str(fpath), "matches": matches})

    return {"success": True, "directory": str(root), "pattern": pattern, "results": results, "total_matches": total_matches}


@_timed
def op_file_hash(file_path: str, algorithm: str = "sha256") -> dict[str, Any]:
    algo = algorithm.lower()
    if algo not in _HASH_ALGORITHMS:
        return {"success": False, "error": f"Unsupported hash algorithm {algorithm}", "exit_code": EXIT_INVALID_INPUT}
    path = _editor._validate_path(file_path, allow_write=False)
    if not path or not path.exists() or not path.is_file():
        return {"success": False, "error": "Invalid target file", "exit_code": EXIT_FILE_NOT_FOUND}
    try:
        h = hashlib.new(algo)
        with open(path, "rb") as f:
            while chunk := f.read(CHUNK_SIZE):
                h.update(chunk)
        return {"success": True, "path": str(path), "algorithm": algo, "hash": h.hexdigest()}
    except Exception as exc:
        return {"success": False, "error": str(exc)}


@_timed
def op_word_count(file_path: str, encoding: Optional[str] = None) -> dict[str, Any]:
    path = _editor._validate_path(file_path, allow_write=False)
    if not path or not path.exists() or not path.is_file():
        return {"success": False, "error": "Invalid target file", "exit_code": EXIT_FILE_NOT_FOUND}
    if _editor._is_binary(path):
        return {"success": False, "error": "Binary file detected", "exit_code": EXIT_INVALID_INPUT}
    try:
        lines, words, chars = 0, 0, 0
        with open(path, encoding=encoding or DEFAULT_ENCODING, errors="surrogateescape") as f:
            for line in f:
                lines += 1
                words += len(line.split())
                chars += len(line)
        return {"success": True, "path": str(path), "lines": lines, "words": words, "characters": chars, "bytes": path.stat().st_size}
    except Exception as exc:
        return {"success": False, "error": str(exc)}


@_timed
def op_find_files(
    file_path: str,
    file_pattern: str = "*",
    exclude_pattern: Optional[str] = None,
    include_hidden: bool = False,
    file_type: str = "any",
    recursive: bool = True,
    max_results: int = MAX_FIND_RESULTS,
    min_size: Optional[int] = None,
    max_size_filter: Optional[int] = None,
    modified_after: Optional[float] = None,
    modified_before: Optional[float] = None,
    max_depth: int = 0,
) -> dict[str, Any]:
    root = _editor._validate_path(file_path, allow_write=False)
    if not root or not root.exists() or not root.is_dir():
        return {"success": False, "error": "Invalid root search directory", "exit_code": EXIT_FILE_NOT_FOUND}

    glob_fn = root.rglob if recursive else root.glob
    results: list[dict[str, Any]] = []

    for entry in glob_fn(file_pattern):
        if len(results) >= max_results:
            break
        if not include_hidden and any(p.startswith(".") for p in entry.parts):
            continue
        if exclude_pattern and fnmatch.fnmatch(entry.name, exclude_pattern):
            continue
        if file_type == "file" and not entry.is_file():
            continue
        if file_type == "dir" and not entry.is_dir():
            continue
        if max_depth > 0:
            try:
                rel = entry.relative_to(root)
                if len(rel.parts) > max_depth:
                    continue
            except ValueError:
                continue

        try:
            st = entry.stat()
        except OSError:
            continue

        if min_size is not None and st.st_size < min_size:
            continue
        if max_size_filter is not None and st.st_size > max_size_filter:
            continue
        if modified_after is not None and st.st_mtime < modified_after:
            continue
        if modified_before is not None and st.st_mtime > modified_before:
            continue

        results.append({
            "path": str(entry),
            "name": entry.name,
            "is_file": entry.is_file(),
            "is_dir": entry.is_dir(),
            "size": st.st_size,
            "modified": datetime.fromtimestamp(st.st_mtime).isoformat(),
        })

    return {"success": True, "directory": str(root), "results": results, "count": len(results)}


# ---------- HEAD / TAIL ----------

@_timed
def op_head(file_path: str, n_lines: int = 10, encoding: Optional[str] = None) -> dict[str, Any]:
    path = _editor._validate_path(file_path, allow_write=False)
    if not path or not path.exists() or not path.is_file():
        return {"success": False, "error": "Invalid target file", "exit_code": EXIT_FILE_NOT_FOUND}
    if _editor._is_binary(path):
        return {"success": False, "error": "Binary file detected", "exit_code": EXIT_INVALID_INPUT}
    try:
        selected: list[str] = []
        with open(path, encoding=encoding or DEFAULT_ENCODING, errors="surrogateescape") as f:
            for i, line in enumerate(f):
                if i >= n_lines:
                    break
                selected.append(line.rstrip("\r\n"))
        return {"success": True, "path": str(path), "lines": selected, "content": "\n".join(selected), "line_count": len(selected)}
    except Exception as exc:
        return {"success": False, "error": str(exc)}


@_timed
def op_tail(file_path: str, n_lines: int = 10, encoding: Optional[str] = None) -> dict[str, Any]:
    path = _editor._validate_path(file_path, allow_write=False)
    if not path or not path.exists() or not path.is_file():
        return {"success": False, "error": "Invalid target file", "exit_code": EXIT_FILE_NOT_FOUND}
    if _editor._is_binary(path):
        return {"success": False, "error": "Binary file detected", "exit_code": EXIT_INVALID_INPUT}
    try:
        buf: deque[str] = deque(maxlen=n_lines)
        with open(path, encoding=encoding or DEFAULT_ENCODING, errors="surrogateescape") as f:
            for line in f:
                buf.append(line.rstrip("\r\n"))
        lines = list(buf)
        return {"success": True, "path": str(path), "lines": lines, "content": "\n".join(lines), "line_count": len(lines)}
    except Exception as exc:
        return {"success": False, "error": str(exc)}


# ---------- ARCHIVE / EXTRACT ----------

@_timed
def op_archive(source_path: str, target_path: str, compression: str = "deflate", recursive: bool = True) -> dict[str, Any]:
    src = _editor._validate_path(source_path, allow_write=False)
    arc = _editor._validate_path(target_path, allow_write=True)
    if not src or not arc or not src.exists():
        return {"success": False, "error": "Invalid source or archive path", "exit_code": EXIT_FILE_NOT_FOUND}

    cmap = {"deflate": zipfile.ZIP_DEFLATED, "store": zipfile.ZIP_STORED, "bz2": zipfile.ZIP_BZIP2, "lzma": zipfile.ZIP_LZMA}
    method = cmap.get(compression, zipfile.ZIP_DEFLATED)

    try:
        file_count = 0
        with zipfile.ZipFile(arc, "w", compression=method) as zf:
            if src.is_file():
                zf.write(src, src.name)
                file_count = 1
            else:
                base_dir = src
                glob_fn = src.rglob("*") if recursive else src.glob("*")
                for f in sorted(glob_fn):
                    if f.is_file():
                        arcname = f.relative_to(base_dir)
                        zf.write(f, arcname)
                        file_count += 1
        return {"success": True, "source": str(src), "archive": str(arc), "files_archived": file_count, "archive_size": arc.stat().st_size}
    except Exception as exc:
        return {"success": False, "error": str(exc)}


@_timed
def op_extract(archive_path: str, target_path: str, password: Optional[str] = None) -> dict[str, Any]:
    arc = _editor._validate_path(archive_path, allow_write=False)
    dst = _editor._validate_path(target_path, allow_write=True)
    if not arc or not dst or not arc.exists() or not zipfile.is_zipfile(arc):
        return {"success": False, "error": "Invalid ZIP archive", "exit_code": EXIT_INVALID_INPUT}

    # Guard: archive size
    if arc.stat().st_size > MAX_ARCHIVE_SIZE:
        return {"success": False, "error": f"Archive exceeds maximum size ({MAX_ARCHIVE_SIZE} bytes)", "exit_code": EXIT_INVALID_INPUT}

    try:
        dst.mkdir(parents=True, exist_ok=True)
        with zipfile.ZipFile(arc, "r") as zf:
            pwd = password.encode() if password else None
            # Zip-slip protection
            for member in zf.namelist():
                if not _check_zip_slip(member, dst):
                    return {"success": False, "error": f"Zip-slip path traversal detected: {member}", "exit_code": EXIT_INVALID_INPUT}
            zf.extractall(dst, pwd=pwd)
            return {"success": True, "archive": str(arc), "target": str(dst), "files_extracted": len(zf.namelist())}
    except RuntimeError as exc:
        if "password" in str(exc).lower():
            return {"success": False, "error": "Incorrect or missing archive password", "exit_code": EXIT_INVALID_INPUT}
        return {"success": False, "error": str(exc)}
    except Exception as exc:
        return {"success": False, "error": str(exc)}


# ---------- TEMPLATE WRITE ----------

@_timed
def op_template_write(
    file_path: str,
    content: str,
    variables: dict[str, str],
    encoding: Optional[str] = None,
    undefined_var: str = "error",
) -> dict[str, Any]:
    if content is None:
        return {"success": False, "error": "content parameter required", "exit_code": EXIT_INVALID_INPUT}

    def _repl(m: re.Match[str]) -> str:
        k = m.group(1)
        if k in variables:
            return str(variables[k])
        # Check env vars
        env_val = os.environ.get(k) or get_agent_var(k)
        if env_val:
            return env_val
        if undefined_var == "empty":
            return ""
        if undefined_var == "keep":
            return m.group(0)
        raise KeyError(k)

    try:
        rendered = _TEMPLATE_RE.sub(_repl, str(content))
        opts = EditOptions(file_path=file_path, content=rendered, encoding=encoding)
        return op_write(opts)
    except KeyError as exc:
        return {"success": False, "error": f"Undefined template variable: {exc}", "exit_code": EXIT_INVALID_INPUT}
    except Exception as exc:
        return {"success": False, "error": str(exc)}


# ---------- BATCH / BATCH_EDIT ----------

def _parse_edits(edits_input: Any) -> list[dict[str, Any]]:
    """Parse batch edits input from JSON string, list of dicts, @file, or file path."""
    if isinstance(edits_input, list):
        return edits_input
    if edits_input is None:
        return []
    if isinstance(edits_input, str):
        edits_str = edits_input.strip()
        if edits_str.startswith("@"):
            path_obj = Path(edits_str[1:].strip()).expanduser().resolve()
            if not path_obj.is_file():
                raise ValueError(f"Batch edits file not found: {edits_str}")
            data = json.loads(path_obj.read_text(encoding="utf-8"))
            return data if isinstance(data, list) else [data]
        if edits_str.startswith("[") or edits_str.startswith("{"):
            try:
                data = json.loads(edits_str)
                return data if isinstance(data, list) else [data]
            except json.JSONDecodeError:
                pass
        # Try as file path
        try:
            path_obj = Path(edits_str).expanduser().resolve()
            if path_obj.is_file():
                data = json.loads(path_obj.read_text(encoding="utf-8"))
                return data if isinstance(data, list) else [data]
        except (OSError, json.JSONDecodeError):
            pass
        # Last resort: try JSON
        try:
            data = json.loads(edits_str)
            return data if isinstance(data, list) else [data]
        except json.JSONDecodeError as e:
            raise ValueError(f"Invalid JSON string or edits file path: {e}") from e
    return []


@_timed
def op_batch(ops: list[dict[str, Any]], continue_on_error: bool = False) -> dict[str, Any]:
    if not ops or not isinstance(ops, list):
        return {"success": False, "error": "ops parameter must be a non-empty list", "exit_code": EXIT_INVALID_INPUT}

    results = []
    for i, op_data in enumerate(ops):
        try:
            opts = EditOptions(**op_data)
            res = _run(opts)
            results.append({"index": i, "operation": opts.operation, "result": res})
            if not res.get("success") and not continue_on_error:
                return {"success": False, "error": f"Batch halted at operation {i}", "results": results, "completed": i + 1, "total": len(ops)}
        except Exception as exc:
            results.append({"index": i, "operation": op_data.get("operation", "unknown"), "result": {"success": False, "error": str(exc)}})
            if not continue_on_error:
                return {"success": False, "error": f"Batch halted at operation {i}: {exc}", "results": results, "completed": i + 1, "total": len(ops)}

    return {"success": True, "results": results, "count": len(results)}


@_timed
def op_batch_edit(
    file_path: str,
    edits: list[dict[str, Any]],
    encoding: Optional[str] = None,
    max_backups: int = MAX_BACKUPS,
    continue_on_error: bool = False,
    dry_run: bool = False,
) -> dict[str, Any]:
    if not edits or not isinstance(edits, list):
        return {"success": False, "error": "edits parameter must be a non-empty list", "exit_code": EXIT_INVALID_INPUT}

    res = _editor._read_content(file_path, encoding)
    if not res["success"]:
        return res

    content = res["content"]
    path: Path = res["path"]
    orig_content = content
    edit_results = []

    for i, edit in enumerate(edits):
        op = (edit.get("operation") or edit.get("action") or "replace").lower()
        try:
            if op == "replace":
                srch = str(edit.get("search_text") or edit.get("search") or edit.get("old") or edit.get("find") or "")
                repl = str(edit.get("replacement") or edit.get("content") or edit.get("new") or edit.get("replace") or "")
                if edit.get("decode_escapes"):
                    srch, repl = _unescape_string(srch), _unescape_string(repl)
                use_regex = _as_bool(edit.get("regex", False))
                ignore_case = _as_bool(edit.get("ignore_case", False))
                dotall = _as_bool(edit.get("dotall", False))
                max_repl = _as_int(edit.get("max_replacements"))
                content, _ = _replace_in_content(
                    content, _normalize_newlines(srch), _normalize_newlines(repl),
                    regex=use_regex, ignore_case=ignore_case, dotall=dotall,
                    max_replacements=max_repl,
                )
            elif op == "insert_line":
                ln = _as_int(edit.get("line_number") or edit.get("line"), 1) or 1
                text = str(edit.get("content") or "") + "\n"
                lines = content.splitlines(keepends=True)
                lines.insert(max(0, min(ln - 1, len(lines))), text)
                content = "".join(lines)
            elif op == "delete_line":
                ln = _as_int(edit.get("line_number") or edit.get("line"), 1) or 1
                lines = content.splitlines(keepends=True)
                if 1 <= ln <= len(lines):
                    lines.pop(ln - 1)
                    content = "".join(lines)
            elif op == "append":
                text = str(edit.get("content") or "")
                if edit.get("decode_escapes"):
                    text = _unescape_string(text)
                sep = "\n" if content and not content.endswith("\n") else ""
                content += sep + text
            elif op == "prepend":
                text = str(edit.get("content") or "")
                if edit.get("decode_escapes"):
                    text = _unescape_string(text)
                sep = "\n" if content and not text.endswith("\n") else ""
                content = text + sep + content
            else:
                raise ValueError(f"Unknown batch_edit operation: {op}")

            edit_results.append({"index": i, "operation": op, "success": True})
        except Exception as exc:
            edit_results.append({"index": i, "operation": op, "success": False, "error": str(exc)})
            if not continue_on_error:
                return {"success": False, "error": f"batch_edit failed at edit {i}: {exc}", "completed": edit_results}

    diff_text = _generate_diff(orig_content, content, str(path))
    if dry_run:
        return {"success": True, "path": str(path), "mode": "dry-run", "diff": diff_text, "edits": edit_results}

    try:
        backup = _editor._make_backup(path, max_backups)
        _editor._atomic_write(path, content, res.get("encoding_used", DEFAULT_ENCODING))
        return {"success": True, "path": str(path), "edits": edit_results, "diff": diff_text, "backup_path": str(backup) if backup else None}
    except Exception as exc:
        return {"success": False, "error": str(exc)}


# ==============================================================================
# SECTION 8: Top-Level Execution Engine & Dispatcher
# ==============================================================================

def _run(options: EditOptions) -> dict[str, Any]:
    """Dispatch options to appropriate operation function."""
    op = options.operation
    fpath = options.file_path

    # Handle stdin content injection
    if options.stdin and options.content is None:
        options.content = _read_stdin()

    # Batch operations
    if op == "batch":
        ops_payload = options.ops
        if isinstance(ops_payload, str):
            try:
                ops_payload = json.loads(ops_payload)
            except json.JSONDecodeError:
                try:
                    p = Path(ops_payload).expanduser().resolve()
                    if p.is_file():
                        ops_payload = json.loads(p.read_text(encoding="utf-8"))
                except Exception:
                    pass
        if not isinstance(ops_payload, list):
            return {"success": False, "error": "Invalid or missing ops payload for batch", "exit_code": EXIT_INVALID_INPUT}
        return op_batch(ops_payload, continue_on_error=options.continue_on_error)

    if op == "batch_edit":
        edits_payload = options.edits
        if isinstance(edits_payload, str):
            try:
                edits_payload = _parse_edits(edits_payload)
            except (ValueError, json.JSONDecodeError) as exc:
                return {"success": False, "error": str(exc), "exit_code": EXIT_INVALID_INPUT}
        if not isinstance(edits_payload, list):
            return {"success": False, "error": "Invalid or missing edits payload for batch_edit", "exit_code": EXIT_INVALID_INPUT}
        return op_batch_edit(
            fpath, edits_payload, encoding=options.encoding,
            max_backups=options.max_backups, continue_on_error=options.continue_on_error,
            dry_run=options.dry_run,
        )

    # All other operations require file_path
    if not fpath:
        return {"success": False, "error": "file_path parameter is required for operation", "exit_code": EXIT_INVALID_INPUT}

    dispatch_map: Dict[str, Callable[[], dict[str, Any]]] = {
        "read": lambda: op_read(fpath, options.max_size, options.encoding, options.show_lines, options.start_line, options.end_line),
        "view": lambda: op_read(fpath, options.max_size, options.encoding, options.show_lines, options.start_line, options.end_line),
        "read_lines": lambda: op_read_lines(fpath, options.line_number or options.start_line or 1, options.end_line or sys.maxsize, options.encoding),
        "write": lambda: op_write(options),
        "append": lambda: op_append(fpath, options.content or "", options.encoding, options.add_newline, options.max_write_size, options.decode_escapes, options.preserve_newlines),
        "prepend": lambda: op_prepend(fpath, options.content or "", options.encoding, options.add_newline, options.max_write_size, options.decode_escapes, options.preserve_newlines),
        "replace": lambda: op_replace(options),
        "count": lambda: op_file_search(fpath, options.search_text or "", use_regex=options.regex, case_sensitive=options.case_sensitive, encoding=options.encoding),
        "insert_line": lambda: op_insert_line(fpath, options.line_number or 1, options.content or "", options.encoding, options.max_backups, options.decode_escapes),
        "insert": lambda: op_insert_line(fpath, options.line_number or 1, options.content or "", options.encoding, options.max_backups, options.decode_escapes),
        "delete_line": lambda: op_delete_line(fpath, options.line_number or 1, options.encoding, options.max_backups),
        "delete": lambda: op_delete_line(fpath, options.line_number, options.encoding, options.max_backups) if options.line_number else op_delete(fpath, options.recursive),
        "replace_lines": lambda: op_replace_lines(fpath, options.start_line or 1, options.end_line or 1, options.content or "", options.encoding, options.max_backups, options.dry_run, options.decode_escapes),
        "search": lambda: op_file_search(fpath, options.search_text or "", options.regex, options.case_sensitive, options.line_context or options.context_lines, options.encoding, options.max_matches),
        "file_search": lambda: op_file_search(fpath, options.search_text or "", options.regex, options.case_sensitive, options.line_context or options.context_lines, options.encoding, options.max_matches),
        "copy": lambda: op_copy(fpath, options.target_path or "", options.preserve_metadata, options.recursive),
        "move": lambda: op_move(fpath, options.target_path or ""),
        "info": lambda: op_info(fpath),
        "create_dir": lambda: op_create_dir(fpath, options.parents),
        "list_dir": lambda: op_list_dir(fpath, options.include_hidden, options.sort_by, options.descending),
        "diff": lambda: op_diff(fpath, options.target_path, options.encoding, options.context_lines),
        "truncate": lambda: op_truncate(fpath, options.truncate_size, options.max_backups),
        "set_permissions": lambda: op_set_permissions(fpath, options.mode or "644"),
        "normalize_line_endings": lambda: op_normalize_line_endings(fpath, options.to_type or "lf", options.encoding, options.max_backups),
        "revert_to_backup": lambda: op_revert_to_backup(fpath, options.backup_timestamp, options.max_backups),
        "list_backups": lambda: op_list_backups(fpath),
        "grep_dir": lambda: op_grep_dir(fpath, options.search_text or "", options.regex, options.case_sensitive, options.include_hidden, options.file_pattern, options.exclude_pattern, options.max_matches, options.line_context or options.context_lines, options.encoding, options.recursive, options.max_depth),
        "file_hash": lambda: op_file_hash(fpath, options.algorithm),
        "word_count": lambda: op_word_count(fpath, options.encoding),
        "find_files": lambda: op_find_files(fpath, options.file_pattern, options.exclude_pattern, options.include_hidden, options.file_type, options.recursive, options.max_results, options.min_size, options.max_size_filter, options.modified_after, options.modified_before, options.max_depth),
        "head": lambda: op_head(fpath, options.n_lines, options.encoding),
        "tail": lambda: op_tail(fpath, options.n_lines, options.encoding),
        "compare_files": lambda: op_compare_files(fpath, options.target_path or "", options.compare_mode, options.encoding),
        "archive": lambda: op_archive(fpath, options.target_path or "", options.compression, options.recursive),
        "extract": lambda: op_extract(fpath, options.target_path or "", options.password),
        "template_write": lambda: op_template_write(fpath, options.content or "", options.variables, options.encoding, options.undefined_var),
    }

    fn = dispatch_map.get(op)
    if not fn:
        return {"success": False, "error": f"Unsupported or unknown operation '{op}'", "exit_code": EXIT_INVALID_INPUT}

    return fn()


def execute_tool(options: EditOptions) -> dict[str, Any]:
    """Public API entrypoint."""
    return _run(options)


def generate_tool_schema() -> dict[str, Any]:
    """Generate OpenAI/AIChat Function Tool Schema definition."""
    return {
        "name": "edit_file",
        "description": f"Unified file editing and directory manipulation engine v{__version__} supporting 38 file and text operations.",
        "parameters": {
            "type": "object",
            "properties": {
                "operation": {
                    "type": "string",
                    "enum": sorted(VALID_OPERATIONS),
                    "description": "Operation to perform",
                },
                "file_path": {"type": "string", "description": "Target file or directory path"},
                "target_path": {"type": "string", "description": "Secondary path for copy/move/diff/archive operations"},
                "content": {"type": "string", "description": "Text content to write, append, prepend, or insert"},
                "search": {"type": "string", "description": "Search text string or regex pattern"},
                "replacement": {"type": "string", "description": "Replacement text for replace operation"},
                "edits": {"type": "string", "description": "JSON array string or file path for batch_edit mode"},
                "ops": {"type": "string", "description": "JSON array string or file path for batch mode"},
                "line": {"type": "integer", "description": "Line number (1-based) for line operations"},
                "start_line": {"type": "integer", "description": "Start line for range operations"},
                "end_line": {"type": "integer", "description": "End line for range operations"},
                "encoding": {"type": "string", "description": "File encoding (default: auto-detect)"},
                "regex": {"type": "boolean", "description": "Enable regex pattern matching"},
                "ignore_case": {"type": "boolean", "description": "Case-insensitive search"},
                "decode_escapes": {"type": "boolean", "description": "Decode escape sequences in text strings"},
                "backup": {"type": "boolean", "description": "Create timestamped backup before modifying file"},
                "dry_run": {"type": "boolean", "description": "Simulate mutation without modifying disk"},
                "recursive": {"type": "boolean", "description": "Process directories recursively"},
                "preserve_newlines": {"type": "boolean", "description": "Preserve original newline style"},
                "include_hidden": {"type": "boolean", "description": "Include hidden files"},
                "file_pattern": {"type": "string", "description": "Glob pattern filter"},
                "max_matches": {"type": "integer", "description": "Maximum search matches"},
                "context_lines": {"type": "integer", "description": "Context lines for search/diff"},
                "algorithm": {"type": "string", "description": "Hash algorithm"},
                "n_lines": {"type": "integer", "description": "Line count for head/tail"},
                "compression": {"type": "string", "description": "Archive compression type"},
                "timeout": {"type": "number", "description": "Operation timeout in seconds"},
            },
            "required": ["operation", "file_path"],
        },
    }


# ==============================================================================
# SECTION 9: Output Routing
# ==============================================================================

def write_llm_output(data: dict[str, Any]) -> None:
    """Write JSON payload to stdout or specified LLM_OUTPUT path."""
    out_path = os.environ.get("LLM_OUTPUT", "/dev/stdout") or "/dev/stdout"
    payload = json.dumps(data, ensure_ascii=False, cls=ToolJSONEncoder)

    if out_path.strip() in _DIRECT_STDOUT_TARGETS:
        try:
            if hasattr(sys.stdout, "buffer"):
                sys.stdout.buffer.write((payload + "\n").encode("utf-8"))
                sys.stdout.buffer.flush()
            else:
                sys.stdout.write(payload + "\n")
                sys.stdout.flush()
        except (UnicodeEncodeError, OSError):
            sys.stdout.write(json.dumps(data, ensure_ascii=True, cls=ToolJSONEncoder) + "\n")
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


def print_human_readable_ui(data: dict[str, Any], no_color: bool = False) -> None:
    """Render Cyber-Neon human friendly UI on stderr."""
    if not _display_ui(no_color):
        return

    success = data.get("success", False)
    status_color = NEON_GREEN if success else NEON_RED
    status_symbol = "✓" if success else "✗"
    box_w = 72
    border = "─" * box_w

    _cprint(f"{NEON_PURPLE}╭{border}╮{RESET}", no_color=no_color)
    _cprint(
        f"{NEON_PURPLE}│{RESET} {NEON_PINK}⚡ [UNIVERSAL FILE WEAVER v{__version__}]{RESET} "
        f"{status_color}{BOLD}{status_symbol} {'SUCCESS' if success else 'FAILED'}{RESET}",
        no_color=no_color,
    )
    _cprint(f"{NEON_PURPLE}├{border}┤{RESET}", no_color=no_color)

    target = data.get("path") or data.get("directory") or data.get("source") or data.get("archive") or "N/A"
    _cprint(f"{NEON_PURPLE}│{RESET} {NEON_CYAN}Target:{RESET}      {target}", no_color=no_color)
    _cprint(f"{NEON_PURPLE}│{RESET} {NEON_CYAN}Operation:{RESET}   {NEON_YELLOW}{data.get('operation', data.get('action', 'N/A'))}{RESET}", no_color=no_color)
    _cprint(f"{NEON_PURPLE}│{RESET} {NEON_CYAN}Duration:{RESET}    {DIM}{data.get('duration_ms', 0)}ms{RESET}", no_color=no_color)

    if "encoding" in data and data["encoding"]:
        _cprint(f"{NEON_PURPLE}│{RESET} {NEON_CYAN}Encoding:{RESET}    {data['encoding']}", no_color=no_color)
    if "size" in data:
        _cprint(f"{NEON_PURPLE}│{RESET} {NEON_CYAN}Size:{RESET}        {data['size']} bytes", no_color=no_color)
    if "replacements" in data:
        _cprint(f"{NEON_PURPLE}│{RESET} {NEON_CYAN}Replaced:{RESET}    {NEON_GREEN}{data['replacements']}{RESET} occurrence(s)", no_color=no_color)
    if "match_count" in data:
        _cprint(f"{NEON_PURPLE}│{RESET} {NEON_CYAN}Matches:{RESET}     {NEON_GREEN}{data['match_count']}{RESET}", no_color=no_color)
    if "line_count" in data:
        _cprint(f"{NEON_PURPLE}│{RESET} {NEON_CYAN}Lines:{RESET}       {data['line_count']}", no_color=no_color)
    if data.get("dry_run") or data.get("mode") == "dry-run":
        _cprint(f"{NEON_PURPLE}│{RESET} {NEON_YELLOW}DRY-RUN:{RESET}     Simulation only — no changes written", no_color=no_color)

    if not success and "error" in data:
        _cprint(f"{NEON_PURPLE}├{border}┤{RESET}", no_color=no_color)
        _cprint(f"{NEON_PURPLE}│{RESET} {NEON_RED}Error:{RESET}       {data['error']}", no_color=no_color)

    if "diff" in data and data["diff"]:
        _cprint(f"{NEON_PURPLE}├{border}┤{RESET}", no_color=no_color)
        _cprint(f"{NEON_PURPLE}│{RESET} {BOLD}Diff Preview:{RESET}", no_color=no_color)
        for line in data["diff"].splitlines()[:15]:
            if line.startswith("+") and not line.startswith("+++"):
                colored = f"{NEON_GREEN}{line}{RESET}"
            elif line.startswith("-") and not line.startswith("---"):
                colored = f"{NEON_RED}{line}{RESET}"
            elif line.startswith("@"):
                colored = f"{NEON_CYAN}{line}{RESET}"
            else:
                colored = line
            _cprint(f"{NEON_PURPLE}│{RESET}   {colored}", no_color=no_color)

    _cprint(f"{NEON_PURPLE}╰{border}╯{RESET}", no_color=no_color)


# ==============================================================================
# SECTION 10: AIChat Entrypoint
# ==============================================================================

def run(**kwargs: Any) -> dict[str, Any]:
    """Programmatic entrypoint for AIChat integration."""
    opts = EditOptions(**kwargs)
    res = _run(opts)
    res["operation"] = opts.operation
    print_human_readable_ui(res, no_color=opts.no_color)
    write_llm_output(res)
    return res


# ==============================================================================
# SECTION 11: CLI Parser & Main Entrypoint
# ==============================================================================

def _parse_var_pairs(pairs: Optional[List[str]]) -> dict[str, str]:
    """Parse KEY=VALUE pairs from repeatable --var / --env-var options."""
    result: dict[str, str] = {}
    if not pairs:
        return result
    for pair in pairs:
        if "=" in pair:
            k, v = pair.split("=", 1)
            result[k.strip()] = v.strip()
    return result


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="edit_file.py",
        description=f"Pyrmethus Universal File Weaver v{__version__} — Unified 38-Operation Suite",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--operation", "--action", "-a", dest="operation", help="Operation to perform")
    parser.add_argument("file_path_pos", nargs="?", help="Positional primary file path")
    parser.add_argument("--file-path", "--target", "-f", "-t", dest="file_path", help="Primary file path")
    parser.add_argument("--target-path", dest="target_path", help="Secondary path")
    parser.add_argument("--content", "-c", help="Content text string")
    parser.add_argument("--search", "--search-text", "--pattern", "-s", "-p", dest="search", help="Search text or pattern")
    parser.add_argument("--replacement", "-r", help="Replacement text")
    parser.add_argument("--edits", "-e", help="JSON array string or file path for batch_edit mode")
    parser.add_argument("--ops", help="JSON array string or file path for batch mode")
    parser.add_argument("--line", "--line-number", "-n", dest="line", type=int, help="Line number (1-based)")
    parser.add_argument("--start-line", type=int, help="Start line (1-based)")
    parser.add_argument("--end-line", type=int, help="End line")
    parser.add_argument("--encoding", default=None, help="File encoding (default: auto-detect)")
    parser.add_argument("--max-size", type=int, default=DEFAULT_MAX_READ)
    parser.add_argument("--max-write-size", type=int, default=DEFAULT_MAX_WRITE)
    parser.add_argument("--max-file-bytes", type=int, default=None)
    parser.add_argument("--max-replacements", type=int, default=None)
    parser.add_argument("--max-backups", type=int, default=MAX_BACKUPS)
    parser.add_argument("--max-matches", type=int, default=1000)
    parser.add_argument("--context-lines", "--line-context", dest="context_lines", type=int, default=3)
    parser.add_argument("--truncate-size", type=int, default=0)
    parser.add_argument("--mode", help="Octal permission mode")
    parser.add_argument("--to-type", choices=["lf", "crlf"], help="Line ending type")
    parser.add_argument("--backup-timestamp", help="Backup timestamp for revert")
    parser.add_argument("--algorithm", default="sha256", choices=sorted(_HASH_ALGORITHMS))
    parser.add_argument("--n-lines", type=int, default=10)
    parser.add_argument("--compare-mode", choices=["bytes", "text"], default="bytes")
    parser.add_argument("--compression", choices=["deflate", "store", "bz2", "lzma"], default="deflate")
    parser.add_argument("--password", help="Archive password")
    parser.add_argument("--undefined-var", choices=["error", "keep", "empty"], default="error")
    parser.add_argument("--file-pattern", default="*")
    parser.add_argument("--exclude-pattern", default=None)
    parser.add_argument("--min-size", type=int, default=None)
    parser.add_argument("--max-size-filter", type=int, default=None)
    parser.add_argument("--modified-after", type=float, default=None)
    parser.add_argument("--modified-before", type=float, default=None)
    parser.add_argument("--file-type", choices=["any", "file", "dir"], default="any")
    parser.add_argument("--max-results", type=int, default=MAX_FIND_RESULTS)
    parser.add_argument("--max-depth", type=int, default=0)
    parser.add_argument("--sort-by", choices=["name", "size", "modified", "type"], default="name")
    parser.add_argument("--var", action="append", dest="var_pairs", help="Template variable KEY=VALUE (repeatable)")
    parser.add_argument("--env-var", action="append", dest="env_var_pairs", help="Env var override KEY=VALUE (repeatable)")
    parser.add_argument("--timeout", type=float, default=DEFAULT_TIMEOUT, help="Operation timeout in seconds")
    parser.add_argument("--regex", "--use-regex", action="store_true", dest="regex")
    parser.add_argument("--ignore-case", "--case-insensitive", action="store_true", dest="ignore_case")
    parser.add_argument("--no-global", action="store_false", dest="global_replace", default=True)
    parser.add_argument("--decode-escapes", action="store_true")
    parser.add_argument("--dotall", action="store_true")
    parser.add_argument("--preserve-newlines", dest="preserve_newlines", action="store_true", default=True)
    parser.add_argument("--no-preserve-newlines", dest="preserve_newlines", action="store_false")
    parser.add_argument("--backup", action="store_true", default=False)
    parser.add_argument("--no-backup", dest="backup", action="store_false")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--show-lines", action="store_true", default=True)
    parser.add_argument("--no-lines", dest="show_lines", action="store_false")
    parser.add_argument("--add-newline", dest="add_newline", action="store_true", default=True)
    parser.add_argument("--no-add-newline", dest="add_newline", action="store_false")
    parser.add_argument("--include-hidden", action="store_true")
    parser.add_argument("--descending", action="store_true")
    parser.add_argument("--parents", action="store_true", default=True)
    parser.add_argument("--recursive", action="store_true")
    parser.add_argument("--preserve-metadata", action="store_true", default=True)
    parser.add_argument("--continue-on-error", action="store_true")
    parser.add_argument("--schema", action="store_true")
    parser.add_argument("--no-color", action="store_true")
    parser.add_argument("--verbose", "-v", action="store_true")
    parser.add_argument("--stdin", action="store_true", help="Read content from stdin")
    parser.add_argument("--force", action="store_true", help="Overwrite without confirmation")
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    return parser


def main() -> int:
    """CLI Execution Entrypoint."""
    parser = _build_parser()
    args = parser.parse_args()

    if args.schema:
        sys.stdout.write(json.dumps(generate_tool_schema(), indent=2) + "\n")
        sys.stdout.flush()
        return EXIT_SUCCESS

    fpath = args.file_path or args.file_path_pos
    variables = _parse_var_pairs(args.var_pairs)

    # Apply env-var overrides
    if args.env_var_pairs:
        for k, v in _parse_var_pairs(args.env_var_pairs).items():
            os.environ[k] = v

    if args.verbose:
        logging.basicConfig(level=logging.DEBUG, format="[%(levelname)s] %(message)s", force=True)

    opts = EditOptions(
        operation=args.operation,
        file_path=fpath,
        target_path=args.target_path,
        content=args.content,
        search=args.search,
        replacement=args.replacement,
        edits=args.edits,
        ops=args.ops,
        line=args.line,
        start_line=args.start_line,
        end_line=args.end_line,
        encoding=args.encoding,
        max_size=args.max_size,
        max_write_size=args.max_write_size,
        max_file_bytes=args.max_file_bytes if args.max_file_bytes is not None else _resolve_max_file_bytes(),
        max_replacements=args.max_replacements,
        max_backups=args.max_backups,
        max_matches=args.max_matches,
        context_lines=args.context_lines,
        truncate_size=args.truncate_size,
        mode=args.mode,
        to_type=args.to_type,
        backup_timestamp=args.backup_timestamp,
        algorithm=args.algorithm,
        n_lines=args.n_lines,
        compare_mode=args.compare_mode,
        compression=args.compression,
        password=args.password,
        undefined_var=args.undefined_var,
        file_pattern=args.file_pattern,
        exclude_pattern=args.exclude_pattern,
        min_size=args.min_size,
        max_size_filter=args.max_size_filter,
        modified_after=args.modified_after,
        modified_before=args.modified_before,
        file_type=args.file_type,
        max_results=args.max_results,
        max_depth=args.max_depth,
        sort_by=args.sort_by,
        variables=variables,
        timeout=args.timeout,
        regex=args.regex,
        ignore_case=args.ignore_case,
        global_replace=args.global_replace,
        decode_escapes=args.decode_escapes,
        dotall=args.dotall,
        preserve_newlines=args.preserve_newlines,
        backup=args.backup,
        dry_run=args.dry_run,
        show_lines=args.show_lines,
        add_newline=args.add_newline,
        include_hidden=args.include_hidden,
        descending=args.descending,
        parents=args.parents,
        recursive=args.recursive,
        preserve_metadata=args.preserve_metadata,
        continue_on_error=args.continue_on_error,
        no_color=args.no_color,
        verbose=args.verbose,
        stdin=args.stdin,
        force=args.force,
    )

    res = execute_tool(opts)
    res["operation"] = opts.operation
    print_human_readable_ui(res, no_color=opts.no_color)
    write_llm_output(res)

    exit_code = res.get("exit_code")
    if exit_code is not None:
        return int(exit_code)
    return EXIT_SUCCESS if res.get("success") else EXIT_ERROR



# ------------------------------------------------------------------------------
# v4.1 hardening helpers
# ------------------------------------------------------------------------------
def validate_runtime_environment() -> None:
    """Lightweight runtime sanity checks for AIChat/argc execution."""
    if os.environ.get("LLM_TOOL_MAX_FILE_BYTES"):
        try:
            int(os.environ["LLM_TOOL_MAX_FILE_BYTES"])
        except ValueError:
            raise ToolError("LLM_TOOL_MAX_FILE_BYTES must be an integer",
                            EXIT_INVALID_INPUT)


if __name__ == "__main__":
    sys.exit(main())
