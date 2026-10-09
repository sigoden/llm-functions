#!/usr/bin/env python3
"""ip_geo - Geolocation + network intel for any IP (or current public IP)."""
from __future__ import annotations

import json
import os
import socket
import sys
import time
import urllib.error
import urllib.request
from typing import Any

TOOL_NAME = "ip_geo"
VERSION = "1.1.0"
DEFAULT_FIELDS = "status,message,country,countryCode,region,regionName,city,zip,lat,lon,timezone,isp,org,as,query,proxy,hosting"

def neon(text: str, color: str = "c") -> str:
    if not sys.stderr.isatty() or os.environ.get("NO_COLOR"):
        return text
    codes = {
        "c": "\033[38;5;51m",
        "m": "\033[38;5;198m",
        "g": "\033[38;5;46m",
        "y": "\033[38;5;226m",
        "r": "\033[38;5;196m",
        "w": "\033[1;97m",
        "d": "\033[2;37m",
        "p": "\033[38;5;129m",
    }
    return f"{codes.get(color, '')}{text}\033[0m"

def pretty_box(name: str, version: str, success: bool, summary: str, rows: list[tuple[str, str]] | None = None) -> None:
    width = 74
    glyph, status, sc = ("✓", "SUCCESS", "g") if success else ("✗", "FAILED", "r")
    top = "╭" + "─" * (width - 2) + "╮"
    mid = "├" + "─" * (width - 2) + "┤"
    bot = "╰" + "─" * (width - 2) + "╯"
    print(neon(top, "p"), file=sys.stderr)
    print(
        f"│ {neon('⚡', 'c')} [{neon(name, 'c')} {neon('v' + version, 'd')}] "
        f"{neon(glyph, sc)} {neon(status, sc)} › {neon(summary, 'w')}",
        file=sys.stderr,
    )
    print(neon(mid, "p"), file=sys.stderr)
    if rows:
        for k, v in rows:
            print(f"│ {neon(k + ':', 'm'):<18} {neon(str(v), 'w')}", file=sys.stderr)
    print(neon(bot, "p"), file=sys.stderr)

def _is_valid_ip(ip: str) -> bool:
    try:
        socket.inet_pton(socket.AF_INET, ip)
        return True
    except OSError:
        try:
            socket.inet_pton(socket.AF_INET6, ip)
            return True
        except OSError:
            return False

def run(
    ip: str = "",
    fields: str = DEFAULT_FIELDS,
) -> dict[str, Any]:
    """Return geolocation and network information for an IP address.

    Args:
        ip: Target IP address. Empty string = detect current public IP.
        fields: Comma-separated list of fields to request from ip-api.com.
    """
    warnings: list[str] = []
    started = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    t0 = time.perf_counter()

    target = (ip or "").strip()
    if target and not _is_valid_ip(target):
        return {
            "success": False,
            "error": {"code": "INVALID_INPUT", "message": f"'{target}' is not a valid IPv4/IPv6 address"},
            "warnings": warnings,
            "data": None,
        }

    # Build URL (ip-api.com free tier, no key)
    base = "http://ip-api.com/json"
    url = f"{base}/{target}" if target else base
    if fields:
        url += f"?fields={fields}"

    try:
        req = urllib.request.Request(
            url,
            headers={"User-Agent": f"aichat-ip_geo/{VERSION}"},
            method="GET",
        )
        with urllib.request.urlopen(req, timeout=8) as resp:
            body = resp.read().decode("utf-8", errors="replace")
            data = json.loads(body)
    except urllib.error.HTTPError as e:
        return {
            "success": False,
            "error": {"code": "NETWORK_ERROR", "message": f"HTTP {e.code}: {e.reason}"},
            "warnings": warnings,
            "data": None,
        }
    except urllib.error.URLError as e:
        return {
            "success": False,
            "error": {"code": "NETWORK_ERROR", "message": str(e.reason)},
            "warnings": warnings,
            "data": None,
        }
    except Exception as e:
        return {
            "success": False,
            "error": {"code": "EXECUTION_ERROR", "message": str(e)},
            "warnings": warnings,
            "data": None,
        }

    duration_ms = round((time.perf_counter() - t0) * 1000, 1)
    finished = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())

    if data.get("status") == "fail":
        return {
            "success": False,
            "error": {"code": "NOT_FOUND", "message": data.get("message", "lookup failed")},
            "warnings": warnings,
            "data": data,
            "duration_ms": duration_ms,
            "started_at": started,
            "finished_at": finished,
        }

    return {
        "success": True,
        "data": data,
        "warnings": warnings,
        "error": None,
        "duration_ms": duration_ms,
        "started_at": started,
        "finished_at": finished,
        "context": {
            "source": "ip-api.com",
            "target": target or "auto-detect",
        },
    }

if __name__ == "__main__":
    import argparse

    p = argparse.ArgumentParser(description="IP geolocation + network intel")
    p.add_argument("--ip", default="", help="Target IP (empty = current public IP)")
    p.add_argument("--fields", default=DEFAULT_FIELDS, help="Comma-separated fields")
    p.add_argument("--pretty", action="store_true", help="Force neon boxed UI on stderr")
    args = p.parse_args()

    result = run(ip=args.ip, fields=args.fields)

    # Machine path – pure JSON only
    print(json.dumps(result, ensure_ascii=False))

    # Human path – neon box on stderr
    if args.pretty or sys.stderr.isatty():
        d = result.get("data") or {}
        rows = [
            ("IP", d.get("query", args.ip or "auto")),
            ("Country", f"{d.get('country', '?')} ({d.get('countryCode', '')})"),
            ("City", d.get("city", "?")),
            ("ISP / Org", f"{d.get('isp', '?')} / {d.get('org', '?')}"),
            ("ASN", d.get("as", "?")),
            ("Coords", f"{d.get('lat', '?')}, {d.get('lon', '?')}"),
            ("Duration", f"{result.get('duration_ms', 0)} ms"),
        ]
        pretty_box(
            TOOL_NAME,
            VERSION,
            bool(result.get("success")),
            "geo lookup complete" if result.get("success") else "lookup failed",
            rows,
        )
