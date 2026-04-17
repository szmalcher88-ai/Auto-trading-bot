import os
from dotenv import load_dotenv

load_dotenv()

# === BINANCE TESTNET ===
TESTNET_API_KEY = os.getenv('BINANCE_TESTNET_API_KEY')
TESTNET_SECRET_KEY = os.getenv('BINANCE_TESTNET_SECRET_KEY')
TESTNET_BASE_URL = 'https://testnet.binancefuture.com'

# === POSITION SIZING ===
LEVERAGE = 1              # 1x = no leverage
POSITION_SIZE_PCT = 50.0  # % of portfolio per trade
MAX_POSITIONS = 1
STOP_LOSS_PERCENT = 2.7   # used by set_stop_loss() if called

# === STRATEGY PARAMETERS ===
TIMEFRAME = '1h'
SYMBOL = 'ETHUSDT'
LOOKBACK_WINDOW = 100        # h — kernel regression lookback
RELATIVE_WEIGHT = 10.0       # r — relative weighting
REGRESSION_LEVEL = 69        # x — start at bar
LAG = 1                      # lag for gaussian kernel
USE_KERNEL_SMOOTHING = True  # True = crossover mode, False = rate of change
SL_TYPE = 'atr'              # 'atr' or 'percent'
SL_PERCENT = 2.7             # stop loss percentage (used when SL_TYPE = 'percent')
ATR_PERIOD = 20              # ATR period for ATR-based SL
ATR_MULTIPLIER = 6.0         # ATR multiplier for ATR-based SL
USE_DYNAMIC_SL = True        # trailing SL
TRAILING_SL_MODE = 'pine'  # 'pine' = close-based, 'execution' = high/low-based
VOLATILITY_MIN = 5           # volatility filter min (ATR short period)
VOLATILITY_MAX = 10          # volatility filter max (ATR long period)
ENABLE_RE_ENTRY = True       # re-entry after SL
RE_ENTRY_DELAY = 1           # bars to wait before re-entry
REQUIRE_COLOR_CONFIRMATION = False

# === COMMISSION ===
COMMISSION_PCT = 0.05  # 0.05% per trade — Binance Futures taker fee

# === DATA ===
KLINES_LIMIT = 200           # how many candles to fetch from Binance
CANDLE_CLOSE_BUFFER_SEC = 5  # seconds to wait after candle close before fetching

# === KILL SWITCH ===
KILL_SWITCH_CONSECUTIVE_LOSSES = 5
KILL_SWITCH_EQUITY_DROP_PERCENT = 10.0
KILL_SWITCH_PAUSE_HOURS = 24
