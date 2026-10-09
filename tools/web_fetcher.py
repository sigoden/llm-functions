#!/usr/bin/env python3
# ==============================================================================
# web_fetcher.py — Pyrmethus AIChat Tool Template v1.2.0
# argc/aichat compatible · Human-Readable Colorized Outputs
#
# @describe Advanced web fetching utility supporting HTTP methods, batch requests, downloads, ping, trace diagnostics, headers, and JSON path extraction.
#
# @option --action! <ACTION>             Action to perform: fetch, head, download, ping, headers, trace, batch (required)
# @option --url <TEXT>                   Target URL (required for single-URL actions)
# @option --method <METHOD>              HTTP method: GET, POST, PUT, DELETE, PATCH, HEAD, OPTIONS (default: GET)
# @option --data <TEXT>                  Data payload for POST/PUT/PATCH requests
# @option --headers <TEXT>               Comma-separated custom headers (e.g., "Header1: Value1,Header2: Value2")
# @option --timeout <NUM>                Request timeout in seconds (default: 30)
# @option --auth <TEXT>                  Basic auth credentials in "username:password" format
# @option --output <PATH>                Destination file path for download action
# @option --extract <EXPR>               JSONPath dotted expression to extract data (e.g. "data.items[0].name")
# @option --batch <PATH>                 JSON file path containing list of request objects for batch mode
# @option --format <FORMAT>              Output format: text, json, csv (default: text)
# @option --retry <NUM>                  Number of retry attempts on failure (default: 0)
# @option --proxy <TEXT>                 Proxy URL (e.g., "http://127.0.0.1:8080")
# @flag   --no-verify-ssl                Disable SSL certificate verification
# @flag   --no-color                     Disable ANSI color output
# @flag   --verbose                      Enable detailed debug log output
#
# @env LLM_OUTPUT=/dev/stdout            Output path for LLM integration
# ==============================================================================

from __future__ import annotations

import argparse
import base64
import csv
import gzip
import hashlib
import io
import json
import os
import re
import socket
import ssl
import sys
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request
import zlib
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Any, Dict, Optional

__version__ = "1.2.0"

# ==============================================================================
# SECTION 1: Color Palette & Formatting Helpers
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

_ANSI_RE = re.compile(r"\033\[[0-9;]*[mGKHF]")


def _strip_ansi(text: str) -> str:
    """Remove all ANSI escape sequences from text."""
    return _ANSI_RE.sub("", text)


def _is_tty() -> bool:
    """Return True if stdout is attached to an interactive terminal."""
    return sys.stdout.isatty()


def _cprint(text: str, file: Any = None, no_color: bool = False) -> None:
    """Print pre-formatted ANSI text, stripping colors if stdout is not a TTY or --no-color is set."""
    target = file or sys.stdout
    if no_color or not _is_tty():
        text = _strip_ansi(text)
    print(text, file=target, flush=True)


def _mask_sensitive_headers(headers_dict: Dict[str, str]) -> Dict[str, str]:
    """Mask sensitive authentication tokens in human/log outputs."""
    masked = {}
    sensitive_keys = {"authorization", "cookie", "set-cookie", "x-api-key", "api-key"}
    for k, v in headers_dict.items():
        if k.lower() in sensitive_keys:
            masked[k] = "********"
        else:
            masked[k] = v
    return masked


def print_human_readable_ui(data: dict[str, Any], no_color: bool = False) -> None:
    """
    Render a human-friendly, colorized box UI for terminal users.
    Only executes if running in an interactive TTY.
    """
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
        f"{NEON_PURPLE}│{RESET} {NEON_PINK}⚡ [WEB FETCHER ENGINE v{__version__}]{RESET} {status_color}{BOLD}{status_symbol} {status_text}{RESET}"
    )
    _cprint(f"{NEON_PURPLE}├{border}┤{RESET}")
    _cprint(
        f"{NEON_PURPLE}│{RESET} {NEON_CYAN}Action:{RESET}       {NEON_YELLOW}{data.get('action', 'N/A')}{RESET}"
    )
    _cprint(
        f"{NEON_PURPLE}│{RESET} {NEON_CYAN}Target URL:{RESET}   {data.get('url', 'N/A')}"
    )
    _cprint(
        f"{NEON_PURPLE}│{RESET} {NEON_CYAN}HTTP Status:{RESET}  {NEON_GREEN}{data.get('status_code', 'N/A')}{RESET}"
    )
    _cprint(
        f"{NEON_PURPLE}│{RESET} {NEON_CYAN}Size Bytes:{RESET}   {NEON_YELLOW}{data.get('size_bytes', 0):,}{RESET}"
    )
    _cprint(
        f"{NEON_PURPLE}│{RESET} {NEON_CYAN}Duration:{RESET}     {DIM}{data.get('duration_ms', 0)}ms{RESET}"
    )

    if "attempts" in data and data["attempts"] > 1:
        _cprint(
            f"{NEON_PURPLE}│{RESET} {NEON_CYAN}Attempts:{RESET}     {NEON_PINK}{data['attempts']}{RESET}"
        )

    if not success and "error" in data:
        _cprint(f"{NEON_PURPLE}├{border}┤{RESET}")
        _cprint(f"{NEON_PURPLE}│{RESET} {NEON_RED}Error:{RESET}        {data['error']}")

    body_preview = data.get("preview")
    if body_preview:
        _cprint(f"{NEON_PURPLE}├{border}┤{RESET}")
        _cprint(f"{NEON_PURPLE}│{RESET} {BOLD}Content Preview:{RESET}")
        for line in str(body_preview).splitlines()[:4]:
            _cprint(
                f"{NEON_PURPLE}│{RESET}   {DIM}{line[:58]}...{RESET}"
                if len(line) > 58
                else f"{NEON_PURPLE}│{RESET}   {line}"
            )

    _cprint(f"{NEON_PURPLE}╰{border}╯{RESET}")


# ==============================================================================
# SECTION 2: Core Logic Implementation
# ==============================================================================


def extract_json_path(data: Any, path: str) -> Any:
    """Extract a value from nested structures using dotted paths and array indexing."""
    if not path:
        return data
    current = data
    tokens = re.findall(r'"[^"]*"|\[\d+\]|[^.\[\]]+', path)
    for token in tokens:
        if current is None:
            return None
        if token.startswith("[") and token.endswith("]"):
            idx = int(token[1:-1])
            if isinstance(current, list) and 0 <= idx < len(current):
                current = current[idx]
            else:
                return None
        else:
            key = token.strip('"')
            if isinstance(current, dict):
                current = current.get(key)
            else:
                return None
    return current


def _format_output(data: dict[str, Any], fmt: str) -> str:
    """Format dictionary result according to user preference (text, json, csv)."""
    fmt_lower = fmt.lower().strip()
    if fmt_lower == "json":
        return json.dumps(data, indent=2, ensure_ascii=False)
    elif fmt_lower == "csv":
        out = io.StringIO()
        writer = csv.writer(out)
        writer.writerow(["Key", "Value"])
        for k, v in data.items():
            if isinstance(v, (dict, list)):
                writer.writerow([k, json.dumps(v, ensure_ascii=False)])
            else:
                writer.writerow([k, str(v)])
        return out.getvalue()
    else:
        content = (
            data.get("content") or data.get("preview") or data.get("error") or str(data)
        )
        return str(content)


def execute_tool(
    action: str,
    url: Optional[str] = None,
    method: str = "GET",
    data: Optional[str] = None,
    headers: Optional[str] = None,
    timeout: int = 30,
    auth: Optional[str] = None,
    output: Optional[str] = None,
    extract: Optional[str] = None,
    batch: Optional[str] = None,
    format: str = "text",
    retry: int = 0,
    proxy: Optional[str] = None,
    no_verify_ssl: bool = False,
    no_color: bool = False,
    verbose: bool = False,
) -> dict[str, Any]:
    """
    Core execution logic for web fetching and network diagnostics.
    Wizard Upgraded with retry logic, chunked downloads, TLS timing, and batch support.
    """
    start_time = time.perf_counter()
    action_clean = action.lower().strip()

    if action_clean != "batch" and not url:
        return {
            "success": False,
            "error": "URL parameter is required for single-URL actions",
            "exit_code": 1,
        }

    # Normalize target URL
    target_url = url.strip() if url else ""
    if target_url and not target_url.startswith(("http://", "https://")):
        target_url = "https://" + target_url

    # Parse HTTP Headers
    req_headers: Dict[str, str] = {"User-Agent": f"Pyrmethus-WebFetcher/{__version__}"}
    if headers:
        for pair in headers.split(","):
            if ":" in pair:
                k, v = pair.split(":", 1)
                req_headers[k.strip()] = v.strip()

    if auth:
        encoded_auth = base64.b64encode(auth.encode("utf-8")).decode("ascii")
        req_headers["Authorization"] = f"Basic {encoded_auth}"

    # Setup SSL Context
    ssl_ctx = (
        ssl._create_unverified_context()
        if no_verify_ssl
        else ssl.create_default_context()
    )

    # Setup Opener with Proxy support
    handlers: list[urllib.request.BaseHandler] = []
    if no_verify_ssl:
        handlers.append(urllib.request.HTTPSHandler(context=ssl_ctx))
    if proxy:
        handlers.append(urllib.request.ProxyHandler({"http": proxy, "https": proxy}))

    opener = (
        urllib.request.build_opener(*handlers)
        if handlers
        else urllib.request.build_opener()
    )

    # --------------------------------------------------------------------------
    # ACTION: BATCH MODE PROCESSING
    # --------------------------------------------------------------------------
    if action_clean == "batch":
        if not batch or not Path(batch).exists():
            return {
                "success": False,
                "error": f"Batch file missing or invalid: '{batch}'",
                "exit_code": 1,
            }
        try:
            batch_data = json.loads(Path(batch).read_text(encoding="utf-8"))
            if not isinstance(batch_data, list):
                return {
                    "success": False,
                    "error": "Batch JSON root must be an array of request objects",
                    "exit_code": 1,
                }

            batch_results = []
            max_workers = min(10, max(1, len(batch_data)))

            def _process_item(item: dict) -> dict:
                return execute_tool(
                    action=item.get("action", "fetch"),
                    url=item.get("url"),
                    method=item.get("method", "GET"),
                    data=item.get("data"),
                    headers=item.get("headers"),
                    timeout=item.get("timeout", timeout),
                    auth=item.get("auth"),
                    output=item.get("output"),
                    extract=item.get("extract"),
                    retry=item.get("retry", 0),
                    proxy=item.get("proxy", proxy),
                    no_verify_ssl=item.get("no_verify_ssl", no_verify_ssl),
                    no_color=no_color,
                    verbose=verbose,
                )

            with ThreadPoolExecutor(max_workers=max_workers) as executor:
                futures = [executor.submit(_process_item, item) for item in batch_data]
                for future in as_completed(futures):
                    batch_results.append(future.result())

            duration_ms = round((time.perf_counter() - start_time) * 1000, 2)
            successful_count = sum(1 for r in batch_results if r.get("success"))

            res_dict = {
                "success": True,
                "action": "batch",
                "total_requests": len(batch_data),
                "successful_requests": successful_count,
                "failed_requests": len(batch_data) - successful_count,
                "results": batch_results,
                "duration_ms": duration_ms,
                "exit_code": 0 if successful_count == len(batch_data) else 1,
            }
            res_dict["formatted_output"] = _format_output(res_dict, format)
            return res_dict
        except Exception as exc:
            return {
                "success": False,
                "error": f"Batch execution failed: {exc}",
                "exit_code": 1,
            }

    # --------------------------------------------------------------------------
    # RETRY EXECUTION WRAPPER FOR SINGLE ACTIONS
    # --------------------------------------------------------------------------
    attempts = 0
    max_attempts = max(1, retry + 1)
    last_error: Optional[Exception] = None

    while attempts < max_attempts:
        attempts += 1
        try:
            # ------------------------------------------------------------------
            # ACTION 1: FETCH / HEAD / HEADERS
            # ------------------------------------------------------------------
            if action_clean in ("fetch", "head", "headers"):
                req_method = (
                    "HEAD" if action_clean in ("head", "headers") else method.upper()
                )
                payload = (
                    data.encode("utf-8")
                    if data and req_method in ("POST", "PUT", "PATCH")
                    else None
                )

                req = urllib.request.Request(
                    target_url, data=payload, headers=req_headers, method=req_method
                )

                with opener.open(req, timeout=timeout) as resp:
                    status_code = resp.getcode()
                    raw_body = resp.read()

                    # Decompress if gzip/deflate
                    encoding = resp.headers.get("Content-Encoding", "").lower()
                    if encoding == "gzip":
                        raw_body = gzip.decompress(raw_body)
                    elif encoding == "deflate":
                        raw_body = zlib.decompress(raw_body)

                    body_text = raw_body.decode("utf-8", errors="replace")
                    headers_dict = dict(resp.getheaders())
                    safe_headers = _mask_sensitive_headers(headers_dict)

                    if extract and body_text:
                        try:
                            parsed_json = json.loads(body_text)
                            extracted_val = extract_json_path(parsed_json, extract)
                            body_text = json.dumps(
                                extracted_val, indent=2, ensure_ascii=False
                            )
                        except json.JSONDecodeError:
                            pass

                    duration_ms = round((time.perf_counter() - start_time) * 1000, 2)

                    res_dict = {
                        "success": True,
                        "action": action_clean,
                        "url": target_url,
                        "status_code": status_code,
                        "size_bytes": len(raw_body),
                        "headers": safe_headers,
                        "content": body_text if action_clean != "headers" else None,
                        "preview": json.dumps(safe_headers, indent=2)
                        if action_clean == "headers"
                        else body_text[:200],
                        "attempts": attempts,
                        "duration_ms": duration_ms,
                        "exit_code": 0,
                    }
                    res_dict["formatted_output"] = _format_output(res_dict, format)
                    return res_dict

            # ------------------------------------------------------------------
            # ACTION 2: DOWNLOAD (STREAMED CHUNKS)
            # ------------------------------------------------------------------
            elif action_clean == "download":
                out_file = (
                    Path(output).expanduser().resolve()
                    if output
                    else Path.cwd() / "downloaded_file"
                )
                out_file.parent.mkdir(parents=True, exist_ok=True)

                req = urllib.request.Request(
                    target_url, headers=req_headers, method="GET"
                )
                with opener.open(req, timeout=timeout) as resp:
                    status_code = resp.getcode()
                    sha256_hash = hashlib.sha256()
                    total_bytes = 0

                    fd, tmp_path = tempfile.mkstemp(dir=out_file.parent)
                    try:
                        with os.fdopen(fd, "wb") as fh:
                            while True:
                                chunk = resp.read(65536)
                                if not chunk:
                                    break
                                fh.write(chunk)
                                sha256_hash.update(chunk)
                                total_bytes += len(chunk)
                        os.replace(tmp_path, out_file)
                    except Exception:
                        if os.path.exists(tmp_path):
                            os.unlink(tmp_path)
                        raise

                    duration_ms = round((time.perf_counter() - start_time) * 1000, 2)

                    res_dict = {
                        "success": True,
                        "action": "download",
                        "url": target_url,
                        "status_code": status_code,
                        "saved_file": str(out_file),
                        "size_bytes": total_bytes,
                        "sha256": sha256_hash.hexdigest(),
                        "attempts": attempts,
                        "duration_ms": duration_ms,
                        "exit_code": 0,
                    }
                    res_dict["formatted_output"] = _format_output(res_dict, format)
                    return res_dict

            # ------------------------------------------------------------------
            # ACTION 3: PING / TRACE (FULL TCP & TLS HANDSHAKE TIMING)
            # ------------------------------------------------------------------
            elif action_clean in ("ping", "trace"):
                parsed = urllib.parse.urlparse(target_url)
                host = parsed.hostname or ""
                port = parsed.port or (443 if parsed.scheme == "https" else 80)

                t0 = time.perf_counter()
                addrs = socket.getaddrinfo(host, port, proto=socket.IPPROTO_TCP)
                dns_ms = round((time.perf_counter() - t0) * 1000, 2)
                ips = list({a[4][0] for a in addrs})

                t1 = time.perf_counter()
                raw_sock = socket.create_connection((host, port), timeout=timeout)
                tcp_ms = round((time.perf_counter() - t1) * 1000, 2)

                tls_ms = 0.0
                if parsed.scheme == "https":
                    t2 = time.perf_counter()
                    trace_ssl_ctx = (
                        ssl._create_unverified_context()
                        if no_verify_ssl
                        else ssl.create_default_context()
                    )
                    with trace_ssl_ctx.wrap_socket(raw_sock, server_hostname=host):
                        tls_ms = round((time.perf_counter() - t2) * 1000, 2)
                else:
                    raw_sock.close()

                duration_ms = round((time.perf_counter() - start_time) * 1000, 2)

                res_dict = {
                    "success": True,
                    "action": action_clean,
                    "url": target_url,
                    "host": host,
                    "resolved_ips": ips,
                    "dns_latency_ms": dns_ms,
                    "tcp_connect_latency_ms": tcp_ms,
                    "tls_handshake_latency_ms": tls_ms
                    if parsed.scheme == "https"
                    else None,
                    "attempts": attempts,
                    "duration_ms": duration_ms,
                    "exit_code": 0,
                }
                res_dict["formatted_output"] = _format_output(res_dict, format)
                return res_dict

            else:
                return {
                    "success": False,
                    "error": f"Unsupported or unknown action: '{action}'",
                    "exit_code": 1,
                }

        except Exception as exc:
            last_error = exc
            if attempts < max_attempts:
                time.sleep(0.5 * (2 ** (attempts - 1)))  # Exponential backoff

    duration_ms = round((time.perf_counter() - start_time) * 1000, 2)
    err_dict = {
        "success": False,
        "error": f"Web fetcher execution failed after {attempts} attempt(s): {last_error}",
        "attempts": attempts,
        "duration_ms": duration_ms,
        "exit_code": 1,
    }
    err_dict["formatted_output"] = _format_output(err_dict, format)
    return err_dict


# ==============================================================================
# SECTION 3: Output Routing (LLM vs Human Terminal)
# ==============================================================================


def write_llm_output(data: dict[str, Any]) -> None:
    """Format and write clean JSON output to LLM_OUTPUT destination."""
    out_path = os.environ.get("LLM_OUTPUT", "/dev/stdout")
    json_payload = json.dumps(data, indent=2, ensure_ascii=False) + "\n"

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


# ==============================================================================
# SECTION 4: Function Entry Point for AIChat
# ==============================================================================


def run(
    action: str,
    url: Optional[str] = None,
    method: str = "GET",
    data: Optional[str] = None,
    headers: Optional[str] = None,
    timeout: int = 30,
    auth: Optional[str] = None,
    output: Optional[str] = None,
    extract: Optional[str] = None,
    batch: Optional[str] = None,
    format: str = "text",
    retry: int = 0,
    proxy: Optional[str] = None,
    no_verify_ssl: bool = False,
    no_color: bool = False,
    verbose: bool = False,
) -> dict[str, Any]:
    """
    AIChat Programmatic Entrypoint.
    Parameter names match option/flag slugs (with underscores).
    Returns result object cleanly for imported modules.
    """
    result = execute_tool(
        action=action,
        url=url,
        method=method,
        data=data,
        headers=headers,
        timeout=timeout,
        auth=auth,
        output=output,
        extract=extract,
        batch=batch,
        format=format,
        retry=retry,
        proxy=proxy,
        no_verify_ssl=no_verify_ssl,
        no_color=no_color,
        verbose=verbose,
    )

    print_human_readable_ui(result, no_color=no_color)
    write_llm_output(result)
    return result


# ==============================================================================
# SECTION 5: CLI Argument Parser
# ==============================================================================


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="web_fetcher.py",
        description=f"AIChat Advanced Web Fetcher & Diagnostics Tool v{__version__}",
    )
    parser.add_argument(
        "--action",
        "-a",
        required=True,
        choices=["fetch", "head", "download", "ping", "headers", "trace", "batch"],
        help="Action to perform (required)",
    )
    parser.add_argument(
        "--url",
        "-u",
        type=str,
        default=None,
        help="Target URL (required for single-URL actions)",
    )
    parser.add_argument(
        "--method",
        "-m",
        default="GET",
        help="HTTP method: GET, POST, PUT, DELETE, PATCH, HEAD, OPTIONS (default: GET)",
    )
    parser.add_argument(
        "--data",
        "-d",
        type=str,
        default=None,
        help="Data payload for POST/PUT/PATCH requests",
    )
    parser.add_argument(
        "--headers",
        type=str,
        default=None,
        help="Comma-separated custom headers (e.g., 'Header1: Value1,Header2: Value2')",
    )
    parser.add_argument(
        "--timeout",
        type=int,
        default=30,
        help="Request timeout in seconds (default: 30)",
    )
    parser.add_argument(
        "--auth",
        type=str,
        default=None,
        help="Basic auth credentials in 'username:password' format",
    )
    parser.add_argument(
        "--output",
        "-o",
        type=str,
        default=None,
        help="Destination file path for download action",
    )
    parser.add_argument(
        "--extract",
        type=str,
        default=None,
        help="JSONPath dotted expression to extract data (e.g. 'data.items[0].name')",
    )
    parser.add_argument(
        "--batch",
        type=str,
        default=None,
        help="JSON file path containing list of request objects for batch mode",
    )
    parser.add_argument(
        "--format",
        choices=["text", "json", "csv"],
        default="text",
        help="Output format: text, json, csv (default: text)",
    )
    parser.add_argument(
        "--retry",
        type=int,
        default=0,
        help="Number of retry attempts on failure (default: 0)",
    )
    parser.add_argument(
        "--proxy",
        type=str,
        default=None,
        help="Proxy URL (e.g., 'http://127.0.0.1:8080')",
    )
    parser.add_argument(
        "--no-verify-ssl",
        action="store_true",
        default=False,
        dest="no_verify_ssl",
        help="Disable SSL certificate verification",
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
    res = run(
        action=args.action,
        url=args.url,
        method=args.method,
        data=args.data,
        headers=args.headers,
        timeout=args.timeout,
        auth=args.auth,
        output=args.output,
        extract=args.extract,
        batch=args.batch,
        format=args.format,
        retry=args.retry,
        proxy=args.proxy,
        no_verify_ssl=args.no_verify_ssl,
        no_color=args.no_color,
        verbose=args.verbose,
    )
    sys.exit(res.get("exit_code", 0))
