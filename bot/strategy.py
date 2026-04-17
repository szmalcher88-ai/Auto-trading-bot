"""
Signal logic — 1:1 replica of Pine Script kernel regression strategy.

Priority order:
  1. SL hit -> close
  2. TP (color change) -> close
  3. Re-entry -> open
  4. Standard entry -> open
"""

import logging
import numpy as np

from bot.kernels import rational_quadratic, gaussian
from bot.filters import filter_volatility
from bot.config import (
    LOOKBACK_WINDOW, RELATIVE_WEIGHT, REGRESSION_LEVEL, LAG,
    USE_KERNEL_SMOOTHING, SL_TYPE, SL_PERCENT, ATR_PERIOD, ATR_MULTIPLIER,
    USE_DYNAMIC_SL, TRAILING_SL_MODE,
    VOLATILITY_MIN, VOLATILITY_MAX,
    ENABLE_RE_ENTRY, RE_ENTRY_DELAY,
)

logger = logging.getLogger(__name__)


class Strategy:
    """Kernel regression signal logic — mirrors Pine Script 1:1."""

    def __init__(self):
        # Position state (synced from exchange on init)
        self.state = 'flat'        # 'flat', 'long', 'short'
        self.entry_price = 0.0
        self.stop_loss = 0.0

        # Re-entry tracking
        self.pending_re_entry = False
        self.bars_since_exit = 0
        self.last_exit_type = None  # 'long' or 'short'

        # ATR state (persists across candles for Wilder's RMA)
        self.last_atr = None

    # ------------------------------------------------------------------
    # State sync
    # ------------------------------------------------------------------

    def sync_state(self, position, entry_price):
        """Sync strategy state from exchange position."""
        if position is None:
            self.state = 'flat'
            self.entry_price = 0.0
            self.stop_loss = 0.0
        else:
            self.state = position  # 'long' or 'short'
            self.entry_price = entry_price
            self.stop_loss = self._calculate_stop_loss(position, entry_price)
            logger.info(
                f"[STRAT] State synced: {self.state}, entry={self.entry_price:.2f}, "
                f"SL={self.stop_loss:.2f}"
            )

    # ------------------------------------------------------------------
    # Stop loss
    # ------------------------------------------------------------------

    def _calculate_stop_loss(self, side, price, atr_value=None):
        """Calculate initial stop loss from price.

        Uses ATR-based SL when SL_TYPE='atr' and atr_value is available,
        otherwise falls back to percent-based SL.
        """
        if SL_TYPE == 'atr' and atr_value is not None and atr_value > 0:
            atr_dist = atr_value * ATR_MULTIPLIER
            if side == 'long':
                return price - atr_dist
            else:
                return price + atr_dist
        else:
            if side == 'long':
                return price * (1 - SL_PERCENT / 100)
            else:
                return price * (1 + SL_PERCENT / 100)

    def _update_trailing_sl(self, close_price, high_price=None, low_price=None,
                            atr_value=None):
        """Update trailing SL — only tightens, never loosens.

        Modes:
          - 'pine': uses close price (Pine Script behavior)
          - 'execution': uses high for longs, low for shorts (realistic)
        """
        if not USE_DYNAMIC_SL:
            return

        if TRAILING_SL_MODE == 'execution' and high_price is not None:
            ref_long = high_price
            ref_short = low_price
        else:
            ref_long = close_price
            ref_short = close_price

        if self.state == 'long':
            new_sl = self._calculate_stop_loss('long', ref_long, atr_value)
            if new_sl > self.stop_loss:
                self.stop_loss = new_sl
        elif self.state == 'short':
            new_sl = self._calculate_stop_loss('short', ref_short, atr_value)
            if new_sl < self.stop_loss:
                self.stop_loss = new_sl

    # ------------------------------------------------------------------
    # ATR computation (Wilder's RMA — same as Pine Script ta.atr())
    # ------------------------------------------------------------------

    def _compute_atr(self, high, low, close):
        """Compute ATR using Wilder's smoothing (RMA).

        Uses self.last_atr to maintain state across candle calls.
        Returns current ATR value (float) or None if not enough data.
        """
        n = len(close)
        if n < ATR_PERIOD + 1:
            return None

        # True Range for the latest bar
        tr = max(
            high[-1] - low[-1],
            abs(high[-1] - close[-2]),
            abs(low[-1] - close[-2]),
        )

        if self.last_atr is not None:
            # RMA update: atr = (prev_atr * (period - 1) + tr) / period
            atr = (self.last_atr * (ATR_PERIOD - 1) + tr) / ATR_PERIOD
        else:
            # Cold start: compute full ATR from available data
            tr_arr = np.maximum(
                high[1:] - low[1:],
                np.maximum(
                    np.abs(high[1:] - close[:-1]),
                    np.abs(low[1:] - close[:-1]),
                ),
            )
            if len(tr_arr) < ATR_PERIOD:
                return None
            # Seed with SMA, then apply RMA for remaining bars
            atr = np.mean(tr_arr[:ATR_PERIOD])
            for j in range(ATR_PERIOD, len(tr_arr)):
                atr = (atr * (ATR_PERIOD - 1) + tr_arr[j]) / ATR_PERIOD

        self.last_atr = atr
        return atr

    # ------------------------------------------------------------------
    # Main signal calculation
    # ------------------------------------------------------------------

    def calculate_signals(self, ohlcv):
        """
        Main signal calculation — called once per closed candle.

        Args:
            ohlcv: dict with numpy arrays: 'open', 'high', 'low', 'close', 'volume'

        Returns:
            dict with:
              - 'action': 'open_long' | 'open_short' | 'close_long' | 'close_short' | None
              - 'reason': str
              - 'details': dict with debug info
        """
        close = ohlcv['close']
        high = ohlcv['high']
        low = ohlcv['low']

        # --- 0. ATR computation (for ATR-based SL) ---
        atr_val = self._compute_atr(high, low, close) if SL_TYPE == 'atr' else None

        # --- 1. Kernel regression ---
        yhat1 = rational_quadratic(close, LOOKBACK_WINDOW, RELATIVE_WEIGHT, REGRESSION_LEVEL)
        yhat2 = gaussian(close, LOOKBACK_WINDOW - LAG, REGRESSION_LEVEL)

        # --- 2. Bullish / Bearish + signal change detection ---
        if USE_KERNEL_SMOOTHING:
            # Crossover mode: gaussian above/below RQ
            is_bullish_now = yhat2[-1] >= yhat1[-1]
            is_bearish_now = yhat2[-1] <= yhat1[-1]
            was_bullish = yhat2[-2] >= yhat1[-2]
            was_bearish = yhat2[-2] <= yhat1[-2]
        else:
            # Rate of change mode: kernel rising/falling
            # Pine: isBullishRate = yhat1[1] < yhat1 → Python: yhat1[-2] < yhat1[-1]
            is_bullish_now = yhat1[-1] > yhat1[-2]
            is_bearish_now = yhat1[-1] < yhat1[-2]
            was_bullish = yhat1[-2] > yhat1[-3]
            was_bearish = yhat1[-2] < yhat1[-3]

        bullish_change = bool(is_bullish_now and not was_bullish)
        bearish_change = bool(is_bearish_now and not was_bearish)

        # --- 4. Volatility filter ---
        vol_filter = filter_volatility(high, low, close, VOLATILITY_MIN, VOLATILITY_MAX, True)
        vol_passes = bool(vol_filter[-1])

        # Build details dict
        details = {
            'yhat1': float(yhat1[-1]),
            'yhat2': float(yhat2[-1]),
            'is_bullish': bool(is_bullish_now),
            'bullish_change': bullish_change,
            'bearish_change': bearish_change,
            'vol_passes': vol_passes,
            'state': self.state,
            'stop_loss': self.stop_loss,
            'entry_price': self.entry_price,
            'pending_re_entry': self.pending_re_entry,
            'bars_since_exit': self.bars_since_exit,
            'atr': float(atr_val) if atr_val is not None else None,
        }

        # --- 5. Update trailing SL (before checking SL hit) ---
        if self.state != 'flat':
            self._update_trailing_sl(close[-1], high_price=high[-1], low_price=low[-1],
                                     atr_value=atr_val)
            details['stop_loss'] = self.stop_loss

        # --- Priority 1: SL hit (intra-bar check using high/low) ---
        # NOTE: SL is simulated — checked on closed candle only (1H frequency).
        # This means actual loss can exceed SL_PERCENT in fast moves.
        # For production: consider adding exchange-side STOP_MARKET order as safety net.
        if self.state == 'long' and low[-1] <= self.stop_loss:
            logger.info(
                f"[SL] Stop loss triggered: low={low[-1]:.2f} <= stop_loss={self.stop_loss:.2f} "
                f"— simulated, not exchange-side"
            )
            return {'action': 'close_long', 'reason': 'stop_loss', 'details': details}

        if self.state == 'short' and high[-1] >= self.stop_loss:
            logger.info(
                f"[SL] Stop loss triggered: high={high[-1]:.2f} >= stop_loss={self.stop_loss:.2f} "
                f"— simulated, not exchange-side"
            )
            return {'action': 'close_short', 'reason': 'stop_loss', 'details': details}

        # --- Priority 2: TP — color change ---
        if self.state == 'long' and bearish_change:
            logger.info("[STRAT] Color change -> close LONG")
            return {'action': 'close_long', 'reason': 'color_change', 'details': details}

        if self.state == 'short' and bullish_change:
            logger.info("[STRAT] Color change -> close SHORT")
            return {'action': 'close_short', 'reason': 'color_change', 'details': details}

        # --- Priority 3: Re-entry ---
        if self.pending_re_entry and ENABLE_RE_ENTRY:
            self.bars_since_exit += 1
            details['bars_since_exit'] = self.bars_since_exit

            if self.bars_since_exit > RE_ENTRY_DELAY + 5:
                # Timeout
                self.pending_re_entry = False
                logger.info("[STRAT] Re-entry timeout — cancelled")

            elif self.bars_since_exit > RE_ENTRY_DELAY and vol_passes:
                if self.last_exit_type == 'long':
                    logger.info("[STRAT] Re-entry -> open SHORT")
                    return {'action': 'open_short', 'reason': 're_entry', 'details': details}
                elif self.last_exit_type == 'short':
                    logger.info("[STRAT] Re-entry -> open LONG")
                    return {'action': 'open_long', 'reason': 're_entry', 'details': details}

        # --- Priority 4: Standard entry ---
        is_flat = self.state == 'flat'

        if bullish_change and is_flat and vol_passes:
            logger.info("[STRAT] Bullish change -> open LONG")
            return {'action': 'open_long', 'reason': 'bullish_change', 'details': details}

        if bearish_change and is_flat and vol_passes:
            logger.info("[STRAT] Bearish change -> open SHORT")
            return {'action': 'open_short', 'reason': 'bearish_change', 'details': details}

        # --- No signal ---
        if bullish_change and is_flat and not vol_passes:
            logger.info("[STRAT] Bullish change BLOCKED by volatility filter")
        if bearish_change and is_flat and not vol_passes:
            logger.info("[STRAT] Bearish change BLOCKED by volatility filter")

        return {'action': None, 'reason': None, 'details': details}

    # ------------------------------------------------------------------
    # State updates (called AFTER exchange execution confirms)
    # ------------------------------------------------------------------

    def on_open(self, side, fill_price):
        """Update state after opening a position."""
        self.state = side
        self.entry_price = fill_price
        self.stop_loss = self._calculate_stop_loss(side, fill_price, atr_value=self.last_atr)
        self.pending_re_entry = False
        logger.info(
            f"[STRAT] Opened {side.upper()} at {fill_price:.2f}, SL={self.stop_loss:.2f}"
            f"{f', ATR={self.last_atr:.2f}' if self.last_atr else ''}"
        )

    def on_close(self, side):
        """Update state after closing a position."""
        self.pending_re_entry = ENABLE_RE_ENTRY
        self.bars_since_exit = 0
        self.last_exit_type = side
        self.state = 'flat'
        self.entry_price = 0.0
        self.stop_loss = 0.0
        logger.info(
            f"[STRAT] Closed {side.upper()}, re-entry={'pending' if self.pending_re_entry else 'off'}"
        )
