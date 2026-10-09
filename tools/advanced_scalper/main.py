import json
import logging
import os
import threading
import time
from typing import Any, Dict, List

import websocket
from dotenv import load_dotenv

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")


def get_base_url(testnet: bool) -> str:
    return "https://api-testnet.bybit.com" if testnet else "https://api.bybit.com"


def get_ws_url(testnet: bool) -> str:
    return (
        "wss://stream-testnet.bybit.com/v5/public/linear"
        if testnet
        else "wss://stream.bybit.com/v5/public/linear"
    )


class ConfigManager:
    def __init__(self, config_path: str):
        with open(config_path) as f:
            self.config = json.load(f)

        load_dotenv()
        self.api_key = os.getenv("BYBIT_API_KEY")
        self.api_secret = os.getenv("BYBIT_API_SECRET")

        if not self.api_key or not self.api_secret:
            raise RuntimeError("API credentials missing in environment variables.")


class MarketDataStreamer:
    """Handles multi-pair websocket connections and maintains shared market state."""

    def __init__(self, ws_url: str, symbols: List[str]):
        self.ws_url = ws_url
        self.symbols = symbols
        self.state: Dict[str, Dict[str, Any]] = {
            sym: {
                "best_bid": 0.0,
                "best_ask": 0.0,
                "bid_vol": 0.0,
                "ask_vol": 0.0,
                "closes": [],
                "last_kline_start": 0,
            }
            for sym in symbols
        }
        self.ws_app = None
        self._thread = None
        self.is_connected = False

    def on_message(self, ws, message: str):
        try:
            msg = json.loads(message)
            topic = msg.get("topic", "")
            data = msg.get("data")
            if not data:
                return

            symbol = None
            if isinstance(data, dict) and "s" in data:
                symbol = data["s"]
            elif isinstance(data, list) and len(data) > 0 and "s" in data[0]:
                symbol = data[0]["s"]

            if not symbol or symbol not in self.state:
                return

            sym_state = self.state[symbol]

            if topic.startswith("orderbook"):
                d = data if isinstance(data, dict) else {}
                if d.get("b") and len(d["b"]) > 0:
                    sym_state["best_bid"] = float(d["b"][0][0])
                    sym_state["bid_vol"] = float(d["b"][0][1])
                if d.get("a") and len(d["a"]) > 0:
                    sym_state["best_ask"] = float(d["a"][0][0])
                    sym_state["ask_vol"] = float(d["a"][0][1])

            elif topic.startswith("kline"):
                rows = data if isinstance(data, list) else [data]
                for row in rows:
                    c = float(row.get("close", row[4] if isinstance(row, list) else 0))
                    start_time = row.get(
                        "start", row[0] if isinstance(row, list) else 0
                    )

                    if sym_state["last_kline_start"] == start_time:
                        if sym_state["closes"]:
                            sym_state["closes"][-1] = c
                    else:
                        sym_state["closes"].append(c)
                        sym_state["last_kline_start"] = start_time
                        if len(sym_state["closes"]) > 4:
                            sym_state["closes"].pop(0)

        except Exception as exc:
            logging.error(f"WS Parse Error: {exc}")

    def on_open(self, ws):
        logging.info("Multi-Pair WS Connected")
        self.is_connected = True
        args = []
        for sym in self.symbols:
            args.extend([f"orderbook.50.{sym}", f"kline.1.{sym}"])

        ws.send(json.dumps({"op": "subscribe", "args": args}))

    def on_close(self, ws, close_status_code, close_msg):
        logging.warning("WS Closed. Reconnecting...")
        self.is_connected = False
        time.sleep(2)
        self.start()

    def start(self):
        self.ws_app = websocket.WebSocketApp(
            self.ws_url,
            on_open=self.on_open,
            on_message=self.on_message,
            on_close=self.on_close,
        )
        self._thread = threading.Thread(
            target=self.ws_app.run_forever,
            kwargs={"ping_interval": 20, "ping_timeout": 10},
            daemon=True,
        )
        self._thread.start()


class BotCore:
    def __init__(self, config_path: str):
        self.cfg = ConfigManager(config_path)
        self.symbols = list(self.cfg.config["symbols"].keys())
        self.ws_url = get_ws_url(self.cfg.config.get("testnet", False))
        self.streamer = MarketDataStreamer(self.ws_url, self.symbols)

    def run(self):
        from execution import ExecutionEngine
        from strategy import SignalEngine

        signal_engine = SignalEngine(self.cfg.config)
        execution_engine = ExecutionEngine(
            self.cfg.api_key, self.cfg.api_secret, self.cfg.config.get("testnet", False)
        )

        self.streamer.start()
        while not self.streamer.is_connected:
            time.sleep(0.1)

        logging.info(
            f"Started monitoring {len(self.symbols)} symbols: {', '.join(self.symbols)}"
        )
        try:
            while True:
                for symbol in self.symbols:
                    state = self.streamer.state[symbol]
                    if state["best_bid"] > 0 and len(state["closes"]) > 0:
                        timestamp = int(time.time() * 1000)
                        sig = signal_engine.evaluate(symbol, state, timestamp)
                        if sig:
                            execution_engine.execute_order(sig)

                execution_engine.manage_trailing_stops(self.streamer.state)
                time.sleep(1.0)
        except KeyboardInterrupt:
            logging.info("Shutting down bot.")


if __name__ == "__main__":
    bot = BotCore("config.json")
    bot.run()
