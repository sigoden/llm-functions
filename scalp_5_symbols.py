import json
import subprocess

symbols = ["BTCUSDT", "ETHUSDT", "ADAUSDT", "DOGEUSDT", "TRXUSDT"]
# Using a fixed target profit of $0.05
target_pnl = 0.05

for sym in symbols:
    print(f"--- Scalping {sym} ---")
    # Fetch ticker
    res = subprocess.check_output(
        [
            "python3",
            "/data/data/com.termux/files/home/.config/aichat/llm-functions/tools/bybit-terminal.py",
            "--action",
            "get_ticker",
            "--symbol",
            sym,
            "--json",
        ]
    )
    ticker = json.loads(res)["result"]["list"][0]
    price = float(ticker["lastPrice"])
    # Notional min $5.1
    qty = 5.1 / price

    # 1. Place Market Buy
    order = subprocess.check_output(
        [
            "python3",
            "/data/data/com.termux/files/home/.config/aichat/llm-functions/tools/bybit-terminal.py",
            "--action",
            "place_order",
            "--symbol",
            sym,
            "--side",
            "Buy",
            "--qty",
            str(qty),
            "--order_type",
            "Market",
            "--category",
            "linear",
            "--json",
        ]
    )
    print(f"Entry: {order.decode().strip()}")

    # 2. Place Limit Sell (TP)
    # TP Price: entry + 0.05 / qty
    tp_price = price + (target_pnl / qty)
    tp_order = subprocess.check_output(
        [
            "python3",
            "/data/data/com.termux/files/home/.config/aichat/llm-functions/tools/bybit-terminal.py",
            "--action",
            "place_order",
            "--symbol",
            sym,
            "--side",
            "Sell",
            "--qty",
            str(qty),
            "--order_type",
            "Limit",
            "--price",
            str(tp_price),
            "--category",
            "linear",
            "--reduce_only",
            "True",
            "--json",
        ]
    )
    print(f"Exit: {tp_order.decode().strip()}")
