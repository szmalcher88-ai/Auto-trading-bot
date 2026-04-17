"""
One-time migration script: import existing CSV data into SQLite database.

Production state:
  - autoresearch_results.csv: ~4827 rows (latest run, all combinations)
  - autoresearch_alltime.csv: ~105 rows (top configs across historical runs)
  - autoresearch.db: empty (0 bytes)

Run:
    python migrate_csv_to_db.py
    python migrate_csv_to_db.py --dry-run        # validate without writing
    python migrate_csv_to_db.py --alltime-only   # only import alltime historical runs
"""

import argparse
import asyncio
import csv
import json
import os
import sys
from datetime import datetime, timezone


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _safe_float(val, default=0.0):
    try:
        v = float(val)
        return v if v == v else default  # NaN guard
    except (ValueError, TypeError):
        return default


def _safe_int(val, default=0):
    try:
        return int(float(val))
    except (ValueError, TypeError):
        return default


def _safe_bool(val):
    if isinstance(val, bool):
        return val
    return str(val).strip().lower() in ('true', '1', 'yes')


def _read_csv(path):
    if not os.path.exists(path):
        return []
    with open(path, 'r', encoding='utf-8') as f:
        reader = csv.DictReader(f)
        return list(reader)


def _is_valid_row(row, require_run_date=False):
    """Basic sanity: lookback and regression must be numeric."""
    try:
        lbk = row.get('lookback_window') or row.get('lookback', '')
        reg = row.get('regression_level') or row.get('regression', '')
        if not lbk or not reg:
            return False
        float(lbk)
        float(reg)
        if require_run_date:
            rd = row.get('run_date', '').strip()
            if not rd or not rd[0].isdigit():
                return False
        return True
    except (ValueError, TypeError):
        return False


def _extract_config_from_results_row(row):
    """Map autoresearch_results.csv columns to config dict."""
    return {
        'lookback_window': _safe_int(row.get('lookback_window', 0)),
        'regression_level': _safe_int(row.get('regression_level', 0)),
        'use_kernel_smoothing': _safe_bool(row.get('use_kernel_smoothing', True)),
        'relative_weight': _safe_float(row.get('relative_weight', 10.0)),
        'lag': _safe_int(row.get('lag', 1)),
        'atr_period': _safe_int(row.get('atr_period', 20)),
        'atr_multiplier': _safe_float(row.get('atr_multiplier', 6.0)),
        'volatility_min': _safe_int(row.get('volatility_min', 5)),
        'volatility_max': _safe_int(row.get('volatility_max', 10)),
        're_entry_delay': _safe_int(row.get('re_entry_delay', 1)),
    }


def _extract_config_from_alltime_row(row):
    """Map autoresearch_alltime.csv columns (short names) to config dict."""
    return {
        'lookback_window': _safe_int(row.get('lookback') or row.get('lookback_window', 0)),
        'regression_level': _safe_int(row.get('regression') or row.get('regression_level', 0)),
        'use_kernel_smoothing': _safe_bool(row.get('smoothing') or row.get('use_kernel_smoothing', True)),
        'relative_weight': _safe_float(row.get('relative_weight', 10.0)),
        'lag': _safe_int(row.get('lag', 1)),
        'atr_period': _safe_int(row.get('atr_period', 20)),
        'atr_multiplier': _safe_float(row.get('atr_multiplier', 6.0)),
        'volatility_min': _safe_int(row.get('vol_min') or row.get('volatility_min', 5)),
        'volatility_max': _safe_int(row.get('vol_max') or row.get('volatility_max', 10)),
        're_entry_delay': _safe_int(row.get('reentry_delay') or row.get('re_entry_delay', 1)),
    }


def _extract_metrics_from_results_row(row):
    """Build per-symbol metrics dict from autoresearch_results.csv row."""
    metrics = {}
    for sym, prefix in [('ETHUSDT', 'eth'), ('BTCUSDT', 'btc'), ('SOLUSDT', 'sol'),
                        ('AVAXUSDT', 'avax'), ('BNBUSDT', 'bnb'), ('ADAUSDT', 'ada'),
                        ('DOGEUSDT', 'doge'), ('MATICUSDT', 'matic')]:
        pf = row.get(f'{prefix}_pf')
        if pf is not None and pf != '':
            metrics[sym] = {
                'profit_factor': _safe_float(pf),
                'max_drawdown_pct': _safe_float(row.get(f'{prefix}_dd', 0)),
                'net_profit_pct': _safe_float(row.get(f'{prefix}_profit', 0)),
                'n_trades': _safe_int(row.get(f'{prefix}_trades', 0)),
            }
    return metrics


def _extract_metrics_from_alltime_row(row):
    """Build per-symbol metrics dict from autoresearch_alltime.csv row."""
    metrics = {}
    for sym, prefix in [('ETHUSDT', 'eth'), ('BTCUSDT', 'btc'), ('SOLUSDT', 'sol')]:
        pf = row.get(f'{prefix}_pf')
        if pf is not None and pf != '':
            metrics[sym] = {
                'profit_factor': _safe_float(pf),
                'max_drawdown_pct': _safe_float(row.get(f'{prefix}_dd', 0)),
                'net_profit_pct': _safe_float(row.get(f'{prefix}_profit', 0)),
                'n_trades': 0,
            }
    return metrics


def _parse_run_date(run_date_str):
    """Parse run_date string to UTC datetime."""
    formats = ['%Y-%m-%d %H:%M', '%Y-%m-%dT%H:%M:%S', '%Y-%m-%dT%H:%M']
    for fmt in formats:
        try:
            dt = datetime.strptime(run_date_str.strip(), fmt)
            return dt.replace(tzinfo=timezone.utc)
        except ValueError:
            continue
    return datetime.now(timezone.utc)


# ---------------------------------------------------------------------------
# Migration logic
# ---------------------------------------------------------------------------

async def migrate(dry_run=False, alltime_only=False, reset=False):
    from bot.db import AutoResearchDB, run_async

    results_path = 'autoresearch_results.csv'
    alltime_path = 'autoresearch_alltime.csv'
    meta_path = 'autoresearch_meta.json'

    print("=" * 60)
    print("  CSV -> SQLite Migration")
    print("=" * 60)

    # --- Load files ---
    results_rows = _read_csv(results_path)
    alltime_rows = _read_csv(alltime_path)

    # Read meta.json for latest run info
    meta = {}
    if os.path.exists(meta_path):
        try:
            with open(meta_path, 'r') as f:
                meta = json.load(f)
        except Exception:
            pass

    valid_results = [r for r in results_rows if _is_valid_row(r)]
    valid_alltime = [r for r in alltime_rows if _is_valid_row(r, require_run_date=True)]

    print(f"  autoresearch_results.csv: {len(results_rows)} rows ({len(valid_results)} valid)")
    print(f"  autoresearch_alltime.csv: {len(alltime_rows)} rows ({len(valid_alltime)} valid)")
    print(f"  autoresearch_meta.json:   {meta.get('date', 'N/A')}")
    print()

    if dry_run:
        print("  DRY RUN -- no data will be written to database.")
        print()

    # --- Identify historical run dates (alltime rows NOT from the latest run) ---
    latest_date_str = meta.get('date', '')[:16] if meta.get('date') else ''
    # latest_date_str looks like "2026-03-25T21:40", normalize to "2026-03-25 21:40"
    latest_date_normalized = latest_date_str.replace('T', ' ')

    # Group alltime rows by run_date, excluding the latest run (already in results.csv)
    historical_runs = {}
    for row in valid_alltime:
        rd = row.get('run_date', '').strip()
        if rd == latest_date_normalized or rd.startswith(latest_date_normalized):
            continue  # part of current run, covered by results.csv
        if rd not in historical_runs:
            historical_runs[rd] = []
        historical_runs[rd].append(row)

    print(f"  Historical run dates found in alltime CSV: {len(historical_runs)}")
    for rd in sorted(historical_runs.keys()):
        print(f"    {rd}: {len(historical_runs[rd])} result(s)")
    print()

    if dry_run:
        print("  Current run results.csv rows to import:", len(valid_results))
        print("  Total historical alltime rows to import:",
              sum(len(v) for v in historical_runs.values()))
        print()
        print("  Dry run complete. Run without --dry-run to execute.")
        return

    # --- Connect ---
    db = AutoResearchDB()
    await db.connect()
    print("  Connected to database.")

    if reset:
        print("  Resetting database (deleting all existing runs/results)...")
        deleted_results = await db.db.autoresearchresult.delete_many()
        deleted_runs = await db.db.autoresearchrun.delete_many()
        print(f"  Deleted {deleted_results} results and {deleted_runs} runs.")
        print()

    total_saved = 0

    # --- Step 1: Import historical runs from alltime.csv ---
    if historical_runs:
        print(f"\n  Importing {len(historical_runs)} historical run(s) from alltime CSV...")
        for run_date_str in sorted(historical_runs.keys()):
            rows = historical_runs[run_date_str]
            assets = ['ETHUSDT', 'BTCUSDT', 'SOLUSDT']

            run_id = await db.create_run(
                total_combinations=len(rows),
                assets=assets,
            )
            # Mark run completed with the historical date
            await db.complete_run(run_id, duration_seconds=0)
            # Override startedAt to match historical date by direct update
            start_dt = _parse_run_date(run_date_str)
            try:
                await db.db.autoresearchrun.update(
                    where={'id': run_id},
                    data={'startedAt': start_dt, 'completedAt': start_dt},
                )
            except Exception:
                pass

            for row in rows:
                config = _extract_config_from_alltime_row(row)
                metrics = _extract_metrics_from_alltime_row(row)
                score = _safe_float(row.get('score', 0))
                await db.save_result(run_id, config, metrics, score)
                total_saved += 1

            print(f"    [{run_date_str}] {len(rows)} results saved (run_id={run_id[:8]}...)")

    # --- Step 2: Import current run from results.csv ---
    if not alltime_only and valid_results:
        print(f"\n  Importing current run from autoresearch_results.csv ({len(valid_results)} rows)...")

        assets = meta.get('assets', ['ETHUSDT', 'BTCUSDT', 'SOLUSDT'])
        total_combinations = meta.get('experiments', len(valid_results))
        duration = meta.get('duration_seconds', 0)

        run_id = await db.create_run(
            total_combinations=total_combinations,
            assets=assets,
        )
        await db.complete_run(run_id, duration_seconds=_safe_int(duration))

        # Set startedAt from meta date if available
        if meta.get('date'):
            try:
                meta_dt = _parse_run_date(meta['date'])
                await db.db.autoresearchrun.update(
                    where={'id': run_id},
                    data={'startedAt': meta_dt, 'completedAt': meta_dt},
                )
            except Exception:
                pass

        for i, row in enumerate(valid_results):
            config = _extract_config_from_results_row(row)
            metrics = _extract_metrics_from_results_row(row)
            score = _safe_float(row.get('score', 0))
            await db.save_result(run_id, config, metrics, score)
            total_saved += 1

            if (i + 1) % 500 == 0:
                print(f"    {i + 1}/{len(valid_results)} rows imported...")

        print(f"    Done. {len(valid_results)} results saved (run_id={run_id[:8]}...)")

    # --- Disconnect and report ---
    await db.disconnect()

    print()
    print("=" * 60)
    print(f"  Migration complete. Total rows saved: {total_saved}")
    print("=" * 60)


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------

async def validate():
    """Cross-check database row counts against CSV files."""
    from bot.db import AutoResearchDB

    print("\n  Validation:")
    db = AutoResearchDB()
    try:
        await db.connect()
        top = await db.get_top_results(limit=5)
        all_results = await db.get_all_results(limit=10000)
        latest_run = await db.get_latest_run()
        await db.disconnect()

        results_count = len(_read_csv('autoresearch_results.csv')) - 1  # subtract header
        alltime_count = len([r for r in _read_csv('autoresearch_alltime.csv')
                             if _is_valid_row(r, require_run_date=True)])

        print(f"    Database results: {len(all_results)}")
        print(f"    CSV results rows: {results_count}")
        print(f"    CSV alltime rows: {alltime_count}")
        print(f"    Latest run in DB: {latest_run}")
        print(f"    Top 5 scores:     {[round(r['score'], 4) for r in top]}")
    except Exception as e:
        print(f"    Validation failed: {e}")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def parse_args():
    p = argparse.ArgumentParser(description='Migrate autoresearch CSV data to SQLite database')
    p.add_argument('--dry-run', action='store_true',
                   help='Validate and print plan without writing to database')
    p.add_argument('--alltime-only', action='store_true',
                   help='Only import historical runs from alltime CSV (skip results.csv)')
    p.add_argument('--validate', action='store_true',
                   help='After migration, print summary from database')
    p.add_argument('--reset', action='store_true',
                   help='Delete all existing runs/results before migrating (use when re-running)')
    return p.parse_args()


if __name__ == '__main__':
    args = parse_args()

    try:
        asyncio.run(migrate(dry_run=args.dry_run, alltime_only=args.alltime_only, reset=args.reset))
        if args.validate and not args.dry_run:
            asyncio.run(validate())
    except KeyboardInterrupt:
        print("\nAborted.")
        sys.exit(1)
    except Exception as e:
        print(f"\nMigration failed: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)
