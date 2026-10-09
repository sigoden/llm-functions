#!/usr/bin/env python3
# ==============================================================================
# vsearch_tool.py — Pyrmethus Video Search Tool v3.3.1-ASCENDED
# Structural alignment with edit.py + caching & sandbox validation
# ==============================================================================

# @describe Scrapes and searches video links, thumbnails, and metadata across multiple video platforms.
#
# @option --query! <TEXT>               Search query (required)
# @option --engine <ENGINE>             Engine: pexels, yahoo_video, dailymotion, bing, xnxx, xvideos, pornhub, pornhub_gifs, xhamster, spankbang, redtube, thumbzilla, eporner, beeg, youjizz, motherless, hqporner, txxx, zebra_girls (default: pexels)
# @option --limit <NUM>                 Maximum results to fetch (default: 20)
# @option --page <NUM>                  Starting page number (default: 1)
# @option --output-format <FMT>         Output format: json, html, csv (default: json)
# @option --proxy <URL>                 Proxy server URL (http/https/socks5)
# @option --timeout <SEC>               HTTP timeout in seconds (default: 15)
# @option --custom-ua <UA>              Override the User-Agent header string
# @option --cache-dir <DIR>             Custom cache folder location
# @option --cache-ttl <SEC>             Result cache TTL in seconds (default: 3600)
# @option --exclude-words <CSV>         Comma-separated list of words to exclude from video titles
# @option --require-words <CSV>         Comma-separated list of words required to be in video titles
# @flag   --download-thumbs             Automatically download video thumbnails locally for offline reports
# @flag   --no-thumbs                   Skip thumbnail processing
# @flag   --use-cache                   Enable result caching for identical queries
# @flag   --no-color                    Disable ANSI color output
# @flag   --no-verify                   Disable SSL verification for requests
# @flag   --open                        Automatically open generated HTML or CSV report
# @flag   --share                       Automatically trigger termux-share on report path
# @flag   --verbose                     Enable detailed debug logging to stderr
#
# @env LLM_OUTPUT=/dev/stdout           Output path for LLM integration
# ==============================================================================

from __future__ import annotations

import argparse
import concurrent.futures
import csv
import hashlib
import html
import json
import logging
import os
import random
import re
import shutil
import signal
import subprocess
import sys
import tempfile
import time
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Literal, Optional
from urllib.parse import quote_plus, urljoin, urlparse

# Guard third-party imports
try:
    import requests
    from requests.adapters import HTTPAdapter
    from requests.packages.urllib3.util.retry import Retry  # type: ignore
except ImportError:
    print(
        "Error: 'requests' module is required. Install with: pip install requests",
        file=sys.stderr,
    )
    sys.exit(1)

try:
    from bs4 import BeautifulSoup
except ImportError:
    print(
        "Error: 'beautifulsoup4' module is required. Install with: pip install beautifulsoup4",
        file=sys.stderr,
    )
    sys.exit(1)

__version__ = "3.3.2"
__all__ = [
    "ToolCache",
    "ToolError",
    "__version__",
    "execute_tool",
    "get_agent_var",
    "get_builtin_var",
    "get_execution_context",
    "run",
]

EXIT_SUCCESS = 0
EXIT_ERROR = 1
EXIT_FILE_NOT_FOUND = 2
EXIT_TIMEOUT = 124
EXIT_PERMISSION_DENIED = 126
EXIT_INVALID_INPUT = 127
EXIT_INTERRUPTED = 130

NEON_CYAN = "\033[38;5;51m"
NEON_GREEN = "\033[38;5;46m"
NEON_RED = "\033[38;5;196m"
NEON_YELLOW = "\033[38;5;226m"
NEON_PURPLE = "\033[38;5;129m"
NEON_PINK = "\033[38;5;198m"
RESET = "\033[0m"
BOLD = "\033[1m"
DIM = "\033[2m"

_ANSI_RE = re.compile(r"\x1b(?:[@-Z\\-_]|\[[0-?]*[ -/]*[@-~])|\033\[[0-9;?]*[a-zA-Z]")

REALISTIC_USER_AGENTS = [
    # Chrome (Windows/Mac/Linux)
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/127.0.0.0 Safari/537.36",
    # Firefox
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:131.0) Gecko/20100101 Firefox/131.0",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10.15; rv:131.0) Gecko/20100101 Firefox/131.0",
    "Mozilla/5.0 (X11; Linux x86_64; rv:131.0) Gecko/20100101 Firefox/131.0",
    # Safari
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/18.1 Safari/605.1.15",
    # Edge
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36 Edg/128.0.0.0",
    # Mobile
    "Mozilla/5.0 (iPhone; CPU iPhone OS 17_6_1 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.6 Mobile/15E148 Safari/604.1",
    "Mozilla/5.0 (Linux; Android 14; Pixel 8) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128.0.0.0 Mobile Safari/537.36",
]


def _strip_ansi(text: str) -> str:
    return _ANSI_RE.sub("", text)


def _is_tty() -> bool:
    return sys.stderr.isatty() and os.environ.get("TERM", "").lower() not in (
        "dumb",
        "",
    )


def _cprint(
    text: str, file: Any = None, no_color: bool = False, end: str = "\n"
) -> None:
    target = file or sys.stderr
    if no_color or not _is_tty():
        text = _strip_ansi(text)
    print(text, file=target, flush=True, end=end)


def _validate_sandbox(path: Path) -> bool:
    """Validate that the given path resides within allowed sandbox locations (Home, Temp, Termux prefix)."""
    allowed_roots: list[Path] = [
        Path.home().resolve(),
        Path("/tmp").resolve(),
        Path(tempfile.gettempdir()).resolve(),
    ]

    prefix = os.environ.get("PREFIX")
    if prefix:
        allowed_roots.append(Path(prefix).resolve())
        allowed_roots.append((Path(prefix) / "tmp").resolve())

    tmpdir = os.environ.get("TMPDIR")
    if tmpdir:
        allowed_roots.append(Path(tmpdir).resolve())

    llm_root = os.environ.get("LLM_ROOT_DIR")
    if llm_root:
        allowed_roots.append(Path(llm_root).resolve())

    if Path("/data/data/com.termux").exists():
        allowed_roots.append(Path("/data/data/com.termux").resolve())

    try:
        resolved = path.resolve()
        s = str(resolved)
        return any(s.startswith(str(root)) for root in allowed_roots)
    except OSError:
        return False


class ToolError(Exception):
    def __init__(self, message: str, exit_code: int = EXIT_ERROR) -> None:
        super().__init__(message)
        self.message = message
        self.exit_code = exit_code


class ToolJSONEncoder(json.JSONEncoder):
    def default(self, obj: Any) -> Any:
        if isinstance(obj, Path):
            return str(obj)
        if isinstance(obj, datetime):
            return obj.isoformat()
        if isinstance(obj, timedelta):
            return obj.total_seconds()
        if isinstance(obj, bytes):
            return obj.decode("utf-8", errors="replace")
        if isinstance(obj, (set, frozenset)):
            return list(obj)
        return super().default(obj)


class ToolCache:
    """Caching subsystem with custom directories and TTL bounds."""

    def __init__(self, cache_dir: Optional[str] = None) -> None:
        if cache_dir:
            self.cache_dir = Path(cache_dir).expanduser().resolve()
        else:
            self.cache_dir = Path.home() / ".config" / "aichat" / "cache"
        self.cache_dir.mkdir(parents=True, exist_ok=True)

    def _make_key(self, key_data: str) -> str:
        return hashlib.sha256(key_data.encode("utf-8")).hexdigest()

    def get(self, key_data: str, ttl_seconds: int = 3600) -> Optional[Any]:
        key = self._make_key(key_data)
        cache_file = self.cache_dir / f"{key}.json"
        if cache_file.exists():
            try:
                mtime = cache_file.stat().st_mtime
                if time.time() - mtime < ttl_seconds:
                    with open(cache_file, encoding="utf-8") as f:
                        return json.load(f)
            except Exception:
                pass
        return None

    def set(self, key_data: str, value: Any) -> None:
        key = self._make_key(key_data)
        cache_file = self.cache_dir / f"{key}.json"
        if _validate_sandbox(cache_file):
            try:
                with open(cache_file, "w", encoding="utf-8") as f:
                    json.dump(value, f, cls=ToolJSONEncoder)
            except Exception:
                pass


class GracefulShutdown:
    def __init__(self) -> None:
        self.old_sigint = signal.signal(signal.SIGINT, self._handle_signal)
        self.old_sigterm = signal.signal(signal.SIGTERM, self._handle_signal)

    def _handle_signal(self, signum: int, frame: Any) -> None:
        sys.stderr.write(
            f"\n[INFO] Operation interrupted (Signal {signum}). Cleaning up...\n"
        )
        sys.exit(EXIT_INTERRUPTED)

    def restore(self) -> None:
        signal.signal(signal.SIGINT, self.old_sigint)
        signal.signal(signal.SIGTERM, self.old_sigterm)


def print_human_readable_ui(data: dict[str, Any], no_color: bool = False) -> None:
    if not _is_tty() or no_color:
        return
    success = data.get("success", False)
    status_color = NEON_GREEN if success else NEON_RED
    status_symbol = "✓" if success else "✗"
    status_text = "SUCCESS" if success else "FAILED"

    box_w = 68
    border = "─" * box_w

    _cprint(f"{NEON_PURPLE}╭{border}╮{RESET}")
    _cprint(
        f"{NEON_PURPLE}│{RESET} {NEON_PINK}⚡ [VIDEO SEARCH ENGINE v{__version__}]{RESET} {status_color}{BOLD}{status_symbol} {status_text}{RESET}"
    )
    _cprint(f"{NEON_PURPLE}├{border}┤{RESET}")
    _cprint(
        f"{NEON_PURPLE}│{RESET} {NEON_CYAN}Query:{RESET}    {data.get('query', 'N/A')}"
    )
    _cprint(
        f"{NEON_PURPLE}│{RESET} {NEON_CYAN}Engine:{RESET}   {data.get('engine', 'N/A')}"
    )
    _cprint(
        f"{NEON_PURPLE}│{RESET} {NEON_CYAN}Count:{RESET}    {NEON_YELLOW}{data.get('count', 0)}{RESET}"
    )
    _cprint(
        f"{NEON_PURPLE}│{RESET} {NEON_CYAN}Cached:{RESET}   {NEON_YELLOW}{data.get('cached', False)}{RESET}"
    )
    _cprint(
        f"{NEON_PURPLE}│{RESET} {NEON_CYAN}Duration:{RESET} {DIM}{data.get('duration_ms', 0)}ms{RESET}"
    )

    if data.get("downloaded_thumbs_count"):
        _cprint(
            f"{NEON_PURPLE}│{RESET} {NEON_CYAN}Downloaded Thumbs:{RESET} {NEON_GREEN}{data['downloaded_thumbs_count']}{RESET}"
        )

    if data.get("html_file"):
        _cprint(
            f"{NEON_PURPLE}│{RESET} {NEON_CYAN}HTML File:{RESET}{NEON_GREEN} {data['html_file']}{RESET}"
        )
    if data.get("csv_file"):
        _cprint(
            f"{NEON_PURPLE}│{RESET} {NEON_CYAN}CSV File:{RESET} {NEON_GREEN} {data['csv_file']}{RESET}"
        )

    if not success and "error" in data:
        _cprint(f"{NEON_PURPLE}├{border}┤{RESET}")
        _cprint(f"{NEON_PURPLE}│{RESET} {NEON_RED}Error:{RESET}    {data['error']}")

    results = data.get("results", [])
    if results:
        _cprint(f"{NEON_PURPLE}├{border}┤{RESET}")
        _cprint(
            f"{NEON_PURPLE}│{RESET} {BOLD}Results (Top {min(len(results), 5)}):{RESET}"
        )
        for i, item in enumerate(results[:5], 1):
            title = item.get("title", "Untitled")[:45]
            link = item.get("link", "#")
            dur = item.get("time", "")
            time_str = f" [{dur}]" if dur and dur != "N/A" else ""
            local_str = (
                f" {NEON_GREEN}[Offline Thumb]{RESET}"
                if item.get("local_thumb")
                else ""
            )
            _cprint(
                f"{NEON_PURPLE}│{RESET}   {NEON_CYAN}{i}.{RESET} {title}{DIM}{time_str}{RESET}{local_str}"
            )
            _cprint(f"{NEON_PURPLE}│{RESET}      {DIM}↳ {link}{RESET}")
        if len(results) > 5:
            _cprint(
                f"{NEON_PURPLE}│{RESET}   {DIM}... and {len(results) - 5} more results{RESET}"
            )

    _cprint(f"{NEON_PURPLE}╰{border}╯{RESET}")


def get_agent_var(name: str, default: str = "") -> str:
    env_name = f"LLM_AGENT_VAR_{name.upper()}"
    return os.environ.get(env_name, default)


def get_builtin_var(name: str) -> Optional[str]:
    env_name = f"LLM_AGENT_VAR_{name}"
    return os.environ.get(env_name)


def get_execution_context() -> dict[str, Any]:
    termux_prefix = os.environ.get("PREFIX", "")
    return {
        "tool_name": os.environ.get("LLM_TOOL_NAME", "vsearch_tool"),
        "cache_dir": os.environ.get("LLM_TOOL_CACHE_DIR"),
        "output_path": os.environ.get("LLM_OUTPUT"),
        "cwd": get_builtin_var("__cwd__") or os.getcwd(),
        "termux_prefix": termux_prefix,
        "is_termux": "com.termux" in termux_prefix
        or Path("/data/data/com.termux").exists(),
        "os": get_builtin_var("__os__") or sys.platform,
    }


def get_headers(custom_ua: Optional[str] = None) -> dict[str, str]:
    ua = custom_ua or random.choice(REALISTIC_USER_AGENTS)
    return {
        "User-Agent": ua,
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.9",
        "Accept-Encoding": "gzip, deflate",
        "Connection": "keep-alive",
        "Upgrade-Insecure-Requests": "1",
    }


ENGINE_MAP: dict[str, dict[str, Any]] = {
    "pexels": {
        "url": "https://www.pexels.com",
        "search_path": "/search/videos/{query}/?page={page}",
        "video_item_selector": "article.MediaCard_card__6_MG7, article[data-testid='video-card'], div.MediaCard_card__6_MG7",
        "link_selector": "a.MediaCard_content__kA4yf, a[data-testid='video-card-link'], a",
        "title_selector": "a.MediaCard_content__kA4yf, img",
        "title_attribute": "alt",
        "img_selector": "img",
        # Cloudflare is friendlier to requests that look like in-site navigation.
        "extra_headers": {
            "Referer": "https://www.pexels.com/",
            "Sec-Fetch-Dest": "document",
            "Sec-Fetch-Mode": "navigate",
            "Sec-Fetch-Site": "same-origin",
            "Sec-Fetch-User": "?1",
        },
    },
    "yahoo_video": {
        "url": "https://video.search.yahoo.com",
        "search_path": "/search/video?p={query}&b={page}",
        "video_item_selector": "li.tile, li.type-video, div.video-tile",
        "link_selector": "a.video-tile, a.tile-link, a",
        "link_attribute": "data-referenceurl",
        "title_selector": "p.tile-title, p.text-primary, p",
        "img_selector": "img.tile-image, img",
        "time_selector": "p.time, span.time",
    },
    "dailymotion": {
        "url": "https://www.dailymotion.com",
        "search_path": "/search/{query}",
        "video_item_selector": "div[data-testid='video-card'], div.video-card, article",
        "link_selector": "a[href*='/video/'], a",
        "title_selector": "span[title], div[title], h2, h3, .video-card__title",
        "title_attribute": "title",
        "img_selector": "img",
        "time_selector": "span.duration, .video-card__duration",
    },
    "bing": {
        "url": "https://www.bing.com",
        "search_path": "/videos/search?q={query}&first={page}",
        "video_item_selector": "div.mc_vtvc",
        "link_selector": "a.mc_vtvc_link",
        "title_selector": "div.mc_vtvc_title",
        "img_selector": "img.rms_img, img",
        "time_selector": "span.duration, .mc_bc_rc, .mc_vtvc_meta_row",
        "cookies": {"SRCHHPGUSR": "ADLT=OFF"},
        # mmeta JSON attr on the card carries the real video URL (murl),
        # thumbnail (turl), and page URL (pgurl) — far better than the
        # bing.com/videos/riverview redirect href.
        "meta_attribute": "mmeta",
        "meta_link_key": "murl",
        "meta_img_key": "turl",
    },
    "xnxx": {
        "url": "https://www.xnxx.com",
        "search_path": "/search/{query}/{page}/",
        "video_item_selector": "div.thumb-block, div.video-block, div.mozaique, div.video-item",
        "link_selector": "a.thumb, a.title-link, a[href*='/video-']",
        "title_selector": ".thumb-under a, .title a, .thumb-title a, h3 a",
        "img_selector": "img[data-src], img[data-lazy], img[data-thumb], img.lazy",
        "time_selector": ".duration, .video-duration, span.duration",
    },
    "xvideos": {
        "url": "https://www.xvideos.com",
        "search_path": "/?k={query}&p={page}",
        "video_item_selector": "div.thumb-block, div.mozaique, div.video-item",
        "link_selector": "a.thumb, a.title, a[href*='/video']",
        "title_selector": ".thumb-under a, .title a",
        "img_selector": "img[data-src], img[data-lazy], img[data-thumb], img.lazy",
        "time_selector": ".duration, .thumb-under .duration",
    },
    "pornhub": {
        "url": "https://www.pornhub.com",
        "search_path": "/video/search?search={query}&page={page}",
        "video_item_selector": "li.pcVideoListItem, .videoBox, .ph-video-block, div[data-vid]",
        "link_selector": "a.title, a[data-uuid], a[href*='/view_video.php']",
        "title_selector": "span.title a, .title a, .video-title",
        "img_selector": "img[data-mediabook], img[data-src], img.thumb_img, img",
        "time_selector": ".duration, span.video-duration",
    },
    "pornhub_gifs": {
        "url": "https://www.pornhub.com",
        "search_path": "/gifs/search?search={query}&page={page}",
        "video_item_selector": "li.gifVideoBlock, .gifVideoBlock",
        "link_selector": "a.gifLink, a",
        "title_selector": ".title, .gif-title",
        "img_selector": "video, img, .gif-image",
    },
    "xhamster": {
        "url": "https://xhamster.com",
        "search_path": "/search/{query}/{page}",
        "video_item_selector": "div.video-thumb, div.thumb-list__item, article.card",
        "link_selector": "a[data-role='thumb-link'], a.video-thumb-info__name, a[href*='/videos/']",
        "title_selector": "a.video-thumb-info__name, a.video-title, .title a",
        "img_selector": "img.thumb-image-container__image, img[data-src], img[data-lazy], img",
        "time_selector": ".thumb-image-container__duration, .duration",
        "cookies": {"xhamster_age_confirmed": "1"},
    },
    "spankbang": {
        "url": "https://spankbang.com",
        "search_path": "/s/{query}/{page}/",
        "video_item_selector": "div.video-item, div.v-it, .video-item",
        "link_selector": "a.n, a.thumb, a[href*='/video/']",
        "title_selector": "a.n, .title, .n",
        "img_selector": "img.lazy, img[data-src], img",
        "time_selector": "span.l, span.duration",
    },
    "redtube": {
        "url": "https://www.redtube.com",
        "search_path": "/?search={query}&page={page}",
        "video_item_selector": "div.videoblock_list, div.tm_video_block, li.video_item, div.video_block_wrapper",
        "link_selector": "a.tm_video_title, a.video_link, a[href*='/video-'], a",
        "title_selector": "a.tm_video_title, span.video_title, .video-title-text",
        "img_selector": "img[data-src], img[data-lazy], img[data-thumb], img.video_thumb_image, img",
        "time_selector": "span.duration, .duration, .video-properties",
        "cookies": {"showAgeDisclaimer": "0", "age_verified": "1"},
    },
    "thumbzilla": {
        "url": "https://www.thumbzilla.com",
        "search_path": "/search?q={query}&page={page}",
        "video_item_selector": ".video-thumb, div.video-card, div.tz-grid__item, div.video-box",
        "link_selector": "a.js-thumb, a.title, a.tm_video_link, a",
        "title_selector": "a.title, .video-title, .video-title-text",
        "img_selector": "img[data-src], img[data-lazy], img[data-thumb], img.thumb-image, img",
        "time_selector": ".duration, .video-duration",
    },
    "eporner": {
        "url": "https://www.eporner.com",
        "search_path": "/search/{query}/{page}/",
        "video_item_selector": "div.mb, div.jsVideoItemGrid, div.boxVideo",
        "link_selector": "p.mbtit a, a[data-vid], a.videoLink, a",
        "title_selector": "p.mbtit a, a.videoLink",
        "img_selector": "img[data-src], img[data-lazy], img[data-thumb], img.lazy, img",
        "time_selector": "span.mbtim, .duration",
        "cookies": {"age_verified": "1"},
    },
    "beeg": {
        "url": "https://beeg.com",
        "search_path": "/search?q={query}",
        "video_item_selector": "div.item, div.video-item, article",
        "link_selector": "a",
        "title_selector": ".title, h3, a",
        "img_selector": "img",
        "time_selector": ".duration",
    },
    "youjizz": {
        "url": "https://www.youjizz.com",
        "search_path": "/search/{query}-{page}.html",
        "video_item_selector": "div.video-thumb, div.item, div.video-item",
        "link_selector": "a.frame, a[href*='/videos/']",
        "title_selector": ".video-title a, h3.title, a.frame span",
        "img_selector": "img[data-src], img[data-lazy], img[data-thumb], img",
        "time_selector": "span.time, .duration",
    },
    "motherless": {
        "url": "https://motherless.com",
        "search_path": "/term/{query}?page={page}",
        "video_item_selector": "div.thumb-container, div.media-thumb",
        "link_selector": "a.img-container, a.title, a",
        "title_selector": "a.title, caption, .title",
        "img_selector": "img.static, img",
        "time_selector": "span.captions, .duration",
    },
    "hqporner": {
        "url": "https://hqporner.com",
        "search_path": "/?q={query}&p={page}",
        "video_item_selector": "div.video-item, article.box, div.box",
        "link_selector": "a.hd-link, a.image, a",
        "title_selector": "a.hd-link, .title",
        "img_selector": "img[data-src], img",
        "time_selector": "span.duration, .time",
    },
    "txxx": {
        "url": "https://txxx.com",
        "search_path": "/search/{query}/{page}/",
        "video_item_selector": "div.video-block, div.thumb-block",
        "link_selector": "a.thumb, a.title",
        "title_selector": ".title a, a.title",
        "img_selector": "img[data-src], img",
        "time_selector": ".duration",
    },
    "zebra_girls": {
        "url": "https://www.pornhub.com",
        "search_path": "/gifs/search?search=zebra+girls+strapon&page={page}",
        "video_item_selector": "li.gifVideoBlock, .gifVideoBlock",
        "link_selector": "a, .gif-link",
        "title_selector": ".title, .gif-title",
        "img_selector": "video, img, .gif-image",
        "specialized": True,
        "content_type": "strapon_lesbian",
    },
}


# Per-engine rate limiter (sliding window, simple & thread-safe)
_ENGINE_LAST_REQUEST: dict[str, float] = {}
_ENGINE_LOCK = __import__("threading").Lock()

# Per-engine minimum delay (seconds) — heavier/anti-bot sites get longer waits
ENGINE_RATE_LIMITS: dict[str, float] = {
    "pexels": 0.5,
    "yahoo_video": 1.0,
    "dailymotion": 1.0,
    "bing": 1.0,
    "xnxx": 2.0,
    "xvideos": 2.0,
    "pornhub": 2.5,
    "pornhub_gifs": 2.5,
    "xhamster": 2.0,
    "spankbang": 2.0,
    "redtube": 2.0,
    "thumbzilla": 2.0,
    "eporner": 2.0,
    "beeg": 2.5,
    "youjizz": 2.5,
    "motherless": 2.5,
    "hqporner": 2.5,
    "txxx": 2.0,
    "zebra_girls": 2.5,
}

DEFAULT_RATE_LIMIT = 1.5  # seconds for unknown engines


def _engine_rate_wait(engine: str) -> None:
    """Sleep if needed to respect per-engine rate limit."""
    delay = ENGINE_RATE_LIMITS.get(engine, DEFAULT_RATE_LIMIT)
    if delay <= 0:
        return
    with _ENGINE_LOCK:
        last = _ENGINE_LAST_REQUEST.get(engine, 0.0)
        now = time.monotonic()
        wait = delay - (now - last)
        if wait > 0:
            time.sleep(wait)
        _ENGINE_LAST_REQUEST[engine] = time.monotonic()


def _retry_request(
    session,
    method: str,
    url: str,
    *,
    max_retries: int = 3,
    backoff_factor: float = 1.0,
    timeout: int = 15,
    verify: bool = True,
    **kwargs,
) -> requests.Response:
    """HTTP request with exponential backoff and retry-aware 429/5xx handling."""
    last_exc: Optional[Exception] = None
    last_resp: Optional[requests.Response] = None

    for attempt in range(max_retries):
        try:
            resp = session.request(
                method, url, timeout=timeout, verify=verify, **kwargs
            )
            last_resp = resp

            # Respect Retry-After header on 429/503
            if resp.status_code in (429, 503):
                retry_after = resp.headers.get("Retry-After")
                if retry_after:
                    try:
                        wait_s = float(retry_after)
                    except ValueError:
                        wait_s = backoff_factor * (2**attempt)
                else:
                    wait_s = backoff_factor * (2**attempt)
                if attempt < max_retries - 1:
                    time.sleep(min(wait_s, 30))
                    continue

            if resp.status_code >= 500 and attempt < max_retries - 1:
                time.sleep(backoff_factor * (2**attempt))
                continue

            return resp
        except (
            requests.exceptions.ConnectionError,
            requests.exceptions.Timeout,
            requests.exceptions.ChunkedEncodingError,
        ) as e:
            last_exc = e
            if attempt < max_retries - 1:
                time.sleep(backoff_factor * (2**attempt))
                continue
            raise

    if last_resp is not None:
        return last_resp
    if last_exc is not None:
        raise last_exc
    raise ToolError(
        "Request failed after retries with no response or exception captured."
    )


def slugify(text: str) -> str:
    text = html.unescape(text or "")
    text = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "", text)
    text = re.sub(r"[^a-zA-Z0-9\s\-_.]", "", text)
    text = re.sub(r"\s+", "_", text.strip()).strip("._-")
    return text[:100] or "untitled"


def _slug_to_title(path: str) -> str:
    """Derive a human title from a URL slug like 'sunlight-seen-through-leaves-10395606'."""
    slug = path.rstrip("/").rsplit("/", 1)[-1]
    slug = re.sub(r"-\d+$", "", slug)
    slug = slug.replace("_", "-").replace("+", "-")
    words = slug.split("-")
    words = [w for w in words if w]
    if not words:
        return ""
    title = " ".join(words)
    return title[:1].upper() + title[1:] if title else ""


def extract_item(
    item: BeautifulSoup, cfg: dict, base_url: str
) -> Optional[dict[str, Any]]:
    try:
        # ── Structured metadata attribute (e.g. Bing mmeta JSON) ────────────
        meta: dict[str, Any] = {}
        meta_attr = cfg.get("meta_attribute", "")
        if meta_attr and item.has_attr(meta_attr):
            try:
                parsed = json.loads(item[meta_attr])
                if isinstance(parsed, dict):
                    meta = parsed
            except (json.JSONDecodeError, TypeError):
                meta = {}

        title = ""
        title_sel = cfg.get("title_selector", "")
        if title_sel:
            selectors = [s.strip() for s in title_sel.split(",") if s.strip()]
            for sel in selectors:
                el = item.select_one(sel)
                if el:
                    attr = cfg.get("title_attribute", "")
                    if attr and el.get(attr):
                        t = el[attr].strip()
                        if t:
                            title = t
                            break
                    for alt_attr in ["title", "alt"]:
                        if el.get(alt_attr):
                            t = el[alt_attr].strip()
                            if t:
                                title = t
                                break
                    if title:
                        break
                    t = el.get_text(strip=True)
                    if t:
                        title = t
                        break

        link = "#"
        link_sel = cfg.get("link_selector", "")
        if link_sel:
            selectors = [s.strip() for s in link_sel.split(",") if s.strip()]
            for sel in selectors:
                el = item.select_one(sel)
                if el:
                    link_attr = cfg.get("link_attribute", "href")
                    if el.has_attr(link_attr):
                        link = urljoin(base_url, el[link_attr])
                        if link != "#":
                            break

        # Prefer the real media URL from structured metadata over internal
        # redirect links (Bing riverview links are useless to consumers).
        meta_link_key = cfg.get("meta_link_key", "")
        if meta_link_key and meta.get(meta_link_key):
            candidate = str(meta[meta_link_key]).strip()
            if candidate.startswith("http"):
                link = candidate

        # Fallback: derive title from the URL slug (Pexels cards often have
        # empty img alt attributes but descriptive slugs).
        if not title and link and link != "#":
            title = _slug_to_title(urlparse(link).path)
        if not title:
            title = "Untitled"
        title = html.unescape(title)

        if link == "#" or len(title) < 2:
            return None

        img_url = None
        meta_img_key = cfg.get("meta_img_key", "")
        if meta_img_key and meta.get(meta_img_key):
            candidate = str(meta[meta_img_key]).strip()
            if candidate.startswith("http"):
                img_url = candidate
        img_sel = cfg.get("img_selector", "")
        if not img_url and img_sel:
            selectors = [s.strip() for s in img_sel.split(",") if s.strip()]
            for sel in selectors:
                el = item.select_one(sel)
                if el:
                    for attr in [
                        "data-src",
                        "data-src-hq",
                        "vrhdata",
                        "data-lazy",
                        "data-original",
                        "data-thumb",
                        "src",
                        "poster",
                    ]:
                        val = el.get(attr, "").strip()
                        if val and not val.startswith("data:"):
                            img_url = urljoin(base_url, val)
                            break
                    if img_url:
                        break

        time_str = "N/A"
        time_sel = cfg.get("time_selector", "")
        if time_sel:
            selectors = [s.strip() for s in time_sel.split(",") if s.strip()]
            for sel in selectors:
                el = item.select_one(sel)
                if el:
                    time_str = el.get_text(strip=True) or "N/A"
                    if time_str != "N/A":
                        break

        tags = []
        if cfg.get("specialized") and cfg.get("content_type") == "strapon_lesbian":
            tags = ["strapon", "lesbian", "interracial"]

        return {
            "title": html.escape(title[:200]),
            "link": link,
            "img_url": img_url or "",
            "time": time_str,
            "source": base_url,
            "content_tags": tags,
        }
    except Exception:
        return None


def execute_scrape(
    engine: str,
    query: str,
    limit: int = 20,
    page: int = 1,
    timeout: int = 15,
    proxy: Optional[str] = None,
    no_verify: bool = False,
    custom_ua: Optional[str] = None,
) -> list[dict[str, Any]]:
    cfg = ENGINE_MAP.get(engine)
    if not cfg:
        raise ToolError(
            f"Unsupported engine '{engine}'. Available engines: {', '.join(ENGINE_MAP.keys())}"
        )

    base_url = cfg["url"]
    session = requests.Session()
    retries = Retry(
        total=3, backoff_factor=1, status_forcelist=[429, 500, 502, 503, 504]
    )
    session.mount("http://", HTTPAdapter(max_retries=retries))
    session.mount("https://", HTTPAdapter(max_retries=retries))

    if proxy:
        session.proxies = {"http": proxy, "https": proxy}

    if cfg.get("cookies"):
        session.cookies.update(cfg["cookies"])

    if engine == "bing":
        session.cookies.update({"SRCHHPGUSR": "ADLT=OFF"})

    results: list[dict[str, Any]] = []
    seen_urls: set[str] = set()

    search_path = cfg["search_path"].format(query=quote_plus(query), page=page)
    url = urljoin(base_url, search_path)

    session.headers.update(get_headers(custom_ua))
    if cfg.get("extra_headers"):
        session.headers.update(cfg["extra_headers"])

    # Apply per-engine rate limiting
    _engine_rate_wait(engine)

    try:
        resp = _retry_request(
            session,
            "GET",
            url,
            max_retries=3,
            backoff_factor=1.0,
            timeout=timeout,
            verify=not no_verify,
        )
        resp.raise_for_status()
    except Exception as e:
        raise ToolError(f"HTTP request failed for {engine}: {e}", exit_code=EXIT_ERROR)

    soup = BeautifulSoup(resp.text, "html.parser")
    selectors = [s.strip() for s in cfg["video_item_selector"].split(",")]

    items = []
    for sel in selectors:
        items = soup.select(sel)
        if items:
            break

    for item in items:
        if len(results) >= limit:
            break
        data = extract_item(item, cfg, base_url)
        if data and data["link"] not in seen_urls:
            seen_urls.add(data["link"])
            results.append(data)

    return results


def download_thumbnails(
    results: list[dict[str, Any]],
    output_dir: Path,
    timeout: int = 10,
    proxy: Optional[str] = None,
    no_verify: bool = False,
    custom_ua: Optional[str] = None,
) -> int:
    """Download video thumbnails in parallel and attach 'local_thumb' path to result dicts."""
    thumb_dir = output_dir / "thumbnails"
    thumb_dir.mkdir(parents=True, exist_ok=True)
    downloaded_count = 0

    def _fetch_one(item: dict[str, Any]) -> bool:
        url = item.get("img_url")
        if not url or not url.startswith("http"):
            return False
        try:
            ext = Path(urlparse(url).path).suffix.lower()
            if ext not in [".jpg", ".jpeg", ".png", ".webp", ".gif"]:
                ext = ".jpg"
            fname = f"thumb_{hashlib.md5(url.encode()).hexdigest()[:12]}{ext}"
            fpath = thumb_dir / fname
            if not fpath.exists():
                session = requests.Session()
                if proxy:
                    session.proxies = {"http": proxy, "https": proxy}
                session.headers.update(get_headers(custom_ua))
                resp = _retry_request(
                    session,
                    "GET",
                    url,
                    max_retries=2,
                    backoff_factor=0.5,
                    timeout=timeout,
                    verify=not no_verify,
                )
                if resp.status_code == 200:
                    fpath.write_bytes(resp.content)
            if fpath.exists():
                item["local_thumb"] = str(fpath.resolve())
                return True
        except Exception:
            pass
        return False

    with concurrent.futures.ThreadPoolExecutor(max_workers=8) as executor:
        futures = [executor.submit(_fetch_one, item) for item in results]
        for f in concurrent.futures.as_completed(futures):
            if f.result():
                downloaded_count += 1

    return downloaded_count


def generate_html_report(
    query: str, engine: str, results: list[dict[str, Any]], out_dir: Path
) -> str:
    out_dir.mkdir(parents=True, exist_ok=True)
    filename = (
        f"{engine}_{slugify(query)}_{datetime.now().strftime('%Y%m%d_%H%M%S')}.html"
    )
    filepath = out_dir / filename

    if not _validate_sandbox(filepath):
        raise ToolError("Output file path lies outside allowed sandbox.")

    cards_html = []
    for item in results:
        local_thumb = item.get("local_thumb")
        if local_thumb and Path(local_thumb).exists():
            thumb = Path(local_thumb).as_uri()
        else:
            thumb = (
                item.get("img_url")
                or "https://via.placeholder.com/320x200?text=No+Thumbnail"
            )

        cards_html.append(f"""
        <div class="card">
            <a href="{item["link"]}" target="_blank">
                <img src="{thumb}" alt="{item["title"]}" loading="lazy" onError="this.src='https://via.placeholder.com/320x200?text=Image+Error';">
            </a>
            <div class="info">
                <a class="title" href="{item["link"]}" target="_blank">{item["title"]}</a>
                <div class="meta">⏱️ {item.get("time", "N/A")}</div>
            </div>
        </div>
        """)

    html_content = f"""<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <title>{html.escape(query)} - {engine.title()} Results</title>
    <style>
        body {{ font-family: system-ui, sans-serif; background: #0a0a1a; color: #fff; margin: 0; padding: 2rem; }}
        h1 {{ text-align: center; color: #00d4ff; }}
        .grid {{ display: grid; grid-template-columns: repeat(auto-fill, minmax(280px, 1fr)); gap: 1.5rem; margin-top: 2rem; }}
        .card {{ background: #16213e; border-radius: 12px; overflow: hidden; border: 1px solid #2a2a3e; transition: transform .2s; }}
        .card:hover {{ transform: translateY(-5px); border-color: #00d4ff; }}
        .card img {{ width: 100%; height: 180px; object-fit: cover; }}
        .info {{ padding: 1rem; }}
        .title {{ color: #fff; text-decoration: none; font-weight: bold; display: block; margin-bottom: .5rem; }}
        .title:hover {{ color: #00d4ff; }}
        .meta {{ color: #a0a0a0; font-size: 0.85rem; }}
    </style>
</head>
<body>
    <h1>Search Results: {html.escape(query)} ({engine.title()})</h1>
    <div class="grid">
        {"".join(cards_html)}
    </div>
</body>
</html>"""

    filepath.write_text(html_content, encoding="utf-8")
    return str(filepath)


def generate_csv_report(
    query: str, engine: str, results: list[dict[str, Any]], out_dir: Path
) -> str:
    out_dir.mkdir(parents=True, exist_ok=True)
    filename = (
        f"{engine}_{slugify(query)}_{datetime.now().strftime('%Y%m%d_%H%M%S')}.csv"
    )
    filepath = out_dir / filename

    if not _validate_sandbox(filepath):
        raise ToolError("Output file path lies outside allowed sandbox.")

    with open(filepath, "w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(
            [
                "Title",
                "Link",
                "Thumbnail URL",
                "Local Thumbnail Path",
                "Duration",
                "Source",
            ]
        )
        for item in results:
            writer.writerow(
                [
                    item.get("title"),
                    item.get("link"),
                    item.get("img_url"),
                    item.get("local_thumb", ""),
                    item.get("time"),
                    item.get("source"),
                ]
            )
    return str(filepath)


def execute_tool(
    query: str,
    engine: str = "pexels",
    limit: Optional[int] = 20,
    page: Optional[int] = 1,
    output_format: str = "json",
    proxy: Optional[str] = None,
    timeout: int = 15,
    download_thumbs: bool = False,
    no_thumbs: bool = False,
    use_cache: bool = False,
    no_color: bool = False,
    verbose: bool = False,
    no_verify: bool = False,
    custom_ua: Optional[str] = None,
    cache_dir: Optional[str] = None,
    cache_ttl: int = 3600,
    exclude_words: Optional[str] = None,
    require_words: Optional[str] = None,
    output_dir: Optional[str] = None,
) -> dict[str, Any]:
    start_time = time.monotonic()

    if verbose:
        logging.basicConfig(level=logging.DEBUG, format="[DEBUG] %(message)s")
        logging.debug(f"Starting execution with query: {query}, engine: {engine}")

    if not query or not query.strip():
        return {
            "success": False,
            "error": "Query string cannot be empty.",
            "exit_code": EXIT_INVALID_INPUT,
            "duration_ms": 0.0,
        }

    limit_val = limit if (limit is not None and limit > 0) else 20
    page_val = page if (page is not None and page > 0) else 1
    engine_val = engine.lower().strip()

    cache = ToolCache(cache_dir)
    cache_key = (
        f"{query}:{engine_val}:{limit_val}:{page_val}:{output_format}:{download_thumbs}"
    )
    if use_cache:
        cached_result = cache.get(cache_key, cache_ttl)
        if cached_result is not None:
            cached_result["cached"] = True
            return cached_result

    shutdown = GracefulShutdown()

    try:
        results = execute_scrape(
            engine=engine_val,
            query=query,
            limit=limit_val,
            page=page_val,
            timeout=timeout,
            proxy=proxy,
            no_verify=no_verify,
            custom_ua=custom_ua,
        )

        if exclude_words:
            excludes = [
                w.strip().lower() for w in exclude_words.split(",") if w.strip()
            ]
            results = [
                item
                for item in results
                if not any(w in item["title"].lower() for w in excludes)
            ]

        if require_words:
            requires = [
                w.strip().lower() for w in require_words.split(",") if w.strip()
            ]
            results = [
                item
                for item in results
                if all(w in item["title"].lower() for w in requires)
            ]

        if no_thumbs:
            for item in results:
                item["img_url"] = ""

        if output_dir:
            report_dir = Path(output_dir).expanduser().resolve()
        else:
            report_dir = Path.home() / "vsearch_results"

        if not _validate_sandbox(report_dir):
            # Fall back to default sandbox-safe location if user-provided path is unsafe
            report_dir = Path.home() / "vsearch_results"

        downloaded_count = 0

        if download_thumbs and not no_thumbs:
            downloaded_count = download_thumbnails(
                results=results,
                output_dir=report_dir,
                timeout=timeout,
                proxy=proxy,
                no_verify=no_verify,
                custom_ua=custom_ua,
            )

        html_file = None
        csv_file = None
        if output_format.lower() == "html":
            html_file = generate_html_report(query, engine_val, results, report_dir)
        elif output_format.lower() == "csv":
            csv_file = generate_csv_report(query, engine_val, results, report_dir)

        duration_ms = round((time.monotonic() - start_time) * 1000, 2)

        result_payload = {
            "success": True,
            "query": query,
            "engine": engine_val,
            "count": len(results),
            "downloaded_thumbs_count": downloaded_count,
            "results": results,
            "html_file": html_file,
            "csv_file": csv_file,
            "context": get_execution_context(),
            "cached": False,
            "duration_ms": duration_ms,
            "exit_code": EXIT_SUCCESS,
        }

        if use_cache:
            cache.set(cache_key, result_payload)

        return result_payload

    except ToolError as te:
        duration_ms = round((time.monotonic() - start_time) * 1000, 2)
        return {
            "success": False,
            "error": te.message,
            "exit_code": te.exit_code,
            "duration_ms": duration_ms,
        }
    except Exception as exc:
        duration_ms = round((time.monotonic() - start_time) * 1000, 2)
        return {
            "success": False,
            "error": f"Scraper error: {exc}",
            "exit_code": EXIT_ERROR,
            "duration_ms": duration_ms,
        }
    finally:
        shutdown.restore()


def write_llm_output(data: dict[str, Any]) -> None:
    """Format and write JSON payload to LLM_OUTPUT target safely."""
    out_path = os.environ.get("LLM_OUTPUT", "/dev/stdout")
    json_payload = (
        json.dumps(data, indent=2, ensure_ascii=False, cls=ToolJSONEncoder) + "\n"
    )

    direct_targets = {"/dev/stdout", "/dev/fd/1", "-"}
    if out_path in direct_targets:
        sys.stdout.write(json_payload)
        sys.stdout.flush()
    else:
        out_file_path = Path(out_path).expanduser().resolve()
        if not _validate_sandbox(out_file_path):
            sys.stderr.write(
                f"Error: Output target '{out_file_path}' lies outside sandbox.\n"
            )
            sys.stdout.write(json_payload)
            sys.stdout.flush()
            return

        try:
            out_file_path.parent.mkdir(parents=True, exist_ok=True)
            with open(out_file_path, "a", encoding="utf-8") as fp:
                fp.write(json_payload)
        except OSError as err:
            sys.stderr.write(f"Failed writing to LLM_OUTPUT '{out_path}': {err}\n")
            sys.stdout.write(json_payload)
            sys.stdout.flush()


def open_report(target_report: str) -> None:
    """Open report path using termux-open, xdg-open, or macOS open."""
    if shutil.which("termux-open"):
        subprocess.run(["termux-open", target_report])
    elif shutil.which("xdg-open"):
        subprocess.run(["xdg-open", target_report])
    elif sys.platform == "darwin":
        subprocess.run(["open", target_report])


def run(
    query: str,
    engine: str = "pexels",
    limit: Optional[int] = 20,
    page: Optional[int] = 1,
    output_format: Literal["json", "html", "csv"] = "json",
    proxy: Optional[str] = None,
    timeout: int = 15,
    download_thumbs: bool = False,
    no_thumbs: bool = False,
    use_cache: bool = False,
    no_color: bool = False,
    verbose: bool = False,
    no_verify: bool = False,
    custom_ua: Optional[str] = None,
    cache_dir: Optional[str] = None,
    cache_ttl: int = 3600,
    exclude_words: Optional[str] = None,
    require_words: Optional[str] = None,
    open: bool = False,
    share: bool = False,
    output_dir: Optional[str] = None,
) -> None:
    """Execute video search scraper across supported platforms."""
    result = execute_tool(
        query=query,
        engine=engine,
        limit=limit,
        page=page,
        output_format=output_format,
        proxy=proxy,
        timeout=timeout,
        download_thumbs=download_thumbs,
        no_thumbs=no_thumbs,
        use_cache=use_cache,
        no_color=no_color,
        verbose=verbose,
        no_verify=no_verify,
        custom_ua=custom_ua,
        cache_dir=cache_dir,
        cache_ttl=cache_ttl,
        exclude_words=exclude_words,
        require_words=require_words,
        output_dir=output_dir,
    )

    print_human_readable_ui(result, no_color=no_color)
    write_llm_output(result)

    target_report = result.get("html_file") or result.get("csv_file")
    if open and target_report:
        open_report(target_report)

    if share and target_report and shutil.which("termux-share"):
        subprocess.run(["termux-share", target_report])


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="vsearch_tool.py",
        description=f"AIChat Video Search Tool v{__version__}",
    )
    parser.add_argument(
        "--query",
        "-q",
        required=True,
        metavar="TEXT",
        help="Search query terms (required)",
    )
    parser.add_argument(
        "--engine",
        "-e",
        default="pexels",
        choices=list(ENGINE_MAP.keys()),
        help="Video search engine (default: pexels)",
    )
    parser.add_argument(
        "--limit",
        "-l",
        type=int,
        default=20,
        help="Max results to return (default: 20)",
    )
    parser.add_argument(
        "--page",
        "-p",
        type=int,
        default=1,
        help="Starting page number (default: 1)",
    )
    parser.add_argument(
        "--output-format",
        "-o",
        dest="output_format",
        choices=["json", "html", "csv"],
        default="json",
        help="Output format (default: json)",
    )
    parser.add_argument(
        "--proxy",
        metavar="URL",
        help="Proxy server URL",
    )
    parser.add_argument(
        "--timeout",
        type=int,
        default=15,
        help="HTTP timeout in seconds (default: 15)",
    )
    parser.add_argument(
        "--download-thumbs",
        action="store_true",
        dest="download_thumbs",
        help="Auto download thumbnail images locally for offline access",
    )
    parser.add_argument(
        "--no-thumbs",
        action="store_true",
        dest="no_thumbs",
        help="Skip thumbnail image extraction",
    )
    parser.add_argument(
        "--use-cache",
        action="store_true",
        dest="use_cache",
        help="Enable result caching",
    )
    parser.add_argument(
        "--no-color",
        action="store_true",
        dest="no_color",
        help="Disable ANSI color output",
    )
    parser.add_argument(
        "--no-verify",
        action="store_true",
        dest="no_verify",
        help="Disable SSL verification",
    )
    parser.add_argument(
        "--custom-ua",
        dest="custom_ua",
        help="Override request User-Agent header",
    )
    parser.add_argument(
        "--cache-dir",
        dest="cache_dir",
        help="Custom query cache directory",
    )
    parser.add_argument(
        "--cache-ttl",
        type=int,
        default=3600,
        dest="cache_ttl",
        help="Cache TTL in seconds (default: 3600)",
    )
    parser.add_argument(
        "--exclude-words",
        dest="exclude_words",
        help="Comma-separated words to exclude from title matches",
    )
    parser.add_argument(
        "--require-words",
        dest="require_words",
        help="Comma-separated words to require in title matches",
    )
    parser.add_argument(
        "--open",
        action="store_true",
        help="Automatically open output reports",
    )
    parser.add_argument(
        "--share",
        action="store_true",
        help="Share output reports using termux-share",
    )
    parser.add_argument(
        "--verbose",
        "-v",
        action="store_true",
        help="Enable debug logging",
    )
    parser.add_argument(
        "--output-dir",
        dest="output_dir",
        help="Directory for HTML/CSV reports and thumbnails (default: ~/vsearch_results)",
    )
    return parser


if __name__ == "__main__":
    args = _build_parser().parse_args()
    res = execute_tool(
        query=args.query,
        engine=args.engine,
        limit=args.limit,
        page=args.page,
        output_format=args.output_format,
        proxy=args.proxy,
        timeout=args.timeout,
        download_thumbs=args.download_thumbs,
        no_thumbs=args.no_thumbs,
        use_cache=args.use_cache,
        no_color=args.no_color,
        verbose=args.verbose,
        no_verify=args.no_verify,
        custom_ua=args.custom_ua,
        cache_dir=args.cache_dir,
        cache_ttl=args.cache_ttl,
        exclude_words=args.exclude_words,
        require_words=args.require_words,
        output_dir=args.output_dir,
    )

    print_human_readable_ui(res, no_color=args.no_color)
    write_llm_output(res)

    target_report = res.get("html_file") or res.get("csv_file")
    if args.open and target_report:
        open_report(target_report)

    if args.share and target_report and shutil.which("termux-share"):
        subprocess.run(["termux-share", target_report])

    sys.exit(res.get("exit_code", EXIT_SUCCESS))
