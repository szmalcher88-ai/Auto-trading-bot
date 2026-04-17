# Database Integration - AutoResearch

## Overview

The trading bot now uses **SQLite** with **Prisma Client Python** to store all autoresearch results in a structured database, replacing the previous CSV-only approach.

## Benefits

- **Better Querying**: Fast lookups, filtering, and sorting of results
- **Historical Tracking**: All runs are preserved with timestamps and metadata
- **Data Integrity**: Proper relationships between runs and results
- **Concurrency Safe**: Multiple runs won't corrupt data
- **Backward Compatible**: CSV export still maintained for compatibility

## Database Schema

### Tables

#### `autoresearch_runs`
Stores metadata about each autoresearch execution:
- `id` (UUID): Unique run identifier
- `startedAt`: When the run began
- `completedAt`: When the run finished (null if still running)
- `totalCombinations`: Number of parameter combinations tested
- `assetsCount`: Number of assets tested
- `assets`: Comma-separated list of asset symbols
- `status`: "running" or "completed"
- `durationSeconds`: Total execution time

#### `autoresearch_results`
Stores individual configuration test results:
- `id`: Auto-incrementing primary key
- `runId`: Foreign key to `autoresearch_runs`
- **Parameters**: `lookbackWindow`, `regressionLevel`, `useKernelSmoothing`, `relativeWeight`, `lag`, `atrPeriod`, `atrMultiplier`, `volatilityMin`, `volatilityMax`, `reentryDelay`
- **Metrics per asset** (ETH, BTC, SOL, AVAX, BNB, ADA, DOGE, MATIC):
  - `{asset}Trades`: Number of trades
  - `{asset}ProfitFactor`: Profit factor
  - `{asset}MaxDrawdown`: Maximum drawdown percentage
  - `{asset}Profit`: Net profit percentage
- `score`: Cross-asset consistency score
- `createdAt`: Timestamp

### Indexes
- `runId` for fast run-based queries
- `score` for leaderboard queries
- `(lookbackWindow, regressionLevel, useKernelSmoothing)` for config lookups

## File Locations

- **Database**: `autoresearch.db` (SQLite file in project root)
- **Schema**: `prisma/schema.prisma`
- **Database Helper**: `bot/db.py`

## Setup

### Initial Setup
```bash
pip install prisma
prisma generate
prisma db push
```

### After Schema Changes
```bash
prisma db push
prisma generate
```

## Usage

### Running AutoResearch
The `autoresearch.py` script automatically:
1. Creates a new run record in the database
2. Tests all parameter combinations
3. Saves each result to the database in real-time
4. Marks the run as completed with duration
5. Also exports to CSV for backward compatibility

### Dashboard API
The dashboard reads from the database by default:
- `/api/autoresearch` - Latest completed run results
- `/api/leaderboard` - Top configurations across all runs
- Falls back to CSV if database is unavailable

### Triggering from Dashboard
The "Run AutoResearch" button in the dashboard:
1. Sends parameters to `/api/autoresearch/run`
2. Starts `autoresearch.py` in background
3. Results automatically saved to database
4. Dashboard refreshes after estimated completion time

## Data Flow

```
User clicks "Run AutoResearch"
  ↓
Dashboard → POST /api/autoresearch/run
  ↓
FastAPI spawns: python autoresearch.py --args
  ↓
autoresearch.py:
  - Creates run record in DB
  - Tests combinations
  - Saves each result to DB
  - Marks run as completed
  - Also exports to CSV
  ↓
Dashboard → GET /api/autoresearch
  ↓
FastAPI reads from database (or CSV fallback)
  ↓
Dashboard displays results
```

## Backward Compatibility

- CSV files (`autoresearch_results.csv`, `autoresearch_meta.json`, `autoresearch_alltime.csv`) are still generated
- API endpoints fall back to CSV if database is unavailable
- Existing CSV data can be imported into database if needed

## Database Maintenance

### View Database Contents
```bash
# Install SQLite CLI
# Then:
sqlite3 autoresearch.db "SELECT * FROM autoresearch_runs;"
sqlite3 autoresearch.db "SELECT * FROM autoresearch_results ORDER BY score DESC LIMIT 10;"
```

### Backup Database
```bash
cp autoresearch.db autoresearch_backup.db
```

### Reset Database
```bash
rm autoresearch.db
prisma db push
```

## Future Enhancements

Potential features enabled by the database:
- Filter results by date range
- Compare runs side-by-side
- Track parameter evolution over time
- Identify most-tested parameter ranges
- Export custom reports
- API endpoints for advanced queries
