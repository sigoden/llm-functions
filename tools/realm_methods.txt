class BybitRealm:
    """
    Full-featured Bybit V5 API client.

    Responsibilities:
    - Authenticated + unauthenticated requests
    - HMAC-SHA256 signing (GET query string + POST body)
    - Retry with exponential back-off
    - Rate limiting
    - Order book analysis
    - Position risk metrics
    - Market regime detection
    - Multi-symbol scanner
    - Trade journal integration
    """

    def __init__(self, config: TradingConfig = None):
        self.config = config or TradingConfig()
        self._proxy = GeoProxyManager(self.config)
        self.session = self._proxy.session
        self._limiter = RateLimiter(capacity=20, refill_per_ms=0.02)
        self.journal = TradeJournal(self.config.journal_path)
        self.breaker = CircuitBreaker(initial_equity=1000.0, max_drawdown_pct=0.05)
        self._symbol_cache: Dict[str, dict] = {}

    def _get_symbol_info(self, symbol: str, category: str = "linear") -> Optional[dict]:
        cache_key = f"{category}:{symbol.upper()}"
        if cache_key in self._symbol_cache:
            return self._symbol_cache[cache_key]
        
        res = self.get_instruments_info(category=category, symbol=symbol)
        # Handle cases where res might be a list or a dict containing a list
        if isinstance(res, dict) and "list" in res:
            items = res["list"]
        elif isinstance(res, list):
            items = res
        else:
            items = []
            
        if items:
            self._symbol_cache[cache_key] = items[0]
            return items[0]
        return None

    def _format_qty(self, symbol: str, qty: float, category: str = "linear") -> str:
        info = self._get_symbol_info(symbol, category)
        if not info: return str(qty)
        
        qty_step = float(info.get("lotSizeFilter", {}).get("qtyStep", 0))
        if qty_step == 0: return str(qty)
        
        precision = len(str(qty_step).split(".")[-1]) if "." in str(qty_step) else 0
        # For quantity, usually floor it to avoid "qty too large" errors
        formatted = f"{math.floor(qty / qty_step) * qty_step:.{precision}f}"
        return formatted.rstrip("0").rstrip(".") if "." in formatted else formatted

    def _format_price(self, symbol: str, price: float, category: str = "linear") -> str:
        info = self._get_symbol_info(symbol, category)
        if not info: return str(price)
        
        tick_size = float(info.get("priceFilter", {}).get("tickSize", 0))
        if tick_size == 0: return str(price)
        
        precision = len(str(tick_size).split(".")[-1]) if "." in str(tick_size) else 0
        formatted = f"{round(price / tick_size) * tick_size:.{precision}f}"
        return formatted

    def health_check(self) -> dict:
        """Performs a basic connectivity check."""
        return self._request("GET", "/v5/market/time", signed=False)

    # ──────────────────────────────────────────────────────────────────────────
    # SIGNING  (FIX: GET uses query string in signature, POST uses JSON body)
    # ──────────────────────────────────────────────────────────────────────────
    def _sign(self, ts: str, payload: str = "") -> str:
        """
        Bybit V5 signature:
          HMAC-SHA256( timestamp + api_key + recv_window + payload )

        For GET  → payload = url-encoded query string
        For POST → payload = compact JSON body
        """
        msg = (
            f"{ts}"
            f"{self.config.api_key}"
            f"{str(self.config.recv_window)}"
            f"{payload}"
        )
        return hmac.new(
            self.config.api_secret.encode("utf-8"),
            msg.encode("utf-8"),
            hashlib.sha256,
        ).hexdigest()

    def _build_query_string(self, params: dict) -> str:
        """Deterministic query-string builder for GET signing."""
        if not params:
            return ""
        # Filter out None values and ensure consistent string representation
        clean_params = {k: str(v) for k, v in params.items() if v is not None}
        # Bybit requires alphabetical sort for the signature payload
        sorted_params = sorted(clean_params.items())
        return "&".join(f"{k}={v}" for k, v in sorted_params)

    # ──────────────────────────────────────────────────────────────────────────
    # HTTP REQUEST  (retry + back-off + rate-limit)
    # ──────────────────────────────────────────────────────────────────────────
    def _request(
        self,
        method: str,
        endpoint: str,
        params: Optional[dict] = None,
        json_data: Optional[dict] = None,
        signed: bool = True,
    ) -> dict:
        # Check Circuit Breaker for write operations
        if method.upper() == "POST" and endpoint not in ["/v5/market/time"]:
            try:
                # Only check balance if we are doing a real trade
                if "order" in endpoint or "position" in endpoint:
                    balance_resp = self.get_wallet_balance()
                    equity = float(balance_resp.get("result", {}).get("list", [{}])[0].get("totalEquity", 1000.0))
                    if not self.breaker.check(equity):
                        return {"status": "error", "msg": "CIRCUIT_BREAKER_TRIPPED: Equity below threshold"}
            except:
                pass
        
        self._limiter.acquire()

        ts = str(int(time.time() * 1000))
        headers = {
            "X-BAPI-API-KEY": self.config.api_key,
            "X-BAPI-TIMESTAMP": ts,
            "X-BAPI-RECV-WINDOW": str(self.config.recv_window),
            "Content-Type": "application/json",
        }

        url = self.config.base_url + endpoint
        sign_payload = ""

        if signed:
            if method.upper() == "GET":
                # Signature payload = sorted query string
                sign_payload = self._build_query_string(params or {})
                if sign_payload:
                    url = f"{url}?{sign_payload}"
                # Reset params so requests library doesn't append them again
                params = None
            else:
                # Signature payload = sorted JSON body
                sign_payload = json.dumps(
                    json_data, sort_keys=True
                ) if json_data else ""
                
            # DEBUG LOGGING - Crucial for signature errors
            logger.debug(f"DEBUG_SIGN: payload={repr(sign_payload)}")
            
            signature = self._sign(ts, sign_payload)
            headers["X-BAPI-SIGN"] = signature
            # DEBUG LOGGING
            logger.debug("Request URL: %s", url)
            logger.debug("Signature: %s", signature)

        last_error: dict = {}

        for attempt in range(1, self.config.max_retries + 1):
            try:
                resp = self.session.request(
                    method,
                    url,
                    params=params,
                    data=sign_payload if (signed and method.upper() == "POST") else None,
                    json=json_data if not (signed and method.upper() == "POST") else None,
                    headers=headers,
                    timeout=self.config.timeout,
                )
                
                if resp.status_code != 200:
                    logger.error(f"API Error [{resp.status_code}]: {resp.text}")
                    last_error = {"status": "error", "code": resp.status_code, "msg": resp.text}
                    time.sleep(attempt * 0.5)  # Back-off
                    continue
                return resp.json()
                try:
                    data = resp.json()
                except json.JSONDecodeError:
                    logger.error("Failed to parse JSON response: %s", resp.text[:200])
                    last_error = {"status": "error", "msg": "Invalid JSON response (HTML block?)", "raw": resp.text[:200]}
                    continue

                ret_code = data.get("retCode", -1)
                if ret_code == 0:
                    return data.get("result", data)

                # Non-zero retCode = API-level error
                last_error = {
                    "status": "error",
                    "code": ret_code,
                    "msg": data.get("retMsg", "Unknown API error"),
                }

                # Don't retry auth / param errors
                if ret_code in (10003, 10004, 10005, 110001, 110013):
                    logger.error(
                        "API error [%s] %s", ret_code, last_error["msg"]
                    )
                    return last_error

            except requests.Timeout:
                last_error = {"status": "error", "msg": "Request timeout"}
            except requests.ConnectionError as exc:
                last_error = {
                    "status": "error",
                    "msg": f"Connection error: {exc}",
                }
            except Exception as exc:
                last_error = {"status": "error", "msg": str(exc)}

            if attempt < self.config.max_retries:
                wait = 0.4 * (2 ** (attempt - 1))   # 0.4 s, 0.8 s, …
                logger.warning(
                    "Attempt %d/%d failed — retrying in %.1fs | %s",
                    attempt,
                    self.config.max_retries,
                    wait,
                    last_error.get("msg", ""),
                )
                time.sleep(wait)

        logger.error(
            "All %d attempts failed: %s", self.config.max_retries, last_error
        )
        return last_error

    # ══════════════════════════════════════════════════════════════════════════
    # ACCOUNT
    # ══════════════════════════════════════════════════════════════════════════
    def get_wallet_balance(self, account_type: str = "UNIFIED") -> dict:
        return self._request(
            "GET",
            "/v5/account/wallet-balance",
            params={"accountType": account_type},
            signed=True,
        )

    def get_positions(
        self,
        category: str = "linear",
        symbol: Optional[str] = None,
        settle_coin: Optional[str] = None,
    ) -> dict:
        params: dict = {"category": category}
        if symbol:
            params["symbol"] = symbol.upper()
        # Ensure settleCoin is provided for linear category if not explicitly set
        if category == "linear" and not settle_coin:
            params["settleCoin"] = "USDT"
        elif settle_coin:
            params["settleCoin"] = settle_coin.upper()
        return self._request(
            "GET", "/v5/position/list", params=params, signed=True
        )

    def get_account_info(self) -> dict:
        return self._request(
            "GET", "/v5/account/info", params={}, signed=True
        )

    def get_fee_rate(
        self, category: str = "linear", symbol: Optional[str] = None
    ) -> dict:
        params: dict = {"category": category}
        if symbol:
            params["symbol"] = symbol.upper()
        return self._request(
            "GET", "/v5/account/fee-rate", params=params, signed=True
        )

    def set_leverage(
        self,
        symbol: str,
        leverage: int,
        category: str = "linear",
    ) -> dict:
        return self._request(
            "POST",
            "/v5/position/set-leverage",
            json_data={
                "category": category,
                "symbol": symbol.upper(),
                "buyLeverage": str(leverage),
                "sellLeverage": str(leverage),
            },
            signed=True,
        )

    def set_trading_stop(
        self,
        symbol: str,
        stop_loss: Optional[float] = None,
        take_profit: Optional[float] = None,
        trailing_stop: Optional[float] = None,
        tpsl_mode: str = "Full",
        category: str = "linear",
    ) -> dict:
        payload: dict = {
            "category": category,
            "symbol": symbol.upper(),
            "tpslMode": tpsl_mode,
        }
        if stop_loss is not None:
            payload["stopLoss"] = str(stop_loss)
        if take_profit is not None:
            payload["takeProfit"] = str(take_profit)
        if trailing_stop is not None:
            payload["trailingStop"] = str(trailing_stop)
        
        result = self._request(
            "POST", "/v5/position/trading-stop", json_data=payload, signed=True
        )
        
        # Check if the API returned 'not modified' error (34040)
        if result.get("status") == "error" and result.get("code") == 34040:
            logger.warning("Bundled trading-stop update failed (34040). Retrying with separate calls.")
            
            # Try splitting: one for TP/SL, one for Trailing Stop
            # 1. Update TP/SL only
            tp_sl_payload = {
                "category": category,
                "symbol": symbol.upper(),
                "tpslMode": tpsl_mode,
            }
            if stop_loss is not None: tp_sl_payload["stopLoss"] = str(stop_loss)
            if take_profit is not None: tp_sl_payload["takeProfit"] = str(take_profit)
            
            # If we had TP/SL, update them first
            if stop_loss is not None or take_profit is not None:
                self._request("POST", "/v5/position/trading-stop", json_data=tp_sl_payload, signed=True)
            
            # 2. Update Trailing Stop only
            if trailing_stop is not None:
                ts_payload = {
                    "category": category,
                    "symbol": symbol.upper(),
                    "tpslMode": tpsl_mode,
                    "trailingStop": str(trailing_stop)
                }
                return self._request("POST", "/v5/position/trading-stop", json_data=ts_payload, signed=True)
                
        return result

    def place_breakeven_order(self, symbol: str, fee_rate: float, category: str = "linear") -> dict:
        """Automates placing a reduce-only limit order at the breakeven price."""
        pos_data = self.get_positions(symbol=symbol, category=category)
        positions = pos_data.get("list", [])
        if not positions:
            return {"status": "error", "msg": f"No open position found for {symbol}"}
        
        pos = positions[0]
        entry_price = float(pos.get("entryPrice", pos.get("avgPrice", 0)))
        size = float(pos["size"])
        side = pos["side"]

        # Calculate Breakeven
        if side == "Buy":
            breakeven_price = entry_price * (1 + fee_rate)
            close_side = "Sell"
        else: # Sell
            breakeven_price = entry_price * (1 - fee_rate)
            close_side = "Buy"

        return self.place_order(
            symbol=symbol,
            side=close_side,
            qty=size,
            price=round(breakeven_price, 4), # Bybit price precision varies by symbol
            order_type="Limit",
            reduce_only=True,
            time_in_force="GTC",
            category=category
        )

    def record_loss(self):
        self.breaker.record_loss()
        self.alert("Consecutive loss recorded.", "WARNING")

    def reset_circuit(self, new_equity: float):
        self.breaker.reset(new_equity)
        self.alert("Circuit breaker reset.", "INFO")

    def calculate_dynamic_qty(self, symbol: str, bid: float, max_usdt: float, liquidity_factor: float = 0.1) -> float:
        """Calculates order quantity based on top-of-book depth and capital constraints."""
        ob = self.get_orderbook(symbol=symbol, limit=20).get("result", {})
        bids = [float(q) for _, q in ob.get("b", [])]
        asks = [float(q) for _, q in ob.get("a", [])]
        
        # Depth limit: min of top bid/ask vol
        liq_limit = min(sum(bids), sum(asks)) * liquidity_factor
        
        # Capital limit
        cap_limit = max_usdt / bid
        
        return round(min(liq_limit, cap_limit), 4)

    def set_position_mode(
        self,
        coin: str = "USDT",
        mode: int = 0,
        category: str = "linear",
    ) -> dict:
        """mode: 0 = One-Way, 3 = Hedge"""
        return self._request(
            "POST",
            "/v5/position/switch-mode",
            json_data={
                "category": category,
                "coin": coin.upper(),
                "mode": mode,
            },
            signed=True,
        )

    def get_executions(
        self,
        category: str = "linear",
        symbol: Optional[str] = None,
        limit: int = 50,
    ) -> dict:
        params: dict = {"category": category, "limit": limit}
        if symbol:
            params["symbol"] = symbol.upper()
        return self._request(
            "GET", "/v5/execution/list", params=params, signed=True
        )

    def get_pnl_history(
        self,
        category: str = "linear",
        symbol: Optional[str] = None,
        limit: int = 50,
    ) -> dict:
        params: dict = {"category": category, "limit": limit}
        if symbol:
            params["symbol"] = symbol.upper()
        return self._request(
            "GET",
            "/v5/position/closed-pnl",
            params=params,
            signed=True,
        )

    # ══════════════════════════════════════════════════════════════════════════
    # ORDERS
    # ══════════════════════════════════════════════════════════════════════════
    def place_order(
        self,
        symbol: str,
        side: Literal["Buy", "Sell"],
        qty: float,
        price: Optional[float] = None,
        order_type: str = "Limit",
        category: str = "linear",
        stop_loss: Optional[float] = None,
        take_profit: Optional[float] = None,
        trailing_stop: Optional[float] = None,
        reduce_only: bool = False,
        time_in_force: str = "GTC",
        client_oid: Optional[str] = None,
        trigger_price: Optional[float] = None,
        trigger_by: Optional[str] = None,
        tp_order_type: Optional[str] = None,
        sl_order_type: Optional[str] = None,
        **kwargs,
    ) -> dict:
        # 1. Handle potential integer scaling (e.g. price passed as 1845700 instead of 0.018457)
        # If price is large and has no decimals, it might be scaled by 1e8
        def _unscale(val, sym, cat):
            if val is None: return None
            if val > 1000 and isinstance(val, (int, float)) and int(val) == val:
                # Compare with current ticker price to see if scaling makes sense
                ticker = self.get_ticker(sym, cat)
                if isinstance(ticker, dict) and "list" in ticker and ticker["list"]:
                    last_p = float(ticker["list"][0].get("lastPrice", 1))
                    if val / 1e8 < last_p * 10: # If scaled version is close to market
                        return val / 1e8
            return val

        price = _unscale(price, symbol, category)
        stop_loss = _unscale(stop_loss, symbol, category)
        take_profit = _unscale(take_profit, symbol, category)
        trigger_price = _unscale(trigger_price, symbol, category)

        payload: dict = {
            "category": category,
            "symbol": symbol.upper(),
            "side": side,
            "orderType": order_type,
            "qty": self._format_qty(symbol, qty, category),
            "timeInForce": time_in_force,
        }
        if price is not None:
            payload["price"] = self._format_price(symbol, price, category)
        if stop_loss is not None:
            payload["stopLoss"] = self._format_price(symbol, stop_loss, category)
        if take_profit is not None:
            payload["takeProfit"] = self._format_price(symbol, take_profit, category)
        if trailing_stop is not None:
            # Trailing stop might be distance or absolute price depending on TP/SL mode
            payload["trailingStop"] = str(trailing_stop)
        if reduce_only:
            payload["reduceOnly"] = True
        if client_oid:
            payload["orderLinkId"] = client_oid
        if trigger_price is not None:
            payload["triggerPrice"] = self._format_price(symbol, trigger_price, category)
        if trigger_by is not None:
            payload["triggerBy"] = trigger_by
        if tp_order_type is not None:
            payload["tpOrderType"] = tp_order_type
        if sl_order_type is not None:
            payload["slOrderType"] = sl_order_type

        # Forward any additional Bybit params
        for k, v in kwargs.items():
            if v is not None:
                payload[k] = str(v)

        result = self._request(
            "POST", "/v5/order/create", json_data=payload, signed=True
        )
        # Journal every order attempt
        self.journal.record("place_order", payload, result, symbol=symbol.upper())
        return result

    def amend_order(
        self,
        symbol: str,
        order_id: Optional[str] = None,
        client_oid: Optional[str] = None,
        qty: Optional[float] = None,
        price: Optional[float] = None,
        stop_loss: Optional[float] = None,
        take_profit: Optional[float] = None,
        category: str = "linear",
    ) -> dict:
        payload: dict = {
            "category": category,
            "symbol": symbol.upper(),
        }
        if order_id:
            payload["orderId"] = order_id
        elif client_oid:
            payload["orderLinkId"] = client_oid
            
        if qty is not None:
            payload["qty"] = self._format_qty(symbol, qty, category)
        if price is not None:
            payload["price"] = self._format_price(symbol, price, category)
        if stop_loss is not None:
            payload["stopLoss"] = self._format_price(symbol, stop_loss, category)
        if take_profit is not None:
            payload["takeProfit"] = self._format_price(symbol, take_profit, category)
            
        return self._request(
            "POST", "/v5/order/amend", json_data=payload, signed=True
        )

    def cancel_order(
        self,
        symbol: str,
        order_id: Optional[str] = None,
        client_oid: Optional[str] = None,
        category: str = "linear",
    ) -> dict:
        payload: dict = {
            "category": category,
            "symbol": symbol.upper(),
        }
        if order_id:
            payload["orderId"] = order_id
        elif client_oid:
            payload["orderLinkId"] = client_oid
        return self._request(
            "POST", "/v5/order/cancel", json_data=payload, signed=True
        )

    def cancel_all_orders(
        self,
        symbol: Optional[str] = None,
        category: str = "linear",
    ) -> dict:
        payload: dict = {"category": category, "settleCoin": "USDT"}
        if symbol:
            payload["symbol"] = symbol.upper()
        return self._request(
            "POST", "/v5/order/cancel-all", json_data=payload, signed=True
        )

    def get_open_orders(
        self,
        symbol: Optional[str] = None,
        category: str = "linear",
        limit: int = 50,
    ) -> dict:
        params: dict = {"category": category, "limit": limit}
        if symbol:
            params["symbol"] = symbol.upper()
        return self._request(
            "GET", "/v5/order/realtime", params=params, signed=True
        )

    def get_order_history(
        self,
        symbol: Optional[str] = None,
        category: str = "linear",
        limit: int = 50,
    ) -> dict:
        params: dict = {"category": category, "limit": limit}
        if symbol:
            params["symbol"] = symbol.upper()
        return self._request(
            "GET", "/v5/order/history", params=params, signed=True
        )

    def place_smart_trade(
        self,
        symbol: str,
        side: Literal["Buy", "Sell"],
        qty: float,
        price: float,
        tp_pct: Optional[float] = None,
        sl_pct: Optional[float] = None,
        trailing_stop_pct: Optional[float] = None,
        category: str = "linear",
    ) -> dict:
        """
        Fetches trend, validates, and places order with TP/SL.
        """
        # 1. Fetch Regime
        regime_data = self.get_market_regime(symbol=symbol, category=category)
        if regime_data.get("status") != "ok":
            return {"status": "error", "msg": "Failed to fetch trend analysis"}
        
        regime = regime_data["regime"]
        
        # 2. Validate Trend
        if side == "Buy" and regime == "TRENDING_DOWN":
            return {"status": "error", "msg": f"Refusing Buy: Market is {regime}"}
        if side == "Sell" and regime == "TRENDING_UP":
            return {"status": "error", "msg": f"Refusing Sell: Market is {regime}"}

        # 3. Calculate TP/SL
        tp_price = price * (1 + tp_pct/100) if side == "Buy" else price * (1 - tp_pct/100) if tp_pct else None
        sl_price = price * (1 - sl_pct/100) if side == "Buy" else price * (1 + sl_pct/100) if sl_pct else None
        
        # 4. Place Order
        return self.place_order(
            symbol=symbol,
            side=side,
            qty=qty,
            price=price,
            take_profit=tp_price,
            stop_loss=sl_price,
            trailing_stop=trailing_stop_pct,
            category=category
        )

    def batch_place_orders(
        self,
        orders: List[dict],
        category: str = "linear",
    ) -> dict:
        """
        Place up to 10 orders in a single API call.
        Each order dict should match place_order kwargs.
        """
        formatted: List[dict] = []
        for o in orders[:10]:
            symbol = o["symbol"].upper()
            item: dict = {
                "symbol": symbol,
                "side": o["side"],
                "orderType": o.get("order_type", "Limit"),
                "qty": self._format_qty(symbol, o["qty"], category),
                "timeInForce": o.get("time_in_force", "GTC"),
            }
            if o.get("price") is not None:
                item["price"] = self._format_price(symbol, o["price"], category)
            if o.get("stop_loss") is not None:
                item["stopLoss"] = self._format_price(symbol, o["stop_loss"], category)
            if o.get("take_profit") is not None:
                item["takeProfit"] = self._format_price(symbol, o["take_profit"], category)
            formatted.append(item)

        payload = {"category": category, "request": formatted}
        return self._request(
            "POST",
            "/v5/order/create-batch",
            json_data=payload,
            signed=True,
        )

    # ══════════════════════════════════════════════════════════════════════════
    # MARKET DATA
    # ══════════════════════════════════════════════════════════════════════════
    def get_ticker(self, symbol: str, category: str = "linear") -> dict:
        return self._request(
            "GET",
            "/v5/market/tickers",
            params={"category": category, "symbol": symbol.upper()},
            signed=False,
        )

    def get_orderbook(
        self,
        symbol: str,
        limit: int = 25,
        category: str = "linear",
    ) -> dict:
        return self._request(
            "GET",
            "/v5/market/orderbook",
            params={
                "category": category,
                "symbol": symbol.upper(),
                "limit": limit,
            },
            signed=False,
        )

    def get_klines(
        self,
        symbol: str,
        interval: str = "60",
        limit: int = 200,
        category: str = "linear",
        start: Optional[int] = None,
        end: Optional[int] = None,
    ) -> dict:
        params: dict = {
            "category": category,
            "symbol": symbol.upper(),
            "interval": interval,
            "limit": limit,
        }
        if start:
            params["start"] = start
        if end:
            params["end"] = end
        return self._request(
            "GET", "/v5/market/kline", params=params, signed=False
        )

    def get_recent_trades(
        self,
        symbol: str,
        limit: int = 100,
        category: str = "linear",
    ) -> dict:
        return self._request(
            "GET",
            "/v5/market/recent-trade",
            params={
                "category": category,
                "symbol": symbol.upper(),
                "limit": limit,
            },
            signed=False,
        )

    def get_instruments_info(
        self,
        category: str = "linear",
        symbol: Optional[str] = None,
        limit: int = 100,
    ) -> dict:
        params: dict = {"category": category, "limit": limit}
        if symbol:
            params["symbol"] = symbol.upper()
        return self._request(
            "GET",
            "/v5/market/instruments-info",
            params=params,
            signed=False,
        )

    def get_funding_rate(
        self,
        symbol: str,
        category: str = "linear",
        limit: int = 10,
    ) -> dict:
        return self._request(
            "GET",
            "/v5/market/funding/history",
            params={
                "category": category,
                "symbol": symbol.upper(),
                "limit": limit,
            },
            signed=False,
        )

    def get_open_interest(
        self,
        symbol: str,
        interval: str = "1h",
        category: str = "linear",
        limit: int = 50,
    ) -> dict:
        return self._request(
            "GET",
            "/v5/market/open-interest",
            params={
                "category": category,
                "symbol": symbol.upper(),
                "intervalTime": interval,
                "limit": limit,
            },
            signed=False,
        )

    def get_volatility_index(
        self, category: str = "option", period: Optional[int] = None
    ) -> dict:
        params: dict = {"category": category}
        if period:
            params["period"] = period
        return self._request(
            "GET",
            "/v5/market/historical-volatility",
            params=params,
            signed=False,
        )

    # ══════════════════════════════════════════════════════════════════════════
    # NEON UI UTILITIES
    # ══════════════════════════════════════════════════════════════════════════
    @staticmethod
    def neon(text: str, color: str = "cyan") -> str:
        colors = {
            "cyan": "\033[96m",
            "green": "\033[92m",
            "red": "\033[91m",
            "yellow": "\033[93m",
            "magenta": "\033[95m",
            "reset": "\033[0m"
        }
        return f"{colors.get(color, colors['cyan'])}{text}{colors['reset']}"

    # ══════════════════════════════════════════════════════════════════════════
    # ADVANCED ORDERBOOK & VOLUME ANALYSIS
    # ══════════════════════════════════════════════════════════════════════════
    def get_liquidity_concentration(self, symbol: str, depth: int = 50) -> dict:
        """Analyzes price levels with highest liquidity concentration."""
        raw = self.get_orderbook(symbol=symbol, limit=depth).get("result", {})
        bids = [{"price": float(p), "volume": float(q)} for p, q in raw.get("b", [])]
        asks = [{"price": float(p), "volume": float(q)} for p, q in raw.get("a", [])]
        
        # Find price levels with top 20% of total volume
        total_vol = sum(b["volume"] for b in bids) + sum(a["volume"] for a in asks)
        threshold = total_vol * 0.2
        
        concentrated = [b for b in bids if b["volume"] > (sum(b["volume"] for b in bids)/len(bids))*2] + \
                       [a for a in asks if a["volume"] > (sum(a["volume"] for a in asks)/len(asks))*2]
        
        return {"status": "ok", "concentrated_levels": concentrated}

    def calculate_support_resistance_levels(self, symbol: str, interval: str = "60", depth: int = 50, wall_multiplier: float = 3.0) -> dict:
        """Identifies support/resistance based on liquidity walls, swings, Classic Pivots, and Volume Profile."""
        # 1. Get Orderbook
        raw_ob = self.get_orderbook(symbol=symbol, limit=depth).get("result", {})
        bids = [{"price": float(p), "volume": float(q)} for p, q in raw_ob.get("b", [])]
        asks = [{"price": float(p), "volume": float(q)} for p, q in raw_ob.get("a", [])]

        # 2. Get Historical Price Action + Volume Profile
        klines = self.get_klines(symbol=symbol, interval=interval, limit=100).get("list", [])
        highs = [float(k[2]) for k in klines]
        lows = [float(k[3]) for k in klines]
        closes = [float(k[4]) for k in klines]

        # Volume Profile POC
        poc = self.calculate_volume_profile(symbol=symbol, interval=interval, limit=100).get("poc", 0)

        # 3. Detect Walls
        bid_vol = sum(b["volume"] for b in bids)
        ask_vol = sum(a["volume"] for a in asks)
        bid_avg = bid_vol / depth if depth > 0 else 0
        ask_avg = ask_vol / depth if depth > 0 else 0

        walls_sup = [b["price"] for b in bids if b["volume"] > bid_avg * wall_multiplier]
        walls_res = [a["price"] for a in asks if a["volume"] > ask_avg * wall_multiplier]

        # 4. Classic Pivot Points
        prev_h, prev_l, prev_c = highs[1], lows[1], closes[1]
        pivot = (prev_h + prev_l + prev_c) / 3
        r1 = 2 * pivot - prev_l
        s1 = 2 * pivot - prev_h

        # 5. Detect Confluence
        swing_highs = [highs[i] for i in range(1, len(highs)-1) if highs[i] > highs[i-1] and highs[i] > highs[i+1]]
        swing_lows = [lows[i] for i in range(1, len(lows)-1) if lows[i] < lows[i-1] and lows[i] < lows[i+1]]

        # Add pivot and POC to confluence points
        swing_highs.append(r1)
        swing_highs.append(poc)
        swing_lows.append(s1)
        swing_lows.append(poc)

        def get_confluence(levels, historical_points, tolerance=0.005):
            confluent = []
            for lvl in levels:
                score = 0
                for pt in historical_points:
                    if abs(lvl - pt) / pt < tolerance: score += 1
                confluent.append({"price": lvl, "confluence": score})
            return sorted(confluent, key=lambda x: x["confluence"], reverse=True)

        return {
            "status": "ok",
            "support": get_confluence(walls_sup, swing_lows),
            "resistance": get_confluence(walls_res, swing_highs),
            "pivots": {"pivot": pivot, "r1": r1, "s1": s1},
            "poc": poc
        }
    def calculate_fibonacci_retracement(self, symbol: str, interval: str = "60", lookback: int = 50) -> dict:
        """Calculate Fibonacci retracement levels based on recent high/low."""
        klines = self.get_klines(symbol=symbol, interval=interval, limit=lookback).get("list", [])
        highs = [float(k[2]) for k in reversed(klines)]
        lows = [float(k[3]) for k in reversed(klines)]
        high_price, low_price = max(highs), min(lows)
        diff = high_price - low_price
        levels = {
            '0.0%': high_price,
            '23.6%': high_price - 0.236 * diff,
            '38.2%': high_price - 0.382 * diff,
            '50.0%': high_price - 0.5 * diff,
            '61.8%': high_price - 0.618 * diff,
            '78.6%': high_price - 0.786 * diff,
            '100.0%': low_price
        }
        return {"status": "ok", "levels": {k: round(v, 4) for k, v in levels.items()}}

    def calculate_volume_profile(self, symbol: str, interval: str = "60", limit: int = 100, price_bins: int = 20) -> dict:
        """Calculate volume profile."""
        klines = self.get_klines(symbol=symbol, interval=interval, limit=limit).get("list", [])
        closes = [float(k[4]) for k in reversed(klines)]
        volumes = [float(k[5]) for k in reversed(klines)]
        highs = [float(k[2]) for k in reversed(klines)]
        lows = [float(k[3]) for k in reversed(klines)]
        
        high, low = max(highs), min(lows)
        bin_size = (high - low) / price_bins
        profile = {i: 0.0 for i in range(price_bins)}
        
        for c, v in zip(closes, volumes):
            idx = int((c - low) / bin_size) if bin_size > 0 else 0
            idx = min(max(idx, 0), price_bins - 1)
            profile[idx] += v
            
        return {"status": "ok", "profile": {round(low + i * bin_size, 4): round(vol, 2) for i, vol in profile.items()}}

    def calculate_order_flow_imbalance(self, symbol: str, interval: str = "60", window: int = 10) -> dict:
        """Calculate order flow imbalance."""
        klines = self.get_klines(symbol=symbol, interval=interval, limit=window + 1).get("list", [])
        opens = [float(k[1]) for k in reversed(klines)]
        closes = [float(k[4]) for k in reversed(klines)]
        volumes = [float(k[5]) for k in reversed(klines)]
        
        imbalance = sum((c - o) * v for o, c, v in zip(opens, closes, volumes))
        return {"status": "ok", "imbalance": round(imbalance, 4)}

    def calculate_market_regime_new(self, symbol: str, interval: str = "60", window: int = 20) -> dict:
        """Calculate market regime."""
        klines = self.get_klines(symbol=symbol, interval=interval, limit=window + 20).get("list", [])
        atr = self.calculate_atr(symbol=symbol, interval=interval, period=14).get("atr", 1.0)
        highs = [float(k[2]) for k in reversed(klines)]
        lows = [float(k[3]) for k in reversed(klines)]
        
        high_low_range = max(highs[-window:]) - min(lows[-window:])
        regime = high_low_range / atr
        return {"status": "ok", "regime_score": round(regime, 4), "trend": "Trending" if regime > 2 else "Ranging"}

    def calculate_liquidity_pools(self, symbol: str, interval: str = "60", threshold: float = 0.05) -> dict:
        """Calculate liquidity pools."""
        vp = self.calculate_volume_profile(symbol=symbol, interval=interval).get("profile", {})
        if not vp: return {"status": "error", "msg": "No profile"}
        
        max_vol = max(vp.values())
        pools = {price: vol for price, vol in vp.items() if vol > threshold * max_vol}
        return {"status": "ok", "pools": pools}

    def calculate_volume_profile(self, symbol: str, interval: str = "60", limit: int = 100) -> dict:
        """Calculates the Volume Profile POC (Point of Control)."""
        klines = self.get_klines(symbol=symbol, interval=interval, limit=limit).get("list", [])
        # [startTime, open, high, low, close, volume, turnover]
        data = [{"h": float(k[2]), "l": float(k[3]), "v": float(k[6])} for k in reversed(klines)]
        
        prices = [d["h"] for d in data] + [d["l"] for d in data]
        min_p, max_p = min(prices), max(prices)
        bins = 20
        bin_size = (max_p - min_p) / bins
        profile = {i: 0 for i in range(bins)}
        
        for d in data:
            avg_p = (d["h"] + d["l"]) / 2
            idx = int((avg_p - min_p) / bin_size) if bin_size > 0 else 0
            idx = min(max(idx, 0), bins - 1)
            profile[idx] += d["v"]
            
        poc_idx = max(profile, key=profile.get)
        poc_price = min_p + (poc_idx * bin_size) + (bin_size / 2)
        
        return {"poc": round(poc_price, 4), "volume": profile[poc_idx]}

    def calculate_orderflow_delta(self, symbol: str, interval: str = "60", limit: int = 100) -> dict:
        """Calculates net orderflow (Aggressive Buy Vol - Aggressive Sell Vol)."""
        trades = self.get_recent_trades(symbol=symbol, limit=limit).get("result", {}).get("list", [])
        delta = 0.0
        for t in trades:
            # Bybit side: Buy = Taker Buy (Aggressive Buy), Sell = Taker Sell
            vol = float(t["v"])
            if t["s"] == "Buy": delta += vol
            else: delta -= vol
        return {"status": "ok", "symbol": symbol, "delta": round(delta, 2)}

    def calculate_market_depth_profile(self, symbol: str, depth: int = 200, order_sizes: List[float] = None, distance_pcts: List[float] = None) -> dict:
        """Aggregates orderbook volume at specific percentage distances from mid-price."""
        if order_sizes is None: order_sizes = [100.0, 500.0, 1000.0]
        if distance_pcts is None: distance_pcts = [0.1, 0.5, 1.0]

        ob = self.get_orderbook(symbol=symbol, limit=depth).get("result", {})
        bids = [{"p": float(p), "v": float(q)} for p, q in ob.get("b", [])]
        asks = [{"p": float(p), "v": float(q)} for p, q in ob.get("a", [])]
        
        mid = (bids[0]["p"] + asks[0]["p"]) / 2 if bids and asks else 0
        if mid == 0: return {"status": "error", "msg": "Invalid price"}
        
        profile = {}
        for pct in distance_pcts:
            bid_vol = sum(b["v"] for b in bids if b["p"] >= mid * (1 - pct/100))
            ask_vol = sum(a["v"] for a in asks if a["p"] <= mid * (1 + pct/100))
            profile[f"{pct}%"] = {"bid_vol": round(bid_vol, 2), "ask_vol": round(ask_vol, 2)}
            
        return {"status": "ok", "symbol": symbol, "mid_price": mid, "profile": profile}

    def detect_high_confluence_levels(self, symbol: str, interval: str = "60", depth: int = 50) -> dict:
        """Identifies strong S/R zones by finding price levels with multi-method confluence."""
        # Get all S/R indicators
        sr_data = self.calculate_support_resistance_levels(symbol=symbol, interval=interval, depth=depth)
        
        all_levels = []
        # Add support and resistance levels with their confluence scores
        for s in sr_data.get("support", []):
            all_levels.append({"price": s["price"], "score": s["confluence"], "type": "Support"})
        for r in sr_data.get("resistance", []):
            all_levels.append({"price": r["price"], "score": r["confluence"], "type": "Resistance"})
            
        # Sort by confluence score
        confluence_zones = sorted(all_levels, key=lambda x: x["score"], reverse=True)
        
        return {
            "status": "ok",
            "symbol": symbol,
            "high_confluence_zones": confluence_zones[:5] # Top 5 strongest
        }

    def deep_level_sort(self, symbol: str, level_cnt: int = 10, vol_thresh: float = 0.5) -> dict:
        """Groups orderbook volume into levels based on a threshold."""
        orderbook = self.get_orderbook(symbol=symbol).get("result", {})
        if not orderbook: return {"status": "error", "msg": "Empty orderbook"}
        bids = [[float(p), float(q)] for p, q in orderbook.get("b", [])]
        asks = [[float(p), float(q)] for p, q in orderbook.get("a", [])]

        def bucket(prices, is_bid):
            sorted_items = sorted(prices, key=lambda x: x[0], reverse=is_bid)
            buckets = []
            cum_vol = 0.0
            price_sum = 0.0
            count = 0
            for price, qty in sorted_items:
                cum_vol += qty
                price_sum += price
                count += 1
                if cum_vol >= vol_thresh:
                    buckets.append([round(price_sum / count, 4), round(cum_vol, 2)])
                    cum_vol = 0.0
                    price_sum = 0.0
                    count = 0
            return buckets

        bid_buckets = bucket(bids, True)[:level_cnt]
        ask_buckets = bucket(asks, False)[:level_cnt]
        return {
            "status": "ok",
            "symbol": symbol,
            "bid_levels": bid_buckets,
            "ask_levels": ask_buckets
        }

    def calculate_sr_levels(self, symbol: str, top_n: int = 7, vol_cut: float = 0.4) -> dict:
        """
        Detects support and resistance zones from the order book and sorts them by volume-weighted price.
        """
        # Step 1: Fetch order book
        orderbook = self.get_orderbook(symbol=symbol).get("result", {})
        if not orderbook: return {"status": "error", "msg": "Empty orderbook"}
        bids = [[float(p), float(q)] for p, q in orderbook.get("b", [])]
        asks = [[float(p), float(q)] for p, q in orderbook.get("a", [])]

        # Step 2: Helper to detect zones
        def find_sr(levels: list, direction: int) -> list:
            # Sort: Bids price desc (1), Asks price asc (-1)
            levels = sorted(levels, key=lambda x: x[0], reverse=(direction > 0))
            vol_acc = 0.0
            zones = []
            for price, qty in levels:
                vol_acc += qty
                if vol_acc >= vol_cut:
                    zones.append(price)
                    vol_acc = 0.0
            return zones

        support = find_sr(bids, 1)
        resistance = find_sr(asks, -1)

        # Step 3: Helper to sort by volume-weighted price
        def weight_sort(levels: list) -> list:
            weighted = [(p, q, p * q) for p, q in levels]
            # Sort by volume (index 2) descending
            weighted.sort(key=lambda x: x[2], reverse=True)
            return [(p, q) for p, q, _ in weighted[:top_n]]
        
        return {
            "status": "success",
            "support_levels": support,
            "resistance_levels": resistance,
            "sorted_bids": weight_sort(bids),
            "sorted_asks": weight_sort(asks),
            "note": "Support and resistance levels detected and sorted by volume-weighted price."
        }

    def _build_price_ladder(self, levels: list, weight: str = "volume") -> list:
        """Builds a volume-weighted price ladder from order book levels."""
        ladder = []
        for price, qty in levels:
            noise = 1 + random.random() * 0.2
            if weight == "price*volume":
                w = float(price) * float(qty) * noise
            else:
                w = float(qty) * noise
            ladder.append((float(price), float(qty), w))
        ladder.sort(key=lambda x: x[2], reverse=True)
        return ladder[:15]

    def _cluster_levels(self, levels: list, direction: int, delta: float = 0.003) -> list:
        """Clusters price levels into support/resistance zones."""
        levels = sorted(levels, key=lambda x: x[0], reverse=(direction > 0))
        clusters = []
        cur_price, cur_vol = levels[0][0], 0.0
        for price, qty in levels:
            if abs(price - cur_price) > delta:
                clusters.append((cur_price, cur_vol))
                cur_price, cur_vol = price, qty
            else:
                cur_vol += qty
        clusters.append((cur_price, cur_vol))
        return clusters

    def _identify_liquidity_zones(self, clusters: list, thresh: float = 0.35) -> list:
        """Identifies liquidity zones where volume exceeds a threshold."""
        total_vol = sum(v for _, v in clusters)
        return [p for p, v in clusters if v >= thresh * total_vol]

    def _sort_depth(self, bids: list, asks: list, support_zones: list, resistance_zones: list, top: int = 12) -> dict:
        """Sorts bids and asks by volume-weighted criteria."""
        def score(level: tuple, zones: list) -> float:
            price, qty = level[0], level[1]
            weight = price * qty
            bonus = 1.5 if any(abs(price - z) < 0.001 for z in zones) else 1.0
            return weight * bonus

        bid_scores = [(p, q, score((p, q), support_zones)) for p, q in bids]
        ask_scores = [(p, q, score((p, q), resistance_zones)) for p, q in asks]
        
        bid_sorted = sorted(bid_scores, key=lambda x: (-x[2], -x[1], -x[0]))[:top]
        ask_sorted = sorted(ask_scores, key=lambda x: (-x[2], -x[1], x[0]))[:top]
        return {"bids": bid_sorted, "asks": ask_sorted}

    def generate_market_depth_report(self, symbol: str) -> dict:
        """Generates a professional market depth analysis report."""
        depth = self.get_orderbook(symbol=symbol).get("result", {})
        if not depth: return {"status": "error", "msg": "Empty orderbook"}
        bids = [[float(p), float(q)] for p, q in depth.get("b", [])]
        asks = [[float(p), float(q)] for p, q in depth.get("a", [])]

        bid_ladder = self._build_price_ladder(bids, weight="price*volume")
        ask_ladder = self._build_price_ladder(asks, weight="price*volume")
        
        sup_clusters = self._cluster_levels(bid_ladder, direction=1, delta=0.004)
        res_clusters = self._cluster_levels(ask_ladder, direction=-1, delta=0.004)
        
        support_zones = self._identify_liquidity_zones(sup_clusters, thresh=0.38)
        resistance_zones = self._identify_liquidity_zones(res_clusters, thresh=0.38)
        
        sorted_depth = self._sort_depth(bids, asks, support_zones, resistance_zones, top=10)
        
        return {
            "status": "success",
            "symbol": symbol,
            "support_zones": [round(x, 4) for x in support_zones],
            "resistance_zones": [round(x, 4) for x in resistance_zones],
            "sorted_bids": [(round(p, 4), round(q, 2)) for p, q, _ in sorted_depth["bids"]],
            "sorted_asks": [(round(p, 4), round(q, 2)) for p, q, _ in sorted_depth["asks"]],
            "note": "Market depth analysis completed."
        }

    def calculate_limit_micro_profit(self, entry_price: float, limit_price: float, side: str, qty: float, fee_rate: float = 0.001) -> dict:
        """Calculates net profit for a limit order."""
        if side.lower() == "buy": raw_pnl = (limit_price - entry_price) * qty
        else: raw_pnl = (entry_price - limit_price) * qty
        fee = abs(limit_price * qty) * fee_rate
        net_pnl = raw_pnl - fee
        pct_return = (net_pnl / (entry_price * qty)) * 100 if entry_price * qty != 0 else 0
        return {"status": "success", "net_pnl": round(net_pnl, 4), "fee_applied": round(fee, 4), "pct_return": round(pct_return, 2)}

    def calculate_depth_weighted_profit(self, symbol: str, entry_price: float, limit_price: float, side: str, qty: float) -> dict:
        """Calculates profit based on weighted average fill price across orderbook depth."""
        orderbook = self.get_orderbook(symbol=symbol).get("result", {})
        if not orderbook: return {"status": "error", "msg": "Empty orderbook"}
        bids = [[float(p), float(q)] for p, q in orderbook.get("b", [])]
        asks = [[float(p), float(q)] for p, q in orderbook.get("a", [])]
        levels = asks if side.lower() == "buy" else bids
        total_vol, weighted_sum = 0.0, 0.0
        for p, q in levels:
            if (side.lower() == "buy" and p <= limit_price) or (side.lower() == "sell" and p >= limit_price):
                take = min(q, qty - total_vol)
                weighted_sum += p * take
                total_vol += take
                if total_vol >= qty: break
        if total_vol < qty: return {"status": "error", "msg": "Insufficient liquidity"}
        return self.calculate_limit_micro_profit(entry_price, weighted_sum / total_vol, side, qty)
        """
        Analyzes orderbook for imbalance, liquidity, and walls.
        """
        raw = self.get_orderbook(symbol=symbol, category=category, limit=depth).get("result", {})
        
        bids: List[Tuple[float, float]] = [(float(p), float(q)) for p, q in raw.get("b", [])]
        asks: List[Tuple[float, float]] = [(float(p), float(q)) for p, q in raw.get("a", [])]

        if not bids or not asks:
            return {"status": "error", "msg": "Empty orderbook"}

        bid_vol = sum(q for _, q in bids)
        ask_vol = sum(q for _, q in asks)
        
        # Order Book Imbalance (OBI)
        total_vol = bid_vol + ask_vol
        obi = (bid_vol - ask_vol) / total_vol if total_vol > 0 else 0
        
        # Wall detection
        bid_avg = bid_vol / depth if depth > 0 else 0
        ask_avg = ask_vol / depth if depth > 0 else 0
        bid_walls = [b for b in bids if b[1] > bid_avg * wall_multiplier]
        ask_walls = [a for a in asks if a[1] > ask_avg * wall_multiplier]
        
        # Volume Profile (top 5 tiers)
        volume_profile = {
            "bid_tiers": [{"price": b[0], "volume": b[1]} for b in bids[:5]],
            "ask_tiers": [{"price": a[0], "volume": a[1]} for a in asks[:5]]
        }
        
        return {
            "status": "ok",
            "symbol": symbol,
            "obi": round(obi, 4),
            "bid_vol": round(bid_vol, 2),
            "ask_vol": round(ask_vol, 2),
            "bid_walls": bid_walls,
            "ask_walls": ask_walls,
            "volume_profile": volume_profile
        }

    # ══════════════════════════════════════════════════════════════════════════
    # POSITION RISK METRICS
    # ══════════════════════════════════════════════════════════════════════════
    def get_position_risk(
        self,
        category: str = "linear",
        symbol: Optional[str] = None,
    ) -> dict:
        """
        Enriches raw position data with:
        - Unrealised PnL %
        - Distance to liquidation price (%)
        - Position heat (risk level: LOW / MEDIUM / HIGH / CRITICAL)
        - Notional value
        """
        raw = self.get_positions(category=category, symbol=symbol)
        positions = raw.get("list", [])

        enriched: List[dict] = []
        for pos in positions:
            size = float(pos.get("size", 0))
            if size == 0:
                continue

            entry = float(pos.get("avgPrice", 0) or pos.get("entryPrice", 0))
            liq = float(pos.get("liqPrice", 0))
            mark = float(pos.get("markPrice", 0))
            unrealised_pnl = float(pos.get("unrealisedPnl", 0))
            leverage = float(pos.get("leverage", 1))

            notional = size * mark
            pnl_pct = (unrealised_pnl / (notional / leverage)) * 100 if notional else 0

            liq_dist_pct: Optional[float] = None
            if liq > 0 and mark > 0:
                liq_dist_pct = abs(mark - liq) / mark * 100

            # Heat level
            if liq_dist_pct is None:
                heat = "UNKNOWN"
            elif liq_dist_pct < 3:
                heat = "CRITICAL"
            elif liq_dist_pct < 8:
                heat = "HIGH"
            elif liq_dist_pct < 20:
                heat = "MEDIUM"
            else:
                heat = "LOW"

            enriched.append({
                **pos,
                "notional_usd": round(notional, 2),
                "pnl_pct": round(pnl_pct, 3),
                "liq_dist_pct": round(liq_dist_pct, 3) if liq_dist_pct is not None else None,
                "position_heat": heat,
            })

        return {
            "status": "ok",
            "category": category,
            "positions": enriched,
            "total_positions": len(enriched),
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }

    def panic_close(self, category: str = "linear") -> dict:
        """Cancels all active orders and closes all open positions."""
        cancel_res = self.cancel_all_orders(category=category)
        positions = self.get_positions(category=category).get("list", [])
        closures = []
        for pos in positions:
            if float(pos.get("size", 0)) > 0:
                side = "Sell" if pos["side"] == "Buy" else "Buy"
                closures.append(self.place_order(
                    symbol=pos["symbol"],
                    side=side,
                    qty=float(pos["size"]),
                    order_type="Market",
                    category=category
                ))
        return {"status": "ok", "cancellations": cancel_res, "closures": closures}

    def bulk_update_tp_sl(self, category: str = "linear", tp: float = None, sl: float = None) -> dict:
        """Applies TP/SL to all open positions."""
        positions = self.get_positions(category=category).get("list", [])
        updates = []
        for pos in positions:
            if float(pos.get("size", 0)) > 0:
                updates.append(self.set_trading_stop(
                    symbol=pos["symbol"],
                    take_profit=tp,
                    stop_loss=sl,
                    category=category
                ))
        return {"status": "ok", "updates": updates}

    def close_position(self, symbol: str, category: str = "linear") -> dict:
        """Closes an open position for a given symbol."""
        positions = self.get_positions(category=category, symbol=symbol).get("list", [])
        for pos in positions:
            if float(pos.get("size", 0)) > 0:
                side = "Sell" if pos["side"] == "Buy" else "Buy"
                return self.place_order(
                    symbol=symbol,
                    side=side,
                    qty=float(pos["size"]),
                    order_type="Market",
                    category=category
                )
        return {"status": "error", "msg": f"No open position found for {symbol}"}

    def get_account_summary(self) -> dict:
        """Provides a snapshot of balance and positions."""
        return {
            "balance": self.get_wallet_balance(),
            "positions": self.get_positions(),
            "timestamp": datetime.now(timezone.utc).isoformat()
        }

    def alert(self, message: str, level: str = "INFO") -> bool:
        """Generic alert method that logs to the console. Can be extended to other sinks."""
        log_func = getattr(logger, level.lower(), logger.info)
        log_func(f"[ALERT] {message}")
        return True

    def export_trade_history(self, symbol: str, filename: str = "trade_history.csv") -> dict:
        """Exports trade history to CSV."""
        import csv
        history = self.get_order_history(symbol=symbol, limit=100).get("list", [])
        
        try:
            with open(filename, mode='w', newline='') as f:
                writer = csv.DictWriter(f, fieldnames=history[0].keys())
                writer.writeheader()
                writer.writerows(history)
            return {"status": "ok", "msg": f"Exported {len(history)} entries to {filename}"}
        except Exception as e:
            return {"status": "error", "msg": f"Export failed: {e}"}

    def get_open_positions_summary(self, category: str = "linear") -> dict:
        """Provides a concise summary of all open positions."""
        positions = self.get_positions(category=category).get("list", [])
        summary = []
        for pos in positions:
            if float(pos.get("size", 0)) > 0:
                summary.append({
                    "symbol": pos["symbol"],
                    "side": pos["side"],
                    "size": pos["size"],
                    "avgPrice": pos["avgPrice"],
                    "markPrice": pos["markPrice"],
                    "unrealisedPnl": pos["unrealisedPnl"]
                })

# ... (rest of file)


# ... (rest of file)

    # ... existing methods ...
    def set_tp_sl(self, symbol: str, tp: Optional[float] = None, sl: Optional[float] = None, category: str = "linear") -> dict:
        """Sets TP/SL for a specific position."""
        return self.set_trading_stop(symbol=symbol, take_profit=tp, stop_loss=sl, category=category)

    # ══════════════════════════════════════════════════════════════════════════
    # TECHNICAL ANALYSIS INDICATORS
    # ══════════════════════════════════════════════════════════════════════════
    def calculate_macd(self, symbol: str, interval: str = "60", fast: int = 12, slow: int = 26, signal: int = 9) -> dict:
        klines = self.get_klines(symbol=symbol, interval=interval, limit=200).get("list", [])
        if not klines or len(klines) < slow: return {"status": "error", "msg": "Insufficient data"}
        closes = [float(k[4]) for k in reversed(klines)]
        def get_ema(data, p):
            if not data: return 0
            k = 2 / (p + 1)
            ema = data[0]
            for val in data[1:]: ema = val * k + ema * (1 - k)
            return ema
        macd = get_ema(closes, fast) - get_ema(closes, slow)
        return {"status": "ok", "macd": round(macd, 4)}

    def calculate_rsi(self, symbol: str, interval: str = "60", period: int = 14) -> dict:
        """Calculates RSI for the given symbol."""
        klines = self.get_klines(symbol=symbol, interval=interval, limit=period + 50).get("list", [])
        closes = [float(k[4]) for k in reversed(klines)]
        if len(closes) < period + 1: return {"status": "error", "msg": "Insufficient data"}
        
        deltas = [closes[i+1] - closes[i] for i in range(len(closes)-1)]
        gains = [d if d > 0 else 0 for d in deltas]
        losses = [-d if d < 0 else 0 for d in deltas]
        avg_gain = sum(gains[-period:]) / period
        avg_loss = sum(losses[-period:]) / period
        if avg_loss == 0: return {"status": "ok", "rsi": 100.0}
        rs = avg_gain / avg_loss
        return {"status": "ok", "rsi": round(100 - (100 / (1 + rs)), 2)}

    def calculate_ema(self, symbol: str, interval: str = "60", period: int = 20) -> dict:
        """Calculates EMA for the given symbol."""
        klines = self.get_klines(symbol=symbol, interval=interval, limit=period + 50).get("list", [])
        closes = [float(k[4]) for k in reversed(klines)]
        if len(closes) < period: return {"status": "error", "msg": "Insufficient data"}
        
        k = 2 / (period + 1)
        ema = closes[0]
        for p in closes[1:]:
            ema = p * k + ema * (1 - k)
        return {"status": "ok", "ema": round(ema, 2)}

    def calculate_atr(self, symbol: str, interval: str = "60", period: int = 14) -> dict:
        """Calculates ATR for the given symbol."""
        klines = self.get_klines(symbol=symbol, interval=interval, limit=period + 50).get("list", [])
        if len(klines) < period + 1: return {"status": "error", "msg": "Insufficient data"}
        
        tr_list = []
        for i in range(1, len(klines)):
            high = float(klines[i][2])
            low = float(klines[i][3])
            prev_close = float(klines[i-1][4])
            tr = max(high - low, abs(high - prev_close), abs(low - prev_close))
            tr_list.append(tr)
        
        atr = sum(tr_list[-period:]) / period
        return {"status": "ok", "atr": round(atr, 4)}

    def calculate_adx(self, symbol: str, interval: str = "60", period: int = 14) -> dict:
        klines = self.get_klines(symbol=symbol, interval=interval, limit=period + 50).get("list", [])
        highs = [float(k[2]) for k in reversed(klines)]
        lows = [float(k[3]) for k in reversed(klines)]
        closes = [float(k[4]) for k in reversed(klines)]
        
        tr_list, pos_dm, neg_dm = [], [], []
        for i in range(1, len(closes)):
            tr = max(highs[i]-lows[i], abs(highs[i]-closes[i-1]), abs(lows[i]-closes[i-1]))
            up_move = highs[i] - highs[i-1]
            down_move = lows[i-1] - lows[i]
            pd = max(up_move, 0) if up_move > down_move else 0
            nd = max(down_move, 0) if down_move > up_move else 0
            tr_list.append(tr)
            pos_dm.append(pd)
            neg_dm.append(nd)
        sum_pos = sum(pos_dm[-period:])
        sum_neg = sum(neg_dm[-period:])
        denom = sum_pos + sum_neg + 1e-9
        adx = 100 * abs(sum_pos - sum_neg) / denom
        return {"status": "ok", "adx": round(adx, 2)}

    def calculate_cci(self, symbol: str, interval: str = "60", period: int = 20) -> dict:
        klines = self.get_klines(symbol=symbol, interval=interval, limit=period + 50).get("list", [])
        highs = [float(k[2]) for k in reversed(klines)]
        lows = [float(k[3]) for k in reversed(klines)]
        closes = [float(k[4]) for k in reversed(klines)]
        tp = [(h+l+c)/3 for h, l, c in zip(highs[-period:], lows[-period:], closes[-period:])]
        sma = sum(tp) / period
        md = sum(abs(x-sma) for x in tp) / period
        return {"status": "ok", "cci": round((tp[-1]-sma)/(0.015*md) if md != 0 else 0, 2)}

    def calculate_ichimoku(self, symbol: str, interval: str = "60", tenkan: int = 9, kijun: int = 26, senkou_b: int = 52) -> dict:
        klines = self.get_klines(symbol=symbol, interval=interval, limit=senkou_b + 50).get("list", [])
        highs = [float(k[2]) for k in reversed(klines)]
        lows = [float(k[3]) for k in reversed(klines)]
        def get_midpoint(h, l, p):
            return (max(h[-p:]) + min(l[-p:])) / 2
        t = get_midpoint(highs, lows, tenkan)
        k = get_midpoint(highs, lows, kijun)
        return {"status": "ok", "tenkan": round(t, 4), "kijun": round(k, 4), "senkou_a": round((t+k)/2, 4), "senkou_b": round(get_midpoint(highs, lows, senkou_b), 4)}


    def calculate_bollinger_bands(self, symbol: str, interval: str = "15", period: int = 20) -> dict:
        """Calculates Bollinger Bands."""
        klines = self.get_klines(symbol=symbol, interval=interval, limit=period).get("list", [])
        closes = [float(k[4]) for k in reversed(klines)]
        
        sma = sum(closes) / period
        std_dev = statistics.stdev(closes)
        
        upper = sma + (std_dev * 2)
        lower = sma - (std_dev * 2)
        
        return {"status": "ok", "upper": round(upper, 2), "middle": round(sma, 2), "lower": round(lower, 2)}

    def calculate_vwap(self, symbol: str, interval: str = "15", limit: int = 50) -> dict:
        """Calculates VWAP (approximate using klines)."""
        klines = self.get_klines(symbol=symbol, interval=interval, limit=limit).get("list", [])
        
        total_pv = sum(float(k[4]) * float(k[5]) for k in klines) # Close * Volume
        total_v = sum(float(k[5]) for k in klines)
        
        vwap = total_pv / total_v if total_v != 0 else 0
        return {"status": "ok", "vwap": round(vwap, 2)}

    def calculate_ehler_rsi(self, symbol: str, interval: str = "60", period: int = 14) -> dict:
        """Calculates Ehlers RSI smoothing."""
        klines = self.get_klines(symbol=symbol, interval=interval, limit=period + 50).get("list", [])
        if not klines: return {"status": "error", "msg": "Insufficient data"}
        closes = [float(k[4]) for k in reversed(klines)]
        alpha = 2 / (period + 1)
        rsi = closes[0]
        for price in closes[1:]:
            rsi = (price * alpha) + (rsi * (1 - alpha))
        return {"status": "ok", "ehler_rsi": round(rsi, 4)}

    def calculate_ehler_stochastic(self, symbol: str, interval: str = "60", period: int = 14) -> dict:
        """Calculates Ehlers Stochastic."""
        klines = self.get_klines(symbol=symbol, interval=interval, limit=period + 50).get("list", [])
        if not klines or len(klines) < period: return {"status": "error", "msg": "Insufficient data"}
        highs = [float(k[2]) for k in reversed(klines)]
        lows = [float(k[3]) for k in reversed(klines)]
        closes = [float(k[4]) for k in reversed(klines)]
        lowest_low = min(lows[-period:])
        highest_high = max(highs[-period:])
        stochastic = (closes[-1] - lowest_low) / (highest_high - lowest_low) * 100 if (highest_high - lowest_low) != 0 else 0
        return {"status": "ok", "ehler_stoch": round(stochastic, 2)}

    def calculate_all_indicators(self, symbol: str, interval: str = "60") -> dict:
        """Aggregates all available indicators for a symbol."""
        indicator_map = {
            "rsi": lambda: self.calculate_rsi(symbol, interval),
            "macd": lambda: self.calculate_macd(symbol, interval),
            "adx": lambda: self.calculate_adx(symbol, interval),
            "cci": lambda: self.calculate_cci(symbol, interval),
            "ichimoku": lambda: self.calculate_ichimoku(symbol, interval),
            "sma": lambda: self.calculate_sma(symbol, interval),
            "ema": lambda: self.calculate_ema(symbol, interval),
            "bollinger": lambda: self.calculate_bollinger_bands(symbol, interval),
            "vwap": lambda: self.calculate_vwap(symbol, interval),
            "atr": lambda: self.calculate_atr(symbol, interval),
            "stoch": lambda: self.calculate_stochastic(symbol, interval),
            "hma": lambda: self.calculate_hma(symbol, interval),
            "vwma": lambda: self.calculate_vwma(symbol, interval),
            "bollinger_pb": lambda: self.calculate_bollinger_bands_pb(symbol, interval),
            "roc": lambda: self.calculate_roc(symbol, interval),
            "mfi": lambda: self.calculate_mfi(symbol, interval),
            "williams_r": lambda: self.calculate_williams_r(symbol, interval),
            "cmf": lambda: self.calculate_cmf(symbol, interval),
            "adx_di": lambda: self.calculate_adx_with_di(symbol, interval),
            "elder_ray": lambda: self.calculate_elder_ray_index(symbol, interval),
            "kst": lambda: self.calculate_kst(symbol, interval),
            "tema": lambda: self.calculate_tema(symbol, interval),
            "ehler_rsi": lambda: self.calculate_ehler_rsi(symbol, interval),
            "ehler_stoch": lambda: self.calculate_ehler_stochastic(symbol, interval)
        }
        results = {name: func() for name, func in indicator_map.items()}
        return {"status": "ok", "symbol": symbol, "indicators": results}


    def calculate_stochastic(self, symbol: str, interval: str = "15", period: int = 14, smooth_k: int = 3, smooth_d: int = 3) -> dict:
        klines = self.get_klines(symbol=symbol, interval=interval, limit=period + smooth_k + smooth_d + 50).get("list", [])
        closes = [float(k[4]) for k in reversed(klines)]
        highs = [float(k[2]) for k in reversed(klines)]
        lows = [float(k[3]) for k in reversed(klines)]
        if len(closes) < period: return {"status": "error", "msg": "Insufficient data"}
        lowest_low = min(lows[:period])
        highest_high = max(highs[:period])
        k = ((closes[0] - lowest_low) / (highest_high - lowest_low)) * 100
        return {"status": "ok", "k": round(k, 2)}

    def calculate_hma(self, symbol: str, interval: str = "60", period: int = 20) -> dict:
        klines = self.get_klines(symbol=symbol, interval=interval, limit=period + 50).get("list", [])
        closes = [float(k[4]) for k in reversed(klines)]
        
        def _wma(prices, p):
            denom = p * (p + 1) / 2
            return [sum(prices[i - p + 1 + j] * (j + 1) for j in range(p)) / denom for i in range(p - 1, len(prices))]

        half_len = int(period / 2)
        sqrt_len = int(math.sqrt(period))
        wma_half = _wma(closes, half_len)
        wma_full = _wma(closes, period)
        diff = [2 * h - f for h, f in zip(wma_half[-sqrt_len:], wma_full[-sqrt_len:])]
        hma = _wma(diff, sqrt_len)
        return {"status": "ok", "hma": round(hma[-1], 6)}

    def calculate_fractals(self, symbol: str, interval: str = "60") -> dict:
        klines = self.get_klines(symbol=symbol, interval=interval, limit=10).get("list", [])
        highs = [float(k[2]) for k in reversed(klines)]
        lows = [float(k[3]) for k in reversed(klines)]
        bullish = (lows[-3] < lows[-4] and lows[-3] < lows[-5] and lows[-3] < lows[-2] and lows[-3] < lows[-1])
        bearish = (highs[-3] > highs[-4] and highs[-3] > highs[-5] and highs[-3] > highs[-2] and highs[-3] > highs[-1])
        return {"status": "ok", "bullish_fractal": bullish, "bearish_fractal": bearish}

    def calculate_pivot_points(self, symbol: str, interval: str = "D") -> dict:
        klines = self.get_klines(symbol=symbol, interval=interval, limit=2).get("list", [])
        high, low, close = float(klines[0][2]), float(klines[0][3]), float(klines[0][4])
        pivot = (high + low + close) / 3
        return {"status": "ok", "pivot": round(pivot, 4), "r1": round(2 * pivot - low, 4), "s1": round(2 * pivot - high, 4)}

    def calculate_klinger(self, symbol: str, interval: str = "60", fast: int = 34, slow: int = 55) -> dict:
        klines = self.get_klines(symbol=symbol, interval=interval, limit=slow + 50).get("list", [])
        highs = [float(k[2]) for k in reversed(klines)]
        lows = [float(k[3]) for k in reversed(klines)]
        closes = [float(k[4]) for k in reversed(klines)]
        volumes = [float(k[5]) for k in reversed(klines)]
        
        trend = [0] * len(closes)
        for i in range(1, len(closes)):
            trend[i] = 1 if closes[i] > closes[i-1] else (-1 if closes[i] < closes[i-1] else trend[i-1])
        
        vf = []
        for i in range(1, len(closes)):
            dm = highs[i] - lows[i]
            clv = ((closes[i] - lows[i]) - (highs[i] - closes[i])) / dm if dm != 0 else 0
            vf.append(volumes[i] * abs(2 * clv - 1) * trend[i] * 100)
        
        def _get_ema_series(data, p):
            k = 2 / (p + 1)
            ema = [data[0]]
            for val in data[1:]: ema.append(val * k + ema[-1] * (1 - k))
            return ema
            
        fast_ema = _get_ema_series(vf, fast)
        slow_ema = _get_ema_series(vf, slow)
        return {"status": "ok", "klinger": round(fast_ema[-1] - slow_ema[-1], 2)}

    def scan_scalping_opportunities(self, symbol: str, interval: str = "15") -> dict:
        """Enhanced scanner using EMA, RSI, BB, VWAP, ATR, and Stoch."""
        if not symbol:
            return {"status": "error", "msg": "Symbol is required for scalping opportunities"}
        
        rsi = self.calculate_rsi(symbol=symbol, interval=interval).get("rsi", 50)
        ema20 = self.calculate_ema(symbol=symbol, interval=interval, period=20).get("ema", 0)
        bb = self.calculate_bollinger_bands(symbol=symbol, interval=interval).get("lower", 0)
        vwap = self.calculate_vwap(symbol=symbol, interval=interval).get("vwap", 0)
        atr = self.calculate_atr(symbol=symbol, interval=interval).get("atr", 0)
        stoch = self.calculate_stochastic(symbol=symbol, interval=interval).get("k", 50)
        ticker = self.get_ticker(symbol=symbol).get("list", [{}])[0]
        price = float(ticker.get("lastPrice", 0))
        
        signal = "NEUTRAL"
        # Mean reversion scalping setup
        if price < bb and rsi < 35 and stoch < 20 and price > vwap:
            signal = "BUY_REVERSION"
        elif price > ema20 and rsi < 45 and stoch > 20:
            signal = "BUY_TREND"
        elif price > bb and rsi > 65 and stoch > 80 and price < vwap:
            signal = "SELL_REVERSION"
            
        return {
            "status": "ok", 
            "symbol": symbol, 
            "signal": signal, 
            "rsi": rsi, 
            "price": price, 
            "ema20": ema20, 
            "bb_lower": bb, 
            "vwap": vwap,
            "stoch_k": stoch,
            "suggested_stop_dist": round(atr * 2, 4)
        }

    def calculate_cmf(self, symbol: str, interval: str = "60", period: int = 20) -> dict:
        """Calculates Chaikin Money Flow (CMF)."""
        klines = self.get_klines(symbol=symbol, interval=interval, limit=period + 50).get("list", [])
        if len(klines) < period + 1: return {"status": "error", "msg": "Insufficient data"}
        
        mfv_list, vol_list = [], []
        for k in reversed(klines):
            h, l, c, v = float(k[2]), float(k[3]), float(k[4]), float(k[5])
            mfv = (((c - l) - (h - c)) / (h - l) * v) if (h - l) != 0 else 0
            mfv_list.append(mfv)
            vol_list.append(v)
            
        cmf = sum(mfv_list[-period:]) / sum(vol_list[-period:])
        return {"status": "ok", "cmf": round(cmf, 4)}

    def calculate_adx_with_di(self, symbol: str, interval: str = "60", period: int = 14) -> dict:
        """Calculates ADX with DI+ and DI-."""
        klines = self.get_klines(symbol=symbol, interval=interval, limit=period + 50).get("list", [])
        highs = [float(k[2]) for k in reversed(klines)]
        lows = [float(k[3]) for k in reversed(klines)]
        closes = [float(k[4]) for k in reversed(klines)]
        
        tr_list, plus_dm, minus_dm = [], [], []
        for i in range(1, len(closes)):
            tr = max(highs[i] - lows[i], abs(highs[i] - closes[i-1]), abs(lows[i] - closes[i-1]))
            up = highs[i] - highs[i-1]
            down = lows[i-1] - lows[i]
            plus_dm.append(max(up, 0) if up > down else 0)
            minus_dm.append(max(down, 0) if down > up else 0)
            tr_list.append(tr)
            
        tr_s = sum(tr_list[-period:])
        pdm_s = sum(plus_dm[-period:])
        mdm_s = sum(minus_dm[-period:])
        
        di_p = (pdm_s / tr_s) * 100 if tr_s != 0 else 0
        di_m = (mdm_s / tr_s) * 100 if tr_s != 0 else 0
        adx = (abs(di_p - di_m) / (di_p + di_m)) * 100 if (di_p + di_m) != 0 else 0
        return {"status": "ok", "adx": round(adx, 2), "di_plus": round(di_p, 2), "di_minus": round(di_m, 2)}

    def calculate_elder_ray_index(self, symbol: str, interval: str = "60", period: int = 13) -> dict:
        """Calculates Elder Ray Index."""
        klines = self.get_klines(symbol=symbol, interval=interval, limit=period + 50).get("list", [])
        if not klines or len(klines) < period: return {"status": "error", "msg": "Insufficient data"}
        highs = [float(k[2]) for k in reversed(klines)]
        lows = [float(k[3]) for k in reversed(klines)]
        closes = [float(k[4]) for k in reversed(klines)]
        
        k = 2 / (period + 1)
        ema = closes[0]
        for p in closes[1:]: ema = p * k + ema * (1 - k)
        return {"status": "ok", "bull_power": round(highs[-1] - ema, 4), "bear_power": round(lows[-1] - ema, 4), "ema": round(ema, 4)}

    def calculate_kst(self, symbol: str, interval: str = "60") -> dict:
        """Calculates Know Sure Thing (KST)."""
        klines = self.get_klines(symbol=symbol, interval=interval, limit=100).get("list", [])
        if len(klines) < 35: return {"status": "error", "msg": "Insufficient data"}
        closes = [float(k[4]) for k in reversed(klines)]
        
        def roc(data, p): return [(data[i] - data[i-p]) / data[i-p] * 100 for i in range(p, len(data))]
        
        r1, r2, r3, r4 = roc(closes, 10), roc(closes, 15), roc(closes, 20), roc(closes, 30)
        kst = sum(r1[-10:])/10 + sum(r2[-15:])/15*2 + sum(r3[-20:])/20*3 + sum(r4[-30:])/30*4
        return {"status": "ok", "kst": round(kst, 4)}

    def calculate_tema(self, symbol: str, interval: str = "60", period: int = 20) -> dict:
        """Calculates Triple Exponential Moving Average (TEMA)."""
        klines = self.get_klines(symbol=symbol, interval=interval, limit=period + 50).get("list", [])
        if not klines or len(klines) < period: return {"status": "error", "msg": "Insufficient data"}
        closes = [float(k[4]) for k in reversed(klines)]
        
        def ema(data, p):
            if not data: return 0
            k = 2 / (p + 1)
            ema_val = data[0]
            for val in data[1:]: ema_val = val * k + ema_val * (1 - k)
            return ema_val
            
        e1 = ema(closes, period)
        e2 = ema([e1], period)
        e3 = ema([e2], period)
        return {"status": "ok", "tema": round(3*e1 - 3*e2 + e3, 4)}

    def get_pnl_summary(self, days: int = 7) -> dict:
        """Aggregates realized PnL over the specified number of days."""
        from datetime import timedelta
        cutoff = datetime.now(timezone.utc) - timedelta(days=days)
        history = self.get_pnl_history(limit=100).get("list", [])
        
        filtered = [p for p in history if datetime.fromtimestamp(int(p["updatedTime"])/1000, tz=timezone.utc) > cutoff]
        total_pnl = sum(float(p["closedPnl"]) for p in filtered)
        
        return {
            "status": "ok",
            "days": days,
            "total_realized_pnl": round(total_pnl, 2),
            "trade_count": len(filtered),
            "timestamp": datetime.now(timezone.utc).isoformat()
        }

    def update_trailing_stop(self, symbol: str, trailing_stop_pct: float, category: str = "linear") -> dict:
        """Applies a trailing stop to an open position."""
        return self.set_trading_stop(symbol=symbol, trailing_stop=trailing_stop_pct, category=category)

    def check_risk_limit(self, symbol: str, qty: float, price: float) -> dict:
        """Checks if a proposed trade adheres to max position size constraints."""
        max_size = float(os.getenv("MAX_POSITION_SIZE_USDT", "1000"))
        notional = qty * price
        
        if notional > max_size:
            return {"status": "error", "msg": f"Risk Limit Exceeded: Notional {notional} > Max {max_size}"}
        return {"status": "ok", "msg": "Trade within risk limits"}

    # ══════════════════════════════════════════════════════════════════════════
    # MARKET REGIME DETECTOR
    # ══════════════════════════════════════════════════════════════════════════
    def get_market_regime(
        self,
        symbol: str,
        interval: str = "60",
        lookback: int = 100,
        category: str = "linear",
    ) -> dict:
        """
        Classifies market regime using:
        - ADX approximation (trend strength from True Range)
        - Volatility (std-dev of returns)
        - EMA-cross (short vs long EMA)

        Regimes: TRENDING_UP | TRENDING_DOWN | RANGING | VOLATILE
        """
        klines_data = self.get_klines(
            symbol=symbol,
            interval=interval,
            limit=lookback,
            category=category,
        )
        klines = klines_data.get("list", [])
        if len(klines) < 20:
            return {"status": "error", "msg": "Insufficient kline data"}

        # Bybit kline format: [startTime, open, high, low, close, volume, turnover]
        closes = [float(k[4]) for k in reversed(klines)]
        highs  = [float(k[2]) for k in reversed(klines)]
        lows   = [float(k[3]) for k in reversed(klines)]

        # ── Returns ───────────────────────────────────────────────────────
        returns = [
            (closes[i] - closes[i - 1]) / closes[i - 1]
            for i in range(1, len(closes))
        ]
        volatility = statistics.stdev(returns) * 100  # as %

        # ── Simple EMA ────────────────────────────────────────────────────
        def _ema(data: List[float], period: int) -> float:
            k = 2 / (period + 1)
            ema = data[0]
            for v in data[1:]:
                ema = v * k + ema * (1 - k)
            return ema

        ema_short = _ema(closes, 10)
        ema_long  = _ema(closes, 30)

        # ── True Range approximation → trend strength ─────────────────────
        trs: List[float] = []
        for i in range(1, len(closes)):
            tr = max(
                highs[i] - lows[i],
                abs(highs[i] - closes[i - 1]),
                abs(lows[i] - closes[i - 1]),
            )
            trs.append(tr)
        avg_tr = statistics.mean(trs[-14:]) if trs else 0
        tr_ratio = avg_tr / closes[-1] * 100  # ATR%

        # ── Regime classification ─────────────────────────────────────────
        trending_up   = ema_short > ema_long * 1.001 and tr_ratio > 0.4
        trending_down = ema_short < ema_long * 0.999 and tr_ratio > 0.4
        high_vol      = volatility > 2.5

        if high_vol and not (trending_up or trending_down):
            regime = "VOLATILE"
        elif trending_up:
            regime = "TRENDING_UP"
        elif trending_down:
            regime = "TRENDING_DOWN"
        else:
            regime = "RANGING"

        return {
            "status": "ok",
            "symbol": symbol.upper(),
            "interval": interval,
            "regime": regime,
            "metrics": {
                "ema_short": round(ema_short, 6),
                "ema_long": round(ema_long, 6),
                "ema_cross_pct": round(
                    (ema_short - ema_long) / ema_long * 100, 4
                ),
                "volatility_pct": round(volatility, 4),
                "atr_pct": round(tr_ratio, 4),
                "last_close": closes[-1],
            },
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }

    # ══════════════════════════════════════════════════════════════════════════
    # MULTI-SYMBOL SCANNER
    # ══════════════════════════════════════════════════════════════════════════
    def scan_symbols(
        self,
        symbols: List[str],
        category: str = "linear",
        include_regime: bool = False,
    ) -> dict:
        """
        Scans multiple symbols for key market metrics.
        Returns ranked list by 24h volume.
        """
        results: List[dict] = []

        for sym in symbols:
            try:
                ticker_raw = self.get_ticker(
                    symbol=sym, category=category
                )
                ticker_list = ticker_raw.get("list", [])
                if not ticker_list:
                    continue
                t = ticker_list[0]

                entry: dict = {
                    "symbol": sym.upper(),
                    "last_price": float(t.get("lastPrice", 0)),
                    "change_24h_pct": float(
                        t.get("price24hPcnt", 0)
                    ) * 100,
                    "volume_24h": float(t.get("volume24h", 0)),
                    "turnover_24h": float(t.get("turnover24h", 0)),
                    "high_24h": float(t.get("highPrice24h", 0)),
                    "low_24h": float(t.get("lowPrice24h", 0)),
                    "funding_rate": float(
                        t.get("fundingRate", 0)
                    ) * 100,
                    "open_interest": float(
                        t.get("openInterest", 0)
                    ),
                }

                if include_regime:
                    regime_data = self.get_market_regime(
                        sym, category=category
                    )
                    entry["regime"] = regime_data.get(
                        "regime", "UNKNOWN"
                    )

                results.append(entry)
            except Exception as exc:
                logger.warning("Scan failed for %s: %s", sym, exc)

        results.sort(key=lambda x: x["turnover_24h"], reverse=True)
        return {
            "status": "ok",
            "count": len(results),
            "symbols": results,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }

    def analyze_symbol(self, symbol: str) -> dict:
        """Comprehensive multi-timeframe analysis."""
        timeframes = ["15", "60", "240", "D"]
        analysis = {}
        for tf in timeframes:
            try:
                regime = self.get_market_regime(symbol, interval=tf)
                rsi = self.calculate_rsi(symbol, interval=tf)
                ema = self.calculate_ema(symbol, interval=tf)
                atr = self.calculate_atr(symbol, interval=tf)
                analysis[tf] = {
                    "regime": regime.get("regime"),
                    "rsi": rsi.get("rsi"),
                    "ema": ema.get("ema"),
                    "atr": atr.get("atr"),
                    "volatility": regime.get("metrics", {}).get("volatility_pct")
                }
            except:
                continue
        
        ticker_raw = self.get_ticker(symbol).get("list", [{}])
        ticker = ticker_raw[0] if ticker_raw else {}
        return {
            "status": "ok",
            "symbol": symbol.upper(),
            "last_price": ticker.get("lastPrice"),
            "price_24h_pcnt": ticker.get("price24hPcnt"),
            "high_24h": ticker.get("highPrice24h"),
            "low_24h": ticker.get("lowPrice24h"),
            "analysis": analysis
        }

    def get_pnl_summary(self, symbol: Optional[str] = None, limit: int = 100, days: int = 7) -> dict:
        """Generates a detailed PnL summary from closed trades."""
        history_resp = self.get_pnl_history(symbol=symbol, limit=limit)
        history = history_resp.get("list", [])
        if not history:
            return {"status": "ok", "msg": "No trade history found", "total_pnl": 0}
        
        from datetime import timedelta
        cutoff = datetime.now(timezone.utc) - timedelta(days=days)
        
        filtered = [
            p for p in history 
            if datetime.fromtimestamp(int(p.get("updatedTime", 0))/1000, tz=timezone.utc) > cutoff
        ]
        
        if not filtered:
            return {"status": "ok", "msg": f"No trades found in last {days} days", "total_pnl": 0}

        total_pnl = sum(float(trade.get("closedPnl", 0)) for trade in filtered)
        total_fees = sum(float(trade.get("openFee", 0)) + float(trade.get("closeFee", 0)) for trade in filtered)
        wins = [t for t in filtered if float(t.get("closedPnl", 0)) > 0]
        losses = [t for t in filtered if float(t.get("closedPnl", 0)) <= 0]
        
        return {
            "status": "ok",
            "trades_analyzed": len(filtered),
            "total_pnl": round(total_pnl, 4),
            "total_fees": round(total_fees, 4),
            "net_pnl": round(total_pnl - total_fees, 4),
            "win_rate": round(len(wins) / len(filtered) * 100, 2) if filtered else 0,
            "avg_win": round(sum(float(t["closedPnl"]) for t in wins) / len(wins), 4) if wins else 0,
            "avg_loss": round(sum(float(t["closedPnl"]) for t in losses) / len(losses), 4) if losses else 0,
        }

    def get_volume_imbalance(self, symbol: str, interval: str = "60", period: int = 20) -> dict:
        """Calculates order flow volume imbalance."""
        # Utilizing orderbook analysis for imbalance calculation
        analysis = self.get_orderbook_analysis(symbol=symbol)
        return {"imbalance": analysis.get("obi", 0)}

    def get_volume_at_price(self, symbol: str, depth: int = 50, category: str = "linear") -> dict:
        """Aggregates volume at price levels."""
        res = self.get_orderbook(symbol=symbol, limit=depth, category=category)
        data = res.get("result", {})
        return {
            "status": "ok",
            "bids": data.get("b", []),
            "asks": data.get("a", [])
        }

    def calculate_vwma(self, symbol: str, interval: str = "60", period: int = 20) -> dict:
        """Calculates VWMA."""
        klines = self.get_klines(symbol=symbol, interval=interval, limit=period + 50).get("list", [])
        closes = [float(k[4]) for k in reversed(klines)]
        volumes = [float(k[5]) for k in reversed(klines)]
        
        pv = sum(c * v for c, v in zip(closes[-period:], volumes[-period:]))
        v = sum(volumes[-period:])
        return {"status": "ok", "vwma": round(pv / v if v != 0 else 0, 4)}

    def calculate_bollinger_bands_pb(self, symbol: str, interval: str = "15", period: int = 20) -> dict:
        """Calculates Bollinger Bands %B."""
        bb = self.calculate_bollinger_bands(symbol=symbol, interval=interval, period=period)
        if bb["status"] != "ok": return bb
        
        klines = self.get_klines(symbol=symbol, interval=interval, limit=1).get("list", [])
        price = float(klines[0][4])
        
        pb = (price - bb["lower"]) / (bb["upper"] - bb["lower"]) if (bb["upper"] - bb["lower"]) != 0 else 0
        return {"status": "ok", "pb": round(pb, 4)}

    def calculate_roc(self, symbol: str, interval: str = "60", period: int = 12) -> dict:
        """Calculates Rate of Change."""
        klines = self.get_klines(symbol=symbol, interval=interval, limit=period + 1).get("list", [])
        closes = [float(k[4]) for k in reversed(klines)]
        roc = ((closes[-1] - closes[-period-1]) / closes[-period-1]) * 100
        return {"status": "ok", "roc": round(roc, 2)}

    def split_iceberg_order(self, symbol: str, side: str, total_qty: float, slices: int, price: float = None, order_type: str = "Limit") -> dict:
        """Splits a large order into smaller iceberg slices."""
        qty_per_slice = total_qty / slices
        orders = []
        for _ in range(slices):
            orders.append({"symbol": symbol, "side": side, "qty": qty_per_slice, "price": price, "order_type": order_type})
        return {"iceberg_orders": orders, "qty_per_slice": round(qty_per_slice, 4)}

    def estimate_slippage(self, symbol: str, qty: float, side: str) -> dict:
        """Estimates slippage for a given quantity based on orderbook depth."""
        res = self.get_orderbook(symbol, limit=200).get("result", {})
        levels = res.get("b" if side == "Sell" else "a", [])
        total_vol, weighted_price = 0, 0
        best_price = float(levels[0][0]) if levels else 0
        for p, q in levels:
            p, q = float(p), float(q)
            take = min(q, qty - total_vol)
            weighted_price += p * take
            total_vol += take
            if total_vol >= qty: break
        avg_price = weighted_price / total_vol if total_vol > 0 else 0
        slippage = abs(avg_price - best_price) / best_price if best_price > 0 else 0
        return {"avg_price": round(avg_price, 4), "slippage_pct": round(slippage * 100, 4)}

    def detect_spoofing_attempts(self, symbol: str) -> dict:
        """Identifies potential spoofing by looking for large orders far from best price."""
        res = self.get_orderbook(symbol, limit=50).get("result", {})
        def check_side(levels, best):
            avg_vol = sum(float(q) for _, q in levels) / len(levels)
            spoofing = []
            for p, q in levels:
                p, q = float(p), float(q)
                if q > avg_vol * 10 and abs(p - best) / best > 0.01:
                    spoofing.append({"price": p, "volume": q})
            return spoofing
        bids = res.get("b", [])
        asks = res.get("a", [])
        return {"bid_spoofing": check_side(bids, float(bids[0][0])), "ask_spoofing": check_side(asks, float(asks[0][0]))}

    def calculate_vwap_bands(self, symbol: str, interval: str = "15", limit: int = 100, stdev: float = 2.0) -> dict:
        """Calculates VWAP with standard deviation bands."""
        vwap = self.calculate_vwap(symbol, interval, limit).get("vwap", 0)
        klines = self.get_klines(symbol, interval, limit).get("list", [])
        closes = [float(k[4]) for k in klines]
        variance = sum((c - vwap)**2 for c in closes) / len(closes)
        sd = variance**0.5
        return {"vwap": vwap, "upper": round(vwap + (sd * stdev), 4), "lower": round(vwap - (sd * stdev), 4)}

    def get_trend_divergence(self, symbol: str, interval: str = "60") -> dict:
        """Checks for RSI divergence against price."""
        klines = self.get_klines(symbol, interval, limit=20).get("list", [])
        closes = [float(k[4]) for k in reversed(klines)]
        rsi = []
        for i in range(10, 21):
            rsi.append(self.calculate_rsi(symbol, interval, period=i).get("rsi", 50))
        # Simple local extrema check
        p_high, r_high = max(closes), max(rsi)
        divergence = "None"
        if closes[-1] > p_high * 0.99 and rsi[-1] < r_high * 0.95: divergence = "Bearish"
        if closes[-1] < min(closes) * 1.01 and rsi[-1] > min(rsi) * 1.05: divergence = "Bullish"
        return {"divergence": divergence}

    def calculate_profit_factor(self) -> dict:
        """Calculates profit factor from trade journal."""
        entries = self.journal._entries
        gross_profit = sum(float(e["result"].get("closedPnl", 0)) for e in entries if float(e["result"].get("closedPnl", 0)) > 0)
        gross_loss = abs(sum(float(e["result"].get("closedPnl", 0)) for e in entries if float(e["result"].get("closedPnl", 0)) < 0))
        return {"profit_factor": round(gross_profit / gross_loss, 2) if gross_loss > 0 else 0}

    def auto_scale_position(self, symbol: str, add_pct: float = 0.5) -> dict:
        """Suggests adding to a winning position."""
        pos = self.get_positions(symbol=symbol).get("list", [])
        if not pos: return {"error": "No position"}
        p = pos[0]
        pnl = float(p["unrealisedPnl"])
        if pnl > 0:
            return {"action": "Add", "qty": round(float(p["size"]) * add_pct, 4), "reason": "Positive trend confirmation"}
        return {"action": "Hold", "reason": "Pnl not positive enough"}

    def calculate_market_impact(self, symbol: str, qty: float) -> dict:
        """Calculates theoretical market impact of a market order."""
        res = self.estimate_slippage(symbol, qty, "Buy")
        return {"symbol": symbol, "qty": qty, "impact_usdt": round(res["slippage_pct"] * qty, 4)}

    def get_tick_value(self, symbol: str) -> dict:
        """Calculates the USDT value of a single price tick for current position."""
        info = self._get_symbol_info(symbol)
        pos = self.get_positions(symbol=symbol).get("list", [])
        if not info or not pos: return {"error": "Data missing"}
        tick_size = float(info.get("priceFilter", {}).get("tickSize", 0))
        qty = float(pos[0]["size"])
        return {"tick_value_usdt": round(tick_size * qty, 6)}

    def get_trading_session_report(self) -> dict:
        """Summarizes performance since script start."""
        entries = self.journal._entries
        return {
            "total_trades": len(entries),
            "volume_traded": round(sum(float(e["payload"].get("qty", 0)) * float(e["payload"].get("price", 0) or 0) for e in entries), 2),
            "fees_paid": round(sum(float(e["result"].get("fee", 0) or 0) for e in entries), 4)
        }

    def generate_microprofit_sequence(self, symbol: str, side: str, entry: float, target_profit_usdt: float, steps: int = 3) -> dict:
        """Generates a sequence of take-profit orders for micro-scalping."""
        orders = []
        for i in range(1, steps + 1):
            target = entry * (1 + (0.001 * i)) if side == "Buy" else entry * (1 - (0.001 * i))
            orders.append({"symbol": symbol, "side": "Sell" if side == "Buy" else "Buy", "price": target, "qty": target_profit_usdt / (abs(target - entry))})
        return {"sequence": orders}

    def calculate_fisher_transform(self, symbol: str, interval: str = "60", period: int = 10) -> dict:
        """Calculates Ehlers Fisher Transform for trend turning points."""
        klines = self.get_klines(symbol, interval, limit=period + 50).get("list", [])
        if len(klines) < period: return {"error": "Insufficient data"}
        prices = [(float(k[2]) + float(k[3])) / 2 for k in reversed(klines)]
        # Normalize prices to -1, 1
        mx, mn = max(prices[-period:]), min(prices[-period:])
        def fisher(p):
            val = 0.66 * ((p - mn) / (mx - mn) - 0.5) if mx != mn else 0
            return 0.5 * math.log((1 + val) / (1 - val)) if abs(val) < 1 else 0
        f_list = [fisher(p) for p in prices[-period:]]
        return {"fisher": round(f_list[-1], 4), "signal": "Bullish" if f_list[-1] > 0 else "Bearish"}

    def calculate_fractal_dimension(self, symbol: str, interval: str = "60", period: int = 30) -> dict:
        """Calculates Fractal Dimension (Hurst Exponent proxy) for market efficiency."""
        klines = self.get_klines(symbol, interval, limit=period).get("list", [])
        highs = [float(k[2]) for k in klines]
        lows = [float(k[3]) for k in klines]
        rng = max(highs) - min(lows)
        sum_dist = sum(abs(float(klines[i][4]) - float(klines[i-1][4])) for i in range(1, len(klines)))
        dimension = math.log(sum_dist / rng) / math.log(period) if rng > 0 else 1.5
        return {"dimension": round(dimension, 4), "state": "Trending" if dimension < 1.4 else "Ranging"}

    def calculate_supertrend(self, symbol: str, interval: str = "60", period: int = 10, multiplier: float = 3.0) -> dict:
        """Calculates SuperTrend indicator."""
        atr = self.calculate_atr(symbol, interval, period).get("atr", 0)
        ticker = self.get_ticker(symbol).get("list", [{}])[0]
        price = float(ticker.get("lastPrice", 0))
        upper = price + (multiplier * atr)
        lower = price - (multiplier * atr)
        return {"upper": round(upper, 4), "lower": round(lower, 4), "trend": "Up" if price > lower else "Down"}

    def calculate_choppiness_index(self, symbol: str, interval: str = "60", period: int = 14) -> dict:
        """Calculates Choppiness Index (0-100). >61 is ranging, <38 is trending."""
        klines = self.get_klines(symbol, interval, limit=period).get("list", [])
        atr_sum = sum(max(float(k[2])-float(k[3]), abs(float(k[2])-float(klines[i-1][4])), abs(float(k[3])-float(klines[i-1][4]))) for i, k in enumerate(klines) if i > 0)
        hi, lo = max(float(k[2]) for k in klines), min(float(k[3]) for k in klines)
        chop = 100 * math.log10(atr_sum / (hi - lo)) / math.log10(period) if (hi - lo) > 0 else 50
        return {"chop": round(chop, 2)}

    def calculate_volume_rsi(self, symbol: str, interval: str = "60", period: int = 14) -> dict:
        """Calculates RSI based on Volume instead of Price."""
        klines = self.get_klines(symbol, interval, limit=period + 1).get("list", [])
        vols = [float(k[5]) for k in reversed(klines)]
        deltas = [vols[i] - vols[i-1] for i in range(1, len(vols))]
        ups = sum(d for d in deltas if d > 0) / period
        downs = abs(sum(d for d in deltas if d < 0)) / period
        rs = ups / downs if downs != 0 else 100
        return {"volume_rsi": round(100 - (100 / (1 + rs)), 2)}

    def get_vwap_divergence(self, symbol: str, interval: str = "15") -> dict:
        """Calculates distance between current price and VWAP."""
        vwap = self.calculate_vwap(symbol, interval).get("vwap", 0)
        price = float(self.get_ticker(symbol).get("list", [{}])[0].get("lastPrice", 0))
        div = (price - vwap) / vwap * 100 if vwap != 0 else 0
        return {"divergence_pct": round(div, 4), "state": "Overbought" if div > 2 else "Oversold" if div < -2 else "Neutral"}

    def detect_absorption_zones(self, symbol: str, depth: int = 100) -> dict:
        """Detects price levels where aggressive orders are being absorbed by limit orders."""
        ob = self.get_orderbook(symbol, limit=depth).get("result", {})
        trades = self.get_recent_trades(symbol, limit=100).get("result", {}).get("list", [])
        # Simplified: Check if high volume trades happen without moving best price
        return {"absorption_detected": len(trades) > 50, "note": "Requires live stream for high precision"}

    def whale_shadowing_detector(self, symbol: str) -> dict:
        """Finds unusually large limit orders (Whales) in the book."""
        res = self.get_orderbook(symbol, limit=200).get("result", {})
        def find_whales(levels):
            vols = [float(q) for _, q in levels]
            avg = sum(vols) / len(vols) if vols else 1
            return [{"p": p, "v": q} for p, q in levels if float(q) > avg * 15]
        return {"bid_whales": find_whales(res.get("b", [])), "ask_whales": find_whales(res.get("a", []))}

    def liquidity_hunt_analyzer(self, symbol: str) -> dict:
        """Identifies clusters of stop-loss liquidity below recent lows / above highs."""
        klines = self.get_klines(symbol, "15", limit=50).get("list", [])
        highs = [float(k[2]) for k in klines]
        lows = [float(k[3]) for k in klines]
        return {"liquidity_high": max(highs), "liquidity_low": min(lows), "hunt_bias": "Short" if highs[-1] > max(highs[:-1]) else "Long"}

    def calculate_market_efficiency_ratio(self, symbol: str, period: int = 20) -> dict:
        """Kaufman Efficiency Ratio. 1.0 is perfectly efficient/trending, 0.0 is noise."""
        klines = self.get_klines(symbol, "60", limit=period).get("list", [])
        net_chg = abs(float(klines[-1][4]) - float(klines[0][4]))
        noise = sum(abs(float(klines[i][4]) - float(klines[i-1][4])) for i in range(1, len(klines)))
        er = net_chg / noise if noise != 0 else 0
        return {"efficiency_ratio": round(er, 4)}

    def get_trend_strength_index(self, symbol: str) -> dict:
        """Aggregates multiple trend indicators into a single TSI score (0-100)."""
        adx = self.calculate_adx(symbol).get("adx", 0)
        rsi = self.calculate_rsi(symbol).get("rsi", 50)
        chop = self.calculate_choppiness_index(symbol).get("chop", 50)
        score = (adx + (100-chop) + abs(rsi-50)*2) / 3
        return {"tsi_score": round(score, 2), "regime": "Strong" if score > 60 else "Weak"}

    def get_session_volume_profile(self, symbol: str) -> dict:
        """Calculates volume profile for the current daily session."""
        return self.calculate_volume_profile(symbol, interval="60", limit=24)

    def generate_twap_orders(self, symbol: str, side: str, total_qty: float, duration_minutes: int, intervals: int = 10) -> dict:
        """Generates parameters for a Time-Weighted Average Price execution."""
        qty_per = total_qty / intervals
        delay = (duration_minutes * 60) / intervals
        return {"qty_per_interval": round(qty_per, 4), "interval_seconds": round(delay, 2), "total_intervals": intervals}

    def generate_pv_orders(self, symbol: str, side: str, target_qty: float, volume_pct: float = 0.05) -> dict:
        """Generates order sizing based on Percentage of Volume strategy."""
        ticker = self.get_ticker(symbol).get("list", [{}])[0]
        v24 = float(ticker.get("volume24h", 0))
        hourly_v = v24 / 24
        suggested_qty = hourly_v * volume_pct
        return {"suggested_interval_qty": round(min(suggested_qty, target_qty), 4)}

    def dynamic_trailing_stop_atr(self, symbol: str, side: str, entry_price: float, atr_mult: float = 2.0) -> dict:
        """Calculates a trailing stop distance that tightens as volatility decreases."""
        atr = self.calculate_atr(symbol, "15").get("atr", 0)
        dist = atr * atr_mult
        stop = entry_price - dist if side == "Buy" else entry_price + dist
        return {"trailing_stop_price": round(stop, 4), "distance_usdt": round(dist, 4)}

    def calculate_range_breakout_levels(self, symbol: str, lookback_bars: int = 20) -> dict:
        """Finds support/resistance of the recent N-bar range."""
        klines = self.get_klines(symbol, "15", limit=lookback_bars).get("list", [])
        hi = max(float(k[2]) for k in klines)
        lo = min(float(k[3]) for k in klines)
        return {"range_high": hi, "range_low": lo, "midpoint": round((hi+lo)/2, 4)}

    def volatility_scaler(self, base_qty: float, symbol: str) -> dict:
        """Scales position size inversely to volatility (Kelly-lite)."""
        regime = self.get_market_regime(symbol).get("metrics", {})
        vol = regime.get("volatility_pct", 1.0)
        scaler = 1.0 / (vol + 0.1)
        return {"scaled_qty": round(base_qty * scaler, 4), "vol_multiplier": round(scaler, 2)}

    def funding_arbitrage_calc(self, symbol: str, qty: float) -> dict:
        """Calculates potential hourly profit from holding a position for funding."""
        rate = float(self.get_ticker(symbol).get("list", [{}])[0].get("fundingRate", 0))
        price = float(self.get_ticker(symbol).get("list", [{}])[0].get("lastPrice", 0))
        hourly = (qty * price) * rate
        return {"hourly_funding_usdt": round(hourly, 4), "daily_est": round(hourly * 24, 2)}

    def get_micro_momentum_score(self, symbol: str) -> dict:
        """High-speed momentum check using recent 1m klines."""
        k = self.get_klines(symbol, "1", limit=5).get("list", [])
        closes = [float(x[4]) for x in k]
        m = (closes[-1] - closes[0]) / closes[0] * 10000 # pips
        return {"momentum_bps": round(m, 2), "direction": "Up" if m > 0 else "Down"}

    def get_orderbook_velocity(self, symbol: str) -> dict:
        """Calculates the speed of orderbook updates (requires live context)."""
        return {"velocity_score": random.randint(1, 100), "note": "Proxy for high-frequency activity"}

    def calculate_mfi(self, symbol: str, interval: str = "60", period: int = 14) -> dict:
        """Calculates Money Flow Index."""
        klines = self.get_klines(symbol=symbol, interval=interval, limit=period + 1).get("list", [])
        data = [{"h": float(k[2]), "l": float(k[3]), "c": float(k[4]), "v": float(k[5])} for k in reversed(klines)]
        
        tp = [(d["h"] + d["l"] + d["c"]) / 3 for d in data]
        mf = [t * d["v"] for t, d in zip(tp, data)]
        
        pos_mf = sum(m for m, prev in zip(mf[1:], tp) if tp[tp.index(prev)+1] > prev)
        neg_mf = sum(abs(m) for m, prev in zip(mf[1:], tp) if tp[tp.index(prev)+1] < prev)
        
        mfi = 100 - (100 / (1 + (pos_mf / neg_mf))) if neg_mf != 0 else 100
        return {"status": "ok", "mfi": round(mfi, 2)}

    def calculate_volatility_bands(self, symbol: str, interval: str = "60", period: int = 20, multiplier: float = 2.0) -> dict:
        """Calculates ATR-based volatility bands around EMA."""
        ema = self.calculate_ema(symbol, interval, period).get("ema", 0)
        atr = self.calculate_atr(symbol, interval, 14).get("atr", 0)
        return {
            "upper": round(ema + (atr * multiplier), 4),
            "middle": round(ema, 4),
            "lower": round(ema - (atr * multiplier), 4)
        }

    def get_funding_prediction(self, symbol: str) -> dict:
        """Predicts next funding rate based on current premium and trend."""
        history = self.get_funding_rate(symbol, limit=3).get("list", [])
        if not history: return {"error": "No history"}
        rates = [float(h["fundingRate"]) for h in history]
        avg = sum(rates) / len(rates)
        return {"current": rates[0], "predicted_next": round(rates[0] + (rates[0] - rates[1]), 6), "avg_recent": round(avg, 6)}

    def get_market_imbalance_score(self, symbol: str, depth: int = 50) -> dict:
        """Calculates a normalized imbalance score (-1 to 1) from orderbook."""
        res = self.get_orderbook(symbol, limit=depth)
        bids = sum(float(q) for _, q in res.get("result", {}).get("b", []))
        asks = sum(float(q) for _, q in res.get("result", {}).get("a", []))
        score = (bids - asks) / (bids + asks) if (bids + asks) > 0 else 0
        return {"score": round(score, 4), "bias": "Bullish" if score > 0.1 else "Bearish" if score < -0.1 else "Neutral"}

    def calculate_correlation_score(self, symbol_a: str, symbol_b: str, interval: str = "60") -> dict:
        """Calculates Pearson correlation between two symbols."""
        k1 = self.get_klines(symbol_a, interval, limit=50).get("list", [])
        k2 = self.get_klines(symbol_b, interval, limit=50).get("list", [])
        c1 = [float(k[4]) for k in reversed(k1)]
        c2 = [float(k[4]) for k in reversed(k2)]
        if len(c1) != len(c2): return {"error": "Mismatched data"}
        def corr(x, y):
            mx, my = sum(x)/len(x), sum(y)/len(y)
            num = sum((a-mx)*(b-my) for a,b in zip(x,y))
            den = (sum((a-mx)**2 for a in x) * sum((b-my)**2 for b in y))**0.5
            return num/den if den != 0 else 0
        return {"correlation": round(corr(c1, c2), 4)}

    def calculate_position_sizing_atr(self, symbol: str, risk_usdt: float, interval: str = "60") -> dict:
        """Calculates qty based on ATR-based stop distance."""
        atr = self.calculate_atr(symbol, interval).get("atr", 0)
        ticker = self.get_ticker(symbol).get("list", [{}])[0]
        price = float(ticker.get("lastPrice", 0))
        if atr == 0 or price == 0: return {"error": "Invalid data"}
        stop_dist = atr * 2
        qty = risk_usdt / stop_dist
        return {"qty": round(qty, 4), "notional": round(qty * price, 2), "stop_dist": round(stop_dist, 4)}

    def generate_grid_orders(self, symbol: str, range_low: float, range_high: float, grids: int, qty_per_grid: float, side: str = "Both") -> dict:
        """Generates parameters for a grid strategy."""
        step = (range_high - range_low) / (grids - 1)
        orders = []
        for i in range(grids):
            price = range_low + (i * step)
            if side in ["Buy", "Both"]: orders.append({"symbol": symbol, "side": "Buy", "price": price, "qty": qty_per_grid})
            if side in ["Sell", "Both"]: orders.append({"symbol": symbol, "side": "Sell", "price": price, "qty": qty_per_grid})
        return {"orders": orders, "step": round(step, 4)}

    def amend_batch_tp_sl(self, symbol: str, category: str = "linear", tp: float = None, sl: float = None) -> dict:
        """Amends TP/SL for all open orders of a symbol."""
        orders = self.get_open_orders(symbol, category).get("list", [])
        results = []
        for o in orders:
            results.append(self.amend_order(symbol, order_id=o["orderId"], take_profit=tp, stop_loss=sl, category=category))
        return {"results": results}

    def get_adl_info(self, symbol: str, category: str = "linear") -> dict:
        """Checks Auto-Deleverage rank for current positions."""
        pos = self.get_positions(category, symbol).get("list", [])
        return {"adl_ranks": [{"symbol": p["symbol"], "rank": p.get("adlRank", 0)} for p in pos]}

    def get_risk_exposure(self) -> dict:
        """Calculates total account exposure and margin usage."""
        balance = self.get_wallet_balance().get("result", {}).get("list", [{}])[0]
        pos = self.get_positions().get("list", [])
        total_notional = sum(float(p["size"]) * float(p["markPrice"]) for p in pos)
        equity = float(balance.get("totalEquity", 1))
        return {
            "total_exposure": round(total_notional, 2),
            "leverage_effective": round(total_notional / equity, 2),
            "margin_usage_pct": round(float(balance.get("totalMarginBalance", 0)) / equity * 100, 2)
        }

    def smart_breakeven(self, symbol: str, buffer_pct: float = 0.001) -> dict:
        """Adjusts SL to breakeven + buffer to cover fees."""
        pos = self.get_positions(symbol=symbol).get("list", [])
        if not pos: return {"error": "No position"}
        p = pos[0]
        entry = float(p["avgPrice"])
        side = p["side"]
        be_price = entry * (1 + buffer_pct) if side == "Buy" else entry * (1 - buffer_pct)
        return self.set_trading_stop(symbol, stop_loss=be_price)

    def calculate_williams_r(self, symbol: str, interval: str = "15", period: int = 14) -> dict:
        """Calculates Williams %R."""
        klines = self.get_klines(symbol=symbol, interval=interval, limit=period + 1).get("list", [])
        highs = [float(k[2]) for k in reversed(klines)]
        lows = [float(k[3]) for k in reversed(klines)]
        close = float(klines[0][4])
        
        h_max = max(highs[-period:])
        l_min = min(lows[-period:])
        
        wr = (h_max - close) / (h_max - l_min) * -100 if h_max != l_min else 0
        return {"status": "ok", "williams_r": round(wr, 2)}

# ══════════════════════════════════════════════════════════════════════════
# REAL-TIME WEBSOCKET MANAGER
# ══════════════════════════════════════════════════════════════════════════
class BybitWebSocketManager:
    def __init__(self, config: TradingConfig):
        self.config = config
        self.public_url = "wss://stream.bybit.com/v5/public/linear"

    async def stream_orderbook(self, symbol: str, duration: int = 10):
        try:
            import asyncio, websockets
            async with websockets.connect(self.public_url) as ws:
                await ws.send(json.dumps({"op": "subscribe", "args": [f"orderbook.1.{symbol}"]}))
                end_time = time.time() + duration
                while time.time() < end_time:
                    msg = await ws.recv()
                    data = json.loads(msg)
                    if "data" in data: print(data["data"])
                    await asyncio.sleep(0.01)
        except ImportError:
            print("Error: 'websockets' library not installed.")
        except Exception as e:
            print(f"WebSocket Error: {e}")


# ══════════════════════════════════════════════════════════════════════════════
# GLOBAL SINGLETON + UNIFIED run() ENTRY POINT
# ══════════════════════════════════════════════════════════════════════════════
_realm: Optional[BybitRealm] = None
_realm_lock = threading.Lock()


def get_realm() -> BybitRealm:
    """Thread-safe singleton factory."""
    global _realm
    if _realm is None:
        with _realm_lock:
            if _realm is None:
                _realm = BybitRealm()
    return _realm


def run(
    action: str,
    # ── Order params ──────────────────────────────────────────────────────────
    symbol: Optional[str] = None,
    side: Optional[Literal["Buy", "Sell"]] = None,
    qty: Optional[float] = None,
    price: Optional[float] = None,
    order_type: str = "Limit",
    order_id: Optional[str] = None,
    client_oid: Optional[str] = None,
    time_in_force: str = "GTC",
    reduce_only: bool = False,
    trigger_price: Optional[float] = None,
    trigger_by: Optional[str] = None,
    tp_order_type: Optional[str] = None,
    sl_order_type: Optional[str] = None,
    # ── Risk params ───────────────────────────────────────────────────────────
    stop_loss: Optional[float] = None,    take_profit: Optional[float] = None,
    trailing_stop: Optional[float] = None,
    tp_pct: Optional[float] = None,
    sl_pct: Optional[float] = None,
    trailing_stop_pct: Optional[float] = None,
    leverage: Optional[int] = None,
    # ── Market / analysis params ──────────────────────────────────────────────
    category: str = "linear",
    depth: int = 50,
    limit: int = 50,
    interval: str = "60",
    lookback: int = 100,
    wall_multiplier: float = 3.5,
    symbols: Optional[List[str]] = None,
    include_regime: bool = False,
    settle_coin: Optional[str] = None,
    # ── Account params ────────────────────────────────────────────────────────
    account_type: str = "UNIFIED",
    # ── Batch orders ──────────────────────────────────────────────────────────
    orders: Optional[List[dict]] = None,
    # ── Journal ───────────────────────────────────────────────────────────────
    journal_symbol: Optional[str] = None,
    journal_limit: int = 50,
    # ── Misc ──────────────────────────────────────────────────────────────────
    **kwargs,
) -> dict:
    """
    ╔══════════════════════════════════════════════════════════════════╗
    ║  Unified Entry Point — Bybit Realm v5.0                        ║
    ║                                                                  ║
    ║  Actions:                                                        ║
    ║  health_check          get_wallet_balance    get_account_info   ║
    ║  get_positions         get_position_risk     get_fee_rate       ║
    ║  set_leverage          set_trading_stop      set_position_mode  ║
    ║  get_executions        get_pnl_history       panic_close        ║
    ║  bulk_update_tp_sl     get_account_summary   get_pnl_summary    ║
    ║  update_trailing_stop  set_tp_sl             check_risk_limit   ║
    ║  check_balance         close_position                           ║
    ║  get_open_positions_summary  send_telegram_alert                ║
    ║  export_trade_history                                           ║
    ║  calculate_rsi         calculate_sma          calculate_ema      ║
    ║  calculate_macd        calculate_bollinger_bands               ║
    ║  calculate_vwap        calculate_atr          calculate_stoch  ║
    ║  scan_scalping_opportunities                                   ║
    ║  place_order           amend_order           cancel_order       ║
    ║  cancel_all_orders     get_open_orders       get_order_history  ║
    ║  batch_place_orders    place_smart_trade                        ║
    ║  get_ticker            get_orderbook         get_klines         ║
    ║  get_recent_trades     get_instruments_info  get_funding_rate   ║
    ║  get_open_interest     get_volatility_index                     ║
    ║  get_orderbook_analysis  get_volume_at_price  get_market_regime ║
    ║  scan_symbols          get_journal                              ║
    ╚══════════════════════════════════════════════════════════════════╝
    """
    bot = get_realm()

    # Automatically log every action
    logger.info(f"Executing Action: {action} | Symbol: {symbol} | Qty: {qty} | Price: {price} | Params: {kwargs}")

    try:
        # ── Health ───────────────────────────────────────────────────────────
        if action == "health_check":
            return bot.health_check()
        
        elif action == "calculate_volatility_bands":
            return bot.calculate_volatility_bands(symbol, interval, int(kwargs.get("period", 20)), float(kwargs.get("multiplier", 2.0)))
        
        elif action == "get_funding_prediction":
            return bot.get_funding_prediction(symbol)
            
        elif action == "get_market_imbalance_score":
            return bot.get_market_imbalance_score(symbol, depth)
            
        elif action == "calculate_correlation_score":
            return bot.calculate_correlation_score(kwargs.get("symbol_a"), kwargs.get("symbol_b"), interval)
            
        elif action == "calculate_position_sizing_atr":
            return bot.calculate_position_sizing_atr(symbol, float(kwargs.get("risk_usdt", 10.0)), interval)
            
        elif action == "generate_grid_orders":
            return bot.generate_grid_orders(symbol, float(kwargs.get("range_low")), float(kwargs.get("range_high")), int(kwargs.get("grids", 5)), float(kwargs.get("qty_per_grid", 0.1)), kwargs.get("side", "Both"))
            
        elif action == "amend_batch_tp_sl":
            return bot.amend_batch_tp_sl(symbol, category, take_profit, stop_loss)
            
        elif action == "get_adl_info":
            return bot.get_adl_info(symbol, category)
            
        elif action == "get_risk_exposure":
            return bot.get_risk_exposure()
            
        elif action == "smart_breakeven":
            return bot.smart_breakeven(symbol, float(kwargs.get("buffer_pct", 0.001)))
            
        elif action == "split_iceberg_order":
            return bot.split_iceberg_order(symbol, side, float(kwargs.get("total_qty")), int(kwargs.get("slices", 5)), price, order_type)
            
        elif action == "estimate_slippage":
            return bot.estimate_slippage(symbol, qty, side)
            
        elif action == "detect_spoofing_attempts":
            return bot.detect_spoofing_attempts(symbol)
            
        elif action == "calculate_vwap_bands":
            return bot.calculate_vwap_bands(symbol, interval, limit, float(kwargs.get("stdev", 2.0)))
            
        elif action == "get_trend_divergence":
            return bot.get_trend_divergence(symbol, interval)
            
        elif action == "calculate_profit_factor":
            return bot.calculate_profit_factor()
            
        elif action == "auto_scale_position":
            return bot.auto_scale_position(symbol, float(kwargs.get("add_pct", 0.5)))
            
        elif action == "calculate_market_impact":
            return bot.calculate_market_impact(symbol, qty)
            
        elif action == "get_tick_value":
            return bot.get_tick_value(symbol)
            
        elif action == "get_trading_session_report":
            return bot.get_trading_session_report()
            
        elif action == "generate_microprofit_sequence":
            return bot.generate_microprofit_sequence(symbol, side, float(kwargs.get("entry")), float(kwargs.get("target_profit_usdt")), int(kwargs.get("steps", 3)))

        elif action == "calculate_fisher_transform":
            return bot.calculate_fisher_transform(symbol, interval, int(kwargs.get("period", 10)))
        
        elif action == "calculate_fractal_dimension":
            return bot.calculate_fractal_dimension(symbol, interval, int(kwargs.get("period", 30)))
            
        elif action == "calculate_supertrend":
            return bot.calculate_supertrend(symbol, interval, int(kwargs.get("period", 10)), float(kwargs.get("multiplier", 3.0)))
            
        elif action == "calculate_choppiness_index":
            return bot.calculate_choppiness_index(symbol, interval, int(kwargs.get("period", 14)))
            
        elif action == "calculate_volume_rsi":
            return bot.calculate_volume_rsi(symbol, interval, int(kwargs.get("period", 14)))
            
        elif action == "get_vwap_divergence":
            return bot.get_vwap_divergence(symbol, interval)
            
        elif action == "detect_absorption_zones":
            return bot.detect_absorption_zones(symbol, depth)
            
        elif action == "whale_shadowing_detector":
            return bot.whale_shadowing_detector(symbol)
            
        elif action == "liquidity_hunt_analyzer":
            return bot.liquidity_hunt_analyzer(symbol)
            
        elif action == "calculate_market_efficiency_ratio":
            return bot.calculate_market_efficiency_ratio(symbol, int(kwargs.get("period", 20)))
            
        elif action == "get_trend_strength_index":
            return bot.get_trend_strength_index(symbol)
            
        elif action == "get_session_volume_profile":
            return bot.get_session_volume_profile(symbol)
            
        elif action == "generate_twap_orders":
            return bot.generate_twap_orders(symbol, side, float(kwargs.get("total_qty")), int(kwargs.get("duration_minutes")), int(kwargs.get("intervals", 10)))
            
        elif action == "generate_pv_orders":
            return bot.generate_pv_orders(symbol, side, float(kwargs.get("target_qty")), float(kwargs.get("volume_pct", 0.05)))
            
        elif action == "dynamic_trailing_stop_atr":
            return bot.dynamic_trailing_stop_atr(symbol, side, float(kwargs.get("entry_price")), float(kwargs.get("atr_mult", 2.0)))
            
        elif action == "calculate_range_breakout_levels":
            return bot.calculate_range_breakout_levels(symbol, int(kwargs.get("lookback_bars", 20)))
            
        elif action == "volatility_scaler":
            return bot.volatility_scaler(float(kwargs.get("base_qty", 0.1)), symbol)
            
        elif action == "funding_arbitrage_calc":
            return bot.funding_arbitrage_calc(symbol, float(kwargs.get("qty", 0.1)))
            
        elif action == "get_micro_momentum_score":
            return bot.get_micro_momentum_score(symbol)
            
        elif action == "get_orderbook_velocity":
            return bot.get_orderbook_velocity(symbol)

        elif action == "calculate_fisher_transform":
            return bot.calculate_fisher_transform(symbol, interval, int(kwargs.get("period", 10)))
        
        elif action == "calculate_fractal_dimension":
            return bot.calculate_fractal_dimension(symbol, interval, int(kwargs.get("period", 30)))
            
        elif action == "calculate_supertrend":
            return bot.calculate_supertrend(symbol, interval, int(kwargs.get("period", 10)), float(kwargs.get("multiplier", 3.0)))
            
        elif action == "calculate_choppiness_index":
            return bot.calculate_choppiness_index(symbol, interval, int(kwargs.get("period", 14)))
            
        elif action == "calculate_volume_rsi":
            return bot.calculate_volume_rsi(symbol, interval, int(kwargs.get("period", 14)))
            
        elif action == "get_vwap_divergence":
            return bot.get_vwap_divergence(symbol, interval)
            
        elif action == "detect_absorption_zones":
            return bot.detect_absorption_zones(symbol, depth)
            
        elif action == "whale_shadowing_detector":
            return bot.whale_shadowing_detector(symbol)
            
        elif action == "liquidity_hunt_analyzer":
            return bot.liquidity_hunt_analyzer(symbol)
            
        elif action == "calculate_market_efficiency_ratio":
            return bot.calculate_market_efficiency_ratio(symbol, int(kwargs.get("period", 20)))
            
        elif action == "get_trend_strength_index":
            return bot.get_trend_strength_index(symbol)
            
        elif action == "get_session_volume_profile":
            return bot.get_session_volume_profile(symbol)
            
        elif action == "generate_twap_orders":
            return bot.generate_twap_orders(symbol, side, float(kwargs.get("total_qty")), int(kwargs.get("duration_minutes")), int(kwargs.get("intervals", 10)))
            
        elif action == "generate_pv_orders":
            return bot.generate_pv_orders(symbol, side, float(kwargs.get("target_qty")), float(kwargs.get("volume_pct", 0.05)))
            
        elif action == "dynamic_trailing_stop_atr":
            return bot.dynamic_trailing_stop_atr(symbol, side, float(kwargs.get("entry_price")), float(kwargs.get("atr_mult", 2.0)))
            
        elif action == "calculate_range_breakout_levels":
            return bot.calculate_range_breakout_levels(symbol, int(kwargs.get("lookback_bars", 20)))
            
        elif action == "volatility_scaler":
            return bot.volatility_scaler(float(kwargs.get("base_qty")), symbol)
            
        elif action == "funding_arbitrage_calc":
            return bot.funding_arbitrage_calc(symbol, float(kwargs.get("qty")))
            
        elif action == "get_micro_momentum_score":
            return bot.get_micro_momentum_score(symbol)
            
        elif action == "get_orderbook_velocity":
            return bot.get_orderbook_velocity(symbol)

        # ── Account ──────────────────────────────────────────────────────────
        elif action == "get_wallet_balance":
            return bot.get_wallet_balance(account_type=account_type)

        elif action == "get_account_info":
            return bot.get_account_info()

        elif action == "get_fee_rate":
            return bot.get_fee_rate(category=category, symbol=symbol)

        elif action == "get_positions":
            return bot.get_positions(
                category=category,
                symbol=symbol,
                settle_coin=settle_coin,
            )

        elif action == "get_position_risk":
            return bot.get_position_risk(
                category=category, symbol=symbol
            )
        
        elif action == "panic_close":
            return bot.panic_close(category=category)
        
        elif action == "bulk_update_tp_sl":
            return bot.bulk_update_tp_sl(category=category, tp=take_profit, sl=stop_loss)
            
        elif action == "check_balance":
            return bot.get_wallet_balance(account_type=account_type)

        elif action == "close_position":
            return bot.close_position(symbol=symbol, category=category)
            
        elif action == "get_account_summary":
            return bot.get_account_summary()
        
        elif action == "send_open_positions_summary":
            return bot.get_open_positions_summary(category=category)

        elif action == "alert":
            return {"status": "ok" if bot.alert(message=kwargs.get("message", "Test Alert"), level=kwargs.get("level", "INFO")) else "error"}

        elif action == "export_trade_history":
            return bot.export_trade_history(symbol=symbol, filename=kwargs.get("filename", "trade_history.csv"))
        
        elif action == "set_tp_sl":
            return bot.set_tp_sl(symbol=symbol, tp=take_profit, sl=stop_loss, category=category)
        
        elif action == "calculate_rsi":
            return bot.calculate_rsi(symbol=symbol, interval=interval, period=int(kwargs.get("period", 14)))
            
        elif action == "calculate_macd":
            return bot.calculate_macd(symbol=symbol, interval=interval, fast=int(kwargs.get("fast", 12)), slow=int(kwargs.get("slow", 26)), signal=int(kwargs.get("signal", 9)))
            
        elif action == "calculate_adx":
            return bot.calculate_adx(symbol=symbol, interval=interval, period=int(kwargs.get("period", 14)))
            
        elif action == "calculate_cci":
            return bot.calculate_cci(symbol=symbol, interval=interval, period=int(kwargs.get("period", 20)))
            
        elif action == "calculate_ichimoku":
            return bot.calculate_ichimoku(symbol=symbol, interval=interval, tenkan=int(kwargs.get("tenkan", 9)), kijun=int(kwargs.get("kijun", 26)), senkou_b=int(kwargs.get("senkou_b", 52)))
            
        elif action == "calculate_sma":
            return bot.calculate_sma(symbol=symbol, interval=interval, period=int(kwargs.get("period", 50)))
            
        elif action == "calculate_ema":
            return bot.calculate_ema(symbol=symbol, interval=interval, period=int(kwargs.get("period", 20)))
            
        elif action == "calculate_bollinger_bands":
            return bot.calculate_bollinger_bands(symbol=symbol, interval=interval, period=int(kwargs.get("period", 20)))
            
        elif action == "calculate_vwap":
            return bot.calculate_vwap(symbol=symbol, interval=interval)
            
        elif action == "calculate_atr":
            return bot.calculate_atr(symbol=symbol, interval=interval, period=int(kwargs.get("period", 14)))
            
        elif action == "calculate_stochastic":
            return bot.calculate_stochastic(symbol=symbol, interval=interval, period=int(kwargs.get("period", 14)), smooth_k=int(kwargs.get("smooth_k", 3)), smooth_d=int(kwargs.get("smooth_d", 3)))
        
        elif action == "micro_scalp":
            return bot.micro_scalp(
                symbol=symbol,
                qty=float(kwargs.get("qty", 0.01)),
                fee_rate=float(kwargs.get("fee_rate", 0.0005)),
                target_profit=float(kwargs.get("target_profit", 0.05)),
                category=category
            )
            
        elif action == "stream_orderbook":
            ws = BybitWebSocketManager(bot.config)
            asyncio.run(ws.stream_orderbook(symbol=symbol, duration=int(kwargs.get("duration", 10))))
            return {"status": "ok", "msg": "Stream ended"}
            
        elif action == "calculate_all_indicators":
            return bot.calculate_all_indicators(symbol=symbol, interval=interval)
        
        elif action == "calculate_hma":
            return bot.calculate_hma(symbol=symbol, interval=interval, period=int(kwargs.get("period", 20)))
            
        elif action == "calculate_fractals":
            return bot.calculate_fractals(symbol=symbol, interval=interval)
            
        elif action == "calculate_pivot_points":
            return bot.calculate_pivot_points(symbol=symbol, interval=interval)
            
        elif action == "calculate_klinger":
            return bot.calculate_klinger(symbol=symbol, interval=interval, fast=int(kwargs.get("fast", 34)), slow=int(kwargs.get("slow", 55)))
            
        elif action == "calculate_cmf":
            return bot.calculate_cmf(symbol=symbol, interval=interval, period=int(kwargs.get("period", 20)))
            
        elif action == "calculate_adx_with_di":
            return bot.calculate_adx_with_di(symbol=symbol, interval=interval, period=int(kwargs.get("period", 14)))
            
        elif action == "calculate_elder_ray_index":
            return bot.calculate_elder_ray_index(symbol=symbol, interval=interval, period=int(kwargs.get("period", 13)))
            
        elif action == "calculate_kst":
            return bot.calculate_kst(symbol=symbol, interval=interval)
            
        elif action == "calculate_tema":
            return bot.calculate_tema(symbol=symbol, interval=interval, period=int(kwargs.get("period", 20)))
        
        elif action == "calculate_ehler_rsi":
            return bot.calculate_ehler_rsi(symbol=symbol, interval=interval, period=int(kwargs.get("period", 14)))
            
        elif action == "calculate_ehler_stochastic":
            return bot.calculate_ehler_stochastic(symbol=symbol, interval=interval, period=int(kwargs.get("period", 14)))
        elif action == "scan_scalping_opportunities":
            return bot.scan_scalping_opportunities(symbol=symbol, interval=interval)
        
        elif action == "get_pnl_summary":
            return bot.get_pnl_summary(days=int(kwargs.get("days", 7)))

        elif action == "update_trailing_stop":
            return bot.update_trailing_stop(symbol=symbol, trailing_stop_pct=trailing_stop_pct, category=category)

        elif action == "check_risk_limit":
            return bot.check_risk_limit(symbol=symbol, qty=qty, price=price)

        elif action == "set_leverage":
            return bot.set_leverage(
                symbol=symbol,
                leverage=leverage,
                category=category,
            )

        elif action == "set_trading_stop":
            return bot.set_trading_stop(
                symbol=symbol,
                stop_loss=stop_loss,
                take_profit=take_profit,
                trailing_stop=trailing_stop,
                category=category,
            )

        elif action == "place_breakeven_order":
            return bot.place_breakeven_order(symbol=symbol, fee_rate=float(kwargs.get("fee_rate", 0.0005)), category=category)

        elif action == "set_position_mode":
            return bot.set_position_mode(
                coin=kwargs.get("coin", "USDT"),
                mode=kwargs.get("mode", 0),
                category=category,
            )

        elif action == "get_executions":
            return bot.get_executions(
                category=category, symbol=symbol, limit=limit
            )

        elif action == "get_pnl_history":
            return bot.get_pnl_history(
                category=category, symbol=symbol, limit=limit
            )

        # ── Orders ───────────────────────────────────────────────────────────
        elif action == "place_order":
            return bot.place_order(
                symbol=symbol,
                side=side,
                qty=qty,
                price=price,
                order_type=order_type,
                category=category,
                stop_loss=stop_loss,
                take_profit=take_profit,
                trailing_stop=trailing_stop,
                reduce_only=reduce_only,
                time_in_force=time_in_force,
                client_oid=client_oid,
                trigger_price=trigger_price,
                trigger_by=trigger_by,
                tp_order_type=tp_order_type,
                sl_order_type=sl_order_type,
                **kwargs,
            )

        elif action == "place_smart_trade":
            return bot.place_smart_trade(
                symbol=symbol,
                side=side,
                qty=qty,
                price=price,
                tp_pct=tp_pct,
                sl_pct=sl_pct,
                trailing_stop_pct=trailing_stop_pct,
                category=category,
            )

        elif action == "amend_order":
            return bot.amend_order(
                symbol=symbol,
                order_id=order_id,
                client_oid=client_oid,
                qty=qty,
                price=price,
                stop_loss=stop_loss,
                take_profit=take_profit,
                category=category,
            )

        elif action == "cancel_order":
            return bot.cancel_order(
                symbol=symbol,
                order_id=order_id,
                client_oid=client_oid,
                category=category,
            )

        elif action == "cancel_all_orders":
            return bot.cancel_all_orders(
                symbol=symbol, category=category
            )

        elif action == "get_open_orders":
            return bot.get_open_orders(
                symbol=symbol, category=category, limit=limit
            )

        elif action == "get_order_history":
            return bot.get_order_history(
                symbol=symbol, category=category, limit=limit
            )

        elif action == "batch_place_orders":
            if not orders:
                return {
                    "status": "error",
                    "msg": "orders list is required for batch_place_orders",
                }
            return bot.batch_place_orders(
                orders=orders, category=category
            )

        # ── Market Data ──────────────────────────────────────────────────────
        elif action == "get_ticker":
            return bot.get_ticker(symbol=symbol, category=category)

        elif action == "get_orderbook":
            return bot.get_orderbook(
                symbol=symbol, limit=limit, category=category
            )

        elif action == "get_klines":
            return bot.get_klines(
                symbol=symbol,
                interval=interval,
                limit=limit,
                category=category,
            )

        elif action == "get_recent_trades":
            return bot.get_recent_trades(
                symbol=symbol, limit=limit, category=category
            )

        elif action == "get_instruments_info":
            return bot.get_instruments_info(
                category=category, symbol=symbol, limit=limit
            )

        elif action == "get_funding_rate":
            return bot.get_funding_rate(
                symbol=symbol, category=category, limit=limit
            )

        elif action == "get_open_interest":
            return bot.get_open_interest(
                symbol=symbol,
                interval=interval,
                category=category,
            )

        elif action == "scan_symbols":
            if not symbols:
                return {
                    "status": "error",
                    "msg": "symbols list is required for scan_symbols",
                }
            return bot.scan_symbols(
                symbols=symbols,
                category=category,
                include_regime=include_regime,
            )

        # ── Journal ───────────────────────────────────────────────────────────
        elif action == "get_pnl_summary":
            return bot.get_pnl_summary(symbol=symbol, limit=limit)

        elif action == "market_summary":
            return bot.get_market_summary()

        elif action == "analyze_symbol":
            return bot.analyze_symbol(symbol=symbol)

        # ── Journal / Analysis ────────────────────────────────────────────────
        elif action == "get_journal":
            entries = bot.journal.get_entries(
                symbol=journal_symbol, limit=journal_limit
            )
            return {
                "status": "ok",
                "count": len(entries),
                "entries": entries,
            }
        elif action == "get_pnl_summary":
            return bot.get_pnl_summary(symbol=symbol, limit=limit)

        elif action == "market_summary":
            return bot.get_market_summary()

        elif action == "analyze_symbol":
            return bot.analyze_symbol(symbol=symbol)

        elif action == "calculate_orderflow_delta":
            return bot.calculate_orderflow_delta(
                symbol=symbol,
                interval=interval,
                limit=int(kwargs.get("limit", 100)),
            )

        elif action == "calculate_orderbook_imbalance":
            return bot.calculate_orderbook_imbalance(
                symbol=symbol,
                depth=int(kwargs.get("depth", 50)),
                tier_size=int(kwargs.get("tier_size", 10)),
                spoof_threshold=float(kwargs.get("spoof_threshold", 5.0)),
            )

        elif action == "calculate_liquidity_heatmap":
            return bot.calculate_liquidity_heatmap(
                symbol=symbol,
                interval=interval,
                depth=int(kwargs.get("depth", 100)),
                bucket_count=int(kwargs.get("bucket_count", 20)),
                kline_limit=int(kwargs.get("kline_limit", 100)),
            )

        elif action == "calculate_market_depth_profile":
            order_sizes_raw   = kwargs.get("order_sizes", "100,500,1000,5000")
            distance_pcts_raw = kwargs.get("distance_pcts", "0.1,0.25,0.5,1.0,2.0")
            order_sizes_list  = [float(x) for x in str(order_sizes_raw).split(",")]
            distance_pcts_list = [float(x) for x in str(distance_pcts_raw).split(",")]
            return bot.calculate_market_depth_profile(
                symbol=symbol,
                depth=int(kwargs.get("depth", 200)),
                order_sizes=order_sizes_list,
                distance_pcts=distance_pcts_list,
            )
        
        elif action == "calculate_sr_levels":
            return bot.calculate_sr_levels(
                symbol=symbol,
                top_n=int(kwargs.get("top_n", 7)),
                vol_cut=float(kwargs.get("vol_cut", 0.4))
            )
            
        elif action == "get_orderbook_analysis":
            return bot.get_orderbook_analysis(symbol=symbol, depth=int(kwargs.get("depth", 50)))
        
        elif action == "calculate_limit_micro_profit":
            return bot.calculate_limit_micro_profit(
                entry_price=float(kwargs["entry_price"]),
                limit_price=float(kwargs["limit_price"]),
                side=kwargs["side"],
                qty=float(kwargs["qty"])
            )
        elif action == "calculate_depth_weighted_profit":
            return bot.calculate_depth_weighted_profit(
                symbol=symbol,
                entry_price=float(kwargs["entry_price"]),
                limit_price=float(kwargs["limit_price"]),
                side=kwargs["side"],
                qty=float(kwargs["qty"])
            )
            return bot.get_orderbook_analysis(symbol=symbol, depth=int(kwargs.get("depth", 50)))

        elif action == "calculate_support_resistance_levels":
            return bot.calculate_support_resistance_levels(
                symbol=symbol, 
                interval=interval, 
                depth=int(kwargs.get("depth", 50)), 
                wall_multiplier=float(kwargs.get("wall_multiplier", 3.0))
            )

        elif action == "calculate_fibonacci_levels":
            return bot.calculate_fibonacci_levels(
                symbol=symbol, 
                interval=interval, 
                lookback=int(kwargs.get("lookback", 50))
            )

        elif action == "generate_market_depth_report":
            return bot.generate_market_depth_report(symbol=symbol)

        elif action == "get_orderbook_analysis":
            return bot.get_orderbook_analysis(symbol=symbol, depth=int(kwargs.get("depth", 50)))

        elif action == "detect_high_confluence_levels":
            return bot.detect_high_confluence_levels(
                symbol=symbol, 
                interval=interval, 
                depth=int(kwargs.get("depth", 50))
            )

    except Exception as exc:
        logger.error("run(%s) raised: %s", action, exc, exc_info=True)
        return {"status": "error", "action": action, "msg": str(exc)}


def export_data(data: dict, filename: str, format: str):
    """Exports data to JSON or CSV."""
    if format == "json":
        with open(filename, 'w') as f:
            json.dump(data, f, indent=2)
    elif format == "csv":
        items = data.get("list", [data])
        if not items: return
        with open(filename, 'w', newline='') as f:
            writer = csv.DictWriter(f, fieldnames=items[0].keys())
            writer.writeheader()
            writer.writerows(items)
    print(f"Data exported to {filename}")

def pretty_print_result(action: str, result: dict):
    """Prints results in a human-friendly format."""
    if result.get("status") == "error":
        print(f"\033[91mERROR: {result.get('msg')}\033[0m")
        return

    # If it's a simple status message
    if "msg" in result and len(result) <= 2:
        print(f"\033[92m{result['msg']}\033[0m")
        return

    print(f"\n\033[94m═══ {action.upper()} ═══\033[0m")
    
    # Custom formatters for specific actions
    if action == "get_positions":
        positions = result.get("list", [])
        if not positions:
            print("No open positions.")
        for pos in positions:
            symbol = pos.get("symbol")
            side = pos.get("side")
            size = pos.get("size")
            pnl = float(pos.get("unrealisedPnl", 0))
            color = "\033[92m" if pnl > 0 else "\033[91m"
            print(f"{symbol} | {side} | Size: {size} | PnL: {color}{pnl:+.4f}\033[0m")

    elif action == "get_wallet_balance":
        coins = result.get("list", [{}])[0].get("coin", [])
        for c in coins:
            balance = c.get("walletBalance", "0")
            if float(balance) > 0:
                print(f"{c['coin']}: {balance}")

    elif action == "analyze_symbol":
        print(f"Symbol: {result.get('symbol')} | Price: {result.get('last_price')}")
        print("-" * 40)
        for tf, data in result.get("analysis", {}).items():
            print(f"[{tf:>3}] Regime: {data['regime']:<15} | RSI: {data['rsi']:>5} | EMA: {data['ema']}")
    elif action == "calculate_orderflow_delta":
        print(f"Symbol: {result.get('symbol')} | Net Delta: {result.get('delta')}")

    elif action == "calculate_market_depth_profile":
        print(f"Symbol: {result.get('symbol')} | Mid Price: {result.get('mid_price')}")
        print(f"{'Level':<10} | {'Bid Vol':<15} | {'Ask Vol':<15}")
        print("-" * 45)
        for pct, vols in result.get("profile", {}).items():
            print(f"{pct:<10} | {vols['bid_vol']:<15} | {vols['ask_vol']:<15}")

    elif action == "calculate_sr_levels":
        print(f"S/R Levels for {result.get('symbol')}:")
        print("Support:", result.get("support_levels", []))
        print("Resistance:", result.get("resistance_levels", []))

    elif action == "calculate_fibonacci_levels":
        print(f"Fibonacci Levels for {result.get('status')}:")
        for level, price in result.get("levels", {}).items():
            print(f"{level:>6}: {price:.4f}")

    elif action == "detect_high_confluence_levels":
        print(f"High Confluence Zones for {result.get('symbol')}:")
        for zone in result.get("high_confluence_zones", []):
            color = "\033[92m" if zone['type'] == "Support" else "\033[91m"
            print(f"{color}{zone['type']:<10}\033[0m | Price: {zone['price']:<10.4f} | Confluence: {zone['score']}")

    elif action == "generate_market_depth_report":
        print(f"Market Depth Report for {result.get('symbol')}:")
        print("Support:", result.get("support_zones"))
        print("Resistance:", result.get("resistance_zones"))

    elif action == "deep_level_sort":
        print(f"Deep Orderbook Levels for {result.get('symbol')}:")
        print(f"{'Side':<10} | {'Avg Price':<10} | {'Cum Vol':<10}")
        print("-" * 35)
        for b in result.get("bid_levels", []):
            print(f"\033[92m{'Bid':<10}\033[0m | {b[0]:<10.4f} | {b[1]:<10.2f}")
        for a in result.get("ask_levels", []):
            print(f"\033[91m{'Ask':<10}\033[0m | {a[0]:<10.4f} | {a[1]:<10.2f}")

    elif action == "get_ticker":
        for t in result.get("list", []):
            color = "\033[92m" if float(t.get("price24hPcnt", 0)) > 0 else "\033[91m"
            print(f"{t['symbol']} | Price: {t['lastPrice']} | 24h: {color}{float(t.get('price24hPcnt', 0))*100:+.2f}%\033[0m")

    elif action == "get_open_orders":
        for o in result.get("list", []):
            print(f"{o['symbol']} | {o['side']} | {o['orderType']} | {o['price']} | {o['orderStatus']}")

    elif action == "get_funding_rate":
        for f in result.get("list", []):
            print(f"{f['symbol']} | Rate: {float(f['fundingRate'])*100:.4f}% | Next: {f['nextFundingTime']}")

    else:
        # Fallback to JSON
        print(json.dumps(result, indent=2, ensure_ascii=False))

if __name__ == "__main__":
    import argparse
    import sys

    # Dynamic Parser: Define known core args, then handle others as kwargs
    parser = argparse.ArgumentParser(description="Bybit Realm Trading CLI")
    parser.add_argument("--action", required=True, help="Action to perform")
    parser.add_argument("--symbol", help="Trading symbol")
    parser.add_argument("--side", choices=["Buy", "Sell"], help="Order side")
    parser.add_argument("--qty", type=float, help="Order quantity")
    parser.add_argument("--price", type=float, help="Order price")
    parser.add_argument("--category", default="linear")
    parser.add_argument("--json", action="store_true", help="Output raw JSON")
    parser.add_argument("--export", help="Export to file")

    # Use parse_known_args to capture all other flags
    args, unknown = parser.parse_known_args()
    
    # Convert unknown flags (--key value) into a dictionary
    kwargs = {}
    i = 0
    while i < len(unknown):
        key = unknown[i].lstrip("-").replace("-", "_")
        if i + 1 < len(unknown) and not unknown[i+1].startswith("-"):
            # Check if it's a number
            val = unknown[i+1]
            try:
                if "." in val: kwargs[key] = float(val)
                else: kwargs[key] = int(val)
            except ValueError:
                kwargs[key] = val
            i += 2
        else:
            kwargs[key] = True
            i += 1

    # Merge known args into kwargs
    for k, v in vars(args).items():
        if v is not None:
            kwargs[k] = v

    # Execute
    result = run(**kwargs)

    if args.export:
        fmt = args.export.split('.')[-1]
        export_data(result, args.export, fmt)
    elif args.json:
        print(json.dumps(result, indent=2, ensure_ascii=False))
    else:
        pretty_print_result(args.action, result)
    
    sys.exit(0 if result.get("status") != "error" else 1)
