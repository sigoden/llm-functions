#!/usr/bin/env python3
# ==============================================================================
# ddg_search.py — Pyrmethus AIChat DuckDuckGo Master Search Tool v2.3.1-ENHANCED
# argc/aichat compatible · Human-Readable UI · Persistent Caching · Dual Engine · Parallel Workers
#
# @describe Perform privacy-focused web and instant-answer searches using DuckDuckGo.
#
# @meta require-tools aichat
#
# @option --query! <STRING>              Search query string (required)
# @option --limit <NUM>                  Maximum search results (default: 5)
# @option --mode <MODE>                  Execution mode: summary/detailed (default: summary)
# @option --engine <ENGINE>              Search mode: web/instant/hybrid (default: web)
# @option --use-cache                    Enable result caching for repeated queries
# @option --no-color                     Disable ANSI color output
# @option --verbose                      Enable detailed debug log output
# @option --log-file <PATH>              Write debug logs to a file
# @option --output-format <FMT>          Output format for LLM: json|jsonl|csv|md (default: json)
# @option --timeout <SEC>                Request timeout in seconds (default: 15)
# @option --workers <NUM>                Number of parallel workers (default: 1)
# ==============================================================================

from __future__ import annotations

import argparse
import concurrent.futures
import csv
import hashlib
import html
import io
import json
import logging
import os
import pickle
import re
import signal
import sys
import time
import urllib.parse
import urllib.request
from datetime import datetime, timedelta
from enum import Enum
from html.parser import HTMLParser
from pathlib import Path
from typing import Any, Literal, Optional

# Optional BeautifulSoup import for superior HTML parsing resilience
try:
    from bs4 import BeautifulSoup

    HAS_BS4 = True
except ImportError:
    HAS_BS4 = False

# ---------------------------------------------------------------------------
# 1️⃣  Version & Exit-Code Constants
# ---------------------------------------------------------------------------
__version__ = "2.3.1-ENHANCED"
__all__ = ["ToolCache", "__version__", "execute_tool", "run"]


class EXIT_CODE(Enum):
    SUCCESS = 0
    ERROR = 1
    INVALID_INPUT = 127
    INTERRUPTED = 130


EXIT_SUCCESS = EXIT_CODE.SUCCESS.value
EXIT_ERROR = EXIT_CODE.ERROR.value
EXIT_INVALID_INPUT = EXIT_CODE.INVALID_INPUT.value
EXIT_INTERRUPTED = EXIT_CODE.INTERRUPTED.value


# ---------------------------------------------------------------------------
# 2️⃣  Enums & Custom JSON Encoder
# ---------------------------------------------------------------------------
class ExecutionMode(str, Enum):
    SUMMARY = "summary"
    DETAILED = "detailed"


class ToolJSONEncoder(json.JSONEncoder):
    """Serialize Path, Enum, datetime, bytes, sets, etc. safely."""

    def default(self, obj: Any) -> Any:
        if isinstance(obj, Path):
            return str(obj)
        if isinstance(obj, Enum):
            return obj.value
        if isinstance(obj, datetime):
            return obj.isoformat()
        if isinstance(obj, timedelta):
            return obj.total_seconds()
        if isinstance(obj, bytes):
            return obj.decode("utf-8", errors="replace")
        if isinstance(obj, (set, frozenset)):
            return list(obj)
        return super().default(obj)


# ---------------------------------------------------------------------------
# 3️⃣  Terminal UI Helpers (color handling)
# ---------------------------------------------------------------------------
NEON_CYAN = "\033[38;5;51m"
NEON_GREEN = "\033[38;5;46m"
NEON_RED = "\033[38;5;196m"
NEON_YELLOW = "\033[38;5;226m"
NEON_PURPLE = "\033[38;5;129m"
NEON_PINK = "\033[38;5;198m"
RESET = "\033[0m"
BOLD = "\033[1m"
DIM = "\033[2m"

_ANSI_RE = re.compile(r"\x1b(?:[@-Z\\-_]|\[[0-9;]*[ -/]*[@-~])|\033\[[0-9;?]*[a-zA-Z]")


def _strip_ansi(text: str) -> str:
    return _ANSI_RE.sub("", text)


def _is_tty() -> bool:
    return sys.stderr.isatty() and os.environ.get("TERM", "").lower() not in (
        "dumb",
        "",
    )


def _cprint(
    text: str,
    file: Any = None,
    *,
    no_color: bool = False,
    end: str = "\n",
) -> None:
    target = file or sys.stderr
    if no_color or not _is_tty():
        text = _strip_ansi(text)
    print(text, file=target, flush=True, end=end)


def print_human_readable_ui(data: dict[str, Any], *, no_color: bool = False) -> None:
    """Render a colourful box for human users on stderr."""
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
        f"{NEON_PURPLE}│{RESET} {NEON_PINK}⚡ [DUCKDUCKGO SEARCH v{__version__}]{RESET} "
        f"{status_color}{BOLD}{status_symbol} {status_text}{RESET}"
    )
    _cprint(f"{NEON_PURPLE}├{border}┤{RESET}")
    _cprint(
        f"{NEON_PURPLE}│{RESET} {NEON_CYAN}Query:{RESET}    {data.get('query', 'N/A')}"
    )
    _cprint(
        f"{NEON_PURPLE}│{RESET} {NEON_CYAN}Engine:{RESET}   {data.get('engine', 'web')}"
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

    if not success and "error" in data:
        _cprint(f"{NEON_PURPLE}├{border}┤{RESET}")
        _cprint(f"{NEON_PURPLE}│{RESET} {NEON_RED}Error:{RESET}    {data['error']}")

    instant = data.get("instant_answer")
    if instant and instant.get("abstract"):
        _cprint(f"{NEON_PURPLE}├{border}┤{RESET}")
        _cprint(
            f"{NEON_PURPLE}│{RESET} {BOLD}Instant Answer ({instant.get('source', 'DuckDuckGo')}):{RESET}"
        )
        _cprint(f"{NEON_PURPLE}│{RESET} {DIM}{instant['abstract'][:120]}...{RESET}")

    results = data.get("results", [])
    if results:
        _cprint(f"{NEON_PURPLE}├{border}┤{RESET}")
        _cprint(f"{NEON_PURPLE}│{RESET} {BOLD}Top Results ({len(results)}):{RESET}")
        for item in results[:5]:
            _cprint(f"{NEON_PURPLE}│{RESET}   {NEON_CYAN}›{RESET} {item.get('title')}")
            _cprint(f"{NEON_PURPLE}│{RESET}     {DIM}{item.get('url')}{RESET}")
    _cprint(f"{NEON_PURPLE}╰{border}╯{RESET}")


# ---------------------------------------------------------------------------
# 4️⃣  Cache Manager (persistent, size-limited, TTL)
# ---------------------------------------------------------------------------
class ToolCache:
    """Thread-safe persistent cache with SHA-256 keys and a max-size limit."""

    _DEFAULT_MAX_FILES = 100
    _DEFAULT_TTL = 3600  # 1 hour

    def __init__(
        self,
        *,
        cache_dir: Optional[Path] = None,
        max_files: int = _DEFAULT_MAX_FILES,
        ttl: int = _DEFAULT_TTL,
    ) -> None:
        self.cache_dir = (
            Path(cache_dir) if cache_dir else Path.home() / ".cache" / "aichat_tools"
        )
        self.max_files = max_files
        self.ttl = ttl
        try:
            old_umask = os.umask(0o022)
            self.cache_dir.mkdir(parents=True, exist_ok=True)
            os.umask(old_umask)
        except Exception:
            pass

    def _key(self, data: str) -> str:
        return hashlib.sha256(data.encode("utf-8")).hexdigest()

    def _prune(self) -> None:
        try:
            all_files = sorted(
                self.cache_dir.glob("*.cache"),
                key=lambda p: p.stat().st_mtime,
            )
            while len(all_files) > self.max_files:
                oldest = all_files.pop(0)
                oldest.unlink(missing_ok=True)
        except Exception:
            pass

    def get(self, key_data: str) -> Optional[Any]:
        cache_file = self.cache_dir / f"{self._key(key_data)}.cache"
        if not cache_file.exists():
            return None
        try:
            if time.time() - cache_file.stat().st_mtime > self.ttl:
                cache_file.unlink(missing_ok=True)
                return None
            with open(cache_file, "rb") as fp:
                return pickle.load(fp)
        except Exception:
            return None

    def set(self, key_data: str, val: Any) -> None:
        cache_file = self.cache_dir / f"{self._key(key_data)}.cache"
        tmp_file = cache_file.with_suffix(".tmp")
        try:
            old_umask = os.umask(0o022)
            with open(tmp_file, "wb") as fp:
                pickle.dump(val, fp)
            os.umask(old_umask)
            tmp_file.replace(cache_file)
            self._prune()
        except Exception:
            if tmp_file.exists():
                tmp_file.unlink(missing_ok=True)


# ---------------------------------------------------------------------------
# 5️⃣  Graceful Signal Handling
# ---------------------------------------------------------------------------
class GracefulShutdown:
    """Restore original signal handlers after interruption."""

    def __init__(self) -> None:
        self.interrupted = False
        try:
            self._old_sigint = signal.signal(signal.SIGINT, self._handler)
            self._old_sigterm = signal.signal(signal.SIGTERM, self._handler)
        except ValueError:
            self._old_sigint = signal.SIG_DFL
            self._old_sigterm = signal.SIG_DFL

    def _handler(self, signum: int, frame: Any) -> None:
        self.interrupted = True

    def restore(self) -> None:
        try:
            signal.signal(signal.SIGINT, self._old_sigint)
            signal.signal(signal.SIGTERM, self._old_sigterm)
        except ValueError:
            pass


# ---------------------------------------------------------------------------
# 6️⃣  HTTP Helpers (with retries, timeout, user-agent)
# ---------------------------------------------------------------------------
def _http_get(url: str, *, timeout: int, headers: dict[str, str]) -> str:
    max_retries = 3
    for attempt in range(1, max_retries + 1):
        try:
            req = urllib.request.Request(url, headers=headers)
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                return resp.read().decode(
                    resp.headers.get_content_charset() or "utf-8", errors="ignore"
                )
        except Exception as exc:
            if attempt == max_retries:
                raise
            backoff = 0.5 * (2 ** (attempt - 1))
            logging.debug("Request failed (%s). Retrying in %.1fs...", exc, backoff)
            time.sleep(backoff)


def _http_post(
    url: str, post_data: bytes, *, timeout: int, headers: dict[str, str]
) -> str:
    max_retries = 3
    for attempt in range(1, max_retries + 1):
        try:
            req = urllib.request.Request(
                url, data=post_data, headers=headers, method="POST"
            )
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                return resp.read().decode(
                    resp.headers.get_content_charset() or "utf-8", errors="ignore"
                )
        except Exception as exc:
            if attempt == max_retries:
                raise
            backoff = 0.5 * (2 ** (attempt - 1))
            logging.debug("POST failed (%s). Retrying in %.1fs...", exc, backoff)
            time.sleep(backoff)


# ---------------------------------------------------------------------------
# 7️⃣  Instant-Answer Fetcher
# ---------------------------------------------------------------------------
def fetch_instant_answer(query: str, *, timeout: int) -> dict[str, Any]:
    api_url = "https://api.duckduckgo.com/"
    params = {
        "q": query,
        "format": "json",
        "no_redirect": "1",
        "no_html": "1",
    }
    api_url += "?" + urllib.parse.urlencode(params)
    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
        )
    }

    try:
        payload = _http_get(api_url, timeout=timeout, headers=headers)
        data = json.loads(payload)
        related = []
        for topic in data.get("RelatedTopics", []):
            if "Text" in topic and "FirstURL" in topic:
                related.append({"text": topic["Text"], "url": topic["FirstURL"]})

        return {
            "abstract": data.get("AbstractText", ""),
            "source": data.get("AbstractSource", ""),
            "url": data.get("AbstractURL", ""),
            "heading": data.get("Heading", ""),
            "related_topics": related[:5],
        }
    except Exception:
        return {
            "abstract": "",
            "source": "",
            "url": "",
            "heading": "",
            "related_topics": [],
        }


# ---------------------------------------------------------------------------
# 8️⃣  Web-Result Fetcher (HTML parsing)
# ---------------------------------------------------------------------------
class DDGHTMLParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.results: list[dict[str, str]] = []
        self.in_result = False
        self.in_title = False
        self.in_snippet = False
        self.current_title: list[str] = []
        self.current_snippet: list[str] = []
        self.current_link: str = ""

    def handle_starttag(self, tag: str, attrs: list[tuple[str, Optional[str]]]) -> None:
        attrs_dict = dict(attrs)
        cls = attrs_dict.get("class") or ""

        if tag == "div" and (("result" in cls) or ("web-result" in cls)):
            self.in_result = True
            self.current_title = []
            self.current_snippet = []
            self.current_link = ""

        if self.in_result:
            if tag == "a" and "result__a" in cls:
                self.in_title = True
                self.current_link = attrs_dict.get("href") or ""
            elif (tag == "a" or tag == "div") and "result__snippet" in cls:
                self.in_snippet = True

    def handle_endtag(self, tag: str) -> None:
        if tag == "a" and self.in_title:
            self.in_title = False
        elif (tag == "a" or tag == "div") and self.in_snippet:
            self.in_snippet = False
        elif tag == "div" and self.in_result:
            title_str = html.unescape("".join(self.current_title)).strip()
            snippet_str = html.unescape("".join(self.current_snippet)).strip()
            snippet_str = re.sub(r"\s+", " ", snippet_str)

            link = self.current_link
            if "/l/?" in link or "uddg=" in link:
                parsed = urllib.parse.parse_qs(urllib.parse.urlparse(link).query)
                if "uddg" in parsed:
                    link = parsed["uddg"][0]
            elif link.startswith("//"):
                link = "https:" + link

            if title_str and link and not link.startswith("https://duckduckgo.com/"):
                self.results.append(
                    {"title": title_str, "url": link, "snippet": snippet_str}
                )

            self.in_result = False

    def handle_data(self, data: str) -> None:
        if self.in_title:
            self.current_title.append(data)
        elif self.in_snippet:
            self.current_snippet.append(data)


def fetch_web_results(query: str, limit: int, *, timeout: int) -> list[dict[str, str]]:
    url = "https://html.duckduckgo.com/html/"
    post_data = urllib.parse.urlencode({"q": query, "b": "", "kl": ""}).encode("utf-8")
    headers = {
        "User-Agent": "Lynx/2.8.9rel.1 libwww-FM/2.14 SSL-MM/1.4.1 OpenSSL/1.1.1w",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.9",
        "Content-Type": "application/x-www-form-urlencoded",
        "Cookie": "SRCHHPGUSR=ADLT=OFF; adlt=off; kl=us-en",
    }

    try:
        payload = _http_post(url, post_data, timeout=timeout, headers=headers)
    except Exception as exc:
        raise RuntimeError(f"Failed to fetch web results: {exc}")

    if "anomaly_detector" in payload or (
        ("Notice:" in payload) and ("bots" in payload)
    ):
        raise RuntimeError("DuckDuckGo has rate-limited or blocked the request.")

    results: list[dict[str, str]] = []

    if HAS_BS4:
        soup = BeautifulSoup(payload, "html.parser")
        for res in soup.find_all("div", class_=re.compile(r"result|web-result")):
            title_el = res.find("a", class_="result__a")
            if not title_el:
                continue
            link = title_el.get("href", "")
            if "/l/?" in link or "uddg=" in link:
                parsed_qs = urllib.parse.parse_qs(urllib.parse.urlparse(link).query)
                if "uddg" in parsed_qs:
                    link = parsed_qs["uddg"][0]
            elif link.startswith("//"):
                link = "https:" + link

            title_text = html.unescape(title_el.get_text(strip=True))
            snippet_el = res.find("a", class_="result__snippet") or res.find(
                "div", class_="result__snippet"
            )
            snippet_text = (
                html.unescape(snippet_el.get_text(strip=True)) if snippet_el else ""
            )
            snippet_text = re.sub(r"\s+", " ", snippet_text)

            if title_text and link and not link.startswith("https://duckduckgo.com/"):
                results.append(
                    {"title": title_text, "url": link, "snippet": snippet_text}
                )

            if len(results) >= limit:
                break
    else:
        parser = DDGHTMLParser()
        parser.feed(payload)
        results = parser.results[:limit]

    # Deduplicate results by URL
    seen = set()
    unique = []
    for r in results:
        if r["url"] not in seen:
            seen.add(r["url"])
            unique.append(r)
    return unique[:limit]


# ---------------------------------------------------------------------------
# 9️⃣  Core Execution Engine (with parallel worker support)
# ---------------------------------------------------------------------------
def execute_tool(
    query: str,
    *,
    limit: Optional[int] = None,
    mode: ExecutionMode = ExecutionMode.SUMMARY,
    engine: str = "web",
    use_cache: bool = False,
    no_color: bool = False,
    verbose: bool = False,
    timeout: int = 15,
    workers: int = 1,
) -> dict[str, Any]:
    """Run a single search and return a structured response dict."""
    start_time = time.monotonic()
    limit_val = limit if (limit is not None and limit > 0) else 5
    limit_val = min(limit_val, 20)

    clean_query = query.strip()
    if not clean_query:
        return {
            "success": False,
            "error": "Query string cannot be empty.",
            "exit_code": EXIT_INVALID_INPUT,
            "duration_ms": 0.0,
        }

    cache = ToolCache()
    cache_key = f"ddg_master:{clean_query}:{limit_val}:{mode.value}:{engine}:{timeout}:{workers}"
    if use_cache:
        cached = cache.get(cache_key)
        if cached:
            cached["cached"] = True
            return cached

    shutdown = GracefulShutdown()
    try:
        instant_data: dict[str, Any] = {}
        web_results: list[dict[str, str]] = []

        # Utilize parallel threads when hybrid mode or multi-workers are requested
        max_threads = max(1, workers)
        if engine == "hybrid":
            with concurrent.futures.ThreadPoolExecutor(
                max_workers=max_threads
            ) as executor:
                f_instant = executor.submit(
                    fetch_instant_answer, clean_query, timeout=timeout
                )
                f_web = executor.submit(
                    fetch_web_results, clean_query, limit_val, timeout=timeout
                )
                instant_data = f_instant.result()
                web_results = f_web.result()
        elif engine == "instant":
            instant_data = fetch_instant_answer(clean_query, timeout=timeout)
        else:
            web_results = fetch_web_results(clean_query, limit_val, timeout=timeout)

        duration_ms = round((time.monotonic() - start_time) * 1000, 2)

        result: dict[str, Any] = {
            "success": True,
            "query": clean_query,
            "engine": engine,
            "mode": mode.value,
            "count": len(web_results),
            "instant_answer": instant_data if instant_data.get("abstract") else None,
            "results": web_results
            if mode == ExecutionMode.DETAILED
            else web_results[:limit_val],
            "cached": False,
            "duration_ms": duration_ms,
            "exit_code": EXIT_SUCCESS,
        }

        if use_cache:
            cache.set(cache_key, result)

        return result

    except Exception as exc:
        duration_ms = round((time.monotonic() - start_time) * 1000, 2)
        return {
            "success": False,
            "query": clean_query,
            "engine": engine,
            "error": f"Search execution failed: {exc}",
            "exit_code": EXIT_ERROR,
            "duration_ms": duration_ms,
        }
    finally:
        shutdown.restore()


# ---------------------------------------------------------------------------
# 🔟  LLM Output Writer (with Markdown and CSV support)
# ---------------------------------------------------------------------------
def write_llm_output(data: dict[str, Any], *, output_format: str) -> None:
    """Write the payload to LLM_OUTPUT (or stdout) respecting json, jsonl, csv, or md."""
    out_path = os.environ.get("LLM_OUTPUT", "/dev/stdout")

    if output_format == "jsonl":
        payload = json.dumps(data, ensure_ascii=False, cls=ToolJSONEncoder) + "\n"
    elif output_format == "md":
        lines = [
            f"# Search Results: {data.get('query', '')}",
            f"**Engine:** {data.get('engine')} | **Duration:** {data.get('duration_ms')}ms\n",
        ]
        if data.get("instant_answer"):
            ia = data["instant_answer"]
            lines.append(f"## Instant Answer ({ia.get('source', 'DuckDuckGo')})")
            lines.append(f"{ia.get('abstract')}\n[Source Link]({ia.get('url')})\n")
        lines.append("## Web Results")
        for item in data.get("results", []):
            lines.append(
                f"- [{item.get('title')}]({item.get('url')})\n  > {item.get('snippet')}"
            )
        payload = "\n".join(lines) + "\n"
    elif output_format == "csv":
        output = io.StringIO()
        writer = csv.writer(output)
        writer.writerow(["Title", "URL", "Snippet"])
        for item in data.get("results", []):
            writer.writerow(
                [item.get("title", ""), item.get("url", ""), item.get("snippet", "")]
            )
        payload = output.getvalue()
    else:
        payload = (
            json.dumps(data, indent=2, ensure_ascii=False, cls=ToolJSONEncoder) + "\n"
        )

    if out_path in {"/dev/stdout", "/dev/fd/1", "-"}:
        sys.stdout.write(payload)
        sys.stdout.flush()
    else:
        try:
            Path(out_path).parent.mkdir(parents=True, exist_ok=True)
            with open(out_path, "a", encoding="utf-8") as fp:
                fp.write(payload)
        except OSError as err:
            sys.stderr.write(f"Failed writing to LLM_OUTPUT '{out_path}': {err}\n")
            sys.stdout.write(payload)
            sys.stdout.flush()


# ---------------------------------------------------------------------------
# 1️⃣1️⃣ Programmatic Entrypoint (AIChat compatible)
# ---------------------------------------------------------------------------
def run(
    query: str,
    limit: Optional[int] = 5,
    mode: Literal["summary", "detailed"] = "summary",
    engine: str = "web",
    use_cache: bool = False,
    no_color: bool = False,
    verbose: bool = False,
    log_file: Optional[str] = None,
    output_format: str = "json",
    timeout: int = 15,
    workers: int = 1,
) -> dict[str, Any]:
    """Programmatic entry point for AIChat integration."""
    result = execute_tool(
        query=query,
        limit=limit,
        mode=ExecutionMode(mode),
        engine=engine,
        use_cache=use_cache,
        no_color=no_color,
        verbose=verbose,
        timeout=timeout,
        workers=workers,
    )
    print_human_readable_ui(result, no_color=no_color)
    write_llm_output(result, output_format=output_format)
    return result


# ---------------------------------------------------------------------------
# 1️⃣2️⃣ CLI Argument Parser & Main
# ---------------------------------------------------------------------------
def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="ddg_search.py",
        description=f"AIChat DuckDuckGo Master Search Tool v{__version__}",
    )
    parser.add_argument(
        "--query", "-q", required=True, help="Search query string (required)"
    )
    parser.add_argument(
        "--limit", type=int, default=5, help="Maximum search results (default: 5)"
    )
    parser.add_argument(
        "--mode",
        choices=[ExecutionMode.SUMMARY.value, ExecutionMode.DETAILED.value],
        default=ExecutionMode.SUMMARY.value,
        help="Execution mode: summary or detailed (default: summary)",
    )
    parser.add_argument(
        "--engine",
        choices=["web", "instant", "hybrid"],
        default="web",
        help="Search engine mode: web, instant, or hybrid (default: web)",
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
        help="Disable ANSI colour output",
    )
    parser.add_argument(
        "--verbose", "-v", action="store_true", help="Enable debug logging"
    )
    parser.add_argument(
        "--log-file", help="Path to a file where debug logs will be appended"
    )
    parser.add_argument(
        "--output-format",
        choices=["json", "jsonl", "csv", "md"],
        default="json",
        help="Output format for LLM integration (default: json)",
    )
    parser.add_argument(
        "--timeout",
        type=int,
        default=15,
        help="Request timeout in seconds (default: 15)",
    )
    parser.add_argument(
        "--workers", type=int, default=1, help="Number of parallel workers (default: 1)"
    )
    return parser


def main() -> None:
    parser = _build_parser()
    args = parser.parse_args()

    log_level = logging.DEBUG if args.verbose else logging.INFO
    logging.basicConfig(
        level=log_level,
        format="%(asctime)s %(levelname)s %(message)s",
        handlers=[logging.StreamHandler(sys.stderr)],
    )
    if args.log_file:
        file_handler = logging.FileHandler(args.log_file, mode="a", encoding="utf-8")
        file_handler.setLevel(logging.DEBUG)
        file_handler.setFormatter(
            logging.Formatter("%(asctime)s %(levelname)s %(message)s")
        )
        logging.getLogger().addHandler(file_handler)

    res = run(
        query=args.query,
        limit=args.limit,
        mode=args.mode,
        engine=args.engine,
        use_cache=args.use_cache,
        no_color=args.no_color,
        verbose=args.verbose,
        log_file=args.log_file,
        output_format=args.output_format,
        timeout=args.timeout,
        workers=args.workers,
    )
    sys.exit(res.get("exit_code", EXIT_SUCCESS))


if __name__ == "__main__":
    main()
