# Test Suite

Comprehensive unit and API tests for the trading bot.

## Running Tests

### Run all tests
```bash
pytest
```

### Run with coverage report
```bash
pytest --cov=bot --cov=api --cov-report=html
```

### Run specific test file
```bash
pytest tests/test_kernels.py -v
```

### Run specific test class
```bash
pytest tests/test_strategy.py::TestCalculateStopLoss -v
```

### Run specific test
```bash
pytest tests/test_state.py::TestUpdateSignal::test_update_signal_increments_signal_seq -v
```

## Test Structure

- `test_kernels.py` — Nadaraya-Watson kernel estimators (rational_quadratic, gaussian)
- `test_filters.py` — Volatility filtering and ATR computation
- `test_strategy.py` — Strategy logic, ATR-based SL, signal generation
- `test_state.py` — SharedState thread safety and signal tracking
- `test_kill_switch.py` — Kill switch logic and pause/resume
- `test_utils.py` — Utility functions (sync_time, safe_api_call)
- `test_api.py` — FastAPI endpoints and CORS

## Coverage Requirements

- Minimum coverage: 70%
- Target coverage: 85%+

## CI Integration

Tests run automatically on:
- Every push to any branch
- Every pull request
- Manual workflow dispatch

CI pipeline:
1. Unit tests (pytest)
2. Lint (flake8)
3. Docker build check
