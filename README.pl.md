# Auto Trading Bot

Zautomatyzowany bot tradingowy **ETHUSDT Futures** oparty o **Kernel Regression** (Nadaraya-Watson) — pełna implementacja w Pythonie, bez zależności od TradingView / ngrok / Flask.

Bot samodzielnie pobiera dane OHLCV z Binance, liczy sygnały kernel regression, generuje wejścia/wyjścia i wykonuje trade'y. Dashboard webowy + silnik backtestu + cross-asset optymalizator parametrów (AutoResearch) wszystko w jednym procesie.

---

## Funkcjonalności

- **Silnik sygnałów** — rational quadratic kernel + filtr zmienności ATR (1:1 replika Pine Script, zwalidowane `<0.07%` vs TradingView)
- **Zarządzanie ryzykiem** — trailing stop loss oparty o ATR, kill switch (kolejne straty / drop equity), logika re-entry
- **Dashboard webowy** — FastAPI + React (Tailwind), localhost:8080, 7 zakładek (Dashboard, Settings, Logs, Signals, AutoResearch, Orchestrator, Leaderboard)
- **Silnik backtestu** — offline symulacja z prowizją, cache, dwa tryby trailing SL
- **AutoResearch** — 3-fazowy smart search (explore/exploit/refine), globalna pamięć, cross-asset scoring (ETH/BTC/SOL)
- **Walk-Forward Validation** — anchored expanding windows, testy out-of-sample, scoring stabilności
- **Multi-worker orchestration** — rozproszone sweepy parametrów między maszynami
- **Docker-ready** — deployment jedną komendą na dowolny VPS

---

## Szybki Start

```bash
# 1. Sklonuj
git clone https://github.com/YOUR_USERNAME/Auto-trading-bot.git
cd Auto-trading-bot

# 2. Skonfiguruj środowisko
cp .env.example .env
# Edytuj .env — dodaj klucze Binance Testnet API + UPLOAD_API_KEY

# 3. Zainstaluj zależności
pip install -r requirements.txt

# 4. Uruchom
python main.py
```

Dashboard dostępny na **http://localhost:8080**.

> **UWAGA:** Domyślnie tylko testnet. Nigdy nie przełączaj na mainnet bez dokładnych testów i zrozumienia ryzyk.

---

## Architektura

```
main.py
  ├── Wątek 1: Trading Loop (co 1H: fetch → kernel → signal → trade)
  ├── Wątek 2: FastAPI Dashboard (localhost:8080)
  └── SharedState (thread-safe, bot ↔ dashboard)

backtest.py        — offline symulacja strategii na danych historycznych
autoresearch.py    — cross-asset parameter sweep + leaderboard
push-to-server.py  — klient zdalnego workera (rozproszone sweepy)
```

### Struktura repo

```
├── api/server.py              # FastAPI (15+ endpointów)
├── dashboard/index.html       # React + Tailwind (7 zakładek)
├── bot/
│   ├── config.py              # Parametry strategii + config API
│   ├── state.py               # SharedState (thread-safe)
│   ├── exchange.py            # Klient Binance (ordery, pozycja, leverage)
│   ├── data_fetcher.py        # OHLCV z Binance Futures + cache
│   ├── kernels.py             # Rational Quadratic + Gaussian
│   ├── filters.py             # Filtr zmienności (ATR short > ATR long)
│   ├── strategy.py            # Logika sygnałów (smoothing, SL, re-entry)
│   ├── kill_switch.py         # 5 strat / 10% DD → 24h pauza
│   └── trade_logger.py        # Logowanie tradów do CSV
├── main.py                    # Bot loop + FastAPI (2 wątki)
├── backtest.py                # Silnik backtestu
├── autoresearch.py            # Cross-asset parameter sweep
├── push-to-server.py          # Klient zdalnego workera
└── tests/                     # Zestaw testów pytest
```

---

## Parametry strategii (domyślne)

| Parametr             | Wartość                             |
| -------------------- | ----------------------------------- |
| Timeframe            | 1H                                  |
| Symbol               | ETHUSDT (Binance Futures Perpetual) |
| Lookback Window (h)  | 100                                 |
| Regression Level (x) | 69                                  |
| Relative Weight (r)  | 10.0                                |
| Kernel Smoothing     | On                                  |
| SL Type              | ATR                                 |
| ATR Period / Mult    | 20 / 6.0                            |
| Trailing SL Mode     | Pine (close-based)                  |
| Volatility Min / Max | 5 / 10                              |
| Leverage             | 1x                                  |
| Position Size        | 50% portfela                        |
| Commission           | 0.05% (Binance Futures taker)       |
| Kill Switch          | 5 strat / 10% DD / 24h pauza        |

---

## Zakładki Dashboard

| Zakładka     | Co robi                                                           |
| ------------ | ----------------------------------------------------------------- |
| Dashboard    | Live status, pozycja, PnL, emergency buttons, reset kill switch   |
| Settings     | Edytuj parametry strategii z UI, walidacja zakresów               |
| Logs         | Live tail logów bota (auto-odświeżanie)                           |
| Signals      | Historia sygnałów w czasie rzeczywistym (yhat1, yhat2, vol filter)|
| AutoResearch | Wyniki grid sweep, heatmap, najlepsze configi                     |
| Orchestrator | Multi-worker parameter sweepy, status workerów                    |
| Leaderboard  | All-time najlepsze configi, apply do live bota                    |

---

## Narzędzia CLI

### Backtest

```bash
python backtest.py --symbol ETHUSDT --sl-type atr --atr-period 20 --atr-multiplier 6
python backtest.py --lookback 73 --regression 69 --relative-weight 8
python backtest.py --no-sl --no-reentry  # tylko kernel
```

### AutoResearch — grid sweep

```bash
python autoresearch.py
python autoresearch.py --h-min 60 --h-max 110 --h-step 1 --x-min 62 --x-max 69 --x-step 1
python autoresearch.py --atr-period-values 10 14 20 30 --atr-mult-values 3 4 5 6 8
```

### AutoResearch — smart mode (genetyczny)

```bash
python autoresearch.py --mode smart --time-budget 3600
python autoresearch.py --mode smart --time-budget 7200 --assets ETHUSDT BTCUSDT SOLUSDT
```

### Walk-Forward Validation

```bash
python autoresearch.py --mode walkforward --folds 3 --test-months 3
```

### Zdalny worker (push-to-server)

```bash
export SERVER_URL="http://YOUR_SERVER_IP:8080"
export UPLOAD_API_KEY="your-secret-key"
python push-to-server.py --mode smart --time-budget 3600 --name "My PC"

# Multi-worker na jednej maszynie
python push-to-server.py --workers 4 --mode smart --time-budget 3600
```

---

## Endpointy API

| Endpoint                      | Metoda | Cel                                   |
| ----------------------------- | ------ | ------------------------------------- |
| `/api/status`                 | GET    | Stan bota, pozycja, balance, live PnL |
| `/api/signals`                | GET    | Ostatni sygnał                        |
| `/api/signal-history`         | GET    | Historia sygnałów                     |
| `/api/settings`               | GET/POST | Pobierz/zmień parametry             |
| `/api/trades`                 | GET    | Ostatnie 20 tradów z CSV              |
| `/api/killswitch`             | GET    | Status kill switch                    |
| `/api/killswitch/reset`       | POST   | Ręczny reset kill switch              |
| `/api/logs`                   | GET    | Ostatnie 100 linii logów              |
| `/api/equity`                 | GET    | Equity curve                          |
| `/api/autoresearch`           | GET    | Wyniki sweepu                         |
| `/api/autoresearch/upload`    | POST   | Upload wyników (wymaga `X-Upload-Key`)|
| `/api/leaderboard`            | GET    | All-time najlepsze configi            |
| `/api/emergency/close`        | POST   | Zamknij pozycję natychmiast           |
| `/api/emergency/pause`        | POST   | Pauzuj trading                        |
| `/api/emergency/resume`       | POST   | Wznów trading                         |

---

## Deployment (Docker)

### Docker lokalnie

```bash
cp .env.example .env  # edytuj z prawdziwymi kluczami
docker compose up -d
```

### Bootstrap VPS

```bash
ssh root@YOUR_SERVER_IP
mkdir -p /opt/trading-bot/data /opt/trading-bot/cache
touch /opt/trading-bot/data/{autoresearch_results.csv,autoresearch_alltime.csv,autoresearch_meta.json,trade_log.csv,trading_bot.log,signal_state.json}
# Skopiuj docker-compose.yml + .env na serwer, potem:
cd /opt/trading-bot && docker compose up -d
```

### Auto-deploy przez GitHub Actions

Ustaw w **Settings → Secrets → Actions**:

| Secret            | Wartość                                     |
| ----------------- | ------------------------------------------- |
| `DEPLOY_HOST`     | IP twojego serwera                          |
| `DEPLOY_USER`     | `root`                                      |
| `DEPLOY_SSH_KEY`  | zawartość prywatnego klucza SSH             |
| `DEPLOY_PORT`     | `22`                                        |

Oraz w **Settings → Variables → Actions**:

| Zmienna           | Wartość                                     |
| ----------------- | ------------------------------------------- |
| `DEPLOY_ENABLED`  | `true`                                      |

Każdy push na `main` buduje nowy image i deployuje automatycznie.

---

## Testy

```bash
pip install -r requirements.txt
pytest                          # pełny zestaw testów
pytest tests/test_kernels.py -v # konkretny test
python validate_kernels.py      # walidacja kerneli vs TradingView
python validate_signals.py      # walidacja logiki sygnałów
```

---

## Dokumentacja

- [README.md](./README.md) — wersja angielska
- [PROJECT.md](./PROJECT.md) — szczegółowa architektura i deployment
- [DATABASE.md](./DATABASE.md) — schema SQLite (Prisma)
- [TESTING.md](./TESTING.md) — strategia testów
- [MIGRATION_ORCHESTRATOR.md](./MIGRATION_ORCHESTRATOR.md) — notatki migracji orchestratora
- [CLAUDE.md](./CLAUDE.md) — wytyczne rozwoju (projektowe)

---

## Disclaimer

**Ten bot tradeuje prawdziwe pieniądze na rynkach derywatów kryptowalutowych.** Używaj tylko testnetu jeśli nie rozumiesz w pełni ryzyk. Straty mogą przekroczyć depozyt. Brak gwarancji. Autorzy nie odpowiadają za jakiekolwiek straty.

## Licencja

MIT — zobacz [LICENSE](./LICENSE).
