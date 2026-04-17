# Signal API Changes -- Trading Bot Standalone

Changes required in this project (`trading-bot-standalone`) to expose signal data for consumption by the Midas trading dashboard.

**Context:** Midas will poll `GET /api/signals` every 10 seconds. It needs `action`, `reason`, and a sequence number to detect new trade signals efficiently. Currently only the `details` dict (kernel values) is stored in `SharedState` -- `action` and `reason` from `calculate_signals()` are discarded in `main.py`.

---

## 1. Expose `action` and `reason` in SharedState and API

### Problem

`main.py` calls `state.update_signal(details)` but discards the `action` and `reason` keys from the `calculate_signals()` return value. The `/api/status` and `/api/signals` endpoints therefore never include the computed trade intent.

### File: `main.py`

Change the `state.update_signal(details)` call to include `action` and `reason`:

```python
result = strategy.calculate_signals(ohlcv)
action = result.get("action")
reason = result.get("reason")
details = result.get("details", {})

# Include action/reason so the API can expose them
state.update_signal({**details, "action": action, "reason": reason})
```

### File: `bot/state.py`

Add new fields and update methods:

```python
def __init__(self):
    # ... existing fields ...
    self.last_action = None       # NEW: "open_long", "close_short", etc. or None
    self.last_reason = None       # NEW: "bullish_change", "stop_loss", etc. or None
    self.signal_seq = 0           # NEW: increments every update_signal() call
    self.action_seq = 0           # NEW: increments only when action is not None
    self.signal_history = []      # NEW: capped list of last 50 non-None actions
```

Update `update_signal()`:

```python
def update_signal(self, signal_data):
    with self._lock:
        action = signal_data.pop("action", None)
        reason = signal_data.pop("reason", None)

        self.last_signal = signal_data
        self.last_action = action
        self.last_reason = reason
        self.last_loop_time = time.time()
        self.signal_seq += 1

        if action is not None:
            self.action_seq += 1
            self.signal_history.append({
                "action": action,
                "reason": reason,
                "timestamp": time.time(),
                "symbol": signal_data.get("symbol", SYMBOL),
                "is_bullish": signal_data.get("is_bullish"),
                "yhat1": signal_data.get("yhat1"),
                "yhat2": signal_data.get("yhat2"),
            })
            # Cap at 50 entries
            if len(self.signal_history) > 50:
                self.signal_history = self.signal_history[-50:]
```

Update `get_status()` to include new fields:

```python
def get_status(self):
    with self._lock:
        return {
            # ... existing fields ...
            "last_signal": self.last_signal,
            "last_action": self.last_action,         # NEW
            "last_reason": self.last_reason,         # NEW
            "signal_seq": self.signal_seq,           # NEW
            "action_seq": self.action_seq,           # NEW
        }
```

Add a new method for signal history:

```python
def get_signal_history(self):
    with self._lock:
        return list(self.signal_history)
```

---

## 2. Update API endpoints

### File: `api/server.py`

Update `GET /api/signals`:

```python
@app.get("/api/signals")
async def get_signals():
    status = shared_state.get_status()
    return {
        "last_signal": status.get("last_signal"),
        "last_action": status.get("last_action"),        # NEW
        "last_reason": status.get("last_reason"),        # NEW
        "last_loop_time": status.get("last_loop"),
        "signal_seq": status.get("signal_seq"),          # NEW
        "action_seq": status.get("action_seq"),          # NEW
        "symbol": SYMBOL,                                # NEW
        "timeframe": TIMEFRAME,                          # NEW
    }
```

Add new `GET /api/signal-history` endpoint:

```python
@app.get("/api/signal-history")
async def get_signal_history():
    history = shared_state.get_signal_history()
    return {
        "history": history,
        "count": len(history),
        "symbol": SYMBOL,
        "timeframe": TIMEFRAME,
    }
```

The `/api/status` endpoint already returns `get_status()`, so `last_action`, `last_reason`, `signal_seq`, and `action_seq` will automatically appear there too.

---

## 3. Expected API response shapes after changes

### `GET /api/signals` (updated)

```json
{
  "last_signal": {
    "yhat1": 3245.67,
    "yhat2": 3244.12,
    "is_bullish": true,
    "bullish_change": true,
    "bearish_change": false,
    "vol_passes": true,
    "state": "long",
    "stop_loss": 3157.24,
    "entry_price": 3200.50,
    "pending_re_entry": false,
    "bars_since_exit": 0
  },
  "last_action": "open_long",
  "last_reason": "bullish_change",
  "last_loop_time": 1711324800.0,
  "signal_seq": 142,
  "action_seq": 23,
  "symbol": "ETHUSDT",
  "timeframe": "1h"
}
```

### `GET /api/signal-history` (new)

```json
{
  "history": [
    {
      "action": "open_long",
      "reason": "bullish_change",
      "timestamp": 1711324800.0,
      "symbol": "ETHUSDT",
      "is_bullish": true,
      "yhat1": 3245.67,
      "yhat2": 3244.12
    },
    {
      "action": "close_long",
      "reason": "color_change",
      "timestamp": 1711321200.0,
      "symbol": "ETHUSDT",
      "is_bullish": false,
      "yhat1": 3220.10,
      "yhat2": 3221.55
    }
  ],
  "count": 2,
  "symbol": "ETHUSDT",
  "timeframe": "1h"
}
```

### `GET /api/status` (existing, with new fields added)

All existing fields remain unchanged. New fields added at the top level:

- `last_action` (string or null)
- `last_reason` (string or null)
- `signal_seq` (int)
- `action_seq` (int)

---

## 4. How Midas will consume this

Midas `SignalPollerService` will:

1. Poll `GET /api/signals` every 10 seconds
2. Compare returned `action_seq` with its stored value
3. If `action_seq` changed and `last_action` is not null, trigger `handleExternalSignal()` in BotEngine
4. Use `signal_seq` to detect "bot is alive but no trade signal" (heartbeat)

Midas `/signals` page will:

1. Call `GET /api/signal-history` to display signal history table
2. Use `GET /api/status` (via the poller cache) to display live bot state (position, balance, kernel values, next candle countdown)

---

## 5. Checklist

- [ ] `main.py` -- pass `action` and `reason` into `state.update_signal()`
- [ ] `bot/state.py` -- add `last_action`, `last_reason`, `signal_seq`, `action_seq`, `signal_history` fields
- [ ] `bot/state.py` -- update `update_signal()` to extract and store action/reason, increment seqs, append history
- [ ] `bot/state.py` -- update `get_status()` to include new fields
- [ ] `bot/state.py` -- add `get_signal_history()` method
- [ ] `api/server.py` -- update `GET /api/signals` response
- [ ] `api/server.py` -- add `GET /api/signal-history` endpoint
- [ ] Test: verify `/api/signals` returns `action_seq` that increments on each trade signal
- [ ] Test: verify `/api/signal-history` returns capped list of past actions
- [ ] Deploy to production server (http://YOUR_SERVER_IP:8080)
