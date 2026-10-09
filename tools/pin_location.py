#!/usr/bin/env python3
"""pin_location.py - Pin the current location and save it.

Parameters
----------
label : str, optional (default="current_location")
    A label for the location pin.

The script uses `termux-location` to fetch the current GPS coordinates,
adds a label and timestamp, and appends them to `pinned_locations.json`
in the project root directory.
"""

import argparse
import datetime
import json
import os
import subprocess


def run(label: str = "current_location") -> str:
    """
    Pins the current location using termux-location and saves it to a file.

    Args:
        label: A label for the location pin (default: "current_location").

    Returns:
        A success or error message string.
    """
    try:
        # Run termux-location
        result = subprocess.run(
            ["termux-location"], capture_output=True, text=True, check=True
        )
        location_data = json.loads(result.stdout)

        # Add a label and timestamp
        location_data["label"] = label
        location_data["timestamp"] = datetime.datetime.now().isoformat()

        # Save to a file (pinned_locations.json)
        # Using LLM_ROOT_DIR if available, else current directory
        root_dir = os.environ.get("LLM_ROOT_DIR", ".")
        storage_file = os.path.join(root_dir, "pinned_locations.json")

        data = []
        if os.path.exists(storage_file):
            with open(storage_file) as f:
                try:
                    data = json.load(f)
                except json.JSONDecodeError:
                    pass

        data.append(location_data)

        with open(storage_file, "w") as f:
            json.dump(data, f, indent=2)

        return f"Successfully pinned location '{label}' at {location_data['latitude']}, {location_data['longitude']}"
    except Exception as e:
        return f"Error pinning location: {e!s}"


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Pin the current location.")
    parser.add_argument(
        "--label",
        type=str,
        default="current_location",
        help="Label for the location pin",
    )
    args = parser.parse_args()
    print(run(args.label))
