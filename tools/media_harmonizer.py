#!/usr/bin/env python3
# ==============================================================================
# media_harmonizer.py — Pyrmethus AIChat Tool Template v1.1.0
# argc/aichat compatible · Human-Readable Colorized Outputs
#
# @describe Harmonic Media Transmuter — Transcribes, translates, summarizes media files or generates subtitles using ffmpeg & Whisper.
#
# @option --target! <PATH>               Target video or audio file path (required)
# @option --action <ACTION>              Execution action: transcribe, translate, summarize, subtitles, all (default: summarize)
# @option --model <MODEL>                Whisper model size: tiny, base, small, medium, large (default: base)
# @option --language <LANG>              Source audio language code e.g. en, es, fr, auto (default: auto)
# @option --output-dir <DIR>             Directory to save generated output files (default: same as target)
# @flag   --no-color                     Disable ANSI color output
# @flag   --verbose                      Enable detailed debug log output
#
# @env LLM_OUTPUT=/dev/stdout            Output path for LLM integration
# ==============================================================================

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from typing import Any, Optional

__version__ = "1.1.0"

# ==============================================================================
# SECTION 1: Color Palette & Formatting Helpers
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

_ANSI_RE = re.compile(r"\033\[[0-9;]*[mGKHF]")


def _strip_ansi(text: str) -> str:
    """Remove all ANSI escape sequences from text."""
    return _ANSI_RE.sub("", text)


def _is_tty() -> bool:
    """Return True if stdout is attached to an interactive terminal."""
    return sys.stdout.isatty()


def _cprint(text: str, file: Any = None, no_color: bool = False) -> None:
    """Print pre-formatted ANSI text, stripping colors if stdout is not a TTY or --no-color is set."""
    target = file or sys.stdout
    if no_color or not _is_tty():
        text = _strip_ansi(text)
    print(text, file=target, flush=True)


def print_human_readable_ui(data: dict[str, Any], no_color: bool = False) -> None:
    """
    Render a human-friendly, colorized box UI for terminal users.
    Only executes if running in an interactive TTY.
    """
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
        f"{NEON_PURPLE}│{RESET} {NEON_PINK}⚡ [HARMONIC MEDIA TRANSMUTER]{RESET} {status_color}{BOLD}{status_symbol} {status_text}{RESET}"
    )
    _cprint(f"{NEON_PURPLE}├{border}┤{RESET}")
    _cprint(
        f"{NEON_PURPLE}│{RESET} {NEON_CYAN}Target:{RESET}       {data.get('target', 'N/A')}"
    )
    _cprint(
        f"{NEON_PURPLE}│{RESET} {NEON_CYAN}Action:{RESET}       {NEON_YELLOW}{data.get('action', 'N/A')}{RESET}"
    )
    _cprint(
        f"{NEON_PURPLE}│{RESET} {NEON_CYAN}Model:{RESET}        {data.get('model', 'base')}"
    )
    _cprint(
        f"{NEON_PURPLE}│{RESET} {NEON_CYAN}Word Count:{RESET}   {NEON_GREEN}{data.get('word_count', 0):,}{RESET}"
    )
    _cprint(
        f"{NEON_PURPLE}│{RESET} {NEON_CYAN}Duration:{RESET}     {DIM}{data.get('duration_ms', 0)}ms{RESET}"
    )

    if not success and "error" in data:
        _cprint(f"{NEON_PURPLE}├{border}┤{RESET}")
        _cprint(f"{NEON_PURPLE}│{RESET} {NEON_RED}Error:{RESET}        {data['error']}")

    created_files = data.get("generated_files", [])
    if created_files:
        _cprint(f"{NEON_PURPLE}├{border}┤{RESET}")
        _cprint(
            f"{NEON_PURPLE}│{RESET} {BOLD}Generated Artifacts ({len(created_files)}):{RESET}"
        )
        for file_path in created_files:
            _cprint(f"{NEON_PURPLE}│{RESET}   {NEON_CYAN}›{RESET} {file_path}")

    summary_text = data.get("summary")
    if summary_text:
        _cprint(f"{NEON_PURPLE}├{border}┤{RESET}")
        _cprint(f"{NEON_PURPLE}│{RESET} {BOLD}Executive Summary Preview:{RESET}")
        preview_lines = summary_text.strip().splitlines()[:4]
        for line in preview_lines:
            _cprint(
                f"{NEON_PURPLE}│{RESET}   {DIM}{line[:60]}...{RESET}"
                if len(line) > 60
                else f"{NEON_PURPLE}│{RESET}   {line}"
            )

    _cprint(f"{NEON_PURPLE}╰{border}╯{RESET}")


# ==============================================================================
# SECTION 2: Core Logic Implementation
# ==============================================================================


def _check_dependencies() -> tuple[bool, str]:
    """Verify system dependencies (ffmpeg and whisper)."""
    if not shutil.which("ffmpeg"):
        return (
            False,
            "ffmpeg not found in PATH. Install via Termux: 'pkg install ffmpeg'",
        )

    # Check whisper CLI or python package
    has_whisper_cli = shutil.which("whisper") is not None
    try:
        import whisper  # type: ignore # noqa: F401

        has_whisper_py = True
    except ImportError:
        has_whisper_py = False

    if not (has_whisper_cli or has_whisper_py):
        return (
            False,
            "Whisper not found. Install via: 'pip install openai-whisper' or setup whisper CLI",
        )

    return True, ""


def _extract_audio(
    input_file: Path, temp_wav_path: Path, verbose: bool = False
) -> None:
    """Extract audio from target video/audio file to 16kHz mono WAV using ffmpeg."""
    cmd = [
        "ffmpeg",
        "-y",
        "-i",
        str(input_file),
        "-vn",
        "-acodec",
        "pcm_s16le",
        "-ar",
        "16000",
        "-ac",
        "1",
        str(temp_wav_path),
    ]
    stdout_dest = None if verbose else subprocess.DEVNULL
    stderr_dest = None if verbose else subprocess.DEVNULL
    subprocess.run(cmd, check=True, stdout=stdout_dest, stderr=stderr_dest)


def _run_whisper_transcription(
    audio_path: Path, model_size: str, language: str, task: str = "transcribe"
) -> dict[str, Any]:
    """Execute Whisper transcription either using Python module or CLI fallback."""
    # Attempt Python module execution
    try:
        import whisper  # type: ignore

        model = whisper.load_model(model_size)
        kwargs = {"task": task}
        if language and language.lower() != "auto":
            kwargs["language"] = language

        result = model.transcribe(str(audio_path), **kwargs)
        return {
            "text": result.get("text", "").strip(),
            "segments": result.get("segments", []),
            "language": result.get("language", language),
        }
    except Exception:
        pass

    # Fallback to Whisper CLI execution
    if shutil.which("whisper"):
        with tempfile.TemporaryDirectory() as tmp_out:
            cmd = [
                "whisper",
                str(audio_path),
                "--model",
                model_size,
                "--task",
                task,
                "--output_dir",
                tmp_out,
                "--output_format",
                "json",
            ]
            if language and language.lower() != "auto":
                cmd.extend(["--language", language])

            subprocess.run(
                cmd, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL
            )
            json_file = Path(tmp_out) / f"{audio_path.stem}.json"
            if json_file.exists():
                with open(json_file, encoding="utf-8") as f:
                    data = json.load(f)
                    return {
                        "text": data.get("text", "").strip(),
                        "segments": data.get("segments", []),
                        "language": data.get("language", language),
                    }

    raise RuntimeError(
        "Whisper transcription failed on both Python API and CLI interface."
    )


def _generate_subtitles_srt(segments: list[dict[str, Any]]) -> str:
    """Format Whisper segments into standard SRT subtitle format."""

    def format_timestamp(seconds: float) -> str:
        millis = int((seconds % 1) * 1000)
        seconds_int = int(seconds)
        mins, secs = divmod(seconds_int, 60)
        hours, mins = divmod(mins, 60)
        return f"{hours:02d}:{mins:02d}:{secs:02d},{millis:03d}"

    srt_lines = []
    for idx, seg in enumerate(segments, 1):
        start = format_timestamp(seg.get("start", 0.0))
        end = format_timestamp(seg.get("end", 0.0))
        text = seg.get("text", "").strip()
        srt_lines.append(f"{idx}\n{start} --> {end}\n{text}\n")

    return "\n".join(srt_lines)


def _generate_structured_summary(transcript: str, language: str) -> str:
    """Generate executive summary and structured key takeaways from transcript."""
    words = transcript.split()
    word_count = len(words)

    # Simple chunk-based extraction fallback for local processing
    sentences = re.split(r"(?<=[.!?]) +", transcript)
    key_sentences = sentences[:5] if len(sentences) >= 5 else sentences

    summary_lines = [
        "# Harmonic Media Summary",
        f"- **Language**: {language}",
        f"- **Total Words**: {word_count:,}",
        "\n## Executive Summary",
        " ".join(key_sentences),
        "\n## Key Highlights",
    ]

    step = max(1, len(sentences) // 4)
    for i in range(0, len(sentences), step):
        if sentences[i].strip():
            summary_lines.append(f"- {sentences[i].strip()}")

    return "\n".join(summary_lines)


def execute_tool(
    target: str,
    action: str = "summarize",
    model: str = "base",
    language: str = "auto",
    output_dir: Optional[str] = None,
    no_color: bool = False,
    verbose: bool = False,
) -> dict[str, Any]:
    """
    Core execution logic for media transcription, translation, summarization, and subtitle generation.
    """
    start_time = time.perf_counter()
    target_path = Path(target).expanduser().resolve()

    if not target_path.exists():
        return {
            "success": False,
            "error": f"Target media path does not exist: {target}",
            "exit_code": 1,
        }

    # Verify dependency tools
    deps_ok, dep_err = _check_dependencies()
    if not deps_ok:
        return {"success": False, "error": dep_err, "exit_code": 1}

    # Setup output directory
    out_dir = (
        Path(output_dir).expanduser().resolve() if output_dir else target_path.parent
    )
    out_dir.mkdir(parents=True, exist_ok=True)

    action_clean = action.lower().strip()
    generated_artifacts: list[str] = []

    try:
        with tempfile.TemporaryDirectory(prefix="harmonizer_") as tmp_dir:
            temp_wav = Path(tmp_dir) / "extracted_audio.wav"

            # Step 1: Extract Audio via ffmpeg
            _extract_audio(target_path, temp_wav, verbose=verbose)

            # Step 2: Whisper Transcription / Translation
            task_type = "translate" if action_clean == "translate" else "transcribe"
            whisper_res = _run_whisper_transcription(
                audio_path=temp_wav, model_size=model, language=language, task=task_type
            )

            transcript_text = whisper_res.get("text", "")
            segments = whisper_res.get("segments", [])
            detected_lang = whisper_res.get("language", "auto")
            word_count = len(transcript_text.split())

            stem = target_path.stem

            # Step 3: Handle Action Outputs
            if action_clean in ("transcribe", "translate", "all"):
                txt_file = out_dir / f"{stem}_{action_clean}.txt"
                txt_file.write_text(transcript_text, encoding="utf-8")
                generated_artifacts.append(str(txt_file))

            if action_clean in ("subtitles", "all"):
                srt_content = _generate_subtitles_srt(segments)
                srt_file = out_dir / f"{stem}.srt"
                srt_file.write_text(srt_content, encoding="utf-8")
                generated_artifacts.append(str(srt_file))

            summary_output = None
            if action_clean in ("summarize", "all"):
                summary_output = _generate_structured_summary(
                    transcript_text, detected_lang
                )
                sum_file = out_dir / f"{stem}_summary.md"
                sum_file.write_text(summary_output, encoding="utf-8")
                generated_artifacts.append(str(sum_file))

            duration_ms = round((time.perf_counter() - start_time) * 1000, 2)

            return {
                "success": True,
                "target": str(target_path),
                "action": action_clean,
                "model": model,
                "language": detected_lang,
                "word_count": word_count,
                "transcript": transcript_text
                if len(transcript_text) < 1000
                else transcript_text[:1000] + "...",
                "summary": summary_output,
                "generated_files": generated_artifacts,
                "duration_ms": duration_ms,
                "exit_code": 0,
            }

    except Exception as exc:
        return {
            "success": False,
            "error": f"Media transmutation failed: {exc}",
            "exit_code": 1,
        }


# ==============================================================================
# SECTION 3: Output Routing (LLM vs Human Terminal)
# ==============================================================================


def write_llm_output(data: dict[str, Any]) -> None:
    """Format and write clean JSON output to LLM_OUTPUT destination."""
    out_path = os.environ.get("LLM_OUTPUT", "/dev/stdout")
    json_payload = json.dumps(data, indent=2, ensure_ascii=False) + "\n"

    direct_targets = {"/dev/stdout", "/dev/fd/1", "-"}
    if out_path in direct_targets:
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
# SECTION 4: Function Entry Point for AIChat
# ==============================================================================


def run(
    target: str,
    action: str = "summarize",
    model: str = "base",
    language: str = "auto",
    output_dir: Optional[str] = None,
    no_color: bool = False,
    verbose: bool = False,
) -> None:
    """
    AIChat Programmatic Entrypoint.
    Parameter names match option/flag slugs (with underscores).
    """
    result = execute_tool(
        target=target,
        action=action,
        model=model,
        language=language,
        output_dir=output_dir,
        no_color=no_color,
        verbose=verbose,
    )

    print_human_readable_ui(result, no_color=no_color)
    write_llm_output(result)


# ==============================================================================
# SECTION 5: CLI Argument Parser
# ==============================================================================


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="media_harmonizer.py",
        description=f"Harmonic Media Transmuter v{__version__}",
    )
    # Support positional argument as requested: `python media_harmonizer.py video.mp4 --action transcribe`
    parser.add_argument(
        "pos_target",
        nargs="?",
        metavar="TARGET",
        help="Target video or audio file path",
    )
    parser.add_argument(
        "--target",
        "-t",
        dest="target",
        metavar="PATH",
        help="Target video or audio file path (required if positional not supplied)",
    )
    parser.add_argument(
        "--action",
        "-a",
        choices=["transcribe", "translate", "summarize", "subtitles", "all"],
        default="summarize",
        help="Execution action (default: summarize)",
    )
    parser.add_argument(
        "--model",
        "-m",
        choices=["tiny", "base", "small", "medium", "large"],
        default="base",
        help="Whisper model size (default: base)",
    )
    parser.add_argument(
        "--language",
        "-l",
        default="auto",
        help="Source language code e.g. en, es, fr (default: auto)",
    )
    parser.add_argument(
        "--output-dir",
        "-o",
        dest="output_dir",
        metavar="DIR",
        help="Directory to save generated output files",
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
    parser = _build_parser()
    args = parser.parse_args()

    resolved_target = args.target or args.pos_target
    if not resolved_target:
        parser.error(
            "Target media file is required (e.g. 'python media_harmonizer.py video.mp4')"
        )

    res = execute_tool(
        target=resolved_target,
        action=args.action,
        model=args.model,
        language=args.language,
        output_dir=args.output_dir,
        no_color=args.no_color,
        verbose=args.verbose,
    )

    print_human_readable_ui(res, no_color=args.no_color)
    write_llm_output(res)
    sys.exit(res.get("exit_code", 0))
