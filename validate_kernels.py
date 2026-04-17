"""
Kernel Regression Validation Script

Fetches 200 closed 1H ETHUSDT candles from Binance, computes both
kernel estimators, and prints a table for manual comparison with
TradingView's kernel regression indicator.
"""

import logging
import numpy as np

from bot.config import (
    SYMBOL, TIMEFRAME, KLINES_LIMIT,
    LOOKBACK_WINDOW, RELATIVE_WEIGHT, REGRESSION_LEVEL, LAG,
)
from bot.exchange import Exchange
from bot.data_fetcher import DataFetcher
from bot.kernels import rational_quadratic, gaussian

# Minimal logging — only errors
logging.basicConfig(level=logging.WARNING)


def main():
    print("=" * 70)
    print("  Kernel Regression Validation — ETHUSDT 1H")
    print("=" * 70)
    print()

    # Connect and fetch data
    exchange = Exchange()
    fetcher = DataFetcher(exchange.client)

    candles = fetcher.get_klines(SYMBOL, TIMEFRAME, KLINES_LIMIT)
    close_prices = np.array([c['close'] for c in candles])

    print(f"Candles fetched: {len(candles)} (closed only)")
    print(f"Date range: {_fmt_ts(candles[0]['open_time'])} -> {_fmt_ts(candles[-1]['close_time'])}")
    print(f"Parameters: h={LOOKBACK_WINDOW}, r={RELATIVE_WEIGHT}, x={REGRESSION_LEVEL}, lag={LAG}")
    print()

    # Compute kernels
    yhat1 = rational_quadratic(close_prices, LOOKBACK_WINDOW, RELATIVE_WEIGHT, REGRESSION_LEVEL)
    yhat2 = gaussian(close_prices, LOOKBACK_WINDOW - LAG, REGRESSION_LEVEL)

    # Print last 20 bars
    print(f"{'Bar':>5} | {'Close':>10} | {'yhat1 (RQ)':>12} | {'yhat2 (Gauss)':>14} | Bullish")
    print("-" * 70)

    start = max(len(candles) - 20, 0)
    for idx in range(start, len(candles)):
        close = close_prices[idx]
        y1 = yhat1[idx]
        y2 = yhat2[idx]

        if np.isnan(y1) or np.isnan(y2):
            bullish_str = "N/A"
        else:
            bullish_str = "True" if y2 >= y1 else "False"

        y1_str = f"{y1:.2f}" if not np.isnan(y1) else "NaN"
        y2_str = f"{y2:.2f}" if not np.isnan(y2) else "NaN"

        print(f"{idx:>5} | {close:>10.2f} | {y1_str:>12} | {y2_str:>14} | {bullish_str}")

    print()

    # Current signal
    last_y1 = yhat1[-1]
    last_y2 = yhat2[-1]
    prev_y1 = yhat1[-2]
    prev_y2 = yhat2[-2]

    current_bullish = last_y2 >= last_y1
    prev_bullish = prev_y2 >= prev_y1
    signal_change = current_bullish != prev_bullish

    print(f"Current signal: {'BULLISH' if current_bullish else 'BEARISH'} (yhat2 {'>=' if current_bullish else '<'} yhat1)")
    print(f"Previous signal: {'BULLISH' if prev_bullish else 'BEARISH'}")
    print(f"Signal change: {'YES' if signal_change else 'NO'}")
    print()

    # Kernel values for quick comparison
    print(f"yhat1 (RQ):    {last_y1:.6f}")
    print(f"yhat2 (Gauss): {last_y2:.6f}")
    print(f"Difference:    {abs(last_y2 - last_y1):.6f}")
    print()

    # Validation instructions
    print("=" * 70)
    print("  VALIDATION INSTRUCTIONS")
    print("=" * 70)
    print()
    print("To validate these values against TradingView:")
    print("1. Open TradingView -> ETHUSDT 1H chart")
    print("2. Add indicator: Nadaraya-Watson Envelope (jdehorty)")
    print("   Settings: h=110, r=10, x=64, lag=1")
    print("3. Hover over the kernel regression line at each bar")
    print("4. Compare yhat1 values — they should match within 0.01%")
    print("5. Check if Bullish/Bearish matches the line color (green/red)")
    print()
    print("NOTE: Small differences (<0.1%) are expected due to:")
    print("  - Binance vs TradingView price feed differences")
    print("  - Floating point precision")
    print("  - Candle close timing (we use only closed candles)")


def _fmt_ts(ms):
    """Format millisecond timestamp to readable string."""
    from datetime import datetime, timezone
    return datetime.fromtimestamp(ms / 1000, tz=timezone.utc).strftime('%Y-%m-%d %H:%M UTC')


if __name__ == '__main__':
    main()
