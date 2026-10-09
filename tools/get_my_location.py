#!/usr/bin/env python3
# ==============================================================================
# get_my_location.py — Location & Weather Monitor v2.0.0
# Structured command-line integration with Termux APIs and Open-Meteo
# ==============================================================================

# @describe Query location coordinates, reverse address lookup, weather, and altitude.
#
# @option --provider              Location provider: gps, network, cell (default: gps)
# @option --fallbacks             Comma-separated fallback providers in order of execution (default: network)
# @option --unit                  Temperature unit: celsius, fahrenheit (default: celsius)
# @option --speed-unit            Speed representation unit: ms, kmh, mph (default: ms)
# @option --alt-unit              Altitude representation unit: meters, feet (default: meters)
# @option --precision             Decimal places to round coordinates (default: 6)
# @option --user-agent            HTTP User-Agent header for API requests
# @option --timeout               Subprocess and connection timeouts in seconds (default: 15)
# @option --output                Write output report directly to this file
# @option --lat                   Coordinate latitude override (mocks GPS location query)
# @option --lon                   Coordinate longitude override (mocks GPS location query)
# @option --loop-delay            Seconds to wait between queries in continuous monitoring mode
# @flag   --json                  Output result formatted as a raw JSON payload
# @flag   --open                  Automatically open the Google Maps link in browser
# @flag   --share                 Share output report text via termux-share command
# @flag   --verbose               Enable verbose logging outputs to stderr
# @flag   --dry-run               Simulate geocoding/weather calls with dummy coordinates
#

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import time
import urllib.request
from datetime import datetime
from typing import Any, Dict, Optional, Tuple

WEATHER_CODES = {
    0: "Sunny/Clear",
    1: "Mainly Clear",
    2: "Partly Cloudy",
    3: "Overcast",
    45: "Foggy",
    48: "Depositing Rime Fog",
    51: "Light Drizzle",
    53: "Moderate Drizzle",
    55: "Dense Drizzle",
    56: "Light Freezing Drizzle",
    57: "Dense Freezing Drizzle",
    61: "Slight Rain",
    63: "Moderate Rain",
    65: "Heavy Rain",
    66: "Light Freezing Rain",
    67: "Heavy Freezing Rain",
    71: "Slight Snowfall",
    73: "Moderate Snowfall",
    75: "Heavy Snowfall",
    77: "Snow Grains",
    80: "Slight Rain Showers",
    81: "Moderate Rain Showers",
    82: "Violent Rain Showers",
    85: "Slight Snow Showers",
    86: "Heavy Snow Showers",
    95: "Thunderstorm",
    96: "Thunderstorm with Slight Hail",
    99: "Thunderstorm with Heavy Hail",
}

_verbose: bool = False


def _debug(msg: str) -> None:
    if _verbose:
        print(f"[DEBUG] {msg}", file=sys.stderr)


def _info(msg: str) -> None:
    print(f"[INFO] {msg}", file=sys.stderr)


def _warn(msg: str) -> None:
    print(f"[WARNING] {msg}", file=sys.stderr)


def _validate_sandbox(path: Path) -> bool:
    home = Path.home().resolve()
    tmp = Path("/tmp").resolve()
    try:
        resolved = path.resolve()
        s = str(resolved)
        return s.startswith(str(home)) or s.startswith(str(tmp))
    except OSError:
        return False


def _get_location_raw(provider: str = "gps", timeout: int = 15) -> dict:
    if not shutil.which("termux-location"):
        raise RuntimeError(
            "termux-location command not found on PATH. Make sure Termux:API is installed."
        )
    cmd = ["termux-location", "-p", provider, "-r", "once"]
    _debug(f"Executing: {' '.join(cmd)}")
    result = subprocess.check_output(cmd, text=True, timeout=timeout)
    return json.loads(result)


def _get_coordinates(
    provider: str,
    fallbacks: list[str],
    timeout: int,
) -> Tuple[Optional[float], Optional[float], Dict[str, Any]]:
    # Attempt Primary Provider
    try:
        data = _get_location_raw(provider, timeout)
        lat, lon = data.get("latitude"), data.get("longitude")
        if lat is not None and lon is not None:
            return lat, lon, data
    except Exception as e:
        _warn(f"Failed to query primary provider '{provider}': {e}")

    # Fallback Providers
    for fb in fallbacks:
        fb = fb.strip()
        if not fb:
            continue
        try:
            _info(f"Trying fallback provider '{fb}'...")
            data = _get_location_raw(fb, timeout)
            lat, lon = data.get("latitude"), data.get("longitude")
            if lat is not None and lon is not None:
                return lat, lon, data
        except Exception as e:
            _warn(f"Fallback provider '{fb}' query failed: {e}")

    return None, None, {}


def _reverse_geocode(
    lat: float,
    lon: float,
    user_agent: Optional[str] = None,
    timeout: int = 10,
) -> Tuple[str, Dict[str, Any]]:
    try:
        url = f"https://nominatim.openstreetmap.org/reverse?format=json&lat={lat}&lon={lon}&addressdetails=1"
        ua = user_agent or "TermuxLocationScript/5.0"
        req = urllib.request.Request(url, headers={"User-Agent": ua})

        with urllib.request.urlopen(req, timeout=timeout) as response:
            geo_data = json.loads(response.read().decode("utf-8"))
            addr = geo_data.get("address", {})
            if addr:
                house_number = addr.get("house_number", "")
                road = addr.get("road", addr.get("pedestrian", ""))
                city = addr.get("city", addr.get("town", addr.get("village", "")))
                state = addr.get("state", "")

                if road:
                    street = f"{house_number} {road}".strip()
                    clean_address = f"{street}, {city}, {state}".strip(", ")
                    return clean_address, geo_data
                else:
                    return geo_data.get("display_name", "Unknown Address"), geo_data
            return "Could not resolve to a known street address.", geo_data
    except Exception as e:
        _warn(f"Address reverse geocode failed: {e}")
        return f"Lookup Failed ({e})", {}


def _get_weather(
    lat: float,
    lon: float,
    unit: str = "celsius",
    user_agent: Optional[str] = None,
    timeout: int = 10,
) -> Tuple[str, Dict[str, Any]]:
    try:
        temp_unit = "fahrenheit" if unit.lower() == "fahrenheit" else "celsius"
        url = f"https://api.open-meteo.com/v1/forecast?latitude={lat}&longitude={lon}&current_weather=true&temperature_unit={temp_unit}"
        ua = user_agent or "TermuxLocationScript/5.0"
        req = urllib.request.Request(url, headers={"User-Agent": ua})

        with urllib.request.urlopen(req, timeout=timeout) as response:
            weather_data = json.loads(response.read().decode("utf-8"))
            current = weather_data.get("current_weather", {})
            if current:
                temp = current.get("temperature")
                wind = current.get("windspeed")
                wcode = current.get("weathercode", 0)
                wtext = WEATHER_CODES.get(wcode, "Unknown Weather")
                t_suffix = "°F" if temp_unit == "fahrenheit" else "°C"
                return f"{temp}{t_suffix}, {wtext} (Wind: {wind} km/h)", weather_data
            return "Could not resolve weather metrics.", {}
    except Exception as e:
        _warn(f"Weather query failed: {e}")
        return f"Weather Lookup Failed ({e})", {}


def query_location_report(
    provider: str = "gps",
    fallbacks: str = "network",
    unit: str = "celsius",
    speed_unit: str = "ms",
    alt_unit: str = "meters",
    precision: int = 6,
    user_agent: Optional[str] = None,
    timeout: int = 15,
    lat_override: Optional[float] = None,
    lon_override: Optional[float] = None,
    dry_run: bool = False,
) -> Dict[str, Any]:
    lat, lon = lat_override, lon_override
    data = {}
    provider_used = "OVERRIDE"

    if dry_run:
        _info("[DRY-RUN] Simulating location queries.")
        lat = lat if lat is not None else 37.7749
        lon = lon if lon is not None else -122.4194
        provider_used = "SIMULATOR"
        data = {"accuracy": 5.0, "altitude": 10.0, "speed": 0.0}
    elif lat is None or lon is None:
        fb_list = [f.strip() for f in fallbacks.split(",") if f.strip()]
        lat, lon, data = _get_coordinates(provider, fb_list, timeout)
        provider_used = data.get("provider", provider).upper()

    if lat is None or lon is None:
        return {"success": False, "error": "Could not obtain valid coordinates."}

    # Format values
    lat_r = round(lat, precision)
    lon_r = round(lon, precision)
    accuracy = data.get("accuracy", "Unknown")
    alt_val = data.get("altitude", 0.0)
    speed_val = data.get("speed", 0.0)

    # Unit conversions
    if alt_unit.lower() == "feet":
        alt_str = f"{round(alt_val * 3.28084, 1)} feet"
    else:
        alt_str = f"{alt_val} meters"

    if speed_unit.lower() == "kmh":
        speed_str = f"{round(speed_val * 3.6, 1)} km/h"
    elif speed_unit.lower() == "mph":
        speed_str = f"{round(speed_val * 2.23694, 1)} mph"
    else:
        speed_str = f"{speed_val} m/s"

    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    # Geocoding & Weather
    address_str, geo_raw = _reverse_geocode(lat, lon, user_agent, timeout)
    weather_str, weather_raw = _get_weather(lat, lon, unit, user_agent, timeout)

    report_data = {
        "success": True,
        "timestamp": timestamp,
        "provider": provider_used,
        "coordinates": {"latitude": lat_r, "longitude": lon_r},
        "accuracy": accuracy,
        "altitude": alt_str,
        "speed": speed_str,
        "address": address_str,
        "weather": weather_str,
        "google_maps": f"https://www.google.com/maps?q={lat_r},{lon_r}",
        "openstreetmap": f"https://www.openstreetmap.org/?mlat={lat_r}&mlon={lon_r}#map=15/{lat_r}/{lon_r}",
        "raw_responses": {
            "location": data,
            "geocoding": geo_raw,
            "weather": weather_raw,
        },
    }
    return report_data


def _format_text_report(report: Dict[str, Any]) -> str:
    if not report.get("success"):
        return f"Error: {report.get('error')}"

    out = f"🕒 Time: {report['timestamp']}\n"
    coords = report["coordinates"]
    out += f"📍 Coordinates: {coords['latitude']}, {coords['longitude']}\n"
    out += f"📡 Provider: {report['provider']} (Accuracy: ~{report['accuracy']}m)\n"
    out += f"⛰️  Altitude: {report['altitude']}\n"
    out += f"🏃 Speed: {report['speed']}\n"
    out += f"🗺️  Google Maps: {report['google_maps']}\n"
    out += f"🗺️  OpenStreetMap: {report['openstreetmap']}\n"
    out += f"🏠 Address: {report['address']}\n"
    out += f"☁️  Weather: {report['weather']}\n"
    return out


def _cli() -> int:
    global _verbose
    p = argparse.ArgumentParser(description="Query coordinates and weather metrics.")
    p.add_argument("--provider", default="gps")
    p.add_argument("--fallbacks", default="network")
    p.add_argument("--unit", choices=("celsius", "fahrenheit"), default="celsius")
    p.add_argument(
        "--speed-unit", choices=("ms", "kmh", "mph"), default="ms", dest="speed_unit"
    )
    p.add_argument(
        "--alt-unit", choices=("meters", "feet"), default="meters", dest="alt_unit"
    )
    p.add_argument("--precision", type=int, default=6)
    p.add_argument("--user-agent", default=None, dest="user_agent")
    p.add_argument("--timeout", type=int, default=15)
    p.add_argument("--output", default=None)
    p.add_argument("--lat", type=float, default=None)
    p.add_argument("--lon", type=float, default=None)
    p.add_argument("--loop-delay", type=int, default=None, dest="loop_delay")
    p.add_argument("--json", action="store_true")
    p.add_argument("--open", action="store_true")
    p.add_argument("--share", action="store_true")
    p.add_argument("--verbose", "-v", action="store_true")
    p.add_argument("--dry-run", action="store_true", dest="dry_run")
    args = p.parse_args()

    _verbose = args.verbose

    def run_once() -> Tuple[Dict[str, Any], str]:
        report = query_location_report(
            provider=args.provider,
            fallbacks=args.fallbacks,
            unit=args.unit,
            speed_unit=args.speed_unit,
            alt_unit=args.alt_unit,
            precision=args.precision,
            user_agent=args.user_agent,
            timeout=args.timeout,
            lat_override=args.lat,
            lon_override=args.lon,
            dry_run=args.dry_run,
        )
        text_rep = _format_text_report(report)
        return report, text_rep

    # Loop Mode
    if args.loop_delay is not None:
        try:
            while True:
                report, text_rep = run_once()
                if args.json:
                    print(json.dumps(report, indent=2))
                else:
                    print(text_rep)
                    print("-" * 40)
                time.sleep(args.loop_delay)
        except KeyboardInterrupt:
            _info("Exiting continuous monitor mode.")
            return 0
    else:
        report, text_rep = run_once()

        # Output handling
        output_str = json.dumps(report, indent=2) if args.json else text_rep

        if args.output:
            out_path = Path(args.output).expanduser().resolve()
            if not _validate_sandbox(out_path):
                print(
                    f"Error: Output destination '{out_path}' lies outside allowed sandbox.",
                    file=sys.stderr,
                )
                return 1
            with open(out_path, "w", encoding="utf-8") as f:
                f.write(output_str + "\n")
        else:
            print(output_str)

        # Open in maps
        if args.open and report.get("success"):
            gmaps_url = report.get("google_maps")
            if gmaps_url and shutil.which("termux-open"):
                subprocess.run(["termux-open", gmaps_url])

        # Share via Termux Share
        if args.share and report.get("success") and shutil.which("termux-share"):
            # Temporary file write to feed into termux-share stdin/command
            with tempfile.NamedTemporaryFile(
                suffix=".txt", delete=False, mode="w", encoding="utf-8"
            ) as tmpf:
                tmpf.write(text_rep)
                tmp_name = tmpf.name
            try:
                subprocess.run(["termux-share", tmp_name], check=True)
            finally:
                try:
                    os.unlink(tmp_name)
                except OSError:
                    pass

        return 0 if report.get("success") else 1


def run(
    provider: str = "gps",
    fallbacks: str = "network",
    unit: str = "celsius",
    speed_unit: str = "ms",
    alt_unit: str = "meters",
    precision: int = 6,
    user_agent: Optional[str] = None,
    timeout: int = 15,
    lat: Optional[float] = None,
    lon: Optional[float] = None,
    dry_run: bool = False,
    json_mode: bool = True,
) -> str:
    """Query coordinates and weather metrics.

    Args:
        provider: Location provider: gps, network, cell (default: gps)
        fallbacks: Comma-separated fallback providers (default: network)
        unit: Temperature unit (celsius/fahrenheit)
        speed_unit: Speed unit (ms/kmh/mph)
        alt_unit: Altitude unit (meters/feet)
        precision: Decimal places to round coords
        user_agent: HTTP User-Agent header
        timeout: Subprocess and connection timeout
        lat: Latitude override
        lon: Longitude override
        dry_run: Simulate calls with dummy coordinates
        json_mode: Return JSON string formatted report
    """
    report = query_location_report(
        provider=provider,
        fallbacks=fallbacks,
        unit=unit,
        speed_unit=speed_unit,
        alt_unit=alt_unit,
        precision=precision,
        user_agent=user_agent,
        timeout=timeout,
        lat_override=lat,
        lon_override=lon,
        dry_run=dry_run,
    )
    if json_mode:
        return json.dumps(report, indent=2)
    return _format_text_report(report)


if __name__ == "__main__":
    sys.exit(_cli())
