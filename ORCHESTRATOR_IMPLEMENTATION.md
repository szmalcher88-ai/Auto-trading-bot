# Distributed Worker Orchestrator - Implementation Summary

## Overview

Successfully implemented a pull-based distributed worker system for AutoResearch parameter sweeps. The server now orchestrates config generation and job distribution, while workers poll for work, compute backtests locally, and submit results back.

## Architecture Changes

### Before (Old System)
- `push-to-server.py` decided what to compute via CLI args
- Ran `autoresearch.py` as subprocess
- No coordination between workers → potential duplicate work
- One-shot execution model

### After (New System)
- Server is single source of truth for sweep plans
- Workers are "dumb compute nodes" that poll for jobs
- Atomic job claiming prevents duplicate work
- Long-running worker processes with heartbeat monitoring
- Real-time progress tracking in dashboard

## Files Created

### 1. `bot/orchestrator.py` (new)
**Purpose**: Core orchestration logic and database layer

**Key Components**:
- `OrchestratorDB` class - SQLite CRUD for sweeps, jobs, workers
- `generate_grid_configs()` - Generates all config combos from param ranges
- `split_into_batches()` - Chunks configs into job-sized batches
- Atomic job claiming with `UPDATE ... WHERE status='pending' LIMIT 1`
- Automatic timeout recovery for stale jobs (>10 min without heartbeat)

**Database Schema**:
- `sweeps` table - Campaign metadata, progress tracking
- `jobs` table - Batches of configs with status (pending/claimed/completed/failed)
- `workers` table - Registered compute nodes with heartbeat timestamps

### 2. `ORCHESTRATOR_TEST.md` (new)
**Purpose**: Step-by-step testing guide

**Contents**:
- How to start server and workers
- How to create sweeps via dashboard
- How to verify results
- Troubleshooting common issues
- Success criteria checklist

### 3. `ORCHESTRATOR_IMPLEMENTATION.md` (this file)
**Purpose**: Implementation documentation

## Files Modified

### 1. `autoresearch.py`
**Changes**:
- Added `evaluate_combo()` function - extracted from grid loop
- Consolidates backtest evaluation logic into one importable function
- Workers call this directly (no subprocess overhead)

**Function Signature**:
```python
def evaluate_combo(combo_dict, asset_data, assets, 
                   apply_config_fn, run_backtest_fn, calculate_metrics_fn)
```

**Returns**: Dict with asset_results, score, balanced_score, confidence, config_hash, row

### 2. `api/server.py`
**Changes**:
- Added 11 new orchestrator endpoints
- Updated CORS to allow PATCH and DELETE methods
- Lazy import of `OrchestratorDB` (graceful degradation if not available)

**New Endpoints**:

#### Sweep Management
- `POST /api/sweeps` - Create sweep (generates configs, splits into jobs)
- `GET /api/sweeps` - List all sweeps with progress
- `GET /api/sweeps/{id}` - Get detailed sweep info
- `PATCH /api/sweeps/{id}` - Update status (pause/resume/cancel)
- `DELETE /api/sweeps/{id}` - Delete sweep and jobs

#### Worker Operations
- `POST /api/worker/register` - Register worker, returns worker_id
- `POST /api/worker/claim` - Atomically claim next pending job
- `POST /api/worker/submit` - Submit completed job results
- `POST /api/worker/heartbeat` - Keep-alive ping
- `GET /api/workers` - List all workers with status

**Authentication**: All endpoints use `x-upload-key` header (same as existing upload API)

### 3. `push-to-server.py`
**Changes**: Complete rewrite from "run-once wrapper" to "long-running worker client"

**New Architecture**:
```
WorkerClient class:
├── register() - Register with server
├── prefetch_data() - Cache price data for all assets
├── claim_job() - Poll for next job
├── evaluate_configs() - Run backtests (calls autoresearch.evaluate_combo)
├── submit_job() - Upload results
├── heartbeat_loop() - Background thread, ping every 30s
└── run() - Main loop with exponential backoff
```

**Features**:
- Graceful shutdown on Ctrl+C (signal handlers)
- Automatic retry with exponential backoff (10s → 60s)
- Direct import of backtest functions (no subprocess)
- Shares price data cache across all configs in a batch

**Usage**:
```powershell
python push-to-server.py --server http://... --key ... [--name "My PC"]
```

### 4. `dashboard/index.html`
**Changes**: Added new "Orchestrator" tab

**UI Components**:

#### Create Sweep Form
- Sweep name, batch size
- Parameter ranges: h_min/max/step, x_min/max/step
- Smoothing mode selector
- Create/Cancel buttons

#### Active Sweeps List
- Sweep name, status, creation time
- Progress bar (completed/total configs)
- Job stats (pending/claimed/completed/failed)
- Control buttons (Pause/Resume/Cancel/Delete)

#### Workers Table
- Worker name, hostname
- Status (Idle/Busy/Offline) with color coding
- Last seen timestamp
- CPU core count

**Real-time Updates**: Polls `/api/sweeps` and `/api/workers` every 5 seconds

## Data Flow

```
1. User creates sweep in dashboard
   └─> POST /api/sweeps
       └─> generate_grid_configs() creates all combos
       └─> split_into_batches() creates jobs
       └─> Store in SQLite

2. Worker polls for work
   └─> POST /api/worker/claim?worker_id=...
       └─> Atomic UPDATE jobs SET status='claimed'
       └─> Returns batch of configs

3. Worker evaluates configs
   └─> evaluate_combo() for each config
       └─> Direct calls to backtest functions
       └─> Returns results dict

4. Worker submits results
   └─> POST /api/worker/submit
       └─> Update job status to 'completed'
       └─> Merge results into autoresearch_alltime.csv
       └─> Increment sweep progress

5. Dashboard polls for updates
   └─> GET /api/sweeps (every 5s)
   └─> GET /api/workers (every 5s)
   └─> Real-time progress bars and status
```

## Key Design Decisions

### 1. SQLite is Sufficient
- For 2-3 workers, SQLite handles atomic job claiming without issues
- `UPDATE ... WHERE status='pending' LIMIT 1` prevents race conditions
- No need for Redis or Postgres

### 2. Config Generation on Server
- Server generates ALL configs when sweep is created
- Guarantees zero overlap between workers
- Each config is computed exactly once

### 3. Worker Imports Functions Directly
- No subprocess overhead (was ~2.9s per combo, now faster)
- Shares price data cache across all configs in a batch
- Simpler error handling and debugging

### 4. Automatic Timeout Recovery
- Jobs claimed >10 min ago with no heartbeat are reset to pending
- Another worker can pick them up
- Handles worker crashes gracefully

### 5. Backward Compatibility Preserved
- Existing `POST /api/autoresearch/upload` still works
- Standalone `autoresearch.py` CLI still works
- CSV files and formats unchanged
- New system is additive, not breaking

## Testing

See `ORCHESTRATOR_TEST.md` for detailed testing procedure.

**Quick Test**:
1. Start server: `python main.py`
2. Start worker: `python push-to-server.py --server http://localhost:8080 --key your-key`
3. Open dashboard: `http://localhost:8080`
4. Go to Orchestrator tab
5. Create a small sweep (h=60-80 step 10, x=60-70 step 10 = 6 configs)
6. Watch worker claim, process, and submit results
7. Verify results appear in AutoResearch and Leaderboard tabs

## Performance Improvements

### Before
- Subprocess overhead: ~2.9s per combo
- No parallelization (single machine)
- Manual coordination between machines

### After
- Direct function calls: faster (no subprocess startup)
- Parallel execution across 2-3 workers
- Automatic job distribution
- Real-time progress monitoring

**Expected Speedup**: ~2-3x with 2-3 workers (linear scaling for independent configs)

## Future Enhancements

### Smart Mode Distribution (Not Implemented)
- Smart mode is currently single-worker only
- Adaptive phases (explore/exploit/refine) depend on prior results
- Distributing smart mode requires more complex coordination
- Could implement as "smart coordinator" that generates batches dynamically

### Possible Additions
1. **Job retry limit** - Mark jobs as failed after N retries
2. **Worker priority** - Prefer faster workers for urgent sweeps
3. **Partial results** - Submit partial results on worker crash
4. **Result streaming** - Stream results as they complete (not batched)
5. **Sweep templates** - Save/load common sweep configurations
6. **Worker resource limits** - CPU/memory constraints per worker
7. **Job dependencies** - Chain sweeps (sweep B uses results from sweep A)

## Migration Guide

### For Existing Users

**No changes required** - the old workflow still works:
```powershell
# Old way (still works)
python autoresearch.py --h-min 30 --h-max 110
```

**To use new orchestrator**:
1. Start server as usual: `python main.py`
2. Start worker(s): `python push-to-server.py --server http://localhost:8080 --key your-key`
3. Create sweeps via dashboard Orchestrator tab (or via API)

### For Multi-Machine Setups

**Before**: Manual coordination
```powershell
# Machine 1
python push-to-server.py --h-min 30 --h-max 60

# Machine 2
python push-to-server.py --h-min 61 --h-max 110
```

**After**: Automatic distribution
```powershell
# Server (one machine)
python main.py

# Worker 1 (any machine)
python push-to-server.py --server http://server-ip:8080 --key your-key

# Worker 2 (any machine)
python push-to-server.py --server http://server-ip:8080 --key your-key
```

Create one sweep in dashboard, both workers automatically share the work.

## Security Considerations

### Authentication
- All orchestrator endpoints require `x-upload-key` header
- Same key as existing upload API (`UPLOAD_API_KEY` in `.env`)
- Workers must have valid key to register, claim, or submit

### Network Exposure
- Server binds to `0.0.0.0:8080` (accessible from network)
- For production: use reverse proxy (nginx) with HTTPS
- Consider VPN or firewall rules for worker access

### Data Validation
- Server validates sweep params (ranges, batch size)
- Job results are validated before merging into alltime CSV
- Config hashes prevent duplicate work

## Monitoring & Debugging

### Dashboard Indicators
- **Worker status**: Idle (green) / Busy (yellow) / Offline (gray)
- **Sweep progress**: Real-time progress bar
- **Job stats**: Pending / Claimed / Completed / Failed counts

### Log Files
- Server logs: `trading_bot.log`
- Worker logs: stdout/stderr (redirect to file if needed)

### Database Inspection
```powershell
sqlite3 autoresearch.db
> SELECT * FROM sweeps;
> SELECT * FROM jobs WHERE status='claimed';
> SELECT * FROM workers;
```

### Common Issues
1. **Worker shows Offline**: Check heartbeat (last_seen timestamp)
2. **Jobs stuck in Claimed**: Check for stale jobs (>10 min), server resets automatically
3. **No jobs available**: Check sweep status is 'running' (not 'pending' or 'paused')

## Conclusion

The distributed worker orchestrator system is now fully implemented and ready for testing. All planned features have been completed:

✅ OrchestratorDB with SQLite schema  
✅ Sweep/job/worker CRUD operations  
✅ Atomic job claiming with timeout recovery  
✅ Worker client with heartbeat and graceful shutdown  
✅ Dashboard UI with real-time updates  
✅ API endpoints for all operations  
✅ Backward compatibility preserved  
✅ Testing guide and documentation  

The system is designed for 2-3 workers and provides significant speedup over single-machine execution while maintaining simplicity and reliability.
