#!/usr/bin/env python3
# @describe Fetch content from a URL using curl with advanced options.
# @option --url! The URL to fetch.
# @option --timeout=30 <INT> Request timeout in seconds (used as --max-time when max-time is default).
# @option --max-time=60 <INT> Maximum transfer time in seconds.
# @option --connect-timeout=10 <INT> Connection timeout in seconds.
# @option --user-agent=curl_tool/1.0 Custom User-Agent header.
# @option --output= Output file path (default: LLM_OUTPUT or return body in tool result).
# @option --dump-header= Output file path for HTTP response headers.
# @option --cookie-file= Path to cookie file for session management.
# @option --method=GET HTTP method (GET, POST, PUT, DELETE, PATCH, HEAD).
# @option --data= Request body data for POST/PUT/PATCH.
# @option --headers= Comma-separated headers (e.g., "Key: Val,Key2: Val2").
# @flag --follow-redirects Follow HTTP redirects.
# @option --max-redirects=5 <INT> Maximum number of redirects.
# @flag --verify-ssl Verify SSL certificates (default: true).
# @flag --compressed Request compressed response.
# @flag --silent Silent mode (less curl stderr).
# @flag --verbose Verbose curl output to stderr.
# @flag --include Include HTTP response headers in the body output.
# @option --retry=3 <INT> Number of retry attempts.
# @option --retry-delay=1 <INT> Delay between retries in seconds.
# @option --limit-rate= Limit download rate (e.g., 100k, 1m).
# @option --proxy= Proxy server URL.
# @option --auth= Authentication credentials (user:password or bearer token).
# @option --auth-type=basic Authentication type (basic or bearer).
# @option --max-bytes=10485760 <INT> Abort if body exceeds this many bytes (default 10 MiB).
# @flag --json-on-success If set, return JSON with body, http_code, and final_url on success.
# @env LLM_OUTPUT=/dev/stdout The output path when --output is empty.

"""
curl_fetch.py — HTTP fetch via curl (llm-functions / Termux / webx).

Compatibility:
- Preserves the public run() and fetch_url() signatures.
- Preserves the CLI option names and output conventions.
- Success: response body (UTF-8 with replacement), or JSON if --json-on-success.
- HTTP / curl failures: single-line JSON {"status":"error",...}.
- Pre-flight failures: ERROR: ... (missing url, curl, invalid method).

Enhancements:
- Stronger numeric/input validation and safer URL parsing.
- Correct --silent behavior.
- Better cleanup and atomic file writes.
- Response-size enforcement with curl --max-filesize where possible.
- Accurate curl exit diagnostics.
- Safer header parsing and sensitive-header redaction in errors.
- Retry-aware defaults and redirect handling.
- More reliable header parsing for redirects/interim responses.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import urlparse

ALLOWED_METHODS = frozenset(
    {"GET", "POST", "PUT", "DELETE", "PATCH", "HEAD", "OPTIONS"}
)

DEFAULT_MAX_BYTES = 10 * 1024 * 1024
MAX_ALLOWED_BYTES = 100 * 1024 * 1024
DEFAULT_USER_AGENT = "curl_tool/1.0"

CURL_EXIT_CODES = {
    1: "Unsupported protocol",
    2: "Failed to initialize",
    3: "URL malformed",
    4: "Feature not supported",
    5: "Could not resolve proxy",
    6: "Could not resolve host",
    7: "Failed to connect to host",
    8: "FTP server error",
    18: "Partial file transfer",
    22: "HTTP request returned an error status",
    23: "Write error",
    26: "Read error",
    27: "Out of memory",
    28: "Operation timeout / connection timed out",
    35: "SSL connect error",
    47: "Too many redirects",
    52: "Server returned nothing",
    55: "Failed sending network data",
    56: "Failed receiving network data",
    60: "SSL peer certificate or SSH remote key was not OK",
    63: "Maximum file size exceeded",
    67: "Login denied",
    77: "Problem with local certificate",
    92: "HTTP/2 stream error",
}

SENSITIVE_HEADER_NAMES = frozenset(
    {
        "authorization",
        "proxy-authorization",
        "cookie",
        "set-cookie",
        "x-api-key",
        "api-key",
        "x-auth-token",
    }
)


# =========================================================================
# JSON helpers
# =========================================================================


def _sanitize_json_text(s: str, max_len: int = 2000) -> str:
    if not s:
        return ""
    s = str(s)[: max_len + 100].replace("\x00", "")
    out: List[str] = []
    for ch in s:
        if ch in "\n\r\t":
            out.append(ch)
        elif ord(ch) < 32:
            out.append(" ")
        else:
            out.append(ch)
    return "".join(out)[:max_len]


def _redact_headers(headers: Dict[str, str]) -> Dict[str, str]:
    return {
        key: ("[REDACTED]" if key.lower() in SENSITIVE_HEADER_NAMES else value)
        for key, value in headers.items()
    }


def _error_json(
    http_code: int,
    msg: str,
    stderr: str = "",
    extra: Optional[Dict[str, Any]] = None,
) -> str:
    payload: Dict[str, Any] = {
        "status": "error",
        "http_code": int(http_code),
        "msg": _sanitize_json_text(msg, 2000),
    }
    if stderr:
        payload["stderr"] = _sanitize_json_text(stderr, 1000)
    if extra:
        for key, value in extra.items():
            if isinstance(value, str):
                payload[key] = _sanitize_json_text(value, 2000)
            else:
                payload[key] = value
    return json.dumps(payload, ensure_ascii=False, separators=(",", ":"))


def _success_json(
    http_code: int,
    body: str,
    final_url: str,
    bytes_read: int,
    truncated: bool,
    headers: Optional[Dict[str, str]] = None,
) -> str:
    payload: Dict[str, Any] = {
        "status": "ok",
        "http_code": int(http_code),
        "final_url": final_url,
        "bytes": int(bytes_read),
        "truncated": bool(truncated),
        "body": body,
    }
    if headers:
        payload["headers"] = headers
    return json.dumps(payload, ensure_ascii=False, separators=(",", ":"))


# =========================================================================
# Validation / parsing
# =========================================================================


def _positive_int(value: Any, name: str, minimum: int = 1) -> int:
    try:
        result = int(value)
    except (TypeError, ValueError):
        raise ValueError(f"{name} must be an integer")
    if result < minimum:
        raise ValueError(f"{name} must be >= {minimum}")
    return result


def _nonnegative_int(value: Any, name: str) -> int:
    try:
        result = int(value)
    except (TypeError, ValueError):
        raise ValueError(f"{name} must be an integer")
    if result < 0:
        raise ValueError(f"{name} must be >= 0")
    return result


def _parse_headers_csv(raw: str) -> List[str]:
    if not raw or not raw.strip():
        return []

    raw = raw.strip()

    if raw.startswith("[") and raw.endswith("]"):
        try:
            parsed = json.loads(raw)
            if isinstance(parsed, list):
                raw_items = [str(item).strip() for item in parsed if item]
            else:
                raw_items = []
        except (TypeError, ValueError):
            raw_items = []
        if raw_items:
            return raw_items

    # Split commas outside double quotes.
    parts = re.split(r',(?=(?:[^"]*"[^"]*")*[^"]*$)', raw)
    cleaned: List[str] = []
    for part in parts:
        item = part.strip()
        if len(item) >= 2 and item.startswith('"') and item.endswith('"'):
            item = item[1:-1].strip()
        if item:
            cleaned.append(item)
    return cleaned


def _validate_headers(headers: List[str]) -> None:
    for header in headers:
        if ":" not in header:
            raise ValueError(f"invalid header (expected 'Name: Value'): {header}")
        name, _ = header.split(":", 1)
        if not re.fullmatch(r"[!#$%&'*+\-.^_`|~0-9A-Za-z]+", name.strip()):
            raise ValueError(f"invalid header name: {name.strip()}")


def _resolve_output(output: str) -> Optional[str]:
    """None means return body in tool result (stdout semantics)."""
    candidate = (output or "").strip()
    if candidate:
        if candidate in ("/dev/stdout", "-"):
            return None
        return candidate

    env = os.environ.get("LLM_OUTPUT", "").strip()
    if not env or env in ("/dev/stdout", "-"):
        return None
    return env


def _effective_max_time(timeout: int, max_time: int) -> int:
    timeout_i = _positive_int(timeout, "timeout")
    max_time_i = _positive_int(max_time, "max_time")

    # Preserve the original compatibility rule:
    # a customized timeout replaces the default max-time.
    if max_time_i == 60 and timeout_i != 30:
        return timeout_i
    return max_time_i


def _validate_url(url: str) -> str:
    target = (url or "").strip()
    if not target:
        raise ValueError("url is empty")

    parsed = urlparse(target)
    if parsed.scheme.lower() not in {"http", "https"}:
        raise ValueError("url must start with http:// or https://")
    if not parsed.netloc:
        raise ValueError("url is malformed")
    if any(ord(ch) < 32 for ch in target):
        raise ValueError("url contains control characters")
    return target


def _validate_auth(auth: str, auth_type: str) -> str:
    at = (auth_type or "basic").strip().lower()
    if at not in {"basic", "bearer"}:
        raise ValueError("auth_type must be basic or bearer")
    if auth and at == "basic" and ":" not in auth:
        raise ValueError("basic auth must use user:password format")
    return at


# =========================================================================
# Response header parsing
# =========================================================================


def _parse_header_file(path: str) -> Dict[str, str]:
    """Parse the final HTTP header block, returning lowercase keys."""
    if not path or not os.path.isfile(path):
        return {}

    try:
        text = Path(path).read_text(encoding="iso-8859-1", errors="replace")
    except OSError:
        return {}

    # curl may emit multiple blocks for redirects and 1xx responses.
    # A header block begins with HTTP/... and ends at a blank line.
    blocks = re.split(r"\r?\n\r?\n", text)
    final_block = ""
    for block in blocks:
        if re.search(r"(?im)^HTTP/\S+\s+\d{3}", block):
            final_block = block

    if not final_block:
        return {}

    headers: Dict[str, str] = {}
    current_name: Optional[str] = None

    for raw_line in final_block.splitlines():
        line = raw_line.strip("\r")
        if re.match(r"^HTTP/\S+\s+\d{3}", line, re.I):
            current_name = None
            continue

        # Support folded/continued legacy header lines.
        if line[:1] in {" ", "\t"} and current_name:
            headers[current_name] = f"{headers[current_name]} {line.strip()}"
            continue

        if ":" not in line:
            continue

        key, value = line.split(":", 1)
        key = key.strip().lower()
        if not key:
            continue
        headers[key] = value.strip()
        current_name = key

    return headers


# =========================================================================
# curl command builder
# =========================================================================


def _build_curl_command(
    url: str,
    curl_bin: str,
    max_time: int,
    connect_timeout: int,
    user_agent: str,
    retry: int,
    retry_delay: int,
    follow_redirects: bool,
    max_redirects: int,
    verify_ssl: bool,
    cookie_file: str,
    compressed: bool,
    verbose: bool,
    include_headers: bool,
    limit_rate: str,
    method: str,
    data: str,
    headers: str,
    proxy: str,
    auth: str,
    auth_type: str,
    max_bytes: int = DEFAULT_MAX_BYTES,
) -> List[str]:
    cmd: List[str] = [
        curl_bin,
        "--max-time",
        str(max_time),
        "--connect-timeout",
        str(connect_timeout),
        "--user-agent",
        user_agent or DEFAULT_USER_AGENT,
        "--retry",
        str(max(0, retry)),
        "--retry-delay",
        str(max(0, retry_delay)),
        "--max-filesize",
        str(max_bytes),
    ]

    if follow_redirects:
        cmd.extend(["--location", "--max-redirs", str(max(0, max_redirects))])
    else:
        cmd.append("--no-location")

    if not verify_ssl:
        cmd.append("--insecure")

    if cookie_file:
        # -b/-c is intentionally retained for session persistence.
        cmd.extend(["--cookie", cookie_file, "--cookie-jar", cookie_file])

    if compressed:
        cmd.append("--compressed")

    if verbose:
        cmd.append("--verbose")

    if include_headers:
        cmd.append("--include")

    if limit_rate:
        cmd.extend(["--limit-rate", limit_rate])

    meth = (method or "GET").upper()
    if meth != "GET":
        cmd.extend(["--request", meth])

    if data:
        cmd.extend(["--data", data])

    parsed_headers = _parse_headers_csv(headers)
    for header in parsed_headers:
        if ":" in header:
            cmd.extend(["--header", header])

    if proxy:
        cmd.extend(["--proxy", proxy])

    if auth:
        if auth_type == "bearer":
            cmd.extend(["--header", f"Authorization: Bearer {auth}"])
        else:
            cmd.extend(["--user", auth])

    cmd.append(url)
    return cmd


def _read_body_capped(path: str, max_bytes: int) -> Tuple[bytes, bool]:
    if not os.path.isfile(path):
        return b"", False

    try:
        file_size = os.path.getsize(path)
    except OSError:
        return b"", False

    truncated = file_size > max_bytes
    read_limit = min(file_size, max_bytes)
    chunks: List[bytes] = []
    total = 0

    try:
        with open(path, "rb") as stream:
            while total < read_limit:
                block = stream.read(min(65536, read_limit - total))
                if not block:
                    break
                chunks.append(block)
                total += len(block)
    except OSError:
        return b"", truncated

    return b"".join(chunks), truncated


def _atomic_write_text(path: str, content: str) -> None:
    dest = Path(path).expanduser()
    dest.parent.mkdir(parents=True, exist_ok=True)

    fd, temp_name = tempfile.mkstemp(
        prefix=f".{dest.name}.",
        suffix=".tmp",
        dir=str(dest.parent),
        text=True,
    )
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temp_name, dest)
    except Exception:
        try:
            os.unlink(temp_name)
        except OSError:
            pass
        raise


# =========================================================================
# Core fetch
# =========================================================================


def fetch_url(
    url: str,
    timeout: int = 30,
    max_time: int = 60,
    connect_timeout: int = 10,
    user_agent: str = DEFAULT_USER_AGENT,
    output: str = "",
    dump_header: str = "",
    cookie_file: str = "",
    method: str = "GET",
    data: str = "",
    headers: str = "",
    follow_redirects: bool = False,
    max_redirects: int = 5,
    verify_ssl: bool = True,
    compressed: bool = False,
    silent: bool = False,
    verbose: bool = False,
    include: bool = False,
    retry: int = 3,
    retry_delay: int = 1,
    limit_rate: str = "",
    proxy: str = "",
    auth: str = "",
    auth_type: str = "basic",
    max_bytes: int = DEFAULT_MAX_BYTES,
    json_on_success: bool = False,
) -> str:
    try:
        target_url = _validate_url(url)

        meth = (method or "GET").upper()
        if meth not in ALLOWED_METHODS:
            return f"ERROR: unsupported method {method}"

        effective_max_time = _effective_max_time(timeout, max_time)
        connect_timeout_i = _positive_int(connect_timeout, "connect_timeout")
        if connect_timeout_i > effective_max_time:
            connect_timeout_i = effective_max_time

        retry_i = _nonnegative_int(retry, "retry")
        retry_delay_i = _nonnegative_int(retry_delay, "retry_delay")
        max_redirects_i = _nonnegative_int(max_redirects, "max_redirects")
        max_bytes_i = max(
            1024,
            min(_positive_int(max_bytes, "max_bytes"), MAX_ALLOWED_BYTES),
        )
        auth_type_i = _validate_auth(auth or "", auth_type)
        output_path = _resolve_output(output)

        header_list = _parse_headers_csv(headers)
        _validate_headers(header_list)

        curl_bin = shutil.which("curl")
        if not curl_bin:
            return "ERROR: curl not found on PATH (pkg install curl)"

        cookie_path = (cookie_file or "").strip()
        if cookie_path:
            cookie_path = str(Path(cookie_path).expanduser())
            cookie_parent = Path(cookie_path).parent
            cookie_parent.mkdir(parents=True, exist_ok=True)

        if dump_header:
            dump_parent = Path(dump_header).expanduser().parent
            dump_parent.mkdir(parents=True, exist_ok=True)

        base_cmd = _build_curl_command(
            url=target_url,
            curl_bin=curl_bin,
            max_time=effective_max_time,
            connect_timeout=connect_timeout_i,
            user_agent=(user_agent or DEFAULT_USER_AGENT).strip(),
            retry=retry_i,
            retry_delay=retry_delay_i,
            follow_redirects=bool(follow_redirects),
            max_redirects=max_redirects_i,
            verify_ssl=bool(verify_ssl),
            cookie_file=cookie_path,
            compressed=bool(compressed),
            verbose=bool(verbose),
            include_headers=bool(include),
            limit_rate=(limit_rate or "").strip(),
            method=meth,
            data=data or "",
            headers=headers or "",
            proxy=(proxy or "").strip(),
            auth=(auth or "").strip(),
            auth_type=auth_type_i,
            max_bytes=max_bytes_i,
        )

    except ValueError as exc:
        return f"ERROR: {exc}"
    except OSError as exc:
        return f"ERROR: {exc}"

    body_path: Optional[str] = None
    meta_path: Optional[str] = None

    try:
        with tempfile.NamedTemporaryFile(delete=False) as body_tmp:
            body_path = body_tmp.name

        with tempfile.NamedTemporaryFile(
            delete=False,
            mode="w",
            encoding="utf-8",
        ) as meta_tmp:
            meta_path = meta_tmp.name

        exec_cmd = list(base_cmd)

        # Original behavior intended "silent" to suppress curl output.
        # The previous implementation accidentally enabled --silent when
        # silent=False. Keep stderr useful by default, while remaining quiet
        # when the caller explicitly asks for silence.
        if silent:
            exec_cmd.append("--silent")
        else:
            exec_cmd.extend(["--show-error"])

        # Keep tool output clean: body is stored separately.
        exec_cmd.extend(
            [
                "-w",
                "%{http_code}\n%{url_effective}",
                "-o",
                body_path,
                "-D",
                meta_path,
            ]
        )

        proc = subprocess.run(
            exec_cmd,
            capture_output=True,
            text=True,
            timeout=effective_max_time + 45,
            check=False,
        )

        trailer = (proc.stdout or "").strip().splitlines()
        http_code = 0
        final_url = target_url

        if trailer:
            first = trailer[0].strip()
            if re.fullmatch(r"\d{3}", first):
                http_code = int(first)
            if len(trailer) >= 2:
                final_url = trailer[-1].strip() or target_url

        parsed_headers = _parse_header_file(meta_path or "")
        safe_headers = _redact_headers(parsed_headers)

        dest_header = (dump_header or "").strip()
        if dest_header and meta_path and os.path.isfile(meta_path):
            try:
                shutil.copy2(meta_path, str(Path(dest_header).expanduser()))
            except OSError as exc:
                # Header dumping is optional; surface it without hiding the
                # actual HTTP result.
                if proc.returncode == 0:
                    return _error_json(
                        http_code,
                        f"unable to write dump-header: {exc}",
                        extra={"url": target_url, "final_url": final_url},
                    )

        raw_body, truncated = _read_body_capped(body_path or "", max_bytes_i)

        # curl 22 is expected for HTTP >= 400 because --fail-with-body is used.
        if proc.returncode != 0:
            if proc.returncode == 63:
                return _error_json(
                    http_code,
                    f"Response exceeded max_bytes ({max_bytes_i})",
                    proc.stderr or "",
                    {
                        "url": target_url,
                        "final_url": final_url,
                        "headers": safe_headers,
                    },
                )

            if truncated:
                return _error_json(
                    http_code,
                    f"Response exceeded max_bytes ({max_bytes_i})",
                    proc.stderr or "",
                    {
                        "url": target_url,
                        "final_url": final_url,
                        "headers": safe_headers,
                    },
                )

            if http_code and not (200 <= http_code < 300):
                preview = raw_body.decode("utf-8", errors="replace")
                return _error_json(
                    http_code,
                    f"Request failed with status {http_code}",
                    proc.stderr or "",
                    {
                        "url": target_url,
                        "final_url": final_url,
                        "body_preview": _sanitize_json_text(preview, 800),
                        "headers": safe_headers,
                    },
                )

            err_msg = CURL_EXIT_CODES.get(
                proc.returncode,
                f"curl exited with code {proc.returncode}",
            )
            return _error_json(
                http_code,
                err_msg,
                proc.stderr or "",
                {
                    "url": target_url,
                    "final_url": final_url,
                    "exit_code": proc.returncode,
                },
            )

        if truncated:
            return _error_json(
                http_code,
                f"Success but body exceeded max_bytes ({max_bytes_i})",
                extra={
                    "url": target_url,
                    "final_url": final_url,
                    "bytes": len(raw_body),
                },
            )

        body_text = raw_body.decode("utf-8", errors="replace")

        # HEAD responses legitimately have no body.
        if http_code and not (200 <= http_code < 300):
            return _error_json(
                http_code,
                f"Request failed with status {http_code}",
                proc.stderr or "",
                {
                    "url": target_url,
                    "final_url": final_url,
                    "body_preview": _sanitize_json_text(body_text, 800),
                    "headers": safe_headers,
                },
            )

        if json_on_success:
            result = _success_json(
                http_code,
                body_text,
                final_url,
                len(raw_body),
                False,
                safe_headers,
            )
        else:
            result = body_text

        if output_path:
            try:
                _atomic_write_text(output_path, result)
            except OSError as exc:
                return _error_json(
                    http_code,
                    f"unable to write output: {exc}",
                    extra={"url": target_url, "final_url": final_url},
                )
            return (
                f"OK: http_code={http_code} bytes={len(raw_body)} "
                f"final_url={final_url} path={output_path}"
            )

        return result

    except subprocess.TimeoutExpired:
        return _error_json(
            0,
            "Request timed out",
            extra={"url": target_url},
        )
    except OSError as exc:
        return f"ERROR: {exc}"
    except Exception as exc:
        # Keep unexpected runtime failures compatible with the tool's
        # machine-readable error contract instead of exposing a traceback.
        return _error_json(
            0,
            f"Unexpected fetch error: {type(exc).__name__}: {exc}",
            extra={"url": target_url},
        )
    finally:
        for path in (body_path, meta_path):
            if path and os.path.isfile(path):
                try:
                    os.unlink(path)
                except OSError:
                    pass


# =========================================================================
# llm-functions entry
# =========================================================================


def run(
    url: str,
    timeout: int = 30,
    max_time: int = 60,
    connect_timeout: int = 10,
    user_agent: str = DEFAULT_USER_AGENT,
    output: str = "",
    dump_header: str = "",
    cookie_file: str = "",
    method: str = "GET",
    data: str = "",
    headers: str = "",
    follow_redirects: bool = False,
    max_redirects: int = 5,
    verify_ssl: bool = True,
    compressed: bool = False,
    silent: bool = False,
    verbose: bool = False,
    include: bool = False,
    retry: int = 3,
    retry_delay: int = 1,
    limit_rate: str = "",
    proxy: str = "",
    auth: str = "",
    auth_type: str = "basic",
    max_bytes: int = DEFAULT_MAX_BYTES,
    json_on_success: bool = False,
) -> str:
    """Fetch a URL with curl and return the body, OK summary, or JSON error.

    Args:
        url: HTTP or HTTPS URL to fetch (required).
        timeout: Seconds; maps to curl --max-time when max_time is still 60.
        max_time: curl --max-time in seconds.
        connect_timeout: curl --connect-timeout in seconds.
        user_agent: User-Agent header string.
        output: Write response to this path; empty uses LLM_OUTPUT or returns body.
        dump_header: Save response headers to this file.
        cookie_file: Cookie jar path for load/save.
        method: GET, POST, PUT, DELETE, PATCH, HEAD, or OPTIONS.
        data: Request body for POST/PUT/PATCH.
        headers: Comma-separated or JSON-formatted extra headers.
        follow_redirects: Follow redirects with --location.
        max_redirects: Maximum redirect hops.
        verify_ssl: If false, use curl --insecure.
        compressed: Request gzip/deflate (--compressed).
        silent: curl --silent.
        verbose: curl --verbose (stderr).
        include: Include response headers in the downloaded body (--include).
        retry: curl --retry count.
        retry_delay: Seconds between retries.
        limit_rate: Cap speed, e.g. 500k or 2m.
        proxy: HTTP/SOCKS proxy URL.
        auth: Basic user:password or bearer token string.
        auth_type: basic or bearer.
        max_bytes: Maximum response body size.
        json_on_success: Return JSON with status, http_code, final_url, and body.
    """
    return fetch_url(
        url=url,
        timeout=timeout,
        max_time=max_time,
        connect_timeout=connect_timeout,
        user_agent=user_agent,
        output=output,
        dump_header=dump_header,
        cookie_file=cookie_file,
        method=method,
        data=data,
        headers=headers,
        follow_redirects=follow_redirects,
        max_redirects=max_redirects,
        verify_ssl=verify_ssl,
        compressed=compressed,
        silent=silent,
        verbose=verbose,
        include=include,
        retry=retry,
        retry_delay=retry_delay,
        limit_rate=limit_rate,
        proxy=proxy,
        auth=auth,
        auth_type=auth_type,
        max_bytes=max_bytes,
        json_on_success=json_on_success,
    )


# =========================================================================
# CLI
# =========================================================================


def _add_cli_args(p: argparse.ArgumentParser) -> None:
    p.add_argument("--url", required=True)
    p.add_argument("--timeout", type=int, default=30)
    p.add_argument("--max-time", type=int, default=60)
    p.add_argument("--connect-timeout", type=int, default=10)
    p.add_argument("--user-agent", default=DEFAULT_USER_AGENT)
    p.add_argument("--output", default="")
    p.add_argument("--dump-header", default="")
    p.add_argument("--cookie-file", default="")
    p.add_argument("--method", default="GET")
    p.add_argument("--data", default="")
    p.add_argument("--headers", default="")
    p.add_argument("--follow-redirects", action="store_true")
    p.add_argument("--max-redirects", type=int, default=5)
    p.add_argument("--verify-ssl", action="store_true", default=True)
    p.add_argument("--no-verify-ssl", action="store_false", dest="verify_ssl")
    p.add_argument("--compressed", action="store_true")
    p.add_argument("--silent", action="store_true")
    p.add_argument("--verbose", action="store_true")
    p.add_argument("--include", action="store_true")
    p.add_argument("--retry", type=int, default=3)
    p.add_argument("--retry-delay", type=int, default=1)
    p.add_argument("--limit-rate", default="")
    p.add_argument("--proxy", default="")
    p.add_argument("--auth", default="")
    p.add_argument("--auth-type", default="basic")
    p.add_argument("--max-bytes", type=int, default=DEFAULT_MAX_BYTES)
    p.add_argument("--json-on-success", action="store_true")


def _exit_code(result: str) -> int:
    if result.startswith("ERROR:"):
        return 1

    try:
        payload = json.loads(result)
        if isinstance(payload, dict) and payload.get("status") == "error":
            return 1
    except (TypeError, ValueError):
        pass

    return 0


def main() -> None:
    p = argparse.ArgumentParser(description="Fetch URL with curl")
    _add_cli_args(p)
    args = p.parse_args()

    out = run(
        url=args.url,
        timeout=args.timeout,
        max_time=args.max_time,
        connect_timeout=args.connect_timeout,
        user_agent=args.user_agent,
        output=args.output,
        dump_header=args.dump_header,
        cookie_file=args.cookie_file,
        method=args.method,
        data=args.data,
        headers=args.headers,
        follow_redirects=args.follow_redirects,
        max_redirects=args.max_redirects,
        verify_ssl=args.verify_ssl,
        compressed=args.compressed,
        silent=args.silent,
        verbose=args.verbose,
        include=args.include,
        retry=args.retry,
        retry_delay=args.retry_delay,
        limit_rate=args.limit_rate,
        proxy=args.proxy,
        auth=args.auth,
        auth_type=args.auth_type,
        max_bytes=args.max_bytes,
        json_on_success=args.json_on_success,
    )

    print(out)
    sys.exit(_exit_code(out))


if __name__ == "__main__":
    main()
