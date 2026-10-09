from datetime import datetime, timezone


class AccountMixin:
    def get_wallet_balance(self, account_type="UNIFIED"):
        return self._request(
            "GET",
            "/v5/account/wallet-balance",
            {"accountType": account_type},
            category="account",
        )

    def get_positions(self, category="linear", symbol=None, settle_coin="USDT"):
        params = {"category": category}
        if symbol:
            params["symbol"] = symbol.upper()
        if category == "linear":
            params["settleCoin"] = settle_coin
        return self._request("GET", "/v5/position/list", params, category="account")

    def get_account_info(self):
        return self._request("GET", "/v5/account/info", category="account")

    def get_fee_rate(self, category="linear", symbol=None):
        params = {"category": category}
        if symbol:
            params["symbol"] = symbol.upper()
        return self._request("GET", "/v5/account/fee-rate", params, category="account")

    def get_pnl_history(self, category="linear", symbol=None, limit=50):
        params = {"category": category, "limit": limit}
        if symbol:
            params["symbol"] = symbol.upper()
        return self._request(
            "GET", "/v5/position/closed-pnl", params, category="account"
        )

    def get_order_history(self, category="linear", symbol=None, limit=50):
        """Get historical orders (filled/cancelled)."""
        params = {"category": category, "limit": limit}
        if symbol:
            params["symbol"] = symbol.upper()
        return self._request("GET", "/v5/order/history", params, category="account")

    def get_leverage(self, symbol, category="linear"):
        """Get current leverage for a symbol."""
        return self._request(
            "GET",
            "/v5/position/list",
            {"category": category, "symbol": symbol.upper()},
            category="account",
        )

    def get_account_summary(self):
        bal = self.get_wallet_balance()
        pos = self.get_positions()
        return {
            "status": "ok",
            "balance": bal,
            "positions": pos,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }

    def get_settlement_coin_info(self, coin=None):
        """Get settlement coin information."""
        params = {}
        if coin:
            params["settleCoin"] = coin.upper()
        return self._request(
            "GET", "/v5/asset/settlement-record", params, category="account"
        )

    def get_performance_summary(self, limit: int = 100) -> dict:
        """Calculates trading performance from the SQLite journal."""
        import sqlite3

        try:
            conn = sqlite3.connect(self.config.db_path)
            cursor = conn.cursor()
            cursor.execute(
                "SELECT result FROM trades WHERE action = 'place_order' AND status = 'success' ORDER BY timestamp DESC LIMIT ?",
                (limit,),
            )
            trades = cursor.fetchall()
            conn.close()

            if not trades:
                return {"status": "ok", "msg": "No trade history found."}

            wins, losses = 0, 0
            total_profit, total_loss = 0.0, 0.0

            # This is a heuristic since Bybit V5 returns orderId,
            # real PnL requires fetching closed-pnl for those orders.
            # For now, let's fetch the actual closed PnL from the API for better accuracy.
            pnl_res = self.get_pnl_history(limit=limit)
            pnl_list = pnl_res.get("list", [])

            for p in pnl_list:
                pnl = float(p.get("closedPnl", 0))
                if pnl > 0:
                    wins += 1
                    total_profit += pnl
                elif pnl < 0:
                    losses += 1
                    total_loss += abs(pnl)

            win_rate = (wins / (wins + losses) * 100) if (wins + losses) > 0 else 0
            pf = (
                (total_profit / total_loss)
                if total_loss > 0
                else (total_profit if total_profit > 0 else 0)
            )

            return {
                "status": "ok",
                "total_trades": len(pnl_list),
                "wins": wins,
                "losses": losses,
                "win_rate_pct": round(win_rate, 2),
                "profit_factor": round(pf, 2),
                "net_pnl": round(total_profit - total_loss, 4),
            }
        except Exception as e:
            return {"status": "error", "msg": str(e)}


class RiskManagerMixin:
    def set_leverage(self, symbol, leverage, category="linear"):
        return self._request(
            "POST",
            "/v5/position/set-leverage",
            json_data={
                "category": category,
                "symbol": symbol.upper(),
                "buyLeverage": str(leverage),
                "sellLeverage": str(leverage),
            },
            category="trade",
        )

    def set_margin_mode(self, symbol, is_isolated, leverage=1, category="linear"):
        return self._request(
            "POST",
            "/v5/position/switch-isolated",
            json_data={
                "category": category,
                "symbol": symbol.upper(),
                "tradeMode": 1 if is_isolated else 0,
                "buyLeverage": str(leverage),
                "sellLeverage": str(leverage),
            },
            category="trade",
        )

    def get_position_risk(self, category="linear", symbol=None):
        pos_res = self.get_positions(category, symbol)
        positions = pos_res.get("list", [])
        enriched = []
        for p in positions:
            sz = float(p.get("size", 0))
            if sz == 0:
                continue
            enriched.append(
                {
                    **p,
                    "notional": sz * float(p.get("markPrice", 0)),
                    "pnl_pct": float(p.get("unrealisedPnl", 0))
                    / (sz * float(p.get("avgPrice", 1)))
                    * 100
                    if sz > 0
                    else 0,
                }
            )
        return {"status": "ok", "positions": enriched}
