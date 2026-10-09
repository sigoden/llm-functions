#!/usr/bin/env python3
"""
Tor Utilities Module for LLM-Functions Bybit Tools
Provides unified Tor connection verification, proxy configuration, and fallback handling.
"""

import logging
import os
import socket
import sys
from typing import Any, Dict, Optional, Tuple

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

# Configure logging
logger = logging.getLogger("bybit_tor")
if not logger.handlers:
    handler = logging.StreamHandler(sys.stderr)
    handler.setFormatter(logging.Formatter("[TOR] %(levelname)s: %(message)s"))
    logger.addHandler(handler)
    logger.setLevel(logging.INFO)

# =============================================================================
# Tor Configuration
# =============================================================================
TOR_SOCKS_HOST: str = os.environ.get("TOR_SOCKS_HOST", "127.0.0.1")
TOR_SOCKS_PORT: int = int(os.environ.get("TOR_SOCKS_PORT", "9050"))
TOR_CONNECT_TIMEOUT: float = float(os.environ.get("TOR_CONNECT_TIMEOUT", "15"))
TOR_READ_TIMEOUT: float = float(os.environ.get("TOR_READ_TIMEOUT", "30"))
TOR_TIMEOUT: Tuple[float, float] = (TOR_CONNECT_TIMEOUT, TOR_READ_TIMEOUT)

# socks5h:// — DNS resolved on Tor exit node (prevents DNS leaks)
_SOCKS5H_PROXY: str = f"socks5h://{TOR_SOCKS_HOST}:{TOR_SOCKS_PORT}"
TOR_PROXIES: Dict[str, str] = {
    "http": _SOCKS5H_PROXY,
    "https": _SOCKS5H_PROXY,
}

# Neutral User-Agent (avoids fingerprinting)
DEFAULT_USER_AGENT: str = (
    "Mozilla/5.0 (Windows NT 10.0; rv:109.0) Gecko/20100101 Firefox/115.0"
)


# =============================================================================
# Tor Connection Utilities
# =============================================================================
def check_tor_port() -> bool:
    """
    Check if Tor SOCKS port is accepting connections.

    Returns:
        True if Tor port is open, False otherwise.
    """
    try:
        with socket.create_connection((TOR_SOCKS_HOST, TOR_SOCKS_PORT), timeout=5):
            logger.info(f"Tor SOCKS port {TOR_SOCKS_HOST}:{TOR_SOCKS_PORT} is open")
            return True
    except OSError as e:
        logger.warning(f"Tor SOCKS port not reachable: {e}")
        return False


def verify_tor_connection() -> str:
    """
    Verify traffic is routing through Tor by checking the exit node.

    Returns:
        Exit node IP address if Tor is working correctly.

    Raises:
        RuntimeError: If Tor is not working or traffic is not routed through Tor.
    """
    if not check_tor_port():
        raise RuntimeError(
            f"Tor SOCKS port {TOR_SOCKS_HOST}:{TOR_SOCKS_PORT} not reachable"
        )

    check_url = "https://check.torproject.org/api/ip"
    try:
        resp = requests.get(
            check_url,
            proxies=TOR_PROXIES,
            timeout=TOR_TIMEOUT,
            headers={"User-Agent": DEFAULT_USER_AGENT},
        )
        resp.raise_for_status()
        data = resp.json()
    except Exception as exc:
        raise RuntimeError(f"Tor verification request failed: {exc}") from exc

    if not data.get("IsTor", False):
        raise RuntimeError(
            f"Traffic NOT routing through Tor. IP: {data.get('IP', 'unknown')}"
        )

    exit_ip = data.get("IP", "hidden")
    logger.info(f"Tor connection verified. Exit node: {exit_ip}")
    return exit_ip


def get_tor_status() -> Dict[str, Any]:
    """
    Get comprehensive Tor connection status.

    Returns:
        Dictionary with port status, connection status, and exit IP.
    """
    status = {
        "port_open": check_tor_port(),
        "tor_working": False,
        "exit_ip": None,
        "error": None,
    }

    if status["port_open"]:
        try:
            status["exit_ip"] = verify_tor_connection()
            status["tor_working"] = True
        except RuntimeError as e:
            status["error"] = str(e)

    return status


# =============================================================================
# Proxy Configuration
# =============================================================================
def get_proxy_config(
    use_tor: bool = True,
) -> Tuple[Optional[Dict[str, str]], Tuple[float, float], str]:
    """
    Get proxy configuration based on Tor availability.

    Args:
        use_tor: Whether to attempt using Tor (falls back to direct if unavailable)

    Returns:
        Tuple of (proxies, timeout, user_agent)
    """
    user_agent = DEFAULT_USER_AGENT

    if not use_tor:
        # Direct connection - no proxy
        return None, (10, 25), user_agent

    # Try Tor first
    if check_tor_port():
        try:
            verify_tor_connection()
            logger.info("Using Tor for requests")
            return TOR_PROXIES, TOR_TIMEOUT, user_agent
        except RuntimeError as e:
            logger.warning(
                f"Tor available but not working: {e}. Falling back to direct."
            )

    # Fallback to direct connection
    logger.info("Tor not available, using direct connection")
    return None, (10, 25), user_agent


def get_session_with_retries(
    proxies: Optional[Dict[str, str]] = None,
    timeout: Tuple[float, float] = (10, 25),
    max_retries: int = 3,
) -> requests.Session:
    """
    Create a requests session with retry logic.

    Args:
        proxies: Proxy configuration dictionary
        timeout: Connection and read timeout tuple
        max_retries: Maximum number of retry attempts

    Returns:
        Configured requests.Session
    """
    session = requests.Session()

    retry_strategy = Retry(
        total=max_retries,
        backoff_factor=1,
        status_forcelist=[429, 500, 502, 503, 504],
        allowed_methods=["GET", "POST"],
    )

    adapter = HTTPAdapter(max_retries=retry_strategy)
    session.mount("http://", adapter)
    session.mount("https://", adapter)

    session.proxies = proxies
    session.timeout = timeout
    session.headers["User-Agent"] = DEFAULT_USER_AGENT

    return session


# =============================================================================
# Convenience Functions
# =============================================================================
def prepare_request(
    use_tor: bool = True,
    verify_tor: bool = True,
) -> Tuple[Optional[Dict[str, str]], Tuple[float, float], str, Optional[str]]:
    """
    Prepare request parameters with Tor support.

    Args:
        use_tor: Whether to attempt using Tor
        verify_tor: Whether to verify Tor connection before returning

    Returns:
        Tuple of (proxies, timeout, user_agent, exit_ip)
    """
    exit_ip = None

    if use_tor and verify_tor:
        try:
            exit_ip = verify_tor_connection()
        except RuntimeError as e:
            logger.warning(f"Tor verification failed: {e}")
            # Fall back to direct
            use_tor = False

    proxies, timeout, user_agent = get_proxy_config(use_tor)

    return proxies, timeout, user_agent, exit_ip


# =============================================================================
# Main entry point for testing
# =============================================================================
if __name__ == "__main__":
    import json

    print("=== Tor Status Check ===")
    status = get_tor_status()
    print(json.dumps(status, indent=2))

    if status["tor_working"]:
        print(f"\n✓ Tor is working. Exit IP: {status['exit_ip']}")
    else:
        print("\n✗ Tor is not working properly")
        if status["error"]:
            print(f"  Error: {status['error']}")
