#!/usr/bin/env python3
# @describe Download an image from a URL and save it to the local filesystem.
# @option --url! <TEXT>                          The direct image URL to download
# @option --output-path <PATH>                    Optional custom path/filename to save the image to
# @flag   --verbose                              Enable verbose logs
"""
download_image.py - Download and save images.
"""

import argparse
import json
import os
import sys
import urllib.parse

import requests


def run(url: str, output_path: str = None, verbose: bool = False) -> dict:
    # 1. Resolve output path
    if not output_path:
        # Determine standard caches/downloads directory
        cache_dir = os.environ.get("LLM_TOOL_CACHE_DIR")
        if not cache_dir:
            cache_dir = os.path.join(os.getcwd(), "cache", "download_image")
        os.makedirs(cache_dir, exist_ok=True)

        # Extrapolate filename from url
        parsed_url = urllib.parse.urlparse(url)
        filename = os.path.basename(parsed_url.path)
        if not filename or "." not in filename:
            filename = f"downloaded_{int(requests.utils.time.time())}.jpg"
        output_path = os.path.join(cache_dir, filename)
    else:
        # Make sure parent directories exist
        parent = os.path.dirname(os.path.abspath(output_path))
        os.makedirs(parent, exist_ok=True)

    # 2. Perform download
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    }
    try:
        if verbose:
            print(f"Downloading {url} to {output_path}...", file=sys.stderr)

        response = requests.get(url, headers=headers, timeout=30, stream=True)
        response.raise_for_status()

        # Verify content type is an image
        content_type = response.headers.get("Content-Type", "")
        if "image" not in content_type and verbose:
            print(
                f"Warning: Content-Type is '{content_type}', not standard image type.",
                file=sys.stderr,
            )

        with open(output_path, "wb") as f:
            for chunk in response.iter_content(chunk_size=8192):
                if chunk:
                    f.write(chunk)

        return {
            "success": True,
            "url": url,
            "saved_path": os.path.abspath(output_path),
            "file_size": os.path.getsize(output_path),
            "content_type": content_type,
        }
    except Exception as e:
        return {"success": False, "url": url, "error": str(e)}


if __name__ == "__main__":
    # 1. Parse JSON input if passed by aichat's tool dispatcher
    if len(sys.argv) > 1 and (
        sys.argv[1].startswith("{") or sys.argv[1].startswith("[")
    ):
        try:
            kwargs = json.loads(sys.argv[1])
            url_val = kwargs.get("url")
            path_val = kwargs.get("output_path")
            verb_val = kwargs.get("verbose", False)
            if not url_val:
                print(json.dumps({"success": False, "error": "URL is required"}))
                sys.exit(1)
            print(json.dumps(run(url_val, path_val, verb_val), indent=2))
            sys.exit(0)
        except Exception as err:
            print(
                json.dumps(
                    {"success": False, "error": f"JSON argument parse error: {err}"}
                )
            )
            sys.exit(1)

    # 2. Fallback to standard CLI arguments
    parser = argparse.ArgumentParser(description="Download images tool")
    parser.add_argument("--url", required=True)
    parser.add_argument("--output-path")
    parser.add_argument("--verbose", action="store_true")
    args = parser.parse_args()
    print(json.dumps(run(args.url, args.output_path, args.verbose), indent=2))
