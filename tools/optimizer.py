#!/usr/bin/env python3
# ==============================================================================
# optimizer.py — Pyrmethus AIChat Tool Template v1.1.0
# argc/aichat compatible · Human-Readable Colorized Outputs
#
# @describe 18. Life Force Optimizer — Monitors battery, storage, CPU, RAM and suggests real-time optimizations, app-killing rituals, and performance enchantments.
#
# @option --target <PATH>                Target storage path to inspect (default: /)
# @option --interval <SECONDS>           Refresh interval in seconds when running in watch daemon mode (default: 5)
# @option --cpu-threshold <PERCENT>      CPU load percentage threshold to flag heavy processes (default: 80.0)
# @flag   --watch                        Run as a continuous monitoring daemon
# @flag   --kill-heavy                   Automatically identify high-resource process PIDs for termination rituals
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


import re
from functools import lru_cache
from typing import TextIO


def _is_tty(file: Optional[TextIO] = None) -> bool:
    """Check if the given file (or stdout) is a TTY."""
    target = file or sys.stdout
    return hasattr(target, "isatty") and target.isatty()


@lru_cache(maxsize=128)
def _strip_ansi(text: str) -> str:
    """Remove ANSI escape sequences from text."""
    ansi_escape = re.compile(r"\x1b\[[0-9;]*[a-zA-Z]")
    return ansi_escape.sub("", text)


def _cprint(
    text: str,
    file: Optional[TextIO] = None,
    no_color: bool = False,
    *,
    strip_ansi: bool = True,
    force_flush: bool = True,
) -> None:
    """Print pre-formatted ANSI text, stripping colors if stdout is not a TTY or --no-color is set.

    Args:
        text: The text to print, may contain ANSI escape sequences.
        file: Output stream (defaults to sys.stdout).
        no_color: Force disable color output.
        strip_ansi: Whether to strip ANSI codes when color is disabled.
        force_flush: Whether to flush the output stream after printing.
    """
    target = file or sys.stdout
    if no_color or not _is_tty(target):
        if strip_ansi:
            text = _strip_ansi(text)
    print(text, file=target, flush=force_flush)


def cprint_success(text: str, **kwargs: Any) -> None:
    """Print green success message."""
    _cprint(f"\x1b[32m{text}\x1b[0m", **kwargs)


def cprint_error(text: str, **kwargs: Any) -> None:
    """Print red error message."""
    _cprint(f"\x1b[31m{text}\x1b[0m", **kwargs)


def cprint_warning(text: str, **kwargs: Any) -> None:
    """Print yellow warning message."""
    _cprint(f"\x1b[33m{text}\x1b[0m", **kwargs)


def cprint_info(text: str, **kwargs: Any) -> None:
    """Print blue info message."""
    _cprint(f"\x1b[34m{text}\x1b[0m", **kwargs)


def cprint_debug(text: str, **kwargs: Any) -> None:
    """Print dim debug message."""
    _cprint(f"\x1b[2m{text}\x1b[0m", **kwargs)


__all__ = [
    "_cprint",
    "_is_tty",
    "_strip_ansi",
    "cprint_debug",
    "cprint_error",
    "cprint_info",
    "cprint_success",
    "cprint_warning",
]

from dataclasses import dataclass, field
from typing import Callable

try:
    from typing import Self
except ImportError:
    from typing_extensions import Self


def _is_tty(stream: TextIO | None = None) -> bool:
    """Check if the given stream (default stdout) is a TTY."""
    stream = stream or sys.stdout
    return hasattr(stream, "isatty") and stream.isatty()


def _supports_color(stream: TextIO | None = None) -> bool:
    """Check if the terminal supports color output."""
    if not _is_tty(stream):
        return False
    if os.environ.get("TERM") == "dumb":
        return False
    if os.environ.get("NO_COLOR"):
        return False
    if sys.platform == "win32":
        return os.environ.get("ANSICON") is not None or "WT_SESSION" in os.environ
    return True


class _Ansi:
    """ANSI escape code constants with safe fallbacks."""

    RESET = "\033[0m"
    BOLD = "\033[1m"
    DIM = "\033[2m"

    RED = "\033[31m"
    GREEN = "\033[32m"
    YELLOW = "\033[33m"
    BLUE = "\033[34m"
    MAGENTA = "\033[35m"
    CYAN = "\033[36m"
    WHITE = "\033[37m"

    BRIGHT_RED = "\033[91m"
    BRIGHT_GREEN = "\033[92m"
    BRIGHT_YELLOW = "\033[93m"
    BRIGHT_BLUE = "\033[94m"
    BRIGHT_MAGENTA = "\033[95m"
    BRIGHT_CYAN = "\033[96m"
    BRIGHT_WHITE = "\033[97m"

    @classmethod
    def disable(cls) -> None:
        """Disable all ANSI codes (for NO_COLOR or non-TTY)."""
        for attr in dir(cls):
            if not attr.startswith("_") and attr.isupper():
                setattr(cls, attr, "")


NEON_PURPLE = _Ansi.BRIGHT_MAGENTA
NEON_PINK = _Ansi.MAGENTA
NEON_CYAN = _Ansi.BRIGHT_CYAN
NEON_YELLOW = _Ansi.BRIGHT_YELLOW
NEON_GREEN = _Ansi.BRIGHT_GREEN
NEON_RED = _Ansi.BRIGHT_RED
RESET = _Ansi.RESET
BOLD = _Ansi.BOLD
DIM = _Ansi.DIM


def _cprint(text: str, stream: TextIO | None = None) -> None:
    """Print colored text to stream (default stdout) without extra newline handling."""
    stream = stream or sys.stdout
    stream.write(text + "\n")
    stream.flush()


def _progress_bar(
    value: float,
    max_value: float = 100.0,
    width: int = 20,
    color_fn: Callable[[float], str] | None = None,
) -> str:
    """Generate a colored progress bar string."""
    if max_value <= 0:
        return " " * width
    pct = max(0.0, min(1.0, value / max_value))
    filled = int(pct * width)
    empty = width - filled
    if color_fn:
        color = color_fn(pct)
        return f"{color}{'█' * filled}{_Ansi.RESET}{'░' * empty}"
    return f"{_Ansi.BRIGHT_GREEN}{'█' * filled}{_Ansi.RESET}{'░' * empty}"


def _color_for_pct(pct: float) -> str:
    """Return ANSI color code based on percentage (green->yellow->red)."""
    if pct < 0.5:
        return _Ansi.BRIGHT_GREEN
    if pct < 0.8:
        return _Ansi.BRIGHT_YELLOW
    return _Ansi.BRIGHT_RED


@dataclass(slots=True)
class OptimizerData:
    """Structured data container for optimizer UI rendering."""

    success: bool = False
    cpu_load_percent: float = 0.0
    ram_usage_percent: float = 0.0
    storage_used_percent: float = 0.0
    battery_level: str | float = "N/A"
    duration_ms: int = 0
    heavy_processes: list[dict[str, Any]] = field(default_factory=list)
    optimization_rituals: list[str] = field(default_factory=list)
    timestamp: float = field(default_factory=time.time)
    metadata: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Self:
        """Create instance from arbitrary dict, ignoring unknown keys."""
        known = {f.name for f in cls.__dataclass_fields__.values()}
        filtered = {k: v for k, v in data.items() if k in known}
        return cls(**filtered)

    def to_dict(self) -> dict[str, Any]:
        """Serialize to dictionary."""
        return {
            "success": self.success,
            "cpu_load_percent": self.cpu_load_percent,
            "ram_usage_percent": self.ram_usage_percent,
            "storage_used_percent": self.storage_used_percent,
            "battery_level": self.battery_level,
            "duration_ms": self.duration_ms,
            "heavy_processes": self.heavy_processes,
            "optimization_rituals": self.optimization_rituals,
            "timestamp": self.timestamp,
            "metadata": self.metadata,
        }

    def to_json(self, indent: int | None = 2) -> str:
        """Serialize to JSON string."""
        return json.dumps(self.to_dict(), indent=indent, default=str)


@dataclass(slots=True)
class UiConfig:
    """Configuration for UI rendering."""

    box_width: int = 64
    show_progress_bars: bool = True
    max_processes: int = 5
    max_rituals: int = 10
    stream: TextIO = sys.stdout
    use_color: bool = True
    timestamp_format: str = "%H:%M:%S"

    def __post_init__(self) -> None:
        self.box_width = max(40, min(120, self.box_width))
        if not self.use_color or not _supports_color(self.stream):
            _Ansi.disable()


def print_human_readable_ui(
    data: dict[str, Any] | OptimizerData,
    no_color: bool = False,
    config: UiConfig | None = None,
) -> None:
    """
    Render a human-friendly, colorized box UI for terminal users.
    Only executes if running in an interactive TTY (unless forced via config).
    """
    cfg = config or UiConfig(use_color=not no_color)
    stream = cfg.stream

    if not _is_tty(stream) and cfg.use_color:
        return

    if isinstance(data, dict):
        opt_data = OptimizerData.from_dict(data)
    else:
        opt_data = data

    status_color = NEON_GREEN if opt_data.success else NEON_RED
    status_symbol = "✓" if opt_data.success else "✗"
    status_text = "HEALTHY" if opt_data.success else "DEGRADED"

    bw = cfg.box_width
    inner_w = bw - 2
    border = "─" * bw

    def print_line(content: str = "") -> None:
        _cprint(
            f"{NEON_PURPLE}│{RESET} {content:<{inner_w}}{NEON_PURPLE}│{RESET}", stream
        )

    def print_border(
        top: bool = False, bottom: bool = False, middle: bool = False
    ) -> None:
        if top:
            _cprint(f"{NEON_PURPLE}╭{border}╮{RESET}", stream)
        elif bottom:
            _cprint(f"{NEON_PURPLE}╰{border}╯{RESET}", stream)
        elif middle:
            _cprint(f"{NEON_PURPLE}├{border}┤{RESET}", stream)

    print_border(top=True)
    title = f"{NEON_PINK}⚡ [18. LIFE FORCE OPTIMIZER]{RESET} {status_color}{BOLD}{status_symbol} {status_text}{RESET}"
    print_line(title)
    print_border(middle=True)

    cpu = opt_data.cpu_load_percent
    ram = opt_data.ram_usage_percent
    storage = opt_data.storage_used_percent
    battery = opt_data.battery_level

    if cfg.show_progress_bars:
        cpu_bar = _progress_bar(cpu, color_fn=_color_for_pct)
        ram_bar = _progress_bar(ram, color_fn=_color_for_pct)
        storage_bar = _progress_bar(storage, color_fn=_color_for_pct)
        print_line(
            f"{NEON_CYAN}CPU Load:{RESET}       {NEON_YELLOW}{cpu:>5.1f}%{RESET} {cpu_bar}"
        )
        print_line(
            f"{NEON_CYAN}RAM Usage:{RESET}      {NEON_YELLOW}{ram:>5.1f}%{RESET} {ram_bar}"
        )
        print_line(
            f"{NEON_CYAN}Storage Used:{RESET}   {NEON_YELLOW}{storage:>5.1f}%{RESET} {storage_bar}"
        )
    else:
        print_line(f"{NEON_CYAN}CPU Load:{RESET}       {NEON_YELLOW}{cpu:.1f}%{RESET}")
        print_line(f"{NEON_CYAN}RAM Usage:{RESET}      {NEON_YELLOW}{ram:.1f}%{RESET}")
        print_line(
            f"{NEON_CYAN}Storage Used:{RESET}   {NEON_YELLOW}{storage:.1f}%{RESET}"
        )

    bat_str = f"{battery}%" if isinstance(battery, (int, float)) else str(battery)
    print_line(f"{NEON_CYAN}Battery Level:{RESET}  {NEON_GREEN}{bat_str}{RESET}")

    ts = time.strftime(cfg.timestamp_format, time.localtime(opt_data.timestamp))
    print_line(f"{NEON_CYAN}Timestamp:{RESET}      {DIM}{ts}{RESET}")
    print_line(
        f"{NEON_CYAN}Duration:{RESET}       {DIM}{opt_data.duration_ms}ms{RESET}"
    )

    heavy_procs = opt_data.heavy_processes[: cfg.max_processes]
    if heavy_procs:
        print_border(middle=True)
        print_line(f"{BOLD}High-Resource Process Targets ({len(heavy_procs)}):{RESET}")
        for proc in heavy_procs:
            pid = proc.get("pid", "?")
            name = proc.get("name", "unknown")
            cpu_p = proc.get("cpu", 0)
            mem_p = proc.get("mem", 0)
            print_line(
                f"   {NEON_RED}› PID {pid}{RESET} ({name}): {cpu_p}% CPU, {mem_p}% MEM"
            )

    rituals = opt_data.optimization_rituals[: cfg.max_rituals]
    if rituals:
        print_border(middle=True)
        print_line(f"{BOLD}Performance Enchantments & Rituals:{RESET}")
        for ritual in rituals:
            print_line(f"   {NEON_CYAN}🔮{RESET} {ritual}")

    if opt_data.metadata:
        print_border(middle=True)
        print_line(f"{BOLD}Metadata:{RESET}")
        for k, v in opt_data.metadata.items():
            print_line(f"   {NEON_CYAN}{k}:{RESET} {v}")

    print_border(bottom=True)


def print_json_output(
    data: dict[str, Any] | OptimizerData, stream: TextIO | None = None
) -> None:
    """Print structured JSON output for machine consumption."""
    stream = stream or sys.stdout
    if isinstance(data, OptimizerData):
        stream.write(data.to_json() + "\n")
    else:
        stream.write(json.dumps(data, indent=2, default=str) + "\n")
    stream.flush()


def create_optimizer_data(
    success: bool,
    cpu: float,
    ram: float,
    storage: float,
    battery: str | float = "N/A",
    duration_ms: int = 0,
    heavy_processes: list[dict[str, Any]] | None = None,
    optimization_rituals: list[str] | None = None,
    **metadata: Any,
) -> OptimizerData:
    """Factory function to create OptimizerData with sensible defaults."""
    return OptimizerData(
        success=success,
        cpu_load_percent=cpu,
        ram_usage_percent=ram,
        storage_used_percent=storage,
        battery_level=battery,
        duration_ms=duration_ms,
        heavy_processes=heavy_processes or [],
        optimization_rituals=optimization_rituals or [],
        metadata=metadata,
    )


# ==============================================================================
# SECTION 2: Core Logic Implementation
# ==============================================================================
import platform
import time
from dataclasses import dataclass
from functools import lru_cache
from typing import Dict, Tuple

try:
    import psutil

    PSUTIL_AVAILABLE = True
except ImportError:
    PSUTIL_AVAILABLE = False


@dataclass
class SystemStats:
    cpu_percent: float
    ram_percent: float
    cpu_count: int
    ram_total_gb: float
    ram_available_gb: float
    load_avg_1m: float
    load_avg_5m: float
    load_avg_15m: float


_CACHE_TTL = 0.5
_last_cache_time: float = 0.0
_cached_stats: Optional[SystemStats] = None


def _get_cpu_and_ram() -> Tuple[float, float]:
    """Retrieve CPU load percentage and RAM usage percentage on Linux / Termux."""
    cpu_pct, ram_pct = 0.0, 0.0

    # Read CPU load average
    try:
        if Path("/proc/loadavg").exists():
            with open("/proc/loadavg") as f:
                parts = f.read().split()
                if len(parts) >= 3:
                    load1 = float(parts[0])
                    load5 = float(parts[1])
                    load15 = float(parts[2])
                    cores = os.cpu_count() or 1
                    cpu_pct = round(min(100.0, (load1 / cores) * 100), 1)
    except (OSError, ValueError, IndexError):
        pass

    # Read RAM usage from /proc/meminfo
    try:
        if Path("/proc/meminfo").exists():
            mem_data: Dict[str, int] = {}
            with open("/proc/meminfo") as f:
                for line in f:
                    parts = line.split(":")
                    if len(parts) == 2:
                        key = parts[0].strip()
                        val_str = parts[1].strip().split()[0]
                        try:
                            mem_data[key] = int(val_str)
                        except ValueError:
                            continue

            total = mem_data.get("MemTotal", 1)
            free = mem_data.get("MemAvailable", mem_data.get("MemFree", 0))
            ram_pct = round(((total - free) / total) * 100, 1)
    except (OSError, ValueError, ZeroDivisionError):
        pass

    return cpu_pct, ram_pct


def _get_cpu_and_ram_cross_platform() -> Tuple[float, float]:
    """Retrieve CPU and RAM usage with cross-platform support."""
    system = platform.system().lower()

    if system == "linux" or "termux" in platform.platform().lower():
        return _get_cpu_and_ram()

    if PSUTIL_AVAILABLE:
        try:
            cpu_pct = psutil.cpu_percent(interval=0.1)
            mem = psutil.virtual_memory()
            ram_pct = mem.percent
            return round(cpu_pct, 1), round(ram_pct, 1)
        except Exception:
            pass

    return 0.0, 0.0


def get_system_stats(use_cache: bool = True) -> SystemStats:
    """Get detailed system statistics with optional caching."""
    global _last_cache_time, _cached_stats

    current_time = time.time()
    if use_cache and _cached_stats and (current_time - _last_cache_time) < _CACHE_TTL:
        return _cached_stats

    cpu_pct, ram_pct = _get_cpu_and_ram_cross_platform()
    cpu_count = os.cpu_count() or 1

    load_avg_1m = load_avg_5m = load_avg_15m = 0.0
    ram_total_gb = ram_available_gb = 0.0

    try:
        if Path("/proc/loadavg").exists():
            with open("/proc/loadavg") as f:
                parts = f.read().split()
                if len(parts) >= 3:
                    load_avg_1m = float(parts[0])
                    load_avg_5m = float(parts[1])
                    load_avg_15m = float(parts[2])
    except (OSError, ValueError, IndexError):
        pass

    try:
        if PSUTIL_AVAILABLE:
            mem = psutil.virtual_memory()
            ram_total_gb = round(mem.total / (1024**3), 2)
            ram_available_gb = round(mem.available / (1024**3), 2)
        elif Path("/proc/meminfo").exists():
            mem_data: Dict[str, int] = {}
            with open("/proc/meminfo") as f:
                for line in f:
                    parts = line.split(":")
                    if len(parts) == 2:
                        key = parts[0].strip()
                        val_str = parts[1].strip().split()[0]
                        try:
                            mem_data[key] = int(val_str)
                        except ValueError:
                            continue
            total_kb = mem_data.get("MemTotal", 0)
            avail_kb = mem_data.get("MemAvailable", mem_data.get("MemFree", 0))
            ram_total_gb = round(total_kb / (1024**2), 2)
            ram_available_gb = round(avail_kb / (1024**2), 2)
    except Exception:
        pass

    _cached_stats = SystemStats(
        cpu_percent=cpu_pct,
        ram_percent=ram_pct,
        cpu_count=cpu_count,
        ram_total_gb=ram_total_gb,
        ram_available_gb=ram_available_gb,
        load_avg_1m=load_avg_1m,
        load_avg_5m=load_avg_5m,
        load_avg_15m=load_avg_15m,
    )
    _last_cache_time = current_time
    return _cached_stats


def is_system_under_load(
    cpu_threshold: float = 80.0, ram_threshold: float = 85.0
) -> bool:
    """Check if system is under high load."""
    stats = get_system_stats()
    return stats.cpu_percent >= cpu_threshold or stats.ram_percent >= ram_threshold


def get_optimal_worker_count(
    reserve_cpu_pct: float = 10.0, reserve_ram_pct: float = 15.0
) -> int:
    """Calculate optimal worker count based on current system load."""
    stats = get_system_stats()
    cpu_available = max(0, 100 - stats.cpu_percent - reserve_cpu_pct)
    ram_available = max(0, 100 - stats.ram_percent - reserve_ram_pct)

    cpu_workers = max(1, int(stats.cpu_count * (cpu_available / 100)))
    ram_workers = max(1, int(stats.cpu_count * (ram_available / 100)))

    return min(cpu_workers, ram_workers, stats.cpu_count)


class SystemMonitor:
    """Context manager for continuous system monitoring."""

    def __init__(self, interval: float = 1.0):
        self.interval = interval
        self._running = False
        self._stats_history: list[SystemStats] = []

    def __enter__(self) -> SystemMonitor:
        self._running = True
        return self

    def __exit__(self, exc_type, exc_val, exc_tb) -> None:
        self._running = False

    def sample(self) -> SystemStats:
        """Take a single sample."""
        stats = get_system_stats(use_cache=False)
        self._stats_history.append(stats)
        return stats

    def get_average(self, last_n: int = 10) -> Optional[SystemStats]:
        """Get average stats over last N samples."""
        if not self._stats_history:
            return None
        recent = self._stats_history[-last_n:]
        n = len(recent)
        return SystemStats(
            cpu_percent=sum(s.cpu_percent for s in recent) / n,
            ram_percent=sum(s.ram_percent for s in recent) / n,
            cpu_count=recent[-1].cpu_count,
            ram_total_gb=recent[-1].ram_total_gb,
            ram_available_gb=sum(s.ram_available_gb for s in recent) / n,
            load_avg_1m=sum(s.load_avg_1m for s in recent) / n,
            load_avg_5m=sum(s.load_avg_5m for s in recent) / n,
            load_avg_15m=sum(s.load_avg_15m for s in recent) / n,
        )


import time
from typing import List

_BATTERY_CACHE: Dict[str, Tuple[float, str]] = {}
_CACHE_TTL = 2.0


def _get_battery_status() -> str:
    """Retrieve battery percentage for Termux or Linux environments."""
    # Check Termux API
    if shutil.which("termux-battery-status"):
        try:
            res = subprocess.check_output(["termux-battery-status"], timeout=2)
            bdata = json.loads(res.decode())
            percentage = bdata.get("percentage")
            plugged = bdata.get("plugged", "UNPLUGGED")
            status = "Charging" if plugged != "UNPLUGGED" else "Discharging"
            if percentage is not None:
                return f"{percentage}% ({status})"
        except Exception:
            pass

    # Check Linux /sys/class/power_supply
    try:
        p_path = Path("/sys/class/power_supply")
        if p_path.exists():
            batteries = _get_linux_batteries(p_path)
            if batteries:
                total_cap = sum(b["capacity"] for b in batteries)
                avg_cap = total_cap // len(batteries)
                charging = any(b["status"] == "Charging" for b in batteries)
                status = "Charging" if charging else "Discharging"
                return f"{avg_cap}% ({status})"
    except Exception:
        pass

    return "100% (AC)"


def _get_linux_batteries(p_path: Path) -> List[Dict[str, object]]:
    """Parse all battery supplies under /sys/class/power_supply."""
    batteries = []
    for supply in p_path.iterdir():
        if not supply.is_dir():
            continue
        cap_file = supply / "capacity"
        status_file = supply / "status"
        type_file = supply / "type"
        if not cap_file.exists():
            continue
        try:
            cap_text = cap_file.read_text().strip()
            capacity = int(cap_text)
        except (ValueError, OSError):
            continue
        status = "Unknown"
        if status_file.exists():
            try:
                status = status_file.read_text().strip()
            except OSError:
                pass
        batt_type = "Unknown"
        if type_file.exists():
            try:
                batt_type = type_file.read_text().strip()
            except OSError:
                pass
        if batt_type.lower() == "battery" or "BAT" in supply.name.upper():
            batteries.append(
                {"capacity": capacity, "status": status, "name": supply.name}
            )
    return batteries


def get_all_battery_info() -> List[Dict[str, object]]:
    """Return detailed info for all detected batteries (Linux only)."""
    p_path = Path("/sys/class/power_supply")
    if not p_path.exists():
        return []
    return _get_linux_batteries(p_path)


def clear_battery_cache() -> None:
    """Clear the internal battery status cache."""
    _BATTERY_CACHE.clear()


def _get_cached_battery_status() -> str:
    """Return cached battery status if fresh, otherwise refresh."""
    now = time.monotonic()
    cached = _BATTERY_CACHE.get("default")
    if cached and (now - cached[0]) < _CACHE_TTL:
        return cached[1]
    status = _get_battery_status()
    _BATTERY_CACHE["default"] = (now, status)
    return status


import sys

if sys.version_info >= (3, 9):
    pass
else:
    pass


def _get_heavy_processes(
    cpu_threshold: float,
    mem_threshold: float = 15.0,
    timeout: float = 5.0,
    filter_names: Optional[List[str]] = None,
) -> List[Dict[str, Any]]:
    """Scan process table for high CPU or RAM consuming processes.

    Args:
        cpu_threshold: Minimum CPU percentage to include.
        mem_threshold: Minimum memory percentage to include.
        timeout: Subprocess timeout in seconds.
        filter_names: Optional list of process name substrings to include.

    Returns:
        List of process dicts sorted by CPU descending.
    """
    heavy: List[Dict[str, Any]] = []

    if _has_psutil():
        heavy = _get_heavy_processes_psutil(cpu_threshold, mem_threshold, filter_names)
    elif shutil.which("ps"):
        heavy = _get_heavy_processes_ps(
            cpu_threshold, mem_threshold, timeout, filter_names
        )

    return sorted(heavy, key=lambda x: x["cpu"], reverse=True)


def _has_psutil() -> bool:
    try:
        import psutil  # noqa: F401

        return True
    except ImportError:
        return False


def _get_heavy_processes_psutil(
    cpu_threshold: float,
    mem_threshold: float,
    filter_names: Optional[List[str]] = None,
) -> List[Dict[str, Any]]:
    import psutil

    heavy: List[Dict[str, Any]] = []
    for proc in psutil.process_iter(["pid", "cpu_percent", "memory_percent", "name"]):
        try:
            info = proc.info
            cpu_f = info.get("cpu_percent") or 0.0
            mem_f = info.get("memory_percent") or 0.0
            name = info.get("name") or ""
            pid = info.get("pid")

            if pid is None:
                continue

            if cpu_f < cpu_threshold and mem_f < mem_threshold:
                continue

            if filter_names and not any(
                f.lower() in name.lower() for f in filter_names
            ):
                continue

            heavy.append({"pid": pid, "cpu": cpu_f, "mem": mem_f, "name": name})
        except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.ZombieProcess):
            continue

    return heavy


def _get_heavy_processes_ps(
    cpu_threshold: float,
    mem_threshold: float,
    timeout: float,
    filter_names: Optional[List[str]] = None,
) -> List[Dict[str, Any]]:
    heavy: List[Dict[str, Any]] = []
    try:
        out = subprocess.check_output(
            ["ps", "-eo", "pid,%cpu,%mem,comm"],
            text=True,
            timeout=timeout,
            stderr=subprocess.DEVNULL,
        )
        lines = out.strip().splitlines()
        if not lines:
            return heavy

        header = lines[0].lower()
        pid_idx = header.find("pid")
        cpu_idx = header.find("%cpu")
        mem_idx = header.find("%mem")
        comm_idx = header.find("comm")

        for line in lines[1:]:
            if not line.strip():
                continue
            parts = line.split(maxsplit=3)
            if len(parts) != 4:
                continue
            pid_str, cpu_str, mem_str, comm = parts
            try:
                cpu_f = float(cpu_str)
                mem_f = float(mem_str)
                if cpu_f < cpu_threshold and mem_f < mem_threshold:
                    continue
                if filter_names and not any(
                    f.lower() in comm.lower() for f in filter_names
                ):
                    continue
                heavy.append(
                    {
                        "pid": int(pid_str),
                        "cpu": cpu_f,
                        "mem": mem_f,
                        "name": comm.strip(),
                    }
                )
            except ValueError:
                continue
    except (subprocess.SubprocessError, subprocess.TimeoutExpired, OSError):
        pass

    return heavy


def get_process_details(pid: int) -> Optional[Dict[str, Any]]:
    """Return detailed info for a specific PID using psutil if available."""
    if _has_psutil():
        import psutil

        try:
            proc = psutil.Process(pid)
            return {
                "pid": pid,
                "name": proc.name(),
                "exe": proc.exe(),
                "cmdline": proc.cmdline(),
                "cpu_percent": proc.cpu_percent(interval=0.1),
                "memory_percent": proc.memory_percent(),
                "memory_info": proc.memory_info()._asdict(),
                "status": proc.status(),
                "create_time": proc.create_time(),
                "num_threads": proc.num_threads(),
                "username": proc.username(),
            }
        except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.ZombieProcess):
            return None
    return None


def kill_process_tree(pid: int, sig: int = 15, timeout: float = 3.0) -> bool:
    """Kill a process and its children. Returns True if terminated."""
    if not _has_psutil():
        return False
    import psutil

    try:
        parent = psutil.Process(pid)
        children = parent.children(recursive=True)
        for child in children:
            child.send_signal(sig)
        parent.send_signal(sig)
        gone, alive = psutil.wait_procs(children + [parent], timeout=timeout)
        for p in alive:
            p.kill()
        return len(alive) == 0
    except (psutil.NoSuchProcess, psutil.AccessDenied):
        return False


_process_cache: Dict[str, Any] = {}
_cache_ttl: float = 2.0


def get_heavy_processes_cached(
    cpu_threshold: float,
    mem_threshold: float = 15.0,
    ttl: float = 2.0,
) -> List[Dict[str, Any]]:
    """Cached wrapper around _get_heavy_processes."""
    import time

    global _process_cache, _cache_ttl
    _cache_ttl = ttl
    key = f"{cpu_threshold}:{mem_threshold}"
    now = time.time()
    if key in _process_cache:
        cached, timestamp = _process_cache[key]
        if now - timestamp < ttl:
            return cached
    result = _get_heavy_processes(cpu_threshold, mem_threshold)
    _process_cache[key] = (result, now)
    return result


def execute_tool(
    target: str = "/",
    interval: int = 5,
    cpu_threshold: float = 80.0,
    watch: bool = False,
    kill_heavy: bool = False,
    no_color: bool = False,
    verbose: bool = False,
) -> dict[str, Any]:
    """
    Core execution logic for system resource monitoring and performance optimization.
    """
    start_time = time.perf_counter()
    target_path = Path(target).expanduser().resolve()

    if not target_path.exists():
        target_path = Path("/")

    try:
        # Storage usage check
        disk_usage = shutil.disk_usage(target_path)
        storage_pct = round((disk_usage.used / disk_usage.total) * 100, 1)

        cpu_load, ram_usage = _check_cpu_and_ram = _get_cpu_and_ram()
        battery_status = _get_battery_status()
        heavy_procs = _get_heavy_processes(cpu_threshold)

        # Generate optimization rituals
        rituals = []
        if ram_usage > 75.0:
            rituals.append(
                "Memory Reclaim Ritual: Purge cache using 'sync && sysctl -w vm.drop_caches=3' or restart heavy daemons."
            )
        if storage_pct > 85.0:
            rituals.append(
                "Storage Liberation Ritual: Clean package cache ('apt clean' or 'pkg clean') & remove stale temp files."
            )
        if cpu_load > cpu_threshold:
            rituals.append(
                f"CPU Banishing Ritual: High CPU detected ({cpu_load}%). Consider terminating top PID targets."
            )
        if "Discharging" in battery_status:
            rituals.append(
                "Life Force Preservation: Battery discharging. Lower background refresh intervals & turn off debug logs."
            )

        if not rituals:
            rituals.append(
                "Harmonic Resonance Achieved: All system life metrics are operating at optimal levels."
            )

        duration_ms = round((time.perf_counter() - start_time) * 1000, 2)

        return {
            "success": True,
            "target": str(target_path),
            "cpu_load_percent": cpu_load,
            "ram_usage_percent": ram_usage,
            "storage_used_percent": storage_pct,
            "battery_level": battery_status,
            "heavy_processes": heavy_procs,
            "optimization_rituals": rituals,
            "duration_ms": duration_ms,
            "exit_code": 0,
        }

    except Exception as exc:
        return {
            "success": False,
            "error": f"Life Force Optimizer execution failed: {exc}",
            "exit_code": 1,
        }


# ==============================================================================
# SECTION 3: Output Routing (LLM vs Human Terminal)
# ==============================================================================

import sys
import tempfile


def write_llm_output(data: dict[str, Any]) -> None:
    """Format and write clean JSON output to LLM_OUTPUT destination."""
    out_path = os.environ.get("LLM_OUTPUT", "/dev/stdout")
    json_payload = json.dumps(data, indent=2, ensure_ascii=False, sort_keys=True) + "\n"

    direct_targets = {"/dev/stdout", "/dev/fd/1", "-", "/dev/stderr", "/dev/fd/2"}
    if out_path in direct_targets:
        _write_to_stream(json_payload, out_path)
        return

    try:
        parent = Path(out_path).parent
        if parent and not parent.exists():
            parent.mkdir(parents=True, exist_ok=True)
        _atomic_write(out_path, json_payload)
    except OSError as err:
        sys.stderr.write(f"Failed writing to LLM_OUTPUT '{out_path}': {err}\n")
        _write_to_stream(json_payload, "/dev/stdout")


def _write_to_stream(payload: str, target: str) -> None:
    """Write payload to stdout or stderr based on target."""
    stream: TextIO = (
        sys.stdout if target in {"/dev/stdout", "/dev/fd/1", "-"} else sys.stderr
    )
    stream.write(payload)
    stream.flush()


def _atomic_write(filepath: str, content: str) -> None:
    """Write content atomically using a temporary file and rename."""
    path = Path(filepath)
    with tempfile.NamedTemporaryFile(
        mode="w",
        encoding="utf-8",
        dir=path.parent,
        prefix=f".{path.name}.",
        suffix=".tmp",
        delete=False,
    ) as tmp:
        tmp.write(content)
        tmp_path = Path(tmp.name)
    try:
        tmp_path.replace(path)
    except OSError:
        tmp_path.unlink(missing_ok=True)
        raise


def validate_llm_output(data: Any) -> dict[str, Any]:
    """Validate and coerce input to a JSON-serializable dict."""
    if isinstance(data, dict):
        return data
    if hasattr(data, "model_dump"):
        return data.model_dump()
    if hasattr(data, "dict"):
        return data.dict()
    if hasattr(data, "__dict__"):
        return vars(data)
    raise TypeError(f"Expected dict-like object, got {type(data).__name__}")


def write_llm_output_safe(data: Any) -> None:
    """Entry point for AIChat: validate then write LLM output."""
    validated = validate_llm_output(data)
    write_llm_output(validated)


# ==============================================================================
# SECTION 4: Function Entry Point for AIChat
# ==============================================================================

__all__ = ["validate_llm_output", "write_llm_output", "write_llm_output_safe"]


def run(
    target: str = "/",
    interval: int = 5,
    cpu_threshold: float = 80.0,
    watch: bool = False,
    kill_heavy: bool = False,
    no_color: bool = False,
    verbose: bool = False,
) -> None:
    """
    AIChat Programmatic Entrypoint.
    Parameter names match option/flag slugs (with underscores).
    """
    if watch:
        try:
            while True:
                if _is_tty():
                    os.system("clear" if os.name == "posix" else "cls")
                result = execute_tool(
                    target=target,
                    interval=interval,
                    cpu_threshold=cpu_threshold,
                    watch=True,
                    kill_heavy=kill_heavy,
                    no_color=no_color,
                    verbose=verbose,
                )
                print_human_readable_ui(result, no_color=no_color)
                write_llm_output(result)
                time.sleep(interval)
        except KeyboardInterrupt:
            sys.exit(0)
    else:
        result = execute_tool(
            target=target,
            interval=interval,
            cpu_threshold=cpu_threshold,
            watch=False,
            kill_heavy=kill_heavy,
            no_color=no_color,
            verbose=verbose,
        )
        print_human_readable_ui(result, no_color=no_color)
        write_llm_output(result)


# ==============================================================================
# SECTION 5: CLI Argument Parser
# ==============================================================================

import sys
from typing import Sequence

__version__ = "18.0.0"


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="optimizer.py",
        description=f"AIChat 18. Life Force Optimizer v{__version__}",
        epilog=(
            "Examples:\n"
            "  %(prog)s --target /home --watch --interval 10\n"
            "  %(prog)s -t /var/log --cpu-threshold 90 --kill-heavy\n"
            "  %(prog)s --no-color --verbose"
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )

    general = parser.add_argument_group("General Options")
    general.add_argument(
        "--target",
        "-t",
        default="/",
        metavar="PATH",
        help="Target storage path to inspect (default: /)",
    )
    general.add_argument(
        "--version",
        "-V",
        action="version",
        version=f"%(prog)s {__version__}",
        help="Show program version and exit",
    )

    monitoring = parser.add_argument_group("Monitoring Options")
    monitoring.add_argument(
        "--interval",
        "-i",
        type=_positive_int,
        default=5,
        metavar="SECONDS",
        help="Refresh interval in seconds when running in watch daemon mode (default: 5)",
    )
    monitoring.add_argument(
        "--cpu-threshold",
        type=_cpu_threshold,
        default=80.0,
        dest="cpu_threshold",
        metavar="PERCENT",
        help="CPU load percentage threshold to flag heavy processes (default: 80.0)",
    )
    monitoring.add_argument(
        "--watch",
        "-w",
        action="store_true",
        default=False,
        help="Run as a continuous monitoring daemon",
    )

    safety = parser.add_argument_group("Safety Options")
    safety.add_argument(
        "--kill-heavy",
        action="store_true",
        default=False,
        dest="kill_heavy",
        help="Identify high-resource process PIDs for termination rituals",
    )
    safety.add_argument(
        "--dry-run",
        action="store_true",
        default=False,
        dest="dry_run",
        help="Simulate actions without making changes (implies --no-kill)",
    )

    output = parser.add_argument_group("Output Options")
    output.add_argument(
        "--no-color",
        action="store_true",
        default=False,
        dest="no_color",
        help="Disable ANSI color output",
    )
    output.add_argument(
        "--verbose",
        "-v",
        action="store_true",
        default=False,
        help="Enable detailed debug logging",
    )
    output.add_argument(
        "--json",
        action="store_true",
        default=False,
        help="Emit machine-readable JSON output",
    )

    return parser


def _positive_int(value: str) -> int:
    ivalue = int(value)
    if ivalue <= 0:
        raise argparse.ArgumentTypeError(f"{value} is not a positive integer")
    return ivalue


def _cpu_threshold(value: str) -> float:
    fvalue = float(value)
    if not (0.0 <= fvalue <= 100.0):
        raise argparse.ArgumentTypeError(f"{value} must be between 0.0 and 100.0")
    return fvalue


def _validate_target(path_str: str) -> Path:
    path = Path(path_str).expanduser().resolve()
    if not path.exists():
        raise argparse.ArgumentTypeError(f"Target path does not exist: {path}")
    if not path.is_dir():
        raise argparse.ArgumentTypeError(f"Target path is not a directory: {path}")
    return path


def parse_args(argv: Optional[Sequence[str]] = None) -> argparse.Namespace:
    parser = _build_parser()
    args = parser.parse_args(argv)

    # Post-process validation
    args.target = _validate_target(args.target)

    # Safety logic: dry-run disables kill-heavy
    if args.dry_run:
        args.kill_heavy = False

    return args


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = parse_args(argv)

    # Placeholder for actual optimizer logic
    print(f"Target: {args.target}")
    print(f"Watch mode: {args.watch}")
    print(f"Interval: {args.interval}s")
    print(f"CPU threshold: {args.cpu_threshold}%")
    print(f"Kill heavy: {args.kill_heavy}")
    print(f"Dry run: {args.dry_run}")
    print(f"Color: {not args.no_color}")
    print(f"Verbose: {args.verbose}")
    print(f"JSON output: {args.json}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
if __name__ == "__main__":
    args = _build_parser().parse_args()

    if args.watch:
        try:
            while True:
                if _is_tty():
                    os.system("clear" if os.name == "posix" else "cls")
                res = execute_tool(
                    target=args.target,
                    interval=args.interval,
                    cpu_threshold=args.cpu_threshold,
                    watch=True,
                    kill_heavy=args.kill_heavy,
                    no_color=args.no_color,
                    verbose=args.verbose,
                )
                print_human_readable_ui(res, no_color=args.no_color)
                write_llm_output(res)
                time.sleep(args.interval)
        except KeyboardInterrupt:
            sys.exit(0)
    else:
        res = execute_tool(
            target=args.target,
            interval=args.interval,
            cpu_threshold=args.cpu_threshold,
            watch=False,
            kill_heavy=args.kill_heavy,
            no_color=args.no_color,
            verbose=args.verbose,
        )
        print_human_readable_ui(res, no_color=args.no_color)
        write_llm_output(res)
        sys.exit(res.get("exit_code", 0))
