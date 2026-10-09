#!/usr/bin/env python3
# @describe Search for images on the web using Google CSE or Bing fallbacks, with optional automatic bulk downloading.
# @option --query! <TEXT>                          Image search query
# @option --limit <NUM>                            Maximum number of results to return (default: 10)
# @option --download-dir <PATH>                    Optional directory to download the search results to
"""
image_search.py - Search for images on the web.
"""

import argparse
import json
import logging
import os
import sys
import urllib.parse
from typing import Any, Dict, List

import requests


def load_env():
    # Check parent directory (root of repository) for .env file
    env_path = os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))), ".env"
    )
    if os.path.exists(env_path):
        with open(env_path) as f:
            for line in f:
                line = line.strip()
                if line and not line.startswith("#") and "=" in line:
                    key, val = line.split("=", 1)
                    os.environ[key.strip()] = val.strip()


def search_google_images(query: str, count: int = 10) -> List[Dict[str, Any]]:
    api_key = os.environ.get("GOOGLE_API_KEY")
    cse_id = os.environ.get("GOOGLE_CSE_ID")
    if not api_key or not cse_id:
        return []

    url = "https://www.googleapis.com/customsearch/v1"
    params = {
        "key": api_key,
        "cx": cse_id,
        "q": query,
        "searchType": "image",
        "num": min(count, 10),
    }
    try:
        response = requests.get(url, params=params, timeout=15)
        response.raise_for_status()
        data = response.json()

        results = []
        for item in data.get("items", []):
            results.append(
                {
                    "title": item.get("title"),
                    "image_url": item.get("link"),
                    "thumbnail_url": item.get("image", {}).get("thumbnailLink"),
                    "width": item.get("image", {}).get("width"),
                    "height": item.get("image", {}).get("height"),
                    "source": item.get("image", {}).get("contextLink"),
                }
            )
        return results
    except Exception as e:
        logging.warning(f"Google Image Search failed: {e}")
        return []


def search_bing_images(query: str, count: int = 10) -> List[Dict[str, Any]]:
    """Free Bing Image Search fallback using HTML scraping."""
    url = f"https://www.bing.com/images/search?q={urllib.parse.quote(query)}"
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    }
    try:
        response = requests.get(url, headers=headers, timeout=15)
        response.raise_for_status()

        import re

        body = response.text
        results = []
        matches = re.finditer(
            r"murl&quot;:&quot;([^&]+)&quot;.*?t&quot;:&quot;([^&]+)&quot;", body
        )
        for match in matches:
            img_url = match.group(1)
            title = match.group(2)

            img_url = urllib.parse.unquote(img_url).replace("\\/", "/")
            title = urllib.parse.unquote(title)

            turl_match = re.search(r"turl&quot;:&quot;([^&]+)&quot;", match.group(0))
            turl = turl_match.group(1) if turl_match else None
            if turl:
                turl = urllib.parse.unquote(turl).replace("\\/", "/")

            results.append(
                {
                    "title": title,
                    "image_url": img_url,
                    "thumbnail_url": turl,
                    "width": None,
                    "height": None,
                    "source": None,
                }
            )
            if len(results) >= count:
                break
        return results
    except Exception as e:
        logging.warning(f"Bing keyless image search failed: {e}")
        return []


def download_image_file(url: str, download_dir: str) -> str:
    """Download single image file into download_dir."""
    os.makedirs(download_dir, exist_ok=True)
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    }
    try:
        parsed_url = urllib.parse.urlparse(url)
        filename = os.path.basename(parsed_url.path)
        if not filename or "." not in filename:
            filename = f"image_{hash(url) & 0xFFFFFFFF}.jpg"
        # Sanitize filename
        filename = "".join(c for c in filename if c.isalnum() or c in "._-")
        output_path = os.path.join(download_dir, filename)

        response = requests.get(url, headers=headers, timeout=20, stream=True)
        response.raise_for_status()
        with open(output_path, "wb") as f:
            for chunk in response.iter_content(chunk_size=8192):
                if chunk:
                    f.write(chunk)
        return os.path.abspath(output_path)
    except Exception as e:
        logging.warning(f"Failed to download image from {url}: {e}")
        return None


def run(query: str, limit: int = 10, download_dir: str = None) -> List[Dict[str, Any]]:
    load_env()
    # 1. Try Google Image Search CSE first
    results = search_google_images(query, limit)
    if not results:
        # 2. Fallback to Bing Keyless Image Search
        results = search_bing_images(query, limit)

    # 3. Perform downloading if download_dir is specified
    if download_dir and results:
        for item in results:
            url = item.get("image_url")
            if url:
                saved_path = download_image_file(url, download_dir)
                item["saved_path"] = saved_path

    return results


if __name__ == "__main__":
    # 1. Parse JSON input if passed by aichat's tool dispatcher
    if len(sys.argv) > 1 and (
        sys.argv[1].startswith("{") or sys.argv[1].startswith("[")
    ):
        try:
            kwargs = json.loads(sys.argv[1])
            query_val = kwargs.get("query")
            limit_val = kwargs.get("limit", 10)
            download_val = kwargs.get("download_dir") or kwargs.get("download_path")
            if not query_val:
                print(json.dumps([{"error": "Query is required"}]))
                sys.exit(1)
            print(json.dumps(run(query_val, limit_val, download_val), indent=2))
            sys.exit(0)
        except Exception as err:
            print(json.dumps([{"error": f"JSON argument parse error: {err}"}]))
            sys.exit(1)

    # 2. Fallback to standard CLI arguments
    parser = argparse.ArgumentParser(description="Image search tool")
    parser.add_argument("--query", required=True)
    parser.add_argument("--limit", type=int, default=10)
    parser.add_argument("--download-dir")
    args = parser.parse_args()
    print(json.dumps(run(args.query, args.limit, args.download_dir), indent=2))
