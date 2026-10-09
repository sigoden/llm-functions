#!/usr/bin/env python3
import argparse

from bybit_realm import run


def main():
    parser = argparse.ArgumentParser(description="Bybit Realm CLI")
    subparsers = parser.add_subparsers(dest="action", help="Action to perform")

    # get_balance
    bal = subparsers.add_parser("get_balance")
    bal.add_argument("--account-type", default="UNIFIED")

    # get_positions
    pos = subparsers.add_parser("get_positions")
    pos.add_argument("--symbol")
    pos.add_argument("--category", default="linear")

    # place_order
    order = subparsers.add_parser("place_order")
    order.add_argument("--symbol", required=True)
    order.add_argument("--side", required=True)
    order.add_argument("--qty", type=float, required=True)
    order.add_argument("--order-type", required=True)
    order.add_argument("--price", type=float)

    args = parser.parse_args()

    if args.action == "get_balance":
        print(run(action="get_wallet_balance", account_type=args.account_type))
    elif args.action == "get_positions":
        print(run(action="get_positions", symbol=args.symbol, category=args.category))
    elif args.action == "place_order":
        print(
            run(
                action="place_order",
                symbol=args.symbol,
                side=args.side,
                qty=args.qty,
                order_type=args.order_type,
                price=args.price,
            )
        )
    else:
        parser.print_help()


if __name__ == "__main__":
    main()
