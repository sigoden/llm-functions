#!/usr/bin/env python3
# @describe Web search with multiple backends, optional crawl (News RSS, DDG, Bing, SearXNG)
"""
google_search.py — Web search tool with optional content crawling.

Compatible with llm-functions / webx: run() returns str (JSON or ERROR:...).

Backends (order): Brave API (BRAVE_API_KEY), Google News RSS (news queries),
DuckDuckGo HTML, Bing, SearXNG (SEARXNG_URL / discovery / fallbacks), Brave HTML, Google HTML.
"""

from __future__ import annotations

import argparse
import base64
import json
import logging
import os
import random
import re
import shutil
import subprocess
import sys
import time
import urllib.parse
import xml.etree.ElementTree as ET
from dataclasses import asdict, dataclass
from html.parser import HTMLParser
from typing import Any, Dict, List, Literal, Optional, Tuple

try:
    from bs4 import BeautifulSoup

    HAS_BS4 = True
except ImportError:
    HAS_BS4 = False

# =========================================================================
# Constants
# =========================================================================

_USER_AGENT_POOL = [
    (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/125.0.0.0 Safari/537.36"
    ),
    (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 14_5) "
        "AppleWebKit/605.1.15 (KHTML, like Gecko) "
        "Version/17.4 Safari/605.1.15"
    ),
    ("Mozilla/5.0 (X11; Linux x86_64; rv:128.0) Gecko/20100101 Firefox/128.0"),
    (
        "Mozilla/5.0 (Linux; Android 14; Pixel 8) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/125.0.0.0 Mobile Safari/537.36"
    ),
]

_BOT_DETECTION_PATTERNS = [
    "captcha",
    "verify you are human",
    "unusual traffic",
    "automated requests",
    "challenge-platform",
    "please complete the security check",
    "cf-challenge",
    "just a moment",
]

_BLOCKED_TITLE_PATTERNS = [
    "<title>access denied</title>",
    "<title>403 forbidden</title>",
    "<title>sorry</title>",
    "<title>blocked</title>",
    "<title>just a moment</title>",
    "<title>attention required</title>",
]

SEARX_SPACE_INSTANCES_URL = "https://searx.space/data/instances.json"
SEARX_PROBE_QUERY = "searxng"

CURLIE_BIN = shutil.which("curlie")
WGET2_BIN = shutil.which("wget2")
CURL_BIN = shutil.which("curl")
WGET_BIN = shutil.which("wget")

# =========================================================================
# Data structures (webx / llm-functions output shape)
# =========================================================================


@dataclass
class SearchResult:
    position: int
    url: str
    title: str = ""
    snippet: str = ""
    content: str = ""
    error: str = ""


@dataclass
class SearchMetadata:
    query: str = ""
    engine_used: str = ""
    fetcher_tool: str = ""
    fetcher_version: str = ""
    total_results: int = 0
    search_time_ms: int = 0
    had_bot_detection: bool = False
    retries_used: int = 0


# =========================================================================
# Tool detection
# =========================================================================


def _preferred_fetcher() -> str:
    if CURLIE_BIN:
        return "curlie"
    if WGET2_BIN:
        return "wget2"
    if CURL_BIN:
        return "curl"
    if WGET_BIN:
        return "wget"
    raise OSError("No HTTP tool found. Install: curlie, wget2, curl, or wget")


def _get_tool_version(tool_name: str) -> Optional[str]:
    binary_map = {
        "curlie": CURLIE_BIN,
        "wget2": WGET2_BIN,
        "curl": CURL_BIN,
        "wget": WGET_BIN,
    }
    binary = binary_map.get(tool_name)
    if not binary:
        return None
    try:
        result = subprocess.run(
            [binary, "--version"],
            capture_output=True,
            text=True,
            timeout=5,
        )
        lines = result.stdout.strip().split("\n")
        return lines[0] if lines else None
    except Exception:
        return None


# =========================================================================
# HTML text extractor (stdlib fallback)
# =========================================================================


class ContentExtractor(HTMLParser):
    def __init__(self, max_length: int = 3000):
        super().__init__()
        self.max_length = max_length
        self.content: List[str] = []
        self._skip = False
        self.current_length = 0
        self._title_parts: List[str] = []
        self._in_title = False

    def handle_starttag(self, tag, attrs):
        if tag in ("script", "style", "noscript", "svg", "path"):
            self._skip = True
        elif tag == "title":
            self._in_title = True
        elif tag in (
            "p",
            "div",
            "h1",
            "h2",
            "h3",
            "h4",
            "h5",
            "h6",
            "li",
            "span",
            "td",
            "th",
            "blockquote",
            "pre",
            "article",
            "section",
            "main",
        ):
            if self.content and self.content[-1] not in (" ", "\n"):
                self.content.append(" ")
        elif tag == "br":
            self.content.append("\n")

    def handle_endtag(self, tag):
        if tag in ("script", "style", "noscript", "svg"):
            self._skip = False
        elif tag == "title":
            self._in_title = False
        elif tag in ("p", "div", "li", "tr", "blockquote", "article", "section"):
            if self.content and self.content[-1] != "\n":
                self.content.append("\n")

    def handle_data(self, data):
        if self._in_title:
            self._title_parts.append(data.strip())
        if not self._skip and data.strip():
            for word in data.split():
                if self.current_length >= self.max_length:
                    return
                self.content.append(word)
                self.current_length += len(word) + 1

    def get_text(self) -> str:
        return " ".join(" ".join(self.content).split())

    def get_title(self) -> str:
        return " ".join(self._title_parts).strip()


# =========================================================================
# Response validation
# =========================================================================


def _detect_bot_challenge(html: str) -> bool:
    html_lower = html.lower()
    for pattern in _BOT_DETECTION_PATTERNS:
        if pattern in html_lower:
            return True
    for title_pattern in _BLOCKED_TITLE_PATTERNS:
        if title_pattern in html_lower:
            return True
    return False


def _validate_json_response(body: Optional[str]) -> Tuple[bool, str]:
    if body is None:
        return False, "Response is None"
    stripped = body.strip()
    if not stripped:
        return False, "Empty JSON body"
    if stripped.lstrip().startswith("<"):
        return False, "HTML instead of JSON (format=json likely disabled — 403)"
    try:
        data = json.loads(stripped)
    except json.JSONDecodeError as e:
        return False, f"Not JSON: {e}"
    if not isinstance(data, dict):
        return False, "JSON root is not an object"
    return True, ""


def _validate_response(html: Optional[str], url: str) -> Tuple[bool, str]:
    if html is None:
        return False, "Response is None"
    stripped = html.strip()
    if not stripped:
        return False, "Empty response body"
    if (
        len(stripped) < 80
        and not stripped.startswith("<?xml")
        and not stripped.startswith("<rss")
    ):
        return False, f"Suspiciously short response ({len(stripped)} bytes)"
    if _detect_bot_challenge(stripped):
        return False, "Bot detection / CAPTCHA page detected"
    return True, ""


def _strip_html_snippet(text: str) -> str:
    if not text or "<" not in text:
        return text.strip()
    if HAS_BS4:
        return BeautifulSoup(text, "html.parser").get_text(" ", strip=True)
    return re.sub(r"<[^>]+>", " ", text).strip()


# =========================================================================
# HTTP fetch
# =========================================================================


def _random_user_agent() -> str:
    return random.choice(_USER_AGENT_POOL)


def _build_fetch_command(
    url: str,
    fetcher: str,
    timeout: int,
    user_agent: str,
    extra_headers: Optional[Dict[str, str]] = None,
    http11_only: bool = True,
    method: str = "GET",
    data: Optional[Dict[str, str]] = None,
) -> List[str]:
    headers = {
        "User-Agent": user_agent,
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.5",
        "Accept-Encoding": "gzip, deflate",
        "DNT": "1",
        "Connection": "keep-alive",
        "Upgrade-Insecure-Requests": "1",
    }
    if extra_headers:
        headers.update(extra_headers)

    if fetcher in ("curlie", "curl"):
        binary = CURLIE_BIN if fetcher == "curlie" else CURL_BIN
        cmd = [
            binary,
            "-s",
            "-S",
            "-L",
            "--compressed",
            "--max-time",
            str(timeout),
            "--max-redirs",
            "10",
        ]
        if http11_only:
            cmd.append("--http1.1")
        for key, val in headers.items():
            cmd.extend(["-H", f"{key}: {val}"])
        if method == "POST" and data:
            cmd.extend(["-X", "POST", "-d", urllib.parse.urlencode(data)])
        cmd.append(url)

    elif fetcher == "wget2":
        cmd = [
            WGET2_BIN,
            "-q",
            "-O",
            "-",
            f"--timeout={timeout}",
            "--max-redirect=10",
            "--compression=auto",
        ]
        for key, val in headers.items():
            cmd.append(f"--header={key}: {val}")
        if method == "POST" and data:
            cmd.append(f"--post-data={urllib.parse.urlencode(data)}")
        cmd.append(url)

    else:
        cmd = [WGET_BIN, "-q", "-O", "-", f"--timeout={timeout}", "--max-redirect=10"]
        for key, val in headers.items():
            cmd.append(f"--header={key}: {val}")
        if method == "POST" and data:
            cmd.append(f"--post-data={urllib.parse.urlencode(data)}")
        cmd.append(url)

    return cmd


def _fetch_url(
    url: str,
    timeout: int = 10,
    verbose: bool = False,
    user_agent: Optional[str] = None,
    extra_headers: Optional[Dict[str, str]] = None,
    http11_only: bool = True,
    method: str = "GET",
    data: Optional[Dict[str, str]] = None,
    expect_json: bool = False,
    skip_validation: bool = False,
) -> Optional[str]:
    logger = logging.getLogger(__name__)
    fetcher = _preferred_fetcher()
    ua = user_agent or _random_user_agent()
    cmd = _build_fetch_command(
        url=url,
        fetcher=fetcher,
        timeout=timeout,
        user_agent=ua,
        extra_headers=extra_headers,
        http11_only=http11_only,
        method=method,
        data=data,
    )
    if verbose:
        logger.debug("Fetch [%s]: %s", fetcher, " ".join(cmd))

    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout + 10)
    except subprocess.TimeoutExpired:
        logger.warning("Timeout for %s", url)
        return None
    except FileNotFoundError:
        logger.error("Binary missing for %s", fetcher)
        return None

    if proc.returncode != 0:
        logger.warning(
            "%s rc=%d for %s: %s",
            fetcher,
            proc.returncode,
            url,
            (proc.stderr or "")[:200],
        )
        return None

    if skip_validation:
        return proc.stdout

    if expect_json:
        ok, reason = _validate_json_response(proc.stdout)
    else:
        ok, reason = _validate_response(proc.stdout, url)

    if not ok:
        logger.warning("Invalid response from %s: %s", url, reason)
        return None
    return proc.stdout


def _fetch_url_with_retry(
    url: str,
    timeout: int = 10,
    max_retries: int = 2,
    verbose: bool = False,
    user_agent: Optional[str] = None,
    extra_headers: Optional[Dict[str, str]] = None,
    http11_only: bool = True,
    method: str = "GET",
    data: Optional[Dict[str, str]] = None,
    expect_json: bool = False,
    skip_validation: bool = False,
) -> Tuple[Optional[str], int]:
    logger = logging.getLogger(__name__)
    retries_used = 0
    for attempt in range(max_retries + 1):
        result = _fetch_url(
            url,
            timeout=timeout,
            verbose=verbose,
            user_agent=user_agent or _random_user_agent(),
            extra_headers=extra_headers,
            http11_only=http11_only,
            method=method,
            data=data,
            expect_json=expect_json,
            skip_validation=skip_validation,
        )
        if result is not None:
            return result, retries_used
        retries_used += 1
        if attempt < max_retries:
            time.sleep((2**attempt) + random.uniform(0.3, 1.2))
    return None, retries_used


# =========================================================================
# SearXNG instance discovery (searx.space)
# =========================================================================


def _searx_cache_path() -> str:
    custom = os.environ.get("SEARXNG_CACHE_FILE", "").strip()
    if custom:
        return custom
    home = os.path.expanduser("~")
    return os.path.join(home, ".cache", "webx_searxng_good.json")


def _load_cached_searx_bases() -> List[str]:
    path = _searx_cache_path()
    try:
        if not os.path.isfile(path):
            return []
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
        bases = data.get("bases", [])
        if isinstance(bases, list):
            return [str(b).rstrip("/") for b in bases if b][:10]
    except (OSError, json.JSONDecodeError, TypeError):
        pass
    return []


def _save_cached_searx_bases(bases: List[str]) -> None:
    if not bases:
        return
    path = _searx_cache_path()
    try:
        os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            json.dump(
                {"bases": bases[:10], "updated": int(time.time())},
                f,
                indent=2,
            )
    except OSError:
        pass


def _parse_searx_space_instances(raw: str) -> List[str]:
    """Extract base URLs from searx.space instances.json (schema-tolerant)."""
    urls: List[str] = []
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        return urls

    candidates: List[Any] = []
    if isinstance(data, dict):
        if "instances" in data:
            inst = data["instances"]
            if isinstance(inst, dict):
                candidates = list(inst.values())
            elif isinstance(inst, list):
                candidates = inst
        else:
            candidates = list(data.values())
    elif isinstance(data, list):
        candidates = data

    for item in candidates:
        if not isinstance(item, dict):
            continue
        url = (item.get("url") or item.get("base_url") or "").strip().rstrip("/")
        if not url.startswith("http"):
            continue
        status = str(item.get("status", "")).lower()
        if status in ("offline", "down", "error"):
            continue
        if url not in urls:
            urls.append(url)
    return urls


def _fetch_searx_space_url_list(timeout: int = 15, verbose: bool = False) -> List[str]:
    logger = logging.getLogger(__name__)
    html, _ = _fetch_url_with_retry(
        SEARX_SPACE_INSTANCES_URL,
        timeout=timeout,
        max_retries=1,
        verbose=verbose,
        expect_json=True,
        skip_validation=False,
    )
    if not html:
        if verbose:
            logger.warning("Could not fetch searx.space instances.json")
        return []
    return _parse_searx_space_instances(html)


def _probe_searx_base(base: str, timeout: int = 10, verbose: bool = False) -> int:
    """Return number of results if JSON works, else 0."""
    eq = urllib.parse.quote_plus(SEARX_PROBE_QUERY)
    url = f"{base}/search?q={eq}&format=json&language=en&safesearch=1"
    body, _ = _fetch_url_with_retry(
        url,
        timeout=timeout,
        max_retries=0,
        verbose=verbose,
        expect_json=True,
        extra_headers={"Accept": "application/json"},
    )
    if not body:
        return 0
    try:
        data = json.loads(body)
        results = data.get("results") or []
        return len(results) if isinstance(results, list) else 0
    except (json.JSONDecodeError, TypeError):
        return 0


def _discover_searx_instances(
    timeout: int = 10,
    max_probe: int = 12,
    min_results: int = 2,
    verbose: bool = False,
) -> List[str]:
    """
    Probe searx.space list + cache. Enabled when SEARXNG_DISCOVER=1
    or when no SEARXNG_URL / SEARXNG_URLS set.
    """
    logger = logging.getLogger(__name__)
    good: List[str] = []

    for base in _load_cached_searx_bases():
        n = _probe_searx_base(base, timeout=min(timeout, 8), verbose=verbose)
        if n >= min_results and base not in good:
            good.append(base)
            logger.info("SearXNG cache hit: %s (%d results)", base, n)
        if len(good) >= 3:
            _save_cached_searx_bases(good)
            return good

    space_urls = _fetch_searx_space_url_list(timeout=timeout, verbose=verbose)
    random.shuffle(space_urls)
    probed = 0
    for base in space_urls:
        if probed >= max_probe:
            break
        probed += 1
        if base in good:
            continue
        n = _probe_searx_base(base, timeout=timeout, verbose=verbose)
        if n >= min_results:
            good.append(base)
            logger.info("SearXNG discovered: %s (%d results)", base, n)
        if len(good) >= 3:
            break

    if good:
        _save_cached_searx_bases(good)
    return good


# =========================================================================
# Content extraction
# =========================================================================


def _extract_text_bs4(html: str, max_length: int = 3000) -> Tuple[str, str]:
    soup = BeautifulSoup(html, "html.parser")
    title_tag = soup.find("title")
    title = title_tag.get_text(strip=True) if title_tag else ""
    for tag in soup(["script", "style", "noscript", "svg", "nav", "footer", "iframe"]):
        tag.decompose()
    main_content = (
        soup.find("main")
        or soup.find("article")
        or soup.find("div", {"id": re.compile(r"content|main", re.I)})
        or soup.body
        or soup
    )
    text = main_content.get_text(separator=" ", strip=True)
    text = re.sub(r"\s+", " ", text)
    return text[:max_length], title


def _extract_text_stdlib(html: str, max_length: int = 3000) -> Tuple[str, str]:
    parser = ContentExtractor(max_length)
    parser.feed(html)
    return parser.get_text(), parser.get_title()


def _extract_text(html: str, max_length: int = 3000) -> Tuple[str, str]:
    if HAS_BS4:
        return _extract_text_bs4(html, max_length)
    return _extract_text_stdlib(html, max_length)


def crawl_url(
    url: str,
    timeout: int = 10,
    max_length: int = 3000,
    verbose: bool = False,
    max_retries: int = 1,
) -> str:
    html, retries = _fetch_url_with_retry(
        url,
        timeout=timeout,
        max_retries=max_retries,
        verbose=verbose,
    )
    if html is None:
        return f"[Error: Failed to fetch {url} after {retries} retries]"
    if not re.search(r"<\s*(html|head|body|div|p)\b", html[:2000], re.I):
        return "[Non-HTML content detected]"
    text, _ = _extract_text(html, max_length)
    return text if text else "[No extractable text content]"


# =========================================================================
# Parsers
# =========================================================================


def _looks_like_news_query(q: str) -> bool:
    return bool(re.search(r"\b(news|headlines|breaking|latest)\b", q, re.I))


def _parse_google_news_rss(xml_text: str, num_results: int) -> List[Dict[str, str]]:
    results: List[Dict[str, str]] = []
    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError:
        return results
    for item in root.findall(".//item"):
        link_el = item.find("link")
        if link_el is None:
            continue
        url = (link_el.text or "").strip()
        if not url.startswith("http"):
            continue
        title_el = item.find("title")
        desc_el = item.find("description")
        snippet = ""
        if desc_el is not None and desc_el.text:
            snippet = _strip_html_snippet(desc_el.text)
        results.append(
            {
                "url": url,
                "title": (title_el.text or "").strip() if title_el is not None else "",
                "snippet": snippet,
            }
        )
        if len(results) >= num_results:
            break
    return results


def _parse_duckduckgo_bs4(html: str, num_results: int) -> List[Dict[str, str]]:
    results: List[Dict[str, str]] = []
    soup = BeautifulSoup(html, "html.parser")
    for r in soup.select(".result, .web-result, .results_links"):
        a_tag = r.select_one(".result__a, .result-link, a.result__url")
        snippet_tag = r.select_one(".result__snippet, .result-snippet")
        if not a_tag:
            continue
        href = a_tag.get("href", "")
        if "uddg=" in href:
            try:
                href = urllib.parse.parse_qs(urllib.parse.urlparse(href).query).get(
                    "uddg", [""]
                )[0]
            except Exception:
                continue
        href = urllib.parse.unquote(href)
        if not href.startswith("http") or "duckduckgo.com" in href:
            continue
        results.append(
            {
                "url": href,
                "title": a_tag.get_text(strip=True),
                "snippet": snippet_tag.get_text(strip=True) if snippet_tag else "",
            }
        )
        if len(results) >= num_results:
            break
    return results


def _parse_duckduckgo_regex(html: str, num_results: int) -> List[Dict[str, str]]:
    results: List[Dict[str, str]] = []
    seen: set[str] = set()
    for m in re.finditer(r"uddg=([^&\"\'>\s]+)", html):
        url = urllib.parse.unquote(m.group(1))
        if not url.startswith("http") or url in seen or "duckduckgo.com" in url:
            continue
        seen.add(url)
        results.append({"url": url, "title": "", "snippet": ""})
        if len(results) >= num_results:
            break
    return results


def _parse_duckduckgo(html: str, num_results: int) -> List[Dict[str, str]]:
    if HAS_BS4:
        r = _parse_duckduckgo_bs4(html, num_results)
        if r:
            return r
    return _parse_duckduckgo_regex(html, num_results)


def _decode_bing_url(href: str) -> str:
    if not href.startswith("https://www.bing.com/ck/a"):
        return href
    try:
        params = urllib.parse.parse_qs(urllib.parse.urlparse(href).query)
        if "u" not in params or not params["u"]:
            return href
        encoded_url = params["u"][0]
        b64_part = encoded_url
        for prefix in ("a1L", "a1", "a"):
            if b64_part.startswith(prefix) and prefix != "aHR0c":
                b64_part = b64_part[len(prefix) :]
                break
        if not b64_part.startswith("aHR0"):
            if encoded_url.startswith("a1"):
                b64_part = encoded_url[2:]
            elif encoded_url.startswith("a"):
                b64_part = encoded_url[1:]
        b64_part = re.sub(r"[^a-zA-Z0-9+/=]", "", b64_part.strip())
        padding = len(b64_part) % 4
        if padding:
            b64_part += "=" * (4 - padding)
        decoded_url = base64.b64decode(b64_part).decode("utf-8", errors="replace")
        return decoded_url if decoded_url.startswith("http") else href
    except Exception:
        return href


def _parse_bing_bs4(html: str, num_results: int) -> List[Dict[str, str]]:
    results: List[Dict[str, str]] = []
    if not HAS_BS4:
        return results
    soup = BeautifulSoup(html, "html.parser")
    seen: set[str] = set()
    for result in soup.select(".b_algo"):
        h2 = result.select_one("h2")
        if not h2:
            continue
        title_link = h2.select_one("a")
        if not title_link:
            continue
        clean_url = _decode_bing_url(title_link.get("href", ""))
        if not clean_url.startswith("http") or clean_url in seen:
            continue
        seen.add(clean_url)
        snippet = ""
        p = result.select_one("p")
        if p:
            snippet = p.get_text(strip=True)
        results.append(
            {
                "url": clean_url,
                "title": title_link.get_text(strip=True),
                "snippet": snippet,
            }
        )
        if len(results) >= num_results:
            break
    return results


def _parse_bing_regex(html: str, num_results: int) -> List[Dict[str, str]]:
    results: List[Dict[str, str]] = []
    seen: set[str] = set()
    for m in re.finditer(
        r'<li[^>]*class="[^"]*b_algo[^"]*"[^>]*>.*?<a[^>]+href="([^"]+)"[^>]*>([^<]*)</a>',
        html,
        re.DOTALL | re.I,
    ):
        url = _decode_bing_url(m.group(1))
        title = re.sub(r"\s+", " ", m.group(2)).strip()
        if not url.startswith("http") or url in seen:
            continue
        seen.add(url)
        results.append({"url": url, "title": title, "snippet": ""})
        if len(results) >= num_results:
            return results
    return results


def _parse_bing(html: str, num_results: int) -> List[Dict[str, str]]:
    if HAS_BS4:
        r = _parse_bing_bs4(html, num_results)
        if r:
            return r
    return _parse_bing_regex(html, num_results)


def _parse_searx_json(html: str, num_results: int) -> List[Dict[str, str]]:
    results: List[Dict[str, str]] = []
    try:
        data = json.loads(html)
        for item in data.get("results", []):
            url = item.get("url", "")
            if not url.startswith("http"):
                continue
            results.append(
                {
                    "url": url,
                    "title": item.get("title", ""),
                    "snippet": item.get("content", "") or item.get("snippet", ""),
                }
            )
            if len(results) >= num_results:
                break
    except (json.JSONDecodeError, KeyError, TypeError):
        pass
    return results


def _parse_brave_bs4(html: str, num_results: int) -> List[Dict[str, str]]:
    results: List[Dict[str, str]] = []
    if not HAS_BS4:
        return results
    soup = BeautifulSoup(html, "html.parser")
    for r in soup.select(".snippet, .fdb"):
        a_tag = r.select_one("a[href]")
        if not a_tag:
            continue
        href = a_tag.get("href", "")
        if not href.startswith("http"):
            continue
        snippet_tag = r.select_one(".snippet-description")
        results.append(
            {
                "url": href,
                "title": a_tag.get_text(strip=True),
                "snippet": snippet_tag.get_text(strip=True) if snippet_tag else "",
            }
        )
        if len(results) >= num_results:
            break
    return results


def _parse_google_bs4(html: str, num_results: int) -> List[Dict[str, str]]:
    results: List[Dict[str, str]] = []
    seen: set[str] = set()
    soup = BeautifulSoup(html, "html.parser")
    skip_domains = (
        "google.com",
        "google.co.",
        "gstatic.com",
        "youtube.com",
        "accounts.google",
    )
    for a_tag in soup.select("a[href]"):
        href = a_tag.get("href", "")
        if "/url?q=" in href:
            try:
                url = urllib.parse.parse_qs(urllib.parse.urlparse(href).query).get(
                    "q", [""]
                )[0]
            except Exception:
                continue
        elif href.startswith("http"):
            url = href
        else:
            continue
        url = urllib.parse.unquote(url)
        if any(d in url for d in skip_domains) or url in seen:
            continue
        seen.add(url)
        results.append({"url": url, "title": a_tag.get_text(strip=True), "snippet": ""})
        if len(results) >= num_results:
            break
    return results


def _parse_google_regex(html: str, num_results: int) -> List[Dict[str, str]]:
    results: List[Dict[str, str]] = []
    seen: set[str] = set()
    for m in re.finditer(r'/url\?q=([^&"]+)', html):
        url = urllib.parse.unquote(m.group(1))
        if not url.startswith("http") or "google." in url or url in seen:
            continue
        seen.add(url)
        results.append({"url": url, "title": "", "snippet": ""})
        if len(results) >= num_results:
            break
    return results


def _parse_google(html: str, num_results: int) -> List[Dict[str, str]]:
    if HAS_BS4:
        r = _parse_google_bs4(html, num_results)
        if r:
            return r
    return _parse_google_regex(html, num_results)


# =========================================================================
# Brave Search API (optional)
# =========================================================================


def _search_brave_api(
    query: str, num_results: int, timeout: int
) -> List[Dict[str, str]]:
    api_key = os.environ.get("BRAVE_API_KEY", "").strip()
    if not api_key:
        return []
    eq = urllib.parse.quote_plus(query)
    url = f"https://api.search.brave.com/res/v1/web/search?q={eq}&count={min(num_results, 20)}"
    html, _ = _fetch_url_with_retry(
        url,
        timeout=timeout,
        max_retries=1,
        extra_headers={
            "Accept": "application/json",
            "X-Subscription-Token": api_key,
        },
        expect_json=True,
    )
    if not html:
        return []
    try:
        data = json.loads(html)
        out: List[Dict[str, str]] = []
        for item in data.get("web", {}).get("results", []):
            u = item.get("url", "")
            if u.startswith("http"):
                out.append(
                    {
                        "url": u,
                        "title": item.get("title", ""),
                        "snippet": item.get("description", ""),
                    }
                )
            if len(out) >= num_results:
                break
        return out
    except (json.JSONDecodeError, KeyError):
        return []


# =========================================================================
# Engine list
# =========================================================================


def _static_searx_fallback_bases() -> List[str]:
    return [
        "https://searx.tiekoetter.com",
        "https://searxng.site",
        "https://search.bladerunn.in",
        "https://searx.work",
    ]


def _searx_engine_configs(
    query: str,
    lang: str,
    searx_safe: str,
    timeout: int = 10,
    verbose: bool = False,
) -> List[Dict[str, Any]]:
    eq = urllib.parse.quote_plus(query)
    bases: List[str] = []

    custom = os.environ.get("SEARXNG_URL", "").strip().rstrip("/")
    if custom:
        bases.append(custom)
    for b in os.environ.get("SEARXNG_URLS", "").split(","):
        b = b.strip().rstrip("/")
        if b and b not in bases:
            bases.append(b)

    discover_env = os.environ.get("SEARXNG_DISCOVER", "").strip().lower()
    force_discover = discover_env in ("1", "true", "yes")
    auto_discover = force_discover or (
        not custom and not os.environ.get("SEARXNG_URLS", "").strip()
    )

    if auto_discover:
        max_probe = int(os.environ.get("SEARXNG_PROBE_MAX", "12") or "12")
        discovered = _discover_searx_instances(
            timeout=timeout,
            max_probe=max(3, min(max_probe, 25)),
            verbose=verbose,
        )
        for b in discovered:
            if b not in bases:
                bases.append(b)

    for b in _load_cached_searx_bases():
        if b not in bases:
            bases.append(b)

    for b in _static_searx_fallback_bases():
        if b not in bases:
            bases.append(b)

    engines: List[Dict[str, Any]] = []
    for base in bases[:8]:
        engines.append(
            {
                "name": f"SearXNG ({base})",
                "url": (
                    f"{base}/search?q={eq}&format=json"
                    f"&language={lang}&safesearch={searx_safe}"
                ),
                "method": "GET",
                "parser": _parse_searx_json,
                "is_json": True,
                "extra_headers": {"Accept": "application/json"},
                "skip_bot_check": True,
            }
        )
    return engines


def _build_search_engines(
    query: str,
    num_results: int,
    lang: str,
    region: str,
    safe_search: str = "moderate",
    timeout: int = 10,
    verbose: bool = False,
) -> List[Dict[str, Any]]:
    encoded_query = urllib.parse.quote_plus(query)
    searx_safe = {"strict": "2", "moderate": "1", "off": "0"}.get(safe_search, "1")
    google_safe = {"strict": "active", "moderate": "active", "off": "off"}.get(
        safe_search, "active"
    )

    engines: List[Dict[str, Any]] = []

    if _looks_like_news_query(query):
        engines.append(
            {
                "name": "Google News RSS",
                "url": (
                    "https://news.google.com/rss/search?"
                    f"q={encoded_query}&hl=en-US&gl=US&ceid=US:en"
                ),
                "method": "GET",
                "parser": _parse_google_news_rss,
                "is_json": False,
                "extra_headers": {
                    "Accept": "application/rss+xml, application/xml, */*"
                },
                "skip_bot_check": True,
                "skip_validation": True,
            }
        )

    engines.extend(
        [
            {
                "name": "DuckDuckGo HTML",
                "url": "https://html.duckduckgo.com/html/",
                "method": "POST",
                "data": {"q": query, "b": "", "kl": f"{region}-{lang}"},
                "parser": _parse_duckduckgo,
                "is_json": False,
                "extra_headers": {
                    "Content-Type": "application/x-www-form-urlencoded",
                    "Referer": "https://html.duckduckgo.com/",
                },
            },
            {
                "name": "Bing",
                "url": (
                    f"https://www.bing.com/search?q={encoded_query}"
                    f"&setlang={lang}&cc={region}"
                ),
                "method": "GET",
                "parser": _parse_bing,
                "is_json": False,
                "extra_headers": None,
            },
        ]
    )

    engines.extend(_searx_engine_configs(query, lang, searx_safe, timeout, verbose))

    engines.extend(
        [
            {
                "name": "Brave",
                "url": (
                    f"https://search.brave.com/search"
                    f"?q={encoded_query}&source=web&safesearch={safe_search}"
                ),
                "method": "GET",
                "parser": _parse_brave_bs4,
                "is_json": False,
                "extra_headers": None,
            },
            {
                "name": "Google",
                "url": (
                    f"https://www.google.com/search?q={encoded_query}"
                    f"&hl={lang}&gl={region}&num={num_results}&safe={google_safe}"
                ),
                "method": "GET",
                "parser": _parse_google,
                "is_json": False,
                "extra_headers": None,
            },
        ]
    )

    return engines


def _web_search(
    query: str,
    num_results: int = 10,
    lang: str = "en",
    region: str = "us",
    safe_search: str = "moderate",
    timeout: int = 10,
    max_retries: int = 2,
    verbose: bool = False,
) -> Tuple[List[Dict[str, str]], SearchMetadata]:
    logger = logging.getLogger(__name__)
    fetcher = _preferred_fetcher()
    metadata = SearchMetadata(
        query=query,
        fetcher_tool=fetcher,
        fetcher_version=_get_tool_version(fetcher) or "unknown",
    )
    start_time = time.monotonic()
    total_retries = 0

    api_results = _search_brave_api(query, num_results, timeout)
    if api_results:
        metadata.engine_used = "Brave API"
        metadata.total_results = len(api_results)
        metadata.search_time_ms = int((time.monotonic() - start_time) * 1000)
        return api_results, metadata

    engines = _build_search_engines(
        query,
        num_results,
        lang,
        region,
        safe_search,
        timeout,
        verbose,
    )

    for engine in engines:
        try:
            logger.info("Trying %s", engine["name"])
            html, retries = _fetch_url_with_retry(
                engine["url"],
                timeout=timeout,
                max_retries=max_retries,
                verbose=verbose,
                extra_headers=engine.get("extra_headers"),
                method=engine.get("method", "GET"),
                data=engine.get("data"),
                expect_json=engine.get("is_json", False),
                skip_validation=engine.get("skip_validation", False),
            )
            total_retries += retries

            if html is None:
                logger.warning("%s returned no usable response", engine["name"])
                continue

            if not engine.get("is_json") and not engine.get("skip_bot_check"):
                if _detect_bot_challenge(html):
                    logger.warning("%s returned bot detection page", engine["name"])
                    metadata.had_bot_detection = True
                    continue

            parsed = engine["parser"](html, num_results)
            if parsed:
                logger.info("Got %d results from %s", len(parsed), engine["name"])
                metadata.engine_used = engine["name"]
                metadata.total_results = len(parsed)
                metadata.retries_used = total_retries
                metadata.search_time_ms = int((time.monotonic() - start_time) * 1000)
                return parsed, metadata

            logger.warning("%s returned no parseable results", engine["name"])

        except Exception as e:
            logger.warning("%s failed: %s", engine["name"], e, exc_info=verbose)
            continue

    metadata.search_time_ms = int((time.monotonic() - start_time) * 1000)
    metadata.retries_used = total_retries
    return [], metadata


# =========================================================================
# Logging & validation
# =========================================================================


def setup_logging(verbose: bool = False) -> None:
    level = logging.DEBUG if verbose else logging.WARNING
    logging.basicConfig(
        level=level,
        format="%(asctime)s - %(levelname)s - %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )


def validate_query(query: str) -> str:
    if not query or not query.strip():
        raise ValueError("Search query cannot be empty")
    cleaned = re.sub(r"\s+", " ", query.strip())
    if len(cleaned) > 500:
        raise ValueError(f"Query too long ({len(cleaned)} chars, max 500)")
    return cleaned


# =========================================================================
# Main search (internal)
# =========================================================================


def search_google(
    query: str,
    num_results: int = 10,
    lang: str = "en",
    region: str = "us",
    safe_search: str = "moderate",
    pause: float = 2.0,
    crawl: bool = False,
    content_length: int = 3000,
    timeout: int = 10,
    verbose: bool = False,
) -> List[Dict[str, Any]]:
    setup_logging(verbose)
    logger = logging.getLogger(__name__)
    query = validate_query(query)

    raw_results, metadata = _web_search(
        query,
        num_results=num_results,
        lang=lang,
        region=region,
        safe_search=safe_search,
        timeout=timeout,
        max_retries=2,
        verbose=verbose,
    )

    if not raw_results:
        error_detail = "All search engines failed."
        if metadata.had_bot_detection:
            error_detail += (
                " Bot detection — pip install beautifulsoup4; set SEARXNG_URL"
                " or SEARXNG_DISCOVER=1; or BRAVE_API_KEY."
            )
        raise RuntimeError(f"{error_detail} Query: {query}")

    logger.info(
        "%d results from %s (%dms)",
        metadata.total_results,
        metadata.engine_used,
        metadata.search_time_ms,
    )

    results: List[Dict[str, Any]] = []
    for i, item in enumerate(raw_results[:num_results], 1):
        result = SearchResult(
            position=i,
            url=item["url"],
            title=item.get("title", ""),
            snippet=item.get("snippet", ""),
        )
        if crawl:
            crawled = crawl_url(
                item["url"],
                timeout=timeout,
                max_length=content_length,
                verbose=verbose,
                max_retries=1,
            )
            if crawled and not crawled.startswith("[Error:"):
                result.content = crawled
            else:
                result.error = crawled or "Failed to fetch content"
            if i < len(raw_results[:num_results]):
                time.sleep(pause + random.uniform(0.2, 0.8))
        results.append(asdict(result))

    return results


# =========================================================================
# Diagnostics
# =========================================================================


def diagnose(verbose: bool = True) -> Dict[str, Any]:
    setup_logging(verbose)
    logger = logging.getLogger(__name__)
    report: Dict[str, Any] = {}

    try:
        fetcher = _preferred_fetcher()
    except OSError as e:
        report["fetcher_error"] = str(e)
        fetcher = None

    report["tools"] = {
        "curlie": CURLIE_BIN or "not found",
        "wget2": WGET2_BIN or "not found",
        "curl": CURL_BIN or "not found",
        "wget": WGET_BIN or "not found",
        "preferred": fetcher or "none",
        "beautifulsoup4": HAS_BS4,
    }
    report["env"] = {
        "SEARXNG_URL": os.environ.get("SEARXNG_URL", ""),
        "SEARXNG_DISCOVER": os.environ.get("SEARXNG_DISCOVER", ""),
        "SEARXNG_CACHE_FILE": _searx_cache_path(),
        "BRAVE_API_KEY_set": bool(os.environ.get("BRAVE_API_KEY", "").strip()),
    }

    report["searx_cache"] = _load_cached_searx_bases()

    if fetcher:
        space = _fetch_searx_space_url_list(timeout=12, verbose=verbose)
        report["searx_space_urls_fetched"] = len(space)
        report["searx_space_sample"] = space[:5]

        for base in (
            _load_cached_searx_bases()[:2] or _static_searx_fallback_bases()[:2]
        ):
            n = _probe_searx_base(base, timeout=10, verbose=verbose)
            report[f"probe_{base}"] = n

        html, _ = _fetch_url_with_retry(
            "https://html.duckduckgo.com/html/",
            timeout=12,
            max_retries=0,
            method="POST",
            data={"q": "test", "b": ""},
            extra_headers={
                "Content-Type": "application/x-www-form-urlencoded",
                "Referer": "https://html.duckduckgo.com/",
            },
        )
        report["probe_duckduckgo"] = {
            "ok": bool(html),
            "parsed": len(_parse_duckduckgo(html or "", 3)) if html else 0,
        }

    return report


# =========================================================================
# webx / llm-functions entry — MUST keep signature and JSON shape
# =========================================================================


def run(
    query: str,
    limit: int = 10,
    lang: str = "en",
    region: str = "us",
    safe_search: Literal["off", "moderate", "strict"] = "moderate",
    pause: float = 2.0,
    crawl: bool = False,
    content_length: int = 3000,
    timeout: int = 10,
    verbose: bool = False,
) -> str:
    """Search the web and return JSON text or an ERROR line for the agent.

    Args:
        query: Search terms, e.g. latest news or python asyncio tutorial.
        limit: Maximum number of results (default 10).
        lang: Language code (default en).
        region: Region code (default us).
        safe_search: off, moderate, or strict.
        pause: Seconds between crawl requests when crawl is true.
        crawl: If true, fetch page text for each result URL.
        content_length: Max characters per crawled page.
        timeout: HTTP timeout in seconds.
        verbose: Enable debug logging to stderr.
    """
    try:
        results = search_google(
            query=query,
            num_results=limit,
            lang=lang,
            region=region,
            safe_search=safe_search,
            pause=pause,
            crawl=crawl,
            content_length=content_length,
            timeout=timeout,
            verbose=verbose,
        )
        payload = {"query": query, "results": results}
        return json.dumps(payload, ensure_ascii=False, indent=2)
    except ValueError as e:
        return f"ERROR: {e}"
    except RuntimeError as e:
        return f"ERROR: {e}"
    except Exception as e:
        return f"ERROR: unexpected: {e}"


# =========================================================================
# CLI (unchanged flags for webx wrappers)
# =========================================================================


def main() -> None:
    parser = argparse.ArgumentParser(description="Web search tool")
    parser.add_argument("query", nargs="?", help="Search query")
    parser.add_argument("-n", "--limit", type=int, default=10)
    parser.add_argument("--lang", default="en")
    parser.add_argument("--region", default="us")
    parser.add_argument(
        "--safe-search",
        choices=["off", "moderate", "strict"],
        default="moderate",
    )
    parser.add_argument("--pause", type=float, default=2.0)
    parser.add_argument("--crawl", action="store_true")
    parser.add_argument("--content-length", type=int, default=3000)
    parser.add_argument("--timeout", type=int, default=10)
    parser.add_argument("-v", "--verbose", action="store_true")
    parser.add_argument("--diagnose", action="store_true")
    args = parser.parse_args()

    if args.diagnose:
        print(json.dumps(diagnose(verbose=True), indent=2))
        sys.exit(0)

    if not args.query:
        parser.error("query required (or --diagnose)")

    out = run(
        query=args.query,
        limit=args.limit,
        lang=args.lang,
        region=args.region,
        safe_search=args.safe_search,
        pause=args.pause,
        crawl=args.crawl,
        content_length=args.content_length,
        timeout=args.timeout,
        verbose=args.verbose,
    )
    print(out)
    sys.exit(0 if not out.startswith("ERROR:") else 1)


if __name__ == "__main__":
    main()
