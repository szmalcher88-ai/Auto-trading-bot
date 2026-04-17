import logging
from datetime import datetime
from binance.client import Client
from bot.config import (
    TESTNET_API_KEY, TESTNET_SECRET_KEY, TESTNET_BASE_URL,
    SYMBOL, STOP_LOSS_PERCENT, LEVERAGE, POSITION_SIZE_PCT,
)
from bot.utils import safe_api_call, sync_time

logger = logging.getLogger(__name__)


class Exchange:
    """Binance Futures Testnet connection and order management."""

    def __init__(self):
        self.client = Client(
            api_key=TESTNET_API_KEY,
            api_secret=TESTNET_SECRET_KEY,
            testnet=True,
        )
        self.client.REQUEST_TIMEOUT = 60
        self.client.FUTURES_URL = f'{TESTNET_BASE_URL}/fapi'
        self.client.FUTURES_DATA_URL = f'{TESTNET_BASE_URL}/fapi'

        self.position = None   # 'long', 'short', or None
        self.entry_price = 0

        sync_time(self.client)
        self._setup_symbol()

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _api(self, fn, *args, **kwargs):
        """Shorthand for safe_api_call with self.client."""
        return safe_api_call(self.client, fn, *args, **kwargs)

    def _setup_symbol(self):
        """Set leverage and margin type for the trading symbol."""
        # Set leverage
        try:
            self._api(
                self.client.futures_change_leverage,
                symbol=SYMBOL,
                leverage=LEVERAGE,
            )
            logger.info(f"[EXCHANGE] Leverage set to {LEVERAGE}x for {SYMBOL}")
        except Exception as e:
            logger.warning(f"[EXCHANGE] Failed to set leverage: {e}")

        # Set margin type to CROSSED
        from binance.exceptions import BinanceAPIException
        try:
            self.client.futures_change_margin_type(
                symbol=SYMBOL,
                marginType='CROSSED',
            )
            logger.info(f"[EXCHANGE] Margin type: CROSSED")
        except BinanceAPIException as e:
            if e.code == -4046:  # "No need to change margin type"
                logger.info(f"[EXCHANGE] Margin type: CROSSED (already set)")
            else:
                logger.warning(f"[EXCHANGE] Failed to set margin type: {e}")
        except Exception as e:
            logger.warning(f"[EXCHANGE] Failed to set margin type: {e}")

    def calculate_position_size(self):
        """Calculate position size as % of portfolio value."""
        balance = float(self.get_account_balance())
        position_value = balance * (POSITION_SIZE_PCT / 100)

        # Get current ETH price
        ticker = self._api(self.client.futures_symbol_ticker, symbol=SYMBOL)
        eth_price = float(ticker['price'])

        # Calculate ETH quantity (round to 3 decimal places — Binance minimum)
        quantity = round(position_value / eth_price, 3)

        # Minimum order size check (Binance minimum for ETHUSDT = 0.001 ETH)
        if quantity < 0.001:
            logger.warning(f"[EXCHANGE] Position size too small: {quantity} ETH")
            return 0.0

        logger.info(
            f"[EXCHANGE] Position size: {quantity} ETH "
            f"({position_value:.2f} USDT, {POSITION_SIZE_PCT}% of {balance:.2f})"
        )
        return quantity

    # ------------------------------------------------------------------
    # Account / Position queries
    # ------------------------------------------------------------------

    def get_account_balance(self):
        """Get USDT available balance."""
        try:
            account = self._api(self.client.futures_account)
            for asset in account['assets']:
                if asset['asset'] == 'USDT':
                    return float(asset['availableBalance'])
            return 0
        except Exception as e:
            logger.error(f"Error getting balance: {e}")
            return 0

    def get_current_position(self):
        """Return current position dict or None."""
        try:
            positions = self._api(self.client.futures_position_information, symbol=SYMBOL)
            for pos in positions:
                if float(pos['positionAmt']) != 0:
                    return {
                        'side': 'long' if float(pos['positionAmt']) > 0 else 'short',
                        'amount': abs(float(pos['positionAmt'])),
                        'entry_price': float(pos['entryPrice']),
                    }
            return None
        except Exception as e:
            logger.error(f"Error getting position: {e}")
            return None

    def sync_position_from_exchange(self):
        """Sync internal state with actual Binance position."""
        try:
            pos = self.get_current_position()
            if pos:
                self.position = pos['side']
                self.entry_price = pos['entry_price']
                logger.info(
                    f"[SYNC] Position synced from exchange: {pos['side'].upper()}, "
                    f"entry={pos['entry_price']}, size={pos['amount']}"
                )
            else:
                self.position = None
                self.entry_price = 0
                logger.info("[SYNC] No open position on exchange")
        except Exception as e:
            self.position = None
            self.entry_price = 0
            logger.error(f"[SYNC] Failed to sync position: {e}")

    # ------------------------------------------------------------------
    # Order confirmation
    # ------------------------------------------------------------------

    def confirm_order_fill(self, order_id, signal_price=0, max_attempts=5):
        """Poll Binance to confirm MARKET order fill.

        Returns (filled: bool, avg_price: float).
        """
        import time as time_module

        status = 'UNKNOWN'
        for attempt in range(max_attempts):
            try:
                order_info = self._api(
                    self.client.futures_get_order,
                    symbol=SYMBOL,
                    orderId=order_id,
                )
                status = order_info.get('status', '')
                avg_price = float(order_info.get('avgPrice', 0))

                if status == 'FILLED':
                    if signal_price and avg_price:
                        slippage = ((avg_price - signal_price) / signal_price) * 100
                        logger.info(
                            f"[FILL] Order {order_id} FILLED at {avg_price} "
                            f"(signal price: {signal_price}, slippage: {slippage:.3f}%)"
                        )
                    else:
                        logger.info(f"[FILL] Order {order_id} FILLED at {avg_price}")
                    return True, avg_price

                if status in ('CANCELED', 'REJECTED', 'EXPIRED'):
                    logger.error(f"[FILL] Order {order_id} NOT FILLED — status: {status}")
                    return False, 0

            except Exception as e:
                logger.warning(f"[FILL] Error checking order {order_id} (attempt {attempt + 1}): {e}")

            if attempt < max_attempts - 1:
                time_module.sleep(2)

        logger.error(f"[FILL] Order {order_id} NOT FILLED after {max_attempts} attempts — status: {status}")
        return False, 0

    # ------------------------------------------------------------------
    # Open / Close
    # ------------------------------------------------------------------

    def open_long(self, price):
        """Open long position. Returns True on success."""
        try:
            current_pos = self.get_current_position()
            if current_pos and current_pos['side'] == 'short':
                self.close_position()

            quantity = self.calculate_position_size()
            if quantity <= 0:
                logger.error("[EXCHANGE] Cannot open LONG — position size is 0")
                return False, 0

            order = self._api(
                self.client.futures_create_order,
                symbol=SYMBOL,
                side='BUY',
                type='MARKET',
                quantity=quantity,
            )
            logger.info(f"Order details: {order}")

            order_id = order.get('orderId')
            filled, avg_price = self.confirm_order_fill(order_id, signal_price=price)

            if filled:
                self.position = 'long'
                self.entry_price = avg_price if avg_price else price
                logger.info(f"Opened LONG position at {self.entry_price}")
                return True, avg_price
            else:
                logger.error(f"[FILL] LONG order {order_id} not confirmed — state NOT updated")
                return False, 0

        except Exception as e:
            logger.error(f"Error opening long: {e}")
            return False, 0

    def open_short(self, price):
        """Open short position. Returns True on success."""
        try:
            current_pos = self.get_current_position()
            if current_pos and current_pos['side'] == 'long':
                self.close_position()

            quantity = self.calculate_position_size()
            if quantity <= 0:
                logger.error("[EXCHANGE] Cannot open SHORT — position size is 0")
                return False, 0

            order = self._api(
                self.client.futures_create_order,
                symbol=SYMBOL,
                side='SELL',
                type='MARKET',
                quantity=quantity,
            )
            logger.info(f"Order details: {order}")

            order_id = order.get('orderId')
            filled, avg_price = self.confirm_order_fill(order_id, signal_price=price)

            if filled:
                self.position = 'short'
                self.entry_price = avg_price if avg_price else price
                logger.info(f"Opened SHORT position at {self.entry_price}")
                return True, avg_price
            else:
                logger.error(f"[FILL] SHORT order {order_id} not confirmed — state NOT updated")
                return False, 0

        except Exception as e:
            logger.error(f"Error opening short: {e}")
            return False, 0

    def close_position(self):
        """Close current position. Returns (success, avg_price)."""
        try:
            current_pos = self.get_current_position()
            if not current_pos:
                logger.info("No position to close")
                return True, 0

            # Cancel any open orders first
            try:
                open_orders = self._api(self.client.futures_get_open_orders, symbol=SYMBOL)
                for order in open_orders:
                    try:
                        self._api(
                            self.client.futures_cancel_order,
                            symbol=SYMBOL,
                            orderId=order['orderId'],
                        )
                        logger.info(f"Cancelled order {order['orderId']}")
                    except Exception as e:
                        logger.warning(f"Failed to cancel order {order['orderId']}: {e}")
            except Exception as e:
                logger.warning(f"Failed to get/cancel open orders: {e}")

            side = 'SELL' if current_pos['side'] == 'long' else 'BUY'
            order = self._api(
                self.client.futures_create_order,
                symbol=SYMBOL,
                side=side,
                type='MARKET',
                quantity=current_pos['amount'],
            )
            logger.info(f"Order details: {order}")

            order_id = order.get('orderId')
            filled, avg_price = self.confirm_order_fill(order_id)

            if filled:
                logger.info(f"Closed {current_pos['side']} position at {avg_price}")
                self.position = None
                self.entry_price = 0
                return True, avg_price
            else:
                logger.error(f"[FILL] Close order {order_id} not confirmed — syncing state from exchange")
                self.sync_position_from_exchange()
                return False, 0

        except Exception as e:
            logger.error(f"Error closing position after retries: {e}")
            return False, 0

    # ------------------------------------------------------------------
    # Stop Loss (optional safety net — disabled by default)
    # ------------------------------------------------------------------

    def set_stop_loss(self, side, entry_price, sl_percent=None):
        """Set STOP_MARKET order on Binance.

        Pass sl_percent to override config default.
        """
        try:
            sl_pct = sl_percent if sl_percent is not None else STOP_LOSS_PERCENT

            if side == 'long':
                stop_price = entry_price * (1 - sl_pct / 100)
                order_side = 'SELL'
            else:
                stop_price = entry_price * (1 + sl_pct / 100)
                order_side = 'BUY'

            stop_price = round(stop_price, 2)

            # Use current position size for SL order
            current_pos = self.get_current_position()
            qty = current_pos['amount'] if current_pos else self.calculate_position_size()

            self._api(
                self.client.futures_create_order,
                symbol=SYMBOL,
                side=order_side,
                type='STOP_MARKET',
                stopPrice=stop_price,
                quantity=qty,
                reduceOnly=True,
            )
            logger.info(f"[SL] Stop loss set at {sl_pct}% ({stop_price} USDT)")
            return True

        except Exception as e:
            logger.error(f"[SL] Error setting stop loss: {e}")
            return False
