#!/usr/bin/env python3
"""
Technical Indicators Library (Pure Python)
A comprehensive set of 25+ technical indicators for market analysis.
"""

import math
from typing import Dict, List, Optional, Tuple

# --- Moving Averages ---


def sma(data: List[float], period: int) -> List[Optional[float]]:
    if len(data) < period:
        return [None] * len(data)
    results = [None] * (period - 1)
    for i in range(period, len(data) + 1):
        results.append(sum(data[i - period : i]) / period)
    return results


def ema(data: List[float], period: int) -> List[Optional[float]]:
    if len(data) < period:
        return [None] * len(data)
    alpha = 2 / (period + 1)
    results = [None] * (period - 1)
    # Start with SMA for the first EMA value
    current_ema = sum(data[:period]) / period
    results.append(current_ema)
    for price in data[period:]:
        current_ema = (price - current_ema) * alpha + current_ema
        results.append(current_ema)
    return results


def wma(data: List[float], period: int) -> List[Optional[float]]:
    if len(data) < period:
        return [None] * len(data)
    weights = list(range(1, period + 1))
    weight_sum = sum(weights)
    results = [None] * (period - 1)
    for i in range(period, len(data) + 1):
        window = data[i - period : i]
        weighted_val = sum(window[j] * weights[j] for j in range(period)) / weight_sum
        results.append(weighted_val)
    return results


def hma(data: List[float], period: int) -> List[Optional[float]]:
    """Hull Moving Average"""
    if len(data) < period:
        return [None] * len(data)
    half_period = period // 2
    sqrt_period = int(math.sqrt(period))

    wma_half = wma(data, half_period)
    wma_full = wma(data, period)

    diff = []
    for wh, wf in zip(wma_half, wma_full):
        if wh is not None and wf is not None:
            diff.append(2 * wh - wf)
        else:
            diff.append(None)

    # Remove leading Nones for the final WMA
    clean_diff = [x for x in diff if x is not None]
    if len(clean_diff) < sqrt_period:
        return [None] * len(data)

    final_hma_part = wma(clean_diff, sqrt_period)
    return [None] * (len(data) - len(final_hma_part)) + final_hma_part


# --- Oscillators ---


def rsi(data: List[float], period: int = 14) -> List[Optional[float]]:
    if len(data) <= period:
        return [None] * len(data)
    deltas = [data[i + 1] - data[i] for i in range(len(data) - 1)]
    gains = [d if d > 0 else 0 for d in deltas]
    losses = [-d if d < 0 else 0 for d in deltas]

    avg_gain = sum(gains[:period]) / period
    avg_loss = sum(losses[:period]) / period

    results = [None] * period
    if avg_loss == 0:
        results.append(100.0)
    else:
        rs = avg_gain / avg_loss
        results.append(100 - (100 / (1 + rs)))

    for i in range(period, len(deltas)):
        avg_gain = (avg_gain * (period - 1) + gains[i]) / period
        avg_loss = (avg_loss * (period - 1) + losses[i]) / period
        if avg_loss == 0:
            results.append(100.0)
        else:
            rs = avg_gain / avg_loss
            results.append(100 - (100 / (1 + rs)))
    return results


def stoch(
    highs: List[float],
    lows: List[float],
    closes: List[float],
    k_period: int = 14,
    d_period: int = 3,
) -> Tuple[List[Optional[float]], List[Optional[float]]]:
    if len(closes) < k_period:
        return [None] * len(closes), [None] * len(closes)
    k_vals = [None] * (k_period - 1)
    for i in range(k_period, len(closes) + 1):
        window_lows = lows[i - k_period : i]
        window_highs = highs[i - k_period : i]
        lowest_low = min(window_lows)
        highest_high = max(window_highs)
        if highest_high == lowest_low:
            k_vals.append(100.0)
        else:
            k_vals.append(
                100 * (closes[i - 1] - lowest_low) / (highest_high - lowest_low)
            )

    # D is SMA of K
    clean_k = [x if x is not None else 0 for x in k_vals]
    d_vals = sma(clean_k, d_period)
    return k_vals, d_vals


def macd(
    data: List[float], fast: int = 12, slow: int = 26, signal: int = 9
) -> Tuple[List[Optional[float]], List[Optional[float]], List[Optional[float]]]:
    ema_fast = ema(data, fast)
    ema_slow = ema(data, slow)
    macd_line = []
    for f, s in zip(ema_fast, ema_slow):
        if f is not None and s is not None:
            macd_line.append(f - s)
        else:
            macd_line.append(None)

    clean_macd = [x if x is not None else 0 for x in macd_line]
    signal_line = ema(clean_macd, signal)
    histogram = []
    for m, s in zip(macd_line, signal_line):
        if m is not None and s is not None:
            histogram.append(m - s)
        else:
            histogram.append(None)
    return macd_line, signal_line, histogram


def williams_r(
    highs: List[float], lows: List[float], closes: List[float], period: int = 14
) -> List[Optional[float]]:
    if len(closes) < period:
        return [None] * len(closes)
    results = [None] * (period - 1)
    for i in range(period, len(closes) + 1):
        hh = max(highs[i - period : i])
        ll = min(lows[i - period : i])
        if hh == ll:
            results.append(-50.0)
        else:
            results.append(-100 * (hh - closes[i - 1]) / (hh - ll))
    return results


def cci(
    highs: List[float], lows: List[float], closes: List[float], period: int = 20
) -> List[Optional[float]]:
    if len(closes) < period:
        return [None] * len(closes)
    tp = [(h + l + c) / 3 for h, l, c in zip(highs, lows, closes)]
    tp_sma = sma(tp, period)
    results = [None] * (period - 1)
    for i in range(period, len(tp) + 1):
        window = tp[i - period : i]
        avg = tp_sma[i - 1]
        mad = sum(abs(x - avg) for x in window) / period
        if mad == 0:
            results.append(0.0)
        else:
            results.append((tp[i - 1] - avg) / (0.015 * mad))
    return results


def mfi(
    highs: List[float],
    lows: List[float],
    closes: List[float],
    volumes: List[float],
    period: int = 14,
) -> List[Optional[float]]:
    if len(closes) <= period:
        return [None] * len(closes)
    tp = [(h + l + c) / 3 for h, l, c in zip(highs, lows, closes)]
    mf = [tp[i] * volumes[i] for i in range(len(tp))]
    results = [None] * period
    for i in range(period, len(tp)):
        pos_mf, neg_mf = 0.0, 0.0
        for j in range(i - period + 1, i + 1):
            if tp[j] > tp[j - 1]:
                pos_mf += mf[j]
            else:
                neg_mf += mf[j]
        if neg_mf == 0:
            results.append(100.0)
        else:
            mfr = pos_mf / neg_mf
            results.append(100 - (100 / (1 + mfr)))
    return results


# --- Volatility ---


def atr(
    highs: List[float], lows: List[float], closes: List[float], period: int = 14
) -> List[Optional[float]]:
    if len(closes) <= period:
        return [None] * len(closes)
    tr_list = [highs[0] - lows[0]]
    for i in range(1, len(closes)):
        tr = max(
            highs[i] - lows[i],
            abs(highs[i] - closes[i - 1]),
            abs(lows[i] - closes[i - 1]),
        )
        tr_list.append(tr)
    results = [None] * (period - 1)
    current_atr = sum(tr_list[:period]) / period
    results.append(current_atr)
    for i in range(period, len(tr_list)):
        current_atr = (current_atr * (period - 1) + tr_list[i]) / period
        results.append(current_atr)
    return results


def bollinger_bands(
    data: List[float], period: int = 20, std_dev: float = 2.0
) -> Tuple[List[Optional[float]], List[Optional[float]], List[Optional[float]]]:
    basis = sma(data, period)
    upper, lower = [], []
    for i in range(len(data)):
        if basis[i] is None:
            upper.append(None)
            lower.append(None)
        else:
            window = data[i - period + 1 : i + 1]
            avg = basis[i]
            variance = sum((x - avg) ** 2 for x in window) / period
            dev = math.sqrt(variance) * std_dev
            upper.append(avg + dev)
            lower.append(avg - dev)
    return upper, basis, lower


def keltner_channels(
    highs: List[float],
    lows: List[float],
    closes: List[float],
    period: int = 20,
    atr_mult: float = 2.0,
) -> Tuple[List[Optional[float]], List[Optional[float]], List[Optional[float]]]:
    basis = ema(closes, period)
    atr_vals = atr(highs, lows, closes, period)
    upper, lower = [], []
    for b, a in zip(basis, atr_vals):
        if b is not None and a is not None:
            upper.append(b + (a * atr_mult))
            lower.append(b - (a * atr_mult))
        else:
            upper.append(None)
            lower.append(None)
    return upper, basis, lower


def donchian_channels(
    highs: List[float], lows: List[float], period: int = 20
) -> Tuple[List[Optional[float]], List[Optional[float]], List[Optional[float]]]:
    if len(highs) < period:
        return [None] * len(highs), [None] * len(highs), [None] * len(highs)
    upper, lower, mid = (
        [None] * (period - 1),
        [None] * (period - 1),
        [None] * (period - 1),
    )
    for i in range(period, len(highs) + 1):
        h = max(highs[i - period : i])
        l = min(lows[i - period : i])
        upper.append(h)
        lower.append(l)
        mid.append((h + l) / 2)
    return upper, mid, lower


# --- Trend & Momentum ---


def adx(
    highs: List[float], lows: List[float], closes: List[float], period: int = 14
) -> List[Optional[float]]:
    if len(closes) <= period * 2:
        return [None] * len(closes)
    tr_list, pos_dm, neg_dm = [], [], []
    for i in range(1, len(closes)):
        tr = max(
            highs[i] - lows[i],
            abs(highs[i] - closes[i - 1]),
            abs(lows[i] - closes[i - 1]),
        )
        tr_list.append(tr)
        up_move = highs[i] - highs[i - 1]
        down_move = lows[i - 1] - lows[i]
        if up_move > down_move and up_move > 0:
            pos_dm.append(up_move)
        else:
            pos_dm.append(0)
        if down_move > up_move and down_move > 0:
            neg_dm.append(down_move)
        else:
            neg_dm.append(0)

    # Smooth with Wilder's
    sm_tr = sum(tr_list[:period])
    sm_pos = sum(pos_dm[:period])
    sm_neg = sum(neg_dm[:period])

    dx_list = []
    for i in range(period, len(tr_list)):
        di_pos = 100 * sm_pos / sm_tr if sm_tr != 0 else 0
        di_neg = 100 * sm_neg / sm_tr if sm_tr != 0 else 0
        dx = (
            100 * abs(di_pos - di_neg) / (di_pos + di_neg)
            if (di_pos + di_neg) != 0
            else 0
        )
        dx_list.append(dx)
        sm_tr = sm_tr - (sm_tr / period) + tr_list[i]
        sm_pos = sm_pos - (sm_pos / period) + pos_dm[i]
        sm_neg = sm_neg - (sm_neg / period) + neg_dm[i]

    adx_res = [None] * (period * 2 - 1)
    current_adx = sum(dx_list[:period]) / period
    adx_res.append(current_adx)
    for i in range(period, len(dx_list)):
        current_adx = (current_adx * (period - 1) + dx_list[i]) / period
        adx_res.append(current_adx)
    return adx_res


def roc(data: List[float], period: int = 12) -> List[Optional[float]]:
    if len(data) <= period:
        return [None] * len(data)
    results = [None] * period
    for i in range(period, len(data)):
        results.append(100 * (data[i] - data[i - period]) / data[i - period])
    return results


def trix(data: List[float], period: int = 15) -> List[Optional[float]]:
    ema1 = ema(data, period)
    ema1_clean = [x if x is not None else 0 for x in ema1]
    ema2 = ema(ema1_clean, period)
    ema2_clean = [x if x is not None else 0 for x in ema2]
    ema3 = ema(ema2_clean, period)
    results = [None]
    for i in range(1, len(ema3)):
        if ema3[i] is not None and ema3[i - 1] is not None and ema3[i - 1] != 0:
            results.append((ema3[i] - ema3[i - 1]) / ema3[i - 1])
        else:
            results.append(None)
    return results


def awesome_oscillator(highs: List[float], lows: List[float]) -> List[Optional[float]]:
    hl2 = [(h + l) / 2 for h, l in zip(highs, lows)]
    sma5 = sma(hl2, 5)
    sma34 = sma(hl2, 34)
    res = []
    for s5, s34 in zip(sma5, sma34):
        if s5 is not None and s34 is not None:
            res.append(s5 - s34)
        else:
            res.append(None)
    return res


# --- Volume ---


def obv(closes: List[float], volumes: List[float]) -> List[float]:
    results = [volumes[0]]
    for i in range(1, len(closes)):
        if closes[i] > closes[i - 1]:
            results.append(results[-1] + volumes[i])
        elif closes[i] < closes[i - 1]:
            results.append(results[-1] - volumes[i])
        else:
            results.append(results[-1])
    return results


def cmf(
    highs: List[float],
    lows: List[float],
    closes: List[float],
    volumes: List[float],
    period: int = 20,
) -> List[Optional[float]]:
    if len(closes) < period:
        return [None] * len(closes)
    mfv = []
    for h, l, c, v in zip(highs, lows, closes, volumes):
        if h == l:
            mfv.append(0.0)
        else:
            mfv.append((((c - l) - (h - c)) / (h - l)) * v)

    results = [None] * (period - 1)
    for i in range(period, len(mfv) + 1):
        results.append(
            sum(mfv[i - period : i]) / sum(volumes[i - period : i])
            if sum(volumes[i - period : i]) != 0
            else 0
        )
    return results


# --- Others ---


def vortex(
    highs: List[float], lows: List[float], closes: List[float], period: int = 14
) -> Tuple[List[Optional[float]], List[Optional[float]]]:
    if len(closes) <= period:
        return [None] * len(closes), [None] * len(closes)
    tr = [
        max(
            highs[i] - lows[i],
            abs(highs[i] - closes[i - 1]),
            abs(lows[i] - closes[i - 1]),
        )
        for i in range(1, len(closes))
    ]
    vm_pos = [abs(highs[i] - lows[i - 1]) for i in range(1, len(closes))]
    vm_neg = [abs(lows[i] - highs[i - 1]) for i in range(1, len(closes))]

    vi_pos, vi_neg = [None] * period, [None] * period
    for i in range(period, len(tr) + 1):
        sum_tr = sum(tr[i - period : i])
        if sum_tr == 0:
            vi_pos.append(1.0)
            vi_neg.append(1.0)
        else:
            vi_pos.append(sum(vm_pos[i - period : i]) / sum_tr)
            vi_neg.append(sum(vm_neg[i - period : i]) / sum_tr)
    return vi_pos, vi_neg


def aroon(
    highs: List[float], lows: List[float], period: int = 25
) -> Tuple[List[Optional[float]], List[Optional[float]]]:
    if len(highs) < period:
        return [None] * len(highs), [None] * len(highs)
    up, down = [None] * (period - 1), [None] * (period - 1)
    for i in range(period, len(highs) + 1):
        window_h = highs[i - period : i]
        window_l = lows[i - period : i]
        up.append(
            100 * (period - (period - 1 - window_h[::-1].index(max(window_h)))) / period
        )
        down.append(
            100 * (period - (period - 1 - window_l[::-1].index(min(window_l)))) / period
        )
    return up, down


def supertrend(
    highs: List[float],
    lows: List[float],
    closes: List[float],
    period: int = 10,
    multiplier: float = 3.0,
) -> Tuple[List[Optional[float]], List[Optional[float]]]:
    if len(closes) <= period:
        return [None] * len(closes), [None] * len(closes)
    atr_vals = atr(highs, lows, closes, period)
    hl2 = [(h + l) / 2 for h, l in zip(highs, lows)]

    upper_band = [hl2[i] + (multiplier * (atr_vals[i] or 0)) for i in range(len(hl2))]
    lower_band = [hl2[i] - (multiplier * (atr_vals[i] or 0)) for i in range(len(hl2))]

    st = [None] * len(closes)
    trend = [1] * len(closes)  # 1 for up, -1 for down

    # Initialize first valid index
    start_idx = period
    st[start_idx] = upper_band[start_idx]

    for i in range(start_idx + 1, len(closes)):
        if closes[i - 1] > upper_band[i - 1]:
            trend[i] = 1
        elif closes[i - 1] < lower_band[i - 1]:
            trend[i] = -1
        else:
            trend[i] = trend[i - 1]
            if trend[i] == 1 and lower_band[i] < lower_band[i - 1]:
                lower_band[i] = lower_band[i - 1]
            if trend[i] == -1 and upper_band[i] > upper_band[i - 1]:
                upper_band[i] = upper_band[i - 1]

        st[i] = lower_band[i] if trend[i] == 1 else upper_band[i]

    return st, [float(t) for t in trend]


def cmo(data: List[float], period: int = 14) -> List[Optional[float]]:
    """Chande Momentum Oscillator"""
    if len(data) <= period:
        return [None] * len(data)
    results = [None] * period
    for i in range(period, len(data)):
        window = data[i - period : i + 1]
        s_up = sum(max(0, window[j] - window[j - 1]) for j in range(1, len(window)))
        s_down = sum(max(0, window[j - 1] - window[j]) for j in range(1, len(window)))
        if (s_up + s_down) == 0:
            results.append(0.0)
        else:
            results.append(100 * (s_up - s_down) / (s_up + s_down))
    return results


def ichimoku(highs: List[float], lows: List[float]) -> Dict[str, List[Optional[float]]]:
    """Ichimoku Cloud components"""

    def mid_price(h, l, p):
        res = [None] * (p - 1)
        for i in range(p, len(h) + 1):
            res.append((max(h[i - p : i]) + min(l[i - p : i])) / 2)
        return res

    tenkan = mid_price(highs, lows, 9)
    kijun = mid_price(highs, lows, 26)

    # Senkou Span A
    ssa = []
    for t, k in zip(tenkan, kijun):
        if t is not None and k is not None:
            ssa.append((t + k) / 2)
        else:
            ssa.append(None)

    # Senkou Span B
    ssb = mid_price(highs, lows, 52)

    return {
        "tenkan": tenkan,
        "kijun": kijun,
        "senkou_a": [None] * 26 + ssa[:-26] if len(ssa) > 26 else [None] * len(ssa),
        "senkou_b": [None] * 26 + ssb[:-26] if len(ssb) > 26 else [None] * len(ssb),
    }


# --- Ehlers DSP Tools ---


def fisher_transform(
    highs: List[float], lows: List[float], period: int = 9
) -> Tuple[List[Optional[float]], List[Optional[float]]]:
    """Ehlers Fisher Transform"""
    if len(highs) < period:
        return [None] * len(highs), [None] * len(highs)

    # 1. Median Price range normalization
    med = [(h + l) / 2 for h, l in zip(highs, lows)]
    value = [0.0] * len(med)
    fish = [0.0] * len(med)

    for i in range(period, len(med)):
        window = med[i - period + 1 : i + 1]
        mx = max(window)
        mn = min(window)
        if mx == mn:
            val = 0.0
        else:
            val = 0.33 * 2 * ((med[i] - mn) / (mx - mn) - 0.5) + 0.67 * value[i - 1]

        val = min(val, 0.999)
        val = max(val, -0.999)
        value[i] = val
        fish[i] = 0.5 * math.log((1 + val) / (1 - val)) + 0.5 * fish[i - 1]

    return fish, [None] + fish[:-1]  # Fisher and Trigger


def supersmoother(data: List[float], period: int = 15) -> List[Optional[float]]:
    """Ehlers SuperSmoother Filter"""
    if len(data) < 3:
        return [None] * len(data)
    a1 = math.exp(-1.414 * 3.14159 / period)
    b1 = 2 * a1 * math.cos(1.414 * 180 / period * 3.14159 / 180)
    c2 = b1
    c3 = -a1 * a1
    c1 = 1 - c2 - c3

    filt = [0.0] * len(data)
    # Initialize first two values
    filt[0], filt[1] = data[0], data[1]

    for i in range(2, len(data)):
        filt[i] = c1 * (data[i] + data[i - 1]) / 2 + c2 * filt[i - 1] + c3 * filt[i - 2]
    return filt


def cyber_cycle(
    highs: List[float], lows: List[float], period: int = 15
) -> Tuple[List[Optional[float]], List[Optional[float]]]:
    """Ehlers Cyber Cycle"""
    price = [(h + l) / 2 for h, l in zip(highs, lows)]
    smooth = [0.0] * len(price)
    cycle = [0.0] * len(price)

    for i in range(3, len(price)):
        smooth[i] = (price[i] + 2 * price[i - 1] + 2 * price[i - 2] + price[i - 3]) / 6
        cycle[i] = (
            (1 - 0.5 * 0.5) * (smooth[i] - 2 * smooth[i - 1] + smooth[i - 2])
            + 2 * (1 - 0.5) * cycle[i - 1]
            - (1 - 0.5) * (1 - 0.5) * cycle[i - 2]
        )

    trigger = [0.0] + cycle[:-1]
    return cycle, trigger


# --- Advanced Volume Tools ---


def vwap(closes: List[float], volumes: List[float]) -> List[float]:
    """Volume Weighted Average Price (Full History)"""
    cv = [c * v for c, v in zip(closes, volumes)]
    cum_cv = 0.0
    cum_v = 0.0
    results = []
    for i in range(len(closes)):
        cum_cv += cv[i]
        cum_v += volumes[i]
        results.append(cum_cv / cum_v if cum_v != 0 else closes[i])
    return results


def ad_line(
    highs: List[float], lows: List[float], closes: List[float], volumes: List[float]
) -> List[float]:
    """Accumulation/Distribution Line"""
    results = [0.0]
    for i in range(len(closes)):
        if highs[i] == lows[i]:
            mfm = 0.0
        else:
            mfm = ((closes[i] - lows[i]) - (highs[i] - closes[i])) / (
                highs[i] - lows[i]
            )
        results.append(results[-1] + (mfm * volumes[i]))
    return results[1:]


def vpt(closes: List[float], volumes: List[float]) -> List[float]:
    """Volume Price Trend"""
    results = [0.0]
    for i in range(1, len(closes)):
        vpt_val = results[-1] + volumes[i] * (closes[i] - closes[i - 1]) / closes[i - 1]
        results.append(vpt_val)
    return results


# --- Fibonacci & Pivots ---


def fibonacci_pivots(high: float, low: float, close: float) -> Dict[str, float]:
    """Calculate Fibonacci Pivot Points"""
    p = (high + low + close) / 3
    tr = high - low
    return {
        "P": p,
        "R1": p + (0.382 * tr),
        "R2": p + (0.618 * tr),
        "R3": p + (1.000 * tr),
        "S1": p - (0.382 * tr),
        "S2": p - (0.618 * tr),
        "S3": p - (1.000 * tr),
    }
