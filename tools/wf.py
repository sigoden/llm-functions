#!/usr/bin/env python3
# @describe A web fetching utility that uses Python's built-in urllib.request.
# It supports various HTTP methods, headers, timeouts, and SSL verification options.
# @option --action! <fetch|head|download|ping|headers|trace|batch> The action to perform.
# @option --url <TEXT> The URL to fetch. Required for all single-URL actions.
# @option --method=GET <GET|POST|PUT|DELETE|PATCH|HEAD|OPTIONS> HTTP method to use.
# @option --data <TEXT> Data to send in the request body (e.g., for POST/PUT).
# @option --headers <TEXT> Custom headers, comma-separated (e.g., "Header1: Value1,Header2: Value2").
# @option --timeout=30 <INT> Total request timeout in seconds.
# @option --connect-timeout=10 <INT> Connection timeout in seconds (conceptual).
# @flag --follow-redirects Follow HTTP redirects (default: True).
# @option --max-redirects=5 <INT> Maximum number of redirects to follow.
# @flag --verify-ssl Verify SSL certificates (default: True).
# @option --user-agent=Python_urllib/1.0 <STRING> Custom User-Agent header.
# @option --output <TEXT> Output file path for download action.
# @option --auth <TEXT> Basic authentication credentials (format: "username:password").
# @option --retry=0 <INT> Number of retry attempts on failure.
# @option --retry-delay=1 <INT> Delay between retries in seconds.
# @flag --verbose Enable verbose output with timing and diagnostic information.
# @flag --json Parse response as JSON and pretty-print it.
# @option --extract <TEXT> JSONPath-like expression to extract data (e.g., "data.items[0].name").
# @option --batch <TEXT> Path to a JSON file containing a list of requests to execute.
# @option --cache-ttl=0 <INT> Cache responses for this many seconds (0 = disabled).
# @option --rate-limit=0 <INT> Max requests per second for batch mode (0 = unlimited).
# @option --delimiter <TEXT> CSV delimiter (default: ','). Useful for custom CSV output.
# @option --proxy <TEXT> Proxy URL to use (e.g., "http://127.0.0.1:8080").
# @option --format=text <text|json|csv> Output format for results.
# @option --progress Enable progress bar for download and batch file writes.
# @option --output-file <TEXT> Destination file for batch results (instead of stdout).
# ---------------------------------------------------------------------------

import base64
import csv
import gzip
import hashlib
import io
import json
import logging
import os
import re
import socket
import ssl
import sys
import tempfile
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import zlib
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------
logging.basicConfig(
    level=logging.WARNING,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%Y-%m-%dT%H:%M:%S",
)
logger = logging.getLogger("web_fetcher")

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------
SUPPORTED_METHODS: Tuple[str, ...] = (
    "GET",
    "POST",
    "PUT",
    "DELETE",
    "PATCH",
    "HEAD",
    "OPTIONS",
)

SUPPORTED_ACTIONS: Tuple[str, ...] = (
    "fetch",
    "head",
    "download",
    "ping",
    "headers",
    "trace",
    "batch",
)

SUPPORTED_FORMATS: Tuple[str, ...] = ("text", "json", "csv")

DECODE_ENCODINGS: Tuple[str, ...] = ("utf-8", "utf-16", "latin-1")

TIMEOUT_MIN: int = 1
TIMEOUT_MAX: int = 300
RETRY_MAX: int = 10
RETRY_DELAY_MAX: int = 60
RATE_LIMIT_MAX: int = 100
CACHE_TTL_MAX: int = 3600  # 1 hour
MAX_RESPONSE_BODY: int = 10 * 1024 * 1024  # 10 MB guard

HEADER_SEPARATOR: str = "─" * 60
SECTION_SEPARATOR: str = "═" * 60

CONTENT_TYPE_JSON: str = "application/json"
CONTENT_TYPE_FORM: str = "application/x-www-form-urlencoded"

# Retry on these HTTP status codes (server / gateway errors only)
RETRYABLE_STATUS_CODES: Tuple[int, ...] = (429, 500, 502, 503, 504)

# ---------------------------------------------------------------------------
# In-process response cache (thread-safe)
# ---------------------------------------------------------------------------


@dataclass
class _CacheEntry:
    body: bytes
    status_code: int
    reason: str
    headers: List[Tuple[str, str]]
    url: str
    expires_at: float


class ResponseCache:
    """A simple thread-safe in-memory TTL cache keyed on (url, method, data)."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._store: Dict[str, _CacheEntry] = {}

    def _key(self, url: str, method: str, data: Optional[str]) -> str:
        raw = f"{method.upper()}:{url}:{data or ''}"
        return hashlib.sha256(raw.encode()).hexdigest()

    def get(self, url: str, method: str, data: Optional[str]) -> Optional[_CacheEntry]:
        with self._lock:
            entry = self._store.get(self._key(url, method, data))
            if entry and time.monotonic() < entry.expires_at:
                return entry
            return None

    def set(
        self,
        url: str,
        method: str,
        data: Optional[str],
        entry: _CacheEntry,
    ) -> None:
        with self._lock:
            self._store[self._key(url, method, data)] = entry

    def evict_expired(self) -> int:
        """Remove all expired entries; returns count removed."""
        now = time.monotonic()
        with self._lock:
            expired = [k for k, v in self._store.items() if v.expires_at <= now]
            for k in expired:
                del self._store[k]
        return len(expired)


_cache = ResponseCache()

# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------


@dataclass
class RequestStats:
    """Timing and size statistics for a single HTTP exchange."""

    start_time: float = field(default_factory=time.perf_counter)
    end_time: Optional[float] = None
    response_size: int = 0
    retry_count: int = 0
    cache_hit: bool = False

    def stop(self) -> None:
        self.end_time = time.perf_counter()

    @property
    def elapsed(self) -> float:
        if self.end_time is not None:
            return round(self.end_time - self.start_time, 4)
        return round(time.perf_counter() - self.start_time, 4)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "elapsed_seconds": self.elapsed,
            "response_size_bytes": self.response_size,
            "retry_count": self.retry_count,
            "cache_hit": self.cache_hit,
        }


@dataclass
class ResponseWrapper:
    """Enriched wrapper around an HTTP response."""

    status_code: int
    reason: str
    headers: List[Tuple[str, str]]
    body: bytes
    url: str
    stats: RequestStats = field(default_factory=RequestStats)

    # ------------------------------------------------------------------ #
    # Header helpers
    # ------------------------------------------------------------------ #
    def get_header(self, name: str, default: Optional[str] = None) -> Optional[str]:
        name_lower = name.lower()
        for k, v in self.headers:
            if k.lower() == name_lower:
                return v
        return default

    @property
    def content_type(self) -> Optional[str]:
        return self.get_header("Content-Type")

    @property
    def content_length(self) -> Optional[int]:
        val = self.get_header("Content-Length")
        try:
            return int(val) if val else None
        except ValueError:
            return None

    @property
    def is_json(self) -> bool:
        ct = self.content_type or ""
        return "json" in ct.lower()

    @property
    def is_success(self) -> bool:
        return 200 <= self.status_code < 300

    @property
    def is_redirect(self) -> bool:
        return 300 <= self.status_code < 400

    # ------------------------------------------------------------------ #
    # Body helpers
    # ------------------------------------------------------------------ #
    def text(self) -> str:
        return safe_decode(self.body, self.content_type)

    def json(self) -> Any:
        return json.loads(self.text())

    def headers_as_dict(self) -> Dict[str, str]:
        """Return headers as a plain dict (last value wins on duplicates)."""
        return {k: v for k, v in self.headers}

    def to_dict(self) -> Dict[str, Any]:
        """Serialise to a plain dict for JSON / CSV output."""
        return {
            "url": self.url,
            "status_code": self.status_code,
            "reason": self.reason,
            "headers": self.headers_as_dict(),
            "body": self.text(),
            "stats": self.stats.to_dict(),
        }


# ---------------------------------------------------------------------------
# Encoding helpers
# ---------------------------------------------------------------------------


def detect_charset(content_type: Optional[str]) -> Optional[str]:
    """Extract charset from a Content-Type header."""
    if not content_type:
        return None
    for part in content_type.split(";"):
        part = part.strip()
        if part.lower().startswith("charset="):
            return part.split("=", 1)[1].strip().strip('"').strip("'").lower()
    return None


def safe_decode(raw: bytes, content_type: Optional[str] = None) -> str:
    """Decode *raw* bytes to str."""
    charset = detect_charset(content_type)
    if charset:
        try:
            return raw.decode(charset)
        except (UnicodeDecodeError, LookupError):
            pass
    for enc in DECODE_ENCODINGS:
        try:
            return raw.decode(enc)
        except UnicodeDecodeError:
            continue
    return raw.decode("utf-8", errors="replace")


def decompress_body(raw: bytes, encoding: Optional[str]) -> bytes:
    """Decompress gzip / deflate / br encoded response bodies."""
    if not encoding:
        return raw
    enc = encoding.lower().strip()
    try:
        if enc == "gzip":
            return gzip.decompress(raw)
        if enc == "deflate":
            try:
                return zlib.decompress(raw)
            except zlib.error:
                return zlib.decompress(raw, -zlib.MAX_WBITS)
        if enc == "br":
            try:
                import brotli  # type: ignore

                return brotli.decompress(raw)
            except ImportError:
                logger.warning(
                    "brotli package not installed; returning compressed body."
                )
    except Exception as exc:
        logger.warning("Decompression failed (%s): %s", enc, exc)
    return raw


# ---------------------------------------------------------------------------
# URL utilities
# ---------------------------------------------------------------------------


def validate_and_normalise_url(url: str) -> Tuple[bool, str]:
    """Validate and normalise a URL."""
    if not url or not isinstance(url, str):
        return False, "URL must be a non-empty string."
    url = url.strip()
    if not url.startswith(("http://", "https://")):
        return False, "URL must start with http:// or https://"
    try:
        p = urllib.parse.urlparse(url)
        if not p.netloc:
            return False, "URL must contain a valid hostname."
        normalised = urllib.parse.urlunparse(
            p._replace(
                path=urllib.parse.quote(p.path, safe="/:@!$&'()*+,;="),
                query=urllib.parse.quote(p.query, safe="=&+%"),
            )
        )
        return True, normalised
    except Exception as exc:
        return False, f"Could not parse URL: {exc}"


def build_url_with_params(url: str, params: Dict[str, str]) -> str:
    """Append query parameters to an existing URL."""
    parsed = urllib.parse.urlparse(url)
    existing = urllib.parse.parse_qs(parsed.query, keep_blank_values=True)
    existing.update({k: [v] for k, v in params.items()})
    new_query = urllib.parse.urlencode(existing, doseq=True)
    return urllib.parse.urlunparse(parsed._replace(query=new_query))


# ---------------------------------------------------------------------------
# Header helpers
# ---------------------------------------------------------------------------


def parse_headers(raw: str) -> Tuple[Dict[str, str], Optional[str]]:
    """Parse a comma-separated ``Key: Value`` header string."""
    headers: Dict[str, str] = {}
    warnings: List[str] = []
    for pair in raw.split(","):
        pair = pair.strip()
        if not pair:
            continue
        if ":" not in pair:
            warnings.append(f"Skipped (no colon): '{pair}'")
            continue
        key, value = pair.split(":", 1)
        key = key.strip()
        if not key:
            warnings.append(f"Skipped (empty key): '{pair}'")
            continue
        headers[key] = value.strip()
    return headers, ("; ".join(warnings) or None)


def build_auth_header(auth_str: str) -> Tuple[Optional[str], Optional[str]]:
    """Build an Authorization header value from ``username:password``."""
    if ":" not in auth_str:
        return None, "Auth must be in 'username:password' format."
    encoded = base64.b64encode(auth_str.encode("utf-8")).decode("ascii")
    return f"Basic {encoded}", None


# ---------------------------------------------------------------------------
# JSON extraction
# ---------------------------------------------------------------------------


def extract_json_path(data: Any, path: str) -> Any:
    """Extract a value from a nested structure using a simple dotted path."""
    if not path:
        return data
    current = data
    tokens = re.findall(r'"[^"]*"|\\[[^\\]]+\\]|[^.\\[\\]]+', path)
    for token in tokens:
        if current is None:
            return None
        m = re.fullmatch(r"\[(\d+|\*)\]", token)
        if m:
            idx = m.group(1)
            if not isinstance(current, list):
                return None
            if idx == "*":
                rest_start = path.index(token) + len(token)
                rest = path[rest_start:].lstrip(".")
                return [extract_json_path(item, rest) for item in current]
            i = int(idx)
            current = current[i] if 0 <= i < len(current) else None
        else:
            key = token.strip('"')
            if isinstance(current, dict):
                current = current.get(key)
            else:
                return None
    return current


# ---------------------------------------------------------------------------
# Redirect handlers
# ---------------------------------------------------------------------------


class LimitedRedirectHandler(urllib.request.HTTPRedirectHandler):
    def __init__(self, max_redirects: int = 5) -> None:
        super().__init__()
        self.max_repeats = max_redirects
        self.max_redirections = max_redirects


class NoRedirectHandler(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):  # noqa: ANN
        return None


# ---------------------------------------------------------------------------
# Rate limiter
# ---------------------------------------------------------------------------


class RateLimiter:
    """Token-bucket rate limiter (requests per second)."""

    def __init__(self, rps: float) -> None:
        self._min_interval = 1.0 / rps if rps > 0 else 0.0
        self._last_call: float = 0.0
        self._lock = threading.Lock()

    def acquire(self) -> None:
        if self._min_interval <= 0:
            return
        with self._lock:
            wait = self._min_interval - (time.monotonic() - self._last_call)
            if wait > 0:
                time.sleep(wait)
            self._last_call = time.monotonic()


# ---------------------------------------------------------------------------
# SSL helpers
# ---------------------------------------------------------------------------


def build_ssl_context(verify: bool) -> Optional[ssl.SSLContext]:
    """Return an SSLContext appropriate for *verify* setting."""
    if verify:
        return None  # urllib default = verified
    try:
        ctx = ssl._create_unverified_context()
        return ctx
    except Exception as exc:
        raise RuntimeError(f"Could not create unverified SSL context: {exc}") from exc


def get_ssl_info(url: str, timeout: int = 10) -> Dict[str, Any]:
    """Retrieve TLS certificate metadata for *url*."""
    parsed = urllib.parse.urlparse(url)
    if parsed.scheme != "https":
        return {"error": "URL is not HTTPS"}
    host = parsed.hostname or ""
    port = parsed.port or 443
    result: Dict[str, Any] = {}
    try:
        ctx = ssl.create_default_context()
        with socket.create_connection((host, port), timeout=timeout) as sock:
            with ctx.wrap_socket(sock, server_hostname=host) as ssock:
                cert = ssock.getpeercert()
                result["subject"] = dict(x[0] for x in cert.get("subject", []))
                result["issuer"] = dict(x[0] for x in cert.get("issuer", []))
                result["serial_number"] = cert.get("serialNumber")
                result["not_before"] = cert.get("notBefore")
                result["not_after"] = cert.get("notAfter")
                result["san"] = cert.get("subjectAltName", [])
                result["protocol"] = ssock.version()
                result["cipher"] = ssock.cipher()
    except ssl.SSLCertVerificationError as exc:
        result["error"] = f"Certificate verification failed: {exc}"
    except Exception as exc:
        result["error"] = str(exc)
    return result


# ---------------------------------------------------------------------------
# Core HTTP request engine
# ---------------------------------------------------------------------------


def make_request(
    url: str,
    method: str = "GET",
    data: Optional[str] = None,
    headers_str: Optional[str] = None,
    timeout: int = 30,
    follow_redirects: bool = True,
    max_redirects: int = 5,
    verify_ssl: bool = True,
    user_agent: str = "Python_urllib/2.0",
    auth: Optional[str] = None,
    retry: int = 0,
    retry_delay: int = 1,
    verbose: bool = False,
    cache_ttl: int = 0,
    proxy: Optional[str] = None,
) -> Tuple[Optional[ResponseWrapper], Optional[str]]:
    """Execute an HTTP request and return (ResponseWrapper, None) on success or (None, error_string) on failure."""
    # ------------------------------------------------------------------ #
    # 1. Validate inputs
    # ------------------------------------------------------------------ #
    ok, url_or_err = validate_and_normalise_url(url)
    if not ok:
        return None, f"Error: {url_or_err}"
    url = url_or_err

    method_upper = method.upper()
    if method_upper not in SUPPORTED_METHODS:
        return None, (
            f"Error: Unsupported method '{method}'. "
            f"Supported: {', '.join(SUPPORTED_METHODS)}"
        )
    if not (TIMEOUT_MIN <= timeout <= TIMEOUT_MAX):
        return None, f"Error: Timeout must be {TIMEOUT_MIN}–{TIMEOUT_MAX}s."

    retry = min(max(0, retry), RETRY_MAX)
    retry_delay = min(max(1, retry_delay), RETRY_DELAY_MAX)

    # ------------------------------------------------------------------ #
    # 2. Cache lookup (GET / HEAD only)
    # ------------------------------------------------------------------ #
    cacheable = cache_ttl > 0 and method_upper in ("GET", "HEAD")
    if cacheable:
        cached = _cache.get(url, method_upper, data)
        if cached:
            logger.debug("Cache HIT  %s %s", method_upper, url)
            stats = RequestStats()
            stats.stop()
            stats.cache_hit = True
            return ResponseWrapper(
                status_code=cached.status_code,
                reason=cached.reason,
                headers=cached.headers,
                body=cached.body,
                url=cached.url,
                stats=stats,
            ), None

    # ------------------------------------------------------------------ #
    # 3. Build request headers
    # ------------------------------------------------------------------ #
    request_headers: Dict[str, str] = {}
    if headers_str:
        request_headers, warn = parse_headers(headers_str)
        if warn and verbose:
            logger.warning("Header parse warnings: %s", warn)

    if "User-Agent" not in request_headers:
        request_headers["User-Agent"] = user_agent

    # Signal we can handle compressed responses
    if "Accept-Encoding" not in request_headers:
        request_headers["Accept-Encoding"] = "gzip, deflate"

    if auth:
        header_val, auth_err = build_auth_header(auth)
        if auth_err:
            return None, f"Error: {auth_err}"
        request_headers["Authorization"] = header_val  # type: ignore[assignment]

    # ------------------------------------------------------------------ #
    # 4. Build request body
    # ------------------------------------------------------------------ #
    request_body: Optional[bytes] = None
    body_methods = {"POST", "PUT", "PATCH", "DELETE"}
    if data and method_upper in body_methods:
        if "Content-Type" not in request_headers:
            stripped = data.strip()
            if stripped.startswith(("{", "[")):
                request_headers["Content-Type"] = CONTENT_TYPE_JSON
                request_body = data.encode("utf-8")
            else:
                request_headers["Content-Type"] = CONTENT_TYPE_FORM
                try:
                    pairs = dict(
                        item.split("=", 1) for item in data.split("&") if "=" in item
                    )
                    request_body = urllib.parse.urlencode(pairs).encode("utf-8")
                except (ValueError, AttributeError):
                    request_body = data.encode("utf-8")
        else:
            request_body = data.encode("utf-8")

    if request_body:
        request_headers.setdefault("Content-Length", str(len(request_body)))

    # ------------------------------------------------------------------ #
    # 5. Build opener (SSL + proxy + redirects)
    # ------------------------------------------------------------------ #
    handlers: List[urllib.request.BaseHandler] = []
    try:
        ssl_ctx = build_ssl_context(verify_ssl)
        if ssl_ctx:
            handlers.append(urllib.request.HTTPSHandler(context=ssl_ctx))
    except RuntimeError as exc:
        return None, str(exc)

    if proxy:
        ok_p, proxy_or_err = validate_and_normalise_url(proxy)
        if not ok_p:
            return None, f"Error: Invalid proxy URL – {proxy_or_err}"
        handlers.append(urllib.request.ProxyHandler({"http": proxy, "https": proxy}))

    if follow_redirects:
        handlers.append(LimitedRedirectHandler(max_redirects=max_redirects))
    else:
        handlers.append(NoRedirectHandler())

    try:
        opener = urllib.request.build_opener(*handlers)
    except Exception as exc:
        return None, f"Error: Could not build opener: {exc}"

    # ------------------------------------------------------------------ #
    # 6. Execute with retry / back-off
    # ------------------------------------------------------------------ #
    stats = RequestStats()
    last_error: str = "Unknown error"

    for attempt in range(retry + 1):
        stats.retry_count = attempt
        if attempt > 0:
            wait = retry_delay * (2 ** (attempt - 1))  # exponential back-off
            logger.debug("Retry %d/%d – waiting %.1fs", attempt, retry, wait)
            time.sleep(wait)

        try:
            req = urllib.request.Request(
                url,
                data=request_body,
                method=method_upper,
                headers=request_headers,
            )
            logger.debug("→ %s %s", method_upper, url)
            stats.start_time = time.perf_counter()

            with opener.open(req, timeout=timeout) as resp:
                # Guard against enormous responses
                raw = resp.read(MAX_RESPONSE_BODY + 1)
                stats.stop()

                if len(raw) > MAX_RESPONSE_BODY:
                    return None, (
                        f"Error: Response body exceeds "
                        f"{MAX_RESPONSE_BODY // (1024 * 1024)} MB limit."
                    )

                # Decompress if needed
                raw = decompress_body(raw, resp.headers.get("Content-Encoding"))
                stats.response_size = len(raw)
                logger.debug(
                    "← %d  %.3fs  %d bytes",
                    resp.getcode(),
                    stats.elapsed,
                    stats.response_size,
                )

                wrapper = ResponseWrapper(
                    status_code=resp.getcode(),
                    reason=resp.reason,
                    headers=list(resp.getheaders()),
                    body=raw,
                    url=resp.geturl(),
                    stats=stats,
                )

                # Store in cache
                if cacheable:
                    _cache.set(
                        url,
                        method_upper,
                        data,
                        _CacheEntry(
                            body=raw,
                            status_code=resp.getcode(),
                            reason=resp.reason,
                            headers=list(resp.getheaders()),
                            url=resp.geturl(),
                            expires_at=time.monotonic() + cache_ttl,
                        ),
                    )

                return wrapper, None

        except urllib.error.HTTPError as exc:
            stats.stop()
            if exc.code not in RETRYABLE_STATUS_CODES:
                try:
                    err_body = safe_decode(exc.read(), exc.headers.get("Content-Type"))
                except Exception:
                    err_body = "(could not read error body)"
                return None, f"Error: HTTP {exc.code} {exc.reason}\n{err_body}"
            last_error = f"HTTP {exc.code} {exc.reason}"

        except urllib.error.URLError as exc:
            stats.stop()
            reason = getattr(exc, "reason", str(exc))
            if isinstance(reason, socket.gaierror):
                host = urllib.parse.urlparse(url).netloc
                last_error = f"DNS resolution failed for '{host}': {reason}"
            elif isinstance(reason, ssl.SSLError):
                last_error = f"SSL error: {reason}"
            else:
                last_error = f"URL error: {reason}"

        except (socket.timeout, TimeoutError):
            stats.stop()
            last_error = f"Timed out after {timeout}s"

        except Exception as exc:
            stats.stop()
            last_error = f"Unexpected {type(exc).__name__}: {exc}"

    return None, f"Error: {last_error} (after {retry + 1} attempt(s))"


# ---------------------------------------------------------------------------
# Output formatters
# ---------------------------------------------------------------------------


def _format_text(resp: ResponseWrapper, verbose: bool) -> str:
    """Render a ResponseWrapper as human‑readable text."""
    parts: List[str] = []
    if verbose:
        s = resp.stats
        parts += [
            SECTION_SEPARATOR,
            f"  URL      : {resp.url}",
            f"  Status   : {resp.status_code} {resp.reason}",
            f"  Elapsed  : {s.elapsed:.3f}s",
            f"  Size     : {s.response_size:,} bytes",
            f"  Retries  : {s.retry_count}",
            f"  CacheHit : {s.cache_hit}",
            SECTION_SEPARATOR,
            "",
        ]
    parts.append(f"── HTTP {resp.status_code} {resp.reason} ──")
    parts.append(HEADER_SEPARATOR)
    parts.extend(f"{k}: {v}" for k, v in resp.headers)
    parts.append(HEADER_SEPARATOR)
    parts.append("")
    parts.append(resp.text())
    return "\n".join(parts)


def _format_json(resp: ResponseWrapper, verbose: bool) -> str:
    """Render a ResponseWrapper as a JSON string."""
    d = resp.to_dict()
    if not verbose:
        d.pop("stats", None)
    return json.dumps(d, indent=2, ensure_ascii=False)


def _format_csv(responses: List[ResponseWrapper], delimiter: str = ",") -> str:
    """Render a list of ResponseWrappers as CSV (one row per response)."""
    buf = io.StringIO()
    # Fields we always want – extra columns are ignored
    fields = [
        "url",
        "status_code",
        "reason",
        "elapsed_seconds",
        "response_size_bytes",
        "cache_hit",
        "body_snippet",
    ]
    writer = csv.DictWriter(
        buf, fieldnames=fields, delimiter=delimiter, extrasaction="ignore"
    )
    writer.writeheader()
    for r in responses:
        snippet = r.text()[:200].replace("\n", " ")
        writer.writerow(
            {
                "url": r.url,
                "status_code": r.status_code,
                "reason": r.reason,
                "elapsed_seconds": r.stats.elapsed,
                "response_size_bytes": r.stats.response_size,
                "cache_hit": r.stats.cache_hit,
                "body_snippet": snippet,
            }
        )
    return buf.getvalue()


def format_response(
    resp: ResponseWrapper,
    output_format: str = "text",
    verbose: bool = False,
    parse_json: bool = False,
    extract: Optional[str] = None,
    delimiter: str = ",",
) -> str:
    """Render *resp* according to *output_format*."""
    # JSON extraction takes priority
    if extract or parse_json:
        try:
            json_data = resp.json()
            if extract:
                result = extract_json_path(json_data, extract)
                if result is None:
                    return f"Error: Path '{extract}' not found in response"
                return json.dumps(result, indent=2, ensure_ascii=False)
            return json.dumps(json_data, indent=2, ensure_ascii=False)
        except json.JSONDecodeError as exc:
            return f"Error: Response is not valid JSON: {exc}"

    if output_format == "json":
        return _format_json(resp, verbose)
    if output_format == "csv":
        # When CSV is requested we expect a *list* of responses (batch mode)
        # The caller should pass a list; we just return an empty string here
        return ""
    return _format_text(resp, verbose)


# ---------------------------------------------------------------------------
# Action: fetch
# ---------------------------------------------------------------------------


def action_fetch(
    url: str,
    *,
    method: str = "GET",
    data: Optional[str] = None,
    headers_str: Optional[str] = None,
    timeout: int = 30,
    follow_redirects: bool = True,
    max_redirects: int = 5,
    verify_ssl: bool = True,
    user_agent: str = "Python_urllib/2.0",
    auth: Optional[str] = None,
    retry: int = 0,
    retry_delay: int = 1,
    verbose: bool = False,
    parse_json: bool = False,
    extract: Optional[str] = None,
    output_format: str = "text",
    cache_ttl: int = 0,
    proxy: Optional[str] = None,
    delimiter: Optional[str] = None,
) -> str:
    resp, err = make_request(
        url=url,
        method=method,
        data=data,
        headers_str=headers_str,
        timeout=timeout,
        follow_redirects=follow_redirects,
        max_redirects=max_redirects,
        verify_ssl=verify_ssl,
        user_agent=user_agent,
        auth=auth,
        retry=retry,
        retry_delay=retry_delay,
        verbose=verbose,
        cache_ttl=cache_ttl,
        proxy=proxy,
    )
    if err:
        return err
    assert resp is not None
    # For single‑URL actions we simply render the response; delimiter is ignored.
    return format_response(
        resp,
        output_format=output_format,
        verbose=verbose,
        parse_json=parse_json,
        extract=extract,
        delimiter=delimiter,
    )


# ---------------------------------------------------------------------------
# Action: head
# ---------------------------------------------------------------------------


def action_head(
    url: str,
    *,
    headers_str: Optional[str] = None,
    timeout: int = 30,
    follow_redirects: bool = True,
    max_redirects: int = 5,
    verify_ssl: bool = True,
    user_agent: str = "Python_urllib/2.0",
    auth: Optional[str] = None,
    verbose: bool = False,
    output_format: str = "text",
) -> str:
    resp, err = make_request(
        url=url,
        method="HEAD",
        headers_str=headers_str,
        timeout=timeout,
        follow_redirects=follow_redirects,
        max_redirects=max_redirects,
        verify_ssl=verify_ssl,
        user_agent=user_agent,
        auth=auth,
    )
    if err:
        return err
    assert resp is not None
    return format_response(
        resp,
        output_format=output_format,
        verbose=verbose,
    )


# ---------------------------------------------------------------------------
# Action: headers  (returns headers only as JSON)
# ---------------------------------------------------------------------------


def action_headers(
    url: str,
    *,
    headers_str: Optional[str] = None,
    timeout: int = 30,
    verify_ssl: bool = True,
    user_agent: str = "Python_urllib/2.0",
    auth: Optional[str] = None,
) -> str:
    resp, err = make_request(
        url=url,
        method="HEAD",
        headers_str=headers_str,
        timeout=timeout,
        verify_ssl=verify_ssl,
        user_agent=user_agent,
        auth=auth,
    )
    if err:
        return err
    assert resp is not None
    return json.dumps(resp.headers_as_dict(), indent=2, ensure_ascii=False)


# ---------------------------------------------------------------------------
# Action: download
# ---------------------------------------------------------------------------


def _download_with_progress(
    resp: ResponseWrapper, output_path: str, enable_progress: bool
) -> None:
    """Write *resp.body* to *output_path* optionally showing a tqdm‑style progress bar."""
    # Simple textual progress bar – no external dependency
    total = len(resp.body)
    if not enable_progress or total == 0:
        with open(output_path, "wb") as f:
            f.write(resp.body)
        return

    # Basic progress indicator
    chunk_size = 8192
    written = 0
    with open(output_path, "wb") as f:
        for i in range(0, total, chunk_size):
            start = i
            end = min(i + chunk_size, total)
            f.write(resp.body[start:end])
            written = end
            percent = int((written / total) * 100)
            bar = "=" * percent + " " * (100 - percent)
            print(f"\rDownloading… [{bar}] {percent}%", end="", flush=True)
    print()  # newline after bar finishes


def action_download(
    url: str,
    *,
    output: Optional[str] = None,
    headers_str: Optional[str] = None,
    timeout: int = 60,
    follow_redirects: bool = True,
    max_redirects: int = 5,
    verify_ssl: bool = True,
    user_agent: str = "Python_urllib/2.0",
    auth: Optional[str] = None,
    retry: int = 0,
    retry_delay: int = 1,
    verbose: bool = False,
    proxy: Optional[str] = None,
    output_format: str = "text",
    cache_ttl: int = 0,
    delimiter: Optional[str] = None,
    progress: bool = False,
) -> str:
    resp, err = make_request(
        url=url,
        method="GET",
        headers_str=headers_str,
        timeout=timeout,
        follow_redirects=follow_redirects,
        max_redirects=max_redirects,
        verify_ssl=verify_ssl,
        user_agent=user_agent,
        auth=auth,
        retry=retry,
        retry_delay=retry_delay,
        verbose=verbose,
        cache_ttl=cache_ttl,
        proxy=proxy,
    )
    if err:
        return err
    assert resp is not None

    # ------------------------------------------------------------------ #
    # Determine output path
    # ------------------------------------------------------------------ #
    if not output:
        cd = resp.get_header("Content-Disposition", "")
        m = re.search(r"filename\*?=([^;]+)", cd or "")
        if m:
            output = urllib.parse.unquote(m.group(1)).strip("'\"")
        else:
            path_part = urllib.parse.urlparse(url).path
            output = os.path.basename(path_part) or "downloaded_file"

    # ------------------------------------------------------------------ #
    # Write file (with optional progress bar)
    # ------------------------------------------------------------------ #
    dir_ = os.path.dirname(os.path.abspath(output)) or "."
    try:
        fd, tmp_path = tempfile.mkstemp(dir=dir_)
        try:
            with os.fdopen(fd, "wb") as fh:
                fh.write(resp.body)
            os.replace(tmp_path, output)
        except Exception:
            os.unlink(tmp_path)
            raise
    except OSError as exc:
        return f"Error: Could not write '{output}': {exc}"

    # ------------------------------------------------------------------ #
    # Build result dict
    # ------------------------------------------------------------------ #
    result: Dict[str, Any] = {
        "status": "success",
        "file": output,
        "size_bytes": len(resp.body),
        "content_type": resp.content_type,
        "md5": hashlib.md5(resp.body).hexdigest(),
        "sha256": hashlib.sha256(resp.body).hexdigest(),
    }
    if verbose:
        result["elapsed_seconds"] = resp.stats.elapsed
        result["retries"] = resp.stats.retry_count
        result["source_url"] = resp.url
    return json.dumps(result, indent=2)


# ---------------------------------------------------------------------------
# Action: ping
# ---------------------------------------------------------------------------


def action_ping(
    url: str,
    *,
    timeout: int = 10,
    verify_ssl: bool = True,
    user_agent: str = "Python_urllib/2.0",
    verbose: bool = False,
    count: int = 1,
) -> str:
    """Ping *url* up to *count* times and report reachability / latency."""
    count = max(1, min(count, 10))
    results: List[Dict[str, Any]] = []

    for i in range(count):
        resp, err = make_request(
            url=url,
            method="HEAD",
            timeout=timeout,
            verify_ssl=verify_ssl,
            user_agent=user_agent,
        )
        if err:
            results.append(
                {
                    "attempt": i + 1,
                    "reachable": False,
                    "error": err.replace("Error: ", ""),
                }
            )
        else:
            assert resp is not None
            entry: Dict[str, Any] = {
                "attempt": i + 1,
                "reachable": True,
                "http_status": resp.status_code,
                "reason": resp.reason,
                "elapsed_seconds": resp.stats.elapsed,
            }
            if verbose:
                entry["server"] = resp.get_header("Server")
                entry["content_type"] = resp.content_type
                entry["url"] = resp.url
            results.append(entry)
        if i < count - 1:
            time.sleep(0.5)

    # Aggregate stats when multiple pings
    latencies = [r["elapsed_seconds"] for r in results if r.get("reachable")]
    summary: Dict[str, Any] = {"results": results}
    if latencies:
        summary["latency"] = {
            "min": round(min(latencies), 3),
            "max": round(max(latencies), 3),
            "avg": round(sum(latencies) / len(latencies), 3),
            "count": len(latencies),
        }
    summary["reachable_count"] = len(latencies)
    summary["total_attempts"] = count
    return json.dumps(summary, indent=2)


# ---------------------------------------------------------------------------
# Action: trace  (request tracing / SSL inspection)
# ---------------------------------------------------------------------------


def action_trace(
    url: str,
    *,
    timeout: int = 30,
    verify_ssl: bool = True,
    user_agent: str = "Python_urllib/2.0",
    verbose: bool = False,
) -> str:
    """Deep‑inspect *url*: DNS resolution, TCP reachability, TLS certificate info,
    HTTP response metadata, and redirect chain."""
    trace: Dict[str, Any] = {
        "url": url,
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }

    # 1. DNS
    try:
        parsed = urllib.parse.urlparse(url)
        host = parsed.hostname or ""
        port = parsed.port or (443 if parsed.scheme == "https" else 80)
        t0 = time.perf_counter()
        addrs = socket.getaddrinfo(host, port, proto=socket.IPPROTO_TCP)
        dns_ms = round((time.perf_counter() - t0) * 1000, 2)
        ips = list({a[4][0] for a in addrs})
        trace["dns"] = {"host": host, "resolved_ips": ips, "elapsed_ms": dns_ms}
    except Exception as exc:
        trace["dns"] = {"error": str(exc)}

    # 2. TCP connect
    try:
        t0 = time.perf_counter()
        with socket.create_connection((host, port), timeout=timeout):
            tcp_ms = round((time.perf_counter() - t0) * 1000, 2)
        trace["tcp"] = {"reachable": True, "elapsed_ms": tcp_ms}
    except Exception as exc:
        trace["tcp"] = {"reachable": False, "error": str(exc)}

    # 3. TLS
    if url.startswith("https://"):
        trace["tls"] = get_ssl_info(url, timeout=timeout)

    # 4. HTTP (follow redirects manually to record chain)
    redirect_chain: List[Dict[str, Any]] = []
    current_url = url
    for _ in range(10):
        resp, err = make_request(
            url=current_url,
            method="GET",
            timeout=timeout,
            verify_ssl=verify_ssl,
            user_agent=user_agent,
            follow_redirects=False,
        )
        if err:
            redirect_chain.append({"url": current_url, "error": err})
            break
        assert resp is not None
        hop: Dict[str, Any] = {
            "url": current_url,
            "status": resp.status_code,
            "reason": resp.reason,
            "elapsed_seconds": resp.stats.elapsed,
        }
        if verbose:
            hop["headers"] = resp.headers_as_dict()
        redirect_chain.append(hop)
        if not resp.is_redirect:
            break
        location = resp.get_header("Location")
        if not location:
            break
        current_url = urllib.parse.urljoin(current_url, location)

    trace["redirect_chain"] = redirect_chain
    trace["hops"] = len(redirect_chain)
    return json.dumps(trace, indent=2, ensure_ascii=False)


# ---------------------------------------------------------------------------
# Action: batch
# ---------------------------------------------------------------------------


def action_batch(
    batch_file: str,
    *,
    timeout: int = 30,
    verify_ssl: bool = True,
    user_agent: str = "Python_urllib/2.0",
    retry: int = 0,
    retry_delay: int = 1,
    verbose: bool = False,
    output_format: str = "text",
    cache_ttl: int = 0,
    proxy: Optional[str] = None,
    rate_limit: int = 0,
    delimiter: Optional[str] = None,
    parse_json: bool = False,
    extract: Optional[str] = None,
    progress: bool = False,
) -> str:
    """Execute a batch of HTTP requests defined in a JSON file."""
    # Load batch file
    try:
        batch_path = Path(batch_file)
        if not batch_path.exists():
            return f"Error: Batch file not found: '{batch_file}'"
        with batch_path.open(encoding="utf-8") as fh:
            requests_list = json.load(fh)
        if not isinstance(requests_list, list):
            return "Error: Batch file must contain a JSON array of request objects."
    except json.JSONDecodeError as exc:
        return f"Error: Could not parse batch file as JSON: {exc}"
    except OSError as exc:
        return f"Error: Could not read batch file: {exc}"

    limiter = RateLimiter(rate_limit)
    batch_results: List[Dict[str, Any]] = []
    responses_for_csv: List[ResponseWrapper] = []

    for i, req_def in enumerate(requests_list):
        if not isinstance(req_def, dict):
            batch_results.append(
                {"index": i, "error": "Request definition must be a JSON object."}
            )
            continue

        req_url = req_def.get("url", "")
        if not req_url:
            batch_results.append({"index": i, "error": "Missing 'url' field."})
            continue

        limiter.acquire()

        resp, err = make_request(
            url=req_url,
            method=req_def.get("method", "GET"),
            data=req_def.get("data"),
            headers_str=req_def.get("headers"),
            timeout=req_def.get("timeout", timeout),
            follow_redirects=req_def.get("follow_redirects", True),
            max_redirects=req_def.get("max_redirects", 5),
            verify_ssl=req_def.get("verify_ssl", verify_ssl),
            user_agent=req_def.get("user_agent", user_agent),
            auth=req_def.get("auth"),
            retry=req_def.get("retry", retry),
            retry_delay=req_def.get("retry_delay", retry_delay),
            verbose=verbose,
            cache_ttl=cache_ttl,
            proxy=proxy,
        )

        if err:
            batch_results.append({"index": i, "url": req_url, "error": err})
            continue

        assert resp is not None
        responses_for_csv.append(resp)

        entry: Dict[str, Any] = {
            "index": i,
            "url": resp.url,
            "status_code": resp.status_code,
            "reason": resp.reason,
            "elapsed_seconds": resp.stats.elapsed,
            "size_bytes": resp.stats.response_size,
            "cache_hit": resp.stats.cache_hit,
        }
        if verbose:
            entry["headers"] = resp.headers_as_dict()
        if parse_json or extract:
            try:
                jdata = resp.json()
                entry["body"] = extract_json_path(jdata, extract) if extract else jdata
            except json.JSONDecodeError:
                entry["body"] = resp.text()[:500]
        else:
            entry["body_snippet"] = resp.text()[:200]
        batch_results.append(entry)

    # ------------------------------------------------------------------ #
    # Output handling
    # ------------------------------------------------------------------ #
    if output_format == "csv":
        # When CSV is requested we expect the caller to also provide a delimiter.
        # The delimiter is taken from the environment variable `argc_delimiter`.
        csv_delim = delimiter or ","
        # For batch mode we can stream directly to a file if the user set
        # `--output-file`; otherwise we fall back to stdout.
        output_path = os.environ.get("LLM_OUTPUT", "")
        if output_path:
            # Write CSV to the designated file
            try:
                with open(output_path, "w", newline="", encoding="utf-8") as f:
                    f.write(_format_csv(responses_for_csv, delimiter=csv_delim))
                return f"Batch CSV written to {output_path}"
            except OSError as exc:
                return f"Error: Could not write CSV output: {exc}"
        else:
            # Print to stdout
            return _format_csv(responses_for_csv, delimiter=csv_delim)
    elif output_format == "json":
        # When JSON format is chosen we return the detailed batch result dict
        return json.dumps(
            {"total": len(requests_list), "results": batch_results},
            indent=2,
            ensure_ascii=False,
        )
    # Default: plain text summary
    return json.dumps(
        {"total": len(requests_list), "results": batch_results},
        indent=2,
        ensure_ascii=False,
    )


# ---------------------------------------------------------------------------
# Dispatcher
# ---------------------------------------------------------------------------


def run(
    action: str,
    url: Optional[str] = None,
    method: str = "GET",
    data: Optional[str] = None,
    headers: Optional[str] = None,
    timeout: int = 30,
    connect_timeout: int = 10,
    follow_redirects: bool = True,
    max_redirects: int = 5,
    verify_ssl: bool = True,
    user_agent: str = "Python_urllib/2.0",
    output: Optional[str] = None,
    auth: Optional[str] = None,
    retry: int = 0,
    retry_delay: int = 1,
    verbose: bool = False,
    parse_json: bool = False,
    extract: Optional[str] = None,
    batch: Optional[str] = None,
    cache_ttl: int = 0,
    rate_limit: int = 0,
    proxy: Optional[str] = None,
    output_format: str = "text",
    delimiter: Optional[str] = None,
) -> str:
    if not action:
        return f"Error: No action specified. Supported: {', '.join(SUPPORTED_ACTIONS)}"
    if action not in SUPPORTED_ACTIONS:
        return (
            f"Error: Unknown action '{action}'. "
            f"Supported: {', '.join(SUPPORTED_ACTIONS)}"
        )
    if output_format not in SUPPORTED_FORMATS:
        return (
            f"Error: Unknown format '{output_format}'. "
            f"Supported: {', '.join(SUPPORTED_FORMATS)}"
        )

    # ------------------------------------------------------------------ #
    # Common keyword arguments for single‑URL actions
    # ------------------------------------------------------------------ #
    common = dict(
        timeout=timeout,
        verify_ssl=verify_ssl,
        user_agent=user_agent,
        verbose=verbose,
    )

    # ------------------------------------------------------------------ #
    # Batch mode needs extra handling
    # ------------------------------------------------------------------ #
    if action == "batch":
        if not batch:
            return "Error: --batch <file> is required for the 'batch' action."
        # Pull delimiter from env (or use the argument if supplied)
        delim = delimiter or os.environ.get("argc_delimiter")
        return action_batch(
            batch,
            retry=retry,
            retry_delay=retry_delay,
            output_format=output_format,
            cache_ttl=cache_ttl,
            proxy=proxy,
            rate_limit=rate_limit,
            delimiter=delim,
            parse_json=parse_json,
            extract=extract,
        )

    if not url:
        return f"Error: --url is required for the '{action}' action."

    if action == "fetch":
        return action_fetch(
            url,
            method=method,
            data=data,
            headers_str=headers,
            follow_redirects=follow_redirects,
            max_redirects=max_redirects,
            auth=auth,
            retry=retry,
            retry_delay=retry_delay,
            verbose=verbose,
            parse_json=parse_json,
            extract=extract,
            output_format=output_format,
            cache_ttl=cache_ttl,
            proxy=proxy,
            delimiter=delimiter,
        )
    if action == "head":
        return action_head(
            url,
            headers_str=headers,
            follow_redirects=follow_redirects,
            max_redirects=max_redirects,
            auth=auth,
            output_format=output_format,
        )
    if action == "headers":
        return action_headers(url, headers_str=headers, auth=auth)
    if action == "download":
        return action_download(
            url,
            output=output,
            headers_str=headers,
            follow_redirects=follow_redirects,
            max_redirects=max_redirects,
            auth=auth,
            retry=retry,
            retry_delay=retry_delay,
            verbose=verbose,
            proxy=proxy,
            output_format=output_format,
            cache_ttl=cache_ttl,
            delimiter=delimiter,
            progress=_bool_env("WEBFETCHER_PROGRESS", False),
        )
    if action == "ping":
        return action_ping(url)
    if action == "trace":
        return action_trace(url)

    return f"Error: Action '{action}' is not implemented."


# ---------------------------------------------------------------------------
# Env helpers
# ---------------------------------------------------------------------------


def _bool_env(key: str, default: bool) -> bool:
    raw = os.environ.get(key, "").strip().lower()
    return raw in {"1", "true", "yes", "on"} if raw else default


def _int_env(key: str, default: int, *, lo: int = 0, hi: Optional[int] = None) -> int:
    raw = os.environ.get(key, "").strip()
    try:
        val = int(raw)
        if val < lo:
            return default
        if hi is not None and val > hi:
            return default
        return val
    except ValueError:
        return default


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


def main() -> None:
    if _bool_env("WEBFETCHER_DEBUG", False):
        logging.getLogger("web_fetcher").setLevel(logging.DEBUG)

    try:
        result = run(
            action=os.environ.get("argc_action", "").strip(),
            url=os.environ.get("argc_url", "").strip() or None,
            method=os.environ.get("argc_method", "GET").strip(),
            data=os.environ.get("argc_data") or None,
            headers=os.environ.get("argc_headers") or None,
            user_agent=os.environ.get("argc_user_agent", "Python_urllib/2.0").strip(),
            output=os.environ.get("argc_output") or None,
            auth=os.environ.get("argc_auth") or None,
            extract=os.environ.get("argc_extract") or None,
            batch=os.environ.get("argc_batch") or None,
            proxy=os.environ.get("argc_proxy") or None,
            output_format=os.environ.get("argc_format", "text").strip(),
            delimiter=os.environ.get("argc_delimiter"),
            timeout=_int_env("argc_timeout", 30, lo=TIMEOUT_MIN, hi=TIMEOUT_MAX),
            connect_timeout=_int_env("argc_connect_timeout", 10, lo=1),
            max_redirects=_int_env("argc_max_redirects", 5, lo=0),
            retry=_int_env("argc_retry", 0, lo=0, hi=RETRY_MAX),
            retry_delay=_int_env("argc_retry_delay", 1, lo=1, hi=RETRY_DELAY_MAX),
            cache_ttl=_int_env("argc_cache_ttl", 0, lo=0, hi=CACHE_TTL_MAX),
            rate_limit=_int_env("argc_rate_limit", 0, lo=0, hi=RATE_LIMIT_MAX),
            follow_redirects=_bool_env("argc_follow_redirects", True),
            verify_ssl=_bool_env("argc_verify_ssl", True),
            verbose=_bool_env("argc_verbose", False),
            parse_json=_bool_env("argc_json", False),
        )

        output_path = os.environ.get("LLM_OUTPUT", "").strip()
        if output_path:
            try:
                with open(output_path, "a", encoding="utf-8") as fh:
                    fh.write(result + "\n")
            except OSError as exc:
                print(
                    f"Error: Cannot write to LLM_OUTPUT '{output_path}': {exc}",
                    file=sys.stderr,
                )
                print(result)
        else:
            print(result)

    except KeyboardInterrupt:
        print("\nInterrupted.", file=sys.stderr)
        sys.exit(130)
    except Exception as exc:
        print(f"Fatal: {type(exc).__name__}: {exc}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
