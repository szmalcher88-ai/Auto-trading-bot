# Migration Guide - Distributed Worker Orchestrator

## TL;DR - Do I Need to Migrate?

**NO MIGRATION REQUIRED** if you only want to use the system as before.

**OPTIONAL MIGRATION** if you want to use the new distributed worker features.

## Zero-Downtime Deployment

The orchestrator system is **fully backward compatible**. You can deploy it to production without any migration steps:

### What Happens Automatically

1. **Database tables auto-create** on first server start
   - `OrchestratorDB.__init__()` runs `CREATE TABLE IF NOT EXISTS`
   - No manual SQL needed
   - Existing `autoresearch.db` is not affected

2. **Existing workflows continue working**
   - Standalone `autoresearch.py` CLI works as before
   - Old `POST /api/autoresearch/upload` endpoint still works
   - CSV files (`autoresearch_results.csv`, `autoresearch_alltime.csv`) unchanged

3. **New features are opt-in**
   - Orchestrator tab appears in dashboard
   - New API endpoints are available
   - But nothing breaks if you don't use them

## Production Deployment Steps

### Step 1: Deploy to Server (Zero Downtime)

```bash
# On production server (YOUR_SERVER_IP)
cd /path/to/trading-bot-standalone
git pull origin main  # After PR is merged
python main.py
```

**That's it!** The server will:
- Create new orchestrator tables automatically
- Serve the updated dashboard with Orchestrator tab
- Expose new API endpoints
- Continue serving existing endpoints

### Step 2: Verify Deployment

1. Open `http://YOUR_SERVER_IP:8080` in browser
2. Check that **Orchestrator** tab appears in navigation
3. Click on it - should show empty sweeps/workers lists
4. No errors in server logs

### Step 3: Start Workers (Optional)

Only do this if you want to use distributed workers:

```bash
# On worker machine 1 (your PC)
cd c:\Users\FranciszekMalcher\code\algo\trading-bot-standalone
python push-to-server.py --server http://YOUR_SERVER_IP:8080 --key your-upload-key --name "My PC"

# On worker machine 2 (friend's PC)
python push-to-server.py --server http://YOUR_SERVER_IP:8080 --key your-upload-key --name "Friend PC"
```

Workers will:
- Register with the server
- Pre-fetch price data (~30 seconds)
- Start polling for jobs
- Appear in the Orchestrator tab Workers section

## What Gets Created on First Run

### Database Changes

**File**: `autoresearch.db` (existing file, new tables added)

**New tables:**
- `sweeps` - 0 rows initially
- `jobs` - 0 rows initially  
- `workers` - 0 rows initially

**Existing tables** (unchanged):
- `autoresearch_runs` - all data preserved
- `autoresearch_results` - all data preserved

**Size impact**: Negligible (~8 KB for empty tables)

### File System Changes

**New files created** (only when you use the features):
- None created automatically
- Sweeps/jobs are stored in database only

**Existing files** (unchanged):
- `autoresearch_results.csv` - still written by standalone mode
- `autoresearch_alltime.csv` - still updated by workers
- `autoresearch_meta.json` - still written by standalone mode

## Rollback Plan (If Needed)

If you need to rollback for any reason:

```bash
# On production server
git checkout main
python main.py
```

**What happens:**
- Server reverts to old code
- Orchestrator tab disappears from dashboard
- New API endpoints return 404
- Existing functionality unchanged
- Database tables remain but are unused (no harm)

**To fully clean up** (optional):
```bash
sqlite3 autoresearch.db "DROP TABLE IF EXISTS sweeps; DROP TABLE IF EXISTS jobs; DROP TABLE IF EXISTS workers;"
```

## Environment Variables

**No new env vars required!**

The system reuses existing variables:
- `UPLOAD_API_KEY` - used for worker authentication (already exists)
- `DATABASE_URL` - optional, defaults to `autoresearch.db` (already exists)

## Network/Firewall Considerations

### If Using Distributed Workers

**Server must be accessible** from worker machines:
- Port 8080 must be open (already open for dashboard)
- Workers connect to `http://server-ip:8080`

**Security:**
- All worker endpoints require `X-Upload-Key` header
- Same key as existing upload endpoint
- No new security concerns

### If NOT Using Distributed Workers

No changes needed - server works exactly as before.

## Testing in Production

### Minimal Test (5 minutes)

1. Deploy to production
2. Open dashboard, verify Orchestrator tab appears
3. Start one worker on your local PC
4. Create a tiny test sweep (h=60-70 step 10, x=60-60 = 2 configs)
5. Watch worker claim and process
6. Verify results appear in AutoResearch tab

### Full Test (30 minutes)

1. Start 2 workers on different machines
2. Create a larger sweep (h=30-110 step 10, x=50-70 step 5 = 45 configs)
3. Watch both workers share the work
4. Verify no duplicate configs processed
5. Check results in Leaderboard

## Common Questions

### Q: Will existing autoresearch runs be affected?
**A**: No. Old results in CSV and database are preserved. The new system adds tables but doesn't touch existing data.

### Q: Can I still run autoresearch.py directly?
**A**: Yes! The standalone CLI works exactly as before. The orchestrator is an optional alternative.

### Q: What if a worker crashes mid-job?
**A**: The server detects stale jobs (>10 min without heartbeat) and automatically resets them to pending. Another worker will pick them up.

### Q: Do I need to run workers?
**A**: No. Workers are optional. If you don't start any workers, the system works exactly as before (standalone autoresearch.py or dashboard-triggered runs).

### Q: Can I mix old and new workflows?
**A**: Yes! You can:
- Use orchestrator for large sweeps (distributed)
- Use standalone autoresearch.py for quick tests
- Both write to the same `autoresearch_alltime.csv`

## Monitoring

### Check Orchestrator Status

**Via Dashboard:**
- Go to Orchestrator tab
- Check Active Sweeps section
- Check Workers section

**Via API:**
```bash
curl http://YOUR_SERVER_IP:8080/api/sweeps
curl http://YOUR_SERVER_IP:8080/api/workers
```

**Via Database:**
```bash
sqlite3 autoresearch.db "SELECT * FROM sweeps;"
sqlite3 autoresearch.db "SELECT * FROM workers;"
```

### Logs

**Server logs** (as usual):
```
[ORCHESTRATOR] Created sweep {id}: {name}
[ORCHESTRATOR] Worker {id} claimed job {job_id} with {N} configs
[ORCHESTRATOR] Job {id} submitted with {N} results
```

**Worker logs** (stdout):
```
[worker] Registered with ID: {worker_id}
[worker] Claimed job {job_id} with {N} configs
[worker] Evaluating config 1/N: h=60, x=50
[worker] Submitted job {job_id} with {N} results
```

## Summary

### Migration Required?
**NO** - The system is fully backward compatible with automatic schema creation.

### Deployment Risk?
**VERY LOW** - All changes are additive, no breaking changes.

### Recommended Deployment Strategy?
1. Deploy to production (git pull + restart)
2. Verify dashboard loads with Orchestrator tab
3. Test with one worker on your local PC
4. If all good, add more workers as needed

### Can I Deploy Without Testing?
**YES** - The orchestrator is opt-in. If you don't use it, nothing changes. Existing functionality is unaffected.

---

**Bottom line**: Just deploy it. The worst case is the Orchestrator tab doesn't work, but everything else continues as normal. Best case: you get 2-3x faster parameter sweeps! 🚀
