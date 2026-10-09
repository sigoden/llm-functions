"""Trading engines: exit-strategy planning and (micro)statistical arbitrage.

Pure logic modules — no network I/O. They consume already-fetched orderbook
snapshots and indicator values, which keeps them testable and reusable from
the unified `tools/bybit` package, the CLI, and backtests.
"""

from __future__ import annotations

from typing import List, Optional, Sequence, Tuple


# ══════════════════════════════════════════════════════════════════════════
# EXIT STRATEGY ENGINE
# ══════════════════════════════════════════════════════════════════════════


class ExitStrategyEngine:
    """Builds multi-level exit plans (scale-outs, final target, stop, trailing)."""

    def calculate_multi_exit_levels(
        self,
        entry_price: float,
        position_size: float,
        side: str,
        current_price: float,
        atr: float,
        config: Optional[dict] = None,
        bids: Optional[Sequence[Sequence[float]]] = None,
        asks: Optional[Sequence[Sequence[float]]] = None,
    ) -> dict:
        """Produce a laddered exit plan.

        Returns:
            dict with `direction`, `stop_loss`, `take_profits` (each with
            qty/price/rr), `trailing` suggestion and `rr_plan` summary.
        """
        cfg = config or {}
        long = str(side).strip().lower() in ("buy", "long")

        atr = float(atr or 0) or abs(float(current_price) - float(entry_price)) or float(entry_price) * 0.005
        entry = float(entry_price)
        current = float(current_price)
        size = float(position_size)

        sl_mult = float(cfg.get("sl_atr_mult", 1.5))
        tp_mults = [float(m) for m in cfg.get("tp_atr_mults", [1.5, 3.0, 5.0])]
        scale = [float(p) for p in cfg.get("scale_pcts", [0.5, 0.3, 0.2])]
        while len(scale) < len(tp_mults):
            scale.append(0.0)
        scale = scale[: len(tp_mults)]

        # Sanity checks on direction
        if long:
            stop = entry - atr * sl_mult
            if stop >= current:
                stop = current - atr * 0.5
        else:
            stop = entry + atr * sl_mult
            if stop <= current:
                stop = current + atr * 0.5

        # Liquidity-aware targets: pull visible walls from the book if provided
        wall_prices: List[float] = []
        if long and asks:
            wall_prices = [float(p) for p, _ in sorted(asks, key=lambda x: float(x[0])) if float(p) > current]
        elif not long and bids:
            wall_prices = [float(p) for p, _ in sorted(bids, key=lambda x: float(x[0]), reverse=True) if float(p) < current]

        tps = []
        remaining = size
        for i, mult in enumerate(tp_mults):
            target = (entry + atr * mult) if long else (entry - atr * mult)
            # Snap to a nearby liquidity wall when one exists within half an ATR
            if wall_prices:
                nearest = min(wall_prices, key=lambda p: abs(p - target))
                if abs(nearest - target) <= atr * 0.5:
                    target = nearest
                    wall_prices.remove(nearest)
            qty = remaining * scale[i] if i < len(scale) - 1 else remaining
            remaining -= qty
            tps.append(
                {
                    "level": i + 1,
                    "price": round(target, 8),
                    "qty": round(qty, 8),
                    "rr": round(mult / sl_mult, 2),
                }
            )

        return {
            "status": "ok",
            "direction": "LONG" if long else "SHORT",
            "stop_loss": round(stop, 8),
            "take_profits": tps,
            "trailing": {
                "enabled": bool(cfg.get("trailing", True)),
                "activation_rr": float(cfg.get("trailing_activation_rr", 1.0)),
                "distance_atr": float(cfg.get("trailing_atr", 2.0)),
            },
            "rr_plan": {
                "min_rr": round(tp_mults[0] / sl_mult, 2) if tp_mults else None,
                "max_rr": round(tp_mults[-1] / sl_mult, 2) if tp_mults else None,
                "sl_distance_atr": sl_mult,
            },
        }


# ══════════════════════════════════════════════════════════════════════════
# STATISTICAL ARBITRAGE ENGINE (microstructure flavour)
# ══════════════════════════════════════════════════════════════════════════


class StatisticalArbitrageEngine:
    """Microstructure arbitrage / market-making opportunity scoring."""

    def analyze_market_making_opportunity(
        self,
        bid_prices: Sequence[float],
        bid_sizes: Sequence[float],
        ask_prices: Sequence[float],
        ask_sizes: Sequence[float],
        current_price: float,
        inventory_ratio: float = 0.0,
    ) -> dict:
        """Score the book for market-making: spread, skew, edge after fees."""
        bids = [(float(p), float(q)) for p, q in zip(bid_prices, bid_sizes)]
        asks = [(float(p), float(q)) for p, q in zip(ask_prices, ask_sizes)]

        if not bids or not asks:
            return {"status": "error", "msg": "Empty book"}

        best_bid, best_ask = bids[0][0], asks[0][0]
        mid = (best_bid + best_ask) / 2.0
        spread = best_ask - best_bid
        spread_bps = (spread / mid * 10000) if mid else 0.0

        depth_bid = sum(q for _, q in bids)
        depth_ask = sum(q for _, q in asks)
        skew = (depth_bid - depth_ask) / (depth_bid + depth_ask) if (depth_bid + depth_ask) else 0.0

        # Inventory-aware quoting offset
        offset = -inventory_ratio * spread * 0.5  # long inventory -> quote lower

        # Maker edge after a conservative 1 bp round-trip cost
        fee_bps = 1.0
        edge_bps = spread_bps - fee_bps

        viable = edge_bps > 0 and abs(skew) < 0.6
        return {
            "status": "ok",
            "best_bid": best_bid,
            "best_ask": best_ask,
            "mid": round(mid, 8),
            "spread": spread,
            "spread_bps": round(spread_bps, 2),
            "depth_bid": round(depth_bid, 2),
            "depth_ask": round(depth_ask, 2),
            "skew": round(skew, 4),
            "suggested_bid": round(mid - spread * 0.5 + offset, 8),
            "suggested_ask": round(mid + spread * 0.5 + offset, 8),
            "maker_edge_bps": round(edge_bps, 2),
            "viable": viable,
        }

    def analyze_statistical_arbitrage(self, current_price: float) -> dict:
        """Placeholder-style rolling-stat arb snapshot.

        Real stat-arb requires a pair universe; this returns the baseline
        state so callers get a stable interface. Pairs z-score logic lives in
        `MarketDataMixin.get_cointegrated_spread`.
        """
        return {
            "status": "ok",
            "note": "Stat-arb requires a symbol pair; use get_cointegrated_spread(a, b) for the z-score.",
            "current_price": float(current_price),
        }
