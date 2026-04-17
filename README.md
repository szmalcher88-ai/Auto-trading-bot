# Auto Trading Bot

Automated **ETHUSDT Futures** trading bot using **Kernel Regression** (Nadaraya-Watson) — full Python, no TradingView / ngrok / Flask dependencies.

The bot fetches OHLCV data from Binance, computes kernel regression signals, generates entries/exits, and executes trades autonomously. A web dashboard + backtest engine + cross-asset parameter optimizer (AutoResearch) are all bundled in a single process.

---

## Features

- **Signal engine** — rational quadratic kernel + ATR volatility filter (1:1 Pine Script replica, validated `<0.07%` vs TradingView)
- **Risk management** — ATR trailing stop loss, kill switch (consecutive losses / equity drop), re-entry logic
- **Web dashboard** — FastAPI + React (Tailwind), localhost:8080, 7 tabs (Dashboard, Settings, Logs, Signals, AutoResearch, Orchestrator, Leaderboard)
- **Backtest engine** — offline simulation with commission, cache, two trailing SL modes
- **AutoResearch** — 3-phase smart parameter search (explore/exploit/refine), global memory, cross-asset scoring (ETH/BTC/SOL)
- **Walk-Forward Validation** — anchored expanding windows, out-of-sample testing, stability scoring
- **Multi-worker orchestration** — distributed parameter sweeps across machines
- **Docker-ready** — single-command deployment to any VPS

---

## Quick Start

```bash
# 1. Clone
git clone https://github.com/YOUR_USERNAME/Auto-trading-bot.git
cd Auto-trading-bot

# 2. Configure environment
cp .env.example .env
# Edit .env — add Binance Testnet API keys + UPLOAD_API_KEY

# 3. Install dependencies
pip install -r requirements.txt

# 4. Run
python main.py
```

Open the dashboard at **http://localhost:8080**.

> **WARNING:** Testnet only by default. Never switch to mainnet without thoroughly testing and understanding the risks.

---

## Architecture

```
main.py
  ├── Thread 1: Trading Loop (every 1H: fetch → kernel → signal → trade)
  ├── Thread 2: FastAPI Dashboard (localhost:8080)
  └── SharedState (thread-safe, bot ↔ dashboard)

backtest.py        — offline strategy simulation on historical data
autoresearch.py    — cross-asset parameter sweep + leaderboard
push-to-server.py  — remote worker client (distributed sweeps)
```

### Repository Layout

```
├── api/server.py              # FastAPI (15+ endpoints)
├── dashboard/index.html       # React + Tailwind (7 tabs)
├── bot/
│   ├── config.py              # Strategy parameters + API config
│   ├── state.py               # SharedState (thread-safe)
│   ├── exchange.py            # Binance client (orders, position, leverage)
│   ├── data_fetcher.py        # OHLCV from Binance Futures + cache
│   ├── kernels.py             # Rational Quadratic + Gaussian
│   ├── filters.py             # Volatility filter (ATR short > ATR long)
│   ├── strategy.py            # Signal logic (smoothing, SL, re-entry)
│   ├── kill_switch.py         # 5 losses / 10% DD → 24h pause
│   └── trade_logger.py        # CSV trade logging
├── main.py                    # Bot loop + FastAPI (2 threads)
├── backtest.py                # Backtest engine
├── autoresearch.py            # Cross-asset parameter sweep
├── push-to-server.py          # Remote worker client
└── tests/                     # pytest suite
```

---

## Strategy Parameters (defaults)

| Parameter            | Value                               |
| -------------------- | ----------------------------------- |
| Timeframe            | 1H                                  |
| Symbol               | ETHUSDT (Binance Futures Perpetual) |
| Lookback Window (h)  | 100                                 |
| Regression Level (x) | 69                                  |
| Relative Weight (r)  | 10.0                                |
| Kernel Smoothing     | On                                  |
| SL Type              | ATR                                 |
| ATR Period / Mult    | 20 / 6.0                            |
| Trailing SL Mode     | Pine (close-based)                  |
| Volatility Min / Max | 5 / 10                              |
| Leverage             | 1x                                  |
| Position Size        | 50% portfolio                       |
| Commission           | 0.05% (Binance Futures taker)       |
| Kill Switch          | 5 losses / 10% DD / 24h pause       |

---

## Dashboard Tabs

| Tab          | Purpose                                                       |
| ------------ | ------------------------------------------------------------- |
| Dashboard    | Live status, position, PnL, emergency buttons, kill switch reset |
| Settings     | Edit strategy parameters from UI, range validation            |
| Logs         | Live tail of bot log (auto-refresh)                           |
| Signals      | Real-time signal history (yhat1, yhat2, volatility filter)    |
| AutoResearch | Grid sweep results, heatmap, top configs                      |
| Orchestrator | Multi-worker parameter sweeps, worker status                  |
| Leaderboard  | All-time best configs, apply to live bot                      |

---

## CLI Tools

### Backtest

```bash
python backtest.py --symbol ETHUSDT --sl-type atr --atr-period 20 --atr-multiplier 6
python backtest.py --lookback 73 --regression 69 --relative-weight 8
python backtest.py --no-sl --no-reentry  # kernel-only
```

### AutoResearch — grid sweep

```bash
python autoresearch.py
python autoresearch.py --h-min 60 --h-max 110 --h-step 1 --x-min 62 --x-max 69 --x-step 1
python autoresearch.py --atr-period-values 10 14 20 30 --atr-mult-values 3 4 5 6 8
```

### AutoResearch — smart mode (genetic-style)

```bash
python autoresearch.py --mode smart --time-budget 3600
python autoresearch.py --mode smart --time-budget 7200 --assets ETHUSDT BTCUSDT SOLUSDT
```

### Walk-Forward Validation

```bash
python autoresearch.py --mode walkforward --folds 3 --test-months 3
```

### Remote worker (push-to-server)

```bash
export SERVER_URL="http://YOUR_SERVER_IP:8080"
export UPLOAD_API_KEY="your-secret-key"
python push-to-server.py --mode smart --time-budget 3600 --name "My PC"

# Multi-worker on one machine
python push-to-server.py --workers 4 --mode smart --time-budget 3600
```

---

## API Endpoints

| Endpoint                      | Method | Purpose                               |
| ----------------------------- | ------ | ------------------------------------- |
| `/api/status`                 | GET    | Bot state, position, balance, live PnL |
| `/api/signals`                | GET    | Last signal snapshot                   |
| `/api/signal-history`         | GET    | Historical signals                     |
| `/api/settings`               | GET/POST | Get/update strategy parameters       |
| `/api/trades`                 | GET    | Last 20 trades from CSV                |
| `/api/killswitch`             | GET    | Kill switch status                     |
| `/api/killswitch/reset`       | POST   | Manually reset kill switch             |
| `/api/logs`                   | GET    | Last 100 log lines                     |
| `/api/equity`                 | GET    | Equity curve                           |
| `/api/autoresearch`           | GET    | Sweep results                          |
| `/api/autoresearch/upload`    | POST   | Upload remote sweep results (requires `X-Upload-Key`) |
| `/api/leaderboard`            | GET    | All-time best configs                  |
| `/api/emergency/close`        | POST   | Close position immediately             |
| `/api/emergency/pause`        | POST   | Pause trading                          |
| `/api/emergency/resume`       | POST   | Resume trading                         |

---

## Deployment (Docker)

### Local Docker

```bash
cp .env.example .env  # edit with real keys
docker compose up -d
```

### VPS Bootstrap

```bash
ssh root@YOUR_SERVER_IP
mkdir -p /opt/trading-bot/data /opt/trading-bot/cache
touch /opt/trading-bot/data/{autoresearch_results.csv,autoresearch_alltime.csv,autoresearch_meta.json,trade_log.csv,trading_bot.log,signal_state.json}
# Copy docker-compose.yml + .env to the server, then:
cd /opt/trading-bot && docker compose up -d
```

### GitHub Actions auto-deploy

Configure the following in **Settings → Secrets → Actions**:

| Secret            | Value                                       |
| ----------------- | ------------------------------------------- |
| `DEPLOY_HOST`     | your server IP                              |
| `DEPLOY_USER`     | `root`                                      |
| `DEPLOY_SSH_KEY`  | contents of your private SSH key            |
| `DEPLOY_PORT`     | `22`                                        |

And **Settings → Variables → Actions**:

| Variable          | Value                                       |
| ----------------- | ------------------------------------------- |
| `DEPLOY_ENABLED`  | `true`                                      |

Every push to `main` then builds a new image and deploys it automatically.

---

## Testing

```bash
pip install -r requirements.txt
pytest                          # run full test suite
pytest tests/test_kernels.py -v # specific test
python validate_kernels.py      # validate kernels vs TradingView reference
python validate_signals.py      # validate signal logic
```

---

## Documentation

- [README.pl.md](./README.pl.md) — Polish version
- [PROJECT.md](./PROJECT.md) — detailed architecture and deployment
- [DATABASE.md](./DATABASE.md) — SQLite schema (Prisma)
- [TESTING.md](./TESTING.md) — test strategy
- [MIGRATION_ORCHESTRATOR.md](./MIGRATION_ORCHESTRATOR.md) — orchestrator migration notes
- [CLAUDE.md](./CLAUDE.md) — development guidelines (Polish, project-internal)

---

## Disclaimer

**This bot trades real money on cryptocurrency derivatives markets.** Use only testnet unless you fully understand the risks. Losses can exceed deposits. No warranty. The authors are not liable for any losses.

## License

MIT — see [LICENSE](./LICENSE).
