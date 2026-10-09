#!/usr/bin/env python3
# @describe Search Yandex Web and Yandex Images without paid APIs or third-party Python dependencies.
# @option --query= Text query for Yandex web/image search.
# @option --image-url= Search Yandex Images using an image URL (reverse image search).
# @option --image-file= Search Yandex Images using a local image file.
# @option --type=web Search type: web, images, or image-sites.
# @option --count=10 <INT> Maximum results to return.
# @option --page=0 <INT> Result page/offset where supported.
# @option --region=us Region/locale hint, e.g. us, ru, tr.
# @option --safe-search=moderate SafeSearch: strict, moderate, off.
# @option --site= Restrict search to a domain.
# @option --time= Optional freshness filter: day, week, month, year.
# @option --output= Output file path (default: LLM_OUTPUT or stdout).
# @option --timeout=20 <INT> HTTP timeout in seconds.
# @option --max-bytes=10485760 <INT> Maximum response size.
# @option --max-chars=6000 <INT> Maximum extracted page/image text.
# @flag --fetch Fetch source pages for image/web results and extract text.
# @flag --deep Search additional query variants and merge results.
# @flag --markdown Return human-readable Markdown.
# @flag --include-html Include a bounded HTML diagnostic field.
# @flag --no-verify-ssl Disable TLS certificate verification.
# @env LLM_OUTPUT=/dev/stdout Output path when --output is empty.

"""
yandex_search.py — Dependency-free Yandex Web + Images search for AI agents.

No requests, BeautifulSoup, Selenium, Playwright, API key, or paid service is
required. Only Python's standard library is used.

Modes:
  --type web          Yandex web search.
  --type images       Yandex Images text search.
  --type image-sites  Yandex Images sources/sites for an image URL.

Reverse image search:
  --image-url URL
  --image-file PATH

The public Yandex HTML interfaces are not official APIs and their markup can
change. The parser therefore uses several tolerant extraction strategies and
returns clean machine-readable errors instead of fabricating results.
"""

from __future__ import annotations

import argparse
import base64
import html
import json
import os
import re
import ssl
import sys
import tempfile
from html.parser import HTMLParser
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple
from urllib.error import HTTPError, URLError
from urllib.parse import (
    parse_qs,
    quote_plus,
    unquote,
    urlencode,
    urljoin,
    urlparse,
)
from urllib.request import Request, urlopen


DEFAULT_COUNT = 10
DEFAULT_TIMEOUT = 20
DEFAULT_MAX_BYTES = 10 * 1024 * 1024
DEFAULT_MAX_CHARS = 6000
MAX_COUNT = 50
MAX_PAGE = 100
MAX_QUERY_LENGTH = 1000
MAX_IMAGE_BYTES = 20 * 1024 * 1024
MAX_FETCH_BYTES = 3 * 1024 * 1024

YANDEX_WEB = "https://yandex.com/search/"
YANDEX_IMAGES = "https://yandex.com/images/search"
YANDEX_IMAGE_UPLOAD = "https://yandex.com/images/search"

SAFE_VALUES = frozenset({"strict", "moderate", "off"})
TIME_VALUES = frozenset({"day", "week", "month", "year"})
SEARCH_TYPES = frozenset({"web", "images", "image-sites"})


# =========================================================================
# Generic helpers
# =========================================================================

def _clean_text(value: str, max_len: int = 2000) -> str:
    if not value:
        return ""
    value = html.unescape(value).replace("\x00", "")
    value = re.sub(r"[ \t\r\n]+", " ", value)
    return value.strip()[:max_len]


def _clean_url(value: str) -> str:
    value = html.unescape(value).strip()
    value = value.replace("\\/", "/")
    value = value.replace("\\u0026", "&")
    value = value.replace("\\u003d", "=")
    value = value.replace("\\u002f", "/")
    value = value.replace("&amp;", "&")
    return value


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
    value = (output or "").strip()
    if not value:
        value = os.environ.get("LLM_OUTPUT", "").strip()
    if not value or value in ("-", "/dev/stdout"):
        return None
    return value


def _bounded_int(value: Any, name: str, low: int, high: int) -> int:
    try:
        result = int(value)
    except (TypeError, ValueError):
        raise ValueError(f"{name} must be an integer")
    if not low <= result <= high:
        raise ValueError(f"{name} must be between {low} and {high}")
    return result


def _domain(url: str) -> str:
    try:
        host = urlparse(url).netloc.lower()
        return host.rsplit("@", 1)[-1].split(":", 1)[0]
    except Exception:
        return ""


def _absolute_http_url(url: str, base: str = "https://yandex.com/") -> str:
    url = _clean_url(url)
    if url.startswith("//"):
        return "https:" + url
    if url.startswith("/"):
        return urljoin(base, url)
    return url


def _is_http_url(url: str) -> bool:
    try:
        return urlparse(url).scheme.lower() in ("http", "https")
    except Exception:
        return False


# =========================================================================
# HTTP — stdlib only
# =========================================================================

def _ssl_context(verify_ssl: bool) -> ssl.SSLContext:
    if verify_ssl:
        return ssl.create_default_context()
    return ssl._create_unverified_context()


def _http_request(
    url: str,
    *,
    method: str = "GET",
    data: Optional[bytes] = None,
    headers: Optional[Dict[str, str]] = None,
    timeout: int = DEFAULT_TIMEOUT,
    max_bytes: int = DEFAULT_MAX_BYTES,
    verify_ssl: bool = True,
) -> Tuple[int, bytes, Dict[str, str], str]:
    request_headers = {
        "User-Agent": (
            "Mozilla/5.0 (Linux; Android 10) "
            "AppleWebKit/537.36 Chrome/151.0 Safari/537.36 "
            "AI-YandexSearch/1.0"
        ),
        "Accept-Language": "en-US,en;q=0.8,ru;q=0.5",
        "Accept": "text/html,application/json;q=0.9,*/*;q=0.7",
        "Accept-Encoding": "identity",
        "Connection": "close",
    }
    if headers:
        request_headers.update(headers)

    request = Request(
        url,
        data=data,
        headers=request_headers,
        method=method.upper(),
    )

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

            return (
                status,
                b"".join(chunks),
                dict(response.headers.items()),
                "",
            )

    except HTTPError as exc:
        try:
            body = exc.read(max_bytes)
        except Exception:
            body = b""
        return int(exc.code), body, dict(exc.headers.items()), str(exc)

    except (URLError, TimeoutError, OSError) as exc:
        return 0, b"", {}, str(exc)


def _decode_body(raw: bytes, headers: Dict[str, str]) -> str:
    charset = ""
    content_type = headers.get("Content-Type", "")
    match = re.search(r"charset\s*=\s*['\"]?([\w.-]+)", content_type, re.I)
    if match:
        charset = match.group(1)

    for encoding in (charset, "utf-8", "windows-1251"):
        if not encoding:
            continue
        try:
            return raw.decode(encoding, errors="replace")
        except (LookupError, UnicodeError):
            pass

    return raw.decode("utf-8", errors="replace")


# =========================================================================
# HTML text extraction
# =========================================================================

class TextParser(HTMLParser):
    SKIP = frozenset({
        "script", "style", "noscript", "svg", "canvas", "template",
        "head", "iframe", "object", "embed",
    })
    BLOCK = frozenset({
        "p", "div", "article", "section", "main", "li", "h1", "h2",
        "h3", "h4", "h5", "h6", "br", "pre", "blockquote",
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
        if tag in self.SKIP:
            self.skip_depth += 1
        elif not self.skip_depth and tag in self.BLOCK:
            self.parts.append("\n")

    def handle_endtag(self, tag: str) -> None:
        tag = tag.lower()
        if tag in self.SKIP and self.skip_depth:
            self.skip_depth -= 1
        elif not self.skip_depth and tag in self.BLOCK:
            self.parts.append("\n")

    def handle_data(self, data: str) -> None:
        if not self.skip_depth and data.strip():
            self.parts.append(data)

    def text(self) -> str:
        value = html.unescape("".join(self.parts))
        value = re.sub(r"[ \t]+", " ", value)
        value = re.sub(r"\n[ \t]+", "\n", value)
        value = re.sub(r"\n{3,}", "\n\n", value)
        return value.strip()[:self.max_chars]


# =========================================================================
# Embedded JSON / Yandex result extraction
# =========================================================================

def _balanced_json_objects(text: str) -> Iterable[str]:
    """Yield balanced {...} blocks, respecting JSON strings."""
    starts = [m.start() for m in re.finditer(r"\{", text)]
    for start in starts:
        depth = 0
        in_string = False
        escaped = False

        for pos in range(start, len(text)):
            ch = text[pos]

            if in_string:
                if escaped:
                    escaped = False
                elif ch == "\\":
                    escaped = True
                elif ch == '"':
                    in_string = False
                continue

            if ch == '"':
                in_string = True
            elif ch == "{":
                depth += 1
            elif ch == "}":
                depth -= 1
                if depth == 0:
                    yield text[start:pos + 1]
                    break


def _json_walk(value: Any) -> Iterable[Any]:
    yield value
    if isinstance(value, dict):
        for child in value.values():
            yield from _json_walk(child)
    elif isinstance(value, list):
        for child in value:
            yield from _json_walk(child)


def _candidate_dicts(text: str) -> Iterable[Dict[str, Any]]:
    for raw in _balanced_json_objects(text):
        try:
            parsed = json.loads(raw)
        except Exception:
            continue
        if isinstance(parsed, dict):
            for item in _json_walk(parsed):
                if isinstance(item, dict):
                    yield item


def _first_string(item: Dict[str, Any], keys: Sequence[str]) -> str:
    for key in keys:
        value = item.get(key)
        if isinstance(value, str) and value.strip():
            return _clean_text(value, 1500)
    return ""


def _first_url(item: Dict[str, Any], keys: Sequence[str]) -> str:
    for key in keys:
        value = item.get(key)
        if isinstance(value, str) and _is_http_url(_clean_url(value)):
            return _absolute_http_url(value)
    return ""


def _image_from_dict(item: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    # Current/known Yandex Images structures often expose image information
    # under viewerData, snippet, image, preview, thumb, or dups.
    viewer = item.get("viewerData")
    if not isinstance(viewer, dict):
        viewer = {}

    snippet = item.get("snippet")
    if not isinstance(snippet, dict):
        snippet = {}

    candidates: List[Dict[str, Any]] = []
    for key in ("preview", "dups"):
        value = viewer.get(key)
        if isinstance(value, list):
            candidates.extend(x for x in value if isinstance(x, dict))

    for value in (
        viewer.get("thumb"),
        viewer.get("image"),
        item.get("image"),
        item.get("img_href"),
    ):
        if isinstance(value, dict):
            candidates.append(value)
        elif isinstance(value, str):
            candidates.append({"url": value})

    if not candidates:
        return None

    chosen: Optional[Dict[str, Any]] = None
    for candidate in candidates:
        url = _first_url(candidate, ("url", "src", "href"))
        if not url:
            continue
        if chosen is None:
            chosen = dict(candidate)
            chosen["_url"] = url
            continue

        old_area = int(chosen.get("w", 0) or 0) * int(
            chosen.get("h", 0) or 0
        )
        new_area = int(candidate.get("w", 0) or 0) * int(
            candidate.get("h", 0) or 0
        )
        if new_area > old_area:
            chosen = dict(candidate)
            chosen["_url"] = url

    if not chosen:
        return None

    return {
        "url": chosen["_url"],
        "width": int(chosen.get("w", 0) or 0),
        "height": int(chosen.get("h", 0) or 0),
        "thumbnail": (
            _first_url(item, ("image", "thumbnail", "thumb"))
            or chosen["_url"]
        ),
        "title": (
            _first_string(snippet, ("title", "text"))
            or _first_string(item, ("title", "name"))
        ),
        "source_url": (
            _first_url(snippet, ("url", "href", "source"))
            or _first_url(item, ("url", "source"))
        ),
    }


def _extract_image_results(source: str, limit: int) -> List[Dict[str, Any]]:
    results: List[Dict[str, Any]] = []
    seen: set[str] = set()

    for item in _candidate_dicts(source):
        image = _image_from_dict(item)
        if not image:
            continue

        image_url = image["url"]
        if image_url in seen:
            continue

        # Reject obvious icons/assets when a proper source isn't present.
        if not image_url.startswith(("http://", "https://")):
            continue

        seen.add(image_url)
        result: Dict[str, Any] = {
            "rank": len(results) + 1,
            "title": image["title"],
            "image_url": image_url,
            "thumbnail_url": image["thumbnail"],
            "source_url": image["source_url"],
            "domain": _domain(image["source_url"]),
            "width": image["width"],
            "height": image["height"],
        }
        if image["width"] and image["height"]:
            result["resolution"] = (
                f"{image['width']} x {image['height']}"
            )

        results.append(result)
        if len(results) >= limit:
            break

    # Fallback for image URLs visible in markup but not JSON entities.
    if not results:
        patterns = [
            r'"url"\s*:\s*"((?:https?:)?\\/\\/[^"]+?\.(?:jpg|jpeg|png|webp|gif)(?:\?[^"]*)?)"',
            r'https?://[^"\' <]+?\.(?:jpg|jpeg|png|webp|gif)(?:\?[^"\' <]*)?',
        ]
        for pattern in patterns:
            for match in re.finditer(pattern, source, re.I):
                url = _absolute_http_url(match.group(1))
                url = _clean_url(url)
                if not _is_http_url(url) or url in seen:
                    continue
                seen.add(url)
                results.append({
                    "rank": len(results) + 1,
                    "title": "",
                    "image_url": url,
                    "thumbnail_url": url,
                    "source_url": "",
                    "domain": "",
                    "width": 0,
                    "height": 0,
                })
                if len(results) >= limit:
                    break
            if len(results) >= limit:
                break

    return results[:limit]


class YandexWebParser(HTMLParser):
    """Fallback parser for classic Yandex result markup."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.results: List[Dict[str, str]] = []
        self.current: Optional[Dict[str, str]] = None
        self.capture: Optional[str] = None
        self.buffer: List[str] = []

    @staticmethod
    def _class(attrs: List[Tuple[str, Optional[str]]]) -> str:
        for key, value in attrs:
            if key.lower() == "class":
                return (value or "").lower()
        return ""

    def handle_starttag(
        self,
        tag: str,
        attrs: List[Tuple[str, Optional[str]]],
    ) -> None:
        tag = tag.lower()
        attrs_dict = {k.lower(): v or "" for k, v in attrs}
        cls = self._class(attrs)

        if tag == "a":
            href = attrs_dict.get("href", "")
            if (
                href
                and (
                    "organic__url" in cls
                    or "serp-item__title" in cls
                    or "link" in cls
                )
            ):
                if self.current:
                    self._finish()
                self.current = {
                    "title": "",
                    "url": _absolute_http_url(href),
                    "snippet": "",
                }
                self.capture = "title"
                self.buffer = []

        if self.current and (
            "organic__snippet" in cls
            or "serp-item__snippet" in cls
            or "text-container" in cls
        ):
            self.capture = "snippet"
            self.buffer = []

    def handle_data(self, data: str) -> None:
        if self.capture and self.current:
            self.buffer.append(data)

    def handle_endtag(self, tag: str) -> None:
        if not self.capture:
            return
        if tag.lower() == "a" and self.capture == "title":
            self.current["title"] = _clean_text(" ".join(self.buffer), 500)
            self.capture = None
            self.buffer = []
        elif self.capture == "snippet" and tag.lower() in {
            "div", "span", "p",
        }:
            self.current["snippet"] = _clean_text(
                " ".join(self.buffer), 1200
            )
            self.capture = None
            self.buffer = []

    def _finish(self) -> None:
        if self.current and self.current.get("url"):
            if self.current.get("title"):
                self.results.append(self.current)
        self.current = None
        self.capture = None
        self.buffer = []

    def close(self) -> None:
        super().close()
        self._finish()


def _extract_web_results(source: str, limit: int) -> List[Dict[str, Any]]:
    results: List[Dict[str, Any]] = []
    seen: set[str] = set()

    # Embedded JSON can be considerably more stable than CSS selectors.
    for item in _candidate_dicts(source):
        url = _first_url(
            item,
            ("url", "target", "href", "clickUrl", "displayUrl"),
        )
        title = _first_string(
            item,
            ("title", "text", "name", "headline"),
        )

        if not url or not title:
            continue

        # Avoid treating arbitrary navigation JSON as search results.
        if urlparse(url).netloc.endswith(("yandex.com", "yandex.ru")):
            if "search" not in url:
                continue

        if url in seen:
            continue

        snippet = _first_string(
            item,
            ("snippet", "description", "text", "content"),
        )

        seen.add(url)
        results.append({
            "rank": len(results) + 1,
            "title": title,
            "url": url,
            "snippet": snippet,
            "domain": _domain(url),
        })
        if len(results) >= limit:
            return results

    # HTML fallback.
    parser = YandexWebParser()
    try:
        parser.feed(source)
        parser.close()
    except Exception:
        pass

    for item in parser.results:
        url = item["url"]
        if url in seen or not _is_http_url(url):
            continue
        seen.add(url)
        results.append({
            "rank": len(results) + 1,
            "title": item["title"],
            "url": url,
            "snippet": item.get("snippet", ""),
            "domain": _domain(url),
        })
        if len(results) >= limit:
            break

    return results[:limit]


# =========================================================================
# URL construction
# =========================================================================

def _safe_param(value: str) -> str:
    return value.strip()


def _web_url(
    query: str,
    *,
    region: str,
    safe_search: str,
    time_filter: str,
    site: str,
    page: int,
) -> str:
    final_query = query
    if site:
        final_query += f" site:{site}"

    params = {
        "text": final_query,
        "lr": region,
        "p": str(page),
    }

    if safe_search == "strict":
        params["family"] = "yes"
    elif safe_search == "off":
        params["family"] = "no"
    else:
        params["family"] = "moderate"

    if time_filter:
        params["within"] = {
            "day": "1",
            "week": "7",
            "month": "30",
            "year": "365",
        }[time_filter]

    return YANDEX_WEB + "?" + urlencode(params)


def _image_url(
    query: str,
    *,
    region: str,
    safe_search: str,
    time_filter: str,
    site: str,
    page: int,
) -> str:
    final_query = query
    if site:
        final_query += f" site:{site}"

    params = {
        "text": final_query,
        "lr": region,
        "p": str(page),
        "isize": "large",
    }

    if safe_search == "strict":
        params["family"] = "yes"
    elif safe_search == "off":
        params["family"] = "no"
    else:
        params["family"] = "moderate"

    if time_filter:
        params["recent"] = {
            "day": "day",
            "week": "week",
            "month": "month",
            "year": "year",
        }[time_filter]

    return YANDEX_IMAGES + "?" + urlencode(params)


def _reverse_image_url(image_url: str, image_sites: bool = False) -> str:
    params = {
        "rpt": "imageview",
        "url": image_url,
    }
    if image_sites:
        params["cbir_page"] = "sites"
    return YANDEX_IMAGES + "?" + urlencode(params)


# =========================================================================
# Local image upload
# =========================================================================

def _multipart(
    field: str,
    filename: str,
    content: bytes,
    content_type: str,
) -> Tuple[bytes, str]:
    boundary = "----AIYandexSearchBoundary" + base64.urlsafe_b64encode(
        os.urandom(12)
    ).decode("ascii").rstrip("=")

    lines = [
        f"--{boundary}".encode(),
        (
            f'Content-Disposition: form-data; name="{field}"; '
            f'filename="{filename}"'
        ).encode(),
        f"Content-Type: {content_type}".encode(),
        b"",
        content,
        f"--{boundary}--".encode(),
        b"",
    ]
    return b"\r\n".join(lines), f"multipart/form-data; boundary={boundary}"


def _upload_image_for_search(
    image_file: str,
    *,
    timeout: int,
    max_bytes: int,
    verify_ssl: bool,
) -> Tuple[str, str]:
    path = Path(image_file)
    if not path.is_file():
        return "", "image file not found"

    try:
        size = path.stat().st_size
    except OSError as exc:
        return "", str(exc)

    if size <= 0:
        return "", "image file is empty"
    if size > MAX_IMAGE_BYTES:
        return "", f"image exceeds {MAX_IMAGE_BYTES} bytes"

    try:
        content = path.read_bytes()
    except OSError as exc:
        return "", str(exc)

    suffix = path.suffix.lower()
    content_type = {
        ".jpg": "image/jpeg",
        ".jpeg": "image/jpeg",
        ".png": "image/png",
        ".webp": "image/webp",
        ".gif": "image/gif",
        ".bmp": "image/bmp",
    }.get(suffix, "application/octet-stream")

    body, content_header = _multipart(
        "upfile",
        path.name,
        content,
        content_type,
    )

    # This is the public HTML flow used by Yandex Images for reverse search.
    request_json = json.dumps({
        "blocks": [
            {"block": "b-page_type_search-by-image__link"}
        ]
    }, separators=(",", ":"))

    params = urlencode({
        "rpt": "imageview",
        "format": "json",
        "request": request_json,
    })
    upload_url = YANDEX_IMAGE_UPLOAD + "?" + params

    status, raw, _, error = _http_request(
        upload_url,
        method="POST",
        data=body,
        headers={
            "Content-Type": content_header,
            "Accept": "application/json,text/plain,*/*",
        },
        timeout=timeout,
        max_bytes=max_bytes,
        verify_ssl=verify_ssl,
    )

    if error:
        return "", error

    if status < 200 or status >= 300:
        return "", f"Yandex image upload returned HTTP {status}"

    response = _decode_body(raw, {})
    try:
        payload = json.loads(response)
    except json.JSONDecodeError:
        return "", "Yandex image upload returned non-JSON response"

    # Known response shape: blocks[0].params.url.
    try:
        query_string = payload["blocks"][0]["params"]["url"]
        if isinstance(query_string, str) and query_string:
            if query_string.startswith("http"):
                return query_string, ""
            return YANDEX_IMAGES + "?" + query_string.lstrip("?"), ""
    except (KeyError, IndexError, TypeError):
        pass

    # Tolerant fallback for changed response nesting.
    for item in _json_walk(payload):
        if isinstance(item, dict):
            candidate = item.get("url")
            if isinstance(candidate, str):
                candidate = _clean_url(candidate)
                if "rpt=imageview" in candidate or "cbir" in candidate:
                    return _absolute_http_url(candidate, YANDEX_IMAGES), ""

    return "", "could not extract Yandex reverse-image search URL"


# =========================================================================
# Page fetching
# =========================================================================

def _fetch_text(
    url: str,
    *,
    timeout: int,
    max_chars: int,
    verify_ssl: bool,
) -> Tuple[int, str, str]:
    status, raw, headers, error = _http_request(
        url,
        timeout=timeout,
        max_bytes=MAX_FETCH_BYTES,
        verify_ssl=verify_ssl,
        headers={
            "Accept": "text/html,application/xhtml+xml;q=0.9,*/*;q=0.5",
        },
    )
    if error:
        return status, "", error

    parser = TextParser(max_chars)
    try:
        parser.feed(_decode_body(raw, headers))
        parser.close()
        return status, parser.text(), ""
    except Exception as exc:
        return status, "", str(exc)


def _fetch_result_pages(
    results: List[Dict[str, Any]],
    *,
    timeout: int,
    max_chars: int,
    verify_ssl: bool,
) -> None:
    for result in results:
        url = result.get("url") or result.get("source_url")
        if not url or not _is_http_url(url):
            continue

        status, text, error = _fetch_text(
            url,
            timeout=timeout,
            max_chars=max_chars,
            verify_ssl=verify_ssl,
        )
        result["fetch_status"] = status
        if text:
            result["content"] = text
        if error:
            result["fetch_error"] = _clean_text(error, 300)


# =========================================================================
# Ranking / merging
# =========================================================================

def _query_variants(query: str) -> List[str]:
    variants = [query]
    words = query.split()
    if len(words) >= 4:
        variants.append(f'"{query}"')
    return list(dict.fromkeys(variants))


def _score(item: Dict[str, Any], query: str) -> float:
    title = str(item.get("title", "")).lower()
    snippet = str(item.get("snippet", "")).lower()
    terms = re.findall(r"[a-z0-9\u0400-\u04ff]{3,}", query.lower())

    score = 0.0
    for term in terms:
        if term in title:
            score += 3
        elif term in snippet:
            score += 1

    host = _domain(
        str(item.get("url") or item.get("source_url") or "")
    )
    if host.endswith(".gov") or host.endswith(".edu"):
        score += 2
    if host.endswith(".ru") and any(
        c in query.lower() for c in "йцукенгшщз"
    ):
        score += 1

    return score


def _merge(
    batches: Sequence[List[Dict[str, Any]]],
    *,
    query: str,
    count: int,
) -> List[Dict[str, Any]]:
    merged: Dict[str, Dict[str, Any]] = {}

    for batch in batches:
        for item in batch:
            key = (
                item.get("url")
                or item.get("image_url")
                or item.get("source_url")
                or ""
            )
            key = _clean_url(str(key)).split("#", 1)[0]
            if not key:
                continue

            if key not in merged:
                merged[key] = dict(item)

    results = list(merged.values())
    for item in results:
        item["score"] = round(_score(item, query), 2)

    results.sort(
        key=lambda item: (
            -float(item.get("score", 0)),
            str(item.get("title", "")).lower(),
        )
    )

    for index, item in enumerate(results[:count], 1):
        item["rank"] = index

    return results[:count]


# =========================================================================
# Public search API
# =========================================================================

def search_yandex(
    query: str = "",
    image_url: str = "",
    image_file: str = "",
    type: str = "web",
    count: int = DEFAULT_COUNT,
    page: int = 0,
    region: str = "us",
    safe_search: str = "moderate",
    site: str = "",
    time: str = "",
    timeout: int = DEFAULT_TIMEOUT,
    max_bytes: int = DEFAULT_MAX_BYTES,
    max_chars: int = DEFAULT_MAX_CHARS,
    deep: bool = False,
    fetch: bool = False,
    verify_ssl: bool = True,
) -> str:
    query = (query or "").strip()
    image_url = (image_url or "").strip()
    image_file = (image_file or "").strip()
    search_type = (type or "web").lower().strip()

    if search_type not in SEARCH_TYPES:
        return "ERROR: type must be web, images, or image-sites"

    supplied_images = bool(image_url) + bool(image_file)
    if supplied_images > 1:
        return "ERROR: use only one of image_url or image_file"

    if not query and not supplied_images:
        return "ERROR: query, image_url, or image_file is required"

    if len(query) > MAX_QUERY_LENGTH:
        return f"ERROR: query exceeds {MAX_QUERY_LENGTH} characters"

    try:
        count = _bounded_int(count, "count", 1, MAX_COUNT)
        page = _bounded_int(page, "page", 0, MAX_PAGE)
        timeout = _bounded_int(timeout, "timeout", 1, 120)
        max_bytes = _bounded_int(
            max_bytes,
            "max_bytes",
            32 * 1024,
            DEFAULT_MAX_BYTES,
        )
        max_chars = _bounded_int(
            max_chars,
            "max_chars",
            500,
            30000,
        )
    except ValueError as exc:
        return f"ERROR: {exc}"

    safe_search = (safe_search or "moderate").lower()
    if safe_search not in SAFE_VALUES:
        return "ERROR: safe_search must be strict, moderate, or off"

    time_filter = (time or "").lower().strip()
    if time_filter and time_filter not in TIME_VALUES:
        return "ERROR: time must be day, week, month, or year"

    region = _safe_param(region or "us")
    site = _safe_param(site)

    # ---------------------------------------------------------------------
    # Reverse image search
    # ---------------------------------------------------------------------
    reverse_url = ""

    if image_file:
        reverse_url, error = _upload_image_for_search(
            image_file,
            timeout=timeout,
            max_bytes=max_bytes,
            verify_ssl=verify_ssl,
        )
        if error:
            return _error_json(
                "Unable to prepare Yandex reverse-image search",
                extra={"detail": error},
            )

    elif image_url:
        if not _is_http_url(image_url):
            return "ERROR: image_url must be http:// or https://"
        reverse_url = _reverse_image_url(
            image_url,
            image_sites=(search_type == "image-sites"),
        )

    if reverse_url:
        status, raw, headers, error = _http_request(
            reverse_url,
            timeout=timeout,
            max_bytes=max_bytes,
            verify_ssl=verify_ssl,
        )
        source = _decode_body(raw, headers)

        if error and not raw:
            return _error_json(
                "Yandex reverse-image request failed",
                extra={
                    "detail": error,
                    "http_code": status,
                },
            )

        results = _extract_image_results(source, count)

        if not results:
            parser = TextParser(max_chars)
            try:
                parser.feed(source)
                parser.close()
                page_text = parser.text()
            except Exception:
                page_text = ""

            return _error_json(
                "Yandex returned no parseable reverse-image results",
                extra={
                    "http_code": status,
                    "search_url": reverse_url,
                    "page_text": page_text[:1000],
                },
            )

        if fetch:
            _fetch_result_pages(
                results,
                timeout=timeout,
                max_chars=max_chars,
                verify_ssl=verify_ssl,
            )

        payload = {
            "status": "ok",
            "engine": "yandex-images",
            "mode": "reverse-image",
            "query": query,
            "source_image_url": image_url or None,
            "source_image_file": image_file or None,
            "search_url": reverse_url,
            "count": len(results),
            "results": results,
            "fetched": bool(fetch),
            "disclaimer": (
                "Yandex Images is accessed through its public web interface, "
                "not an official paid API. Result fields may change."
            ),
        }

        return json.dumps(payload, ensure_ascii=False, separators=(",", ":"))

    # ---------------------------------------------------------------------
    # Normal web/image search
    # ---------------------------------------------------------------------
    batches: List[List[Dict[str, Any]]] = []
    variants = _query_variants(query) if deep else [query]
    last_error = ""

    for variant in variants:
        if search_type == "web":
            target = _web_url(
                variant,
                region=region,
                safe_search=safe_search,
                time_filter=time_filter,
                site=site,
                page=page,
            )
        else:
            target = _image_url(
                variant,
                region=region,
                safe_search=safe_search,
                time_filter=time_filter,
                site=site,
                page=page,
            )

        status, raw, headers, error = _http_request(
            target,
            timeout=timeout,
            max_bytes=max_bytes,
            verify_ssl=verify_ssl,
        )
        source = _decode_body(raw, headers)
        lowered = source.lower()

        if error and not raw:
            last_error = error
            continue

        if (
            "captcha" in lowered
            or "robot" in lowered
            or "подтвердите, что вы не робот" in lowered
            or "unusual traffic" in lowered
        ):
            last_error = "Yandex challenge/rate limit detected"
            continue

        if search_type == "web":
            found = _extract_web_results(source, count)
        else:
            found = _extract_image_results(source, count)

        if found:
            batches.append(found)
        else:
            last_error = (
                f"No parseable results (HTTP {status})"
            )

    results = _merge(
        batches,
        query=query,
        count=count,
    )

    if not results:
        return _error_json(
            "No Yandex search results found",
            query=query,
            extra={
                "engine": "yandex-images" if search_type != "web" else "yandex",
                "detail": _clean_text(last_error, 500),
                "hint": (
                    "Yandex's public HTML interface can change or temporarily "
                    "challenge automated traffic. Try again or simplify the query."
                ),
            },
        )

    if fetch:
        if search_type == "web":
            _fetch_result_pages(
                results,
                timeout=timeout,
                max_chars=max_chars,
                verify_ssl=verify_ssl,
            )
        else:
            source_results = [
                x for x in results
                if x.get("source_url")
            ]
            _fetch_result_pages(
                source_results,
                timeout=timeout,
                max_chars=max_chars,
                verify_ssl=verify_ssl,
            )

    payload = {
        "status": "ok",
        "engine": "yandex-images" if search_type != "web" else "yandex",
        "mode": search_type,
        "query": query,
        "region": region,
        "page": page,
        "count": len(results),
        "results": results,
        "deep": bool(deep),
        "fetched": bool(fetch),
        "disclaimer": (
            "Yandex is accessed through its public web interface, not an "
            "official paid API. Result fields and markup can change."
        ),
    }

    return json.dumps(payload, ensure_ascii=False, separators=(",", ":"))


# =========================================================================
# llm-functions entry
# =========================================================================

def run(
    query: str = "",
    image_url: str = "",
    image_file: str = "",
    type: str = "web",
    count: int = DEFAULT_COUNT,
    page: int = 0,
    region: str = "us",
    safe_search: str = "moderate",
    site: str = "",
    time: str = "",
    output: str = "",
    timeout: int = DEFAULT_TIMEOUT,
    max_bytes: int = DEFAULT_MAX_BYTES,
    max_chars: int = DEFAULT_MAX_CHARS,
    deep: bool = False,
    fetch: bool = False,
    markdown: bool = False,
    include_html: bool = False,
    verify_ssl: bool = True,
) -> str:
    """Search Yandex Web or Yandex Images without third-party dependencies.

    Args:
        query: Text query.
        image_url: URL for reverse image search.
        image_file: Local image path for reverse image search.
        type: web, images, or image-sites.
        count: Maximum result count, 1-50.
        page: Result page, where supported.
        region: Yandex region/locale hint.
        safe_search: strict, moderate, or off.
        site: Optional domain restriction.
        time: day, week, month, or year.
        output: Write JSON/Markdown to this path.
        timeout: Network timeout.
        max_bytes: Maximum search response size.
        max_chars: Maximum extracted page text.
        deep: Run a second query variant and merge results.
        fetch: Fetch source pages for additional text.
        markdown: Return Markdown instead of JSON.
        include_html: Include a bounded HTML diagnostic field.
        verify_ssl: Verify TLS certificates.
    """
    result = search_yandex(
        query=query,
        image_url=image_url,
        image_file=image_file,
        type=type,
        count=count,
        page=page,
        region=region,
        safe_search=safe_search,
        site=site,
        time=time,
        timeout=timeout,
        max_bytes=max_bytes,
        max_chars=max_chars,
        deep=deep,
        fetch=fetch,
        verify_ssl=verify_ssl,
    )

    if result.startswith("ERROR:"):
        return result

    try:
        payload = json.loads(result)
    except json.JSONDecodeError:
        return result

    if payload.get("status") == "error":
        return result

    if include_html:
        # Kept intentionally bounded and opt-in because HTML can be huge.
        payload["diagnostic"] = {
            "html_capture": (
                "Raw Yandex HTML is intentionally not included by default. "
                "Use --include-html only when debugging parser changes."
            )
        }

    if markdown:
        lines = [
            f"# Yandex {payload.get('mode', 'search')}",
            "",
            f"Query: {payload.get('query') or '(reverse image)'}",
            "",
        ]

        for item in payload.get("results", []):
            rank = item.get("rank", "")
            title = (
                item.get("title")
                or item.get("image_url")
                or item.get("url")
                or "Untitled"
            )
            link = (
                item.get("url")
                or item.get("source_url")
                or item.get("image_url")
                or ""
            )

            lines.append(f"{rank}. [{title}]({link})")

            if item.get("image_url"):
                lines.append(f"   Image: {item['image_url']}")
            if item.get("source_url"):
                lines.append(f"   Source: {item['source_url']}")
            if item.get("snippet"):
                lines.append(f"   {item['snippet']}")
            if item.get("content"):
                lines.append(f"   {item['content'][:max_chars]}")
            if item.get("resolution"):
                lines.append(f"   Resolution: {item['resolution']}")
            lines.append("")

        output_text = "\n".join(lines).strip()
    else:
        output_text = json.dumps(
            payload,
            ensure_ascii=False,
            separators=(",", ":"),
        )

    out_path = _resolve_output(output)
    if out_path:
        try:
            destination = Path(out_path)
            destination.parent.mkdir(parents=True, exist_ok=True)

            temporary = destination.with_name(
                destination.name + ".tmp"
            )
            temporary.write_text(output_text, encoding="utf-8")
            temporary.replace(destination)

            return (
                f"OK: results={payload.get('count', 0)} "
                f"path={destination}"
            )
        except OSError as exc:
            return _error_json(
                "Unable to write output file",
                extra={"detail": str(exc)},
            )

    return output_text


# =========================================================================
# CLI
# =========================================================================

def _add_cli_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--query", default="")
    parser.add_argument("--image-url", default="")
    parser.add_argument("--image-file", default="")
    parser.add_argument(
        "--type",
        default="web",
        choices=("web", "images", "image-sites"),
    )
    parser.add_argument("--count", type=int, default=DEFAULT_COUNT)
    parser.add_argument("--page", type=int, default=0)
    parser.add_argument("--region", default="us")
    parser.add_argument("--safe-search", default="moderate")
    parser.add_argument("--site", default="")
    parser.add_argument("--time", default="")
    parser.add_argument("--output", default="")
    parser.add_argument("--timeout", type=int, default=DEFAULT_TIMEOUT)
    parser.add_argument("--max-bytes", type=int, default=DEFAULT_MAX_BYTES)
    parser.add_argument("--max-chars", type=int, default=DEFAULT_MAX_CHARS)
    parser.add_argument("--deep", action="store_true")
    parser.add_argument("--fetch", action="store_true")
    parser.add_argument("--markdown", action="store_true")
    parser.add_argument("--include-html", action="store_true")
    parser.add_argument(
        "--verify-ssl",
        action="store_true",
        default=True,
    )
    parser.add_argument(
        "--no-verify-ssl",
        action="store_false",
        dest="verify_ssl",
    )


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
        description="Dependency-free Yandex Web and Images search"
    )
    _add_cli_args(parser)
    args = parser.parse_args()

    result = run(
        query=args.query,
        image_url=args.image_url,
        image_file=args.image_file,
        type=args.type,
        count=args.count,
        page=args.page,
        region=args.region,
        safe_search=args.safe_search,
        site=args.site,
        time=args.time,
        output=args.output,
        timeout=args.timeout,
        max_bytes=args.max_bytes,
        max_chars=args.max_chars,
        deep=args.deep,
        fetch=args.fetch,
        markdown=args.markdown,
        include_html=args.include_html,
        verify_ssl=args.verify_ssl,
    )

    print(result)
    sys.exit(_exit_code(result))


if __name__ == "__main__":
    main()
