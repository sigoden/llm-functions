#!/usr/bin/env python3

import requests


def run(ip_address: str, verbose: bool = False) -> dict:
    """
    Get geolocation information for an IP address.

    Args:
        {str} ip_address - The IP address to look up.
        {bool} [verbose] - Whether to include raw API response metadata.
    """
    url = f"http://ip-api.com/json/{ip_address}"
    try:
        response = requests.get(url, timeout=10)
        data = response.json()
        if data.get("status") == "fail":
            return {"success": False, "error": data.get("message", "Lookup failed")}

        result = {
            "success": True,
            "ip": ip_address,
            "country": data.get("country"),
            "city": data.get("city"),
            "timezone": data.get("timezone"),
        }
        if verbose:
            result["raw"] = data
        return result
    except Exception as e:
        return {"success": False, "error": str(e)}
