"""API tests for api/server.py — FastAPI endpoints."""

import pytest
from fastapi.testclient import TestClient
from unittest.mock import Mock, patch, MagicMock
import json
from bot.state import SharedState


@pytest.fixture
def mock_state():
    """Create a real SharedState instance for testing."""
    state = SharedState()
    state.bot_running = True
    state.position_state = 'long'
    state.entry_price = 2100.0
    state.stop_loss = 2050.0
    state.balance = 10500.0
    state.last_action = 'open_long'
    state.last_reason = 'bullish_crossover'
    state.signal_seq = 10
    state.action_seq = 3
    state.last_signal = {
        'symbol': 'ETHUSDT',
        'yhat1': 2105.0,
        'yhat2': 2100.0,
        'is_bullish': True,
        'vol_filter': True,
        'atr': 25.5
    }
    state.signal_history = [
        {
            'action': 'open_long',
            'reason': 'bullish_crossover',
            'timestamp': 1234567890.0,
            'symbol': 'ETHUSDT',
            'is_bullish': True,
            'yhat1': 2105.0,
            'yhat2': 2100.0
        }
    ]
    state.config = {
        'lookback_window': 88,
        'relative_weight': 1.0,
        'regression_level': 71
    }
    return state


@pytest.fixture
def client(mock_state):
    """Create test client with mocked state."""
    from api.server import create_app
    app = create_app(mock_state)
    return TestClient(app)


class TestHealthEndpoint:
    """Tests for GET /health endpoint."""

    @pytest.mark.skip(reason="Requires app routing setup")
    def test_health_returns_ok(self, client):
        """Test health endpoint returns OK."""
        response = client.get('/health')
        
        assert response.status_code == 200
        assert response.json() == {'status': 'ok'}


class TestStatusEndpoint:
    """Tests for GET /api/status endpoint."""

    def test_status_returns_bot_state(self, client):
        """Test status endpoint returns complete bot state."""
        response = client.get('/api/status')
        
        assert response.status_code == 200
        data = response.json()
        assert data['bot_running'] is True
        assert data['position'] == 'long'
        assert data['balance'] == 10500.0


class TestSignalsEndpoint:
    """Tests for GET /api/signals endpoint."""

    def test_signals_returns_signal_data(self, client):
        """Test signals endpoint returns signal with action/reason."""
        response = client.get('/api/signals')
        
        assert response.status_code == 200
        data = response.json()
        assert 'last_signal' in data
        assert data['last_action'] == 'open_long'
        assert data['last_reason'] == 'bullish_crossover'
        assert data['signal_seq'] == 10
        assert data['action_seq'] == 3


class TestSignalHistoryEndpoint:
    """Tests for GET /api/signal-history endpoint."""

    def test_signal_history_returns_list(self, client):
        """Test signal-history endpoint returns action list."""
        response = client.get('/api/signal-history')
        
        assert response.status_code == 200
        data = response.json()
        assert 'history' in data
        assert 'count' in data
        assert data['count'] == 1
        assert data['history'][0]['action'] == 'open_long'


class TestSettingsEndpoints:
    """Tests for settings GET/POST endpoints."""

    def test_get_settings_returns_config(self, client):
        """Test GET /api/settings returns current config."""
        response = client.get('/api/settings')
        
        assert response.status_code == 200
        data = response.json()
        assert 'kernel' in data
        assert 'stop_loss' in data

    @pytest.mark.skip(reason="Requires full app setup")
    def test_post_settings_validates_ranges(self, client):
        """Test POST /api/settings validates parameter ranges."""
        payload = {
            'lookback_window': 5
        }
        
        response = client.post('/api/settings', json=payload)
        
        assert response.status_code == 400
        assert 'errors' in response.json()

    @pytest.mark.skip(reason="Requires full app setup")
    def test_post_settings_validates_sl_type(self, client):
        """Test POST /api/settings validates sl_type values."""
        payload = {
            'sl_type': 'invalid'
        }
        
        response = client.post('/api/settings', json=payload)
        
        assert response.status_code == 400
        data = response.json()
        assert 'errors' in data
        assert any('sl_type' in err for err in data['errors'])


class TestControlEndpoints:
    """Tests for control endpoints (pause/resume/emergency)."""

    @pytest.mark.skip(reason="Requires full app routing setup")
    def test_pause_sets_flag(self, client, mock_state):
        """Test POST /api/pause sets trading_paused flag."""
        response = client.post('/api/pause')
        
        assert response.status_code == 200
        assert mock_state.trading_paused is True

    @pytest.mark.skip(reason="Requires full app routing setup")
    def test_resume_clears_flag(self, client, mock_state):
        """Test POST /api/resume clears trading_paused flag."""
        mock_state.trading_paused = True
        
        response = client.post('/api/resume')
        
        assert response.status_code == 200
        assert mock_state.trading_paused is False

    @pytest.mark.skip(reason="Requires full app routing setup")
    def test_emergency_close_sets_event(self, client, mock_state):
        """Test POST /api/emergency-close sets emergency event."""
        response = client.post('/api/emergency-close')
        
        assert response.status_code == 200
        assert mock_state.emergency_close.is_set()


class TestAutoresearchEndpoints:
    """Tests for autoresearch-related endpoints."""

    def test_autoresearch_db_first_returns_results(self, client):
        """Test /api/autoresearch uses DB when available and returns results."""
        fake_run = {
            'date': '2026-03-25T21:40:35',
            'combinations_tested': 100,
            'duration_seconds': 3600,
            'assets': ['ETHUSDT', 'BTCUSDT', 'SOLUSDT'],
            'status': 'completed',
        }
        fake_rows = [
            {
                'lookback_window': 86, 'regression_level': 64,
                'use_kernel_smoothing': True, 'relative_weight': 12,
                'lag': 1, 'atr_period': 20, 'atr_multiplier': 6.0,
                'volatility_min': 5, 'volatility_max': 8, 're_entry_delay': 1,
                'eth_pf': 1.46, 'eth_dd': 27.8, 'eth_profit': 106.3,
                'btc_pf': 1.86, 'btc_dd': 13.5, 'btc_profit': 74.6,
                'sol_pf': 1.32, 'sol_dd': 28.9, 'sol_profit': 83.9,
                'score': 2.04, 'balanced_score': 0.63,
            }
        ]
        fake_db = Mock()
        fake_db.get_latest_run = Mock(return_value=fake_run)
        fake_db.get_results_for_latest_run = Mock(return_value=fake_rows)
        fake_db.disconnect = Mock(return_value=None)

        with patch('api.server.HAS_DB', True), \
             patch('api.server.AutoResearchDB', return_value=fake_db), \
             patch('api.server.run_async', side_effect=lambda coro: coro):
            response = client.get('/api/autoresearch')

        assert response.status_code == 200

    def test_autoresearch_csv_fallback_no_db(self, client, tmp_path, monkeypatch):
        """Test /api/autoresearch falls back to CSV when DB unavailable."""
        csv_content = (
            'lookback_window,regression_level,use_kernel_smoothing,relative_weight,'
            'lag,atr_period,atr_multiplier,volatility_min,volatility_max,re_entry_delay,'
            'eth_pf,eth_dd,eth_profit,eth_trades,eth_wr,eth_sharpe,'
            'btc_pf,btc_dd,btc_profit,btc_trades,btc_wr,btc_sharpe,'
            'sol_pf,sol_dd,sol_profit,sol_trades,sol_wr,sol_sharpe,score\n'
            '86,64,True,12,1,20,6.0,5,8,1,'
            '1.46,27.8,106.3,133,36.1,1.55,'
            '1.86,13.5,74.6,108,48.1,2.12,'
            '1.32,28.9,83.9,126,38.9,1.36,2.04\n'
        )
        csv_file = tmp_path / 'autoresearch_results.csv'
        csv_file.write_text(csv_content)
        monkeypatch.chdir(tmp_path)

        with patch('api.server.HAS_DB', False):
            response = client.get('/api/autoresearch')

        assert response.status_code == 200
        data = response.json()
        assert data['total_results'] == 1
        assert data['valid_count'] == 1

    def test_leaderboard_db_first_returns_top_results(self, client):
        """Test /api/leaderboard uses DB when available."""
        fake_top = [
            {
                'lookback_window': 91, 'regression_level': 79,
                'score': 2.39, 'eth_pf': 1.98, 'btc_pf': 1.72,
            }
        ]
        fake_db = Mock()
        fake_db.get_all_time_top_results = Mock(return_value=fake_top)
        fake_db.disconnect = Mock(return_value=None)

        with patch('api.server.HAS_DB', True), \
             patch('api.server.AutoResearchDB', return_value=fake_db), \
             patch('api.server.run_async', side_effect=lambda coro: coro), \
             patch('api.server._count_db_runs', return_value=3):
            response = client.get('/api/leaderboard')

        assert response.status_code == 200

    @pytest.mark.skip(reason="Requires full app routing setup")
    def test_upload_requires_auth(self, client):
        """Test POST /api/autoresearch/upload requires X-Upload-Key."""
        payload = {
            'results_csv': 'header\ndata',
            'alltime_rows_csv': 'header\ndata',
            'meta_json': '{}'
        }
        response = client.post('/api/autoresearch/upload', json=payload)

        assert response.status_code == 401

    @pytest.mark.skip(reason="Requires full app routing setup")
    def test_repair_alltime_requires_auth(self, client):
        """Test POST /api/autoresearch/repair-alltime requires auth."""
        response = client.post('/api/autoresearch/repair-alltime', json={})

        assert response.status_code == 401


class TestCORS:
    """Tests for CORS middleware."""

    @pytest.mark.skip(reason="Requires full app routing setup")
    def test_cors_headers_present(self, client):
        """Test CORS headers are present in responses."""
        response = client.get('/api/status')
        
        assert response.status_code == 200
        assert 'access-control-allow-origin' in response.headers
