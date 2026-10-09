#!/usr/bin/env python3
# ==============================================================================
# dns_tool.py — Pyrmethus AIChat Tool Master Template v2.4.0-ASCENDED
# argc/aichat compatible · Human-Readable Colorized Outputs · Native Caching
#
# @describe Comprehensive DNS query, reverse lookup, DoH inspector, and propagation checker tailored for Termux and Android environments.
#
# @meta require-tools aichat
#
# @option --domain -d! <DOMAIN>           Target domain name or IP address (required)
# @option --type -t <TYPE>               Record type: A, AAAA, MX, TXT, NS, CNAME, SOA, PTR, CAA, ALL (default: A)
# @option --provider -p <PROVIDER>       DNS provider: cloudflare, google, quad9, local, all (default: cloudflare)
# @option --action -a <ACTION>           Execution action: query, reverse, doh, sys, propagate (default: query)
# @flag   --use-cache                    Enable result caching for duplicate DNS lookups
# @flag   --no-color                     Disable ANSI color output
# @flag   --verbose                      Enable detailed debug log output
#
# @env LLM_OUTPUT=/dev/stdout            Output path for LLM integration
# ==============================================================================

from __future__ import annotations

import argparse
import hashlib
import json
import logging
import os
import pickle
import re
import signal
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timedelta
from enum import Enum
from pathlib import Path
from typing import Any, Dict, List, Literal, Optional, Tuple, Union

__version__ = "2.4.0"
__all__ = [
    "run",
    "execute_tool",
    "ToolCache",
    "ToolError",
    "get_agent_var",
    "get_builtin_var",
    "get_execution_context",
    "__version__",
]

# DoH Resolver Providers
DOH_PROVIDERS: dict[str, str] = {
    "cloudflare": "https://cloudflare-dns.com/dns-query",
    "google": "https://dns.google/resolve",
    "quad9": "https://dns.quad9.net:5053/dns-query",
}

# Standard DNS RCODE mappings
DNS_RCODES: dict[int, str] = {
    0: "NOERROR",
    1: "FORMERR",
    2: "SERVFAIL",
    3: "NXDOMAIN",
    4: "NOTIMP",
    5: "REFUSED",
}

# DNS Type Name to Numeric Code Mapping
DNS_TYPE_CODES: dict[str, int] = {
    "A": 1,
    "NS": 2,
    "CNAME": 5,
    "SOA": 6,
    "PTR": 12,
    "MX": 15,
    "TXT": 16,
    "AAAA": 28,
    "SRV": 33,
    "CAA": 257,
}

# Inverse Type Code Mapping
DNS_CODE_TO_TYPE: dict[int, str] = {v: k for k, v in DNS_TYPE_CODES.items()}

# ==============================================================================
# SECTION 1: Exit Codes & Exception Models
# ==============================================================================

EXIT_SUCCESS = 0
EXIT_ERROR = 1
EXIT_FILE_NOT_FOUND = 2
EXIT_TIMEOUT = 124
EXIT_PERMISSION_DENIED = 126
EXIT_INVALID_INPUT = 127
EXIT_INTERRUPTED = 130


class ToolError(Exception):
    """Structured exception model for tool operations."""

    def __init__(
        self,
        message: str,
        exit_code: int = EXIT_ERROR,
        details: Optional[dict[str, Any]] = None,
    ) -> None:
        super().__init__(message)
        self.message = message
        self.exit_code = exit_code
        self.details = details or {}

    def to_dict(self) -> dict[str, Any]:
        return {
            "success": False,
            "error": self.message,
            "exit_code": self.exit_code,
            **self.details,
        }


class ToolJSONEncoder(json.JSONEncoder):
    """Custom JSON encoder handling Path, Enum, datetime, timedelta, bytes, and sets safely."""

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


# ==============================================================================
# SECTION 2: Terminal Color Palette & UI Helpers
# ==============================================================================

NEON_CYAN    = "\033[38;5;51m"
NEON_GREEN   = "\033[38;5;46m"
NEON_RED     = "\033[38;5;196m"
NEON_YELLOW  = "\033[38;5;226m"
NEON_PURPLE  = "\033[38;5;129m"
NEON_PINK    = "\033[38;5;198m"
RESET        = "\033[0m"
BOLD         = "\033[1m"
DIM          = "\033[2m"

_ANSI_RE = re.compile(
    r"\x1b(?:[@-Z\\-_]|\[[0-?]*[ -/]*[@-~])|\033\[[0-9;?]*[a-zA-Z]"
)


def _strip_ansi(text: str) -> str:
    """Remove ANSI escape sequences from text."""
    return _ANSI_RE.sub("", text)


def _is_tty() -> bool:
    """Return True if stderr is attached to an interactive terminal and NO_COLOR is not set."""
    if "NO_COLOR" in os.environ:
        return False
    return sys.stderr.isatty() and os.environ.get("TERM", "").lower() not in ("dumb", "")


def _cprint(text: str, file: Any = None, no_color: bool = False, end: str = "\n") -> None:
    """Print ANSI text, stripping colors if stream is not a TTY or --no-color is set."""
    target = file or sys.stderr
    if no_color or not _is_tty():
        text = _strip_ansi(text)
    print(text, file=target, flush=True, end=end)


def _truncate_str(text: str, max_len: int = 54) -> str:
    """Truncate text for UI box alignment."""
    return text if len(text) <= max_len else text[: max_len - 3] + "..."


def print_human_readable_ui(data: dict[str, Any], no_color: bool = False) -> None:
    """Render a human-friendly box UI to stderr."""
    if not _is_tty() or no_color:
        return

    success = data.get("success", False)
    status_color = NEON_GREEN if success else NEON_RED
    status_symbol = "✓" if success else "✗"
    status_text = "RESOLVED" if success else "FAILED"

    box_w = 66
    border = "─" * box_w

    _cprint(f"{NEON_PURPLE}╭{border}╮{RESET}")
    _cprint(f"{NEON_PURPLE}│{RESET} {NEON_PINK}⚡ [TERMUX DNS TOOL v{__version__}]{RESET} {status_color}{BOLD}{status_symbol} {status_text}{RESET}")
    _cprint(f"{NEON_PURPLE}├{border}┤{RESET}")
    _cprint(f"{NEON_PURPLE}│{RESET} {NEON_CYAN}Target:{RESET}      {_truncate_str(str(data.get('domain', 'N/A')), 50)}")
    _cprint(f"{NEON_PURPLE}│{RESET} {NEON_CYAN}Action:{RESET}      {data.get('action', 'query')}")
    _cprint(f"{NEON_PURPLE}│{RESET} {NEON_CYAN}Provider:{RESET}    {data.get('provider', 'N/A')}")

    if not success and "error" in data:
        _cprint(f"{NEON_PURPLE}├{border}┤{RESET}")
        _cprint(f"{NEON_PURPLE}│{RESET} {NEON_RED}Error:{RESET}       {_truncate_str(str(data['error']), 52)}")
    else:
        records = data.get("records", [])
        _cprint(f"{NEON_PURPLE}├{border}┤{RESET}")
        _cprint(f"{NEON_PURPLE}│{RESET} {BOLD}DNS Records ({len(records)} found):{RESET}")
        for rec in records[:10]:
            rtype = rec.get("type_name", rec.get("type", "UNKNOWN"))
            rdata = rec.get("data", "N/A")
            rttl = rec.get("TTL", 0)
            _cprint(f"{NEON_PURPLE}│{RESET}   {NEON_CYAN}›{RESET} {NEON_YELLOW}{rtype:<6}{RESET} {NEON_GREEN}{_truncate_str(str(rdata), 40):<40}{RESET} {DIM}(TTL {rttl}s){RESET}")

        if len(records) > 10:
            _cprint(f"{NEON_PURPLE}│{RESET}   {DIM}... and {len(records) - 10} more record(s){RESET}")

    _cprint(f"{NEON_PURPLE}├{border}┤{RESET}")
    _cprint(f"{NEON_PURPLE}│{RESET} {NEON_CYAN}Duration:{RESET} {DIM}{data.get('duration_ms', 0)}ms{RESET}  |  {NEON_CYAN}Cached:{RESET} {data.get('cached', False)}")
    _cprint(f"{NEON_PURPLE}╰{border}╯{RESET}")


# ==============================================================================
# SECTION 3: Agent & Environment Helpers
# ==============================================================================

def get_agent_var(name: str, default: str = "") -> str:
    """Access agent user-defined variables."""
    return os.environ.get(f"LLM_AGENT_VAR_{name.upper()}", default)


def get_builtin_var(name: str) -> Optional[str]:
    """Access agent built-in environment variables."""
    return os.environ.get(f"LLM_AGENT_VAR_{name}")


def get_execution_context() -> dict[str, Any]:
    """Extract complete execution context."""
    termux_prefix = os.environ.get("PREFIX", "")
    return {
        "tool_name": os.environ.get("LLM_TOOL_NAME", "dns_tool.py"),
        "cache_dir": os.environ.get("LLM_TOOL_CACHE_DIR"),
        "root_dir": os.environ.get("LLM_ROOT_DIR"),
        "output_path": os.environ.get("LLM_OUTPUT"),
        "cwd": get_builtin_var("__cwd__") or os.getcwd(),
        "termux_prefix": termux_prefix,
        "is_termux": "com.termux" in termux_prefix or Path("/data/data/com.termux").exists(),
    }


def get_termux_system_resolvers() -> List[str]:
    """Detect Termux and Android system DNS resolvers using getprop and resolv.conf."""
    resolvers: List[str] = []

    # Try Android getprop properties
    for prop in ("net.dns1", "net.dns2", "net.dns3", "net.dns4"):
        try:
            res = subprocess.run(
                ["getprop", prop],
                capture_output=True,
                text=True,
                timeout=2,
                stderr=subprocess.DEVNULL,
            )
            val = res.stdout.strip()
            if val and val not in resolvers:
                resolvers.append(val)
        except Exception:
            pass

    # Fallback to /etc/resolv.conf
    resolv_conf = Path("/etc/resolv.conf")
    if resolv_conf.exists():
        try:
            for line in resolv_conf.read_text().splitlines():
                line = line.strip()
                if line.startswith("nameserver"):
                    parts = line.split()
                    if len(parts) >= 2 and parts[1] not in resolvers:
                        resolvers.append(parts[1])
        except Exception:
            pass

    return resolvers


# ==============================================================================
# SECTION 4: Native Caching & Signal Handlers
# ==============================================================================

class ToolCache:
    """Caching utility with TTL support."""

    def __init__(self, cache_dir: Optional[Path] = None) -> None:
        self.cache_dir = cache_dir or Path(os.environ.get("LLM_TOOL_CACHE_DIR", Path.home() / ".cache" / "aichat_tools"))
        try:
            self.cache_dir.mkdir(parents=True, exist_ok=True)
        except OSError:
            pass

    def _make_key(self, key_data: str) -> str:
        versioned_data = f"{__version__}:{key_data}"
        return hashlib.sha256(versioned_data.encode("utf-8")).hexdigest()

    def get(self, key_data: str, ttl_seconds: int = 300) -> Optional[Any]:
        cache_file = self.cache_dir / f"{self._make_key(key_data)}.cache"
        if not cache_file.exists():
            return None
        try:
            if time.time() - cache_file.stat().st_mtime > ttl_seconds:
                cache_file.unlink(missing_ok=True)
                return None
            with open(cache_file, "rb") as fp:
                return pickle.load(fp)
        except Exception:
            return None

    def set(self, key_data: str, value: Any) -> None:
        cache_file = self.cache_dir / f"{self._make_key(key_data)}.cache"
        tmp_file = cache_file.with_suffix(".tmp")
        try:
            with open(tmp_file, "wb") as fp:
                pickle.dump(value, fp)
            tmp_file.replace(cache_file)
        except Exception:
            if tmp_file.exists():
                tmp_file.unlink(missing_ok=True)


class GracefulShutdown:
    """Signal handler for graceful cancellation."""

    def __init__(self) -> None:
        self.interrupted = False
        self._old_sigint = signal.signal(signal.SIGINT, self._handle_signal)
        self._old_sigterm = signal.signal(signal.SIGTERM, self._handle_signal)

    def _handle_signal(self, signum: int, frame: Any) -> None:
        self.interrupted = True

    def restore(self) -> None:
        signal.signal(signal.SIGINT, self._old_sigint)
        signal.signal(signal.SIGTERM, self._old_sigterm)

    def should_stop(self) -> bool:
        return self.interrupted


# ==============================================================================
# SECTION 5: Core DNS Engine Implementation (DoH & Socket Resolution)
# ==============================================================================

def query_doh(
    domain: str,
    record_type: str = "A",
    provider: str = "cloudflare",
    timeout: int = 8,
) -> dict[str, Any]:
    """Execute DNS query via DNS over HTTPS (DoH) API."""
    endpoint = DOH_PROVIDERS.get(provider.lower(), DOH_PROVIDERS["cloudflare"])
    params = urllib.parse.urlencode({"name": domain.strip(), "type": record_type.upper()})
    url = f"{endpoint}?{params}"

    req = urllib.request.Request(
        url,
        headers={
            "Accept": "application/dns-json",
            "User-Agent": f"Termux-DNSTool/{__version__}",
        },
    )

    t0 = time.monotonic()
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            data_bytes = resp.read()
            raw_json = json.loads(data_bytes.decode("utf-8"))
            latency_ms = round((time.monotonic() - t0) * 1000, 2)

            rcode = raw_json.get("Status", 0)
            rcode_name = DNS_RCODES.get(rcode, f"RCODE_{rcode}")
            answers = raw_json.get("Answer", [])

            # Enrich record type names
            for ans in answers:
                num_type = ans.get("type", 1)
                ans["type_name"] = DNS_CODE_TO_TYPE.get(num_type, f"TYPE_{num_type}")

            return {
                "success": rcode == 0,
                "provider": provider.lower(),
                "rcode": rcode,
                "rcode_name": rcode_name,
                "latency_ms": latency_ms,
                "records": answers,
            }
    except urllib.error.HTTPError as exc:
        raise ToolError(f"DoH HTTP Error {exc.code}: {exc.reason}", exit_code=EXIT_ERROR)
    except urllib.error.URLError as exc:
        raise ToolError(f"DoH Network Connection Error: {exc.reason}", exit_code=EXIT_ERROR)
    except Exception as exc:
        raise ToolError(f"DoH Query Error: {exc}", exit_code=EXIT_ERROR)


def query_local_socket(domain: str) -> dict[str, Any]:
    """Fallback local socket DNS resolution using standard library socket."""
    t0 = time.monotonic()
    records = []
    try:
        addr_info = socket.getaddrinfo(domain, None)
        seen_ips = set()
        for family, socktype, proto, canonname, sockaddr in addr_info:
            ip = sockaddr[0]
            if ip not in seen_ips:
                seen_ips.add(ip)
                rtype = "AAAA" if ":" in ip else "A"
                records.append({
                    "name": domain,
                    "type": DNS_TYPE_CODES.get(rtype, 1),
                    "type_name": rtype,
                    "TTL": 0,
                    "data": ip,
                })
        latency_ms = round((time.monotonic() - t0) * 1000, 2)
        return {
            "success": len(records) > 0,
            "provider": "local_socket",
            "rcode": 0,
            "rcode_name": "NOERROR",
            "latency_ms": latency_ms,
            "records": records,
        }
    except socket.gaierror as exc:
        return {
            "success": False,
            "provider": "local_socket",
            "rcode": 3,
            "rcode_name": "NXDOMAIN",
            "error": str(exc),
            "latency_ms": round((time.monotonic() - t0) * 1000, 2),
            "records": [],
        }


def query_reverse_dns(ip_address: str) -> dict[str, Any]:
    """Perform PTR reverse DNS lookup for an IPv4/IPv6 address."""
    t0 = time.monotonic()
    try:
        hostname, aliaslist, ipaddrlist = socket.gethostbyaddr(ip_address.strip())
        latency_ms = round((time.monotonic() - t0) * 1000, 2)
        return {
            "success": True,
            "ip": ip_address,
            "hostname": hostname,
            "aliases": aliaslist,
            "latency_ms": latency_ms,
            "records": [{
                "name": ip_address,
                "type": 12,
                "type_name": "PTR",
                "TTL": 0,
                "data": hostname,
            }],
        }
    except socket.herror as exc:
        return {
            "success": False,
            "ip": ip_address,
            "error": f"Host not found: {exc}",
            "latency_ms": round((time.monotonic() - t0) * 1000, 2),
            "records": [],
        }


# ==============================================================================
# SECTION 6: Execution Engine Router
# ==============================================================================

def execute_tool(
    domain: str,
    record_type: str = "A",
    provider: str = "cloudflare",
    action: str = "query",
    use_cache: bool = False,
    no_color: bool = False,
    verbose: bool = False,
) -> dict[str, Any]:
    """Execute DNS inspection based on action type."""
    start_time = time.monotonic()
    domain_clean = domain.strip().lower() if domain else ""
    rtype_clean = record_type.strip().upper()
    action_clean = action.strip().lower()

    if verbose:
        logging.basicConfig(level=logging.DEBUG, format="[DEBUG] %(message)s")
        logging.debug(f"Executing DNS tool: domain={domain_clean}, action={action_clean}")

    if not domain_clean and action_clean != "sys":
        return {
            "success": False,
            "error": "Domain or IP address target parameter is required.",
            "exit_code": EXIT_INVALID_INPUT,
            "duration_ms": 0.0,
        }

    cache = ToolCache()
    cache_key = f"{domain_clean}:{rtype_clean}:{provider}:{action_clean}"
    if use_cache:
        cached_res = cache.get(cache_key)
        if cached_res:
            cached_res["cached"] = True
            return cached_res

    shutdown = GracefulShutdown()

    try:
        if action_clean == "sys":
            resolvers = get_termux_system_resolvers()
            duration_ms = round((time.monotonic() - start_time) * 1000, 2)
            return {
                "success": True,
                "action": "sys",
                "domain": "system",
                "resolvers": resolvers,
                "count": len(resolvers),
                "duration_ms": duration_ms,
                "context": get_execution_context(),
                "exit_code": EXIT_SUCCESS,
            }

        elif action_clean == "reverse":
            res = query_reverse_dns(domain_clean)
            duration_ms = round((time.monotonic() - start_time) * 1000, 2)
            res.update({
                "action": "reverse",
                "domain": domain_clean,
                "duration_ms": duration_ms,
                "context": get_execution_context(),
                "exit_code": EXIT_SUCCESS if res["success"] else EXIT_ERROR,
            })
            return res

        elif action_clean in ("propagate", "check"):
            # Check DNS propagation across multiple global providers
            providers_res = {}
            for prov in DOH_PROVIDERS.keys():
                if shutdown.should_stop():
                    raise ToolError("Interrupted by user signal.", exit_code=EXIT_INTERRUPTED)
                try:
                    providers_res[prov] = query_doh(domain_clean, rtype_clean, provider=prov)
                except Exception as exc:
                    providers_res[prov] = {"success": False, "error": str(exc)}

            duration_ms = round((time.monotonic() - start_time) * 1000, 2)
            return {
                "success": any(p.get("success") for p in providers_res.values()),
                "action": "propagate",
                "domain": domain_clean,
                "record_type": rtype_clean,
                "providers": providers_res,
                "duration_ms": duration_ms,
                "context": get_execution_context(),
                "exit_code": EXIT_SUCCESS,
            }

        else:
            # Default query / doh action
            if provider.lower() == "local":
                res = query_local_socket(domain_clean)
            else:
                res = query_doh(domain_clean, rtype_clean, provider=provider)

            duration_ms = round((time.monotonic() - start_time) * 1000, 2)
            res.update({
                "action": action_clean,
                "domain": domain_clean,
                "record_type": rtype_clean,
                "duration_ms": duration_ms,
                "context": get_execution_context(),
                "exit_code": EXIT_SUCCESS if res.get("success") else EXIT_ERROR,
            })

            if use_cache:
                cache.set(cache_key, res)

            return res

    except ToolError as exc:
        duration_ms = round((time.monotonic() - start_time) * 1000, 2)
        return {
            "success": False,
            "domain": domain_clean,
            "error": exc.message,
            "exit_code": exc.exit_code,
            "duration_ms": duration_ms,
        }
    except Exception as exc:
        duration_ms = round((time.monotonic() - start_time) * 1000, 2)
        return {
            "success": False,
            "domain": domain_clean,
            "error": f"Execution error: {exc}",
            "exit_code": EXIT_ERROR,
            "duration_ms": duration_ms,
        }
    finally:
        shutdown.restore()


# ==============================================================================
# SECTION 7: Output Routing & AIChat Function Entry Point
# ==============================================================================

def write_llm_output(data: dict[str, Any]) -> None:
    """Write clean JSON payload to LLM_OUTPUT."""
    out_path = os.environ.get("LLM_OUTPUT", "/dev/stdout")
    json_payload = json.dumps(data, indent=2, ensure_ascii=False, cls=ToolJSONEncoder) + "\n"

    if out_path in {"/dev/stdout", "/dev/fd/1", "-"}:
        sys.stdout.write(json_payload)
        sys.stdout.flush()
    else:
        try:
            Path(out_path).parent.mkdir(parents=True, exist_ok=True)
            with open(out_path, "a", encoding="utf-8") as fp:
                fp.write(json_payload)
        except OSError:
            sys.stdout.write(json_payload)
            sys.stdout.flush()


def run(
    domain: str,
    type: Literal["A", "AAAA", "MX", "TXT", "NS", "CNAME", "SOA", "PTR", "CAA", "ALL"] = "A",
    provider: Literal["cloudflare", "google", "quad9", "local"] = "cloudflare",
    action: Literal["query", "reverse", "doh", "sys", "propagate"] = "query",
    use_cache: bool = False,
    no_color: bool = False,
    verbose: bool = False,
) -> None:
    """Execute DNS query or system inspection.

    Args:
        domain: Target domain name or IP address (required)
        type: Record type: A, AAAA, MX, TXT, NS, CNAME, SOA, PTR, CAA, ALL (default: A)
        provider: DNS provider: cloudflare, google, quad9, local (default: cloudflare)
        action: Execution action: query, reverse, doh, sys, propagate (default: query)
        use_cache: Enable result caching
        no_color: Disable ANSI color output
        verbose: Enable detailed debug log output
    """
    result = execute_tool(
        domain=domain,
        record_type=type,
        provider=provider,
        action=action,
        use_cache=use_cache,
        no_color=no_color,
        verbose=verbose,
    )

    print_human_readable_ui(result, no_color=no_color)
    write_llm_output(result)


# ==============================================================================
# SECTION 8: CLI Argument Parser
# ==============================================================================

def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="dns_tool.py",
        description=f"Termux DNS Query & Inspection Custom Tool v{__version__}",
    )
    parser.add_argument(
        "--domain", "-d",
        required=True,
        metavar="DOMAIN",
        help="Target domain name or IP address (required)",
    )
    parser.add_argument(
        "--type", "-t",
        default="A",
        choices=["A", "AAAA", "MX", "TXT", "NS", "CNAME", "SOA", "PTR", "CAA", "ALL"],
        help="Record type (default: A)",
    )
    parser.add_argument(
        "--provider", "-p",
        default="cloudflare",
        choices=["cloudflare", "google", "quad9", "local"],
        help="DNS provider (default: cloudflare)",
    )
    parser.add_argument(
        "--action", "-a",
        default="query",
        choices=["query", "reverse", "doh", "sys", "propagate"],
        help="Execution action (default: query)",
    )
    parser.add_argument(
        "--use-cache",
        dest="use_cache",
        action="store_true",
        default=False,
        help="Enable caching",
    )
    parser.add_argument(
        "--no-color",
        dest="no_color",
        action="store_true",
        default=False,
        help="Disable ANSI color UI",
    )
    parser.add_argument(
        "--verbose", "-v",
        action="store_true",
        default=False,
        help="Enable debug logging",
    )
    return parser


if __name__ == "__main__":
    args = _build_parser().parse_args()
    res = execute_tool(
        domain=args.domain,
        record_type=args.type,
        provider=args.provider,
        action=args.action,
        use_cache=args.use_cache,
        no_color=args.no_color,
        verbose=args.verbose,
    )

    print_human_readable_ui(res, no_color=args.no_color)
    write_llm_output(res)
    sys.exit(res.get("exit_code", EXIT_SUCCESS))
