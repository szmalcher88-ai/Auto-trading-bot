"""
Thread-safe shared state between trading loop and API server.

Locking strategy (granular — reduces contention):
  _position_lock  — position_state, entry_price, stop_loss, position_size, unrealized_pnl
  _signal_lock    — last_signal, last_action, last_reason, signal_seq, action_seq,
                    signal_history, last_loop_time
  _config_lock    — config, config_changed (RLock — allows nested reads in same thread)
  _balance_lock   — balance, peak_equity
  _status_lock    — bot_running, trading_paused, next_candle_time (lightweight flags)

Events (lock-free, thread-safe by design):
  config_changed  — set when config is updated from dashboard
  emergency_close — set when emergency close is requested

Signal state (signal_seq, action_seq, signal_history, last_signal) is persisted to
STATE_FILE on every update so it survives process restarts.
"""

import json
import logging
import os
import threading
import time as time_module
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)

STATE_FILE = 'signal_state.json'

# Sentinel — lets __init__ distinguish "caller passed None" from "use default"
_UNSET = object()


class SharedState:
    """Thread-safe shared state between trading loop and FastAPI."""

    def __init__(self, state_file=_UNSET):
        # Path used for signal state persistence (None disables persistence).
        # Looked up at call time so test fixtures can patch bot.state.STATE_FILE.
        self._state_file = STATE_FILE if state_file is _UNSET else state_file

        # Granular locks
        self._position_lock = threading.Lock()
        self._signal_lock = threading.Lock()
        self._config_lock = threading.RLock()   # reentrant — safe for nested config reads
        self._balance_lock = threading.Lock()
        self._status_lock = threading.Lock()

        # Bot status flags
        self.bot_running = False
        self.trading_paused = False
        self.next_candle_time = None

        # Current position
        self.position_state = 'flat'
        self.entry_price = 0.0
        self.stop_loss = 0.0
        self.position_size = 0.0
        self.unrealized_pnl = 0.0

        # Last signal info — loaded from disk if available
        self.last_signal: Dict[str, Any] = {}
        self.last_action: Optional[str] = None
        self.last_reason: Optional[str] = None
        self.signal_seq = 0
        self.action_seq = 0
        self.signal_history = []
        self.last_loop_time = None

        self._load_signal_state()

        # Config (mutable — can be changed from dashboard)
        self.config: Dict[str, Any] = {}
        self.config_changed = threading.Event()

        # Emergency actions
        self.emergency_close = threading.Event()

        # Balance
        self.balance = 0.0
        self.peak_equity = 0.0

    # ------------------------------------------------------------------
    # Signal state persistence
    # ------------------------------------------------------------------

    def _load_signal_state(self):
        """Restore signal counters and history from disk (survives restarts)."""
        if not self._state_file or not os.path.exists(self._state_file):
            return
        try:
            with open(self._state_file, 'r', encoding='utf-8') as f:
                data = json.load(f)
            self.signal_seq = int(data.get('signal_seq', 0))
            self.action_seq = int(data.get('action_seq', 0))
            self.signal_history = list(data.get('signal_history', []))
            self.last_signal = dict(data.get('last_signal', {}))
            self.last_action = data.get('last_action')
            self.last_reason = data.get('last_reason')
            logger.info(
                f"[STATE] Restored signal state from disk: "
                f"signal_seq={self.signal_seq}, action_seq={self.action_seq}, "
                f"history_len={len(self.signal_history)}"
            )
        except Exception as exc:
            logger.warning(f"[STATE] Could not load {self._state_file}: {exc} — starting fresh")

    def _persist_signal_state(self):
        """Write current signal state to disk (called while _signal_lock is held)."""
        if not self._state_file:
            return
        try:
            data = {
                'signal_seq': self.signal_seq,
                'action_seq': self.action_seq,
                'signal_history': self.signal_history,
                'last_signal': self.last_signal,
                'last_action': self.last_action,
                'last_reason': self.last_reason,
            }
            tmp = self._state_file + '.tmp'
            with open(tmp, 'w', encoding='utf-8') as f:
                json.dump(data, f)
            os.replace(tmp, self._state_file)
        except Exception as exc:
            logger.warning(f"[STATE] Could not persist signal state: {exc}")

    # ------------------------------------------------------------------
    # Writes
    # ------------------------------------------------------------------

    def update_signal(self, signal_data: dict):
        with self._signal_lock:
            signal_data = signal_data.copy()
            action = signal_data.pop("action", None)
            reason = signal_data.pop("reason", None)
            self.last_signal = signal_data
            self.last_action = action
            self.last_reason = reason
            self.last_loop_time = time_module.time()
            self.signal_seq += 1
            if action is not None:
                self.action_seq += 1
            self.signal_history.append({
                "action": action,
                "reason": reason,
                "timestamp": time_module.time(),
                "symbol": signal_data.get("symbol"),
                "is_bullish": signal_data.get("is_bullish"),
                "bullish_change": signal_data.get("bullish_change"),
                "bearish_change": signal_data.get("bearish_change"),
                "vol_passes": signal_data.get("vol_passes"),
                "yhat1": signal_data.get("yhat1"),
                "yhat2": signal_data.get("yhat2"),
                "signal_seq": self.signal_seq,
            })
            if len(self.signal_history) > 100:
                self.signal_history = self.signal_history[-100:]
            self._persist_signal_state()

    def update_position(self, state, entry=0.0, sl=0.0, size=0.0, pnl=0.0):
        with self._position_lock:
            self.position_state = state
            self.entry_price = entry
            self.stop_loss = sl
            self.position_size = size
            self.unrealized_pnl = pnl

    def update_balance(self, balance, peak_equity=None):
        with self._balance_lock:
            self.balance = balance
            if peak_equity is not None:
                self.peak_equity = peak_equity

    def update_config(self, new_config: dict):
        with self._config_lock:
            self.config.update(new_config)
            self.config_changed.set()

    # ------------------------------------------------------------------
    # Reads
    # ------------------------------------------------------------------

    def get_config_snapshot(self) -> dict:
        """Return an immutable copy of config under the config lock.

        Use this instead of accessing ``shared_state.config`` directly to
        avoid reading a partially-updated dict from another thread.
        """
        with self._config_lock:
            return dict(self.config)

    def get_status(self) -> dict:
        # Acquire all relevant locks in a fixed order to prevent deadlocks.
        with self._status_lock:
            bot_running = self.bot_running
            trading_paused = self.trading_paused
            next_candle = self.next_candle_time

        with self._position_lock:
            position_state = self.position_state
            entry_price = self.entry_price
            stop_loss = self.stop_loss
            position_size = self.position_size
            unrealized_pnl = self.unrealized_pnl

        with self._balance_lock:
            balance = self.balance
            peak_equity = self.peak_equity

        with self._signal_lock:
            last_loop = self.last_loop_time
            last_signal = self.last_signal.copy()
            last_action = self.last_action
            last_reason = self.last_reason
            signal_seq = self.signal_seq
            action_seq = self.action_seq

        return {
            'bot_running': bot_running,
            'trading_paused': trading_paused,
            'position': position_state,
            'entry_price': entry_price,
            'stop_loss': stop_loss,
            'position_size': position_size,
            'unrealized_pnl': unrealized_pnl,
            'balance': balance,
            'peak_equity': peak_equity,
            'last_loop': last_loop,
            'next_candle': next_candle,
            'last_signal': last_signal,
            'last_action': last_action,
            'last_reason': last_reason,
            'signal_seq': signal_seq,
            'action_seq': action_seq,
        }

    def get_signal_history(self):
        with self._signal_lock:
            return list(self.signal_history)
