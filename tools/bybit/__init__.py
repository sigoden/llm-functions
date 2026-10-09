"""Unified Bybit trading package.

Canonical implementation of the Bybit v5 client, market data, execution,
account/risk, smart orders, price-action analysis, backtesting and the
`run(action=...)` dispatcher used by `bybit_suite.py` and the CLI.

    from tools.bybit.terminal import BybitRealm
    bot = BybitRealm()
    bot.get_ticker("BTCUSDT")
"""

from .base import BybitBaseClient, TradingConfig
from .price_action import PriceActionEngine
from .terminal import BybitRealm, SignalManager, run

__all__ = [
    "BybitBaseClient",
    "BybitRealm",
    "PriceActionEngine",
    "SignalManager",
    "TradingConfig",
    "run",
]
