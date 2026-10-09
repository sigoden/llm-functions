#!/usr/bin/env python3
# ==============================================================================
# web_search.py — Web Search Backend (Pyrmethus Enhanced Edition)
#
# Backends : You.com (ydc_search)
# Outputs  : json | csv | md | html | table
# ==============================================================================

from __future__ import annotations

import argparse
import csv
import datetime
import functools
import inspect
import io
import json
import re
import shutil
import sys
import time
import unicodedata
from typing import Any, Callable, Optional
from urllib.parse import urlparse, urlunparse

# ── constants ─────────────────────────────────────────────────────────────────

_VERSION = "2.1.0"
_MAX_SNIPPET = 200  # chars shown in table / md / html output
_MAX_TITLE = 80
_MAX_URL = 100
_DEFAULT_LIMIT = 10

# ── helpers & utility functions ───────────────────────────────────────────────


def _safe_int(val: Any, default: int) -> int:
    """Safely convert value to int or return default."""
    if val is None:
        return default
    try:
        return int(val)
    except (ValueError, TypeError):
        return default


def _safe_bool(val: Any) -> bool:
    """Safely convert value to bool."""
    if isinstance(val, bool):
        return val
    if isinstance(val, str):
        return val.lower() in ("true", "1", "yes", "on")
    return bool(val)


def _clean_query(query: str) -> str:
    """Strip control characters and collapse whitespace in query string."""
    s = re.sub(r"[\x00-\x1f\x7f-\x9f]", "", query or "")
    return re.sub(r"\s+", " ", s).strip()


def _str_width(s: str) -> int:
    """Calculate visible terminal column width handling East Asian wide characters."""
    return sum(2 if unicodedata.east_asian_width(c) in ("F", "W") else 1 for c in s)


def _sanitize_csv_field(text: str) -> str:
    """Prevent CSV formula injection when opening exports in spreadsheet software."""
    if not text:
        return ""
    if text[0] in ("=", "+", "-", "@", "\t", "\r"):
        return "'" + text
    return text


def _esc_md(text: str) -> str:
    """Escape Markdown control characters to prevent syntax corruption."""
    if not text:
        return ""
    chars = r"\_*[]()~`>#+-=|{}.!"
    for c in chars:
        text = text.replace(c, f"\\{c}")
    return text


# ── result envelope ───────────────────────────────────────────────────────────


def _envelope(
    results: list[dict[str, Any]],
    query: str,
    success: bool = True,
    error: Optional[str] = None,
    cached: bool = False,
    start_time: Optional[float] = None,
    **meta: Any,
) -> dict[str, Any]:
    """Every response uses a standardized schema with latency telemetry."""
    out: dict[str, Any] = {
        "success": success,
        "version": _VERSION,
        "timestamp": datetime.datetime.now(datetime.timezone.utc).isoformat(),
        "query": query,
        "count": len(results),
        "cached": cached,
        "results": results,
    }
    if start_time is not None:
        out["latency_ms"] = round((time.monotonic() - start_time) * 1000, 2)
    if error:
        out["error"] = error
    out.update(meta)
    return out


# ── retry decorator ───────────────────────────────────────────────────────────


def _retry(max_retries: int = 2, base_delay: float = 1.0):
    """Exponential back-off retry decorator preserving wrapped metadata."""

    def decorator(func: Callable):
        @functools.wraps(func)
        def wrapper(*args: Any, **kwargs: Any) -> Any:
            delay = base_delay
            last_exc: Exception = RuntimeError("no attempts")
            for attempt in range(max_retries + 1):
                try:
                    return func(*args, **kwargs)
                except Exception as exc:
                    last_exc = exc
                    if attempt < max_retries:
                        time.sleep(delay)
                        delay *= 2.0
            raise last_exc

        return wrapper

    return decorator


# ── backend fetch ─────────────────────────────────────────────────────────────


def _fetch_search_results(
    query: str,
    limit: int = _DEFAULT_LIMIT,
    include_domains: Optional[str] = None,
    exclude_domains: Optional[str] = None,
    timeout: int = 15,
    max_retries: int = 2,
    base_delay: float = 1.0,
) -> list[dict[str, Any]]:
    """Wraps ydc_search with dynamic parameter discovery and response extraction."""

    @_retry(max_retries=max_retries, base_delay=base_delay)
    def _call() -> list[dict[str, Any]]:
        try:
            from ydc_search import search_ydc  # type: ignore
        except ImportError as e:
            raise RuntimeError(
                f"ydc_search module not found. "
                f"Ensure it is installed and accessible. ({e})"
            ) from e

        kwargs: dict[str, Any] = {
            "query": query,
            "count": limit,
            "include_domains": include_domains,
            "exclude_domains": exclude_domains,
        }

        try:
            sig = inspect.signature(search_ydc)
            if "timeout" in sig.parameters:
                kwargs["timeout"] = timeout
        except (ValueError, TypeError):
            pass

        raw = search_ydc(**kwargs)

        if isinstance(raw, dict):
            raw = raw.get("results") or raw.get("hits") or raw.get("data") or []

        if not isinstance(raw, list):
            raise TypeError(
                f"search_ydc returned {type(raw).__name__}, expected list or dict with results"
            )
        return raw

    return _call()


# ── deduplication & normalisation ────────────────────────────────────────────


def _normalise(results: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Deduplicate by normalized URL and clean result keys."""
    seen: set[str] = set()
    out: list[dict[str, Any]] = []

    for r in results:
        raw_url = (r.get("url") or "").strip()
        if not raw_url:
            continue

        try:
            parsed = urlparse(raw_url)
            norm_url = urlunparse(
                (
                    parsed.scheme.lower(),
                    parsed.netloc.lower(),
                    parsed.path.rstrip("/") if parsed.path != "/" else "/",
                    parsed.params,
                    parsed.query,
                    "",  # strip fragment anchor
                )
            )
        except Exception:
            norm_url = raw_url

        if norm_url in seen:
            continue
        seen.add(norm_url)

        out.append(
            {
                "title": (r.get("title") or "").strip(),
                "url": raw_url,
                "snippet": (r.get("snippet") or r.get("description") or "").strip(),
                **{k: v for k, v in r.items() if k not in ("title", "url", "snippet")},
            }
        )
    return out


# ── formatters ────────────────────────────────────────────────────────────────


def _fmt_json(data: dict[str, Any]) -> str:
    return json.dumps(data, indent=2, ensure_ascii=False)


def _fmt_csv(results: list[dict[str, Any]]) -> str:
    buf = io.StringIO()
    writer = csv.DictWriter(
        buf,
        fieldnames=["title", "url", "snippet"],
        extrasaction="ignore",
        lineterminator="\n",
    )
    writer.writeheader()
    for r in results:
        writer.writerow(
            {
                "title": _sanitize_csv_field(r.get("title", "")[:_MAX_TITLE]),
                "url": _sanitize_csv_field(r.get("url", "")),
                "snippet": _sanitize_csv_field(r.get("snippet", "")[:_MAX_SNIPPET]),
            }
        )
    return buf.getvalue()


def _fmt_md(results: list[dict[str, Any]], query: str) -> str:
    lines = [
        f"# Search Results: {_esc_md(query)}",
        f"*{len(results)} result(s)*\n",
    ]
    for i, r in enumerate(results, 1):
        title = _esc_md(r.get("title", "Untitled"))
        url = r.get("url", "")
        snippet = _esc_md((r.get("snippet") or "")[:_MAX_SNIPPET])
        lines += [
            f"## {i}. [{title}]({url})",
            f"> {snippet}" if snippet else "",
            f"**URL:** <{url}>",
            "",
        ]
    return "\n".join(lines)


def _fmt_html(results: list[dict[str, Any]], query: str) -> str:
    def esc(s: str) -> str:
        return (
            s.replace("&", "&amp;")
            .replace("<", "&lt;")
            .replace(">", "&gt;")
            .replace('"', "&quot;")
        )

    def safe_url(u: str) -> str:
        u_lower = u.lower().strip()
        if u_lower.startswith(("http://", "https://")):
            return esc(u)
        return "#"

    items = ""
    for r in results:
        title = esc(r.get("title", "Untitled"))
        raw_u = r.get("url", "")
        url = safe_url(raw_u)
        u_disp = esc(raw_u)
        snippet = esc((r.get("snippet") or "")[:_MAX_SNIPPET])
        items += (
            f'<li class="result">'
            f'<a class="title" href="{url}">{title}</a>'
            f'<span class="url">{u_disp}</span>'
            f'<p class="snippet">{snippet}</p>'
            f"</li>\n"
        )

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Search: {esc(query)}</title>
<style>
  :root {{ --bg: #111; --text: #ddd; --accent: #bb86fc; --link: #8ab4f8; --sub: #888; --border: #2a2a2a; }}
  @media (prefers-color-scheme: light) {{
    :root {{ --bg: #fff; --text: #202124; --accent: #6200ee; --link: #1a0dab; --sub: #5f6368; --border: #ebebeb; }}
  }}
  body{{font-family:system-ui,-apple-system,sans-serif;max-width:860px;margin:40px auto;
        padding:0 20px;background:var(--bg);color:var(--text)}}
  h1{{color:var(--accent);font-size:1.4rem}}
  .meta{{color:var(--sub);font-size:.85rem;margin-bottom:1.5rem}}
  ul{{list-style:none;padding:0}}
  .result{{margin-bottom:1.6rem;border-bottom:1px solid var(--border);padding-bottom:1rem}}
  .title{{color:var(--link);font-size:1.05rem;font-weight:600;
          text-decoration:none;display:block}}
  .title:hover{{text-decoration:underline}}
  .url{{color:var(--sub);font-size:.8rem;display:block;margin:.2rem 0;word-break:break-all}}
  .snippet{{color:var(--text);opacity:0.9;font-size:.9rem;margin:0}}
</style>
</head>
<body>
<h1>&#128269; {esc(query)}</h1>
<p class="meta">{len(results)} result(s)</p>
<ul>{items}</ul>
</body>
</html>"""


def _fmt_table(results: list[dict[str, Any]]) -> str:
    if not results:
        return "No results found."

    term_width = shutil.get_terminal_size((120, 24)).columns
    max_title = min(_MAX_TITLE, max(20, int(term_width * 0.25)))
    max_url = min(_MAX_URL, max(25, int(term_width * 0.30)))
    max_snip = min(_MAX_SNIPPET, max(30, int(term_width * 0.35)))

    rows = [
        {
            "title": r.get("title", "")[:max_title],
            "url": r.get("url", "")[:max_url],
            "snippet": r.get("snippet", "")[:max_snip],
        }
        for r in results
    ]

    tw = max(_str_width("Title"), max(_str_width(r["title"]) for r in rows))
    uw = max(_str_width("URL"), max(_str_width(r["url"]) for r in rows))
    sw = max(_str_width("Snippet"), max(_str_width(r["snippet"]) for r in rows))

    sep = f"+{'':->4}+{'':->{tw + 2}}+{'':->{uw + 2}}+{'':->{sw + 2}}+"
    head = f"| {'#':>2} | {'Title':<{tw}} | {'URL':<{uw}} | {'Snippet':<{sw}} |"

    lines = [sep, head, sep]
    for i, r in enumerate(rows, 1):
        t_pad = " " * (tw - _str_width(r["title"]))
        u_pad = " " * (uw - _str_width(r["url"]))
        s_pad = " " * (sw - _str_width(r["snippet"]))
        lines.append(
            f"| {i:>2} | {r['title']}{t_pad} | {r['url']}{u_pad} | {r['snippet']}{s_pad} |"
        )
    lines.append(sep)
    return "\n".join(lines)


def _render(
    data: dict[str, Any],
    fmt: str,
    to_tty: bool,
) -> None:
    results = data.get("results", [])
    query = data.get("query", "")

    if fmt == "json":
        print(_fmt_json(data))
    elif fmt == "csv":
        print(_fmt_csv(results))
    elif fmt == "md":
        print(_fmt_md(results, query))
    elif fmt == "html":
        print(_fmt_html(results, query))
    elif not to_tty:
        print(_fmt_json(data))
    else:
        print(_fmt_table(results))


# ── public API ────────────────────────────────────────────────────────────────


def run(
    query: str,
    limit: int = _DEFAULT_LIMIT,
    *,
    include_domains: Optional[str] = None,
    exclude_domains: Optional[str] = None,
    date_filter: Optional[str] = None,
    site_filter: Optional[str] = None,
    file_type: Optional[str] = None,
    lang: Optional[str] = None,
    safe: bool = False,
    export_format: str = "json",
    timeout: int = 15,
    max_retries: int = 2,
    _print: bool = True,
) -> dict[str, Any]:
    """Perform a web search using You.com backend."""
    start_time = time.monotonic()
    cleaned_q = _clean_query(query)

    if not cleaned_q:
        data = _envelope(
            [],
            query,
            success=False,
            error="Query parameter cannot be empty",
            start_time=start_time,
        )
        if _print:
            print(_fmt_json(data))
        return data

    try:
        raw = _fetch_search_results(
            query=cleaned_q,
            limit=limit,
            include_domains=include_domains,
            exclude_domains=exclude_domains,
            timeout=timeout,
            max_retries=max_retries,
        )
    except Exception as exc:
        data = _envelope(
            [], cleaned_q, success=False, error=str(exc), start_time=start_time
        )
        if _print:
            print(_fmt_json(data))
        return data

    results = _normalise(raw)
    data = _envelope(
        results,
        cleaned_q,
        start_time=start_time,
        filters={
            "include_domains": include_domains,
            "exclude_domains": exclude_domains,
            "date_filter": date_filter,
            "site_filter": site_filter,
            "file_type": file_type,
            "lang": lang,
            "safe": safe,
        },
    )

    if _print:
        _render(data, export_format, to_tty=sys.stdout.isatty())

    return data


# ── CLI entry-point ───────────────────────────────────────────────────────────


def _parse_json_argv(raw: str) -> Optional[dict[str, Any]]:
    """Return parsed dict if raw looks like JSON, else None."""
    s = raw.strip()
    if s.startswith("{") or s.startswith("["):
        try:
            res = json.loads(s)
            return res if isinstance(res, dict) else None
        except json.JSONDecodeError:
            return None
    return None


if __name__ == "__main__":
    try:
        # ── path 1: JSON dispatch (aichat tool dispatcher) ────────────────────
        if len(sys.argv) > 1:
            kw = _parse_json_argv(sys.argv[1])
            if kw is not None:
                q = _clean_query(str(kw.get("query", "")))
                if not q:
                    print(
                        _fmt_json(
                            _envelope(
                                [],
                                "",
                                success=False,
                                error="'query' parameter is required",
                            )
                        )
                    )
                    sys.exit(1)
                try:
                    result = run(
                        query=q,
                        limit=_safe_int(kw.get("limit"), _DEFAULT_LIMIT),
                        include_domains=kw.get("include_domains"),
                        exclude_domains=kw.get("exclude_domains"),
                        date_filter=kw.get("date_filter"),
                        site_filter=kw.get("site_filter"),
                        file_type=kw.get("file_type"),
                        lang=kw.get("lang"),
                        safe=_safe_bool(kw.get("safe")),
                        export_format=str(kw.get("export_format", "json")),
                        timeout=_safe_int(kw.get("timeout"), 15),
                        max_retries=_safe_int(kw.get("max_retries"), 2),
                        _print=True,
                    )
                    sys.exit(0 if result.get("success") else 1)
                except Exception as e:
                    print(_fmt_json(_envelope([], q, success=False, error=str(e))))
                    sys.exit(1)

        # ── path 2: standard CLI ──────────────────────────────────────────────
        parser = argparse.ArgumentParser(
            description="Web search with You.com API backend",
            formatter_class=argparse.RawDescriptionHelpFormatter,
            epilog=(
                "Examples:\n"
                '  %(prog)s "python asyncio tutorial" --limit 5\n'
                '  %(prog)s "site:github.com llm tools" --export-format md\n'
                '  %(prog)s "openai api" --include-domains openai.com --export-format html\n'
            ),
        )
        parser.add_argument("query", help="Search query string")
        parser.add_argument(
            "--limit",
            type=int,
            default=_DEFAULT_LIMIT,
            help=f"Max results (default: {_DEFAULT_LIMIT})",
        )
        parser.add_argument(
            "--include-domains",
            dest="include_domains",
            default=None,
            help="Comma-separated domains to include",
        )
        parser.add_argument(
            "--exclude-domains",
            dest="exclude_domains",
            default=None,
            help="Comma-separated domains to exclude",
        )
        parser.add_argument(
            "--export-format",
            dest="export_format",
            choices=["json", "csv", "md", "html", "table"],
            default="table",
            help="Output format (default: table)",
        )
        parser.add_argument(
            "--max-retries",
            dest="max_retries",
            type=int,
            default=2,
            help="HTTP retry count (default: 2)",
        )
        parser.add_argument(
            "--safe",
            action="store_true",
            default=False,
            help="Enable safe-search filter",
        )
        parser.add_argument(
            "--lang", default=None, help="Language/locale hint (e.g. en-US)"
        )
        parser.add_argument(
            "--version", action="version", version=f"%(prog)s {_VERSION}"
        )

        ns = parser.parse_args()
        result = run(
            query=ns.query,
            limit=ns.limit,
            include_domains=ns.include_domains,
            exclude_domains=ns.exclude_domains,
            export_format=ns.export_format,
            max_retries=ns.max_retries,
            safe=ns.safe,
            lang=ns.lang,
            _print=True,
        )
        sys.exit(0 if result.get("success") else 1)

    except KeyboardInterrupt:
        print(
            _fmt_json(
                _envelope([], "", success=False, error="Search cancelled by user")
            )
        )
        sys.exit(130)
    except Exception as e:
        print(_fmt_json(_envelope([], "", success=False, error=str(e))))
        sys.exit(1)
