#!/usr/bin/env python3
# ==============================================================================
# brave_intel.py — Pyrmethus AIChat Tool: Brave Search Engine v2.7.0
# argc/aichat compatible · Human-Readable Box UI · Concurrent Downloads
#
# @describe Performs Brave Search queries, parses multi-tier results, and downloads pages concurrently.
#
# @meta require-tools aichat
#
# @option --query! <STRING>              Search query term (required)
# @option --count <NUM>                  Number of search results (default: 10, max: 50)
# @option --offset <NUM>                 Pagination offset (default: 0)
# @option --timeout <NUM>                Request timeout in seconds (default: 15)
# @option --language <LANG>              Language code filter (e.g., en, es, zh-CN)
# @option --country <COUNTRY>            Country code filter (e.g., us, uk, jp)
# @option --safe-search <LEVEL>          Safe search level: safe, moderate, strict (default: moderate)
# @option --max-downloads <NUM>          Maximum concurrent download threads (default: 3)
# @option --output-format <FMT>          Output format for LLM integration: json, jsonl, csv (default: json)
# @option --log-file <PATH>              Path for debug log file
# @option --proxy <URL>                  HTTP/HTTPS proxy URL
# @option --headers <JSON>               Custom request headers as JSON string
# @option --max-retries <NUM>            Maximum retry attempts (default: 3)
# @option --filter-domains <COMMA>       Comma-separated domains to include
# @option --exclude-domains <COMMA>      Comma-separated domains to exclude
# @option --rate-limit <NUM>             Maximum requests per second (default: 0 = unlimited)
# @option --cache-dir <PATH>             Custom cache directory
# @option --download-dir <PATH>          Custom download directory
# @option --max-file-size <BYTES>        Maximum file size to download in bytes (default: 10485760)
# @flag   --download                     Download HTML of each search result
# @flag   --include-raw                  Include raw search HTML payload
# @flag   --no-color                     Disable ANSI color output
# @flag   --verbose                      Enable detailed debug logging
# @flag   --resume                       Resume/use cached downloaded files
#
# @env LLM_OUTPUT=/dev/stdout            Output path for LLM integration
# ==============================================================================

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import logging
import os
import random
import re
import signal
import subprocess
import sys
import time
import urllib.parse
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Set

try:
    import requests
    from bs4 import BeautifulSoup
except ImportError:
    print(
        "\033[31mError: Missing core dependencies. Please run: pip install requests beautifulsoup4\033[0m",
        file=sys.stderr,
    )
    sys.exit(127)

# Optional progress bar import
try:
    from tqdm import tqdm

    HAS_TQDM = True
except ImportError:
    HAS_TQDM = False

__version__ = "2.7.0"

# ==============================================================================
# SECTION 1: Exit Codes & Exception Models
# ==============================================================================

EXIT_SUCCESS = 0
EXIT_ERROR = 1
EXIT_NOT_FOUND = 404
EXIT_INVALID_INPUT = 127
EXIT_INTERRUPTED = 130


class ToolJSONEncoder(json.JSONEncoder):
    """Custom JSON encoder handling Path and datetime objects safely."""

    def default(self, obj: Any) -> Any:
        if isinstance(obj, Path):
            return str(obj)
        if isinstance(obj, datetime):
            return obj.isoformat()
        return super().default(obj)


class GracefulShutdown:
    """Signal handler for graceful cancellation of operations."""

    def __init__(self) -> None:
        self.interrupted = False
        self._old_sigint = signal.signal(signal.SIGINT, self._handle)
        self._old_sigterm = signal.signal(signal.SIGTERM, self._handle)

    def _handle(self, signum: int, frame: Any) -> None:
        self.interrupted = True

    def restore(self) -> None:
        signal.signal(signal.SIGINT, self._old_sigint)
        signal.signal(signal.SIGTERM, self._old_sigterm)


# ==============================================================================
# SECTION 2: UI Palette & Visual Helpers
# ==============================================================================

NEON_CYAN = "\033[38;5;51m"
NEON_GREEN = "\033[38;5;46m"
NEON_RED = "\033[38;5;196m"
NEON_YELLOW = "\033[38;5;226m"
NEON_PURPLE = "\033[38;5;129m"
NEON_PINK = "\033[38;5;198m"
RESET = "\033[0m"
BOLD = "\033[1m"
DIM = "\033[2m"

_ANSI_RE = re.compile(r"\033\[[0-9;]*[a-zA-Z]")


def _strip_ansi(text: str) -> str:
    """Remove ANSI escape sequences from string."""
    return _ANSI_RE.sub("", text)


def _is_tty() -> bool:
    """Check if stderr is connected to an interactive TTY."""
    return sys.stderr.isatty() and os.environ.get("TERM", "").lower() not in (
        "dumb",
        "",
    )


def _cprint(
    text: str, file: Any = None, no_color: bool = False, end: str = "\n"
) -> None:
    """Print formatted ANSI text to target stream."""
    target = file or sys.stderr
    if no_color or not _is_tty():
        text = _strip_ansi(text)
    print(text, file=target, flush=True, end=end)


def _human_bytes(num: int) -> str:
    """Format byte count into human-readable string."""
    for unit in ["B", "KB", "MB", "GB"]:
        if abs(num) < 1024.0:
            return f"{num:.1f}{unit}"
        num /= 1024.0
    return f"{num:.1f}TB"


def termux_toast(message: str, color: str = "green") -> None:
    """Trigger Termux:API toast notification if available."""
    try:
        subprocess.run(
            ["termux-toast", "-b", color, "-c", "white", message],
            check=False,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
    except Exception:
        pass


def get_agent_var(name: str, default: str = "") -> str:
    """Retrieve Pyrmethus user-defined agent environment variable."""
    return os.environ.get(f"LLM_AGENT_VAR_{name.upper()}", default)


def _get_progress_bar(
    iterable: Iterable[Any], total: int, desc: str, disable: bool
) -> Iterable[Any]:
    """Provide tqdm progress bar if available, else fallback cleanly."""
    if HAS_TQDM and not disable:
        return tqdm(
            iterable,
            total=total,
            desc=desc,
            unit="file",
            colour="cyan",
            disable=disable,
        )
    return iterable


def print_human_readable_ui(data: dict[str, Any], no_color: bool = False) -> None:
    """Render colorized box UI to stderr for terminal users."""
    if not _is_tty() or no_color:
        return

    success = data.get("success", False)
    status_color = NEON_GREEN if success else NEON_RED
    status_symbol = "✓" if success else "✗"
    status_text = "SUCCESS" if success else "FAILED"

    box_w = 64
    border = "─" * box_w

    _cprint(f"{NEON_PURPLE}╭{border}╮{RESET}")
    _cprint(
        f"{NEON_PURPLE}│{RESET} {NEON_PINK}⚡ [BRAVE SEARCH ENGINE v{__version__}]{RESET} "
        f"{status_color}{BOLD}{status_symbol} {status_text}{RESET}"
    )
    _cprint(f"{NEON_PURPLE}├{border}┤{RESET}")
    _cprint(
        f"{NEON_PURPLE}│{RESET} {NEON_CYAN}Query:{RESET}        {NEON_YELLOW}{data.get('query', 'N/A')}{RESET}"
    )
    _cprint(
        f"{NEON_PURPLE}│{RESET} {NEON_CYAN}Count:{RESET}        {data.get('count', 0)}"
    )
    _cprint(
        f"{NEON_PURPLE}│{RESET} {NEON_CYAN}Downloads:{RESET}    {NEON_GREEN}{data.get('download_count', 0)}{RESET}"
    )
    _cprint(
        f"{NEON_PURPLE}│{RESET} {NEON_CYAN}Duration:{RESET}     {DIM}{data.get('duration_ms', 0)}ms{RESET}"
    )

    if not success and "error" in data:
        _cprint(f"{NEON_PURPLE}├{border}┤{RESET}")
        _cprint(f"{NEON_PURPLE}│{RESET} {NEON_RED}Error:{RESET}        {data['error']}")

    results = data.get("results", [])
    if results:
        _cprint(f"{NEON_PURPLE}├{border}┤{RESET}")
        _cprint(
            f"{NEON_PURPLE}│{RESET} {BOLD}Search Results Preview ({len(results)}):{RESET}"
        )
        for res in results[:5]:
            title = res.get("title", "Untitled")[:48]
            url = res.get("url", "")[:50]
            _cprint(f"{NEON_PURPLE}│{RESET}   {NEON_CYAN}›{RESET} {BOLD}{title}{RESET}")
            _cprint(f"{NEON_PURPLE}│{RESET}     {DIM}{url}{RESET}")
        if len(results) > 5:
            _cprint(
                f"{NEON_PURPLE}│{RESET}   {DIM}... and {len(results) - 5} more results{RESET}"
            )

    _cprint(f"{NEON_PURPLE}╰{border}╯{RESET}")


# ==============================================================================
# SECTION 3: Brave Search Scraper Engine
# ==============================================================================

USER_AGENTS = [
    "Mozilla/5.0 (Linux; Android 13; Pixel 7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Mobile Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.2 Safari/605.1.15",
]

DEFAULT_CACHE_DIR = Path.home() / ".cache" / "brave_search"


def _get_default_download_dir() -> Path:
    """Resolve storage path with Termux SD card fallback awareness."""
    termux_storage = Path.home() / "storage" / "downloads" / "brave_search"
    if termux_storage.parent.exists():
        return termux_storage
    return Path.home() / "downloads" / "brave_search"


def _unwrap_brave_redirect(url: str) -> str:
    """Unwrap tracking/redirect links generated by Brave Search."""
    if "/g?r=" in url or "search.brave.com/g?" in url:
        parsed = urllib.parse.urlparse(url)
        qs = urllib.parse.parse_qs(parsed.query)
        if qs.get("r"):
            return qs["r"][0]
    return url


def _clean_snippet_text(text: str) -> str:
    """Sanitize snippet descriptions by removing dates and unwanted prefix tokens."""
    text = re.sub(
        r"^(Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)\s+\d{1,2},\s+\d{4}\s+—\s+",
        "",
        text,
        flags=re.IGNORECASE,
    )
    text = re.sub(
        r"^\d+\s+(hours?|days?|weeks?|months?)\s+ago\s+—\s+",
        "",
        text,
        flags=re.IGNORECASE,
    )
    return re.sub(r"\s+", " ", text).strip()


def _parse_custom_headers(headers_json: Optional[str]) -> Dict[str, str]:
    """Safely parse custom JSON string headers into a dictionary."""
    if not headers_json:
        return {}
    try:
        parsed = json.loads(headers_json)
        if isinstance(parsed, dict):
            return {str(k): str(v) for k, v in parsed.items()}
    except Exception:
        pass
    return {}


def _is_valid_url(
    target_url: str,
    seen_urls: Set[str],
    include_domains: Set[str],
    exclude_domains: Set[str],
) -> Optional[str]:
    """Validate, unwrap, and apply domain filters to result URLs."""
    if not target_url:
        return None

    unwrapped = _unwrap_brave_redirect(target_url)
    if unwrapped in seen_urls:
        return None

    parsed = urllib.parse.urlparse(unwrapped)
    if parsed.scheme not in ("http", "https"):
        return None

    domain = parsed.netloc.lower()

    # Exclude internal Brave Search URLs
    if "search.brave.com" in domain and parsed.path in (
        "/search",
        "/settings",
        "/privacy",
        "/images",
        "/g",
    ):
        return None

    if exclude_domains and any(
        domain == d or domain.endswith("." + d) for d in exclude_domains
    ):
        return None

    if include_domains and not any(
        domain == d or domain.endswith("." + d) for d in include_domains
    ):
        return None

    return unwrapped


class BraveSearchEngine:
    """Multi-tier scraping and parsing engine for Brave Search."""

    def __init__(
        self,
        timeout: int = 15,
        verbose: bool = False,
        max_retries: int = 3,
        proxy: Optional[str] = None,
        custom_headers: Optional[Dict[str, str]] = None,
        rate_limit: int = 0,
        cache_dir: Optional[Path] = None,
        download_dir: Optional[Path] = None,
        max_file_size: int = 10_485_760,
        resume: bool = False,
    ):
        self.timeout = timeout
        self.verbose = verbose
        self.max_retries = max(1, max_retries)
        self.proxy = proxy
        self.rate_limit = rate_limit
        self.cache_dir = cache_dir or DEFAULT_CACHE_DIR
        self.download_dir = download_dir or _get_default_download_dir()
        self.max_file_size = max_file_size
        self.resume = resume
        self._last_request_ts = 0.0

        self.session = requests.Session()
        self.session.headers.update(
            {
                "User-Agent": random.choice(USER_AGENTS),
                "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8",
                "Accept-Language": "en-US,en;q=0.9",
                "Accept-Encoding": "gzip, deflate",
                "Sec-Fetch-Dest": "document",
                "Sec-Fetch-Mode": "navigate",
                "Sec-Fetch-Site": "none",
            }
        )

        if custom_headers:
            self.session.headers.update(custom_headers)

        if self.proxy:
            self.session.proxies.update({"http": self.proxy, "https": self.proxy})

        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self.download_dir.mkdir(parents=True, exist_ok=True)

    def _log(self, message: str) -> None:
        if self.verbose:
            logging.debug(message)
            _cprint(f"{DIM}// [DEBUG] {message}{RESET}")

    def _rate_limit_wait(self) -> None:
        """Enforce maximum requests per second rate limiting."""
        if self.rate_limit > 0:
            interval = 1.0 / float(self.rate_limit)
            elapsed = time.time() - self._last_request_ts
            if elapsed < interval:
                time.sleep(interval - elapsed)
            self._last_request_ts = time.time()

    def fetch_search_page(
        self,
        query: str,
        count: int,
        offset: int,
        language: Optional[str],
        country: Optional[str],
        safe_search: str,
    ) -> str:
        """Fetch search engine raw HTML response with retry handling."""
        params: Dict[str, Any] = {
            "q": query,
            "count": count,
            "offset": offset,
            "safesearch": safe_search,
        }
        if language:
            params["hl"] = language
        if country:
            params["gl"] = country

        url = "https://search.brave.com/search"
        self._log(f"Fetching search endpoint: {url} with query='{query}'")

        last_err: Optional[Exception] = None
        for attempt in range(1, self.max_retries + 1):
            self._rate_limit_wait()
            try:
                resp = self.session.get(
                    url, params=params, timeout=(5.0, float(self.timeout))
                )
                if resp.status_code == 429:
                    self._log(
                        "Brave returned HTTP 429 Rate Limit. Attempting DuckDuckGo fallback..."
                    )
                    ddg_url = "https://html.duckduckgo.com/html/"
                    ddg_headers = {
                        "User-Agent": "Lynx/2.8.9rel.1 libwww-FM/2.14 SSL-MM/1.4.1 OpenSSL/1.1.1w",
                        "Cookie": "SRCHHPGUSR=ADLT=OFF; adlt=off; kl=us-en",
                        "Content-Type": "application/x-www-form-urlencoded",
                    }
                    ddg_resp = requests.post(
                        ddg_url,
                        data={"q": query, "b": "", "kl": ""},
                        headers=ddg_headers,
                        timeout=(5.0, float(self.timeout)),
                    )
                    if ddg_resp.status_code == 200:
                        return ddg_resp.text
                resp.raise_for_status()

                if not resp.encoding or resp.encoding.lower() == "iso-8859-1":
                    resp.encoding = resp.apparent_encoding or "utf-8"

                return resp.text
            except requests.RequestException as exc:
                last_err = exc
                self._log(f"Attempt {attempt}/{self.max_retries} failed: {exc}")
                if attempt < self.max_retries:
                    time.sleep(0.5 * (2 ** (attempt - 1)))

        raise RuntimeError(
            f"HTTP request failed after {self.max_retries} attempt(s): {last_err}"
        )

    def parse_results(
        self,
        html: str,
        max_results: int,
        include_domains: Set[str],
        exclude_domains: Set[str],
    ) -> List[Dict[str, Any]]:
        """Parse search results using JSON-LD, CSS selectors, or regex heuristics."""
        soup = BeautifulSoup(html, "html.parser")
        results: List[Dict[str, Any]] = []
        seen_urls: Set[str] = set()

        def _extract_json_ld_nodes(data: Any) -> List[Dict[str, Any]]:
            nodes = []
            if isinstance(data, dict):
                if "@graph" in data and isinstance(data["@graph"], list):
                    for sub in data["@graph"]:
                        nodes.extend(_extract_json_ld_nodes(sub))
                elif "itemListElement" in data and isinstance(
                    data["itemListElement"], list
                ):
                    nodes.extend(data["itemListElement"])
                elif data.get("@type") in ("SearchResult", "WebPage", "Article"):
                    nodes.append(data)
            elif isinstance(data, list):
                for sub in data:
                    nodes.extend(_extract_json_ld_nodes(sub))
            return nodes

        # ----------------------------------------------------------------------
        # TIER 1: JSON-LD Structured Data
        # ----------------------------------------------------------------------
        try:
            for json_ld in soup.find_all("script", {"type": "application/ld+json"}):
                if not json_ld.string:
                    continue
                try:
                    raw_data = json.loads(json_ld.string)
                except Exception:
                    continue
                for item in _extract_json_ld_nodes(raw_data):
                    if len(results) >= max_results:
                        break
                    valid_url = _is_valid_url(
                        item.get("url", ""), seen_urls, include_domains, exclude_domains
                    )
                    if valid_url:
                        seen_urls.add(valid_url)
                        results.append(
                            {
                                "title": item.get("name")
                                or item.get("headline")
                                or "Untitled",
                                "url": valid_url,
                                "description": _clean_snippet_text(
                                    item.get("description", "No description available")
                                ),
                                "position": len(results) + 1,
                            }
                        )
            if results:
                self._log(f"Tier 1 (JSON-LD) extracted {len(results)} results")
                return results[:max_results]
        except Exception as exc:
            self._log(f"Tier 1 parsing exception: {exc}")

        # ----------------------------------------------------------------------
        # TIER 2: Modern BeautifulSoup DOM Selectors
        # ----------------------------------------------------------------------
        snippet_containers = soup.find_all(
            ["div", "article"],
            class_=re.compile(r"snippet|result|search-result|card", re.I),
        )

        for snippet in snippet_containers:
            if len(results) >= max_results:
                break

            title_tag = snippet.find(
                "a",
                class_=re.compile(r"result-title|heading|title|snippet-title", re.I),
            ) or snippet.find("a", href=True)
            if not title_tag or not title_tag.get("href"):
                continue

            full_url = urllib.parse.urljoin(
                "https://search.brave.com", title_tag["href"]
            )
            valid_url = _is_valid_url(
                full_url, seen_urls, include_domains, exclude_domains
            )
            if not valid_url:
                continue

            seen_urls.add(valid_url)
            title = title_tag.get_text(strip=True) or "Untitled Result"

            desc_tag = snippet.find(
                ["div", "p", "span", "a"],
                class_=re.compile(
                    r"snippet-description|description|body|snippet-content|result__snippet",
                    re.I,
                ),
            )
            raw_desc = (
                desc_tag.get_text(strip=True)
                if desc_tag
                else "No description available"
            )

            results.append(
                {
                    "title": title,
                    "url": valid_url,
                    "description": _clean_snippet_text(raw_desc),
                    "position": len(results) + 1,
                }
            )

        if results:
            self._log(f"Tier 2 (DOM Selectors) extracted {len(results)} results")
            return results[:max_results]

        # ----------------------------------------------------------------------
        # TIER 3: Fallback Heuristic Anchor Regex
        # ----------------------------------------------------------------------
        for a_tag in soup.find_all("a", href=True):
            if len(results) >= max_results:
                break
            full_url = urllib.parse.urljoin("https://search.brave.com", a_tag["href"])
            valid_url = _is_valid_url(
                full_url, seen_urls, include_domains, exclude_domains
            )
            title = a_tag.get_text(strip=True)

            if valid_url and len(title) > 5:
                seen_urls.add(valid_url)
                results.append(
                    {
                        "title": title,
                        "url": valid_url,
                        "description": "Extracted via heuristic anchor parser",
                        "position": len(results) + 1,
                    }
                )

        self._log(f"Tier 3 (Heuristics) extracted {len(results)} results")
        return results[:max_results]

    def download_page(self, url: str, position: int, title: str) -> Dict[str, Any]:
        """Download individual search result page and output clean preview metadata."""
        url_hash = hashlib.md5(url.encode("utf-8")).hexdigest()[:10]

        # Check existing cached file if --resume is active
        if self.resume:
            for existing in self.download_dir.glob(f"{position}_{url_hash}.*"):
                if existing.is_file() and not existing.name.startswith(".tmp_"):
                    try:
                        cached_text = existing.read_text(
                            encoding="utf-8", errors="replace"
                        )
                        clean_text = re.sub(r"<[^>]+>", " ", cached_text)
                        clean_text = re.sub(r"\s+", " ", clean_text).strip()
                        preview = (
                            clean_text[:200] + "..."
                            if len(clean_text) > 200
                            else clean_text
                        )
                        return {
                            "success": True,
                            "position": position,
                            "title": title,
                            "url": url,
                            "filename": existing.name,
                            "filepath": str(existing.resolve()),
                            "size_bytes": existing.stat().st_size,
                            "formatted_size": _human_bytes(existing.stat().st_size),
                            "preview": preview,
                            "cached": True,
                            "downloaded_at": datetime.fromtimestamp(
                                existing.stat().st_mtime, timezone.utc
                            ).isoformat(),
                        }
                    except Exception:
                        pass

        self._rate_limit_wait()
        try:
            resp = self.session.get(url, timeout=(5.0, float(self.timeout)))
            resp.raise_for_status()

            if not resp.encoding or resp.encoding.lower() == "iso-8859-1":
                resp.encoding = resp.apparent_encoding or "utf-8"

            content_text = resp.text
            content_bytes = len(content_text.encode("utf-8"))

            if content_bytes > self.max_file_size:
                return {
                    "success": False,
                    "position": position,
                    "title": title,
                    "url": url,
                    "error": f"File size ({_human_bytes(content_bytes)}) exceeds max allowed threshold ({_human_bytes(self.max_file_size)})",
                }

            # MIME-based file extension selection
            c_type = resp.headers.get("Content-Type", "").lower()
            ext = ".html"
            if "application/pdf" in c_type:
                ext = ".pdf"
            elif "text/plain" in c_type:
                ext = ".txt"
            elif "application/json" in c_type:
                ext = ".json"

            filename = f"{position}_{url_hash}{ext}"
            filepath = self.download_dir / filename
            tmp_path = self.download_dir / f".tmp_{filename}"

            # Atomic file write with cleanup guarantee
            try:
                with open(tmp_path, "w", encoding="utf-8", errors="replace") as fp:
                    fp.write(content_text)
                tmp_path.replace(filepath)
            finally:
                if tmp_path.exists():
                    tmp_path.unlink(missing_ok=True)

            clean_text = re.sub(r"<[^>]+>", " ", content_text)
            clean_text = re.sub(r"\s+", " ", clean_text).strip()
            preview = clean_text[:200] + "..." if len(clean_text) > 200 else clean_text

            return {
                "success": True,
                "position": position,
                "title": title,
                "url": url,
                "filename": filename,
                "filepath": str(filepath.resolve()),
                "size_bytes": content_bytes,
                "formatted_size": _human_bytes(content_bytes),
                "preview": preview,
                "cached": False,
                "downloaded_at": datetime.now(timezone.utc).isoformat(),
            }
        except Exception as exc:
            return {
                "success": False,
                "position": position,
                "title": title,
                "url": url,
                "error": str(exc),
            }


# ==============================================================================
# SECTION 4: Core Execution Controller
# ==============================================================================


def execute_tool(
    query: str,
    count: int = 10,
    offset: int = 0,
    timeout: int = 15,
    language: Optional[str] = None,
    country: Optional[str] = None,
    safe_search: str = "moderate",
    download: bool = False,
    include_raw: bool = False,
    max_downloads: int = 3,
    no_color: bool = False,
    verbose: bool = False,
    output_format: str = "json",
    log_file: Optional[str] = None,
    proxy: Optional[str] = None,
    headers: Optional[str] = None,
    max_retries: int = 3,
    filter_domains: Optional[str] = None,
    exclude_domains: Optional[str] = None,
    rate_limit: Optional[int] = None,
    cache_dir: Optional[str] = None,
    download_dir: Optional[str] = None,
    max_file_size: int = 10_485_760,
    resume: bool = False,
) -> dict[str, Any]:
    """Core logic shared between CLI and programmatic invocations."""
    start_time = time.monotonic()

    if verbose:
        logging.basicConfig(level=logging.DEBUG, format="[DEBUG] %(message)s")
    if log_file:
        logging.basicConfig(
            filename=log_file,
            level=logging.DEBUG,
            format="%(asctime)s %(levelname)s %(message)s",
        )

    count_val = max(1, min(count, 50))
    offset_val = max(0, offset)
    timeout_val = max(1, timeout)
    max_dl_val = max(1, min(max_downloads, 10))
    rate_limit_val = max(0, rate_limit or 0)

    include_set = (
        {d.strip().lower() for d in filter_domains.split(",") if d.strip()}
        if filter_domains
        else set()
    )
    exclude_set = (
        {d.strip().lower() for d in exclude_domains.split(",") if d.strip()}
        if exclude_domains
        else set()
    )

    effective_cache_dir = Path(cache_dir) if cache_dir else DEFAULT_CACHE_DIR
    effective_download_dir = (
        Path(download_dir) if download_dir else _get_default_download_dir()
    )

    shutdown = GracefulShutdown()
    engine = BraveSearchEngine(
        timeout=timeout_val,
        verbose=verbose,
        max_retries=max_retries,
        proxy=proxy,
        custom_headers=_parse_custom_headers(headers),
        rate_limit=rate_limit_val,
        cache_dir=effective_cache_dir,
        download_dir=effective_download_dir,
        max_file_size=max_file_size,
        resume=resume,
    )

    try:
        raw_html = engine.fetch_search_page(
            query=query,
            count=count_val,
            offset=offset_val,
            language=language,
            country=country,
            safe_search=safe_search,
        )

        all_results = engine.parse_results(
            raw_html,
            max_results=count_val + offset_val,
            include_domains=include_set,
            exclude_domains=exclude_set,
        )
        results = all_results[offset_val : offset_val + count_val]

        if not results:
            duration_ms = round((time.monotonic() - start_time) * 1000, 2)
            termux_toast(f"No search results for: {query[:30]}", "red")
            return {
                "success": False,
                "error": f"No search results found for query: '{query}'",
                "exit_code": EXIT_NOT_FOUND,
                "query": query,
                "count": 0,
                "results": [],
                "duration_ms": duration_ms,
            }

        # ----------------------------------------------------------------------
        # Parallel Download Processing with Graceful Interruption
        # ----------------------------------------------------------------------
        downloads: List[Dict[str, Any]] = []
        if download:
            with ThreadPoolExecutor(max_workers=max_dl_val) as executor:
                futures = [
                    executor.submit(
                        engine.download_page,
                        res["url"],
                        res["position"],
                        res["title"],
                    )
                    for res in results
                ]
                for future in _get_progress_bar(
                    as_completed(futures),
                    total=len(futures),
                    desc="Downloading",
                    disable=no_color or not _is_tty(),
                ):
                    if shutdown.interrupted:
                        duration_ms = round((time.monotonic() - start_time) * 1000, 2)
                        return {
                            "success": False,
                            "error": "Execution interrupted by user signal",
                            "exit_code": EXIT_INTERRUPTED,
                            "duration_ms": duration_ms,
                        }
                    downloads.append(future.result())

            downloads.sort(key=lambda x: x.get("position", 0))

        duration_ms = round((time.monotonic() - start_time) * 1000, 2)
        successful_dl = sum(1 for d in downloads if d.get("success"))

        result_payload: dict[str, Any] = {
            "success": True,
            "query": query,
            "count": len(results),
            "offset": offset_val,
            "language": language or "any",
            "country": country or "any",
            "safe_search": safe_search,
            "results": results,
            "downloads": downloads if download else [],
            "download_count": successful_dl,
            "duration_ms": duration_ms,
            "exit_code": EXIT_SUCCESS,
        }

        if include_raw:
            # Token-optimized raw HTML sanitization
            sanitized = re.sub(
                r"<script[^>]*>[\s\S]*?</script>", "", raw_html, flags=re.I
            )
            sanitized = re.sub(
                r"<style[^>]*>[\s\S]*?</style>", "", sanitized, flags=re.I
            )
            sanitized = re.sub(
                r"data:image/[^;]+;base64,[a-zA-Z0-9+/=]+", "", sanitized
            )
            result_payload["raw_html"] = sanitized[:100_000]

        termux_toast(f"Search complete: {len(results)} results", "green")
        return result_payload

    except Exception as exc:
        duration_ms = round((time.monotonic() - start_time) * 1000, 2)
        termux_toast(f"Search error: {str(exc)[:40]}", "red")
        return {
            "success": False,
            "error": f"Brave search failed: {exc}",
            "exit_code": EXIT_ERROR,
            "query": query,
            "duration_ms": duration_ms,
        }
    finally:
        shutdown.restore()


# ==============================================================================
# SECTION 5: Output Routing & Entry Points
# ==============================================================================


def write_llm_output(data: dict[str, Any], output_format: str = "json") -> None:
    """Format and write JSON/JSONL/CSV payload to target LLM destination."""
    out_path = os.environ.get("LLM_OUTPUT", "/dev/stdout")
    fmt = output_format.lower().strip()

    if fmt == "csv":
        out = sys.stdout
        writer = csv.writer(out)
        results = data.get("results", [])
        if results:
            writer.writerow(["position", "title", "url", "description"])
            for r in results:
                writer.writerow(
                    [
                        r.get("position"),
                        r.get("title"),
                        r.get("url"),
                        r.get("description"),
                    ]
                )
        else:
            writer.writerow(["key", "value"])
            for k, v in data.items():
                writer.writerow(
                    [
                        k,
                        json.dumps(v, ensure_ascii=False)
                        if isinstance(v, (dict, list))
                        else str(v),
                    ]
                )
        return

    if fmt == "jsonl":
        json_payload = json.dumps(data, ensure_ascii=False, cls=ToolJSONEncoder) + "\n"
    else:  # default json
        json_payload = (
            json.dumps(data, indent=2, ensure_ascii=False, cls=ToolJSONEncoder) + "\n"
        )

    direct_targets = {"/dev/stdout", "/dev/fd/1", "-"}
    if out_path in direct_targets:
        sys.stdout.write(json_payload)
        sys.stdout.flush()
    else:
        try:
            Path(out_path).parent.mkdir(parents=True, exist_ok=True)
            with open(out_path, "a", encoding="utf-8") as fp:
                fp.write(json_payload)
        except OSError as err:
            sys.stderr.write(f"Failed writing to LLM_OUTPUT '{out_path}': {err}\n")
            sys.stdout.write(json_payload)
            sys.stdout.flush()


def run(
    query: str,
    count: int = 10,
    offset: int = 0,
    timeout: int = 15,
    language: Optional[str] = None,
    country: Optional[str] = None,
    safe_search: str = "moderate",
    download: bool = False,
    include_raw: bool = False,
    max_downloads: int = 3,
    no_color: bool = False,
    verbose: bool = False,
    output_format: str = "json",
    log_file: Optional[str] = None,
    proxy: Optional[str] = None,
    headers: Optional[str] = None,
    max_retries: int = 3,
    filter_domains: Optional[str] = None,
    exclude_domains: Optional[str] = None,
    rate_limit: Optional[int] = None,
    cache_dir: Optional[str] = None,
    download_dir: Optional[str] = None,
    max_file_size: int = 10_485_760,
    resume: bool = False,
) -> None:
    """Entry point for AIChat function call framework."""
    result = execute_tool(
        query=query,
        count=count,
        offset=offset,
        timeout=timeout,
        language=language,
        country=country,
        safe_search=safe_search,
        download=download,
        include_raw=include_raw,
        max_downloads=max_downloads,
        no_color=no_color,
        verbose=verbose,
        output_format=output_format,
        log_file=log_file,
        proxy=proxy,
        headers=headers,
        max_retries=max_retries,
        filter_domains=filter_domains,
        exclude_domains=exclude_domains,
        rate_limit=rate_limit,
        cache_dir=cache_dir,
        download_dir=download_dir,
        max_file_size=max_file_size,
        resume=resume,
    )

    print_human_readable_ui(result, no_color=no_color)
    write_llm_output(result, output_format=output_format)


# ==============================================================================
# SECTION 6: CLI Argument Parser
# ==============================================================================


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="brave_intel.py",
        description=f"AIChat Brave Search Engine Scraper v{__version__}",
    )
    parser.add_argument(
        "--query",
        "-q",
        required=True,
        metavar="STRING",
        help="Search query term (required)",
    )
    parser.add_argument(
        "--count",
        "-c",
        type=int,
        default=10,
        metavar="NUM",
        help="Number of search results (default: 10, max: 50)",
    )
    parser.add_argument(
        "--offset",
        "-o",
        type=int,
        default=0,
        metavar="NUM",
        help="Pagination offset (default: 0)",
    )
    parser.add_argument(
        "--timeout",
        type=int,
        default=15,
        metavar="NUM",
        help="Request timeout in seconds (default: 15)",
    )
    parser.add_argument(
        "--language",
        "-l",
        metavar="LANG",
        help="Language code filter (e.g. en, es, zh-CN)",
    )
    parser.add_argument(
        "--country",
        "-r",
        metavar="COUNTRY",
        help="Country code filter (e.g. us, uk, jp)",
    )
    parser.add_argument(
        "--safe-search",
        choices=["safe", "moderate", "strict"],
        default="moderate",
        dest="safe_search",
        metavar="LEVEL",
        help="Safe search level: safe, moderate, strict (default: moderate)",
    )
    parser.add_argument(
        "--download",
        action="store_true",
        default=False,
        help="Download HTML of each search result",
    )
    parser.add_argument(
        "--include-raw",
        action="store_true",
        default=False,
        dest="include_raw",
        help="Include raw search HTML payload",
    )
    parser.add_argument(
        "--max-downloads",
        type=int,
        default=3,
        dest="max_downloads",
        metavar="NUM",
        help="Maximum concurrent download threads (default: 3)",
    )
    parser.add_argument(
        "--no-color",
        action="store_true",
        default=False,
        dest="no_color",
        help="Disable ANSI color output",
    )
    parser.add_argument(
        "--verbose",
        "-v",
        action="store_true",
        default=False,
        help="Enable detailed debug logging",
    )
    parser.add_argument(
        "--output-format",
        choices=["json", "jsonl", "csv"],
        default="json",
        dest="output_format",
        metavar="FMT",
        help="Output format for LLM integration: json, jsonl, csv (default: json)",
    )
    parser.add_argument(
        "--log-file",
        dest="log_file",
        metavar="PATH",
        help="Path for debug log file",
    )
    parser.add_argument(
        "--proxy",
        metavar="URL",
        help="HTTP/HTTPS proxy URL",
    )
    parser.add_argument(
        "--headers",
        metavar="JSON",
        help="Custom request headers as JSON string",
    )
    parser.add_argument(
        "--max-retries",
        type=int,
        default=3,
        dest="max_retries",
        metavar="NUM",
        help="Maximum retry attempts (default: 3)",
    )
    parser.add_argument(
        "--filter-domains",
        dest="filter_domains",
        metavar="COMMA",
        help="Comma-separated domains to include",
    )
    parser.add_argument(
        "--exclude-domains",
        dest="exclude_domains",
        metavar="COMMA",
        help="Comma-separated domains to exclude",
    )
    parser.add_argument(
        "--rate-limit",
        type=int,
        default=0,
        dest="rate_limit",
        metavar="NUM",
        help="Maximum requests per second (default: 0 = unlimited)",
    )
    parser.add_argument(
        "--cache-dir",
        dest="cache_dir",
        metavar="PATH",
        help="Custom cache directory",
    )
    parser.add_argument(
        "--download-dir",
        dest="download_dir",
        metavar="PATH",
        help="Custom download directory",
    )
    parser.add_argument(
        "--max-file-size",
        type=int,
        default=10_485_760,
        dest="max_file_size",
        metavar="BYTES",
        help="Maximum file size to download in bytes (default: 10485760)",
    )
    parser.add_argument(
        "--resume",
        action="store_true",
        default=False,
        help="Resume/use cached downloaded files",
    )
    return parser


if __name__ == "__main__":
    args = _build_parser().parse_args()
    res = execute_tool(
        query=args.query,
        count=args.count,
        offset=args.offset,
        timeout=args.timeout,
        language=args.language,
        country=args.country,
        safe_search=args.safe_search,
        download=args.download,
        include_raw=args.include_raw,
        max_downloads=args.max_downloads,
        no_color=args.no_color,
        verbose=args.verbose,
        output_format=args.output_format,
        log_file=args.log_file,
        proxy=args.proxy,
        headers=args.headers,
        max_retries=args.max_retries,
        filter_domains=args.filter_domains,
        exclude_domains=args.exclude_domains,
        rate_limit=args.rate_limit,
        cache_dir=args.cache_dir,
        download_dir=args.download_dir,
        max_file_size=args.max_file_size,
        resume=args.resume,
    )

    print_human_readable_ui(res, no_color=args.no_color)
    write_llm_output(res, output_format=args.output_format)
    sys.exit(res.get("exit_code", EXIT_SUCCESS))
