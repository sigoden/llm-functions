import os

tools_dir = "/data/data/com.termux/files/home/.config/aichat/llm-functions/tools/"
# List of tools I have officially refactored
target_tools = [
    "bybit_get_balance.py",
    "bybit_get_ticker.py",
    "bybit_get_positions.py",
    "bybit_get_open_orders.py",
    "bybit_place_order.py",
    "bybit_get_orderbook.py",
    "bybit_get_indicators.py",
    "bybit_cancel_order.py",
]

for filename in target_tools:
    path = os.path.join(tools_dir, filename)
    with open(path) as f:
        lines = f.readlines()

    # Remove existing # @ lines to start fresh
    new_lines = [l for l in lines if not l.strip().startswith("# @")]

    # Prepend valid header for the current tool
    # I will dynamically generate a basic header for the script to handle.
    # ...
