import logging
import time as time_module
from typing import Optional

from binance.exceptions import BinanceAPIException

logger = logging.getLogger(__name__)

# Threshold above which a warning is emitted (ms)
_DRIFT_WARNING_MS = 2000


def sync_time(client) -> Optional[int]:
    """Synchronize local time with Binance server.

    Logs the measured clock offset every call so drift can be monitored over
    time.  Returns the applied offset in milliseconds, or ``None`` on failure.
    """
    raw = getattr(client, 'timestamp_offset', 0)
    try:
        previous_offset = int(raw) if raw is not None else 0
    except (TypeError, ValueError):
        previous_offset = 0
    try:
        for attempt in range(3):
            try:
                server_time = client.get_server_time()
                local_time = int(time_module.time() * 1000)
                offset_ms = server_time['serverTime'] - local_time

                if abs(offset_ms) > _DRIFT_WARNING_MS:
                    logger.warning(
                        f"[TIMESYNC] Large clock drift detected: {offset_ms:+d}ms "
                        f"(previous offset: {previous_offset:+d}ms)"
                    )

                if abs(offset_ms) > 500:
                    client.timestamp_offset = offset_ms
                    logger.info(
                        f"[TIMESYNC] Offset applied: {offset_ms:+d}ms "
                        f"(was {previous_offset:+d}ms, delta={offset_ms - previous_offset:+d}ms)"
                    )
                else:
                    # Still log so we can track drift over time
                    logger.debug(
                        f"[TIMESYNC] Offset within tolerance: {offset_ms:+d}ms — no adjustment needed"
                    )

                return offset_ms
            except Exception as e:
                if attempt == 2:
                    raise e
                time_module.sleep(1)
    except Exception as e:
        logger.error(f"[TIMESYNC] Failed to sync time after 3 attempts: {e}")
        return None


def safe_api_call(client, api_function, *args, **kwargs):
    """Wrapper for API calls with retry logic"""
    max_retries = 3
    retry_delay = 2

    for attempt in range(max_retries):
        try:
            return api_function(*args, **kwargs)
        except BinanceAPIException as e:
            if e.code == -1021:  # Timestamp error
                logger.warning(f"Timestamp error on attempt {attempt + 1}, resyncing...")
                sync_time(client)
            elif e.code == 0 and "502 Bad Gateway" in str(e):
                logger.warning(f"502 Bad Gateway on attempt {attempt + 1}, retrying in {retry_delay}s...")
                time_module.sleep(retry_delay)
                retry_delay *= 2
            else:
                if attempt == max_retries - 1:
                    raise e
                logger.warning(f"API error on attempt {attempt + 1}: {e}")
        except Exception as e:
            if "Read timed out" in str(e) or "Connection" in str(e):
                logger.warning(f"Network error on attempt {attempt + 1}, retrying in {retry_delay}s...")
                time_module.sleep(retry_delay)
                if attempt < max_retries - 1:
                    continue
            if attempt == max_retries - 1:
                raise e
            logger.warning(f"Error on attempt {attempt + 1}: {e}")
            time_module.sleep(retry_delay)

    raise Exception(f"Failed after {max_retries} attempts")
