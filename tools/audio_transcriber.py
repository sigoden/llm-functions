#!/usr/bin/env python3
# ==============================================================================
# audio_transcribe.py — Pyrmethus AIChat Audio & Speech Transcription Tool
# argc/aichat compatible · Colorized UI · Safe Caching · Agent CWD Resolution
#
# @describe Audio inspector, track extractor, and speech-to-text transcriber using Whisper / ffmpeg.
#
# @meta require-tools aichat
#
# @option --target! <PATH>               Target audio/video file path (required)
# @option --action <ACTION>              Operation: transcribe/extract-audio/inspect (default: transcribe)
# @option --language <LANG>              Language code (e.g. en, es, auto)
# @option --model <MODEL>                Whisper model size: tiny/base/small/medium (default: base)
# @option --output-text <PATH>           Save transcription to file path
# @option --mode <MODE>                  Execution mode: summary/detailed (default: summary)
# @option --limit <NUM>                  Maximum characters to return (default: 5000)
# @option --timeout <SEC>                Maximum execution timeout in seconds
# @flag   --use-cache                    Enable result caching for transcriptions
# @flag   --clear-cache                  Clear cache directory and exit
# @flag   --schema                       Print JSON Tool Schema for LLM registration and exit
# @flag   --no-color                     Disable ANSI color output
# @flag   --verbose                      Enable detailed debug log output
#
# @env LLM_OUTPUT=/dev/stdout            Output path for LLM integration
# @env LLM_TOOL_MODE=summary             Default execution mode override
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
import shutil
import subprocess
import sys
import time
from datetime import datetime, timedelta
from enum import Enum
from pathlib import Path
from typing import Any, Literal, Optional

__version__ = "2.5.0"
__all__ = [
    "run",
    "execute_tool",
    "main",
    "validate_inputs",
    "build_cache_key",
    "invalidate_cache",
    "generate_tool_schema",
    "ToolCache",
    "ToolError",
    "GracefulShutdown",
    "get_agent_var",
    "get_builtin_var",
    "get_execution_context",
    "__version__",
]

# ==============================================================================
# SECTION 1: Exit Codes, Validation Rules & Exception Models
# ==============================================================================

EXIT_SUCCESS = 0
EXIT_ERROR = 1
EXIT_FILE_NOT_FOUND = 2
EXIT_TIMEOUT = 124
EXIT_PERMISSION_DENIED = 126
EXIT_INVALID_INPUT = 127
EXIT_INTERRUPTED = 130

VALID_MODES = {"summary", "detailed"}
VALID_ACTIONS = {"transcribe", "extract-audio", "inspect"}
VALID_MODELS = {"tiny", "base", "small", "medium", "large"}


class ExecutionMode(str, Enum):
    SUMMARY = "summary"
    DETAILED = "detailed"


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


def validate_inputs(
    target: Optional[str],
    action: str,
    model: str,
    mode: str,
    limit: Optional[int],
    timeout: Optional[float] = None,
) -> Optional[dict[str, Any]]:
    if not target or not target.strip():
        return {
            "success": False,
            "error": "Target media file path is required.",
            "exit_code": EXIT_INVALID_INPUT,
            "duration_ms": 0.0,
        }
    if action not in VALID_ACTIONS:
        return {
            "success": False,
            "error": f"Invalid action '{action}'. Allowed choices: {sorted(list(VALID_ACTIONS))}",
            "exit_code": EXIT_INVALID_INPUT,
            "duration_ms": 0.0,
        }
    if model not in VALID_MODELS:
        return {
            "success": False,
            "error": f"Invalid Whisper model '{model}'. Allowed choices: {sorted(list(VALID_MODELS))}",
            "exit_code": EXIT_INVALID_INPUT,
            "duration_ms": 0.0,
        }
    if mode not in VALID_MODES:
        return {
            "success": False,
            "error": f"Invalid mode '{mode}'. Allowed choices: {sorted(list(VALID_MODES))}",
            "exit_code": EXIT_INVALID_INPUT,
            "duration_ms": 0.0,
        }
    if limit is not None and limit < 0:
        return {
            "success": False,
            "error": f"Invalid limit '{limit}'. Limit must be >= 0.",
            "exit_code": EXIT_INVALID_INPUT,
            "duration_ms": 0.0,
        }
    if timeout is not None and timeout <= 0:
        return {
            "success": False,
            "error": f"Invalid timeout '{timeout}'. Timeout must be > 0 seconds.",
            "exit_code": EXIT_INVALID_INPUT,
            "duration_ms": 0.0,
        }
    return None


class ToolJSONEncoder(json.JSONEncoder):
    """Resilient JSON encoder handling Path, Enum, datetime, timedelta, bytes, and sets."""

    def default(self, obj: Any) -> Any:
        try:
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
        except Exception:
            pass
        return repr(obj)


# ==============================================================================
# SECTION 2: Terminal Colors & UI Display Helpers
# ==============================================================================

NEON_CYAN    = "\033[38;5;51m"
NEON_GREEN   = "\033[38;5;46m"
NEON_RED     = "\033[38;5;196m"
NEON_YELLOW  = "\033[38;5;226m"
NEON_PURPLE  = "\033[38;5;129m"
NEON_PINK    = "\033[38;5;198m"
RESET        = "\033[0m"
BOLD         = "\033[1m"
DIM          = "\033[2m"

_ANSI_RE = re.compile(r"\x1b(?:[@-Z\\-_]|\[[0-?]*[ -/]*[@-~])|\033\[[0-9;?]*[a-zA-Z]")


def _strip_ansi(text: str) -> str:
    return _ANSI_RE.sub("", text)


def _is_tty() -> bool:
    return sys.stderr.isatty() and os.environ.get("TERM", "").lower() not in ("dumb", "")


def _cprint(text: str, file: Any = None, no_color: bool = False, end: str = "\n") -> None:
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

    box_w = 68
    border = "─" * box_w

    _cprint(f"{NEON_PURPLE}╭{border}╮{RESET}")
    _cprint(f"{NEON_PURPLE}│{RESET} {NEON_PINK}⚡ [AUDIO TRANSCRIBE v{__version__}]{RESET} {status_color}{BOLD}{status_symbol} {status_text}{RESET}")
    _cprint(f"{NEON_PURPLE}├{border}┤{RESET}")
    _cprint(f"{NEON_PURPLE}│{RESET} {NEON_CYAN}Target:{RESET}      {data.get('target', 'N/A')}")
    _cprint(f"{NEON_PURPLE}│{RESET} {NEON_CYAN}Action:{RESET}      {NEON_YELLOW}{data.get('action', 'N/A')}{RESET}")
    _cprint(f"{NEON_PURPLE}│{RESET} {NEON_CYAN}Engine:{RESET}      {data.get('engine', 'N/A')}")
    _cprint(f"{NEON_PURPLE}│{RESET} {NEON_CYAN}Duration:{RESET}    {DIM}{data.get('duration_ms', 0)}ms{RESET}")

    if not success and "error" in data:
        _cprint(f"{NEON_PURPLE}├{border}┤{RESET}")
        _cprint(f"{NEON_PURPLE}│{RESET} {NEON_RED}Error:{RESET}       {data['error']}")

    out_file = data.get("extracted_audio") or data.get("saved_text")
    if out_file:
        _cprint(f"{NEON_PURPLE}├{border}┤{RESET}")
        _cprint(f"{NEON_PURPLE}│{RESET} {NEON_GREEN}Saved Output:{RESET} {out_file}")

    transcript = data.get("transcription")
    if transcript:
        _cprint(f"{NEON_PURPLE}├{border}┤{RESET}")
        _cprint(f"{NEON_PURPLE}│{RESET} {BOLD}Transcription Preview:{RESET}")
        for line in str(transcript).splitlines()[:6]:
            if len(line) > 64:
                line = line[:61] + "..."
            _cprint(f"{NEON_PURPLE}│{RESET}   {NEON_CYAN}›{RESET} {line}")

    _cprint(f"{NEON_PURPLE}╰{border}╯{RESET}")


# ==============================================================================
# SECTION 3: Agent Environment & Path Resolution Helpers
# ==============================================================================

def get_agent_var(name: str, default: str = "") -> str:
    return os.environ.get(f"LLM_AGENT_VAR_{name.upper()}", default)


def get_builtin_var(name: str) -> Optional[str]:
    return os.environ.get(f"LLM_AGENT_VAR_{name}")


def get_execution_context() -> dict[str, Any]:
    termux_prefix = os.environ.get("PREFIX", "")
    return {
        "tool_name": os.environ.get("LLM_TOOL_NAME", "audio_transcribe"),
        "cache_dir": os.environ.get("LLM_TOOL_CACHE_DIR"),
        "root_dir": os.environ.get("LLM_ROOT_DIR"),
        "output_path": os.environ.get("LLM_OUTPUT"),
        "cwd": get_builtin_var("__cwd__") or os.getcwd(),
        "termux_prefix": termux_prefix,
        "is_termux": "com.termux" in termux_prefix or Path("/data/data/com.termux").exists(),
    }


def resolve_agent_path(target_str: str) -> Path:
    raw_path = Path(target_str).expanduser()
    if raw_path.is_absolute():
        return raw_path.resolve(strict=False)

    agent_cwd = get_builtin_var("__cwd__")
    if agent_cwd:
        return (Path(agent_cwd) / raw_path).resolve(strict=False)

    return raw_path.resolve(strict=False)


def env_default(env_name: str, fallback: Any = None) -> dict[str, Any]:
    val = os.getenv(env_name)
    if val is not None:
        return {"default": val}
    if fallback is not None:
        return {"default": fallback}
    return {}


# ==============================================================================
# SECTION 4: Cache Management, Signal Handling & Tool Schema
# ==============================================================================

def build_cache_key(
    target_path: Path,
    mtime: float,
    action: str,
    model: str,
    language: Optional[str],
) -> str:
    return "|".join([
        __version__,
        str(target_path),
        str(mtime),
        action,
        model,
        language or "auto",
    ])


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

    def _hash_key(self, key_str: str) -> str:
        return hashlib.sha256(key_str.encode("utf-8")).hexdigest()

    def get(self, key_str: str, ttl_seconds: int = 86400) -> Optional[Any]:
        cache_file = self.cache_dir / f"{self._hash_key(key_str)}.cache"
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

    def set(self, key_str: str, value: Any) -> None:
        cache_file = self.cache_dir / f"{self._hash_key(key_str)}.cache"
        tmp_file = cache_file.with_suffix(".tmp")
        try:
            with open(tmp_file, "wb") as fp:
                pickle.dump(value, fp)
            tmp_file.replace(cache_file)
        except Exception:
            if tmp_file.exists():
                tmp_file.unlink(missing_ok=True)


def invalidate_cache(cache: Optional[ToolCache] = None, prefix: str = "") -> int:
    cache_obj = cache or ToolCache()
    removed = 0
    if not cache_obj.cache_dir.exists():
        return removed
    for file in cache_obj.cache_dir.glob("*.cache"):
        if not prefix or file.name.startswith(prefix):
            try:
                file.unlink(missing_ok=True)
                removed += 1
            except OSError:
                pass
    return removed


class GracefulShutdown:
    def __init__(self) -> None:
        self.interrupted = False
        self._old_sigint = None
        self._old_sigterm = None

    def __enter__(self) -> GracefulShutdown:
        self.interrupted = False
        try:
            self._old_sigint = signal.signal(signal.SIGINT, self._handle_signal)
            self._old_sigterm = signal.signal(signal.SIGTERM, self._handle_signal)
        except ValueError:
            pass
        return self

    def __exit__(self, exc_type: Any, exc_val: Any, exc_tb: Any) -> None:
        self.restore()

    def _handle_signal(self, signum: int, frame: Any) -> None:
        self.interrupted = True

    def restore(self) -> None:
        if getattr(self, "_old_sigint", None) is not None:
            try:
                signal.signal(signal.SIGINT, self._old_sigint)
            except ValueError:
                pass
        if getattr(self, "_old_sigterm", None) is not None:
            try:
                signal.signal(signal.SIGTERM, self._old_sigterm)
            except ValueError:
                pass

    def should_stop(self) -> bool:
        return getattr(self, "interrupted", False)


def generate_tool_schema() -> dict[str, Any]:
    return {
        "name": "audio_transcribe",
        "description": "Transcribe speech to text, extract audio tracks from video files, and inspect audio metadata.",
        "parameters": {
            "type": "object",
            "properties": {
                "target": {
                    "type": "string",
                    "description": "Target audio/video file path"
                },
                "action": {
                    "type": "string",
                    "enum": ["transcribe", "extract-audio", "inspect"],
                    "description": "Action mode (default: transcribe)"
                },
                "language": {
                    "type": "string",
                    "description": "Language code (e.g., 'en', 'es', or 'auto')"
                },
                "model": {
                    "type": "string",
                    "enum": ["tiny", "base", "small", "medium", "large"],
                    "description": "Whisper model size (default: base)"
                },
                "output_text": {
                    "type": "string",
                    "description": "Save transcription output to text file path"
                },
                "mode": {
                    "type": "string",
                    "enum": ["summary", "detailed"],
                    "description": "Execution mode (default: summary)"
                },
                "limit": {
                    "type": "integer",
                    "description": "Maximum characters in transcription text output (default: 5000)"
                },
                "use_cache": {
                    "type": "boolean",
                    "description": "Enable result caching for transcriptions"
                }
            },
            "required": ["target"]
        }
    }


# ==============================================================================
# SECTION 5: Core Execution Engine
# ==============================================================================

def execute_tool(
    target: str,
    action: str = "transcribe",
    language: Optional[str] = None,
    model: str = "base",
    output_text: Optional[str] = None,
    mode: str = "summary",
    limit: Optional[int] = None,
    timeout: Optional[float] = None,
    use_cache: bool = False,
    no_color: bool = False,
    verbose: bool = False,
) -> dict[str, Any]:
    start_time = time.monotonic()

    bad_inputs = validate_inputs(target, action, model, mode, limit, timeout)
    if bad_inputs:
        return bad_inputs

    limit_val = limit if limit is not None else 5000
    req_timeout = timeout if timeout is not None else 120.0
    target_path = resolve_agent_path(target)

    if not target_path.exists():
        return {
            "success": False,
            "error": f"Target file does not exist: {target} (Resolved: {target_path})",
            "exit_code": EXIT_FILE_NOT_FOUND,
            "duration_ms": 0.0,
        }

    mtime = target_path.stat().st_mtime
    cache = ToolCache()
    cache_key = build_cache_key(target_path, mtime, action, model, language)

    if use_cache and action == "transcribe":
        cached_result = cache.get(cache_key)
        if cached_result is not None:
            cached_result["cached"] = True
            return cached_result

    ffmpeg_bin = shutil.which("ffmpeg")
    ffprobe_bin = shutil.which("ffprobe")
    whisper_bin = shutil.which("whisper")

    engine_used = "none"
    transcription_text: Optional[str] = None
    extracted_audio_path: Optional[str] = None
    saved_text_path: Optional[str] = None
    media_metadata: Optional[dict[str, Any]] = None

    try:
        with GracefulShutdown() as shutdown:
            # ------------------------------------------------------------------
            # ACTION 1: INSPECT MEDIA METADATA
            # ------------------------------------------------------------------
            if action == "inspect" or mode == "detailed":
                if ffprobe_bin:
                    cmd = [
                        ffprobe_bin,
                        "-v", "quiet",
                        "-print_format", "json",
                        "-show_format",
                        "-show_streams",
                        str(target_path),
                    ]
                    res = subprocess.run(cmd, capture_output=True, text=True, timeout=15.0)
                    if res.returncode == 0:
                        try:
                            media_metadata = json.loads(res.stdout)
                            engine_used = "ffprobe"
                        except json.JSONDecodeError:
                            pass

            # ------------------------------------------------------------------
            # ACTION 2: EXTRACT AUDIO TRACK
            # ------------------------------------------------------------------
            if action == "extract-audio" or (action == "transcribe" and not target_path.suffix.lower() in {".mp3", ".wav", ".m4a", ".flac", ".ogg"}):
                if ffmpeg_bin:
                    out_wav = target_path.with_suffix(".extracted.wav")
                    cmd = [
                        ffmpeg_bin,
                        "-y",
                        "-i", str(target_path),
                        "-vn",
                        "-acodec", "pcm_s16le",
                        "-ar", "16000",
                        "-ac", "1",
                        str(out_wav),
                    ]
                    res = subprocess.run(cmd, capture_output=True, text=True, timeout=req_timeout)
                    if res.returncode == 0:
                        extracted_audio_path = str(out_wav)
                        engine_used = "ffmpeg"
                        if action == "extract-audio":
                            return {
                                "success": True,
                                "target": str(target_path),
                                "action": action,
                                "extracted_audio": extracted_audio_path,
                                "engine": engine_used,
                                "duration_ms": round((time.monotonic() - start_time) * 1000, 2),
                                "exit_code": EXIT_SUCCESS,
                            }
                elif action == "extract-audio":
                    raise ToolError("ffmpeg binary is not available to extract audio.")

            # ------------------------------------------------------------------
            # ACTION 3: TRANSCRIBE SPEECH
            # ------------------------------------------------------------------
            if action == "transcribe":
                audio_input = extracted_audio_path or str(target_path)

                # Method A: Try python package whisper
                try:
                    import whisper # type: ignore
                    engine_used = "python-whisper"
                    model_obj = whisper.load_model(model)
                    kwargs = {}
                    if language and language != "auto":
                        kwargs["language"] = language
                    
                    res_dict = model_obj.transcribe(audio_input, **kwargs)
                    transcription_text = res_dict.get("text", "").strip()
                except ImportError:
                    # Method B: Try whisper CLI
                    if whisper_bin:
                        engine_used = "whisper-cli"
                        cmd = [whisper_bin, audio_input, "--model", model, "--output_format", "txt"]
                        if language and language != "auto":
                            cmd.extend(["--language", language])
                        
                        res = subprocess.run(cmd, capture_output=True, text=True, timeout=req_timeout)
                        if res.returncode == 0:
                            # Read auto created txt file
                            txt_file = Path(audio_input).with_suffix(".txt")
                            if txt_file.exists():
                                transcription_text = txt_file.read_text(encoding="utf-8").strip()
                                txt_file.unlink(missing_ok=True)
                            else:
                                transcription_text = res.stdout.strip()
                    else:
                        raise ToolError(
                            "No transcription engine available. Please install 'openai-whisper' ("
                            "pip install openai-whisper) or 'ffmpeg'."
                        )

            if shutdown.should_stop():
                raise ToolError("Execution interrupted by signal.", EXIT_INTERRUPTED)

            if transcription_text and len(transcription_text) > limit_val:
                preview_text = transcription_text[:limit_val] + f"\n... [Truncated {len(transcription_text) - limit_val} chars]"
            else:
                preview_text = transcription_text

            if output_text and transcription_text:
                out_path = resolve_agent_path(output_text)
                out_path.parent.mkdir(parents=True, exist_ok=True)
                out_path.write_text(transcription_text, encoding="utf-8")
                saved_text_path = str(out_path)

        duration_ms = round((time.monotonic() - start_time) * 1000, 2)

        result: dict[str, Any] = {
            "success": True,
            "target": str(target_path),
            "action": action,
            "model": model,
            "language": language or "auto",
            "engine": engine_used,
            "transcription": preview_text,
            "extracted_audio": extracted_audio_path,
            "saved_text": saved_text_path,
            "media_metadata": media_metadata if mode == "detailed" else None,
            "context": get_execution_context(),
            "cached": False,
            "duration_ms": duration_ms,
            "exit_code": EXIT_SUCCESS,
        }

        if use_cache and action == "transcribe":
            cache.set(cache_key, result)

        return result

    except ToolError as exc:
        duration_ms = round((time.monotonic() - start_time) * 1000, 2)
        return {
            "success": False,
            "error": exc.message,
            "exit_code": exc.exit_code,
            "duration_ms": duration_ms,
        }
    except subprocess.TimeoutExpired:
        duration_ms = round((time.monotonic() - start_time) * 1000, 2)
        return {
            "success": False,
            "error": f"Audio transcription timed out after {req_timeout}s",
            "exit_code": EXIT_TIMEOUT,
            "duration_ms": duration_ms,
        }
    except Exception as exc:
        duration_ms = round((time.monotonic() - start_time) * 1000, 2)
        return {
            "success": False,
            "error": f"Audio tool execution error: {exc}",
            "exit_code": EXIT_ERROR,
            "duration_ms": duration_ms,
        }


# ==============================================================================
# SECTION 6: Output Routing
# ==============================================================================

def write_llm_output(data: dict[str, Any]) -> None:
    out_path = os.environ.get("LLM_OUTPUT", "/dev/stdout")
    payload = json.dumps(data, ensure_ascii=False, cls=ToolJSONEncoder)

    if out_path in {"/dev/stdout", "/dev/fd/1", "-"}:
        sys.stdout.write(payload + "\n")
        sys.stdout.flush()
        return

    try:
        Path(out_path).parent.mkdir(parents=True, exist_ok=True)
        with open(out_path, "a", encoding="utf-8") as fp:
            fp.write(payload + "\n")
    except OSError as err:
        sys.stderr.write(f"Failed writing to LLM_OUTPUT '{out_path}': {err}\n")
        sys.stdout.write(payload + "\n")
        sys.stdout.flush()


# ==============================================================================
# SECTION 7: Function Entry Point for AIChat
# ==============================================================================

def run(
    target: str,
    action: Literal["transcribe", "extract-audio", "inspect"] = "transcribe",
    language: Optional[str] = None,
    model: Literal["tiny", "base", "small", "medium", "large"] = "base",
    output_text: Optional[str] = None,
    mode: Literal["summary", "detailed"] = "summary",
    limit: Optional[int] = None,
    timeout: Optional[float] = None,
    use_cache: bool = False,
    no_color: bool = False,
    verbose: bool = False,
) -> None:
    """AIChat Tool entrypoint function."""
    result = execute_tool(
        target=target,
        action=action,
        language=language,
        model=model,
        output_text=output_text,
        mode=mode,
        limit=limit,
        timeout=timeout,
        use_cache=use_cache,
        no_color=no_color,
        verbose=verbose,
    )

    print_human_readable_ui(result, no_color=no_color)
    write_llm_output(result)


# ==============================================================================
# SECTION 8: CLI Parser & Main Entrypoint
# ==============================================================================

def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="audio_transcribe.py",
        description=f"AIChat Audio & Speech Tool v{__version__}",
    )
    parser.add_argument(
        "--target", "-t",
        required=False,
        metavar="PATH",
        help="Target audio or video file path",
    )
    parser.add_argument(
        "--action", "-a",
        choices=["transcribe", "extract-audio", "inspect"],
        default="transcribe",
        help="Operation action (default: transcribe)",
    )
    parser.add_argument(
        "--language", "-l",
        help="Language code (e.g. en, es, auto)",
    )
    parser.add_argument(
        "--model", "-m",
        choices=["tiny", "base", "small", "medium", "large"],
        default="base",
        help="Whisper model size (default: base)",
    )
    parser.add_argument(
        "--output-text",
        dest="output_text",
        metavar="PATH",
        help="Save transcription to text file path",
    )
    parser.add_argument(
        "--mode",
        choices=["summary", "detailed"],
        **env_default("LLM_TOOL_MODE", "summary"),
        help="Execution mode (default: summary)",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=5000,
        help="Maximum characters in transcription text output (default: 5000)",
    )
    parser.add_argument(
        "--timeout",
        type=float,
        help="Maximum execution timeout in seconds",
    )
    parser.add_argument(
        "--use-cache",
        action="store_true",
        dest="use_cache",
        help="Enable result caching for transcriptions",
    )
    parser.add_argument(
        "--clear-cache",
        action="store_true",
        dest="clear_cache",
        help="Clear tool cache directory and exit",
    )
    parser.add_argument(
        "--schema",
        action="store_true",
        help="Print JSON Tool Schema for LLM registration and exit",
    )
    parser.add_argument(
        "--no-color",
        action="store_true",
        dest="no_color",
        help="Disable ANSI color output",
    )
    parser.add_argument(
        "--verbose", "-v",
        action="store_true",
        help="Enable detailed debug logging",
    )
    return parser


def main() -> int:
    args = _build_parser().parse_args()

    if args.schema:
        schema = generate_tool_schema()
        sys.stdout.write(json.dumps(schema, indent=2) + "\n")
        sys.stdout.flush()
        return EXIT_SUCCESS

    if args.clear_cache:
        removed = invalidate_cache()
        _cprint(f"{NEON_GREEN}Cleared {removed} cache file(s).{RESET}", no_color=args.no_color)
        return EXIT_SUCCESS

    if not args.target:
        _cprint(f"{NEON_RED}Error: --target parameter is required for execution.{RESET}", no_color=args.no_color)
        return EXIT_INVALID_INPUT

    res = execute_tool(
        target=args.target,
        action=args.action,
        language=args.language,
        model=args.model,
        output_text=args.output_text,
        mode=args.mode,
        limit=args.limit,
        timeout=args.timeout,
        use_cache=args.use_cache,
        no_color=args.no_color,
        verbose=args.verbose,
    )

    print_human_readable_ui(res, no_color=args.no_color)
    write_llm_output(res)
    return res.get("exit_code", EXIT_SUCCESS)


if __name__ == "__main__":
    sys.exit(main())
