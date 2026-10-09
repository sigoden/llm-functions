#!/usr/bin/env python3
# ==============================================================================
# brave_intel.py — Pyrmethus AIChat Tool: Brave Search Engine v2.7.0
# argc/aichat compatible · Human-Readable Box UI · Concurrent Downloads
# Official Brave Search API (api.search.brave.com) with HTML fallback
#
# @describe Performs Brave Search queries via official API or HTML fallback, parses multi-tier results, and downloads pages concurrently.
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
# @option --rate-limit <NUM>             Max requests per second (0 = unlimited, default: 0)
# @flag   --download                     Download HTML of each search result
# @flag   --include-raw                  Include raw search HTML/JSON payload
# @flag   --force-html                   Force HTML scraping even if API key available
# @flag   --no-color                     Disable ANSI color output
# @flag   --verbose                      Enable detailed debug logging
#
# @env BRAVE_API_KEY                     Official Brave Search API subscription token
# @env LLM_OUTPUT=/dev/stdout            Output path for LLM integration
# ==============================================================================

from __future__ import annotations

import argparse
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
from typing import Any, Dict, List, Optional, Set, Tuple

try:
    import requests
    from bs4 import BeautifulSoup
except ImportError:
    print(
        "\033[31mError: Missing dependencies. Please run: pip install requests beautifulsoup4\033[0m",
        file=sys.stderr,
    )
    sys.exit(127)

__version__ = "2.7.0"

# ==============================================================================
# SECTION 1: Exit Codes & Exception Models
# ==============================================================================

EXIT_SUCCESS = 0
EXIT_ERROR = 1
EXIT_NOT_FOUND = 404
EXIT_INVALID_INPUT = 127
EXIT_INTERRUPTED = 130
EXIT_RATE_LIMITED = 429


class ToolJSONEncoder(json.JSONEncoder):
    """Custom JSON encoder handling Path and datetime objects safely."""

    def default(self, obj: Any) -> Any:
        if isinstance(obj, Path):
            return str(obj)
        if isinstance(obj, datetime):
            return obj.isoformat()
        return super().default(obj)


class GracefulShutdown:
    """Signal handler for graceful cancellation of batch download operations."""

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


def print_human_readable_ui(data: dict[str, Any], no_color: bool = False) -> None:
    """Render colorized box UI to stderr for terminal users."""
    if not _is_tty() or no_color:
        return

    success = data.get("success", False)
    status_color = NEON_GREEN if success else NEON_RED
    status_symbol = "✓" if success else "✗"
    status_text = "SUCCESS" if success else "FAILED"
    mode = data.get("mode", "unknown")

    box_w = 64
    border = "─" * box_w

    _cprint(f"{NEON_PURPLE}╭{border}╮{RESET}")
    _cprint(
        f"{NEON_PURPLE}│{RESET} {NEON_PINK}⚡ [BRAVE SEARCH ENGINE v{__version__}]{RESET} "
        f"{status_color}{BOLD}{status_symbol} {status_text}{RESET} {DIM}[{mode}]{RESET}"
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
# SECTION 3: Brave Search Engine — Dual Mode (API + HTML Fallback)
# ==============================================================================

USER_AGENTS = [
    "Mozilla/5.0 (Linux; Android 14; Pixel 8) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128.0.0.0 Mobile Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7; rv:131.0) Gecko/20100101 Firefox/131.0",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36",
    "Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.0 Mobile/15E148 Safari/604.1",
]


def _get_download_dir() -> Path:
    """Resolve storage path with Termux SD card fallback awareness."""
    termux_storage = Path.home() / "storage" / "downloads" / "brave_search"
    if termux_storage.parent.exists():
        return termux_storage
    return Path.home() / "downloads" / "brave_search"


CACHE_DIR = Path.home() / ".cache" / "brave_search"
DOWNLOAD_DIR = _get_download_dir()


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


class BraveSearchEngine:
    """Dual-mode scraping and parsing engine for Brave Search (API + HTML fallback)."""

    def __init__(
        self,
        timeout: int = 15,
        verbose: bool = False,
        rate_limit: float = 0.0,
        force_html: bool = False,
    ):
        self.timeout = timeout
        self.verbose = verbose
        self.rate_limit = rate_limit
        self.force_html = force_html
        self._last_request_time = 0.0

        # Check for API key
        self.api_key = os.environ.get("BRAVE_API_KEY", "").strip()
        self.use_api = bool(self.api_key) and not force_html

        # Session for connection pooling
        self.session = requests.Session()
        self.session.headers.update(
            {
                "User-Agent": random.choice(USER_AGENTS),
                "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8",
                "Accept-Language": "en-US,en;q=0.9",
                "Sec-Fetch-Dest": "document",
                "Sec-Fetch-Mode": "navigate",
                "Sec-Fetch-Site": "none",
            }
        )

        if self.use_api:
            self.session.headers.update(
                {
                    "Accept": "application/json",
                    "X-Subscription-Token": self.api_key,
                }
            )
            self.api_endpoint = "https://api.search.brave.com/res/v1/web/search"
            self._log(f"Initialized in API mode (endpoint: {self.api_endpoint})")
        else:
            self.html_endpoint = "https://search.brave.com/search"
            self._log(
                "Initialized in HTML scraping mode"
                + (" (forced)" if force_html else " (no API key)")
            )

    def _log(self, message: str) -> None:
        if self.verbose:
            logging.debug(message)
            _cprint(f"{DIM}// [DEBUG] {message}{RESET}")

    def _rate_limit_wait(self) -> None:
        """Enforce rate limiting between requests."""
        if self.rate_limit <= 0:
            return  # Skip rate limiting for unlimited or zero rate
        min_interval = 1.0 / self.rate_limit
        elapsed = time.monotonic() - self._last_request_time
        if elapsed < min_interval:
            time.sleep(min_interval - elapsed)
        self._last_request_time = time.monotonic()

    def fetch_search_page(
        self,
        query: str,
        count: int,
        offset: int,
        language: Optional[str],
        country: Optional[str],
        safe_search: str,
    ) -> Tuple[str, str]:
        """
        Fetch search results via API or HTML fallback.
        Returns: (raw_response, mode) where mode is 'api' or 'html'
        """
        self._rate_limit_wait()

        if self.use_api:
            return self._fetch_via_api(
                query, count, offset, language, country, safe_search
            )
        else:
            return self._fetch_via_html(
                query, count, offset, language, country, safe_search
            )

    def _fetch_via_api(
        self,
        query: str,
        count: int,
        offset: int,
        language: Optional[str],
        country: Optional[str],
        safe_search: str,
    ) -> Tuple[str, str]:
        """Fetch results via official Brave Search API."""
        params: Dict[str, Any] = {
            "q": query,
            "count": min(count, 20),  # API max is 20 per request
            "offset": offset,
            "safesearch": safe_search,
        }
        if language:
            params["search_lang"] = language
        if country:
            params["country"] = country

        self._log(f"Fetching API endpoint: {self.api_endpoint} with query='{query}'")

        last_err: Optional[Exception] = None
        for attempt in range(1, 4):
            try:
                resp = self.session.get(
                    self.api_endpoint, params=params, timeout=(5.0, float(self.timeout))
                )

                if resp.status_code == 429:
                    retry_after = int(resp.headers.get("Retry-After", "60"))
                    self._log(f"Rate limited (429). Retry-After: {retry_after}s")
                    if attempt < 3:
                        time.sleep(min(retry_after, 30))
                        continue
                    raise RuntimeError(
                        f"Rate limited after retries. Retry-After: {retry_after}s"
                    )

                resp.raise_for_status()

                # Return raw JSON text
                return resp.text, "api"

            except requests.RequestException as exc:
                last_err = exc
                self._log(f"API attempt {attempt} failed: {exc}")
                if attempt < 3:
                    time.sleep(1.0 * attempt)

        raise RuntimeError(f"API request failed after retries: {last_err}")

    def _fetch_via_html(
        self,
        query: str,
        count: int,
        offset: int,
        language: Optional[str],
        country: Optional[str],
        safe_search: str,
    ) -> Tuple[str, str]:
        """Fetch results via HTML scraping (fallback)."""
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
        self._log(f"Fetching HTML endpoint: {url} with query='{query}'")

        last_err: Optional[Exception] = None
        max_retries = 3
        for attempt in range(1, max_retries + 1):
            try:
                resp = self.session.get(
                    url, params=params, timeout=(5.0, float(self.timeout))
                )
                if resp.status_code == 429:
                    retry_after = int(resp.headers.get("Retry-After", "60"))
                    self._log(f"Rate limited (429). Retry-After: {retry_after}s")
                    if attempt < max_retries:
                        time.sleep(min(retry_after, 30))
                        continue
                    raise RuntimeError(
                        f"Rate limited after retries. Retry-After: {retry_after}s"
                    )
                resp.raise_for_status()

                if not resp.encoding or resp.encoding.lower() == "iso-8859-1":
                    resp.encoding = resp.apparent_encoding or "utf-8"

                return resp.text, "html"

            except requests.RequestException as exc:
                last_err = exc
                self._log(f"HTML attempt {attempt} failed: {exc}")
                if attempt < max_retries:
                    backoff = 2**attempt
                    self._log(f"Retrying in {backoff}s...")
                    time.sleep(backoff)

        raise RuntimeError(
            f"HTML request failed after {max_retries} retries: {last_err}"
        )

    def parse_results(
        self, raw_response: str, mode: str, max_results: int
    ) -> List[Dict[str, Any]]:
        """Parse search results from API JSON or HTML."""
        if mode == "api":
            return self._parse_api_results(raw_response, max_results)
        else:
            return self._parse_html_results(raw_response, max_results)

    def _parse_api_results(
        self, json_text: str, max_results: int
    ) -> List[Dict[str, Any]]:
        """Parse official Brave Search API JSON response."""
        results: List[Dict[str, Any]] = []
        seen_urls: Set[str] = set()

        try:
            data = json.loads(json_text)
            web_results = data.get("web", {}).get("results", [])

            for idx, item in enumerate(web_results):
                if len(results) >= max_results:
                    break

                url = item.get("url", "")
                if not url or url in seen_urls:
                    continue

                parsed = urllib.parse.urlparse(url)
                if parsed.scheme not in ("http", "https"):
                    continue

                seen_urls.add(url)
                results.append(
                    {
                        "title": item.get("title", "Untitled"),
                        "url": url,
                        "description": _clean_snippet_text(
                            item.get("description", "No description available")
                        ),
                        "position": idx + 1,
                        "age": item.get("age", ""),
                        "language": item.get("language", ""),
                        "family_friendly": item.get("family_friendly", True),
                        "source": "api",
                    }
                )

            self._log(f"API parsed {len(results)} results")
            return results[:max_results]

        except json.JSONDecodeError as exc:
            self._log(f"API JSON parse error: {exc}")
            return []
        except Exception as exc:
            self._log(f"API parsing exception: {exc}")
            return []

    def _parse_html_results(self, html: str, max_results: int) -> List[Dict[str, Any]]:
        """Parse search results from HTML using multi-tier strategy."""
        soup = BeautifulSoup(html, "html.parser")
        results: List[Dict[str, Any]] = []
        seen_urls: Set[str] = set()

        def _is_valid_url(target_url: str) -> Optional[str]:
            if not target_url:
                return None
            unwrapped = _unwrap_brave_redirect(target_url)
            if unwrapped in seen_urls:
                return None
            parsed = urllib.parse.urlparse(unwrapped)
            if parsed.scheme not in ("http", "https"):
                return None
            if "search.brave.com" in parsed.netloc and parsed.path in (
                "/search",
                "/settings",
                "/privacy",
                "/images",
                "/g",
            ):
                return None
            return unwrapped

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
                raw_data = json.loads(json_ld.string)
                for item in _extract_json_ld_nodes(raw_data):
                    if len(results) >= max_results:
                        break
                    valid_url = _is_valid_url(item.get("url", ""))
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
                                "source": "json-ld",
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
            valid_url = _is_valid_url(full_url)
            if not valid_url:
                continue

            seen_urls.add(valid_url)
            title = title_tag.get_text(strip=True) or "Untitled Result"

            desc_tag = snippet.find(
                ["div", "p", "span"],
                class_=re.compile(
                    r"snippet-description|description|body|snippet-content", re.I
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
                    "source": "dom",
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
            valid_url = _is_valid_url(full_url)
            title = a_tag.get_text(strip=True)

            if valid_url and len(title) > 5:
                seen_urls.add(valid_url)
                results.append(
                    {
                        "title": title,
                        "url": valid_url,
                        "description": "Extracted via heuristic anchor parser",
                        "position": len(results) + 1,
                        "source": "heuristic",
                    }
                )

        self._log(f"Tier 3 (Heuristics) extracted {len(results)} results")
        return results[:max_results]

    def download_page(self, url: str, position: int, title: str) -> Dict[str, Any]:
        """Download individual search result page and output clean preview metadata."""
        try:
            resp = self.session.get(url, timeout=(5.0, float(self.timeout)))
            resp.raise_for_status()

            if not resp.encoding or resp.encoding.lower() == "iso-8859-1":
                resp.encoding = resp.apparent_encoding or "utf-8"

            content_text = resp.text

            # MIME-based file extension selection
            c_type = resp.headers.get("Content-Type", "").lower()
            ext = ".html"
            if "application/pdf" in c_type:
                ext = ".pdf"
            elif "text/plain" in c_type:
                ext = ".txt"
            elif "application/json" in c_type:
                ext = ".json"

            url_hash = hashlib.md5(url.encode("utf-8")).hexdigest()[:10]
            filename = f"{position}_{url_hash}{ext}"
            filepath = DOWNLOAD_DIR / filename
            tmp_path = DOWNLOAD_DIR / f".tmp_{filename}"

            DOWNLOAD_DIR.mkdir(parents=True, exist_ok=True)

            # Atomic file write with cleanup guarantee
            try:
                with open(tmp_path, "w", encoding="utf-8", errors="replace") as fp:
                    fp.write(content_text)
                tmp_path.replace(filepath)
            finally:
                if tmp_path.exists():
                    tmp_path.unlink(missing_ok=True)

            # Clean snippet preview
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
                "size_bytes": len(content_text),
                "formatted_size": _human_bytes(len(content_text)),
                "preview": preview,
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
    rate_limit: float = 0.0,
    force_html: bool = False,
    no_color: bool = False,
    verbose: bool = False,
) -> dict[str, Any]:
    """Core logic shared between CLI and programmatic invocations."""
    start_time = time.monotonic()

    if verbose:
        logging.basicConfig(level=logging.DEBUG, format="[DEBUG] %(message)s")

    count_val = max(1, min(count, 50))
    offset_val = max(0, offset)
    timeout_val = max(1, timeout)
    max_dl_val = max(1, min(max_downloads, 10))
    rate_limit_val = max(0.0, rate_limit)

    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    DOWNLOAD_DIR.mkdir(parents=True, exist_ok=True)

    shutdown = GracefulShutdown()
    engine = BraveSearchEngine(
        timeout=timeout_val,
        verbose=verbose,
        rate_limit=rate_limit_val,
        force_html=force_html,
    )

    mode = "api" if engine.use_api else "html"

    try:
        raw_response, fetch_mode = engine.fetch_search_page(
            query=query,
            count=count_val,
            offset=offset_val,
            language=language,
            country=country,
            safe_search=safe_search,
        )

        all_results = engine.parse_results(
            raw_response, fetch_mode, max_results=count_val + offset_val
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
                "mode": mode,
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
                for future in as_completed(futures):
                    if shutdown.interrupted:
                        duration_ms = round((time.monotonic() - start_time) * 1000, 2)
                        return {
                            "success": False,
                            "error": "Execution interrupted by signal",
                            "exit_code": EXIT_INTERRUPTED,
                            "duration_ms": duration_ms,
                            "mode": mode,
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
            "mode": mode,
            "fetch_mode": fetch_mode,
        }

        if include_raw:
            if fetch_mode == "api":
                # For API, include raw JSON (truncated)
                result_payload["raw_json"] = raw_response[:100_000]
            else:
                # For HTML, sanitize
                sanitized = re.sub(
                    r"<script[^>]*>[\s\S]*?</script>", "", raw_response, flags=re.I
                )
                sanitized = re.sub(
                    r"<style[^>]*>[\s\S]*?</style>", "", sanitized, flags=re.I
                )
                sanitized = re.sub(
                    r"data:image/[^;]+;base64,[a-zA-Z0-9+/=]+", "", sanitized
                )
                result_payload["raw_html"] = sanitized[:100_000]

        termux_toast(f"Search complete: {len(results)} results ({fetch_mode})", "green")
        return result_payload

    except RuntimeError as exc:
        duration_ms = round((time.monotonic() - start_time) * 1000, 2)
        err_msg = str(exc)
        exit_code = (
            EXIT_RATE_LIMITED
            if "rate limited" in err_msg.lower() or "429" in err_msg
            else EXIT_ERROR
        )
        termux_toast(f"Search error: {err_msg[:40]}", "red")
        return {
            "success": False,
            "error": f"Brave search failed: {exc}",
            "exit_code": exit_code,
            "query": query,
            "duration_ms": duration_ms,
            "mode": mode,
        }
    except Exception as exc:
        duration_ms = round((time.monotonic() - start_time) * 1000, 2)
        termux_toast(f"Search error: {str(exc)[:40]}", "red")
        return {
            "success": False,
            "error": f"Brave search failed: {exc}",
            "exit_code": EXIT_ERROR,
            "query": query,
            "duration_ms": duration_ms,
            "mode": mode,
        }
    finally:
        shutdown.restore()


# ==============================================================================
# SECTION 5: Output Routing & Entry Points
# ==============================================================================


def write_llm_output(data: dict[str, Any]) -> None:
    """Format and write JSON output payload to target LLM destination."""
    out_path = os.environ.get("LLM_OUTPUT", "/dev/stdout")
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
    rate_limit: float = 0.0,
    force_html: bool = False,
    no_color: bool = False,
    verbose: bool = False,
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
        rate_limit=rate_limit,
        force_html=force_html,
        no_color=no_color,
        verbose=verbose,
    )

    print_human_readable_ui(result, no_color=no_color)
    write_llm_output(result)


# ==============================================================================
# SECTION 6: CLI Argument Parser
# ==============================================================================


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="brave_intel.py",
        description=f"AIChat Brave Search Engine v{__version__} (API + HTML Fallback)",
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
        help="Include raw search HTML/JSON payload",
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
        "--rate-limit",
        type=float,
        default=0.0,
        dest="rate_limit",
        metavar="NUM",
        help="Max requests per second (0 = unlimited, default: 0)",
    )
    parser.add_argument(
        "--force-html",
        action="store_true",
        default=False,
        dest="force_html",
        help="Force HTML scraping even if API key is available",
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
        rate_limit=args.rate_limit,
        force_html=args.force_html,
        no_color=args.no_color,
        verbose=args.verbose,
    )

    print_human_readable_ui(res, no_color=args.no_color)
    write_llm_output(res)
    sys.exit(res.get("exit_code", EXIT_SUCCESS))
