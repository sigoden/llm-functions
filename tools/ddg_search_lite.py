#!/usr/bin/env python3
# @describe Search the web without paid APIs or third-party Python dependencies.
# @option --query! Search query. Supports normal search-engine operators.
# @option --count=8 <INT> Maximum results to return.
# @option --region=us-en DuckDuckGo region code.
# @option --safe-search=moderate SafeSearch: strict, moderate, off.
# @option --time= Optional freshness filter: day, week, month, year.
# @option --site= Restrict results to a domain, e.g. example.com.
# @option --output= Output JSON to a file instead of stdout.
# @option --timeout=20 <INT> HTTP timeout in seconds.
# @option --max-bytes=5242880 <INT> Maximum search-response size.
# @flag --deep Search several query variants and merge/deduplicate results.
# @flag --fetch Fetch result pages and include compact page text.
# @option --max-chars=6000 <INT> Maximum extracted text per fetched page.
# @flag --json Return structured JSON (default).
# @flag --markdown Return human-readable Markdown.
# @flag --raw Return the parsed search-engine text for diagnostics.
# @flag --no-verify-ssl Disable TLS certificate verification.
# @env LLM_OUTPUT=/dev/stdout Output path when --output is empty.

"""
web_search.py — Dependency-free web search for LLMs / llm-functions / Termux.

Uses DuckDuckGo's public HTML/Lite search pages rather than a paid search API.
It requires only Python's standard library. Optional page fetching also uses
the standard library.

The search backend is intentionally treated as an unofficial HTML interface:
HTML layouts can change, and automated requests can occasionally be challenged.
The tool therefore fails cleanly and never pretends that an empty challenge page
is a successful search.

Success: JSON by default, or Markdown/raw output when requested.
Pre-flight failures: ERROR: ...
"""

from __future__ import annotations

import argparse
import html
import json
import os
import re
import ssl
import sys
import time
from html.parser import HTMLParser
from typing import Any, Dict, List, Optional, Sequence, Tuple
from urllib.error import HTTPError, URLError
from urllib.parse import parse_qs, quote_plus, unquote, urljoin, urlparse
from urllib.request import Request, urlopen


DEFAULT_COUNT = 8
DEFAULT_TIMEOUT = 20
DEFAULT_MAX_BYTES = 5 * 1024 * 1024
DEFAULT_MAX_CHARS = 6000
MAX_COUNT = 25
MAX_QUERY_LENGTH = 1000
MAX_FETCH_BYTES = 3 * 1024 * 1024

SEARCH_ENDPOINTS = (
    "https://html.duckduckgo.com/html/",
    "https://lite.duckduckgo.com/lite/",
)

ALLOWED_SAFE_SEARCH = frozenset({"strict", "moderate", "off"})
ALLOWED_TIME = frozenset({"day", "week", "month", "year"})

CURL_STYLE_EXIT_CODES = {
    408: "Request timeout",
    429: "Search engine rate limit",
    500: "Search engine server error",
    502: "Bad gateway",
    503: "Search service unavailable",
    504: "Search service timeout",
}


# =========================================================================
# JSON / text helpers
# =========================================================================

def _clean_text(value: str, max_len: int = 2000) -> str:
    if not value:
        return ""
    value = html.unescape(value)
    value = value.replace("\x00", "")
    value = re.sub(r"[\t\r\n ]+", " ", value)
    return value.strip()[:max_len]


def _error_json(
    msg: str,
    *,
    query: str = "",
    code: int = 0,
    extra: Optional[Dict[str, Any]] = None,
) -> str:
    payload: Dict[str, Any] = {
        "status": "error",
        "code": int(code),
        "msg": _clean_text(msg, 2000),
    }
    if query:
        payload["query"] = _clean_text(query, MAX_QUERY_LENGTH)
    if extra:
        payload.update(extra)
    return json.dumps(payload, ensure_ascii=False, separators=(",", ":"))


def _resolve_output(output: str) -> Optional[str]:
    if output and output.strip():
        value = output.strip()
        if value in ("/dev/stdout", "-"):
            return None
        return value

    env = os.environ.get("LLM_OUTPUT", "").strip()
    if not env or env in ("/dev/stdout", "-"):
        return None
    return env


def _valid_int(value: Any, name: str, minimum: int, maximum: int) -> int:
    try:
        result = int(value)
    except (TypeError, ValueError):
        raise ValueError(f"{name} must be an integer")
    if result < minimum or result > maximum:
        raise ValueError(f"{name} must be between {minimum} and {maximum}")
    return result


# =========================================================================
# Search HTML parser
# =========================================================================

class SearchParser(HTMLParser):
    """Small tolerant parser for DuckDuckGo HTML result pages."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.results: List[Dict[str, str]] = []
        self._current: Optional[Dict[str, str]] = None
        self._capture: Optional[str] = None
        self._buffer: List[str] = []
        self._links_seen = 0

    @staticmethod
    def _classes(attrs: Sequence[Tuple[str, Optional[str]]]) -> str:
        return " ".join(
            value or ""
            for key, value in attrs
            if key.lower() == "class"
        ).lower()

    def handle_starttag(
        self,
        tag: str,
        attrs: List[Tuple[str, Optional[str]]],
    ) -> None:
        tag = tag.lower()
        attrs_dict = {k.lower(): v or "" for k, v in attrs}
        classes = self._classes(attrs)

        if tag == "a":
            href = attrs_dict.get("href", "")
            if not href:
                return

            if (
                "result__a" in classes
                or "result-link" in classes
                or "result-link" in attrs_dict.get("class", "")
            ):
                if self._current:
                    self._finish_current()

                self._current = {
                    "title": "",
                    "url": self._normalize_url(href),
                    "snippet": "",
                }
                self._capture = "title"
                self._buffer = []

            elif self._current and (
                "result__snippet" in classes
                or "result-snippet" in classes
            ):
                self._capture = "snippet"
                self._buffer = []

        elif self._current and tag in ("div", "td"):
            if "result__snippet" in classes:
                self._capture = "snippet"
                self._buffer = []

    def handle_data(self, data: str) -> None:
        if self._capture and self._current:
            self._buffer.append(data)

    def handle_endtag(self, tag: str) -> None:
        if not self._capture:
            return
        tag = tag.lower()
        if tag == "a" and self._capture == "title":
            self._current["title"] = _clean_text(" ".join(self._buffer), 500)
            self._capture = None
            self._buffer = []
        elif tag in ("a", "div", "td") and self._capture == "snippet":
            text = _clean_text(" ".join(self._buffer), 1200)
            if text:
                self._current["snippet"] = text
            self._capture = None
            self._buffer = []

    def handle_comment(self, data: str) -> None:
        # Ignore comments deliberately.
        return

    def _finish_current(self) -> None:
        if not self._current:
            return
        if self._current.get("title") and self._current.get("url"):
            self.results.append(self._current)
        self._current = None
        self._capture = None
        self._buffer = []

    def close(self) -> None:
        super().close()
        self._finish_current()

    @staticmethod
    def _normalize_url(raw: str) -> str:
        raw = html.unescape(raw)
        if raw.startswith("//"):
            raw = "https:" + raw

        # DuckDuckGo often wraps targets in /l/?uddg=<encoded-url>.
        parsed = urlparse(raw)
        if parsed.path.startswith("/l/") or parsed.path == "/l":
            target = parse_qs(parsed.query).get("uddg", [""])[0]
            if target:
                raw = unquote(target)

        return raw


class PageTextParser(HTMLParser):
    """Extract readable-ish text without requiring BeautifulSoup."""

    SKIP_TAGS = frozenset({
        "script", "style", "noscript", "svg", "canvas", "template",
        "head", "iframe", "object", "embed",
    })

    BLOCK_TAGS = frozenset({
        "p", "div", "article", "section", "main", "li", "h1", "h2",
        "h3", "h4", "h5", "h6", "br", "tr", "blockquote", "pre",
    })

    def __init__(self, max_chars: int) -> None:
        super().__init__(convert_charrefs=True)
        self.max_chars = max_chars
        self.parts: List[str] = []
        self.skip_depth = 0

    def handle_starttag(
        self,
        tag: str,
        attrs: List[Tuple[str, Optional[str]]],
    ) -> None:
        tag = tag.lower()
        if tag in self.SKIP_TAGS:
            self.skip_depth += 1
        elif not self.skip_depth and tag in self.BLOCK_TAGS:
            self.parts.append("\n")

    def handle_endtag(self, tag: str) -> None:
        tag = tag.lower()
        if tag in self.SKIP_TAGS and self.skip_depth:
            self.skip_depth -= 1
        elif not self.skip_depth and tag in self.BLOCK_TAGS:
            self.parts.append("\n")

    def handle_data(self, data: str) -> None:
        if self.skip_depth or not data.strip():
            return
        self.parts.append(data)
        if len("".join(self.parts)) >= self.max_chars * 2:
            self.close()

    def text(self) -> str:
        value = html.unescape("".join(self.parts))
        value = re.sub(r"[ \t]+", " ", value)
        value = re.sub(r"\n\s*\n\s*\n+", "\n\n", value)
        return value.strip()[: self.max_chars]


# =========================================================================
# HTTP
# =========================================================================

def _ssl_context(verify_ssl: bool) -> ssl.SSLContext:
    if verify_ssl:
        return ssl.create_default_context()
    return ssl._create_unverified_context()


def _http_get(
    url: str,
    *,
    timeout: int,
    max_bytes: int,
    user_agent: str,
    verify_ssl: bool,
    referer: str = "",
) -> Tuple[int, str, str]:
    headers = {
        "User-Agent": user_agent,
        "Accept": (
            "text/html,application/xhtml+xml,application/xml;q=0.9,"
            "text/plain;q=0.8,*/*;q=0.5"
        ),
        "Accept-Language": "en-US,en;q=0.8",
        "Accept-Encoding": "identity",
        "Connection": "close",
    }
    if referer:
        headers["Referer"] = referer

    request = Request(url, headers=headers, method="GET")

    try:
        with urlopen(
            request,
            timeout=timeout,
            context=_ssl_context(verify_ssl),
        ) as response:
            status = int(getattr(response, "status", 200) or 200)
            chunks: List[bytes] = []
            total = 0

            while total < max_bytes:
                chunk = response.read(min(65536, max_bytes - total))
                if not chunk:
                    break
                chunks.append(chunk)
                total += len(chunk)

            raw = b"".join(chunks)
            charset = response.headers.get_content_charset() or "utf-8"
            return status, raw.decode(charset, errors="replace"), ""

    except HTTPError as exc:
        try:
            raw = exc.read(max_bytes)
            body = raw.decode("utf-8", errors="replace")
        except Exception:
            body = ""
        return int(exc.code), body, str(exc)

    except (URLError, TimeoutError, OSError) as exc:
        return 0, "", str(exc)


# =========================================================================
# Search / ranking
# =========================================================================

def _build_search_url(
    query: str,
    *,
    region: str,
    safe_search: str,
    time_filter: str,
    site: str,
    endpoint: str,
) -> str:
    final_query = query.strip()
    if site:
        final_query += f" site:{site.strip()}"

    params = [
        f"q={quote_plus(final_query)}",
        f"kl={quote_plus(region)}",
    ]

    # DDG's HTML endpoint uses df for freshness.
    if time_filter:
        params.append(f"df={quote_plus(time_filter)}")

    if safe_search == "strict":
        params.append("kp=2")
    elif safe_search == "off":
        params.append("kp=-2")
    else:
        params.append("kp=1")

    return endpoint + "?" + "&".join(params)


def _query_variants(query: str) -> List[str]:
    variants = [query.strip()]

    # Useful for research agents: a second query often finds sources
    # missed by the first ranking without requiring a paid meta-search API.
    if len(query.split()) >= 4:
        variants.append(f'"{query.strip()}"')

    return list(dict.fromkeys(variants))


def _domain(url: str) -> str:
    try:
        return urlparse(url).netloc.lower().split("@")[-1].split(":")[0]
    except Exception:
        return ""


def _score_result(result: Dict[str, str], query: str) -> float:
    text = f"{result.get('title', '')} {result.get('snippet', '')}".lower()
    terms = [x for x in re.findall(r"[a-z0-9]{3,}", query.lower()) if x]

    score = 0.0
    for term in terms:
        if term in result.get("title", "").lower():
            score += 3.0
        elif term in text:
            score += 1.0

    host = _domain(result.get("url", ""))
    if host.endswith(".gov") or host.endswith(".edu"):
        score += 2.0
    if host in {"wikipedia.org", "docs.python.org", "developer.mozilla.org"}:
        score += 1.0

    return score


def _merge_results(
    batches: Sequence[List[Dict[str, str]]],
    query: str,
    limit: int,
) -> List[Dict[str, str]]:
    merged: Dict[str, Dict[str, str]] = {}

    for batch in batches:
        for result in batch:
            url = result.get("url", "").strip()
            if not url:
                continue

            parsed = urlparse(url)
            if parsed.scheme not in ("http", "https"):
                continue

            key = url.split("#", 1)[0].rstrip("/")
            if key not in merged:
                merged[key] = dict(result)

    results = list(merged.values())
    for result in results:
        result["domain"] = _domain(result["url"])
        result["score"] = round(_score_result(result, query), 2)

    results.sort(
        key=lambda item: (
            -float(item.get("score", 0)),
            item.get("title", "").lower(),
        )
    )

    for index, result in enumerate(results[:limit], 1):
        result["rank"] = index

    return results[:limit]


# =========================================================================
# Optional page extraction
# =========================================================================

def _fetch_pages(
    results: List[Dict[str, str]],
    *,
    timeout: int,
    max_chars: int,
    verify_ssl: bool,
) -> None:
    for result in results:
        url = result.get("url", "")
        if not url:
            continue

        status, body, error = _http_get(
            url,
            timeout=timeout,
            max_bytes=MAX_FETCH_BYTES,
            user_agent="ai-web-search/1.0",
            verify_ssl=verify_ssl,
        )

        result["fetch_status"] = status
        if error:
            result["fetch_error"] = _clean_text(error, 300)
            continue

        parser = PageTextParser(max_chars)
        try:
            parser.feed(body)
            parser.close()
            text = parser.text()
        except Exception as exc:
            result["fetch_error"] = _clean_text(str(exc), 300)
            continue

        if text:
            result["content"] = text


# =========================================================================
# Public API
# =========================================================================

def search_web(
    query: str,
    count: int = DEFAULT_COUNT,
    region: str = "us-en",
    safe_search: str = "moderate",
    time: str = "",
    site: str = "",
    timeout: int = DEFAULT_TIMEOUT,
    max_bytes: int = DEFAULT_MAX_BYTES,
    deep: bool = False,
    fetch: bool = False,
    max_chars: int = DEFAULT_MAX_CHARS,
    verify_ssl: bool = True,
) -> str:
    query = (query or "").strip()

    if not query:
        return "ERROR: query is empty"
    if len(query) > MAX_QUERY_LENGTH:
        return f"ERROR: query exceeds {MAX_QUERY_LENGTH} characters"

    try:
        count = _valid_int(count, "count", 1, MAX_COUNT)
        timeout = _valid_int(timeout, "timeout", 1, 120)
        max_bytes = _valid_int(max_bytes, "max_bytes", 16 * 1024, DEFAULT_MAX_BYTES)
        max_chars = _valid_int(max_chars, "max_chars", 500, 30000)
    except ValueError as exc:
        return f"ERROR: {exc}"

    safe_search = (safe_search or "moderate").lower()
    if safe_search not in ALLOWED_SAFE_SEARCH:
        return "ERROR: safe_search must be strict, moderate, or off"

    time_filter = (time or "").lower().strip()
    if time_filter not in ALLOWED_TIME and time_filter:
        return "ERROR: time must be day, week, month, or year"

    region = (region or "us-en").strip().lower()
    site = (site or "").strip()

    batches: List[List[Dict[str, str]]] = []
    variants = _query_variants(query) if deep else [query]
    last_error = ""

    for variant in variants:
        batch_found = False

        for endpoint in SEARCH_ENDPOINTS:
            search_url = _build_search_url(
                variant,
                region=region,
                safe_search=safe_search,
                time_filter=time_filter,
                site=site,
                endpoint=endpoint,
            )

            status, body, error = _http_get(
                search_url,
                timeout=timeout,
                max_bytes=max_bytes,
                user_agent="ai-web-search/1.0",
                verify_ssl=verify_ssl,
            )

            if error:
                last_error = error
                continue

            lowered = body.lower()
            if (
                "captcha" in lowered
                or "unusual traffic" in lowered
                or "robot check" in lowered
                or "automated queries" in lowered
            ):
                last_error = "Search engine challenge/rate limit detected"
                continue

            parser = SearchParser()
            try:
                parser.feed(body)
                parser.close()
            except Exception as exc:
                last_error = str(exc)
                continue

            if parser.results:
                batches.append(parser.results)
                batch_found = True
                break

            last_error = (
                f"Search returned no parseable results (HTTP {status})"
            )

        if not batch_found:
            continue

    results = _merge_results(batches, query, count)

    if not results:
        return _error_json(
            "No search results found",
            query=query,
            extra={
                "hint": (
                    "DuckDuckGo's public HTML interface may be temporarily "
                    "rate-limited or changed. Try again later or simplify the query."
                ),
                "backend": "duckduckgo-html",
                "detail": _clean_text(last_error, 500),
            },
        )

    if fetch:
        _fetch_pages(
            results,
            timeout=timeout,
            max_chars=max_chars,
            verify_ssl=verify_ssl,
        )

    payload: Dict[str, Any] = {
        "status": "ok",
        "query": query,
        "count": len(results),
        "results": results,
        "backend": "duckduckgo-html",
        "deep": bool(deep),
        "fetched": bool(fetch),
        "disclaimer": (
            "Results are obtained from a public HTML search interface, "
            "not a paid or official search API. Verify important claims "
            "against primary sources."
        ),
    }

    return json.dumps(payload, ensure_ascii=False, separators=(",", ":"))


def run(
    query: str,
    count: int = DEFAULT_COUNT,
    region: str = "us-en",
    safe_search: str = "moderate",
    time: str = "",
    site: str = "",
    output: str = "",
    timeout: int = DEFAULT_TIMEOUT,
    max_bytes: int = DEFAULT_MAX_BYTES,
    deep: bool = False,
    fetch: bool = False,
    max_chars: int = DEFAULT_MAX_CHARS,
    markdown: bool = False,
    raw: bool = False,
    verify_ssl: bool = True,
) -> str:
    """Search the web with no Python dependencies or paid API.

    Args:
        query: Search query.
        count: Maximum results, 1-25.
        region: DuckDuckGo region code such as us-en.
        safe_search: strict, moderate, or off.
        time: day, week, month, or year.
        site: Optional domain restriction.
        output: Write result to a file; empty uses LLM_OUTPUT/stdout.
        timeout: HTTP timeout.
        max_bytes: Maximum bytes read from each search response.
        deep: Search an additional query variant and merge results.
        fetch: Fetch each result page and attach compact extracted text.
        max_chars: Maximum extracted characters per page.
        markdown: Return Markdown instead of JSON.
        raw: Return raw parsed search HTML text for diagnostics.
        verify_ssl: Verify TLS certificates.
    """
    result = search_web(
        query=query,
        count=count,
        region=region,
        safe_search=safe_search,
        time=time,
        site=site,
        timeout=timeout,
        max_bytes=max_bytes,
        deep=deep,
        fetch=fetch,
        max_chars=max_chars,
        verify_ssl=verify_ssl,
    )

    if result.startswith("ERROR:") or '"status":"error"' in result:
        return result

    try:
        payload = json.loads(result)
    except json.JSONDecodeError:
        return result

    if raw:
        return json.dumps(payload, ensure_ascii=False, indent=2)

    if markdown:
        lines = [
            f"# Web Search: {payload.get('query', '')}",
            "",
        ]
        for item in payload.get("results", []):
            lines.append(
                f"{item.get('rank', '')}. [{item.get('title', '')}]"
                f"({item.get('url', '')})"
            )
            if item.get("snippet"):
                lines.append(f"   {item['snippet']}")
            if item.get("content"):
                lines.append(f"   {item['content'][:max_chars]}")
            lines.append("")
        return "\n".join(lines).strip()

    output_text = json.dumps(
        payload,
        ensure_ascii=False,
        separators=(",", ":"),
    )

    out_path = _resolve_output(output)
    if out_path:
        try:
            from pathlib import Path
            dest = Path(out_path)
            dest.parent.mkdir(parents=True, exist_ok=True)
            temp = dest.with_name(dest.name + ".tmp")
            temp.write_text(output_text, encoding="utf-8")
            temp.replace(dest)
            return f"OK: results={payload.get('count', 0)} path={dest}"
        except OSError as exc:
            return _error_json(
                "Unable to write output file",
                query=query,
                extra={"detail": str(exc)},
            )

    return output_text


# =========================================================================
# CLI
# =========================================================================

def _add_cli_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--query", required=True)
    parser.add_argument("--count", type=int, default=DEFAULT_COUNT)
    parser.add_argument("--region", default="us-en")
    parser.add_argument("--safe-search", default="moderate")
    parser.add_argument("--time", default="")
    parser.add_argument("--site", default="")
    parser.add_argument("--output", default="")
    parser.add_argument("--timeout", type=int, default=DEFAULT_TIMEOUT)
    parser.add_argument("--max-bytes", type=int, default=DEFAULT_MAX_BYTES)
    parser.add_argument("--max-chars", type=int, default=DEFAULT_MAX_CHARS)
    parser.add_argument("--deep", action="store_true")
    parser.add_argument("--fetch", action="store_true")
    parser.add_argument("--markdown", action="store_true")
    parser.add_argument("--raw", action="store_true")
    parser.add_argument("--verify-ssl", action="store_true", default=True)
    parser.add_argument("--no-verify-ssl", action="store_false", dest="verify_ssl")


def _exit_code(result: str) -> int:
    if result.startswith("ERROR:"):
        return 1
    try:
        payload = json.loads(result)
        if isinstance(payload, dict) and payload.get("status") == "error":
            return 1
    except Exception:
        pass
    return 0


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Dependency-free web search for AI agents"
    )
    _add_cli_args(parser)
    args = parser.parse_args()

    result = run(
        query=args.query,
        count=args.count,
        region=args.region,
        safe_search=args.safe_search,
        time=args.time,
        site=args.site,
        output=args.output,
        timeout=args.timeout,
        max_bytes=args.max_bytes,
        deep=args.deep,
        fetch=args.fetch,
        max_chars=args.max_chars,
        markdown=args.markdown,
        raw=args.raw,
        verify_ssl=args.verify_ssl,
    )

    print(result)
    sys.exit(_exit_code(result))


if __name__ == "__main__":
    main()
