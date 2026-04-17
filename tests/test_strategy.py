"""Unit tests for bot/strategy.py — signal logic and stop loss."""

import numpy as np
import pytest
from unittest.mock import patch, Mock
from bot.strategy import Strategy


class TestStrategyInitialization:
    """Tests for Strategy initialization."""

    def test_initial_state(self):
        """Test strategy starts with correct initial state."""
        strategy = Strategy()
        
        assert strategy.state == 'flat'
        assert strategy.entry_price == 0.0
        assert strategy.stop_loss == 0.0
        assert strategy.pending_re_entry is False
        assert strategy.bars_since_exit == 0
        assert strategy.last_exit_type is None
        assert strategy.last_atr is None


class TestCalculateStopLoss:
    """Tests for _calculate_stop_loss method."""

    @patch('bot.strategy.SL_TYPE', 'percent')
    @patch('bot.strategy.SL_PERCENT', 2.5)
    def test_percent_sl_long(self):
        """Test percent-based SL for long position."""
        strategy = Strategy()
        
        sl = strategy._calculate_stop_loss('long', price=2000.0)
        
        assert sl == pytest.approx(1950.0, rel=0.001)

    @patch('bot.strategy.SL_TYPE', 'percent')
    @patch('bot.strategy.SL_PERCENT', 2.5)
    def test_percent_sl_short(self):
        """Test percent-based SL for short position."""
        strategy = Strategy()
        
        sl = strategy._calculate_stop_loss('short', price=2000.0)
        
        assert sl == pytest.approx(2050.0, rel=0.001)

    @patch('bot.strategy.SL_TYPE', 'atr')
    @patch('bot.strategy.ATR_MULTIPLIER', 5.0)
    def test_atr_sl_long(self):
        """Test ATR-based SL for long position."""
        strategy = Strategy()
        
        sl = strategy._calculate_stop_loss('long', price=2000.0, atr_value=20.0)
        
        assert sl == pytest.approx(1900.0, rel=0.001)

    @patch('bot.strategy.SL_TYPE', 'atr')
    @patch('bot.strategy.ATR_MULTIPLIER', 5.0)
    def test_atr_sl_short(self):
        """Test ATR-based SL for short position."""
        strategy = Strategy()
        
        sl = strategy._calculate_stop_loss('short', price=2000.0, atr_value=20.0)
        
        assert sl == pytest.approx(2100.0, rel=0.001)

    @patch('bot.strategy.SL_TYPE', 'atr')
    @patch('bot.strategy.SL_PERCENT', 2.5)
    def test_atr_sl_fallback_to_percent_when_no_atr(self):
        """Test falls back to percent SL when ATR not available."""
        strategy = Strategy()
        
        sl = strategy._calculate_stop_loss('long', price=2000.0, atr_value=None)
        
        assert sl == pytest.approx(1950.0, rel=0.001)


class TestComputeATR:
    """Tests for _compute_atr method."""

    @patch('bot.strategy.ATR_PERIOD', 14)
    def test_atr_insufficient_data_returns_none(self):
        """Test ATR returns None when insufficient data."""
        strategy = Strategy()
        
        high = np.array([105, 110, 108])
        low = np.array([95, 100, 98])
        close = np.array([100, 105, 103])
        
        result = strategy._compute_atr(high, low, close)
        
        assert result is None

    @patch('bot.strategy.ATR_PERIOD', 3)
    def test_atr_cold_start_computation(self):
        """Test ATR cold start (no previous ATR)."""
        strategy = Strategy()
        
        high = np.array([105, 110, 108, 112, 115])
        low = np.array([95, 100, 98, 102, 105])
        close = np.array([100, 105, 103, 107, 110])
        
        result = strategy._compute_atr(high, low, close)
        
        assert result is not None
        assert result > 0
        assert strategy.last_atr == result

    @patch('bot.strategy.ATR_PERIOD', 3)
    def test_atr_rma_update(self):
        """Test ATR uses RMA (Wilder's smoothing) for updates."""
        strategy = Strategy()
        
        high = np.array([105, 110, 108, 112])
        low = np.array([95, 100, 98, 102])
        close = np.array([100, 105, 103, 107])
        
        atr1 = strategy._compute_atr(high, low, close)
        
        high2 = np.array([105, 110, 108, 112, 125])
        low2 = np.array([95, 100, 98, 102, 105])
        close2 = np.array([100, 105, 103, 107, 120])
        
        atr2 = strategy._compute_atr(high2, low2, close2)
        
        assert atr2 is not None
        assert atr2 > atr1
        assert strategy.last_atr == atr2

    @patch('bot.strategy.ATR_PERIOD', 20)
    def test_atr_production_parameters(self):
        """Test ATR with production parameters."""
        strategy = Strategy()
        
        np.random.seed(42)
        high = np.random.uniform(2000, 2500, 100)
        low = high - np.random.uniform(10, 50, 100)
        close = (high + low) / 2
        
        result = strategy._compute_atr(high, low, close)
        
        assert result is not None
        assert result > 0
        assert strategy.last_atr == result


class TestUpdateTrailingSL:
    """Tests for _update_trailing_sl method."""

    @patch('bot.strategy.USE_DYNAMIC_SL', False)
    def test_trailing_sl_disabled_does_nothing(self):
        """Test trailing SL does nothing when disabled."""
        strategy = Strategy()
        strategy.state = 'long'
        strategy.stop_loss = 1950.0
        
        strategy._update_trailing_sl(close_price=2100.0)
        
        assert strategy.stop_loss == 1950.0

    @patch('bot.strategy.USE_DYNAMIC_SL', True)
    @patch('bot.strategy.TRAILING_SL_MODE', 'pine')
    @patch('bot.strategy.SL_TYPE', 'percent')
    @patch('bot.strategy.SL_PERCENT', 2.5)
    def test_trailing_sl_tightens_long(self):
        """Test trailing SL tightens for long position."""
        strategy = Strategy()
        strategy.state = 'long'
        strategy.stop_loss = 1950.0
        
        strategy._update_trailing_sl(close_price=2100.0)
        
        assert strategy.stop_loss > 1950.0
        assert strategy.stop_loss == pytest.approx(2047.5, rel=0.001)

    @patch('bot.strategy.USE_DYNAMIC_SL', True)
    @patch('bot.strategy.TRAILING_SL_MODE', 'pine')
    @patch('bot.strategy.SL_TYPE', 'percent')
    @patch('bot.strategy.SL_PERCENT', 2.5)
    def test_trailing_sl_does_not_loosen_long(self):
        """Test trailing SL never loosens for long position."""
        strategy = Strategy()
        strategy.state = 'long'
        strategy.stop_loss = 2000.0
        
        strategy._update_trailing_sl(close_price=2050.0)
        
        assert strategy.stop_loss == 2000.0

    @patch('bot.strategy.USE_DYNAMIC_SL', True)
    @patch('bot.strategy.TRAILING_SL_MODE', 'execution')
    @patch('bot.strategy.SL_TYPE', 'percent')
    @patch('bot.strategy.SL_PERCENT', 2.5)
    def test_trailing_sl_uses_high_for_execution_mode(self):
        """Test execution mode uses high for long positions."""
        strategy = Strategy()
        strategy.state = 'long'
        strategy.stop_loss = 1950.0
        
        strategy._update_trailing_sl(close_price=2100.0, high_price=2150.0, low_price=2050.0)
        
        expected_sl = 2150.0 * (1 - 0.025)
        assert strategy.stop_loss == pytest.approx(expected_sl, rel=0.001)

    @patch('bot.strategy.USE_DYNAMIC_SL', True)
    @patch('bot.strategy.TRAILING_SL_MODE', 'pine')
    @patch('bot.strategy.SL_TYPE', 'atr')
    @patch('bot.strategy.ATR_MULTIPLIER', 5.0)
    def test_trailing_sl_with_atr(self):
        """Test trailing SL uses ATR when configured."""
        strategy = Strategy()
        strategy.state = 'long'
        strategy.stop_loss = 1900.0
        
        strategy._update_trailing_sl(close_price=2100.0, atr_value=20.0)
        
        expected_sl = 2100.0 - (20.0 * 5.0)
        assert strategy.stop_loss == pytest.approx(expected_sl, rel=0.001)


class TestOnOpen:
    """Tests for on_open method."""

    @patch('bot.strategy.SL_TYPE', 'percent')
    @patch('bot.strategy.SL_PERCENT', 2.5)
    def test_on_open_long_sets_state(self):
        """Test on_open sets correct state for long position."""
        strategy = Strategy()
        
        strategy.on_open('long', fill_price=2000.0)
        
        assert strategy.state == 'long'
        assert strategy.entry_price == 2000.0
        assert strategy.stop_loss == pytest.approx(1950.0, rel=0.001)
        assert strategy.pending_re_entry is False

    @patch('bot.strategy.SL_TYPE', 'atr')
    @patch('bot.strategy.ATR_MULTIPLIER', 5.0)
    def test_on_open_uses_atr_for_sl(self):
        """Test on_open uses ATR for SL calculation when configured."""
        strategy = Strategy()
        strategy.last_atr = 20.0
        
        strategy.on_open('long', fill_price=2000.0)
        
        assert strategy.stop_loss == pytest.approx(1900.0, rel=0.001)


class TestOnClose:
    """Tests for on_close method."""

    @patch('bot.strategy.ENABLE_RE_ENTRY', True)
    @patch('bot.strategy.RE_ENTRY_DELAY', 2)
    def test_on_close_enables_re_entry(self):
        """Test on_close enables re-entry tracking."""
        strategy = Strategy()
        strategy.state = 'long'
        
        strategy.on_close('long')
        
        assert strategy.state == 'flat'
        assert strategy.entry_price == 0.0
        assert strategy.stop_loss == 0.0
        assert strategy.pending_re_entry is True
        assert strategy.last_exit_type == 'long'
        assert strategy.bars_since_exit == 0

    @patch('bot.strategy.ENABLE_RE_ENTRY', False)
    def test_on_close_without_re_entry(self):
        """Test on_close when re-entry disabled."""
        strategy = Strategy()
        strategy.state = 'long'
        
        strategy.on_close('long')
        
        assert strategy.state == 'flat'
        assert strategy.pending_re_entry is False


class TestSyncState:
    """Tests for sync_state method."""

    def test_sync_state_from_exchange_long(self):
        """Test syncing state from exchange long position."""
        strategy = Strategy()
        
        strategy.sync_state(position='long', entry_price=2000.0)
        
        assert strategy.state == 'long'
        assert strategy.entry_price == 2000.0

    def test_sync_state_from_exchange_flat(self):
        """Test syncing state when no position."""
        strategy = Strategy()
        strategy.state = 'long'
        
        strategy.sync_state(position=None, entry_price=0.0)
        
        assert strategy.state == 'flat'
        assert strategy.entry_price == 0.0


class TestCalculateSignalsIntegration:
    """Integration tests for calculate_signals method."""

    @patch('bot.strategy.LOOKBACK_WINDOW', 88)
    @patch('bot.strategy.REGRESSION_LEVEL', 71)
    @patch('bot.strategy.RELATIVE_WEIGHT', 1.0)
    @patch('bot.strategy.LAG', 1)
    @patch('bot.strategy.USE_KERNEL_SMOOTHING', True)
    @patch('bot.strategy.VOLATILITY_MIN', 3)
    @patch('bot.strategy.VOLATILITY_MAX', 5)
    @patch('bot.strategy.SL_TYPE', 'percent')
    def test_calculate_signals_returns_complete_dict(self):
        """Test calculate_signals returns all required fields."""
        strategy = Strategy()
        
        ohlcv = {
            'open': np.random.uniform(2000, 2100, 150),
            'high': np.random.uniform(2050, 2150, 150),
            'low': np.random.uniform(1950, 2050, 150),
            'close': np.random.uniform(2000, 2100, 150),
            'volume': np.random.uniform(1000, 5000, 150)
        }
        
        result = strategy.calculate_signals(ohlcv)
        
        assert 'action' in result
        assert 'reason' in result
        assert 'details' in result
        details = result['details']
        assert 'yhat1' in details
        assert 'yhat2' in details
        assert 'is_bullish' in details
        assert 'vol_passes' in details
        assert 'state' in details

    @patch('bot.strategy.LOOKBACK_WINDOW', 20)
    @patch('bot.strategy.REGRESSION_LEVEL', 15)
    @patch('bot.strategy.RELATIVE_WEIGHT', 5.0)
    @patch('bot.strategy.LAG', 1)
    @patch('bot.strategy.USE_KERNEL_SMOOTHING', True)
    @patch('bot.strategy.SL_TYPE', 'atr')
    @patch('bot.strategy.ATR_PERIOD', 14)
    def test_calculate_signals_includes_atr(self):
        """Test calculate_signals includes ATR when SL_TYPE is atr."""
        strategy = Strategy()
        
        ohlcv = {
            'open': np.random.uniform(2000, 2100, 50),
            'high': np.random.uniform(2050, 2150, 50),
            'low': np.random.uniform(1950, 2050, 50),
            'close': np.random.uniform(2000, 2100, 50),
            'volume': np.random.uniform(1000, 5000, 50)
        }
        
        result = strategy.calculate_signals(ohlcv)
        
        assert 'details' in result
        assert 'atr' in result['details']
        if result['details']['atr'] is not None:
            assert result['details']['atr'] > 0

    @patch('bot.strategy.LOOKBACK_WINDOW', 20)
    @patch('bot.strategy.REGRESSION_LEVEL', 15)
    @patch('bot.strategy.RELATIVE_WEIGHT', 5.0)
    @patch('bot.strategy.LAG', 1)
    @patch('bot.strategy.USE_KERNEL_SMOOTHING', True)
    def test_calculate_signals_bullish_crossover(self):
        """Test bullish signal detection."""
        strategy = Strategy()
        
        close = np.linspace(1900, 2100, 50)
        ohlcv = {
            'open': close - 5,
            'high': close + 10,
            'low': close - 10,
            'close': close,
            'volume': np.full(50, 2000.0)
        }
        
        result = strategy.calculate_signals(ohlcv)
        
        assert result['details']['is_bullish'] is True

    @patch('bot.strategy.LOOKBACK_WINDOW', 20)
    @patch('bot.strategy.REGRESSION_LEVEL', 15)
    @patch('bot.strategy.RELATIVE_WEIGHT', 5.0)
    @patch('bot.strategy.LAG', 1)
    @patch('bot.strategy.USE_KERNEL_SMOOTHING', True)
    def test_calculate_signals_bearish_crossover(self):
        """Test bearish signal detection."""
        strategy = Strategy()
        
        close = np.linspace(2100, 1900, 50)
        ohlcv = {
            'open': close + 5,
            'high': close + 10,
            'low': close - 10,
            'close': close,
            'volume': np.full(50, 2000.0)
        }
        
        result = strategy.calculate_signals(ohlcv)
        
        assert result['details']['is_bullish'] is False

    @pytest.mark.skip(reason="Requires full OHLCV setup with proper volatility filter")
    def test_sl_hit_closes_position(self):
        """Test SL hit triggers position close."""
        pass

    @pytest.mark.skip(reason="Requires full signal flow setup")
    def test_re_entry_after_delay(self):
        """Test re-entry logic after delay period."""
        pass
