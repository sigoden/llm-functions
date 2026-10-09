#!/usr/bin/env python3
"""Read-only live smoke test for the unified Bybit suite.

Run this on the device where the tools are deployed (e.g. Termux):

    python scripts/bybit_live_smoke.py [--with-auth] [--symbol BTCUSDT] [--interval 60]

It exercises public market endpoints plus (optionally, with --with-auth)
the authenticated account endpoints. It NEVER places, amends or cancels
orders — execution paths are tested only through the dry-run plan builder.

Exit code 0 = all checks passed.
"""

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))


def main() -> int:
    parser = argparse.ArgumentParser(description="Bybit suite live smoke test (read-only)")
    parser.add_argument("--with-auth", action="store_true",
                        help="also test authenticated endpoints (no orders placed)")
    parser.add_argument("--symbol", default="BTCUSDT")
    parser.add_argument("--interval", default="60")
    parser.add_argument("--tor", action="store_true", help="route via Tor SOCKS5 proxy")
    args = parser.parse_args()

    if args.tor:
        import os
        os.environ["PROXY_ENABLED"] = "true"
        os.environ["PROXY_TYPE"] = "socks5h"
        os.environ["PROXY_HOST"] = "127.0.0.1"
        os.environ["PROXY_PORT"] = "9050"

    from tools.bybit.terminal import BybitRealm

    failures = []
    bot = BybitRealm()

    def check(name: str, fn):
        try:
            res = fn()
            status = res.get("status")
            ok = status is None or status != "error"
            print(f"  [{'PASS' if ok else 'FAIL'}] {name}")
            if not ok:
                failures.append(name)
                print(f"           {res.get('msg', res)}")
            return res
        except Exception as e:
            failures.append(name)
            print(f"  [FAIL] {name}: {e}")
            return {"status": "error", "msg": str(e)}

    try:
        print(f"== Public market data ({args.symbol}, {args.interval}m) ==")
        check("server time", bot.health_check)
        check("ticker", lambda: bot.get_ticker(args.symbol))
        check("klines", lambda: bot.get_klines(args.symbol, args.interval, 300))
        check("orderbook", lambda: bot.get_orderbook(args.symbol, 50))
        check("orderbook analysis", lambda: bot.get_orderbook_analysis(args.symbol, 50))
        check("funding rate", lambda: bot.get_funding_rate(args.symbol, 5))
        check("open interest", lambda: bot.get_open_interest(args.symbol, limit=5))

        print("== Trend / price-action analysis ==")
        check("market regime", lambda: bot.get_market_regime(args.symbol, args.interval))
        check("supertrend", lambda: bot.calculate_supertrend(args.symbol, args.interval))
        check("price_action_analysis",
              lambda: bot.price_action_analysis(args.symbol, args.interval, 300))
        check("trend_scan",
              lambda: bot.trend_scan("BTCUSDT,ETHUSDT,SOLUSDT", args.interval, 300))
        check("wbta_signal", lambda: bot.wbta_signal(args.symbol, args.interval))

        print("== Dry-run planning (no orders are placed) ==")
        check("build_trade_plan", lambda: bot.build_trade_plan(args.symbol, args.interval))
        check("execute_signal dry-run",
              lambda: bot.execute_signal(args.symbol, args.interval, execute=False))
        check("backtest_pa", lambda: bot.backtest_pa(args.symbol, args.interval, 600))

        if args.with_auth:
            print("== Authenticated (read-only) ==")
            check("wallet balance", bot.get_wallet_balance)
            check("account info", bot.get_account_info)
            check("positions", lambda: bot.get_positions())
            check("open orders", lambda: bot.get_open_orders())
            check("fee rate", lambda: bot.get_fee_rate(symbol=args.symbol))
            check("closed PnL", lambda: bot.get_pnl_history(limit=10))
            check("performance summary", lambda: bot.get_performance_summary(limit=50))
    finally:
        bot.close()

    print()
    if failures:
        print(f"{len(failures)} check(s) failed: {failures}")
        return 1
    print("All checks passed.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
