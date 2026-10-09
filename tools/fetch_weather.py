#!/usr/bin/env python3
# ==============================================================================
# weather.py — AIChat Enhanced Current Weather & Forecast Tool v3.0.0
# argc/aichat compatible · Human-Readable Colorized Outputs · Native Caching
#
# @describe Fetch current weather, multi-day forecasts, and hourly outlooks for any location or auto-detected IP.
#
# @meta require-tools aichat
#
# @option --location <LOCATION>          City name, coordinates, or 'auto' (default: auto)
# @option --units <UNITS>                Unit system: metric or imperial (default: metric)
# @option --days <NUM>                   Forecast days: 1 to 7 (default: 1)
# @option --mode <MODE>                  Execution mode: summary, detailed, hourly (default: summary)
# @flag   --use-cache                    Enable result caching (15-minute TTL)
# @flag   --no-color                     Disable ANSI color output
# @flag   --verbose                      Enable detailed debug log output
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
import platform
import re
import signal
import socket
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone
from enum import Enum
from pathlib import Path
from typing import Any, Dict, List, Literal, Optional, Tuple, Union

__version__ = "3.0.0"
__all__ = [
    "run",
    "execute_tool",
    "ToolCache",
    "ToolError",
    "get_agent_var",
    "get_builtin_var",
    "get_execution_context",
    "__version__",
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


class UnitSystem(str, Enum):
    METRIC = "metric"
    IMPERIAL = "imperial"


class ExecutionMode(str, Enum):
    SUMMARY = "summary"
    DETAILED = "detailed"
    HOURLY = "hourly"


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
    """Zero-crash custom JSON encoder supporting standard and dynamic Python objects."""

    def default(self, obj: Any) -> Any:
        if isinstance(obj, Path):
            return str(obj)
        if isinstance(obj, Enum):
            return obj.value
        if isinstance(obj, (datetime, timedelta)):
            return obj.isoformat() if isinstance(obj, datetime) else obj.total_seconds()
        if isinstance(obj, bytes):
            return obj.decode("utf-8", errors="replace")
        if isinstance(obj, (set, frozenset)):
            return list(obj)
        if isinstance(obj, ToolError):
            return obj.to_dict()
        if isinstance(obj, Exception):
            return str(obj)
        try:
            return super().default(obj)
        except TypeError:
            return str(obj)


# ==============================================================================
# SECTION 2: Terminal Color Palette & UI Helpers
# ==============================================================================

NEON_CYAN    = "\033[38;5;51m"
NEON_GREEN   = "\033[38;5;46m"
NEON_RED     = "\033[38;5;196m"
NEON_YELLOW  = "\033[38;5;226m"
NEON_PURPLE  = "\033[38;5;129m"
NEON_PINK    = "\033[38;5;198m"
NEON_ORANGE  = "\033[38;5;208m"
NEON_BLUE    = "\033[38;5;39m"
RESET        = "\033[0m"
BOLD         = "\033[1m"
DIM          = "\033[2m"

_ANSI_RE = re.compile(r"\x1b(?:[@-Z\\-_]|\[[0-?]*[ -/]*[@-~])|\033\[[0-9;?]*[a-zA-Z]")


def _strip_ansi(text: str) -> str:
    """Remove all ANSI escape sequences from text."""
    return _ANSI_RE.sub("", text)


def _is_tty() -> bool:
    """Check interactive TTY state while honoring standard overrides."""
    if os.environ.get("NO_COLOR"):
        return False
    if os.environ.get("FORCE_COLOR"):
        return True
    return sys.stderr.isatty() and os.environ.get("TERM", "").lower() not in ("dumb", "")


def _cprint(text: str, file: Any = None, no_color: bool = False, end: str = "\n") -> None:
    """Print pre-formatted ANSI text, stripping colors if stream is not a TTY or --no-color is set."""
    target = file or sys.stderr
    if no_color or not _is_tty():
        text = _strip_ansi(text)
    print(text, file=target, flush=True, end=end)


def _uv_risk_badge(uv_index: Optional[float]) -> str:
    """Return colorized UV risk level."""
    if uv_index is None:
        return "N/A"
    if uv_index <= 2:
        return f"{NEON_GREEN}{uv_index:.1f} (Low){RESET}"
    elif uv_index <= 5:
        return f"{NEON_YELLOW}{uv_index:.1f} (Moderate){RESET}"
    elif uv_index <= 7:
        return f"{NEON_ORANGE}{uv_index:.1f} (High){RESET}"
    elif uv_index <= 10:
        return f"{NEON_RED}{uv_index:.1f} (Very High){RESET}"
    return f"{NEON_PURPLE}{uv_index:.1f} (Extreme){RESET}"


def _deg_to_cardinal(deg: Optional[float]) -> str:
    """Convert meteorological wind direction in degrees to cardinal notation."""
    if deg is None:
        return "N/A"
    cardinals = [
        "N", "NNE", "NE", "ENE", "E", "ESE", "SE", "SSE",
        "S", "SSW", "SW", "WSW", "W", "WNW", "NW", "NNW"
    ]
    idx = int((deg + 11.25) / 22.5) % 16
    return cardinals[idx]


def print_human_readable_ui(data: dict[str, Any], no_color: bool = False) -> None:
    """Render a colorized box UI for terminal users to stderr."""
    if not _is_tty() or no_color:
        return

    success = data.get("success", False)
    box_w = 68
    border = "─" * box_w

    if not success:
        _cprint(f"{NEON_RED}╭{border}╮{RESET}")
        _cprint(f"{NEON_RED}│{RESET} {NEON_RED}{BOLD}✗ WEATHER OPERATION FAILED{RESET}")
        _cprint(f"{NEON_RED}├{border}┤{RESET}")
        _cprint(f"{NEON_RED}│{RESET} {NEON_RED}Error:{RESET} {data.get('error', 'Unknown error')}")
        _cprint(f"{NEON_RED}╰{border}╯{RESET}")
        return

    loc = data.get("location", {})
    curr = data.get("current", {})
    forecast = data.get("daily_forecast", [])
    today_fc = forecast[0] if forecast else {}
    u_temp = data.get("units", {}).get("temperature", "°C")
    u_wind = data.get("units", {}).get("wind_speed", "km/h")
    u_precip = data.get("units", {}).get("precipitation", "mm")
    u_press = data.get("units", {}).get("pressure", "hPa")

    location_title = loc.get("display_name") or f"{loc.get('name', 'Unknown')}, {loc.get('country', '')}"

    _cprint(f"{NEON_PURPLE}╭{border}╮{RESET}")
    _cprint(f"{NEON_PURPLE}│{RESET} {NEON_PINK}⚡ [WEATHER REPORT v{__version__}]{RESET} {NEON_GREEN}{BOLD}✓ LIVE{RESET}")
    _cprint(f"{NEON_PURPLE}├{border}┤{RESET}")
    _cprint(f"{NEON_PURPLE}│{RESET} {NEON_CYAN}Location:{RESET}       {BOLD}{location_title}{RESET}")
    _cprint(f"{NEON_PURPLE}│{RESET} {NEON_CYAN}Coordinates:{RESET}    {loc.get('latitude')}, {loc.get('longitude')} ({loc.get('timezone')})")
    _cprint(f"{NEON_PURPLE}│{RESET} {NEON_CYAN}Current Sky:{RESET}    {curr.get('condition_emoji', '')} {BOLD}{curr.get('condition', 'N/A')}{RESET}")
    _cprint(f"{NEON_PURPLE}│{RESET} {NEON_CYAN}Temperature:{RESET}    {NEON_YELLOW}{curr.get('temperature')}{u_temp}{RESET} (Feels like: {curr.get('apparent_temperature')}{u_temp})")
    _cprint(f"{NEON_PURPLE}│{RESET} {NEON_CYAN}Humidity/Clouds:{RESET} {curr.get('relative_humidity')}% RH  ·  {curr.get('cloud_cover')}% Clouds")
    _cprint(f"{NEON_PURPLE}│{RESET} {NEON_CYAN}Wind & Gusts:{RESET}   {curr.get('wind_speed')} {u_wind} {curr.get('wind_cardinal')} (Gusts: {curr.get('wind_gusts', 'N/A')} {u_wind})")
    _cprint(f"{NEON_PURPLE}│{RESET} {NEON_CYAN}Pressure:{RESET}       {curr.get('surface_pressure')} {u_press}")
    _cprint(f"{NEON_PURPLE}├{border}┤{RESET}")
    _cprint(f"{NEON_PURPLE}│{RESET} {BOLD}Today's Outlook ({today_fc.get('date', 'Today')}):{RESET}")
    _cprint(f"{NEON_PURPLE}│{RESET}   {NEON_CYAN}›{RESET} High / Low:      {NEON_RED}{today_fc.get('temp_max')}{u_temp}{RESET} / {NEON_BLUE}{today_fc.get('temp_min')}{u_temp}{RESET}")
    _cprint(f"{NEON_PURPLE}│{RESET}   {NEON_CYAN}›{RESET} Condition:       {today_fc.get('condition_emoji', '')} {today_fc.get('condition', 'N/A')}")
    _cprint(f"{NEON_PURPLE}│{RESET}   {NEON_CYAN}›{RESET} Rain Probability:{NEON_YELLOW} {today_fc.get('precipitation_probability')}%{RESET} ({today_fc.get('precipitation_sum')} {u_precip})")
    _cprint(f"{NEON_PURPLE}│{RESET}   {NEON_CYAN}›{RESET} Peak UV Index:   {_uv_risk_badge(today_fc.get('uv_index_max'))}")
    _cprint(f"{NEON_PURPLE}│{RESET}   {NEON_CYAN}›{RESET} Daylight Hours:  {today_fc.get('daylight_duration_hours', 'N/A')}h  (☀️ {today_fc.get('sunrise', 'N/A')} ➜ 🌙 {today_fc.get('sunset', 'N/A')})")

    # Multi-day forecast preview if requested
    if len(forecast) > 1:
        _cprint(f"{NEON_PURPLE}├{border}┤{RESET}")
        _cprint(f"{NEON_PURPLE}│{RESET} {BOLD}{len(forecast)}-Day Outlook Summary:{RESET}")
        for day in forecast[1:]:
            d_str = day.get('date', '')
            emoji = day.get('condition_emoji', '')
            cond = day.get('condition', '')[:14]
            hi = f"{day.get('temp_max')}{u_temp}"
            lo = f"{day.get('temp_min')}{u_temp}"
            rain = f"{day.get('precipitation_probability')}%"
            _cprint(f"{NEON_PURPLE}│{RESET}   {NEON_CYAN}›{RESET} {d_str}: {emoji} {cond:<15} {NEON_RED}{hi:>6}{RESET} / {NEON_BLUE}{lo:>6}{RESET} · Rain: {rain:>4}")

    # Hourly mini-trend table (Next 8 slots)
    hourly = data.get("hourly_forecast", [])
    if hourly and data.get("mode") in ("hourly", "detailed"):
        _cprint(f"{NEON_PURPLE}├{border}┤{RESET}")
        _cprint(f"{NEON_PURPLE}│{RESET} {BOLD}Next 8 Hours Breakdown:{RESET}")
        slots = hourly[:8]
        times_row = " ".join(f"{s.get('time', '')[-5:]:>6}" for s in slots)
        temps_row = " ".join(f"{s.get('temperature'):>5}{u_temp}" for s in slots)
        icons_row = " ".join(f"{s.get('condition_emoji', ' '):>6}" for s in slots)
        _cprint(f"{NEON_PURPLE}│{RESET}   Time: {DIM}{times_row}{RESET}")
        _cprint(f"{NEON_PURPLE}│{RESET}   Icon: {icons_row}")
        _cprint(f"{NEON_PURPLE}│{RESET}   Temp: {NEON_YELLOW}{temps_row}{RESET}")

    _cprint(f"{NEON_PURPLE}├{border}┤{RESET}")
    _cprint(f"{NEON_PURPLE}│{RESET} {NEON_CYAN}Cached:{RESET} {NEON_YELLOW}{data.get('cached', False)}{RESET}  {NEON_CYAN}Duration:{RESET} {DIM}{data.get('duration_ms', 0)}ms{RESET}")
    _cprint(f"{NEON_PURPLE}╰{border}╯{RESET}")


# ==============================================================================
# SECTION 3: Agent & Environment Helpers
# ==============================================================================

def get_agent_var(name: str, default: str = "") -> str:
    """Access agent user-defined variables (LLM_AGENT_VAR_<NAME>)."""
    return os.environ.get(f"LLM_AGENT_VAR_{name.upper()}", default)


def get_builtin_var(name: str) -> Optional[str]:
    """Access agent built-in environment variables (e.g., __cwd__, __os__)."""
    return os.environ.get(f"LLM_AGENT_VAR_{name}")


def get_execution_context() -> dict[str, Any]:
    """Extract complete execution context from the tool and runtime environment."""
    termux_prefix = os.environ.get("PREFIX", "")
    return {
        "tool_name": os.environ.get("LLM_TOOL_NAME", "weather"),
        "cache_dir": os.environ.get("LLM_TOOL_CACHE_DIR"),
        "root_dir": os.environ.get("LLM_ROOT_DIR"),
        "output_path": os.environ.get("LLM_OUTPUT"),
        "cwd": get_builtin_var("__cwd__") or os.getcwd(),
        "python_version": sys.version.split()[0],
        "platform": platform.system().lower(),
        "is_termux": "com.termux" in termux_prefix or Path("/data/data/com.termux").exists(),
    }


# ==============================================================================
# SECTION 4: Native Caching & Signal Handlers
# ==============================================================================

class ToolCache:
    """Resilient file-backed caching utility with TTL support."""

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

    def get(self, key_data: str, ttl_seconds: int = 900) -> Optional[Any]:
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
            cache_file.unlink(missing_ok=True)
            return None

    def set(self, key_data: str, value: Any) -> None:
        cache_file = self.cache_dir / f"{self._make_key(key_data)}.cache"
        tmp_file = cache_file.with_suffix(f".tmp.{os.getpid()}")
        try:
            with open(tmp_file, "wb") as fp:
                pickle.dump(value, fp)
            tmp_file.replace(cache_file)
        except Exception:
            if tmp_file.exists():
                tmp_file.unlink(missing_ok=True)


class GracefulShutdown:
    """Signal handler for graceful execution cancellation."""

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
# SECTION 5: Core Weather Logic & Geocoding
# ==============================================================================

WMO_WEATHER_CODES: dict[int, tuple[str, str]] = {
    0: ("Clear sky", "☀️"),
    1: ("Mainly clear", "🌤️"),
    2: ("Partly cloudy", "⛅"),
    3: ("Overcast", "☁️"),
    45: ("Foggy", "🌫️"),
    48: ("Depositing rime fog", "🌫️"),
    51: ("Light drizzle", "🌦️"),
    53: ("Moderate drizzle", "🌦️"),
    55: ("Dense drizzle", "🌧️"),
    56: ("Light freezing drizzle", "🌧️❄️"),
    57: ("Dense freezing drizzle", "🌧️❄️"),
    61: ("Slight rain", "🌦️"),
    63: ("Moderate rain", "🌧️"),
    65: ("Heavy rain", "🌧️"),
    66: ("Light freezing rain", "🌧️❄️"),
    67: ("Heavy freezing rain", "🌧️❄️"),
    71: ("Slight snowfall", "🌨️"),
    73: ("Moderate snowfall", "🌨️"),
    75: ("Heavy snowfall", "❄️"),
    77: ("Snow grains", "❄️"),
    80: ("Slight rain showers", "🌦️"),
    81: ("Moderate rain showers", "🌧️"),
    82: ("Violent rain showers", "⛈️"),
    85: ("Slight snow showers", "🌨️"),
    86: ("Heavy snow showers", "❄️"),
    95: ("Thunderstorm", "⚡"),
    96: ("Thunderstorm with slight hail", "⛈️"),
    97: ("Thunderstorm with heavy hail", "⛈️"),
}


def _interpret_wmo(code: Optional[int]) -> tuple[str, str]:
    if code is None:
        return ("Unknown", "❓")
    return WMO_WEATHER_CODES.get(code, ("Variable conditions", "🌡️"))


def _http_get_json(url: str, timeout: int = 10, retries: int = 2) -> dict[str, Any]:
    """Perform robust HTTP GET request with retries and timeout protection."""
    headers = {
        "User-Agent": f"AIChatWeatherTool/{__version__} (compatible; Python urllib)",
        "Accept": "application/json",
    }
    req = urllib.request.Request(url, headers=headers)
    last_err: Optional[Exception] = None

    for attempt in range(retries + 1):
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                return json.loads(resp.read().decode("utf-8"))
        except (urllib.error.URLError, socket.timeout) as err:
            last_err = err
            if isinstance(err, socket.timeout) or (isinstance(err, urllib.error.URLError) and "timed out" in str(err)):
                if attempt == retries:
                    raise ToolError(f"HTTP request timed out after {timeout}s: {url}", exit_code=EXIT_TIMEOUT)
            if attempt < retries:
                time.sleep(0.5 * (attempt + 1))
        except Exception as err:
            raise ToolError(f"HTTP request failed: {err}", exit_code=EXIT_ERROR)

    raise ToolError(f"Network request failed after {retries + 1} attempts: {last_err}", exit_code=EXIT_ERROR)


def resolve_ip_location() -> dict[str, Any]:
    """Auto-detect geographic location based on public IP address."""
    providers = [
        "https://ipapi.co/json/",
        "https://freeipapi.com/api/json",
    ]
    for url in providers:
        try:
            data = _http_get_json(url, timeout=5, retries=1)
            lat = data.get("latitude") or data.get("lat")
            lon = data.get("longitude") or data.get("lon")
            if lat is not None and lon is not None:
                city = data.get("city") or data.get("cityName") or "Unknown"
                country = data.get("country_name") or data.get("countryName") or data.get("country") or ""
                region = data.get("region") or data.get("regionName") or ""
                return {
                    "name": city,
                    "display_name": f"{city}, {region} ({country})",
                    "latitude": float(lat),
                    "longitude": float(lon),
                    "country": country,
                    "timezone": data.get("timezone", "auto"),
                }
        except Exception:
            continue

    raise ToolError(
        "Auto-location detection via IP failed. Please specify an explicit --location argument.",
        exit_code=EXIT_INVALID_INPUT,
    )


def geocode_location(location_name: str) -> dict[str, Any]:
    """Resolve location name, coordinates, or 'auto' token to coordinates."""
    loc_clean = location_name.strip()

    if not loc_clean or loc_clean.lower() in ("auto", "current", "here", "ip", "default"):
        # Check agent-provided location variable first
        agent_loc = get_agent_var("LOCATION")
        if agent_loc and agent_loc.lower() not in ("auto", "current"):
            return geocode_location(agent_loc)
        return resolve_ip_location()

    # Direct coordinates matching (e.g., "37.7749,-122.4194" or "37.7749, -122.4194")
    coord_match = re.match(r"^([+-]?\d+(?:\.\d+)?)\s*,\s*([+-]?\d+(?:\.\d+)?)$", loc_clean)
    if coord_match:
        lat, lon = float(coord_match.group(1)), float(coord_match.group(2))
        return {
            "name": f"{lat:.4f}, {lon:.4f}",
            "display_name": f"Coordinates: {lat:.4f}, {lon:.4f}",
            "latitude": lat,
            "longitude": lon,
            "country": "Custom Coordinates",
            "timezone": "auto",
        }

    encoded_query = urllib.parse.quote(loc_clean)
    geo_url = f"https://geocoding-api.open-meteo.com/v1/search?name={encoded_query}&count=1&language=en&format=json"
    
    geo_data = _http_get_json(geo_url, timeout=8)
    results = geo_data.get("results")
    if not results:
        raise ToolError(f"Location not found: '{location_name}'", exit_code=EXIT_INVALID_INPUT)

    best = results[0]
    city = best.get("name", loc_clean)
    admin1 = best.get("admin1", "")
    country = best.get("country", "")
    parts = [p for p in (city, admin1, country) if p]
    display = ", ".join(parts)

    return {
        "name": city,
        "display_name": display,
        "latitude": best.get("latitude"),
        "longitude": best.get("longitude"),
        "country": country,
        "admin1": admin1,
        "timezone": best.get("timezone", "auto"),
    }


def fetch_weather_data(
    lat: float,
    lon: float,
    tz: str,
    days: int = 1,
    units: str = "metric",
) -> dict[str, Any]:
    """Fetch live weather metrics, hourly forecast, and multi-day outlooks from Open-Meteo."""
    is_imperial = units.lower() == "imperial"
    temp_unit = "fahrenheit" if is_imperial else "celsius"
    wind_unit = "mph" if is_imperial else "kmh"
    precip_unit = "inch" if is_imperial else "mm"

    params = {
        "latitude": f"{lat:.6f}",
        "longitude": f"{lon:.6f}",
        "current": (
            "temperature_2m,relative_humidity_2m,apparent_temperature,is_day,"
            "precipitation,weather_code,surface_pressure,wind_speed_10m,"
            "wind_direction_10m,wind_gusts_10m,cloud_cover"
        ),
        "hourly": "temperature_2m,relative_humidity_2m,precipitation_probability,weather_code,wind_speed_10m",
        "daily": (
            "weather_code,temperature_2m_max,temperature_2m_min,apparent_temperature_max,"
            "apparent_temperature_min,precipitation_sum,precipitation_probability_max,"
            "uv_index_max,sunrise,sunset,daylight_duration,wind_speed_10m_max"
        ),
        "temperature_unit": temp_unit,
        "wind_speed_unit": wind_unit,
        "precipitation_unit": precip_unit,
        "timezone": tz,
        "forecast_days": max(1, min(7, days)),
    }

    query_str = urllib.parse.urlencode(params)
    weather_url = f"https://api.open-meteo.com/v1/forecast?{query_str}"
    return _http_get_json(weather_url, timeout=10)


def execute_tool(
    location: Optional[str] = "auto",
    units: str = "metric",
    days: int = 1,
    mode: str = "summary",
    use_cache: bool = False,
    no_color: bool = False,
    verbose: bool = False,
) -> dict[str, Any]:
    """Main execution entry point shared across CLI and direct invocation."""
    start_time = time.monotonic()

    if verbose:
        logging.basicConfig(level=logging.DEBUG, format="[DEBUG] %(message)s")
        logging.debug(f"Starting weather inquiry: loc='{location}', units='{units}', days={days}, mode='{mode}'")

    # Reconcile unit configuration with agent preferences if available
    agent_units = get_agent_var("UNITS")
    if agent_units and units == "metric":
        units = agent_units

    units_norm = "imperial" if units.lower() in ("imperial", "f", "fahrenheit", "us", "imp") else "metric"
    target_loc = location or "auto"
    days_val = max(1, min(7, int(days) if days else 1))

    cache = ToolCache()
    cache_key = f"weather_v3:{target_loc.strip().lower()}:{units_norm}:{days_val}:{mode}"

    if use_cache:
        cached_res = cache.get(cache_key, ttl_seconds=900)  # 15-minute TTL
        if cached_res is not None:
            if verbose:
                logging.debug("Cache hit! Returning cached weather report.")
            cached_res["cached"] = True
            return cached_res

    shutdown = GracefulShutdown()

    try:
        if shutdown.should_stop():
            return {
                "success": False,
                "error": "Execution cancelled by user signal.",
                "exit_code": EXIT_INTERRUPTED,
                "duration_ms": round((time.monotonic() - start_time) * 1000, 2),
            }

        geo = geocode_location(target_loc)
        raw = fetch_weather_data(geo["latitude"], geo["longitude"], geo["timezone"], days=days_val, units=units_norm)

        curr = raw.get("current", {})
        daily = raw.get("daily", {})
        hourly = raw.get("hourly", {})

        curr_code = curr.get("weather_code")
        curr_desc, curr_emoji = _interpret_wmo(curr_code)
        wind_dir = curr.get("wind_direction_10m")

        current_summary = {
            "time": curr.get("time"),
            "temperature": curr.get("temperature_2m"),
            "apparent_temperature": curr.get("apparent_temperature"),
            "relative_humidity": curr.get("relative_humidity_2m"),
            "cloud_cover": curr.get("cloud_cover"),
            "is_day": bool(curr.get("is_day", 1)),
            "precipitation": curr.get("precipitation"),
            "surface_pressure": curr.get("surface_pressure"),
            "weather_code": curr_code,
            "condition": curr_desc,
            "condition_emoji": curr_emoji,
            "wind_speed": curr.get("wind_speed_10m"),
            "wind_gusts": curr.get("wind_gusts_10m"),
            "wind_direction": wind_dir,
            "wind_cardinal": _deg_to_cardinal(wind_dir),
        }

        # Parse daily forecasts
        daily_forecasts: list[dict[str, Any]] = []
        d_times = daily.get("time", [])
        for i in range(len(d_times)):
            f_code = daily.get("weather_code", [])[i] if i < len(daily.get("weather_code", [])) else None
            f_desc, f_emoji = _interpret_wmo(f_code)
            daylight_sec = daily.get("daylight_duration", [])[i] if i < len(daily.get("daylight_duration", [])) else 0
            daylight_hrs = round(daylight_sec / 3600.0, 1) if daylight_sec else None

            daily_forecasts.append({
                "date": d_times[i],
                "weather_code": f_code,
                "condition": f_desc,
                "condition_emoji": f_emoji,
                "temp_max": daily.get("temperature_2m_max", [])[i] if i < len(daily.get("temperature_2m_max", [])) else None,
                "temp_min": daily.get("temperature_2m_min", [])[i] if i < len(daily.get("temperature_2m_min", [])) else None,
                "apparent_temp_max": daily.get("apparent_temperature_max", [])[i] if i < len(daily.get("apparent_temperature_max", [])) else None,
                "apparent_temp_min": daily.get("apparent_temperature_min", [])[i] if i < len(daily.get("apparent_temperature_min", [])) else None,
                "precipitation_sum": daily.get("precipitation_sum", [])[i] if i < len(daily.get("precipitation_sum", [])) else None,
                "precipitation_probability": daily.get("precipitation_probability_max", [])[i] if i < len(daily.get("precipitation_probability_max", [])) else None,
                "uv_index_max": daily.get("uv_index_max", [])[i] if i < len(daily.get("uv_index_max", [])) else None,
                "wind_speed_max": daily.get("wind_speed_10m_max", [])[i] if i < len(daily.get("wind_speed_10m_max", [])) else None,
                "sunrise": daily.get("sunrise", [])[i] if i < len(daily.get("sunrise", [])) else None,
                "sunset": daily.get("sunset", [])[i] if i < len(daily.get("sunset", [])) else None,
                "daylight_duration_hours": daylight_hrs,
            })

        # Parse next 24 hours hourly sequence
        hourly_forecasts: list[dict[str, Any]] = []
        h_times = hourly.get("time", [])
        current_iso = curr.get("time", "")

        # Find closest hourly start index
        start_idx = 0
        if current_iso in h_times:
            start_idx = h_times.index(current_iso)

        for j in range(start_idx, min(start_idx + 24, len(h_times))):
            h_code = hourly.get("weather_code", [])[j] if j < len(hourly.get("weather_code", [])) else None
            h_desc, h_emoji = _interpret_wmo(h_code)
            hourly_forecasts.append({
                "time": h_times[j],
                "temperature": hourly.get("temperature_2m", [])[j] if j < len(hourly.get("temperature_2m", [])) else None,
                "relative_humidity": hourly.get("relative_humidity_2m", [])[j] if j < len(hourly.get("relative_humidity_2m", [])) else None,
                "precipitation_probability": hourly.get("precipitation_probability", [])[j] if j < len(hourly.get("precipitation_probability", [])) else None,
                "weather_code": h_code,
                "condition": h_desc,
                "condition_emoji": h_emoji,
                "wind_speed": hourly.get("wind_speed_10m", [])[j] if j < len(hourly.get("wind_speed_10m", [])) else None,
            })

        units_spec = {
            "temperature": "°F" if units_norm == "imperial" else "°C",
            "wind_speed": "mph" if units_norm == "imperial" else "km/h",
            "precipitation": "in" if units_norm == "imperial" else "mm",
            "pressure": "hPa",
        }

        duration_ms = round((time.monotonic() - start_time) * 1000, 2)

        result: dict[str, Any] = {
            "success": True,
            "query": target_loc,
            "mode": mode,
            "location": geo,
            "units": units_spec,
            "current": current_summary,
            "daily_forecast": daily_forecasts,
            "hourly_forecast": hourly_forecasts,
            "context": get_execution_context(),
            "cached": False,
            "duration_ms": duration_ms,
            "exit_code": EXIT_SUCCESS,
        }

        if mode == "detailed":
            result["raw_response"] = raw

        if use_cache:
            cache.set(cache_key, result)

        return result

    except ToolError as exc:
        res = exc.to_dict()
        res["duration_ms"] = round((time.monotonic() - start_time) * 1000, 2)
        return res
    except Exception as exc:
        return {
            "success": False,
            "error": f"Unexpected weather tool error: {exc}",
            "exit_code": EXIT_ERROR,
            "duration_ms": round((time.monotonic() - start_time) * 1000, 2),
        }
    finally:
        shutdown.restore()


# ==============================================================================
# SECTION 6: Output Routing (LLM vs Human Terminal)
# ==============================================================================

def write_llm_output(data: dict[str, Any]) -> None:
    """Format and write clean JSON output to LLM_OUTPUT destination safely."""
    out_path = os.environ.get("LLM_OUTPUT", "/dev/stdout")
    json_payload = json.dumps(data, indent=2, ensure_ascii=False, cls=ToolJSONEncoder) + "\n"

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
# SECTION 7: Function Entry Point for AIChat
# ==============================================================================

def run(
    location: Optional[str] = "auto",
    units: Literal["metric", "imperial"] = "metric",
    days: int = 1,
    mode: Literal["summary", "detailed", "hourly"] = "summary",
    use_cache: bool = False,
    no_color: bool = False,
    verbose: bool = False,
) -> None:
    """Execute the weather tool with specified parameters.

    Args:
        location: City name, coordinates, or 'auto' (default: auto)
        units: Unit system: metric or imperial (default: metric)
        days: Forecast days from 1 to 7 (default: 1)
        mode: Execution mode: summary, detailed, or hourly (default: summary)
        use_cache: Enable 15-minute result caching
        no_color: Disable ANSI color output
        verbose: Enable detailed debug logging
    """
    result = execute_tool(
        location=location,
        units=units,
        days=days,
        mode=mode,
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
        prog="weather.py",
        description=f"AIChat Enhanced Weather & Forecast Tool v{__version__}",
    )
    parser.add_argument(
        "--location", "-l",
        default="auto",
        metavar="LOCATION",
        help="City, coordinates (lat,lon), or 'auto' (default: auto)",
    )
    parser.add_argument(
        "--units", "-u",
        choices=["metric", "imperial"],
        default="metric",
        help="Unit system (default: metric)",
    )
    parser.add_argument(
        "--days", "-d",
        type=int,
        default=1,
        help="Forecast days: 1 to 7 (default: 1)",
    )
    parser.add_argument(
        "--mode", "-m",
        choices=["summary", "detailed", "hourly"],
        default="summary",
        help="Execution mode (default: summary)",
    )
    parser.add_argument(
        "--use-cache",
        action="store_true",
        default=False,
        dest="use_cache",
        help="Enable 15-minute result caching",
    )
    parser.add_argument(
        "--no-color",
        action="store_true",
        default=False,
        dest="no_color",
        help="Disable ANSI color output",
    )
    parser.add_argument(
        "--verbose", "-v",
        action="store_true",
        default=False,
        help="Enable detailed debug logging",
    )
    return parser


if __name__ == "__main__":
    args = _build_parser().parse_args()
    res = execute_tool(
        location=args.location,
        units=args.units,
        days=args.days,
        mode=args.mode,
        use_cache=args.use_cache,
        no_color=args.no_color,
        verbose=args.verbose,
    )

    print_human_readable_ui(res, no_color=args.no_color)
    write_llm_output(res)
    sys.exit(res.get("exit_code", EXIT_SUCCESS))
