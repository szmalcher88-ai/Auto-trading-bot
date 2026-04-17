"""Unit tests for bot/filters.py — volatility filtering."""

import numpy as np
import pytest
from bot.filters import atr, filter_volatility


class TestATR:
    """Tests for ATR (Average True Range) calculation."""

    def test_atr_basic_computation(self):
        """Test ATR computation with simple data."""
        high = np.array([105, 110, 108, 112, 115])
        low = np.array([95, 100, 98, 102, 105])
        close = np.array([100, 105, 103, 107, 110])
        
        result = atr(high, low, close, period=3)
        
        assert len(result) == len(close)
        assert result[2] > 0
        assert result[3] > 0

    def test_atr_wilder_smoothing(self):
        """Test ATR uses Wilder's RMA smoothing."""
        np.random.seed(42)
        high = np.random.uniform(100, 110, 50)
        low = np.random.uniform(90, 100, 50)
        close = np.random.uniform(95, 105, 50)
        
        result = atr(high, low, close, period=14)
        
        assert len(result) == 50
        assert result[13] > 0
        
        for i in range(14, len(result)):
            assert result[i] > 0

    def test_atr_handles_gaps(self):
        """Test ATR correctly handles price gaps."""
        high = np.array([105, 110, 120, 115, 118])
        low = np.array([95, 100, 110, 105, 108])
        close = np.array([100, 105, 115, 110, 113])
        
        result = atr(high, low, close, period=3)
        
        assert result[2] > 10

    def test_atr_production_parameters(self):
        """Test ATR with production parameters."""
        np.random.seed(42)
        high = np.random.uniform(2000, 2500, 200)
        low = high - np.random.uniform(10, 50, 200)
        close = (high + low) / 2 + np.random.uniform(-10, 10, 200)
        
        result = atr(high, low, close, period=20)
        
        assert len(result) == 200
        assert result[19] > 0
        assert not np.isnan(result[19:]).any()


class TestFilterVolatility:
    """Tests for filter_volatility function."""

    def test_filter_disabled_returns_all_true(self):
        """Test filter returns all True when disabled."""
        high = np.random.uniform(100, 110, 50)
        low = np.random.uniform(90, 100, 50)
        close = np.random.uniform(95, 105, 50)
        
        result = filter_volatility(high, low, close, min_length=5, max_length=10, use_filter=False)
        
        assert len(result) == len(close)
        assert result.all()
        assert result.dtype == bool

    def test_filter_compares_short_vs_long_atr(self):
        """Test filter compares short-term vs long-term ATR."""
        high = np.array([105, 110, 108, 112, 115, 120, 118, 125, 123, 130] * 5)
        low = np.array([95, 100, 98, 102, 105, 110, 108, 115, 113, 120] * 5)
        close = np.array([100, 105, 103, 107, 110, 115, 113, 120, 118, 125] * 5)
        
        result = filter_volatility(high, low, close, min_length=3, max_length=10, use_filter=True)
        
        assert len(result) == len(close)
        assert result.dtype == bool
        assert result.any()

    def test_filter_detects_volatility_spike(self):
        """Test filter detects elevated volatility periods."""
        high = np.concatenate([
            np.random.uniform(100, 102, 30),
            np.random.uniform(100, 120, 20),
            np.random.uniform(100, 102, 30)
        ])
        low = high - np.concatenate([
            np.random.uniform(1, 2, 30),
            np.random.uniform(5, 15, 20),
            np.random.uniform(1, 2, 30)
        ])
        close = (high + low) / 2
        
        result = filter_volatility(high, low, close, min_length=5, max_length=20, use_filter=True)
        
        spike_region = result[35:45]
        assert spike_region.sum() > 5

    def test_filter_production_parameters(self):
        """Test filter with production parameters (min=3, max=5)."""
        np.random.seed(42)
        high = np.random.uniform(2000, 2500, 200)
        low = high - np.random.uniform(10, 50, 200)
        close = (high + low) / 2
        
        result = filter_volatility(high, low, close, min_length=3, max_length=5, use_filter=True)
        
        assert len(result) == 200
        assert result.dtype == bool
        assert 0 < result.sum() < 200

    def test_filter_output_is_boolean_array(self):
        """Test filter returns boolean numpy array."""
        high = np.array([105, 110, 108, 112, 115])
        low = np.array([95, 100, 98, 102, 105])
        close = np.array([100, 105, 103, 107, 110])
        
        result = filter_volatility(high, low, close, min_length=2, max_length=3, use_filter=True)
        
        assert isinstance(result, np.ndarray)
        assert result.dtype == bool
