"""Target analysis utilities for USDT‑based profit‑target calculations."""


class USDTTargetCalculator:
    """
    Calculate USDT‑based profit targets for both long and short positions.

    Parameters
    ----------
    entry_price : float
        Price at which the position is opened.
    position_size : float
        Size of the position (in contracts or base‑asset units).
    leverage : int
        Leverage applied to the position.
    side : str, optional
        ``"Long"`` or ``"Short"``. Determines whether price levels are
        generated above (long) or below (short) the entry price.
        Defaults to ``"Long"``.
    maker_fee : float, optional
        Fee rate for maker orders (default: 0.0002 = 0.02%).
    taker_fee : float, optional
        Fee rate for taker orders (default: 0.0004 = 0.04%).
    account_balance : float, optional
        Total account balance, used for percentage‑based risk limits
        (default: 10 000 USDT).
    """

    def __init__(
        self,
        entry_price: float,
        position_size: float,
        leverage: int,
        *,
        side: str = "Long",
        maker_fee: float = 0.0002,
        taker_fee: float = 0.0004,
        account_balance: float = 10_000.0,
    ) -> None:
        self.entry_price = float(entry_price)
        self.position_size = float(position_size)
        self.leverage = int(leverage)
        self.side = side.title()  # Normalise to "Long" / "Short"
        if self.side not in {"Long", "Short"}:
            raise ValueError("side must be 'Long' or 'Short'")
        self.maker_fee = maker_fee
        self.taker_fee = taker_fee
        self.account_balance = account_balance

        # Derived values
        self.usdt_per_contract = self.entry_price * self.position_size / self.leverage

    def _calc_fee(self, side: str, price: float) -> float:
        """
        Compute the absolute fee (USDT) for a given side and exit price.

        The fee is based on the notional value ``price * position_size /
        leverage`` multiplied by the appropriate fee rate.
        """
        notional = price * self.position_size / self.leverage
        fee_rate = self.maker_fee if side == "Buy" else self.taker_fee
        return notional * fee_rate

    def calculate_profit_targets_usdt(
        self,
        *,
        target_usdt: float = 5.0,
        risk_reward: float = 2.0,
        max_risk_pct: float = 2.0,
        num_levels: int = 5,
    ) -> dict:
        """
        Compute a series of profit‑target price levels and associated metrics.

        The method validates inputs, determines the maximum permissible risk,
        calculates price steps, generates the target levels (respecting the
        position ``side``), estimates total fees, and returns a structured
        dictionary.

        Parameters
        ----------
        target_usdt : float, optional
            Desired profit per level (default: 5.0 USDT).
        risk_reward : float, optional
            Desired risk‑reward ratio (default: 2.0).
        max_risk_pct : float, optional
            Maximum percentage of account balance that may be risked
            (default: 2.0%).
        num_levels : int, optional
            Number of price levels to generate (default: 5).

        Returns
        -------
        dict
            A dictionary containing:
            - ``status``: ``"ok"`` on success or ``"error"`` with a message.
            - ``entry_price``, ``position_size``, ``leverage``, ``side``.
            - ``target_usdt``, ``risk_reward``, ``max_risk_pct``,
              ``num_levels``.
            - ``levels``: list of target price levels.
            - ``risk_usdt``: maximum allowed risk in USDT.
            - ``fee_est_usdt``: estimated total fee across all levels.
            - ``roi_pct``: projected return on investment (%).
            - ``risk_reward_ratio``: the configured risk‑reward ratio.
        """
        # ------------------------------------------------------------------
        # 1️⃣ Input validation
        # ------------------------------------------------------------------
        if self.entry_price <= 0:
            return {"status": "error", "msg": "Entry price must be > 0"}
        if self.position_size <= 0:
            return {"status": "error", "msg": "Position size must be > 0"}
        if self.leverage <= 0:
            return {"status": "error", "msg": "Leverage must be > 0"}
        if target_usdt <= 0:
            return {"status": "error", "msg": "Target USDT must be > 0"}
        if risk_reward <= 0:
            return {"status": "error", "msg": "Risk‑reward must be > 0"}
        if max_risk_pct <= 0:
            return {"status": "error", "msg": "Max risk % must be > 0"}
        if num_levels <= 0:
            return {"status": "error", "msg": "Number of levels must be > 0"}

        # ------------------------------------------------------------------
        # 2️⃣ Risk limits
        # ------------------------------------------------------------------
        max_risk_usdt = (max_risk_pct / 100.0) * self.account_balance

        # ------------------------------------------------------------------
        # 3️⃣ Price step calculation
        # ------------------------------------------------------------------
        # profit = position_size * leverage * price_step * risk_reward
        price_step = target_usdt / (self.position_size * risk_reward)

        # ------------------------------------------------------------------
        # 4️⃣ Generate target price levels
        # ------------------------------------------------------------------
        levels = []
        for i in range(1, num_levels + 1):
            if self.side == "Long":
                target_price = self.entry_price + price_step * i
            else:  # Short
                target_price = self.entry_price - price_step * i
            levels.append(target_price)

        # ------------------------------------------------------------------
        # 5️⃣ Fee estimation (assume we exit at each level with a full position)
        # ------------------------------------------------------------------
        total_fee_usdt = sum(self._calc_fee("Sell", p) for p in levels)

        # ------------------------------------------------------------------
        # 6️⃣ ROI calculation
        # ------------------------------------------------------------------
        entry_notional = self.entry_price * self.position_size
        projected_profit_usdt = target_usdt * num_levels
        roi_pct = (projected_profit_usdt / entry_notional) * 100.0

        # ------------------------------------------------------------------
        # 7️⃣ Assemble result
        # ------------------------------------------------------------------
        result = {
            "status": "ok",
            "entry_price": self.entry_price,
            "position_size": self.position_size,
            "leverage": self.leverage,
            "side": self.side,
            "target_usdt": target_usdt,
            "risk_reward": risk_reward,
            "max_risk_pct": max_risk_pct,
            "num_levels": num_levels,
            "levels": levels,
            "risk_usdt": max_risk_usdt,
            "fee_est_usdt": round(total_fee_usdt, 6),
            "roi_pct": round(roi_pct, 4),
            "risk_reward_ratio": risk_reward,
        }
        return result
