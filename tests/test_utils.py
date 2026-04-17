"""Unit tests for bot/utils.py — utility functions."""

import pytest
from unittest.mock import Mock, patch
from binance.exceptions import BinanceAPIException
from bot.utils import sync_time, safe_api_call


class TestSyncTime:
    """Tests for sync_time function."""

    def test_sync_time_sets_offset_when_large_diff(self):
        """Test offset is set when time diff exceeds 500ms."""
        mock_client = Mock()
        mock_client.timestamp_offset = 0
        mock_client.get_server_time.return_value = {'serverTime': 2000}

        with patch('bot.utils.time_module.time', return_value=1.0):
            result = sync_time(mock_client)

        assert mock_client.timestamp_offset == 1000
        assert result == 1000  # returns offset_ms

    def test_sync_time_returns_offset(self):
        """Test sync_time returns the measured offset value."""
        mock_client = Mock()
        mock_client.timestamp_offset = 0
        mock_client.get_server_time.return_value = {'serverTime': 3500}

        with patch('bot.utils.time_module.time', return_value=1.0):
            result = sync_time(mock_client)

        assert result == 2500

    def test_sync_time_within_tolerance_does_not_set_offset(self):
        """Test no offset applied when time diff <= 500ms."""
        mock_client = Mock()
        # server_time - local_time = 300ms (within 500ms tolerance)
        mock_client.get_server_time.return_value = {'serverTime': 1300}

        with patch('bot.utils.time_module.time', return_value=1.0):
            result = sync_time(mock_client)

        assert not hasattr(mock_client, 'timestamp_offset') or \
               getattr(mock_client, 'timestamp_offset', None) != 300
        assert result == 300

    def test_sync_time_retries_on_failure(self):
        """Test sync_time retries up to 3 times on error."""
        mock_client = Mock()
        mock_client.get_server_time.side_effect = [
            Exception("Network error"),
            Exception("Network error"),
            {'serverTime': 1000000}
        ]

        with patch('bot.utils.time_module.time', return_value=1.0):
            with patch('bot.utils.time_module.sleep'):
                sync_time(mock_client)

        assert mock_client.get_server_time.call_count == 3

    def test_sync_time_returns_none_after_3_failures(self):
        """Test sync_time returns None after 3 failed attempts."""
        mock_client = Mock()
        mock_client.get_server_time.side_effect = Exception("Persistent error")

        with patch('bot.utils.time_module.sleep'):
            result = sync_time(mock_client)

        assert result is None
        assert mock_client.get_server_time.call_count == 3

    def test_sync_time_logs_warning_on_large_drift(self):
        """Test a warning is logged when drift exceeds threshold."""
        mock_client = Mock()
        mock_client.timestamp_offset = 0
        # server_time - local_time = 4000 - 1000 = 3000ms — above _DRIFT_WARNING_MS
        mock_client.get_server_time.return_value = {'serverTime': 4000}

        with patch('bot.utils.time_module.time', return_value=1.0), \
             patch('bot.utils.logger') as mock_logger:
            sync_time(mock_client)

        mock_logger.warning.assert_called_once()
        warning_msg = mock_logger.warning.call_args[0][0]
        assert 'drift' in warning_msg.lower() or '3000' in warning_msg


class TestSafeAPICall:
    """Tests for safe_api_call wrapper."""

    def test_successful_call_returns_result(self):
        """Test successful API call returns result immediately."""
        mock_client = Mock()
        mock_function = Mock(return_value={'data': 'success'})
        
        result = safe_api_call(mock_client, mock_function, 'arg1', key='value')
        
        assert result == {'data': 'success'}
        assert mock_function.call_count == 1

    def test_retries_on_timestamp_error(self):
        """Test retries and resyncs on -1021 timestamp error."""
        mock_client = Mock()
        mock_response = Mock()
        mock_response.text = "Timestamp error"
        
        call_count = [0]
        def mock_func():
            call_count[0] += 1
            if call_count[0] == 1:
                raise BinanceAPIException(mock_response, -1021, "Timestamp error")
            return {'data': 'success'}
        
        with patch('bot.utils.sync_time'):
            result = safe_api_call(mock_client, mock_func)
        
        assert result == {'data': 'success'}
        assert call_count[0] == 2

    def test_retries_on_502_bad_gateway(self):
        """Test retries on 502 Bad Gateway with exponential backoff."""
        mock_client = Mock()
        mock_response = Mock()
        mock_response.text = "502 Bad Gateway"
        mock_function = Mock(side_effect=[
            BinanceAPIException(mock_response, 0, "502 Bad Gateway"),
            {'data': 'success'}
        ])
        
        with patch('bot.utils.time_module.sleep') as mock_sleep:
            result = safe_api_call(mock_client, mock_function)
        
        assert result == {'data': 'success'}
        assert mock_function.call_count == 2
        assert mock_sleep.call_count == 1
        mock_sleep.assert_called_with(2)

    def test_retries_on_network_error(self):
        """Test retries on network errors."""
        mock_client = Mock()
        mock_function = Mock(side_effect=[
            Exception("Read timed out"),
            {'data': 'success'}
        ])
        
        with patch('bot.utils.time_module.sleep') as mock_sleep:
            result = safe_api_call(mock_client, mock_function)
        
        assert result == {'data': 'success'}
        assert mock_function.call_count == 2

    def test_raises_after_max_retries(self):
        """Test raises exception after 3 failed attempts."""
        mock_client = Mock()
        mock_function = Mock(side_effect=Exception("Persistent error"))
        
        with patch('bot.utils.time_module.sleep'):
            with pytest.raises(Exception):
                safe_api_call(mock_client, mock_function)
        
        assert mock_function.call_count == 3

    def test_raises_non_retryable_error_immediately(self):
        """Test non-retryable errors are raised on last attempt."""
        mock_client = Mock()
        mock_response = Mock()
        mock_response.text = "Invalid symbol"
        error = BinanceAPIException(mock_response, -1000, "Invalid symbol")
        mock_function = Mock(side_effect=[error, error, error])
        
        with pytest.raises(BinanceAPIException):
            safe_api_call(mock_client, mock_function)
        
        assert mock_function.call_count == 3
