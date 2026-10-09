import asyncio
import inspect
import json
from datetime import datetime, timezone
from typing import Optional

from utils.bybit_base import calculate_exit_price, calculate_pnl
from utils.trading_engines import ExitStrategyEngine, StatisticalArbitrageEngine

from .account import AccountMixin, RiskManagerMixin
from .base import BybitBaseClient, TradeJournal, TradingConfig, logger
from .execution import ExecutionMixin
from .market import MarketDataMixin
from .price_action import PriceActionEngine, analyze_price_action
from .smart import SmartOrderMixin
from .unified import PriceActionMixin


class SignalManager:
    def __init__(self, path="trading_signals.json"):
        from pathlib import Path

        self.path = Path(path)
        self.signals = self._load()

    def _load(self):
        if self.path.exists():
            try:
                return json.loads(self.path.read_text())
            except:
                return []
        return []

    def add(self, signal):
        import uuid

        signal["id"] = str(uuid.uuid4())
        self.signals.append(signal)
        self.path.write_text(json.dumps(self.signals, indent=2))
        return signal["id"]

    def get_all(self):
        return self.signals


class BybitRealm(
    BybitBaseClient,
    MarketDataMixin,
    ExecutionMixin,
    AccountMixin,
    RiskManagerMixin,
    SmartOrderMixin,
    PriceActionMixin,
):
    def __init__(self, config: Optional[TradingConfig] = None):
        super().__init__(config)
        self.journal = TradeJournal(self.config)
        self.signals = SignalManager()
        self.arb_engine = StatisticalArbitrageEngine()
        self.exit_engine = ExitStrategyEngine()

    def analyze_market_making(self, symbol: str, inventory_ratio: float = 0.0) -> dict:
        """Full orderbook analysis with market making signals"""
        ob = self.get_orderbook(symbol, limit=50)
        bids = sorted(
            [(float(p), float(q)) for p, q in ob.get("b", [])],
            key=lambda x: x[0],
            reverse=True,
        )
        asks = sorted(
            [(float(p), float(q)) for p, q in ob.get("a", [])], key=lambda x: x[0]
        )
        if not bids or not asks:
            return {"status": "error", "msg": f"No liquidity for {symbol}. OB: {ob}"}
        current_price = (bids[0][0] + asks[0][0]) / 2
        result = self.arb_engine.analyze_market_making_opportunity(
            [p for p, v in bids],
            [v for p, v in bids],
            [p for p, v in asks],
            [v for p, v in asks],
            current_price,
            inventory_ratio,
        )
        stat_arb = self.arb_engine.analyze_statistical_arbitrage(current_price)
        return {"status": "ok", "market_making": result, "stat_arb": stat_arb}

    def calculate_exit_strategy(
        self, symbol: str, entry_price: float, position_size: float, side: str
    ) -> dict:
        """Multi-strategy exit analysis"""
        ob = self.get_orderbook(symbol, limit=50)
        ticker_res = self.get_ticker(symbol)
        # get_ticker returns {"category": "linear", "list": [...]}
        ticker = ticker_res.get("list", [{}])[0]
        bids = [(float(p), float(q)) for p, q in ob.get("b", [])]
        asks = [(float(p), float(q)) for p, q in ob.get("a", [])]
        current_price = float(ticker.get("lastPrice", 0))
        atr = float(ticker.get("atr", current_price * 0.01))

        result = self.exit_engine.calculate_multi_exit_levels(
            entry_price, position_size, side, current_price, atr, {}, bids, asks
        )
        return {"status": "ok", "result": result}

    def health_check(self) -> dict:
        """Checks connectivity and returns server time info."""
        return self._request("GET", "/v5/market/time", signed=False)

    def calculate_pnl(
        self,
        entry_price: float,
        exit_price: float,
        size: float,
        side: str,
        fees: float = 0.0,
        leverage: float = 1.0,
    ) -> dict:
        """Calculates PnL in USDT and percentage based on initial margin."""
        return {
            "status": "ok",
            "result": calculate_pnl(
                entry_price, exit_price, size, side, fees, leverage
            ),
        }

    def calculate_exit_price(
        self,
        entry_price: float,
        target_pnl: float,
        size: float,
        side: str,
        fees: float = 0.0,
    ) -> dict:
        """Calculates the required exit price to achieve a target PnL."""
        return {
            "status": "ok",
            "result": calculate_exit_price(entry_price, target_pnl, size, side, fees),
        }

    def list_actions(self) -> dict:
        """Returns a list of all available actions and their parameters."""
        actions = {}
        for name in dir(self):
            if name.startswith("_") or name in [
                "close",
                "config",
                "session",
                "journal",
                "signals",
                "breaker",
            ]:
                continue
            attr = getattr(self, name)
            if callable(attr):
                try:
                    sig = inspect.signature(attr)
                    actions[name] = {
                        "params": [p for p in sig.parameters],
                        "doc": (attr.__doc__ or "No documentation available").strip(),
                    }
                except:
                    pass
        return {"status": "ok", "actions": actions}

    def get_rate_limits(self) -> dict:
        """Returns the last known rate limit status for each category."""
        return {"status": "ok", "rate_limits": self.last_rate_limits}

    def get_dashboard(self) -> dict:
        """Provides a high-level overview of account health, positions, and pending orders."""
        try:
            # 1. Account Balance
            bal_res = self.get_wallet_balance()
            if bal_res.get("status") == "error":
                return bal_res
            bal_data = bal_res.get("list", bal_res.get("result", {}).get("list", [{}]))[
                0
            ]

            # 2. Open Positions
            pos_res = self.get_positions()
            positions = [
                p for p in pos_res.get("list", []) if float(p.get("size", 0)) > 0
            ]

            # 3. Open Orders
            orders_res = self.get_open_orders()
            orders = orders_res.get("list", [])

            # 4. Performance (from Journal)
            perf = self.get_performance_summary(limit=50)

            return {
                "status": "ok",
                "account": {
                    "total_equity": bal_data.get("totalEquity"),
                    "total_wallet_balance": bal_data.get("totalWalletBalance"),
                    "total_margin_balance": bal_data.get("totalMarginBalance"),
                    "total_available_balance": bal_data.get("totalAvailableBalance"),
                    "unrealised_pnl": bal_data.get("totalUnrealisedPnl"),
                },
                "positions_count": len(positions),
                "active_positions": [
                    {
                        "symbol": p["symbol"],
                        "side": p["side"],
                        "size": p["size"],
                        "entry": p["avgPrice"],
                        "mark": p["markPrice"],
                        "pnl": p["unrealisedPnl"],
                        "pnl_pct": f"{float(p['unrealisedPnl']) / (float(p['positionValue']) + 1e-9) * 100:.2f}%",
                    }
                    for p in positions
                ],
                "orders_count": len(orders),
                "performance": perf if perf.get("status") == "ok" else "N/A",
                "timestamp": datetime.now(timezone.utc).isoformat(),
            }
        except Exception as e:
            return {"status": "error", "msg": f"Dashboard generation failed: {e}"}

    def close(self):
        if hasattr(self, "session"):
            self.session.close()


def run(**kwargs) -> dict:
    action = kwargs.pop("action", None)
    if not action:
        return {
            "status": "error",
            "msg": "Missing 'action' parameter. Use --action list_actions to see available commands.",
        }

    bot = BybitRealm()

    # Discovery Aliases
    if action in ["help", "list"]:
        action = "list_actions"

    method = getattr(bot, action, None)
    if method and not inspect.ismethod(method):
        method = method.__get__(bot, BybitRealm)

    if not method or not callable(method):
        return {
            "status": "error",
            "msg": f"Action '{action}' not found. Use 'list_actions' to see all commands.",
        }

    try:
        sig = inspect.signature(method)
        params = {}
        for k, v in kwargs.items():
            if k in sig.parameters:
                p = sig.parameters[k]
                try:
                    if k == "orders" and isinstance(v, str):
                        params[k] = json.loads(v)
                    elif p.annotation == int or k in [
                        "limit",
                        "depth",
                        "period",
                        "leverage",
                    ]:
                        params[k] = int(float(v))
                    elif p.annotation == float or k in [
                        "qty",
                        "price",
                        "stop_loss",
                        "take_profit",
                    ]:
                        params[k] = float(v)
                    elif p.annotation == bool or str(v).lower() in ["true", "false"]:
                        params[k] = str(v).lower() == "true"
                    else:
                        params[k] = v
                except:
                    params[k] = v

        logger.info(f"Dispatching: {action}")

        if asyncio.iscoroutinefunction(method):
            return asyncio.run(method(**params))
        return method(**params)
    except Exception as e:
        import traceback

        logger.error(f"Execution error in '{action}': {e}", exc_info=True)
        return {"status": "error", "msg": str(e), "traceback": traceback.format_exc()}
    finally:
        bot.close()
