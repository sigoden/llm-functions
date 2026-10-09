#!/data/data/com.termux/files/usr/bin/python3
"""
get_my_location.py - Query location coordinates, reverse address lookup, weather, and altitude.
Requires: termux-api, requests, termux-location, termux-telephony-deviceinfo
"""

import argparse
import json
import subprocess
import sys
import time
import urllib.parse
import urllib.request
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Dict, Optional

# Configuration
CONFIG_DIR = Path.home() / ".config" / "get_my_location"
CONFIG_FILE = CONFIG_DIR / "config.json"
CACHE_DIR = Path.home() / ".cache" / "get_my_location"
CACHE_FILE = CACHE_DIR / "location_cache.json"
CACHE_TTL = 300  # 5 minutes

# Default settings
DEFAULT_PROVIDER = "gps"
DEFAULT_FALLBACKS = "network"
DEFAULT_UNIT = "celsius"
DEFAULT_SPEED_UNIT = "ms"
DEFAULT_ALT_UNIT = "meters"
DEFAULT_PRECISION = 6
DEFAULT_TIMEOUT = 15
DEFAULT_USER_AGENT = "Termux-Location/1.0"

# API endpoints
NOMINATIM_URL = "https://nominatim.openstreetmap.org/reverse"
OPENWEATHER_URL = "https://api.openweathermap.org/data/2.5/weather"
OPEN_ELEVATION_URL = "https://api.open-elevation.com/api/v1/lookup"


@dataclass
class LocationData:
    latitude: float
    longitude: float
    altitude: Optional[float] = None
    accuracy: Optional[float] = None
    speed: Optional[float] = None
    bearing: Optional[float] = None
    provider: str = ""
    timestamp: int = 0
    address: Optional[str] = None
    city: Optional[str] = None
    state: Optional[str] = None
    country: Optional[str] = None
    postal_code: Optional[str] = None
    timezone: Optional[str] = None
    temperature: Optional[float] = None
    humidity: Optional[int] = None
    pressure: Optional[int] = None
    weather_desc: Optional[str] = None
    wind_speed: Optional[float] = None
    wind_deg: Optional[int] = None


class LocationProvider:
    def __init__(self, verbose: bool = False):
        self.verbose = verbose
        self.config = self.load_config()
        self.ensure_dirs()

    def ensure_dirs(self):
        CONFIG_DIR.mkdir(parents=True, exist_ok=True)
        CACHE_DIR.mkdir(parents=True, exist_ok=True)

    def load_config(self) -> Dict[str, Any]:
        if CONFIG_FILE.exists():
            try:
                with open(CONFIG_FILE) as f:
                    return json.load(f)
            except Exception as e:
                self.log(f"Config load error: {e}", "WARN")
        return {}

    def save_config(self, config: Dict[str, Any]):
        try:
            with open(CONFIG_FILE, "w") as f:
                json.dump(config, f, indent=2)
        except Exception as e:
            self.log(f"Config save error: {e}", "ERROR")

    def log(self, msg: str, level: str = "INFO"):
        if self.verbose or level in ("ERROR", "WARN"):
            timestamp = time.strftime("%Y-%m-%d %H:%M:%S")
            print(f"{timestamp} [{level}] {msg}", file=sys.stderr)

    def get_termux_location(
        self, provider: str, timeout: int
    ) -> Optional[Dict[str, Any]]:
        """Get location using termux-location command."""
        try:
            cmd = ["termux-location", "-p", provider, "-r", "once"]
            result = subprocess.run(
                cmd, capture_output=True, text=True, timeout=timeout
            )
            if result.returncode == 0 and result.stdout.strip():
                return json.loads(result.stdout)
            else:
                self.log(f"termux-location failed: {result.stderr}", "WARN")
        except subprocess.TimeoutExpired:
            self.log(f"termux-location timeout ({timeout}s)", "WARN")
        except Exception as e:
            self.log(f"termux-location error: {e}", "WARN")
        return None

    def get_location_with_fallbacks(
        self, provider: str, fallbacks: str, timeout: int
    ) -> Optional[Dict[str, Any]]:
        """Try primary provider, then fallbacks."""
        providers = [provider] + [f.strip() for f in fallbacks.split(",") if f.strip()]
        for p in providers:
            self.log(f"Trying provider: {p}")
            loc = self.get_termux_location(p, timeout)
            if loc:
                loc["provider"] = p
                return loc
        return None

    def reverse_geocode(
        self, lat: float, lon: float, timeout: int, user_agent: str
    ) -> Optional[Dict[str, Any]]:
        """Get address from coordinates using Nominatim."""
        params = {
            "lat": lat,
            "lon": lon,
            "format": "json",
            "addressdetails": 1,
            "accept-language": "en",
        }
        url = f"{NOMINATIM_URL}?{urllib.parse.urlencode(params)}"
        headers = {"User-Agent": user_agent}

        try:
            req = urllib.request.Request(url, headers=headers)
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                data = json.loads(resp.read().decode())
                return data
        except Exception as e:
            self.log(f"Reverse geocode error: {e}", "WARN")
        return None

    def get_weather(
        self, lat: float, lon: float, timeout: int, user_agent: str, unit: str
    ) -> Optional[Dict[str, Any]]:
        """Get weather from OpenWeatherMap (requires API key in config)."""
        api_key = self.config.get("openweather_api_key")
        if not api_key:
            self.log("No OpenWeatherMap API key configured", "DEBUG")
            return None

        units = "metric" if unit == "celsius" else "imperial"
        params = {"lat": lat, "lon": lon, "appid": api_key, "units": units}
        url = f"{OPENWEATHER_URL}?{urllib.parse.urlencode(params)}"
        headers = {"User-Agent": user_agent}

        try:
            req = urllib.request.Request(url, headers=headers)
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                return json.loads(resp.read().decode())
        except Exception as e:
            self.log(f"Weather API error: {e}", "WARN")
        return None

    def get_elevation(
        self, lat: float, lon: float, timeout: int, user_agent: str
    ) -> Optional[float]:
        """Get elevation from Open-Elevation API."""
        params = {"locations": f"{lat},{lon}"}
        url = f"{OPEN_ELEVATION_URL}?{urllib.parse.urlencode(params)}"
        headers = {"User-Agent": user_agent}

        try:
            req = urllib.request.Request(url, headers=headers)
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                data = json.loads(resp.read().decode())
                if data.get("results"):
                    return data["results"][0].get("elevation")
        except Exception as e:
            self.log(f"Elevation API error: {e}", "WARN")
        return None

    def load_cache(self) -> Optional[LocationData]:
        """Load cached location if still valid."""
        if not CACHE_FILE.exists():
            return None
        try:
            with open(CACHE_FILE) as f:
                cache = json.load(f)
            if time.time() - cache.get("timestamp", 0) < CACHE_TTL:
                return LocationData(**cache["data"])
        except Exception:
            pass
        return None

    def save_cache(self, data: LocationData):
        """Save location to cache."""
        try:
            cache = {"timestamp": time.time(), "data": asdict(data)}
            with open(CACHE_FILE, "w") as f:
                json.dump(cache, f)
        except Exception as e:
            self.log(f"Cache save error: {e}", "WARN")

    def format_output(self, data: LocationData, args) -> str:
        """Format output based on arguments."""
        if args.json:
            return json.dumps(asdict(data), indent=2)

        lines = []
        lines.append(
            f"📍 Location: {data.latitude:.{args.precision}f}, {data.longitude:.{args.precision}f}"
        )
        lines.append(f"   Provider: {data.provider}")
        lines.append(
            f"   Accuracy: {data.accuracy:.1f}m"
            if data.accuracy
            else "   Accuracy: N/A"
        )
        lines.append(
            f"   Timestamp: {time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(data.timestamp))}"
        )

        if data.altitude is not None:
            alt = data.altitude
            if args.alt_unit == "feet":
                alt = alt * 3.28084
                unit = "ft"
            else:
                unit = "m"
            lines.append(f"   Altitude: {alt:.1f} {unit}")

        if data.speed is not None:
            speed = data.speed
            if args.speed_unit == "kmh":
                speed = speed * 3.6
                unit = "km/h"
            elif args.speed_unit == "mph":
                speed = speed * 2.23694
                unit = "mph"
            else:
                unit = "m/s"
            lines.append(f"   Speed: {speed:.1f} {unit}")

        if data.bearing is not None:
            lines.append(f"   Bearing: {data.bearing:.0f}°")

        if data.address:
            lines.append(f"\n🏠 Address: {data.address}")
        if data.city:
            lines.append(f"   City: {data.city}")
        if data.state:
            lines.append(f"   State: {data.state}")
        if data.country:
            lines.append(f"   Country: {data.country}")
        if data.postal_code:
            lines.append(f"   Postal Code: {data.postal_code}")
        if data.timezone:
            lines.append(f"   Timezone: {data.timezone}")

        if data.temperature is not None:
            temp = data.temperature
            unit = "°C" if args.unit == "celsius" else "°F"
            lines.append(f"\n🌤 Weather: {temp:.1f}{unit}")
            if data.weather_desc:
                lines.append(f"   Condition: {data.weather_desc}")
            if data.humidity is not None:
                lines.append(f"   Humidity: {data.humidity}%")
            if data.pressure is not None:
                lines.append(f"   Pressure: {data.pressure} hPa")
            if data.wind_speed is not None:
                ws = data.wind_speed
                if args.speed_unit == "kmh":
                    ws = ws * 3.6
                elif args.speed_unit == "mph":
                    ws = ws * 2.23694
                lines.append(f"   Wind: {ws:.1f} {args.speed_unit} @ {data.wind_deg}°")

        maps_url = f"https://maps.google.com/?q={data.latitude},{data.longitude}"
        lines.append(f"\n🗺 Google Maps: {maps_url}")

        return "\n".join(lines)

    def run(self, args) -> int:
        # Handle dry run
        if args.dry_run:
            data = LocationData(
                latitude=37.7749,
                longitude=-122.4194,
                altitude=10.0,
                accuracy=5.0,
                speed=0.0,
                bearing=0.0,
                provider="gps",
                timestamp=int(time.time()),
                address="San Francisco, CA, USA",
                city="San Francisco",
                state="California",
                country="USA",
                postal_code="94102",
                timezone="America/Los_Angeles",
                temperature=20.0,
                humidity=65,
                pressure=1013,
                weather_desc="Clear sky",
                wind_speed=3.0,
                wind_deg=180,
            )
            print(self.format_output(data, args))
            return 0

        # Check cache first
        if not args.no_cache:
            cached = self.load_cache()
            if cached and not args.force_refresh:
                self.log("Using cached location")
                print(self.format_output(cached, args))
                if args.open:
                    self.open_maps(cached.latitude, cached.longitude)
                if args.share:
                    self.share_output(self.format_output(cached, args))
                return 0

        # Get location
        loc = self.get_location_with_fallbacks(
            args.provider, args.fallbacks, args.timeout
        )
        if not loc:
            print("ERROR: Could not get location from any provider", file=sys.stderr)
            return 1

        # Build location data
        data = LocationData(
            latitude=loc.get("latitude", 0),
            longitude=loc.get("longitude", 0),
            altitude=loc.get("altitude"),
            accuracy=loc.get("accuracy"),
            speed=loc.get("speed"),
            bearing=loc.get("bearing"),
            provider=loc.get("provider", args.provider),
            timestamp=loc.get("timestamp", int(time.time() * 1000)) // 1000,
        )

        # Reverse geocode
        if not args.no_address:
            addr_data = self.reverse_geocode(
                data.latitude, data.longitude, args.timeout, args.user_agent
            )
            if addr_data:
                data.address = addr_data.get("display_name")
                addr = addr_data.get("address", {})
                data.city = addr.get("city") or addr.get("town") or addr.get("village")
                data.state = addr.get("state")
                data.country = addr.get("country")
                data.postal_code = addr.get("postcode")

        # Get elevation if not provided
        if data.altitude is None and not args.no_elevation:
            data.altitude = self.get_elevation(
                data.latitude, data.longitude, args.timeout, args.user_agent
            )

        # Get weather
        if not args.no_weather:
            weather = self.get_weather(
                data.latitude, data.longitude, args.timeout, args.user_agent, args.unit
            )
            if weather:
                main = weather.get("main", {})
                data.temperature = main.get("temp")
                data.humidity = main.get("humidity")
                data.pressure = main.get("pressure")
                weather_arr = weather.get("weather", [])
                if weather_arr:
                    data.weather_desc = weather_arr[0].get("description")
                wind = weather.get("wind", {})
                data.wind_speed = wind.get("speed")
                data.wind_deg = wind.get("deg")

        # Save cache
        if not args.no_cache:
            self.save_cache(data)

        # Output
        output = self.format_output(data, args)
        print(output)

        # Handle output file
        if args.output:
            try:
                with open(args.output, "w") as f:
                    f.write(output)
                self.log(f"Output written to {args.output}")
            except Exception as e:
                self.log(f"Output file error: {e}", "ERROR")

        # Open maps
        if args.open:
            self.open_maps(data.latitude, data.longitude)

        # Share
        if args.share:
            self.share_output(output)

        return 0

    def open_maps(self, lat: float, lon: float):
        """Open Google Maps in browser."""
        url = f"https://maps.google.com/?q={lat},{lon}"
        try:
            subprocess.run(["termux-open-url", url], check=False)
        except Exception:
            pass

    def share_output(self, text: str):
        """Share output via termux-share."""
        try:
            subprocess.run(
                ["termux-share", "-t", "text/plain", "-c", text], check=False
            )
        except Exception:
            pass


def create_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Query location coordinates, reverse address lookup, weather, and altitude.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  get_my_location.py                    # Basic location with address
  get_my_location.py --json             # JSON output
  get_my_location.py -p network         # Use network provider
  get_my_location.py --no-weather       # Skip weather lookup
  get_my_location.py -o location.txt    # Save to file
  get_my_location.py --open             # Open in Google Maps
  get_my_location.py --share            # Share via termux-share

Config file: ~/.config/get_my_location/config.json
  Add OpenWeatherMap API key for weather:
  {"openweather_api_key": "YOUR_API_KEY"}
""",
    )
    parser.add_argument(
        "-p",
        "--provider",
        default=DEFAULT_PROVIDER,
        choices=["gps", "network", "cell"],
        help=f"Location provider (default: {DEFAULT_PROVIDER})",
    )
    parser.add_argument(
        "-f",
        "--fallbacks",
        default=DEFAULT_FALLBACKS,
        help=f"Comma-separated fallback providers (default: {DEFAULT_FALLBACKS})",
    )
    parser.add_argument(
        "-u",
        "--unit",
        default=DEFAULT_UNIT,
        choices=["celsius", "fahrenheit"],
        help=f"Temperature unit (default: {DEFAULT_UNIT})",
    )
    parser.add_argument(
        "-s",
        "--speed-unit",
        default=DEFAULT_SPEED_UNIT,
        choices=["ms", "kmh", "mph"],
        help=f"Speed unit (default: {DEFAULT_SPEED_UNIT})",
    )
    parser.add_argument(
        "-a",
        "--alt-unit",
        default=DEFAULT_ALT_UNIT,
        choices=["meters", "feet"],
        help=f"Altitude unit (default: {DEFAULT_ALT_UNIT})",
    )
    parser.add_argument(
        "--precision",
        type=int,
        default=DEFAULT_PRECISION,
        help=f"Decimal places for coordinates (default: {DEFAULT_PRECISION})",
    )
    parser.add_argument(
        "-t",
        "--timeout",
        type=int,
        default=DEFAULT_TIMEOUT,
        help=f"Timeout in seconds (default: {DEFAULT_TIMEOUT})",
    )
    parser.add_argument(
        "--user-agent",
        default=DEFAULT_USER_AGENT,
        help=f"User-Agent header (default: {DEFAULT_USER_AGENT})",
    )
    parser.add_argument(
        "--no-address", action="store_true", help="Skip reverse geocoding"
    )
    parser.add_argument("--no-weather", action="store_true", help="Skip weather lookup")
    parser.add_argument(
        "--no-elevation", action="store_true", help="Skip elevation lookup"
    )
    parser.add_argument("--no-cache", action="store_true", help="Disable caching")
    parser.add_argument(
        "--force-refresh",
        action="store_true",
        help="Force fresh location (ignore cache)",
    )
    parser.add_argument("-o", "--output", help="Write output to file")
    parser.add_argument(
        "--open", action="store_true", help="Open location in Google Maps"
    )
    parser.add_argument(
        "--share", action="store_true", help="Share output via termux-share"
    )
    parser.add_argument("--json", action="store_true", help="Output as JSON")
    parser.add_argument(
        "--dry-run", action="store_true", help="Use dummy data for testing"
    )
    parser.add_argument("-v", "--verbose", action="store_true", help="Verbose logging")
    return parser


def main():
    parser = create_parser()
    args = parser.parse_args()

    provider = LocationProvider(verbose=args.verbose)
    return provider.run(args)


if __name__ == "__main__":
    sys.exit(main())
