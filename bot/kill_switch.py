import logging
from datetime import datetime, timedelta
from bot.config import (
    KILL_SWITCH_CONSECUTIVE_LOSSES,
    KILL_SWITCH_EQUITY_DROP_PERCENT,
    KILL_SWITCH_PAUSE_HOURS,
)

logger = logging.getLogger(__name__)


class KillSwitch:
    """Anti-drawdown kill switch — pauses trading after consecutive losses
    or equity drop from peak."""

    def __init__(self, initial_equity):
        self.consecutive_losses = 0
        self.peak_equity = initial_equity
        self.trading_paused = False
        self.pause_until = None

    # ------------------------------------------------------------------
    # Evaluation
    # ------------------------------------------------------------------

    def evaluate(self, pnl_pct, current_equity):
        """Call after closing a trade. Updates counters and activates
        kill switch if thresholds are breached."""

        # Update consecutive losses
        if pnl_pct < 0:
            self.consecutive_losses += 1
            logger.info(
                f"[KILL] Trade closed — P&L: {pnl_pct:+.2f}% — "
                f"consecutive losses: {self.consecutive_losses}/{KILL_SWITCH_CONSECUTIVE_LOSSES}"
            )
        else:
            self.consecutive_losses = 0
            logger.info(f"[KILL] Trade closed — P&L: {pnl_pct:+.2f}% — consecutive losses: 0")

        # Update peak equity
        if current_equity > self.peak_equity:
            self.peak_equity = current_equity

        # Check equity drop
        equity_drop_pct = ((self.peak_equity - current_equity) / self.peak_equity) * 100

        # Check kill switch conditions
        if self.consecutive_losses >= KILL_SWITCH_CONSECUTIVE_LOSSES:
            self._activate(f"{self.consecutive_losses} consecutive losses")
        elif equity_drop_pct >= KILL_SWITCH_EQUITY_DROP_PERCENT:
            self._activate(f"equity drop {equity_drop_pct:.1f}% from peak")

    # ------------------------------------------------------------------
    # State queries
    # ------------------------------------------------------------------

    def is_paused(self):
        """Return True if trading is paused. Handles pause expiry."""
        if not self.trading_paused:
            return False
        if self.pause_until and datetime.now() >= self.pause_until:
            self.trading_paused = False
            self.consecutive_losses = 0
            self.pause_until = None
            logger.info("[KILL] Kill switch expired — trading resumed")
            return False
        return True

    def get_status(self):
        """Return dict with kill switch state (for logging/monitoring)."""
        return {
            'paused': self.trading_paused,
            'consecutive_losses': self.consecutive_losses,
            'peak_equity': self.peak_equity,
            'pause_until': self.pause_until.isoformat() if self.pause_until else None,
        }

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    def _activate(self, reason):
        """Activate kill switch pause."""
        self.trading_paused = True
        self.pause_until = datetime.now() + timedelta(hours=KILL_SWITCH_PAUSE_HOURS)
        logger.warning(
            f"[KILL] KILL SWITCH ACTIVATED — reason: {reason} — "
            f"paused until {self.pause_until.strftime('%Y-%m-%d %H:%M')}"
        )
