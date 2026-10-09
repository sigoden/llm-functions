I'll create a standalone file with just the breakeven calculation functions.<tool_call>write_to_file<arg_key>repo_name</arg_key><arg_value>Mentallyspammed1/llm-functions</arg_value><arg_key>file_path</arg_key><arg_value>tools/bybit_breakeven.py</arg_value><arg_key>content</arg_key><arg_value>#!/usr/bin/env python3
"""
BYBIT BREAKEVEN CALCULATOR - Standalone Fee-Aware Breakeven Tool v1.0

Calculates breakeven prices accounting for trading fees and minimum net profit targets.
Ensures net profit of 0.02 USDT after all fees are paid.

Usage:
    python bybit_breakeven.py --entry-price 50000 --position-size 100
    python bybit_breakeven.py --entry-price 50000 --position-size 100 --leverage 10
"""
from typing import Dict
import argparse
import json


def calculate_breakeven_with_fees(
    entry_price: float,
    position_size_usdt: float,
    maker_fee_rate: float = 0.0006,  # 0.06% for VIP 0-1
    taker_fee_rate: float = 0.001,   # 0.1% for VIP 0-1
    min_net_profit_usdt: float = 0.02,
    is_maker: bool = True,
    leverage: int = 1,
) -> Dict[str, float]:
    """
    Calculate breakeven price accounting for fees and minimum net profit.
    
    For a LONG position:
    - Entry fee = position_size_usdt * maker_fee_rate
    - Exit fee = position_size_usdt * taker_fee_rate
    - Total fees = entry_fee + exit_fee
    - Required gross profit = total_fees + min_net_profit_usdt
    - Breakeven price = entry_price + (required_gross_profit / position_size)
    
    For a SHORT position:
    - Entry fee = position_size_usdt * maker_fee_rate
    - Exit fee = position_size_usdt * taker_fee_rate
    - Total fees = entry_fee + exit_fee
    - Required gross profit = total_fees + min_net_profit_usdt
    - Breakeven price = entry_price - (required_gross_profit / position_size)
    
    Args:
        entry_price: Entry price of the position
        position_size_usdt: Position size in USDT
        maker_fee_rate: Maker fee rate (default 0.0006 for VIP 0-1)
        taker_fee_rate: Taker fee rate (default 0.001 for VIP 0-1)
        min_net_profit_usdt: Minimum net profit in USDT (default 0.02)
        is_maker: Whether entry was a maker order (default True)
        leverage: Leverage multiplier (default 1)
    
    Returns:
        Dictionary with breakeven prices for long and short positions
    """
    if position_size_usdt <= 0 or entry_price <= 0:
        raise ValueError("Position size and entry price must be positive")
    
    # Calculate position size in base asset
    position_size_base = position_size_usdt / entry_price
    
    # Calculate fees
    entry_fee_rate = maker_fee_rate if is_maker else taker_fee_rate
    entry_fee = position_size_usdt * entry_fee_rate
    exit_fee = position_size_usdt * taker_fee_rate
    total_fees = entry_fee + exit_fee
    
    # Required gross profit to achieve minimum net profit
    required_gross_profit = total_fees + min_net_profit_usdt
    
    # Calculate breakeven prices
    # For LONG: need price to rise enough to cover fees + min profit
    long_breakeven_price = entry_price + (required_gross_profit / position_size_base)
    
    # For SHORT: need price to fall enough to cover fees + min profit
    short_breakeven_price = entry_price - (required_gross_profit / position_size_base)
    
    # Calculate percentage moves needed
    long_move_pct = ((long_breakeven_price - entry_price) / entry_price) * 100
    short_move_pct = ((entry_price - short_breakeven_price) / entry_price) * 100
    
    # Calculate breakeven with leverage (margin-based)
    # With leverage, the required price move is divided by leverage
    long_breakeven_price_leveraged = entry_price + (required_gross_profit / (position_size_base * leverage))
    short_breakeven_price_leveraged = entry_price - (required_gross_profit / (position_size_base * leverage))
    
    long_move_pct_leveraged = ((long_breakeven_price_leveraged - entry_price) / entry_price) * 100
    short_move_pct_leveraged = ((entry_price - short_breakeven_price_leveraged) / entry_price) * 100
    
    return {
        "entry_price": round(entry_price, 6),
        "position_size_usdt": round(position_size_usdt, 2),
        "position_size_base": round(position_size_base, 6),
        "entry_fee": round(entry_fee, 4),
        "exit_fee": round(exit_fee, 4),
        "total_fees": round(total_fees, 4),
        "min_net_profit_usdt": round(min_net_profit_usdt, 4),
        "required_gross_profit": round(required_gross_profit, 4),
        "long_breakeven_price": round(long_breakeven_price, 6),
        "long_move_pct": round(long_move_pct, 4),
        "short_breakeven_price": round(short_breakeven_price, 6),
        "short_move_pct": round(short_move_pct, 4),
        "leverage": leverage,
        "long_breakeven_price_leveraged": round(long_breakeven_price_leveraged, 6),
        "long_move_pct_leveraged": round(long_move_pct_leveraged, 4),
        "short_breakeven_price_leveraged": round(short_breakeven_price_leveraged, 6),
        "short_move_pct_leveraged": round(short_move_pct_leveraged, 4),
    }


def calculate_profit_target_with_fees(
    entry_price: float,
    position_size_usdt: float,
    target_profit_usdt: float,
    maker_fee_rate: float = 0.0006,
    taker_fee_rate: float = 0.001,
    is_maker: bool = True,
    leverage: int = 1,
) -> Dict[str, float]:
    """
    Calculate target price to achieve specific profit after fees.
    
    Args:
        entry_price: Entry price of the position
        position_size_usdt: Position size in USDT
        target_profit_usdt: Target net profit in USDT
        maker_fee_rate: Maker fee rate (default 0.0006)
        taker_fee_rate: Taker fee rate (default 0.001)
        is_maker: Whether entry was a maker order (default True)
        leverage: Leverage multiplier (default 1)
    
    Returns:
        Dictionary with target prices for long and short positions
    """
    if position_size_usdt <= 0 or entry_price <= 0:
        raise ValueError("Position size and entry price must be positive")
    
    position_size_base = position_size_usdt / entry_price
    
    entry_fee_rate = maker_fee_rate if is_maker else taker_fee_rate
    entry_fee = position_size_usdt * entry_fee_rate
    exit_fee = position_size_usdt * taker_fee_rate
    total_fees = entry_fee + exit_fee
    
    required_gross_profit = total_fees + target_profit_usdt
    
    long_target_price = entry_price + (required_gross_profit / position_size_base)
    short_target_price = entry_price - (required_gross_profit / position_size_base)
    
    long_move_pct = ((long_target_price - entry_price) / entry_price) * 100
    short_move_pct = ((entry_price - short_target_price) / entry_price) * 100
    
    long_target_price_leveraged = entry_price + (required_gross_profit / (position_size_base * leverage))
    short_target_price_leveraged = entry_price - (required_gross_profit / (position_size_base * leverage))
    
    long_move_pct_leveraged = ((long_target_price_leveraged - entry_price) / entry_price) * 100
    short_move_pct_leveraged = ((entry_price - short_target_price_leveraged) / entry_price) * 100
    
    return {
        "entry_price": round(entry_price, 6),
        "position_size_usdt": round(position_size_usdt, 2),
        "target_profit_usdt": round(target_profit_usdt, 2),
        "entry_fee": round(entry_fee, 4),
        "exit_fee": round(exit_fee, 4),
        "total_fees": round(total_fees, 4),
        "required_gross_profit": round(required_gross_profit, 4),
        "long_target_price": round(long_target_price, 6),
        "long_move_pct": round(long_move_pct, 4),
        "short_target_price": round(short_target_price, 6),
        "short_move_pct": round(short_move_pct, 4),
        "leverage": leverage,
        "long_target_price_leveraged": round(long_target_price_leveraged, 6),
        "long_move_pct_leveraged": round(long_move_pct_leveraged, 4),
        "short_target_price_leveraged": round(short_target_price_leveraged, 6),
        "short_move_pct_leveraged": round(short_move_pct_leveraged, 4),
    }


def main():
    parser = argparse.ArgumentParser(description="Bybit Breakeven Calculator with Fee Accounting")
    parser.add_argument("--entry-price", type=float, required=True, help="Entry price")
    parser.add_argument("--position-size", type=float, required=True, help="Position size in USDT")
    parser.add_argument("--maker-fee", type=float, default=0.0006, help="Maker fee rate (default 0.0006)")
    parser.add_argument("--taker-fee", type=float, default=0.001, help="Taker fee rate (default 0.001)")
    parser.add_argument("--min-profit", type=float, default=0.02, help="Minimum net profit in USDT (default 0.02)")
    parser.add_argument("--leverage", type=int, default=1, help="Leverage multiplier (default 1)")
    parser.add_argument("--is-maker", action="store_true", default=True, help="Entry was a maker order (default True)")
    parser.add_argument("--target-profit", type=float, help="Calculate target price for specific profit (overrides min-profit)")
    parser.add_argument("--output", type=str, choices=["json", "pretty"], default="pretty", help="Output format")

    args = parser.parse_args()

    if args.target_profit:
        result = calculate_profit_target_with_fees(
            entry_price=args.entry_price,
            position_size_usdt=args.position_size,
            target_profit_usdt=args.target_profit,
            maker_fee_rate=args.maker_fee,
            taker_fee_rate=args.taker_fee,
            is_maker=args.is_maker,
            leverage=args.leverage,
        )
    else:
        result = calculate_breakeven_with_fees(
            entry_price=args.entry_price,
            position_size_usdt=args.position_size,
            maker_fee_rate=args.maker_fee,
            taker_fee_rate=args.taker_fee,
            min_net_profit_usdt=args.min_profit,
            is_maker=args.is_maker,
            leverage=args.leverage,
        )

    if args.output == "json":
        print(json.dumps(result, indent=2))
    else:
        print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
</arg_value></tool_call>
