"""
Backtest Engine — Phase 7
Uses IDENTICAL logic to the live bot (kernels, filters, strategy, SL, re-entry)
on historical data from Binance Futures.

Usage:
    python backtest.py                                  # defaults
    python backtest.py --start 2025-01-01 --end 2026-03-22
    python backtest.py --sl-percent 3.0
    python backtest.py --symbol BTCUSDT --timeframe 4h
"""

import argparse
import csv
import logging
import os
import sys
import time as time_module
from datetime import datetime, timedelta, timezone

import numpy as np
from binance.client import Client
from dotenv import load_dotenv

# ---------------------------------------------------------------------------
# Logging — suppress noisy bot logs, keep backtest output clean
# ---------------------------------------------------------------------------
logging.basicConfig(level=logging.WARNING)
logger = logging.getLogger('backtest')
logger.setLevel(logging.INFO)

# ---------------------------------------------------------------------------
# CLI arguments
# ---------------------------------------------------------------------------

def parse_args():
    p = argparse.ArgumentParser(description='Backtest — Kernel Regression Strategy')

    # Period
    p.add_argument('--start', default='2025-01-01', help='Start date YYYY-MM-DD')
    p.add_argument('--end', default='2026-03-22', help='End date YYYY-MM-DD')

    # Symbol / timeframe
    p.add_argument('--symbol', default='ETHUSDT')
    p.add_argument('--timeframe', default='1h')

    # Capital
    p.add_argument('--capital', type=float, default=10000.0, help='Initial capital USD')

    # Kernel params
    p.add_argument('--lookback-window', '--lookback', type=int, default=110)
    p.add_argument('--relative-weight', type=float, default=10.0)
    p.add_argument('--regression-level', '--regression', type=int, default=64)
    p.add_argument('--lag', type=int, default=1)
    p.add_argument('--use-kernel-smoothing', action='store_true', default=True)
    p.add_argument('--no-kernel-smoothing', dest='use_kernel_smoothing', action='store_false')

    # Stop loss
    p.add_argument('--sl-percent', type=float, default=2.7)
    p.add_argument('--sl-type', choices=['percent', 'atr'], default='percent',
                   help='SL type: percent=fixed %%, atr=ATR-based (default: percent)')
    p.add_argument('--atr-period', type=int, default=14, help='ATR period for ATR SL (default: 14)')
    p.add_argument('--atr-multiplier', type=float, default=2.0, help='ATR multiplier for SL distance (default: 2.0)')
    p.add_argument('--use-dynamic-sl', action='store_true', default=True)
    p.add_argument('--no-dynamic-sl', dest='use_dynamic_sl', action='store_false')
    p.add_argument('--no-sl', action='store_true', default=False,
                   help='Disable SL completely — exit only on color change')
    p.add_argument('--trailing-mode', choices=['pine', 'execution'], default='pine',
                   help='Trailing SL mode: pine=close-based, execution=high/low-based (default: pine)')

    # Volatility filter
    p.add_argument('--volatility-min', '--vol-min', type=int, default=5)
    p.add_argument('--volatility-max', '--vol-max', type=int, default=10)
    p.add_argument('--vol-filter-off', action='store_true', default=False,
                   help='Disable volatility filter entirely')

    # Re-entry
    p.add_argument('--enable-re-entry', action='store_true', default=True)
    p.add_argument('--no-re-entry', '--reentry-off', dest='enable_re_entry', action='store_false')
    p.add_argument('--re-entry-delay', '--reentry-delay', type=int, default=1)

    # Slippage & commission
    p.add_argument('--slippage', type=float, default=0.0,
                   help='Slippage percent per trade (default 0 for TV comparison)')
    p.add_argument('--commission', type=float, default=0.05,
                   help='Commission percent per trade (default 0.05 = Binance Futures taker)')

    # Output
    p.add_argument('--output', default='backtest_trades.csv', help='Trade log CSV path')
    p.add_argument('--no-cache', action='store_true', help='Skip cache, always fetch from API')

    return p.parse_args()


# ---------------------------------------------------------------------------
# Apply CLI params to bot.config module (before importing Strategy)
# ---------------------------------------------------------------------------

def apply_config(args):
    """Override bot.config module attributes with CLI args."""
    import bot.config as cfg
    cfg.SYMBOL = args.symbol
    cfg.TIMEFRAME = args.timeframe
    cfg.LOOKBACK_WINDOW = args.lookback_window
    cfg.RELATIVE_WEIGHT = args.relative_weight
    cfg.REGRESSION_LEVEL = args.regression_level
    cfg.LAG = args.lag
    cfg.USE_KERNEL_SMOOTHING = args.use_kernel_smoothing
    cfg.SL_PERCENT = args.sl_percent
    cfg.USE_DYNAMIC_SL = args.use_dynamic_sl
    cfg.TRAILING_SL_MODE = args.trailing_mode
    cfg.VOLATILITY_MIN = args.volatility_min
    cfg.VOLATILITY_MAX = args.volatility_max
    cfg.ENABLE_RE_ENTRY = args.enable_re_entry
    cfg.RE_ENTRY_DELAY = args.re_entry_delay


# ---------------------------------------------------------------------------
# Data fetching with pagination and caching
# ---------------------------------------------------------------------------

TIMEFRAME_SECONDS = {
    '1m': 60, '3m': 180, '5m': 300, '15m': 900, '30m': 1800,
    '1h': 3600, '2h': 7200, '4h': 14400, '6h': 21600, '8h': 28800,
    '12h': 43200, '1d': 86400, '1w': 604800,
}


def get_cache_path(symbol, timeframe):
    os.makedirs('cache', exist_ok=True)
    return f'cache/{symbol}_{timeframe}.csv'


def load_cache(cache_path):
    """Load cached klines from CSV. Returns list of dicts."""
    if not os.path.exists(cache_path):
        return []
    rows = []
    with open(cache_path, 'r') as f:
        reader = csv.DictReader(f)
        for row in reader:
            rows.append({
                'open_time': int(row['open_time']),
                'open': float(row['open']),
                'high': float(row['high']),
                'low': float(row['low']),
                'close': float(row['close']),
                'volume': float(row['volume']),
            })
    return rows


def save_cache(cache_path, klines):
    """Save klines list to CSV cache."""
    with open(cache_path, 'w', newline='') as f:
        writer = csv.DictWriter(f, fieldnames=['open_time', 'open', 'high', 'low', 'close', 'volume'])
        writer.writeheader()
        writer.writerows(klines)


def fetch_historical_klines(client, symbol, interval, start_date, end_date, use_cache=True):
    """
    Fetch all klines between start and end date.
    Paginates by 1000, sleeps 1s between requests for rate limits.
    Uses CSV cache when available.
    """
    cache_path = get_cache_path(symbol, interval)
    start_ms = int(start_date.timestamp() * 1000)
    end_ms = int(end_date.timestamp() * 1000)

    # Try cache
    if use_cache:
        cached = load_cache(cache_path)
        if cached:
            cached_start = cached[0]['open_time']
            cached_end = cached[-1]['open_time']
            if cached_start <= start_ms and cached_end >= end_ms:
                # Cache covers requested range — filter and return
                filtered = [k for k in cached if start_ms <= k['open_time'] <= end_ms]
                logger.info(f"Cache hit: {len(filtered)} candles from {cache_path}")
                return filtered
            else:
                logger.info(f"Cache partial ({len(cached)} candles) — fetching fresh data")

    # Fetch from Binance
    tf_sec = TIMEFRAME_SECONDS.get(interval, 3600)
    all_klines = []
    current_start_ms = start_ms
    batch_num = 0

    logger.info(f"Fetching {symbol} {interval} from {start_date.date()} to {end_date.date()}...")

    while current_start_ms < end_ms:
        batch_num += 1
        raw = client.futures_klines(
            symbol=symbol,
            interval=interval,
            startTime=current_start_ms,
            endTime=end_ms,
            limit=1000,
        )
        if not raw:
            break

        for k in raw:
            all_klines.append({
                'open_time': int(k[0]),
                'open': float(k[1]),
                'high': float(k[2]),
                'low': float(k[3]),
                'close': float(k[4]),
                'volume': float(k[5]),
            })

        # Next batch starts after last candle
        last_open_time_ms = int(raw[-1][0])
        current_start_ms = last_open_time_ms + tf_sec * 1000

        sys.stdout.write(f"\r  Batch {batch_num}: {len(all_klines)} candles fetched...")
        sys.stdout.flush()

        if len(raw) < 1000:
            break  # No more data

        time_module.sleep(1)  # Rate limit

    print()  # newline after progress

    # Deduplicate by open_time
    seen = set()
    deduped = []
    for k in all_klines:
        if k['open_time'] not in seen:
            seen.add(k['open_time'])
            deduped.append(k)
    deduped.sort(key=lambda x: x['open_time'])

    logger.info(f"Fetched {len(deduped)} candles total")

    # Save to cache
    if use_cache and deduped:
        save_cache(cache_path, deduped)
        logger.info(f"Saved cache: {cache_path}")

    return deduped


# ---------------------------------------------------------------------------
# Backtest simulation
# ---------------------------------------------------------------------------

def run_backtest(klines, args, trading_start_idx=100):
    """
    Optimized bar-by-bar backtest.

    Step 1: Precompute kernels, volatility filter, and signal arrays on full data (ONCE).
    Step 2: Iterate bar by bar with state machine (SL, re-entry, entries/exits).

    This matches how TradingView evaluates: indicators are computed first,
    then strategy logic runs per bar. No lookahead bias because kernel values
    at bar i only depend on bars 0..i.
    """
    from bot.kernels import rational_quadratic, gaussian
    from bot.filters import filter_volatility
    import bot.config as cfg

    n = len(klines)

    # Build OHLCV arrays
    high_prices = np.array([k['high'] for k in klines])
    low_prices = np.array([k['low'] for k in klines])
    close_prices = np.array([k['close'] for k in klines])

    # --- Step 1: Precompute indicators on full data ---
    logger.info("Computing kernel regression (full dataset)...")
    yhat1 = rational_quadratic(close_prices, cfg.LOOKBACK_WINDOW, cfg.RELATIVE_WEIGHT, cfg.REGRESSION_LEVEL)
    yhat2 = gaussian(close_prices, cfg.LOOKBACK_WINDOW - cfg.LAG, cfg.REGRESSION_LEVEL)

    # Bullish/bearish per bar
    if cfg.USE_KERNEL_SMOOTHING:
        # Crossover mode: gaussian above/below RQ
        is_bullish = yhat2 >= yhat1  # bool array
        is_bearish = yhat2 <= yhat1
    else:
        # Rate of change mode: kernel rising/falling
        # Pine: isBullishRate = yhat1[1] < yhat1 → yhat1[i-1] < yhat1[i]
        is_bullish = np.zeros(n, dtype=bool)
        is_bearish = np.zeros(n, dtype=bool)
        for i in range(1, n):
            if not np.isnan(yhat1[i]) and not np.isnan(yhat1[i - 1]):
                is_bullish[i] = yhat1[i] > yhat1[i - 1]
                is_bearish[i] = yhat1[i] < yhat1[i - 1]

    # Volatility filter per bar
    if getattr(args, 'vol_filter_off', False):
        vol_filter = np.ones(n, dtype=bool)  # all pass
    else:
        vol_filter = filter_volatility(high_prices, low_prices, close_prices,
                                       cfg.VOLATILITY_MIN, cfg.VOLATILITY_MAX, True)

    # ATR for ATR-based SL
    atr_values = None
    if args.sl_type == 'atr':
        atr_period = args.atr_period
        tr = np.maximum(
            high_prices - low_prices,
            np.maximum(
                np.abs(high_prices - np.roll(close_prices, 1)),
                np.abs(low_prices - np.roll(close_prices, 1))
            )
        )
        tr[0] = high_prices[0] - low_prices[0]  # first bar has no prev close
        # RMA (Wilder's smoothing) — same as Pine Script ta.atr()
        atr_values = np.zeros(n)
        atr_values[:atr_period] = np.nan
        atr_values[atr_period - 1] = np.mean(tr[:atr_period])
        for j in range(atr_period, n):
            atr_values[j] = (atr_values[j - 1] * (atr_period - 1) + tr[j]) / atr_period

    logger.info("Indicators computed. Running strategy simulation...")

    # --- Step 2: Bar-by-bar state machine ---
    # Warmup: kernels need regression_level bars, but trades start at trading_start_idx
    warmup = max(cfg.REGRESSION_LEVEL + 10, trading_start_idx)

    # State
    state = 'flat'  # flat / long / short
    entry_price = 0.0
    stop_loss = 0.0
    pending_re_entry = False
    bars_since_exit = 0
    last_exit_type = None

    # Trade tracking
    trades = []
    equity_curve = []
    balance = args.capital
    slippage_pct = args.slippage / 100
    commission_pct = args.commission / 100

    current_entry_bar = 0
    current_entry_price = 0.0
    current_entry_time = ''
    current_side = ''
    current_trade_type = ''
    position_size_eth = 0.0

    def calc_sl(side, price, bar_idx=0):
        if args.sl_type == 'atr' and atr_values is not None and not np.isnan(atr_values[bar_idx]):
            atr_dist = atr_values[bar_idx] * args.atr_multiplier
            if side == 'long':
                return price - atr_dist
            else:
                return price + atr_dist
        else:
            if side == 'long':
                return price * (1 - cfg.SL_PERCENT / 100)
            else:
                return price * (1 + cfg.SL_PERCENT / 100)

    def record_close(bar_idx, exit_price_raw, reason):
        nonlocal balance, state, entry_price, stop_loss
        nonlocal pending_re_entry, bars_since_exit, last_exit_type

        side = state
        ep = exit_price_raw

        # Apply slippage
        if side == 'long':
            ep *= (1 - slippage_pct)
        else:
            ep *= (1 + slippage_pct)

        if side == 'long':
            pnl_pct = ((ep - current_entry_price) / current_entry_price) * 100
        else:
            pnl_pct = ((current_entry_price - ep) / current_entry_price) * 100

        pnl_usd = (pnl_pct / 100) * position_size_eth * current_entry_price
        balance += pnl_usd
        # Commission on exit
        exit_commission = ep * position_size_eth * commission_pct
        balance -= exit_commission
        bars_in_trade = bar_idx - current_entry_bar

        bar_time = datetime.fromtimestamp(klines[bar_idx]['open_time'] / 1000, tz=timezone.utc)
        trades.append({
            'entry_time': current_entry_time,
            'exit_time': bar_time.strftime('%Y-%m-%d %H:%M'),
            'side': current_side,
            'entry_price': round(current_entry_price, 2),
            'exit_price': round(ep, 2),
            'pnl_pct': round(pnl_pct, 2),
            'pnl_usd': round(pnl_usd, 2),
            'exit_reason': reason,
            'bars_in_trade': bars_in_trade,
            'trade_type': current_trade_type,
        })
        equity_curve.append({'time': bar_time.strftime('%Y-%m-%d %H:%M'), 'balance': round(balance, 2)})

        # Update state (mirrors Strategy.on_close)
        pending_re_entry = cfg.ENABLE_RE_ENTRY
        bars_since_exit = 0
        last_exit_type = side
        state = 'flat'
        entry_price = 0.0
        stop_loss = 0.0

    def record_open(bar_idx, side, reason):
        nonlocal state, entry_price, stop_loss, pending_re_entry, balance
        nonlocal current_entry_bar, current_entry_price, current_entry_time
        nonlocal current_side, current_trade_type, position_size_eth

        ep = float(close_prices[bar_idx])
        if side == 'long':
            ep *= (1 + slippage_pct)
        else:
            ep *= (1 - slippage_pct)

        position_size_eth = balance / ep
        # Commission on entry
        entry_commission = ep * position_size_eth * commission_pct
        balance -= entry_commission

        bar_time = datetime.fromtimestamp(klines[bar_idx]['open_time'] / 1000, tz=timezone.utc)
        current_entry_bar = bar_idx
        current_entry_price = ep
        current_entry_time = bar_time.strftime('%Y-%m-%d %H:%M')
        current_side = side
        current_trade_type = 're_entry' if reason == 're_entry' else 'signal'

        state = side
        entry_price = ep
        stop_loss = calc_sl(side, ep, bar_idx)
        pending_re_entry = False

    for i in range(warmup, n):
        # Skip bars where kernel is not yet computed
        if np.isnan(yhat1[i]) or np.isnan(yhat2[i]):
            continue

        # Detect signal changes (need bar i and i-1)
        if np.isnan(yhat1[i - 1]) or np.isnan(yhat2[i - 1]):
            continue

        bullish_change = bool(is_bullish[i] and not is_bullish[i - 1])
        bearish_change = bool(is_bearish[i] and not is_bearish[i - 1])
        vol_passes = bool(vol_filter[i])

        # --- Priority 1: Color change / TP (checked FIRST — Pine Script order) ---
        if state == 'long' and bearish_change:
            record_close(i, float(close_prices[i]), 'color_change')
            if vol_passes:
                record_open(i, 'short', 'flip')
            continue

        if state == 'short' and bullish_change:
            record_close(i, float(close_prices[i]), 'color_change')
            if vol_passes:
                record_open(i, 'long', 'flip')
            continue

        # --- Priority 2: SL hit (skipped if --no-sl) ---
        if not args.no_sl:
            if state == 'long' and low_prices[i] <= stop_loss:
                record_close(i, stop_loss, 'stop_loss')
                continue

            if state == 'short' and high_prices[i] >= stop_loss:
                record_close(i, stop_loss, 'stop_loss')
                continue

            # --- Update trailing SL (after checks — applies to next bar) ---
            if state != 'flat' and cfg.USE_DYNAMIC_SL:
                if cfg.TRAILING_SL_MODE == 'execution':
                    ref_long = high_prices[i]
                    ref_short = low_prices[i]
                else:
                    ref_long = close_prices[i]
                    ref_short = close_prices[i]
                if args.sl_type == 'atr' and atr_values is not None and not np.isnan(atr_values[i]):
                    atr_dist = atr_values[i] * args.atr_multiplier
                    if state == 'long':
                        new_sl = ref_long - atr_dist
                        if new_sl > stop_loss:
                            stop_loss = new_sl
                    elif state == 'short':
                        new_sl = ref_short + atr_dist
                        if new_sl < stop_loss:
                            stop_loss = new_sl
                else:
                    if state == 'long':
                        new_sl = ref_long * (1 - cfg.SL_PERCENT / 100)
                        if new_sl > stop_loss:
                            stop_loss = new_sl
                    elif state == 'short':
                        new_sl = ref_short * (1 + cfg.SL_PERCENT / 100)
                        if new_sl < stop_loss:
                            stop_loss = new_sl

        # --- Priority 3: Re-entry (OPPOSITE direction — Pine Script behavior) ---
        # No cascade limit — Pine Script allows unlimited re-entries with timeout
        if pending_re_entry and cfg.ENABLE_RE_ENTRY:
            bars_since_exit += 1
            if bars_since_exit > cfg.RE_ENTRY_DELAY + 5:
                pending_re_entry = False
            elif bars_since_exit > cfg.RE_ENTRY_DELAY and vol_passes and state == 'flat':
                if last_exit_type == 'long':
                    record_open(i, 'short', 're_entry')
                    continue
                elif last_exit_type == 'short':
                    record_open(i, 'long', 're_entry')
                    continue

        # --- Priority 4: Standard entry ---
        if state == 'flat':
            if bullish_change and vol_passes:
                record_open(i, 'long', 'bullish_change')
                continue
            if bearish_change and vol_passes:
                record_open(i, 'short', 'bearish_change')
                continue

    # Close any open position at end of data
    if state != 'flat':
        record_close(n - 1, float(close_prices[-1]), 'end_of_data')

    return trades, equity_curve, balance


# ---------------------------------------------------------------------------
# Metrics calculation
# ---------------------------------------------------------------------------

def calculate_metrics(trades, initial_capital, final_balance):
    """Calculate all backtest performance metrics."""
    if not trades:
        return {'total_trades': 0}

    pnls = [t['pnl_pct'] for t in trades]
    pnls_usd = [t['pnl_usd'] for t in trades]
    bars = [t['bars_in_trade'] for t in trades]

    wins = [p for p in pnls if p > 0]
    losses = [p for p in pnls if p <= 0]
    wins_usd = [p for p in pnls_usd if p > 0]
    losses_usd = [p for p in pnls_usd if p < 0]

    # Long / Short breakdown
    long_trades = [t for t in trades if t['side'] == 'long']
    short_trades = [t for t in trades if t['side'] == 'short']
    long_wins = [t for t in long_trades if t['pnl_pct'] > 0]
    short_wins = [t for t in short_trades if t['pnl_pct'] > 0]
    long_gross_profit = sum(t['pnl_usd'] for t in long_trades if t['pnl_usd'] > 0)
    long_gross_loss = abs(sum(t['pnl_usd'] for t in long_trades if t['pnl_usd'] < 0))
    short_gross_profit = sum(t['pnl_usd'] for t in short_trades if t['pnl_usd'] > 0)
    short_gross_loss = abs(sum(t['pnl_usd'] for t in short_trades if t['pnl_usd'] < 0))

    # Max drawdown (equity curve based)
    peak = initial_capital
    max_dd_usd = 0
    max_dd_pct = 0
    running_balance = initial_capital
    for t in trades:
        running_balance += t['pnl_usd']
        if running_balance > peak:
            peak = running_balance
        dd = peak - running_balance
        dd_pct = (dd / peak * 100) if peak > 0 else 0
        if dd > max_dd_usd:
            max_dd_usd = dd
        if dd_pct > max_dd_pct:
            max_dd_pct = dd_pct

    gross_profit = sum(wins_usd) if wins_usd else 0
    gross_loss = abs(sum(losses_usd)) if losses_usd else 0
    profit_factor = (gross_profit / gross_loss) if gross_loss > 0 else float('inf')

    net_profit_usd = final_balance - initial_capital
    net_profit_pct = (net_profit_usd / initial_capital) * 100

    # Sharpe ratio (trade-level, annualized assuming ~8760 hourly bars/year)
    if len(pnls) > 1:
        avg_return = np.mean(pnls)
        std_return = np.std(pnls, ddof=1)
        sharpe = (avg_return / std_return * np.sqrt(len(pnls))) if std_return > 0 else 0
    else:
        sharpe = 0

    return {
        'total_trades': len(trades),
        'wins': len(wins),
        'losses': len(losses),
        'win_rate': len(wins) / len(trades) * 100 if trades else 0,
        'net_profit_usd': net_profit_usd,
        'net_profit_pct': net_profit_pct,
        'profit_factor': profit_factor,
        'max_drawdown_usd': max_dd_usd,
        'max_drawdown_pct': max_dd_pct,
        'avg_win': np.mean(wins) if wins else 0,
        'avg_loss': np.mean(losses) if losses else 0,
        'avg_rr': (abs(np.mean(wins)) / abs(np.mean(losses))) if losses else float('inf'),
        'best_trade': max(pnls),
        'worst_trade': min(pnls),
        'avg_bars_all': np.mean(bars),
        'avg_bars_win': np.mean([t['bars_in_trade'] for t in trades if t['pnl_pct'] > 0]) if wins else 0,
        'avg_bars_loss': np.mean([t['bars_in_trade'] for t in trades if t['pnl_pct'] <= 0]) if losses else 0,
        'sharpe': sharpe,
        # Long breakdown
        'long_trades': len(long_trades),
        'long_win_rate': len(long_wins) / len(long_trades) * 100 if long_trades else 0,
        'long_pf': (long_gross_profit / long_gross_loss) if long_gross_loss > 0 else float('inf'),
        # Short breakdown
        'short_trades': len(short_trades),
        'short_win_rate': len(short_wins) / len(short_trades) * 100 if short_trades else 0,
        'short_pf': (short_gross_profit / short_gross_loss) if short_gross_loss > 0 else float('inf'),
    }


# ---------------------------------------------------------------------------
# Output
# ---------------------------------------------------------------------------

def print_results(args, metrics, initial_capital, final_balance):
    """Print formatted backtest results."""
    m = metrics
    print()
    print('=' * 55)
    print('  BACKTEST RESULTS')
    print('=' * 55)
    print(f'  Period: {args.start} to {args.end}')
    print(f'  Symbol: {args.symbol} {args.timeframe}')
    print(f'  Initial Capital: {initial_capital:,.2f} USD')
    print()
    print('  Parameters:')
    print(f'    Kernel: h={args.lookback_window}, r={args.relative_weight}, '
          f'x={args.regression_level}, lag={args.lag}, smoothing={args.use_kernel_smoothing}')
    if args.sl_type == 'atr':
        print(f'    SL: ATR({args.atr_period})x{args.atr_multiplier} {"dynamic" if args.use_dynamic_sl else "fixed"} (trailing: {args.trailing_mode})')
    else:
        print(f'    SL: {args.sl_percent}% {"dynamic" if args.use_dynamic_sl else "fixed"} (trailing: {args.trailing_mode})')
    print(f'    Vol filter: {args.volatility_min}/{args.volatility_max}')
    print(f'    Re-entry: {"enabled" if args.enable_re_entry else "disabled"}, delay={args.re_entry_delay}')
    if args.slippage > 0:
        print(f'    Slippage: {args.slippage}%')
    if args.commission > 0:
        print(f'    Commission: {args.commission}% per trade')
    print()
    if m['total_trades'] == 0:
        print('  No trades executed.')
        print('=' * 55)
        return

    print('  Results:'  )
    print(f'    Net Profit:     {m["net_profit_usd"]:+,.0f} USD ({m["net_profit_pct"]:+.2f}%)')
    print(f'    Final Balance:  {final_balance:,.2f} USD')
    print(f'    Max Drawdown:   {m["max_drawdown_usd"]:,.0f} USD ({m["max_drawdown_pct"]:.2f}%)')
    print(f'    Total Trades:   {m["total_trades"]}')
    print(f'    Win Rate:       {m["win_rate"]:.2f}% ({m["wins"]}/{m["total_trades"]})')
    print(f'    Profit Factor:  {m["profit_factor"]:.2f}')
    print(f'    Avg Win:        {m["avg_win"]:+.2f}%')
    print(f'    Avg Loss:       {m["avg_loss"]:+.2f}%')
    print(f'    Avg R:R:        {m["avg_rr"]:.2f}')
    print(f'    Best Trade:     {m["best_trade"]:+.2f}%')
    print(f'    Worst Trade:    {m["worst_trade"]:+.2f}%')
    print(f'    Avg Bars:       {m["avg_bars_all"]:.1f} (win: {m["avg_bars_win"]:.1f}, loss: {m["avg_bars_loss"]:.1f})')
    print(f'    Sharpe Ratio:   {m["sharpe"]:.2f}')
    print()
    print(f'    Long:  trades={m["long_trades"]}, win_rate={m["long_win_rate"]:.1f}%, PF={m["long_pf"]:.2f}')
    print(f'    Short: trades={m["short_trades"]}, win_rate={m["short_win_rate"]:.1f}%, PF={m["short_pf"]:.2f}')
    print('=' * 55)
    print()


def save_trades_csv(trades, filepath):
    """Save trade list to CSV."""
    if not trades:
        return
    fieldnames = [
        'entry_time', 'exit_time', 'side', 'entry_price', 'exit_price',
        'pnl_pct', 'pnl_usd', 'exit_reason', 'bars_in_trade', 'trade_type',
    ]
    with open(filepath, 'w', newline='') as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(trades)
    logger.info(f"Saved {len(trades)} trades to {filepath}")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    args = parse_args()

    # Load .env for Binance API keys
    load_dotenv()

    # Override bot config with CLI params (before importing Strategy)
    apply_config(args)

    # Parse dates
    start_date = datetime.strptime(args.start, '%Y-%m-%d').replace(tzinfo=timezone.utc)
    end_date = datetime.strptime(args.end, '%Y-%m-%d').replace(tzinfo=timezone.utc)

    # 3 months warmup before trading start — kernels need stable history
    fetch_start = start_date - timedelta(days=92)

    # Connect to Binance Futures MAINNET for historical data
    # Public klines endpoint doesn't require authentication
    client = Client('', '', testnet=False)

    # Fetch data
    klines = fetch_historical_klines(
        client, args.symbol, args.timeframe,
        fetch_start, end_date,
        use_cache=not args.no_cache,
    )

    if len(klines) < 200:
        print(f"ERROR: Not enough data. Got {len(klines)} candles, need at least 200")
        sys.exit(1)

    # Find the bar index where trading period starts (trades only from here)
    start_ms = int(start_date.timestamp() * 1000)
    trading_start_idx = 0
    for idx, k in enumerate(klines):
        if k['open_time'] >= start_ms:
            trading_start_idx = idx
            break

    logger.info(f"Data: {len(klines)} candles, warmup bars: {trading_start_idx}, trading from bar {trading_start_idx}")

    # --- Step 1: Print config for verification ---
    print()
    print('=== BACKTEST CONFIG ===')
    print(f'  SL Type:         {"DISABLED" if args.no_sl else "percent"}')
    print(f'  SL %:            {"-" if args.no_sl else args.sl_percent}')
    print(f'  Dynamic SL:      {"-" if args.no_sl else args.use_dynamic_sl}')
    print(f'  Re-entry:        {args.enable_re_entry}')
    print(f'  Re-entry delay:  {args.re_entry_delay}')
    print(f'  Re-entry dir:    OPPOSITE (Pine Script behavior)')
    print(f'  Cascade limit:   NONE (unlimited re-entries, timeout {args.re_entry_delay + 5} bars)')
    print(f'  Position flip:   YES (on color change)')
    print(f'  Priority order:  1) Color change (TP)  2) SL hit  (Pine Script order)')
    print(f'  Kernel:          h={args.lookback_window}, r={args.relative_weight}, x={args.regression_level}, lag={args.lag}')
    print(f'  Vol filter:      {args.volatility_min}/{args.volatility_max}')
    print(f'  Capital:         {args.capital:,.0f} USD')
    print(f'  Slippage:        {args.slippage}%')
    print(f'  Warmup:          {trading_start_idx} bars ({fetch_start.date()} to {start_date.date()})')
    print()

    # --- Debug: signal dump around Jan 1-5 2025 ---
    from bot.kernels import rational_quadratic, gaussian
    from bot.filters import filter_volatility
    import bot.config as cfg2

    close_all = np.array([k['close'] for k in klines])
    high_all = np.array([k['high'] for k in klines])
    low_all = np.array([k['low'] for k in klines])
    yhat1_dbg = rational_quadratic(close_all, cfg2.LOOKBACK_WINDOW, cfg2.RELATIVE_WEIGHT, cfg2.REGRESSION_LEVEL)
    yhat2_dbg = gaussian(close_all, cfg2.LOOKBACK_WINDOW - cfg2.LAG, cfg2.REGRESSION_LEVEL)
    is_bull_dbg = yhat2_dbg >= yhat1_dbg
    vol_dbg = filter_volatility(high_all, low_all, close_all, cfg2.VOLATILITY_MIN, cfg2.VOLATILITY_MAX, True)

    # Show bars from Jan 1 00:00 to Jan 5 00:00
    debug_start_ms = int(datetime(2025, 1, 1, 0, 0, tzinfo=timezone.utc).timestamp() * 1000)
    debug_end_ms = int(datetime(2025, 1, 5, 0, 0, tzinfo=timezone.utc).timestamp() * 1000)

    print('=== SIGNAL DEBUG: Jan 1-5 2025 ===')
    print(f'{"Bar":>5} | {"Time":>16} | {"Close":>9} | {"yhat1":>9} | {"yhat2":>9} | {"Bull":>4} | {"BChg":>4} | {"BrChg":>5} | {"Vol":>3} | Action')
    print('-' * 105)
    for i, k in enumerate(klines):
        if k['open_time'] < debug_start_ms or k['open_time'] > debug_end_ms:
            continue
        if np.isnan(yhat1_dbg[i]) or i < 1 or np.isnan(yhat1_dbg[i-1]):
            continue
        bt = datetime.fromtimestamp(k['open_time'] / 1000, tz=timezone.utc)
        bull = bool(is_bull_dbg[i])
        bull_chg = bool(is_bull_dbg[i] and not is_bull_dbg[i-1])
        bear_chg = bool(not is_bull_dbg[i] and is_bull_dbg[i-1])
        vp = bool(vol_dbg[i])
        action = ''
        if bull_chg and vp:
            action = 'OPEN LONG'
        elif bear_chg and vp:
            action = 'OPEN SHORT'
        elif bull_chg and not vp:
            action = '(bull blocked)'
        elif bear_chg and not vp:
            action = '(bear blocked)'
        # Mark TV entry
        note = ''
        if bt.hour == 5 and bt.day == 2:
            note = ' <-- old trigger'
        if bt.hour == 19 and bt.day == 3:
            note = ' <-- TV trigger'
        print(f'{i:>5} | {bt.strftime("%Y-%m-%d %H:%M"):>16} | {close_all[i]:>9.2f} | {yhat1_dbg[i]:>9.2f} | {yhat2_dbg[i]:>9.2f} | {"Y" if bull else "N":>4} | {"Y" if bull_chg else "-":>4} | {"Y" if bear_chg else "-":>5} | {"Y" if vp else "N":>3} | {action}{note}')
    print()

    # --- Check specific prices ---
    for check_dt in [datetime(2025, 1, 2, 5, 0, tzinfo=timezone.utc),
                     datetime(2025, 1, 3, 19, 0, tzinfo=timezone.utc)]:
        check_ms = int(check_dt.timestamp() * 1000)
        for k in klines:
            if k['open_time'] == check_ms:
                print(f'Binance price at {check_dt.strftime("%Y-%m-%d %H:%M")} UTC: close={k["close"]:.2f}')
                break
    print()

    # Run backtest
    print(f"Backtesting {args.symbol} {args.timeframe} | {args.start} to {args.end} | Capital: {args.capital:,.0f} USD\n")

    trades, equity_curve, final_balance = run_backtest(klines, args, trading_start_idx)

    # Metrics (all trades are already within period since warmup covers pre-period)
    metrics = calculate_metrics(trades, args.capital, final_balance)
    print_results(args, metrics, args.capital, final_balance)

    # --- First 10 trades for TV comparison ---
    print('First 10 trades:')
    print(f'{"#":>3} | {"Entry Time":>16} | {"Side":>5} | {"Entry":>9} | {"Exit":>9} | {"Reason":>13} | {"PnL%":>7} | {"Bars":>4} | {"Type":>8}')
    print('-' * 100)
    for idx, t in enumerate(trades[:10], 1):
        print(f'{idx:>3} | {t["entry_time"]:>16} | {t["side"]:>5} | {t["entry_price"]:>9} | {t["exit_price"]:>9} | {t["exit_reason"]:>13} | {t["pnl_pct"]:>+7.2f}% | {t["bars_in_trade"]:>4} | {t["trade_type"]:>8}')
    print()

    # Save CSV
    save_trades_csv(trades, args.output)
    print(f"Trade log saved: {args.output}")
    print(f"Total trades in period: {len(trades)}")


if __name__ == '__main__':
    main()
