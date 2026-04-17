"""
Trading Bot Standalone — ETHUSDT Kernel Regression
Main entry point — runs trading loop in background thread, FastAPI dashboard on main thread.
"""

import logging
import threading
import time as time_module
from datetime import datetime, timezone

import numpy as np
import uvicorn

from bot.config import *
from bot.exchange import Exchange
from bot.data_fetcher import DataFetcher
from bot.strategy import Strategy
from bot.kill_switch import KillSwitch
from bot.trade_logger import TradeLogger
from bot.state import SharedState
from bot.utils import sync_time
from api.server import create_app

# Proactive time synchronisation interval (6 hours in seconds)
TIME_SYNC_INTERVAL = 6 * 3600

# Setup logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler('trading_bot.log'),
        logging.StreamHandler(),
    ]
)
logger = logging.getLogger(__name__)


# ------------------------------------------------------------------
# Config helpers
# ------------------------------------------------------------------

def load_config_as_dict():
    """Load current config.py values into a dict for shared state."""
    return {
        'lookback_window': LOOKBACK_WINDOW,
        'relative_weight': RELATIVE_WEIGHT,
        'regression_level': REGRESSION_LEVEL,
        'lag': LAG,
        'use_kernel_smoothing': USE_KERNEL_SMOOTHING,
        'sl_percent': SL_PERCENT,
        'use_dynamic_sl': USE_DYNAMIC_SL,
        'trailing_sl_mode': TRAILING_SL_MODE,
        'volatility_min': VOLATILITY_MIN,
        'volatility_max': VOLATILITY_MAX,
        'enable_re_entry': ENABLE_RE_ENTRY,
        're_entry_delay': RE_ENTRY_DELAY,
        'require_color_confirmation': REQUIRE_COLOR_CONFIRMATION,
        'kill_switch_consecutive_losses': KILL_SWITCH_CONSECUTIVE_LOSSES,
        'kill_switch_equity_drop_percent': KILL_SWITCH_EQUITY_DROP_PERCENT,
        'kill_switch_pause_hours': KILL_SWITCH_PAUSE_HOURS,
        'leverage': LEVERAGE,
        'position_size_pct': POSITION_SIZE_PCT,
        'symbol': SYMBOL,
        'timeframe': TIMEFRAME,
    }


def apply_config_changes(config, strategy, kill_switch):
    """Apply config dict changes to live strategy/kill_switch instances."""
    import bot.config as cfg

    for key, value in config.items():
        upper_key = key.upper()
        if hasattr(cfg, upper_key):
            old = getattr(cfg, upper_key)
            if old != value:
                setattr(cfg, upper_key, value)
                logger.info(f"[CONFIG] Applied {key}: {old} -> {value}")

    logger.info("[CONFIG] Config changes applied to bot modules")


# ------------------------------------------------------------------
# Trade execution helpers
# ------------------------------------------------------------------

def execute_open(exchange, strategy, kill_switch, trade_logger, side, close_price,
                 trade_type='signal', bar_index=0):
    """Open a position and update all components."""
    logger.info(f"[TRADE] Opening {side.upper()} (type={trade_type})...")

    if side == 'long':
        success, fill_price = exchange.open_long(close_price)
    else:
        success, fill_price = exchange.open_short(close_price)

    if not success:
        logger.error(f"[TRADE] Failed to open {side.upper()}")
        return False

    strategy.on_open(side, fill_price)

    slippage = ((fill_price - close_price) / close_price * 100) if close_price else 0
    trade_logger.log_open(
        timestamp=datetime.now(timezone.utc).isoformat(),
        action=f'open_{side}',
        entry_price=close_price,
        fill_price=fill_price,
        slippage_pct=slippage,
        trade_type=trade_type,
        bar_index=bar_index,
    )

    logger.info(
        f"[TRADE] Opened {side.upper()} at {fill_price:.2f} "
        f"(signal price: {close_price:.2f}, slippage: {slippage:.3f}%)"
    )
    return True


def execute_close(exchange, strategy, kill_switch, trade_logger, side, reason,
                  bar_index=0):
    """Close a position and update all components."""
    entry_price = strategy.entry_price
    current_pos = exchange.get_current_position()
    position_qty = current_pos['amount'] if current_pos else 0

    logger.info(f"[TRADE] Closing {side.upper()} — reason: {reason}...")

    success, fill_price = exchange.close_position()

    if not success:
        logger.error(f"[TRADE] Failed to close {side.upper()}")
        return False

    if side == 'long':
        pnl_pct = ((fill_price - entry_price) / entry_price) * 100
    else:
        pnl_pct = ((entry_price - fill_price) / entry_price) * 100
    pnl_usd = (pnl_pct / 100) * entry_price * position_qty

    current_balance = exchange.get_account_balance()
    kill_switch.evaluate(pnl_pct, current_balance)
    strategy.on_close(side)

    trade_logger.log_close(
        timestamp=datetime.now(timezone.utc).isoformat(),
        action=f'close_{side}',
        exit_price=fill_price,
        pnl_usd=pnl_usd,
        pnl_pct=pnl_pct,
        exit_reason=reason,
        balance_after=current_balance,
        consecutive_losses=kill_switch.consecutive_losses,
        bar_index=bar_index,
    )

    logger.info(
        f"[TRADE] Closed {side.upper()} at {fill_price:.2f} — "
        f"PnL: {pnl_pct:+.2f}% ({pnl_usd:+.2f} USD) — reason: {reason}"
    )
    return True


# ------------------------------------------------------------------
# Smart sleep — checks emergency actions every 1 second
# ------------------------------------------------------------------

def smart_sleep(state, seconds):
    """Sleep in 1-second increments for fast emergency response."""
    end_time = time_module.time() + seconds
    while time_module.time() < end_time:
        if state.emergency_close.is_set():
            return 'emergency_close'
        time_module.sleep(1)
    return 'normal'


# ------------------------------------------------------------------
# Trading loop (runs in background thread)
# ------------------------------------------------------------------

def trading_loop(state, exchange, strategy, data_fetcher, kill_switch, trade_logger):
    """Main trading loop — runs continuously in a daemon thread."""
    logger.info("[LOOP] Trading thread started")
    last_processed_candle = None
    last_time_sync = time_module.time()

    while True:
        try:
            # Check emergency close
            if state.emergency_close.is_set():
                state.emergency_close.clear()
                if strategy.state != 'flat':
                    logger.warning("[EMERGENCY] Closing position immediately")
                    execute_close(exchange, strategy, kill_switch, trade_logger,
                                  strategy.state, 'emergency_dashboard')
                    # Update shared state
                    state.update_position('flat')
                    state.update_balance(exchange.get_account_balance(), kill_switch.peak_equity)
                else:
                    logger.info("[EMERGENCY] No position to close")

            # Check manual pause
            if state.trading_paused:
                time_module.sleep(10)
                continue

            # Check if config changed — use snapshot to avoid partial reads
            if state.config_changed.is_set():
                config_snapshot = state.get_config_snapshot()
                state.config_changed.clear()
                apply_config_changes(config_snapshot, strategy, kill_switch)

            # Periodic proactive time synchronisation (every 6 hours)
            if time_module.time() - last_time_sync >= TIME_SYNC_INTERVAL:
                logger.info("[TIMESYNC] Periodic time sync...")
                sync_time(exchange.client)
                last_time_sync = time_module.time()

            # Wait for next candle close
            seconds = data_fetcher.time_until_next_candle(TIMEFRAME)
            total_wait = seconds + CANDLE_CLOSE_BUFFER_SEC
            minutes = int(total_wait) // 60
            secs = int(total_wait) % 60
            state.next_candle_time = time_module.time() + total_wait
            logger.info(f"[LOOP] Sleeping {minutes}m {secs}s until next candle + buffer...")

            wake_reason = smart_sleep(state, total_wait)
            if wake_reason == 'emergency_close':
                continue  # Will handle at top of loop

            # Fetch OHLCV data
            candles = data_fetcher.get_klines(SYMBOL, TIMEFRAME, KLINES_LIMIT)
            if len(candles) < REGRESSION_LEVEL + 10:
                logger.error(f"[LOOP] Not enough candles ({len(candles)}), need at least {REGRESSION_LEVEL + 10}")
                continue

            # Duplicate execution guard
            latest_candle_time = candles[-1]['open_time']
            if latest_candle_time == last_processed_candle:
                logger.info("[LOOP] Candle already processed — skipping")
                continue
            last_processed_candle = latest_candle_time

            ohlcv = {
                'open': np.array([c['open'] for c in candles]),
                'high': np.array([c['high'] for c in candles]),
                'low': np.array([c['low'] for c in candles]),
                'close': np.array([c['close'] for c in candles]),
                'volume': np.array([c['volume'] for c in candles]),
            }

            # Calculate signal
            result = strategy.calculate_signals(ohlcv)
            action = result['action']
            reason = result.get('reason')
            details = result['details']

            logger.info(
                f"[SIGNAL] yhat1={details['yhat1']:.2f}, yhat2={details['yhat2']:.2f}, "
                f"bullish={details['is_bullish']}, bearish={not details['is_bullish']}, "
                f"change={'bull' if details['bullish_change'] else 'bear' if details['bearish_change'] else 'none'}, "
                f"vol_filter={'PASS' if details['vol_passes'] else 'BLOCK'}"
            )
            logger.info(f"[LOOP] state={details['state']}, action={action or 'NONE'}")

            # Update shared state for dashboard
            state.update_signal({**details, "action": action, "reason": reason, "symbol": SYMBOL})
            balance = exchange.get_account_balance()
            state.update_balance(balance, kill_switch.peak_equity)

            # Update position info in shared state
            if strategy.state != 'flat':
                current_price = float(ohlcv['close'][-1])
                if strategy.state == 'long':
                    pnl = ((current_price - strategy.entry_price) / strategy.entry_price) * 100
                else:
                    pnl = ((strategy.entry_price - current_price) / strategy.entry_price) * 100
                pos = exchange.get_current_position()
                size = pos['amount'] if pos else 0
                state.update_position(strategy.state, strategy.entry_price, strategy.stop_loss, size, pnl)
            else:
                state.update_position('flat')

            if action is None:
                logger.info("[LOOP] No signal — continuing")
                continue

            # Check kill switch (only for opens)
            if action in ('open_long', 'open_short') and kill_switch.is_paused():
                ks = kill_switch.get_status()
                logger.warning(f"[KILL] Signal rejected (paused): {action} — resume at {ks['pause_until']}")
                continue

            # Check manual pause (only for opens)
            if action in ('open_long', 'open_short') and state.trading_paused:
                logger.warning(f"[LOOP] Signal rejected (manual pause): {action}")
                continue

            # Execute trade
            close_price = float(ohlcv['close'][-1])
            bar_idx = len(candles) - 1
            trade_type = reason if reason in ('re_entry',) else 'signal'

            if action == 'open_long':
                execute_open(exchange, strategy, kill_switch, trade_logger, 'long', close_price,
                             trade_type=trade_type, bar_index=bar_idx)
            elif action == 'open_short':
                execute_open(exchange, strategy, kill_switch, trade_logger, 'short', close_price,
                             trade_type=trade_type, bar_index=bar_idx)
            elif action == 'close_long':
                execute_close(exchange, strategy, kill_switch, trade_logger, 'long', reason,
                              bar_index=bar_idx)
            elif action == 'close_short':
                execute_close(exchange, strategy, kill_switch, trade_logger, 'short', reason,
                              bar_index=bar_idx)

            # Update shared state after trade
            balance = exchange.get_account_balance()
            state.update_balance(balance, kill_switch.peak_equity)
            if strategy.state != 'flat':
                pos = exchange.get_current_position()
                size = pos['amount'] if pos else 0
                state.update_position(strategy.state, strategy.entry_price, strategy.stop_loss, size, 0)
            else:
                state.update_position('flat')

        except Exception as e:
            logger.error(f"[LOOP] Error: {e}", exc_info=True)
            logger.info("[LOOP] Retrying in 60s...")
            time_module.sleep(60)


# ------------------------------------------------------------------
# Main
# ------------------------------------------------------------------

def main():
    print("=" * 50)
    print("Trading Bot Standalone")
    print("=" * 50)

    # 1. Create shared state
    state = SharedState()
    state.config = load_config_as_dict()

    # 2. Initialize components
    exchange = Exchange()
    data_fetcher = DataFetcher(exchange.client)
    strategy = Strategy()
    trade_logger = TradeLogger('trade_log.csv')

    balance = exchange.get_account_balance()
    logger.info(f"Connected to Binance Testnet — Balance: {balance} USDT")

    # 3. Sync position from exchange (crash recovery)
    exchange.sync_position_from_exchange()
    strategy.sync_state(exchange.position, exchange.entry_price)

    kill_switch = KillSwitch(initial_equity=float(balance))
    logger.info(f"Kill switch initialized (peak equity: {balance} USDT)")
    logger.info(f"Strategy state: {strategy.state.upper()}")

    # Update shared state
    state.update_balance(balance, kill_switch.peak_equity)
    if strategy.state != 'flat':
        pos = exchange.get_current_position()
        size = pos['amount'] if pos else 0
        state.update_position(strategy.state, strategy.entry_price, strategy.stop_loss, size, 0)

    # Expose exchange client to shared state (for live price in API)
    state.exchange_client = exchange.client

    # 4. Start trading loop in background thread
    trading_thread = threading.Thread(
        target=trading_loop,
        args=(state, exchange, strategy, data_fetcher, kill_switch, trade_logger),
        daemon=True,
    )
    trading_thread.start()
    state.bot_running = True
    logger.info("[MAIN] Trading thread started")

    # 5. Start FastAPI server (blocks main thread)
    app = create_app(state, kill_switch)
    logger.info("[MAIN] Starting dashboard on http://0.0.0.0:8080")

    try:
        uvicorn.run(app, host="0.0.0.0", port=8080, log_level="warning")
    except KeyboardInterrupt:
        logger.info("[MAIN] Bot stopped by user (Ctrl+C)")
        state.bot_running = False


if __name__ == '__main__':
    main()
