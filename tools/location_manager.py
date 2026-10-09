#!/usr/bin/env python3
# ==============================================================================
# location_manager.py — Pyrmethus AIChat Tool Master Template v2.3.0-ASCENDED
# argc/aichat compatible · Human-Readable Colorized Outputs · Native Safe Caching
#
# @describe Consolidates pin location tools and review pinned locations tools into a unified location manager.
#
# @meta require-tools aichat
#
# @option --action <ACTION>          Operation to perform: pin, review, or merge (default: review)
# @option --location-id <ID>         Identifier for a location entry
# @option --latitude <LAT>           Latitude coordinate for pinning
# @option --longitude <LON>          Longitude coordinate for pinning
# @option --label <LABEL>            Custom label or title for the location
# @option --notes <NOTES>            Additional descriptive notes for the pin
# @option --mode <MODE>              Execution mode: summary/detailed (default: summary)
# @option --limit <NUM>              Maximum locations to process (default: 100)
# @option --file-pattern <PATTERN>   File glob pattern filter (e.g., *.json)
# @option --env-var <KEY=VALUE>      Custom environment variable (repeatable)
# @flag   --recursive                Process location data recursively
# @flag   --use-cache                Enable result caching for expensive operations
# @flag   --no-color                 Disable ANSI color output
# @flag   --verbose                  Enable detailed debug log output
#
# @env LLM_OUTPUT=/dev/stdout        Output path for LLM integration
# ==============================================================================

from __future__ import annotations

import argparse
import fnmatch
import hashlib
import json
import logging
import os
import re
import signal
import sys
import time
from datetime import datetime, timedelta
from enum import Enum
from pathlib import Path
from typing import Any, Literal, Optional

__version__ = '2.3.0'
__all__ = [
    'run',
    'execute_tool',
    'ToolCache',
    'ToolError',
    'get_agent_var',
    'get_builtin_var',
    'get_execution_context',
    '__version__',
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


class ExecutionMode(str, Enum):
    SUMMARY = 'summary'
    DETAILED = 'detailed'


class ActionType(str, Enum):
    PIN = 'pin'
    REVIEW = 'review'
    MERGE = 'merge'


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
            'success': False,
            'error': self.message,
            'exit_code': self.exit_code,
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
            return obj.decode('utf-8', errors='replace')
        if isinstance(obj, (set, frozenset)):
            return list(obj)
        return super().default(obj)


# ==============================================================================
# SECTION 2: Terminal Color Palette & UI Helpers
# ==============================================================================

NEON_CYAN    = '\033[38;5;51m'
NEON_GREEN   = '\033[38;5;46m'
NEON_RED     = '\033[38;5;196m'
NEON_YELLOW  = '\033[38;5;226m'
NEON_PURPLE  = '\033[38;5;129m'
NEON_PINK    = '\033[38;5;198m'
RESET        = '\033[0m'
BOLD         = '\033[1m'
DIM          = '\033[2m'

# Advanced ANSI escape sequence stripping regex (includes 24-bit RGB and control codes)
_ANSI_RE = re.compile(
    r'\x1b(?:[@-Z\\-_]|\[[0-?]*[ -/]*[@-~])|\033\[[0-9;?]*[a-zA-Z]'
)


def _strip_ansi(text: str) -> str:
    """Remove all ANSI escape sequences from text."""
    return _ANSI_RE.sub('', text)


def _is_tty() -> bool:
    """Return True if stderr is attached to an interactive, non-dumb terminal."""
    return sys.stderr.isatty() and os.environ.get('TERM', '').lower() not in ('dumb', '')


def _cprint(text: str, file: Any = None, no_color: bool = False, end: str = '\n') -> None:
    """Print pre-formatted ANSI text to stderr by default, stripping colors if stream is not a TTY."""
    target = file or sys.stderr
    if no_color or not _is_tty():
        text = _strip_ansi(text)
    print(text, file=target, flush=True, end=end)


def print_progress(current: int, total: int, message: str = '', no_color: bool = False) -> None:
    """Render a visual progress bar for long-running batch operations on stderr."""
    if not _is_tty() or no_color:
        return
    percent = (current / total) * 100.0 if total > 0 else 100.0
    bar_width = 30
    filled = int(bar_width * percent / 100.0)
    bar = '█' * filled + '░' * (bar_width - filled)

    _cprint(
        f'\r{NEON_CYAN}Progress:{RESET} [{NEON_GREEN}{bar}{RESET}] {percent:.1f}% {message}',
        end='',
        no_color=no_color,
    )
    if current >= total:
        _cprint('', no_color=no_color)


def print_human_readable_ui(data: dict[str, Any], no_color: bool = False) -> None:
    """
    Render a human-friendly, colorized box UI for terminal users to stderr.
    Only executes if running in an interactive TTY.
    """
    if not _is_tty() or no_color:
        return

    success = data.get('success', False)
    status_color = NEON_GREEN if success else NEON_RED
    status_symbol = '✓' if success else '✗'
    status_text = 'SUCCESS' if success else 'FAILED'

    box_w = 64
    border = '─' * box_w

    _cprint(f'{NEON_PURPLE}╭{border}╮{RESET}')
    _cprint(f'{NEON_PURPLE}│{RESET} {NEON_PINK}⚡ [LOCATION MANAGER v{__version__}]{RESET} {status_color}{BOLD}{status_symbol} {status_text}{RESET}')
    _cprint(f'{NEON_PURPLE}├{border}┤{RESET}')
    _cprint(f'{NEON_PURPLE}│{RESET} {NEON_CYAN}Action:{RESET}   {data.get("action", "N/A")}')
    _cprint(f'{NEON_PURPLE}│{RESET} {NEON_CYAN}Mode:{RESET}     {data.get("mode", "N/A")}')
    _cprint(f'{NEON_PURPLE}│{RESET} {NEON_CYAN}Count:{RESET}    {NEON_YELLOW}{data.get("count", 0)}{RESET}')
    _cprint(f'{NEON_PURPLE}│{RESET} {NEON_CYAN}Cached:{RESET}   {NEON_YELLOW}{data.get("cached", False)}{RESET}')
    _cprint(f'{NEON_PURPLE}│{RESET} {NEON_CYAN}Duration:{RESET} {DIM}{data.get("duration_ms", 0)}ms{RESET}')

    if not success and 'error' in data:
        _cprint(f'{NEON_PURPLE}├{border}┤{RESET}')
        _cprint(f'{NEON_PURPLE}│{RESET} {NEON_RED}Error:{RESET}    {data["error"]}')

    items = data.get('items', [])
    if items:
        _cprint(f'{NEON_PURPLE}├{border}┤{RESET}')
        _cprint(f'{NEON_PURPLE}│{RESET} {BOLD}Pinned Locations ({len(items)}):{RESET}')
        for item in items[:10]:
            label = item.get('label', item.get('id', 'Unknown'))
            lat = item.get('latitude', 'N/A')
            lon = item.get('longitude', 'N/A')
            _cprint(f'{NEON_PURPLE}│{RESET}   {NEON_CYAN}›{RESET} {BOLD}{label}{RESET} [{lat}, {lon}]')
        if len(items) > 10:
            _cprint(f'{NEON_PURPLE}│{RESET}   {DIM}... and {len(items) - 10} more items{RESET}')

    _cprint(f'{NEON_PURPLE}╰{border}╯{RESET}')


# ==============================================================================
# SECTION 3: Agent & Environment Helpers
# ==============================================================================

def get_agent_var(name: str, default: str = '') -> str:
    """Access agent user-defined variables (LLM_AGENT_VAR_<NAME>)."""
    env_name = f'LLM_AGENT_VAR_{name.upper()}'
    return os.environ.get(env_name, default)


def get_builtin_var(name: str) -> Optional[str]:
    """Access agent built-in environment variables (e.g., __cwd__, __os__)."""
    env_name = f'LLM_AGENT_VAR_{name}'
    return os.environ.get(env_name)


def get_execution_context() -> dict[str, Any]:
    """Extract complete execution context from the llm-functions and Termux environment."""
    termux_prefix = os.environ.get('PREFIX', '')
    return {
        'tool_name': os.environ.get('LLM_TOOL_NAME'),
        'cache_dir': os.environ.get('LLM_TOOL_CACHE_DIR'),
        'root_dir': os.environ.get('LLM_ROOT_DIR'),
        'output_path': os.environ.get('LLM_OUTPUT'),
        'cwd': get_builtin_var('__cwd__') or os.getcwd(),
        'termux_prefix': termux_prefix,
        'is_termux': 'com.termux' in termux_prefix or Path('/data/data/com.termux').exists(),
    }


def _parse_env_vars(env_vars: Optional[list[str]]) -> dict[str, str]:
    """Parse environment variables provided in KEY=VALUE format."""
    if not env_vars:
        return {}
    parsed: dict[str, str] = {}
    for item in env_vars:
        if '=' in item:
            key, val = item.split('=', 1)
            parsed[key.strip()] = val.strip()
    return parsed


# ==============================================================================
# SECTION 4: Safe JSON-Based Native Caching & Signal Handlers
# ==============================================================================

class ToolCache:
    """Caching utility using safe JSON serialization with TTL support."""

    def __init__(self, cache_dir: Optional[Path] = None) -> None:
        if cache_dir:
            self.cache_dir = cache_dir
        elif 'LLM_TOOL_CACHE_DIR' in os.environ:
            self.cache_dir = Path(os.environ['LLM_TOOL_CACHE_DIR'])
        else:
            self.cache_dir = Path.home() / '.cache' / 'aichat_tools'

        try:
            self.cache_dir.mkdir(parents=True, exist_ok=True)
        except OSError:
            pass

    def _make_key(self, key_data: str) -> str:
        return hashlib.sha256(key_data.encode('utf-8')).hexdigest()

    def get(self, key_data: str, ttl_seconds: int = 3600) -> Optional[Any]:
        cache_file = self.cache_dir / f'{self._make_key(key_data)}.json'
        if not cache_file.exists():
            return None
        try:
            mtime = cache_file.stat().st_mtime
            if time.time() - mtime > ttl_seconds:
                cache_file.unlink(missing_ok=True)
                return None
            with open(cache_file, 'r', encoding='utf-8') as fp:
                return json.load(fp)
        except Exception:
            return None

    def set(self, key_data: str, value: Any) -> None:
        cache_file = self.cache_dir / f'{self._make_key(key_data)}.json'
        tmp_file = cache_file.with_suffix('.tmp')
        try:
            with open(tmp_file, 'w', encoding='utf-8') as fp:
                json.dump(value, fp, cls=ToolJSONEncoder, ensure_ascii=False)
            tmp_file.replace(cache_file)
        except Exception:
            if tmp_file.exists():
                tmp_file.unlink(missing_ok=True)


class GracefulShutdown:
    """Signal handler for graceful cancellation of batch operations."""

    def __init__(self) -> None:
        self.interrupted = False
        self._old_sigint = signal.signal(signal.SIGINT, self._handle_signal)
        self._old_sigterm = signal.signal(signal.SIGTERM, self._handle_signal)

    def _handle_signal(self, signum: int, frame: Any) -> None:
        self.interrupted = True

    def restore(self) -> None:
        """Restore previous signal handlers."""
        signal.signal(signal.SIGINT, self._old_sigint)
        signal.signal(signal.SIGTERM, self._old_sigterm)

    def should_stop(self) -> bool:
        return self.interrupted


# ==============================================================================
# SECTION 5: Core Logic Implementation
# ==============================================================================

def execute_tool(
    action: str = 'review',
    location_id: Optional[str] = None,
    latitude: Optional[float] = None,
    longitude: Optional[float] = None,
    label: Optional[str] = None,
    notes: Optional[str] = None,
    mode: str = 'summary',
    limit: Optional[int] = None,
    file_pattern: Optional[str] = None,
    env_vars: Optional[list[str]] = None,
    recursive: bool = False,
    use_cache: bool = False,
    no_color: bool = False,
    verbose: bool = False,
) -> dict[str, Any]:
    """
    Core location management logic combining pinning, reviewing, and merging locations.
    """
    start_time = time.monotonic()

    if verbose:
        logging.basicConfig(
            stream=sys.stderr,
            level=logging.DEBUG,
            format='[DEBUG] %(message)s',
            force=True,
        )
        logging.debug(f'Starting execution with action: {action}')

    parsed_env = _parse_env_vars(env_vars)
    limit_val = limit if (limit is not None and limit >= 0) else 100

    ctx = get_execution_context()
    storage_dir = Path(ctx['cache_dir']) if ctx['cache_dir'] else Path.home() / '.cache' / 'aichat_tools'
    storage_file = storage_dir / 'pinned_locations.json'

    cache = ToolCache()
    cache_key = f'locations:{action}:{location_id}:{latitude}:{longitude}:{label}:{mode}:{limit_val}'
    if use_cache and action == 'review':
        cached_result = cache.get(cache_key)
        if cached_result is not None:
            if verbose:
                logging.debug('Cache hit! Returning cached result.')
            cached_result['cached'] = True
            return cached_result

    shutdown = GracefulShutdown()

    try:
        locations_data: list[dict[str, Any]] = []
        if storage_file.exists():
            try:
                with open(storage_file, 'r', encoding='utf-8') as fp:
                    locations_data = json.load(fp)
            except Exception:
                locations_data = []

        if action == 'pin':
            if latitude is None or longitude is None:
                return {
                    'success': False,
                    'error': 'Latitude and Longitude are required for pinning a location.',
                    'exit_code': EXIT_INVALID_INPUT,
                    'duration_ms': round((time.monotonic() - start_time) * 1000, 2),
                }
            
            new_id = location_id or f'loc_{int(time.time() * 1000)}'
            new_entry = {
                'id': new_id,
                'latitude': float(latitude),
                'longitude': float(longitude),
                'label': label or f'Pin {new_id}',
                'notes': notes or '',
                'updated_at': datetime.now().isoformat(),
            }

            # Update existing or append
            updated = False
            for idx, loc in enumerate(locations_data):
                if loc.get('id') == new_id:
                    locations_data[idx] = new_entry
                    updated = True
                    break
            if not updated:
                locations_data.append(new_entry)

            with open(storage_file, 'w', encoding='utf-8') as fp:
                json.dump(locations_data, fp, cls=ToolJSONEncoder, indent=2)

            processed_items = [new_entry]

        elif action == 'merge':
            # Deduplicate locations by combining identical or near-identical items
            seen_ids: set[str] = set()
            merged_list: list[dict[str, Any]] = []
            for item in locations_data:
                if shutdown.should_stop():
                    return {
                        'success': False,
                        'error': 'Execution interrupted by user signal.',
                        'exit_code': EXIT_INTERRUPTED,
                        'duration_ms': round((time.monotonic() - start_time) * 1000, 2),
                    }
                item_id = item.get('id')
                if item_id and item_id not in seen_ids:
                    seen_ids.add(item_id)
                    merged_list.append(item)

            locations_data = merged_list
            with open(storage_file, 'w', encoding='utf-8') as fp:
                json.dump(locations_data, fp, cls=ToolJSONEncoder, indent=2)

            processed_items = locations_data[:limit_val]

        else:  # 'review' mode
            if location_id:
                processed_items = [loc for loc in locations_data if loc.get('id') == location_id]
            else:
                processed_items = locations_data[:limit_val]

        duration_ms = round((time.monotonic() - start_time) * 1000, 2)

        result: dict[str, Any] = {
            'success': True,
            'action': action,
            'mode': mode,
            'count': len(processed_items),
            'items': processed_items if mode == 'detailed' else processed_items[:10],
            'parsed_env_vars': parsed_env,
            'context': ctx,
            'cached': False,
            'duration_ms': duration_ms,
            'exit_code': EXIT_SUCCESS,
        }

        if use_cache:
            cache.set(cache_key, result)

        return result

    except PermissionError as exc:
        duration_ms = round((time.monotonic() - start_time) * 1000, 2)
        return {
            'success': False,
            'error': f'Permission denied accessing storage path: {exc}',
            'exit_code': EXIT_PERMISSION_DENIED,
            'duration_ms': duration_ms,
        }
    except Exception as exc:
        duration_ms = round((time.monotonic() - start_time) * 1000, 2)
        return {
            'success': False,
            'error': f'Tool execution error: {exc}',
            'exit_code': EXIT_ERROR,
            'duration_ms': duration_ms,
        }
    finally:
        shutdown.restore()


# ==============================================================================
# SECTION 6: Output Routing (LLM vs Human Terminal)
# ==============================================================================

def write_llm_output(data: dict[str, Any]) -> None:
    """Format and write clean JSON output to LLM_OUTPUT destination safely."""
    out_path = os.environ.get('LLM_OUTPUT', '/dev/stdout')
    json_payload = json.dumps(data, indent=2, ensure_ascii=False, cls=ToolJSONEncoder) + '\n'

    direct_targets = {'/dev/stdout', '/dev/fd/1', '-'}
    if out_path in direct_targets:
        sys.stdout.write(json_payload)
        sys.stdout.flush()
    else:
        try:
            Path(out_path).parent.mkdir(parents=True, exist_ok=True)
            with open(out_path, 'a', encoding='utf-8') as fp:
                fp.write(json_payload)
        except OSError as err:
            sys.stderr.write(f"Failed writing to LLM_OUTPUT '{out_path}': {err}\n")
            sys.stdout.write(json_payload)
            sys.stdout.flush()


# ==============================================================================
# SECTION 7: Function Entry Point for AIChat
# ==============================================================================

def run(
    action: Literal['pin', 'review', 'merge'] = 'review',
    location_id: Optional[str] = None,
    latitude: Optional[float] = None,
    longitude: Optional[float] = None,
    label: Optional[str] = None,
    notes: Optional[str] = None,
    mode: Literal['summary', 'detailed'] = 'summary',
    limit: Optional[int] = None,
    file_pattern: Optional[str] = None,
    env_var: Optional[list[str]] = None,
    recursive: bool = False,
    use_cache: bool = False,
    no_color: bool = False,
    verbose: bool = False,
) -> None:
    """Execute the location manager tool with specified parameters.

    Args:
        action: Operation to perform: pin, review, or merge (default: review)
        location_id: Identifier for a location entry
        latitude: Latitude coordinate for pinning
        longitude: Longitude coordinate for pinning
        label: Custom label or title for the location
        notes: Additional descriptive notes for the pin
        mode: Execution mode: summary or detailed (default: summary)
        limit: Maximum items to process (default: 100)
        file_pattern: Optional file glob pattern filter
        env_var: Custom environment variable in KEY=VALUE format (repeatable)
        recursive: Process directories recursively
        use_cache: Enable result caching
        no_color: Disable ANSI color output
        verbose: Enable detailed debug log output
    """
    result = execute_tool(
        action=action,
        location_id=location_id,
        latitude=latitude,
        longitude=longitude,
        label=label,
        notes=notes,
        mode=mode,
        limit=limit,
        file_pattern=file_pattern,
        env_vars=env_var,
        recursive=recursive,
        use_cache=use_cache,
        no_color=no_color,
        verbose=verbose,
    )

    print_human_readable_ui(result, no_color=no_color)
    write_llm_output(result)


# ==============================================================================
# SECTION 8: CLI Argument Parser
# ==============================================================================

def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog='location_manager.py',
        description=f'AIChat Location Manager Tool v{__version__}',
    )
    parser.add_argument(
        '--action',
        choices=['pin', 'review', 'merge'],
        default='review',
        help='Operation to perform (default: review)',
    )
    parser.add_argument(
        '--location-id',
        dest='location_id',
        metavar='ID',
        help='Identifier for a location entry',
    )
    parser.add_argument(
        '--latitude',
        type=float,
        metavar='LAT',
        help='Latitude coordinate for pinning',
    )
    parser.add_argument(
        '--longitude',
        type=float,
        metavar='LON',
        help='Longitude coordinate for pinning',
    )
    parser.add_argument(
        '--label',
        metavar='LABEL',
        help='Custom label or title for the location',
    )
    parser.add_argument(
        '--notes',
        metavar='NOTES',
        help='Additional descriptive notes for the pin',
    )
    parser.add_argument(
        '--mode',
        choices=['summary', 'detailed'],
        default='summary',
        help='Execution mode (default: summary)',
    )
    parser.add_argument(
        '--limit',
        type=int,
        default=None,
        help='Maximum items to process',
    )
    parser.add_argument(
        '--file-pattern',
        dest='file_pattern',
        metavar='PATTERN',
        help='File glob pattern filter (e.g. *.json)',
    )
    parser.add_argument(
        '--env-var',
        action='append',
        dest='env_var',
        metavar='KEY=VALUE',
        help='Custom environment variable (repeatable)',
    )
    parser.add_argument(
        '--recursive',
        action='store_true',
        default=False,
        help='Process data recursively',
    )
    parser.add_argument(
        '--use-cache',
        action='store_true',
        default=False,
        dest='use_cache',
        help='Enable result caching',
    )
    parser.add_argument(
        '--no-color',
        action='store_true',
        default=False,
        dest='no_color',
        help='Disable ANSI color output',
    )
    parser.add_argument(
        '--verbose', '-v',
        action='store_true',
        default=False,
        help='Enable detailed debug logging',
    )
    return parser


if __name__ == '__main__':
    args = _build_parser().parse_args()
    res = execute_tool(
        action=args.action,
        location_id=args.location_id,
        latitude=args.latitude,
        longitude=args.longitude,
        label=args.label,
        notes=args.notes,
        mode=args.mode,
        limit=args.limit,
        file_pattern=args.file_pattern,
        env_vars=args.env_var,
        recursive=args.recursive,
        use_cache=args.use_cache,
        no_color=args.no_color,
        verbose=args.verbose,
    )

    print_human_readable_ui(res, no_color=args.no_color)
    write_llm_output(res)
    sys.exit(res.get('exit_code', EXIT_SUCCESS))
