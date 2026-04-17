"""
AutoResearch — Cross-Asset Kernel Parameter Optimization

Iterates over any combination of strategy parameters,
tests each on ETH/BTC/SOL, and ranks by cross-asset consistency score.

Usage:
    python autoresearch.py
    python autoresearch.py --assets ETHUSDT BTCUSDT SOLUSDT AVAXUSDT
    python autoresearch.py --assets ETHUSDT BTCUSDT
    python autoresearch.py --h-step 5 --x-step 2
    python autoresearch.py --h-min 60 --h-max 80 --h-step 1 --x-min 55 --x-max 64 --x-step 1
    python autoresearch.py --smoothing both
    python autoresearch.py --r-values 5 8 10 15 20
    python autoresearch.py --atr-period-values 10 14 20 30 --atr-mult-values 3 4 5 6 8 10
    python autoresearch.py --vol-min-values 1 3 5 7 --vol-max-values 5 7 10 14 20
    python autoresearch.py --h-min 60 --h-max 110 --h-step 5 --x-min 67 --x-max 67 --r-values 5 8 10 15 20
"""

import argparse
import csv
import hashlib
import io
import json
import os
import random
import sys
import time
from argparse import Namespace
from datetime import datetime, timedelta, timezone
from itertools import product

import numpy as np
from binance.client import Client
from dotenv import load_dotenv

# Lazy import for Prisma/SQLite — autoresearch works without it
try:
    from bot.db import AutoResearchDB, run_async
    HAS_DB = True
except ImportError:
    HAS_DB = False

load_dotenv()

# ---------------------------------------------------------------------------
# Default values (used when parameter is not being swept)
# ---------------------------------------------------------------------------
DEFAULTS = {
    'relative_weight': 10.0,
    'lag': 1,
    'sl_type': 'atr',
    'atr_period': 20,
    'atr_multiplier': 6.0,
    'use_dynamic_sl': True,
    'trailing_mode': 'pine',
    'volatility_min': 5,
    'volatility_max': 10,
    'enable_re_entry': True,
    're_entry_delay': 1,
    'commission': 0.05,
    'slippage': 0.0,
    'no_sl': False,
    'vol_filter_off': False,
    'capital': 10000.0,
    'start': '2025-01-01',
    'end': datetime.now().strftime('%Y-%m-%d'),
    'timeframe': '1h',
    'output': 'backtest_trades.csv',
    'no_cache': False,
}

DEFAULT_ASSETS = ['ETHUSDT', 'BTCUSDT', 'SOLUSDT']

# Seconds per combo (measured from 81-combo run: 3m53s / 81 = ~2.9s)
SECS_PER_COMBO = 2.9


def parse_args():
    p = argparse.ArgumentParser(description='AutoResearch — cross-asset kernel parameter optimization')

    # Mode selection
    p.add_argument('--mode', choices=['grid', 'smart', 'worker'], default='grid',
                   help='grid=brute force, smart=3-phase search, worker=infinite loop with server sync')
    p.add_argument('--time-budget', type=int, default=3600,
                   help='Time budget in seconds for smart mode (default: 1h)')
    p.add_argument('--seed', type=int, default=None,
                   help='Random seed for smart/worker mode (use different seeds per parallel instance)')
    p.add_argument('--sync-interval', type=int, default=50,
                   help='Worker mode: re-sync tested hashes from server every N experiments (default: 50)')

    # Assets/tokens to test
    p.add_argument('--assets', type=str, nargs='+', default=None,
                   help='Assets to test (e.g. --assets ETHUSDT BTCUSDT SOLUSDT AVAXUSDT)')

    # Lookback window (h) range
    p.add_argument('--h-min', type=int, default=30, help='Lookback window min (default: 30)')
    p.add_argument('--h-max', type=int, default=110, help='Lookback window max (default: 110)')
    p.add_argument('--h-step', type=int, default=10, help='Lookback window step (default: 10)')

    # Regression level (x) range
    p.add_argument('--x-min', type=int, default=25, help='Regression level min (default: 25)')
    p.add_argument('--x-max', type=int, default=64, help='Regression level max (default: 64)')
    p.add_argument('--x-step', type=int, default=5, help='Regression level step (default: 5)')

    # Smoothing
    p.add_argument('--smoothing', choices=['on', 'off', 'both'], default='on',
                   help='Kernel smoothing mode: on, off, or both (default: on)')

    # Additional parameter sweeps (list-based)
    p.add_argument('--r-values', type=float, nargs='+', default=None,
                   help='Relative weight values to test (e.g. --r-values 5 8 10 15 20)')
    p.add_argument('--lag-values', type=int, nargs='+', default=None,
                   help='Lag values to test (e.g. --lag-values 1 2 3)')
    p.add_argument('--atr-period-values', type=int, nargs='+', default=None,
                   help='ATR period values (e.g. --atr-period-values 10 14 20 30)')
    p.add_argument('--atr-mult-values', type=float, nargs='+', default=None,
                   help='ATR multiplier values (e.g. --atr-mult-values 3 4 5 6 8 10)')
    p.add_argument('--vol-min-values', type=int, nargs='+', default=None,
                   help='Volatility min values (e.g. --vol-min-values 1 3 5 7)')
    p.add_argument('--vol-max-values', type=int, nargs='+', default=None,
                   help='Volatility max values (e.g. --vol-max-values 5 7 10 14 20)')
    p.add_argument('--reentry-delay-values', type=int, nargs='+', default=None,
                   help='Re-entry delay values (e.g. --reentry-delay-values 0 1 2 3)')

    # Upload to central server
    p.add_argument('--upload-url', type=str, default=None,
                   help='Server URL to auto-upload results (e.g. http://YOUR_SERVER_IP:8080/api/autoresearch/upload)')
    p.add_argument('--upload-key', type=str, default=None,
                   help='API key for upload authentication')
    p.add_argument('--upload-batch-size', type=int, default=20,
                   help='Upload results every N valid experiments (default: 20)')
    p.add_argument('--author', type=str, default=None,
                   help='Author name for upload tagging (default: system username)')

    # Walk-Forward Validation
    p.add_argument('--walkforward', action='store_true', default=False,
                   help='Run walk-forward validation on top configs')
    p.add_argument('--wf-folds', type=int, default=3,
                   help='Number of WF folds (default: 3)')
    p.add_argument('--wf-test-months', type=int, default=3,
                   help='Test window size in months (default: 3)')
    p.add_argument('--wf-top-n', type=int, default=50,
                   help='Top N configs from Phase 1 to validate (default: 50)')
    p.add_argument('--phase2-only', action='store_true', default=False,
                   help='Skip Phase 1, use existing results from alltime CSV')
    p.add_argument('--wf-input', type=str, default='autoresearch_alltime.csv',
                   help='Input CSV for phase2-only mode (default: autoresearch_alltime.csv)')

    return p.parse_args()


# ---------------------------------------------------------------------------
# Score function
# ---------------------------------------------------------------------------

def calculate_score(results_per_asset):
    """
    Cross-asset consistency score.

    - ANY asset PF < 1.0 -> 0 (REJECTED)
    - ANY asset DD > 40% -> 0 (REJECTED)
    - Otherwise: avg_pf x min_pf (rewards consistency)
    """
    # Check for missing keys or 0 trades
    for asset, r in results_per_asset.items():
        if 'profit_factor' not in r or r.get('total_trades', 0) == 0:
            return 0.0  # Rejected — no data

    pfs = [r['profit_factor'] for r in results_per_asset.values()]
    dds = [r['max_drawdown_pct'] for r in results_per_asset.values()]

    if any(pf < 1.0 for pf in pfs):
        return 0.0
    if any(dd > 40 for dd in dds):
        return 0.0

    avg_pf = sum(pfs) / len(pfs)
    min_pf = min(pfs)
    return round(avg_pf * min_pf, 4)


def calculate_balanced_score(results_per_asset):
    """
    Score that rewards equal performance across all assets.
    Uses min_profit * balance_factor where balance_factor penalizes imbalance.
    """
    for asset, r in results_per_asset.items():
        if 'profit_factor' not in r or r.get('total_trades', 0) == 0:
            return 0.0

    pfs = [r['profit_factor'] for r in results_per_asset.values()]
    dds = [r['max_drawdown_pct'] for r in results_per_asset.values()]
    profits = [r['net_profit_pct'] for r in results_per_asset.values()]

    if any(pf < 1.0 for pf in pfs):
        return 0.0
    if any(dd > 40 for dd in dds):
        return 0.0
    if not all(p > 0 for p in profits):
        return 0.0

    min_profit = min(profits)
    avg_profit = sum(profits) / len(profits)

    # Coefficient of variation (0 = perfectly balanced, 1+ = very imbalanced)
    std_dev = (sum((p - avg_profit)**2 for p in profits) / len(profits)) ** 0.5
    cv = std_dev / avg_profit if avg_profit > 0 else 1.0

    balance_factor = max(0, 1 - cv)

    return round(min_profit * balance_factor / 100, 4)


# ---------------------------------------------------------------------------
# Build args namespace for a single backtest run
# ---------------------------------------------------------------------------

def make_args(symbol, combo):
    """Build an argparse-like Namespace for backtest.run_backtest.
    combo is a dict with all parameter values for this iteration."""
    d = dict(DEFAULTS)
    d.update(combo)
    d['symbol'] = symbol
    d['sl_percent'] = 2.7  # unused when sl_type=atr, but needed by config
    return Namespace(**d)


# ---------------------------------------------------------------------------
# Format helpers
# ---------------------------------------------------------------------------

def fmt_time(seconds):
    """Format seconds as Xh Ym Zs or Ym Zs."""
    h = int(seconds) // 3600
    m = (int(seconds) % 3600) // 60
    s = int(seconds) % 60
    if h > 0:
        return f'{h}h{m:02d}m{s:02d}s'
    return f'{m}m{s:02d}s'


def smooth_label(val):
    return 'On' if val else 'Off'


# ---------------------------------------------------------------------------
# Upload to central server
# ---------------------------------------------------------------------------

def fetch_server_hashes(upload_url):
    """Fetch all tested config hashes from server. Returns set of hashes."""
    if not upload_url:
        return set()
    
    try:
        import urllib.request
        import urllib.error
        
        url = upload_url.replace('/upload', '/export-alltime')
        with urllib.request.urlopen(url, timeout=15) as resp:
            content = resp.read().decode('utf-8')
        
        hashes = set()
        reader = csv.DictReader(io.StringIO(content))
        for row in reader:
            h = row.get('config_hash', '').strip()
            if h:
                hashes.add(h)
        return hashes
    except Exception as e:
        print(f'  [SYNC] Failed to fetch server hashes: {e}')
        return set()


def upload_results(results_batch, upload_url, upload_key, author=None):
    """Upload batch of results to central server. Never crashes — logs warnings."""
    if not upload_url:
        return False

    try:
        import urllib.request
        import urllib.error

        author_name = author or os.getenv('USERNAME', os.getenv('USER', 'unknown'))

        payload_results = []
        for entry in results_batch:
            # entry is a tuple: (params, score, balanced, asset_results, confidence)
            params, score, balanced, asset_results, conf = entry
            item = {
                'config_hash': config_hash(params),
                'lookback': params['h'],
                'regression': params['x'],
                'relative_weight': params.get('r', 10),
                'smoothing': params.get('smoothing', True),
                'vol_min': params.get('vol_min', 5),
                'vol_max': params.get('vol_max', 10),
                'atr_period': params.get('atr_period', 20),
                'atr_multiplier': params.get('atr_multiplier', 6.0),
                'reentry_delay': params.get('reentry_delay', 1),
                'score': score,
                'balanced_score': balanced,
                'confidence': conf,
            }
            for asset_name, ar in asset_results.items():
                prefix = asset_name.replace('USDT', '').lower()
                item[f'{prefix}_pf'] = round(ar.get('profit_factor', 0), 2)
                item[f'{prefix}_dd'] = round(ar.get('max_drawdown_pct', 0), 1)
                item[f'{prefix}_profit'] = round(ar.get('net_profit_pct', 0), 1)
                item[f'{prefix}_trades'] = ar.get('total_trades', 0)
                item[f'{prefix}_wr'] = round(ar.get('win_rate', 0), 1)
            payload_results.append(item)

        payload = json.dumps({
            'key': upload_key,
            'author': author_name,
            'results': payload_results,
        }).encode('utf-8')

        req = urllib.request.Request(
            upload_url,
            data=payload,
            headers={'Content-Type': 'application/json'},
            method='POST',
        )

        with urllib.request.urlopen(req, timeout=30) as resp:
            print(f'  [UPLOAD] Sent {len(results_batch)} results to {upload_url}')
            return True

    except urllib.error.URLError:
        print(f'  [UPLOAD] Cannot connect to {upload_url} -- results saved locally, will retry next batch')
        return False
    except Exception as e:
        print(f'  [UPLOAD] Error: {e} -- continuing locally')
        return False


# ---------------------------------------------------------------------------
# All-Time Leaderboard (append-only)
# ---------------------------------------------------------------------------
ALLTIME_CSV = 'autoresearch_alltime.csv'
ALLTIME_FIELDS = [
    'run_date', 'lookback', 'regression', 'smoothing',
    'relative_weight', 'lag', 'atr_period', 'atr_multiplier',
    'vol_min', 'vol_max', 'reentry_delay',
    'eth_pf', 'btc_pf', 'sol_pf',
    'eth_dd', 'btc_dd', 'sol_dd',
    'eth_profit', 'btc_profit', 'sol_profit',
    'score', 'balanced_score', 'confidence', 'config_hash',
]


def load_alltime_best():
    """Load all-time best from CSV and print it. Returns None if no file."""
    if not os.path.exists(ALLTIME_CSV):
        return None
    try:
        with open(ALLTIME_CSV, 'r') as f:
            reader = csv.DictReader(f)
            rows = list(reader)
        if not rows:
            return None
        best = max(rows, key=lambda r: float(r.get('score', 0)))
        return best
    except Exception:
        return None


def append_alltime(ranked_top20, run_date):
    """Append TOP 20 results to all-time CSV (creates file if needed)."""
    file_exists = os.path.exists(ALLTIME_CSV) and os.path.getsize(ALLTIME_CSV) > 0
    with open(ALLTIME_CSV, 'a', newline='') as f:
        writer = csv.DictWriter(f, fieldnames=ALLTIME_FIELDS)
        if not file_exists:
            writer.writeheader()
        for r in ranked_top20:
            writer.writerow({
                'run_date': run_date,
                'lookback': r['lookback_window'],
                'regression': r['regression_level'],
                'smoothing': r['use_kernel_smoothing'],
                'relative_weight': r['relative_weight'],
                'lag': r['lag'],
                'atr_period': r['atr_period'],
                'atr_multiplier': r['atr_multiplier'],
                'vol_min': r['volatility_min'],
                'vol_max': r['volatility_max'],
                'reentry_delay': r['re_entry_delay'],
                'eth_pf': r.get('eth_pf', 0),
                'btc_pf': r.get('btc_pf', 0),
                'sol_pf': r.get('sol_pf', 0),
                'eth_dd': r.get('eth_dd', 0),
                'btc_dd': r.get('btc_dd', 0),
                'sol_dd': r.get('sol_dd', 0),
                'eth_profit': r.get('eth_profit', 0),
                'btc_profit': r.get('btc_profit', 0),
                'sol_profit': r.get('sol_profit', 0),
                'score': r['score'],
                'balanced_score': r.get('balanced_score', 0),
                'confidence': r.get('confidence', 0),
                'config_hash': r.get('config_hash', ''),
            })
    print(f'  Appended {len(ranked_top20)} results to {ALLTIME_CSV}')


# ---------------------------------------------------------------------------
# Build grid of all parameter combinations
# ---------------------------------------------------------------------------

def build_grid(cli):
    """Build list of combo dicts from CLI args. Returns (combos, iterated_params, fixed_params)."""

    # --- Build value lists for each parameter ---
    # h range
    lookback_range = list(range(cli.h_min, cli.h_max + 1, cli.h_step))
    if lookback_range[-1] != cli.h_max and cli.h_max not in lookback_range:
        lookback_range.append(cli.h_max)

    # x range
    regression_range = list(range(cli.x_min, cli.x_max + 1, cli.x_step))
    if regression_range[-1] != cli.x_max and cli.x_max not in regression_range:
        regression_range.append(cli.x_max)

    # smoothing
    if cli.smoothing == 'both':
        smoothing_variants = [True, False]
    elif cli.smoothing == 'on':
        smoothing_variants = [True]
    else:
        smoothing_variants = [False]

    # Additional sweepable params: (param_name, cli_values, default, display_name)
    extra_params = [
        ('relative_weight',  cli.r_values,             DEFAULTS['relative_weight'],  'r'),
        ('lag',              cli.lag_values,            DEFAULTS['lag'],              'lag'),
        ('atr_period',       cli.atr_period_values,     DEFAULTS['atr_period'],       'atr_period'),
        ('atr_multiplier',   cli.atr_mult_values,       DEFAULTS['atr_multiplier'],   'atr_mult'),
        ('volatility_min',   cli.vol_min_values,        DEFAULTS['volatility_min'],   'vol_min'),
        ('volatility_max',   cli.vol_max_values,        DEFAULTS['volatility_max'],   'vol_max'),
        ('re_entry_delay',   cli.reentry_delay_values,  DEFAULTS['re_entry_delay'],   'reentry_delay'),
    ]

    # Determine which params are iterated vs fixed
    iterated = {}   # param_name -> list of values
    fixed = {}      # param_name -> single value

    # h and x are always present
    if len(lookback_range) > 1:
        iterated['lookback_window'] = lookback_range
    else:
        fixed['lookback_window'] = lookback_range[0]

    if len(regression_range) > 1:
        iterated['regression_level'] = regression_range
    else:
        fixed['regression_level'] = regression_range[0]

    if len(smoothing_variants) > 1:
        iterated['use_kernel_smoothing'] = smoothing_variants
    else:
        fixed['use_kernel_smoothing'] = smoothing_variants[0]

    for param_name, cli_values, default, _ in extra_params:
        if cli_values is not None and len(cli_values) > 1:
            iterated[param_name] = cli_values
        elif cli_values is not None and len(cli_values) == 1:
            fixed[param_name] = cli_values[0]
        else:
            fixed[param_name] = default

    # Build cartesian product of all iterated params
    if not iterated:
        # Single combo with all fixed values
        combo = dict(fixed)
        # Ensure required keys
        for key in ['lookback_window', 'regression_level', 'use_kernel_smoothing']:
            if key not in combo:
                if key == 'lookback_window':
                    combo[key] = lookback_range[0]
                elif key == 'regression_level':
                    combo[key] = regression_range[0]
                elif key == 'use_kernel_smoothing':
                    combo[key] = smoothing_variants[0]
        return [combo], iterated, fixed

    param_names = list(iterated.keys())
    param_values = [iterated[k] for k in param_names]

    combos = []
    for vals in product(*param_values):
        combo = dict(fixed)
        for name, val in zip(param_names, vals):
            combo[name] = val
        # Ensure required keys present
        for key, default_val in [('lookback_window', lookback_range[0]),
                                  ('regression_level', regression_range[0]),
                                  ('use_kernel_smoothing', smoothing_variants[0])]:
            if key not in combo:
                combo[key] = default_val
        # Skip vol_min >= vol_max — filter would block all signals
        if combo.get('volatility_min', 0) >= combo.get('volatility_max', 999):
            continue
        combos.append(combo)

    return combos, iterated, fixed


# ---------------------------------------------------------------------------
# Smart Mode — config hashing, scoring, search
# ---------------------------------------------------------------------------

PARAM_SPACE = {
    'h': {'min': 50, 'max': 120, 'type': 'int'},
    'x': {'min': 50, 'max': 80, 'type': 'int'},
    'r': {'values': [1, 3, 5, 8, 10, 12, 15, 20], 'type': 'choice'},
    'vol_min': {'values': [1, 2, 3, 5, 7], 'type': 'choice'},
    'vol_max': {'values': [5, 7, 8, 10, 14, 20], 'type': 'choice'},
    'smoothing': {'values': [True], 'type': 'choice'},
}


def config_hash(params):
    """Deterministic hash of all parameters."""
    key = (f"h{params['h']}_x{params['x']}_r{params['r']}"
           f"_vm{params['vol_min']}_vx{params['vol_max']}_sm{params['smoothing']}")
    return hashlib.md5(key.encode()).hexdigest()[:12]


def confidence_score(results_per_asset):
    """Higher = more confident this config is real, not noise."""
    try:
        pfs = [r['profit_factor'] for r in results_per_asset.values()]
        trades = [r['total_trades'] for r in results_per_asset.values()]
        if not pfs or not trades or min(trades) == 0:
            return 0.0
        min_pf = min(pfs)
        min_trades = min(trades)
        avg_pf = sum(pfs) / len(pfs)
        pf_variance = sum((pf - avg_pf) ** 2 for pf in pfs) / len(pfs)
        conf = min_pf * (min_trades ** 0.5) / (1 + pf_variance)
        return round(conf, 2)
    except (KeyError, TypeError):
        return 0.0


def evaluate_combo(combo_dict, asset_data, assets, apply_config_fn, run_backtest_fn, 
                   calculate_metrics_fn):
    """
    Run backtest for a single config combo across all assets.
    
    This is the core evaluation function extracted from the grid loop,
    designed to be called by workers without subprocess overhead.
    
    Args:
        combo_dict: dict with lookback_window, regression_level, etc.
        asset_data: dict of symbol -> (klines, trading_start_idx)
        assets: list of symbol strings (e.g. ['ETHUSDT', 'BTCUSDT', 'SOLUSDT'])
        apply_config_fn: backtest.apply_config function
        run_backtest_fn: backtest.run_backtest function
        calculate_metrics_fn: backtest.calculate_metrics function
    
    Returns:
        dict with keys:
            - 'combo': the input combo_dict
            - 'asset_results': dict of symbol -> metrics
            - 'score': cross-asset consistency score
            - 'balanced_score': balanced score
            - 'confidence': confidence score
            - 'config_hash': hash of key params
            - 'row': full CSV row dict with per-asset columns
    """
    h = combo_dict['lookback_window']
    x = combo_dict['regression_level']
    smooth = combo_dict['use_kernel_smoothing']
    r = combo_dict.get('relative_weight', DEFAULTS['relative_weight'])
    lag = combo_dict.get('lag', DEFAULTS['lag'])
    atr_p = combo_dict.get('atr_period', DEFAULTS['atr_period'])
    atr_m = combo_dict.get('atr_multiplier', DEFAULTS['atr_multiplier'])
    vol_min = combo_dict.get('volatility_min', DEFAULTS['volatility_min'])
    vol_max = combo_dict.get('volatility_max', DEFAULTS['volatility_max'])
    re_delay = combo_dict.get('re_entry_delay', DEFAULTS['re_entry_delay'])
    
    row = {
        'lookback_window': h,
        'regression_level': x,
        'use_kernel_smoothing': smooth,
        'relative_weight': r,
        'lag': lag,
        'atr_period': atr_p,
        'atr_multiplier': atr_m,
        'volatility_min': vol_min,
        'volatility_max': vol_max,
        're_entry_delay': re_delay,
    }
    
    asset_results = {}
    
    for symbol in assets:
        args = make_args(symbol, combo_dict)
        apply_config_fn(args)
        
        klines, tsi = asset_data[symbol]
        trades, _, final_balance = run_backtest_fn(klines, args, tsi)
        m = calculate_metrics_fn(trades, args.capital, final_balance)
        
        asset_results[symbol] = m
        
        pf = min(m.get('profit_factor', 0), 10.0)
        dd = m.get('max_drawdown_pct', 0)
        net = m.get('net_profit_pct', 0)
        n_trades = m.get('total_trades', 0)
        wr = m.get('win_rate', 0)
        sharpe = m.get('sharpe', 0)
        m['profit_factor'] = pf  # store capped value for scoring

        s = symbol.replace('USDT', '').lower()
        row[f'{s}_pf'] = round(pf, 2)
        row[f'{s}_dd'] = round(dd, 1)
        row[f'{s}_profit'] = round(net, 1)
        row[f'{s}_trades'] = n_trades
        row[f'{s}_wr'] = round(wr, 1)
        row[f'{s}_sharpe'] = round(sharpe, 2)
    
    score = calculate_score(asset_results)
    balanced = calculate_balanced_score(asset_results)
    conf = confidence_score(asset_results)
    
    row['score'] = score
    row['balanced_score'] = balanced
    row['confidence'] = conf
    row['config_hash'] = config_hash({
        'h': h, 'x': x, 'r': r, 'vol_min': vol_min,
        'vol_max': vol_max, 'smoothing': smooth,
    })
    
    return {
        'combo': combo_dict,
        'asset_results': asset_results,
        'score': score,
        'balanced_score': balanced,
        'confidence': conf,
        'config_hash': row['config_hash'],
        'row': row,
    }


def load_tested_hashes():
    """Load config hashes from alltime CSV to skip already-tested configs."""
    hashes = set()
    if not os.path.exists(ALLTIME_CSV):
        return hashes
    try:
        with open(ALLTIME_CSV, 'r') as f:
            reader = csv.DictReader(f)
            for row in reader:
                h = row.get('config_hash', '').strip()
                if h:
                    hashes.add(h)
    except Exception:
        pass
    return hashes


def load_alltime_results():
    """Load all valid results from alltime CSV for warm start."""
    results = []
    if not os.path.exists(ALLTIME_CSV):
        return results
    try:
        with open(ALLTIME_CSV, 'r') as f:
            reader = csv.DictReader(f)
            for row in reader:
                bs = float(row.get('balanced_score', 0))
                sc = float(row.get('score', 0))
                if bs > 0:
                    results.append({
                        'params': {
                            'h': int(row.get('lookback', 0)),
                            'x': int(row.get('regression', 0)),
                            'r': float(row.get('relative_weight', 10)),
                            'vol_min': int(row.get('vol_min', 5)),
                            'vol_max': int(row.get('vol_max', 10)),
                            'smoothing': row.get('smoothing', 'True') == 'True',
                        },
                        'score': sc,
                        'balanced_score': bs,
                        'confidence': float(row.get('confidence', 0)),
                    })
    except Exception:
        pass
    return results


def random_sample(param_space):
    """Random config from param space."""
    return {
        'h': random.randint(param_space['h']['min'], param_space['h']['max']),
        'x': random.randint(param_space['x']['min'], param_space['x']['max']),
        'r': random.choice(param_space['r']['values']),
        'vol_min': random.choice(param_space['vol_min']['values']),
        'vol_max': random.choice(param_space['vol_max']['values']),
        'smoothing': True,
    }


def mutate(params, magnitude='medium'):
    """Create a neighbor config by mutating parameters."""
    child = params.copy()

    if magnitude == 'small':
        child['h'] = max(50, min(120, child['h'] + random.choice([-1, 0, 1])))
        child['x'] = max(50, min(80, child['x'] + random.choice([-1, 0, 1])))
    elif magnitude == 'medium':
        child['h'] = max(50, min(120, child['h'] + random.randint(-5, 5)))
        child['x'] = max(50, min(80, child['x'] + random.randint(-3, 3)))

        r_values = PARAM_SPACE['r']['values']
        current_idx = r_values.index(child['r']) if child['r'] in r_values else 0
        new_idx = max(0, min(len(r_values) - 1, current_idx + random.choice([-1, 0, 1])))
        child['r'] = r_values[new_idx]

        if random.random() < 0.3:
            child['vol_min'] = random.choice(PARAM_SPACE['vol_min']['values'])
            child['vol_max'] = random.choice(PARAM_SPACE['vol_max']['values'])

    return child


def detect_region(top_results, n=20):
    """Detect parameter region where best configs cluster."""
    configs = [r[0] for r in top_results[:n] if r[1] > 0]
    if len(configs) < 3:
        return None

    hs = sorted(c['h'] for c in configs)
    xs = sorted(c['x'] for c in configs)

    return {
        'h_min': hs[0], 'h_max': hs[-1], 'h_median': hs[len(hs) // 2],
        'x_min': xs[0], 'x_max': xs[-1], 'x_median': xs[len(xs) // 2],
        'r_values': list(set(c['r'] for c in configs)),
        'vol_min_values': list(set(c['vol_min'] for c in configs)),
        'vol_max_values': list(set(c['vol_max'] for c in configs)),
    }


def sample_from_region(region):
    """Sample within detected robust region."""
    return {
        'h': random.randint(region['h_min'], region['h_max']),
        'x': random.randint(region['x_min'], region['x_max']),
        'r': random.choice(region['r_values']),
        'vol_min': random.choice(region['vol_min_values']),
        'vol_max': random.choice(region['vol_max_values']),
        'smoothing': True,
    }


def select_paper_candidates(all_results, min_pf=1.3):
    """Auto-select configs ready for paper trading."""
    candidates = []
    for params, score, balanced, results_dict, conf in all_results:
        if balanced <= 0:
            continue
        pfs = [r.get('profit_factor', 0) for r in results_dict.values()]
        if all(pf >= min_pf for pf in pfs):
            candidates.append((params, score, balanced, results_dict, conf))
    candidates.sort(key=lambda x: x[2], reverse=True)
    return candidates[:3]


def smart_search(time_budget, param_space, assets, asset_data, run_single_bt,
                 upload_url=None, upload_key=None, upload_batch_size=20, author=None):
    """
    3-phase intelligent search: Explore -> Exploit -> Refine.

    run_single_bt(params, symbol) -> metrics dict
    """
    start_time = time.time()
    # (params, score, balanced_score, results_dict, confidence)
    all_results = []
    upload_buffer = []
    upload_sent = 0
    upload_failed = 0

    # --- Global Memory: load history ---
    tested_hashes = load_tested_hashes()
    alltime_history = load_alltime_results()
    warm_count = 0

    # Warm start: seed with valid configs from history
    for entry in alltime_history:
        p = entry['params']
        h = config_hash(p)
        if h not in tested_hashes:
            tested_hashes.add(h)
        # Add as known result (no results_dict — won't be saved to CSV again)
        all_results.append((p, entry['score'], entry['balanced_score'], {}, entry.get('confidence', 0)))
        warm_count += 1

    print(f'  Loaded {len(tested_hashes)} previously tested configs')
    print(f'  Warm start with {warm_count} valid configs from history')

    phase_times = {
        'explore': 0.20 * time_budget,
        'exploit': 0.60 * time_budget,
        'refine': 0.20 * time_budget,
    }

    skipped = 0
    early_stopped = 0
    total_experiments = 0

    def run_with_early_stop(params):
        """Test assets sequentially with early stopping."""
        nonlocal early_stopped
        results = {}
        for i, asset in enumerate(assets):
            m = run_single_bt(params, asset)
            results[asset] = m

            pf = min(m.get('profit_factor', 0), 10.0)
            m['profit_factor'] = pf  # store capped value for scoring
            trades = m.get('total_trades', 0)

            if trades < 30:
                early_stopped += 1
                return results, 0.0, 0.0, 0.0

            if i == 0 and pf < 1.0:
                early_stopped += 1
                return results, 0.0, 0.0, 0.0

            if i == 1:
                min_pf = min(r.get('profit_factor', 0) for r in results.values())
                if min_pf < 1.1:
                    early_stopped += 1
                    return results, 0.0, 0.0, 0.0

        score = calculate_score(results)
        balanced = calculate_balanced_score(results)
        conf = confidence_score(results)
        return results, score, balanced, conf

    def try_config(params, phase_label, count):
        """Try a config, skip duplicates, run backtest."""
        nonlocal skipped, total_experiments, upload_buffer, upload_sent, upload_failed

        if params['vol_min'] >= params['vol_max']:
            return None

        h = config_hash(params)
        if h in tested_hashes:
            skipped += 1
            return None
        tested_hashes.add(h)

        results, score, balanced, conf = run_with_early_stop(params)
        total_experiments += 1
        all_results.append((params, score, balanced, results, conf))

        # Upload buffer
        if score > 0 and results and upload_url:
            upload_buffer.append((params, score, balanced, results, conf))
            if len(upload_buffer) >= upload_batch_size:
                ok = upload_results(upload_buffer, upload_url, upload_key, author)
                if ok:
                    upload_sent += len(upload_buffer)
                else:
                    upload_failed += len(upload_buffer)
                upload_buffer = []

        elapsed = time.time() - start_time
        marker = ''
        if balanced > 0 and len(all_results) > 1:
            best_bs = max(r[2] for r in all_results)
            if balanced >= best_bs:
                marker = ' * NEW BEST'
        print(f'  [{phase_label} {count}] h={params["h"]}, x={params["x"]}, '
              f'r={params["r"]}, vol={params["vol_min"]}/{params["vol_max"]} '
              f'-> score={score:.4f}, bscore={balanced:.4f}, conf={conf:.1f} '
              f'({elapsed:.0f}s / {time_budget}s){marker}')

        return (params, score, balanced, results, conf)

    # ============ PHASE 1: EXPLORE ============
    print(f'\n{"=" * 60}')
    print(f'  PHASE 1: EXPLORATION ({int(phase_times["explore"])}s)')
    print(f'{"=" * 60}')

    explore_end = start_time + phase_times['explore']
    explore_count = 0

    while time.time() < explore_end:
        params = random_sample(param_space)
        result = try_config(params, 'EXPLORE', explore_count + 1)
        if result is not None:
            explore_count += 1

    all_results.sort(key=lambda x: x[2], reverse=True)
    best_bs = all_results[0][2] if all_results else 0
    print(f'\n  Phase 1 done: {explore_count} experiments, best bscore={best_bs:.4f}')

    # Detect region from exploration results
    region = detect_region(all_results)
    if region:
        print(f'  Detected region: h=[{region["h_min"]}-{region["h_max"]}], '
              f'x=[{region["x_min"]}-{region["x_max"]}], '
              f'r={region["r_values"]}')

    # ============ PHASE 2: EXPLOIT ============
    print(f'\n{"=" * 60}')
    print(f'  PHASE 2: EXPLOITATION ({int(phase_times["exploit"])}s)')
    print(f'{"=" * 60}')

    exploit_end = start_time + phase_times['explore'] + phase_times['exploit']
    exploit_count = 0

    top_configs = [r[0] for r in all_results[:10] if r[2] > 0]

    while time.time() < exploit_end:
        if not top_configs:
            # No valid configs yet — keep exploring
            params = random_sample(param_space)
        elif region and random.random() < 0.7:
            # 70% sample from detected region
            params = sample_from_region(region)
        else:
            # 30% mutate from top configs
            parent = random.choice(top_configs)
            params = mutate(parent, magnitude='medium')

        result = try_config(params, 'EXPLOIT', exploit_count + 1)
        if result is not None:
            exploit_count += 1
            # Update top configs periodically
            if exploit_count % 20 == 0:
                all_results.sort(key=lambda x: x[2], reverse=True)
                top_configs = [r[0] for r in all_results[:10] if r[2] > 0]
                region = detect_region(all_results) or region

    all_results.sort(key=lambda x: x[2], reverse=True)
    best_bs = all_results[0][2] if all_results else 0
    print(f'\n  Phase 2 done: {exploit_count} experiments, best bscore={best_bs:.4f}')

    # ============ PHASE 3: REFINE ============
    print(f'\n{"=" * 60}')
    print(f'  PHASE 3: REFINEMENT ({int(phase_times["refine"])}s)')
    print(f'{"=" * 60}')

    refine_end = start_time + time_budget
    refine_count = 0

    top3 = [r[0] for r in all_results[:3] if r[2] > 0]

    while time.time() < refine_end and top3:
        parent = random.choice(top3)
        child = mutate(parent, magnitude='small')

        if child['vol_min'] >= child['vol_max']:
            continue

        h = config_hash(child)
        if h in tested_hashes:
            skipped += 1
            continue
        tested_hashes.add(h)

        # Full test (no early stopping in refine)
        results = {}
        for asset in assets:
            results[asset] = run_single_bt(child, asset)
        score = calculate_score(results)
        balanced = calculate_balanced_score(results)
        conf = confidence_score(results)
        all_results.append((child, score, balanced, results, conf))
        total_experiments += 1
        refine_count += 1

        # Upload buffer
        if score > 0 and upload_url:
            upload_buffer.append((child, score, balanced, results, conf))
            if len(upload_buffer) >= upload_batch_size:
                ok = upload_results(upload_buffer, upload_url, upload_key, author)
                if ok:
                    upload_sent += len(upload_buffer)
                else:
                    upload_failed += len(upload_buffer)
                upload_buffer = []

        all_results.sort(key=lambda x: x[2], reverse=True)

        elapsed = time.time() - start_time
        print(f'  [REFINE {refine_count}] h={child["h"]}, x={child["x"]}, '
              f'r={child["r"]} -> bscore={balanced:.4f}, conf={conf:.1f} '
              f'({elapsed:.0f}s / {time_budget}s)')

    elapsed_total = time.time() - start_time

    # ============ SUMMARY ============
    # Filter to only results with actual backtest data (not warm-start placeholders)
    new_results = [(p, s, b, r, c) for p, s, b, r, c in all_results if r]
    valid_new = [(p, s, b, r, c) for p, s, b, r, c in new_results if s > 0]

    print(f'\n{"=" * 60}')
    print(f'  SMART SEARCH SUMMARY')
    print(f'{"=" * 60}')
    print(f'  Time budget:       {time_budget}s ({fmt_time(time_budget)})')
    print(f'  Time used:         {elapsed_total:.0f}s ({fmt_time(elapsed_total)})')
    print(f'  Total experiments: {total_experiments}')
    print(f'    Phase 1 (explore): {explore_count} experiments')
    print(f'    Phase 2 (exploit): {exploit_count} experiments')
    print(f'    Phase 3 (refine):  {refine_count} experiments')
    print(f'  Skipped (duplicates): {skipped}')
    print(f'  Early stopped:     {early_stopped} ({early_stopped * 100 // max(1, total_experiments)}%)')
    print(f'  Valid configs:     {len(valid_new)}')
    print()

    # TOP 5 by balanced score
    top5 = sorted(new_results, key=lambda x: x[2], reverse=True)[:5]
    if top5 and top5[0][2] > 0:
        print(f'  === TOP 5 BY BALANCED SCORE ===')
        for i, (p, s, b, r, c) in enumerate(top5, 1):
            if b <= 0:
                break
            asset_strs = []
            for sym, m in r.items():
                short = sym.replace('USDT', '')
                asset_strs.append(f'{short}: PF={m.get("profit_factor", 0):.2f}, '
                                  f'{m.get("net_profit_pct", 0):+.0f}%')
            print(f'  #{i} h={p["h"]}, x={p["x"]}, r={p["r"]}, '
                  f'vol={p["vol_min"]}/{p["vol_max"]} -> '
                  f'BScore={b:.4f}, Conf={c:.1f}')
            print(f'     {" | ".join(asset_strs)}')
        print()

    # TOP 5 by score
    top5_score = sorted(new_results, key=lambda x: x[1], reverse=True)[:5]
    if top5_score and top5_score[0][1] > 0:
        print(f'  === TOP 5 BY SCORE (best edge) ===')
        for i, (p, s, b, r, c) in enumerate(top5_score, 1):
            if s <= 0:
                break
            asset_strs = []
            for sym, m in r.items():
                short = sym.replace('USDT', '')
                asset_strs.append(f'{short}: PF={m.get("profit_factor", 0):.2f}, '
                                  f'{m.get("net_profit_pct", 0):+.0f}%')
            print(f'  #{i} h={p["h"]}, x={p["x"]}, r={p["r"]}, '
                  f'vol={p["vol_min"]}/{p["vol_max"]} -> '
                  f'Score={s:.4f}, Conf={c:.1f}')
            print(f'     {" | ".join(asset_strs)}')
        print()

    # Robust Region
    region = detect_region(sorted(new_results, key=lambda x: x[2], reverse=True))
    if region:
        print(f'  === ROBUST REGION (where most TOP configs cluster) ===')
        print(f'  h:       {region["h_min"]}-{region["h_max"]} (median: {region["h_median"]})')
        print(f'  x:       {region["x_min"]}-{region["x_max"]} (median: {region["x_median"]})')
        print(f'  r:       {sorted(region["r_values"])}')
        print(f'  vol:     {sorted(region["vol_min_values"])}/{sorted(region["vol_max_values"])} dominant')
        print()

    # Paper Trading Candidates
    candidates = select_paper_candidates(new_results)
    if candidates:
        print(f'  === PAPER TRADING CANDIDATES (auto-selected) ===')
        print(f'  All assets PF >= 1.3, sorted by Balanced Score')
        print()
        for i, (p, s, b, r, c) in enumerate(candidates, 1):
            asset_strs = []
            for sym, m in r.items():
                short = sym.replace('USDT', '')
                asset_strs.append(f'{short}: PF={m.get("profit_factor", 0):.2f}, '
                                  f'{m.get("net_profit_pct", 0):+.0f}%')
            print(f'  #{i} h={p["h"]}, x={p["x"]}, r={p["r"]}, '
                  f'vol={p["vol_min"]}/{p["vol_max"]} -> '
                  f'BScore={b:.4f}, Conf={c:.1f}')
            print(f'     {" | ".join(asset_strs)}')
        print()

    # Flush remaining upload buffer
    if upload_buffer and upload_url:
        ok = upload_results(upload_buffer, upload_url, upload_key, author)
        if ok:
            upload_sent += len(upload_buffer)
        else:
            upload_failed += len(upload_buffer)

    # Upload summary
    if upload_url:
        print(f'  === UPLOAD SUMMARY ===')
        print(f'  Server: {upload_url}')
        print(f'  Uploaded: {upload_sent} results')
        print(f'  Failed uploads: {upload_failed}')
        print(f'  Author: {author or os.getenv("USERNAME", os.getenv("USER", "unknown"))}')
        print()

    return new_results, elapsed_total


def worker_loop(param_space, assets, asset_data, run_single_bt, upload_url, upload_key,
                sync_interval, author=None):
    """
    Worker mode: infinite loop that samples random configs, tests them, and uploads immediately.
    Re-syncs tested hashes from server every sync_interval experiments to avoid duplication.
    """
    tested_hashes = set()
    experiments_since_sync = 0
    total_experiments = 0
    skipped = 0
    uploaded = 0
    upload_failed = 0
    
    author_name = author or os.getenv('USERNAME', os.getenv('USER', 'unknown'))
    
    print(f'\n{"=" * 60}')
    print(f'  WORKER MODE (infinite loop)')
    print(f'{"=" * 60}')
    print(f'  Upload URL:    {upload_url}')
    print(f'  Author:        {author_name}')
    print(f'  Sync interval: every {sync_interval} experiments')
    print(f'  Assets:        {", ".join(assets)}')
    print(f'{"=" * 60}\n')
    
    # Initial sync
    print('  [SYNC] Fetching tested hashes from server...')
    tested_hashes = fetch_server_hashes(upload_url)
    print(f'  [SYNC] Loaded {len(tested_hashes)} previously tested configs\n')
    
    while True:
        try:
            # Re-sync from server periodically
            if experiments_since_sync >= sync_interval:
                print(f'\n  [SYNC] Re-fetching tested hashes from server...')
                server_hashes = fetch_server_hashes(upload_url)
                new_count = len(server_hashes - tested_hashes)
                tested_hashes = server_hashes
                experiments_since_sync = 0
                print(f'  [SYNC] Now tracking {len(tested_hashes)} tested configs ({new_count} new from server)\n')
            
            # Sample random config
            params = random_sample(param_space)
            
            # Skip if already tested
            h = config_hash(params)
            if h in tested_hashes:
                skipped += 1
                continue
            tested_hashes.add(h)
            
            # Run backtest on all assets
            results = {}
            for asset in assets:
                results[asset] = run_single_bt(params, asset)
            
            score = calculate_score(results)
            balanced = calculate_balanced_score(results)
            conf = confidence_score(results)
            
            total_experiments += 1
            experiments_since_sync += 1
            
            # Upload immediately if valid
            if score > 0 and upload_url:
                ok = upload_results([(params, score, balanced, results, conf)],
                                    upload_url, upload_key, author)
                if ok:
                    uploaded += 1
                else:
                    upload_failed += 1
            
            # Progress log
            marker = ' ✓' if score > 0 else ''
            print(f'  [EXP {total_experiments}] h={params["h"]}, x={params["x"]}, '
                  f'r={params["r"]}, vol={params["vol_min"]}/{params["vol_max"]} '
                  f'-> score={score:.4f}, bscore={balanced:.4f}, conf={conf:.1f} '
                  f'(uploaded: {uploaded}, failed: {upload_failed}, skipped: {skipped}){marker}')
        
        except KeyboardInterrupt:
            print(f'\n\n[WORKER] Stopped by user. Total experiments: {total_experiments}, uploaded: {uploaded}')
            break
        except Exception as e:
            print(f'  [WORKER] Error: {e} — continuing...')
            time.sleep(5)


# ---------------------------------------------------------------------------
# Walk-Forward Validation
# ---------------------------------------------------------------------------

WALKFORWARD_CSV = 'autoresearch_walkforward.csv'
WALKFORWARD_FIELDS = [
    'h', 'x', 'r', 'vol_min', 'vol_max', 'smoothing',
    'atr_period', 'atr_multiplier', 'reentry_delay',
    'pf_train_avg', 'pf_test_avg', 'pf_stability',
    'folds_positive', 'total_folds', 'worst_fold_pf',
    'test_trades_total', 'wf_score', 'wf_balanced_score', 'status',
]

# Approximate bars per month for 1H timeframe
BARS_PER_MONTH_1H = 720  # 30 days * 24 hours


def generate_walkforward_splits(total_bars, train_start_idx, wf_folds=3,
                                wf_test_months=3, buffer_size=120):
    """Generate anchored expanding window splits for walk-forward validation.

    All folds share the same anchor (start of data). Training window expands
    each fold, test window is fixed length.

    Returns list of dicts: {'fold': int, 'train_start': int, 'train_end': int,
                             'test_start': int, 'test_end': int}
    """
    test_bars = wf_test_months * BARS_PER_MONTH_1H
    # Initial train size: enough for first fold
    # Fold 1 train = [train_start_idx ... first_train_end]
    # We need: train_start_idx + initial_train + buffer + test <= total_bars (for last fold)
    # Work backwards from total_bars to determine initial_train
    total_needed_after_anchor = (wf_folds * test_bars) + ((wf_folds) * buffer_size)
    initial_train_end = total_bars - total_needed_after_anchor

    if initial_train_end <= train_start_idx + BARS_PER_MONTH_1H:
        # Not enough data — fall back to equal splits
        available = total_bars - train_start_idx
        chunk = available // (wf_folds + 1)
        initial_train_end = train_start_idx + chunk

    splits = []
    for fold in range(wf_folds):
        train_end = initial_train_end + fold * (test_bars + buffer_size)
        test_start = train_end + buffer_size
        test_end = min(test_start + test_bars, total_bars)

        if test_end <= test_start or train_end <= train_start_idx:
            break

        splits.append({
            'fold': fold + 1,
            'train_start': train_start_idx,
            'train_end': train_end,
            'test_start': test_start,
            'test_end': test_end,
        })

    return splits


def run_walkforward_for_config(config, asset_data, assets, splits,
                               apply_config_fn, run_backtest_fn,
                               calculate_metrics_fn, early_stop=True):
    """Run walk-forward validation for a single config across all folds/assets.

    Returns metrics dict with per-fold breakdowns and aggregate scores.
    """
    all_train_pfs = []
    all_test_pfs = []
    all_test_trades = 0
    per_fold = []
    per_asset_test_profits = {asset: [] for asset in assets}
    folds_positive = 0

    for split in splits:
        fold_data = {'fold_id': split['fold']}
        fold_test_pfs = []
        fold_has_reject = False

        for symbol in assets:
            klines, _ = asset_data[symbol]

            # Slice data for train and test windows
            train_klines = klines[split['train_start']:split['train_end']]
            test_klines = klines[split['test_start']:split['test_end']]

            if len(train_klines) < 200 or len(test_klines) < 50:
                s = symbol.replace('USDT', '').lower()
                fold_data[symbol] = {'pf_train': 0, 'pf_test': 0, 'trades_test': 0}
                fold_has_reject = True
                continue

            # Build args
            combo = {
                'lookback_window': config['h'],
                'regression_level': config['x'],
                'use_kernel_smoothing': config.get('smoothing', True),
                'relative_weight': config.get('r', DEFAULTS['relative_weight']),
                'lag': config.get('lag', DEFAULTS['lag']),
                'atr_period': config.get('atr_period', DEFAULTS['atr_period']),
                'atr_multiplier': config.get('atr_multiplier', DEFAULTS['atr_multiplier']),
                'volatility_min': config.get('vol_min', DEFAULTS['volatility_min']),
                'volatility_max': config.get('vol_max', DEFAULTS['volatility_max']),
                're_entry_delay': config.get('reentry_delay', DEFAULTS['re_entry_delay']),
            }
            args = make_args(symbol, combo)
            apply_config_fn(args)

            # Run train backtest — warmup = first 100 bars of train window
            train_warmup = min(100, len(train_klines) // 3)
            trades_train, _, fb_train = run_backtest_fn(train_klines, args, train_warmup)
            m_train = calculate_metrics_fn(trades_train, args.capital, fb_train)

            # Run test backtest — prepend warmup bars from TRAINING data
            # (before train_end) to let kernel initialize. Warmup must NOT
            # enter the buffer zone between train and test windows.
            warmup_needed = max(config['h'] + 20, 120)
            warmup_start = max(0, split['train_end'] - warmup_needed)
            test_with_warmup = klines[warmup_start:split['test_end']]
            test_trading_start = split['test_start'] - warmup_start
            trades_test, _, fb_test = run_backtest_fn(test_with_warmup, args,
                                                       test_trading_start)
            m_test = calculate_metrics_fn(trades_test, args.capital, fb_test)

            pf_train = m_train.get('profit_factor', 0)
            pf_test = m_test.get('profit_factor', 0)
            trades_test_n = m_test.get('total_trades', 0)
            net_test = m_test.get('net_profit_pct', 0)

            # Cap infinite PF for averaging
            if pf_train == float('inf'):
                pf_train = 10.0
            if pf_test == float('inf'):
                pf_test = 10.0

            all_train_pfs.append(pf_train)
            all_test_pfs.append(pf_test)
            all_test_trades += trades_test_n
            fold_test_pfs.append(pf_test)
            per_asset_test_profits[symbol].append(net_test)

            fold_data[symbol] = {
                'pf_train': round(pf_train, 2),
                'pf_test': round(pf_test, 2),
                'trades_test': trades_test_n,
            }

        per_fold.append(fold_data)

        # Fold positive = mean PF_test > 1.0 AND min PF_test > 0.8
        if fold_test_pfs and not fold_has_reject:
            mean_pf = sum(fold_test_pfs) / len(fold_test_pfs)
            min_pf = min(fold_test_pfs)
            if mean_pf > 1.0 and min_pf > 0.8:
                folds_positive += 1

        # Early stopping: if 2 folds done and 0 positive → skip rest
        if early_stop and split['fold'] >= 2 and folds_positive == 0:
            break

    # Compute aggregate metrics
    pf_train_avg = sum(all_train_pfs) / len(all_train_pfs) if all_train_pfs else 0
    pf_test_avg = sum(all_test_pfs) / len(all_test_pfs) if all_test_pfs else 0
    pf_stability = (pf_test_avg / pf_train_avg) if pf_train_avg > 0 else 0
    worst_fold_pf = min(all_test_pfs) if all_test_pfs else 0

    # Per-asset average test profit
    avg_asset_profits = {}
    for asset in assets:
        profits = per_asset_test_profits[asset]
        avg_asset_profits[asset] = sum(profits) / len(profits) if profits else 0

    metrics = {
        'pf_train_avg': round(pf_train_avg, 4),
        'pf_test_avg': round(pf_test_avg, 4),
        'pf_stability': round(pf_stability, 4),
        'folds_positive': folds_positive,
        'total_folds': len(per_fold),
        'worst_fold_pf': round(worst_fold_pf, 4),
        'test_trades_total': all_test_trades,
        'per_asset_test_profits': avg_asset_profits,
        'per_fold': per_fold,
    }

    metrics['wf_score'] = compute_wf_score(metrics)
    metrics['wf_balanced_score'] = compute_wf_balanced_score(metrics)
    metrics['status'] = evaluate_wf_config(metrics)

    return metrics


def compute_wf_score(metrics):
    """Walk-forward score: rewards test performance, stability, and fold consistency."""
    pf_test = metrics.get('pf_test_avg', 0)
    stability = metrics.get('pf_stability', 0)
    folds_pos = metrics.get('folds_positive', 0)
    total_folds = metrics.get('total_folds', 1)

    if pf_test <= 0 or total_folds == 0:
        return 0.0

    wf_score = pf_test * stability * (folds_pos / total_folds)
    return round(wf_score, 4)


def compute_wf_balanced_score(metrics):
    """Cross-asset consistency in WF context."""
    profits = metrics.get('per_asset_test_profits', {})
    stability = metrics.get('pf_stability', 0)

    if not profits:
        return 0.0

    profit_values = list(profits.values())
    if any(p <= 0 for p in profit_values):
        return 0.0

    min_profit = min(profit_values)
    avg_profit = sum(profit_values) / len(profit_values)

    if avg_profit <= 0:
        return 0.0

    std_dev = (sum((p - avg_profit) ** 2 for p in profit_values) / len(profit_values)) ** 0.5
    cv = std_dev / avg_profit
    balance_factor = max(0, 1 - cv)

    return round(min_profit * balance_factor * stability / 100, 4)


def evaluate_wf_config(metrics):
    """Classify WF result: VALIDATED, REJECT, OVERFIT, UNSTABLE, RISKY, INSUFFICIENT_DATA."""
    if metrics.get('test_trades_total', 0) < 20:
        return 'INSUFFICIENT_DATA'
    if metrics.get('pf_test_avg', 0) < 1.1:
        return 'REJECT'
    if metrics.get('pf_stability', 0) < 0.4:
        return 'OVERFIT'
    if metrics.get('folds_positive', 0) < 2:
        return 'UNSTABLE'
    if metrics.get('worst_fold_pf', 0) < 0.7:
        return 'RISKY'
    return 'VALIDATED'


def load_top_configs_from_csv(csv_path, top_n=50):
    """Load top N configs from alltime CSV, sorted by score descending."""
    if not os.path.exists(csv_path):
        print(f'  [ERROR] File not found: {csv_path}')
        return []

    configs = []
    seen_hashes = set()

    try:
        with open(csv_path, 'r') as f:
            reader = csv.DictReader(f)
            for row in reader:
                score = float(row.get('score', 0))
                if score <= 0:
                    continue

                cfg = {
                    'h': int(row.get('lookback', row.get('lookback_window', 88))),
                    'x': int(row.get('regression', row.get('regression_level', 71))),
                    'r': float(row.get('relative_weight', 10.0)),
                    'smoothing': str(row.get('smoothing', 'True')).lower() in ('true', '1', 'on'),
                    'vol_min': int(row.get('vol_min', row.get('volatility_min', 5))),
                    'vol_max': int(row.get('vol_max', row.get('volatility_max', 10))),
                    'atr_period': int(row.get('atr_period', 20)),
                    'atr_multiplier': float(row.get('atr_multiplier', 6.0)),
                    'reentry_delay': int(row.get('reentry_delay', row.get('re_entry_delay', 1))),
                    'lag': int(row.get('lag', 1)),
                    'score': score,
                }

                # Deduplicate by key params
                key = f"h{cfg['h']}_x{cfg['x']}_r{cfg['r']}_vm{cfg['vol_min']}_vx{cfg['vol_max']}"
                if key in seen_hashes:
                    continue
                seen_hashes.add(key)

                configs.append(cfg)
    except Exception as e:
        print(f'  [ERROR] Failed to read {csv_path}: {e}')
        return []

    configs.sort(key=lambda c: c['score'], reverse=True)
    return configs[:top_n]


def run_walkforward_mode(cli, assets, asset_data, apply_config_fn,
                         run_backtest_fn, calculate_metrics_fn):
    """Main Walk-Forward Validation mode."""
    print()
    print('=' * 70)
    print('  WALK-FORWARD VALIDATION')
    print('=' * 70)

    # --- Phase 1: Load top configs ---
    if cli.phase2_only:
        print(f'  Phase 1: Using top {cli.wf_top_n} from {cli.wf_input}')
        configs = load_top_configs_from_csv(cli.wf_input, cli.wf_top_n)
    else:
        print(f'  Phase 1: Using top {cli.wf_top_n} from {ALLTIME_CSV}')
        configs = load_top_configs_from_csv(ALLTIME_CSV, cli.wf_top_n)

    if not configs:
        print('  [ERROR] No configs to validate. Run autoresearch first.')
        return

    n_configs = len(configs)

    # --- Generate fold structure ---
    # Use first asset's data length as reference
    first_asset = assets[0]
    ref_klines, ref_tsi = asset_data[first_asset]
    buffer_size = max(configs[0]['h'], 120) if configs else 120

    splits = generate_walkforward_splits(
        total_bars=len(ref_klines),
        train_start_idx=ref_tsi,
        wf_folds=cli.wf_folds,
        wf_test_months=cli.wf_test_months,
        buffer_size=buffer_size,
    )

    if not splits:
        print('  [ERROR] Not enough data for walk-forward splits.')
        return

    total_backtests = n_configs * len(splits) * len(assets)
    print(f'  Phase 2: {len(splits)} folds x {len(assets)} assets x {n_configs} configs = {total_backtests} backtests')
    print()

    # Print fold structure
    print('  Fold structure:')
    for sp in splits:
        # Convert bar indices to approximate dates
        train_s = ref_klines[sp['train_start']]['open_time']
        train_e = ref_klines[min(sp['train_end'] - 1, len(ref_klines) - 1)]['open_time']
        test_s = ref_klines[min(sp['test_start'], len(ref_klines) - 1)]['open_time']
        test_e = ref_klines[min(sp['test_end'] - 1, len(ref_klines) - 1)]['open_time']

        def ms_to_date(ms):
            return datetime.fromtimestamp(ms / 1000, tz=timezone.utc).strftime('%b %Y')

        train_bars = sp['train_end'] - sp['train_start']
        test_bars = sp['test_end'] - sp['test_start']
        print(f'    Fold {sp["fold"]}: train [{ms_to_date(train_s)} - {ms_to_date(train_e)}] '
              f'({train_bars} bars), '
              f'test [{ms_to_date(test_s)} - {ms_to_date(test_e)}] '
              f'({test_bars} bars), buffer={buffer_size} bars')
    print()

    # --- Phase 2: Run WF for each config ---
    t_start = time.time()
    all_wf_results = []
    status_counts = {}

    for idx, config in enumerate(configs, 1):
        h = config['h']
        x = config['x']
        r = config['r']
        vol_min = config['vol_min']
        vol_max = config['vol_max']

        print(f'[WF {idx}/{n_configs}] h={h}, x={x}, r={r}, vol={vol_min}/{vol_max}')

        metrics = run_walkforward_for_config(
            config, asset_data, assets, splits,
            apply_config_fn, run_backtest_fn, calculate_metrics_fn,
            early_stop=True,
        )

        # Print per-fold details
        for fold_info in metrics['per_fold']:
            parts = []
            for symbol in assets:
                if symbol in fold_info:
                    d = fold_info[symbol]
                    short = symbol.replace('USDT', '')
                    parts.append(f'{short} pf_train={d["pf_train"]:.2f} pf_test={d["pf_test"]:.2f}')
            print(f'  Fold {fold_info["fold_id"]}: {" | ".join(parts)}')

        status = metrics['status']
        status_counts[status] = status_counts.get(status, 0) + 1

        print(f'  -> PF test avg={metrics["pf_test_avg"]:.2f}, '
              f'stability={metrics["pf_stability"]:.2f}, '
              f'folds+={metrics["folds_positive"]}/{metrics["total_folds"]}, '
              f'status={status}, '
              f'WF score={metrics["wf_score"]:.4f}')
        print()

        all_wf_results.append((config, metrics))

    elapsed = time.time() - t_start

    # --- Save results to CSV ---
    with open(WALKFORWARD_CSV, 'w', newline='') as f:
        writer = csv.DictWriter(f, fieldnames=WALKFORWARD_FIELDS)
        writer.writeheader()
        for config, metrics in all_wf_results:
            writer.writerow({
                'h': config['h'],
                'x': config['x'],
                'r': config['r'],
                'vol_min': config['vol_min'],
                'vol_max': config['vol_max'],
                'smoothing': config.get('smoothing', True),
                'atr_period': config.get('atr_period', 20),
                'atr_multiplier': config.get('atr_multiplier', 6.0),
                'reentry_delay': config.get('reentry_delay', 1),
                'pf_train_avg': metrics['pf_train_avg'],
                'pf_test_avg': metrics['pf_test_avg'],
                'pf_stability': metrics['pf_stability'],
                'folds_positive': metrics['folds_positive'],
                'total_folds': metrics['total_folds'],
                'worst_fold_pf': metrics['worst_fold_pf'],
                'test_trades_total': metrics['test_trades_total'],
                'wf_score': metrics['wf_score'],
                'wf_balanced_score': metrics['wf_balanced_score'],
                'status': metrics['status'],
            })
    print(f'Results saved to {WALKFORWARD_CSV}')
    print()

    # --- Summary ---
    print('=' * 70)
    print('  WALK-FORWARD RESULTS')
    print('=' * 70)
    print(f'  Tested: {n_configs} configs')
    print(f'  Duration: {fmt_time(elapsed)}')
    for status_name in ['VALIDATED', 'REJECT', 'OVERFIT', 'UNSTABLE', 'RISKY', 'INSUFFICIENT_DATA']:
        count = status_counts.get(status_name, 0)
        print(f'  {status_name + ":":<22s} {count}')
    print()

    # --- TOP 10 by WF Score ---
    validated = [(c, m) for c, m in all_wf_results if m['wf_score'] > 0]
    validated.sort(key=lambda x: x[1]['wf_score'], reverse=True)
    top_n = min(10, len(validated))

    if top_n > 0:
        print(f'  === TOP {top_n} BY WF SCORE ===')
        print(f'  {"#":<4}| {"h":<4}| {"x":<4}| {"r":<5}| {"Vol":<6}| '
              f'{"WF Score":<10}| {"PF Train":<10}| {"PF Test":<9}| '
              f'{"Stability":<11}| {"Folds+":<8}| {"Status":<20}')
        print('  ' + '-' * 95)
        for i, (c, m) in enumerate(validated[:top_n], 1):
            print(f'  {i:<4}| {c["h"]:<4}| {c["x"]:<4}| {c["r"]:<5}| '
                  f'{c["vol_min"]}/{c["vol_max"]:<4}| '
                  f'{m["wf_score"]:<10.4f}| {m["pf_train_avg"]:<10.2f}| '
                  f'{m["pf_test_avg"]:<9.2f}| {m["pf_stability"]:<11.2f}| '
                  f'{m["folds_positive"]}/{m["total_folds"]:<6}| {m["status"]:<20}')
        print()

    # --- TOP 10 by WF Balanced Score ---
    balanced = [(c, m) for c, m in all_wf_results if m['wf_balanced_score'] > 0]
    balanced.sort(key=lambda x: x[1]['wf_balanced_score'], reverse=True)
    top_b = min(10, len(balanced))

    if top_b > 0:
        print(f'  === TOP {top_b} BY WF BALANCED SCORE ===')
        print(f'  {"#":<4}| {"h":<4}| {"x":<4}| {"r":<5}| {"Vol":<6}| '
              f'{"WF BScore":<11}| {"PF Test":<9}| {"Stability":<11}| {"Status":<20}')
        print('  ' + '-' * 80)
        for i, (c, m) in enumerate(balanced[:top_b], 1):
            print(f'  {i:<4}| {c["h"]:<4}| {c["x"]:<4}| {c["r"]:<5}| '
                  f'{c["vol_min"]}/{c["vol_max"]:<4}| '
                  f'{m["wf_balanced_score"]:<11.4f}| {m["pf_test_avg"]:<9.2f}| '
                  f'{m["pf_stability"]:<11.2f}| {m["status"]:<20}')
        print()


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    from backtest import fetch_historical_klines, apply_config, run_backtest, calculate_metrics

    cli = parse_args()

    # Use custom assets if provided, otherwise use defaults
    ASSETS = cli.assets if cli.assets else DEFAULT_ASSETS

    combos, iterated, fixed = build_grid(cli)
    total_combos = len(combos)
    total_runs = total_combos * len(ASSETS)
    est_secs = total_combos * SECS_PER_COMBO

    # Show all-time best on startup
    alltime_best = load_alltime_best()

    print('=' * 70)
    print('  AUTORESEARCH — Kernel Parameter Optimization')
    print('=' * 70)
    if alltime_best:
        print(f'  All-time best: h={alltime_best["lookback"]}, x={alltime_best["regression"]}, '
              f'score={float(alltime_best["score"]):.4f} (from {alltime_best["run_date"]})')
    else:
        print('  All-time best: no previous runs')
    print()

    # Show what's being iterated vs fixed
    print('  === SWEEP CONFIG ===')
    if iterated:
        parts = []
        for name, vals in iterated.items():
            if name == 'lookback_window':
                parts.append(f'h=[{min(vals)}-{max(vals)}/{cli.h_step}]')
            elif name == 'regression_level':
                parts.append(f'x=[{min(vals)}-{max(vals)}/{cli.x_step}]')
            elif name == 'use_kernel_smoothing':
                parts.append(f'smooth=[On,Off]')
            else:
                short = name.replace('relative_weight', 'r').replace('atr_multiplier', 'atr_mult') \
                             .replace('atr_period', 'atr_per').replace('volatility_min', 'vol_min') \
                             .replace('volatility_max', 'vol_max').replace('re_entry_delay', 'reentry')
                parts.append(f'{short}=[{",".join(str(v) for v in vals)}]')
        print(f'  Iterating: {", ".join(parts)}')
    else:
        print('  Iterating: (single config)')

    fixed_parts = []
    sample = combos[0]  # use first combo to show fixed values
    param_display = [
        ('lookback_window', 'h'), ('regression_level', 'x'),
        ('use_kernel_smoothing', 'smooth'), ('relative_weight', 'r'),
        ('lag', 'lag'), ('atr_period', 'atr_per'), ('atr_multiplier', 'atr_mult'),
        ('volatility_min', 'vol_min'), ('volatility_max', 'vol_max'),
        ('re_entry_delay', 'reentry'),
    ]
    for param_name, short in param_display:
        if param_name not in iterated:
            val = fixed.get(param_name, sample.get(param_name, DEFAULTS.get(param_name, '?')))
            if param_name == 'use_kernel_smoothing':
                val = smooth_label(val)
            fixed_parts.append(f'{short}={val}')
    if fixed_parts:
        print(f'  Fixed: {", ".join(fixed_parts)}')
    print(f'  SL: ATR, commission={DEFAULTS["commission"]}%, trailing={DEFAULTS["trailing_mode"]}')
    print()

    print(f'  Combinations: {total_combos} x {len(ASSETS)} assets = {total_runs} backtests')
    print(f'  Estimated time: ~{fmt_time(est_secs)}')
    print(f'  Assets: {", ".join(ASSETS)}')
    print('=' * 70)
    print()

    # --- Step 1: Pre-fetch all data ---
    client = Client('', '', testnet=False)

    if cli.walkforward:
        # WF needs more data: from Oct 2024 (warmup) to now
        wf_data_start = datetime(2024, 7, 1, tzinfo=timezone.utc)  # extra warmup
        wf_data_end = datetime.now(timezone.utc)
        start_date = datetime(2024, 10, 1, tzinfo=timezone.utc)
    else:
        start_date = datetime.strptime(DEFAULTS['start'], '%Y-%m-%d').replace(tzinfo=timezone.utc)
        wf_data_end = datetime.strptime(DEFAULTS['end'], '%Y-%m-%d').replace(tzinfo=timezone.utc)
        wf_data_start = start_date - timedelta(days=92)

    end_date = wf_data_end
    fetch_start = wf_data_start

    asset_data = {}  # symbol -> (klines, trading_start_idx)
    for symbol in ASSETS:
        print(f'  Fetching {symbol}...')
        klines = fetch_historical_klines(
            client, symbol, DEFAULTS['timeframe'],
            fetch_start, end_date,
            use_cache=(not cli.walkforward),  # skip cache for WF (need full range)
        )
        start_ms = int(start_date.timestamp() * 1000)
        trading_start_idx = 0
        for idx, k in enumerate(klines):
            if k['open_time'] >= start_ms:
                trading_start_idx = idx
                break
        asset_data[symbol] = (klines, trading_start_idx)
        print(f'    {symbol}: {len(klines)} candles, warmup={trading_start_idx} bars')

    print()

    # --- Walk-Forward mode dispatch ---
    if cli.walkforward:
        run_walkforward_mode(
            cli, ASSETS, asset_data, apply_config, run_backtest, calculate_metrics,
        )
        return

    # --- Smart/Worker mode dispatch ---
    if cli.mode in ('smart', 'worker'):
        # Seed RNG for reproducibility
        seed = cli.seed if cli.seed is not None else random.randint(0, 2**32 - 1)
        random.seed(seed)
        print(f'  Random seed: {seed} (pass --seed {seed} to reproduce)')
        print()
        
        def run_single_bt(params, symbol):
            """Run a single backtest for smart/worker mode."""
            combo = {
                'lookback_window': params['h'],
                'regression_level': params['x'],
                'use_kernel_smoothing': params.get('smoothing', True),
                'relative_weight': params.get('r', DEFAULTS['relative_weight']),
                'lag': DEFAULTS['lag'],
                'atr_period': DEFAULTS['atr_period'],
                'atr_multiplier': DEFAULTS['atr_multiplier'],
                'volatility_min': params.get('vol_min', DEFAULTS['volatility_min']),
                'volatility_max': params.get('vol_max', DEFAULTS['volatility_max']),
                're_entry_delay': DEFAULTS['re_entry_delay'],
            }
            args = make_args(symbol, combo)
            apply_config(args)
            klines, tsi = asset_data[symbol]
            trades, _, final_balance = run_backtest(klines, args, tsi)
            return calculate_metrics(trades, args.capital, final_balance)
    
    if cli.mode == 'worker':
        # Worker mode requires upload URL
        if not cli.upload_url:
            print('[ERROR] Worker mode requires --upload-url')
            sys.exit(1)
        
        worker_loop(
            PARAM_SPACE, ASSETS, asset_data, run_single_bt,
            upload_url=cli.upload_url, upload_key=cli.upload_key,
            sync_interval=cli.sync_interval, author=cli.author,
        )
        return
    
    if cli.mode == 'smart':
        print('=' * 60)
        print(f'  MODE: SMART SEARCH ({fmt_time(cli.time_budget)} budget)')
        print('=' * 60)

        smart_results, elapsed_total = smart_search(
            cli.time_budget, PARAM_SPACE, ASSETS, asset_data, run_single_bt,
            upload_url=cli.upload_url, upload_key=cli.upload_key,
            upload_batch_size=cli.upload_batch_size, author=cli.author,
        )

        # Save to CSV (same format as grid)
        csv_path = 'autoresearch_results.csv'
        fieldnames = [
            'lookback_window', 'regression_level', 'use_kernel_smoothing',
            'relative_weight', 'lag', 'atr_period', 'atr_multiplier',
            'volatility_min', 'volatility_max', 're_entry_delay',
        ]
        for s in ['eth', 'btc', 'sol']:
            fieldnames += [f'{s}_pf', f'{s}_dd', f'{s}_profit', f'{s}_trades', f'{s}_wr', f'{s}_sharpe']
        fieldnames += ['score', 'balanced_score', 'confidence', 'config_hash']

        csv_rows = []
        for params, score, balanced, results_dict, conf in smart_results:
            if not results_dict:
                continue
            row = {
                'lookback_window': params['h'],
                'regression_level': params['x'],
                'use_kernel_smoothing': params.get('smoothing', True),
                'relative_weight': params.get('r', DEFAULTS['relative_weight']),
                'lag': DEFAULTS['lag'],
                'atr_period': DEFAULTS['atr_period'],
                'atr_multiplier': DEFAULTS['atr_multiplier'],
                'volatility_min': params.get('vol_min', DEFAULTS['volatility_min']),
                'volatility_max': params.get('vol_max', DEFAULTS['volatility_max']),
                're_entry_delay': DEFAULTS['re_entry_delay'],
                'score': score,
                'balanced_score': balanced,
                'confidence': conf,
                'config_hash': config_hash(params),
            }
            for sym, m in results_dict.items():
                s = sym.replace('USDT', '').lower()
                row[f'{s}_pf'] = round(m.get('profit_factor', 0), 2)
                row[f'{s}_dd'] = round(m.get('max_drawdown_pct', 0), 1)
                row[f'{s}_profit'] = round(m.get('net_profit_pct', 0), 1)
                row[f'{s}_trades'] = m.get('total_trades', 0)
                row[f'{s}_wr'] = round(m.get('win_rate', 0), 1)
                row[f'{s}_sharpe'] = round(m.get('sharpe', 0), 2)
            csv_rows.append(row)

        with open(csv_path, 'w', newline='') as f:
            writer = csv.DictWriter(f, fieldnames=fieldnames)
            writer.writeheader()
            writer.writerows(csv_rows)
        print(f'Results saved to {csv_path}')

        # Append TOP 20 to alltime
        valid_rows = sorted([r for r in csv_rows if r['score'] > 0],
                            key=lambda r: r['score'], reverse=True)[:20]
        if valid_rows:
            run_date = datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M')
            append_alltime(valid_rows, run_date)

        # Save metadata
        meta = {
            'date': datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%S'),
            'mode': 'smart',
            'time_budget': cli.time_budget,
            'experiments': len(csv_rows),
            'valid': len(valid_rows),
            'duration_seconds': int(elapsed_total),
            'assets': list(ASSETS),
        }
        with open('autoresearch_meta.json', 'w') as f:
            json.dump(meta, f, indent=2)
        print(f'Metadata saved to autoresearch_meta.json')
        return

    # --- Step 2: Run grid ---
    results = []         # list of dicts for CSV
    best_score = 0.0
    best_config = None
    combo_num = 0
    t_start = time.time()
    grid_upload_buffer = []
    grid_upload_sent = 0
    grid_upload_failed = 0

    for combo in combos:
        combo_num += 1
        h = combo['lookback_window']
        x = combo['regression_level']
        smooth = combo['use_kernel_smoothing']
        r = combo.get('relative_weight', DEFAULTS['relative_weight'])
        lag = combo.get('lag', DEFAULTS['lag'])
        atr_p = combo.get('atr_period', DEFAULTS['atr_period'])
        atr_m = combo.get('atr_multiplier', DEFAULTS['atr_multiplier'])
        vol_min = combo.get('volatility_min', DEFAULTS['volatility_min'])
        vol_max = combo.get('volatility_max', DEFAULTS['volatility_max'])
        re_delay = combo.get('re_entry_delay', DEFAULTS['re_entry_delay'])

        # Build row with ALL params
        row = {
            'lookback_window': h,
            'regression_level': x,
            'use_kernel_smoothing': smooth,
            'relative_weight': r,
            'lag': lag,
            'atr_period': atr_p,
            'atr_multiplier': atr_m,
            'volatility_min': vol_min,
            'volatility_max': vol_max,
            're_entry_delay': re_delay,
        }
        asset_results = {}

        # Build label showing iterated params
        label_parts = [f'h={h}', f'x={x}']
        if 'use_kernel_smoothing' in iterated:
            label_parts.append(f'smooth={smooth_label(smooth)}')
        if 'relative_weight' in iterated:
            label_parts.append(f'r={r}')
        if 'lag' in iterated:
            label_parts.append(f'lag={lag}')
        if 'atr_period' in iterated:
            label_parts.append(f'atr_per={atr_p}')
        if 'atr_multiplier' in iterated:
            label_parts.append(f'atr_mult={atr_m}')
        if 'volatility_min' in iterated:
            label_parts.append(f'vol_min={vol_min}')
        if 'volatility_max' in iterated:
            label_parts.append(f'vol_max={vol_max}')
        if 're_entry_delay' in iterated:
            label_parts.append(f'reentry={re_delay}')

        print(f'[{combo_num}/{total_combos}] {", ".join(label_parts)}')

        for symbol in ASSETS:
            args = make_args(symbol, combo)
            apply_config(args)

            klines, tsi = asset_data[symbol]
            trades, _, final_balance = run_backtest(klines, args, tsi)
            m = calculate_metrics(trades, args.capital, final_balance)

            pf = m.get('profit_factor', 0)
            dd = m.get('max_drawdown_pct', 0)
            net = m.get('net_profit_pct', 0)
            n_trades = m.get('total_trades', 0)
            wr = m.get('win_rate', 0)
            sharpe = m.get('sharpe', 0)

            note = ''
            if n_trades < 30:
                note = ' [!] LOW SAMPLE'

            print(f'  {symbol}: PF={pf:.2f}, DD={dd:.0f}%, {net:+.0f}%, '
                  f'trades={n_trades}, WR={wr:.0f}%{note}')

            asset_results[symbol] = m

            # CSV columns per asset
            s = symbol.replace('USDT', '').lower()
            row[f'{s}_pf'] = round(pf, 2)
            row[f'{s}_dd'] = round(dd, 1)
            row[f'{s}_profit'] = round(net, 1)
            row[f'{s}_trades'] = n_trades
            row[f'{s}_wr'] = round(wr, 1)
            row[f'{s}_sharpe'] = round(sharpe, 2)

        score = calculate_score(asset_results)
        row['score'] = score
        row['balanced_score'] = calculate_balanced_score(asset_results)
        row['confidence'] = confidence_score(asset_results)
        row['config_hash'] = config_hash({
            'h': h, 'x': x, 'r': r, 'vol_min': vol_min,
            'vol_max': vol_max, 'smoothing': smooth,
        })

        if score == 0:
            reject_reasons = []
            for sym, m in asset_results.items():
                if m.get('profit_factor', 0) < 1.0:
                    reject_reasons.append(f'{sym.replace("USDT","")} losing')
                if m.get('max_drawdown_pct', 0) > 40:
                    reject_reasons.append(f'{sym.replace("USDT","")} DD>40%')
            print(f'  SCORE: 0.00 (REJECTED -- {", ".join(reject_reasons)})')
        else:
            marker = ''
            if score > best_score:
                best_score = score
                best_config = combo.copy()
                marker = ' * NEW BEST'
            print(f'  SCORE: {score:.4f}{marker}')

        results.append(row)

        # Upload buffer for grid mode
        if score > 0 and cli.upload_url:
            grid_params = {
                'h': h, 'x': x, 'r': r, 'vol_min': vol_min,
                'vol_max': vol_max, 'smoothing': smooth,
            }
            grid_upload_buffer.append((grid_params, score,
                                       row.get('balanced_score', 0), asset_results,
                                       row.get('confidence', 0)))
            if len(grid_upload_buffer) >= cli.upload_batch_size:
                ok = upload_results(grid_upload_buffer, cli.upload_url, cli.upload_key, cli.author)
                if ok:
                    grid_upload_sent += len(grid_upload_buffer)
                else:
                    grid_upload_failed += len(grid_upload_buffer)
                grid_upload_buffer = []

        # Progress every 5 combos
        if combo_num % 5 == 0:
            elapsed = time.time() - t_start
            rate = elapsed / combo_num
            eta = rate * (total_combos - combo_num)
            pct = combo_num / total_combos * 100
            bar_len = 30
            filled = int(bar_len * combo_num / total_combos)
            bar = '#' * filled + '-' * (bar_len - filled)
            if best_config:
                best_str = f'h={best_config["lookback_window"]},x={best_config["regression_level"]}'
            else:
                best_str = 'none'
            print(f'\n  Progress: [{bar}] {pct:.0f}% ({combo_num}/{total_combos}) '
                  f'| Best: {best_str} score={best_score:.4f} '
                  f'| ETA: {fmt_time(eta)}\n')

        print()

    elapsed_total = time.time() - t_start

    # Flush remaining grid upload buffer
    if grid_upload_buffer and cli.upload_url:
        ok = upload_results(grid_upload_buffer, cli.upload_url, cli.upload_key, cli.author)
        if ok:
            grid_upload_sent += len(grid_upload_buffer)
        else:
            grid_upload_failed += len(grid_upload_buffer)

    # --- Step 3: Save CSV ---
    csv_path = 'autoresearch_results.csv'
    fieldnames = [
        'lookback_window', 'regression_level', 'use_kernel_smoothing',
        'relative_weight', 'lag', 'atr_period', 'atr_multiplier',
        'volatility_min', 'volatility_max', 're_entry_delay',
    ]
    for s in ['eth', 'btc', 'sol']:
        fieldnames += [f'{s}_pf', f'{s}_dd', f'{s}_profit', f'{s}_trades', f'{s}_wr', f'{s}_sharpe']
    fieldnames.append('score')
    fieldnames.append('balanced_score')
    fieldnames.append('confidence')
    fieldnames.append('config_hash')

    with open(csv_path, 'w', newline='') as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(results)
    print(f'Results saved to {csv_path}')

    # --- Step 3a: Save to database (optional, non-critical) ---
    if HAS_DB:
        print('  Saving results to database...')
        try:
            db = AutoResearchDB()
            run_id = run_async(db.create_run(total_combos, list(ASSETS)))

            async def save_all_results():
                for r in results:
                    # Build per-symbol metrics from the flattened CSV row columns
                    metrics = {}
                    for sym in ASSETS:
                        prefix = sym.replace('USDT', '').lower()
                        pf = r.get(f'{prefix}_pf')
                        if pf is not None:
                            metrics[sym] = {
                                'profit_factor': float(pf),
                                'max_drawdown_pct': float(r.get(f'{prefix}_dd', 0)),
                                'net_profit_pct': float(r.get(f'{prefix}_profit', 0)),
                                'n_trades': int(r.get(f'{prefix}_trades', 0)),
                            }
                    await db.save_result(run_id, r, metrics, r.get('score', 0))
                await db.complete_run(run_id, int(elapsed_total))

            run_async(save_all_results())
            run_async(db.disconnect())
            print(f'  Database: {len(results)} results saved')
        except Exception as e:
            print(f'  Database save failed (non-critical): {e}')

    # --- Step 3b: Save metadata JSON ---
    meta = {
        'date': datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%S'),
        'combinations_tested': total_combos,
        'valid': len([r for r in results if r['score'] > 0]),
        'rejected': len([r for r in results if r['score'] == 0]),
        'duration_seconds': int(elapsed_total),
        'h_range': [cli.h_min, cli.h_max, cli.h_step],
        'x_range': [cli.x_min, cli.x_max, cli.x_step],
        'smoothing': cli.smoothing,
        'assets': list(ASSETS),
        'iterated_params': {k: v if not isinstance(v, (list,)) else [str(x) for x in v]
                            for k, v in iterated.items()},
        'fixed_params': {k: v for k, v in fixed.items()},
    }
    with open('autoresearch_meta.json', 'w') as f:
        json.dump(meta, f, indent=2)
    print(f'Metadata saved to autoresearch_meta.json')

    # --- Step 3c: Append TOP 20 to all-time leaderboard ---
    valid_for_alltime = [r for r in results if r['score'] > 0]
    ranked_for_alltime = sorted(valid_for_alltime, key=lambda r: r['score'], reverse=True)[:20]
    if ranked_for_alltime:
        run_date = datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M')
        append_alltime(ranked_for_alltime, run_date)
    print()

    # --- Step 4: Final report ---
    valid = [r for r in results if r['score'] > 0]
    rejected = len(results) - len(valid)
    ranked = sorted(valid, key=lambda r: r['score'], reverse=True)

    print('=' * 130)
    print('  AUTORESEARCH RESULTS')
    print('=' * 130)
    print(f'  Tested: {total_combos} combinations x {len(ASSETS)} assets')
    print(f'  Duration: {fmt_time(elapsed_total)}')
    print(f'  Rejected (any asset losing or DD>40%): {rejected}/{total_combos}')
    print(f'  Valid (all assets profitable): {len(valid)}/{total_combos}')
    print()

    top_n = min(10, len(ranked))
    if top_n == 0:
        print('  No valid configurations found!')
        return

    # Build header dynamically based on iterated params
    print(f'  === TOP {top_n} CONFIGURATIONS ===')
    hdr_parts = [f'{"Rank":<5}', f'{"h":<5}', f'{"x":<4}', f'{"Sm":<4}']
    if 'relative_weight' in iterated:
        hdr_parts.append(f'{"r":<6}')
    if 'lag' in iterated:
        hdr_parts.append(f'{"lag":<4}')
    if 'atr_period' in iterated:
        hdr_parts.append(f'{"ATRp":<5}')
    if 'atr_multiplier' in iterated:
        hdr_parts.append(f'{"ATRm":<6}')
    if 'volatility_min' in iterated:
        hdr_parts.append(f'{"Vmin":<5}')
    if 'volatility_max' in iterated:
        hdr_parts.append(f'{"Vmax":<5}')
    if 're_entry_delay' in iterated:
        hdr_parts.append(f'{"ReD":<4}')
    hdr_parts += [f'{"Score":<7}',
                  f'{"ETH PF":<8}', f'{"BTC PF":<8}', f'{"SOL PF":<8}',
                  f'{"ETH DD":<8}', f'{"BTC DD":<8}', f'{"SOL DD":<8}',
                  f'{"ETH %":<8}', f'{"BTC %":<8}', f'{"SOL %":<8}']
    hdr = '  ' + '| '.join(hdr_parts)
    print(hdr)
    print('  ' + '-' * (len(hdr) - 2))

    def print_row(prefix, r, score_key='score'):
        s_label = smooth_label(r['use_kernel_smoothing'])
        parts = [f'{prefix:<5}', f'{r["lookback_window"]:<5}', f'{r["regression_level"]:<4}', f'{s_label:<4}']
        if 'relative_weight' in iterated:
            parts.append(f'{r["relative_weight"]:<6}')
        if 'lag' in iterated:
            parts.append(f'{r["lag"]:<4}')
        if 'atr_period' in iterated:
            parts.append(f'{r["atr_period"]:<5}')
        if 'atr_multiplier' in iterated:
            parts.append(f'{r["atr_multiplier"]:<6}')
        if 'volatility_min' in iterated:
            parts.append(f'{r["volatility_min"]:<5}')
        if 'volatility_max' in iterated:
            parts.append(f'{r["volatility_max"]:<5}')
        if 're_entry_delay' in iterated:
            parts.append(f'{r["re_entry_delay"]:<4}')
        score_val = r.get(score_key, r['score']) if score_key else r['score']
        parts += [
            f'{score_val:<7.4f}',
            f'{r["eth_pf"]:<8.2f}', f'{r["btc_pf"]:<8.2f}', f'{r["sol_pf"]:<8.2f}',
            f'{r["eth_dd"]:<7.1f}%', f'{r["btc_dd"]:<7.1f}%', f'{r["sol_dd"]:<7.1f}%',
            f'{r["eth_profit"]:>+6.1f}%', f'{r["btc_profit"]:>+6.1f}%', f'{r["sol_profit"]:>+6.1f}%',
        ]
        print('  ' + '| '.join(parts))

    for i, r in enumerate(ranked[:top_n], 1):
        print_row(str(i), r)
    print()

    # --- Balanced Score ranking ---
    balanced_valid = [r for r in results if r.get('balanced_score', 0) > 0]
    balanced_ranked = sorted(balanced_valid, key=lambda r: r['balanced_score'], reverse=True)
    balanced_top_n = min(10, len(balanced_ranked))
    if balanced_top_n > 0:
        print(f'  === TOP {balanced_top_n} BY BALANCED SCORE (most consistent) ===')
        print(hdr.replace('Score', 'BScore'))
        print('  ' + '-' * (len(hdr) - 2))
        for i, r in enumerate(balanced_ranked[:balanced_top_n], 1):
            print_row(str(i), r, score_key='balanced_score')
        print()

    best_r = ranked[0]
    print(f'  === RECOMMENDATION ===')
    rec_parts = [f'h={best_r["lookback_window"]}', f'x={best_r["regression_level"]}',
                 f'smooth={smooth_label(best_r["use_kernel_smoothing"])}']
    if 'relative_weight' in iterated:
        rec_parts.append(f'r={best_r["relative_weight"]}')
    if 'lag' in iterated:
        rec_parts.append(f'lag={best_r["lag"]}')
    if 'atr_period' in iterated:
        rec_parts.append(f'atr_per={best_r["atr_period"]}')
    if 'atr_multiplier' in iterated:
        rec_parts.append(f'atr_mult={best_r["atr_multiplier"]}')
    if 'volatility_min' in iterated:
        rec_parts.append(f'vol_min={best_r["volatility_min"]}')
    if 'volatility_max' in iterated:
        rec_parts.append(f'vol_max={best_r["volatility_max"]}')
    if 're_entry_delay' in iterated:
        rec_parts.append(f'reentry={best_r["re_entry_delay"]}')
    print(f'  Best config: {", ".join(rec_parts)} (score: {best_r["score"]:.4f})')
    print()

    # Upload summary for grid mode
    if cli.upload_url:
        print(f'  === UPLOAD SUMMARY ===')
        print(f'  Server: {cli.upload_url}')
        print(f'  Uploaded: {grid_upload_sent} results')
        print(f'  Failed uploads: {grid_upload_failed}')
        print(f'  Author: {cli.author or os.getenv("USERNAME", os.getenv("USER", "unknown"))}')
        print()


if __name__ == '__main__':
    main()
