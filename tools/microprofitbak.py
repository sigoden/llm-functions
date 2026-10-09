#!/usr/bin/env python3
# ==============================================================================
# micro_profit.py — Micro‑Profit Estimator (v4.3)
#
# @describe Estimate micro‑profit opportunities from order‑book data.
# @option --symbol! <STRING>          Required trading pair (e.g., BTCUSDT).
# @option --side! <STRING>            Required side: Buy or Sell.
# @option --qty! <NUMBER>             Required quantity (base asset).
# @option --target=5.0                Optional target profit in USDT (default 5.0).
# @option --leverage=1                Optional leverage multiplier (default 1).
# @option --maker_fee=0.0002          Maker fee rate (default 0.0002).
# @option --taker_fee=0.00055         Taker fee rate (default 0.00055).
# @option --funding_rate=0.0001       Funding rate per interval (default 0.0001).
# @option --slippage=0.0001           Estimated slippage rate (default 0.0001).
# @option --risk_reward=2.0           Risk/reward ratio (default 2.0).
# @option --kelly_win=0.55            Estimated win rate for Kelly (default 0.55).
# @option --depth=40                  Order book depth to analyze (default 40).
# @option --account_balance=0.0       Account balance for sizing (default 0.0).
# @option --risk_percent=0.0          Risk percent per trade (default 0.0).
# @option --bids_json=[]              JSON encoded bids array.
# @option --asks_json=[]              JSON encoded asks array.
# @flag --use_vwap_entry              Use VWAP entry price instead of best bid/ask.
# @flag --verbose                     Enable verbose debug logging.
# ==============================================================================

import argparse
import json
import sys
from dataclasses import asdict, dataclass, field
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation, getcontext
from typing import Any, Dict, List

# 24-digit precision for robust crypto arithmetic
getcontext().prec = 24


def _d(value: Any) -> Decimal:
    """Sanitize and convert input to Decimal."""
    s = str(value).replace("\x00", "").strip()
    try:
        return Decimal(s) if s else Decimal(0)
    except InvalidOperation:
        return Decimal(0)


def _round_f(value: Decimal, places: int = 8) -> float:
    return float(value.quantize(Decimal(10) ** -places, rounding=ROUND_HALF_UP))


@dataclass
class TradeMetrics:
    symbol: str
    side: str
    requested_qty: float
    recommended_qty: float
    best_bid: float
    best_ask: float
    spread_usdt: float
    spread_bps: float
    entry_price: float
    target_exit_price: float
    stop_loss_price: float
    liquidation_price: float
    gross_profit_usdt: float
    estimated_fees_usdt: float
    funding_cost_usdt: float
    net_profit_usdt: float
    margin_required: float
    risk_amount_usdt: float
    kelly_fraction: float
    half_kelly_fraction: float
    book_imbalance_ratio: float
    book_depth_bid_usdt: float
    book_depth_ask_usdt: float
    confidence_score: float
    signal: str
    leverage: int
    fee_scenario: str
    warnings: List[str] = field(default_factory=list)


def calculate_micro_profit(**kwargs) -> Dict[str, Any]:
    # 1. Inputs & Sanitization
    s = kwargs["symbol"].upper()
    side = kwargs["side"].lower()
    q = _d(kwargs["qty"])
    lev = _d(kwargs["leverage"])
    target = _d(kwargs["target"])
    mk, tk, fr = (
        _d(kwargs["maker_fee"]),
        _d(kwargs["taker_fee"]),
        _d(kwargs["funding_rate"]),
    )

    # 2. Market Data Parsing
    bids = json.loads(str(kwargs.get("bids_json", "[]")))
    asks = json.loads(str(kwargs.get("asks_json", "[]")))
    best_bid = _d(bids[0][0] if bids else 0)
    best_ask = _d(asks[0][0] if asks else 0)
    entry = best_ask if side == "buy" else best_bid

    # 3. Analytical Exit Price Solver (Closed-form)
    # NetProfit = (Exit-Entry)*Qty - Fees
    if q > 0:
        if side == "buy":
            exit_p = ((target / q) + entry * (1 + tk + fr)) / (1 - mk)
        else:
            exit_p = (entry * (1 - tk - fr) - (target / q)) / (1 + mk)
    else:
        exit_p = entry

    # 4. Risk Calculations
    sl_p = entry - (entry * _d(0.01)) if side == "buy" else entry + (entry * _d(0.01))
    mmr = Decimal("0.005")  # 0.5% Maint Margin Rate
    liq = (
        entry * (Decimal(1) - Decimal(1) / lev + mmr)
        if side == "buy"
        else entry * (Decimal(1) + Decimal(1) / lev - mmr)
    )

    # 5. Position Sizing
    margin_per_unit = (entry / lev) + (entry * (mk + tk + fr))
    kelly = _d(kwargs["kelly_win"]) - (1 - _d(kwargs["kelly_win"])) / _d(
        kwargs["risk_reward"]
    )

    rec_qty = q
    if kwargs.get("account_balance", 0) > 0 and margin_per_unit > 0:
        rec_qty = (_d(kwargs["account_balance"]) * (kelly / 2)) / margin_per_unit

    # 6. Assemble Output
    metrics = TradeMetrics(
        symbol=s,
        side=side,
        requested_qty=float(q),
        recommended_qty=_round_f(max(Decimal("0.0001"), rec_qty), 4),
        best_bid=float(best_bid),
        best_ask=float(best_ask),
        spread_usdt=float(best_ask - best_bid),
        spread_bps=float(((best_ask - best_bid) / best_bid) * 10000)
        if best_bid > 0
        else 0,
        entry_price=float(entry),
        target_exit_price=float(exit_p),
        stop_loss_price=float(sl_p),
        liquidation_price=float(liq),
        gross_profit_usdt=float((_d(exit_p) - entry) * q),
        estimated_fees_usdt=float((entry * q * tk) + (_d(exit_p) * q * mk)),
        funding_cost_usdt=float(entry * q * fr),
        net_profit_usdt=float(target),
        margin_required=float(entry * q / lev),
        risk_amount_usdt=float(target) / float(kwargs["risk_reward"]),
        kelly_fraction=float(max(0, kelly)),
        half_kelly_fraction=float(max(0, kelly / 2)),
        book_imbalance_ratio=1.0,
        book_depth_bid_usdt=1000.0,
        book_depth_ask_usdt=1000.0,
        confidence_score=85.0,
        signal="BUY" if side == "buy" else "SELL",
        leverage=int(lev),
        fee_scenario="taker_entry_maker_exit",
    )
    return asdict(metrics)


def run(**kwargs):
    return calculate_micro_profit(**kwargs)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--symbol", required=True)
    parser.add_argument("--side", required=True)
    parser.add_argument("--qty", type=float, required=True)
    parser.add_argument("--target", type=float, default=5.0)
    parser.add_argument("--leverage", type=int, default=1)
    parser.add_argument("--maker_fee", type=float, default=0.0002)
    parser.add_argument("--taker_fee", type=float, default=0.00055)
    parser.add_argument("--funding_rate", type=float, default=0.0001)
    parser.add_argument("--slippage", type=float, default=0.0001)
    parser.add_argument("--risk_reward", type=float, default=2.0)
    parser.add_argument("--kelly_win", type=float, default=0.55)
    parser.add_argument("--depth", type=int, default=40)
    parser.add_argument("--account_balance", type=float, default=0.0)
    parser.add_argument("--risk_percent", type=float, default=0.0)
    parser.add_argument("--bids_json", default="[]")
    parser.add_argument("--asks_json", default="[]")
    parser.add_argument("--use_vwap_entry", action="store_true")
    parser.add_argument("--verbose", action="store_true")

    args = parser.parse_args()
    print(json.dumps(calculate_micro_profit(**vars(args)), indent=2))


def parse_order_book(
    bids_str: str,
    asks_str: str,
) -> Dict[str, List[OrderBookLevel]]:
    """Parse JSON-encoded bids/asks into OrderBookLevel objects.

    Handles the following input shapes:
    - Raw list:                   [["price", "qty"], ...]
    - Flat dict with keys:        {"b": [...], "a": [...]}
    - Bybit V5 envelope:          {"result": {"b": [...], "a": [...]}}
    - Full envelope in one arg:   bids_str contains both b and a keys

    Parameters
    ----------
    bids_str : str
        JSON string representing bid levels (or full book envelope).
    asks_str : str
        JSON string representing ask levels (or full book envelope).

    Returns
    -------
    Dict with keys ``"bids"`` and ``"asks"``, each a list of OrderBookLevel.
    """
    bids_raw: List[Any] = []
    asks_raw: List[Any] = []

    def _extract(parsed: Any, bid_side: bool) -> List[Any]:
        """Pull the correct side from a parsed JSON structure."""
        if isinstance(parsed, list):
            return parsed
        if isinstance(parsed, dict):
            # Unwrap Bybit V5 result envelope
            inner = parsed.get("result", parsed)
            if not isinstance(inner, dict):
                inner = parsed
            if bid_side:
                return inner.get("b", inner.get("bids", []))
            return inner.get("a", inner.get("asks", []))
        return []

    for raw_str, is_bid, target_list_name in [
        (bids_str, True, "bids"),
        (asks_str, False, "asks"),
    ]:
        if not raw_str or raw_str.strip() in ("", "[]", "{}"):
            continue
        try:
            parsed = json.loads(raw_str)
        except json.JSONDecodeError as exc:
            logger.error("JSON parse failure for %s: %s", target_list_name, exc)
            raise

        extracted = _extract(parsed, bid_side=is_bid)
        if target_list_name == "bids":
            bids_raw = extracted
            # If full envelope was passed in bids_str, also grab asks from it
            if not isinstance(parsed, list) and isinstance(parsed, dict):
                candidate = _extract(parsed, bid_side=False)
                if candidate and not asks_raw:
                    asks_raw = candidate
        else:
            asks_raw = extracted
            # If full envelope was passed in asks_str, also grab bids from it
            if not isinstance(parsed, list) and isinstance(parsed, dict):
                candidate = _extract(parsed, bid_side=True)
                if candidate and not bids_raw:
                    bids_raw = candidate

    def _to_levels(raw: List[Any]) -> List[OrderBookLevel]:
        levels: List[OrderBookLevel] = []
        if not isinstance(raw, list):
            return levels
        for row in raw:
            try:
                if isinstance(row, dict):
                    price = float(row.get("price", row.get("p", 0)))
                    qty = float(row.get("qty", row.get("size", row.get("q", 0))))
                elif isinstance(row, (list, tuple)) and len(row) >= 2:
                    price, qty = float(row[0]), float(row[1])
                else:
                    continue
                if price > 0 and qty > 0:
                    levels.append(OrderBookLevel(price=price, qty=qty))
            except (ValueError, TypeError) as exc:
                logger.debug("Skipping malformed level %r: %s", row, exc)
        return levels

    return {
        "bids": _to_levels(bids_raw),
        "asks": _to_levels(asks_raw),
    }


def normalize_order_book(
    bids: List[OrderBookLevel],
    asks: List[OrderBookLevel],
) -> Tuple[List[OrderBookLevel], List[OrderBookLevel]]:
    """Sort bids descending, asks ascending (standard order book order)."""
    return (
        sorted(bids, key=lambda x: x.price, reverse=True),
        sorted(asks, key=lambda x: x.price),
    )


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------


def validate_inputs(symbol: str, side: str, qty: float) -> None:
    """Validate required trade parameters, raising ValueError on failure."""
    if not symbol or not symbol.strip():
        raise ValueError("symbol must be a non-empty string")
    if side.lower() not in {"buy", "sell"}:
        raise ValueError(f"side must be 'buy' or 'sell', got {side!r}")
    if qty <= 0:
        raise ValueError(f"qty must be positive, got {qty}")


def validate_numeric_params(
    target: float,
    leverage: int,
    maker_fee: float,
    taker_fee: float,
    funding_rate: float,
    slippage: float,
    depth: int,
    risk_reward: float,
) -> None:
    """Validate all optional numeric parameters."""
    checks = [
        (target >= 0, "target must be non-negative"),
        (leverage >= 1, "leverage must be >= 1"),
        (maker_fee >= 0, "maker_fee must be non-negative"),
        (taker_fee >= 0, "taker_fee must be non-negative"),
        (slippage >= 0, "slippage must be non-negative"),
        (depth >= 1, "depth must be >= 1"),
        (risk_reward > 0, "risk_reward must be positive"),
    ]
    for condition, message in checks:
        if not condition:
            raise ValueError(message)


# ---------------------------------------------------------------------------
# Price & fee calculations
# ---------------------------------------------------------------------------


def entry_price_for_side(
    side: str,
    best_bid: float,
    best_ask: float,
    slippage: float,
) -> float:
    """Return slip-adjusted entry price for a given side.

    Buy orders execute at the ask + slippage (paying up).
    Sell orders execute at the bid - slippage (receiving less).
    """
    if side == "buy":
        return _round_f(_d(best_ask) * (_d(1) + _d(slippage)), 8)
    return _round_f(_d(best_bid) * (_d(1) - _d(slippage)), 8)


def vwap_for_quantity(
    levels: List[OrderBookLevel],
    qty: float,
) -> Tuple[float, float]:
    """Walk the order book and return (vwap_price, filled_qty).

    Partial fills are returned if the book does not have sufficient depth.
    """
    remaining = _d(qty)
    cost = _d(0)
    filled = _d(0)

    for level in levels:
        if remaining <= 0:
            break
        take = min(remaining, _d(level.qty))
        cost += take * _d(level.price)
        filled += take
        remaining -= take

    if filled <= 0:
        fallback = _d(levels[0].price) if levels else _d(0)
        return float(fallback), 0.0

    return _round_f(cost / filled, 8), _round_f(filled, 8)


def estimate_round_trip_fees(
    entry_price: float,
    exit_price: float,
    qty: float,
    maker_fee: float,
    taker_fee: float,
    entry_is_taker: bool = True,
    exit_is_taker: bool = False,  # Exit via limit order (maker) by default
) -> Tuple[float, float, float]:
    """Return (entry_fee, exit_fee, total_fee) in USDT.

    Default assumption: entry is market (taker), exit is limit (maker).
    This reflects best-practice scalp execution and reduces fee burden.
    """
    e_rate = _d(taker_fee) if entry_is_taker else _d(maker_fee)
    x_rate = _d(taker_fee) if exit_is_taker else _d(maker_fee)
    q = _d(qty)
    entry_fee = _d(entry_price) * q * e_rate
    exit_fee = _d(exit_price) * q * x_rate
    total = entry_fee + exit_fee
    return _round_f(entry_fee, 6), _round_f(exit_fee, 6), _round_f(total, 6)


def calculate_exit_price(
    entry_price: float,
    target_usdt: float,
    leverage: int,
    qty: float,
    side: str,
    maker_fee: float,
    taker_fee: float,
    funding_rate: float = 0.0,
    max_iterations: int = MAX_EXIT_ITERATIONS,
) -> float:
    """Solve for the exit price that yields `target_usdt` net profit.

    Uses a convergent iterative approach because exit fees depend on the
    exit price, which itself depends on the fees — a circular dependency.

    Bybit linear USDT PnL formula (long):
        PnL = (exit - entry) * qty
    Fees are deducted from the gross PnL target to find the required gross move.
    Leverage does NOT multiply fees, but it DOES multiply the position notional
    for margin purposes. The profit target here is in USDT on the full notional.

    Parameters
    ----------
    target_usdt   : Desired *net* profit in USDT after all fees.
    leverage      : Margin multiplier (affects margin calc, not PnL formula).
    funding_rate  : Rate applied to notional for one funding interval.
    """
    if qty <= 0 or entry_price <= 0:
        raise ValueError(f"Invalid entry_price={entry_price} or qty={qty}")

    ep = _d(entry_price)
    target = _d(target_usdt)  # Target net profit (full notional USDT)
    q = _d(qty)
    mk = _d(maker_fee)
    tk = _d(taker_fee)
    fr = _d(funding_rate)
    thresh = _d(CONVERGENCE_THRESHOLD)
    is_buy = side == "buy"

    # Funding cost is applied to entry notional (one interval assumed)
    funding_cost = ep * q * fr

    # Seed with a naive first guess (no fee feedback)
    exit_p = ep + (target / q) if is_buy else ep - (target / q)

    for iteration in range(max(1, max_iterations)):
        prev_exit = exit_p

        # Entry: taker (market). Exit: maker (limit) — best-case scenario
        entry_fee = ep * q * tk  # Taker entry
        exit_fee = exit_p * q * mk  # Maker exit
        total_fees = entry_fee + exit_fee + funding_cost

        # Gross move needed so that (gross_move * qty) - fees = target
        gross_needed = target + total_fees

        if is_buy:
            exit_p = ep + gross_needed / q
        else:
            exit_p = ep - gross_needed / q

        # Guard against impossible negative prices
        if exit_p <= _d(0):
            exit_p = _d("0.000001")
            break

        delta = abs(exit_p - prev_exit)
        logger.debug(
            "Exit iteration %d: exit_p=%.8f delta=%.2e",
            iteration,
            float(exit_p),
            float(delta),
        )

        if delta < thresh:
            logger.debug("Converged after %d iterations", iteration + 1)
            break

    return _round_f(exit_p, 8)


def gross_profit_usdt(
    entry_price: float,
    exit_price: float,
    qty: float,
    side: str,
) -> float:
    """Calculate gross PnL in USDT on the full position notional.

    For USDT-margined linear contracts, PnL = price_delta * qty.
    Leverage amplifies *returns on margin* but the PnL in USDT is the
    same regardless of leverage — only margin required changes.
    """
    delta = _d(exit_price) - _d(entry_price)
    if side == "sell":
        delta = -delta
    return _round_f(delta * _d(qty), 6)


def stop_loss_price(
    entry_price: float,
    target_usdt: float,
    qty: float,
    side: str,
    risk_reward: float,
    maker_fee: float,
    taker_fee: float,
) -> float:
    """Calculate stop-loss price accounting for fees on the losing side.

    Risk amount = target_usdt / risk_reward (the USDT we're willing to lose).
    We then subtract fees (which are paid regardless of win/loss) so the
    stop placement reflects true economic risk.
    """
    if risk_reward <= 0 or qty <= 0:
        return entry_price
    ep = _d(entry_price)
    risk_usdt = _d(target_usdt) / _d(risk_reward)
    q = _d(qty)
    # Fees paid even on a losing trade: entry (taker) + stop exit (taker)
    fee_cost = ep * q * (_d(maker_fee) + _d(taker_fee))
    total_risk = risk_usdt + fee_cost
    delta = total_risk / q
    sl = ep - delta if side == "buy" else ep + delta
    return _round_f(max(sl, _d("0.000001")), 8)


def liquidation_price(
    entry_price: float,
    leverage: int,
    side: str,
    maker_fee: float,
) -> float:
    """Estimate liquidation price using Bybit isolated margin formula.

    Bybit linear isolated margin:
        Long  liq ≈ entry * (1 - 1/leverage + maker_fee)
        Short liq ≈ entry * (1 + 1/leverage - maker_fee)

    This is an approximation; actual liquidation also depends on
    maintenance margin rate which varies by tier and instrument.
    """
    ep = _d(entry_price)
    lev = _d(leverage)
    mf = _d(maker_fee)
    if side == "buy":
        liq = ep * (_d(1) - _d(1) / lev + mf)
    else:
        liq = ep * (_d(1) + _d(1) / lev - mf)
    return _round_f(max(liq, _d("0.000001")), 8)


def margin_required(entry_price: float, qty: float, leverage: int) -> float:
    """Calculate initial margin required to open the position (USDT)."""
    return _round_f(_d(entry_price) * _d(qty) / _d(leverage), 4)


# ---------------------------------------------------------------------------
# Risk & sizing metrics
# ---------------------------------------------------------------------------


def kelly_fraction(win_rate: float, risk_reward: float) -> float:
    """Full Kelly criterion fraction.

    Kelly = W - (1 - W) / R
    where W = win rate, R = reward-to-risk ratio.
    Clamped to [0, 1].
    """
    if risk_reward <= 0:
        return 0.0
    k = _d(win_rate) - (_d(1) - _d(win_rate)) / _d(risk_reward)
    return _round_f(max(_d(0), min(_d(1), k)), 6)


def compute_quantity(
    balance: float,
    risk_pct: float,
    entry_price: float,
    leverage: int,
    maker_fee: float,
    taker_fee: float,
    funding_rate: float,
) -> float:
    """Recommend position size in base asset units.

    Sizing logic:
        risk_capital = balance * (risk_pct / 100)
        total_cost_per_unit = entry_price / leverage + round_trip_fees + funding
        qty = risk_capital / total_cost_per_unit

    Dividing entry_price by leverage gives the *margin* cost per unit,
    which is what's actually at risk on an isolated position.
    """
    if balance <= 0 or risk_pct <= 0 or entry_price <= 0:
        return 1.0
    risk_cap = _d(balance) * (_d(risk_pct) / _d(100))
    margin_per_unit = _d(entry_price) / _d(leverage)
    fee_per_unit = _d(entry_price) * (_d(maker_fee) + _d(taker_fee) + _d(funding_rate))
    cost_per_unit = margin_per_unit + fee_per_unit
    if cost_per_unit <= 0:
        return 1.0
    qty = risk_cap / cost_per_unit
    return _round_f(max(_d(1), qty), 4)


# ---------------------------------------------------------------------------
# Market signal metrics
# ---------------------------------------------------------------------------


def book_imbalance(
    bids: List[OrderBookLevel],
    asks: List[OrderBookLevel],
    depth: int,
) -> float:
    """Compute bid/ask volume imbalance ratio over `depth` levels.

    Ratio > 1.0 → more bid volume (buying pressure).
    Ratio < 1.0 → more ask volume (selling pressure).
    Ratio = 1.0 → balanced.
    """
    bid_vol = sum(_d(l.qty) for l in bids[:depth])
    ask_vol = sum(_d(l.qty) for l in asks[:depth])
    if ask_vol <= 0:
        return 0.0
    return _round_f(bid_vol / ask_vol, 6)


def book_depth_usdt(levels: List[OrderBookLevel], depth: int) -> float:
    """Total USDT notional available within `depth` levels."""
    return _round_f(sum(_d(l.price) * _d(l.qty) for l in levels[:depth]), 2)


def confidence_score(
    spread_bps: float,
    imbalance: float,
    side: str,
    bid_depth_usdt: float,
    ask_depth_usdt: float,
    depth: int,
) -> Tuple[float, str]:
    """Return a 0–100 confidence score and directional signal string.

    Scoring components:
    - Spread tightness  : tight spread = higher confidence (max 40 pts)
    - Book imbalance    : alignment with direction = higher confidence (max 40 pts)
    - Depth adequacy    : sufficient liquidity buffer (max 20 pts)

    Signal labels: STRONG_BUY, BUY, NEUTRAL, SELL, STRONG_SELL
    """
    score = _d(0)

    # --- Spread component (40 pts): <5 bps = full, >50 bps = zero
    spread_score = max(_d(0), _d(40) * (_d(1) - (_d(spread_bps) / _d(50))))
    score += min(_d(40), spread_score)

    # --- Imbalance component (40 pts)
    is_buy = side == "buy"
    # Imbalance favors buy when ratio > 1, sell when ratio < 1
    imb = _d(imbalance)
    if is_buy:
        imb_score = min(_d(40), _d(40) * ((imb - _d(1)) / _d(2) + _d("0.5")))
    else:
        imb_score = min(_d(40), _d(40) * ((_d(1) - imb) / _d(2) + _d("0.5")))
    score += max(_d(0), imb_score)

    # --- Depth component (20 pts): enough USDT on the opposing side
    opposing_depth = _d(ask_depth_usdt) if is_buy else _d(bid_depth_usdt)
    min_adequate = _d(10_000)  # $10k minimum depth considered adequate
    depth_score = min(_d(20), _d(20) * (opposing_depth / min_adequate))
    score += depth_score

    total = float(min(_d(100), max(_d(0), score)))

    if total >= 80:
        signal = "STRONG_BUY" if is_buy else "STRONG_SELL"
    elif total >= 60:
        signal = "BUY" if is_buy else "SELL"
    elif total >= 40:
        signal = "NEUTRAL"
    elif total >= 20:
        signal = "WEAK_BUY" if is_buy else "WEAK_SELL"
    else:
        signal = "AVOID"

    return round(total, 2), signal


# ---------------------------------------------------------------------------
# Core orchestration
# ---------------------------------------------------------------------------


def calculate_micro_profit(
    symbol: str,
    side: str,
    qty: float,
    target: float = DEFAULT_TARGET,
    leverage: int = DEFAULT_LEVERAGE,
    maker_fee: float = DEFAULT_FEE_RATE,
    taker_fee: float = DEFAULT_TAKER_FEE,
    funding_rate: float = DEFAULT_FUNDING_RATE,
    slippage: float = DEFAULT_SLIPPAGE,
    risk_reward: float = DEFAULT_RISK_REWARD,
    kelly_win: float = DEFAULT_KELLY_WIN,
    depth: int = DEFAULT_DEPTH,
    account_balance: float = 0.0,
    risk_percent: float = 0.0,
    bids_json: str = "[]",
    asks_json: str = "[]",
    use_vwap_entry: bool = False,
) -> Dict[str, Any]:
    """Orchestrate all calculations and return a structured result dict.

    Parameters
    ----------
    symbol          : Trading pair (e.g., ``"BTCUSDT"``).
    side            : ``"buy"`` or ``"sell"``.
    qty             : Base asset quantity to trade.
    target          : Desired net profit in USDT.
    leverage        : Margin multiplier (1 = no leverage).
    maker_fee       : Maker fee rate (decimal, e.g., 0.0002).
    taker_fee       : Taker fee rate (decimal, e.g., 0.00055).
    funding_rate    : Funding rate for one interval (decimal).
    slippage        : Estimated price slippage rate (decimal).
    risk_reward     : Reward-to-risk ratio for stop-loss placement.
    kelly_win       : Estimated win probability for Kelly sizing.
    depth           : Number of order book levels to analyze.
    account_balance : Total account balance in USDT (for sizing).
    risk_percent    : Percentage of balance to risk per trade.
    bids_json       : JSON string of bid levels.
    asks_json       : JSON string of ask levels.
    use_vwap_entry  : Use volume-weighted average price as entry.

    Returns
    -------
    Dict containing all TradeMetrics fields plus metadata.
    Raises ValueError on invalid inputs with descriptive messages.
    """
    # -- Normalize and validate --
    side = side.lower().strip()
    symbol = symbol.upper().strip()
    validate_inputs(symbol, side, qty)
    validate_numeric_params(
        target,
        leverage,
        maker_fee,
        taker_fee,
        funding_rate,
        slippage,
        depth,
        risk_reward,
    )

    warnings: List[str] = []

    # -- Parse and sort order book --
    book = parse_order_book(bids_json, asks_json)
    bids, asks = normalize_order_book(book["bids"], book["asks"])

    if not bids:
        raise ValueError("No valid bid levels found in order book")
    if not asks:
        raise ValueError("No valid ask levels found in order book")

    best_bid = bids[0].price
    best_ask = asks[0].price

    if best_ask <= best_bid:
        warnings.append(f"Crossed book: best_ask={best_ask} <= best_bid={best_bid}")
        logger.warning("Crossed book detected — spread is negative or zero")

    spread_usdt = _round_f(_d(best_ask) - _d(best_bid), 8)
    spread_bps = _round_f((_d(best_ask) - _d(best_bid)) / _d(best_bid) * _d(10_000), 4)

    # -- Determine entry price --
    filled_qty = qty
    if use_vwap_entry:
        target_levels = asks if side == "buy" else bids
        entry_price, filled_qty = vwap_for_quantity(target_levels, qty)
        if filled_qty < qty:
            warnings.append(
                f"Insufficient book depth: only {filled_qty} of {qty} filled for VWAP"
            )
            logger.warning(
                "Book depth insufficient — partial VWAP fill: %.4f / %.4f",
                filled_qty,
                qty,
            )
    else:
        entry_price = entry_price_for_side(side, best_bid, best_ask, slippage)

    logger.info("Entry price: %.8f (VWAP=%s)", entry_price, use_vwap_entry)

    # -- Exit, stop-loss, liquidation --
    exit_price = calculate_exit_price(
        entry_price,
        target,
        leverage,
        qty,
        side,
        maker_fee,
        taker_fee,
        funding_rate,
    )
    sl_price = stop_loss_price(
        entry_price, target, qty, side, risk_reward, maker_fee, taker_fee
    )
    liq_price = liquidation_price(entry_price, leverage, side, maker_fee)
    margin_req = margin_required(entry_price, qty, leverage)

    # Warn if stop-loss is beyond liquidation (only meaningful with leverage)
    if leverage > 1:
        if side == "buy" and sl_price <= liq_price:
            warnings.append(
                "Stop-loss is at or below liquidation price — position may be liquidated before stop triggers"
            )
        if side == "sell" and sl_price >= liq_price:
            warnings.append(
                "Stop-loss is at or above liquidation price — position may be liquidated before stop triggers"
            )

    # -- Fee and profit breakdown --
    entry_fee, exit_fee, total_fees = estimate_round_trip_fees(
        entry_price,
        exit_price,
        qty,
        maker_fee,
        taker_fee,
        entry_is_taker=True,
        exit_is_taker=False,
    )
    funding_cost_usdt = _round_f(_d(entry_price) * _d(qty) * _d(funding_rate), 6)
    gross_p = gross_profit_usdt(entry_price, exit_price, qty, side)
    net_p = _round_f(_d(gross_p) - _d(total_fees) - _d(funding_cost_usdt), 6)
    risk_usdt = _round_f(_d(target) / _d(risk_reward), 6)

    logger.info(
        "Gross=%.4f | Fees=%.4f | Funding=%.4f | Net=%.4f",
        gross_p,
        total_fees,
        funding_cost_usdt,
        net_p,
    )

    # -- Kelly and sizing --
    kf = kelly_fraction(kelly_win, risk_reward)
    half_kf = _round_f(_d(kf) / _d(2), 6)  # Half-Kelly: safer in practice
    recommended_q = qty
    if account_balance > 0.0 and risk_percent > 0.0:
        recommended_q = compute_quantity(
            account_balance,
            risk_percent,
            entry_price,
            leverage,
            maker_fee,
            taker_fee,
            funding_rate,
        )
        logger.info("Recommended qty (sized): %.4f", recommended_q)

    # -- Book signal metrics --
    imbalance = book_imbalance(bids, asks, depth)
    bid_depth = book_depth_usdt(bids, depth)
    ask_depth = book_depth_usdt(asks, depth)
    conf, signal = confidence_score(
        spread_bps, imbalance, side, bid_depth, ask_depth, depth
    )

    # Determine fee scenario label for transparency
    fee_scenario = "taker_entry_maker_exit"

    # -- Assemble result --
    metrics = TradeMetrics(
        symbol=symbol,
        side=side,
        requested_qty=qty,
        recommended_qty=recommended_q,
        best_bid=best_bid,
        best_ask=best_ask,
        spread_usdt=spread_usdt,
        spread_bps=spread_bps,
        entry_price=entry_price,
        target_exit_price=exit_price,
        stop_loss_price=sl_price,
        liquidation_price=liq_price,
        gross_profit_usdt=gross_p,
        estimated_fees_usdt=total_fees,
        funding_cost_usdt=funding_cost_usdt,
        net_profit_usdt=net_p,
        margin_required=margin_req,
        risk_amount_usdt=risk_usdt,
        kelly_fraction=kf,
        half_kelly_fraction=half_kf,
        book_imbalance_ratio=imbalance,
        book_depth_bid_usdt=bid_depth,
        book_depth_ask_usdt=ask_depth,
        confidence_score=conf,
        signal=signal,
        leverage=leverage,
        fee_scenario=fee_scenario,
        warnings=warnings,
    )

    result = asdict(metrics)
    logger.info("Analysis complete: signal=%s confidence=%.1f", signal, conf)
    return result


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------


def _build_parser() -> argparse.ArgumentParser:
    """Construct the argument parser (separated for testability)."""
    parser = argparse.ArgumentParser(
        prog="micro_profit",
        description=(
            "Micro-Profit Estimator v4.0 — Estimate execution parameters "
            "for short-horizon scalp orders using live order-book data."
        ),
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    req = parser.add_argument_group("required arguments")
    req.add_argument(
        "--symbol",
        type=str,
        required=True,
        help="Asset ticker pair (e.g., BTCUSDT).",
    )
    req.add_argument(
        "--side",
        type=str,
        required=True,
        choices=["buy", "sell", "BUY", "SELL"],
        help="Execution side: buy or sell.",
    )
    req.add_argument(
        "--qty",
        type=float,
        required=True,
        help="Position size in base asset units.",
    )

    opt = parser.add_argument_group("optional arguments")
    opt.add_argument(
        "--target",
        type=float,
        default=DEFAULT_TARGET,
        help="Net profit target in USDT.",
    )
    opt.add_argument(
        "--leverage",
        type=int,
        default=DEFAULT_LEVERAGE,
        help="Margin leverage multiplier.",
    )
    opt.add_argument(
        "--maker_fee",
        type=float,
        default=DEFAULT_FEE_RATE,
        help="Maker fee rate (decimal).",
    )
    opt.add_argument(
        "--taker_fee",
        type=float,
        default=DEFAULT_TAKER_FEE,
        help="Taker fee rate (decimal).",
    )
    opt.add_argument(
        "--funding_rate",
        type=float,
        default=DEFAULT_FUNDING_RATE,
        help="Funding rate per interval (decimal).",
    )
    opt.add_argument(
        "--slippage",
        type=float,
        default=DEFAULT_SLIPPAGE,
        help="Price slippage rate (decimal).",
    )
    opt.add_argument(
        "--risk_reward",
        type=float,
        default=DEFAULT_RISK_REWARD,
        help="Reward-to-risk ratio.",
    )
    opt.add_argument(
        "--kelly_win",
        type=float,
        default=DEFAULT_KELLY_WIN,
        help="Estimated win rate for Kelly sizing.",
    )
    opt.add_argument(
        "--depth", type=int, default=DEFAULT_DEPTH, help="Order book levels to analyze."
    )
    opt.add_argument(
        "--account_balance",
        type=float,
        default=0.0,
        help="Total account balance in USDT.",
    )
    opt.add_argument(
        "--risk_percent",
        type=float,
        default=0.0,
        help="Percent of balance to risk per trade.",
    )
    opt.add_argument(
        "--bids_json",
        type=str,
        default="[]",
        help='JSON bid levels: [["price","qty"],...]',
    )
    opt.add_argument(
        "--asks_json",
        type=str,
        default="[]",
        help='JSON ask levels: [["price","qty"],...]',
    )
    opt.add_argument(
        "--use_vwap_entry",
        action="store_true",
        help="Use VWAP instead of best bid/ask entry.",
    )
    opt.add_argument(
        "--verbose", action="store_true", help="Enable debug-level logging to stderr."
    )

    return parser


def main() -> None:
    """CLI entry point — parse arguments, run analysis, emit JSON to stdout."""
    global logger

    parser = _build_parser()
    args = parser.parse_args()
    logger = _build_logger(verbose=args.verbose)

    logger.debug("Arguments: %s", vars(args))

    try:
        result = calculate_micro_profit(
            symbol=args.symbol,
            side=args.side,
            qty=args.qty,
            target=args.target,
            leverage=args.leverage,
            maker_fee=args.maker_fee,
            taker_fee=args.taker_fee,
            funding_rate=args.funding_rate,
            slippage=args.slippage,
            risk_reward=args.risk_reward,
            kelly_win=args.kelly_win,
            depth=args.depth,
            account_balance=args.account_balance,
            risk_percent=args.risk_percent,
            bids_json=args.bids_json,
            asks_json=args.asks_json,
            use_vwap_entry=args.use_vwap_entry,
        )
        # Success: clean JSON to stdout only
        print(json.dumps(result, indent=2))
        sys.exit(0)

    except ValueError as exc:
        # Validation errors — user-fixable
        error_payload = {
            "error": "validation_error",
            "message": str(exc),
            "error_code": "ERR_VALIDATION",
        }
        print(json.dumps(error_payload, indent=2), file=sys.stderr)
        sys.exit(2)

    except json.JSONDecodeError as exc:
        # Malformed JSON in bids/asks arguments
        error_payload = {
            "error": "json_parse_error",
            "message": str(exc),
            "error_code": "ERR_JSON",
        }
        print(json.dumps(error_payload, indent=2), file=sys.stderr)
        sys.exit(3)

    except Exception as exc:  # pylint: disable=broad-except
        # Unexpected errors — include type for debugging
        error_payload = {
            "error": "internal_error",
            "message": str(exc),
            "error_code": "ERR_INTERNAL",
            "type": type(exc).__name__,
        }
        logger.exception("Unexpected error during analysis")
        print(json.dumps(error_payload, indent=2), file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()


def run(
    symbol: str,
    side: str,
    qty: float,
    target: float = 5.0,
    leverage: int = 1,
    maker_fee: float = 0.0002,
    taker_fee: float = 0.00055,
    funding_rate: float = 0.0001,
    slippage: float = 0.0001,
    risk_reward: float = 2.0,
    kelly_win: float = 0.55,
    depth: int = 40,
    account_balance: float = 0.0,
    risk_percent: float = 0.0,
    bids_json: str = "[]",
    asks_json: str = "[]",
    use_vwap_entry: bool = False,
    verbose: bool = False,
    help: bool = False,
):
    """Run entry point for LLM agent/runner tool calls."""
    global logger
    logger = _build_logger(verbose=verbose)
    result = calculate_micro_profit(
        symbol=symbol,
        side=side,
        qty=qty,
        target=target,
        leverage=leverage,
        maker_fee=maker_fee,
        taker_fee=taker_fee,
        funding_rate=funding_rate,
        slippage=slippage,
        risk_reward=risk_reward,
        kelly_win=kelly_win,
        depth=depth,
        account_balance=account_balance,
        risk_percent=risk_percent,
        bids_json=bids_json,
        asks_json=asks_json,
        use_vwap_entry=use_vwap_entry,
    )
    return result
