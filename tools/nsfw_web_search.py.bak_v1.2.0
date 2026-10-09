#!/usr/bin/env python3
# ==============================================================================
# nsfw_web_search.py — Unfiltered NSFW Web Search Tool v1.1.0-ASCENDED
# argc/aichat compatible · Termux-ready · Dual-output + Proxy Rotation
#
# @describe Perform an unfiltered web search (SafeSearch forced OFF). Supports optional proxy rotation for reliability.
#
# @option --query! <STRING>              Search query (required)
# @option --max-results <INT>            Maximum results (default: 8, max: 20)
# @option --region <STRING>              Region code (default: us-en)
# @option --proxy <URL>                  Single proxy URL (http://host:port or socks5://...)
# @flag   --use-proxy-rotation           Enable automatic free-proxy rotation
# @option --proxy-list <PATH>            Custom proxy list file (one URL per line)
# @flag   --no-color                     Disable ANSI colour output
# @flag   --verbose                      Show proxy attempts + debug
#
# @env LLM_OUTPUT=/dev/stdout
# @env LLM_TOOL_CACHE_TTL=30m
# @env NSFW_SEARCH_PROXIES             Comma-separated proxy list (overrides built-in)
# ==============================================================================

from __future__ import annotations

import base64
import hashlib
import json
import os
import random
import re
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional
from urllib.parse import parse_qs, quote_plus, urlparse

__version__ = "1.2.0"

try:
    import requests
    from bs4 import BeautifulSoup
    HAS_REQUESTS = True
except ImportError:
    HAS_REQUESTS = False

# ==============================================================================
# Neon UI (same ascended style)
# ==============================================================================

NEON_PINK   = "\033[38;5;198m"
NEON_CYAN   = "\033[38;5;51m"
NEON_GREEN  = "\033[38;5;46m"
NEON_ORANGE = "\033[38;5;202m"
NEON_PURPLE = "\033[38;5;129m"
NEON_YELLOW = "\033[38;5;226m"
NEON_RED    = "\033[38;5;196m"
NEON_LIME   = "\033[38;5;82m"
RESET = "\033[0m"
BOLD  = "\033[1m"
DIM   = "\033[2m"

BOX_TL, BOX_TR, BOX_BL, BOX_BR = "╭", "╮", "╰", "╯"
BOX_V, BOX_H, BOX_LT, BOX_RT   = "│", "─", "├", "┤"

_COLOR_OVERRIDE: Optional[bool] = None

def _is_tty() -> bool:
    return sys.stderr.isatty() and os.environ.get("TERM", "").lower() not in ("dumb", "")

def _color_enabled() -> bool:
    if _COLOR_OVERRIDE is not None:
        return _COLOR_OVERRIDE
    if "NO_COLOR" in os.environ:
        return False
    return _is_tty()

def _cprint(text: str, end: str = "\n") -> None:
    if not _color_enabled():
        text = re.sub(r"\x1b\[[0-9;]*m", "", text)
    print(text, end=end, flush=True, file=sys.stderr)

def get_width() -> int:
    try:
        return max(40, min(os.get_terminal_size(sys.stderr.fileno()).columns, 110))
    except Exception:
        return 80

def print_human_readable_ui(data: dict[str, Any], no_color: bool = False) -> None:
    global _COLOR_OVERRIDE
    previous = _COLOR_OVERRIDE
    _COLOR_OVERRIDE = False if no_color else None
    try:
        if not _is_tty() and not no_color:
            return
        success = bool(data.get("success", False))
        query = str(data.get("query") or "")
        results = data.get("results") or []
        duration_ms = data.get("duration_ms", 0)
        proxy_used = data.get("proxy_used")
        bw = max(get_width() - 4, 30)
        border = BOX_H * bw

        status_color = NEON_GREEN if success else NEON_RED
        status_symbol = "✓" if success else "✗"
        status_text = "SUCCESS" if success else "FAILED"

        _cprint(f"{NEON_PURPLE}{BOX_TL}{border}{BOX_TR}{RESET}")
        _cprint(
            f"{NEON_PINK} 🔍 {NEON_CYAN}[NSFW-SEARCH v{__version__}]{RESET} "
            f"{status_color}{BOLD}{status_symbol} {status_text}{RESET} "
            f"{NEON_YELLOW}›{RESET} {BOLD}{query[:bw-22]}{RESET}"
        )
        _cprint(
            f"{NEON_PURPLE}{BOX_V}{RESET} "
            f"{NEON_CYAN}Results:{RESET} {NEON_LIME}{len(results)}{RESET}  "
            f"{NEON_CYAN}Duration:{RESET} {NEON_LIME}{duration_ms}ms{RESET}  "
            f"{NEON_CYAN}Cached:{RESET} {NEON_YELLOW}{data.get('cached', False)}{RESET}"
        )
        if proxy_used:
            _cprint(f"{NEON_PURPLE}{BOX_V}{RESET} {NEON_ORANGE}Proxy:{RESET} {DIM}{proxy_used}{RESET}")
        _cprint(f"{NEON_PURPLE}{BOX_LT}{border}{BOX_RT}{RESET}")

        if not results:
            _cprint(f"{NEON_PURPLE}{BOX_V}{RESET} {DIM}(no results){RESET}")
        else:
            for i, r in enumerate(results[:12], 1):
                title = (r.get("title") or "")[:bw-8]
                link  = (r.get("link") or "")[:bw-8]
                _cprint(f"{NEON_PURPLE}{BOX_V}{RESET} {NEON_ORANGE}{i}.{RESET} {BOLD}{title}{RESET}")
                _cprint(f"{NEON_PURPLE}{BOX_V}{RESET}    {DIM}{link}{RESET}")
                snippet = (r.get("snippet") or "").strip()
                if snippet:
                    _cprint(f"{NEON_PURPLE}{BOX_V}{RESET}    {snippet[:bw-6]}")
                if i < len(results):
                    _cprint(f"{NEON_PURPLE}{BOX_V}{RESET}")

        _cprint(f"{NEON_PURPLE}{BOX_BL}{border}{BOX_BR}{RESET}")
    finally:
        _COLOR_OVERRIDE = previous

def write_llm_output(data: dict[str, Any]) -> None:
    out_path = os.environ.get("LLM_OUTPUT", "/dev/stdout").strip() or "/dev/stdout"
    payload = json.dumps(data, indent=2, ensure_ascii=False) + "\n"
    if out_path in {"/dev/stdout", "-", "/dev/fd/1"}:
        sys.stdout.write(payload)
        sys.stdout.flush()
        return
    try:
        p = Path(out_path).expanduser()
        p.parent.mkdir(parents=True, exist_ok=True)
        with open(p, "a", encoding="utf-8") as f:
            f.write(payload)
    except Exception:
        sys.stdout.write(payload)
        sys.stdout.flush()

# ==============================================================================
# Cache
# ==============================================================================

class SimpleCache:
    def __init__(self):
        base = os.environ.get("LLM_TOOL_CACHE_DIR") or str(Path.home() / ".cache" / "aichat_tools")
        self.dir = Path(base) / "nsfw_web_search"
        try:
            self.dir.mkdir(parents=True, exist_ok=True)
            self.ok = True
        except Exception:
            self.ok = False

    def _key(self, data: str) -> Path:
        h = hashlib.sha256(data.encode()).hexdigest()
        return self.dir / f"{h}.json"

    def get(self, key_data: str, ttl: float = 1800.0) -> Optional[dict]:
        if not self.ok:
            return None
        p = self._key(key_data)
        try:
            if time.time() - p.stat().st_mtime > ttl:
                p.unlink(missing_ok=True)
                return None
            return json.loads(p.read_text(encoding="utf-8"))
        except Exception:
            return None

    def set(self, key_data: str, value: dict) -> None:
        if not self.ok:
            return
        p = self._key(key_data)
        tmp = p.with_suffix(".tmp")
        try:
            tmp.write_text(json.dumps(value, ensure_ascii=False), encoding="utf-8")
            tmp.replace(p)
        except Exception:
            pass

# ==============================================================================
# Proxy rotation
# ==============================================================================

# Small built-in list of free public proxies (rotate these). 
# Master can override with NSFW_SEARCH_PROXIES or --proxy-list
_DEFAULT_PROXIES = [
    # These are examples – real free lists die fast. Prefer your own list.
    "http://45.77.64.133:8080",
    "http://138.197.102.119:80",
    "http://167.71.5.83:8080",
    "http://159.89.49.172:8080",
    "http://134.209.29.120:8080",
]

def _load_proxies(proxy: Optional[str], use_rotation: bool, proxy_list_path: Optional[str]) -> list[str]:
    proxies: list[str] = []

    if proxy:
        proxies.append(proxy.strip())

    env_list = os.environ.get("NSFW_SEARCH_PROXIES", "").strip()
    if env_list:
        proxies.extend([p.strip() for p in env_list.split(",") if p.strip()])

    if proxy_list_path:
        try:
            text = Path(proxy_list_path).expanduser().read_text(encoding="utf-8")
            proxies.extend([ln.strip() for ln in text.splitlines() if ln.strip() and not ln.startswith("#")])
        except Exception:
            pass

    if use_rotation and not proxies:
        proxies = list(_DEFAULT_PROXIES)

    # de-dupe while preserving order
    seen = set()
    unique = []
    for p in proxies:
        if p not in seen:
            seen.add(p)
            unique.append(p)
    return unique

def _make_session(proxy_url: Optional[str] = None) -> "requests.Session":
    s = requests.Session()
    s.headers.update({
        "User-Agent": "Mozilla/5.0 (Linux; Android 13; Termux) AppleWebKit/537.36 "
                      "(KHTML, like Gecko) Chrome/120.0.0.0 Mobile Safari/537.36",
        "Accept": "text/html,application/xhtml+xml",
        "Accept-Language": "en-US,en;q=0.9",
    })
    if proxy_url:
        s.proxies = {
            "http": proxy_url,
            "https": proxy_url,
        }
    return s

def _unwrap_bing_redirect(href: str) -> str:
    """Decode Bing /ck/a redirect wrappers (u=a1<base64url>) to the real target."""
    if "bing.com/ck/" not in href:
        return href
    try:
        qs = parse_qs(urlparse(href).query)
        encoded = qs.get("u", [""])[0]
        if encoded.startswith("a1") and len(encoded) > 2:
            padded = encoded[2:] + "=" * (-len(encoded[2:]) % 4)
            decoded = base64.urlsafe_b64decode(padded).decode("utf-8", errors="replace")
            if decoded.startswith(("http://", "https://")):
                return decoded
    except Exception:
        pass
    return href


def _parse_results(html: str, selectors: tuple[str, ...], max_results: int) -> list[dict]:
    """Parse ordinary search-result cards and discard non-http links."""
    soup = BeautifulSoup(html, "html.parser")
    results: list[dict] = []
    for selector in selectors:
        for card in soup.select(selector):
            # Title anchor varies by engine/markup:
            #   DDG:      a.result__a
            #   Bing web: h2 > a  (desktop)  or  a > h2  (mobile)
            anchor = None
            for candidate in card.select("a.result__a, h2 a, a.result-link, a.tilk"):
                # Skip Bing's site-attribution link (site name, not result title)
                if "tilk" in (candidate.get("class") or []):
                    continue
                anchor = candidate
                break
            if anchor is None:
                # Mobile Bing nests the h2 inside the anchor: <a><h2>Title</h2></a>
                heading = card.find("h2")
                if heading is not None:
                    parent_a = heading.find_parent("a")
                    if parent_a is not None:
                        anchor = parent_a
            if anchor is None:
                continue
            href = anchor.get("href") or ""
            if "uddg=" in href:
                qs = parse_qs(urlparse(href).query)
                href = qs.get("uddg", [href])[0]
            href = _unwrap_bing_redirect(href)
            if not href.startswith(("http://", "https://")):
                continue
            snippet_el = card.select_one(".result__snippet, .b_caption p, .result-snippet")
            results.append({
                "title": anchor.get_text(" ", strip=True),
                "link": href,
                "snippet": snippet_el.get_text(" ", strip=True) if snippet_el else "",
            })
            if len(results) >= max_results:
                return results
    return results


def _search_bing(query: str, max_results: int, region: str) -> list[dict]:
    """Small dependency-free fallback for DDG challenge/empty responses."""
    url = "https://www.bing.com/search?q=" + quote_plus(query)
    response = requests.get(url, headers={"User-Agent": _make_session().headers["User-Agent"],
                                          "Accept-Language": region}, timeout=14)
    response.raise_for_status()
    return _parse_results(response.text, ("li.b_algo",), max_results)


def _search_ddg(
    query: str,
    max_results: int = 8,
    region: str = "us-en",
    proxies: Optional[list[str]] = None,
    verbose: bool = False,
) -> tuple[list[dict], Optional[str]]:
    """Try direct DDG, then configured proxies; never treats a blocked page as success."""
    if not HAS_REQUESTS:
        raise RuntimeError("requests + beautifulsoup4 required")

    url = (
        f"https://html.duckduckgo.com/html/"
        f"?q={quote_plus(query)}"
        f"&kp=-2"          # SafeSearch OFF
        f"&kl={region}"
    )

    candidates = [None]  # direct connection first
    if proxies:
        random.shuffle(proxies)
        candidates.extend(proxies)

    last_error = None
    for proxy in candidates:
        try:
            if verbose and proxy:
                _cprint(f"{DIM}Trying proxy: {proxy}{RESET}")
            session = _make_session(proxy)
            resp = session.get(url, timeout=14)
            resp.raise_for_status()

            results = _parse_results(resp.text, (".result",), max_results)
            if results:
                return results, proxy
            # Empty HTML commonly means a bot challenge, not a valid search.
            last_error = "search backend returned no parseable results"
        except Exception as exc:
            last_error = str(exc)
            if verbose:
                _cprint(f"{NEON_RED}Proxy failed: {proxy or 'direct'} → {exc}{RESET}")
            continue

    raise RuntimeError(f"All proxies (and direct) failed. Last error: {last_error}")

# ==============================================================================
# Core execution
# ==============================================================================

def execute_tool(
    query: str,
    max_results: int = 8,
    region: str = "us-en",
    proxy: Optional[str] = None,
    use_proxy_rotation: bool = False,
    proxy_list: Optional[str] = None,
    no_color: bool = False,
    verbose: bool = False,
    use_cache: bool = True,
) -> dict[str, Any]:
    start = time.monotonic()
    started_at = datetime.now(timezone.utc).isoformat()
    warnings: list[str] = []

    query = (query or "").strip()
    if not query:
        return {
            "success": False,
            "error": "query must not be empty",
            "query": query,
            "results": [],
            "duration_ms": 0,
            "cached": False,
            "proxy_used": None,
            "warnings": ["empty query"],
            "started_at": started_at,
            "finished_at": started_at,
        }

    max_results = max(1, min(int(max_results or 8), 20))
    region = (region or "us-en").strip().lower()

    proxies = _load_proxies(proxy, use_proxy_rotation, proxy_list)

    cache = SimpleCache()
    cache_key = json.dumps({
        "q": query, "n": max_results, "r": region,
        "proxies": sorted(proxies) if proxies else None
    }, sort_keys=True)

    if use_cache:
        hit = cache.get(cache_key, ttl=1800)
        if hit:
            hit["cached"] = True
            hit["warnings"] = hit.get("warnings", []) + ["Served from cache"]
            return hit

    try:
        results, used_proxy = _search_ddg(
            query, max_results=max_results, region=region,
            proxies=proxies, verbose=verbose
        )
        success = True
        error = None
    except Exception as exc:
        # DDG frequently returns a challenge page. Use a bounded, explicit
        # fallback rather than reporting a misleading successful zero-result.
        try:
            results = _search_bing(query, max_results, region)
            used_proxy = None
            success = True
            error = None
            warnings.append("DuckDuckGo unavailable; used Bing fallback")
        except Exception as fallback_exc:
            results = []
            used_proxy = None
            success = False
            error = f"DDG: {exc}; Bing: {fallback_exc}"
            warnings.append("All search backends failed")

    duration_ms = round((time.monotonic() - start) * 1000, 1)
    finished_at = datetime.now(timezone.utc).isoformat()

    data = {
        "success": success,
        "query": query,
        "max_results": max_results,
        "region": region,
        "results": results,
        "result_count": len(results),
        "duration_ms": duration_ms,
        "cached": False,
        "proxy_used": used_proxy,
        "error": error,
        "warnings": warnings,
        "started_at": started_at,
        "finished_at": finished_at,
        "tool": "nsfw_web_search",
        "version": __version__,
        "note": "SafeSearch forced OFF (kp=-2). Proxy rotation available for reliability.",
    }

    if use_cache and success and results:
        cache.set(cache_key, data)

    return data

def run(
    query: str,
    max_results: int = 8,
    region: str = "us-en",
    proxy: Optional[str] = None,
    use_proxy_rotation: bool = False,
    proxy_list: Optional[str] = None,
    no_color: bool = False,
    verbose: bool = False,
) -> None:
    """Unfiltered NSFW web search with optional proxy rotation.

    Args:
        query: Search query (required)
        max_results: Number of results (1-20)
        region: DuckDuckGo region
        proxy: Single proxy URL
        use_proxy_rotation: Enable built-in free proxy rotation
        proxy_list: Path to custom proxy list file
        no_color: Disable neon UI
        verbose: Show proxy attempt logs
    """
    res = execute_tool(
        query=query,
        max_results=max_results,
        region=region,
        proxy=proxy,
        use_proxy_rotation=use_proxy_rotation,
        proxy_list=proxy_list,
        no_color=no_color,
        verbose=verbose,
    )
    print_human_readable_ui(res, no_color=no_color)
    write_llm_output(res)

# ==============================================================================
# CLI
# ==============================================================================

if __name__ == "__main__":
    import argparse
    p = argparse.ArgumentParser(description="NSFW unfiltered web search with proxy rotation")
    p.add_argument("--query", "-q", required=True)
    p.add_argument("--max-results", type=int, default=8)
    p.add_argument("--region", default="us-en")
    p.add_argument("--proxy", default=None)
    p.add_argument("--use-proxy-rotation", action="store_true")
    p.add_argument("--proxy-list", default=None)
    p.add_argument("--no-color", action="store_true")
    p.add_argument("--verbose", action="store_true")
    args = p.parse_args()

    res = execute_tool(
        query=args.query,
        max_results=args.max_results,
        region=args.region,
        proxy=args.proxy,
        use_proxy_rotation=args.use_proxy_rotation,
        proxy_list=args.proxy_list,
        no_color=args.no_color,
        verbose=args.verbose,
    )
    print_human_readable_ui(res, no_color=args.no_color)
    write_llm_output(res)
    sys.exit(0 if res.get("success") else 1)
