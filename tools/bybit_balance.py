#!/usr/bin/env python3
"""Get wallet balance from Bybit exchange."""


def run(
    account_type: str = "UNIFIED",
):
    """Get wallet balance from Bybit V5 API
    Args:
        account_type: Account type (UNIFIED/CONTRACT/SPOT)
    """
    import bybit_core

    params = {"accountType": account_type}
    data = bybit_core.api_request(
        "GET", "/v5/account/wallet-balance", params=params, signed=True
    )

    if data.get("retCode") == 0:
        coins = data.get("result", {}).get("list", [{}])[0].get("coin", [])
        return {"success": True, "account_type": account_type, "coins": coins}
    else:
        return {"success": False, "error": data.get("retMsg")}


if __name__ == "__main__":
    import json
    from argparse import ArgumentParser

    parser = ArgumentParser(description="Get balance from Bybit")
    parser.add_argument("--account-type", default="UNIFIED")
    args = parser.parse_args()

    result = run(account_type=args.account_type)
    print(json.dumps(result, indent=2))
