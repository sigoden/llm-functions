import math
import statistics
import time
from typing import List, Optional


class MarketDataMixin:
    def get_ticker(self, symbol: str, category: str = "linear") -> dict:
        """Retrieves the latest price and 24h statistics for a symbol."""
        return self._request(
            "GET",
            "/v5/market/tickers",
            {"category": category, "symbol": symbol.upper()},
            signed=False,
            category="market",
        )

    def get_orderbook(
        self, symbol: str, limit: int = 50, category: str = "linear"
    ) -> dict:
        """Retrieves the order book depth for a symbol."""
        return self._request(
            "GET",
            "/v5/market/orderbook",
            {"category": category, "symbol": symbol.upper(), "limit": limit},
            signed=False,
            category="market",
        )

    def get_klines(
        self,
        symbol: str,
        interval: str = "60",
        limit: int = 50,
        category: str = "linear",
    ) -> dict:
        """Retrieves historical kline (candlestick) data."""
        return self._request(
            "GET",
            "/v5/market/kline",
            {
                "category": category,
                "symbol": symbol.upper(),
                "interval": interval,
                "limit": limit,
            },
            signed=False,
            category="market",
        )

    def get_instruments_info(
        self, category: str = "linear", symbol: Optional[str] = None
    ) -> dict:
        """Retrieves detailed instrument specifications (tick size, lot size, etc.)."""
        params = {"category": category}
        if symbol:
            params["symbol"] = symbol.upper()
        return self._request(
            "GET",
            "/v5/market/instruments-info",
            params,
            signed=False,
            category="market",
        )

    def get_funding_rate(
        self, symbol: str, category: str = "linear", limit: int = 50
    ) -> dict:
        """Retrieves the historical funding rate for perpetual contracts."""
        return self._request(
            "GET",
            "/v5/market/funding/history",
            {"category": category, "symbol": symbol.upper(), "limit": limit},
            signed=False,
            category="market",
        )

    def get_recent_trades(
        self, symbol: str, limit: int = 50, category: str = "linear"
    ) -> dict:
        """Retrieves the most recent public trades on the exchange."""
        return self._request(
            "GET",
            "/v5/market/recent-trade",
            {"category": category, "symbol": symbol.upper(), "limit": limit},
            signed=False,
            category="market",
        )

    def get_open_interest(
        self,
        symbol: str,
        interval: str = "1h",
        limit: int = 50,
        category: str = "linear",
    ) -> dict:
        """Retrieves open interest data."""
        return self._request(
            "GET",
            "/v5/market/open-interest",
            {
                "category": category,
                "symbol": symbol.upper(),
                "interval": interval,
                "limit": limit,
            },
            signed=False,
            category="market",
        )

    def get_volatility_index(self, symbol: str, category: str = "linear") -> dict:
        """Retrieves volatility index data."""
        return self._request(
            "GET",
            "/v5/market/volatility",
            {"category": category, "symbol": symbol.upper()},
            signed=False,
            category="market",
        )

    def _get_klines_safely(
        self, symbol: str, interval: str, limit: int, category: str = "linear"
    ) -> List[list]:
        res = self.get_klines(symbol, interval, limit, category)
        if isinstance(res, dict) and "list" in res:
            return res["list"]
        return res.get("result", {}).get("list", []) if isinstance(res, dict) else []

    @staticmethod
    def _orderbook_levels(ob: dict) -> tuple:
        """Normalize an orderbook payload into (bids, asks) of [(price, qty)].

        Accepts both the already-unwrapped shape returned by
        BybitBaseClient._request ({"b": [...], "a": [...]}) and a nested
        {"result": {"b": ..., "a": ...}} shape.
        """
        if not isinstance(ob, dict):
            return [], []
        res = ob.get("result", ob) if "result" in ob and not ("b" in ob or "a" in ob) else ob
        bids, asks = [], []
        for raw in res.get("b", []) or []:
            try:
                bids.append((float(raw[0]), float(raw[1])))
            except (TypeError, ValueError, IndexError):
                continue
        for raw in res.get("a", []) or []:
            try:
                asks.append((float(raw[0]), float(raw[1])))
            except (TypeError, ValueError, IndexError):
                continue
        return bids, asks

    # ══════════════════════════════════════════════════════════════════════════
    # TECHNICAL INDICATORS
    # ══════════════════════════════════════════════════════════════════════════

    def calculate_rsi(
        self, symbol: str, interval: str = "60", period: int = 14
    ) -> dict:
        """Calculates the Relative Strength Index (RSI, Wilder smoothing)."""
        klines = self._get_klines_safely(symbol, interval, period + 100)
        if len(klines) < period + 1:
            return {"status": "error", "msg": "No data"}
        closes = [float(k[4]) for k in reversed(klines)]
        diffs = [closes[i + 1] - closes[i] for i in range(len(closes) - 1)]
        gains = [d if d > 0 else 0 for d in diffs]
        losses = [-d if d < 0 else 0 for d in diffs]
        avg_g = sum(gains[:period]) / period
        avg_l = sum(losses[:period]) / period
        for i in range(period, len(gains)):
            avg_g = (avg_g * (period - 1) + gains[i]) / period
            avg_l = (avg_l * (period - 1) + losses[i]) / period
        if avg_l == 0:
            return {"status": "ok", "rsi": 100}
        return {"status": "ok", "rsi": round(100 - (100 / (1 + avg_g / avg_l)), 2)}

    def calculate_macd(
        self,
        symbol: str,
        interval: str = "60",
        fast: int = 12,
        slow: int = 26,
        signal: int = 9,
    ) -> dict:
        """Calculates MACD, Signal Line, and Histogram."""
        klines = self._get_klines_safely(symbol, interval, 100)
        closes = [float(k[4]) for k in reversed(klines)]

        def ema(data, p):
            k = 2 / (p + 1)
            e = [data[0]]
            for x in data[1:]:
                e.append(x * k + e[-1] * (1 - k))
            return e

        fast_ema, slow_ema = ema(closes, fast), ema(closes, slow)
        macd = [f - s for f, s in zip(fast_ema, slow_ema)]
        sig = ema(macd, signal)
        return {
            "status": "ok",
            "macd": round(macd[-1], 4),
            "signal": round(sig[-1], 4),
            "hist": round(macd[-1] - sig[-1], 4),
        }

    def calculate_atr(
        self, symbol: str, interval: str = "60", period: int = 14
    ) -> dict:
        """Calculates the Average True Range (ATR)."""
        klines = self._get_klines_safely(symbol, interval, period + 1)
        if len(klines) < period + 1:
            return {"status": "error", "msg": "No data"}
        # API returns newest-first; walk chronologically (oldest -> newest)
        chron = list(reversed(klines))
        tr = []
        for i in range(1, len(chron)):
            h, l, pc = (
                float(chron[i][2]),
                float(chron[i][3]),
                float(chron[i - 1][4]),
            )
            tr.append(max(h - l, abs(h - pc), abs(l - pc)))
        return {"status": "ok", "atr": round(sum(tr[-period:]) / period, 4)}

    def calculate_adx(
        self, symbol: str, interval: str = "60", period: int = 14
    ) -> dict:
        """Calculates Average Directional Index (ADX, Wilder)."""
        klines = self._get_klines_safely(symbol, interval, period * 3 + 10)
        if len(klines) < period * 2:
            return {"status": "error", "msg": "No data"}
        h, l, c = (
            [float(k[2]) for k in reversed(klines)],
            [float(k[3]) for k in reversed(klines)],
            [float(k[4]) for k in reversed(klines)],
        )
        tr, pdm, ndm = [], [], []
        for i in range(1, len(c)):
            tr.append(max(h[i] - l[i], abs(h[i] - c[i - 1]), abs(l[i] - c[i - 1])))
            up, down = h[i] - h[i - 1], l[i - 1] - l[i]
            pdm.append(max(up, 0) if up > down else 0)
            ndm.append(max(down, 0) if down > up else 0)
        # ADX = average of the last `period` DX values (Wilder)
        dx_series = []
        for i in range(period - 1, len(tr)):
            s_tr = sum(tr[i - period + 1 : i + 1])
            s_pdm = sum(pdm[i - period + 1 : i + 1])
            s_ndm = sum(ndm[i - period + 1 : i + 1])
            di_p = 100 * s_pdm / s_tr if s_tr > 0 else 0
            di_n = 100 * s_ndm / s_tr if s_tr > 0 else 0
            dx_series.append(
                100 * abs(di_p - di_n) / (di_p + di_n) if (di_p + di_n) > 0 else 0
            )
        adx = sum(dx_series[-period:]) / period if dx_series else 0
        s_tr = sum(tr[-period:])
        di_p = 100 * sum(pdm[-period:]) / s_tr if s_tr > 0 else 0
        di_n = 100 * sum(ndm[-period:]) / s_tr if s_tr > 0 else 0
        return {
            "status": "ok",
            "adx": round(adx, 2),
            "di_plus": round(di_p, 2),
            "di_minus": round(di_n, 2),
        }

    def calculate_sma(
        self, symbol: str, interval: str = "60", period: int = 20
    ) -> dict:
        """Calculates Simple Moving Average (SMA)."""
        klines = self._get_klines_safely(symbol, interval, period)
        closes = [float(k[4]) for k in reversed(klines)]
        if len(closes) < period:
            return {"status": "error", "msg": "No data"}
        return {"status": "ok", "sma": round(sum(closes) / period, 4)}

    def calculate_ema(
        self, symbol: str, interval: str = "60", period: int = 20
    ) -> dict:
        """Calculates Exponential Moving Average (EMA)."""
        klines = self._get_klines_safely(symbol, interval, period + 50)
        closes = [float(k[4]) for k in reversed(klines)]  # chronological
        if len(closes) < period:
            return {"status": "error", "msg": "No data"}
        k = 2 / (period + 1)
        ema = closes[0]
        for val in closes[1:]:
            ema = val * k + ema * (1 - k)
        return {"status": "ok", "ema": round(ema, 4)}

    def calculate_cci(
        self, symbol: str, interval: str = "60", period: int = 20
    ) -> dict:
        """Calculates Commodity Channel Index (CCI)."""
        klines = self._get_klines_safely(symbol, interval, period + 50)
        h, l, c = (
            [float(k[2]) for k in reversed(klines)],
            [float(k[3]) for k in reversed(klines)],
            [float(k[4]) for k in reversed(klines)],
        )
        tp = [
            (hi + lo + cl) / 3
            for hi, lo, cl in zip(h[-period:], l[-period:], c[-period:])
        ]
        sma = sum(tp) / period
        md = sum(abs(x - sma) for x in tp) / period
        return {
            "status": "ok",
            "cci": round((tp[-1] - sma) / (0.015 * md) if md != 0 else 0, 2),
        }

    def calculate_ichimoku(
        self,
        symbol: str,
        interval: str = "60",
        tenkan: int = 9,
        kijun: int = 26,
        senkou_b: int = 52,
    ) -> dict:
        """Calculates Ichimoku Cloud components."""
        klines = self._get_klines_safely(symbol, interval, senkou_b + 50)
        h, l = (
            [float(k[2]) for k in reversed(klines)],
            [float(k[3]) for k in reversed(klines)],
        )

        def mid(hi, lo, p):
            return (max(hi[-p:]) + min(lo[-p:])) / 2

        t, k = mid(h, l, tenkan), mid(h, l, kijun)
        return {
            "status": "ok",
            "tenkan": round(t, 4),
            "kijun": round(k, 4),
            "senkou_a": round((t + k) / 2, 4),
            "senkou_b": round(mid(h, l, senkou_b), 4),
        }

    def calculate_bollinger_bands(
        self, symbol: str, interval: str = "15", period: int = 20
    ) -> dict:
        """Calculates Bollinger Bands (Upper, Middle, Lower)."""
        klines = self._get_klines_safely(symbol, interval, period)
        closes = [float(k[4]) for k in reversed(klines)]
        if len(closes) < period:
            return {"status": "error", "msg": "No data"}
        sma = sum(closes) / period
        std = statistics.stdev(closes)
        return {
            "status": "ok",
            "upper": round(sma + 2 * std, 2),
            "middle": round(sma, 2),
            "lower": round(sma - 2 * std, 2),
        }

    def calculate_vwap(
        self, symbol: str, interval: str = "15", limit: int = 50
    ) -> dict:
        """Calculates Volume Weighted Average Price (VWAP)."""
        klines = self._get_klines_safely(symbol, interval, limit)
        pv = sum(float(k[4]) * float(k[5]) for k in klines)
        v = sum(float(k[5]) for k in klines)
        return {"status": "ok", "vwap": round(pv / v if v != 0 else 0, 4)}

    def calculate_momentum(
        self, symbol: str, interval: str = "60", period: int = 14
    ) -> dict:
        """Calculates simple momentum (Price / Price_N_periods_ago)."""
        klines = self._get_klines_safely(symbol, interval, period + 1)
        closes = [float(k[4]) for k in reversed(klines)]
        momentum = closes[-1] - closes[-period]
        return {"status": "ok", "momentum": round(momentum, 4)}

    def calculate_ehlers_rsi(
        self, symbol: str, interval: str = "60", period: int = 14
    ) -> dict:
        """Calculates Ehlers-smoothed RSI (RSI series passed through an
        exponential smoother)."""
        klines = self._get_klines_safely(symbol, interval, period + 100)
        closes = [float(k[4]) for k in reversed(klines)]
        if len(closes) < period + 1:
            return {"status": "error", "msg": "No data"}
        diffs = [closes[i + 1] - closes[i] for i in range(len(closes) - 1)]
        gains = [d if d > 0 else 0 for d in diffs]
        losses = [-d if d < 0 else 0 for d in diffs]
        avg_g = sum(gains[:period]) / period
        avg_l = sum(losses[:period]) / period
        rsi_series = []
        for i in range(period, len(gains)):
            avg_g = (avg_g * (period - 1) + gains[i]) / period
            avg_l = (avg_l * (period - 1) + losses[i]) / period
            rsi_series.append(100 if avg_l == 0 else 100 - (100 / (1 + avg_g / avg_l)))
        alpha = 2 / (period + 1)
        smooth = rsi_series[0]
        for val in rsi_series[1:]:
            smooth = val * alpha + smooth * (1 - alpha)
        return {"status": "ok", "ehler_rsi": round(smooth, 2)}

    def calculate_ehler_stochastic(
        self, symbol: str, interval: str = "60", period: int = 14
    ) -> dict:
        """Calculates Ehlers Stochastic."""
        klines = self._get_klines_safely(symbol, interval, period + 50)
        if len(klines) < period:
            return {"status": "error", "msg": "No data"}
        h, l, c = (
            [float(k[2]) for k in reversed(klines)],
            [float(k[3]) for k in reversed(klines)],
            [float(k[4]) for k in reversed(klines)],
        )
        lo, hi = min(l[-period:]), max(h[-period:])
        stoch = (c[-1] - lo) / (hi - lo) * 100 if hi != lo else 0
        return {"status": "ok", "ehler_stoch": round(stoch, 2)}

    def calculate_stochastic(
        self,
        symbol: str,
        interval: str = "15",
        period: int = 14,
        smooth_k: int = 3,
        smooth_d: int = 3,
    ) -> dict:
        """Calculates Stochastic Oscillator %K (smoothed) and %D."""
        klines = self._get_klines_safely(symbol, interval, period + smooth_k + smooth_d)
        h, l, c = (
            [float(k[2]) for k in reversed(klines)],
            [float(k[3]) for k in reversed(klines)],
            [float(k[4]) for k in reversed(klines)],
        )
        if len(c) < period:
            return {"status": "error", "msg": "No data"}
        raw_k = []
        for i in range(period - 1, len(c)):
            lo, hi = min(l[i - period + 1 : i + 1]), max(h[i - period + 1 : i + 1])
            raw_k.append((c[i] - lo) / (hi - lo) * 100 if hi != lo else 0.0)
        k = sum(raw_k[-smooth_k:]) / smooth_k if smooth_k else raw_k[-1]
        d = sum(raw_k[-smooth_d:]) / smooth_d if smooth_d else raw_k[-1]
        return {"status": "ok", "k": round(k, 2), "d": round(d, 2)}

    def calculate_hma(
        self, symbol: str, interval: str = "60", period: int = 20
    ) -> dict:
        """Calculates Hull Moving Average (HMA)."""
        klines = self._get_klines_safely(symbol, interval, period + 50)
        closes = [float(k[4]) for k in reversed(klines)]

        def wma(prices, p):
            denom = p * (p + 1) / 2
            return [
                sum(prices[i - p + 1 + j] * (j + 1) for j in range(p)) / denom
                for i in range(p - 1, len(prices))
            ]

        h_len, s_len = int(period / 2), int(math.sqrt(period))
        w_h, w_f = wma(closes, h_len), wma(closes, period)
        diff = [2 * h - f for h, f in zip(w_h[-s_len:], w_f[-s_len:])]
        hma = wma(diff, s_len)
        return {"status": "ok", "hma": round(hma[-1], 6)}

    def calculate_fractals(self, symbol: str, interval: str = "60") -> dict:
        """Calculates Williams Fractals."""
        klines = self._get_klines_safely(symbol, interval, 10)
        if len(klines) < 5:
            return {"status": "error", "msg": "No data"}
        h, l = (
            [float(k[2]) for k in reversed(klines)],
            [float(k[3]) for k in reversed(klines)],
        )
        bull = l[-3] < l[-4] and l[-3] < l[-5] and l[-3] < l[-2] and l[-3] < l[-1]
        bear = h[-3] > h[-4] and h[-3] > h[-5] and h[-3] > h[-2] and h[-3] > h[-1]
        return {"status": "ok", "bullish": bull, "bearish": bear}

    def calculate_pivot_points(self, symbol: str, interval: str = "D") -> dict:
        """Calculates standard Pivot Points from the previous completed candle."""
        klines = self._get_klines_safely(symbol, interval, 2)
        if not klines:
            return {"status": "error", "msg": "No data"}
        # klines[0] may be the still-forming candle; klines[1] is the last completed one
        src = klines[1] if len(klines) > 1 else klines[0]
        h, l, c = float(src[2]), float(src[3]), float(src[4])
        p = (h + l + c) / 3
        return {
            "status": "ok",
            "pivot": round(p, 4),
            "r1": round(2 * p - l, 4),
            "s1": round(2 * p - h, 4),
            "r2": round(p + (h - l), 4),
            "s2": round(p - (h - l), 4),
        }

    def calculate_klinger(
        self, symbol: str, interval: str = "60", fast: int = 34, slow: int = 55
    ) -> dict:
        """Calculates Klinger Volume Oscillator."""
        klines = self._get_klines_safely(symbol, interval, slow + 50)
        h, l, c, v = (
            [float(k[2]) for k in reversed(klines)],
            [float(k[3]) for k in reversed(klines)],
            [float(k[4]) for k in reversed(klines)],
            [float(k[5]) for k in reversed(klines)],
        )
        trend = [0] * len(c)
        for i in range(1, len(c)):
            trend[i] = (
                1 if c[i] > c[i - 1] else (-1 if c[i] < c[i - 1] else trend[i - 1])
            )
        vf = []
        for i in range(1, len(c)):
            dm = h[i] - l[i]
            clv = ((c[i] - l[i]) - (h[i] - c[i])) / dm if dm != 0 else 0
            vf.append(v[i] * abs(2 * clv - 1) * trend[i] * 100)

        def ema(data, p):
            k = 2 / (p + 1)
            e = [data[0]]
            for val in data[1:]:
                e.append(val * k + e[-1] * (1 - k))
            return e

        f_e, s_e = ema(vf, fast), ema(vf, slow)
        return {"status": "ok", "klinger": round(f_e[-1] - s_e[-1], 2)}

    def calculate_cmf(
        self, symbol: str, interval: str = "60", period: int = 20
    ) -> dict:
        """Calculates Chaikin Money Flow (CMF)."""
        klines = self._get_klines_safely(symbol, interval, period + 50)
        mfv, vol = [], []
        for k in reversed(klines):
            h, l, c, v = float(k[2]), float(k[3]), float(k[4]), float(k[5])
            mfv.append(((c - l) - (h - c)) / (h - l) * v if h != l else 0)
            vol.append(v)
        return {
            "status": "ok",
            "cmf": round(sum(mfv[-period:]) / sum(vol[-period:]), 4),
        }

    def calculate_adx_with_di(
        self, symbol: str, interval: str = "60", period: int = 14
    ) -> dict:
        """Calculates ADX with DI+ and DI-."""
        return self.calculate_adx(symbol, interval, period)

    def calculate_elder_ray_index(
        self, symbol: str, interval: str = "60", period: int = 13
    ) -> dict:
        """Calculates Elder Ray Index."""
        klines = self._get_klines_safely(symbol, interval, period + 50)
        h, l, c = (
            [float(k[2]) for k in reversed(klines)],
            [float(k[3]) for k in reversed(klines)],
            [float(k[4]) for k in reversed(klines)],
        )
        k = 2 / (period + 1)
        ema = c[0]
        for p in c[1:]:
            ema = p * k + ema * (1 - k)
        return {
            "status": "ok",
            "bull": round(h[-1] - ema, 4),
            "bear": round(l[-1] - ema, 4),
        }

    def calculate_kst(self, symbol: str, interval: str = "60") -> dict:
        """Calculates Know Sure Thing (KST)."""
        klines = self._get_klines_safely(symbol, interval, 100)
        c = [float(k[4]) for k in reversed(klines)]

        def roc(data, p):
            return [
                (data[i] - data[i - p]) / data[i - p] * 100 for i in range(p, len(data))
            ]

        r1, r2, r3, r4 = roc(c, 10), roc(c, 15), roc(c, 20), roc(c, 30)
        kst = (
            sum(r1[-10:]) / 10
            + sum(r2[-15:]) / 15 * 2
            + sum(r3[-20:]) / 20 * 3
            + sum(r4[-30:]) / 30 * 4
        )
        return {"status": "ok", "kst": round(kst, 4)}

    def calculate_tema(
        self, symbol: str, interval: str = "60", period: int = 20
    ) -> dict:
        """Calculates Triple Exponential Moving Average (TEMA)."""
        klines = self._get_klines_safely(symbol, interval, period + 100)
        c = [float(k[4]) for k in reversed(klines)]

        def ema_series(data, p):
            k = 2 / (p + 1)
            out = [data[0]]
            for v in data[1:]:
                out.append(v * k + out[-1] * (1 - k))
            return out

        e1 = ema_series(c, period)
        e2 = ema_series(e1, period)
        e3 = ema_series(e2, period)
        tema = 3 * e1[-1] - 3 * e2[-1] + e3[-1]
        return {"status": "ok", "tema": round(tema, 4)}

    def calculate_fisher_transform(
        self, symbol: str, interval: str = "60", period: int = 10
    ) -> dict:
        """Calculates Ehlers Fisher Transform."""
        klines = self._get_klines_safely(symbol, interval, period + 50)
        p = [(float(k[2]) + float(k[3])) / 2 for k in reversed(klines)]
        mx, mn = max(p[-period:]), min(p[-period:])

        def fish(val):
            v = 0.66 * ((val - mn) / (mx - mn) - 0.5) if mx != mn else 0
            return 0.5 * math.log((1 + v) / (1 - v)) if abs(v) < 1 else 0

        f = [fish(x) for x in p[-period:]]
        return {"status": "ok", "fisher": round(f[-1], 4)}

    def calculate_fractal_dimension(
        self, symbol: str, interval: str = "60", period: int = 30
    ) -> dict:
        """Calculates Fractal Dimension."""
        klines = self._get_klines_safely(symbol, interval, period)
        h, l = [float(k[2]) for k in klines], [float(k[3]) for k in klines]
        rng = max(h) - min(l)
        dist = sum(
            abs(float(klines[i][4]) - float(klines[i - 1][4]))
            for i in range(1, len(klines))
        )
        fd = math.log(dist / rng) / math.log(period) if rng > 0 else 1.5
        return {"status": "ok", "dimension": round(fd, 4)}

    def calculate_supertrend(
        self,
        symbol: str,
        interval: str = "60",
        period: int = 10,
        multiplier: float = 3.0,
    ) -> dict:
        """Calculates the SuperTrend indicator (standard trailing-band logic).

        Bands only widen when price closes beyond the previous band, which
        makes SuperTrend behave as a trailing stop rather than a fixed channel.
        """
        klines = self._get_klines_safely(symbol, interval, period + 50)
        if len(klines) < period + 1:
            return {"status": "error", "msg": "No data"}
        chron = list(reversed(klines))  # oldest -> newest
        h = [float(k[2]) for k in chron]
        l = [float(k[3]) for k in chron]
        c = [float(k[4]) for k in chron]

        trs = []
        for i in range(1, len(chron)):
            trs.append(max(h[i] - l[i], abs(h[i] - c[i - 1]), abs(l[i] - c[i - 1])))
        atr = sum(trs[:period]) / period

        f_upper, f_lower, trend = [], [], []
        for i in range(len(c)):
            mid = (h[i] + l[i]) / 2
            b_upper = mid + multiplier * atr
            b_lower = mid - multiplier * atr
            if i >= period:
                atr = (atr * (period - 1) + trs[i - 1]) / period
            if i == 0:
                f_upper.append(b_upper)
                f_lower.append(b_lower)
                trend.append(1)
                continue
            f_upper.append(
                b_upper
                if (b_upper < f_upper[-1] or c[i - 1] > f_upper[-1])
                else f_upper[-1]
            )
            f_lower.append(
                b_lower
                if (b_lower > f_lower[-1] or c[i - 1] < f_lower[-1])
                else f_lower[-1]
            )
            if trend[-1] == 1:
                trend.append(1 if c[i] >= f_lower[-1] else -1)
            else:
                trend.append(-1 if c[i] <= f_upper[-1] else 1)

        stop = f_lower[-1] if trend[-1] == 1 else f_upper[-1]
        return {
            "status": "ok",
            "upper": round(f_upper[-1], 4),
            "lower": round(f_lower[-1], 4),
            "trend": "Up" if trend[-1] == 1 else "Down",
            "stop": round(stop, 4),
            "price": round(c[-1], 4),
        }

    def calculate_choppiness_index(
        self, symbol: str, interval: str = "60", period: int = 14
    ) -> dict:
        """Calculates Choppiness Index."""
        klines = self._get_klines_safely(symbol, interval, period + 1)
        if len(klines) < 2:
            return {"status": "error", "msg": "No data"}
        chron = list(reversed(klines))  # chronological
        atr_s = 0.0
        for i in range(1, len(chron)):
            h, l, pc = (
                float(chron[i][2]),
                float(chron[i][3]),
                float(chron[i - 1][4]),
            )
            atr_s += max(h - l, abs(h - pc), abs(l - pc))
        hi = max(float(k[2]) for k in chron[-period:])
        lo = min(float(k[3]) for k in chron[-period:])
        chop = (
            100 * math.log10(atr_s / (hi - lo)) / math.log10(period) if hi != lo else 50
        )
        return {"status": "ok", "chop": round(chop, 2)}

    def calculate_volume_rsi(
        self, symbol: str, interval: str = "60", period: int = 14
    ) -> dict:
        """Calculates Volume-based RSI."""
        klines = self._get_klines_safely(symbol, interval, period + 1)
        v = [float(k[5]) for k in reversed(klines)]
        d = [v[i] - v[i - 1] for i in range(1, len(v))]
        u, dn = (
            sum(x for x in d if x > 0) / period,
            abs(sum(x for x in d if x < 0)) / period,
        )
        rs = u / dn if dn != 0 else 100
        return {"status": "ok", "volume_rsi": round(100 - (100 / (1 + rs)), 2)}

    def calculate_mfi(
        self, symbol: str, interval: str = "60", period: int = 14
    ) -> dict:
        """Calculates Money Flow Index (MFI)."""
        klines = self._get_klines_safely(symbol, interval, period + 1)
        d = [
            {"h": float(k[2]), "l": float(k[3]), "c": float(k[4]), "v": float(k[5])}
            for k in reversed(klines)
        ]
        tp = [(x["h"] + x["l"] + x["c"]) / 3 for x in d]
        mf = [t * x["v"] for t, x in zip(tp, d)]
        p_mf = sum(m for i, m in enumerate(mf[1:]) if tp[i + 1] > tp[i])
        n_mf = sum(m for i, m in enumerate(mf[1:]) if tp[i + 1] < tp[i])
        return {
            "status": "ok",
            "mfi": round(100 - (100 / (1 + p_mf / n_mf)) if n_mf != 0 else 100, 2),
        }

    def calculate_williams_r(
        self, symbol: str, interval: str = "15", period: int = 14
    ) -> dict:
        """Calculates Williams %R."""
        klines = self._get_klines_safely(symbol, interval, period)
        if not klines:
            return {"status": "error", "msg": "No data"}
        chron = list(reversed(klines))  # chronological
        h = [float(k[2]) for k in chron]
        l = [float(k[3]) for k in chron]
        c = float(chron[-1][4])  # most recent close
        hi, lo = max(h), min(l)
        wr = (hi - c) / (hi - lo) * -100 if hi != lo else 0
        return {"status": "ok", "williams_r": round(wr, 2)}

    def calculate_vwma(
        self, symbol: str, interval: str = "60", period: int = 20
    ) -> dict:
        """Calculates Volume Weighted Moving Average (VWMA)."""
        klines = self._get_klines_safely(symbol, interval, period)
        c, v = (
            [float(k[4]) for k in reversed(klines)],
            [float(k[5]) for k in reversed(klines)],
        )
        return {
            "status": "ok",
            "vwma": round(sum(ci * vi for ci, vi in zip(c, v)) / sum(v), 4),
        }

    def calculate_bollinger_bands_pb(
        self, symbol: str, interval: str = "15", period: int = 20
    ) -> dict:
        """Calculates Bollinger Bands %B."""
        bb = self.calculate_bollinger_bands(symbol, interval, period)
        price = float(self._get_klines_safely(symbol, interval, 1)[0][4])
        pb = (
            (price - bb["lower"]) / (bb["upper"] - bb["lower"])
            if bb["upper"] != bb["lower"]
            else 0
        )
        return {"status": "ok", "pb": round(pb, 4)}

    def calculate_roc(
        self, symbol: str, interval: str = "60", period: int = 12
    ) -> dict:
        """Calculates Rate of Change (ROC)."""
        klines = self._get_klines_safely(symbol, interval, period + 1)
        c = [float(k[4]) for k in reversed(klines)]
        return {"status": "ok", "roc": round((c[-1] - c[0]) / c[0] * 100, 2)}

    def calculate_all_indicators(self, symbol: str, interval: str = "60") -> dict:
        """Aggregates all indicators for a symbol."""
        res = {}
        for name in dir(self):
            if name.startswith("calculate_") and name != "calculate_all_indicators":
                try:
                    res[name[10:]] = getattr(self, name)(symbol, interval)
                except:
                    pass
        return {"status": "ok", "symbol": symbol, "indicators": res}

    # ══════════════════════════════════════════════════════════════════════════
    # MARKET ANALYSIS TOOLS
    # ══════════════════════════════════════════════════════════════════════════

    def get_market_regime(
        self, symbol: str, interval: str = "60", lookback: int = 100
    ) -> dict:
        """Classifies market regime (TRENDING_UP, TRENDING_DOWN, RANGING, VOLATILE)."""
        klines = self._get_klines_safely(symbol, interval, lookback)
        if len(klines) < 20:
            return {"status": "error", "msg": "No data"}
        c, h, l = (
            [float(k[4]) for k in reversed(klines)],
            [float(k[2]) for k in reversed(klines)],
            [float(k[3]) for k in reversed(klines)],
        )
        ret = [(c[i] - c[i - 1]) / c[i - 1] for i in range(1, len(c))]
        vol = statistics.stdev(ret) * 100

        def ema(data, p):
            k = 2 / (p + 1)
            e = data[0]
            for v in data[1:]:
                e = v * k + e * (1 - k)
            return e

        e_s, e_l = ema(c, 10), ema(c, 30)
        tr = [
            max(h[i] - l[i], abs(h[i] - c[i - 1]), abs(l[i] - c[i - 1]))
            for i in range(1, len(c))
        ]
        atr_p = statistics.mean(tr[-14:]) / c[-1] * 100
        reg = "RANGING"
        if vol > 2.5:
            reg = "VOLATILE"
        elif e_s > e_l * 1.001 and atr_p > 0.4:
            reg = "TRENDING_UP"
        elif e_s < e_l * 0.999 and atr_p > 0.4:
            reg = "TRENDING_DOWN"
        return {
            "status": "ok",
            "regime": reg,
            "volatility": round(vol, 4),
            "atr_pct": round(atr_p, 4),
        }

    def calculate_kalman_filter_trend(self, symbol: str, interval: str = "60") -> str:
        """Uses a Kalman Filter to smooth price data and detect trend direction."""
        klines = self._get_klines_safely(symbol, interval, 30)
        closes = [float(k[4]) for k in reversed(klines)]
        if len(closes) < 5:
            return "NEUTRAL"
        x, p = closes[0], 1.0
        for m in closes:
            k = (p + 1e-4) / (p + 1e-4 + 1e-2)
            x = x + k * (m - x)
            p = (1 - k) * (p + 1e-4)
        return "UP" if x > closes[-1] else "DOWN"

    def get_orderbook_analysis(self, symbol: str, depth: int = 50) -> dict:
        """Analyzes orderbook imbalance (OBI)."""
        bids, asks = self._orderbook_levels(self.get_orderbook(symbol, limit=depth))
        bv = sum(q for _, q in bids)
        av = sum(q for _, q in asks)
        return {
            "status": "ok",
            "obi": (bv - av) / (bv + av) if bv + av > 0 else 0,
            "bid_vol": round(bv, 2),
            "ask_vol": round(av, 2),
            "best_bid": bids[0][0] if bids else None,
            "best_ask": asks[0][0] if asks else None,
        }

    def analyze_symbol(self, symbol: str) -> dict:
        """Comprehensive symbol analysis across multiple timeframes."""
        res = {}
        for tf in ["15", "60"]:
            res[tf] = {
                "regime": self.get_market_regime(symbol, tf),
                "rsi_20": self.calculate_rsi(symbol, tf, period=20),
                "rsi_100": self.calculate_rsi(symbol, tf, period=100),
                "macd": self.calculate_macd(symbol, tf),
                "vwap": self.calculate_vwap(symbol, tf),
                "sma": self.calculate_sma(symbol, tf),
                "ema": self.calculate_ema(symbol, tf),
                "hma": self.calculate_hma(symbol, tf),
                "momentum": self.calculate_momentum(symbol, tf),
                "bb_squeeze": self.calculate_vol_weighted_bb_width(symbol, tf).get(
                    "squeeze"
                ),
                "trend": self.calculate_half_trend(symbol, tf),
            }
        return {"status": "ok", "symbol": symbol.upper(), "analysis": res}

    def scan_markets(
        self, symbols: str = "BTCUSDT,ETHUSDT,SOLUSDT", interval: str = "60"
    ) -> dict:
        """Scans multiple symbols for key metrics."""
        return {s: self.analyze_symbol(s) for s in symbols.split(",")}

    def get_volatility_regime(self, symbol: str, fast: int = 5, slow: int = 24) -> str:
        """Categorizes volatility environment."""
        f, s = (
            self.calculate_atr(symbol, "15", fast)["atr"],
            self.calculate_atr(symbol, "15", slow)["atr"],
        )
        r = f / s if s > 0 else 1.0
        return "EXPLOSIVE" if r > 1.8 else ("COMPRESSED" if r < 0.6 else "NORMAL")

    def calculate_cmo(
        self, symbol: str, interval: str = "60", period: int = 14
    ) -> dict:
        """Calculates Chande Momentum Oscillator (CMO)."""
        klines = self._get_klines_safely(symbol, interval, period + 1)
        c = [float(k[4]) for k in reversed(klines)]
        g, l = [], []
        for i in range(len(c) - 1):
            d = c[i + 1] - c[i]
            g.append(d if d > 0 else 0)
            l.append(abs(d) if d < 0 else 0)
        sg, sl = sum(g[-period:]), sum(l[-period:])
        return {
            "status": "ok",
            "cmo": round((sg - sl) / (sg + sl) * 100 if sg + sl > 0 else 0, 2),
        }

    def calculate_vol_weighted_bb_width(
        self, symbol: str, interval: str = "15", period: int = 20
    ) -> dict:
        """Calculates Volume-Weighted Bollinger Band Width."""
        bb = self.calculate_bollinger_bands(symbol, interval, period)
        w = (bb["upper"] - bb["lower"]) / bb["middle"] if bb["middle"] != 0 else 0
        return {"status": "ok", "width": round(w, 4), "squeeze": w < 0.02}

    def calculate_half_trend(
        self, symbol: str, interval: str = "60", amplitude: int = 2
    ) -> dict:
        """Half-Trend Directional Signal Filter."""
        klines = self._get_klines_safely(symbol, interval, 20)
        h, l, c = (
            [float(k[2]) for k in reversed(klines)],
            [float(k[3]) for k in reversed(klines)],
            [float(k[4]) for k in reversed(klines)],
        )
        if not c:
            return {"status": "error", "msg": "No data"}
        # Most recent candles (chronological list => the tail)
        mh = sum(h[-amplitude:]) / amplitude
        ml = sum(l[-amplitude:]) / amplitude
        return {
            "status": "ok",
            "direction": "BULLISH" if c[-1] > (mh + ml) / 2 else "BEARISH",
            "midpoint": round((mh + ml) / 2, 4),
        }

    def calculate_cvd_divergence(self, symbol: str, limit: int = 200) -> dict:
        """Detects Cumulative Volume Delta (CVD) divergence."""
        trades = self.get_recent_trades(symbol, limit=limit)
        t = trades.get("list", trades.get("result", {}).get("list", [])) or []
        delta = sum(float(x["v"]) if x["s"] == "Buy" else -float(x["v"]) for x in t)
        pc = float(self.get_ticker(symbol).get("list", [{}])[0].get("price24hPcnt", 0))
        div = "NONE"
        if pc > 0 and delta < 0:
            div = "BEARISH_DIVERGENCE"
        elif pc < 0 and delta > 0:
            div = "BULLISH_DIVERGENCE"
        return {"status": "ok", "delta": round(delta, 2), "divergence": div}

    def get_value_area_bounds(
        self, symbol: str, interval: str = "60", bins: int = 20
    ) -> dict:
        """Calculates Value Area (VAH/VAL) from Volume Profile."""
        p = self.calculate_volume_profile(symbol, interval, price_bins=bins).get(
            "profile", {}
        )
        sb = sorted(p.items(), key=lambda x: x[1], reverse=True)
        tv, av, vp = sum(p.values()), 0.0, []
        for pr, v in sb:
            av += v
            vp.append(float(pr))
            if av >= tv * 0.7:
                break
        return {
            "status": "ok",
            "vah": max(vp) if vp else 0,
            "val": min(vp) if vp else 0,
        }

    def calculate_hurst_approximation(self, symbol: str, interval: str = "15") -> dict:
        """Calculates Hurst Exponent approximation."""
        klines = self._get_klines_safely(symbol, interval, 30)
        c = [float(k[4]) for k in reversed(klines)]
        ret = [c[i] - c[i - 1] for i in range(1, len(c))]
        std, r = statistics.stdev(ret), max(c) - min(c)
        h = math.log(r / std) / math.log(len(c)) if std > 0 and r / std > 1 else 0.5
        return {
            "status": "ok",
            "hurst": round(h, 4),
            "class": "TRENDING" if h > 0.5 else "MEAN_REVERTING",
        }

    def get_orderbook_support_resistance(self, symbol: str, depth: int = 50) -> dict:
        """Parses orderbook to identify price clusters (liquidity walls) acting as S&R."""
        ob = self.get_orderbook(symbol, limit=depth)
        res = ob.get("result", ob)

        bids = [(float(p), float(q)) for p, q in res.get("b", res.get("bids", []))]
        asks = [(float(p), float(q)) for p, q in res.get("a", res.get("asks", []))]

        if not bids or not asks:
            return {"status": "error", "msg": "Empty orderbook"}

        bid_vol = sum(q for p, q in bids)
        ask_vol = sum(q for p, q in asks)

        avg_bid = bid_vol / len(bids)
        avg_ask = ask_vol / len(asks)

        # Identify walls: Volume > 3x average
        support_levels = sorted([p for p, q in bids if q > avg_bid * 3], reverse=True)
        resistance_levels = sorted([p for p, q in asks if q > avg_ask * 3])

        return {
            "status": "ok",
            "support": [round(p, 5) for p in support_levels[:5]],
            "resistance": [round(p, 5) for p in resistance_levels[:5]],
        }

    def get_supertrend_stop(self, symbol: str, side: str) -> float:
        """Returns SuperTrend stop level."""
        st = self.calculate_supertrend(symbol)
        return float(st["lower"] if side == "Buy" else st["upper"])

    def calculate_orderbook_entry(
        self, symbol: str, side: str, depth: int = 50
    ) -> dict:
        """Calculates optimal entry price based on orderbook imbalance and walls."""
        ob = self.get_orderbook(symbol, limit=depth)
        res = ob.get("result", ob)

        bids_raw = res.get("b", res.get("bids", []))
        asks_raw = res.get("a", res.get("asks", []))

        bids = []
        for item in bids_raw:
            try:
                bids.append((float(item[0]), float(item[1])))
            except:
                continue

        asks = []
        for item in asks_raw:
            try:
                asks.append((float(item[0]), float(item[1])))
            except:
                continue

        if not bids or not asks:
            return {"status": "error", "msg": "Empty orderbook"}

        bid_vol = sum(q for p, q in bids)
        ask_vol = sum(q for p, q in asks)
        obi = (
            (bid_vol - ask_vol) / (bid_vol + ask_vol) if (bid_vol + ask_vol) > 0 else 0
        )

        avg_bid = bid_vol / len(bids)
        avg_ask = ask_vol / len(asks)
        bid_walls = [p for p, q in bids if q > avg_bid * 2.5]
        ask_walls = [p for p, q in asks if q > avg_ask * 2.5]

        side_norm = side.lower()
        if side_norm in ["buy", "long"]:
            suggested = bid_walls[0] if bid_walls else bids[0][0]
            # If OBI is very positive, we want to be more aggressive (entry at best bid)
            if obi > 0.4:
                suggested = bids[0][0]
            # If OBI is negative, we might wait for a deeper wall
            elif obi < -0.2 and len(bid_walls) > 1:
                suggested = bid_walls[1]
            return {
                "status": "ok",
                "suggested_entry": suggested,
                "obi": round(obi, 4),
                "bid_walls": bid_walls[:5],
            }
        else:
            suggested = ask_walls[0] if ask_walls else asks[0][0]
            if obi < -0.4:
                suggested = asks[0][0]
            elif obi > 0.2 and len(ask_walls) > 1:
                suggested = ask_walls[1]
            return {
                "status": "ok",
                "suggested_entry": suggested,
                "obi": round(obi, 4),
                "ask_walls": ask_walls[:5],
            }

    def analyze_orderbook_for_profit(
        self,
        symbol: str,
        side: str,
        desired_profit: float,
        current_price: float,
        qty: float,
        depth: int = 50,
    ) -> dict:
        """Analyzes orderbook for viable entry/exit prices based on target profit."""
        ob = self.get_orderbook(symbol, limit=depth)
        result = ob.get("result", ob)

        # Bybit orderbook returns "b" and "a" as list of [price, qty] strings
        bids_raw = result.get("b", result.get("bids", []))
        asks_raw = result.get("a", result.get("asks", []))

        # Parse the [price, qty] pairs - Bybit returns them as strings
        bids = []
        for item in bids_raw[:25]:
            if isinstance(item, list) and len(item) >= 2:
                try:
                    bids.append((float(item[0]), float(item[1])))
                except (ValueError, TypeError):
                    continue
        bids.sort(key=lambda x: x[0], reverse=True)

        asks = []
        for item in asks_raw[:25]:
            if isinstance(item, list) and len(item) >= 2:
                try:
                    asks.append((float(item[0]), float(item[1])))
                except (ValueError, TypeError):
                    continue
        asks.sort(key=lambda x: x[0])

        if side.lower() == "buy":
            # Looking for ask prices to exit (sell) higher.
            targets = [
                price
                for price, vol in asks
                if (price - current_price) * qty >= desired_profit
            ]
            if not targets:
                targets = [price for price, vol in asks[:5]]
            return {
                "status": "ok",
                "suggested_exit_prices": targets,
                "liquidity_at_targets": [q for p, q in asks if p in targets],
            }
        else:
            # Looking for bid prices to exit (buy) lower.
            targets = [
                price
                for price, vol in bids
                if (current_price - price) * qty >= desired_profit
            ]
            if not targets:
                targets = [price for price, vol in bids[:5]]
            return {
                "status": "ok",
                "suggested_exit_prices": targets,
                "liquidity_at_targets": [q for p, q in bids if p in targets],
            }

    def check_funding_rate_impact(self, symbol: str, threshold: float = 0.01) -> bool:
        """Checks if |funding rate| is above `threshold` (percent, e.g. 0.01 = 0.01%)."""
        fr = float(self.get_ticker(symbol).get("list", [{}])[0].get("fundingRate", 0))
        return abs(fr) * 100 >= float(threshold)

    def get_spot_futures_basis(self, symbol_spot: str, symbol_linear: str) -> dict:
        """Calculates Spot-Futures Basis."""
        ps = float(
            self.get_ticker(symbol_spot, "spot")
            .get("list", [{}])[0]
            .get("lastPrice", 0)
        )
        pf = float(
            self.get_ticker(symbol_linear, "linear")
            .get("list", [{}])[0]
            .get("lastPrice", 0)
        )
        return {"basis_pct": round((pf - ps) / ps * 100, 4) if ps > 0 else 0}

    def get_cointegrated_spread(self, symbol_a: str, symbol_b: str) -> dict:
        """Calculates z-score of spread between two symbols."""
        ka, kb = (
            self._get_klines_safely(symbol_a, "15", 50),
            self._get_klines_safely(symbol_b, "15", 50),
        )
        ca, cb = (
            [float(k[4]) for k in reversed(ka)],
            [float(k[4]) for k in reversed(kb)],
        )
        r = [a / b for a, b in zip(ca, cb)]
        m, s = statistics.mean(r), statistics.stdev(r)
        return {"z_score": round((r[-1] - m) / s, 2) if s > 0 else 0}

    def calculate_short_squeeze_risk(self, symbol: str) -> dict:
        """Assess risk of a short squeeze."""
        oi_res = self.get_open_interest(symbol, limit=10)
        oi = oi_res.get("list", oi_res.get("result", {}).get("list", [])) or []
        if len(oi) < 2:
            return {"status": "ok", "squeeze_risk": "LOW", "oi_change": 0}
        # Sort by timestamp so oi[-1] is the latest reading regardless of API order
        oi_sorted = sorted(oi, key=lambda x: int(x.get("timestamp", 0)))
        oi_c = float(oi_sorted[-1].get("openInterest", 0))
        oi_p = float(oi_sorted[-2].get("openInterest", 0))
        pc = float(self.get_ticker(symbol).get("list", [{}])[0].get("price24hPcnt", 0))
        risk = "HIGH" if pc > 0.02 and oi_c < oi_p else "NORMAL"
        return {
            "status": "ok",
            "squeeze_risk": risk,
            "oi_change_pct": round((oi_c - oi_p) / oi_p * 100, 2) if oi_p else 0,
        }

    def get_scalper_signal(self, symbol: str, depth: int = 15) -> str:
        """OBI-based scalping signal."""
        obi = self.get_orderbook_analysis(symbol, depth)["obi"]
        return (
            "BUY_MOMENTUM"
            if obi > 0.35
            else ("SELL_MOMENTUM" if obi < -0.35 else "STAY_FLAT")
        )

    def get_vwap_cross_state(self, symbol: str, interval: str = "15") -> str:
        """Checks price relative to VWAP."""
        v = self.calculate_vwap(symbol, interval)["vwap"]
        p = float(self.get_ticker(symbol).get("list", [{}])[0].get("lastPrice", 0))
        return "CROSS_UP" if p > v else "CROSS_DOWN"

    def check_trend_confluence(self, symbol: str) -> str:
        """Checks trend alignment across multiple timeframes."""
        r1, r2, r3 = (
            self.get_market_regime(symbol, "15")["regime"],
            self.get_market_regime(symbol, "60")["regime"],
            self.get_market_regime(symbol, "240")["regime"],
        )
        if r1 == r2 == r3 == "TRENDING_UP":
            return "STRONG_BUY"
        if r1 == r2 == r3 == "TRENDING_DOWN":
            return "STRONG_SELL"
        return "DIVERGENT"

    def calculate_support_resistance_levels(
        self,
        symbol: str,
        interval: str = "60",
        depth: int = 50,
        wall_multiplier: float = 3.0,
    ) -> dict:
        """Identifies support/resistance based on liquidity walls, swing points, and classic pivots."""
        # 1. Get Orderbook for walls
        raw_ob = self.get_orderbook(symbol=symbol, limit=depth)
        res = raw_ob.get("result", raw_ob)
        bids = [
            {"p": float(p), "v": float(q)} for p, q in res.get("b", res.get("bids", []))
        ]
        asks = [
            {"p": float(p), "v": float(q)} for p, q in res.get("a", res.get("asks", []))
        ]

        if not bids or not asks:
            return {"status": "error", "msg": "Empty orderbook"}

        # 2. Get Historical Price Action
        klines = self.get_klines(symbol=symbol, interval=interval, limit=100).get(
            "list", []
        )
        if not klines:
            return {"status": "error", "msg": "No kline data"}
        highs = [float(k[2]) for k in klines]
        lows = [float(k[3]) for k in klines]
        closes = [float(k[4]) for k in klines]

        # 3. Detect Walls
        bid_avg = sum(b["v"] for b in bids) / len(bids)
        ask_avg = sum(a["v"] for a in asks) / len(asks)
        walls_sup = [b["p"] for b in bids if b["v"] > bid_avg * wall_multiplier]
        walls_res = [a["p"] for a in asks if a["v"] > ask_avg * wall_multiplier]

        # 4. Classic Pivot Points
        prev_h, prev_l, prev_c = highs[1], lows[1], closes[1]
        pivot = (prev_h + prev_l + prev_c) / 3
        r1, s1 = 2 * pivot - prev_l, 2 * pivot - prev_h

        # 5. Detect Confluence
        swing_highs = [
            highs[i]
            for i in range(2, len(highs) - 2)
            if highs[i] > highs[i - 1] and highs[i] > highs[i + 1]
        ]
        swing_lows = [
            lows[i]
            for i in range(2, len(lows) - 2)
            if lows[i] < lows[i - 1] and lows[i] < lows[i + 1]
        ]

        historical_points = swing_highs + swing_lows + [pivot, r1, s1]

        def get_confluence(levels, historical_points, tolerance=0.005):
            confluent = []
            for lvl in levels:
                score = 0
                for pt in historical_points:
                    if abs(lvl - pt) / pt < tolerance:
                        score += 1
                confluent.append({"price": round(lvl, 5), "score": score})
            return sorted(confluent, key=lambda x: x["score"], reverse=True)

        return {
            "status": "ok",
            "support": get_confluence(walls_sup, swing_lows + [s1]),
            "resistance": get_confluence(walls_res, swing_highs + [r1]),
            "pivots": {
                "pivot": round(pivot, 5),
                "r1": round(r1, 5),
                "s1": round(s1, 5),
            },
        }

    def calculate_fibonacci_levels(self, symbol: str, lookback: int = 50) -> dict:
        """Calculates Fibonacci levels."""
        k = self.get_klines(symbol, "60", limit=lookback).get("list", [])
        h, l = max(float(x[2]) for x in k), min(float(x[3]) for x in k)
        d = h - l
        return {
            f"{p}%": round(h - (p / 100) * d, 4)
            for p in [0, 23.6, 38.2, 50, 61.8, 78.6, 100]
        }

    def calculate_volume_profile(
        self, symbol: str, interval: str = "60", limit: int = 100, price_bins: int = 20
    ) -> dict:
        """Calculates Volume Profile."""
        k = self.get_klines(symbol, interval, limit=limit).get("list", [])
        c, v = [float(x[4]) for x in reversed(k)], [float(x[5]) for x in reversed(k)]
        hi, lo = max(c), min(c)
        bs = (hi - lo) / price_bins if hi != lo else 1.0
        prof = dict.fromkeys(range(price_bins), 0.0)
        for cp, vp in zip(c, v):
            idx = min(max(int((cp - lo) / bs), 0), price_bins - 1)
            prof[idx] += vp
        return {
            "profile": {round(lo + i * bs, 4): round(vol, 2) for i, vol in prof.items()}
        }

    def calculate_order_flow_imbalance(self, symbol: str, window: int = 10) -> dict:
        """Calculates OFI."""
        k = self.get_klines(symbol, "60", limit=window + 1).get("list", [])
        o, c, v = (
            [float(x[1]) for x in reversed(k)],
            [float(x[4]) for x in reversed(k)],
            [float(x[5]) for x in reversed(k)],
        )
        return {
            "imbalance": round(sum((cp - op) * vp for op, cp, vp in zip(o, c, v)), 4)
        }

    def calculate_liquidity_pools(self, symbol: str, threshold: float = 0.05) -> dict:
        """Identifies high volume price levels."""
        p = self.calculate_volume_profile(symbol)["profile"]
        mv = max(p.values()) if p else 0
        return {"pools": {pr: v for pr, v in p.items() if v > threshold * mv}}

    def calculate_market_depth_profile(
        self, symbol: str, distance_pcts: List[float] = [0.1, 0.5, 1.0]
    ) -> dict:
        """Aggregates OB volume at % distances."""
        bids, asks = self._orderbook_levels(self.get_orderbook(symbol, limit=200))
        b = [{"p": p, "v": q} for p, q in bids]
        a = [{"p": p, "v": q} for p, q in asks]
        m = (b[0]["p"] + a[0]["p"]) / 2 if b and a else 0
        res = {}
        for pct in distance_pcts:
            bv = sum(x["v"] for x in b if x["p"] >= m * (1 - pct / 100))
            av = sum(x["v"] for x in a if x["p"] <= m * (1 + pct / 100))
            res[f"{pct}%"] = {"bid": round(bv, 2), "ask": round(av, 2)}
        return {"status": "ok", "profile": res}

    def check_liquidity_sweep_and_wait(self, symbol: str) -> bool:
        """Monitors for liquidity sweeps (wicks)."""
        k = self._get_klines_safely(symbol, "5", 1)
        if not k:
            return False
        o, h, l, c = float(k[0][1]), float(k[0][2]), float(k[0][3]), float(k[0][4])
        if abs(c - o) > 0 and (min(o, c) - l) > abs(c - o) * 3:
            time.sleep(1.5)
            return True
        return False

    def is_maker_scalp_viable(self, symbol: str, fee: float = 0.0002) -> bool:
        """Checks if spread covers fees."""
        bids, asks = self._orderbook_levels(self.get_orderbook(symbol, limit=1))
        if not bids or not asks:
            return False
        s = (asks[0][0] - bids[0][0]) / bids[0][0]
        return s > (fee * 2.5)

    def route_strategy_by_regime(self, symbol: str) -> str:
        """Routes strategy based on Hurst exponent."""
        h = self.calculate_hurst_approximation(symbol)["hurst"]
        return (
            "TREND_FOLLOWING"
            if h > 0.55
            else ("MEAN_REVERSION" if h < 0.45 else "NEUTRAL")
        )

    def check_orderbook_wall_obstruction(self, symbol: str, side: str) -> bool:
        """Checks for large walls blocking entry."""
        a = self.get_orderbook_analysis(symbol, 10)
        bv, av = a["bid_vol"], a["ask_vol"]
        return not (
            (side == "Buy" and av > bv * 2.5) or (side == "Sell" and bv > av * 2.5)
        )
