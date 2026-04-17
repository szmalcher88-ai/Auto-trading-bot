"""Unit tests for bot/kill_switch.py — trading pause logic."""

import pytest
from datetime import datetime, timedelta
from unittest.mock import patch
from bot.kill_switch import KillSwitch


class TestKillSwitchInitialization:
    """Tests for KillSwitch initialization."""

    def test_initial_state(self):
        """Test kill switch starts with correct initial state."""
        ks = KillSwitch(initial_equity=10000.0)
        
        assert ks.consecutive_losses == 0
        assert ks.peak_equity == 10000.0
        assert ks.trading_paused is False
        assert ks.pause_until is None


class TestKillSwitchEvaluate:
    """Tests for evaluate method."""

    def test_winning_trade_resets_consecutive_losses(self):
        """Test winning trade resets consecutive loss counter."""
        ks = KillSwitch(initial_equity=10000.0)
        ks.consecutive_losses = 3
        
        ks.evaluate(pnl_pct=2.5, current_equity=10250.0)
        
        assert ks.consecutive_losses == 0
        assert not ks.trading_paused

    def test_losing_trade_increments_counter(self):
        """Test losing trade increments consecutive loss counter."""
        ks = KillSwitch(initial_equity=10000.0)
        
        ks.evaluate(pnl_pct=-1.5, current_equity=9850.0)
        
        assert ks.consecutive_losses == 1
        assert not ks.trading_paused

    def test_consecutive_losses_trigger_kill_switch(self):
        """Test kill switch activates after consecutive losses threshold."""
        ks = KillSwitch(initial_equity=10000.0)
        
        for i in range(4):
            ks.evaluate(pnl_pct=-1.0, current_equity=10000.0 - (i + 1) * 100)
        
        assert ks.consecutive_losses == 4
        assert not ks.trading_paused
        
        ks.evaluate(pnl_pct=-1.0, current_equity=9500.0)
        
        assert ks.consecutive_losses == 5
        assert ks.trading_paused
        assert ks.pause_until is not None

    def test_equity_drop_triggers_kill_switch(self):
        """Test kill switch activates on equity drop threshold."""
        ks = KillSwitch(initial_equity=10000.0)
        ks.peak_equity = 12000.0
        
        ks.evaluate(pnl_pct=-5.0, current_equity=10700.0)
        
        assert ks.trading_paused
        assert ks.pause_until is not None

    def test_peak_equity_updates_on_new_high(self):
        """Test peak equity updates when equity increases."""
        ks = KillSwitch(initial_equity=10000.0)
        
        ks.evaluate(pnl_pct=5.0, current_equity=10500.0)
        assert ks.peak_equity == 10500.0
        
        ks.evaluate(pnl_pct=3.0, current_equity=10815.0)
        assert ks.peak_equity == 10815.0

    def test_peak_equity_does_not_decrease(self):
        """Test peak equity never decreases."""
        ks = KillSwitch(initial_equity=10000.0)
        ks.peak_equity = 12000.0
        
        ks.evaluate(pnl_pct=-2.0, current_equity=11000.0)
        
        assert ks.peak_equity == 12000.0


class TestKillSwitchIsPaused:
    """Tests for is_paused method."""

    def test_not_paused_returns_false(self):
        """Test is_paused returns False when not activated."""
        ks = KillSwitch(initial_equity=10000.0)
        
        assert ks.is_paused() is False

    def test_paused_returns_true(self):
        """Test is_paused returns True when activated."""
        ks = KillSwitch(initial_equity=10000.0)
        ks.trading_paused = True
        ks.pause_until = datetime.now() + timedelta(hours=24)
        
        assert ks.is_paused() is True

    def test_expired_pause_auto_resumes(self):
        """Test pause expires and auto-resumes after timeout."""
        ks = KillSwitch(initial_equity=10000.0)
        ks.trading_paused = True
        ks.consecutive_losses = 5
        ks.pause_until = datetime.now() - timedelta(hours=1)
        
        assert ks.is_paused() is False
        assert ks.trading_paused is False
        assert ks.consecutive_losses == 0
        assert ks.pause_until is None

    def test_pause_not_expired_stays_paused(self):
        """Test pause remains active before expiry."""
        ks = KillSwitch(initial_equity=10000.0)
        ks.trading_paused = True
        ks.pause_until = datetime.now() + timedelta(hours=23)
        
        assert ks.is_paused() is True
        assert ks.trading_paused is True


class TestKillSwitchGetStatus:
    """Tests for get_status method."""

    def test_get_status_returns_dict(self):
        """Test get_status returns complete state dict."""
        ks = KillSwitch(initial_equity=10000.0)
        ks.consecutive_losses = 2
        ks.peak_equity = 11000.0
        
        status = ks.get_status()
        
        assert isinstance(status, dict)
        assert status['paused'] is False
        assert status['consecutive_losses'] == 2
        assert status['peak_equity'] == 11000.0
        assert status['pause_until'] is None

    def test_get_status_with_active_pause(self):
        """Test get_status includes pause_until when paused."""
        ks = KillSwitch(initial_equity=10000.0)
        pause_time = datetime.now() + timedelta(hours=24)
        ks.trading_paused = True
        ks.pause_until = pause_time
        
        status = ks.get_status()
        
        assert status['paused'] is True
        assert status['pause_until'] == pause_time.isoformat()


class TestKillSwitchIntegration:
    """Integration tests for full kill switch workflow."""

    def test_full_consecutive_loss_scenario(self):
        """Test complete scenario of consecutive losses triggering pause."""
        ks = KillSwitch(initial_equity=10000.0)
        
        for i in range(5):
            assert not ks.is_paused()
            ks.evaluate(pnl_pct=-1.0, current_equity=10000.0 - (i + 1) * 100)
        
        assert ks.is_paused()
        assert ks.consecutive_losses == 5
        
        status = ks.get_status()
        assert status['paused'] is True
        assert status['consecutive_losses'] == 5

    def test_recovery_after_pause_expiry(self):
        """Test trading resumes after pause expires."""
        ks = KillSwitch(initial_equity=10000.0)
        
        for i in range(5):
            ks.evaluate(pnl_pct=-1.0, current_equity=10000.0 - (i + 1) * 100)
        
        assert ks.is_paused()
        
        ks.pause_until = datetime.now() - timedelta(seconds=1)
        
        assert not ks.is_paused()
        assert ks.consecutive_losses == 0

    def test_mixed_wins_and_losses(self):
        """Test counter resets correctly with mixed results."""
        ks = KillSwitch(initial_equity=10000.0)
        
        ks.evaluate(pnl_pct=-1.0, current_equity=9900.0)
        assert ks.consecutive_losses == 1
        
        ks.evaluate(pnl_pct=-1.0, current_equity=9800.0)
        assert ks.consecutive_losses == 2
        
        ks.evaluate(pnl_pct=2.0, current_equity=9996.0)
        assert ks.consecutive_losses == 0
        
        ks.evaluate(pnl_pct=-1.0, current_equity=9896.0)
        assert ks.consecutive_losses == 1
