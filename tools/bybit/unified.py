"""Unified price-action / trend layer for the Bybit trading suite.

`PriceActionMixin` ties the package together:
  * market structure + indicators ......... PriceActionEngine
  * WBTA observatory (L2/flow/funding/OI) .. tools.bybit_wbta
  * execution ............................. ExecutionMixin / SmartOrderMixin
  * risk .................................. risk-first position sizing

All plan-building actions are dry by design; the only action that can place
an order is `execute_signal`, and only with `execute=True` plus passing
safety gates.
"""

from __future__ import annotations

from datetime import datetime, timezone

from .price_action import PriceActionEngine, analyze_price_action


class PriceActionMixin:
    def _available_usdt(self) -> float:
        """Best-effort available USDT balance (0 when unavailable)."""
        try:
            bal_res = self.get_wallet_balance()
            if bal_res.get("status") == "error":
                return 0.0
            bal_list = bal_res.get("list", bal_res.get("result", {}).get("list", [{}]))
            coins = bal_list[0].get("coin", []) if bal_list else []
            usdt = next((c for c in coins if c.get("coin") == "USDT"), {})
            return float(
                usdt.get("availableToWithdraw")
                or usdt.get("availableToTrade")
                or usdt.get("walletBalance")
                or 0
            )
        except Exception:
            return 0.0

    def price_action_analysis(
        self,
        symbol: str,
        interval: str = "60",
        limit: int = 300,
        min_confidence: float = 55.0,
        rr_min: float = 2.0,
    ) -> dict:
        """Market-structure + trend analysis for price action trading.

        Returns swing structure (HH/HL vs LH/LL), EMA stack alignment, ATR,
        RSI, ADX, S/R zones and a composite LONG/SHORT/WAIT signal with
        entry, stop and TP ladder.
        """
        klines = self._get_klines_safely(symbol, interval, limit)
        if len(klines) < 60:
            return {"status": "error", "msg": f"Not enough klines for {symbol}"}
        res = analyze_price_action(klines, min_confidence=min_confidence, rr_min=rr_min)
        res["symbol"] = symbol.upper()
        res["interval"] = interval
        res["timestamp"] = datetime.now(timezone.utc).isoformat()
        return res

    def build_trade_plan(
        self,
        symbol: str,
        interval: str = "60",
        limit: int = 300,
        risk_pct: float = 1.0,
        leverage: float = 1.0,
        rr_min: float = 2.0,
        min_confidence: float = 55.0,
        fee_rate: float = 0.00055,
    ) -> dict:
        """Full trade plan: price-action signal + risk-first position sizing.

        Dry by design — nothing is placed. Returns entry/SL/TP ladder, qty,
        notional, margin and expected fees. Use `execute_signal` to act.
        """
        klines = self._get_klines_safely(symbol, interval, limit)
        if len(klines) < 60:
            return {"status": "error", "msg": f"Not enough klines for {symbol}"}

        engine = PriceActionEngine()
        signal = engine.price_action_signal(klines, min_confidence, rr_min)

        plan = engine.trade_plan(
            signal,
            balance=self._available_usdt(),
            risk_pct=risk_pct,
            leverage=leverage,
            fee_rate=fee_rate,
            rr_min=rr_min,
        )
        plan["symbol"] = symbol.upper()
        plan["interval"] = interval
        plan["timestamp"] = datetime.now(timezone.utc).isoformat()
        plan["dry_run"] = True
        return plan

    def trend_scan(
        self,
        symbols: str = "BTCUSDT,ETHUSDT,SOLUSDT",
        interval: str = "60",
        limit: int = 300,
        min_confidence: float = 55.0,
    ) -> dict:
        """Rank a list of symbols by price-action trend quality."""
        results = []
        for sym in [s.strip().upper() for s in str(symbols).split(",") if s.strip()]:
            try:
                pa = self.price_action_analysis(
                    sym, interval, limit, min_confidence=min_confidence
                )
                sig = pa.get("signal", {})
                struct = pa.get("structure", {})
                ema = pa.get("ema_stack", {})
                state = struct.get("state", "UNKNOWN")
                align = ema.get("alignment", "NONE")
                score = 0
                if state == "UPTREND":
                    score += 2
                elif state == "DOWNTREND":
                    score -= 2
                if align == "BULLISH":
                    score += 2
                elif align == "BEARISH":
                    score -= 2
                if sig.get("action") in ("LONG", "SHORT"):
                    score += 1 if sig["action"] == "LONG" else -1
                results.append(
                    {
                        "symbol": sym,
                        "trend_score": score,
                        "structure": state,
                        "ema_alignment": align,
                        "signal": sig.get("action"),
                        "confidence": sig.get("confidence"),
                        "atr": pa.get("atr"),
                        "rsi": pa.get("rsi"),
                    }
                )
            except Exception as e:
                results.append({"symbol": sym, "error": str(e)})
        results.sort(key=lambda r: abs(r.get("trend_score", 0)), reverse=True)
        return {
            "status": "ok",
            "interval": interval,
            "results": results,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }

    def wbta_signal(
        self, symbol: str, interval: str = "15", use_tor: bool = False
    ) -> dict:
        """WBTA observatory snapshot: L2 orderbook, trade flow, funding/OI,
        35+ TA indicators and a multi-factor LONG/SHORT/HOLD signal."""
        try:
            from tools import bybit_wbta
        except ImportError as e:
            return {
                "status": "error",
                "msg": f"WBTA observatory unavailable ({e}). Install numpy/pandas.",
            }
        try:
            orch = bybit_wbta.MarketOrchestrator(
                symbol=symbol,
                interval=interval,
                delay=20,
                use_tor=use_tor,
                once=True,
                json_out=True,
                silent=True,
            )
            res = orch.run_cycle()
            res["status"] = "ok"
            res["source"] = "wbta_observatory"
            return res
        except Exception as e:
            return {"status": "error", "msg": f"WBTA cycle failed: {e}"}

    def full_signal(
        self, symbol: str, interval: str = "60", limit: int = 300
    ) -> dict:
        """Composite decision: price action + WBTA observatory + orderbook.

        The two engines must agree for a tradeable signal; conflicts and
        one-sided signals are reported as WAIT with reasons.
        """
        pa = self.price_action_analysis(symbol, interval, limit)
        wb = self.wbta_signal(symbol, "15" if interval == "15" else interval)
        ob = self.get_orderbook_analysis(symbol, depth=50)

        pa_action = pa.get("signal", {}).get("action", "WAIT")
        wb_action = (wb.get("trading_signal") or {}).get("action", "HOLD")
        wb_action = {"HOLD": "WAIT", "NEUTRAL": "WAIT"}.get(wb_action, wb_action)
        wb_failed = wb.get("status") == "error"

        if pa_action != "WAIT" and pa_action == wb_action:
            final = pa_action
            confidence = (
                float(pa.get("signal", {}).get("confidence", 0))
                + float((wb.get("trading_signal") or {}).get("confidence_score", 0))
            ) / 2
            note = "PA and WBTA agree"
        elif pa_action != "WAIT" and wb_action == "WAIT":
            final = "WAIT"
            confidence = float(pa.get("signal", {}).get("confidence", 0)) * 0.6
            note = (
                "WBTA observatory unavailable — waiting for flow confirmation"
                if wb_failed
                else "PA signal present but WBTA observatory is neutral — wait for flow confirmation"
            )
        elif wb_action != "WAIT" and pa_action == "WAIT":
            final = "WAIT"
            confidence = float((wb.get("trading_signal") or {}).get("confidence_score", 0)) * 0.6
            note = "WBTA flow signal present but price structure disagrees — wait for structure"
        else:
            final = "WAIT"
            confidence = 0
            note = "PA and WBTA conflict — no trade"

        return {
            "status": "ok",
            "symbol": symbol.upper(),
            "interval": interval,
            "decision": final,
            "confidence": round(confidence, 1),
            "note": note,
            "price_action": pa.get("signal", {}),
            "structure": pa.get("structure", {}),
            "ema_stack": pa.get("ema_stack", {}),
            "wbta_signal": wb.get("trading_signal"),
            "wbta_l2": wb.get("l2_signal"),
            "orderbook": ob,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }

    def backtest_pa(
        self,
        symbol: str,
        interval: str = "60",
        limit: int = 1000,
        risk_pct: float = 1.0,
        rr: float = 2.0,
        sl_atr: float = 1.5,
        start_balance: float = 1000.0,
        fee_rate: float = 0.00055,
        min_confidence: float = 55.0,
    ) -> dict:
        """Backtest the price-action strategy on historical klines.

        Historical results are indicative only — they never guarantee future
        profitability. Always paper-trade first.
        """
        from .backtest import run_backtest

        klines = self._get_klines_safely(symbol, interval, limit)
        if len(klines) < 300:
            return {"status": "error", "msg": f"Need >=300 klines, got {len(klines)}"}
        res = run_backtest(
            klines,
            risk_pct=risk_pct,
            rr=rr,
            sl_atr=sl_atr,
            start_balance=start_balance,
            fee_rate=fee_rate,
            min_confidence=min_confidence,
        )
        res["symbol"] = symbol.upper()
        res["interval"] = interval
        res["warning"] = (
            "Backtests use closed candles and conservative fills. "
            "They are research tools, not profit guarantees."
        )
        return res

    def execute_signal(
        self,
        symbol: str,
        interval: str = "60",
        side: str = "auto",
        risk_pct: float = 1.0,
        leverage: float = 1.0,
        rr_min: float = 2.0,
        min_confidence: float = 60.0,
        execute: bool = False,
        max_risk_pct: float = 2.0,
    ) -> dict:
        """Execute the price-action signal through the smart-order layer.

        Safety gates (all must pass):
          * signal confidence >= min_confidence
          * risk_pct <= max_risk_pct (hard cap)
          * requested side matches the signal side
          * execute must be explicitly True, otherwise a dry-run plan is returned
        """
        klines = self._get_klines_safely(symbol, interval, 300)
        if len(klines) < 60:
            return {"status": "error", "msg": f"Not enough klines for {symbol}"}

        engine = PriceActionEngine()
        signal = engine.price_action_signal(klines, min_confidence, rr_min)

        if side and str(side).lower() != "auto":
            want = "LONG" if str(side).lower() in ("buy", "long") else "SHORT"
            if signal.action != want:
                return {
                    "status": "no_trade",
                    "msg": f"Requested {want} but signal is {signal.action} — gated.",
                    "signal": {
                        "action": signal.action,
                        "confidence": signal.confidence,
                        "reasons": signal.reasons,
                        "filters": signal.filters,
                    },
                }
        if signal.action == "WAIT":
            return {
                "status": "no_trade",
                "msg": "No tradeable signal",
                "signal": {
                    "action": signal.action,
                    "confidence": signal.confidence,
                    "reasons": signal.reasons,
                    "filters": signal.filters,
                },
            }
        if risk_pct > max_risk_pct:
            return {
                "status": "gated",
                "msg": f"risk_pct {risk_pct}% exceeds hard cap {max_risk_pct}%",
            }

        balance = self._available_usdt()

        plan = engine.trade_plan(
            signal,
            balance=balance,
            risk_pct=risk_pct,
            leverage=leverage,
            rr_min=rr_min,
        )
        if plan.get("status") != "ok":
            return plan

        plan["symbol"] = symbol.upper()
        plan["interval"] = interval
        plan["execute"] = execute
        if not execute:
            plan["dry_run"] = True
            plan["msg"] = "Dry run — pass execute=true to place this trade."
            return plan

        # LIVE execution through the smart-order layer
        res = self.place_smart_order(
            symbol=symbol,
            side=signal.action.capitalize(),
            risk_pct=risk_pct,
            sl_price=signal.stop_loss,
            tp_price=signal.take_profits[0] if signal.take_profits else None,
        )
        plan["order_result"] = res
        plan["dry_run"] = False
        return plan

    def market_snapshot(self, symbol: str, interval: str = "60") -> dict:
        """One-shot overview: ticker, funding, orderbook and PA trend state."""
        ticker = self.get_ticker(symbol).get("list", [{}])[0]
        ob = self.get_orderbook_analysis(symbol, 25)
        st = self.calculate_supertrend(symbol, interval)
        reg = self.get_market_regime(symbol, interval)
        return {
            "status": "ok",
            "symbol": symbol.upper(),
            "price": ticker.get("lastPrice"),
            "price24h_pct": ticker.get("price24hPcnt"),
            "funding_rate": ticker.get("fundingRate"),
            "volume24h": ticker.get("volume24h"),
            "orderbook": ob,
            "supertrend": st,
            "regime": reg,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }

    def journal_stats(self, limit: int = 100) -> dict:
        """Trading performance summary (win rate, profit factor, net PnL)."""
        return self.get_performance_summary(limit=limit)
