#!/usr/bin/env python3
# ==============================================================================
# web_search_free.py — Pyrmethus AIChat Tool: Free Web Search Engine v2.8.0
# argc/aichat compatible · Human-Readable Box UI · API-Keyless DDG Scraper
#
# @describe Performs fast, API-free web searches by parsing DuckDuckGo HTML and Lite endpoints with URL normalization and backoff jitter.
#
# @meta require-tools aichat
#
# @option --query! <STRING>              Search query term (required)
# @option --count <NUM>                  Number of search results to return (default: 10, max: 30)
# @option --timeout <NUM>                Request timeout in seconds (default: 15)
# @option --max-retries <NUM>            Maximum retries on failure (default: 3)
# @flag   --filter-spam                  Filter out low-quality or empty search results
# @flag   --no-color                     Disable ANSI color output
# @flag   --verbose                      Enable detailed debug logging
#
# @env LLM_OUTPUT=/dev/stdout            Output path for LLM integration
# ==============================================================================

from __future__ import annotations

import argparse
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
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Set

try:
    import requests
    from bs4 import BeautifulSoup
except ImportError:
    print(
        "\033[31mError: Missing dependencies. Please run: pip install requests beautifulsoup4\033[0m",
        file=sys.stderr,
    )
    sys.exit(127)

__version__ = "2.8.0"

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
    """Signal handler for graceful cancellation of batch operations."""

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

NEON_CYAN   = "\033[38;5;51m"
NEON_GREEN  = "\033[38;5;46m"
NEON_RED    = "\033[38;5;196m"
NEON_YELLOW = "\033[38;5;226m"
NEON_PURPLE = "\033[38;5;129m"
NEON_PINK   = "\033[38;5;198m"
RESET       = "\033[0m"
BOLD        = "\033[1m"
DIM         = "\033[2m"

_ANSI_RE = re.compile(r"\033\[[0-9;]*[a-zA-Z]")


def _strip_ansi(text: str) -> str:
    """Remove ANSI escape sequences from string."""
    return _ANSI_RE.sub("", text)


def _is_tty() -> bool:
    """Check if stderr is connected to an interactive TTY."""
    return sys.stderr.isatty() and os.environ.get("TERM", "").lower() not in ("dumb", "")


def _cprint(text: str, file: Any = None, no_color: bool = False, end: str = "\n") -> None:
    """Print formatted ANSI text to target stream."""
    target = file or sys.stderr
    if no_color or not _is_tty():
        text = _strip_ansi(text)
    print(text, file=target, flush=True, end=end)


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
        f"{NEON_PURPLE}│{RESET} {NEON_PINK}⚡ [FREE WEB SEARCH ENGINE v{__version__}]{RESET} "
        f"{status_color}{BOLD}{status_symbol} {status_text}{RESET}"
    )
    _cprint(f"{NEON_PURPLE}├{border}┤{RESET}")
    _cprint(f"{NEON_PURPLE}│{RESET} {NEON_CYAN}Query:{RESET}        {NEON_YELLOW}{data.get('query', 'N/A')}{RESET}")
    _cprint(f"{NEON_PURPLE}│{RESET} {NEON_CYAN}Results:{RESET}      {NEON_GREEN}{data.get('count', 0)}{RESET}")
    _cprint(f"{NEON_PURPLE}│{RESET} {NEON_CYAN}Engine Tier:{RESET}  {data.get('engine_tier', 'N/A')}")
    _cprint(f"{NEON_PURPLE}│{RESET} {NEON_CYAN}Duration:{RESET}     {DIM}{data.get('duration_ms', 0)}ms{RESET}")

    if not success and "error" in data:
        _cprint(f"{NEON_PURPLE}├{border}┤{RESET}")
        _cprint(f"{NEON_PURPLE}│{RESET} {NEON_RED}Error:{RESET}        {data['error']}")

    results = data.get("results", [])
    if results:
        _cprint(f"{NEON_PURPLE}├{border}┤{RESET}")
        _cprint(f"{NEON_PURPLE}│{RESET} {BOLD}Search Results Preview ({len(results)}):{RESET}")
        for res in results[:5]:
            title = res.get("title", "Untitled")[:48]
            url = res.get("url", "")[:50]
            _cprint(f"{NEON_PURPLE}│{RESET}   {NEON_CYAN}›{RESET} {BOLD}{title}{RESET}")
            _cprint(f"{NEON_PURPLE}│{RESET}     {DIM}{url}{RESET}")
        if len(results) > 5:
            _cprint(f"{NEON_PURPLE}│{RESET}   {DIM}... and {len(results) - 5} more results{RESET}")

    _cprint(f"{NEON_PURPLE}╰{border}╯{RESET}")


# ==============================================================================
# SECTION 3: API-Free Scraping Engine
# ==============================================================================

USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 14_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/18.1 Safari/605.1.15",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:133.0) Gecko/20100101 Firefox/133.0",
    "Mozilla/5.0 (Linux; Android 14; Pixel 8) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Mobile Safari/537.36",
]


def _unwrap_ddg_url(raw_url: str) -> str:
    """Unwrap destination URL from DuckDuckGo tracking links."""
    if "uddg=" in raw_url:
        parsed = urllib.parse.urlparse(raw_url)
        qs = urllib.parse.parse_qs(parsed.query)
        if "uddg" in qs and qs["uddg"]:
            return qs["uddg"][0]
    if raw_url.startswith("//"):
        return "https:" + raw_url
    return raw_url


def _normalize_url(url: str) -> str:
    """Strip common tracking parameters and normalize scheme/path."""
    if not url:
        return ""
    try:
        parsed = urllib.parse.urlparse(url)
        qs = urllib.parse.parse_qs(parsed.query)
        kill = {
            "utm_source", "utm_medium", "utm_campaign", "utm_term", "utm_content",
            "fbclid", "gclid", "ref", "source", "si", "ncid", "mc_cid", "mc_eid"
        }
        clean_qs = {k: v for k, v in qs.items() if k.lower() not in kill}
        clean_query = urllib.parse.urlencode(clean_qs, doseq=True)
        path = parsed.path.rstrip("/") or "/"
        scheme = "https" if parsed.scheme in ("http", "https") else parsed.scheme
        return urllib.parse.urlunparse((scheme, parsed.netloc.lower(), path, "", clean_query, ""))
    except Exception:
        return url


def _clean_snippet(text: str, max_len: int = 280) -> str:
    """Normalize whitespace, remove zero-width characters, and truncate cleanly."""
    if not text:
        return "No snippet available"
    text = re.sub(r"[\u200b\u200c\u200d\ufeff]", "", text)
    text = re.sub(r"\s+", " ", text).strip()
    if len(text) > max_len:
        truncated = text[:max_len].rsplit(" ", 1)[0]
        text = truncated + "…" if truncated else text[:max_len] + "…"
    return text


def _is_garbage_result(title: str, url: str, snippet: str, filter_spam: bool = True) -> bool:
    """Filter out empty titles or low-quality result shells."""
    if not filter_spam:
        return False
    if len(title.strip()) < 5 or title.lower().strip() in {"untitled", "untitled result", "no title"}:
        return True
    if len(snippet.strip()) < 15 and "no snippet" in snippet.lower():
        return True
    return False


class FreeSearchEngine:
    """API-Free search scraper utilizing DuckDuckGo HTML and Lite endpoints."""

    def __init__(self, timeout: int = 15, max_retries: int = 3, verbose: bool = False):
        self.timeout = timeout
        self.max_retries = max(1, max_retries)
        self.verbose = verbose
        self.session = requests.Session()

    def __enter__(self) -> FreeSearchEngine:
        return self

    def __exit__(self, exc_type: Any, exc_val: Any, exc_tb: Any) -> bool:
        self.close()
        return False

    def close(self) -> None:
        """Close the HTTP session."""
        self.session.close()

    def _log(self, message: str) -> None:
        if self.verbose:
            logging.debug(message)
            _cprint(f"{DIM}// [DEBUG] {message}{RESET}")

    def _get_headers(self) -> Dict[str, str]:
        return {
            "User-Agent": random.choice(USER_AGENTS),
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
            "Accept-Language": random.choice(["en-US,en;q=0.9", "en-GB,en;q=0.8", "en;q=0.7"]),
            "Accept-Encoding": "gzip, deflate, br",
            "DNT": "1",
            "Connection": "keep-alive",
            "Upgrade-Insecure-Requests": "1",
            "Sec-Fetch-Dest": "document",
            "Sec-Fetch-Mode": "navigate",
            "Sec-Fetch-Site": "none",
            "Sec-Fetch-User": "?1",
            "Cache-Control": "max-age=0",
        }

    def _backoff(self, attempt: int) -> None:
        """Exponential backoff with randomized jitter."""
        base = min(8.0, (2 ** (attempt - 1)) * 0.7)
        jitter = random.uniform(0.15, 0.85)
        sleep_time = base + jitter
        self._log(f"Backoff waiting {sleep_time:.2f}s (attempt {attempt})")
        time.sleep(sleep_time)

    def search_ddg_html(self, query: str, count: int, filter_spam: bool = True) -> List[Dict[str, Any]]:
        """Tier 1: Scrape DuckDuckGo HTML endpoint (html.duckduckgo.com)."""
        url = "https://html.duckduckgo.com/html/"
        data = {"q": query, "b": ""}

        self._log(f"Querying Tier 1 endpoint: {url}")
        resp = self.session.post(
            url,
            data=data,
            headers=self._get_headers(),
            timeout=(5.0, float(self.timeout)),
        )
        resp.raise_for_status()

        if not resp.encoding or resp.encoding.lower() == "iso-8859-1":
            resp.encoding = resp.apparent_encoding or "utf-8"

        soup = BeautifulSoup(resp.text, "html.parser")
        results: List[Dict[str, Any]] = []
        seen_urls: Set[str] = set()

        for result_div in soup.find_all("div", class_=re.compile(r"result|results_links")):
            if len(results) >= count:
                break

            a_tag = result_div.find("a", class_=re.compile(r"result__a"))
            if not a_tag or not a_tag.get("href"):
                continue

            clean_url = _normalize_url(_unwrap_ddg_url(a_tag["href"]))
            if not clean_url.startswith("https://") or "duckduckgo.com" in clean_url:
                continue

            if clean_url in seen_urls:
                continue

            title = a_tag.get_text(strip=True) or "Untitled Result"
            snippet_tag = result_div.find(["a", "div"], class_=re.compile(r"result__snippet"))
            raw_snippet = snippet_tag.get_text(strip=True) if snippet_tag else ""
            snippet = _clean_snippet(raw_snippet)

            if _is_garbage_result(title, clean_url, snippet, filter_spam=filter_spam):
                continue

            seen_urls.add(clean_url)
            results.append({
                "title": title,
                "url": clean_url,
                "snippet": snippet,
                "position": len(results) + 1,
            })

        return results

    def search_ddg_lite(self, query: str, count: int, filter_spam: bool = True) -> List[Dict[str, Any]]:
        """Tier 2 Fallback: Scrape DuckDuckGo Lite endpoint (lite.duckduckgo.com)."""
        url = "https://lite.duckduckgo.com/lite/"
        data = {"q": query}

        self._log(f"Querying Tier 2 Fallback endpoint: {url}")
        resp = self.session.post(
            url,
            data=data,
            headers=self._get_headers(),
            timeout=(5.0, float(self.timeout)),
        )
        resp.raise_for_status()

        if not resp.encoding or resp.encoding.lower() == "iso-8859-1":
            resp.encoding = resp.apparent_encoding or "utf-8"

        soup = BeautifulSoup(resp.text, "html.parser")
        results: List[Dict[str, Any]] = []
        seen_urls: Set[str] = set()

        rows = soup.find_all("tr")
        i = 0
        while i < len(rows) and len(results) < count:
            row = rows[i]
            a_tag = row.find("a", class_=re.compile(r"result-link"))
            if a_tag and a_tag.get("href"):
                clean_url = _normalize_url(_unwrap_ddg_url(a_tag["href"]))
                if clean_url.startswith("https://") and "duckduckgo.com" not in clean_url and clean_url not in seen_urls:
                    title = a_tag.get_text(strip=True) or "Untitled Result"

                    raw_snippet = ""
                    if i + 1 < len(rows):
                        next_row = rows[i + 1]
                        snippet_td = next_row.find("td", class_=re.compile(r"result-snippet"))
                        if snippet_td:
                            raw_snippet = snippet_td.get_text(strip=True)

                    snippet = _clean_snippet(raw_snippet)

                    if not _is_garbage_result(title, clean_url, snippet, filter_spam=filter_spam):
                        seen_urls.add(clean_url)
                        results.append({
                            "title": title,
                            "url": clean_url,
                            "snippet": snippet,
                            "position": len(results) + 1,
                        })
            i += 1

        return results


# ==============================================================================
# SECTION 4: Core Execution Controller
# ==============================================================================

def execute_tool(
    query: str,
    count: int = 10,
    timeout: int = 15,
    max_retries: int = 3,
    filter_spam: bool = True,
    no_color: bool = False,
    verbose: bool = False,
) -> dict[str, Any]:
    start_time = time.monotonic()

    if verbose:
        logging.basicConfig(level=logging.DEBUG, format="[DEBUG] %(message)s")

    count_val = max(1, min(count, 30))
    timeout_val = max(1, timeout)
    retries_val = max(1, max_retries)

    shutdown = GracefulShutdown()

    results: List[Dict[str, Any]] = []
    tier_used = "Tier 1 (DDG HTML)"
    last_err: Optional[Exception] = None

    with FreeSearchEngine(timeout=timeout_val, max_retries=retries_val, verbose=verbose) as engine:
        # Try Tier 1: DDG HTML
        for attempt in range(1, retries_val + 1):
            if shutdown.interrupted:
                return {
                    "success": False,
                    "error": "Execution interrupted by user signal",
                    "exit_code": EXIT_INTERRUPTED,
                    "duration_ms": round((time.monotonic() - start_time) * 1000, 2),
                }
            try:
                results = engine.search_ddg_html(query, count_val, filter_spam=filter_spam)
                if results:
                    break
            except Exception as exc:
                last_err = exc
                if attempt < retries_val:
                    engine._backoff(attempt)

        # Try Tier 2 Fallback: DDG Lite if Tier 1 yields no results
        if not results and not shutdown.interrupted:
            tier_used = "Tier 2 (DDG Lite Fallback)"
            for attempt in range(1, retries_val + 1):
                try:
                    results = engine.search_ddg_lite(query, count_val, filter_spam=filter_spam)
                    if results:
                        break
                except Exception as exc:
                    last_err = exc
                    if attempt < retries_val:
                        engine._backoff(attempt)

    duration_ms = round((time.monotonic() - start_time) * 1000, 2)

    if not results:
        err_msg = f"No search results found for query: '{query}'"
        if last_err:
            err_msg += f" (Last error: {last_err})"
        termux_toast(f"No search results for: {query[:30]}", "red")
        return {
            "success": False,
            "error": err_msg,
            "exit_code": EXIT_NOT_FOUND,
            "query": query,
            "count": 0,
            "results": [],
            "duration_ms": duration_ms,
        }

    termux_toast(f"Search complete: {len(results)} results", "green")
    return {
        "success": True,
        "query": query,
        "count": len(results),
        "engine_tier": tier_used,
        "results": results,
        "duration_ms": duration_ms,
        "exit_code": EXIT_SUCCESS,
    }


# ==============================================================================
# SECTION 5: Output Routing & Entry Points
# ==============================================================================

def write_llm_output(data: dict[str, Any]) -> None:
    out_path = os.environ.get("LLM_OUTPUT", "/dev/stdout")
    json_payload = json.dumps(data, indent=2, ensure_ascii=False, cls=ToolJSONEncoder) + "\n"

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
    timeout: int = 15,
    max_retries: int = 3,
    filter_spam: bool = True,
    no_color: bool = False,
    verbose: bool = False,
) -> None:
    """Entry point for AIChat function call framework."""
    result = execute_tool(
        query=query,
        count=count,
        timeout=timeout,
        max_retries=max_retries,
        filter_spam=filter_spam,
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
        prog="web_search_free.py",
        description=f"AIChat Free Web Search Engine v{__version__}",
    )
    parser.add_argument(
        "--query", "-q",
        required=True,
        metavar="STRING",
        help="Search query term (required)",
    )
    parser.add_argument(
        "--count", "-c",
        type=int,
        default=10,
        metavar="NUM",
        help="Number of search results to return (default: 10, max: 30)",
    )
    parser.add_argument(
        "--timeout",
        type=int,
        default=15,
        metavar="NUM",
        help="Request timeout in seconds (default: 15)",
    )
    parser.add_argument(
        "--max-retries",
        type=int,
        default=3,
        dest="max_retries",
        metavar="NUM",
        help="Maximum retries on failure (default: 3)",
    )
    parser.add_argument(
        "--filter-spam",
        action="store_true",
        default=True,
        dest="filter_spam",
        help="Filter out low-quality or empty search results",
    )
    parser.add_argument(
        "--no-color",
        action="store_true",
        default=False,
        dest="no_color",
        help="Disable ANSI color output",
    )
    parser.add_argument(
        "--verbose", "-v",
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
        timeout=args.timeout,
        max_retries=args.max_retries,
        filter_spam=args.filter_spam,
        no_color=args.no_color,
        verbose=args.verbose,
    )
    print_human_readable_ui(res, no_color=args.no_color)
    write_llm_output(res)
    sys.exit(res.get("exit_code", EXIT_SUCCESS))
