#!/usr/bin/env python3
"""navigate_between.py - Calculate distance/bearing between two pinned locations."""

import argparse
import json
import math
import os


def haversine(lat1, lon1, lat2, lon2):
    R = 6371  # Earth radius in km
    dlat = math.radians(lat2 - lat1)
    dlon = math.radians(lon2 - lon1)
    a = (
        math.sin(dlat / 2) ** 2
        + math.cos(math.radians(lat1))
        * math.cos(math.radians(lat2))
        * math.sin(dlon / 2) ** 2
    )
    c = 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))
    return R * c


def bearing(lat1, lon1, lat2, lon2):
    lat1, lon1, lat2, lon2 = map(math.radians, [lat1, lon1, lat2, lon2])
    dlon = lon2 - lon1
    x = math.sin(dlon) * math.cos(lat2)
    y = math.cos(lat1) * math.sin(lat2) - (
        math.sin(lat1) * math.cos(lat2) * math.cos(dlon)
    )
    return (math.degrees(math.atan2(x, y)) + 360) % 360


def find_location(data, label):
    for entry in data:
        if entry.get("label") == label:
            return entry
    return None


def run(label_a: str, label_b: str) -> str:
    """
    Navigate between two pinned locations.

    Args:
        label_a: The label of the starting location.
        label_b: The label of the destination location.

    Returns:
        A navigation summary string.
    """
    root_dir = os.environ.get("LLM_ROOT_DIR", ".")
    storage_file = os.path.join(root_dir, "pinned_locations.json")

    if not os.path.exists(storage_file):
        return "Error: pinned_locations.json not found."

    with open(storage_file) as f:
        data = json.load(f)

    loc_a = find_location(data, label_a)
    loc_b = find_location(data, label_b)

    if not loc_a or not loc_b:
        return f"Error: Could not find both labels '{label_a}' and '{label_b}'."

    dist = haversine(
        loc_a["latitude"], loc_a["longitude"], loc_b["latitude"], loc_b["longitude"]
    )
    brng = bearing(
        loc_a["latitude"], loc_a["longitude"], loc_b["latitude"], loc_b["longitude"]
    )

    return f"Navigation from '{label_a}' to '{label_b}': Distance: {dist:.2f} km, Bearing: {brng:.2f}°"


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Navigate between two points.")
    parser.add_argument("label_a", help="Starting label")
    parser.add_argument("label_b", help="Destination label")
    args = parser.parse_args()
    print(run(args.label_a, args.label_b))
