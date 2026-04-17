# PROJECT.md — Trading Bot Standalone

## What this project is

A fully standalone Python trading bot for **ETHUSDT** on **Binance Futures Testnet**. No TradingView, no ngrok, no Flask. The bot fetches its own OHLCV data from Binance, computes a Nadaraya-Watson kernel regression, generates trade signals that replicate a Pine Script strategy 1:1, and executes real orders via the Binance Futures API.

There is also an **AutoResearch** subsystem that backtests parameter combinations across multiple assets to find the best configuration.

---

## Development phases (from CLAUDE.md)

| # | Phase | Status |
|---|-------|--------|
| 1 | Repository structure + component migration | ✅ Done |
| 2 | Data fetcher + kernel regression (validated vs TradingView, diff <0.07%) | ✅ Done |
| 3 | Signal logic — 1:1 Pine Script replica | ✅ Done |
| 4 | Execution loop + trade execution | ✅ Done |
| 5 | Full 24h integration test | ⬜ Pending |

---

## Directory structure

```
trading-bot-standalone/
│
├── main.py                    # Entry point — wires all components
│
├── bot/                       # Core trading modules
│   ├── config.py              # All strategy & system parameters
│   ├── strategy.py            # Signal logic (Pine Script 1:1 replica)
│   ├── kernels.py             # Nadaraya-Watson kernel estimators (RQ + Gaussian)
│   ├── filters.py             # Volatility filter (ATR-based)
│   ├── exchange.py            # Binance Futures client + order management
│   ├── data_fetcher.py        # OHLCV candle fetcher from Binance
│   ├── kill_switch.py         # Anti-drawdown kill switch
│   ├── state.py               # Thread-safe shared state (loop ↔ API)
│   ├── trade_logger.py        # Trade log writer (CSV)
│   ├── db.py                  # SQLite/Prisma database helpers
│   ├── orchestrator.py        # Distributed worker orchestration (sweeps, jobs, workers)
│   └── utils.py               # API retry + time sync helpers
│
├── api/
│   └── server.py              # FastAPI server — all REST endpoints
│
├── dashboard/
│   └── index.html             # Single-file dashboard UI (~2400 lines)
│
├── prisma/
│   └── schema.prisma          # SQLite schema via Prisma Client Python
│
├── autoresearch.py            # Parameter sweep / optimization tool
├── push-to-server.py          # Distributed worker client (polls server for jobs)
├── backtest.py                # Backtesting engine (used by autoresearch)
├── validate_kernels.py        # Kernel accuracy validation vs TradingView
├── validate_signals.py        # Signal accuracy validation
├── monitor_bot.py             # External health monitoring script
│
├── cache/                     # CSV price data cache (1h OHLCV)
│   ├── BTCUSDT_1h.csv         # ~12 900 candles
│   ├── ETHUSDT_1h.csv
│   └── SOLUSDT_1h.csv
│
├── autoresearch.db            # SQLite results database
├── autoresearch_results.csv   # CSV export (backward compatibility)
├── autoresearch_alltime.csv   # All-time best results across all runs
├── autoresearch_meta.json     # Latest run metadata
├── signal_state.json          # Persisted signal counters + history (survives restarts)
├── trade_log.csv              # Live trade history
├── trading_bot.log            # Application log file
│
├── tests/                     # Unit tests (pytest)
│   ├── conftest.py            # Autouse fixtures (signal state isolation via tmp_path)
│   ├── test_orchestrator.py   # Orchestrator system tests (20 tests)
│   ├── test_state.py          # SharedState tests (signal seq, persistence, thread safety)
│   ├── test_strategy.py       # Strategy logic tests
│   ├── test_kernels.py        # Kernel regression tests
│   └── ...                    # Other test modules
│
├── requirements.txt           # Python dependencies
├── .env / .env.example        # Binance Testnet API keys + UPLOAD_API_KEY
├── README.md                  # Quick-start guide
├── DATABASE.md                # Database integration details
├── ORCHESTRATOR_IMPLEMENTATION.md  # Distributed worker architecture docs
├── ORCHESTRATOR_TEST.md       # Orchestrator testing guide
└── CLAUDE.md                  # Internal AI context doc (Polish)
```

---

## How the bot runs (`main.py`)

Two concurrent execution paths share a single `SharedState` object:

```
main()
 ├── trading_loop()  ──► background daemon thread
 │     └── every 1h candle close:
 │           fetch OHLCV → calculate_signals() → execute trade → update state
 │
 └── uvicorn (FastAPI)  ──► main thread, blocks at http://0.0.0.0:8080
       └── serves dashboard + REST API reads from SharedState
```

**Startup sequence:**
1. Create `SharedState`
2. Init `Exchange` → set 1x leverage + CROSSED margin
3. `sync_position_from_exchange()` — crash recovery, restores position from Binance
4. `KillSwitch(initial_equity=balance)`
5. Start trading thread (daemon)
6. Start FastAPI / dashboard

---

## Strategy logic (`bot/strategy.py`)

The `Strategy` class is a 1:1 Python replica of the Pine Script kernel regression strategy.

### Kernel regression (`bot/kernels.py`)

Two Nadaraya-Watson estimators run on the close price array:

| Variable | Kernel | Parameters |
|----------|--------|------------|
| `yhat1` | Rational Quadratic | h=110, r=10, x=64 |
| `yhat2` | Gaussian | h=109 (=h−lag), x=64 |

Both use `start_at_bar` (x) as the effective window size. Validated against TradingView output to within **0.04–0.07%** difference.

### Signal generation

Two modes controlled by `USE_KERNEL_SMOOTHING`:

- **Crossover mode** (default, `True`): signal fires when yhat2 crosses above/below yhat1
- **Rate-of-change mode** (`False`): signal fires when yhat1 direction flips

### Decision priority (per closed candle)

```
1. SL hit          → close position  (checked using high/low, simulated — not exchange-side)
2. Color change    → close position  (TP: kernel direction flip)
3. Re-entry        → open opposite   (after SL, wait RE_ENTRY_DELAY bars)
4. Standard entry  → open position   (bullish/bearish change + volatility passes)
```

### Trailing stop loss

- Only tightens, never loosens
- **`pine` mode**: SL referenced to close price (matches Pine Script)
- **`execution` mode**: SL referenced to high (long) or low (short) — more realistic for live trading

### Volatility filter (`bot/filters.py`)

ATR(VOLATILITY_MIN=5) > ATR(VOLATILITY_MAX=10) → filter passes (elevated volatility conditions required for entry).

---

## Key configuration (`bot/config.py`)

### Strategy parameters

| Parameter | Default | Meaning |
|-----------|---------|---------|
| `SYMBOL` | `ETHUSDT` | Trading pair |
| `TIMEFRAME` | `1h` | Candle interval |
| `LOOKBACK_WINDOW` | 110 | Kernel bandwidth (h) |
| `RELATIVE_WEIGHT` | 10.0 | RQ kernel weight (r) |
| `REGRESSION_LEVEL` | 64 | Kernel window size (x) |
| `LAG` | 1 | Gaussian kernel lag |
| `USE_KERNEL_SMOOTHING` | `True` | Crossover vs rate-of-change mode |
| `SL_PERCENT` | 2.7% | Stop loss distance |
| `USE_DYNAMIC_SL` | `True` | Trailing stop loss enabled |
| `TRAILING_SL_MODE` | `pine` | `pine` or `execution` |
| `VOLATILITY_MIN` | 5 | ATR short period for vol filter |
| `VOLATILITY_MAX` | 10 | ATR long period for vol filter |
| `ENABLE_RE_ENTRY` | `True` | Re-entry after SL |
| `RE_ENTRY_DELAY` | 1 | Bars to wait before re-entry |

### Position sizing

| Parameter | Default | Meaning |
|-----------|---------|---------|
| `LEVERAGE` | 1 | No leverage |
| `POSITION_SIZE_PCT` | 50% | % of balance per trade |
| `MAX_POSITIONS` | 1 | Only one open position |
| `COMMISSION_PCT` | 0.05% | Binance Futures taker fee |

### Kill switch

| Parameter | Default | Meaning |
|-----------|---------|---------|
| `KILL_SWITCH_CONSECUTIVE_LOSSES` | 5 | Trigger after N losses in a row |
| `KILL_SWITCH_EQUITY_DROP_PERCENT` | 10% | Trigger on equity drop from peak |
| `KILL_SWITCH_PAUSE_HOURS` | 24 | Pause duration after trigger |

---

## Kill switch (`bot/kill_switch.py`)

Activated when either condition is met after closing a trade:
- 5 consecutive losing trades, OR
- Equity drops ≥ 10% from the peak recorded equity

Once activated: new entries are rejected for 24 hours. Existing open positions are NOT forcibly closed by the kill switch — it only blocks new opens.

---

## API server (`api/server.py`)

FastAPI app served at `http://localhost:8080` (local) or `http://YOUR_SERVER_IP:8080` (production). **CORS is enabled for all origins** (`allow_origins=["*"]`) so that external consumers such as the Midas dashboard can call the API directly from the browser.

Key endpoints:

| Method | Path | Auth | Description |
|--------|------|------|-------------|
| `GET` | `/` | — | Dashboard HTML |
| `GET` | `/api/status` | — | Bot state, position, balance, last signal, action/reason, sequence numbers |
| `GET` | `/api/signals` | — | Latest signal: last_action, last_reason, signal_seq, action_seq, last_signal dict |
| `GET` | `/api/signal-history` | — | Last 50 non-null trade actions with metadata (consumed by Midas Signals page) |
| `POST` | `/api/config` | — | Live config update (hot-reload, no restart needed) |
| `POST` | `/api/emergency-close` | — | Immediately close open position |
| `POST` | `/api/pause` / `/api/resume` | — | Manual trading pause |
| `GET` | `/api/autoresearch` | — | Latest autoresearch results (from DB or CSV) |
| `GET` | `/api/autoresearch/export` | — | Download autoresearch_results.csv |
| `GET` | `/api/autoresearch/export-alltime` | — | Download autoresearch_alltime.csv (used by push-to-server.py sync) |
| `GET` | `/api/leaderboard` | — | Top configs across all runs (deduplicated) |
| `POST` | `/api/autoresearch/run` | — | Trigger a new autoresearch run in background |
| `POST` | `/api/autoresearch/upload` | `X-Upload-Key` | Receive results pushed from a local machine — merges+deduplicates alltime CSV |
| `POST` | `/api/autoresearch/repair-alltime` | `X-Upload-Key` | One-shot repair: fix broken header + deduplicate the alltime CSV |
| `GET` | `/api/sweeps/always-on` | — | Get (or auto-create) the always-on perpetual sweep |
| `PATCH` | `/api/sweeps/always-on` | `X-Upload-Key` | Update time budget / target workers for always-on sweep |
| `POST` | `/api/sweeps` | `X-Upload-Key` | Create new sweep (orchestrator) |
| `GET` | `/api/sweeps` | — | List all sweeps with progress |
| `GET` | `/api/sweeps/{id}` | — | Get detailed sweep info |
| `PATCH` | `/api/sweeps/{id}` | `X-Upload-Key` | Pause/resume/cancel sweep |
| `DELETE` | `/api/sweeps/{id}` | `X-Upload-Key` | Delete sweep and jobs |
| `POST` | `/api/worker/register` | `X-Upload-Key` | Register worker node |
| `POST` | `/api/worker/claim` | `X-Upload-Key` | Claim next pending job |
| `POST` | `/api/worker/submit` | `X-Upload-Key` | Submit job results |
| `POST` | `/api/worker/heartbeat` | `X-Upload-Key` | Worker keep-alive ping |
| `GET` | `/api/workers` | — | List all workers with status |

### Upload endpoint details

`POST /api/autoresearch/upload` requires an `X-Upload-Key` header matching the server's `UPLOAD_API_KEY` env var.

Request body (JSON):

| Field | Type | Description |
|-------|------|-------------|
| `results_csv` | string | Full CSV content — replaces `autoresearch_results.csv` |
| `alltime_rows_csv` | string | Full alltime CSV (header + all rows) — **merged and deduplicated** into `autoresearch_alltime.csv` |
| `meta` | object | JSON object — replaces `autoresearch_meta.json` |

Response:

```json
{
  "status": "ok",
  "uploaded": 120,
  "alltime_merged": 64,
  "alltime_added": 12,
  "alltime_header_fixed": false
}
```

Deduplication uses a composite key of all parameter columns (`lookback|regression|smoothing|relative_weight|lag|atr_period|atr_multiplier|vol_min|vol_max|reentry_delay`). Incoming rows win on conflict (newer run overwrites older). Rows where `lookback` or `regression` are not numeric are silently rejected (guards against old 4-column format data leaking in).

### Repair endpoint details

`POST /api/autoresearch/repair-alltime` — protected by the same `X-Upload-Key`. Reads the current `autoresearch_alltime.csv`, detects the canonical header (`run_date` as first field), re-parses all rows, filters out invalid rows, deduplicates, and rewrites the file. Used to fix files corrupted by old append-only logic.

Config changes POSTed to `/api/config` are applied to live bot modules without restarting, via `state.config_changed` event + `apply_config_changes()`.

---

## Dashboard (`dashboard/index.html`)

Single-file React + Tailwind UI. Tabs visible in the top navigation bar:

| Tab | What it shows |
|-----|---------------|
| **Dashboard** | Bot status, position, balance, PnL, last signal, equity curve, recent trades |
| **Settings** | Live-editable kernel, SL, volatility filter, re-entry, kill switch, position params |
| **Logs** | Streaming log tail (auto-scroll, colour-coded by log prefix) |
| **Signals** | Live signal status card (last_action badge, reason, signal_seq, action_seq, yhat1/2) + full signal history table — polls every 10s, flashes on new action |
| **AutoResearch** | Top-20 table, heatmap, best-vs-current comparison, apply-best button |
| **Orchestrator** | Create sweeps, monitor active sweeps with progress bars, view worker status (NEW) |
| **Leaderboard** | All-time best configs across all uploaded runs |

The **Signals** tab reads from `GET /api/signals` (current state) and `GET /api/signal-history` (last 50 actions). It displays newest-first rows with action badge, reason label, direction, yhat1, and yhat2.

---

## Midas integration

[Midas](http://YOUR_SERVER_IP:3000) is an external dashboard that consumes signal feeds from trading bots. The bot is registered as a signal source under **"Kernel Bot — ETH 1h"** pointing to `http://YOUR_SERVER_IP:8080`.

Midas polls `GET /api/signal-history` to display the signal feed. CORS headers (`allow_origins=["*"]`) are required and are set via `CORSMiddleware` in `api/server.py`.

To add the bot as a signal source in Midas:
1. Go to `http://YOUR_SERVER_IP:3000/signals`
2. Click **Add Signal Source**
3. Name: `Kernel Bot — ETH 1h`, URL: `http://YOUR_SERVER_IP:8080`
4. Click **Test Connection** then **Add**

---

## AutoResearch system

Cross-asset parameter optimization. Sweeps combinations of kernel parameters, backtests each on multiple assets (default: ETH, BTC, SOL), and ranks by a cross-asset consistency score.

**Two modes of operation:**

1. **Standalone mode** — Run `autoresearch.py` directly on one machine
2. **Distributed mode** — Server orchestrates sweeps, workers poll for jobs (2-3x faster)

### Run it (server-side)

```bash
# Default sweep (h: 30–110, x: default ranges, ETH+BTC+SOL)
python autoresearch.py

# Narrow search
python autoresearch.py --h-min 60 --h-max 80 --h-step 1 --x-min 55 --x-max 64 --x-step 1

# More assets
python autoresearch.py --assets ETHUSDT BTCUSDT SOLUSDT AVAXUSDT BNBUSDT

# Custom r values
python autoresearch.py --r-values 5 8 10 15 20

# Smart mode: intelligent 3-phase search (recommended for repeated runs)
# Pulls shared history from server first, skips already-tested configs
python push-to-server.py --server http://YOUR_SERVER_IP:8080 --key your-key --mode smart --time-budget 3600
```

### Run locally and push results to the server (`push-to-server.py`)

Because the VPS has limited CPU, large sweeps are better run locally. `push-to-server.py` runs `autoresearch.py` on your machine with all forwarded args, then uploads the results via `POST /api/autoresearch/upload`.

```powershell
# Set credentials (PowerShell)
$env:SERVER_URL     = "http://YOUR_SERVER_IP:8080"
$env:UPLOAD_API_KEY = "your-secret-key"

# Full sweep
python push-to-server.py --h-min 30 --h-max 110 --h-step 10

# Or pass credentials as CLI flags
python push-to-server.py --server http://YOUR_SERVER_IP:8080 --key your-key --h-min 60 --h-max 80 --h-step 1

# All autoresearch.py arguments are forwarded
python push-to-server.py --server http://YOUR_SERVER_IP:8080 --key your-key --assets ETHUSDT BTCUSDT --r-values 5 8 10 15
```

After the push completes, open `http://YOUR_SERVER_IP:8080` → **AutoResearch** tab to view updated results.

### Data flow

```
Server-side run:
  CLI / Dashboard button
    → POST /api/autoresearch/run
    → FastAPI spawns: python autoresearch.py --args (background subprocess)
    → autoresearch.py tests combos → saves to DB + CSV
    → Dashboard polls GET /api/autoresearch

Local run + push:
  python push-to-server.py --args
    → GET /api/autoresearch/export-alltime (pull shared history from server)
    → merge server rows into local autoresearch_alltime.csv (dedup by composite param key)
    → runs autoresearch.py locally (smart mode skips already-tested configs)
    → reads autoresearch_results.csv + autoresearch_alltime.csv + meta.json
    → POST /api/autoresearch/upload (X-Upload-Key header)
    → server writes files → Dashboard shows updated results
```

### Scoring

Each configuration is scored on:
- Profit factor per asset
- Max drawdown per asset
- Win rate / number of trades
- Cross-asset consistency (same config performing well across ETH, BTC, SOL)

---

## Distributed Worker Orchestrator

**Pull-based distributed system** for parallelizing AutoResearch sweeps across multiple worker machines. Features an **always-on perpetual sweep** that keeps workers busy continuously without manual intervention.

### Architecture

```
Server (orchestrates)          Workers (compute)
├─ Always-On Smart Research   ├─ Worker 1 (your PC)
├─ Generate jobs on demand    ├─ Worker 2 (friend's PC)
├─ Track progress             └─ Worker N (...)
└─ Merge results                   │
       ▲                           │
       └───────────────────────────┘
          Workers poll for jobs
```

**Server responsibilities:**
- Maintain a single always-on perpetual sweep (`Smart Research`) — auto-created on startup
- Auto-generate a new job when a worker claims the last pending one (workers never sit idle)
- Track sweep progress and job status
- Merge worker results into `autoresearch_alltime.csv`

**Worker responsibilities:**
- Register with server (name, hostname, CPU count)
- Poll for next available job
- Run smart-mode backtests locally (random parameter exploration with unique seed per worker)
- Submit results back to server
- Send heartbeat every 30s

### Key Features

1. **Always-on perpetual sweep** — server auto-creates `Smart Research` on startup; perpetual sweeps never auto-complete and always have pending jobs ready
2. **Auto job creation** — when a worker claims the last pending job, the server immediately creates a new one so the next worker gets work without waiting
3. **Atomic job claiming** — no duplicate work across workers
4. **Automatic timeout recovery** — stale jobs (>10 min without heartbeat) reset to pending
5. **Real-time progress tracking** — Dashboard shows live updates
6. **Graceful shutdown** — workers handle Ctrl+C properly
7. **Backward compatible** — old `autoresearch.py` CLI still works

### Database Schema (SQLite)

**Tables in `autoresearch.db`:**

- **`sweeps`** — Campaign metadata (name, status, params, progress, `perpetual` flag)
- **`jobs`** — Batches of configs (status: pending/claimed/completed/failed)
- **`workers`** — Registered compute nodes (status: idle/busy/offline)

**Schema migration:** The `perpetual` column is added automatically via `ALTER TABLE` in `_init_schema()` on first startup — no manual SQL needed on existing databases.

### API Endpoints

**Sweep Management:**
- `POST /api/sweeps` — Create sweep (server generates configs)
- `GET /api/sweeps` — List all sweeps with progress
- `GET /api/sweeps/{id}` — Get detailed sweep info
- `PATCH /api/sweeps/{id}` — Pause/resume/cancel sweep
- `DELETE /api/sweeps/{id}` — Delete sweep and jobs

**Worker Operations:**
- `POST /api/worker/register` — Register worker, returns worker_id
- `POST /api/worker/claim` — Claim next pending job
- `POST /api/worker/submit` — Submit job results
- `POST /api/worker/heartbeat` — Keep-alive ping
- `GET /api/workers` — List all workers with status

All endpoints require `X-Upload-Key` header (same as upload endpoint).

### Usage

**Start server (includes orchestrator + always-on sweep):**
```bash
python main.py
# Dashboard at http://localhost:8080
# Always-on Smart Research sweep created automatically on startup
```

**Start worker(s) — point to prod:**
```bash
# Worker on your PC pointing to production
python push-to-server.py --server http://YOUR_SERVER_IP:8080 --key your-key --name "My PC"

# Multiple workers (each gets an independent job with a different random seed)
python push-to-server.py --server http://YOUR_SERVER_IP:8080 --key your-key --name "Worker-2"
```

**Always-on sweep runs automatically** — no manual sweep creation needed. Workers connect and immediately start picking up smart-search jobs from the `Smart Research` sweep.

**Adjust always-on sweep settings via API:**
```bash
# Change time budget and target worker count
curl -X PATCH http://YOUR_SERVER_IP:8080/api/sweeps/always-on \
  -H "x-upload-key: your-key" \
  -H "Content-Type: application/json" \
  -d '{"time_budget_minutes": 60, "target_workers": 4}'
```

**Monitor progress:**
- **Always-On Smart Research** panel (top of Orchestrator tab) — live status, budget, worker count
- **Workers** section shows worker status (idle/busy/offline)
- Results automatically merged into AutoResearch and Leaderboard tabs

### Performance

**Before (single machine):**
- ~2.9 seconds per config
- Sequential execution only

**After (2-3 workers):**
- ~2-3x speedup with parallel execution
- No subprocess overhead (direct function imports)
- Automatic load balancing

### Documentation

- `ORCHESTRATOR_IMPLEMENTATION.md` — Full architecture details
- `ORCHESTRATOR_TEST.md` — Step-by-step testing guide
- `tests/test_orchestrator.py` — 20 unit tests (88% coverage)

---

## Tests (`tests/`)

| File | What it covers |
|------|---------------|
| `test_state.py` | SharedState — signal updates, sequence counters, history, thread safety |
| `test_strategy.py` | Signal logic, SL, re-entry |
| `test_kernels.py` | Kernel regression accuracy |
| `test_filters.py` | Volatility filter |
| `test_kill_switch.py` | Kill switch trigger conditions |
| `test_api.py` | FastAPI endpoint contracts |
| `test_orchestrator.py` | Sweep/job/worker lifecycle |
| `test_utils.py` | API retry helpers |

### Test isolation for signal state

`tests/conftest.py` provides an **autouse fixture** `disable_signal_state_persistence` that patches `bot.state.STATE_FILE` to a unique `tmp_path` for every test. This prevents on-disk `signal_state.json` (from a previous test run or real bot) from leaking into tests and corrupting sequence counter assertions.

No individual test file needs any changes — isolation is fully automatic.

---

## Database (`prisma/schema.prisma`, `bot/db.py`)

SQLite file: `autoresearch.db`

Two tables:
- `autoresearch_runs` — metadata per run (UUID, timestamps, assets, status)
- `autoresearch_results` — one row per combo per run, with metrics for each asset and the composite score

API falls back to CSV if database is unavailable. CSV files (`autoresearch_results.csv`, `autoresearch_alltime.csv`) are always written for backward compatibility.

### Setup

```bash
pip install prisma
prisma generate
prisma db push
```

---

## SharedState (`bot/state.py`)

Thread-safe bridge between the trading loop and the FastAPI server. Uses `threading.Lock` for read/write safety and `threading.Event` for signals.

| Attribute | Purpose |
|-----------|---------|
| `bot_running` | Is the trading thread alive |
| `trading_paused` | Manual pause from dashboard |
| `position_state` | `'flat'` / `'long'` / `'short'` |
| `entry_price`, `stop_loss`, `position_size` | Current position details |
| `unrealized_pnl` | Live PnL % |
| `balance`, `peak_equity` | Account balance tracking |
| `last_signal` | Last `calculate_signals()` output dict (kernel values only) |
| `last_action` | Last trade action string (e.g. `open_long`) or None |
| `last_reason` | Reason for last action (e.g. `bullish_change`) or None |
| `signal_seq` | Counter incremented every update_signal() call (heartbeat) |
| `action_seq` | Counter incremented only when action is not None (trade signal) |
| `signal_history` | Capped list of last 50 non-None actions with metadata |
| `config` | Mutable config dict |
| `config_changed` | Event set when dashboard updates config |
| `emergency_close` | Event set when dashboard triggers force-close |
| `next_candle_time` | Epoch seconds until next candle (for countdown UI) |

### Signal state persistence

`signal_seq`, `action_seq`, `signal_history`, `last_signal`, `last_action`, and `last_reason` are **persisted to `signal_state.json`** after every `update_signal()` call. On startup, `_load_signal_state()` restores them from disk.

This means:
- **Sequence counters never reset to 0** after a container/process restart
- **Signal history survives restarts** — external pollers (e.g. Midas) see a monotonically increasing `signal_seq` and can continue from the correct baseline
- Writes are **atomic** (write to `.tmp` + `os.replace`) — no corruption if the process is killed mid-write

The file path defaults to `signal_state.json` in the working directory. Tests override it via the `disable_signal_state_persistence` fixture in `tests/conftest.py` (patches `bot.state.STATE_FILE` to a `tmp_path`).

`signal_state.json` is bind-mounted as a Docker volume (`./data/signal_state.json:/app/signal_state.json`) so it persists across image upgrades and `docker compose up --force-recreate`.

---

## Validation tools

| Script | Purpose |
|--------|---------|
| `validate_kernels.py` | Compares Python kernel output against known TradingView values. Acceptable diff: <0.07%. |
| `validate_signals.py` | Validates signal logic against Pine Script reference output. |

---

## Setup & run

```bash
# 1. Install dependencies
pip install -r requirements.txt

# 2. Set up Prisma
prisma generate
prisma db push

# 3. Configure API keys
cp .env.example .env
# Edit .env:
#   BINANCE_TESTNET_API_KEY=...
#   BINANCE_TESTNET_SECRET_KEY=...

# 4. Run the bot (trading loop + dashboard)
python main.py

# Dashboard at http://localhost:8080
```

### Run only backtesting / autoresearch (no live trading)

```bash
python autoresearch.py
python backtest.py --symbol ETHUSDT --start 2024-01-01
```

---

## Important caveats

- **Stop loss is simulated** — checked on candle close only (1h). In fast markets, actual loss can exceed SL_PERCENT. Consider adding a `STOP_MARKET` order on Binance as a safety net.
- **Testnet only** — `TESTNET_BASE_URL` points to `testnet.binancefuture.com`. Never switch to mainnet without explicit env change + review.
- **Single symbol** — bot is hardcoded to one symbol per run (SYMBOL in config). Multi-asset live trading is not implemented.
- **1h granularity** — the loop sleeps until the next 1h candle close. Emergency close is checked every 5 seconds via `smart_sleep()`.

---

## DevOps

### Rules

1. **CI must pass before merging** — no merge to `main` without green CI (lint + kernel validation + signal validation + Docker build).
2. **Never commit secrets** — `.env` is gitignored. All sensitive values go into GitHub Secrets or the server `.env` file only.
3. **Validate before every deploy** — `validate_kernels.py` and `validate_signals.py` run automatically in CI. A red validation = blocked deploy.
4. **One change at a time** — kernel/strategy changes must include a validation run. Do not bundle multiple logic changes in one commit.
5. **Never touch mainnet** — `TESTNET_BASE_URL` stays in config. Mainnet promotion requires an explicit, reviewed config change.
6. **Image tags** — production always uses `latest` (built from `main`). Specific commits are tagged `sha-<short-sha>` for rollback traceability.
7. **Persistent data is NOT in the image** — runtime files (`autoresearch.db`, `trade_log.csv`, logs, CSVs) live in `./data/` on the host and are bind-mounted. Never bake live data into the image.
8. **Read PROJECT.md before any change. Update PROJECT.md after any change.**

---

### Docker

#### New files added

| File | Purpose |
|------|---------|
| `Dockerfile` | Multi-layer build: deps → Prisma generate → app code |
| `docker-entrypoint.sh` | Init runtime files, run `prisma db push`, start `main.py` |
| `docker-compose.yml` | Production service definition with volumes + healthcheck |
| `.dockerignore` | Excludes `.env`, logs, DB, `__pycache__`, build artifacts |

#### How the image is built

```
python:3.11-slim
  → install gcc + curl (system)
  → pip install -r requirements.txt
  → copy prisma/schema.prisma → prisma generate  (cached layer)
  → copy full source
  → EXPOSE 8080
  → ENTRYPOINT docker-entrypoint.sh
```

`prisma generate` runs at build time (generates Python client code into the image).  
`prisma db push` runs at container startup (creates/migrates the SQLite DB file on the host volume).

#### Persistent data layout on the host

```
/opt/trading-bot/           ← deployment directory on server
├── .env                    ← API keys (never in repo)
├── docker-compose.yml      ← copied from repo or pulled
├── cache/                  ← OHLCV CSV cache (optional, speeds up autoresearch)
└── data/                   ← all runtime data (bind-mounted into container)
    ├── autoresearch.db
    ├── trade_log.csv
    ├── trading_bot.log
    ├── autoresearch_results.csv
    ├── autoresearch_alltime.csv
    ├── autoresearch_meta.json
    └── signal_state.json   ← signal counters + history (survives restarts)
```

#### First-time setup on a server

```bash
# 1. Create deployment directory
mkdir -p /opt/trading-bot/data /opt/trading-bot/cache

# 2. Copy .env with real API keys
cp .env.example /opt/trading-bot/.env
# Edit /opt/trading-bot/.env — set BINANCE keys and UPLOAD_API_KEY

# 3. Copy compose file
cp docker-compose.yml /opt/trading-bot/docker-compose.yml

# 4. Pre-create data files (prevents Docker creating them as directories)
touch /opt/trading-bot/data/trade_log.csv
touch /opt/trading-bot/data/trading_bot.log
touch /opt/trading-bot/data/autoresearch_results.csv
touch /opt/trading-bot/data/autoresearch_alltime.csv
touch /opt/trading-bot/data/signal_state.json
echo '{}' > /opt/trading-bot/data/autoresearch_meta.json

# 5. Pull image and start
cd /opt/trading-bot
docker compose pull
docker compose up -d

# 6. Watch logs
docker logs -f trading-bot
```

#### Local development with Docker

```bash
# Build locally and run (uses build: . in compose or override)
docker build -t trading-bot:local .
docker run --rm -p 8080:8080 --env-file .env trading-bot:local

# Or with compose (change image: to build: . in docker-compose.yml)
docker compose up --build
```

#### Useful Docker commands

```bash
# Status + health
docker ps
docker inspect --format='{{.State.Health.Status}}' trading-bot

# Live logs
docker logs -f trading-bot

# Restart without re-pulling
docker compose restart trading-bot

# Force recreate with latest image
docker compose up -d --force-recreate

# Open shell in running container
docker exec -it trading-bot sh

# Backup database
docker cp trading-bot:/app/autoresearch.db ./backup-$(date +%Y%m%d).db
```

---

### CI/CD (GitHub Actions)

#### Workflows

| File | Trigger | Purpose |
|------|---------|---------|
| `.github/workflows/ci.yml` | Every push / PR | Lint + validate + Docker build check |
| `.github/workflows/deploy.yml` | Manual (`workflow_dispatch`) | Build image → push to GHCR → SSH deploy |

#### CI pipeline (`.github/workflows/ci.yml`)

```
push / pull_request (any branch)
  └── lint job
        ├── pip install + prisma generate (DATABASE_URL=file:./autoresearch.db)
        └── flake8 lint (max-line-length=120)
  └── docker-build job (runs after lint)
        └── docker build (smoke test — ensures image builds)
```

> **Note:** `validate_kernels.py` and `validate_signals.py` are **manual-only** tools —
> they connect to Binance live API and are intended for human comparison against TradingView,
> not for automated CI. Run them locally: `python validate_kernels.py`.

#### Deploy pipeline (`.github/workflows/deploy.yml`)

Triggered **manually** via `workflow_dispatch` — can be run from any branch from the GitHub Actions UI or via CLI:

```bash
gh workflow run deploy.yml --ref feat/my-branch
```

```
workflow_dispatch (any branch)
  └── build-and-push job
        ├── docker/login to ghcr.io (GITHUB_TOKEN)
        ├── docker/metadata → tags: latest + sha-<short>
        └── docker/build-push → ghcr.io/YOUR_USERNAME/trading-bot-standalone:latest
  └── deploy job (needs: build-and-push)
        └── SSH into server
              ├── docker pull latest
              ├── docker compose up -d --force-recreate
              └── healthcheck: /api/status must return 200 within 90s
```

The `deploy` job is **gated by a repository variable** `DEPLOY_ENABLED`. Until the server is ready and secrets are configured, the job is skipped (not failed). To enable:
1. Go to **Settings → Variables → Actions → New repository variable**
2. Name: `DEPLOY_ENABLED`, Value: `true`
3. Add the four required secrets (see table above)

#### GitHub Secrets required

| Secret | Where used | Value |
|--------|-----------|-------|
| `DEPLOY_HOST` | deploy.yml | `YOUR_SERVER_IP` |
| `DEPLOY_USER` | deploy.yml | `root` |
| `DEPLOY_SSH_KEY` | deploy.yml | Contents of `~/.ssh/your_deploy_key` |
| `DEPLOY_PORT` | deploy.yml | `22` |

Repository variable (Settings → Variables → Actions):

| Variable | Value |
|----------|-------|
| `DEPLOY_ENABLED` | `true` |

`GITHUB_TOKEN` is auto-injected by GitHub Actions — no setup needed.  
`BINANCE_TESTNET_API_KEY` / `BINANCE_TESTNET_SECRET_KEY` / `UPLOAD_API_KEY` live in the server `.env` file at `/opt/trading-bot/.env`, **not** in GitHub Secrets (they never leave the server).

#### How to set up SSH deploy key

```bash
# On your local machine: generate a dedicated deploy key (no passphrase)
ssh-keygen -t ed25519 -C "github-actions-deploy" -f ~/.ssh/trading-bot-deploy -N ""

# Add public key to server
ssh-copy-id -i ~/.ssh/trading-bot-deploy.pub user@your-server

# Add private key to GitHub Secrets as DEPLOY_SSH_KEY
cat ~/.ssh/trading-bot-deploy   # copy this value into the secret
```

---

### Rollback procedure

```bash
# SSH into server
ssh user@your-server

# List available image tags
docker images ghcr.io/YOUR_USERNAME/trading-bot-standalone

# Pull a specific commit SHA tag
docker pull ghcr.io/YOUR_USERNAME/trading-bot-standalone:sha-<abc1234>

# Update compose to use that tag, then recreate
cd /opt/trading-bot
# Edit docker-compose.yml: image: ghcr.io/.../trading-bot-standalone:sha-<abc1234>
docker compose up -d --force-recreate
```
