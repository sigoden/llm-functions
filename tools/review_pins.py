#!/usr/bin/env python3
"""review_pins.py - List pinned locations and visualize on a text map."""

import argparse
import json
import os


def load_config():
    with open("reference_map.txt") as f:
        lines = f.readlines()
        bounds = None
        width = 20
        height = 10
        for line in lines:
            if line.startswith("BOUNDS="):
                bounds = [float(x) for x in line.split("=")[1].split(",")]
            elif line.startswith("GRID_WIDTH="):
                width = int(line.split("=")[1])
            elif line.startswith("GRID_HEIGHT="):
                height = int(line.split("=")[1])
        return bounds, width, height


def run(dummy_arg: str = "none") -> str:
    """
    List pinned locations and visualize on a text map.

    Args:
        dummy_arg: Dummy argument to satisfy builder.

    Returns:
        A success/error message.
    """
    root_dir = os.environ.get("LLM_ROOT_DIR", ".")
    storage_file = os.path.join(root_dir, "pinned_locations.json")

    if not os.path.exists(storage_file):
        return "No pinned locations found."

    with open(storage_file) as f:
        pins = json.load(f)

    bounds, width, height = load_config()
    lat_min, lat_max, lon_min, lon_max = bounds

    # Initialize empty grid
    grid = [[" " for _ in range(width)] for _ in range(height)]

    output = f"{'Label':<15} | {'Lat':<10} | {'Lon':<10}\n"
    output += "-" * 40 + "\n"
    for pin in pins:
        output += (
            f"{pin['label']:<15} | {pin['latitude']:.5f} | {pin['longitude']:.5f}\n"
        )

        # Calculate grid position
        lat_norm = (pin["latitude"] - lat_min) / (lat_max - lat_min)
        lon_norm = (pin["longitude"] - lon_min) / (lon_max - lon_min)

        row = height - 1 - int(lat_norm * (height - 1))
        col = int(lon_norm * (width - 1))

        if 0 <= row < height and 0 <= col < width:
            grid[row][col] = "X"

    output += "\nText Map:\n"
    output += "+" + "-" * width + "+\n"
    for row in grid:
        output += "|" + "".join(row) + "|\n"
    output += "+" + "-" * width + "+"

    print(output)
    return "Pins reviewed."


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Review and visualize pins.")
    parser.add_argument("--dummy", type=str, default="none", help="Dummy argument")
    args = parser.parse_args()
    print(run(args.dummy))
