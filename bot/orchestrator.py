"""
Orchestrator — Distributed Worker Coordination System

Manages sweep campaigns, job queues, and worker registration for distributed
AutoResearch computation. Workers poll for jobs, compute backtests locally,
and submit results back to the server.
"""

import json
import logging
import sqlite3
import uuid
from datetime import datetime, timedelta, timezone
from itertools import product
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

ALWAYS_ON_SWEEP_NAME = "Smart Research"

DEFAULT_PARAM_SPACE = {
    'h': [30, 110],
    'x': [50, 70],
    'smoothing': [True, False],
}

DEFAULT_TIME_BUDGET_MINUTES = 60
DEFAULT_TARGET_WORKERS = 4


class OrchestratorDB:
    """SQLite-based orchestration database for sweeps, jobs, and workers."""
    
    def __init__(self, db_path: str = 'autoresearch.db'):
        self.db_path = db_path
        self._init_schema()
    
    def _init_schema(self):
        """Create tables if they don't exist, and migrate existing schemas."""
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()
        
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS sweeps (
                id TEXT PRIMARY KEY,
                name TEXT NOT NULL,
                status TEXT NOT NULL,
                params TEXT NOT NULL,
                batch_size INTEGER NOT NULL,
                total_configs INTEGER NOT NULL,
                completed_configs INTEGER NOT NULL DEFAULT 0,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                perpetual INTEGER NOT NULL DEFAULT 0
            )
        """)
        
        # Migrate existing DB: add perpetual column if missing
        try:
            cursor.execute("ALTER TABLE sweeps ADD COLUMN perpetual INTEGER NOT NULL DEFAULT 0")
            conn.commit()
        except sqlite3.OperationalError:
            pass  # Column already exists
        
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS jobs (
                id TEXT PRIMARY KEY,
                sweep_id TEXT NOT NULL,
                status TEXT NOT NULL,
                worker_id TEXT,
                configs TEXT NOT NULL,
                results TEXT,
                claimed_at TEXT,
                completed_at TEXT,
                FOREIGN KEY (sweep_id) REFERENCES sweeps(id) ON DELETE CASCADE
            )
        """)
        
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS workers (
                id TEXT PRIMARY KEY,
                name TEXT NOT NULL,
                hostname TEXT NOT NULL,
                last_seen TEXT NOT NULL,
                status TEXT NOT NULL,
                current_job_id TEXT,
                machine_info TEXT
            )
        """)
        
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_jobs_sweep ON jobs(sweep_id)")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_jobs_status ON jobs(status)")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_workers_status ON workers(status)")
        
        conn.commit()
        conn.close()
    
    def _get_conn(self):
        """Get a database connection with row factory."""
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        return conn
    
    # -------------------------------------------------------------------------
    # Sweep Operations
    # -------------------------------------------------------------------------
    
    def create_sweep(self, name: str, params: Dict[str, Any], num_workers: int = 1) -> Dict[str, Any]:
        """
        Create a new smart-mode sweep campaign.
        
        Args:
            name: Human-readable sweep name
            params: Smart mode parameters (time_budget_minutes, param_space)
            num_workers: Number of parallel workers to allocate
        
        Returns:
            Created sweep dict with id
        """
        sweep_id = str(uuid.uuid4())
        now = datetime.now(timezone.utc).isoformat()
        
        # Smart mode: create one job per worker
        # Each worker runs independent smart search with different seed
        conn = self._get_conn()
        cursor = conn.cursor()
        
        cursor.execute("""
            INSERT INTO sweeps (id, name, status, params, batch_size, total_configs, 
                                completed_configs, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (sweep_id, name, 'pending', json.dumps(params), 1, 
              num_workers, 0, now, now))
        
        # Create one job per worker (smart mode jobs)
        for i in range(num_workers):
            job_id = str(uuid.uuid4())
            job_config = {
                'mode': 'smart',
                'worker_index': i,
                'time_budget': params.get('time_budget_minutes', 60) * 60,
                'param_space': params.get('param_space', {
                    'h': (30, 110),
                    'x': (50, 70),
                    'smoothing': [True, False]
                })
            }
            cursor.execute("""
                INSERT INTO jobs (id, sweep_id, status, configs)
                VALUES (?, ?, ?, ?)
            """, (job_id, sweep_id, 'pending', json.dumps([job_config])))
        
        conn.commit()
        conn.close()
        
        return {
            'id': sweep_id,
            'name': name,
            'status': 'pending',
            'params': params,
            'batch_size': 1,
            'total_configs': num_workers,
            'completed_configs': 0,
            'created_at': now,
            'updated_at': now,
        }
    
    def get_sweep(self, sweep_id: str) -> Optional[Dict[str, Any]]:
        """Get sweep by ID with job statistics."""
        conn = self._get_conn()
        cursor = conn.cursor()
        
        cursor.execute("SELECT * FROM sweeps WHERE id = ?", (sweep_id,))
        row = cursor.fetchone()
        
        if not row:
            conn.close()
            return None
        
        cursor.execute("""
            SELECT status, COUNT(*) as count
            FROM jobs
            WHERE sweep_id = ?
            GROUP BY status
        """, (sweep_id,))
        
        job_stats = {r['status']: r['count'] for r in cursor.fetchall()}
        
        conn.close()
        
        sweep = dict(row)
        sweep['params'] = json.loads(sweep['params'])
        sweep['job_stats'] = job_stats
        
        return sweep
    
    def list_sweeps(self) -> List[Dict[str, Any]]:
        """List all sweeps with summary info."""
        conn = self._get_conn()
        cursor = conn.cursor()
        
        cursor.execute("SELECT * FROM sweeps ORDER BY created_at DESC")
        rows = cursor.fetchall()
        
        sweeps = []
        for row in rows:
            sweep = dict(row)
            sweep['params'] = json.loads(sweep['params'])
            
            cursor.execute("""
                SELECT status, COUNT(*) as count
                FROM jobs
                WHERE sweep_id = ?
                GROUP BY status
            """, (sweep['id'],))
            
            sweep['job_stats'] = {r['status']: r['count'] for r in cursor.fetchall()}
            sweeps.append(sweep)
        
        conn.close()
        return sweeps
    
    def update_sweep_status(self, sweep_id: str, status: str):
        """Update sweep status (pause/resume/cancel/complete)."""
        conn = self._get_conn()
        cursor = conn.cursor()
        
        now = datetime.now(timezone.utc).isoformat()
        cursor.execute("""
            UPDATE sweeps
            SET status = ?, updated_at = ?
            WHERE id = ?
        """, (status, now, sweep_id))
        
        conn.commit()
        conn.close()
    
    def delete_sweep(self, sweep_id: str):
        """Delete sweep and all its jobs (CASCADE)."""
        conn = self._get_conn()
        cursor = conn.cursor()
        
        cursor.execute("DELETE FROM sweeps WHERE id = ?", (sweep_id,))
        
        conn.commit()
        conn.close()
    
    # -------------------------------------------------------------------------
    # Always-On Perpetual Sweep
    # -------------------------------------------------------------------------

    def ensure_always_on_sweep(
        self,
        time_budget_minutes: int = DEFAULT_TIME_BUDGET_MINUTES,
        target_workers: int = DEFAULT_TARGET_WORKERS,
    ) -> Dict[str, Any]:
        """
        Ensure there is always one running perpetual sweep with enough pending
        jobs for all target workers.  Creates a new sweep when none exists, or
        tops-up pending jobs when the existing sweep has run dry.

        Returns the sweep dict.
        """
        conn = self._get_conn()
        cursor = conn.cursor()

        # Find the most-recent running perpetual sweep
        cursor.execute("""
            SELECT id, name, params
            FROM sweeps
            WHERE perpetual = 1 AND status = 'running'
            ORDER BY created_at DESC
            LIMIT 1
        """)
        row = cursor.fetchone()

        if row:
            sweep_id = row['id']
            params = json.loads(row['params'])

            # Update params only when explicitly overriding the default (i.e. a
            # user-driven PATCH), not on every startup call which uses the default.
            if time_budget_minutes != DEFAULT_TIME_BUDGET_MINUTES:
                new_params = dict(params)
                new_params['time_budget_minutes'] = time_budget_minutes
                if new_params != params:
                    cursor.execute(
                        "UPDATE sweeps SET params = ? WHERE id = ?",
                        (json.dumps(new_params), sweep_id),
                    )
                    params = new_params
                    logger.info(
                        f"[ORCHESTRATOR] Updated always-on sweep params: {new_params}"
                    )

            # How many pending jobs does it already have?
            cursor.execute(
                "SELECT COUNT(*) as cnt FROM jobs WHERE sweep_id = ? AND status = 'pending'",
                (sweep_id,),
            )
            pending = cursor.fetchone()['cnt']

            need = max(0, target_workers - pending)
            if need > 0:
                self._add_worker_jobs(cursor, sweep_id, params, need, start_index=pending)
                logger.info(
                    f"[ORCHESTRATOR] Added {need} jobs to always-on sweep {sweep_id}"
                )
            conn.commit()
            conn.close()

            return self.get_sweep(sweep_id)

        conn.close()

        # No running perpetual sweep — create one
        params = {
            'time_budget_minutes': time_budget_minutes,
            'param_space': DEFAULT_PARAM_SPACE,
        }
        sweep = self.create_perpetual_sweep(ALWAYS_ON_SWEEP_NAME, params, target_workers)
        logger.info(f"[ORCHESTRATOR] Created always-on sweep {sweep['id']}")
        return sweep

    def create_perpetual_sweep(
        self,
        name: str,
        params: Dict[str, Any],
        num_workers: int,
    ) -> Dict[str, Any]:
        """Create a perpetual (always-on) smart sweep and set it to running."""
        sweep_id = str(uuid.uuid4())
        now = datetime.now(timezone.utc).isoformat()

        conn = self._get_conn()
        cursor = conn.cursor()

        cursor.execute("""
            INSERT INTO sweeps
                (id, name, status, params, batch_size, total_configs,
                 completed_configs, created_at, updated_at, perpetual)
            VALUES (?, ?, 'running', ?, 1, ?, 0, ?, ?, 1)
        """, (sweep_id, name, json.dumps(params), num_workers, now, now))

        self._add_worker_jobs(cursor, sweep_id, params, num_workers, start_index=0)

        conn.commit()
        conn.close()

        return self.get_sweep(sweep_id)

    def _add_worker_jobs(
        self,
        cursor,
        sweep_id: str,
        params: Dict[str, Any],
        count: int,
        start_index: int = 0,
    ):
        """Insert `count` smart-mode job rows for a sweep (one job per worker)."""
        for i in range(count):
            job_id = str(uuid.uuid4())
            job_config = {
                'mode': 'smart',
                'worker_index': start_index + i,
                'time_budget': params.get('time_budget_minutes', DEFAULT_TIME_BUDGET_MINUTES) * 60,
                'param_space': params.get('param_space', DEFAULT_PARAM_SPACE),
            }
            cursor.execute("""
                INSERT INTO jobs (id, sweep_id, status, configs)
                VALUES (?, ?, 'pending', ?)
            """, (job_id, sweep_id, json.dumps([job_config])))
    
    def increment_sweep_progress(self, sweep_id: str, completed_count: int):
        """Increment completed_configs counter for a sweep."""
        conn = self._get_conn()
        cursor = conn.cursor()
        
        now = datetime.now(timezone.utc).isoformat()
        cursor.execute("""
            UPDATE sweeps
            SET completed_configs = completed_configs + ?,
                updated_at = ?
            WHERE id = ?
        """, (completed_count, now, sweep_id))
        
        cursor.execute("""
            SELECT completed_configs, total_configs
            FROM sweeps
            WHERE id = ?
        """, (sweep_id,))
        
        row = cursor.fetchone()
        if row and row['completed_configs'] >= row['total_configs']:
            cursor.execute("""
                UPDATE sweeps
                SET status = 'completed'
                WHERE id = ?
            """, (sweep_id,))
        
        conn.commit()
        conn.close()
    
    # -------------------------------------------------------------------------
    # Job Operations
    # -------------------------------------------------------------------------
    
    def claim_job(self, worker_id: str) -> Optional[Dict[str, Any]]:
        """
        Atomically claim the next pending job for a worker.
        Also resets stale claimed jobs (>10 min without heartbeat).
        If no pending jobs exist but a perpetual sweep is running, a new job
        is automatically created for this worker before claiming.

        Returns:
            Job dict with configs, or None if no jobs available
        """
        conn = self._get_conn()
        cursor = conn.cursor()
        
        self._reset_stale_jobs(cursor)
        
        cursor.execute("""
            SELECT j.*, s.status as sweep_status
            FROM jobs j
            JOIN sweeps s ON j.sweep_id = s.id
            WHERE j.status = 'pending' AND s.status = 'running'
            ORDER BY j.rowid
            LIMIT 1
        """)
        
        row = cursor.fetchone()
        
        if not row:
            # No pending jobs — try to add a new job from the perpetual sweep
            cursor.execute("""
                SELECT id, params
                FROM sweeps
                WHERE perpetual = 1 AND status = 'running'
                ORDER BY created_at DESC
                LIMIT 1
            """)
            perp = cursor.fetchone()
            if perp:
                params = json.loads(perp['params'])
                self._add_worker_jobs(cursor, perp['id'], params, 1, start_index=0)
                conn.commit()
                logger.info(
                    f"[ORCHESTRATOR] Auto-created job in perpetual sweep {perp['id']} for worker {worker_id}"
                )
                # Now re-query the newly created pending job
                cursor.execute("""
                    SELECT j.*, s.status as sweep_status
                    FROM jobs j
                    JOIN sweeps s ON j.sweep_id = s.id
                    WHERE j.status = 'pending' AND s.status = 'running'
                    ORDER BY j.rowid
                    LIMIT 1
                """)
                row = cursor.fetchone()

        if not row:
            conn.close()
            return None
        
        job_id = row['id']
        now = datetime.now(timezone.utc).isoformat()
        
        cursor.execute("""
            UPDATE jobs
            SET status = 'claimed', worker_id = ?, claimed_at = ?
            WHERE id = ?
        """, (worker_id, now, job_id))
        
        cursor.execute("""
            UPDATE workers
            SET status = 'busy', current_job_id = ?, last_seen = ?
            WHERE id = ?
        """, (job_id, now, worker_id))
        
        conn.commit()
        conn.close()
        
        job = dict(row)
        job['configs'] = json.loads(job['configs'])
        
        return job
    
    def submit_job(self, job_id: str, results: List[Dict[str, Any]]):
        """
        Submit completed job results.
        
        Args:
            job_id: Job UUID
            results: List of result dicts (one per config)
        """
        conn = self._get_conn()
        cursor = conn.cursor()
        
        now = datetime.now(timezone.utc).isoformat()
        
        cursor.execute("""
            UPDATE jobs
            SET status = 'completed', results = ?, completed_at = ?
            WHERE id = ?
        """, (json.dumps(results), now, job_id))
        
        cursor.execute("SELECT sweep_id, worker_id FROM jobs WHERE id = ?", (job_id,))
        row = cursor.fetchone()
        
        if row:
            sweep_id = row['sweep_id']
            worker_id = row['worker_id']
            
            # Update sweep progress in same transaction
            cursor.execute("""
                UPDATE sweeps
                SET completed_configs = completed_configs + ?,
                    updated_at = ?
                WHERE id = ?
            """, (len(results), now, sweep_id))
            
            # Check if sweep is complete (perpetual sweeps never auto-complete)
            cursor.execute("""
                SELECT completed_configs, total_configs, perpetual
                FROM sweeps
                WHERE id = ?
            """, (sweep_id,))
            
            sweep_row = cursor.fetchone()
            if (sweep_row
                    and not sweep_row['perpetual']
                    and sweep_row['completed_configs'] >= sweep_row['total_configs']):
                cursor.execute("""
                    UPDATE sweeps
                    SET status = 'completed'
                    WHERE id = ?
                """, (sweep_id,))
            
            cursor.execute("""
                UPDATE workers
                SET status = 'idle', current_job_id = NULL, last_seen = ?
                WHERE id = ?
            """, (now, worker_id))
        
        conn.commit()
        conn.close()
    
    def fail_job(self, job_id: str, error: str):
        """Mark job as failed and return to pending queue."""
        conn = self._get_conn()
        cursor = conn.cursor()
        
        now = datetime.now(timezone.utc).isoformat()
        
        cursor.execute("""
            UPDATE jobs
            SET status = 'pending', worker_id = NULL, claimed_at = NULL
            WHERE id = ?
        """, (job_id,))
        
        cursor.execute("SELECT worker_id FROM jobs WHERE id = ?", (job_id,))
        row = cursor.fetchone()
        
        if row and row['worker_id']:
            cursor.execute("""
                UPDATE workers
                SET status = 'idle', current_job_id = NULL, last_seen = ?
                WHERE id = ?
            """, (now, row['worker_id']))
        
        conn.commit()
        conn.close()
    
    def _reset_stale_jobs(self, cursor):
        """Reset jobs claimed >10 min ago with no recent heartbeat."""
        stale_threshold = (datetime.now(timezone.utc) - timedelta(minutes=10)).isoformat()
        
        cursor.execute("""
            SELECT j.id, j.worker_id
            FROM jobs j
            LEFT JOIN workers w ON j.worker_id = w.id
            WHERE j.status = 'claimed'
              AND j.claimed_at < ?
              AND (w.last_seen IS NULL OR w.last_seen < ?)
        """, (stale_threshold, stale_threshold))
        
        stale_jobs = cursor.fetchall()
        
        for job in stale_jobs:
            cursor.execute("""
                UPDATE jobs
                SET status = 'pending', worker_id = NULL, claimed_at = NULL
                WHERE id = ?
            """, (job['id'],))
            
            if job['worker_id']:
                cursor.execute("""
                    UPDATE workers
                    SET status = 'offline', current_job_id = NULL
                    WHERE id = ?
                """, (job['worker_id'],))
    
    # -------------------------------------------------------------------------
    # Worker Operations
    # -------------------------------------------------------------------------
    
    def register_worker(self, name: str, hostname: str, machine_info: Dict[str, Any]) -> str:
        """
        Register a new worker or update existing one.
        
        Returns:
            worker_id (UUID)
        """
        conn = self._get_conn()
        cursor = conn.cursor()
        
        cursor.execute("""
            SELECT id FROM workers WHERE name = ? AND hostname = ?
        """, (name, hostname))
        
        row = cursor.fetchone()
        now = datetime.now(timezone.utc).isoformat()
        
        if row:
            worker_id = row['id']
            cursor.execute("""
                UPDATE workers
                SET last_seen = ?, status = 'idle', machine_info = ?
                WHERE id = ?
            """, (now, json.dumps(machine_info), worker_id))
        else:
            worker_id = str(uuid.uuid4())
            cursor.execute("""
                INSERT INTO workers (id, name, hostname, last_seen, status, machine_info)
                VALUES (?, ?, ?, ?, ?, ?)
            """, (worker_id, name, hostname, now, 'idle', json.dumps(machine_info)))
        
        conn.commit()
        conn.close()
        
        return worker_id
    
    def heartbeat(self, worker_id: str):
        """Update worker last_seen timestamp."""
        conn = self._get_conn()
        cursor = conn.cursor()
        
        now = datetime.now(timezone.utc).isoformat()
        cursor.execute("""
            UPDATE workers
            SET last_seen = ?
            WHERE id = ?
        """, (now, worker_id))
        
        conn.commit()
        conn.close()
    
    def list_workers(self) -> List[Dict[str, Any]]:
        """List all workers with status."""
        conn = self._get_conn()
        cursor = conn.cursor()
        
        cursor.execute("SELECT * FROM workers ORDER BY last_seen DESC")
        rows = cursor.fetchall()
        
        workers = []
        for row in rows:
            worker = dict(row)
            if worker['machine_info']:
                worker['machine_info'] = json.loads(worker['machine_info'])
            workers.append(worker)
        
        conn.close()
        return workers
    
    def get_worker(self, worker_id: str) -> Optional[Dict[str, Any]]:
        """Get worker by ID."""
        conn = self._get_conn()
        cursor = conn.cursor()
        
        cursor.execute("SELECT * FROM workers WHERE id = ?", (worker_id,))
        row = cursor.fetchone()
        
        conn.close()
        
        if not row:
            return None
        
        worker = dict(row)
        if worker['machine_info']:
            worker['machine_info'] = json.loads(worker['machine_info'])
        
        return worker


# -----------------------------------------------------------------------------
# Config Generation (adapted from autoresearch.py:build_grid)
# -----------------------------------------------------------------------------

def generate_grid_configs(params: Dict[str, Any]) -> List[Dict[str, Any]]:
    """
    Generate all config combinations from parameter ranges.
    
    Args:
        params: Dict with keys like h_min, h_max, h_step, x_min, x_max, x_step,
                smoothing ('on'/'off'/'both'), r_values (list), etc.
    
    Returns:
        List of config dicts (lookback_window, regression_level, etc.)
    """
    defaults = {
        'relative_weight': 10.0,
        'lag': 1,
        'atr_period': 20,
        'atr_multiplier': 6.0,
        'volatility_min': 5,
        'volatility_max': 10,
        're_entry_delay': 1,
    }
    
    h_min = params.get('h_min', 30)
    h_max = params.get('h_max', 110)
    h_step = params.get('h_step', 10)
    
    x_min = params.get('x_min', 50)
    x_max = params.get('x_max', 70)
    x_step = params.get('x_step', 5)
    
    lookback_range = list(range(h_min, h_max + 1, h_step))
    if lookback_range and lookback_range[-1] != h_max and h_max not in lookback_range:
        lookback_range.append(h_max)
    
    regression_range = list(range(x_min, x_max + 1, x_step))
    if regression_range and regression_range[-1] != x_max and x_max not in regression_range:
        regression_range.append(x_max)
    
    smoothing = params.get('smoothing', 'on')
    if smoothing == 'both':
        smoothing_variants = [True, False]
    elif smoothing == 'on':
        smoothing_variants = [True]
    else:
        smoothing_variants = [False]
    
    extra_params = [
        ('relative_weight', params.get('r_values'), defaults['relative_weight']),
        ('lag', params.get('lag_values'), defaults['lag']),
        ('atr_period', params.get('atr_period_values'), defaults['atr_period']),
        ('atr_multiplier', params.get('atr_mult_values'), defaults['atr_multiplier']),
        ('volatility_min', params.get('vol_min_values'), defaults['volatility_min']),
        ('volatility_max', params.get('vol_max_values'), defaults['volatility_max']),
        ('re_entry_delay', params.get('reentry_delay_values'), defaults['re_entry_delay']),
    ]
    
    iterated = {}
    fixed = {}
    
    if len(lookback_range) > 1:
        iterated['lookback_window'] = lookback_range
    else:
        fixed['lookback_window'] = lookback_range[0] if lookback_range else 88
    
    if len(regression_range) > 1:
        iterated['regression_level'] = regression_range
    else:
        fixed['regression_level'] = regression_range[0] if regression_range else 67
    
    if len(smoothing_variants) > 1:
        iterated['use_kernel_smoothing'] = smoothing_variants
    else:
        fixed['use_kernel_smoothing'] = smoothing_variants[0]
    
    for param_name, cli_values, default in extra_params:
        if cli_values is not None and len(cli_values) > 1:
            iterated[param_name] = cli_values
        elif cli_values is not None and len(cli_values) == 1:
            fixed[param_name] = cli_values[0]
        else:
            fixed[param_name] = default
    
    if not iterated:
        combo = dict(fixed)
        for key, default_val in [('lookback_window', 88), 
                                  ('regression_level', 67), 
                                  ('use_kernel_smoothing', True)]:
            if key not in combo:
                combo[key] = default_val
        return [combo]
    
    param_names = list(iterated.keys())
    param_values = [iterated[k] for k in param_names]
    
    combos = []
    for vals in product(*param_values):
        combo = dict(fixed)
        for name, val in zip(param_names, vals):
            combo[name] = val
        
        for key, default_val in [('lookback_window', lookback_range[0] if lookback_range else 88),
                                  ('regression_level', regression_range[0] if regression_range else 67),
                                  ('use_kernel_smoothing', smoothing_variants[0])]:
            if key not in combo:
                combo[key] = default_val
        
        if combo.get('volatility_min', 0) >= combo.get('volatility_max', 999):
            continue
        
        combos.append(combo)
    
    return combos


def split_into_batches(configs: List[Dict[str, Any]], batch_size: int) -> List[List[Dict[str, Any]]]:
    """Split configs into batches of specified size."""
    batches = []
    for i in range(0, len(configs), batch_size):
        batches.append(configs[i:i + batch_size])
    return batches
