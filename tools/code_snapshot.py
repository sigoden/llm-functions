#!/usr/bin/env python3
# ==============================================================================
# code_snapshot.py — Neon Syntax Highlighting Code Snapshot Tool for AIChat
# Pyrmethus AIChat Tool Master Template v2.2.0-ASCENDED
#
# @describe Generates high-resolution code snapshot images with neon dark themes, window frames, and syntax highlighting.
#
# @meta require-tools aichat
#
# @option --target! <PATH_OR_CODE>        Path to code file OR inline code text string (required)
# @option --output <PATH>                 Destination image path (default: snapshot.png)
# @option --theme <THEME>                 Neon theme: cyberpunk/matrix/synthwave/tokyo-night (default: cyberpunk)
# @option --language <LANG>               Programming language (e.g., python, js, rust, auto) (default: auto)
# @option --title <TITLE>                 Custom title bar text (defaults to filename or language)
# @option --font-size <NUM>               Font size in pixels (default: 18)
# @flag   --line-numbers                  Display line numbers in snapshot
# @flag   --window-frame                  Draw macOS-style window frame with control buttons
# @flag   --glow-effect                   Add outer neon glow border effect
# @flag   --use-cache                     Enable result caching for identical renders
# @flag   --no-color                      Disable ANSI color terminal output
# @flag   --verbose                       Enable detailed debug log output
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
import signal
import sys
import time
from datetime import datetime, timedelta
from enum import Enum
from pathlib import Path
from typing import Any, List, Literal, Optional, Tuple

# Dependency Verification & Imports
try:
    from PIL import Image, ImageDraw, ImageFilter, ImageFont
except ImportError:
    sys.stderr.write("Missing dependency 'Pillow'. Install via: pip install Pillow\n")
    sys.exit(127)

try:
    import pygments
    from pygments.lexer import Lexer
    from pygments.lexers import get_lexer_by_name, guess_lexer
    from pygments.token import Token

    HAS_PYGMENTS = True
except ImportError:
    HAS_PYGMENTS = False

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


def print_human_readable_ui(data: dict[str, Any], no_color: bool = False) -> None:
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
        f"{NEON_PURPLE}│{RESET} {NEON_PINK}⚡ [CODE SNAPSHOT RENDERER v{__version__}]{RESET} {status_color}{BOLD}{status_symbol} {status_text}{RESET}"
    )
    _cprint(f"{NEON_PURPLE}├{border}┤{RESET}")
    _cprint(
        f"{NEON_PURPLE}│{RESET} {NEON_CYAN}Output Path:{RESET} {NEON_GREEN}{data.get('output_path', 'N/A')}{RESET}"
    )
    _cprint(
        f"{NEON_PURPLE}│{RESET} {NEON_CYAN}Theme:{RESET}       {NEON_PINK}{data.get('theme', 'N/A')}{RESET}"
    )
    _cprint(
        f"{NEON_PURPLE}│{RESET} {NEON_CYAN}Language:{RESET}    {NEON_YELLOW}{data.get('language', 'N/A')}{RESET}"
    )
    _cprint(
        f"{NEON_PURPLE}│{RESET} {NEON_CYAN}Lines:{RESET}       {data.get('line_count', 0)}"
    )
    _cprint(
        f"{NEON_PURPLE}│{RESET} {NEON_CYAN}Dimensions:{RESET}  {data.get('width', 0)}x{data.get('height', 0)} px"
    )
    _cprint(
        f"{NEON_PURPLE}│{RESET} {NEON_CYAN}Cached:{RESET}      {data.get('cached', False)}"
    )
    _cprint(
        f"{NEON_PURPLE}│{RESET} {NEON_CYAN}Duration:{RESET}    {DIM}{data.get('duration_ms', 0)}ms{RESET}"
    )

    if not success and "error" in data:
        _cprint(f"{NEON_PURPLE}├{border}┤{RESET}")
        _cprint(f"{NEON_PURPLE}│{RESET} {NEON_RED}Error:{RESET}       {data['error']}")

    _cprint(f"{NEON_PURPLE}╰{border}╯{RESET}")


# ==============================================================================
# SECTION 3: Agent & Environment Helpers
# ==============================================================================


def get_agent_var(name: str, default: str = "") -> str:
    env_name = f"LLM_AGENT_VAR_{name.upper()}"
    return os.environ.get(env_name, default)


def get_builtin_var(name: str) -> Optional[str]:
    env_name = f"LLM_AGENT_VAR_{name}"
    return os.environ.get(env_name)


def get_execution_context() -> dict[str, Any]:
    termux_prefix = os.environ.get("PREFIX", "")
    return {
        "tool_name": os.environ.get("LLM_TOOL_NAME"),
        "cache_dir": os.environ.get("LLM_TOOL_CACHE_DIR"),
        "root_dir": os.environ.get("LLM_ROOT_DIR"),
        "output_path": os.environ.get("LLM_OUTPUT"),
        "cwd": get_builtin_var("__cwd__") or os.getcwd(),
        "termux_prefix": termux_prefix,
        "is_termux": "com.termux" in termux_prefix
        or Path("/data/data/com.termux").exists(),
    }


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
# SECTION 5: Neon Themes & Syntax Tokenizer
# ==============================================================================

THEMES = {
    "cyberpunk": {
        "bg": (13, 15, 24),  # Deep dark void
        "window_bg": (22, 25, 43),  # Cyber navy
        "border": (255, 0, 127),  # Neon magenta/pink
        "glow": (255, 0, 127, 100),  # Glow magenta alpha
        "title": (0, 246, 255),  # Cyan title
        "line_num": (80, 90, 120),
        "default": (248, 248, 242),
        "keyword": (255, 0, 127),  # Pink
        "string": (0, 246, 255),  # Cyan
        "comment": (98, 114, 164),  # Muted purple
        "function": (0, 255, 102),  # Neon green
        "number": (255, 190, 0),  # Bright yellow
        "operator": (255, 128, 0),  # Neon orange
    },
    "matrix": {
        "bg": (3, 10, 5),
        "window_bg": (8, 20, 11),
        "border": (0, 255, 65),  # Matrix Green
        "glow": (0, 255, 65, 120),
        "title": (0, 255, 65),
        "line_num": (25, 75, 40),
        "default": (170, 255, 204),
        "keyword": (0, 255, 65),
        "string": (51, 204, 102),
        "comment": (0, 85, 25),
        "function": (102, 255, 170),
        "number": (204, 255, 0),
        "operator": (0, 200, 80),
    },
    "synthwave": {
        "bg": (18, 13, 28),
        "window_bg": (26, 19, 41),
        "border": (255, 126, 219),  # Neon pink
        "glow": (120, 40, 200, 150),  # Purple glow
        "title": (54, 249, 246),
        "line_num": (108, 103, 131),
        "default": (240, 239, 241),
        "keyword": (254, 68, 80),
        "string": (255, 126, 219),
        "comment": (108, 103, 131),
        "function": (54, 249, 246),
        "number": (254, 222, 93),
        "operator": (254, 150, 50),
    },
    "tokyo-night": {
        "bg": (22, 22, 30),
        "window_bg": (26, 27, 38),
        "border": (122, 162, 247),  # Tokyo Blue
        "glow": (122, 162, 247, 100),
        "title": (187, 154, 247),
        "line_num": (86, 95, 137),
        "default": (192, 202, 245),
        "keyword": (187, 154, 247),
        "string": (158, 206, 106),
        "comment": (86, 95, 137),
        "function": (122, 162, 247),
        "number": (255, 158, 100),
        "operator": (137, 221, 254),
    },
}


def _tokenize_code(code: str, language: str) -> List[List[Tuple[str, str]]]:
    """
    Tokenizes code lines and maps them to token categories.
    Returns: List[Line] where Line is List[(text, token_category)]
    """
    lines_tokens: List[List[Tuple[str, str]]] = []

    if HAS_PYGMENTS:
        try:
            if language and language != "auto":
                lexer = get_lexer_by_name(language)
            else:
                lexer = guess_lexer(code)
        except Exception:
            lexer = None

        if lexer:
            raw_tokens = pygments.lex(code, lexer)
            current_line: List[Tuple[str, str]] = []

            for tok_type, value in raw_tokens:
                # Map pygments tokens to simple category strings
                category = "default"
                if tok_type in Token.Keyword or tok_type in Token.Keyword.Reserved:
                    category = "keyword"
                elif tok_type in Token.String:
                    category = "string"
                elif tok_type in Token.Comment:
                    category = "comment"
                elif tok_type in Token.Name.Function or tok_type in Token.Name.Class:
                    category = "function"
                elif tok_type in Token.Number:
                    category = "number"
                elif tok_type in Token.Operator:
                    category = "operator"

                # Split across newlines
                parts = value.split("\n")
                for i, part in enumerate(parts):
                    if part:
                        current_line.append((part, category))
                    if i < len(parts) - 1:
                        lines_tokens.append(current_line)
                        current_line = []
            if current_line or not lines_tokens:
                lines_tokens.append(current_line)
            return lines_tokens

    # Fallback Lexer: Simple regex tokenization
    kw_regex = re.compile(
        r"\b(def|class|return|if|else|elif|import|from|for|while|in|as|try|except|with|const|let|var|function|async|await|fn|pub|struct|impl)\b"
    )
    str_regex = re.compile(r"(\".*?\"|'.*?'|`.*?`)")
    num_regex = re.compile(r"\b\d+(\.\d+)?\b")
    comment_regex = re.compile(r"(#.*|//.*)")

    for line in code.splitlines():
        tokens: List[Tuple[str, str]] = []
        pos = 0
        while pos < len(line):
            # Check comment first
            m_comm = comment_regex.match(line, pos)
            if m_comm:
                tokens.append((m_comm.group(0), "comment"))
                pos = len(line)
                break
            # Check string
            m_str = str_regex.match(line, pos)
            if m_str:
                tokens.append((m_str.group(0), "string"))
                pos = m_str.end()
                continue
            # Check keyword
            m_kw = kw_regex.match(line, pos)
            if m_kw:
                tokens.append((m_kw.group(0), "keyword"))
                pos = m_kw.end()
                continue
            # Check number
            m_num = num_regex.match(line, pos)
            if m_num:
                tokens.append((m_num.group(0), "number"))
                pos = m_num.end()
                continue

            # Default single character
            tokens.append((line[pos], "default"))
            pos += 1
        lines_tokens.append(tokens)

    return lines_tokens


# ==============================================================================
# SECTION 6: Snapshot Rendering Logic
# ==============================================================================


def render_code_snapshot(
    code: str,
    output_path: Path,
    theme_name: str = "cyberpunk",
    language: str = "auto",
    title: str = "",
    font_size: int = 18,
    show_line_numbers: bool = True,
    show_window_frame: bool = True,
    glow_effect: bool = True,
) -> Tuple[int, int]:
    """Render code into a neon snapshot image using Pillow."""
    palette = THEMES.get(theme_name.lower(), THEMES["cyberpunk"])
    tokens_by_line = _tokenize_code(code, language)

    # Font Setup
    try:
        font = ImageFont.truetype("DejaVuSansMono.ttf", font_size)
    except OSError:
        try:
            font = ImageFont.truetype("Courier New.ttf", font_size)
        except OSError:
            font = ImageFont.load_default()

    # Measure char width and line height using PIL bbox
    dummy_img = Image.new("RGB", (1, 1))
    dummy_draw = ImageDraw.Draw(dummy_img)
    char_bbox = dummy_draw.textbbox((0, 0), "M", font=font)
    char_w = max(char_bbox[2] - char_bbox[0], 10)
    line_h = max(char_bbox[3] - char_bbox[1] + 8, font_size + 6)

    # Layout Parameters
    margin = 50 if glow_effect else 30
    title_h = 44 if show_window_frame else 16
    padding = 24
    line_num_digits = len(str(len(tokens_by_line)))
    line_num_w = (line_num_digits + 2) * char_w if show_line_numbers else 0

    # Determine max line length
    raw_lines = code.splitlines() or [""]
    max_cols = max(len(l.expandtabs(4)) for l in raw_lines)

    code_width = max(max_cols * char_w + line_num_w, 400)
    code_height = max(len(tokens_by_line) * line_h, line_h)

    win_w = code_width + (padding * 2)
    win_h = code_height + title_h + (padding * 2)

    total_w = win_w + (margin * 2)
    total_h = win_h + (margin * 2)

    # Background Canvas
    canvas = Image.new("RGBA", (total_w, total_h), palette["bg"] + (255,))

    # Outer Neon Glow Layer
    if glow_effect:
        glow_canvas = Image.new("RGBA", (total_w, total_h), (0, 0, 0, 0))
        glow_draw = ImageDraw.Draw(glow_canvas)
        glow_box = [margin - 4, margin - 4, margin + win_w + 4, margin + win_h + 4]
        glow_draw.rounded_rectangle(glow_box, radius=16, fill=palette["glow"])
        glow_canvas = glow_canvas.filter(ImageFilter.GaussianBlur(radius=20))
        canvas.alpha_composite(glow_canvas)

    # Window Layer
    win_box = [margin, margin, margin + win_w, margin + win_h]
    draw = ImageDraw.Draw(canvas)

    # Draw Window Frame & Neon Border
    draw.rounded_rectangle(
        win_box,
        radius=12,
        fill=palette["window_bg"] + (255,),
        outline=palette["border"],
        width=2,
    )

    # Draw macOS Window Buttons (Red, Yellow, Green)
    if show_window_frame:
        btn_y = margin + 22
        colors = [(255, 95, 86), (255, 189, 46), (39, 201, 63)]
        for i, col in enumerate(colors):
            btn_x = margin + 24 + (i * 20)
            draw.ellipse([btn_x - 6, btn_y - 6, btn_x + 6, btn_y + 6], fill=col)

        if title:
            title_bbox = draw.textbbox((0, 0), title, font=font)
            title_w = title_bbox[2] - title_bbox[0]
            title_x = margin + (win_w - title_w) // 2
            draw.text((title_x, margin + 14), title, fill=palette["title"], font=font)

    # Render Code Lines & Line Numbers
    start_x = margin + padding + line_num_w
    start_y = margin + title_h + padding

    for idx, line_tokens in enumerate(tokens_by_line):
        y = start_y + (idx * line_h)

        # Draw Line Number
        if show_line_numbers:
            num_str = str(idx + 1).rjust(line_num_digits)
            num_x = margin + padding
            draw.text((num_x, y), num_str, fill=palette["line_num"], font=font)

        # Draw Code Tokens
        x = start_x
        for text, cat in line_tokens:
            color = palette.get(cat, palette["default"])
            # Expand tabs to 4 spaces
            clean_text = text.replace("\t", "    ")
            draw.text((x, y), clean_text, fill=color, font=font)
            x += len(clean_text) * char_w

    # Save Snapshot
    output_path.parent.mkdir(parents=True, exist_ok=True)
    canvas.save(output_path, "PNG")
    return total_w, total_h


# ==============================================================================
# SECTION 7: Core Tool Execution
# ==============================================================================


def execute_tool(
    target: str,
    output: Optional[str] = None,
    theme: str = "cyberpunk",
    language: str = "auto",
    title: Optional[str] = None,
    font_size: Optional[int] = None,
    line_numbers: bool = False,
    window_frame: bool = False,
    glow_effect: bool = False,
    use_cache: bool = False,
    no_color: bool = False,
    verbose: bool = False,
) -> dict[str, Any]:
    start_time = time.monotonic()

    if verbose:
        logging.basicConfig(level=logging.DEBUG, format="[DEBUG] %(message)s")
        logging.debug(f"Target argument: {target}")

    # Determine code input (Path or Raw String)
    target_path = (
        Path(target).expanduser().resolve()
        if len(target) < 500 and "\n" not in target
        else None
    )

    if target_path and target_path.exists() and target_path.is_file():
        try:
            code_text = target_path.read_text(encoding="utf-8")
            display_title = title or target_path.name
        except Exception as err:
            return {
                "success": False,
                "error": f"Failed reading file: {err}",
                "exit_code": EXIT_FILE_NOT_FOUND,
                "duration_ms": 0.0,
            }
    else:
        code_text = target
        display_title = title or "snapshot.code"

    if not code_text.strip():
        return {
            "success": False,
            "error": "Provided code target is empty.",
            "exit_code": EXIT_INVALID_INPUT,
            "duration_ms": 0.0,
        }

    out_file = Path(output or "snapshot.png").expanduser().resolve()
    f_size = font_size if (font_size and font_size > 8) else 18

    # Caching Logic
    cache = ToolCache()
    cache_key = f"{code_text}:{out_file}:{theme}:{language}:{display_title}:{f_size}:{line_numbers}:{window_frame}:{glow_effect}"

    if use_cache:
        cached_result = cache.get(cache_key)
        if cached_result and Path(cached_result.get("output_path", "")).exists():
            cached_result["cached"] = True
            return cached_result

    shutdown = GracefulShutdown()
    try:
        width, height = render_code_snapshot(
            code=code_text,
            output_path=out_file,
            theme_name=theme,
            language=language,
            title=display_title,
            font_size=f_size,
            show_line_numbers=line_numbers,
            show_window_frame=window_frame,
            glow_effect=glow_effect,
        )

        duration_ms = round((time.monotonic() - start_time) * 1000, 2)
        result = {
            "success": True,
            "output_path": str(out_file),
            "theme": theme,
            "language": language,
            "line_count": len(code_text.splitlines()),
            "width": width,
            "height": height,
            "context": get_execution_context(),
            "cached": False,
            "duration_ms": duration_ms,
            "exit_code": EXIT_SUCCESS,
        }

        if use_cache:
            cache.set(cache_key, result)

        return result

    except Exception as exc:
        duration_ms = round((time.monotonic() - start_time) * 1000, 2)
        return {
            "success": False,
            "error": f"Snapshot rendering error: {exc}",
            "exit_code": EXIT_ERROR,
            "duration_ms": duration_ms,
        }
    finally:
        shutdown.restore()


# ==============================================================================
# SECTION 8: Output Routing & Main Entrypoint
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


def run(
    target: str,
    output: Optional[str] = "snapshot.png",
    theme: Literal["cyberpunk", "matrix", "synthwave", "tokyo-night"] = "cyberpunk",
    language: str = "auto",
    title: Optional[str] = None,
    font_size: Optional[int] = 18,
    line_numbers: bool = False,
    window_frame: bool = False,
    glow_effect: bool = False,
    use_cache: bool = False,
    no_color: bool = False,
    verbose: bool = False,
) -> None:
    """Execute code snapshot creation with specified parameters."""
    result = execute_tool(
        target=target,
        output=output,
        theme=theme,
        language=language,
        title=title,
        font_size=font_size,
        line_numbers=line_numbers,
        window_frame=window_frame,
        glow_effect=glow_effect,
        use_cache=use_cache,
        no_color=no_color,
        verbose=verbose,
    )

    print_human_readable_ui(result, no_color=no_color)
    write_llm_output(result)


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="code_snapshot.py",
        description=f"AIChat Neon Code Snapshot Tool v{__version__}",
    )
    parser.add_argument(
        "--target",
        "-t",
        required=True,
        metavar="PATH_OR_CODE",
        help="Target file path or inline code text",
    )
    parser.add_argument(
        "--output",
        "-o",
        default="snapshot.png",
        metavar="PATH",
        help="Destination PNG output path (default: snapshot.png)",
    )
    parser.add_argument(
        "--theme",
        choices=["cyberpunk", "matrix", "synthwave", "tokyo-night"],
        default="cyberpunk",
        help="Neon theme selection (default: cyberpunk)",
    )
    parser.add_argument(
        "--language",
        "-l",
        default="auto",
        help="Programming language (default: auto)",
    )
    parser.add_argument(
        "--title",
        help="Custom title bar text",
    )
    parser.add_argument(
        "--font-size",
        type=int,
        default=18,
        help="Font size in pixels (default: 18)",
    )
    parser.add_argument(
        "--line-numbers",
        action="store_true",
        default=False,
        help="Enable line numbers",
    )
    parser.add_argument(
        "--window-frame",
        action="store_true",
        default=False,
        help="Enable window header frame with control dots",
    )
    parser.add_argument(
        "--glow-effect",
        action="store_true",
        default=False,
        help="Add outer neon glow border effect",
    )
    parser.add_argument(
        "--use-cache",
        action="store_true",
        default=False,
        help="Enable result caching",
    )
    parser.add_argument(
        "--no-color",
        action="store_true",
        default=False,
        help="Disable ANSI colors",
    )
    parser.add_argument(
        "--verbose",
        "-v",
        action="store_true",
        default=False,
        help="Enable debug output",
    )
    return parser


if __name__ == "__main__":
    args = _build_parser().parse_args()
    res = execute_tool(
        target=args.target,
        output=args.output,
        theme=args.theme,
        language=args.language,
        title=args.title,
        font_size=args.font_size,
        line_numbers=args.line_numbers,
        window_frame=args.window_frame,
        glow_effect=args.glow_effect,
        use_cache=args.use_cache,
        no_color=args.no_color,
        verbose=args.verbose,
    )

    print_human_readable_ui(res, no_color=args.no_color)
    write_llm_output(res)
    sys.exit(res.get("exit_code", EXIT_SUCCESS))
