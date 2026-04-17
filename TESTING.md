# Testing Guide

## Overview

The trading bot has a comprehensive unit test suite covering all core logic modules.

## Test Statistics

- **100 tests passing** (12 skipped)
- **38% code coverage** (target: 35%)
- **Test execution time**: ~10 seconds

## Coverage by Module

| Module | Coverage | Tests |
|--------|----------|-------|
| `bot/kernels.py` | 100% | 16 |
| `bot/filters.py` | 100% | 9 |
| `bot/state.py` | 100% | 18 |
| `bot/kill_switch.py` | 100% | 15 |
| `bot/utils.py` | 94% | 12 |
| `bot/strategy.py` | 73% | 24 |
| `api/server.py` | 19% | 6 active, 12 skipped |

## Running Tests

### Quick start
```bash
pytest
```

### With coverage report
```bash
pytest --cov=bot --cov=api --cov-report=html
open coverage_html/index.html
```

### Run specific test file
```bash
pytest tests/test_kernels.py -v
```

### Run specific test
```bash
pytest tests/test_state.py::TestUpdateSignal::test_update_signal_increments_signal_seq -v
```

### Run only non-skipped tests
```bash
pytest -v -m "not skip"
```

## Test Structure

```
tests/
├── __init__.py
├── README.md
├── test_kernels.py      # Nadaraya-Watson kernel estimators
├── test_filters.py      # Volatility filtering and ATR
├── test_strategy.py     # Strategy logic, SL, signals
├── test_state.py        # SharedState thread safety
├── test_kill_switch.py  # Trading pause logic
├── test_utils.py        # Utility functions
└── test_api.py          # FastAPI endpoints (partial)
```

## CI Integration

Tests run automatically on:
- Every push to any branch
- Every pull request
- Manual workflow dispatch

### CI Pipeline (`.github/workflows/ci.yml`)

1. **Unit Tests** (pytest)
   - Run all tests with coverage
   - Upload coverage to Codecov
   - Fail if coverage < 35%

2. **Lint** (flake8)
   - Code style checks
   - Only runs if tests pass

3. **Docker Build**
   - Smoke test for Docker image
   - Only runs if lint passes

## What's Tested

### Core Logic (100% coverage)
- Kernel regression algorithms
- ATR computation (Wilder's RMA)
- Volatility filtering
- SharedState thread safety
- Kill switch logic

### Business Logic (73-94% coverage)
- Stop loss calculation (percent vs ATR-based)
- Trailing SL updates
- Position state management
- Signal sequence tracking
- API retry logic

### Partially Tested
- API endpoints (basic smoke tests)
- Signal history tracking

### Not Tested (Integration Components)
- `bot/exchange.py` — Binance API integration
- `bot/data_fetcher.py` — OHLCV fetching
- `bot/trade_logger.py` — SQLite logging
- `bot/db.py` — Prisma ORM

These components interact with external services and are better suited for integration/E2E tests.

## Test Quality

- **Isolation**: Each test is independent, no shared state
- **Mocking**: External dependencies (Binance API, time) are mocked
- **Thread safety**: Concurrent access tests for SharedState
- **Edge cases**: NaN handling, insufficient data, extreme values
- **Production configs**: Tests validate with actual production parameters

## Future Improvements

1. **API integration tests** — Full FastAPI app testing with proper routing
2. **E2E tests** — Docker-based tests with real Binance testnet
3. **Performance tests** — Benchmark kernel computation speed
4. **Property-based tests** — Hypothesis for kernel invariants
5. **Coverage target** — Increase to 60%+ by testing strategy edge cases
