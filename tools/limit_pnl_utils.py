def calculate_limit_pnl(
    entry_price: float,
    limit_price: float,
    side: str,  # "Buy" or "Sell"
    qty: float,
    fee_rate: float = 0.001,  # 0.1% by default (adjust for Maker/Taker)
    position_size_quote: bool = False,  # True if qty is in quote currency
    use_maker_fee: bool = True,  # Limit orders typically get Maker fees
) -> dict:
    """
    Calculate Profit/Loss for a limit order.

    Args:
        entry_price: Price at which position was entered
        limit_price: Price at which limit order is placed
        side: "Buy" or "Sell" - direction of the limit order
        qty: Quantity (in base currency by default)
        fee_rate: Exchange fee rate (0.001 = 0.1%)
        position_size_quote: If True, qty is in quote currency (e.g., USDT)
        use_maker_fee: If True, apply Maker fee rate (usually lower)

    Returns:
        Dictionary with detailed PnL breakdown
    """
    # Adjust fee rate for Maker orders (usually 0.05-0.08% vs 0.1% Taker)
    actual_fee_rate = fee_rate * 0.5 if use_maker_fee else fee_rate

    # Calculate notional values
    if position_size_quote:
        entry_notional = qty
        limit_notional = qty
        base_qty = qty / entry_price
    else:
        entry_notional = qty * entry_price
        limit_notional = qty * limit_price
        base_qty = qty

    # Calculate raw PnL based on direction
    if side.lower() == "buy":
        # We're buying, so we want entry_price < limit_price to profit
        # But this is a buy limit order, so we are placing an order to buy at a specific price.
        # This function seems to calculate PnL of a position vs a limit order target.
        if side.lower() == "sell":
            raw_pnl = (limit_price - entry_price) * base_qty
        else:  # "buy"
            raw_pnl = (entry_price - limit_price) * base_qty
    elif side.lower() == "sell":
        if side.lower() == "sell":
            raw_pnl = (limit_price - entry_price) * base_qty
        else:
            raw_pnl = (entry_price - limit_price) * base_qty
    else:
        return {"status": "error", "msg": f"Invalid side: {side}. Use 'Buy' or 'Sell'."}

    # Calculate fees (both entry and exit would incur fees)
    # For a complete round-trip:
    entry_fee = entry_notional * actual_fee_rate
    exit_fee = limit_notional * actual_fee_rate
    total_fees = entry_fee + exit_fee

    # Net PnL
    net_pnl = raw_pnl - total_fees

    # Percentage return
    investment = entry_notional
    pct_return = (net_pnl / investment) * 100 if investment != 0 else 0

    # Volume weighted average price (VWAP) for partial fills consideration
    # This is a simplified version assuming full fill

    return {
        "status": "success",
        "entry_price": round(entry_price, 8),
        "limit_price": round(limit_price, 8),
        "side": side,
        "qty": base_qty,
        "entry_notional": round(entry_notional, 4),
        "limit_notional": round(limit_notional, 4),
        "raw_pnl": round(raw_pnl, 4),
        "entry_fee": round(entry_fee, 4),
        "exit_fee": round(exit_fee, 4),
        "total_fees": round(total_fees, 4),
        "net_pnl": round(net_pnl, 4),
        "pct_return": round(pct_return, 2),
        "fee_rate_used": actual_fee_rate,
        "is_maker": use_maker_fee,
        "breakeven_price": round(
            (entry_notional + total_fees) / base_qty if base_qty != 0 else 0, 8
        ),
    }


def calculate_limit_micro_profit(
    entry_price: float,
    limit_price: float,
    side: str,
    qty: float,
    fee_rate: float = 0.001,
) -> dict:
    """
    Simplified micro-profit calculation for small trades.
    """
    if side.lower() == "buy":
        raw_pnl = (limit_price - entry_price) * qty
    else:  # "sell"
        raw_pnl = (entry_price - limit_price) * qty

    # Calculate fee on the limit order execution
    fee = abs(limit_price * qty) * fee_rate
    net_pnl = raw_pnl - fee

    # Percentage return
    investment = entry_price * qty
    pct_return = (net_pnl / investment) * 100 if investment != 0 else 0

    return {
        "status": "success",
        "net_pnl": round(net_pnl, 4),
        "fee_applied": round(fee, 4),
        "pct_return": round(pct_return, 2),
        "raw_pnl": round(raw_pnl, 4),
    }


def calculate_depth_weighted_pnl(
    symbol: str,
    entry_price: float,
    limit_price: float,
    side: str,
    qty: float,
    fee_rate: float = 0.001,
    use_maker: bool = True,
) -> dict:
    """
    Calculate PnL considering order book depth for weighted average fill price.

    This accounts for the fact that large limit orders may be filled at
    multiple price levels from the order book.
    """
    from bybit_realm import get_realm

    try:
        bot = get_realm()
        ob = bot.get_orderbook(symbol=symbol).get("result", {})

        if not ob:
            return {"status": "error", "msg": "Could not fetch orderbook"}

        bids = [[float(p), float(q)] for p, q in ob.get("b", [])]
        asks = [[float(p), float(q)] for p, q in ob.get("a", [])]

        # Choose appropriate side based on limit order direction
        if side.lower() == "buy":
            # We're buying, so we'll be matching against ask prices
            price_levels = asks
            threshold_func = lambda p: p <= limit_price
        else:
            # We're selling, so we'll be matching against bid prices
            price_levels = bids
            threshold_func = lambda p: p >= limit_price

        # Calculate weighted average fill price
        total_filled = 0.0
        weighted_sum = 0.0

        for p, vol in price_levels:
            if threshold_func(p):
                remaining = qty - total_filled
                fill_qty = min(vol, remaining)
                weighted_sum += p * fill_qty
                total_filled += fill_qty

                if total_filled >= qty:
                    break

        if total_filled < qty:
            return {
                "status": "error",
                "msg": f"Insufficient liquidity to fill order. Only {total_filled:.4f} of {qty} qty available.",
            }

        avg_fill_price = weighted_sum / total_filled

        # Now calculate PnL using this weighted fill price
        actual_fee_rate = fee_rate * 0.5 if use_maker else fee_rate

        # Raw PnL
        if side.lower() == "sell":
            raw_pnl = (avg_fill_price - entry_price) * total_filled
        else:  # "buy"
            raw_pnl = (entry_price - avg_fill_price) * total_filled

        entry_notional = entry_price * total_filled
        exit_notional = avg_fill_price * total_filled
        entry_fee = entry_notional * actual_fee_rate
        exit_fee = exit_notional * actual_fee_rate
        total_fees = entry_fee + exit_fee
        net_pnl = raw_pnl - total_fees

        return {
            "status": "success",
            "entry_price": entry_price,
            "limit_price_requested": limit_price,
            "avg_fill_price": round(avg_fill_price, 8),
            "qty_filled": total_filled,
            "raw_pnl": round(raw_pnl, 4),
            "entry_fee": round(entry_fee, 4),
            "exit_fee": round(exit_fee, 4),
            "total_fees": round(total_fees, 4),
            "net_pnl": round(net_pnl, 4),
            "pct_return": round((net_pnl / entry_notional) * 100, 2)
            if entry_notional != 0
            else 0,
        }

    except Exception as e:
        return {
            "status": "error",
            "msg": f"Error calculating depth-weighted PnL: {e!s}",
        }
