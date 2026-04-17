# CLAUDE.md — Trading Bot Standalone

## CORE RULES (NIGDY NIE LAMAJ)

1. **NIE MODYFIKUJ CLAUDE.md** bez WYRAZNEJ zgody Usera. Przed kazda zmiana w tym pliku — ZAPYTAJ i czekaj na potwierdzenie.
2. **NIE MERGUJ zewnetrznych branchy** bez potwierdzenia Usera. Zawsze najpierw raport -> czekaj na decyzje.
3. **NIE przelaczaj na mainnet.**

## Twoja Rola

Jestes implementatorem w teamie. Twoje zadanie: przelozyc instrukcje na dzialajacy kod.

## Projekt

Standalone ETHUSDT trading bot — pelny Python, bez TradingView/ngrok/Flask.
Bot sam pobiera dane z Binance, liczy kernel regression, generuje sygnaly, wykonuje trade'y.
Dashboard webowy (FastAPI + React) na localhost:8080.

## Status: TESTNET + AUTORESEARCH AKTYWNY

## Zrealizowane fazy:

1. Struktura repo + migracja komponentow z trading-bot
2. Data fetcher + kernel regression (zwalidowany vs TradingView, diff <0.07%)
3. Signal logic (1:1 Pine Script replica, smoothing on/off, volatility filter)
4. Execution loop + trade execution + candle sync
5. Testnet monitoring (pierwsze trade'y wykonane, slippage <0.02%)
6. Dashboard UI (FastAPI + React, 5 zakladek: Dashboard, Settings, Logs, AutoResearch, Leaderboard)
7. Backtest engine (CLI params, prowizja 0.05%, cache danych, dwa tryby trailing SL)
8. AutoResearch (cross-asset parameter sweep na ETH/BTC/SOL, all-time leaderboard)
9. Parameter sweep w toku (h, x, r, ATR, vol filter)
10. Live test z best config (2-4 tygodnie)
11. AutoResearch v3 Smart Mode (3-fazowy search: explore/exploit/refine + global memory + config hashing)
12. Walk-Forward Validation (out-of-sample, 3 foldy, cross-asset)
13. Balanced Score (drugi ranking nagradzający cross-asset consistency)
14. Distributed Worker Orchestrator (team autoresearch)
15. Mainnet data fix (sygnały z mainnet, ordery na testnet)

## Architektura

```
main.py
  ├── Thread 1: Trading Loop (co 1H: fetch -> kernel -> signal -> trade)
  ├── Thread 2: FastAPI Dashboard (localhost:8080)
  └── SharedState (thread-safe, bot <-> dashboard)

backtest.py — offline symulacja strategii na historycznych danych
autoresearch.py — cross-asset parameter sweep + leaderboard
```

## Struktura plikow

```
trading-bot-standalone/
├── api/
│   └── server.py              # FastAPI (12+ endpointow)
├── dashboard/
│   └── index.html              # React + Tailwind (5 zakladek)
├── bot/
│   ├── config.py               # Parametry strategii + API
│   ├── state.py                # SharedState (thread-safe)
│   ├── exchange.py             # Binance: ordery, pozycja, leverage, fill confirmation
│   ├── data_fetcher.py         # OHLCV z Binance Futures + cache
│   ├── kernels.py              # Rational Quadratic + Gaussian (zwalidowany)
│   ├── filters.py              # Volatility filter (ATR short > ATR long)
│   ├── strategy.py             # Signal logic (smoothing on/off, SL, re-entry)
│   ├── kill_switch.py          # 5 losses / 10% DD -> 24h pauza
│   ├── trade_logger.py         # CSV trade logging
│   └── utils.py                # safe_api_call, retry, time sync
├── main.py                     # Bot loop + FastAPI (2 threads)
├── backtest.py                 # Backtest engine (CLI, prowizja, cache)
├── autoresearch.py             # Cross-asset parameter sweep + leaderboard
├── monitor_bot.py              # CLI monitor
├── validate_kernels.py         # Kernel validation vs TradingView
├── validate_signals.py         # Signal validation
├── cache/                      # OHLCV data cache per symbol
├── trade_log.csv               # Bot trade history (auto, .gitignore)
├── autoresearch_results.csv    # Last sweep (auto, .gitignore)
├── autoresearch_alltime.csv    # All-time leaderboard (append-only, .gitignore)
├── .env                        # API keys (NIE w git)
└── .env.example                # Przyklad
```

## Aktualne parametry strategii

| Parametr             | Wartosc                             |
| -------------------- | ----------------------------------- |
| Timeframe            | 1H                                  |
| Symbol               | ETHUSDT (Binance Futures Perpetual) |
| Lookback Window (h)  | 100                                 |
| Regression Level (x) | 69                                  |
| Relative Weight (r)  | 10.0                                |
| Lag                  | 1                                   |
| Kernel Smoothing     | On (Off nie dziala cross-asset)     |
| SL Type              | ATR                                 |
| ATR Period           | 20                                  |
| ATR Multiplier       | 6.0                                 |
| Trailing SL Mode     | Pine (close-based)                  |
| Dynamic SL           | On (trailing, only tightens)        |
| Volatility Min       | 5                                   |
| Volatility Max       | 10                                  |
| Re-Entry             | On (opposite position after SL)     |
| Re-Entry Delay       | 1 swieca                            |
| Leverage             | 1x                                  |
| Position Size        | 50% portfela (dynamiczny)           |
| Commission           | 0.05% (Binance Futures taker)       |
| Kill Switch          | 5 losses / 10% DD / 24h pauza       |

## AutoResearch — najlepsze wyniki

| #   | h   | x   | r   | Score | ETH PF | BTC PF | SOL PF |
| --- | --- | --- | --- | ----- | ------ | ------ | ------ |
| 1   | 73  | 69  | 8.0 | 2.176 | 2.18   | 1.39   | 1.33   |
| 2   | 72  | 69  | 8.0 | 2.163 | 2.18   | 1.39   | 1.33   |
| 3   | 110 | 67  | 8.0 | 2.137 | 2.23   | 1.39   | 1.30   |

Score = avg_PF x min_PF (nagradza konsystencje cross-asset). Scoring: PF < 1.0 na ANY asset -> rejected. DD > 40% -> rejected.

## Walk-Forward Validation — najlepsze wyniki

WF testuje configs out-of-sample (anchored expanding window, 3 foldy, 3-miesięczne okno testowe).
Stability = pf_test / pf_train. Stability > 1.0 = brak overfittingu.

| # | h   | x  | r  | Vol  | WF Score | PF Test | Stability | Folds+ | Status    |
|---|-----|----|----|------|----------|---------|-----------|--------|-----------|
| 1 | 100 | 69 | 10 | 5/10 | 3.08     | 2.02    | 1.53      | 3/3    | VALIDATED |
| 2 | 110 | 69 | 10 | 5/10 | 2.86     | 1.94    | 1.47      | 3/3    | VALIDATED |
| 3 | 110 | 67 | 10 | 5/10 | 2.81     | 1.96    | 1.44      | 3/3    | VALIDATED |

Aktualny config bota (h=100, x=69, r=10, vol=5/10) = WF #1.

## Dashboard (localhost:8080)

| Zakladka     | Co robi                                                        |
| ------------ | -------------------------------------------------------------- |
| Dashboard    | Live status, pozycja, PnL z aktualna cena, emergency buttons   |
| Settings     | Parametry strategii — zmiana z UI, walidacja zakresow          |
| Logs         | Live tail 100 linii logow, auto-scroll co 5s                   |
| AutoResearch | Wyniki sweepea, heatmap hxx, TOP 20 z rozwijalnymi szczegolami |
| Leaderboard  | All-time best configs, rozwijane szczegoly, Apply button       |
| Signals      | Real-time signal history (Midas polling)                       |
| Orchestrator | Distributed sweep management, perpetual sweeps                 |

## API Endpointy

| Endpoint                 | Method   | Opis                                  |
| ------------------------ | -------- | ------------------------------------- |
| /api/status              | GET      | Stan bota, pozycja, balance, live PnL |
| /api/settings            | GET/POST | Pobierz/zmien parametry               |
| /api/trades              | GET      | Ostatnie 20 tradow z CSV              |
| /api/killswitch          | GET      | Kill switch status                    |
| /api/logs                | GET      | Ostatnie 100 linii logow              |
| /api/equity              | GET      | Equity curve                          |
| /api/autoresearch        | GET      | Wyniki sweepea                        |
| /api/leaderboard         | GET      | All-time best configs                 |
| /api/autoresearch/export | GET      | Download CSV                          |
| /api/emergency/close     | POST     | Zamknij pozycje natychmiast           |
| /api/emergency/pause     | POST     | Pauzuj trading                        |
| /api/emergency/resume    | POST     | Wznow trading                         |

## Backtest CLI

```bash
python backtest.py --symbol ETHUSDT --sl-type atr --atr-period 20 --atr-multiplier 6
python backtest.py --lookback 73 --regression 69 --relative-weight 8
python backtest.py --vol-min 5 --vol-max 10 --commission 0.05
python backtest.py --no-sl --no-reentry  # kernel only
```

## AutoResearch CLI

```bash
python autoresearch.py  # domyslny sweep
python autoresearch.py --r-values 1 3 5 8 10 15 20
python autoresearch.py --h-min 60 --h-max 110 --h-step 1 --x-min 62 --x-max 69 --x-step 1
python autoresearch.py --atr-period-values 10 14 20 30 --atr-mult-values 3 4 5 6 8
python autoresearch.py --vol-min-values 1 3 5 7 --vol-max-values 5 7 10 14 20
```

## AutoResearch Smart Mode CLI

```bash
# Smart search (3-fazowy: explore → exploit → refine):
python autoresearch.py --mode smart --time-budget 3600

# Z auto-uploadem na serwer:
python autoresearch.py --mode smart --time-budget 3600 \
  --upload-url http://server:8080/api/autoresearch/upload \
  --upload-key your-key --author your-name
```

## Walk-Forward Validation CLI

```bash
# Pełny WF na TOP 50 z alltime:
python autoresearch.py --walkforward --phase2-only --wf-top-n 50

# Szybki test TOP 10:
python autoresearch.py --walkforward --phase2-only --wf-top-n 10

# Custom foldy:
python autoresearch.py --walkforward --wf-folds 4 --wf-test-months 2
```

## Zasady

1. **Testnet only** — NIE mainnet bez jawnej decyzji Usera
2. **Zawsze pytaj przed commit/merge** — zadnych zmian bez potwierdzenia
3. **Jedna zmiana na raz** — zmien, przetestuj, potem nastepna
4. **Pakiet walidacyjny** przy kazdej zmianie kodu bota
5. **Parametry zmienia User** — przez dashboard lub jawna instrukcje
6. **Loguj wszystko** — kazda operacja trafia do logow i CSV
7. **Prowizja 0.05%** — Binance Futures taker fee
8. **AutoResearch wyniki -> leaderboard** — append-only, nigdy nie tracisz
9. **Przeczytaj CLAUDE.md przed kazda sesja** — to Twoje zrodlo prawdy
10. **Przed merge zewnetrznych branchy** — review commit po commicie, raport ryzyk

## Git Workflow

- **main** — stabilny, production-ready
- **dev** — biezace zmiany (tu pracujesz)
- **exploration** — branch od kontrybutorow (DevOps, Docker, CI/CD)
- Zawsze pytaj przed commit/merge
- Commit message format: `[FIX]`, `[FEAT]`, `[REFACTOR]`, `[CHORE]`, `[DOCS]`

## Walidacja

| Co                            | Wynik                            |
| ----------------------------- | -------------------------------- |
| Kernel vs TradingView         | diff <0.07%                      |
| Volatility filter vs Pine     | identyczne                       |
| Smoothing On vs Off           | On jedyny dzialajacy cross-asset |
| Trailing SL Pine vs Execution | Pine lepszy dla 1H crypto        |
| Live trade'y testnet          | slippage <0.02%                  |
| Multi-asset (ETH, BTC, SOL)   | 3/3 zyskowne z ATR SL            |

---

## Znane Bledy i Lessons Learned

**ZASADA: Za kazdym razem jak zrobisz blad — dodaj go tutaj zeby nie powtarzac w przyszlosci.**

### React Hooks (dashboard/index.html)

- **React error #310 / #300:** Hooks (useState, useEffect) MUSZA byc na samym poczatku komponentu, PRZED jakimkolwiek `if`, `return`, `for`. Hooks nie moga byc wewnatrz zagniezdzonych funkcji ani wywolywane warunkowo. Kazdy komponent musi wywolywac te sama liczbe hookow w tej samej kolejnosci przy kazdym renderze.
- **React error z undefined.map():** Dane z API moga byc null/undefined. Zawsze uzywaj fallbackow: `(data.items || []).map(...)`, sprawdzaj `if (!data) return <Loading/>` PO hookach, nie przed nimi.
- **Nie definiuj komponentow wewnatrz komponentow:** Funkcja-komponent zdefiniowana wewnatrz innego komponentu tworzy nowa referencje przy kazdym renderze -> unmount/remount -> potencjalny #310. Przeies na zewnatrz albo uzyj inline JSX.

### Recharts CDN

- **Recharts z CDN nie dziala** — wymaga prop-types. Uzyj czystego SVG zamiast Recharts.

### Kernel Regression

- **startAtBar interpretacja:** Pine `for i = 0 to _startAtBar` -> loop_size = startAtBar (nie startAtBar + lookback).
- **SPOT vs FUTURES data:** Uzywaj `futures_klines()` nie `get_klines()`. Roznica cen ~1-2%.

### Trailing SL

- **close vs high/low:** Execution mode za agresywny dla 1H crypto. Pine mode (close-based) domyslny.

### Backtest

- **Warmup period:** Pobieraj minimum 3 miesiace danych przed okresem tradingowym.
- **Kolejnosc SL vs TP:** Pine sprawdza TP PRZED SL. Backtest musi robic to samo.
- **Prowizja:** 0.05% (Binance Futures taker), nie 0.01%.

### AutoResearch

- **CSV overwrite:** `autoresearch_results.csv` nadpisywany. TOP 20 dopisuj do `autoresearch_alltime.csv` (append-only).
- **Smoothing flag:** `USE_KERNEL_SMOOTHING` musi byc FAKTYCZNIE UZYTY w signal logic — branchuj w kodzie.

### API / Dashboard

- **Nowe endpointy wymagaja restartu bota** — FastAPI laduje routes przy starcie.
- **404 na nowym endpoincie:** Sprawdz rejestracje w `api/server.py` + restart bota.

### Git / Repo

- **Repo to `trading-bot-standalone`** (nie `trading-bot`). Upewnij sie ze pracujesz w dobrym katalogu.
- **Przed merge zewnetrznych branchy** — review kazdy commit, sprawdz konflikty, nie merguj bez potwierdzenia Usera.

### Data Source

- **Testnet vs Mainnet data:** Testnet ma inne ceny niz mainnet (~0.1-1% diff). Sygnaly musza byc obliczane na mainnet data (bo backtest i AutoResearch uzywaja mainnet). Ordery wykonywane na testnet. Bot uzywa dwoch klientow: mainnet (data) + testnet (orders).

### VPS Deploy / Upload

- **UPLOAD_API_KEY musi byc w .env na serwerze** — endpoint `/api/autoresearch/upload` zwroci 500 jezeli zmienna nie jest ustawiona.
- **push-to-server.py wymaga uruchomienia z katalogu projektu** — skrypt odczytuje `autoresearch_results.csv` wzglednie CWD.
- **Pierwsze uruchomienie na VPS** — utworz puste pliki danych przed `docker compose up` inaczej Docker nie podepnie woluminow:
  ```bash
  mkdir -p /opt/trading-bot/data /opt/trading-bot/cache
  touch /opt/trading-bot/data/{autoresearch_results.csv,autoresearch_alltime.csv,autoresearch_meta.json,trade_log.csv,trading_bot.log,signal_state.json}
  ```
