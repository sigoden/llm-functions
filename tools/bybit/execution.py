import asyncio
import random
import time

from .base import logger


class ExecutionMixin:
    def get_open_orders(self, symbol=None, category="linear", settle_coin="USDT"):
        """Get all open orders for a symbol."""
        params = {"category": category}
        if symbol:
            params["symbol"] = symbol.upper()
        if category == "linear":
            params["settleCoin"] = settle_coin
        return self._request("GET", "/v5/order/realtime", params, category="trade")

    def get_executions(self, symbol=None, order_id=None, limit=50, category="linear"):
        """Get trade executions (fills) for the account."""
        params = {"category": category, "limit": limit}
        if symbol:
            params["symbol"] = symbol.upper()
        if order_id:
            params["orderId"] = order_id
        return self._request("GET", "/v5/execution/list", params, category="trade")

    def place_order(
        self,
        symbol,
        side,
        qty,
        order_type="Market",
        price=None,
        category="linear",
        **kwargs,
    ):
        """Places a new order (Market, Limit, Stop)."""
        tif = "GTC"
        if kwargs.get("post_only", False):
            tif = "PostOnly"
        elif "time_in_force" in kwargs:
            tif = kwargs["time_in_force"]

        data = {
            "category": category,
            "symbol": symbol.upper(),
            "side": side,
            "orderType": order_type,
            "qty": self._format_qty(symbol, qty, category),
            "timeInForce": tif,
            "reduceOnly": bool(kwargs.get("reduce_only", False)),
        }
        if price:
            data["price"] = self._format_price(symbol, price, category)
        if "trigger_price" in kwargs:
            data["triggerPrice"] = self._format_price(
                symbol, kwargs["trigger_price"], category
            )
            data["triggerBy"] = kwargs.get("trigger_by", "LastPrice")

        # Take Profit / Stop Loss parameters
        if "take_profit" in kwargs:
            data["takeProfit"] = self._format_price(
                symbol, kwargs["take_profit"], category
            )
        if "stop_loss" in kwargs:
            data["stopLoss"] = self._format_price(symbol, kwargs["stop_loss"], category)
        if "tp_trigger_by" in kwargs:
            data["tpTriggerBy"] = kwargs["tp_trigger_by"]
        if "sl_trigger_by" in kwargs:
            data["slTriggerBy"] = kwargs["sl_trigger_by"]

        res = self._request(
            "POST", "/v5/order/create", json_data=data, category="trade"
        )
        if isinstance(res, dict) and "orderId" in res:
            try:
                self.journal.record("place_order", data, res, symbol)
            except:
                pass
        return res

    def cancel_order(self, symbol, order_id=None, category="linear"):
        """Cancels an existing order by ID."""
        return self._request(
            "POST",
            "/v5/order/cancel",
            json_data={
                "category": category,
                "symbol": symbol.upper(),
                "orderId": order_id,
            },
            category="trade",
        )

    def cancel_all_orders(self, symbol=None, category="linear"):
        """Cancels all active orders for a symbol or category."""
        data = {"category": category}
        if symbol:
            data["symbol"] = symbol.upper()
        return self._request(
            "POST", "/v5/order/cancel-all", json_data=data, category="trade"
        )

    def amend_order(
        self, symbol, order_id, qty=None, price=None, category="linear", **kwargs
    ):
        """Modifies an existing order's quantity or price."""
        data = {"category": category, "symbol": symbol.upper(), "orderId": order_id}
        if qty:
            data["qty"] = self._format_qty(symbol, qty, category)
        if price:
            data["price"] = self._format_price(symbol, price, category)
        if "take_profit" in kwargs:
            data["takeProfit"] = self._format_price(
                symbol, kwargs["take_profit"], category
            )
        if "stop_loss" in kwargs:
            data["stopLoss"] = self._format_price(symbol, kwargs["stop_loss"], category)
        return self._request(
            "POST", "/v5/order/amend", json_data=data, category="trade"
        )

    def set_trading_stop(
        self,
        symbol,
        take_profit=None,
        stop_loss=None,
        trailing_stop=None,
        category="linear",
    ):
        """Sets TP, SL, or Trailing Stop for an open position."""
        logger.info(f"Setting trading stop for {symbol} with category: {category}")
        data = {"category": category, "symbol": symbol.upper(), "positionIdx": 0}
        if take_profit:
            data["takeProfit"] = self._format_price(symbol, take_profit, category)
        if stop_loss:
            data["stopLoss"] = self._format_price(symbol, stop_loss, category)
        if trailing_stop:
            data["trailingStop"] = str(trailing_stop)
        return self._request(
            "POST", "/v5/position/trading-stop", json_data=data, category="trade"
        )

    def execute_iceberg(self, symbol, side, total_qty, slices, price, interval_sec=10):
        """Executes a large limit order by splitting it into smaller slices."""
        res = []
        base_slice = total_qty / slices
        for i in range(slices):
            q = float(self._format_qty(symbol, base_slice * random.uniform(0.9, 1.1)))
            order = self.place_order(symbol, side, q, "Limit", price)
            res.append(order)
            if i < slices - 1:
                time.sleep(interval_sec)
        return res

    async def execute_twap_async(
        self, symbol, side, total_qty, intervals, duration_sec
    ):
        """Executes a TWAP strategy."""
        qty_per = total_qty / intervals
        delay = duration_sec / intervals
        for _ in range(intervals):
            self.place_order(symbol, side, qty_per, "Market")
            await asyncio.sleep(delay)

    def execute_twap(self, symbol, side, total_qty, intervals, duration_sec):
        return asyncio.run(
            self.execute_twap_async(symbol, side, total_qty, intervals, duration_sec)
        )

    def chase_maker_limit(self, symbol, side, qty, timeout_sec=60):
        """Passive order that stays at the best Bid/Ask, chasing the price."""
        ob = self.get_orderbook(symbol, limit=1).get("result", {})
        if not ob.get("b") or not ob.get("a"):
            return {"status": "error", "msg": "Empty book"}
        target = float(ob["b"][0][0]) if side == "Buy" else float(ob["a"][0][0])
        order = self.place_order(
            symbol, side, qty, "Limit", target, time_in_force="PostOnly"
        )
        if isinstance(order, dict) and order.get("status") == "error":
            return order
        oid = order.get("orderId")
        start = time.time()
        while time.time() - start < timeout_sec:
            time.sleep(2)
            pos = self.get_positions(symbol=symbol).get("list", [])
            if pos and float(pos[0]["size"]) > 0:
                return {"status": "ok", "msg": "Filled"}
            new_ob = self.get_orderbook(symbol, limit=1).get("result", {})
            new_target = (
                float(new_ob["b"][0][0]) if side == "Buy" else float(new_ob["a"][0][0])
            )
            if new_target != target:
                target = new_target
                self.amend_order(symbol, oid, price=target)
        self.cancel_order(symbol, oid)
        return {"status": "error", "msg": "Expired"}

    def place_exponential_scale_in_grid(
        self, symbol, side, base_price, total_qty, steps=4
    ):
        """Places a series of limit orders with exponentially increasing sizes."""
        step_pct = 0.01
        multiplier = 1.5
        results = []
        current_qty = total_qty / ((1 - multiplier**steps) / (1 - multiplier))
        current_price = base_price
        for i in range(steps):
            res = self.place_order(symbol, side, current_qty, "Limit", current_price)
            results.append(res)
            current_qty *= multiplier
            current_price *= 1 - step_pct if side == "Buy" else 1 + step_pct
        return {"status": "ok", "orders": results}

    def smart_breakeven(self, symbol, buffer_pct=0.001):
        """Moves stop loss to breakeven."""
        pos = self.get_positions(symbol=symbol).get("list", [])
        if not pos:
            return {"status": "error", "msg": "No position"}
        entry, side = float(pos[0]["avgPrice"]), pos[0]["side"]
        be = entry * (1 + buffer_pct if side == "Buy" else 1 - buffer_pct)
        return self.set_trading_stop(symbol, stop_loss=be)

    def apply_atr_trailing_stop(
        self, symbol: str, side: str, atr_period: int = 14
    ) -> dict:
        """Dynamic Trailing Take Profit (ATR Volatility Tuned)."""
        atr = self.calculate_atr(symbol, "15", atr_period).get("atr", 0)
        if atr == 0:
            return {"status": "error", "msg": "No ATR"}
        return self.set_trading_stop(symbol=symbol, trailing_stop=atr * 3)

    def create_tp_bracket(
        self, symbol: str, side: str, entry: float, qty: float, category: str = "linear"
    ) -> dict:
        """Multiple Target Take-Profit Bracket Splitter."""
        tside = "Sell" if side == "Buy" else "Buy"
        targets = [1.01, 1.025, 1.04] if side == "Buy" else [0.99, 0.975, 0.96]
        res = []
        for t in targets:
            res.append(
                self.place_order(
                    symbol,
                    tside,
                    qty / 3,
                    "Limit",
                    entry * t,
                    category=category,
                    reduce_only=True,
                )
            )
        return {"status": "ok", "brackets": res}

    def set_fee_guaranteed_breakeven(
        self, symbol: str, entry_price: float, side: str, fee_rate: float = 0.0006
    ) -> dict:
        """Fee-Adjusted Breakeven Stop Adjustment."""
        be = entry_price * (
            1 + (fee_rate * 2.2) if side == "Buy" else 1 - (fee_rate * 2.2)
        )
        return self.set_trading_stop(symbol, stop_loss=be)

    def place_safe_stop_market(
        self, symbol: str, side: str, qty: float, trigger_price: float
    ) -> dict:
        """Conditional Stop Order Safety Check."""
        price = float(self.get_ticker(symbol).get("list", [{}])[0].get("lastPrice", 0))
        if (side == "Buy" and trigger_price < price) or (
            side == "Sell" and trigger_price > price
        ):
            return {
                "status": "error",
                "msg": "Invalid trigger price relative to market",
            }
        return self.place_stop_market(symbol, side, qty, trigger_price)

    def place_ioc_order(self, symbol: str, side: str, qty: float, price: float) -> dict:
        """Immediate-Or-Cancel (IOC) Position Wrapper."""
        return self.place_order(
            symbol=symbol, side=side, qty=qty, price=price, time_in_force="IOC"
        )

    def adjust_resting_orders_drift(
        self, symbol: str, max_drift_pct: float = 0.5
    ) -> dict:
        """Active Order Book Spread-Drift Realignment Tool."""
        orders = self.get_open_orders(symbol).get("list", [])
        last = float(self.get_ticker(symbol).get("list", [{}])[0].get("lastPrice", 0))
        amended = []
        for o in orders:
            drift = abs(float(o["price"]) - last) / last * 100
            if drift > max_drift_pct:
                new_p = last * (0.995 if o["side"] == "Buy" else 1.005)
                amended.append(self.amend_order(symbol, o["orderId"], price=new_p))
        return {"status": "ok", "count": len(amended)}

    def calculate_dynamic_qty(
        self, symbol: str, bid: float, max_usdt: float, liq_factor: float = 0.1
    ) -> float:
        """Calculates order quantity based on book depth and capital."""
        ob = self.get_orderbook(symbol, limit=20).get("result", {})
        liq = (
            min(
                sum(float(q) for _, q in ob.get("b", [])),
                sum(float(q) for _, q in ob.get("a", [])),
            )
            * liq_factor
        )
        return round(min(liq, max_usdt / bid), 4)

    def place_stop_market(
        self,
        symbol: str,
        side: str,
        qty: float,
        trigger_price: float,
        trigger_by: str = "LastPrice",
        category: str = "linear",
    ) -> dict:
        """Places a Stop Market order."""
        return self.place_order(
            symbol=symbol,
            side=side,
            qty=qty,
            order_type="Market",
            trigger_price=trigger_price,
            trigger_by=trigger_by,
            category=category,
        )

    def place_stop_limit(
        self,
        symbol: str,
        side: str,
        qty: float,
        price: float,
        trigger_price: float,
        trigger_by: str = "LastPrice",
        category: str = "linear",
    ) -> dict:
        """Places a Stop Limit order."""
        return self.place_order(
            symbol=symbol,
            side=side,
            qty=qty,
            order_type="Limit",
            price=price,
            trigger_price=trigger_price,
            trigger_by=trigger_by,
            category=category,
        )

    def place_spot_with_triggers(
        self, symbol: str, side: str, qty: float, entry: float, tp: float, sl: float
    ) -> dict:
        """Places a Spot order with TP/SL triggers."""
        entry_order = self.place_order(
            symbol, side, qty, "Limit", entry, category="spot"
        )
        exit_side = "Sell" if side == "Buy" else "Buy"
        tp_order = self.place_stop_limit(
            symbol, exit_side, qty, tp, tp, category="spot"
        )
        sl_order = self.place_stop_market(symbol, exit_side, qty, sl, category="spot")
        return {"entry": entry_order, "tp": tp_order, "sl": sl_order}

    def run_micro_profit(
        self,
        symbol: str,
        side: str,
        qty: float,
        target: float = 0.05,
        execute: bool = False,
        category: str = "linear",
    ) -> dict:
        """Runs micro-profit scalper."""
        ob = self.get_orderbook(symbol, limit=40, category=category).get("result", {})
        bids, asks = ob.get("b", []), ob.get("a", [])
        from tools.micro_profit import run

        return run(
            symbol=symbol,
            side=side,
            qty=qty,
            bids=bids,
            asks=asks,
            target=target,
            execute=execute,
        )

    def calculate_limit_micro_profit(
        self, entry: float, limit: float, side: str, qty: float, fee: float = 0.001
    ) -> dict:
        """Calculates net profit for limit order."""
        pnl = (limit - entry) * qty if side.lower() == "buy" else (entry - limit) * qty
        fee_amt = abs(limit * qty) * fee
        return {
            "net_pnl": round(pnl - fee_amt, 4),
            "pct": round((pnl - fee_amt) / (entry * qty) * 100, 2),
        }

    def calculate_target_pnl(
        self,
        side: str,
        entry: float,
        qty: float,
        target_usdt: float,
        fee: float = 0.0002,
    ) -> dict:
        """Calculates price for target profit."""
        d = 1 if side.lower() == "buy" else -1
        exit_p = (target_usdt + (entry * qty * d)) / (qty * d - (qty * fee))
        return {"required_exit": round(exit_p, 4)}

    def panic_close(self, category: str = "linear") -> dict:
        """Closes all positions at market."""
        pos = self.get_positions(category=category).get("list", [])
        res = []
        for p in pos:
            if float(p["size"]) > 0:
                side = "Sell" if p["side"] == "Buy" else "Buy"
                res.append(
                    self.place_order(
                        p["symbol"],
                        side,
                        p["size"],
                        "Market",
                        reduce_only=True,
                        category=category,
                    )
                )
        return {"results": res}

    def generate_twap_orders(self, symbol, side, total_qty, duration_min, intervals=10):
        return {"qty": total_qty / intervals, "delay": (duration_min * 60) / intervals}

    def generate_pv_orders(self, symbol, side, target_qty, vol_pct=0.05):
        v24 = float(self.get_ticker(symbol).get("list", [{}])[0].get("volume24h", 0))
        return {"qty": min(target_qty, (v24 / 24) * vol_pct)}

    def generate_grid_orders(self, symbol, r_low, r_high, grids, qty, side="Both"):
        step = (r_high - r_low) / (grids - 1)
        orders = []
        for i in range(grids):
            p = r_low + i * step
            if side in ["Buy", "Both"]:
                orders.append({"side": "Buy", "price": p, "qty": qty})
            if side in ["Sell", "Both"]:
                orders.append({"side": "Sell", "price": p, "qty": qty})
        return {"orders": orders}

    def place_atr_bracketed_order(self, symbol, side, qty, price, category="linear"):
        atr = self.calculate_atr(symbol, "15").get("atr", 0)
        return self.place_order(
            symbol,
            side,
            qty,
            "Limit",
            price,
            take_profit=price + (atr * 3 if side == "Buy" else -atr * 3),
            stop_loss=price - (atr * 1.5 if side == "Buy" else -atr * 1.5),
            category=category,
        )

    def apply_strict_breakeven_stop(self, symbol, category="linear"):
        p = self.get_positions(symbol=symbol, category=category).get("list", [{}])[0]
        f = float(
            self.get_fee_rate(symbol=symbol, category=category)
            .get("list", [{}])[0]
            .get("takerFeeRate", 0.0006)
        )
        off = float(p["avgPrice"]) * (f * 2.2)
        return self.set_trading_stop(
            symbol,
            stop_loss=float(p["avgPrice"]) + (off if p["side"] == "Buy" else -off),
        )

    def execute_immediate_or_cancel_limit(
        self, symbol, side, qty, price, category="linear"
    ):
        return self.place_order(
            symbol, side, qty, "Limit", price, time_in_force="IOC", category=category
        )

    def execute_pairs_zscore_scalp(self, symbol_a, symbol_b, qty_usd):
        z = self.get_cointegrated_spread(symbol_a, symbol_b).get("z_score", 0)
        if abs(z) < 2.0:
            return {"status": "no_trigger", "z": z}
        pa = float(self.get_ticker(symbol_a).get("list", [{}])[0].get("lastPrice", 0))
        pb = float(self.get_ticker(symbol_b).get("list", [{}])[0].get("lastPrice", 0))
        qa, qb = qty_usd / pa, qty_usd / pb
        if z > 2.0:
            return {
                "a": self.place_order(symbol_a, "Sell", qa),
                "b": self.place_order(symbol_b, "Buy", qb),
            }
        return {
            "a": self.place_order(symbol_a, "Buy", qa),
            "b": self.place_order(symbol_b, "Sell", qb),
        }

    def active_chase_maker_limit(self, symbol, side, qty, max_chase=15):
        return self.chase_maker_limit(symbol, side, qty, timeout_sec=max_chase * 2)

    def batch_place_orders(self, symbol=None, orders=None, category="linear"):
        """Places a batch of orders and validates individual results."""
        if orders is None:
            return {"status": "error", "msg": "No orders provided"}
        data = {"category": category, "request": []}
        for o in orders:
            # Use order-specific symbol if available, fallback to top-level symbol
            s = o.get("symbol", symbol)
            if not s:
                logger.error("Order missing symbol and no default symbol provided")
                continue
            s = s.upper()
            order_data = {
                "symbol": s,
                "side": o["side"],
                "orderType": o.get("orderType", "Limit"),
                "qty": self._format_qty(s, o["qty"], category),
                "price": self._format_price(s, o["price"], category),
                "timeInForce": o.get("timeInForce", "GTC"),
            }
            # Add optional params
            for opt in [
                "orderLinkId",
                "takeProfit",
                "stopLoss",
                "tpTriggerBy",
                "slTriggerBy",
                "reduceOnly",
                "postOnly",
            ]:
                if opt in o:
                    order_data[opt] = o[opt]
            if "post_only" in o:
                order_data["postOnly"] = bool(o["post_only"])
            if "reduce_only" in o:
                order_data["reduceOnly"] = bool(o["reduce_only"])

            data["request"].append(order_data)

        if not data["request"]:
            return {"status": "error", "msg": "No valid orders in batch"}

        res = self._request(
            "POST", "/v5/order/create-batch", json_data=data, category="trade"
        )
        if isinstance(res, dict) and "list" in res:
            for i, order_res in enumerate(res["list"]):
                if (
                    order_res.get("retCode") != 0
                    and order_res.get("retCode") is not None
                ):
                    logger.error(f"Order {i} failed: {order_res.get('retMsg')}")
        return res

    def close_at_bbo_limit(self, symbol, category="linear"):
        """Closes a position using a BBO limit order."""
        pos = self.get_positions(symbol=symbol, category=category).get("list", [{}])[0]
        if not pos or float(pos["size"]) == 0:
            return {"status": "error", "msg": "No position"}
        side = "Sell" if pos["side"] == "Buy" else "Buy"
        qty = pos["size"]
        ob = self.get_orderbook(symbol, limit=1)
        # Using opposite side of position for limit price
        price = ob.get("a", [])[0][0] if side == "Sell" else ob.get("b", [])[0][0]
        return self.place_order(
            symbol,
            side,
            qty,
            order_type="Limit",
            price=price,
            category=category,
            reduce_only=True,
            post_only=True,
        )
