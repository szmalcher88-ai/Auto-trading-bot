# AutoResearch Runner

Runs a local parameter-sweep backtest and uploads the results to the
production server via `POST /api/autoresearch/upload`.

## Setup

```bash
pip install -r requirements.txt
```

Copy `.env.example` → `.env` and fill in your Binance Testnet keys:

```
BINANCE_API_KEY=...
BINANCE_API_SECRET=...
```

## Usage

### Quick run (env vars)

```powershell
$env:SERVER_URL    = "http://YOUR_SERVER_IP:8080"
$env:UPLOAD_API_KEY = "your-upload-key"

python push-to-server.py --h-min 30 --h-max 110 --h-step 10
```

### Quick run (CLI flags)

```bash
python push-to-server.py \
  --server http://YOUR_SERVER_IP:8080 \
  --key your-upload-key \
  --h-min 30 --h-max 110 --h-step 10 \
  --x-min 40 --x-max 70 --x-step 2 \
  --smoothing both \
  --r-values 5 8 10 15 20
```

### Broad sweep (≈2.5 hours)

```bash
python push-to-server.py \
  --server http://YOUR_SERVER_IP:8080 \
  --key your-upload-key \
  --h-min 30 --h-max 120 --h-step 5 \
  --x-min 40 --x-max 70 --x-step 2 \
  --smoothing both \
  --r-values 5 8 10 15 20
```

## What happens

1. `autoresearch.py` runs locally (uses/builds a `cache/` folder so candles are
   only downloaded once).
2. Results are written to `autoresearch_results.csv`, `autoresearch_alltime.csv`,
   and `autoresearch_meta.json`.
3. `push-to-server.py` POSTs all three to the server — the dashboard leaderboard
   updates immediately.

## Files

| File | Purpose |
|------|---------|
| `autoresearch.py` | Parameter-sweep engine |
| `push-to-server.py` | Run sweep → upload to prod |
| `backtest.py` | Single-combo backtest engine |
| `bot/` | Shared strategy/kernel modules |
| `prisma/schema.prisma` | SQLite schema (optional local DB) |
| `requirements.txt` | Python dependencies |
| `cache/` | Candle cache (auto-created, commit to skip re-download) |
