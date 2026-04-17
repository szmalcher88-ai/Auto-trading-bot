# Orchestrator System - Testing Guide

This document describes how to test the distributed worker orchestrator system.

## Prerequisites

1. Python environment with all dependencies installed (`pip install -r requirements.txt`)
2. `.env` file with `UPLOAD_API_KEY` set
3. At least one terminal window for the server, one for the worker

## Test Procedure

### Step 1: Start the Server

In terminal 1, start the main trading bot server (which includes the orchestrator API):

```powershell
python main.py
```

The server should start on `http://localhost:8080` (or `http://0.0.0.0:8080`).

Verify the server is running by opening `http://localhost:8080` in your browser. You should see the dashboard.

### Step 2: Check the Orchestrator Tab

1. Open the dashboard in your browser: `http://localhost:8080`
2. Click on the **Orchestrator** tab
3. You should see:
   - "CREATE NEW SWEEP" section with a "+ New Sweep" button
   - "ACTIVE SWEEPS" section (empty initially)
   - "WORKERS" section (empty initially)

### Step 3: Start a Worker

In terminal 2, start a worker client:

```powershell
$env:SERVER_URL = "http://localhost:8080"
$env:UPLOAD_API_KEY = "your-key-from-env-file"
python push-to-server.py
```

Or with command-line args:

```powershell
python push-to-server.py --server http://localhost:8080 --key your-key-from-env-file --name "Test Worker"
```

The worker should:
1. Register with the server
2. Pre-fetch price data for ETH/BTC/SOL (this takes ~30 seconds)
3. Start polling for jobs

### Step 4: Verify Worker Registration

Go back to the dashboard Orchestrator tab. In the "WORKERS" section, you should now see your worker listed with:
- Name (e.g., "Test Worker" or "worker-{hostname}")
- Hostname
- Status: "Idle" (green)
- Last Seen: current timestamp
- CPU Cores: your machine's CPU count

### Step 5: Create a Test Sweep

In the Orchestrator tab:

1. Click "+ New Sweep"
2. Fill in the form:
   - **Sweep Name**: "Test Sweep Small"
   - **Batch Size**: 10
   - **h_min**: 60
   - **h_max**: 80
   - **h_step**: 10 (this creates 3 values: 60, 70, 80)
   - **x_min**: 60
   - **x_max**: 70
   - **x_step**: 10 (this creates 2 values: 60, 70)
   - **Smoothing**: "on"
3. Click "Create Sweep"

This should create a sweep with 3 × 2 = 6 configs total, split into 1 job (batch size 10).

### Step 6: Watch the Worker Process the Job

1. In terminal 2 (worker), you should see:
   - `[worker] Claimed job {job-id} with 6 configs`
   - Progress messages as each config is evaluated
   - `[worker] Submitted job {job-id} with 6 results`

2. In the dashboard Orchestrator tab:
   - The sweep should show "RUNNING" status
   - Progress bar should fill up as configs complete
   - Job stats should update (Pending → Claimed → Completed)
   - Worker status should change from "Idle" to "Busy" and back to "Idle"

### Step 7: Verify Results

1. Click on the **AutoResearch** tab
2. You should see the 6 new results in the top-20 table
3. Click on the **Leaderboard** tab
4. The new configs should appear in the all-time leaderboard

### Step 8: Test Multiple Workers (Optional)

If you have access to another machine or want to test locally:

1. Open a third terminal
2. Start another worker with a different name:
   ```powershell
   python push-to-server.py --server http://localhost:8080 --key your-key --name "Worker 2"
   ```
3. Create a larger sweep (e.g., h_min=30, h_max=110, h_step=10 → 81 configs)
4. Watch both workers claim and process jobs in parallel

### Step 9: Test Sweep Controls

In the Orchestrator tab:

1. **Pause a running sweep**: Click "Pause" button on an active sweep
   - Workers should stop claiming new jobs from this sweep
   - Status should change to "PAUSED"

2. **Resume a paused sweep**: Click "Resume" button
   - Workers should start claiming jobs again
   - Status should change back to "RUNNING"

3. **Cancel a sweep**: Click "Cancel" button
   - Sweep status should change to "CANCELLED"
   - No more jobs will be claimed

4. **Delete a sweep**: Click "Delete" button and confirm
   - Sweep should disappear from the list

### Step 10: Test Worker Heartbeat & Timeout

1. With a worker running, check the "Last Seen" timestamp in the Workers table
2. It should update every ~30 seconds
3. Stop the worker (Ctrl+C)
4. Wait 5+ minutes
5. Refresh the dashboard - the worker status should show "Offline" (gray)

## Expected Behavior Summary

### Worker Lifecycle
- **Register** → Pre-fetch data → **Claim job** → Evaluate configs → **Submit results** → Repeat
- Heartbeat every 30 seconds keeps the worker "alive"
- Graceful shutdown on Ctrl+C

### Job Claiming
- Jobs are claimed atomically (no two workers get the same job)
- Stale jobs (claimed >10 min ago with no heartbeat) are automatically reset to pending

### Results Merging
- Worker results are merged into `autoresearch_alltime.csv`
- Results appear in both AutoResearch and Leaderboard tabs
- Duplicate configs (same params) are deduplicated by config hash

## Troubleshooting

### Worker can't connect to server
- Check `SERVER_URL` is correct (include `http://`)
- Check `UPLOAD_API_KEY` matches between `.env` and worker
- Check firewall/network settings

### "Orchestrator not available" error
- Check that `bot/orchestrator.py` exists and has no import errors
- Check that `autoresearch.db` can be created/written in the project directory

### Worker gets no jobs
- Check that a sweep exists and has status "RUNNING" (not "PENDING" or "PAUSED")
- Check that the sweep has pending jobs (not all completed/claimed)

### Results don't appear in dashboard
- Check `autoresearch_alltime.csv` was updated (file modification time)
- Refresh the dashboard (F5)
- Check browser console for errors

## Success Criteria

The system is working correctly if:

1. ✅ Worker registers and appears in Workers table
2. ✅ Sweep is created with correct config count
3. ✅ Worker claims job and processes configs
4. ✅ Progress bar updates in real-time
5. ✅ Results appear in AutoResearch and Leaderboard tabs
6. ✅ Worker heartbeat keeps status "Idle" or "Busy" (not "Offline")
7. ✅ Multiple workers can process jobs from the same sweep without overlap
8. ✅ Sweep controls (pause/resume/cancel/delete) work as expected

## Next Steps

Once basic functionality is verified:

1. Test with larger sweeps (100+ configs)
2. Test with multiple workers on different machines
3. Test worker crash recovery (kill worker mid-job, verify job returns to pending)
4. Test server restart (workers should reconnect/re-register)
5. Monitor performance (configs/second, network bandwidth)
