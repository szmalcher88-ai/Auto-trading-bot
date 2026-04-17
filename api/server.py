"""
FastAPI backend for Trading Bot Dashboard.
"""

import csv
import io
import json
import logging
import math
import os
import subprocess
import threading
from typing import Any, Dict

from fastapi import FastAPI, Header, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from pydantic import BaseModel

# Lazy import for Prisma/SQLite — bot works without it
try:
    from bot.db import AutoResearchDB, run_async
    HAS_DB = True
except ImportError:
    HAS_DB = False

logger = logging.getLogger(__name__)

app = FastAPI(title="Trading Bot Dashboard")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["GET", "POST", "PATCH", "DELETE", "OPTIONS"],
    allow_headers=["*"],
)

# Serialises all autoresearch file writes (alltime CSV, results CSV, meta JSON).
# FastAPI+uvicorn runs as a single process; a threading.Lock is sufficient to
# prevent the read-modify-write race when two uploads arrive simultaneously.
_autoresearch_lock = threading.Lock()

# Injected from main.py
shared_state = None
kill_switch_ref = None

# Default config values (for reset)
DEFAULT_CONFIG = {
    'lookback_window': 100,
    'relative_weight': 10.0,
    'regression_level': 69,
    'lag': 1,
    'use_kernel_smoothing': True,
    'sl_type': 'atr',
    'sl_percent': 2.7,
    'atr_period': 20,
    'atr_multiplier': 6.0,
    'use_dynamic_sl': True,
    'trailing_sl_mode': 'pine',
    'volatility_min': 5,
    'volatility_max': 10,
    'enable_re_entry': True,
    're_entry_delay': 1,
    'require_color_confirmation': False,
    'kill_switch_consecutive_losses': 5,
    'kill_switch_equity_drop_percent': 10.0,
    'kill_switch_pause_hours': 24,
    'leverage': 1,
    'position_size_pct': 50.0,
    'symbol': 'ETHUSDT',
    'timeframe': '1h',
}

PARAM_RANGES = {
    'lookback_window': (10, 500),
    'relative_weight': (0.1, 50.0),
    'regression_level': (5, 200),
    'lag': (1, 5),
    'sl_percent': (0.5, 10.0),
    'atr_period': (5, 50),
    'atr_multiplier': (1.0, 15.0),
    'volatility_min': (1, 20),
    'volatility_max': (2, 30),
    're_entry_delay': (0, 10),
    'kill_switch_consecutive_losses': (2, 20),
    'kill_switch_equity_drop_percent': (5.0, 30.0),
    'kill_switch_pause_hours': (1, 72),
    'leverage': (1, 10),
    'position_size_pct': (5.0, 100.0),
}


def create_app(state, kill_switch=None):
    global shared_state, kill_switch_ref
    shared_state = state
    kill_switch_ref = kill_switch
    return app


# ------------------------------------------------------------------
# Dashboard
# ------------------------------------------------------------------

@app.get("/")
def serve_dashboard():
    path = os.path.join(os.path.dirname(os.path.dirname(__file__)), 'dashboard', 'index.html')
    if not os.path.exists(path):
        return {"error": "dashboard/index.html not found"}
    return FileResponse(path, media_type='text/html')


# ------------------------------------------------------------------
# API Endpoints
# ------------------------------------------------------------------

@app.get("/api/status")
def get_status():
    if shared_state is None:
        raise HTTPException(503, "Bot not initialized")
    status = shared_state.get_status()
    # Live PnL from current Binance price
    if status['position'] in ('long', 'short') and hasattr(shared_state, 'exchange_client'):
        try:
            ticker = shared_state.exchange_client.futures_symbol_ticker(symbol='ETHUSDT')
            current_price = float(ticker['price'])
            if status['position'] == 'long':
                status['unrealized_pnl'] = round((current_price - status['entry_price']) / status['entry_price'] * 100, 2)
            else:
                status['unrealized_pnl'] = round((status['entry_price'] - current_price) / status['entry_price'] * 100, 2)
            status['current_price'] = current_price
        except Exception:
            pass
    if kill_switch_ref is not None:
        ks = kill_switch_ref.get_status()
        status['kill_switch_paused'] = ks['paused']
        status['kill_switch_pause_until'] = ks['pause_until']
        status['kill_switch_consecutive_losses'] = ks['consecutive_losses']
    return status


@app.get("/api/signals")
def get_signals():
    if shared_state is None:
        raise HTTPException(503, "Bot not initialized")
    status = shared_state.get_status()
    return {
        'last_signal': status.get('last_signal'),
        'last_action': status.get('last_action'),
        'last_reason': status.get('last_reason'),
        'last_loop_time': status.get('last_loop'),
        'signal_seq': status.get('signal_seq'),
        'action_seq': status.get('action_seq'),
    }


@app.get("/api/signal-history")
def get_signal_history():
    if shared_state is None:
        raise HTTPException(503, "Bot not initialized")
    history = shared_state.get_signal_history()
    return {
        'history': history,
        'count': len(history),
    }


@app.get("/api/settings")
def get_settings():
    if shared_state is None:
        raise HTTPException(503, "Bot not initialized")
    cfg = shared_state.get_config_snapshot()
    return {
        'kernel': {
            'lookback_window': cfg.get('lookback_window', 110),
            'relative_weight': cfg.get('relative_weight', 10.0),
            'regression_level': cfg.get('regression_level', 64),
            'lag': cfg.get('lag', 1),
            'use_kernel_smoothing': cfg.get('use_kernel_smoothing', True),
        },
        'stop_loss': {
            'sl_type': cfg.get('sl_type', 'atr'),
            'sl_percent': cfg.get('sl_percent', 2.7),
            'atr_period': cfg.get('atr_period', 20),
            'atr_multiplier': cfg.get('atr_multiplier', 6.0),
            'use_dynamic_sl': cfg.get('use_dynamic_sl', True),
            'trailing_sl_mode': cfg.get('trailing_sl_mode', 'pine'),
        },
        'volatility_filter': {
            'enabled': True,
            'min': cfg.get('volatility_min', 5),
            'max': cfg.get('volatility_max', 10),
        },
        're_entry': {
            'enabled': cfg.get('enable_re_entry', True),
            'delay': cfg.get('re_entry_delay', 1),
            'require_color_confirmation': cfg.get('require_color_confirmation', False),
        },
        'kill_switch': {
            'consecutive_losses': cfg.get('kill_switch_consecutive_losses', 5),
            'equity_drop_percent': cfg.get('kill_switch_equity_drop_percent', 10.0),
            'pause_hours': cfg.get('kill_switch_pause_hours', 24),
        },
        'position': {
            'leverage': cfg.get('leverage', 1),
            'position_size_pct': cfg.get('position_size_pct', 50.0),
            'symbol': cfg.get('symbol', 'ETHUSDT'),
            'timeframe': cfg.get('timeframe', '1h'),
        },
    }


@app.post("/api/settings")
def update_settings(body: Dict[str, Any]):
    if shared_state is None:
        raise HTTPException(503, "Bot not initialized")

    errors = []
    changes = {}

    for key, value in body.items():
        if key in ('symbol', 'timeframe'):
            continue  # readonly

        if key == 'sl_type' and value not in ('atr', 'percent'):
            errors.append(f"sl_type must be 'atr' or 'percent', got '{value}'")
            continue

        if key == 'trailing_sl_mode' and value not in ('pine', 'execution'):
            errors.append(f"trailing_sl_mode must be 'pine' or 'execution', got '{value}'")
            continue

        if key in PARAM_RANGES:
            lo, hi = PARAM_RANGES[key]
            if not (lo <= value <= hi):
                errors.append(f"{key}: {value} out of range ({lo}-{hi})")
                continue

        if key in DEFAULT_CONFIG:
            changes[key] = value
        else:
            errors.append(f"Unknown parameter: {key}")

    if errors:
        raise HTTPException(400, detail='; '.join(errors))

    if changes:
        # Log changes
        old_cfg = shared_state.get_config_snapshot()
        for k, v in changes.items():
            old_val = old_cfg.get(k, '?')
            if old_val != v:
                logger.info(f"[CONFIG] Parameter {k} changed from {old_val} to {v}")

        shared_state.update_config(changes)

    return {'status': 'ok', 'changes': changes}


@app.post("/api/settings/reset")
def reset_settings():
    if shared_state is None:
        raise HTTPException(503, "Bot not initialized")
    shared_state.update_config(DEFAULT_CONFIG.copy())
    logger.info("[CONFIG] Settings reset to defaults")
    return {'status': 'ok', 'config': DEFAULT_CONFIG}


@app.post("/api/emergency/close")
def emergency_close():
    if shared_state is None:
        raise HTTPException(503, "Bot not initialized")
    shared_state.emergency_close.set()
    logger.warning("[EMERGENCY] Close position requested from dashboard")
    return {'status': 'ok', 'message': 'Emergency close signal sent'}


@app.post("/api/emergency/pause")
def emergency_pause():
    if shared_state is None:
        raise HTTPException(503, "Bot not initialized")
    shared_state.trading_paused = True
    logger.warning("[EMERGENCY] Trading PAUSED from dashboard")
    return {'status': 'ok', 'trading_paused': True}


@app.post("/api/emergency/resume")
def emergency_resume():
    if shared_state is None:
        raise HTTPException(503, "Bot not initialized")
    shared_state.trading_paused = False
    logger.warning("[EMERGENCY] Trading RESUMED from dashboard")
    return {'status': 'ok', 'trading_paused': False}


@app.get("/api/trades")
def get_trades():
    trades = _read_csv('trade_log.csv', 20)
    return {'trades': trades}


@app.get("/api/killswitch")
def get_killswitch():
    if kill_switch_ref is None:
        return {'error': 'kill switch not initialized'}
    ks = kill_switch_ref.get_status()
    ks['manual_pause'] = shared_state.trading_paused if shared_state else False
    return ks


@app.post("/api/killswitch/reset")
def reset_killswitch():
    if kill_switch_ref is None:
        raise HTTPException(503, "Kill switch not initialized")
    kill_switch_ref.trading_paused = False
    kill_switch_ref.pause_until = None
    kill_switch_ref.consecutive_losses = 0
    logger.warning("[KILL] Kill switch RESET from dashboard")
    return {'status': 'ok', 'paused': False, 'consecutive_losses': 0}


@app.get("/api/logs")
def get_logs():
    try:
        if not os.path.exists('trading_bot.log'):
            return {'lines': []}
        with open('trading_bot.log', 'r', encoding='utf-8', errors='replace') as f:
            lines = f.readlines()
        return {'lines': [l.rstrip() for l in lines[-100:]]}
    except Exception as e:
        return {'lines': [f"Error reading log: {e}"]}


@app.get("/api/equity")
def get_equity():
    trades = _read_csv('trade_log.csv', 1000)
    equity = []
    for t in trades:
        bal = t.get('balance_after', '')
        if bal:
            try:
                equity.append({
                    'timestamp': t.get('timestamp', ''),
                    'balance': float(bal),
                })
            except ValueError:
                pass
    return {'equity': equity}


# ------------------------------------------------------------------
# AutoResearch
# ------------------------------------------------------------------

class AutoResearchParams(BaseModel):
    h_min: int = 30
    h_max: int = 110
    h_step: int = 10
    x_min: int = 25
    x_max: int = 64
    x_step: int = 5
    smoothing: str = 'on'
    assets: list = ['ETHUSDT', 'BTCUSDT', 'SOLUSDT']
    r_values: list = None
    lag_values: list = None
    atr_period_values: list = None
    atr_mult_values: list = None
    vol_min_values: list = None
    vol_max_values: list = None
    reentry_delay_values: list = None


@app.post("/api/autoresearch/run")
def run_autoresearch(params: AutoResearchParams):
    """Trigger autoresearch from dashboard with custom params."""
    cmd = ['python', 'autoresearch.py']
    cmd.extend(['--h-min', str(params.h_min), '--h-max', str(params.h_max), '--h-step', str(params.h_step)])
    cmd.extend(['--x-min', str(params.x_min), '--x-max', str(params.x_max), '--x-step', str(params.x_step)])
    cmd.extend(['--smoothing', params.smoothing])
    cmd.extend(['--assets'] + params.assets)

    if params.r_values:
        cmd.extend(['--r-values'] + [str(v) for v in params.r_values])
    if params.lag_values:
        cmd.extend(['--lag-values'] + [str(v) for v in params.lag_values])
    if params.atr_period_values:
        cmd.extend(['--atr-period-values'] + [str(v) for v in params.atr_period_values])
    if params.atr_mult_values:
        cmd.extend(['--atr-mult-values'] + [str(v) for v in params.atr_mult_values])
    if params.vol_min_values:
        cmd.extend(['--vol-min-values'] + [str(v) for v in params.vol_min_values])
    if params.vol_max_values:
        cmd.extend(['--vol-max-values'] + [str(v) for v in params.vol_max_values])
    if params.reentry_delay_values:
        cmd.extend(['--reentry-delay-values'] + [str(v) for v in params.reentry_delay_values])

    h_combos = (params.h_max - params.h_min) // params.h_step + 1
    x_combos = (params.x_max - params.x_min) // params.x_step + 1
    smooth_combos = 2 if params.smoothing == 'both' else 1
    extra = 1
    for vals in [params.r_values, params.lag_values, params.atr_period_values,
                 params.atr_mult_values, params.vol_min_values, params.vol_max_values,
                 params.reentry_delay_values]:
        if vals:
            extra *= len(vals)
    total_combos = h_combos * x_combos * smooth_combos * extra
    est_minutes = int((total_combos * len(params.assets) * 2.9) / 60)

    subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    logger.info(f"[AUTORESEARCH] Started with {total_combos} combinations across {len(params.assets)} assets")

    return {
        'status': 'started',
        'combinations': total_combos,
        'estimated_minutes': est_minutes
    }


@app.get("/api/autoresearch")
def get_autoresearch():
    empty_response = {
        'last_run': None,
        'current_config': None,
        'top_20': [],
        'total_results': 0,
        'valid_count': 0,
        'heatmap': {'h_values': [], 'x_values': [], 'scores_smooth_on': [], 'scores_smooth_off': []},
    }

    # --- Database path (primary) ---
    if HAS_DB:
        try:
            db = AutoResearchDB()
            last_run = run_async(db.get_latest_run())
            if last_run:
                db_rows = run_async(db.get_results_for_latest_run())
                run_async(db.disconnect())
                if db_rows:
                    return _build_autoresearch_response(db_rows, last_run)
            else:
                run_async(db.disconnect())
        except Exception as e:
            logger.warning(f"[AUTORESEARCH] Database read failed, falling back to CSV: {e}")

    # --- CSV fallback ---
    csv_path = 'autoresearch_results.csv'
    meta_path = 'autoresearch_meta.json'

    if not os.path.exists(csv_path):
        empty_response['error'] = 'No autoresearch results found. Run: python autoresearch.py'
        return empty_response

    rows = _read_autoresearch_csv(csv_path)
    if not rows:
        empty_response['error'] = 'No valid results in CSV'
        return empty_response

    last_run = None
    if os.path.exists(meta_path):
        try:
            with open(meta_path, 'r') as f:
                last_run = json.load(f)
        except Exception:
            pass

    return _build_autoresearch_response(rows, last_run)


def _read_autoresearch_csv(csv_path):
    """Parse autoresearch_results.csv into a list of normalised dicts."""
    INT_COLS = {'lookback_window', 'regression_level', 'lag', 'atr_period',
                'volatility_min', 'volatility_max', 're_entry_delay',
                'eth_trades', 'btc_trades', 'sol_trades', 'lookback', 'regression'}
    rows = []
    try:
        with open(csv_path, 'r') as f:
            reader = csv.DictReader(f)
            for r in reader:
                row = {}
                for k, v in r.items():
                    if k in ('smoothing', 'use_kernel_smoothing'):
                        row[k] = v == 'True'
                    else:
                        try:
                            row[k] = float(v)
                            if (k in INT_COLS and math.isfinite(row[k])
                                    and row[k] == int(row[k])):
                                row[k] = int(row[k])
                        except (ValueError, TypeError):
                            row[k] = v
                # Normalise: keep both name variants for frontend compatibility
                if 'lookback_window' in row:
                    row['lookback'] = row['lookback_window']
                elif 'lookback' in row:
                    row['lookback_window'] = row['lookback']
                if 'regression_level' in row:
                    row['regression'] = row['regression_level']
                elif 'regression' in row:
                    row['regression_level'] = row['regression']
                if 'use_kernel_smoothing' in row:
                    row['smoothing'] = row['use_kernel_smoothing']
                elif 'smoothing' in row:
                    row['use_kernel_smoothing'] = row['smoothing']
                rows.append(row)
    except Exception as e:
        logger.error(f"[AUTORESEARCH] Failed to read CSV {csv_path}: {e}")
    return rows


def _build_autoresearch_response(rows, last_run):
    """Build the /api/autoresearch JSON response from a list of result dicts."""
    # Current config from shared state
    current_config = None
    if shared_state:
        cfg = shared_state.get_config_snapshot()
        h = cfg.get('lookback_window', 110)
        x = cfg.get('regression_level', 64)
        smooth = cfg.get('use_kernel_smoothing', True)
        match = next((r for r in rows if r.get('lookback_window') == h
                      and r.get('regression_level') == x
                      and r.get('use_kernel_smoothing') == smooth), None)

        # Also try database lookup if no CSV match
        if match is None and HAS_DB:
            try:
                db = AutoResearchDB()
                db_match = run_async(db.get_results_by_params(h, x, smooth))
                run_async(db.disconnect())
                match = db_match
            except Exception:
                pass

        current_config = {
            'h': h, 'x': x, 'smoothing': smooth,
            'score': match['score'] if match else 0,
            'eth': {'pf': match.get('eth_pf', 0), 'dd': match.get('eth_dd', 0),
                    'profit': match.get('eth_profit', 0)} if match else None,
            'btc': {'pf': match.get('btc_pf', 0), 'dd': match.get('btc_dd', 0),
                    'profit': match.get('btc_profit', 0)} if match else None,
            'sol': {'pf': match.get('sol_pf', 0), 'dd': match.get('sol_dd', 0),
                    'profit': match.get('sol_profit', 0)} if match else None,
        }

    valid = [r for r in rows if r.get('score', 0) > 0]
    ranked = sorted(valid, key=lambda r: r['score'], reverse=True)[:20]
    for i, r in enumerate(ranked, 1):
        r['rank'] = i

    # Heatmap (requires lookback_window and regression_level)
    h_rows = [r for r in rows if 'lookback_window' in r and 'regression_level' in r]
    h_values = sorted(set(int(r['lookback_window']) for r in h_rows))
    x_values = sorted(set(int(r['regression_level']) for r in h_rows))
    score_map = {
        (int(r['lookback_window']), int(r['regression_level']), r.get('use_kernel_smoothing', True)):
        r.get('score', 0)
        for r in h_rows
    }
    scores_on = [[score_map.get((h, x, True), -1) for x in x_values] for h in h_values]
    scores_off = [[score_map.get((h, x, False), -1) for x in x_values] for h in h_values]

    return {
        'last_run': last_run,
        'current_config': current_config,
        'top_20': ranked,
        'total_results': len(rows),
        'valid_count': len(valid),
        'heatmap': {
            'h_values': h_values,
            'x_values': x_values,
            'scores_smooth_on': scores_on,
            'scores_smooth_off': scores_off,
        },
    }


# ------------------------------------------------------------------
# Leaderboard (all-time best)
# ------------------------------------------------------------------

@app.get("/api/leaderboard")
def get_leaderboard():
    # --- Database path (primary) ---
    if HAS_DB:
        try:
            db = AutoResearchDB()
            top = run_async(db.get_all_time_top_results(limit=20))
            run_async(db.disconnect())
            if top:
                for i, r in enumerate(top, 1):
                    r['rank'] = i
                # Count distinct runs from DB for metadata
                return {
                    'total_runs': _count_db_runs(),
                    'unique_configs': len(top),
                    'top_20': top,
                }
        except Exception as e:
            logger.warning(f"[LEADERBOARD] Database read failed, falling back to CSV: {e}")

    # --- CSV fallback ---
    csv_path = 'autoresearch_alltime.csv'
    if not os.path.exists(csv_path):
        return {
            'total_runs': 0, 'unique_configs': 0,
            'top_20': [], 'error': 'No all-time data yet. Run autoresearch.py first.',
        }

    return _leaderboard_from_csv(csv_path)


def _count_db_runs():
    """Return number of completed runs stored in database."""
    if not HAS_DB:
        return 0
    try:
        db = AutoResearchDB()
        run_async(db.connect())
        count = run_async(db.db.autoresearchrun.count(where={'status': 'completed'}))
        run_async(db.disconnect())
        return count
    except Exception:
        return 0


def _leaderboard_from_csv(csv_path):
    """Build leaderboard response from autoresearch_alltime.csv (legacy fallback)."""
    try:
        with open(csv_path, 'r') as f:
            reader = csv.DictReader(f)
            rows = list(reader)
    except Exception as e:
        return {'total_runs': 0, 'unique_configs': 0, 'top_20': [],
                'error': f'Failed to read CSV: {e}'}

    if not rows:
        return {'total_runs': 0, 'unique_configs': 0, 'top_20': []}

    INT_COLS = {'lookback', 'regression', 'lag', 'atr_period', 'vol_min', 'vol_max', 'reentry_delay'}
    best_map = {}
    for r in rows:
        key = (r.get('lookback', ''), r.get('regression', ''),
               r.get('smoothing', ''), r.get('relative_weight', ''),
               r.get('lag', ''), r.get('atr_period', ''),
               r.get('atr_multiplier', ''), r.get('vol_min', ''),
               r.get('vol_max', ''), r.get('reentry_delay', ''))
        try:
            score = float(r.get('score', 0))
        except (ValueError, TypeError):
            score = 0
        if key not in best_map or score > float(best_map[key].get('score', 0)):
            best_map[key] = dict(r)
        existing_date = best_map[key].get('first_found', r.get('run_date', ''))
        current_date = r.get('run_date', '')
        if current_date < existing_date:
            best_map[key]['first_found'] = current_date
        elif 'first_found' not in best_map[key]:
            best_map[key]['first_found'] = current_date

    deduped = []
    for row in best_map.values():
        parsed = {}
        for k, v in row.items():
            if k == 'smoothing':
                parsed[k] = v == 'True'
            elif k in ('run_date', 'first_found'):
                parsed[k] = v
            else:
                try:
                    parsed[k] = float(v)
                    if (k in INT_COLS and math.isfinite(parsed[k])
                            and parsed[k] == int(parsed[k])):
                        parsed[k] = int(parsed[k])
                except (ValueError, TypeError):
                    parsed[k] = v
        deduped.append(parsed)

    ranked = sorted(deduped, key=lambda r: r.get('score', 0), reverse=True)[:20]
    for i, r in enumerate(ranked, 1):
        r['rank'] = i

    run_dates = set(r.get('run_date', '') for r in rows)
    return {
        'total_runs': len(run_dates),
        'unique_configs': len(best_map),
        'top_20': ranked,
    }


@app.get("/api/autoresearch/export")
def export_autoresearch():
    csv_path = 'autoresearch_results.csv'
    if not os.path.exists(csv_path):
        raise HTTPException(404, "No autoresearch results CSV found")
    return FileResponse(csv_path, filename='autoresearch_results.csv',
                        media_type='text/csv')


@app.get("/api/autoresearch/export-alltime")
def export_alltime():
    csv_path = 'autoresearch_alltime.csv'
    if not os.path.exists(csv_path):
        raise HTTPException(404, "No alltime CSV found")
    return FileResponse(csv_path, filename='autoresearch_alltime.csv',
                        media_type='text/csv')


class AutoResearchUpload(BaseModel):
    results_csv: str           # Full CSV content replacing autoresearch_results.csv
    alltime_rows_csv: str      # All alltime rows (with header) to merge into autoresearch_alltime.csv
    meta: Dict[str, Any]       # Replaces autoresearch_meta.json


def _alltime_row_key(row: dict) -> str:
    """
    Stable dedup key based on the actual config parameters.
    Always uses a composite of param values so rows with and without
    config_hash for the same underlying config hash to the same key.
    """
    return '|'.join([
        str(row.get('lookback', row.get('lookback_window', ''))),
        str(row.get('regression', row.get('regression_level', ''))),
        str(row.get('smoothing', row.get('use_kernel_smoothing', ''))),
        str(row.get('relative_weight', '')),
        str(row.get('lag', '')),
        str(row.get('atr_period', '')),
        str(row.get('atr_multiplier', '')),
        str(row.get('vol_min', row.get('volatility_min', ''))),
        str(row.get('vol_max', row.get('volatility_max', ''))),
        str(row.get('reentry_delay', row.get('re_entry_delay', ''))),
    ])


def _is_valid_alltime_row(row: dict) -> bool:
    """
    Basic sanity check: lookback and regression must be parseable as numbers.
    Rejects rows where old 4-column format data ended up in the wrong columns
    (e.g., regression='True' from header mismatch).
    """
    try:
        lbk = row.get('lookback', row.get('lookback_window', ''))
        reg = row.get('regression', row.get('regression_level', ''))
        if not lbk or not reg:
            return False
        float(lbk)
        float(reg)
        return True
    except (ValueError, TypeError):
        return False


def _merge_alltime_csv(alltime_path: str, incoming_csv: str) -> dict:
    """
    Merge incoming alltime CSV rows into the on-disk alltime file.
    - Deduplicates by config_hash / composite key (incoming rows win on conflict).
    - Upgrades the header if the incoming CSV has more columns (new format).
    Returns {'merged': int, 'added': int, 'header_fixed': bool}
    """
    incoming_reader = csv.DictReader(io.StringIO(incoming_csv))
    incoming_rows = list(incoming_reader)
    incoming_fieldnames = list(incoming_reader.fieldnames or [])

    if not incoming_rows:
        return {'merged': 0, 'added': 0, 'header_fixed': False}

    # Load existing rows
    existing_map: dict = {}
    existing_fieldnames: list = []
    if os.path.exists(alltime_path) and os.path.getsize(alltime_path) > 0:
        try:
            with open(alltime_path, 'r', encoding='utf-8') as f:
                reader = csv.DictReader(f)
                existing_fieldnames = list(reader.fieldnames or [])
                for row in reader:
                    if not _is_valid_alltime_row(row):
                        continue
                    k = _alltime_row_key(row)
                    if k:
                        existing_map[k] = row
        except Exception as exc:
            logger.warning(f"[AUTORESEARCH] Could not read existing alltime CSV: {exc}")

    # Prefer the fieldnames list with more columns (incoming wins on ties)
    if len(incoming_fieldnames) >= len(existing_fieldnames):
        fieldnames = incoming_fieldnames
        header_fixed = existing_fieldnames != incoming_fieldnames and bool(existing_fieldnames)
    else:
        fieldnames = existing_fieldnames
        header_fixed = False

    before = len(existing_map)
    for row in incoming_rows:
        if not _is_valid_alltime_row(row):
            continue
        k = _alltime_row_key(row)
        if k:
            existing_map[k] = row

    added = len(existing_map) - before

    with open(alltime_path, 'w', newline='', encoding='utf-8') as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames, extrasaction='ignore')
        writer.writeheader()
        writer.writerows(existing_map.values())

    return {'merged': len(existing_map), 'added': added, 'header_fixed': header_fixed}


@app.post("/api/autoresearch/upload")
def upload_autoresearch(
    payload: AutoResearchUpload,
    x_upload_key: str = Header(..., alias="x-upload-key"),
):
    """Receive autoresearch results pushed from a local machine."""
    expected_key = os.environ.get("UPLOAD_API_KEY", "")
    if not expected_key:
        raise HTTPException(500, "UPLOAD_API_KEY not configured on server")
    if x_upload_key != expected_key:
        raise HTTPException(401, "Invalid upload key")

    results_path = 'autoresearch_results.csv'
    alltime_path = 'autoresearch_alltime.csv'
    meta_path = 'autoresearch_meta.json'

    result_lines = [l for l in payload.results_csv.splitlines() if l.strip()]
    uploaded_count = max(0, len(result_lines) - 1)

    # All file writes are serialised by a lock so two simultaneous uploads
    # cannot overwrite each other's new configs in the alltime CSV.
    with _autoresearch_lock:
        # Replace current results CSV
        if payload.results_csv.strip():
            with open(results_path, 'w', newline='', encoding='utf-8') as f:
                f.write(payload.results_csv)

        # Merge alltime rows with deduplication (read-modify-write under lock)
        alltime_stats: dict = {'merged': 0, 'added': 0, 'header_fixed': False}
        if payload.alltime_rows_csv.strip():
            alltime_stats = _merge_alltime_csv(alltime_path, payload.alltime_rows_csv)

        # Overwrite meta JSON only when provided
        if payload.meta:
            with open(meta_path, 'w', encoding='utf-8') as f:
                json.dump(payload.meta, f, indent=2)

    logger.info(
        f"[AUTORESEARCH] Upload: {uploaded_count} result rows, "
        f"alltime merged={alltime_stats['merged']} added={alltime_stats['added']} "
        f"header_fixed={alltime_stats['header_fixed']}"
    )
    return {
        'status': 'ok',
        'uploaded': uploaded_count,
        'alltime_merged': alltime_stats['merged'],
        'alltime_added': alltime_stats['added'],
        'alltime_header_fixed': alltime_stats['header_fixed'],
    }


@app.post("/api/autoresearch/repair-alltime")
def repair_alltime(x_upload_key: str = Header(..., alias="x-upload-key")):
    """
    One-shot repair: deduplicate and fix the header of autoresearch_alltime.csv.
    Protected by the same X-Upload-Key as uploads.
    """
    expected_key = os.environ.get("UPLOAD_API_KEY", "")
    if not expected_key:
        raise HTTPException(500, "UPLOAD_API_KEY not configured on server")
    if x_upload_key != expected_key:
        raise HTTPException(401, "Invalid upload key")

    alltime_path = 'autoresearch_alltime.csv'
    if not os.path.exists(alltime_path) or os.path.getsize(alltime_path) == 0:
        raise HTTPException(404, "autoresearch_alltime.csv not found or empty")

    with _autoresearch_lock:
        return _repair_alltime_locked(alltime_path)


def _repair_alltime_locked(alltime_path: str):
    """Inner repair logic — must be called while holding _autoresearch_lock."""
    # Read all rows, skip any that don't parse properly
    try:
        with open(alltime_path, 'r', encoding='utf-8') as f:
            content = f.read()
    except Exception as e:
        raise HTTPException(500, f"Could not read file: {e}")

    lines = [l for l in content.splitlines() if l.strip()]
    if not lines:
        raise HTTPException(400, "File is empty after stripping blank lines")

    # The canonical header: first field must be "run_date" (not a timestamp/number).
    # Search for it; if not found, inject the standard one.
    CANONICAL_HEADER = (
        "run_date,lookback,regression,smoothing,relative_weight,lag,"
        "atr_period,atr_multiplier,vol_min,vol_max,reentry_delay,"
        "eth_pf,btc_pf,sol_pf,eth_dd,btc_dd,sol_dd,"
        "eth_profit,btc_profit,sol_profit,score"
    )
    header_line = None
    for line in lines:
        first_field = line.split(',')[0].strip()
        if first_field == 'run_date':
            header_line = line
            break

    if header_line is None:
        # No recognisable header present — use canonical
        header_line = CANONICAL_HEADER

    fieldnames = [c.strip() for c in header_line.split(',')]
    n_cols = len(fieldnames)

    # Parse every non-header line that has the expected column count
    rows_map: dict = {}
    for line in lines:
        if line.strip() == header_line.strip():
            continue
        values = line.split(',')
        if len(values) != n_cols:
            continue
        row = dict(zip(fieldnames, [v.strip() for v in values]))
        if not _is_valid_alltime_row(row):
            continue
        k = _alltime_row_key(row)
        if k:
            rows_map[k] = row

    before_count = len(lines) - 1  # approximate: original data rows

    with open(alltime_path, 'w', newline='', encoding='utf-8') as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames, extrasaction='ignore')
        writer.writeheader()
        writer.writerows(rows_map.values())

    logger.info(
        f"[AUTORESEARCH] repair-alltime: {before_count} raw lines → "
        f"{len(rows_map)} unique configs, header={header_line[:60]}"
    )
    return {
        'status': 'ok',
        'rows_before': before_count,
        'rows_after': len(rows_map),
        'header': header_line[:120],
    }


# ------------------------------------------------------------------
# Orchestrator API — Distributed Worker System
# ------------------------------------------------------------------

try:
    from bot.orchestrator import (
        DEFAULT_PARAM_SPACE,
        DEFAULT_TARGET_WORKERS,
        DEFAULT_TIME_BUDGET_MINUTES,
        OrchestratorDB,
    )
    orchestrator_db = OrchestratorDB()
    HAS_ORCHESTRATOR = True
except Exception as e:
    logger.warning(f"Orchestrator DB not available: {e}")
    HAS_ORCHESTRATOR = False


@app.on_event("startup")
def _startup_ensure_always_on_sweep():
    """On server start, guarantee the always-on perpetual sweep exists and is running."""
    if not HAS_ORCHESTRATOR:
        return
    try:
        sweep = orchestrator_db.ensure_always_on_sweep()
        logger.info(f"[ORCHESTRATOR] Always-on sweep ready: {sweep['id']} ({sweep['name']})")
    except Exception as exc:
        logger.warning(f"[ORCHESTRATOR] Could not ensure always-on sweep: {exc}")


class CreateSweepRequest(BaseModel):
    name: str
    params: Dict[str, Any]
    num_workers: int = 1


class WorkerRegisterRequest(BaseModel):
    name: str
    hostname: str
    machine_info: Dict[str, Any]


class JobSubmitRequest(BaseModel):
    job_id: str
    results: list


class UpdateSweepRequest(BaseModel):
    status: str


class AlwaysOnSweepSettingsRequest(BaseModel):
    time_budget_minutes: int = DEFAULT_TIME_BUDGET_MINUTES
    target_workers: int = DEFAULT_TARGET_WORKERS


@app.get('/api/sweeps/always-on')
def get_always_on_sweep():
    """Return the current always-on perpetual sweep (creates one if absent)."""
    if not HAS_ORCHESTRATOR:
        raise HTTPException(status_code=503, detail="Orchestrator not available")
    try:
        sweep = orchestrator_db.ensure_always_on_sweep()
        return sweep
    except Exception as e:
        logger.error(f"[ORCHESTRATOR] Error fetching always-on sweep: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.patch('/api/sweeps/always-on')
def update_always_on_sweep(req: AlwaysOnSweepSettingsRequest, x_upload_key: str = Header(None)):
    """Update settings (time budget, target workers) for the always-on sweep."""
    if not HAS_ORCHESTRATOR:
        raise HTTPException(status_code=503, detail="Orchestrator not available")

    upload_key = os.getenv('UPLOAD_API_KEY', '')
    if not upload_key or x_upload_key != upload_key:
        raise HTTPException(status_code=401, detail="Invalid upload key")

    try:
        sweep = orchestrator_db.ensure_always_on_sweep(
            time_budget_minutes=req.time_budget_minutes,
            target_workers=req.target_workers,
        )
        logger.info(
            f"[ORCHESTRATOR] Always-on sweep settings updated: "
            f"{req.time_budget_minutes}min budget, {req.target_workers} workers"
        )
        return sweep
    except Exception as e:
        logger.error(f"[ORCHESTRATOR] Error updating always-on sweep: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.post('/api/sweeps')
def create_sweep(req: CreateSweepRequest, x_upload_key: str = Header(None)):
    """Create a new smart-mode sweep campaign."""
    if not HAS_ORCHESTRATOR:
        raise HTTPException(status_code=503, detail="Orchestrator not available")
    
    upload_key = os.getenv('UPLOAD_API_KEY', '')
    if not upload_key or x_upload_key != upload_key:
        raise HTTPException(status_code=401, detail="Invalid upload key")
    
    try:
        sweep = orchestrator_db.create_sweep(req.name, req.params, req.num_workers)
        orchestrator_db.update_sweep_status(sweep['id'], 'running')
        logger.info(f"[ORCHESTRATOR] Created smart-mode sweep {sweep['id']}: {req.name} ({req.num_workers} workers)")
        return sweep
    except Exception as e:
        logger.error(f"[ORCHESTRATOR] Error creating sweep: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.get('/api/sweeps')
def list_sweeps():
    """List all sweeps with summary info."""
    if not HAS_ORCHESTRATOR:
        raise HTTPException(status_code=503, detail="Orchestrator not available")
    
    try:
        sweeps = orchestrator_db.list_sweeps()
        return {'sweeps': sweeps}
    except Exception as e:
        logger.error(f"[ORCHESTRATOR] Error listing sweeps: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.get('/api/sweeps/{sweep_id}')
def get_sweep(sweep_id: str):
    """Get detailed sweep info."""
    if not HAS_ORCHESTRATOR:
        raise HTTPException(status_code=503, detail="Orchestrator not available")
    
    try:
        sweep = orchestrator_db.get_sweep(sweep_id)
        if not sweep:
            raise HTTPException(status_code=404, detail="Sweep not found")
        return sweep
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"[ORCHESTRATOR] Error getting sweep: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.patch('/api/sweeps/{sweep_id}')
def update_sweep(sweep_id: str, req: UpdateSweepRequest, x_upload_key: str = Header(None)):
    """Update sweep status (pause/resume/cancel)."""
    if not HAS_ORCHESTRATOR:
        raise HTTPException(status_code=503, detail="Orchestrator not available")
    
    upload_key = os.getenv('UPLOAD_API_KEY', '')
    if not upload_key or x_upload_key != upload_key:
        raise HTTPException(status_code=401, detail="Invalid upload key")
    
    try:
        orchestrator_db.update_sweep_status(sweep_id, req.status)
        logger.info(f"[ORCHESTRATOR] Updated sweep {sweep_id} status to {req.status}")
        return {'status': 'ok', 'sweep_id': sweep_id, 'new_status': req.status}
    except Exception as e:
        logger.error(f"[ORCHESTRATOR] Error updating sweep: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.delete('/api/sweeps/{sweep_id}')
def delete_sweep(sweep_id: str, x_upload_key: str = Header(None)):
    """Delete sweep and all its jobs."""
    if not HAS_ORCHESTRATOR:
        raise HTTPException(status_code=503, detail="Orchestrator not available")
    
    upload_key = os.getenv('UPLOAD_API_KEY', '')
    if not upload_key or x_upload_key != upload_key:
        raise HTTPException(status_code=401, detail="Invalid upload key")
    
    try:
        orchestrator_db.delete_sweep(sweep_id)
        logger.info(f"[ORCHESTRATOR] Deleted sweep {sweep_id}")
        return {'status': 'ok', 'sweep_id': sweep_id}
    except Exception as e:
        logger.error(f"[ORCHESTRATOR] Error deleting sweep: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.post('/api/worker/register')
def register_worker(req: WorkerRegisterRequest, x_upload_key: str = Header(None)):
    """Register a new worker."""
    if not HAS_ORCHESTRATOR:
        raise HTTPException(status_code=503, detail="Orchestrator not available")
    
    upload_key = os.getenv('UPLOAD_API_KEY', '')
    if not upload_key or x_upload_key != upload_key:
        raise HTTPException(status_code=401, detail="Invalid upload key")
    
    try:
        worker_id = orchestrator_db.register_worker(req.name, req.hostname, req.machine_info)
        logger.info(f"[ORCHESTRATOR] Registered worker {worker_id}: {req.name}@{req.hostname}")
        return {'worker_id': worker_id, 'name': req.name}
    except Exception as e:
        logger.error(f"[ORCHESTRATOR] Error registering worker: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.post('/api/worker/claim')
def claim_job(worker_id: str, x_upload_key: str = Header(None)):
    """Claim next pending job for a worker."""
    if not HAS_ORCHESTRATOR:
        raise HTTPException(status_code=503, detail="Orchestrator not available")
    
    upload_key = os.getenv('UPLOAD_API_KEY', '')
    if not upload_key or x_upload_key != upload_key:
        raise HTTPException(status_code=401, detail="Invalid upload key")
    
    try:
        job = orchestrator_db.claim_job(worker_id)
        if not job:
            return {'job': None, 'message': 'No jobs available'}
        
        logger.info(f"[ORCHESTRATOR] Worker {worker_id} claimed job {job['id']} "
                    f"with {len(job['configs'])} configs")
        return {'job': job}
    except Exception as e:
        logger.error(f"[ORCHESTRATOR] Error claiming job: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.post('/api/worker/submit')
def submit_job(req: JobSubmitRequest, x_upload_key: str = Header(None)):
    """Submit completed job results."""
    if not HAS_ORCHESTRATOR:
        raise HTTPException(status_code=503, detail="Orchestrator not available")
    
    upload_key = os.getenv('UPLOAD_API_KEY', '')
    if not upload_key or x_upload_key != upload_key:
        raise HTTPException(status_code=401, detail="Invalid upload key")
    
    try:
        orchestrator_db.submit_job(req.job_id, req.results)
        
        with _autoresearch_lock:
            _merge_worker_results_to_alltime(req.results)
        
        logger.info(f"[ORCHESTRATOR] Job {req.job_id} submitted with {len(req.results)} results")
        return {'status': 'ok', 'job_id': req.job_id, 'results_count': len(req.results)}
    except Exception as e:
        logger.error(f"[ORCHESTRATOR] Error submitting job: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.post('/api/worker/heartbeat')
def worker_heartbeat(worker_id: str, x_upload_key: str = Header(None)):
    """Worker keep-alive heartbeat."""
    if not HAS_ORCHESTRATOR:
        raise HTTPException(status_code=503, detail="Orchestrator not available")
    
    upload_key = os.getenv('UPLOAD_API_KEY', '')
    if not upload_key or x_upload_key != upload_key:
        raise HTTPException(status_code=401, detail="Invalid upload key")
    
    try:
        orchestrator_db.heartbeat(worker_id)
        return {'status': 'ok', 'worker_id': worker_id}
    except Exception as e:
        logger.error(f"[ORCHESTRATOR] Error updating heartbeat: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.get('/api/workers')
def list_workers():
    """List all workers with status."""
    if not HAS_ORCHESTRATOR:
        raise HTTPException(status_code=503, detail="Orchestrator not available")
    
    try:
        workers = orchestrator_db.list_workers()
        return {'workers': workers}
    except Exception as e:
        logger.error(f"[ORCHESTRATOR] Error listing workers: {e}")
        raise HTTPException(status_code=500, detail=str(e))


def _merge_worker_results_to_alltime(results):
    """Merge worker job results into autoresearch_alltime.csv."""
    alltime_path = 'autoresearch_alltime.csv'
    
    canonical_fields = [
        'run_date', 'lookback', 'regression', 'smoothing', 'relative_weight', 'lag',
        'atr_period', 'atr_multiplier', 'vol_min', 'vol_max', 'reentry_delay',
        'eth_pf', 'btc_pf', 'sol_pf', 'eth_dd', 'btc_dd', 'sol_dd',
        'eth_profit', 'btc_profit', 'sol_profit', 'score', 'balanced_score',
        'confidence', 'config_hash'
    ]
    
    existing_rows = {}
    if os.path.exists(alltime_path):
        with open(alltime_path, 'r', encoding='utf-8') as f:
            reader = csv.DictReader(f)
            for row in reader:
                k = _alltime_row_key(row)
                if k:
                    existing_rows[k] = row
    
    from datetime import datetime, timezone
    run_date = datetime.now(timezone.utc).strftime('%Y-%m-%d')
    
    for result in results:
        if result.get('score', 0) == 0:
            continue
        
        row = {
            'run_date': run_date,
            'lookback': result.get('lookback_window'),
            'regression': result.get('regression_level'),
            'smoothing': result.get('use_kernel_smoothing'),
            'relative_weight': result.get('relative_weight'),
            'lag': result.get('lag'),
            'atr_period': result.get('atr_period'),
            'atr_multiplier': result.get('atr_multiplier'),
            'vol_min': result.get('volatility_min'),
            'vol_max': result.get('volatility_max'),
            'reentry_delay': result.get('re_entry_delay'),
            'eth_pf': result.get('eth_pf'),
            'btc_pf': result.get('btc_pf'),
            'sol_pf': result.get('sol_pf'),
            'eth_dd': result.get('eth_dd'),
            'btc_dd': result.get('btc_dd'),
            'sol_dd': result.get('sol_dd'),
            'eth_profit': result.get('eth_profit'),
            'btc_profit': result.get('btc_profit'),
            'sol_profit': result.get('sol_profit'),
            'score': result.get('score'),
            'balanced_score': result.get('balanced_score'),
            'confidence': result.get('confidence'),
            'config_hash': result.get('config_hash'),
        }
        
        k = _alltime_row_key(row)
        if k:
            existing_rows[k] = row
    
    with open(alltime_path, 'w', newline='', encoding='utf-8') as f:
        writer = csv.DictWriter(f, fieldnames=canonical_fields, extrasaction='ignore')
        writer.writeheader()
        writer.writerows(existing_rows.values())


def _read_csv(filepath, limit):
    try:
        if not os.path.exists(filepath):
            return []
        with open(filepath, 'r') as f:
            reader = csv.DictReader(f)
            all_rows = list(reader)
        return all_rows[-limit:]
    except Exception as e:
        logger.error(f"Error reading {filepath}: {e}")
        return []
