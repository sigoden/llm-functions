#!/usr/bin/env python3
# @describe Web Search Tool using You.com (YDC) API with domain filtering
# @option --query! <TEXT>                          Search query
# @option --count <NUM>                            Number of results (default: 10)
# @option --include-domains <DOMAINS>              Comma-separated domains to include
# @option --exclude-domains <DOMAINS>              Comma-separated domains to exclude
# @flag   --verbose                                Enable verbose output
"""
ydc_search.py - Web Search Tool using You.com (YDC) API
"""

import argparse
import html
import json
import logging
import os
import re
import sys
import urllib.parse
from html.parser import HTMLParser
from typing import Any, Dict, List, Optional

import requests


def setup_logging(verbose: bool = False) -> None:
    level = logging.DEBUG if verbose else logging.WARNING
    root = logging.getLogger()
    root.setLevel(level)
    # Prevent handler duplication across consecutive setup calls
    for handler in root.handlers[:]:
        root.removeHandler(handler)
    handler = logging.StreamHandler(sys.stderr)
    handler.setFormatter(logging.Formatter("%(asctime)s - %(levelname)s - %(message)s"))
    root.addHandler(handler)


def load_env() -> None:
    """Load environment variables from .env file by checking recursively up to 2 levels."""
    base_dir = os.path.dirname(os.path.abspath(__file__))
    dirs_to_check = [
        base_dir,
        os.path.dirname(base_dir),
        os.path.dirname(os.path.dirname(base_dir)),
    ]
    for d in dirs_to_check:
        env_path = os.path.join(d, ".env")
        if os.path.exists(env_path):
            try:
                with open(env_path, encoding="utf-8") as f:
                    for line in f:
                        line = line.strip()
                        if line and not line.startswith("#") and "=" in line:
                            key, val = line.split("=", 1)
                            k_strip = key.strip()
                            if k_strip and k_strip not in os.environ:
                                os.environ[k_strip] = val.strip()
            except Exception:
                pass
            break


def _sanitize_string(text: str) -> str:
    """Removes null bytes and non-printable control characters for safe JSON construction."""
    if not text:
        return ""
    text = text.replace("\x00", "")
    out = []
    for ch in text:
        if ch in "\n\r\t" or ord(ch) >= 32:
            out.append(ch)
        else:
            out.append(" ")
    return "".join(out)


def _clean_html(text: str) -> str:
    """Strips HTML tags, decodes HTML entities, and normalizes spacing."""
    if not text:
        return ""
    text = re.sub(r"<[^>]+>", "", text)
    text = html.unescape(text)
    text = re.sub(r"\s+", " ", text)
    return _sanitize_string(text.strip())


def _get_domain(url: str) -> str:
    """Extracts a normalized domain name from a URL."""
    try:
        parsed = urllib.parse.urlparse(url)
        netloc = parsed.netloc or parsed.path
        if ":" in netloc:
            netloc = netloc.split(":", 1)[0]
        if netloc.startswith("www."):
            netloc = netloc[4:]
        return netloc.lower().strip()
    except Exception:
        return ""


def filter_by_domains(
    results: List[Dict[str, Any]],
    include_domains: Optional[str],
    exclude_domains: Optional[str],
) -> List[Dict[str, Any]]:
    """Filters results based on included and excluded domain sets, including subdomains."""
    if not results:
        return []

    inc_set = set()
    if include_domains:
        for d in include_domains.split(","):
            d_clean = d.strip().lower()
            if d_clean.startswith("www."):
                d_clean = d_clean[4:]
            if d_clean:
                inc_set.add(d_clean)

    exc_set = set()
    if exclude_domains:
        for d in exclude_domains.split(","):
            d_clean = d.strip().lower()
            if d_clean.startswith("www."):
                d_clean = d_clean[4:]
            if d_clean:
                exc_set.add(d_clean)

    filtered = []
    for r in results:
        url = r.get("url") or ""
        domain = _get_domain(url)
        if not domain:
            filtered.append(r)
            continue

        # Check exclusions
        is_excluded = False
        if exc_set:
            for exc_dom in exc_set:
                if domain == exc_dom or domain.endswith("." + exc_dom):
                    is_excluded = True
                    break
        if is_excluded:
            continue

        # Check inclusions
        if inc_set:
            is_included = False
            for inc_dom in inc_set:
                if domain == inc_dom or domain.endswith("." + inc_dom):
                    is_included = True
                    break
            if not is_included:
                continue

        filtered.append(r)
    return filtered


class DDGHTMLParser(HTMLParser):
    """Robust HTML parser for DuckDuckGo's static HTML results layout with nested element depth tracking."""

    def __init__(self):
        super().__init__()
        self.results: List[Dict[str, Any]] = []
        self.current: Optional[Dict[str, Any]] = None
        self.active_tag: Optional[str] = None
        self.active_tag_depth: int = 0

    def handle_starttag(self, tag: str, attrs: List[tuple[str, Optional[str]]]) -> None:
        attrs_dict = dict(attrs)
        cls = attrs_dict.get("class", "") or ""

        if tag == "div" and ("result__body" in cls or "links_main" in cls):
            if self.current:
                self.results.append(self.current)
            self.current = {
                "title": "",
                "url": "",
                "snippet": "",
                "type": "web",
                "age": None,
            }
            self.active_tag = None
            self.active_tag_depth = 0
            return

        if not self.current:
            return

        if self.active_tag:
            # Prevent nested styling elements (<b>, <span>) from contaminating parser state boundaries
            self.active_tag_depth += 1
            return

        if tag == "a" and ("result__a" in cls or "result__url" in cls):
            self.active_tag = "title"
            self.active_tag_depth = 0
            href = attrs_dict.get("href", "") or ""
            self.current["url"] = self._clean_url(href)
            return

        if "result__snippet" in cls:
            self.active_tag = "snippet"
            self.active_tag_depth = 0
            return

    def handle_endtag(self, tag: str) -> None:
        if not self.current or not self.active_tag:
            return

        if self.active_tag_depth > 0:
            self.active_tag_depth -= 1
            return

        if self.active_tag == "title" and tag == "a":
            self.active_tag = None
        elif self.active_tag == "snippet" and tag in ("a", "span", "div"):
            self.active_tag = None

    def handle_data(self, data: str) -> None:
        if not self.current or not self.active_tag:
            return
        if self.active_tag == "title":
            self.current["title"] = self.current["title"] + data
        elif self.active_tag == "snippet":
            self.current["snippet"] = self.current["snippet"] + data

    def _clean_url(self, url: str) -> str:
        if url.startswith("//"):
            url = "https:" + url
        if "uddg=" in url:
            try:
                parsed = urllib.parse.urlparse(url)
                qs = urllib.parse.parse_qs(parsed.query)
                if "uddg" in qs:
                    return qs["uddg"][0]
            except Exception:
                pass
        return url

    def close(self) -> None:
        if self.current:
            self.results.append(self.current)
            self.current = None
        super().close()


def search_ddg(query: str, count: int = 10) -> List[Dict[str, Any]]:
    """Free DuckDuckGo fallback search utilizing structured HTML parsing with regex fallbacks."""
    url = "https://html.duckduckgo.com/html/"
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
        "Accept-Language": "en-US,en;q=0.9",
    }
    try:
        response = requests.post(url, data={"q": query}, headers=headers, timeout=15)
        response.raise_for_status()

        # 1. Primary Structured Parsing
        parser = DDGHTMLParser()
        parser.feed(response.text)
        parser.close()
        results = parser.results

        # 2. Resilient Regex Fallback
        if not results:
            logging.debug(
                "HTMLParser did not find results; attempting fallback regex..."
            )
            matches = re.findall(
                r'<a[^>]+class="(?:result__url|result__a)"[^>]+href="([^"]+)"[^>]*>(.*?)</a>.*?(?:<a[^>]+class="result__snippet"[^>]*>(.*?)</a>)',
                response.text,
                re.DOTALL,
            )
            for link, title, snippet in matches:
                title_clean = _clean_html(title)
                snippet_clean = _clean_html(snippet)

                if "uddg=" in link:
                    try:
                        parsed = urllib.parse.urlparse(link)
                        qs = urllib.parse.parse_qs(parsed.query)
                        if "uddg" in qs:
                            link = qs["uddg"][0]
                    except Exception:
                        pass

                results.append(
                    {
                        "type": "web",
                        "title": title_clean,
                        "url": link,
                        "snippet": snippet_clean,
                        "age": None,
                    }
                )

        # Sanitization, Normalization, and Internal Link Filtering
        cleaned_results = []
        seen_urls = set()
        for r in results:
            url_val = r.get("url", "").strip()
            if not url_val or url_val in seen_urls:
                continue
            if "duckduckgo.com" in url_val and "/l/?" not in url_val:
                continue

            seen_urls.add(url_val)
            cleaned_results.append(
                {
                    "type": r.get("type", "web"),
                    "title": _clean_html(r.get("title", "")),
                    "url": url_val,
                    "snippet": _clean_html(r.get("snippet", "")),
                    "age": r.get("age"),
                }
            )

        return cleaned_results[:count]
    except Exception as e:
        logging.error(f"DuckDuckGo fallback search failed: {e}")
        return []


def search_ydc(
    query: str,
    count: int = 10,
    include_domains: str = None,
    exclude_domains: str = None,
) -> List[Dict[str, Any]]:
    """Search using You.com API with automatic DuckDuckGo fallback."""
    # API key remains hardcoded as requested
    api_key = (
        os.environ.get("YDC_API_KEY")
        or os.environ.get("YOU_API_KEY")
        or "ydc-sk-3be25b63a354f86f-cZsqdcYZe3xHo2qxVUZxEmTI1wAzlfG8-23e9d3b8"
    )

    if api_key:
        params = {"query": query, "count": count}
        if include_domains:
            params["include_domains"] = include_domains
        if exclude_domains:
            params["exclude_domains"] = exclude_domains

        url = f"https://ydc-index.io/v1/search?{urllib.parse.urlencode(params)}"
        headers = {"X-API-Key": api_key}

        try:
            logging.info(f"Initiating You.com search request for: '{query}'")
            response = requests.get(url, headers=headers, timeout=20)
            response.raise_for_status()
            data = response.json()

            results = []
            for section in ["web", "news"]:
                for item in data.get("results", {}).get(section, []):
                    title = _clean_html(item.get("title", ""))
                    url_val = (item.get("url") or "").strip()

                    raw_snippet = " ".join(item.get("snippets", [])) or item.get(
                        "description", ""
                    )
                    snippet = _clean_html(raw_snippet)

                    results.append(
                        {
                            "type": section,
                            "title": title,
                            "url": url_val,
                            "snippet": snippet,
                            "age": item.get("page_age"),
                        }
                    )
            if results:
                return results
        except Exception as e:
            logging.warning(f"YDC search failed: {e}. Falling back to DuckDuckGo...")

    # Fallback to DuckDuckGo search
    return search_ddg(query, count)


def run(
    query: str,
    count: int = 10,
    include_domains: str = None,
    exclude_domains: str = None,
    verbose: bool = False,
) -> List[Dict[str, Any]]:
    setup_logging(verbose)
    load_env()

    query_clean = (query or "").strip()
    if not query_clean:
        return [{"error": "Query is required"}]

    # Request excess headroom when filters are active to prevent post-filter starvation
    headroom_count = count
    if include_domains or exclude_domains:
        headroom_count = max(count * 3, 30)

    results = search_ydc(query_clean, headroom_count, include_domains, exclude_domains)
    filtered = filter_by_domains(results, include_domains, exclude_domains)
    return filtered[:count]


if __name__ == "__main__":
    # 1. Parse JSON input if passed by aichat's tool dispatcher
    if len(sys.argv) > 1 and (
        sys.argv[1].startswith("{") or sys.argv[1].startswith("[")
    ):
        try:
            kwargs = json.loads(sys.argv[1])
            query_val = kwargs.get("query")

            count_val = kwargs.get("count")
            if count_val is not None:
                try:
                    count_val = int(count_val)
                except (ValueError, TypeError):
                    count_val = 10
            else:
                count_val = 10

            inc = kwargs.get("include_domains")
            exc = kwargs.get("exclude_domains")
            verb = kwargs.get("verbose", False)

            if not query_val:
                print(json.dumps([{"error": "Query is required"}]))
                sys.exit(1)
            print(
                json.dumps(
                    run(
                        query_val,
                        count=count_val,
                        include_domains=inc,
                        exclude_domains=exc,
                        verbose=verb,
                    ),
                    indent=2,
                )
            )
            sys.exit(0)
        except Exception as err:
            print(json.dumps([{"error": f"JSON argument parse error: {err}"}]))
            sys.exit(1)

    # 2. Fallback to standard CLI arguments
    parser = argparse.ArgumentParser(description="You.com Search Tool")
    parser.add_argument("query", nargs="?", help="Search query")
    parser.add_argument("--query", dest="query_opt", help="Search query (alternative)")
    parser.add_argument("--count", type=int, default=10)
    parser.add_argument("--include-domains")
    parser.add_argument("--exclude-domains")
    parser.add_argument("--verbose", action="store_true")

    args = parser.parse_args()
    query = args.query or args.query_opt

    if not query:
        print(json.dumps([{"error": "Query is required"}]))
        sys.exit(1)

    try:
        results_output = run(
            query,
            count=args.count,
            include_domains=args.include_domains,
            exclude_domains=args.exclude_domains,
            verbose=args.verbose,
        )
        print(json.dumps(results_output, indent=2))
    except Exception as general_err:
        print(
            json.dumps(
                [{"error": f"Internal execution failure: {general_err}"}], indent=2
            )
        )
        sys.exit(1)
