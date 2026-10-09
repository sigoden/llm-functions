#!/usr/bin/env python3
import json
import os
import sys
from typing import Literal

# Add tools/utils to path to import refactored base
sys.path.append(os.path.join(os.path.dirname(__file__), "utils"))
from bybit_base import api_request


def run(account_type: Literal["UNIFIED", "CONTRACT", "SPOT"] = "UNIFIED") -> dict:
    """Retrieve Bybit wallet balance.
    Args:
        account_type: Account type (UNIFIED, CONTRACT, SPOT)
    """
    params = {"accountType": account_type}
    return api_request("GET", "/v5/account/wallet-balance", params=params, signed=True)


if __name__ == "__main__":
    # For aichat integration, print JSON to stdout
    print(json.dumps(run()))
