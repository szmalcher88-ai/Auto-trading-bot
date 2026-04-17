"""Unit tests for bot/state.py — shared state management."""

import pytest
import time
from bot.state import SharedState


class TestSharedStateInitialization:
    """Tests for SharedState initialization."""

    def test_initial_state(self):
        """Test state initializes with correct defaults."""
        state = SharedState()

        assert state.bot_running is False
        assert state.trading_paused is False
        assert state.position_state == 'flat'
        assert state.entry_price == 0.0
        assert state.stop_loss == 0.0
        assert state.balance == 0.0
        assert state.last_action is None
        assert state.last_reason is None
        assert state.signal_seq == 0
        assert state.action_seq == 0
        assert state.signal_history == []

    def test_granular_locks_exist(self):
        """Test granular locks replace the old single _lock."""
        import threading
        state = SharedState()

        assert isinstance(state._position_lock, type(threading.Lock()))
        assert isinstance(state._signal_lock, type(threading.Lock()))
        assert isinstance(state._balance_lock, type(threading.Lock()))
        assert isinstance(state._config_lock, type(threading.RLock()))
        assert not hasattr(state, '_lock'), "Old single _lock must not exist"

    def test_events_initialized(self):
        """Test threading Events are initialized."""
        import threading
        state = SharedState()

        assert isinstance(state.config_changed, threading.Event)
        assert isinstance(state.emergency_close, threading.Event)
        assert not state.config_changed.is_set()
        assert not state.emergency_close.is_set()


class TestUpdateSignal:
    """Tests for update_signal method."""

    def test_update_signal_increments_signal_seq(self):
        """Test signal_seq increments on every update."""
        state = SharedState()
        
        state.update_signal({"yhat1": 2100.0, "yhat2": 2095.0})
        assert state.signal_seq == 1
        
        state.update_signal({"yhat1": 2105.0, "yhat2": 2100.0})
        assert state.signal_seq == 2

    def test_update_signal_with_action_increments_action_seq(self):
        """Test action_seq increments only when action is present."""
        state = SharedState()
        
        state.update_signal({"action": "open_long", "reason": "bullish_crossover"})
        assert state.signal_seq == 1
        assert state.action_seq == 1
        assert state.last_action == "open_long"
        assert state.last_reason == "bullish_crossover"

    def test_update_signal_without_action_does_not_increment_action_seq(self):
        """Test action_seq stays same when no action."""
        state = SharedState()
        
        state.update_signal({"yhat1": 2100.0})
        assert state.signal_seq == 1
        assert state.action_seq == 0
        assert state.last_action is None

    def test_update_signal_adds_to_history(self):
        """Test actions are added to signal_history."""
        state = SharedState()
        
        state.update_signal({
            "action": "open_long",
            "reason": "bullish_crossover",
            "symbol": "ETHUSDT",
            "is_bullish": True,
            "yhat1": 2100.0,
            "yhat2": 2095.0
        })
        
        assert len(state.signal_history) == 1
        assert state.signal_history[0]["action"] == "open_long"
        assert state.signal_history[0]["reason"] == "bullish_crossover"
        assert state.signal_history[0]["symbol"] == "ETHUSDT"
        assert state.signal_history[0]["is_bullish"] is True
        assert state.signal_history[0]["yhat1"] == 2100.0

    def test_signal_history_caps_at_100(self):
        """Test signal_history maintains max 100 entries."""
        state = SharedState()
        
        for i in range(110):
            state.update_signal({
                "action": f"action_{i}",
                "reason": f"reason_{i}",
                "symbol": "ETHUSDT"
            })
        
        assert len(state.signal_history) == 100
        assert state.signal_history[0]["action"] == "action_10"
        assert state.signal_history[-1]["action"] == "action_109"

    def test_signal_without_action_is_in_history(self):
        """Test signals without action are still recorded in history (every candle logged)."""
        state = SharedState()
        
        state.update_signal({"yhat1": 2100.0, "yhat2": 2095.0})
        
        assert len(state.signal_history) == 1
        assert state.signal_history[0]["action"] is None
        assert state.signal_history[0]["yhat1"] == 2100.0
        assert state.signal_seq == 1

    def test_update_signal_sets_last_loop_time(self):
        """Test update_signal sets last_loop_time."""
        state = SharedState()
        before = time.time()
        
        state.update_signal({"yhat1": 2100.0})
        
        assert state.last_loop_time is not None
        assert state.last_loop_time >= before


class TestUpdatePosition:
    """Tests for update_position method."""

    def test_update_position_sets_all_fields(self):
        """Test update_position updates all position fields."""
        state = SharedState()
        
        state.update_position(
            state='long',
            entry=2100.0,
            sl=2050.0,
            size=0.5,
            pnl=25.0
        )
        
        assert state.position_state == 'long'
        assert state.entry_price == 2100.0
        assert state.stop_loss == 2050.0
        assert state.position_size == 0.5
        assert state.unrealized_pnl == 25.0

    def test_update_position_to_flat(self):
        """Test closing position sets state to flat."""
        state = SharedState()
        state.update_position(state='long', entry=2100.0, sl=2050.0)
        
        state.update_position(state='flat', entry=0.0, sl=0.0, size=0.0, pnl=0.0)
        
        assert state.position_state == 'flat'
        assert state.entry_price == 0.0


class TestUpdateBalance:
    """Tests for update_balance method."""

    def test_update_balance_sets_balance(self):
        """Test update_balance updates balance field."""
        state = SharedState()
        
        state.update_balance(balance=10500.0)
        
        assert state.balance == 10500.0

    def test_update_balance_with_peak_equity(self):
        """Test update_balance updates peak_equity when provided."""
        state = SharedState()
        
        state.update_balance(balance=10500.0, peak_equity=11000.0)
        
        assert state.balance == 10500.0
        assert state.peak_equity == 11000.0


class TestUpdateConfig:
    """Tests for update_config method."""

    def test_update_config_merges_values(self):
        """Test update_config merges new values into existing config."""
        state = SharedState()
        state.config = {'lookback_window': 110, 'relative_weight': 10.0}

        state.update_config({'lookback_window': 88})

        assert state.config['lookback_window'] == 88
        assert state.config['relative_weight'] == 10.0

    def test_update_config_sets_event(self):
        """Test update_config sets config_changed event."""
        state = SharedState()

        assert not state.config_changed.is_set()

        state.update_config({'lookback_window': 88})

        assert state.config_changed.is_set()


class TestGetConfigSnapshot:
    """Tests for get_config_snapshot method."""

    def test_get_config_snapshot_returns_copy(self):
        """Test get_config_snapshot returns an independent copy."""
        state = SharedState()
        state.config = {'lookback_window': 110, 'relative_weight': 10.0}

        snapshot = state.get_config_snapshot()
        snapshot['lookback_window'] = 999  # mutate snapshot

        assert state.config['lookback_window'] == 110  # original unchanged

    def test_get_config_snapshot_reflects_current_values(self):
        """Test snapshot contains up-to-date values."""
        state = SharedState()
        state.update_config({'lookback_window': 88, 'lag': 2})

        snapshot = state.get_config_snapshot()

        assert snapshot['lookback_window'] == 88
        assert snapshot['lag'] == 2

    def test_get_config_snapshot_empty_config(self):
        """Test snapshot of empty config returns empty dict."""
        state = SharedState()
        assert state.get_config_snapshot() == {}

    def test_get_config_snapshot_thread_safe(self):
        """Test concurrent snapshot reads and writes are safe."""
        import threading
        state = SharedState()
        state.config = {'counter': 0}
        errors = []

        def writer():
            for i in range(50):
                state.update_config({'counter': i})

        def reader():
            for _ in range(50):
                try:
                    snap = state.get_config_snapshot()
                    assert isinstance(snap, dict)
                except Exception as exc:
                    errors.append(exc)

        threads = [threading.Thread(target=writer),
                   threading.Thread(target=reader),
                   threading.Thread(target=reader)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert errors == [], f"Thread errors: {errors}"


class TestGetStatus:
    """Tests for get_status method."""

    def test_get_status_returns_complete_dict(self):
        """Test get_status returns all state fields."""
        state = SharedState()
        state.bot_running = True
        state.position_state = 'long'
        state.entry_price = 2100.0
        state.balance = 10500.0
        state.last_action = "open_long"
        state.signal_seq = 5
        state.action_seq = 2
        
        status = state.get_status()
        
        assert status['bot_running'] is True
        assert status['position'] == 'long'
        assert status['entry_price'] == 2100.0
        assert status['balance'] == 10500.0
        assert status['last_action'] == "open_long"
        assert status['signal_seq'] == 5
        assert status['action_seq'] == 2

    def test_get_status_returns_copy_of_signal(self):
        """Test get_status returns a copy of last_signal."""
        state = SharedState()
        state.last_signal = {"yhat1": 2100.0, "yhat2": 2095.0}
        
        status = state.get_status()
        status['last_signal']['yhat1'] = 9999.0
        
        assert state.last_signal['yhat1'] == 2100.0


class TestGetSignalHistory:
    """Tests for get_signal_history method."""

    def test_get_signal_history_returns_list(self):
        """Test get_signal_history returns list of actions."""
        state = SharedState()
        
        state.update_signal({"action": "open_long", "reason": "test"})
        state.update_signal({"action": "close_long", "reason": "sl_hit"})
        
        history = state.get_signal_history()
        
        assert isinstance(history, list)
        assert len(history) == 2
        assert history[0]["action"] == "open_long"
        assert history[1]["action"] == "close_long"

    def test_get_signal_history_returns_copy(self):
        """Test get_signal_history returns a copy, not reference."""
        state = SharedState()
        state.update_signal({"action": "open_long", "reason": "test"})
        
        history = state.get_signal_history()
        history.append({"action": "fake"})
        
        assert len(state.signal_history) == 1


class TestThreadSafety:
    """Tests for thread safety of SharedState."""

    def test_concurrent_signal_updates(self):
        """Test concurrent signal updates are thread-safe."""
        import threading
        state = SharedState()
        
        def update_signals(count):
            for i in range(count):
                state.update_signal({"action": f"action_{i}", "reason": "test"})
        
        threads = [threading.Thread(target=update_signals, args=(25,)) for _ in range(2)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        
        assert state.signal_seq == 50
        assert state.action_seq == 50
        assert len(state.signal_history) == 50  # 50 signals, cap is 100

    def test_concurrent_reads_and_writes(self):
        """Test concurrent reads don't block or corrupt data."""
        import threading
        state = SharedState()
        results = []
        
        def reader():
            for _ in range(10):
                status = state.get_status()
                results.append(status['signal_seq'])
        
        def writer():
            for _ in range(10):
                state.update_signal({"yhat1": 2100.0})
        
        threads = [
            threading.Thread(target=reader),
            threading.Thread(target=writer)
        ]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        
        assert state.signal_seq == 10
        assert len(results) == 10
