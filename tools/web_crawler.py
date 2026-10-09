#!/usr/bin/env python3
# ==============================================================================
# web_crawler.py — Pyrmethus AIChat Tool Template v1.1.0
# argc/aichat compatible · Human-Readable Colorized Outputs
#
# @describe Web crawler that parses HTML, extracts links, recursively crawls pages, searches keywords, and downloads images/thumbnails.
#
# @option --start-url! <TEXT>            Starting URL to crawl (required)
# @option --max-depth <NUM>              Maximum crawl depth (0 = start page only, default: 1)
# @option --max-pages <NUM>              Maximum total pages to crawl (default: 10)
# @option --keyword <TEXT>               Search for a specific keyword in page text
# @option --media-dir <PATH>             Directory to save downloaded images (default: cache/crawled_media)
# @option --thumb-dir <PATH>             Directory to save generated thumbnails (default: cache/crawled_thumbs)
# @option --thumb-width <NUM>            Thumbnail width in pixels (default: 150)
# @flag   --domain-restrict              Restrict crawling to links on the same domain as start-url
# @flag   --download-images              Enable automatic downloading of images found in crawled pages
# @flag   --generate-thumbnails          Generate thumbnails for downloaded images (requires Pillow)
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
import re
import ssl
import sys
import time
import urllib.parse
import urllib.request
from html.parser import HTMLParser
from pathlib import Path
from typing import Any, Optional, Set, Tuple

import requests

__version__ = "1.1.0"

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
        f"{NEON_PURPLE}│{RESET} {NEON_PINK}⚡ [WEB CRAWLER & MEDIA EXTRACTOR]{RESET} {status_color}{BOLD}{status_symbol} {status_text}{RESET}"
    )
    _cprint(f"{NEON_PURPLE}├{border}┤{RESET}")
    _cprint(
        f"{NEON_PURPLE}│{RESET} {NEON_CYAN}Start URL:{RESET}      {data.get('start_url', 'N/A')}"
    )
    _cprint(
        f"{NEON_PURPLE}│{RESET} {NEON_CYAN}Pages Crawled:{RESET}  {NEON_YELLOW}{data.get('total_crawled_count', 0):,}{RESET}"
    )
    _cprint(
        f"{NEON_PURPLE}│{RESET} {NEON_CYAN}Keyword Match:{RESET}  {NEON_GREEN}{data.get('keyword_matches_count', 0):,}{RESET}"
    )
    _cprint(
        f"{NEON_PURPLE}│{RESET} {NEON_CYAN}Media Downloads:{RESET}{NEON_GREEN}{data.get('total_downloads_count', 0):,}{RESET}"
    )
    _cprint(
        f"{NEON_PURPLE}│{RESET} {NEON_CYAN}Duration:{RESET}       {DIM}{data.get('duration_ms', 0)}ms{RESET}"
    )

    if not success and "error" in data:
        _cprint(f"{NEON_PURPLE}├{border}┤{RESET}")
        _cprint(
            f"{NEON_PURPLE}│{RESET} {NEON_RED}Error:{RESET}          {data['error']}"
        )

    pages = data.get("pages", [])
    if pages:
        _cprint(f"{NEON_PURPLE}├{border}┤{RESET}")
        _cprint(
            f"{NEON_PURPLE}│{RESET} {BOLD}Crawled Pages Summary ({len(pages)}):{RESET}"
        )
        for page in pages[:5]:
            url = page.get("url", "")
            kw_match = " [KEYWORD FOUND]" if page.get("keyword_found") else ""
            _cprint(
                f"{NEON_PURPLE}│{RESET}   {NEON_CYAN}›{RESET} {url[:50]}...{NEON_GREEN}{kw_match}{RESET}"
            )

    _cprint(f"{NEON_PURPLE}╰{border}╯{RESET}")


# ==============================================================================
# SECTION 2: Core Logic Implementation
# ==============================================================================


class LinkExtractor(HTMLParser):
    """HTML parser that extracts text and links while excluding code/style tags."""

    def __init__(self, base_url: str):
        super().__init__()
        self.base_url = base_url
        self.links: Set[str] = set()
        self.image_links: Set[str] = set()
        self.text_parts: list[str] = []
        self._ignore = False

    def handle_starttag(self, tag: str, attrs: list[tuple[str, Optional[str]]]) -> None:
        tag_lower = tag.lower()
        if tag_lower in ("script", "style", "head", "noscript"):
            self._ignore = True
            return

        if tag_lower == "a":
            for attr, val in attrs:
                if attr.lower() == "href" and val:
                    abs_url = urllib.parse.urljoin(self.base_url, val.strip())
                    abs_url = abs_url.split("#")[0]
                    if abs_url.startswith(("http://", "https://")):
                        self.links.add(abs_url)
        elif tag_lower == "img":
            for attr, val in attrs:
                if attr.lower() == "src" and val:
                    abs_url = urllib.parse.urljoin(self.base_url, val.strip())
                    abs_url = abs_url.split("#")[0]
                    if abs_url.startswith(("http://", "https://")):
                        self.image_links.add(abs_url)

    def handle_endtag(self, tag: str) -> None:
        if tag.lower() in ("script", "style", "head", "noscript"):
            self._ignore = False

    def handle_data(self, data: str) -> None:
        if not self._ignore:
            text = data.strip()
            if text:
                self.text_parts.append(text)

    def get_text(self) -> str:
        return " ".join(self.text_parts)


def _canonicalize_url(url: str) -> str:
    """Normalize URL by stripping fragments and trailing slashes."""
    parsed = urllib.parse.urlparse(url.strip())
    path = parsed.path.rstrip("/") if parsed.path != "/" else "/"
    return urllib.parse.urlunparse(
        (
            parsed.scheme.lower(),
            parsed.netloc.lower(),
            path,
            parsed.params,
            parsed.query,
            "",
        )
    )


def fetch_and_parse(
    url: str, verbose: bool = False
) -> Tuple[str, str, Set[str], Set[str]]:
    """Fetch URL and return status, page_text, extracted links, and image links."""
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    }
    req = urllib.request.Request(url, headers=headers)
    ssl_context = ssl._create_unverified_context()

    try:
        with urllib.request.urlopen(req, timeout=15, context=ssl_context) as response:
            content_type = response.headers.get("Content-Type", "")
            if "text/html" not in content_type.lower():
                return "skipped_non_html", "", set(), set()

            raw_bytes = response.read()
            try:
                html = raw_bytes.decode("utf-8", errors="replace")
            except Exception:
                html = raw_bytes.decode("latin-1", errors="replace")

            parser = LinkExtractor(url)
            parser.feed(html)

            page_text = re.sub(r"\s+", " ", parser.get_text()).strip()
            return "ok", page_text, parser.links, parser.image_links
    except Exception as e:
        return f"fetch_error: {e}", "", set(), set()


def download_image_file(url: str, download_dir: Path) -> Optional[str]:
    """Download image stream to directory with collision-free MD5 naming."""
    download_dir.mkdir(parents=True, exist_ok=True)
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    }
    try:
        parsed_url = urllib.parse.urlparse(url)
        orig_filename = os.path.basename(parsed_url.path)
        ext = (
            os.path.splitext(orig_filename)[1].lower()
            if "." in orig_filename
            else ".jpg"
        )
        if ext not in (".jpg", ".jpeg", ".png", ".webp", ".gif", ".svg"):
            ext = ".jpg"

        url_hash = hashlib.md5(url.encode("utf-8")).hexdigest()[:10]
        filename = f"img_{url_hash}{ext}"
        output_path = download_dir / filename

        with requests.get(url, headers=headers, timeout=20, stream=True) as response:
            response.raise_for_status()
            with open(output_path, "wb") as f:
                for chunk in response.iter_content(chunk_size=8192):
                    if chunk:
                        f.write(chunk)

        return str(output_path.resolve())
    except Exception as e:
        logging.warning("Failed image download from %s: %s", url, e)
        return None


def make_thumbnail(image_path: str, thumb_dir: Path, width: int = 150) -> Optional[str]:
    """Generate image thumbnail safely using Pillow with RGB conversion."""
    thumb_dir.mkdir(parents=True, exist_ok=True)
    try:
        from PIL import Image

        with Image.open(image_path) as img:
            img_rgb = img.convert("RGB")
            ratio = width / float(img_rgb.size[0])
            height = int(float(img_rgb.size[1]) * float(ratio))
            img_rgb.thumbnail((width, height), Image.Resampling.LANCZOS)

            filename = "thumb_" + Path(image_path).name
            thumb_path = thumb_dir / filename
            img_rgb.save(thumb_path, "JPEG")
            return str(thumb_path.resolve())
    except Exception as e:
        logging.warning("Failed thumbnail generation for %s: %s", image_path, e)
        return None


def execute_tool(
    start_url: str,
    max_depth: int = 1,
    max_pages: int = 10,
    keyword: Optional[str] = None,
    domain_restrict: bool = True,
    download_images: bool = False,
    media_dir: Optional[str] = None,
    generate_thumbnails: bool = False,
    thumb_dir: Optional[str] = None,
    thumb_width: int = 150,
    no_color: bool = False,
    verbose: bool = False,
) -> dict[str, Any]:
    """
    Core execution logic for crawling web pages and extracting links/media.
    """
    start_time = time.perf_counter()

    if not start_url.startswith(("http://", "https://")):
        start_url = "https://" + start_url.strip()

    parsed_start = urllib.parse.urlparse(start_url)
    start_domain = parsed_start.netloc.lower()

    media_path = (
        Path(media_dir).expanduser().resolve()
        if media_dir
        else Path.cwd() / "cache" / "crawled_media"
    )
    thumb_path = (
        Path(thumb_dir).expanduser().resolve()
        if thumb_dir
        else Path.cwd() / "cache" / "crawled_thumbs"
    )

    keyword_lower = keyword.lower().strip() if keyword else None

    queue: list[Tuple[str, int]] = [(start_url, 0)]
    visited: Set[str] = set()
    crawled_pages: list[dict[str, Any]] = []

    keyword_matches_count = 0
    total_downloads_count = 0

    try:
        while queue and len(crawled_pages) < max_pages:
            url, depth = queue.pop(0)
            canon_url = _canonicalize_url(url)

            if canon_url in visited:
                continue
            visited.add(canon_url)

            if domain_restrict:
                curr_domain = urllib.parse.urlparse(canon_url).netloc.lower()
                if curr_domain != start_domain:
                    continue

            status, page_text, links, image_links = fetch_and_parse(canon_url, verbose)

            page_info: dict[str, Any] = {
                "url": canon_url,
                "depth": depth,
                "status": status,
                "keyword_found": False,
                "snippet": None,
                "links_found_count": len(links),
                "images_found_count": len(image_links),
                "downloads": [],
            }

            if status == "ok":
                if keyword_lower:
                    idx = page_text.lower().find(keyword_lower)
                    if idx != -1:
                        page_info["keyword_found"] = True
                        keyword_matches_count += 1
                        start_idx = max(0, idx - 80)
                        end_idx = min(len(page_text), idx + len(keyword_lower) + 80)
                        page_info["snippet"] = (
                            "..." + page_text[start_idx:end_idx].strip() + "..."
                        )

                if download_images and image_links:
                    for img_url in image_links:
                        saved_file = download_image_file(img_url, media_path)
                        if saved_file:
                            total_downloads_count += 1
                            meta = {
                                "url": img_url,
                                "saved_path": saved_file,
                                "thumbnail_path": None,
                            }
                            if generate_thumbnails:
                                thumb_file = make_thumbnail(
                                    saved_file, thumb_path, thumb_width
                                )
                                meta["thumbnail_path"] = thumb_file
                            page_info["downloads"].append(meta)

                crawled_pages.append(page_info)

                if depth < max_depth:
                    for link in links:
                        c_link = _canonicalize_url(link)
                        if c_link not in visited:
                            queue.append((link, depth + 1))
            else:
                page_info["error"] = status
                crawled_pages.append(page_info)

        duration_ms = round((time.perf_counter() - start_time) * 1000, 2)

        return {
            "success": True,
            "start_url": start_url,
            "domain_restricted": domain_restrict,
            "total_crawled_count": len(crawled_pages),
            "keyword_matches_count": keyword_matches_count,
            "total_downloads_count": total_downloads_count,
            "pages": crawled_pages,
            "duration_ms": duration_ms,
            "exit_code": 0,
        }

    except Exception as exc:
        return {
            "success": False,
            "error": f"Web crawler execution failed: {exc}",
            "exit_code": 1,
        }


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
    start_url: str,
    max_depth: int = 1,
    max_pages: int = 10,
    keyword: Optional[str] = None,
    domain_restrict: bool = True,
    download_images: bool = False,
    media_dir: Optional[str] = None,
    generate_thumbnails: bool = False,
    thumb_dir: Optional[str] = None,
    thumb_width: int = 150,
    no_color: bool = False,
    verbose: bool = False,
) -> None:
    """
    AIChat Programmatic Entrypoint.
    Parameter names match option/flag slugs (with underscores).
    """
    result = execute_tool(
        start_url=start_url,
        max_depth=max_depth,
        max_pages=max_pages,
        keyword=keyword,
        domain_restrict=domain_restrict,
        download_images=download_images,
        media_dir=media_dir,
        generate_thumbnails=generate_thumbnails,
        thumb_dir=thumb_dir,
        thumb_width=thumb_width,
        no_color=no_color,
        verbose=verbose,
    )

    print_human_readable_ui(result, no_color=no_color)
    write_llm_output(result)


# ==============================================================================
# SECTION 5: CLI Argument Parser
# ==============================================================================


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="web_crawler.py",
        description=f"AIChat Web Crawler & Link Follower Tool v{__version__}",
    )
    parser.add_argument(
        "--start-url",
        "-s",
        required=True,
        dest="start_url",
        metavar="TEXT",
        help="Starting URL to crawl (required)",
    )
    parser.add_argument(
        "--max-depth",
        type=int,
        default=1,
        dest="max_depth",
        help="Maximum crawl depth (default: 1)",
    )
    parser.add_argument(
        "--max-pages",
        type=int,
        default=10,
        dest="max_pages",
        help="Maximum total pages to crawl (default: 10)",
    )
    parser.add_argument(
        "--keyword",
        "-k",
        type=str,
        default=None,
        help="Search for a specific keyword in page text",
    )
    parser.add_argument(
        "--domain-restrict",
        action="store_true",
        default=True,
        dest="domain_restrict",
        help="Restrict crawl to links on the same domain as start-url",
    )
    parser.add_argument(
        "--download-images",
        action="store_true",
        default=False,
        dest="download_images",
        help="Enable automatic downloading of images found in crawled pages",
    )
    parser.add_argument(
        "--media-dir",
        type=str,
        default=None,
        dest="media_dir",
        help="Directory to save downloaded images",
    )
    parser.add_argument(
        "--generate-thumbnails",
        action="store_true",
        default=False,
        dest="generate_thumbnails",
        help="Generate thumbnails for downloaded images (requires Pillow)",
    )
    parser.add_argument(
        "--thumb-dir",
        type=str,
        default=None,
        dest="thumb_dir",
        help="Directory to save generated thumbnails",
    )
    parser.add_argument(
        "--thumb-width",
        type=int,
        default=150,
        dest="thumb_width",
        help="Thumbnail width in pixels (default: 150)",
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
    res = execute_tool(
        start_url=args.start_url,
        max_depth=args.max_depth,
        max_pages=args.max_pages,
        keyword=args.keyword,
        domain_restrict=args.domain_restrict,
        download_images=args.download_images,
        media_dir=args.media_dir,
        generate_thumbnails=args.generate_thumbnails,
        thumb_dir=args.thumb_dir,
        thumb_width=args.thumb_width,
        no_color=args.no_color,
        verbose=args.verbose,
    )

    print_human_readable_ui(res, no_color=args.no_color)
    write_llm_output(res)
    sys.exit(res.get("exit_code", 0))
