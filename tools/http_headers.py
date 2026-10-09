#!/usr/bin/env python3
# @describe Fetch and analyze HTTP headers for a URL.
# @option --url! <URL> The target URL.
# @option --method <METHOD> HTTP method to use: HEAD or GET (default: HEAD).
# @option --user-agent <UA> Custom User-Agent string.
# @option --timeout <SECONDS> Request timeout in seconds (default: 10).
# @option --ignore-ssl <BOOL> If true, bypass SSL certificate verification.
# @option --follow-redirects <BOOL> If true, follow redirects (default: true).
# @option --output <FORMAT> Output format: json or pretty (default: json).

import json
import os
import socket
import ssl
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from typing import Optional

# ── constants ────────────────────────────────────────────────────────────────
_DEFAULT_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/124.0.0.0 Safari/537.36"
)
_ALLOWED_METHODS = ("HEAD", "GET", "OPTIONS", "POST")
_DEFAULT_TIMEOUT = 10.0
_MAX_TIMEOUT = 120.0
_MIN_TIMEOUT = 0.5

# Security-relevant headers to flag in analysis
_SECURITY_HEADERS = [
    "strict-transport-security",
    "content-security-policy",
    "x-frame-options",
    "x-content-type-options",
    "referrer-policy",
    "permissions-policy",
    "x-xss-protection",
    "cross-origin-opener-policy",
    "cross-origin-embedder-policy",
    "cross-origin-resource-policy",
]

# ── helpers ──────────────────────────────────────────────────────────────────


def _coerce_bool(value) -> bool:
    """Robustly convert env-var strings, ints, or bools to bool."""
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in ("true", "1", "yes", "on")


def _coerce_timeout(value) -> float:
    """Clamp timeout to a safe range."""
    try:
        t = float(value)
    except (TypeError, ValueError):
        return _DEFAULT_TIMEOUT
    return max(_MIN_TIMEOUT, min(t, _MAX_TIMEOUT))


def _validate_url(url: str) -> Optional[str]:
    """
    Return None if the URL looks valid, or an error string if not.
    Improvement 1: explicit URL validation before any network call.
    """
    try:
        parsed = urllib.parse.urlparse(url)
        if parsed.scheme not in ("http", "https"):
            return f"Unsupported scheme '{parsed.scheme}'. Only http/https allowed."
        if not parsed.netloc:
            return "URL is missing a host / netloc."
        return None
    except Exception as exc:
        return f"URL parse error: {exc}"


def _build_ssl_context(ignore_ssl: bool) -> Optional[ssl.SSLContext]:
    """
    Improvement 2: always create an explicit SSL context (not None) so
    the caller gets TLS 1.2+ even when verification is enabled, and we
    attach the ignore_ssl flag as a readable attribute for the output.
    """
    ctx = ssl.create_default_context()
    if ignore_ssl:
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE
    return ctx


def _normalise_headers(raw: dict) -> dict:
    """
    Improvement 3: normalise header keys to lowercase so consumers
    can do case-insensitive look-ups without extra work.
    Also strips leading/trailing whitespace from values.
    """
    return {k.lower().strip(): str(v).strip() for k, v in raw.items()}


def _analyse_headers(headers: dict) -> dict:
    """
    Improvement 4: lightweight security-header audit baked into every
    response so callers get actionable data without a second tool.
    """
    present = [h for h in _SECURITY_HEADERS if h in headers]
    missing = [h for h in _SECURITY_HEADERS if h not in headers]
    score = round(len(present) / len(_SECURITY_HEADERS) * 100)
    return {
        "security_headers_present": present,
        "security_headers_missing": missing,
        "security_score_pct": score,
    }


def _extract_server_info(headers: dict) -> dict:
    """
    Improvement 5: pull commonly-interesting fields into a dedicated
    block so callers don't have to grep the full header dict.
    """
    info: dict = {}
    for key in (
        "server",
        "x-powered-by",
        "via",
        "x-cache",
        "cf-ray",
        "x-amz-request-id",
        "content-type",
        "content-length",
        "cache-control",
        "etag",
        "last-modified",
        "expires",
        "age",
    ):
        if key in headers:
            info[key] = headers[key]
    return info


def _classify_status(code: int) -> str:
    """
    Improvement 6: human-readable status category avoids magic-number
    comparisons in downstream scripts.
    """
    if code < 200:
        return "informational"
    if code < 300:
        return "success"
    if code < 400:
        return "redirection"
    if code < 500:
        return "client_error"
    return "server_error"


# ── no-redirect handler ──────────────────────────────────────────────────────


class _NoRedirectHandler(urllib.request.HTTPRedirectHandler):
    """
    Improvement 7: optional redirect suppression — when follow_redirects
    is False we capture the 3xx response instead of silently following it,
    which lets callers see the Location header and chain manually.
    """

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None  # do not follow


# ── core function ────────────────────────────────────────────────────────────


def run(
    url: str,
    method: str = "HEAD",
    user_agent: str = None,
    timeout: float = _DEFAULT_TIMEOUT,
    ignore_ssl: bool = False,
    follow_redirects: bool = True,
    output: str = "json",
) -> str:
    # ── 1. coerce / validate inputs ─────────────────────────────────────────
    method = (method or "HEAD").upper().strip()
    ignore_ssl = _coerce_bool(ignore_ssl)
    follow_redirects = _coerce_bool(follow_redirects)
    timeout = _coerce_timeout(timeout)
    output = (output or "json").lower().strip()

    # Improvement 8: accept any method in _ALLOWED_METHODS, not just HEAD/GET
    if method not in _ALLOWED_METHODS:
        method = "HEAD"

    ua = (user_agent or "").strip() or _DEFAULT_UA
    ctx = _build_ssl_context(ignore_ssl)

    # Improvement 9: validate URL before touching the network
    url_error = _validate_url(url)
    if url_error:
        return json.dumps({"success": False, "error": url_error}, indent=2)

    # ── 2. build opener ──────────────────────────────────────────────────────
    # Improvement 10: pluggable opener so redirect behaviour is controllable
    if follow_redirects:
        opener = urllib.request.build_opener(urllib.request.HTTPSHandler(context=ctx))
    else:
        opener = urllib.request.build_opener(
            urllib.request.HTTPSHandler(context=ctx),
            _NoRedirectHandler(),
        )

    req = urllib.request.Request(
        url,
        headers={
            "User-Agent": ua,
            # Improvement 11: send realistic Accept headers so servers
            # don't serve degraded responses to bare HEAD requests
            "Accept": "*/*",
            "Accept-Language": "en-US,en;q=0.9",
            "Accept-Encoding": "gzip, deflate, br",
            "Connection": "close",
        },
        method=method,
    )

    # ── 3. execute request ───────────────────────────────────────────────────
    start_time = time.perf_counter()
    try:
        try:
            with opener.open(req, timeout=timeout) as r:
                elapsed = time.perf_counter() - start_time
                raw_headers = _normalise_headers(dict(r.info()))
                final_url = r.geturl()
                status_code = r.getcode()

                # Improvement 12: capture redirect chain length
                redirect_count = 0
                if hasattr(r, "url") and r.url != url:
                    redirect_count = 1  # urllib collapses chain; flag if moved

                result = {
                    "success": True,
                    "url": final_url,
                    "original_url": url,
                    "status_code": status_code,
                    "status_category": _classify_status(status_code),
                    "elapsed_ms": round(elapsed * 1000, 2),
                    "method": method,
                    "ssl_verified": not ignore_ssl,
                    "redirected": final_url != url,
                    "redirect_count": redirect_count,
                    "headers": raw_headers,
                    "server_info": _extract_server_info(raw_headers),
                    "security_audit": _analyse_headers(raw_headers),
                }

        except urllib.error.HTTPError as he:
            elapsed = time.perf_counter() - start_time
            raw_headers = _normalise_headers(dict(he.headers))

            # Improvement 13: HTTPError is still a valid response —
            # include body snippet for 4xx/5xx to aid debugging
            body_snippet = ""
            if method == "GET":
                try:
                    body_snippet = he.read(512).decode("utf-8", errors="replace")
                except Exception:
                    pass

            result = {
                "success": True,
                "url": url,
                "original_url": url,
                "status_code": he.code,
                "status_category": _classify_status(he.code),
                "elapsed_ms": round(elapsed * 1000, 2),
                "method": method,
                "ssl_verified": not ignore_ssl,
                "redirected": False,
                "redirect_count": 0,
                "headers": raw_headers,
                "server_info": _extract_server_info(raw_headers),
                "security_audit": _analyse_headers(raw_headers),
                "error_reason": he.reason,
                **({"body_snippet": body_snippet} if body_snippet else {}),
            }

    # Improvement 14: granular exception mapping — gives callers a
    # machine-readable error_type field instead of raw exception text only
    except ssl.SSLError as exc:
        return json.dumps(
            {
                "success": False,
                "error_type": "ssl_error",
                "error": str(exc),
                "hint": "Try --ignore-ssl true to bypass certificate verification.",
            },
            indent=2,
        )

    except socket.timeout:
        return json.dumps(
            {
                "success": False,
                "error_type": "timeout",
                "error": f"Request timed out after {timeout}s.",
                "hint": "Increase --timeout or check network connectivity.",
            },
            indent=2,
        )

    except urllib.error.URLError as exc:
        reason = str(exc.reason) if hasattr(exc, "reason") else str(exc)
        return json.dumps(
            {
                "success": False,
                "error_type": "url_error",
                "error": reason,
            },
            indent=2,
        )

    except OSError as exc:
        return json.dumps(
            {
                "success": False,
                "error_type": "os_error",
                "error": str(exc),
            },
            indent=2,
        )

    except Exception as exc:
        return json.dumps(
            {
                "success": False,
                "error_type": "unexpected",
                "error": str(exc),
            },
            indent=2,
        )

    # ── 4. format output ─────────────────────────────────────────────────────
    # Improvement 15: optional pretty-print mode renders a human-readable
    # summary table without breaking the default JSON contract
    if output == "pretty":
        lines = [
            f"  URL          : {result.get('url')}",
            f"  Status       : {result.get('status_code')} "
            f"({result.get('status_category')})",
            f"  Elapsed      : {result.get('elapsed_ms')} ms",
            f"  Method       : {result.get('method')}",
            f"  SSL verified : {result.get('ssl_verified')}",
            f"  Redirected   : {result.get('redirected')}",
            "",
            "  Server info:",
        ]
        for k, v in (result.get("server_info") or {}).items():
            lines.append(f"    {k:<26}: {v}")
        lines += [
            "",
            "  Security audit:",
            f"    Score : {result['security_audit']['security_score_pct']}%",
            f"    Present ({len(result['security_audit']['security_headers_present'])}) : "
            + ", ".join(result["security_audit"]["security_headers_present"] or ["-"]),
            f"    Missing ({len(result['security_audit']['security_headers_missing'])}) : "
            + ", ".join(result["security_audit"]["security_headers_missing"] or ["-"]),
            "",
            "  All headers:",
        ]
        for k, v in sorted((result.get("headers") or {}).items()):
            lines.append(f"    {k:<34}: {v}")
        return "\n".join(lines)

    return json.dumps(result, indent=2)


# ── CLI entry-point ───────────────────────────────────────────────────────────

if __name__ == "__main__":
    kwargs: dict = {}

    for k, v in os.environ.items():
        if k.startswith("argc_"):
            key = k[5:]
            if key in ("ignore_ssl", "follow_redirects"):
                kwargs[key] = _coerce_bool(v)
            elif key == "timeout":
                kwargs[key] = _coerce_timeout(v)
            else:
                kwargs[key] = v

    # Fallback: accept bare positional URL as first argv
    if not kwargs.get("url") and len(sys.argv) > 1:
        kwargs["url"] = sys.argv[1]

    if not kwargs.get("url"):
        print(
            json.dumps(
                {
                    "success": False,
                    "error": "Missing required argument: --url",
                },
                indent=2,
            )
        )
        sys.exit(1)

    print(run(**kwargs))
