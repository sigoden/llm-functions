#!/usr/bin/env python3
# ==============================================================================
# web_intel.py — Pyrmethus AIChat Web Intelligence Engine v2.0.0
# argc/aichat compatible · Human-Readable Colorized Outputs · Concurrent Crawling
#
# @describe Unified tool for high-performance web fetching, recursive crawling,
#           media extraction, and network diagnostics.
#
# @option --action! <ACTION>             Action: fetch, crawl, diagnose, batch (required)
# @option --url <TEXT>                   Target URL (required for non-batch actions)
# @option --method <METHOD>              HTTP method: GET, POST, PUT, DELETE, etc. (default: GET)
# @option --data <TEXT>                  Payload for POST/PUT requests
# @option --headers <TEXT>               Custom headers "K: V, K2: V2"
# @option --depth <NUM>                  Crawl depth (default: 1)
# @option --limit <NUM>                  Max pages/items to process (default: 20)
# @option --keyword <TEXT>               Search keyword in crawled pages
# @option --extract <EXPR>               JSONPath expression for API responses
# @option --media-dir <PATH>             Dir for downloaded images
# @option --output <PATH>                Destination file for downloads
# @option --batch <PATH>                 JSON file for batch request objects
# @flag   --domain-restrict              Restrict crawling to start domain
# @flag   --download-images              Download images found during crawl
# @flag   --gen-thumbs                  Generate thumbnails (requires Pillow)
# @flag   --no-verify-ssl                Ignore SSL certificate errors
# @flag   --no-color                     Disable ANSI color output
# @flag   --verbose                      Enable detailed debug logging
#
# @env LLM_OUTPUT=/dev/stdout            Output path for LLM integration
# ==============================================================================

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import socket
import ssl
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any, List, Optional, Set
from urllib.parse import urljoin, urlparse

# Dependencies
try:
    import requests
    from bs4 import BeautifulSoup
except ImportError:
    print(
        "\033[31mError: Missing dependencies. Run: pip install requests beautifulsoup4\033[0m"
    )
    sys.exit(127)

try:
    from PIL import Image

    HAS_PILLOW = True
except ImportError:
    HAS_PILLOW = False

__version__ = "2.0.0"

# ==============================================================================
# SECTION 1: UI & Color Palette
# ==============================================================================

NEON_CYAN, NEON_GREEN, NEON_RED, NEON_YELLOW = (
    "\033[38;5;51m",
    "\033[38;5;46m",
    "\033[38;5;196m",
    "\033[38;5;226m",
)
NEON_PURPLE, NEON_PINK, RESET, BOLD, DIM = (
    "\033[38;5;129m",
    "\033[38;5;198m",
    "\033[0m",
    "\033[1m",
    "\033[2m",
)


def _cprint(text: str, no_color: bool = False) -> None:
    if no_color or not sys.stderr.isatty():
        text = re.sub(r"\x1b(?:[@-Z\\-_]|\[[0-?]*[ -/]*[@-~])", "", text)
    print(text, file=sys.stderr, flush=True)


def print_ui(data: dict[str, Any], no_color: bool = False) -> None:
    if not sys.stderr.isatty() or no_color:
        return

    success = data.get("success", False)
    status_color = NEON_GREEN if success else NEON_RED
    border = "─" * 64

    _cprint(f"{NEON_PURPLE}╭{border}╮{RESET}")
    _cprint(
        f"{NEON_PURPLE}│{RESET} {NEON_PINK}⚡ [WEB INTEL ENGINE v{__version__}]{RESET} {status_color}{BOLD}{'✓ SUCCESS' if success else '✗ FAILED'}{RESET}"
    )
    _cprint(f"{NEON_PURPLE}├{border}┤{RESET}")

    for key, val in data.get("metrics", {}).items():
        _cprint(
            f"{NEON_PURPLE}│{RESET} {NEON_CYAN}{key:<15}:{RESET} {NEON_YELLOW}{val}{RESET}"
        )

    if not success:
        _cprint(f"{NEON_PURPLE}├{border}┤{RESET}")
        _cprint(f"{NEON_PURPLE}│{RESET} {NEON_RED}Error:{RESET} {data.get('error')}")
    elif "preview" in data:
        _cprint(f"{NEON_PURPLE}├{border}┤{RESET}")
        _cprint(f"{NEON_PURPLE}│{RESET} {BOLD}Preview:{RESET}")
        for line in str(data["preview"]).splitlines()[:3]:
            _cprint(f"{NEON_PURPLE}│{RESET}   {DIM}{line[:55]}...{RESET}")

    _cprint(f"{NEON_PURPLE}╰{border}╯{RESET}")


# ==============================================================================
# SECTION 2: The Intelligence Engine
# ==============================================================================


class WebSession:
    """Centralized session handler for all network operations."""

    def __init__(self, headers: Optional[str] = None, no_verify: bool = False):
        self.session = requests.Session()
        self.session.headers.update({"User-Agent": f"WebIntelEngine/{__version__}"})
        if headers:
            for pair in headers.split(","):
                if ":" in pair:
                    k, v = pair.split(":", 1)
                    self.session.headers[k.strip()] = v.strip()
        self.session.verify = not no_verify

    def request(self, method: str, url: str, **kwargs) -> requests.Response:
        return self.session.request(method, url, timeout=15, **kwargs)


class WebIntelCore:
    """Core logic combining Fetching, Crawling, and Diagnostics."""

    def __init__(self, session: WebSession):
        self.session = session

    def extract_json_path(self, data: Any, path: str) -> Any:
        """Advanced dotted-path extraction for JSON."""
        try:
            current = data
            for token in re.findall(r'"[^"]*"|\[\d+\]|[^.\[\]]+', path):
                if token.startswith("["):
                    current = current[int(token[1:-1])]
                else:
                    current = current.get(token.strip('"'))
            return current
        except Exception:
            return None

    def diagnose(self, url: str) -> dict[str, Any]:
        """TCP/TLS Handshake diagnostics."""
        parsed = urlparse(url)
        host = parsed.hostname or ""
        port = parsed.port or (443 if parsed.scheme == "https" else 80)

        metrics = {}
        try:
            # DNS
            t0 = time.perf_counter()
            ips = [
                a[4][0]
                for a in socket.getaddrinfo(host, port, proto=socket.IPPROTO_TCP)
            ]
            metrics["dns_ms"] = round((time.perf_counter() - t0) * 1000, 2)

            # TCP
            t1 = time.perf_counter()
            sock = socket.create_connection((host, port), timeout=5)
            metrics["tcp_ms"] = round((time.perf_counter() - t1) * 1000, 2)

            # TLS
            if parsed.scheme == "https":
                t2 = time.perf_counter()
                ctx = (
                    ssl._create_unverified_context()
                    if not self.session.session.verify
                    else ssl.create_default_context()
                )
                with ctx.wrap_socket(sock, server_hostname=host):
                    metrics["tls_ms"] = round((time.perf_counter() - t2) * 1000, 2)
            else:
                sock.close()

            return {"success": True, "metrics": {**metrics, "ips": ips}}
        except Exception as e:
            return {"success": False, "error": str(e)}

    def fetch(
        self, url: str, method="GET", data=None, extract=None, output=None
    ) -> dict[str, Any]:
        """High-level fetch with optional JSON extraction or file download."""
        try:
            resp = self.session.request(method, url, data=data)
            resp.raise_for_status()

            if output:
                out_path = Path(output).expanduser().resolve()
                out_path.parent.mkdir(parents=True, exist_ok=True)
                with open(out_path, "wb") as f:
                    f.write(resp.content)
                return {
                    "success": True,
                    "metrics": {
                        "status": resp.status_code,
                        "size": len(resp.content),
                        "file": str(out_path),
                    },
                }

            content = resp.text
            if extract:
                try:
                    content = json.dumps(
                        self.extract_json_path(resp.json(), extract), indent=2
                    )
                except Exception:
                    pass

            return {
                "success": True,
                "metrics": {"status": resp.status_code, "size": len(resp.content)},
                "preview": content[:500],
            }
        except Exception as e:
            return {"success": False, "error": str(e)}

    def crawl(
        self,
        start_url: str,
        depth=1,
        limit=20,
        keyword=None,
        domain_restrict=True,
        download_imgs=False,
        media_dir=None,
        gen_thumbs=False,
    ) -> dict[str, Any]:
        """Concurrent recursive crawler with media extraction."""
        start_domain = urlparse(start_url).netloc.lower()
        visited: Set[str] = set()
        results: List[dict] = []
        queue = [(start_url, 0)]

        media_path = (
            Path(media_dir).expanduser().resolve()
            if media_dir
            else Path.cwd() / "cache/media"
        )

        with ThreadPoolExecutor(max_workers=5) as executor:
            while queue and len(results) < limit:
                url, curr_depth = queue.pop(0)
                if url in visited:
                    continue
                visited.add(url)

                # Concurrent Fetching
                future = executor.submit(self.session.get, url, timeout=10)
                try:
                    resp = future.result()
                    if resp.status_code != 200 or "text/html" not in resp.headers.get(
                        "Content-Type", ""
                    ):
                        continue

                    soup = BeautifulSoup(resp.text, "html.parser")
                    text = soup.get_text(separator=" ", strip=True)

                    page_data = {
                        "url": url,
                        "depth": curr_depth,
                        "keyword_match": False,
                    }
                    if keyword and keyword.lower() in text.lower():
                        page_data["keyword_match"] = True

                    # Image Handling
                    if download_imgs:
                        imgs = soup.find_all("img", src=True)
                        media_path.mkdir(parents=True, exist_ok=True)
                        for img in imgs:
                            img_url = urljoin(url, img["src"])
                            try:
                                img_data = self.session.get(img_url, timeout=5).content
                                name = f"{hashlib.md5(img_url.encode()).hexdigest()[:10]}.jpg"
                                fpath = media_path / name
                                with open(fpath, "wb") as f:
                                    f.write(img_data)
                                if gen_thumbs and HAS_PILLOW:
                                    with Image.open(fpath) as i:
                                        i.thumbnail((150, 150))
                                        i.save(media_path / f"thumb_{name}")
                            except:
                                continue

                    results.append(page_data)

                    # Link Discovery
                    if curr_depth < depth:
                        for a in soup.find_all("a", href=True):
                            link = urljoin(url, a["href"]).split("#")[0]
                            if (
                                domain_restrict
                                and urlparse(link).netloc.lower() != start_domain
                            ):
                                continue
                            if link not in visited:
                                queue.append((link, curr_depth + 1))

                except Exception:
                    continue

        return {
            "success": True,
            "metrics": {
                "pages": len(results),
                "matches": sum(1 for r in results if r["keyword_match"]),
            },
            "preview": "\n".join([r["url"] for r in results]),
        }


# ==============================================================================
# SECTION 3: Orchestrator & Entry Points
# ==============================================================================


def execute_tool(**kwargs) -> dict[str, Any]:
    action = kwargs.get("action", "").lower()
    url = kwargs.get("url")

    session = WebSession(
        headers=kwargs.get("headers"), no_verify=kwargs.get("no_verify_ssl")
    )
    core = WebIntelCore(session)

    start_time = time.perf_counter()

    if action == "batch":
        batch_path = Path(kwargs.get("batch", ""))
        if not batch_path.exists():
            return {"success": False, "error": "Batch file missing"}

        batch_items = json.loads(batch_path.read_text())
        results = []
        for item in batch_items:
            results.append(core.fetch(item["url"], method=item.get("method", "GET")))

        res = {
            "success": True,
            "metrics": {
                "total": len(results),
                "ok": sum(1 for r in results if r["success"]),
            },
            "results": results,
        }

    elif action == "fetch":
        res = core.fetch(
            url,
            method=kwargs.get("method", "GET"),
            data=kwargs.get("data"),
            extract=kwargs.get("extract"),
            output=kwargs.get("output"),
        )

    elif action == "crawl":
        res = core.crawl(
            url,
            depth=kwargs.get("depth", 1),
            limit=kwargs.get("limit", 20),
            keyword=kwargs.get("keyword"),
            domain_restrict=kwargs.get("domain_restrict", True),
            download_imgs=kwargs.get("download_images", False),
            media_dir=kwargs.get("media_dir"),
            gen_thumbs=kwargs.get("gen_thumbs", False),
        )

    elif action == "diagnose":
        res = core.diagnose(url)

    else:
        res = {"success": False, "error": f"Unknown action: {action}"}

    res["metrics"] = res.get("metrics", {})
    res["metrics"]["duration_ms"] = round((time.perf_counter() - start_time) * 1000, 2)
    return res


def write_llm_output(data: dict[str, Any]) -> None:
    out_path = os.environ.get("LLM_OUTPUT", "/dev/stdout")
    payload = json.dumps(data, indent=2, ensure_ascii=False) + "\n"
    if out_path in {"/dev/stdout", "/dev/fd/1", "-"}:
        sys.stdout.write(payload)
    else:
        with open(out_path, "a", encoding="utf-8") as f:
            f.write(payload)


def run(**kwargs) -> None:
    result = execute_tool(**kwargs)
    print_ui(result, no_color=kwargs.get("no_color", False))
    write_llm_output(result)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="AIChat Web Intelligence Engine")
    parser.add_argument(
        "--action", required=True, choices=["fetch", "crawl", "diagnose", "batch"]
    )
    parser.add_argument("--url")
    parser.add_argument("--method", default="GET")
    parser.add_argument("--data")
    parser.add_argument("--headers")
    parser.add_argument("--depth", type=int, default=1)
    parser.add_argument("--limit", type=int, default=20)
    parser.add_argument("--keyword")
    parser.add_argument("--extract")
    parser.add_argument("--media-dir")
    parser.add_argument("--output")
    parser.add_argument("--batch")
    parser.add_argument("--domain-restrict", action="store_true", default=True)
    parser.add_argument("--download-images", action="store_true")
    parser.add_argument("--gen-thumbs", action="store_true")
    parser.add_argument("--no-verify-ssl", action="store_true")
    parser.add_argument("--no-color", action="store_true")
    parser.add_argument("--verbose", action="store_true")

    args = parser.parse_args()
    run(**vars(args))
