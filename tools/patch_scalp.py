with open("micro_scalp.py") as f:
    content = f.read()

# Add imports
content = content.replace(
    "import sys",
    "import sys\nfrom statistics import stdev\nfrom collections import deque",
)

# Fix 1: calculate_optimal_qty
calc_qty_code = """
def calculate_optimal_qty(market: Dict[str, Any], base_qty: float, target_profit: float, volatility_window: int = 20) -> float:
    if len(market.get('closes', [])) < volatility_window:
        return base_qty

    closes = market['closes'][-volatility_window:]
    returns = [(closes[i] - closes[i-1]) / closes[i-1] for i in range(1, len(closes))]
    vol = stdev(returns) if returns else 0.001

    vol_multiplier = max(0.5, min(1.5, 0.001 / max(vol, 0.0001)))

    profit_multiplier = 1.0
    if target_profit <= 0.05:
        profit_multiplier = 1.5
    elif target_profit <= 0.10:
        profit_multiplier = 1.2

    optimal_qty = base_qty * vol_multiplier * profit_multiplier
    return round(optimal_qty, 8)

"""

# Fix 2: evaluate_signal_v2
eval_sig_v2 = """
def evaluate_signal_v2(market: Dict[str, Any], qty: float, target_profit: float,
                       maker_fee: float, volume_profile: Optional[Dict] = None) -> Optional[Dict[str, Any]]:
    best_bid = market["best_bid"]
    best_ask = market["best_ask"]
    imbalance = market["imbalance"]
    momentum = market["momentum"]
    tick_size = market["tick_size"]

    volume_multiplier = 1.0
    if volume_profile:
        avg_volume = volume_profile.get('avg_volume', 1)
        current_volume = volume_profile.get('current_volume', 1)
        if current_volume > avg_volume * 1.5:
            volume_multiplier = 1.3
        elif current_volume < avg_volume * 0.5:
            volume_multiplier = 0.7

    momentum_long = MOMENTUM_LONG * volume_multiplier
    momentum_short = MOMENTUM_SHORT * volume_multiplier
    imbalance_long = IMBALANCE_LONG * volume_multiplier
    imbalance_short = IMBALANCE_SHORT * volume_multiplier

    side: Optional[str] = None
    entry_price = 0.0
    exit_price = 0.0

    if momentum > momentum_long and imbalance > imbalance_long:
        side = "Buy"
        entry_price = best_bid
        exit_price = optimize_take_profit(market, entry_price, side, target_profit, qty, maker_fee)
        exit_price = round_to_tick(exit_price, tick_size)
    elif momentum < momentum_short and imbalance < imbalance_short:
        side = "Sell"
        entry_price = best_ask
        exit_price = optimize_take_profit(market, entry_price, side, target_profit, qty, maker_fee)
        exit_price = round_to_tick(exit_price, tick_size)

    if not side:
        return None

    confidence = abs(momentum) / abs(momentum_long) * 0.5 + abs(imbalance) / abs(imbalance_long) * 0.5

    return {
        "side": side,
        "entry_price": entry_price,
        "exit_price": exit_price,
        "momentum": momentum,
        "imbalance": imbalance,
        "tick_size": tick_size,
        "confidence": min(1.0, confidence),
        "volume_multiplier": volume_multiplier,
    }

def optimize_take_profit(market: Dict[str, Any], entry_price: float, side: str,
                         target_profit: float, qty: float, maker_fee: float) -> float:
    if side == "Buy":
        ask_price = market.get('best_ask', entry_price * 1.01)
        targets = [
            entry_price * (1 + 0.001),
            entry_price * (1 + 0.002),
            entry_price * (1 + 0.003),
        ]
        for target in targets:
            if target <= ask_price:
                continue
            estimated_profit = (target - entry_price) * qty
            if estimated_profit >= target_profit:
                return target

        entry_fee = entry_price * qty * maker_fee
        raw_exit = (target_profit + entry_fee + entry_price * qty) / (qty * (1 - maker_fee))
        return raw_exit
    else:
        bid_price = market.get('best_bid', entry_price * 0.99)
        targets = [
            entry_price * (1 - 0.001),
            entry_price * (1 - 0.002),
            entry_price * (1 - 0.003),
        ]
        for target in targets:
            if target >= bid_price:
                continue
            estimated_profit = (entry_price - target) * qty
            if estimated_profit >= target_profit:
                return target

        entry_fee = entry_price * qty * maker_fee
        raw_exit = ((entry_price * qty) - entry_fee - target_profit) / (qty * (1 + maker_fee))
        return raw_exit
"""

# Fix 3: calculate_adaptive_stop_loss
adaptive_sl = """
def calculate_adaptive_stop_loss(entry_price: float, side: str, market_volatility: float,
                                 tick_size: float, base_stop_distance: float = 0.002) -> Tuple[float, float]:
    vol_multiplier = 1.0
    if market_volatility > 0.005:
        vol_multiplier = 1.5
    elif market_volatility < 0.001:
        vol_multiplier = 0.7

    adjusted_distance = base_stop_distance * vol_multiplier

    if side == "Buy":
        stop_loss_price = round_to_tick(entry_price * (1.0 - adjusted_distance), tick_size)
    else:
        stop_loss_price = round_to_tick(entry_price * (1.0 + adjusted_distance), tick_size)

    return stop_loss_price, adjusted_distance
"""

# Fix 4: MultiTimeframeAnalyzer
multi_tf = """
class MultiTimeframeAnalyzer:
    def __init__(self, max_frames: int = 3):
        self.timeframes = {
            '1m': deque(maxlen=60),
            '5m': deque(maxlen=12),
            '15m': deque(maxlen=4),
        }

    def update(self, close_price: float, timestamp: float):
        current_minute = int(timestamp / 60)
        self.timeframes['1m'].append((current_minute, close_price))
        if current_minute % 5 == 0:
            self.timeframes['5m'].append((current_minute // 5, close_price))
        if current_minute % 15 == 0:
            self.timeframes['15m'].append((current_minute // 15, close_price))

    def get_trend_signal(self) -> Tuple[float, str]:
        scores = []
        for tf_name, data in self.timeframes.items():
            if len(data) < 2:
                continue
            prices = [p[1] for p in data]
            if len(prices) >= 2:
                sma = sum(prices) / len(prices)
                current_price = prices[-1]
                tf_score = (current_price - sma) / sma
                scores.append(tf_score)

        if not scores:
            return 0.0, 'neutral'

        avg_score = sum(scores) / len(scores)

        if avg_score > 0.005:
            return min(1.0, avg_score * 100), 'bullish'
        elif avg_score < -0.005:
            return min(1.0, abs(avg_score) * 100), 'bearish'
        else:
            return 0.0, 'neutral'
"""

# Inject after build_market_snapshot
content = content.replace(
    "def get_market_data_rest(",
    multi_tf
    + "\n"
    + calc_qty_code
    + "\n"
    + eval_sig_v2
    + "\n"
    + adaptive_sl
    + "\ndef get_market_data_rest(",
)

# Now patch run_one_cycle
# 1. qty = args["qty"] -> qty = calculate_optimal_qty(market, args["qty"], target_profit)
# BUT only after `market` is fetched.
# Current code has `qty = args["qty"]` at the start of run_one_cycle.
# Let's replace it:
content = content.replace('    qty = args["qty"]', '    base_qty = args["qty"]')

# We need to compute qty after `market` is available.
market_fetch_str = """
    spread_bps = market["spread_bps"]
"""
market_fetch_replace = """
    qty = calculate_optimal_qty(market, base_qty, target_profit)
    spread_bps = market["spread_bps"]
"""
content = content.replace(market_fetch_str, market_fetch_replace)

# 2. signal evaluation + MultiTimeframeAnalyzer + volume_profile
old_sig_eval = """
    sig = evaluate_signal(market, qty, target_profit, maker_fee)
    if not sig:
"""

new_sig_eval = """
    if 'multi_tf_analyzer' not in loop_state:
        loop_state['multi_tf_analyzer'] = MultiTimeframeAnalyzer()

    if market.get('closes'):
        loop_state['multi_tf_analyzer'].update(market['closes'][-1], time.time())

    trend_score, trend_direction = loop_state['multi_tf_analyzer'].get_trend_signal()

    volume_profile = {
        'avg_volume': sum(market.get('bid_vol', 0) + market.get('ask_vol', 0) for _ in range(5)) / 5,
        'current_volume': market.get('bid_vol', 0) + market.get('ask_vol', 0)
    }

    if trend_score > 0.5:
        sig = evaluate_signal_v2(market, qty, target_profit, maker_fee, volume_profile)
        if sig:
            sig['confidence'] *= 1.2
    elif trend_direction == 'neutral':
        sig = evaluate_signal_v2(market, qty * 0.8, target_profit, maker_fee, volume_profile)
    else:
        sig = None

    if not sig:
"""
content = content.replace(old_sig_eval, new_sig_eval)

# 3. Trailing stop replacement
old_trailing_stop = """
    if trailing_stop is not None and trailing_stop is not True and str(trailing_stop).strip() != "":
        trailing_distance = float(trailing_stop)
        if trailing_distance <= 0:
            emit_result({"status": "error", "iteration": iteration, "message": "trailing-stop must be positive"})
            return
        if side == "Buy":
            stop_loss_price = round_to_tick(entry_price * (1.0 - trailing_distance), tick_size)
        else:
            stop_loss_price = round_to_tick(entry_price * (1.0 + trailing_distance), tick_size)
"""

new_trailing_stop = """
    if trailing_stop is not None and trailing_stop is not True and str(trailing_stop).strip() != "":
        trailing_distance = float(trailing_stop)
        if trailing_distance <= 0:
            emit_result({"status": "error", "iteration": iteration, "message": "trailing-stop must be positive"})
            return

        if len(market.get('closes', [])) >= 5:
            recent_prices = market['closes'][-5:]
            price_changes = [abs(recent_prices[i] - recent_prices[i-1]) / recent_prices[i-1]
                            for i in range(1, len(recent_prices))]
            market_volatility = sum(price_changes) / len(price_changes) if price_changes else 0.002
        else:
            market_volatility = 0.002

        stop_loss_price, adjusted_distance = calculate_adaptive_stop_loss(
            entry_price, side, market_volatility, tick_size, trailing_distance
        )
"""
content = content.replace(old_trailing_stop, new_trailing_stop)

with open("micro_scalp.py", "w") as f:
    f.write(content)
