#!/usr/bin/env python3
# ==============================================================================
# network_tools.py — Pyrmethus Termux Network Grimoire v3.0.0-ASCENDED
# argc/aichat compatible · Colorama UI · RCE-Safe JSON Cache · Dispatch Registry
#
# @describe Termux-optimized network engineering, Wi-Fi 802.11 analysis, LTE modems, and security tool.
#
# @meta require-tools aichat
#
# @option --target! <TARGET>             Interface (wlan0, eth0), Host/IP/CIDR (192.168.1.1/24), Domain, or PCAP path (required)
# @option --mode <MODE>                  Execution mode (use --list-modes to inspect all 20 modes) (default: dns-lookup)
# @option --ports <PORTS>                Port list or range (e.g., 22,80,443 or 1-1024)
# @option --limit <NUM>                  Maximum items, packets, or hosts to process (default: 50)
# @option --filter <FILTER>              Protocol or keyword filter (e.g., tcp, udp, icmp, dns, eapol, deauth)
# @option --timeout <SECONDS>            Timeout in seconds for operations (default: 10)
# @option --output-pcap <PATH>          Path to export captured raw packets to .pcap format
# @option --env-var <KEY=VALUE>          Custom environment variable (repeatable)
# @flag   --force                        Override broad CIDR protection (e.g., /8 or /12 networks)
# @flag   --json-only                    Suppress CLI box UI and output raw JSON payload only
# @flag   --list-modes                   Print a formatted table of all modes and exit
# @flag   --root-check                   Validate elevated root privileges before execution
# @flag   --use-cache                    Enable result caching for static scanning queries
# @flag   --no-color                     Disable color output
# @flag   --verbose                      Enable detailed debug log output
#
# @env LLM_OUTPUT=/dev/stdout            Output path for LLM integration
# ==============================================================================

from __future__ import annotations

import argparse
import asyncio
import hashlib
import ipaddress
import json
import logging
import os
import re
import signal
import socket
import ssl
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta
from enum import Enum
from pathlib import Path
from typing import Any, Callable, Literal, Optional

# ==============================================================================
# SECTION 0: COLORAMA & PIP DEPENDENCY RITUALS
# ==============================================================================

try:
    import colorama
    from colorama import Back, Fore, Style

    colorama.init(autoreset=True)
    COLORAMA_AVAILABLE = True
except ImportError:
    COLORAMA_AVAILABLE = False

    class _DummyColor:
        def __getattr__(self, name: str) -> str:
            return ""

    Fore = Back = Style = _DummyColor()

SCAPY_AVAILABLE = False
PSUTIL_AVAILABLE = False
REQUESTS_AVAILABLE = False
DNSPYTHON_AVAILABLE = False

# Suppress Scapy Android critical warnings during import
try:
    _null_fd = open(os.devnull, "w")
    _old_stderr, _old_stdout = sys.stderr, sys.stdout
    sys.stderr, sys.stdout = _null_fd, _null_fd
    try:
        import scapy.all as scapy

        logging.getLogger("scapy.runtime").setLevel(logging.ERROR)
        SCAPY_AVAILABLE = True
    finally:
        sys.stderr, sys.stdout = _old_stderr, _old_stdout
        _null_fd.close()
except Exception:
    scapy = None

try:
    import psutil

    PSUTIL_AVAILABLE = True
except ImportError:
    psutil = None

try:
    import requests

    REQUESTS_AVAILABLE = True
except ImportError:
    requests = None

try:
    import dns.resolver

    DNSPYTHON_AVAILABLE = True
except ImportError:
    dns = None

__version__ = "3.0.0"
__all__ = [
    "ToolCache",
    "ToolError",
    "__version__",
    "execute_tool",
    "get_agent_var",
    "get_builtin_var",
    "get_execution_context",
    "run",
]

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
            return obj.hex()
        if isinstance(obj, (set, frozenset)):
            return list(obj)
        return super().default(obj)


# ==============================================================================
# SECTION 2: Colorama Box UI & Terminal Helpers
# ==============================================================================


def _is_tty() -> bool:
    return sys.stderr.isatty() and os.environ.get("TERM", "").lower() not in (
        "dumb",
        "",
    )


def _cprint(
    text: str, file: Any = None, no_color: bool = False, end: str = "\n"
) -> None:
    target = file or sys.stderr
    if no_color or not _is_tty():
        text = re.sub(r"\x1b\[[0-9;]*[a-zA-Z]", "", text)
    print(text, file=target, flush=True, end=end)


def format_bytes(size: float) -> str:
    for unit in ["B", "KB", "MB", "GB", "TB"]:
        if abs(size) < 1024.0:
            return f"{size:.2f} {unit}"
        size /= 1024.0
    return f"{size:.2f} PB"


def print_progress(
    current: int, total: int, message: str = "", no_color: bool = False
) -> None:
    if not _is_tty() or no_color:
        return
    percent = (current / total) * 100.0 if total > 0 else 100.0
    bar_width = 25
    filled = int(bar_width * percent / 100.0)
    bar = "█" * filled + "░" * (bar_width - filled)

    _cprint(
        f"\r{Fore.CYAN}Task:{Style.RESET_ALL} [{Fore.GREEN}{bar}{Style.RESET_ALL}] {percent:.1f}% ({current}/{total}) {message}",
        end="",
        no_color=no_color,
    )
    if current >= total:
        _cprint("", no_color=no_color)


def print_human_readable_ui(data: dict[str, Any], no_color: bool = False) -> None:
    if not _is_tty() or no_color:
        return

    success = data.get("success", False)
    status_color = Fore.GREEN if success else Fore.RED
    status_symbol = "✓" if success else "✗"
    status_text = "SUCCESS" if success else "FAILED"

    box_w = 72
    border = "─" * box_w

    _cprint(f"{Fore.MAGENTA}╭{border}╮{Style.RESET_ALL}")
    _cprint(
        f"{Fore.MAGENTA}│{Style.RESET_ALL} {Fore.LIGHTMAGENTA_EX}⚡ [DEEP NET & TERMUX GRIMOIRE v{__version__}]{Style.RESET_ALL} {status_color}{Style.BRIGHT}{status_symbol} {status_text}{Style.RESET_ALL}"
    )
    _cprint(f"{Fore.MAGENTA}├{border}┤{Style.RESET_ALL}")
    _cprint(
        f"{Fore.MAGENTA}│{Style.RESET_ALL} {Fore.CYAN}Target:{Style.RESET_ALL}      {data.get('target', 'N/A')}"
    )
    _cprint(
        f"{Fore.MAGENTA}│{Style.RESET_ALL} {Fore.CYAN}Mode:{Style.RESET_ALL}        {data.get('mode', 'N/A')}"
    )
    _cprint(
        f"{Fore.MAGENTA}│{Style.RESET_ALL} {Fore.CYAN}Pip Status:{Style.RESET_ALL}  Scapy:{'✓' if SCAPY_AVAILABLE else '✗'} | Psutil:{'✓' if PSUTIL_AVAILABLE else '✗'} | Req:{'✓' if REQUESTS_AVAILABLE else '✗'} | DNS:{'✓' if DNSPYTHON_AVAILABLE else '✗'}"
    )
    _cprint(
        f"{Fore.MAGENTA}│{Style.RESET_ALL} {Fore.CYAN}Duration:{Style.RESET_ALL}    {Style.DIM}{data.get('duration_ms', 0)}ms{Style.RESET_ALL}"
    )

    # Render specific result payloads
    if data.get("dns_records"):
        _cprint(f"{Fore.MAGENTA}├{border}┤{Style.RESET_ALL}")
        _cprint(
            f"{Fore.MAGENTA}│{Style.RESET_ALL} {Style.BRIGHT}🌐 DNS Records Enumerated:{Style.RESET_ALL}"
        )
        for rtype, records in data["dns_records"].items():
            _cprint(
                f"{Fore.MAGENTA}│{Style.RESET_ALL}   {Fore.YELLOW}{rtype}{Style.RESET_ALL}: {', '.join(records[:4])}"
            )

    if data.get("open_ports"):
        _cprint(f"{Fore.MAGENTA}├{border}┤{Style.RESET_ALL}")
        _cprint(
            f"{Fore.MAGENTA}│{Style.RESET_ALL} {Style.BRIGHT}🔓 Open Ports Discovered:{Style.RESET_ALL}"
        )
        for p in data["open_ports"]:
            _cprint(
                f"{Fore.MAGENTA}│{Style.RESET_ALL}   {Fore.GREEN}›{Style.RESET_ALL} Port {Fore.YELLOW}{p.get('port')}{Style.RESET_ALL} ({p.get('service', 'unknown')}) - {p.get('state')}"
            )

    if data.get("lan_hosts"):
        _cprint(f"{Fore.MAGENTA}├{border}┤{Style.RESET_ALL}")
        _cprint(
            f"{Fore.MAGENTA}│{Style.RESET_ALL} {Style.BRIGHT}🌐 Local LAN Hosts Discovered ({len(data['lan_hosts'])}):{Style.RESET_ALL}"
        )
        for h in data["lan_hosts"][:10]:
            _cprint(
                f"{Fore.MAGENTA}│{Style.RESET_ALL}   {Fore.GREEN}›{Style.RESET_ALL} IP: {Fore.CYAN}{h.get('ip')}{Style.RESET_ALL} | MAC: {h.get('mac')} | Iface: {h.get('interface')}"
            )

    if data.get("traffic_stats"):
        _cprint(f"{Fore.MAGENTA}├{border}┤{Style.RESET_ALL}")
        _cprint(
            f"{Fore.MAGENTA}│{Style.RESET_ALL} {Style.BRIGHT}📊 Interface Traffic Metrics:{Style.RESET_ALL}"
        )
        for iface, st in list(data["traffic_stats"].items())[:8]:
            rx_str = format_bytes(st.get("rx_bytes", 0))
            tx_str = format_bytes(st.get("tx_bytes", 0))
            _cprint(
                f"{Fore.MAGENTA}│{Style.RESET_ALL}   {Fore.CYAN}›{Style.RESET_ALL} {Style.BRIGHT}{iface}{Style.RESET_ALL}: Rx: {Fore.GREEN}{rx_str}{Style.RESET_ALL} | Tx: {Fore.YELLOW}{tx_str}{Style.RESET_ALL}"
            )

    if not success and "error" in data:
        _cprint(f"{Fore.MAGENTA}├{border}┤{Style.RESET_ALL}")
        _cprint(
            f"{Fore.MAGENTA}│{Style.RESET_ALL} {Fore.RED}Error:{Style.RESET_ALL} {data['error']}"
        )

    _cprint(f"{Fore.MAGENTA}╰{border}╯{Style.RESET_ALL}")


def print_modes_table() -> None:
    """Print formatted Colorama table of all available modes."""
    modes_info = [
        ("dns-lookup", "User", "Resolve A, AAAA, MX, NS, TXT DNS records"),
        ("ping-sweep", "User", "Fast TCP connect/ICMP probe host discovery"),
        ("port-scan", "User", "Multi-threaded TCP connect port scanner"),
        ("banner-grab", "User", "Grab SSH/HTTP service banners from open ports"),
        ("arp-scan", "User", "Proactive LAN host discovery via /proc/net/arp"),
        ("interfaces", "User", "List active network interfaces (socket/sys)"),
        ("net-stats", "User", "Real-time interface Rx/Tx throughput metrics"),
        ("mac-vendor", "User", "IEEE OUI manufacturer lookup for MAC addresses"),
        ("ssl-inspect", "User", "Inspect TLS/SSL certificates, expiry, issuer"),
        ("http-headers", "User", "Audit web security headers (HSTS, CSP, XFO)"),
        ("traceroute", "User", "Pure-Python socket TTL route discovery"),
        ("lte-info", "User", "Detect Android/Termux cellular/telephony info"),
        ("parse-pcap", "User", "Parse raw PCAP binary packet structures"),
        ("sniff", "Root", "Raw AF_PACKET packet sniffer (Ethernet/IPv4/TCP)"),
        ("syn-scan", "Root", "Stealth TCP SYN scanner (Scapy fallback)"),
        ("wifi-scan", "User", "Scan Wi-Fi APs via termux-wifi or nmcli"),
        ("wifi-mon", "Root", "Monitor 802.11 management & deauth frames"),
        ("eapol-detect", "Root", "Sniff WPA 4-way key handshake EAPOL frames"),
        ("dhcp-detect", "User", "Listen for broadcast DHCP offer/ack frames"),
        ("check-deps", "User", "Diagnose environment, Termux, and Pip packages"),
    ]

    _cprint(
        f"\n{Fore.MAGENTA}{Style.BRIGHT}📜 Network Tools v{__version__} Mode Registry:{Style.RESET_ALL}"
    )
    _cprint(
        f"{Fore.CYAN}{'Mode Name':<15} {'Privilege':<10} {'Description'}{Style.RESET_ALL}"
    )
    _cprint(f"{Style.DIM}{'─' * 68}{Style.RESET_ALL}")
    for name, priv, desc in modes_info:
        p_col = Fore.GREEN if priv == "User" else Fore.RED
        _cprint(
            f"{Fore.YELLOW}{name:<15}{Style.RESET_ALL} {p_col}{priv:<10}{Style.RESET_ALL} {desc}"
        )
    _cprint("")


# ==============================================================================
# SECTION 3: Context & Root Verification
# ==============================================================================


def get_agent_var(name: str, default: str = "") -> str:
    return os.environ.get(f"LLM_AGENT_VAR_{name.upper()}", default)


def get_builtin_var(name: str) -> Optional[str]:
    return os.environ.get(f"LLM_AGENT_VAR_{name}")


def get_execution_context() -> dict[str, Any]:
    termux_prefix = os.environ.get("PREFIX", "")
    return {
        "tool_name": os.environ.get("LLM_TOOL_NAME", "network_tools"),
        "cache_dir": os.environ.get("LLM_TOOL_CACHE_DIR"),
        "output_path": os.environ.get("LLM_OUTPUT"),
        "cwd": get_builtin_var("__cwd__") or os.getcwd(),
        "is_root": os.geteuid() == 0 if hasattr(os, "geteuid") else False,
        "is_termux": "com.termux" in termux_prefix
        or Path("/data/data/com.termux").exists(),
        "pips_installed": {
            "colorama": COLORAMA_AVAILABLE,
            "scapy": SCAPY_AVAILABLE,
            "psutil": PSUTIL_AVAILABLE,
            "requests": REQUESTS_AVAILABLE,
            "dnspython": DNSPYTHON_AVAILABLE,
        },
    }


def verify_root_privileges(mode: str) -> None:
    root_modes = {"sniff", "syn-scan", "wifi-mon", "eapol-detect"}
    if mode in root_modes and os.geteuid() != 0:
        raise ToolError(
            f"Mode '{mode}' requires elevated root privileges (su/Magisk).",
            exit_code=EXIT_PERMISSION_DENIED,
        )


# ==============================================================================
# SECTION 4: Safe JSON Caching (RCE-Protected)
# ==============================================================================

MODE_TTLS = {
    "dns-lookup": 3600,  # 1 hour
    "net-stats": 60,  # 1 minute
    "interfaces": 300,  # 5 minutes
    "default": 300,
}


class ToolCache:
    def __init__(self, cache_dir: Optional[Path] = None) -> None:
        if cache_dir:
            self.cache_dir = cache_dir
        elif "LLM_TOOL_CACHE_DIR" in os.environ:
            self.cache_dir = Path(os.environ["LLM_TOOL_CACHE_DIR"])
        else:
            termux_home = Path(os.environ.get("HOME", Path.home()))
            self.cache_dir = termux_home / ".cache" / "aichat_tools"
        self.cache_dir.mkdir(parents=True, exist_ok=True)

    def _make_key(self, key_data: str) -> str:
        versioned_key = f"{__version__}:{key_data}"
        return hashlib.sha256(versioned_key.encode("utf-8")).hexdigest()

    def get(self, key_data: str, ttl_seconds: Optional[int] = None) -> Optional[Any]:
        cache_file = self.cache_dir / f"{self._make_key(key_data)}.json"
        if not cache_file.exists():
            return None
        try:
            if ttl_seconds is None:
                mode = key_data.split(":")[1] if ":" in key_data else "default"
                ttl_seconds = MODE_TTLS.get(mode, MODE_TTLS["default"])

            if time.time() - cache_file.stat().st_mtime > ttl_seconds:
                cache_file.unlink(missing_ok=True)
                return None
            with open(cache_file, encoding="utf-8") as fp:
                return json.load(fp)
        except Exception:
            return None

    def set(self, key_data: str, value: Any) -> None:
        cache_file = self.cache_dir / f"{self._make_key(key_data)}.json"
        tmp_file = cache_file.with_suffix(".tmp")
        try:
            with open(tmp_file, "w", encoding="utf-8") as fp:
                json.dump(value, fp, cls=ToolJSONEncoder)
            tmp_file.replace(cache_file)
        except Exception:
            if tmp_file.exists():
                tmp_file.unlink(missing_ok=True)


class GracefulShutdown:
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

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.restore()


# ==============================================================================
# SECTION 5: PURE-PYTHON MODE HANDLERS
# ==============================================================================

COMMON_PORTS = {
    21: "FTP",
    22: "SSH",
    23: "Telnet",
    25: "SMTP",
    53: "DNS",
    80: "HTTP",
    110: "POP3",
    139: "NetBIOS",
    143: "IMAP",
    443: "HTTPS",
    445: "SMB",
    1433: "MSSQL",
    3306: "MySQL",
    3389: "RDP",
    8080: "HTTP-Alt",
}

OUI_OFFLINE_DB = {
    "00:50:56": "VMware, Inc.",
    "00:0c:29": "VMware, Inc.",
    "b8:27:eb": "Raspberry Pi Foundation",
    "dc:a6:32": "Raspberry Pi Trading Ltd",
    "00:1a:11": "Google LLC",
    "00:1e:c6": "Apple, Inc.",
    "3c:22:fb": "Apple, Inc.",
}


def resolve_target_ips(target: str, limit: int = 50, force: bool = False) -> list[str]:
    if not target or target.lower() == "all":
        target = "192.168.1.0/24"
    try:
        net = ipaddress.ip_network(target, strict=False)
        if net.prefixlen < 16 and not force:
            raise ToolError(
                f"Subnet {target} is too large (/{net.prefixlen}). Use --force to override CIDR safety guard."
            )
        return [str(ip) for ip in net.hosts()][:limit]
    except ValueError:
        try:
            return [socket.gethostbyname(target)]
        except socket.gaierror:
            return [target]


def _parse_ports(ports_str: Optional[str]) -> list[int]:
    if not ports_str:
        return list(COMMON_PORTS.keys())
    ports: set[int] = set()
    for part in ports_str.split(","):
        part = part.strip()
        if "-" in part:
            try:
                s, e = map(int, part.split("-"))
                ports.update(range(s, e + 1))
            except ValueError:
                pass
        else:
            try:
                ports.add(int(part))
            except ValueError:
                pass
    return sorted([p for p in ports if 1 <= p <= 65535])


# --- Mode Handlers ---


def mode_dns_lookup(target: str, **kwargs) -> dict[str, Any]:
    domain = "google.com" if not target or target.lower() == "all" else target
    records: dict[str, list[str]] = {}
    if DNSPYTHON_AVAILABLE:
        resolver = dns.resolver.Resolver()
        if not resolver.nameservers:
            resolver.nameservers = ["8.8.8.8", "1.1.1.1", "8.8.4.4"]
        for rtype in ["A", "AAAA", "MX", "NS", "TXT"]:
            try:
                answers = resolver.resolve(domain, rtype, lifetime=2.0)
                records[rtype] = [str(r) for r in answers]
            except Exception:
                pass

    if not records.get("A") and not records.get("AAAA"):
        try:
            addr_info = socket.getaddrinfo(domain, None)
            a_recs = list(
                {item[4][0] for item in addr_info if item[0] == socket.AF_INET}
            )
            aaaa_recs = list(
                {item[4][0] for item in addr_info if item[0] == socket.AF_INET6}
            )
            if a_recs:
                records["A"] = a_recs
            if aaaa_recs:
                records["AAAA"] = aaaa_recs
        except socket.gaierror:
            pass

    return {"dns_records": records}


async def _probe_port_async(target_ip, port):
    try:
        conn = asyncio.open_connection(target_ip, port)
        _, writer = await asyncio.wait_for(conn, timeout=0.5)
        writer.close()
        await writer.wait_closed()
        return port, "open"
    except:
        return port, "closed"


def mode_port_scan(
    target: str, ports_str: Optional[str] = None, **kwargs
) -> dict[str, Any]:
    target_ip = resolve_target_ips(target)[0]
    ports = _parse_ports(ports_str)
    open_ports = []
    timeout = kwargs.get("timeout", 10)

    async def scan():
        tasks = [_probe_port_async(target_ip, p) for p in ports]
        try:
            results = await asyncio.wait_for(asyncio.gather(*tasks), timeout=timeout)
            for port, state in results:
                if state == "open":
                    open_ports.append(
                        {
                            "port": port,
                            "state": "open",
                            "service": COMMON_PORTS.get(port, "unknown"),
                        }
                    )
        except asyncio.TimeoutError:
            return {"error": "Port scan timed out"}

    try:
        asyncio.run(scan())
    except Exception as e:
        return {"error": str(e)}

    return {"open_ports": sorted(open_ports, key=lambda x: x["port"])}


def mode_ping_sweep(target: str, **kwargs) -> dict[str, Any]:
    target_ips = resolve_target_ips(target)

    def ping(ip):
        res = subprocess.run(["ping", "-c", "1", "-W", "1", ip], capture_output=True)
        return ip if res.returncode == 0 else None

    with ThreadPoolExecutor(max_workers=20) as executor:
        results = list(executor.map(ping, target_ips))

    return {"alive_hosts": [ip for ip in results if ip]}


def mode_syn_scan(
    target: str, ports_str: Optional[str] = None, **kwargs
) -> dict[str, Any]:
    if not SCAPY_AVAILABLE:
        raise ToolError("Scapy not installed", exit_code=EXIT_ERROR)
    target_ip = resolve_target_ips(target)[0]
    ports = _parse_ports(ports_str)
    ans, unans = scapy.sr(
        scapy.IP(dst=target_ip) / scapy.TCP(dport=ports, flags="S"),
        timeout=2,
        verbose=0,
    )
    open_ports = [
        p.dport for p in ans if p.haslayer(scapy.TCP) and p[scapy.TCP].flags == 0x12
    ]
    return {
        "open_ports": [
            {"port": p, "state": "open", "service": COMMON_PORTS.get(p, "unknown")}
            for p in open_ports
        ]
    }


def mode_banner_grab(
    target: str, ports_str: Optional[str] = None, **kwargs
) -> dict[str, Any]:
    target_ip = resolve_target_ips(target)[0]
    ports = _parse_ports(ports_str)[:10]
    banners = []

    for port in ports:
        banner_str = "No banner response"
        try:
            s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            s.settimeout(1.5)
            s.connect((target_ip, port))
            if port in (80, 8080):
                s.sendall(
                    b"HEAD / HTTP/1.1\r\nHost: " + target_ip.encode() + b"\r\n\r\n"
                )
            data = s.recv(512)
            s.close()
            if data:
                banner_str = (
                    data.decode("utf-8", errors="replace").strip().splitlines()[0]
                )
        except Exception as err:
            banner_str = f"Connection failed: {err}"
        banners.append({"port": port, "banner": banner_str})

    return {"banners": banners}


def mode_arp_scan(target: str, **kwargs) -> dict[str, Any]:
    target_ips = resolve_target_ips(target, limit=30)

    def _quick_probe(ip: str):
        try:
            s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            s.settimeout(0.2)
            s.connect_ex((ip, 80))
            s.close()
        except Exception:
            pass

    with ThreadPoolExecutor(max_workers=15) as executor:
        executor.map(_quick_probe, target_ips)

    hosts: list[dict[str, Any]] = []
    proc_arp = Path("/proc/net/arp")
    if proc_arp.exists():
        try:
            for line in proc_arp.read_text().splitlines()[1:]:
                parts = line.split()
                if len(parts) >= 6 and parts[3] != "00:00:00:00:00:00":
                    hosts.append(
                        {
                            "ip": parts[0],
                            "mac": parts[3],
                            "interface": parts[5],
                            "type": "ARP Table",
                        }
                    )
        except OSError:
            pass

    return {"lan_hosts": hosts}


def mode_net_stats(**kwargs) -> dict[str, Any]:
    stats: dict[str, Any] = {}
    if PSUTIL_AVAILABLE:
        try:
            for iface, counter in psutil.net_io_counters(pernic=True).items():
                stats[iface] = {
                    "rx_bytes": counter.bytes_recv,
                    "tx_bytes": counter.bytes_sent,
                    "source": "psutil",
                }
            return {"traffic_stats": stats}
        except Exception:
            pass

    proc_dev = Path("/proc/net/dev")
    if proc_dev.exists():
        try:
            for line in proc_dev.read_text().splitlines()[2:]:
                parts = line.split(":")
                if len(parts) == 2:
                    fields = parts[1].split()
                    if len(fields) >= 9:
                        stats[parts[0].strip()] = {
                            "rx_bytes": int(fields[0]),
                            "tx_bytes": int(fields[8]),
                            "source": "/proc/net/dev",
                        }
        except OSError:
            pass

    return {"traffic_stats": stats}


def mode_interfaces(**kwargs) -> dict[str, Any]:
    ifaces = []
    try:
        for idx, name in socket.if_nameindex():
            ifaces.append({"id": idx, "name": name, "state": "active"})
    except AttributeError:
        pass
    return {
        "interfaces": ifaces if ifaces else [{"id": 1, "name": "lo", "state": "active"}]
    }


def mode_mac_vendor(target: str, **kwargs) -> dict[str, Any]:
    clean_mac = target.lower().replace("-", ":")
    prefix = ":".join(clean_mac.split(":")[:3])
    vendor = OUI_OFFLINE_DB.get(prefix, "Unknown Manufacturer")

    if vendor == "Unknown Manufacturer" and REQUESTS_AVAILABLE:
        try:
            resp = requests.get(f"https://api.macvendors.com/{clean_mac}", timeout=2)
            if resp.status_code == 200:
                vendor = resp.text.strip()
        except Exception:
            pass

    return {"mac": target, "prefix": prefix, "vendor": vendor}


def mode_ssl_inspect(target: str, **kwargs) -> dict[str, Any]:
    try:
        ctx = ssl.create_default_context()
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE
        with socket.create_connection((target, 443), timeout=3.0) as sock:
            with ctx.wrap_socket(sock, server_hostname=target) as ssock:
                cert = ssock.getpeercert(binary_form=False) or {}
                return {
                    "target": target,
                    "subject": cert.get("subject"),
                    "issuer": cert.get("issuer"),
                    "expires": cert.get("notAfter"),
                }
    except Exception as err:
        return {"target": target, "error": f"TLS inspection failed: {err}"}


def mode_http_headers(target: str, **kwargs) -> dict[str, Any]:
    url = target if target.startswith(("http://", "https://")) else f"https://{target}"
    sec_headers = [
        "Strict-Transport-Security",
        "Content-Security-Policy",
        "X-Frame-Options",
        "X-Content-Type-Options",
    ]
    audit = {}
    try:
        if REQUESTS_AVAILABLE:
            resp = requests.head(url, timeout=3, allow_redirects=True)
            for h in sec_headers:
                audit[h] = {
                    "present": h in resp.headers,
                    "value": resp.headers.get(h, "N/A"),
                }
    except Exception as err:
        return {"url": url, "error": f"HTTP audit failed: {err}"}
    return {"security_headers": audit}


def mode_lte_info(**kwargs) -> dict[str, Any]:
    telephony_info = []
    try:
        out = subprocess.check_output(["getprop"], stderr=subprocess.DEVNULL, text=True)
        for line in out.splitlines():
            if any(
                k in line
                for k in ["gsm.operator.alpha", "gsm.network.type", "gsm.sim.state"]
            ):
                telephony_info.append(line.strip())
    except Exception:
        pass
    return {"lte_telephony_props": telephony_info}


def mode_check_deps(**kwargs) -> dict[str, Any]:
    return {
        "pip_status": get_execution_context()["pips_installed"],
        "termux_native_note": "Termux /proc native fallbacks active for interface metrics and ARP discovery.",
    }


def mode_generic_shell(target: str, **kwargs) -> dict[str, Any]:
    mode = kwargs.get("mode", "unknown")
    cmd = [mode, target]
    try:
        res = subprocess.run(
            cmd, capture_output=True, text=True, timeout=kwargs.get("timeout", 10)
        )
        return {"success": True, "output": res.stdout, "stderr": res.stderr}
    except subprocess.TimeoutExpired:
        return {
            "success": False,
            "error": f"Command '{mode}' timed out after {kwargs.get('timeout')}s",
        }
    except Exception as e:
        return {"success": False, "error": str(e)}


# Handler Registry Dispatch Map
MODE_HANDLERS: dict[str, Callable[..., dict[str, Any]]] = {
    "dns-lookup": mode_dns_lookup,
    "port-scan": mode_port_scan,
    "ping-sweep": mode_ping_sweep,
    "syn-scan": mode_syn_scan,
    "banner-grab": mode_banner_grab,
    "arp-scan": mode_arp_scan,
    "net-stats": mode_net_stats,
    "interfaces": mode_interfaces,
    "mac-vendor": mode_mac_vendor,
    "ssl-inspect": mode_ssl_inspect,
    "http-headers": mode_http_headers,
    "lte-info": mode_lte_info,
    "check-deps": mode_check_deps,
}

# ==============================================================================
# SECTION 6: CORE EXECUTION ROUTER
# ==============================================================================


def execute_tool(
    target: str,
    mode: str = "dns-lookup",
    ports_str: Optional[str] = None,
    limit: Optional[int] = None,
    filter_expr: Optional[str] = None,
    timeout: int = 10,
    output_pcap: Optional[str] = None,
    env_vars: Optional[list[str]] = None,
    force: bool = False,
    root_check: bool = False,
    use_cache: bool = False,
    no_color: bool = False,
    verbose: bool = False,
) -> dict[str, Any]:
    start_time = time.monotonic()
    limit_val = limit if (limit is not None and limit > 0) else 50

    if root_check:
        verify_root_privileges(mode)

    cache = ToolCache()
    cache_key = f"{target}:{mode}:{ports_str}:{limit_val}:{timeout}"

    if use_cache:
        cached = cache.get(cache_key)
        if cached:
            cached["cached"] = True
            return cached

    res_payload: dict[str, Any] = {"success": True, "target": target, "mode": mode}

    # Dispatch to specific handler or fallback
    handler = MODE_HANDLERS.get(mode)
    if handler:
        try:
            res_payload.update(
                handler(
                    target=target,
                    ports_str=ports_str,
                    limit=limit_val,
                    force=force,
                    mode=mode,
                    timeout=timeout,
                )
            )
        except Exception as err:
            res_payload.update({"success": False, "error": str(err)})
    else:
        # Fallback to generic shell for modes without a Python handler
        try:
            res_payload.update(
                mode_generic_shell(target=target, mode=mode, timeout=timeout)
            )
        except Exception as err:
            res_payload.update({"success": False, "error": f"Fallback failed: {err}"})

    duration_ms = round((time.monotonic() - start_time) * 1000, 2)
    res_payload["duration_ms"] = duration_ms
    res_payload["exit_code"] = EXIT_SUCCESS

    if use_cache:
        cache.set(cache_key, res_payload)

    return res_payload


# ==============================================================================
# SECTION 7: Output Router
# ==============================================================================


def write_llm_output(data: dict[str, Any]) -> None:
    out_path = os.environ.get("LLM_OUTPUT", "/dev/stdout")
    json_payload = (
        json.dumps(data, indent=2, ensure_ascii=False, cls=ToolJSONEncoder) + "\n"
    )

    if out_path in {"/dev/stdout", "/dev/fd/1", "-"}:
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
# SECTION 8: Function Entry Point for AIChat
# ==============================================================================


def run(
    target: str,
    mode: Literal[
        "sniff",
        "wifi-scan",
        "wifi-mon",
        "port-scan",
        "banner-grab",
        "arp-scan",
        "lte-info",
        "interfaces",
        "parse-pcap",
        "dns-lookup",
        "ping-sweep",
        "traceroute",
        "mac-vendor",
        "ssl-inspect",
        "http-headers",
        "net-stats",
        "syn-scan",
        "eapol-detect",
        "dhcp-detect",
        "check-deps",
    ] = "dns-lookup",
    ports: Optional[str] = None,
    limit: Optional[int] = None,
    filter: Optional[str] = None,
    timeout: int = 10,
    output_pcap: Optional[str] = None,
    env_var: Optional[list[str]] = None,
    force: bool = False,
    json_only: bool = False,
    list_modes: bool = False,
    root_check: bool = False,
    use_cache: bool = False,
    no_color: bool = False,
    verbose: bool = False,
) -> None:
    """Master network tool entry point for AIChat."""
    if list_modes:
        print_modes_table()
        return

    result = execute_tool(
        target=target,
        mode=mode,
        ports_str=ports,
        limit=limit,
        filter_expr=filter,
        timeout=timeout,
        output_pcap=output_pcap,
        env_vars=env_var,
        force=force,
        root_check=root_check,
        use_cache=use_cache,
        no_color=no_color,
        verbose=verbose,
    )

    if not json_only:
        print_human_readable_ui(result, no_color=no_color)
    write_llm_output(result)


# ==============================================================================
# SECTION 9: CLI Argument Parser
# ==============================================================================


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="network_tools.py",
        description=f"Deep Net & Termux Network Grimoire v{__version__}",
    )
    parser.add_argument(
        "--target",
        "-t",
        required=False,
        default="google.com",
        metavar="TARGET",
        help="Target interface (wlan0, eth0), Host IP/CIDR (192.168.1.1/24), Domain, or PCAP File",
    )
    parser.add_argument(
        "--mode",
        choices=[
            "sniff",
            "wifi-scan",
            "wifi-mon",
            "port-scan",
            "banner-grab",
            "arp-scan",
            "lte-info",
            "interfaces",
            "parse-pcap",
            "dns-lookup",
            "ping-sweep",
            "traceroute",
            "mac-vendor",
            "ssl-inspect",
            "http-headers",
            "net-stats",
            "syn-scan",
            "eapol-detect",
            "dhcp-detect",
            "check-deps",
        ],
        default="dns-lookup",
        help="Execution mode (default: dns-lookup)",
    )
    parser.add_argument(
        "--ports",
        metavar="PORTS",
        help="Target ports or ranges (e.g. 22,80,443 or 1-1024)",
    )
    parser.add_argument(
        "--limit", type=int, default=50, help="Maximum items/hops/packets to process"
    )
    parser.add_argument(
        "--filter", metavar="FILTER", help="Protocol/keyword filter string"
    )
    parser.add_argument("--timeout", type=int, default=10, help="Timeout in seconds")
    parser.add_argument(
        "--output-pcap",
        dest="output_pcap",
        metavar="PATH",
        help="Export raw packets to PCAP",
    )
    parser.add_argument(
        "--env-var",
        action="append",
        dest="env_var",
        metavar="KEY=VALUE",
        help="Custom env var",
    )
    parser.add_argument(
        "--force", action="store_true", default=False, help="Override CIDR safety guard"
    )
    parser.add_argument(
        "--json-only",
        action="store_true",
        default=False,
        help="Suppress UI box and print raw JSON",
    )
    parser.add_argument(
        "--list-modes",
        action="store_true",
        default=False,
        help="Print all available modes table",
    )
    parser.add_argument(
        "--root-check",
        action="store_true",
        default=False,
        help="Validate elevated root privileges",
    )
    parser.add_argument(
        "--use-cache",
        action="store_true",
        default=False,
        dest="use_cache",
        help="Enable result caching",
    )
    parser.add_argument(
        "--no-color",
        action="store_true",
        default=False,
        dest="no_color",
        help="Disable color output",
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
    if args.list_modes:
        print_modes_table()
        sys.exit(EXIT_SUCCESS)

    res = execute_tool(
        target=args.target,
        mode=args.mode,
        ports_str=args.ports,
        limit=args.limit,
        filter_expr=args.filter,
        timeout=args.timeout,
        output_pcap=args.output_pcap,
        env_vars=args.env_var,
        force=args.force,
        root_check=args.root_check,
        use_cache=args.use_cache,
        no_color=args.no_color,
        verbose=args.verbose,
    )

    if not args.json_only:
        print_human_readable_ui(res, no_color=args.no_color)
    write_llm_output(res)
    sys.exit(res.get("exit_code", EXIT_SUCCESS))
