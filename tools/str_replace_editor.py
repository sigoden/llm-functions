#!/usr/bin/env python3
# ==============================================================================
# str_replace_editor.py — Pyrmethus AIChat Tool v2.2.0-ASCENDED
# argc/aichat compatible · Enhanced Human-Readable Colorized Outputs & Robustness
#
# @describe A robust file editor tool supporting view, literal/regex search,
#           replace, count, write, batch, append, prepend, insert, and delete actions.
#           Supports multiline replacements, escape decoding, dotall regex, batch edits,
#           newline preservation, encoding fallback, BOM preservation, atomic writes,
#           direct-write fallback, and safer I/O.
#
# @option --action! <ACTION>             Operation: view, replace, count, write, batch, append, prepend, insert, delete (required)
# @option --file-path! <PATH>            Path to the target file (required)
# @option --search <TEXT>                Search string/pattern (required for replace/count/delete by pattern)
# @option --replacement <TEXT>           Replacement string (required for replace)
# @option --content <TEXT>               Content for write, append, prepend, or insert actions
# @option --edits <JSON_OR_FILE>         JSON array string or file path for batch replacements
# @option --start-line <NUM>             Starting line (1-based) for view, insert, or delete
# @option --end-line <NUM>               Ending line for view or line-range delete
# @option --line <NUM>                   Alias for --start-line
# @option --encoding <ENC>               Specify file encoding (default: utf-8)
# @option --max-replacements <NUM>       Limit replacements (0=none, negative=unlimited)
# @option --max-file-bytes <NUM>         Guard against very large files (0 disables)
# @flag   --backup                       Create timestamped .bak before modifying
# @flag   --no-color                     Disable ANSI color output
# @flag   --verbose                      Enable detailed debug logging
# @flag   --regex                        Treat search as regex pattern
# @flag   --dry-run                      Simulate replace/write (no file write)
# @flag   --ignore-case                  Case-insensitive search/replace
# @flag   --decode-escapes               Decode \n, \t, \r, \xHH, \uXXXX, \UXXXXXXXX in strings
# @flag   --dotall                       Treat . in regex as matching newlines (re.DOTALL)
# @flag   --preserve-newlines            Preserve detected newline style (default)
# @flag   --no-preserve-newlines         Do not preserve original newline style
# @flag   --schema                       Print JSON Tool Schema for LLM registration and exit
#
# @env LLM_OUTPUT=/dev/stdout            Output path for LLM integration
# @env LLM_TOOL_MAX_FILE_BYTES=268435456 Default maximum input file size in bytes
# @env LLM_TOOL_MAX_DIFF_LINES=30        Maximum diff lines shown in JSON/UI output
# @env LLM_TOOL_ALLOW_ALL_PATHS=0        Allow unsafe edits-file paths when truthy
# @env LLM_TOOL_UI=auto                  Show human UI on TTY; set 0/false to disable, 1/true to force
# ==============================================================================

from __future__ import annotations

import argparse
import codecs
import difflib
import enum
import json
import logging
import os
import re
import shutil
import signal
import sys
import tempfile
import threading
import time
from collections import OrderedDict
from contextlib import contextmanager
from datetime import datetime, timedelta
from pathlib import Path
from typing import (
    Any,
    Dict,
    List,
    Optional,
    Pattern,
    Tuple,
    Union,
)

__version__ = "2.2.0-ASCENDED"
__all__ = ["__version__", "execute_tool", "run", "generate_tool_schema"]

# ==============================================================================
# SECTION 1: Exit Codes, Constants & Exception Models
# ==============================================================================

EXIT_SUCCESS = 0
EXIT_ERROR = 1
EXIT_FILE_NOT_FOUND = 2
EXIT_INVALID_INPUT = 3
EXIT_PERMISSION_DENIED = 126
EXIT_INTERRUPTED = 130

DEFAULT_MAX_FILE_BYTES = 256 * 1024 * 1024
SCHEMA_VERSION = "2.2"

_SIMPLE_ESCAPES: Dict[str, str] = {
    "n": "\n",
    "r": "\r",
    "t": "\t",
    "b": "\b",
    "f": "\f",
    "v": "\v",
    "0": "\0",
    "\\": "\\",
    '"': '"',
    "'": "'",
}

_HEX_CHARS = set("0123456789abcdefABCDEF")
_OCTAL_CHARS = set("01234567")

_BOM_TABLE: Tuple[Tuple[bytes, str], ...] = (
    (b"\xff\xfe\x00\x00", "utf-32-le"),
    (b"\x00\x00\xfe\xff", "utf-32-be"),
    (b"\xef\xbb\xbf", "utf-8-sig"),
    (b"\xff\xfe", "utf-16-le"),
    (b"\xfe\xff", "utf-16-be"),
)

_DIRECT_STDOUT_TARGETS = {"/dev/stdout", "/dev/fd/1", "-", "stdout"}
_DIRECT_STDERR_TARGETS = {"/dev/stderr", "/dev/fd/2", "stderr"}


class ToolJSONEncoder(json.JSONEncoder):
    """Custom JSON encoder handling Path, Enum, datetime, timedelta, bytes, and sets safely."""

    def default(self, obj: Any) -> Any:
        if isinstance(obj, Path):
            return str(obj)
        if isinstance(obj, enum.Enum):
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
# SECTION 2: Color Palette & Formatting Helpers
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

_ANSI_RE = re.compile(r"\x1b(?:[@-Z\\-_]|\[[0-9;]*[ -/]*[@-~])|\033\[[0-9;]*[a-zA-Z]")


def _strip_ansi(text: str) -> str:
    """Remove all ANSI escape sequences from text."""
    return _ANSI_RE.sub("", text)


def _env_truth(name: str, default: str = "") -> bool:
    """Return True when an environment variable has a truthy value."""
    val = os.environ.get(name, default).strip().lower()
    return val not in ("", "0", "false", "no", "off", "none")


def _is_tty() -> bool:
    """Return True if stderr is attached to an interactive terminal and TERM is active."""
    try:
        return sys.stderr.isatty() and os.environ.get("TERM", "").lower() not in (
            "dumb",
            "",
        )
    except Exception:
        return False


def _llm_output_is_stderr() -> bool:
    """Return True when LLM_OUTPUT is routed directly to stderr."""
    out_path = os.environ.get("LLM_OUTPUT", "/dev/stdout") or "/dev/stdout"
    return out_path.strip().lower() in _DIRECT_STDERR_TARGETS


def _use_color(no_color: bool = False) -> bool:
    """Determine whether ANSI colors should be emitted."""
    if no_color:
        return False
    if os.environ.get("NO_COLOR") is not None:
        return False
    if _env_truth("FORCE_COLOR"):
        return True
    return _is_tty()


def _display_ui(no_color: bool = False) -> bool:
    """
    Determine whether the human-readable UI should be displayed.

    LLM_TOOL_UI can explicitly force or suppress the UI. When LLM_OUTPUT is
    stderr, the UI is suppressed by default to avoid polluting JSON output.
    """
    ui_env = os.environ.get("LLM_TOOL_UI")
    if ui_env is not None:
        return _env_truth("LLM_TOOL_UI")

    if _llm_output_is_stderr():
        return False

    if _env_truth("FORCE_COLOR"):
        return True

    return _is_tty()


def _cprint(text: str, file: Any = None, no_color: bool = False) -> None:
    """Print pre-formatted ANSI text to stderr by default, stripping colors when needed."""
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
    """
    Convert literal escape sequences into actual characters safely.

    Supports:
      \\n \\r \\t \\b \\f \\v \\0 \\\\ \\' \\"
      \\xHH, \\uXXXX, \\UXXXXXXXX, and octal \\ooo escapes.
    Unknown escapes are preserved literally.
    """
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

        # Octal escapes: \0, \7, \77, \377, etc.
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

        # Simple escapes.
        if nxt in _SIMPLE_ESCAPES:
            out.append(_SIMPLE_ESCAPES[nxt])
            i += 2
            continue

        # Hex escapes: \xHH
        if nxt == "x" and i + 4 <= n:
            hex_digits = text[i + 2 : i + 4]
            if len(hex_digits) == 2 and all(c in _HEX_CHARS for c in hex_digits):
                try:
                    out.append(chr(int(hex_digits, 16)))
                    i += 4
                    continue
                except (ValueError, OverflowError):
                    pass

        # Unicode escapes: \uXXXX
        if nxt == "u" and i + 6 <= n:
            hex_digits = text[i + 2 : i + 6]
            if len(hex_digits) == 4 and all(c in _HEX_CHARS for c in hex_digits):
                try:
                    out.append(chr(int(hex_digits, 16)))
                    i += 6
                    continue
                except (ValueError, OverflowError):
                    pass

        # Unicode escapes: \UXXXXXXXX
        if nxt == "U" and i + 10 <= n:
            hex_digits = text[i + 2 : i + 10]
            if len(hex_digits) == 8 and all(c in _HEX_CHARS for c in hex_digits):
                try:
                    out.append(chr(int(hex_digits, 16)))
                    i += 10
                    continue
                except (ValueError, OverflowError):
                    pass

        # Unknown escape: preserve literally.
        out.append("\\")
        out.append(nxt)
        i += 2

    return "".join(out)


def _generate_diff(
    old_content: str, new_content: str, file_path: str, max_lines: int = 30
) -> str:
    """Generate a unified diff summary between old and new content."""
    if old_content == new_content:
        return ""

    try:
        max_lines = int(os.environ.get("LLM_TOOL_MAX_DIFF_LINES", max_lines))
    except Exception:
        pass

    old_lines = old_content.splitlines(keepends=True)
    new_lines = new_content.splitlines(keepends=True)
    diff = list(
        difflib.unified_diff(
            old_lines,
            new_lines,
            fromfile=f"a/{file_path}",
            tofile=f"b/{file_path}",
            n=3,
        )
    )
    if not diff:
        return ""

    if max_lines > 0 and len(diff) > max_lines:
        return (
            "".join(diff[:max_lines])
            + f"\n... ({len(diff) - max_lines} more diff lines truncated)\n"
        )
    return "".join(diff)


def _is_binary(content: str) -> bool:
    """Legacy text-content binary detector."""
    if "\x00" in content:
        return True
    if len(content) > 0:
        control_chars = sum(1 for c in content if ord(c) < 32 and c not in "\n\r\t\f\v")
        if control_chars / len(content) > 0.15:
            return True
    return False


def _as_bool(value: Any, default: bool = False) -> bool:
    """Best-effort boolean coercion for JSON/edit payloads."""
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
    """Best-effort integer coercion for JSON/edit payloads."""
    if value is None:
        return default
    try:
        return int(value)
    except Exception:
        return default


def _is_safe_path(path: Path, base: Optional[Path] = None) -> bool:
    """Check if a path is safe for auxiliary inputs such as --edits files."""
    if _env_truth("LLM_TOOL_ALLOW_ALL_PATHS"):
        return True

    try:
        resolved = path.expanduser().resolve()

        if base is not None:
            base_resolved = base.resolve()
            resolved.relative_to(base_resolved)
            return True

        candidates: List[Path] = []
        try:
            candidates.append(Path.cwd().resolve())
        except Exception:
            pass

        try:
            candidates.append(Path.home().resolve())
        except Exception:
            pass

        try:
            candidates.append(Path(tempfile.gettempdir()).resolve())
        except Exception:
            pass

        if os.name == "posix":
            try:
                candidates.append(Path("/tmp").resolve())
            except Exception:
                pass

        for candidate in candidates:
            try:
                resolved.relative_to(candidate)
                return True
            except ValueError:
                pass
            except OSError:
                pass

        return False
    except (ValueError, OSError):
        return False


def _load_edits_file(path_str: str) -> list[dict[str, Any]]:
    """Load and parse a JSON edits file safely."""
    path_obj = Path(path_str).expanduser().resolve()
    if not _is_safe_path(path_obj):
        raise ValueError(f"Path traversal attempt detected: {path_str}")
    if not path_obj.is_file():
        raise ValueError(f"Batch edits file not found: {path_str}")

    content = path_obj.read_text(encoding="utf-8")
    data = json.loads(content)
    if isinstance(data, list):
        return data
    if isinstance(data, dict):
        return [data]
    raise ValueError("Batch edits file must contain a JSON object or array")


def _parse_edits(edits_input: Any) -> list[dict[str, Any]]:
    """Parse batch edits input from JSON string, list of dicts, @file, or file path."""
    if isinstance(edits_input, list):
        return edits_input
    if edits_input is None:
        return []

    if isinstance(edits_input, str):
        edits_str = edits_input.strip()

        if edits_str.startswith("@"):
            return _load_edits_file(edits_str[1:].strip())

        if edits_str.startswith("[") or edits_str.startswith("{"):
            try:
                data = json.loads(edits_str)
                return data if isinstance(data, list) else [data]
            except json.JSONDecodeError:
                pass

        try:
            path_obj = Path(edits_str).expanduser().resolve()
            if _is_safe_path(path_obj) and path_obj.is_file():
                return _load_edits_file(str(path_obj))
            if not edits_str.startswith("[") and not edits_str.startswith("{"):
                if not _is_safe_path(path_obj):
                    raise ValueError(f"Path traversal attempt detected: {edits_str}")
        except OSError as oe:
            raise ValueError(f"Error accessing edits file '{edits_str}': {oe}") from oe

        try:
            data = json.loads(edits_str)
            return data if isinstance(data, list) else [data]
        except json.JSONDecodeError as e:
            raise ValueError(f"Invalid JSON string or edits file path: {e}") from e

    return []


# ==============================================================================
# SECTION 3: Newline, Encoding, and File I/O Helpers
# ==============================================================================


def _normalize_encoding_name(encoding: Optional[str]) -> str:
    """Normalize encoding names for comparison and display."""
    if not encoding:
        return "utf-8"
    return str(encoding).strip().lower().replace("_", "-")


def _validate_encoding(encoding: Optional[str]) -> Optional[str]:
    """Validate user-requested encoding and return its canonical codec name."""
    if encoding is None:
        return None
    try:
        return codecs.lookup(encoding).name
    except LookupError as exc:
        raise ValueError(f"Unknown encoding: {encoding}") from exc


def _is_null_text_encoding(encoding: Optional[str]) -> bool:
    """Return True for UTF-16/UTF-32 families where null bytes are expected."""
    enc = _normalize_encoding_name(encoding)
    return enc.startswith("utf-16") or enc.startswith("utf-32")


def _is_utf_family(encoding: Optional[str]) -> bool:
    """Return True for UTF-8/UTF-16/UTF-32 families."""
    enc = _normalize_encoding_name(encoding)
    return enc.startswith(("utf-8", "utf-16", "utf-32"))


def _detect_bom_encoding(data: bytes) -> Tuple[Optional[str], int]:
    """Detect BOM and return a specific endian-aware encoding plus BOM length."""
    for bom, enc in _BOM_TABLE:
        if data.startswith(bom):
            return enc, len(bom)
    return None, 0


def _strip_bom_char(text: str) -> str:
    """Strip a leading Unicode BOM character if present."""
    if text.startswith("\ufeff"):
        return text[1:]
    return text


def _should_prefer_bom(
    declared_encoding: Optional[str], bom_encoding: Optional[str]
) -> bool:
    """Return True when a detected BOM should take precedence over declared encoding."""
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
    """Detect dominant newline style from raw bytes."""
    if b"\r\n" in data:
        return "\r\n"
    if b"\r" in data:
        return "\r"
    if b"\n" in data:
        return "\n"
    return ""


def _detect_newline_style_text(text: str) -> str:
    """Detect dominant newline style from decoded text."""
    if "\r\n" in text:
        return "\r\n"
    if "\r" in text:
        return "\r"
    if "\n" in text:
        return "\n"
    return ""


def _normalize_newlines(text: str) -> str:
    """Normalize all newline variants to LF for stable editing."""
    if not text:
        return text
    return text.replace("\r\n", "\n").replace("\r", "\n")


def _denormalize_newlines(text: str, style: str) -> str:
    """Convert normalized LF text back to the requested newline style."""
    if not text:
        return text
    text = _normalize_newlines(text)
    if style in ("\r\n", "\r"):
        return text.replace("\n", style)
    return text


def _prepare_normalized_write_text(
    normalized_text: str, newline_style: str, preserve_newlines: bool
) -> str:
    """Prepare normalized edited text for writing while preserving newline style."""
    if preserve_newlines and newline_style:
        return _denormalize_newlines(normalized_text, newline_style)
    return normalized_text


def _looks_binary_bytes(data: bytes) -> bool:
    """Byte-level binary heuristic."""
    if not data:
        return False

    sample = data[:8192]
    if b"\x00" in sample:
        return True

    control_chars = sum(1 for b in sample if b < 32 and b not in (9, 10, 11, 12, 13))
    return len(sample) > 0 and (control_chars / len(sample)) > 0.15


def _decode_bytes_strict(data: bytes, encoding: str) -> str:
    """Decode bytes strictly and strip a leading Unicode BOM character if present."""
    text = data.decode(encoding)
    return _strip_bom_char(text)


def _read_file_text(
    file_path: Path, declared_encoding: Optional[str] = None
) -> Tuple[str, str, bool, str, int, bool]:
    """Read a file as text with robust encoding detection."""
    data = file_path.read_bytes()
    file_size = len(data)
    byte_newline_style = _detect_newline_style(data)
    bom_encoding, bom_length = _detect_bom_encoding(data)
    raw_bom_present = bom_length > 0

    if not data:
        return (
            "",
            _normalize_encoding_name(declared_encoding or "utf-8"),
            False,
            byte_newline_style,
            file_size,
            False,
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
                binary,
                newline_style,
                file_size,
                bom_present,
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
                    False,
                    newline_style,
                    file_size,
                    False,
                )
            except (UnicodeDecodeError, LookupError):
                continue

    try:
        raw_text = data.decode("utf-8", errors="replace")
    except Exception:
        raw_text = data.decode("latin-1", errors="replace")

    newline_style = _detect_newline_style_text(raw_text) or byte_newline_style
    return (
        _normalize_newlines(raw_text),
        "utf-8",
        True,
        newline_style,
        file_size,
        False,
    )


def _encode_text(
    content: str,
    encoding: str,
    explicit_encoding: bool = False,
    bom_present: bool = False,
) -> Tuple[bytes, str, bool]:
    """Encode text for atomic writing."""
    enc = _normalize_encoding_name(encoding or "utf-8")

    try:
        codecs.lookup(enc)
    except LookupError as exc:
        raise ValueError(f"Unknown encoding: {encoding}") from exc

    text = content
    if text.startswith("\ufeff"):
        text = text[1:]

    if bom_present and _is_utf_family(enc):
        if enc == "utf-8":
            text = "\ufeff" + text
        elif enc in {"utf-16-le", "utf-16-be", "utf-32-le", "utf-32-be"}:
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


def _preserve_file_metadata(target_path: Path, temp_path: Path) -> None:
    """Best-effort preservation of file permissions and stat metadata."""
    try:
        shutil.copymode(target_path, temp_path)
    except OSError:
        pass
    try:
        shutil.copystat(target_path, temp_path)
    except OSError:
        pass


def _set_default_new_file_permissions(temp_path: Path) -> None:
    """Set reasonable default permissions for newly created files, respecting umask."""
    try:
        current_umask = os.umask(0)
        os.umask(current_umask)
        mode = 0o666 & ~current_umask
        os.chmod(temp_path, mode)
    except OSError:
        pass


@contextmanager
def _safe_temp_file(target_path: Path, encoding: Optional[str] = None) -> Any:
    """Context manager for safe atomic writes with guaranteed cleanup."""
    fd, temp_path_str = tempfile.mkstemp(
        prefix=f".{target_path.name}.",
        suffix=f".tmp.{os.getpid()}",
        dir=target_path.parent,
    )
    os.close(fd)
    temp_path = Path(temp_path_str)

    try:
        yield temp_path

        if target_path.exists():
            _preserve_file_metadata(target_path, temp_path)
        else:
            _set_default_new_file_permissions(temp_path)

        temp_path.replace(target_path)
    except Exception:
        try:
            temp_path.unlink()
        except FileNotFoundError:
            pass
        except OSError:
            pass
        raise


def _safe_write_bytes(target_path: Path, payload: bytes) -> None:
    """Write bytes to target file atomically."""
    try:
        with _safe_temp_file(target_path) as temp_path:
            temp_path.write_bytes(payload)
    except OSError as atomic_exc:
        try:
            with open(target_path, "wb") as fh:
                fh.write(payload)
        except OSError:
            raise atomic_exc


def _create_backup(target_path: Path) -> Tuple[bool, Optional[str]]:
    """Create a timestamped backup copy of a file."""
    ts = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
    backup_path = target_path.with_name(f"{target_path.name}.{ts}.bak")
    try:
        shutil.copy2(target_path, backup_path)
        return True, str(backup_path)
    except OSError as exc:
        logging.warning("Failed to create backup: %s", exc)
        return False, None


def _resolve_max_file_bytes(cli_value: Optional[int] = None) -> int:
    """Resolve maximum file-size guard from CLI, environment, or default."""
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


# ==============================================================================
# SECTION 4: Agent & Environment Helpers
# ==============================================================================


def get_agent_var(name: str, default: str = "") -> str:
    """Access agent user-defined variables (LLM_AGENT_VAR_<NAME>)."""
    env_name = f"LLM_AGENT_VAR_{name.upper()}"
    return os.environ.get(env_name, default)


def get_builtin_var(name: str) -> Optional[str]:
    """Access agent built-in environment variables (e.g., __cwd__, __os)."""
    env_name = f"LLM_AGENT_VAR_{name}"
    return os.environ.get(env_name)


def get_execution_context() -> dict[str, Any]:
    """Extract complete execution context from environment."""
    termux_prefix = os.environ.get("PREFIX", "")
    return {
        "tool_name": os.environ.get("LLM_TOOL_NAME", "str_replace_editor"),
        "output_path": os.environ.get("LLM_OUTPUT"),
        "cwd": get_builtin_var("__cwd__") or os.getcwd(),
        "is_termux": "com.termux" in termux_prefix
        or Path("/data/data/com.termux").exists(),
        "pid": os.getpid(),
        "version": __version__,
    }


def _print_ui_header(data: dict[str, Any], no_color: bool = False) -> None:
    """Print the header section of the UI."""
    success = data.get("success", False)
    status_color = NEON_GREEN if success else NEON_RED
    status_symbol = "✓" if success else "✗"
    status_text = "SUCCESS" if success else "FAILED"

    box_w = 72
    border = "─" * box_w

    _cprint(f"{NEON_PURPLE}╭{border}╮{RESET}", no_color=no_color)
    _cprint(
        f"{NEON_PURPLE}│{RESET} {NEON_PINK}⚡ [STR REPLACE EDITOR v{__version__}]{RESET} {status_color}{BOLD}{status_symbol} {status_text}{RESET}",
        no_color=no_color,
    )
    _cprint(f"{NEON_PURPLE}├{border}┤{RESET}", no_color=no_color)


def _print_ui_body(data: dict[str, Any], no_color: bool = False) -> None:
    """Print the body section of the UI."""
    action = data.get("action")
    box_w = 72
    border = "─" * box_w

    _cprint(
        f"{NEON_PURPLE}│{RESET} {NEON_CYAN}File:{RESET}        {data.get('file_path', 'N/A')}",
        no_color=no_color,
    )
    _cprint(
        f"{NEON_PURPLE}│{RESET} {NEON_CYAN}Action:{RESET}      {NEON_YELLOW}{data.get('action', 'N/A')}{RESET}",
        no_color=no_color,
    )
    _cprint(
        f"{NEON_PURPLE}│{RESET} {NEON_CYAN}Mode:{RESET}        {NEON_BLUE}{data.get('mode', 'literal')}{RESET}",
        no_color=no_color,
    )
    _cprint(
        f"{NEON_PURPLE}│{RESET} {NEON_CYAN}Encoding:{RESET}    {data.get('encoding', 'N/A')}",
        no_color=no_color,
    )

    newline_style = data.get("newline_style")
    if newline_style is not None:
        newline_display = _preview_text(newline_style, 20)
        _cprint(
            f"{NEON_PURPLE}│{RESET} {NEON_CYAN}Newlines:{RESET}    {newline_display}",
            no_color=no_color,
        )

    if data.get("binary"):
        _cprint(
            f"{NEON_PURPLE}│{RESET} {NEON_RED}Binary:{RESET}      {BOLD}True{RESET} {DIM}(text operations may be limited){RESET}",
            no_color=no_color,
        )

    if action == "view":
        _cprint(
            f"{NEON_PURPLE}│{RESET} {NEON_CYAN}Lines:{RESET}       {data.get('view_start_line')}–{data.get('view_end_line')} (Total: {data.get('total_lines')})",
            no_color=no_color,
        )

    elif action in {"write", "append", "prepend", "insert"}:
        _cprint(
            f"{NEON_PURPLE}│{RESET} {NEON_CYAN}Written:{RESET}     {NEON_GREEN}{data.get('written_bytes', 0)}{RESET} bytes",
            no_color=no_color,
        )
        _cprint(
            f"{NEON_PURPLE}│{RESET} {NEON_CYAN}Changed:{RESET}     {NEON_GREEN}{data.get('changed', False)}{RESET}",
            no_color=no_color,
        )
        if action == "insert":
            _cprint(
                f"{NEON_PURPLE}│{RESET} {NEON_CYAN}At Line:{RESET}     {data.get('inserted_at_line')}",
                no_color=no_color,
            )
        if data.get("dry_run"):
            _cprint(
                f"{NEON_PURPLE}│{RESET} {NEON_YELLOW}DRY-RUN:{RESET}     Simulation only — no changes written",
                no_color=no_color,
            )

    elif action == "delete":
        _cprint(
            f"{NEON_PURPLE}│{RESET} {NEON_CYAN}Deleted:{RESET}     {NEON_RED}{data.get('lines_deleted', 0)}{RESET} line(s)",
            no_color=no_color,
        )
        _cprint(
            f"{NEON_PURPLE}│{RESET} {NEON_CYAN}Changed:{RESET}     {NEON_GREEN}{data.get('changed', False)}{RESET}",
            no_color=no_color,
        )

    elif action in {"replace", "count"}:
        _cprint(
            f"{NEON_PURPLE}│{RESET} {NEON_CYAN}Search:{RESET}      {_preview_text(data.get('search', ''), 70)}",
            no_color=no_color,
        )
        if action == "replace":
            _cprint(
                f"{NEON_PURPLE}│{RESET} {NEON_CYAN}Replacement:{RESET} {_preview_text(data.get('replacement', ''), 70)}",
                no_color=no_color,
            )
            _cprint(
                f"{NEON_PURPLE}│{RESET} {NEON_CYAN}Changed:{RESET}     {NEON_GREEN}{data.get('changed', False)}{RESET}",
                no_color=no_color,
            )
            _cprint(
                f"{NEON_PURPLE}│{RESET} {NEON_CYAN}Matches:{RESET}     {NEON_GREEN}{data.get('replacements_made', 0)}{RESET} occurrence(s)",
                no_color=no_color,
            )
        else:
            _cprint(
                f"{NEON_PURPLE}│{RESET} {NEON_CYAN}Matches:{RESET}     {NEON_GREEN}{data.get('match_count', 0)}{RESET} occurrence(s)",
                no_color=no_color,
            )

    elif action == "batch":
        _cprint(
            f"{NEON_PURPLE}│{RESET} {NEON_CYAN}Edits:{RESET}       {data.get('total_edits', 0)} rule(s)",
            no_color=no_color,
        )
        _cprint(
            f"{NEON_PURPLE}│{RESET} {NEON_CYAN}Matches:{RESET}     {NEON_GREEN}{data.get('total_replacements_made', 0)}{RESET} total occurrence(s)",
            no_color=no_color,
        )

    _cprint(
        f"{NEON_PURPLE}│{RESET} {NEON_CYAN}Duration:{RESET}    {DIM}{data.get('duration_ms', 0)}ms{RESET}",
        no_color=no_color,
    )

    if not data.get("success") and "error" in data:
        _cprint(f"{NEON_PURPLE}├{border}┤{RESET}", no_color=no_color)
        _cprint(
            f"{NEON_PURPLE}│{RESET} {NEON_RED}Error:{RESET}       {data['error']}",
            no_color=no_color,
        )

    diff_text = data.get("diff", "")
    if diff_text:
        _cprint(f"{NEON_PURPLE}├{border}┤{RESET}", no_color=no_color)
        _cprint(f"{NEON_PURPLE}│{RESET} {BOLD}Diff Preview:{RESET}", no_color=no_color)
        for diff_line in diff_text.splitlines()[:20]:
            if diff_line.startswith("+") and not diff_line.startswith("+++"):
                color_line = f"{NEON_GREEN}{diff_line}{RESET}"
            elif diff_line.startswith("-") and not diff_line.startswith("---"):
                color_line = f"{NEON_RED}{diff_line}{RESET}"
            elif diff_line.startswith("@"):
                color_line = f"{NEON_CYAN}{diff_line}{RESET}"
            else:
                color_line = diff_line
            _cprint(f"{NEON_PURPLE}│{RESET}   {color_line}", no_color=no_color)

    if action == "view" and "content" in data:
        _cprint(f"{NEON_PURPLE}├{border}┤{RESET}", no_color=no_color)
        _cprint(f"{NEON_PURPLE}│{RESET} {BOLD}Content Preview:{RESET}", no_color=no_color)
        lines = data["content"].splitlines()
        start_ln = data.get("view_start_line", 1)
        preview_limit = 35
        for idx, line in enumerate(lines[:preview_limit]):
            display_line = line[:120] + ("..." if len(line) > 120 else "")
            _cprint(
                f"{NEON_PURPLE}│{RESET}   {DIM}{start_ln + idx:4d} │{RESET} {display_line}",
                no_color=no_color,
            )


def _print_ui_footer(data: dict[str, Any], no_color: bool = False) -> None:
    """Print the footer section of the UI."""
    box_w = 72
    border = "─" * box_w
    _cprint(f"{NEON_PURPLE}╰{border}╯{RESET}", no_color=no_color)


def print_human_readable_ui(data: dict[str, Any], no_color: bool = False) -> None:
    """Render an enhanced colorized box UI for terminal users on stderr."""
    if not _display_ui(no_color):
        return

    _print_ui_header(data, no_color)
    _print_ui_body(data, no_color)
    _print_ui_footer(data, no_color)


# ==============================================================================
# SECTION 5: Core Editing Engine
# ==============================================================================


class GracefulShutdown:
    """Thread-safe signal handler for graceful cancellation."""

    def __init__(self) -> None:
        self.interrupted = False
        self._lock = threading.Lock()
        self._original_handlers: Dict[int, Any] = {}
        self._signal_supported = True

        try:
            if threading.current_thread() is threading.main_thread():
                self._original_handlers[signal.SIGINT] = signal.signal(
                    signal.SIGINT, self._handle_signal
                )
                self._original_handlers[signal.SIGTERM] = signal.signal(
                    signal.SIGTERM, self._handle_signal
                )
            else:
                self._signal_supported = False
        except (ValueError, OSError, AttributeError):
            self._signal_supported = False
            self._original_handlers = {}

    def _handle_signal(self, signum: int, frame: Any) -> None:
        with self._lock:
            self.interrupted = True

    def check_interrupted(self) -> bool:
        """Thread-safe check for interruption."""
        with self._lock:
            return self.interrupted

    def restore(self) -> None:
        if (
            self._signal_supported
            and threading.current_thread() is threading.main_thread()
        ):
            try:
                for sig, handler in self._original_handlers.items():
                    signal.signal(sig, handler)
            except (ValueError, OSError):
                pass


class RegexCache:
    """Thread-safe regex pattern cache with LRU eviction."""

    def __init__(self, max_size: int = 128):
        self._cache: "OrderedDict[Tuple[str, int], Pattern[str]]" = OrderedDict()
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


def _build_regex_flags(
    ignore_case: bool = False, dotall: bool = False
) -> int:
    """Build regex flags consistently."""
    flags = re.MULTILINE
    if ignore_case:
        flags |= re.IGNORECASE
    if dotall:
        flags |= re.DOTALL
    return flags


def _normalize_max_replacements(max_replacements: Optional[int]) -> Optional[int]:
    """Normalize replacement limit."""
    if max_replacements is None:
        return None
    try:
        value = int(max_replacements)
    except Exception:
        return None
    if value < 0:
        return None
    return value


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
    limit = _normalize_max_replacements(max_replacements)
    if limit == 0:
        return content, 0

    flags = _build_regex_flags(ignore_case=ignore_case, dotall=dotall)

    if regex:
        try:
            pattern = _regex_cache.get(search, flags)
            count_arg = 0 if limit is None else limit
            new_content, count = pattern.subn(replacement, content, count=count_arg)
            return new_content, count
        except re.error as e:
            raise ValueError(f"Regex error: {e}") from e

    if ignore_case:
        escaped_search = re.escape(search)
        try:
            pattern = _regex_cache.get(escaped_search, flags)
            count_arg = 0 if limit is None else limit
            new_content, count = pattern.subn(
                lambda _m: replacement, content, count=count_arg
            )
            return new_content, count
        except re.error as e:
            raise ValueError(f"Regex error: {e}") from e

    if limit is None:
        count = content.count(search)
        new_content = content.replace(search, replacement)
        return new_content, count

    count_all = content.count(search)
    count = min(count_all, limit)
    new_content = content.replace(search, replacement, limit)
    return new_content, count


def _count_matches(
    content: str,
    search: str,
    regex: bool = False,
    ignore_case: bool = False,
    dotall: bool = False,
) -> int:
    """Count search matches without mutating content."""
    flags = _build_regex_flags(ignore_case=ignore_case, dotall=dotall)

    if regex:
        pattern = _regex_cache.get(search, flags)
        return sum(1 for _ in pattern.finditer(content))

    if ignore_case:
        escaped_search = re.escape(search)
        pattern = _regex_cache.get(escaped_search, flags)
        return sum(1 for _ in pattern.finditer(content))

    return content.count(search)


def generate_tool_schema() -> dict[str, Any]:
    """Generate OpenAI/AIChat Function Tool Schema definition."""
    return {
        "name": "str_replace_editor",
        "description": "Comprehensive file editor supporting view, replace, count, write, batch, append, prepend, insert, and delete actions.",
        "parameters": {
            "type": "object",
            "properties": {
                "action": {
                    "type": "string",
                    "enum": ["view", "replace", "count", "write", "batch", "append", "prepend", "insert", "delete"],
                    "description": "Operation to perform"
                },
                "file_path": {
                    "type": "string",
                    "description": "Target file path"
                },
                "search": {
                    "type": "string",
                    "description": "Search string or regex pattern (required for replace, count, delete by pattern)"
                },
                "replacement": {
                    "type": "string",
                    "description": "Replacement text (required for replace action)"
                },
                "content": {
                    "type": "string",
                    "description": "New text content for write, append, prepend, or insert actions"
                },
                "edits": {
                    "type": "string",
                    "description": "JSON string or file path containing array of edit rules for batch mode"
                },
                "start_line": {
                    "type": "integer",
                    "description": "Start line number (1-based) for view, insert, or line-range delete"
                },
                "end_line": {
                    "type": "integer",
                    "description": "End line number for view or line-range delete"
                },
                "line": {
                    "type": "integer",
                    "description": "Alias for start_line"
                },
                "backup": {
                    "type": "boolean",
                    "description": "Create timestamped backup before modifying file"
                },
                "regex": {
                    "type": "boolean",
                    "description": "Treat search parameter as regex pattern"
                },
                "dry_run": {
                    "type": "boolean",
                    "description": "Simulate editing operations without writing to disk"
                },
                "ignore_case": {
                    "type": "boolean",
                    "description": "Perform case-insensitive search and replace"
                },
                "decode_escapes": {
                    "type": "boolean",
                    "description": "Decode escape sequences in search/content strings (e.g., \\n, \\t)"
                },
                "dotall": {
                    "type": "boolean",
                    "description": "Enable dotall regex mode (. matches newlines)"
                },
                "encoding": {
                    "type": "string",
                    "description": "Specify custom file encoding (default: utf-8)"
                },
                "max_replacements": {
                    "type": "integer",
                    "description": "Maximum number of replacements allowed"
                }
            },
            "required": ["action", "file_path"]
        }
    }


# ==============================================================================
# SECTION 6: Core Tool Execution Engine
# ==============================================================================


def execute_tool(
    action: str,
    file_path: str,
    search: Optional[str] = None,
    replacement: Optional[str] = None,
    content: Optional[str] = None,
    edits: Optional[Union[str, List[Dict[str, Any]]]] = None,
    start_line: Optional[int] = None,
    end_line: Optional[int] = None,
    backup: bool = False,
    no_color: bool = False,
    verbose: bool = False,
    regex: bool = False,
    dry_run: bool = False,
    ignore_case: bool = False,
    decode_escapes: bool = False,
    dotall: bool = False,
    encoding: Optional[str] = None,
    max_replacements: Optional[int] = None,
    preserve_newlines: bool = True,
    max_file_bytes: Optional[int] = None,
    line: Optional[int] = None,
) -> dict[str, Any]:
    """Core execution logic supporting view, replace, count, write, batch, append, prepend, insert, and delete actions."""
    start_time = time.monotonic()

    def _duration_ms() -> float:
        return round((time.monotonic() - start_time) * 1000, 2)

    # Resolve line aliases
    effective_start_line = start_line if start_line is not None else line

    try:
        target_path = Path(file_path).expanduser().resolve()
    except Exception as exc:
        return {
            "success": False,
            "error": f"Invalid file path: {exc}",
            "exit_code": EXIT_INVALID_INPUT,
            "duration_ms": _duration_ms(),
            "tool_version": __version__,
            "schema_version": SCHEMA_VERSION,
            "action": action,
            "file_path": str(file_path),
        }

    action_lower = (action or "").lower().strip()
    allowed_actions = {"view", "replace", "count", "write", "batch", "append", "prepend", "insert", "delete"}

    identity_fields: Dict[str, Any] = {
        "tool_version": __version__,
        "schema_version": SCHEMA_VERSION,
        "action": action_lower,
        "file_path": str(target_path),
    }

    def _failure(message: str, code: int, **extra: Any) -> dict[str, Any]:
        payload: Dict[str, Any] = {
            "success": False,
            "error": message,
            "exit_code": code,
            "duration_ms": _duration_ms(),
        }
        payload.update(identity_fields)
        payload.update(extra)
        return payload

    if action_lower not in allowed_actions:
        return _failure(
            f"Unknown action '{action}'. Allowed: {', '.join(sorted(allowed_actions))}.",
            EXIT_INVALID_INPUT,
        )

    if verbose:
        logging.basicConfig(
            level=logging.DEBUG,
            format="[%(levelname)s] %(message)s",
            force=True,
        )
        logging.debug("Action: %s on Path: %s", action_lower, target_path)

    shutdown = GracefulShutdown()

    try:
        try:
            requested_encoding = _validate_encoding(encoding)
        except ValueError as exc:
            return _failure(str(exc), EXIT_INVALID_INPUT)

        # Handle permissions & new file creation
        if action_lower in {"write", "append", "prepend"}:
            try:
                target_path.parent.mkdir(parents=True, exist_ok=True)
            except OSError as exc:
                return _failure(
                    f"Cannot create directory {target_path.parent}: {exc}",
                    EXIT_PERMISSION_DENIED,
                )
        else:
            if not target_path.exists():
                return _failure(
                    f"File does not exist: {file_path}",
                    EXIT_FILE_NOT_FOUND,
                )
            if not target_path.is_file():
                return _failure(
                    f"Not a regular file: {file_path}",
                    EXIT_FILE_NOT_FOUND,
                )

        # File-size guard
        max_bytes = _resolve_max_file_bytes(max_file_bytes)
        if target_path.exists():
            try:
                disk_size = target_path.stat().st_size
            except OSError as exc:
                return _failure(f"Cannot stat file: {exc}", EXIT_PERMISSION_DENIED)

            if max_bytes > 0 and disk_size > max_bytes:
                return _failure(
                    f"File too large: {disk_size} bytes exceeds limit {max_bytes}.",
                    EXIT_INVALID_INPUT,
                )
        else:
            disk_size = 0

        # Read original text
        if action_lower in {"write", "append", "prepend"} and not target_path.exists():
            file_content = ""
            encoding_used = _normalize_encoding_name(requested_encoding or "utf-8")
            is_binary = False
            newline_style = ""
            file_size = 0
            bom_present = False
        else:
            try:
                (
                    file_content,
                    encoding_used,
                    is_binary,
                    newline_style,
                    file_size,
                    bom_present,
                ) = _read_file_text(target_path, requested_encoding)
            except OSError as exc:
                return _failure(
                    f"Cannot read file {target_path}: {exc}",
                    EXIT_PERMISSION_DENIED,
                )
            except ValueError as exc:
                return _failure(str(exc), EXIT_INVALID_INPUT)

        encoding_fallback = (
            bool(requested_encoding)
            and _normalize_encoding_name(requested_encoding) != encoding_used
        )

        if shutdown.check_interrupted():
            return _failure("Operation interrupted by user signal.", EXIT_INTERRUPTED)

        mode = "regex" if regex else "literal"
        context_data = get_execution_context()

        base_fields: Dict[str, Any] = {
            "tool_version": __version__,
            "schema_version": SCHEMA_VERSION,
            "action": action_lower,
            "file_path": str(target_path),
            "mode": mode,
            "encoding": encoding_used,
            "requested_encoding": requested_encoding,
            "encoding_fallback": encoding_fallback,
            "newline_style": newline_style,
            "binary": is_binary,
            "original_file_size": file_size,
            "context": context_data,
        }

        # Refuse text operations on binary files
        if is_binary and action_lower in {"replace", "batch", "append", "prepend", "insert", "delete"}:
            return _failure(
                f"Target file '{file_path}' appears to be binary. Operation aborted.",
                EXIT_INVALID_INPUT,
            )

        # ------------------------------------------------------------------
        # WRITE
        # ------------------------------------------------------------------
        if action_lower == "write":
            if content is None:
                return _failure("--content required for write action", EXIT_INVALID_INPUT)

            incoming = _unescape_string(content) if decode_escapes else content
            incoming_normalized = _normalize_newlines(incoming)
            changed = incoming_normalized != file_content
            effective_bom = bom_present or incoming_normalized.startswith("\ufeff")

            if changed:
                write_text = _prepare_normalized_write_text(incoming_normalized, newline_style, preserve_newlines)
                payload_bytes, final_encoding, encoding_changed = _encode_text(
                    write_text, encoding_used, explicit_encoding=requested_encoding is not None, bom_present=effective_bom
                )
                written_bytes = len(payload_bytes)
            else:
                payload_bytes, final_encoding, encoding_changed, written_bytes = b"", encoding_used, False, 0

            diff_text = _generate_diff(file_content, incoming_normalized, str(target_path)) if changed and target_path.exists() else ""
            backup_created, backup_path_str = (_create_backup(target_path) if changed and backup and target_path.exists() and not dry_run else (False, None))

            if changed and not dry_run:
                _safe_write_bytes(target_path, payload_bytes)

            result = {
                "success": True,
                "written_bytes": written_bytes,
                "changed": changed,
                "backup_created": backup_created,
                "backup_path": backup_path_str,
                "dry_run": dry_run,
                "diff": diff_text,
                "file_size": file_size if dry_run or not changed else written_bytes,
                "exit_code": EXIT_SUCCESS,
                "duration_ms": _duration_ms(),
            }
            result.update(base_fields)
            return result

        # ------------------------------------------------------------------
        # APPEND
        # ------------------------------------------------------------------
        if action_lower == "append":
            incoming_text = content or replacement or search
            if incoming_text is None:
                return _failure("--content (or --replacement/--search) required for append action", EXIT_INVALID_INPUT)

            incoming = _unescape_string(incoming_text) if decode_escapes else incoming_text
            incoming_norm = _normalize_newlines(incoming)

            # Ensure newline separator if file doesn't end with one
            prefix_sep = "\n" if file_content and not file_content.endswith("\n") else ""
            new_content = file_content + prefix_sep + incoming_norm
            changed = True

            write_text = _prepare_normalized_write_text(new_content, newline_style, preserve_newlines)
            payload_bytes, final_encoding, encoding_changed = _encode_text(
                write_text, encoding_used, explicit_encoding=requested_encoding is not None, bom_present=bom_present
            )
            
            diff_text = _generate_diff(file_content, new_content, str(target_path))
            backup_created, backup_path_str = (_create_backup(target_path) if backup and target_path.exists() and not dry_run else (False, None))

            if not dry_run:
                _safe_write_bytes(target_path, payload_bytes)

            result = {
                "success": True,
                "written_bytes": len(payload_bytes),
                "changed": changed,
                "backup_created": backup_created,
                "backup_path": backup_path_str,
                "dry_run": dry_run,
                "diff": diff_text,
                "file_size": len(payload_bytes),
                "exit_code": EXIT_SUCCESS,
                "duration_ms": _duration_ms(),
            }
            result.update(base_fields)
            return result

        # ------------------------------------------------------------------
        # PREPEND
        # ------------------------------------------------------------------
        if action_lower == "prepend":
            incoming_text = content or replacement or search
            if incoming_text is None:
                return _failure("--content (or --replacement/--search) required for prepend action", EXIT_INVALID_INPUT)

            incoming = _unescape_string(incoming_text) if decode_escapes else incoming_text
            incoming_norm = _normalize_newlines(incoming)

            suffix_sep = "\n" if file_content and not incoming_norm.endswith("\n") else ""
            new_content = incoming_norm + suffix_sep + file_content
            changed = True

            write_text = _prepare_normalized_write_text(new_content, newline_style, preserve_newlines)
            payload_bytes, final_encoding, encoding_changed = _encode_text(
                write_text, encoding_used, explicit_encoding=requested_encoding is not None, bom_present=bom_present
            )

            diff_text = _generate_diff(file_content, new_content, str(target_path))
            backup_created, backup_path_str = (_create_backup(target_path) if backup and target_path.exists() and not dry_run else (False, None))

            if not dry_run:
                _safe_write_bytes(target_path, payload_bytes)

            result = {
                "success": True,
                "written_bytes": len(payload_bytes),
                "changed": changed,
                "backup_created": backup_created,
                "backup_path": backup_path_str,
                "dry_run": dry_run,
                "diff": diff_text,
                "file_size": len(payload_bytes),
                "exit_code": EXIT_SUCCESS,
                "duration_ms": _duration_ms(),
            }
            result.update(base_fields)
            return result

        # ------------------------------------------------------------------
        # INSERT (By Line Index)
        # ------------------------------------------------------------------
        if action_lower == "insert":
            incoming_text = content or replacement
            if incoming_text is None:
                return _failure("--content required for insert action", EXIT_INVALID_INPUT)

            target_line = effective_start_line if effective_start_line is not None else 1
            if target_line < 1:
                return _failure("Line number for insert must be >= 1", EXIT_INVALID_INPUT)

            incoming = _unescape_string(incoming_text) if decode_escapes else incoming_text
            incoming_norm = _normalize_newlines(incoming)

            lines = file_content.split("\n")
            insert_idx = min(len(lines), target_line - 1)
            
            lines.insert(insert_idx, incoming_norm)
            new_content = "\n".join(lines)
            changed = True

            write_text = _prepare_normalized_write_text(new_content, newline_style, preserve_newlines)
            payload_bytes, final_encoding, encoding_changed = _encode_text(
                write_text, encoding_used, explicit_encoding=requested_encoding is not None, bom_present=bom_present
            )

            diff_text = _generate_diff(file_content, new_content, str(target_path))
            backup_created, backup_path_str = (_create_backup(target_path) if backup and not dry_run else (False, None))

            if not dry_run:
                _safe_write_bytes(target_path, payload_bytes)

            result = {
                "success": True,
                "inserted_at_line": insert_idx + 1,
                "written_bytes": len(payload_bytes),
                "changed": changed,
                "backup_created": backup_created,
                "backup_path": backup_path_str,
                "dry_run": dry_run,
                "diff": diff_text,
                "file_size": len(payload_bytes),
                "exit_code": EXIT_SUCCESS,
                "duration_ms": _duration_ms(),
            }
            result.update(base_fields)
            return result

        # ------------------------------------------------------------------
        # DELETE (By Line Range or Search Pattern)
        # ------------------------------------------------------------------
        if action_lower == "delete":
            lines = file_content.split("\n")
            lines_deleted = 0

            if effective_start_line is not None:
                s_idx = max(0, effective_start_line - 1)
                e_idx = min(len(lines), end_line) if end_line is not None else s_idx + 1
                if s_idx < len(lines):
                    del lines[s_idx:e_idx]
                    lines_deleted = e_idx - s_idx
                new_content = "\n".join(lines)
            elif search is not None:
                search_text = _unescape_string(search) if decode_escapes else search
                flags = _build_regex_flags(ignore_case=ignore_case, dotall=dotall)
                new_lines = []

                if regex:
                    pattern = _regex_cache.get(search_text, flags)
                    for l in lines:
                        if pattern.search(l):
                            lines_deleted += 1
                        else:
                            new_lines.append(l)
                else:
                    for l in lines:
                        matched = (search_text.lower() in l.lower()) if ignore_case else (search_text in l)
                        if matched:
                            lines_deleted += 1
                        else:
                            new_lines.append(l)
                new_content = "\n".join(new_lines)
            else:
                return _failure("Specify --start-line or --search for delete action", EXIT_INVALID_INPUT)

            changed = lines_deleted > 0
            if changed:
                write_text = _prepare_normalized_write_text(new_content, newline_style, preserve_newlines)
                payload_bytes, final_encoding, encoding_changed = _encode_text(
                    write_text, encoding_used, explicit_encoding=requested_encoding is not None, bom_present=bom_present
                )
                if not dry_run:
                    _safe_write_bytes(target_path, payload_bytes)
            else:
                payload_bytes = b""

            diff_text = _generate_diff(file_content, new_content, str(target_path)) if changed else ""
            backup_created, backup_path_str = (_create_backup(target_path) if changed and backup and not dry_run else (False, None))

            result = {
                "success": True,
                "lines_deleted": lines_deleted,
                "changed": changed,
                "backup_created": backup_created,
                "backup_path": backup_path_str,
                "dry_run": dry_run,
                "diff": diff_text,
                "file_size": len(payload_bytes) if changed and not dry_run else file_size,
                "exit_code": EXIT_SUCCESS,
                "duration_ms": _duration_ms(),
            }
            result.update(base_fields)
            return result

        # ------------------------------------------------------------------
        # VIEW
        # ------------------------------------------------------------------
        if action_lower == "view":
            lines = file_content.splitlines(keepends=False)
            total_lines = len(lines)
            s_idx = max(0, (effective_start_line or 1) - 1)
            e_idx = min(total_lines, end_line) if end_line else total_lines
            if end_line and end_line < (effective_start_line or 1):
                e_idx = s_idx

            sliced = lines[s_idx:e_idx]
            result = {
                "success": True,
                "total_lines": total_lines,
                "view_start_line": s_idx + 1 if total_lines > 0 else 0,
                "view_end_line": min(e_idx, total_lines),
                "content": "\n".join(sliced),
                "file_size": file_size,
                "exit_code": EXIT_SUCCESS,
                "duration_ms": _duration_ms(),
            }
            result.update(base_fields)
            return result

        # ------------------------------------------------------------------
        # COUNT
        # ------------------------------------------------------------------
        if action_lower == "count":
            if search is None:
                return _failure("--search required for count action", EXIT_INVALID_INPUT)

            search_text = _normalize_newlines(_unescape_string(search) if decode_escapes else search)
            if not search_text:
                return _failure("Empty search string not allowed", EXIT_INVALID_INPUT)

            match_count = _count_matches(file_content, search_text, regex=regex, ignore_case=ignore_case, dotall=dotall)
            result = {
                "success": True,
                "search": search_text,
                "match_count": match_count,
                "file_size": file_size,
                "exit_code": EXIT_SUCCESS,
                "duration_ms": _duration_ms(),
            }
            result.update(base_fields)
            return result

        # ------------------------------------------------------------------
        # BATCH & REPLACE
        # ------------------------------------------------------------------
        if action_lower in {"batch", "replace"}:
            if action_lower == "batch" or edits is not None:
                if edits is None:
                    return _failure("--edits required for batch action", EXIT_INVALID_INPUT)

                edit_list = _parse_edits(edits)
                if not edit_list:
                    return _failure("No valid edits found in batch payload", EXIT_INVALID_INPUT)

                current_content = file_content
                batch_results, total_replacements, failed_edits = [], 0, 0
                global_max = _normalize_max_replacements(max_replacements)

                for idx, item in enumerate(edit_list, 1):
                    if not isinstance(item, dict):
                        failed_edits += 1
                        continue

                    item_search = str(item.get("search") or item.get("old") or item.get("find") or "")
                    item_replacement = str(item.get("replacement") or item.get("new") or item.get("replace") or "")

                    if not item_search:
                        failed_edits += 1
                        continue

                    if _as_bool(item.get("decode_escapes", decode_escapes)):
                        item_search = _unescape_string(item_search)
                        item_replacement = _unescape_string(item_replacement)

                    item_search = _normalize_newlines(item_search)
                    item_replacement = _normalize_newlines(item_replacement)

                    try:
                        new_content, count_made = _replace_in_content(
                            current_content,
                            item_search,
                            item_replacement,
                            regex=_as_bool(item.get("regex", regex)),
                            ignore_case=_as_bool(item.get("ignore_case", ignore_case)),
                            dotall=_as_bool(item.get("dotall", dotall)),
                            max_replacements=_normalize_max_replacements(item.get("max_replacements", global_max)),
                        )
                        current_content = new_content
                        total_replacements += count_made
                        batch_results.append({"index": idx, "success": True, "search": item_search, "replacements_made": count_made})
                    except Exception as ex:
                        failed_edits += 1
                        batch_results.append({"index": idx, "success": False, "search": item_search, "error": str(ex)})

                changed = current_content != file_content
                diff_text = _generate_diff(file_content, current_content, str(target_path)) if changed else ""

                if changed:
                    write_text = _prepare_normalized_write_text(current_content, newline_style, preserve_newlines)
                    payload_bytes, final_encoding, encoding_changed = _encode_text(
                        write_text, encoding_used, explicit_encoding=requested_encoding is not None, bom_present=bom_present
                    )
                    if not dry_run:
                        _safe_write_bytes(target_path, payload_bytes)
                else:
                    payload_bytes = b""

                backup_created, backup_path_str = (_create_backup(target_path) if changed and backup and not dry_run else (False, None))

                result = {
                    "success": True,
                    "total_edits": len(edit_list),
                    "total_replacements_made": total_replacements,
                    "failed_edits": failed_edits,
                    "changed": changed,
                    "batch_results": batch_results,
                    "backup_created": backup_created,
                    "backup_path": backup_path_str,
                    "dry_run": dry_run,
                    "diff": diff_text,
                    "file_size": len(payload_bytes) if changed and not dry_run else file_size,
                    "exit_code": EXIT_SUCCESS,
                    "duration_ms": _duration_ms(),
                }
                result.update(base_fields)
                return result

            # Single Replace Action
            if search is None or replacement is None:
                return _failure("--search and --replacement required for replace action", EXIT_INVALID_INPUT)

            search_text = _normalize_newlines(_unescape_string(search) if decode_escapes else search)
            replacement_text = _normalize_newlines(_unescape_string(replacement) if decode_escapes else replacement)

            if not search_text:
                return _failure("Empty search string not allowed for replace action", EXIT_INVALID_INPUT)

            new_content, replacements_made = _replace_in_content(
                file_content,
                search_text,
                replacement_text,
                regex=regex,
                ignore_case=ignore_case,
                dotall=dotall,
                max_replacements=_normalize_max_replacements(max_replacements),
            )

            changed = replacements_made > 0 and new_content != file_content
            diff_text = _generate_diff(file_content, new_content, str(target_path)) if changed else ""

            if changed:
                write_text = _prepare_normalized_write_text(new_content, newline_style, preserve_newlines)
                payload_bytes, final_encoding, encoding_changed = _encode_text(
                    write_text, encoding_used, explicit_encoding=requested_encoding is not None, bom_present=bom_present
                )
                if not dry_run:
                    _safe_write_bytes(target_path, payload_bytes)
            else:
                payload_bytes = b""

            backup_created, backup_path_str = (_create_backup(target_path) if changed and backup and not dry_run else (False, None))

            result = {
                "success": True,
                "search": search_text,
                "replacement": replacement_text,
                "replacements_made": replacements_made,
                "changed": changed,
                "backup_created": backup_created,
                "backup_path": backup_path_str,
                "dry_run": dry_run,
                "diff": diff_text,
                "file_size": len(payload_bytes) if changed and not dry_run else file_size,
                "exit_code": EXIT_SUCCESS,
                "duration_ms": _duration_ms(),
            }
            result.update(base_fields)
            return result

        return _failure("Unexpected control flow in execute_tool", EXIT_ERROR)

    except PermissionError as exc:
        return _failure(f"Permission denied accessing file: {exc}", EXIT_PERMISSION_DENIED)
    except Exception as exc:
        return _failure(f"Operation failed: {exc}", EXIT_ERROR)
    finally:
        shutdown.restore()


# ==============================================================================
# SECTION 7: Output Routing
# ==============================================================================


def write_llm_output(data: dict[str, Any]) -> None:
    """Write clean JSON for LLM consumption via LLM_OUTPUT safely."""
    out_path = os.environ.get("LLM_OUTPUT", "/dev/stdout") or "/dev/stdout"
    out_path = out_path.strip()

    json_payload = (
        json.dumps(data, indent=2, ensure_ascii=False, cls=ToolJSONEncoder) + "\n"
    )

    if out_path in _DIRECT_STDOUT_TARGETS:
        try:
            if hasattr(sys.stdout, "buffer"):
                sys.stdout.buffer.write(json_payload.encode("utf-8"))
                sys.stdout.buffer.flush()
            else:
                sys.stdout.write(json_payload)
                sys.stdout.flush()
        except UnicodeEncodeError:
            sys.stdout.write(
                json.dumps(data, indent=2, ensure_ascii=True, cls=ToolJSONEncoder)
                + "\n"
            )
            sys.stdout.flush()
        return

    try:
        out_file = Path(out_path).expanduser().resolve()
        out_file.parent.mkdir(parents=True, exist_ok=True)
        _safe_write_bytes(out_file, json_payload.encode("utf-8"))
    except OSError as err:
        sys.stderr.write(f"Failed writing LLM_OUTPUT: {err}\n")
        sys.stdout.write(json_payload)
        sys.stdout.flush()


# ==============================================================================
# SECTION 8: AIChat Entrypoint
# ==============================================================================


def run(
    action: str,
    file_path: Optional[str] = None,
    target: Optional[str] = None,
    search: Optional[str] = None,
    replacement: Optional[str] = None,
    content: Optional[str] = None,
    edits: Optional[Union[str, List[Dict[str, Any]]]] = None,
    start_line: Optional[int] = None,
    end_line: Optional[int] = None,
    line: Optional[int] = None,
    backup: bool = False,
    no_color: bool = False,
    verbose: bool = False,
    regex: bool = False,
    dry_run: bool = False,
    ignore_case: bool = False,
    decode_escapes: bool = False,
    dotall: bool = False,
    encoding: Optional[str] = None,
    max_replacements: Optional[int] = None,
    preserve_newlines: bool = True,
    max_file_bytes: Optional[int] = None,
    **kwargs: Any,
) -> dict[str, Any]:
    """AIChat Programmatic Entrypoint with alias support."""
    effective_file_path = file_path or target
    if not effective_file_path:
        raise ValueError("file_path or target parameter is required")

    result = execute_tool(
        action=action,
        file_path=effective_file_path,
        search=search,
        replacement=replacement,
        content=content,
        edits=edits,
        start_line=start_line,
        end_line=end_line,
        line=line,
        backup=backup,
        no_color=no_color,
        verbose=verbose,
        regex=regex,
        dry_run=dry_run,
        ignore_case=ignore_case,
        decode_escapes=decode_escapes,
        dotall=dotall,
        encoding=encoding,
        max_replacements=max_replacements,
        preserve_newlines=preserve_newlines,
        max_file_bytes=max_file_bytes,
    )
    print_human_readable_ui(result, no_color=no_color)
    write_llm_output(result)
    return result


# ==============================================================================
# SECTION 9: CLI Parser & Main Entrypoint
# ==============================================================================


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="str_replace_editor.py",
        description=f"Pyrmethus String Replace Editor Tool v{__version__}",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--action",
        "-a",
        required=False,
        choices=["view", "replace", "count", "write", "batch", "append", "prepend", "insert", "delete"],
        help="Operation to perform",
    )
    parser.add_argument(
        "--file-path", "--target",
        "-f", "-t",
        dest="file_path",
        metavar="PATH",
        help="Target file path",
    )
    parser.add_argument(
        "--search",
        "-s",
        metavar="TEXT",
        help="Search string/pattern",
    )
    parser.add_argument(
        "--replacement",
        "-r",
        metavar="TEXT",
        help="Replacement text",
    )
    parser.add_argument(
        "--content",
        "-c",
        metavar="TEXT",
        help="Content for write, append, prepend, or insert actions",
    )
    parser.add_argument(
        "--edits",
        "-e",
        metavar="JSON_OR_PATH",
        help="JSON array or file path for batch edits",
    )
    parser.add_argument(
        "--start-line",
        type=int,
        dest="start_line",
        metavar="NUM",
        help="Start line (1-based) for view, insert, or delete",
    )
    parser.add_argument(
        "--end-line",
        type=int,
        dest="end_line",
        metavar="NUM",
        help="End line for view or delete range",
    )
    parser.add_argument(
        "--line",
        type=int,
        dest="line",
        metavar="NUM",
        help="Alias for start-line",
    )
    parser.add_argument(
        "--backup",
        action="store_true",
        default=False,
        help="Create timestamped backup",
    )
    parser.add_argument(
        "--no-color",
        action="store_true",
        default=False,
        dest="no_color",
        help="Disable colors",
    )
    parser.add_argument(
        "--verbose",
        "-v",
        action="store_true",
        default=False,
        help="Verbose output",
    )
    parser.add_argument(
        "--regex",
        action="store_true",
        default=False,
        help="Treat search as regex pattern",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        default=False,
        help="Simulate actions without writing",
    )
    parser.add_argument(
        "--ignore-case",
        "-i",
        action="store_true",
        default=False,
        help="Case-insensitive operation",
    )
    parser.add_argument(
        "--decode-escapes",
        action="store_true",
        default=False,
        dest="decode_escapes",
        help="Decode escape sequences (\\n, \\t, \\r, \\xHH, \\uXXXX, \\UXXXXXXXX)",
    )
    parser.add_argument(
        "--dotall",
        action="store_true",
        default=False,
        help="Dotall mode in regex (. matches newline)",
    )
    parser.add_argument(
        "--encoding",
        metavar="ENC",
        help="Specify file encoding (default: utf-8)",
    )
    parser.add_argument(
        "--max-replacements",
        type=int,
        dest="max_replacements",
        metavar="NUM",
        help="Limit replacements (0=none, negative=unlimited)",
    )
    parser.add_argument(
        "--preserve-newlines",
        dest="preserve_newlines",
        action="store_true",
        default=True,
        help="Preserve detected newline style (default)",
    )
    parser.add_argument(
        "--no-preserve-newlines",
        dest="preserve_newlines",
        action="store_false",
        help="Do not preserve original newline style",
    )
    parser.add_argument(
        "--max-file-bytes",
        type=int,
        dest="max_file_bytes",
        metavar="NUM",
        help="Maximum allowed input file size in bytes (0 disables)",
    )
    parser.add_argument(
        "--schema",
        action="store_true",
        default=False,
        help="Print JSON Tool Schema for LLM registration and exit",
    )
    parser.add_argument(
        "--version",
        action="version",
        version=f"%(prog)s {__version__}",
    )
    return parser


def main() -> int:
    """CLI execution entrypoint."""
    parser = _build_parser()
    args = parser.parse_args()

    if args.schema:
        schema = generate_tool_schema()
        sys.stdout.write(json.dumps(schema, indent=2) + "\n")
        sys.stdout.flush()
        return EXIT_SUCCESS

    if not args.action or not args.file_path:
        _cprint(f"{NEON_RED}Error: Both --action and --file-path parameters are required.{RESET}", no_color=args.no_color)
        return EXIT_INVALID_INPUT

    res = run(
        action=args.action,
        file_path=args.file_path,
        search=args.search,
        replacement=args.replacement,
        content=args.content,
        edits=args.edits,
        start_line=args.start_line,
        end_line=args.end_line,
        line=args.line,
        backup=args.backup,
        no_color=args.no_color,
        verbose=args.verbose,
        regex=args.regex,
        dry_run=args.dry_run,
        ignore_case=args.ignore_case,
        decode_escapes=args.decode_escapes,
        dotall=args.dotall,
        encoding=args.encoding,
        max_replacements=args.max_replacements,
        preserve_newlines=args.preserve_newlines,
        max_file_bytes=args.max_file_bytes,
    )
    return res.get("exit_code", EXIT_SUCCESS)


if __name__ == "__main__":
    sys.exit(main())
