#!/usr/bin/env python3
"""mapper_extractor.py - Extract coordinates from a URL and output a GIS table.

Parameters
----------
url : str (required)
    Web page URL to scan for coordinates.
precision : int, optional (default=6)
    Decimal precision for coordinates in the output.
output : str, optional (default=stdout)
    Path to output file. If not provided, results are written to stdout.

The script fetches the given URL, extracts all floating-point numbers,
pairs them sequentially as (lat, lon) coordinates, formats them with the
requested precision, and writes a CSV-like table.
"""

import argparse
import re
import sys
import urllib.request


def run(url: str, precision: int = 6, output: str = None) -> None:
    """
    Extract coordinates from the given URL and output them as a GIS table.

    Args:
        url: Web page URL to scan for coordinates.
        precision: Decimal precision for coordinates (default: 6).
        output: Optional file path for the output. If omitted, prints to stdout.
    """
    # Fetch the URL content
    try:
        with urllib.request.urlopen(url) as response:
            content = response.read().decode("utf-8", errors="ignore")
    except Exception as e:
        sys.stderr.write(f"Error fetching URL: {e}\n")
        return

    # Find all floating-point numbers (including integers) in the content
    numbers = re.findall(r"[-+]?\d*\.\d+|\d+", content)

    # Pair them as (lat, lon) coordinates
    coords = [
        (float(numbers[i]), float(numbers[i + 1]))
        for i in range(0, len(numbers) - 1, 2)
    ]

    # Format each coordinate pair with the requested precision
    formatted_lines = [
        "{:.{p}f},{:.{p}f}".format(lat, lon, p=precision) for lat, lon in coords
    ]

    # Build the output table (CSV-like)
    table = "\n".join(formatted_lines)

    # Write to file or stdout
    if output:
        try:
            with open(output, "w", encoding="utf-8") as out_f:
                out_f.write(table + "\n")
        except Exception as e:
            sys.stderr.write(f"Error writing to file: {e}\n")
    else:
        print(table)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Extract coordinates from a URL.")
    parser.add_argument("url", help="URL to scan for coordinates")
    parser.add_argument(
        "--precision",
        type=int,
        default=6,
        help="Decimal precision for coordinates (default: 6)",
    )
    parser.add_argument(
        "--output", type=str, default=None, help="Output file path (default: stdout)"
    )
    args = parser.parse_args()
    run(args.url, args.precision, args.output)
