import logging
import time as time_module
from datetime import datetime, timezone

import numpy as np

from bot.utils import safe_api_call

logger = logging.getLogger(__name__)


class DataFetcher:
    """Fetches OHLCV data from Binance Futures.

    Uses a separate mainnet client for price data (klines) so that signals
    match the backtest / AutoResearch / TradingView exactly.  The trading
    client (testnet) is kept only for server-time queries that need to stay
    in sync with the exchange where orders are executed.
    """

    def __init__(self, trading_client):
        from binance.client import Client
        # Mainnet client — public endpoints (klines) don't need API keys
        self.data_client = Client('', '')
        # Testnet client — used for server time (order-side clock sync)
        self.trading_client = trading_client
        logger.info("[DATA] Using MAINNET data for signals, TESTNET for orders")

    def get_klines(self, symbol, interval, limit):
        """Fetch OHLCV candles from Binance.

        Returns list of dicts with closed candles only (last candle
        is dropped if still open — zero lookahead bias).
        """
        raw = safe_api_call(
            self.data_client,
            self.data_client.futures_klines,
            symbol=symbol,
            interval=interval,
            limit=limit,
        )

        now_ms = int(time_module.time() * 1000)

        candles = []
        for k in raw:
            open_time = int(k[0])
            close_time = int(k[6])

            # Skip unclosed candle
            if close_time >= now_ms:
                continue

            candles.append({
                'open_time': open_time,
                'open': float(k[1]),
                'high': float(k[2]),
                'low': float(k[3]),
                'close': float(k[4]),
                'volume': float(k[5]),
                'close_time': close_time,
            })

        if candles:
            latest_dt = datetime.fromtimestamp(candles[-1]['close_time'] / 1000, tz=timezone.utc)
            logger.info(
                f"[DATA] Fetched {len(candles)} closed candles for {symbol} {interval}, "
                f"latest close: {latest_dt.strftime('%Y-%m-%d %H:%M')} UTC"
            )
        else:
            logger.warning(f"[DATA] No closed candles returned for {symbol} {interval}")

        return candles

    def get_close_prices(self, symbol, interval, limit):
        """Convenience method — return numpy array of close prices."""
        candles = self.get_klines(symbol, interval, limit)
        return np.array([c['close'] for c in candles])

    def time_until_next_candle(self, interval):
        """Calculate seconds until next candle close using Binance server time.

        Uses server time to avoid local clock desync issues.
        Supports: '1m', '5m', '15m', '30m', '1h', '4h', '1d'.
        """
        interval_ms = {
            '1m': 60_000,
            '5m': 300_000,
            '15m': 900_000,
            '30m': 1_800_000,
            '1h': 3_600_000,
            '4h': 14_400_000,
            '1d': 86_400_000,
        }

        period_ms = interval_ms.get(interval)
        if period_ms is None:
            logger.error(f"[DATA] Unsupported interval: {interval}")
            return 0

        try:
            server_time = safe_api_call(self.trading_client, self.trading_client.get_server_time)
            now_ms = server_time['serverTime']
        except Exception as e:
            logger.warning(f"[DATA] Failed to get server time, using local: {e}")
            now_ms = int(time_module.time() * 1000)

        current_candle_open = (now_ms // period_ms) * period_ms
        next_candle_open = current_candle_open + period_ms
        wait_ms = next_candle_open - now_ms

        seconds_left = max(0, wait_ms / 1000)
        minutes = int(seconds_left) // 60
        seconds = int(seconds_left) % 60
        logger.info(f"[DATA] Next candle close in {minutes}m {seconds}s")

        return seconds_left
