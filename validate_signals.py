"""
Signal Logic Validation Script

Fetches 200 closed 1H ETHUSDT candles, computes kernels + volatility
filter + signal changes, and prints a table for review.
"""

import logging
import numpy as np
from datetime import datetime, timezone

from bot.config import (
    SYMBOL, TIMEFRAME, KLINES_LIMIT,
    LOOKBACK_WINDOW, RELATIVE_WEIGHT, REGRESSION_LEVEL, LAG,
    VOLATILITY_MIN, VOLATILITY_MAX, SL_PERCENT,
)
from bot.exchange import Exchange
from bot.data_fetcher import DataFetcher
from bot.kernels import rational_quadratic, gaussian
from bot.filters import filter_volatility

logging.basicConfig(level=logging.WARNING)


def fmt_ts(ms):
    return datetime.fromtimestamp(ms / 1000, tz=timezone.utc).strftime('%H:%M')


def main():
    print("=" * 90)
    print("  Signal Logic Validation -- ETHUSDT 1H")
    print("=" * 90)
    print()

    exchange = Exchange()
    fetcher = DataFetcher(exchange.client)
    candles = fetcher.get_klines(SYMBOL, TIMEFRAME, KLINES_LIMIT)

    close = np.array([c['close'] for c in candles])
    high = np.array([c['high'] for c in candles])
    low = np.array([c['low'] for c in candles])

    n = len(close)
    print(f"Candles: {n} (closed only)")
    print(f"Params: h={LOOKBACK_WINDOW}, r={RELATIVE_WEIGHT}, x={REGRESSION_LEVEL}, lag={LAG}")
    print(f"SL: {SL_PERCENT}% dynamic trailing")
    print(f"Volatility filter: ATR({VOLATILITY_MIN}) > ATR({VOLATILITY_MAX})")
    print()

    # Compute
    yhat1 = rational_quadratic(close, LOOKBACK_WINDOW, RELATIVE_WEIGHT, REGRESSION_LEVEL)
    yhat2 = gaussian(close, LOOKBACK_WINDOW - LAG, REGRESSION_LEVEL)
    is_bullish = yhat2 >= yhat1
    vol_filter = filter_volatility(high, low, close, VOLATILITY_MIN, VOLATILITY_MAX, True)

    # Table header
    print(f"{'Bar':>3} | {'Time':>5} | {'Close':>8} | {'yhat1':>8} | {'yhat2':>8} | "
          f"{'Bull':>4} | {'BullChg':>7} | {'BearChg':>7} | {'VolFlt':>6} | Signal")
    print("-" * 90)

    # Simulate state machine over last 30 bars to show signals
    state = 'flat'
    entry_price = 0.0
    stop_loss = 0.0

    start = max(REGRESSION_LEVEL + 1, n - 30)
    for idx in range(start, n):
        y1 = yhat1[idx]
        y2 = yhat2[idx]
        if np.isnan(y1) or np.isnan(y2):
            continue

        bull = is_bullish[idx]
        prev_bull = is_bullish[idx - 1] if idx > 0 else False
        bullish_chg = bool(bull and not prev_bull)

        bear = yhat2[idx] <= yhat1[idx]
        prev_bear = yhat2[idx - 1] <= yhat1[idx - 1] if idx > 0 else False
        bearish_chg = bool(bear and not prev_bear)

        vol = bool(vol_filter[idx])

        # Update trailing SL
        if state == 'long':
            new_sl = close[idx] * (1 - SL_PERCENT / 100)
            if new_sl > stop_loss:
                stop_loss = new_sl
        elif state == 'short':
            new_sl = close[idx] * (1 + SL_PERCENT / 100)
            if new_sl < stop_loss:
                stop_loss = new_sl

        # Determine signal
        signal = "-"

        # Priority 1: SL
        if state == 'long' and low[idx] <= stop_loss:
            signal = f"CLOSE_LONG (SL={stop_loss:.0f})"
            state = 'flat'
            entry_price = 0.0
            stop_loss = 0.0
        elif state == 'short' and high[idx] >= stop_loss:
            signal = f"CLOSE_SHORT (SL={stop_loss:.0f})"
            state = 'flat'
            entry_price = 0.0
            stop_loss = 0.0
        # Priority 2: Color change
        elif state == 'long' and bearish_chg:
            signal = "CLOSE_LONG (color)"
            state = 'flat'
            entry_price = 0.0
            stop_loss = 0.0
        elif state == 'short' and bullish_chg:
            signal = "CLOSE_SHORT (color)"
            state = 'flat'
            entry_price = 0.0
            stop_loss = 0.0
        # Priority 4: Entry
        elif bullish_chg and state == 'flat' and vol:
            signal = "OPEN_LONG"
            state = 'long'
            entry_price = close[idx]
            stop_loss = entry_price * (1 - SL_PERCENT / 100)
        elif bearish_chg and state == 'flat' and vol:
            signal = "OPEN_SHORT"
            state = 'short'
            entry_price = close[idx]
            stop_loss = entry_price * (1 + SL_PERCENT / 100)
        elif bullish_chg and state == 'flat' and not vol:
            signal = "BLOCKED (vol)"
        elif bearish_chg and state == 'flat' and not vol:
            signal = "BLOCKED (vol)"

        ts = fmt_ts(candles[idx]['open_time'])
        print(f"{idx:>3} | {ts:>5} | {close[idx]:>8.2f} | {y1:>8.2f} | {y2:>8.2f} | "
              f"{'T' if bull else 'F':>4} | {'YES' if bullish_chg else '-':>7} | "
              f"{'YES' if bearish_chg else '-':>7} | {'PASS' if vol else 'FAIL':>6} | {signal}")

    print()
    print(f"Final state: {state.upper()}")
    if state != 'flat':
        print(f"Entry: {entry_price:.2f}, SL: {stop_loss:.2f}")

    # Current bar summary
    print()
    last_bull = is_bullish[-1]
    prev_bull = is_bullish[-2]
    bullish_chg = bool(last_bull and not prev_bull)
    bearish_chg = bool(not last_bull and prev_bull)
    vol_now = bool(vol_filter[-1])
    print(f"Current kernel: yhat1={yhat1[-1]:.2f}, yhat2={yhat2[-1]:.2f}")
    print(f"Direction: {'BULLISH' if last_bull else 'BEARISH'}")
    print(f"Bullish change: {bullish_chg}, Bearish change: {bearish_chg}")
    print(f"Volatility filter: {'PASS' if vol_now else 'BLOCK'}")


if __name__ == '__main__':
    main()
