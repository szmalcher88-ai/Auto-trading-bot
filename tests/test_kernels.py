"""Unit tests for bot/kernels.py — Nadaraya-Watson kernel estimators."""

import numpy as np
import pytest
from bot.kernels import rational_quadratic, gaussian


class TestRationalQuadratic:
    """Tests for rational_quadratic kernel."""

    def test_basic_computation(self):
        """Test basic kernel computation with simple data."""
        prices = np.array([100.0, 101.0, 102.0, 103.0, 104.0, 105.0] * 20)
        result = rational_quadratic(prices, lookback=10, relative_weight=5.0, start_at_bar=10)
        
        assert len(result) == len(prices)
        assert np.isnan(result[:10]).all()
        assert not np.isnan(result[10:]).any()
        assert np.all(result[10:] > 0)

    def test_output_length_matches_input(self):
        """Test output array has same length as input."""
        prices = np.array([100.0] * 100)
        result = rational_quadratic(prices, lookback=50, relative_weight=10.0, start_at_bar=25)
        
        assert len(result) == len(prices)

    def test_insufficient_data_returns_nan(self):
        """Test that bars before start_at_bar are NaN."""
        prices = np.array([100.0, 101.0, 102.0, 103.0, 104.0])
        result = rational_quadratic(prices, lookback=10, relative_weight=5.0, start_at_bar=10)
        
        assert np.isnan(result).all()

    def test_smooth_uptrend(self):
        """Test kernel smooths upward trend."""
        prices = np.linspace(100, 200, 100)
        result = rational_quadratic(prices, lookback=20, relative_weight=8.0, start_at_bar=30)
        
        valid = result[30:]
        assert np.all(np.diff(valid) >= -0.1)

    def test_kernel_smooths_noise(self):
        """Test kernel reduces noise in price data."""
        np.random.seed(42)
        trend = np.linspace(100, 150, 100)
        noise = np.random.normal(0, 2, 100)
        prices = trend + noise
        
        result = rational_quadratic(prices, lookback=15, relative_weight=5.0, start_at_bar=20)
        
        valid_result = result[20:]
        valid_prices = prices[20:]
        
        result_volatility = np.std(np.diff(valid_result))
        price_volatility = np.std(np.diff(valid_prices))
        
        assert result_volatility < price_volatility

    def test_different_parameters_produce_different_results(self):
        """Test parameter changes affect output."""
        np.random.seed(42)
        prices = np.cumsum(np.random.randn(100) * 5) + 100
        
        result1 = rational_quadratic(prices, lookback=10, relative_weight=5.0, start_at_bar=20)
        result2 = rational_quadratic(prices, lookback=50, relative_weight=5.0, start_at_bar=20)
        
        max_diff = np.max(np.abs(result1[20:] - result2[20:]))
        assert max_diff > 0.5

    def test_production_parameters(self):
        """Test with production config parameters."""
        prices = np.random.uniform(2000, 2500, 200)
        result = rational_quadratic(prices, lookback=88, relative_weight=1.0, start_at_bar=71)
        
        assert len(result) == 200
        assert np.isnan(result[:71]).all()
        assert not np.isnan(result[71:]).any()


class TestGaussian:
    """Tests for gaussian kernel."""

    def test_basic_computation(self):
        """Test basic kernel computation with simple data."""
        prices = np.array([100.0, 101.0, 102.0, 103.0, 104.0, 105.0] * 20)
        result = gaussian(prices, lookback=10, start_at_bar=10)
        
        assert len(result) == len(prices)
        assert np.isnan(result[:10]).all()
        assert not np.isnan(result[10:]).any()
        assert np.all(result[10:] > 0)

    def test_output_length_matches_input(self):
        """Test output array has same length as input."""
        prices = np.array([100.0] * 100)
        result = gaussian(prices, lookback=50, start_at_bar=25)
        
        assert len(result) == len(prices)

    def test_insufficient_data_returns_nan(self):
        """Test that bars before start_at_bar are NaN."""
        prices = np.array([100.0, 101.0, 102.0, 103.0, 104.0])
        result = gaussian(prices, lookback=10, start_at_bar=10)
        
        assert np.isnan(result).all()

    def test_smooth_uptrend(self):
        """Test kernel smooths upward trend."""
        prices = np.linspace(100, 200, 100)
        result = gaussian(prices, lookback=20, start_at_bar=30)
        
        valid = result[30:]
        assert np.all(np.diff(valid) >= -0.1)

    def test_kernel_smooths_noise(self):
        """Test kernel reduces noise in price data."""
        np.random.seed(42)
        trend = np.linspace(100, 150, 100)
        noise = np.random.normal(0, 2, 100)
        prices = trend + noise
        
        result = gaussian(prices, lookback=15, start_at_bar=20)
        
        valid_result = result[20:]
        valid_prices = prices[20:]
        
        result_volatility = np.std(np.diff(valid_result))
        price_volatility = np.std(np.diff(valid_prices))
        
        assert result_volatility < price_volatility

    def test_different_lookback_produces_different_results(self):
        """Test lookback parameter affects output."""
        prices = np.linspace(100, 150, 100)
        
        result1 = gaussian(prices, lookback=10, start_at_bar=20)
        result2 = gaussian(prices, lookback=30, start_at_bar=20)
        
        assert not np.allclose(result1[20:], result2[20:], rtol=0.01)

    def test_production_parameters(self):
        """Test with production config parameters (lookback - lag)."""
        prices = np.random.uniform(2000, 2500, 200)
        result = gaussian(prices, lookback=87, start_at_bar=71)
        
        assert len(result) == 200
        assert np.isnan(result[:71]).all()
        assert not np.isnan(result[71:]).any()


class TestKernelComparison:
    """Tests comparing both kernel implementations."""

    def test_both_kernels_smooth_data(self):
        """Test both kernels produce smoothed output."""
        np.random.seed(42)
        prices = np.linspace(100, 150, 100) + np.random.normal(0, 3, 100)
        
        rq = rational_quadratic(prices, lookback=15, relative_weight=5.0, start_at_bar=20)
        gauss = gaussian(prices, lookback=15, start_at_bar=20)
        
        assert len(rq) == len(gauss) == len(prices)
        
        rq_valid = rq[20:]
        gauss_valid = gauss[20:]
        
        assert np.corrcoef(rq_valid, gauss_valid)[0, 1] > 0.95

    def test_kernels_handle_flat_prices(self):
        """Test kernels handle constant price input."""
        prices = np.array([100.0] * 100)
        
        rq = rational_quadratic(prices, lookback=10, relative_weight=5.0, start_at_bar=15)
        gauss = gaussian(prices, lookback=10, start_at_bar=15)
        
        assert np.allclose(rq[15:], 100.0)
        assert np.allclose(gauss[15:], 100.0)

    def test_kernels_handle_extreme_volatility(self):
        """Test kernels handle large price swings."""
        prices = np.array([100, 200, 50, 300, 75, 250] * 20)
        
        rq = rational_quadratic(prices, lookback=10, relative_weight=5.0, start_at_bar=15)
        gauss = gaussian(prices, lookback=10, start_at_bar=15)
        
        assert not np.isnan(rq[15:]).any()
        assert not np.isnan(gauss[15:]).any()
        assert np.all(rq[15:] > 0)
        assert np.all(gauss[15:] > 0)
