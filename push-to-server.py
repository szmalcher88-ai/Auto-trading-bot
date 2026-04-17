"""
push-to-server.py — Distributed Worker Client

Long-running worker that polls the orchestrator server for jobs, computes
backtests locally, and submits results back. Replaces the old "run once and
upload" pattern with a pull-based distributed worker system.

Usage:
    python push-to-server.py --server http://YOUR_SERVER_IP:8080 --key your-key [--name "My PC"]

Environment variables (optional):
    SERVER_URL       - Server base URL
    UPLOAD_API_KEY   - Upload API key
"""

import argparse
import json
import os
import platform
import signal
import socket
import sys
import threading
import time
import urllib.error
import urllib.request
from datetime import datetime, timedelta, timezone

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
    'end': '2026-03-29',
    'timeframe': '1h',
    'output': 'backtest_trades.csv',
    'no_cache': False,
}

DEFAULT_ASSETS = ['ETHUSDT', 'BTCUSDT', 'SOLUSDT']


class WorkerClient:
    """Long-running worker client that polls for jobs and submits results."""
    
    def __init__(self, server_url, api_key, worker_name=None):
        self.server_url = server_url.rstrip('/')
        self.api_key = api_key
        self.worker_name = worker_name or f"worker-{socket.gethostname()}"
        self.hostname = socket.gethostname()
        self.worker_id = None
        self.running = True
        self.current_job_id = None
        self.heartbeat_thread = None
        
        self.asset_data = {}
        
        signal.signal(signal.SIGINT, self._signal_handler)
        signal.signal(signal.SIGTERM, self._signal_handler)
    
    def _signal_handler(self, signum, frame):
        """Handle Ctrl+C gracefully."""
        print(f'\n[worker] Received signal {signum}, shutting down gracefully...')
        self.running = False
    
    def _http_request(self, method, endpoint, data=None, params=None):
        """Make HTTP request to server with auth."""
        url = f"{self.server_url}{endpoint}"
        if params:
            query = '&'.join(f'{k}={v}' for k, v in params.items())
            url = f"{url}?{query}"
        
        headers = {
            'Content-Type': 'application/json',
            'x-upload-key': self.api_key,
        }
        
        req_data = json.dumps(data).encode('utf-8') if data else None
        req = urllib.request.Request(url, data=req_data, headers=headers, method=method)
        
        try:
            with urllib.request.urlopen(req, timeout=30) as resp:
                return json.loads(resp.read().decode('utf-8'))
        except urllib.error.HTTPError as e:
            body = e.read().decode('utf-8', errors='replace')
            print(f'[worker] HTTP {e.code} error on {method} {endpoint}: {body}')
            raise
        except Exception as e:
            print(f'[worker] Request failed on {method} {endpoint}: {e}')
            raise
    
    def register(self):
        """Register worker with server."""
        machine_info = {
            'cpu_count': os.cpu_count(),
            'platform': platform.platform(),
            'python_version': sys.version.split()[0],
        }
        
        print(f'[worker] Registering as {self.worker_name}@{self.hostname}...')
        
        resp = self._http_request('POST', '/api/worker/register', {
            'name': self.worker_name,
            'hostname': self.hostname,
            'machine_info': machine_info,
        })
        
        self.worker_id = resp['worker_id']
        print(f'[worker] Registered with ID: {self.worker_id}')
    
    def prefetch_data(self):
        """Pre-fetch and cache price data for all assets."""
        from binance.client import Client
        from backtest import fetch_historical_klines
        
        print('[worker] Pre-fetching price data...')
        
        client = Client('', '', testnet=False)
        start_date = datetime.strptime(DEFAULTS['start'], '%Y-%m-%d').replace(tzinfo=timezone.utc)
        end_date = datetime.strptime(DEFAULTS['end'], '%Y-%m-%d').replace(tzinfo=timezone.utc)
        fetch_start = start_date - timedelta(days=92)
        
        for symbol in DEFAULT_ASSETS:
            print(f'  Fetching {symbol}...')
            klines = fetch_historical_klines(
                client, symbol, DEFAULTS['timeframe'],
                fetch_start, end_date, use_cache=True,
            )
            
            start_ms = int(start_date.timestamp() * 1000)
            trading_start_idx = 0
            for idx, k in enumerate(klines):
                if k['open_time'] >= start_ms:
                    trading_start_idx = idx
                    break
            
            self.asset_data[symbol] = (klines, trading_start_idx)
            print(f'    {symbol}: {len(klines)} candles, warmup={trading_start_idx} bars')
        
        print('[worker] Data prefetch complete.')
    
    def claim_job(self):
        """Claim next pending job from server."""
        try:
            resp = self._http_request('POST', '/api/worker/claim', params={'worker_id': self.worker_id})
            return resp.get('job')
        except Exception as e:
            print(f'[worker] Failed to claim job: {e}')
            return None
    
    def submit_job(self, job_id, results):
        """Submit completed job results."""
        try:
            self._http_request('POST', '/api/worker/submit', {
                'job_id': job_id,
                'results': results,
            })
            print(f'[worker] Submitted job {job_id} with {len(results)} results')
            return True
        except Exception as e:
            print(f'[worker] Failed to submit job: {e}')
            return False
    
    def heartbeat_loop(self):
        """Background thread that sends heartbeat every 30s."""
        while self.running:
            try:
                self._http_request('POST', '/api/worker/heartbeat', params={'worker_id': self.worker_id})
            except Exception as e:
                print(f'[worker] Heartbeat failed: {e}')
            
            for _ in range(30):
                if not self.running:
                    break
                time.sleep(1)
    
    def evaluate_configs(self, configs):
        """
        Evaluate a batch of config combos or run smart search.
        
        Returns list of result dicts.
        """
        # Check if this is a smart mode job
        if len(configs) == 1 and isinstance(configs[0], dict) and configs[0].get('mode') == 'smart':
            return self._run_smart_search(configs[0])
        
        # Grid mode: evaluate specific configs
        from autoresearch import evaluate_combo
        from backtest import apply_config, run_backtest, calculate_metrics
        
        results = []
        
        for i, config in enumerate(configs):
            print(f'[worker] Evaluating config {i+1}/{len(configs)}: '
                  f'h={config["lookback_window"]}, x={config["regression_level"]}')
            
            try:
                result = evaluate_combo(
                    config,
                    self.asset_data,
                    DEFAULT_ASSETS,
                    apply_config,
                    run_backtest,
                    calculate_metrics
                )
                
                results.append(result['row'])
                
                score = result['score']
                if score > 0:
                    print(f'  Score: {score:.4f}')
                else:
                    print(f'  Score: 0.00 (rejected)')
            
            except Exception as e:
                print(f'  ERROR: {e}')
                continue
        
        return results
    
    def _run_smart_search(self, job_config):
        """Run smart search mode."""
        import random
        from autoresearch import smart_search, PARAM_SPACE
        from backtest import apply_config, run_backtest, calculate_metrics
        
        time_budget = job_config.get('time_budget', 3600)
        worker_index = job_config.get('worker_index', 0)
        raw_space = job_config.get('param_space', {})

        # Convert simplified [min, max] / [values...] format to the dict format
        # expected by autoresearch.random_sample / smart_search.
        # Fall back to autoresearch.PARAM_SPACE defaults for any key not provided.
        param_space = dict(PARAM_SPACE)
        if 'h' in raw_space:
            h = raw_space['h']
            param_space['h'] = {'min': h[0], 'max': h[1], 'type': 'int'}
        if 'x' in raw_space:
            x = raw_space['x']
            param_space['x'] = {'min': x[0], 'max': x[1], 'type': 'int'}
        if 'smoothing' in raw_space:
            param_space['smoothing'] = {'values': raw_space['smoothing'], 'type': 'choice'}
        
        # Set unique seed per worker
        seed = int(time.time() * 1000) + worker_index
        random.seed(seed)
        
        print(f'[worker] Running SMART SEARCH mode')
        print(f'  Time budget: {time_budget}s ({time_budget//60}min)')
        print(f'  Worker index: {worker_index}')
        print(f'  Random seed: {seed}')
        print(f'  Param space: {param_space}')
        
        def run_single_bt(params, symbol):
            """Run single backtest for smart search."""
            from autoresearch import make_args

            combo = {
                'lookback_window': params['h'],
                'regression_level': params['x'],
                'use_kernel_smoothing': params.get('smoothing', True),
                'relative_weight': params.get('r', 10),
                'lag': 1,
                'atr_period': 20,
                'atr_multiplier': 6,
                'volatility_min': params.get('vol_min', 5),
                'volatility_max': params.get('vol_max', 10),
                're_entry_delay': 1,
            }
            args = make_args(symbol, combo)
            apply_config(args)
            klines, warmup_idx = self.asset_data[symbol]
            trades, _, final_balance = run_backtest(klines, args, warmup_idx)
            return calculate_metrics(trades, args.capital, final_balance)
        
        smart_results, elapsed = smart_search(
            time_budget,
            param_space,
            DEFAULT_ASSETS,
            self.asset_data,
            run_single_bt,
            upload_url=None,
            upload_key=None,
            upload_batch_size=20,
            author=self.worker_name
        )
        
        print(f'[worker] Smart search complete: {len(smart_results)} results in {elapsed:.1f}s')
        
        # Convert smart results to standard format
        results = []
        for params, score, balanced, results_dict, conf in smart_results:
            if not results_dict:
                continue
            
            row = {
                'lookback_window': params['h'],
                'regression_level': params['x'],
                'use_kernel_smoothing': params.get('smoothing', True),
                'relative_weight': params.get('r', 10),
                'lag': params.get('lag', 1),
                'atr_period': params.get('atr_period', 20),
                'atr_multiplier': params.get('atr_multiplier', 6),
                'volatility_min': params.get('volatility_min', 5),
                'volatility_max': params.get('volatility_max', 10),
                're_entry_delay': params.get('re_entry_delay', 1),
                'score': score,
                'balanced_score': balanced,
                'confidence': conf,
            }
            
            # Add per-asset metrics
            for symbol in DEFAULT_ASSETS:
                sym_lower = symbol.lower()
                if symbol in results_dict:
                    m = results_dict[symbol]
                    row[f'{sym_lower}_pf'] = m.get('profit_factor', 0)
                    row[f'{sym_lower}_dd'] = m.get('max_drawdown_pct', 0)
                    row[f'{sym_lower}_profit'] = m.get('total_pnl_pct', 0)
                    row[f'{sym_lower}_trades'] = m.get('total_trades', 0)
                    row[f'{sym_lower}_wr'] = m.get('win_rate', 0)
                    row[f'{sym_lower}_sharpe'] = m.get('sharpe_ratio', 0)
            
            results.append(row)
        
        return results
    
    def run(self):
        """Main worker loop."""
        print(f'[worker] Starting worker loop...')
        
        self.heartbeat_thread = threading.Thread(target=self.heartbeat_loop, daemon=True)
        self.heartbeat_thread.start()
        
        retry_delay = 10
        max_retry_delay = 60
        
        while self.running:
            try:
                job = self.claim_job()
                
                if not job:
                    print(f'[worker] No jobs available, sleeping {retry_delay}s...')
                    time.sleep(retry_delay)
                    retry_delay = min(retry_delay * 1.5, max_retry_delay)
                    continue
                
                retry_delay = 10
                
                job_id = job['id']
                configs = job['configs']
                self.current_job_id = job_id
                
                print(f'\n[worker] Claimed job {job_id} with {len(configs)} configs')
                
                results = self.evaluate_configs(configs)
                
                if results:
                    self.submit_job(job_id, results)
                else:
                    print(f'[worker] No valid results for job {job_id}')
                
                self.current_job_id = None
                print(f'[worker] Job {job_id} complete.\n')
            
            except KeyboardInterrupt:
                print('[worker] Interrupted by user')
                break
            except Exception as e:
                print(f'[worker] Error in main loop: {e}')
                time.sleep(retry_delay)
        
        print('[worker] Worker loop stopped.')


def parse_args():
    p = argparse.ArgumentParser(description='Distributed worker client for AutoResearch')
    p.add_argument('--server', default=None,
                   help='Server base URL, e.g. http://YOUR_SERVER_IP:8080')
    p.add_argument('--key', default=None,
                   help='Upload API key (x-upload-key header)')
    p.add_argument('--name', default=None,
                   help='Worker name (default: worker-{hostname})')
    p.add_argument('--workers', type=int, default=1,
                   help='Number of parallel worker processes to spawn (default: 1)')
    return p.parse_args()


def _run_single_worker(server_url, api_key, name):
    """Entry point for a single worker process."""
    worker = WorkerClient(server_url, api_key, name)
    try:
        worker.register()
        worker.prefetch_data()
        worker.run()
    except KeyboardInterrupt:
        print(f'\n[{name}] Interrupted')
    except Exception as e:
        print(f'\n[{name}] Fatal error: {e}')
        import traceback
        traceback.print_exc()


def main():
    import multiprocessing
    args = parse_args()

    server_url = args.server or os.environ.get('SERVER_URL', '').strip()
    api_key = args.key or os.environ.get('UPLOAD_API_KEY', '').strip()

    if not server_url:
        print('[worker] ERROR: server URL not set.')
        print('  Use --server http://... or set SERVER_URL env var')
        sys.exit(1)

    if not api_key:
        print('[worker] ERROR: upload key not set.')
        print('  Use --key <key> or set UPLOAD_API_KEY env var')
        sys.exit(1)

    base_name = args.name or f'worker-{socket.gethostname()}'
    n = args.workers

    print('=' * 70)
    print('  Distributed Worker Client')
    print('=' * 70)
    print(f'  Server  : {server_url}')
    print(f'  Workers : {n}')
    print(f'  Names   : {base_name} ... {base_name}-{n}' if n > 1 else f'  Name    : {base_name}')
    print('=' * 70)
    print()

    if n == 1:
        _run_single_worker(server_url, api_key, base_name)
        return

    # Spawn N independent worker processes — each gets a unique name suffix
    # so they register as separate workers with different random seeds.
    processes = []
    for i in range(1, n + 1):
        name = f'{base_name}-{i}'
        p = multiprocessing.Process(
            target=_run_single_worker,
            args=(server_url, api_key, name),
            name=name,
            daemon=True,
        )
        p.start()
        print(f'[launcher] Started worker {name} (pid={p.pid})')
        processes.append(p)

    print(f'[launcher] {n} workers running — press Ctrl+C to stop all\n')

    try:
        for p in processes:
            p.join()
    except KeyboardInterrupt:
        print('\n[launcher] Stopping all workers...')
        for p in processes:
            p.terminate()
        for p in processes:
            p.join(timeout=5)
        print('[launcher] All workers stopped.')


if __name__ == '__main__':
    main()
