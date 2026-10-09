import json
import socket
import subprocess


def get_public_ip() -> str:
    """Get public IP address"""
    try:
        result = subprocess.run(
            ["curl", "-s", "https://api.ipify.org"],
            capture_output=True,
            text=True,
            timeout=10,
        )
        if result.returncode == 0 and result.stdout.strip():
            return result.stdout.strip()
    except Exception:
        pass

    try:
        result = subprocess.run(
            ["curl", "-s", "https://ipinfo.io/ip"],
            capture_output=True,
            text=True,
            timeout=10,
        )
        if result.returncode == 0 and result.stdout.strip():
            return result.stdout.strip()
    except Exception:
        pass

    return "N/A"


def get_network_interfaces() -> dict:
    """Get network interface information"""
    interfaces = {}
    hostname = socket.gethostname()

    try:
        # Try using ip command (Linux)
        result = subprocess.run(
            ["ip", "-j", "addr", "show"],
            capture_output=True,
            text=True,
            timeout=10,
        )
        if result.returncode == 0:
            data = json.loads(result.stdout)
            for iface in data:
                name = iface.get("ifname")
                if name and not iface.get("operstate") == "UNKNOWN":
                    addrs = iface.get("addr_info", [])
                    ipv4_addrs = [
                        {"address": a.get("local"), "netmask": a.get("prefixlen")}
                        for a in addrs
                        if a.get("family") == "inet"
                        and not a.get("local", "").startswith("127.")
                    ]
                    if ipv4_addrs:
                        interfaces[name] = ipv4_addrs
    except Exception:
        pass

    # Fallback to socket method
    if not interfaces:
        try:
            ips = socket.gethostbyname_ex(hostname)[2]
            if not ips or ips == ["127.0.0.1"]:
                ips = []
            for ip in ips:
                if not ip.startswith("127."):
                    interfaces["default"] = [{"address": ip, "netmask": "N/A"}]
        except Exception:
            pass

    return interfaces


def run(public_ip: bool = True, interfaces: bool = True) -> dict:
    """Get network IP information including public IP and network interfaces
    Args:
        public_ip: Include public IP address (default: true)
        interfaces: Include network interface information (default: true)
    """
    if not isinstance(public_ip, bool) or not isinstance(interfaces, bool):
        return {"error": "Arguments must be boolean"}
    output = {}

    if public_ip:
        output["public_ip"] = get_public_ip()

    if interfaces:
        output["interfaces"] = get_network_interfaces()

    return output
