import hashlib
import hmac
import logging
import time
from typing import Any, Dict, Optional

import requests
from main import get_base_url


class ExecutionEngine:
    def __init__(self, api_key: str, api_secret: str, testnet: bool = False):
        self.api_key = api_key
        self.api_secret = api_secret
        self.base_url = get_base_url(testnet)
        self.session = requests.Session()
        self.active_positions: Dict[str, Dict[str, Any]] = {}

    def _generate_signature(self, timestamp: int, payload: str) -> str:
        param_str = str(timestamp) + self.api_key + "5000" + payload
        return hmac.new(
            self.api_secret.encode("utf-8"), param_str.encode("utf-8"), hashlib.sha256
        ).hexdigest()

    def _send_signed_post(
        self, endpoint: str, payload_dict: Dict[str, Any]
    ) -> Dict[str, Any]:
        payload_str = "" if not payload_dict else __import__("json").dumps(payload_dict)
        timestamp = int(time.time() * 1000)
        signature = self._generate_signature(timestamp, payload_str)
        headers = {
            "X-BAPI-API-KEY": self.api_key,
            "X-BAPI-SIGN": signature,
            "X-BAPI-SIGN-TYPE": "2",
            "X-BAPI-TIMESTAMP": str(timestamp),
            "X-BAPI-RECV-WINDOW": "5000",
            "Content-Type": "application/json",
        }
        res = self.session.post(
            self.base_url + endpoint, headers=headers, data=payload_str
        )
        res.raise_for_status()
        return res.json()

    def execute_order(self, signal: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        symbol = signal["symbol"]
        side = signal["side"]
        qty = signal["qty"]
        price = signal["entry_price"]

        logging.info(f"Executing {side} {qty} {symbol} @ {price}")

        payload = {
            "category": "linear",
            "symbol": symbol,
            "side": side,
            "orderType": "Limit",
            "qty": str(qty),
            "price": str(price),
            "timeInForce": "PostOnly",
            "isLeverage": 1,
        }

        try:
            res = self._send_signed_post("/v5/order/create", payload)
            if res.get("retCode") == 0:
                logging.info(f"Order Success: {res.get('result', {}).get('orderId')}")
                self.active_positions[symbol] = {
                    "entry_price": price,
                    "side": side,
                    "qty": qty,
                    "max_profit_price": price,
                }
                return res
            else:
                logging.warning(f"Order Rejected: {res}")
        except Exception as e:
            logging.error(f"Execution Error: {e}")

        return None

    def manage_trailing_stops(self, market_state: Dict[str, Dict[str, Any]]):
        """Phase 3: Dynamic lock-in logic."""
        for symbol, pos in list(self.active_positions.items()):
            current_price = (
                market_state[symbol]["best_bid"]
                if pos["side"] == "Buy"
                else market_state[symbol]["best_ask"]
            )

            # Simple simulation of trailing stop tracking
            if pos["side"] == "Buy":
                pos["max_profit_price"] = max(pos["max_profit_price"], current_price)
            elif (
                current_price < pos["max_profit_price"] or pos["max_profit_price"] == 0
            ):
                pos["max_profit_price"] = current_price

            # API calls for modifying trailing stops would go here
            pass
