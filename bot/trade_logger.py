import csv
import logging
import os

logger = logging.getLogger(__name__)

TRADE_LOG_COLUMNS = [
    'timestamp', 'action', 'signal_type', 'trade_type', 'entry_price',
    'exit_price', 'fill_price', 'slippage_pct', 'pnl_usd', 'pnl_pct',
    'exit_reason', 'balance_after', 'consecutive_losses', 'bars_in_trade',
]


class TradeLogger:
    """CSV trade logger — one row per open/close event."""

    def __init__(self, filepath='trade_log.csv'):
        self.filepath = filepath
        self._open_bar = 0  # bar count when trade opened (for bars_in_trade)
        if not os.path.exists(self.filepath):
            with open(self.filepath, 'w', newline='') as f:
                writer = csv.writer(f)
                writer.writerow(TRADE_LOG_COLUMNS)

    def _write_row(self, **kwargs):
        """Append a row to the CSV."""
        try:
            row = [kwargs.get(col, '') for col in TRADE_LOG_COLUMNS]
            with open(self.filepath, 'a', newline='') as f:
                writer = csv.writer(f)
                writer.writerow(row)
        except Exception as e:
            logger.error(f"[CSV] Failed to write trade log: {e}")

    def log_open(self, timestamp, action, entry_price, fill_price, slippage_pct,
                 trade_type='signal', bar_index=0):
        """Log a position open.

        trade_type: 'signal' (standard entry) or 're_entry' (re-entry after SL).
        """
        self._open_bar = bar_index
        self._write_row(
            timestamp=timestamp,
            action=action,
            signal_type='entry',
            trade_type=trade_type,
            entry_price=entry_price,
            fill_price=fill_price,
            slippage_pct=f"{slippage_pct:.3f}",
        )

    def log_close(self, timestamp, action, exit_price, pnl_usd, pnl_pct,
                  exit_reason, balance_after, consecutive_losses, bar_index=0):
        """Log a position close.

        exit_reason: 'stop_loss', 'color_change', etc.
        """
        bars_in_trade = bar_index - self._open_bar if bar_index > 0 else ''
        self._write_row(
            timestamp=timestamp,
            action=action,
            signal_type='exit',
            trade_type=exit_reason,
            exit_price=exit_price,
            fill_price=exit_price,
            pnl_usd=f"{pnl_usd:.2f}",
            pnl_pct=f"{pnl_pct:.2f}",
            exit_reason=exit_reason,
            balance_after=f"{balance_after:.2f}",
            consecutive_losses=consecutive_losses,
            bars_in_trade=bars_in_trade,
        )

    def get_recent_trades(self, n=20):
        """Return the last N trades as list of dicts."""
        try:
            if not os.path.exists(self.filepath):
                return []
            with open(self.filepath, 'r') as f:
                reader = csv.DictReader(f)
                all_trades = list(reader)
            return all_trades[-n:]
        except Exception as e:
            logger.error(f"[CSV] Failed to read trade log: {e}")
            return []
