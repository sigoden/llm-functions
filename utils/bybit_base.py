"""Pure-math PnL / exit-price helpers for the Bybit trading suite.

These functions are intentionally free of any network or account state so
they can be unit-tested in isolation and reused by every entry point
(`tools/bybit/terminal.py`, `bybit_suite.py`, backtests, dashboards).
"""

from __future__ import annotations

from typing import Union


def _direction(side: str) -> float:
    """Return +1 for a long (Buy) position and -1 for a short (Sell)."""
    return 1.0 if str(side).strip().lower() in ("buy", "long", "b", "l") else -1.0


def _fees_in_usdt(fee_rate: float, entry_price: float, exit_price: float, size: float) -> float:
    """Estimate round-trip taker fees in quote (USDT) terms."""
    return abs(fee_rate) * size * (float(entry_price) + float(exit_price))


def calculate_pnl(
    entry_price: float,
    exit_price: float,
    size: float,
    side: str,
    fees: float = 0.0,
    leverage: float = 1.0,
) -> dict:
    """Calculate gross/net PnL in USDT and ROE % on the initial margin.

    Args:
        entry_price: average entry price.
        exit_price:  exit (mark) price.
        size:        contract quantity.
        side:        "Buy" (long) or "Sell" (short).
        fees:        fee *rate* (e.g. 0.00055). A value below 0.01 is treated as
                     a rate, otherwise it is treated as a flat USDT fee.
        leverage:    leverage used (ROE is computed on entry notional / leverage).
    """
    entry = float(entry_price)
    exit_ = float(exit_price)
    qty = float(size)
    d = _direction(side)

    gross = (exit_ - entry) * qty * d

    if 0 < float(fees) < 0.01:
        fee_usdt = _fees_in_usdt(float(fees), entry, exit_, qty)
    else:
        fee_usdt = float(fees)  # flat fee already in USDT

    net = gross - fee_usdt
    margin = (entry * qty) / max(float(leverage) or 1.0, 1.0)

    return {
        "gross_pnl": round(gross, 6),
        "fees": round(fee_usdt, 6),
        "net_pnl": round(net, 6),
        "roe_pct": round(net / margin * 100, 4) if margin else 0.0,
        "side": side,
        "leverage": float(leverage),
    }


def calculate_exit_price(
    entry_price: float,
    target_pnl: float,
    size: float,
    side: str,
    fees: float = 0.0,
) -> float:
    """Price at which the position must exit to realize `target_pnl` USDT (net of fees).

    Solves:  target = (exit - entry) * size * d - fee_rate * size * (entry + exit)
    """
    entry = float(entry_price)
    qty = float(size)
    d = _direction(side)
    target = float(target_pnl)

    if qty <= 0:
        raise ValueError("size must be positive")

    if 0 < float(fees) < 0.01:
        rate = float(fees)
        num = target + qty * d * entry + rate * qty * entry
        den = qty * d - rate * qty
    else:
        # Flat fee: treat as a reduction of the target.
        num = (target + float(fees)) + qty * d * entry
        den = qty * d

    if abs(den) < 1e-15:
        raise ValueError("denominator is zero; cannot solve for exit price")
    return round(num / den, 8)


def format_qty_to_step(qty: float, qty_step: Union[float, str]) -> str:
    """Floor a quantity to the instrument's lot-size step and return a string."""
    step = float(qty_step or 0)
    if step <= 0:
        return str(qty)
    prec = len(str(step).split(".")[-1]) if "." in str(step) else 0
    stepped = (float(qty) // step) * step
    return f"{stepped:.{prec}f}"


def format_price_to_tick(price: float, tick_size: Union[float, str]) -> str:
    """Round a price to the instrument's tick size and return a string."""
    tick = float(tick_size or 0)
    if tick <= 0:
        return str(price)
    prec = len(str(tick).split(".")[-1]) if "." in str(tick) else 0
    rounded = round(float(price) / tick) * tick
    return f"{rounded:.{prec}f}"
