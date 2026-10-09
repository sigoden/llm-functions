from typing import Any, Callable, Dict, List, Optional


def get_closes(klines: List[Any]) -> List[float]:
    return [float(k[4]) for k in reversed(klines)]


def calculate_macd(
    symbol: str,
    interval: str = "60",
    fast: int = 12,
    slow: int = 26,
    signal: int = 9,
    klines: Optional[list] = None,
    get_klines_func: Optional[Callable] = None,
) -> Dict[str, Any]:
    if klines is None:
        if get_klines_func is None:
            return {"status": "error", "msg": "No klines or get_klines_func provided"}
        klines = get_klines_func(symbol=symbol, interval=interval, limit=200).get(
            "list", []
        )

    if not klines or len(klines) < slow:
        return {"status": "error", "msg": "Insufficient data"}

    closes = get_closes(klines)

    def get_ema(data, p):
        if not data:
            return 0
        k = 2 / (p + 1)
        ema = data[0]
        for val in data[1:]:
            ema = val * k + ema * (1 - k)
        return ema

    macd = get_ema(closes, fast) - get_ema(closes, slow)
    return {"status": "ok", "macd": round(macd, 4)}


def calculate_rsi(
    symbol: str,
    interval: str = "60",
    period: int = 14,
    klines: Optional[list] = None,
    get_klines_func: Optional[Callable] = None,
) -> Dict[str, Any]:
    if klines is None:
        if get_klines_func is None:
            return {"status": "error", "msg": "No klines or get_klines_func provided"}
        klines = get_klines_func(
            symbol=symbol, interval=interval, limit=period + 50
        ).get("list", [])

    closes = get_closes(klines)
    if len(closes) < period + 1:
        return {"status": "error", "msg": "Insufficient data"}

    deltas = [closes[i + 1] - closes[i] for i in range(len(closes) - 1)]
    gains = [d if d > 0 else 0 for d in deltas]
    losses = [-d if d < 0 else 0 for d in deltas]
    avg_gain = sum(gains[-period:]) / period
    avg_loss = sum(losses[-period:]) / period
    if avg_loss == 0:
        return {"status": "ok", "rsi": 100.0}
    rs = avg_gain / avg_loss
    return {"status": "ok", "rsi": round(100 - (100 / (1 + rs)), 2)}


def calculate_ema(
    symbol: str,
    interval: str = "60",
    period: int = 20,
    klines: Optional[list] = None,
    get_klines_func: Optional[Callable] = None,
) -> Dict[str, Any]:
    if klines is None:
        if get_klines_func is None:
            return {"status": "error", "msg": "No klines or get_klines_func provided"}
        klines = get_klines_func(
            symbol=symbol, interval=interval, limit=period + 50
        ).get("list", [])

    closes = get_closes(klines)
    if len(closes) < period:
        return {"status": "error", "msg": "Insufficient data"}

    k = 2 / (period + 1)
    ema = closes[0]
    for p in closes[1:]:
        ema = p * k + ema * (1 - k)
    return {"status": "ok", "ema": round(ema, 2)}


def run(
    indicator: str = "rsi",
    symbol: str = "BTCUSDT",
    interval: str = "60",
    period: int = 14,
):
    """Calculate a technical indicator.

    Args:
        indicator: One of 'rsi', 'macd', 'ema'
        symbol: Trading symbol
        interval: Kline interval
        period: Calculation period
    """
    import json

    funcs = {
        "rsi": lambda: calculate_rsi(symbol, interval, period),
        "macd": lambda: calculate_macd(symbol, interval),
        "ema": lambda: calculate_ema(symbol, interval, period),
    }
    fn = funcs.get(indicator)
    if fn is None:
        print(
            json.dumps(
                {
                    "status": "error",
                    "msg": f"Unknown indicator: {indicator}. Choose from: {list(funcs.keys())}",
                }
            )
        )
    else:
        print(json.dumps(fn(), indent=2))


if __name__ == "__main__":
    import sys

    run(*sys.argv[1:])
