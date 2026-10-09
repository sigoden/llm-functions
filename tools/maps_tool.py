#!/usr/bin/env python3
# ==============================================================================
# maps_tool.py — Interactive Mapper v2.5.0
# Security & usability upgrades: sandbox validations, custom tiling, sharing
# ==============================================================================

# @describe Secure interactive mapping tool with Nominatim geocoding and OSRM routing.
#
# @option --operation             Operation: create, search, reverse, list, delete, update, distance, directions, batch (default: create)
# @option --coordinates           Coords array or address string to map (e.g., "[[-33.86,151.2],[34.05,-118.2]]" or "Sydney, Australia")
# @option --style                 Map tile theme: streets, satellite, dark, light, outdoors (default: streets)
# @option --custom-tiles          Direct URL schema for custom tile servers (e.g., "https://{s}.tile.example.com/{z}/{x}/{y}.png")
# @option --markers               Marker visual type: circles, popups (default: circles)
# @option --name                  Location filename for saving metadata (default: auto)
# @option --description           Description to include in location metadata
# @option --category              Location category color: home, work, none (default: none)
# @option --query                 Search text for Nominatim place finder
# @option --limit                 Maximum search result entries (default: 5)
# @option --save-search           Save search result metadata to this location filename
# @option --lat                   Latitude coordinate for reverse search (default: 0.0)
# @option --lon                   Longitude coordinate for reverse search (default: 0.0)
# @option --loc1                  Start location filename or coordinates for distance calculations
# @option --loc2                  End location filename or coordinates for distance calculations
# @option --points                Points list for batch routing (comma-separated or JSON list)
# @option --operations            JSON array of sequential operations for batch running
# @option --zoom                  Default map zoom level (1 to 18) (default: 12)
# @option --unit                  Distance calculation unit: km, miles, nm (default: km)
# @option --accuracy              Termux GPS accuracy parameter: gps, network, cell (default: gps)
# @option --maps-dir              Custom sandbox directory for saved maps
# @option --user-agent            Custom User-Agent header override for Nominatim requests
# @flag   --use-termux-location   Use Termux GPS data for current location
# @flag   --save                  Save folium map to disk
# @flag   --share                 Trigger termux-share to distribute output map file
# @flag   --verbose               Enable detailed warning output to stderr
#

from __future__ import annotations

import argparse
import ast
import functools
import json
import logging
import math
import os
import subprocess
import sys
import time
from pathlib import Path
from typing import Any, Callable, List, Optional, Tuple
from urllib.parse import quote
from urllib.request import Request, urlopen

# Optional dependencies
try:
    import folium
    from folium.plugins import HeatMap, MarkerCluster
except ImportError:
    folium = None

# ==============================================================================
# SECTION 1: Logger & Constants
# ==============================================================================
logger = logging.getLogger(__name__)
if not logger.handlers:
    logger.addHandler(logging.StreamHandler(sys.stderr))

DEFAULT_MAPS_DIR = Path(os.path.expanduser("~")) / ".config" / "aichat" / "maps"
CACHE_FILE_NAME = "geocode_cache.json"

STYLE_TILES = {
    "streets": "openstreetmap",
    "satellite": "https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/satellite/{z}/{x}/{y}",
    "dark": "https://{s}.basemaps.cartocdn.com/dark_all/{z}/{x}/{y}",
    "light": "https://{s}.basemaps.cartocdn.com/light_all/{z}/{x}/{y}",
    "outdoors": "https://{s}.basemaps.cartocdn.com/outdoors_all/{z}/{x}/{y}",
}
CATEGORY_COLORS = {
    "home": "green",
    "work": "blue",
    "none": "red",
    "orange": "orange",
    "purple": "purple",
    "grey": "grey",
    "yellow": "yellow",
}


class MapManager:
    """Secure map file manager with caching and sandbox enforcement."""

    def __init__(self, custom_dir: Optional[str] = None) -> None:
        if custom_dir:
            self.maps_dir = Path(custom_dir).expanduser().resolve()
        else:
            self.maps_dir = DEFAULT_MAPS_DIR.resolve()
        self.maps_dir.mkdir(parents=True, exist_ok=True)
        self.cache_file = self.maps_dir / CACHE_FILE_NAME
        self.cache = self._load_cache()

    def _load_cache(self) -> dict:
        if self.cache_file.exists():
            try:
                with open(self.cache_file, encoding="utf-8") as f:
                    return json.load(f)
            except Exception:
                pass
        return {}

    def save_cache(self):
        try:
            with open(self.cache_file, "w", encoding="utf-8") as f:
                # Ensure CRLF line endings if original cache used them
                json.dump(self.cache, f)
        except Exception:
            pass

    def validate_sandbox(self, path: Path) -> bool:
        home = Path.home().resolve()
        tmp = Path("/tmp").resolve()
        try:
            resolved = path.resolve()
            s = str(resolved)
            return s.startswith(str(home)) or s.startswith(str(tmp))
        except OSError:
            return False

    def validate_path(self, name: str) -> Path:
        if ".." in name or os.sep in name:
            raise ValueError(f"Invalid location name: '{name}'")
        target_path = (self.maps_dir / f"{name}.json").resolve()
        if not self.validate_sandbox(target_path):
            raise ValueError(
                f"Target path '{target_path}' lies outside allowed sandbox."
            )
        return target_path

    def validate_coords(self, lat: float, lon: float):
        if not (-90.0 <= lat <= 90.0) or not (-180.0 <= lon <= 180.0):
            raise ValueError(
                f"Invalid coordinate parameters: lat={lat}, lon={lon}. Coordinates must lie within ranges."
            )


_manager = MapManager()
_verbose: bool = False


# ==============================================================================
# SECTION 2: Decorators & Helpers
# ==============================================================================


def _timed(fn: Callable[..., dict[str, Any]]) -> Callable[..., dict[str, Any]]:
    @functools.wraps(fn)
    def wrapper(*args: Any, **kwargs: Any) -> dict[str, Any]:
        t0 = time.perf_counter()
        result = fn(*args, **kwargs)
        if not isinstance(result, dict):
            result = {"success": False, "error": "Operation returned non-dict"}
        result["duration_ms"] = round((time.perf_counter() - t0) * 1000, 3)
        return result

    return wrapper


def haversine(
    lat1: float, lon1: float, lat2: float, lon2: float, unit: str = "km"
) -> float:
    R_km = 6371.0
    dlat, dlon = math.radians(lat2 - lat1), math.radians(lon2 - lon1)
    a = (
        math.sin(dlat / 2) ** 2
        + math.cos(math.radians(lat1))
        * math.cos(math.radians(lat2))
        * math.sin(dlon / 2) ** 2
    )
    c = R_km * 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))
    if unit.lower() == "miles":
        return c * 0.621371
    elif unit.lower() == "nm":
        return c * 0.539957
    return c


def nominatim_request(
    path: str,
    params: dict[str, str],
    user_agent: Optional[str] = None,
    timeout: int = 5,
) -> Any:
    key = json.dumps(params, sort_keys=True)
    if key in _manager.cache:
        _debug(f"Serving geocode result for {params} from cache.")
        return _manager.cache[key]

    query = "&".join([f"{k}={quote(v)}" for k, v in params.items()])
    url = f"https://nominatim.openstreetmap.org/{path}?{query}"
    ua = user_agent or "maps_tool/2.5"
    req = Request(url, headers={"User-Agent": ua})

    try:
        with urlopen(req, timeout=timeout) as r:
            if r.status == 429:
                raise Exception("Rate limit exceeded (429)")
            data = json.load(r)
            _manager.cache[key] = data
            _manager.save_cache()
            return data
    except Exception as e:
        logger.error(f"Nominatim request failed: {e}")
        # Search fallback to cache
        if key in _manager.cache:
            return _manager.cache[key]
        raise


def osrm_request(coordinates: str, timeout: int = 5) -> Any:
    url = f"http://router.project-osrm.org/route/v1/driving/{coordinates}?overview=full&steps=true"
    req = Request(url, headers={"User-Agent": "maps_tool/2.5"})
    with urlopen(req, timeout=timeout) as r:
        return json.load(r)


def _debug(msg: str) -> None:
    if _verbose:
        print(f"[DEBUG] {msg}", file=sys.stderr)


def get_termux_location(accuracy: str = "gps") -> Optional[Tuple[float, float]]:
    try:
        cmd = ["termux-location", "-p", accuracy]
        loc = json.loads(subprocess.check_output(cmd, text=True, timeout=10))
        return (float(loc["latitude"]), float(loc["longitude"]))
    except Exception as e:
        _debug(f"Termux location retrieval failed: {e}")
        return None


def load_coordinates(
    coords_input: str, user_agent: Optional[str] = None
) -> List[Tuple[float, float]]:
    coords_input = coords_input.strip()
    if coords_input.startswith("[") or coords_input.startswith("("):
        try:
            parsed = ast.literal_eval(coords_input)
            if isinstance(parsed, list):
                if all(isinstance(x, (list, tuple)) and len(x) == 2 for x in parsed):
                    return [(float(item[0]), float(item[1])) for item in parsed]
                elif len(parsed) == 2 and isinstance(parsed[0], (int, float)):
                    return [(float(parsed[0]), float(parsed[1]))]
        except Exception:
            pass
    res = nominatim_request(
        "search", {"q": coords_input, "format": "json", "limit": "1"}, user_agent
    )
    return [(float(res[0]["lat"]), float(res[0]["lon"]))] if res else []


# ==============================================================================
# SECTION 3: Operations
# ==============================================================================


@_timed
def get_directions(
    points: List[str] | str, user_agent: Optional[str] = None
) -> dict[str, Any]:
    try:
        if isinstance(points, str):
            if points.strip().startswith("["):
                pts = json.loads(points)
            else:
                pts = [p.strip() for p in points.split(",") if p.strip()]
        else:
            pts = points

        coords = []
        for p in pts:
            res = nominatim_request(
                "search", {"q": str(p), "format": "json", "limit": "1"}, user_agent
            )
            if res:
                coords.append((float(res[0]["lon"]), float(res[0]["lat"])))
        if len(coords) < 2:
            return {"success": False, "error": "Need at least 2 points"}

        coord_str = ";".join([f"{lon},{lat}" for lon, lat in coords])
        data = osrm_request(coord_str)

        if "routes" not in data or not data["routes"]:
            return {"success": False, "error": "No route found"}

        steps = []
        for leg in data["routes"][0]["legs"]:
            for step in leg["steps"]:
                m = step["maneuver"]
                t = m.get("type", "")
                mod = m.get("modifier", "")
                name = step.get("name", "")
                dist = int(step.get("distance", 0))

                road_info = f" onto {name}" if name else ""
                dist_info = f" for {dist} meters" if dist > 0 else ""

                if t == "depart":
                    instruction = f"Depart {mod}{road_info}{dist_info}."
                elif t == "arrive":
                    instruction = "Arrive at destination."
                elif t == "turn":
                    instruction = f"Turn {mod}{road_info}{dist_info}."
                elif t == "exit roundabout":
                    instruction = f"Exit roundabout {mod}{road_info}{dist_info}."
                else:
                    instruction = f"{t.replace('_', ' ').capitalize()} {mod}{road_info}{dist_info}."

                steps.append(instruction)

        return {
            "success": True,
            "instructions": steps,
            "geometry": data["routes"][0].get("geometry"),
        }
    except Exception as e:
        return {"success": False, "error": str(e)}


@_timed
def search_places(
    query: str,
    limit: int = 5,
    save_search: Optional[str] = None,
    user_agent: Optional[str] = None,
) -> dict[str, Any]:
    try:
        data = nominatim_request(
            "search", {"q": query, "format": "json", "limit": str(limit)}, user_agent
        )
        result = {"success": True, "results": data}
        if save_search and data:
            path = _manager.validate_path(save_search)
            # Store full geocoded details
            meta = {
                "type": "point",
                "lat": float(data[0]["lat"]),
                "lon": float(data[0]["lon"]),
                "description": data[0]["display_name"],
                "class": data[0].get("class"),
                "importance": data[0].get("importance"),
                "osm_type": data[0].get("osm_type"),
            }
            with open(path, "w", encoding="utf-8") as f:
                json.dump(meta, f, indent=2)
            result["saved"] = save_search
        return result
    except Exception as e:
        return {"success": False, "error": str(e)}


@_timed
def reverse_search(
    lat: float,
    lon: float,
    user_agent: Optional[str] = None,
) -> dict[str, Any]:
    try:
        _manager.validate_coords(lat, lon)
        data = nominatim_request(
            "reverse", {"lat": str(lat), "lon": str(lon), "format": "json"}, user_agent
        )
        return {"success": True, "result": data}
    except Exception as e:
        return {"success": False, "error": str(e)}


@_timed
def list_locations() -> dict[str, Any]:
    items = []
    for f in _manager.maps_dir.glob("*.json"):
        if f.name == CACHE_FILE_NAME:
            continue
        try:
            with open(f, encoding="utf-8") as fh:
                meta = json.load(fh)
                items.append(
                    {
                        "name": f.stem,
                        "description": meta.get("description", "N/A"),
                        "lat": meta.get("lat"),
                        "lon": meta.get("lon"),
                    }
                )
        except Exception:
            continue
    return {"success": True, "items": items}


@_timed
def delete_location(name: str) -> dict[str, Any]:
    try:
        path_json = _manager.validate_path(name)
        path_html = path_json.with_suffix(".html")
        path_json.unlink(missing_ok=True)
        path_html.unlink(missing_ok=True)
        return {"success": True, "message": f"Deleted {name}"}
    except Exception as e:
        return {"success": False, "error": str(e)}


@_timed
def update_description(name: str, description: str) -> dict[str, Any]:
    try:
        path = _manager.validate_path(name)
        if not path.exists():
            return {"success": False, "error": "Location not found"}
        with open(path, encoding="utf-8") as f:
            meta = json.load(f)
        meta["description"] = description
        with open(path, "w", encoding="utf-8") as f:
            json.dump(meta, f, indent=2)
        return {"success": True, "message": "Updated"}
    except Exception as e:
        return {"success": False, "error": str(e)}


@_timed
def calculate_distance(loc1: str, loc2: str, unit: str = "km") -> dict[str, Any]:
    try:
        # Load from saved location or parse coordinates directly
        try:
            p1 = _manager.validate_path(loc1)
            l1 = json.load(open(p1, encoding="utf-8"))
            lat1, lon1 = float(l1["lat"]), float(l1["lon"])
        except Exception:
            parsed = ast.literal_eval(loc1)
            lat1, lon1 = float(parsed[0]), float(parsed[1])

        try:
            p2 = _manager.validate_path(loc2)
            l2 = json.load(open(p2, encoding="utf-8"))
            lat2, lon2 = float(l2["lat"]), float(l2["lon"])
        except Exception:
            parsed = ast.literal_eval(loc2)
            lat2, lon2 = float(parsed[0]), float(parsed[1])

        dist = haversine(lat1, lon1, lat2, lon2, unit)
        return {"success": True, "distance": dist, "unit": unit}
    except Exception as e:
        return {"success": False, "error": str(e)}


@_timed
def create_map(
    coordinates: Optional[str] = None,
    style: str = "streets",
    custom_tiles: Optional[str] = None,
    markers: str = "circles",
    location_name: str = "auto",
    description: Optional[str] = None,
    category: Optional[str] = None,
    use_termux_location: bool = False,
    save_location: bool = False,
    visualization: str = "marker",
    zoom: int = 12,
    accuracy: str = "gps",
    share: bool = False,
    user_agent: Optional[str] = None,
) -> dict[str, Any]:
    if folium is None:
        return {"success": False, "error": "Folium not installed"}

    coords_list = []
    if use_termux_location:
        loc = get_termux_location(accuracy)
        if loc:
            coords_list.append(loc)
    elif coordinates:
        coords_list = load_coordinates(coordinates, user_agent)

    if not coords_list:
        return {"success": False, "error": "No coordinates found."}

    # Tile style configuration
    tiles_provider = custom_tiles or STYLE_TILES.get(style, "openstreetmap")
    attr = "Map tiles by custom URL provider" if custom_tiles else "OpenStreetMap"

    m = folium.Map(
        location=coords_list[0], zoom_start=zoom, tiles=tiles_provider, attr=attr
    )
    color = CATEGORY_COLORS.get(category or "none", "red")

    if visualization == "heatmap":
        HeatMap(coords_list).add_to(m)
    elif visualization == "cluster":
        mc = MarkerCluster().add_to(m)
        for lat, lon in coords_list:
            folium.Marker([lat, lon]).add_to(mc)
    else:
        for lat, lon in coords_list:
            if markers == "circles":
                folium.CircleMarker(
                    [lat, lon], radius=5, color=color, fill=True
                ).add_to(m)
            else:
                folium.Marker([lat, lon], icon=folium.Icon(color=color)).add_to(m)

    # Draw Polyline routes automatically if mapping multiple coordinates
    if len(coords_list) > 1:
        folium.PolyLine(coords_list, color="blue", weight=3, opacity=0.7).add_to(m)

    if save_location:
        path = _manager.validate_path(location_name)
        html_path = path.with_suffix(".html")
        m.save(str(html_path))
        with open(path, "w", encoding="utf-8") as f:
            json.dump(
                {
                    "type": "point",
                    "lat": coords_list[0][0],
                    "lon": coords_list[0][1],
                    "description": description,
                },
                f,
                indent=2,
            )

        # Automatically trigger share if requested
        if share and shutil.which("termux-share"):
            try:
                subprocess.run(["termux-share", str(html_path)], check=True)
            except Exception as e:
                _warn(f"Failed to share map file: {e}")

        return {
            "success": True,
            "message": f"Saved map as {location_name}",
            "html_path": str(html_path),
            "json_path": str(path),
        }
    else:
        return {"success": True, "map_html": m.get_root().render()}


@_timed
def batch(operations: list[dict[str, Any]]) -> dict[str, Any]:
    """Execute multiple map operations in sequence with transactional rollback."""
    results = []
    created_locations = []

    for op_data in operations:
        op_data_copy = op_data.copy()
        operation = op_data_copy.pop("operation", None)
        if not operation:
            continue

        # Track saved outputs for rollbacks
        name = op_data_copy.get("name")
        res = run(operation, **op_data_copy)
        results.append({"operation": operation, "result": res})

        if res.get("success"):
            if operation == "create" and op_data_copy.get("save") and name:
                created_locations.append(name)
        else:
            # Rollback previous saves in batch transaction on failure
            _warn(f"Batch failed on {operation}. Rolling back created locations...")
            for loc in created_locations:
                try:
                    delete_location(loc)
                except Exception:
                    pass
            return {
                "success": False,
                "error": f"Batch stopped on operation '{operation}': {res.get('error')}",
                "results": results,
            }

    return {"success": True, "results": results}


# ==============================================================================
# SECTION 4: Dispatcher
# ==============================================================================


def run(operation: Optional[str] = None, **kwargs: Any) -> dict[str, Any]:
    """Dispatcher entry point."""
    global _verbose
    _verbose = kwargs.get("verbose", False)

    if operation is None:
        operation = kwargs.get("operation", "create")

    # Handle maps-dir configuration override
    maps_dir_override = kwargs.get("maps_dir")
    if maps_dir_override:
        global _manager
        _manager = MapManager(maps_dir_override)

    dispatch: dict[str, Callable[..., dict[str, Any]]] = {
        "list": list_locations,
        "delete": lambda: delete_location(kwargs.get("name", "")),
        "distance": lambda: calculate_distance(
            kwargs.get("loc1", ""), kwargs.get("loc2", ""), kwargs.get("unit", "km")
        ),
        "update": lambda: update_description(
            kwargs.get("name", ""), kwargs.get("description", "")
        ),
        "create": lambda: create_map(
            coordinates=kwargs.get("coordinates"),
            style=kwargs.get("style", "streets"),
            custom_tiles=kwargs.get("custom_tiles"),
            markers=kwargs.get("markers", "circles"),
            location_name=kwargs.get("name", "auto"),
            description=kwargs.get("description"),
            category=kwargs.get("category"),
            use_termux_location=kwargs.get("use_termux_location", False),
            save_location=kwargs.get("save", False),
            visualization=kwargs.get("visualization", "marker"),
            zoom=int(kwargs.get("zoom", 12)),
            accuracy=kwargs.get("accuracy", "gps"),
            share=kwargs.get("share", False),
            user_agent=kwargs.get("user_agent"),
        ),
        "search": lambda: search_places(
            kwargs.get("query", ""),
            int(kwargs.get("limit", 5)),
            kwargs.get("save_search"),
            kwargs.get("user_agent"),
        ),
        "reverse": lambda: reverse_search(
            float(kwargs.get("lat", 0.0)),
            float(kwargs.get("lon", 0.0)),
            kwargs.get("user_agent"),
        ),
        "batch": lambda: batch(kwargs.get("operations", [])),
        "directions": lambda: get_directions(
            kwargs.get("points", []), kwargs.get("user_agent")
        ),
    }

    if operation not in dispatch:
        return {"success": False, "error": f"Unknown operation: {operation}"}

    try:
        return dispatch[operation]()
    except Exception as e:
        logger.exception("Error in operation %s", operation)
        return {"success": False, "error": str(e)}


# ==============================================================================
# SECTION 5: CLI
# ==============================================================================


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="maps_tool.py", description="Interactive Mapper"
    )
    parser.add_argument(
        "--maps-dir", help="Custom folder for metadata storage", dest="maps_dir"
    )
    parser.add_argument(
        "--user-agent", help="Custom User Agent for Nominatim", dest="user_agent"
    )
    parser.add_argument(
        "--verbose", "-v", action="store_true", help="Enable verbose diagnostic logs"
    )

    subparsers = parser.add_subparsers(dest="operation", required=True)

    subparsers.add_parser("list", help="List saved locations")

    delete = subparsers.add_parser("delete", help="Delete a location")
    delete.add_argument("name", help="Location name")

    dist = subparsers.add_parser("distance", help="Calculate distance")
    dist.add_argument("loc1", help="First location")
    dist.add_argument("loc2", help="Second location")
    dist.add_argument("--unit", choices=["km", "miles", "nm"], default="km")

    upd = subparsers.add_parser("update", help="Update description")
    upd.add_argument("name", help="Location name")
    upd.add_argument("description", help="New description")

    create = subparsers.add_parser("create", help="Create a map")
    create.add_argument("--coordinates", help="Coords/address")
    create.add_argument("--style", default="streets")
    create.add_argument(
        "--custom-tiles", help="URL pattern for custom map tiles", dest="custom_tiles"
    )
    create.add_argument("--markers", default="circles")
    create.add_argument("--name", default="auto", help="Location name")
    create.add_argument("--description")
    create.add_argument("--category")
    create.add_argument("--save", action="store_true")
    create.add_argument(
        "--visualization", choices=["marker", "heatmap", "cluster"], default="marker"
    )
    create.add_argument("--zoom", type=int, default=12)
    create.add_argument("--accuracy", choices=["gps", "network", "cell"], default="gps")
    create.add_argument("--share", action="store_true")

    search = subparsers.add_parser("search", help="Find places by query")
    search.add_argument("query", help="Place to search for")
    search.add_argument("--limit", type=int, default=5)
    search.add_argument("--save-search", help="Save first result as location")

    rev = subparsers.add_parser("reverse", help="Get place from coordinates")
    rev.add_argument("lat", type=float)
    rev.add_argument("lon", type=float)

    batch = subparsers.add_parser("batch", help="Run multiple operations")
    batch.add_argument(
        "--operations", type=json.loads, help="JSON string of operations"
    )

    direc = subparsers.add_parser("directions", help="Get text directions")
    direc.add_argument("points", nargs="+", help="List of points/addresses")

    return parser


if __name__ == "__main__":
    parser = _build_parser()
    args = parser.parse_args()

    result = run(**vars(args))
    # output strictly clean JSON to stdout
    print(json.dumps(result, indent=2))
    sys.exit(0 if result.get("success") else 1)
