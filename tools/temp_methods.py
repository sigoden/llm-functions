        """Atomically saves journal entries."""
        temp_path = self._path.with_suffix(".tmp")
        temp_path.write_text(
            json.dumps(self._entries, indent=2, ensure_ascii=False)
        )
        temp_path.replace(self._path)

    def record(self, action: str, payload: dict, result: dict, symbol: Optional[str] = None):
        entry = {
            "id": str(uuid.uuid4()),
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "action": action,
            "symbol": symbol or payload.get("symbol", "N/A"),
            "payload": payload,
            "result": result,
            "status": "success" if result.get("status") != "error" else "failed"
        }
        with self._lock:
            self._entries.append(entry)
            self._save()
        return entry["id"]

    # ══════════════════════════════════════════════════════════════════════════
    # MICRO-SCALER (Maker-Only Scalping)
    # ══════════════════════════════════════════════════════════════════════════
    def micro_scalp(self, symbol: str, qty: float, fee_rate: float, target_profit: float, category: str = "linear") -> dict:
        """Executes a phased Maker-only Buy -> Sell trade."""
        
        # 1. Get Best Bid
        ob = self.get_orderbook(symbol=symbol, limit=1, category=category).get("result", {})
        bids = ob.get("b", [])
        if not bids: return {"status": "error", "msg": "No bid data"}
        buy_price = float(bids[0][0])
        
        # 2. Phase A: Maker Buy
        self.alert(f"Phase A: Placing Maker Buy for {symbol} @ {buy_price}", "INFO")
        buy_order = self.place_order(symbol=symbol, side="Buy", qty=qty, price=buy_price, order_type="Limit", time_in_force="PostOnly", category=category)
        if buy_order.get("status") == "error": return buy_order
        
        # 3. Wait for Fill (Looping REST check)
        order_id = buy_order.get("orderId")
        filled = False
        for _ in range(10): # 10s wait
            time.sleep(1)
            orders = self.get_open_orders(symbol=symbol, category=category).get("list", [])
            if not any(o["orderId"] == order_id for o in orders):
                filled = True
                break
        
        if not filled:
            self.cancel_order(symbol=symbol, order_id=order_id, category=category)
            return {"status": "error", "msg": "Buy order timed out"}
        
        # 4. Phase B: Maker Sell
        # P_sell = ( (Q * P_buy) + Profit ) / (Q * (1-F)^2)
        sell_price = ((qty * buy_price) + target_profit) / (qty * (1 - fee_rate)**2)
        self.alert(f"Phase B: Placing Maker Sell @ {round(sell_price, 4)}", "INFO")
        
        return self.place_order(symbol=symbol, side="Sell", qty=qty, price=round(sell_price, 4), order_type="Limit", time_in_force="PostOnly", reduce_only=True, category=category)

    # ... (existing methods)

    def calculate_all_indicators(self, symbol: str, interval: str = "60") -> dict:
        """Aggregates all available indicators for a symbol."""
        # Using a list of methods to call dynamically
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
            "hma": lambda: self.calculate_hma(symbol, interval)
        }
        results = {name: func() for name, func in indicator_map.items()}
        return {"status": "ok", "symbol": symbol, "indicators": results}

    def get_entries(
        self,
        symbol: Optional[str] = None,
        limit: int = 50,
    ) -> List[dict]:
        entries = self._entries
        if symbol:
            entries = [
                e
                for e in entries
                if e.get("payload", {}).get("symbol", "").upper()
                == symbol.upper()
            ]
        return entries[-limit:]

    def summary(self) -> dict:
        return {
            "total_entries": len(self._entries),
            "journal_path": str(self._path.resolve()),
        }


# ══════════════════════════════════════════════════════════════════════════════
# MAIN CLIENT
# ══════════════════════════════════════════════════════════════════════════════
