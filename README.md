# Bot Arena Exchange

Bot Arena Exchange is a **multi-bot, real-time simulated exchange** where server-side bots, user-submitted strategies, and manual traders compete inside a limit-order-book loop with live WebSocket streaming, leaderboards, and risk-aware scoring.

The product direction is described in `bot-arena-exchange.md`: a "Kaggle for trading bots" where serious quant builders compete inside a simulated exchange rather than against static historical datasets.

Multiple bots run concurrently as background asyncio tasks, the market streams in real time, and the frontend renders depth charts, sparklines, and trader detail panels from WebSocket events.

## 1. What this repo contains

This repository has six main parts:

1. **Python backend domain and application code** in `bot_arena_exchange/`
2. **FastAPI HTTP adapter** in `bot_arena_exchange/adapters/api/fastapi_app.py`
3. **LiquidityEngine** — asyncio background bot runtime in `bot_arena_exchange/adapters/api/liquidity_engine.py`
4. **DataLogger** — background subscriber that persists order book updates and fills to CSV in `bot_arena_exchange/application/data_logger.py`
5. **Analytics module** — tape export and realism inspection tools in `analytics/`
6. **React/Vite frontend** in `frontend/` with real-time WebSocket dashboard

It also includes:

- `tests/` — pytest test suite for configuration, bot lifecycle, exchange simulation, order book behavior, and tournament flow
- `bot_arena_exchange/config/default_tournament.json` — default tournament rules, markets, venues, scoring, bot registry, liquidity/trading bot lists, and regime parameters
- `market_spy.py` — CLI tool that connects to the WebSocket stream and prints a real-time activity summary (fills, volume, spreads)
- `docker-compose.yml` — local API + web startup using Python and Node containers
- `Makefile` — convenience `make test` command
- `bot-arena-exchange.md` — product brief, user stories, MVP phases, milestones, and delivery risks

## 2. Current product scope

The implemented prototype covers:

1. Load configurable tournament rules with bot registry, liquidity/trading bot lists, and regime visibility.
2. Validate and submit a Python bot; store accepted bot versions in memory.
3. List an upcoming scheduled tournament; enter a submitted bot version into it.
4. Launch registered server-side bots as background asyncio tasks via the LiquidityEngine.
5. Provide a synchronous API proxy (`SyncApiProxy`) so bots can place/cancel orders inside the exchange lock.
6. Seed continuous market activity through 12 built-in bot strategies spanning market making, trend following, mean reversion, arbitrage, spoofing, and retail noise.
7. Place, queue, match, fill, cancel, or reject limit orders with instant execution.
8. Track trader positions, average costs, realized PnL, fees, and risk status.
9. Exclude system accounts from leaderboards and bypass risk limits for those accounts.
10. Score accounts using raw PnL minus optional delta liquidation penalties.
11. Publish a ranked leaderboard auto-broadcast every 2 seconds via WebSocket.
12. Stream real-time market data (`ORDERBOOK_UPDATE`, `FILL`, `LEADERBOARD_UPDATE`) over WebSocket.
13. Display a live dashboard in React: order book depth with cumulative bars, recent trades, leaderboard with sparklines, trader detail panel with PnL area chart, and rank-change animations.
14. Persist all order book updates and fills to CSV (`data/quotes.csv`, `data/trades.csv`) via the DataLogger.
15. Export unified market tapes and compute microstructure realism metrics via the analytics module.

The broader product plan includes futures, dual venues, richer regimes, margin/liquidation, replay, and diagnostics, but the current codebase is a functional multi-bot arena with a full analytics pipeline.

## 3. Repository structure

```text
.
├── README.md
├── bot-arena-exchange.md
├── docker-compose.yml
├── Makefile
├── market_spy.py
├── pytest.ini
├── analytics/
│   ├── __init__.py
│   ├── tape_export.py
│   ├── realism_report.py
│   └── output/
├── bot_arena_exchange/
│   ├── adapters/
│   │   └── api/
│   │       ├── fastapi_app.py
│   │       └── liquidity_engine.py
│   ├── application/
│   │   ├── api_gateway.py
│   │   ├── data_logger.py
│   │   ├── event_log.py
│   │   ├── exchange_service.py
│   │   ├── tournaments.py
│   │   └── bot_lifecycle/
│   │       ├── repository.py
│   │       ├── service.py
│   │       └── validation.py
│   ├── config/
│   │   ├── default_tournament.json
│   │   └── tournament_config.py
│   ├── domain/
│   │   ├── bots.py
│   │   ├── order_book.py
│   │   ├── scoring.py
│   │   └── tournament.py
│   └── starter_kit/
│       ├── example_bot.py
│       ├── hft_market_maker_bot.py
│       ├── institutional_trend_bot.py
│       ├── lagging_mm_bot.py
│       ├── market_maker_bot.py
│       ├── phantom_spoofer_bot.py
│       ├── retail_noise_trader_bot.py
│       ├── slow_mean_reverter_bot.py
│       ├── spatial_arbitrage_bot.py
│       ├── stat_arb_bot.py
│       ├── stochastic_lagging_mm_bot.py
│       └── trend_bot.py
├── data/
│   ├── quotes.csv
│   └── trades.csv
├── frontend/
│   ├── index.html
│   ├── package.json
│   └── src/
│       ├── main.jsx
│       └── styles.css
└── tests/
    ├── test_orderbook.py
    ├── test_phase0_config_gateway.py
    ├── test_phase1_bot_lifecycle.py
    ├── test_phase2_exchange_simulation.py
    ├── test_phase3_tournament_loop.py
    └── test_tournament_engine.py
```

## 4. Backend architecture step by step

### Step 1: Tournament configuration is loaded

The default config lives at `bot_arena_exchange/config/default_tournament.json`.

It defines:

- **tournament id**: `default-phase-0`
- **system_account_ids**: 22 bot trader IDs — these bypass risk limits and are excluded from leaderboards
- **rules**: duration ticks/seconds, entry deadline offset, minimum participants
- **scoring**: liquidation fee and delta penalty toggle
- **markets**: currently `AAPL` spot with integer prices and lot sizes
- **venues**: `VENUE_1` (0 bps fee, 10 bps spread, 0 latency ticks) and `VENUE_2` (15 bps fee, 30 bps spread, 2 latency ticks)
- **bot_registry**: maps `trader_id` → Python class name for all server-side bots
- **liquidity_bots**: all 22 trader IDs enabled as liquidity providers
- **trading_bots**: list of trader IDs enabled as competing traders (currently empty by default)
- **regimes**: sideways, trending, high volatility, liquidity shock, and black swan — each with `visible_before_tournament`, `volatility_bps`, `liquidity_multiplier`, and `spread_multiplier`

`bot_arena_exchange/config/tournament_config.py` converts that JSON into frozen dataclasses (`TournamentConfig`, `MarketConfig`, `VenueConfig`, `RegimeConfig`, `LiquidityBotsConfig`, `TradingBotsConfig`, `BotRegistryConfig`) and validates all values.

### Step 2: ExchangeService wires the application together

`bot_arena_exchange/application/exchange_service.py` is the main application service.

It creates or receives:

- `TournamentConfig`
- `OrderBook`
- `TournamentManager` (with system account IDs for risk bypass)
- `InMemoryEventLog` (with subscriber queues for WebSocket broadcasting)
- `BotLifecycleService`
- `ApiGateway`
- `TournamentScheduler`
- `asyncio.Lock` for serializing mutations to OrderBook + TournamentManager

Most API endpoints delegate to this service. Key responsibilities:

- return tournament config and starter kit content
- validate and submit bots; list bot versions
- list and inspect tournaments; enter bots into tournaments
- place and cancel orders (instant execution, no latency queue)
- return market/account/event state
- score traders (excluding system accounts)
- publish a leaderboard
- run the scheduled tournament loop (time-based with `ApiProxy` injection into bot `on_tick()`)
- reset all exchange state for a clean restart

### Step 3: Bot lifecycle validates, versions, and executes submitted bots

Bot lifecycle code lives under `bot_arena_exchange/application/bot_lifecycle/`.

A submitted bot is expected to include Python source files with an entry point file containing:

```python
class MyBot:
    def on_tick(self, api):
        # api.place_order(...), api.cancel_order(...), api.get_order_book(...)
        ...

def create_bot():
    return MyBot()
```

Accepted submissions are stored as versions. During tournament execution, `ExchangeService.run_scheduled_tournament()` uses `exec()` to load the submitted code, instantiates the bot via `create_bot()`, and injects an `ApiProxy` into each `on_tick()` call. The `ApiProxy` provides:

- `get_order_book(symbol, venue)` — returns best bid, best ask, and snapshot
- `get_account()` — returns the trader's `TraderAccount`
- `get_position(symbol)` — returns net position for a symbol
- `place_order(side, price, quantity, symbol, venue)` — places an order and returns the order ID
- `cancel_order(order_id)` — cancels an existing order

### Step 4: API gateway validates trading requests

`bot_arena_exchange/application/api_gateway.py` validates order payloads before they reach the order book. It checks:

- tournament status is `RUNNING`
- required fields are present
- side is `BUY` or `SELL`
- price is a positive integer matching market tick size
- quantity is a positive integer matching market lot size
- trader id is a non-empty string
- symbol exists in configured markets
- venue exists and supports the requested symbol
- trader account is active (or is a system account)
- order would not exceed the position limit

### Step 5: OrderBook matches orders by price-time priority

`bot_arena_exchange/domain/order_book.py` implements the limit order book.

- `Order` is a dataclass with id, side, price, quantity, trader id, timestamp, symbol, venue, time-in-force, remaining quantity, and status.
- Bids and asks are grouped by price level; heaps track best bid and best ask.
- Supported TIF: `GTC` (good till cancelled), `IOC` (immediate or cancel), `FOK` (fill or kill).
- Buy orders match the lowest ask at or below the buy price; sell orders match the highest bid at or above the sell price.
- Execution price is the resting maker order price; partial fills reduce remaining quantities.
- Cancelled orders are lazily removed.

### Step 6: TournamentManager updates accounts from trades

`bot_arena_exchange/domain/tournament.py` tracks trader accounts.

Each `TraderAccount` contains trader id, positions by symbol, cost basis totals, average costs, realized PnL, fees paid, status (`ACTIVE` or `DISCONNECTED`), and an `is_system` flag.

- **System accounts** (configured via `system_account_ids`) bypass position limits and are excluded from leaderboards and scoring.
- `TournamentManager.process_trades()` applies venue fees, updates inventory, calculates realized PnL using weighted average cost, and disconnects non-system accounts that breach the position limit.

### Step 7: Event log records actions and broadcasts via WebSocket

`bot_arena_exchange/application/event_log.py` provides `InMemoryEventLog` with pub/sub support.

Events recorded:
- `ORDER_ACCEPTED`, `ORDER_REJECTED`, `FILL`, `CANCEL`, `CANCEL_REJECTED`, `DISCONNECTION`
- `ORDERBOOK_UPDATE` — emitted after every order/cancel to push fresh market state
- `LEADERBOARD_UPDATE` — auto-broadcast every 2 seconds by a background task

Each event is pushed to all active subscriber queues, which the WebSocket endpoint (`/ws/stream`) drains and sends to connected clients. The DataLogger also subscribes to persist events to CSV (see Step 14).

### Step 8: LiquidityEngine runs bots as persistent background tasks

`bot_arena_exchange/adapters/api/liquidity_engine.py` is an asyncio-based runtime.

- Each registered bot runs in its own `asyncio.Task` with a ~0.8s tick interval.
- The engine acquires the exchange-level `asyncio.Lock` before invoking each bot's `on_tick`, making synchronous internal methods (`_place_order_sync` / `_cancel_order_sync`) safe to call.
- A `SyncApiProxy` wraps `ExchangeService` for bot code — bots call it without async/await.
- Bots can be individually enabled/disabled at runtime via the engine's `enable(trader_id)` / `disable(trader_id)` methods.
- Bot status can be queried via `GET /liquidity/bots` and `GET /trading/bots`.

Bot contract — a bot must implement one of:
- `on_tick(api)` — called every tick with a `SyncApiProxy`
- `think(best_bid, best_ask)` — called every tick, returns an optional order dict

### Step 9: Bot registry and built-in bots

The `bot_registry` section of `default_tournament.json` maps 22 trader IDs to 13 Python bot classes. On startup, `fastapi_app.py` instantiates each bot via a class map with per-bot factory functions for venue assignment and parameter tuning.

**Built-in domain bots** (in `bot_arena_exchange/domain/bots.py`):

| Class | Trader IDs | Type | Behavior |
|---|---|---|---|
| `RandomTrader` | `Bot_Random_1`, `Bot_Random_2` | Built-in | Places random BUY/SELL orders near the top-of-book |
| `MeanRevertingTrader` | `Bot_MeanRev` | Built-in | Mean-reverts around a reference price, buying when mid is low and selling when high |

**Starter kit bots** (in `bot_arena_exchange/starter_kit/`):

| Class | Trader IDs | Type | Behavior |
|---|---|---|---|
| `MarketMakerBot` | `Bot_MM_V1_Alpha`, `Bot_MM_V1_Beta`, `Bot_MM_V2_Alpha`, `Bot_MM_V2_Beta` | Starter kit | Inventory-aware market maker with position skew, OFI tracking, and trade impact adjustment. V1 operates on VENUE_1 (size=100); V2 on VENUE_2 (size=20, more risk-averse). |
| `TrendBot` | `Bot_Trend` | Starter kit | Momentum/trend-following bot that buys when price is rising and sells when falling |
| `LaggingMarketMakerBot` | `Bot_LaggingMM` | Starter kit | Cross-venue market maker on VENUE_2 that reads VENUE_1 mid-price through a 15-tick delay deque, simulating latency arbitrage |
| `SpatialArbitrageBot` | `Bot_Apex` | Starter kit | Cross-venue spatial arbitrage — buys on the cheaper venue, sells on the more expensive one |
| `InstitutionalTrendBot` | `Bot_InstTrend` | Starter kit | Alternates between BULL/BEAR/FLAT phases with a 30% chance per tick of firing aggressive spread-crossing orders |
| `HftMarketMakerBot` | `Bot_HFT_V1_Alpha`, `Bot_HFT_V1_Beta` | Starter kit | High-frequency market maker with inventory skewing, OFI tracking, trade impact adjustment, and restocking fallback (spread=2, max_inventory=1000) |
| `RetailNoiseTraderBot` | `Bot_Noise` | Starter kit | Simulates uninformed retail flow with random order placement |
| `StochasticLaggingMMBot` | `Bot_LaggingMM5` | Starter kit | Lagging market maker on VENUE_2 with stochastic parameters, combining delay and randomized behavior |
| `SlowMeanReverterBot` | `Bot_SlowMR` | Starter kit | Cross-venue mean reversion with slower trading frequency |
| `PhantomSpooferBot` | `Bot_Spoofer` | Starter kit | Places large orders on VENUE_2 and cancels them before execution, simulating spoofing behavior |
| `StatArbBot` | `Bot_StatArb_1`–`Bot_StatArb_4` | Starter kit | Statistical arbitrage trading on both venues simultaneously |

All bots run as liquidity providers by default. The `trading_bots.enabled` list in config controls which bots compete for leaderboard ranking (empty by default — add trader IDs to enable competitive scoring).

### Step 10: Scoring computes leaderboard rows

`bot_arena_exchange/domain/scoring.py` scores accounts (excluding system accounts).

A score includes trader id, raw PnL, delta exposure, liquidation penalty, adjusted score, and status. When delta penalty is enabled, remaining positions are penalized by reference price, spread bps, and liquidation fee bps. Results are sorted by adjusted score descending with rank numbers.

### Step 11: TournamentScheduler manages the scheduled competition

`bot_arena_exchange/application/tournaments.py` creates one scheduled tournament from the default config. The scheduler can list tournaments, return detail, accept entries before the deadline, mark running, and publish completed results.

### Step 12: Running a scheduled tournament

`ExchangeService.run_scheduled_tournament()` performs a time-based tournament loop:

1. Find the scheduled tournament; mark it `RUNNING`.
2. Compile and instantiate all entered user bots via `exec()` + `create_bot()`.
3. Run a loop at 100ms per tick for the configured duration.
4. On each tick, call `on_tick(ApiProxy(...))` for every active bot under the exchange lock.
5. After the loop, score traders, compute the leaderboard, and publish results.
6. Mark the tournament `COMPLETED`.

### Step 13: WebSocket streaming

The `/ws/stream` endpoint pushes real-time events to connected clients:

- On connect, sends an initial `snapshot` with full market state.
- Then streams `ORDERBOOK_UPDATE`, `FILL`, and `LEADERBOARD_UPDATE` events as they occur.
- A background task broadcasts the leaderboard every 2 seconds.

### Step 14: DataLogger persists market data to CSV

`bot_arena_exchange/application/data_logger.py` is a background subscriber that records all market activity to disk:

- `ORDERBOOK_UPDATE` events → `data/quotes.csv` (timestamp, venue, best_bid, best_ask, bid_qty, ask_qty, mid_price, spread)
- `FILL` events → `data/trades.csv` (timestamp, venue, price, quantity, buyer_id, seller_id)

Files are written in append mode with auto-flush on every row. The DataLogger survives exchange resets via `/reset` by re-anchoring to the fresh event log. It launches automatically on server startup and stops gracefully on shutdown.

### Step 15: Analytics module

The `analytics/` package provides post-simulation analysis tools:

**`tape_export.py`** — Consolidates `data/quotes.csv` and `data/trades.csv` into a unified chronological market tape. Supports venue filtering, CSV/JSON export, and prints a summary with volume, participation, and spread statistics.

```bash
python analytics/tape_export.py                          # export CSV with summary
python analytics/tape_export.py --venue VENUE_1           # filter by venue
python analytics/tape_export.py --format json              # export as JSON
python analytics/tape_export.py --output analytics/output/tape.json
```

**`realism_report.py`** — Computes market microstructure realism metrics including return distribution (mean, std, skewness, kurtosis), autocorrelation, volatility clustering, spread statistics, trade size distribution, cross-venue price correlation, Hurst exponent (mean-reversion vs trending), and price impact. Supports text and Markdown output formats.

```bash
python analytics/realism_report.py                        # text report to stdout
python analytics/realism_report.py --venue VENUE_1         # single-venue analysis
python analytics/realism_report.py --format md --output analytics/output/realism_report.md
```

### Step 16: market_spy.py — CLI activity monitor

`market_spy.py` is a standalone CLI tool that connects to the WebSocket, listens for a configurable duration, and prints a summary:

- Total fills captured, volume, per-trader participation
- Order book snapshots: best bid/ask evolution, spread stats (avg/min/max)
- Filterable by trader ID (`--trader Juan_Alpha`)
- Configurable duration (`--duration 10`)

Usage:

```bash
python market_spy.py                          # 5s default
python market_spy.py --duration 10            # 10 seconds
python market_spy.py --trader Bot_HFT_V1_Alpha # filter by trader
```

## 5. HTTP API

The FastAPI app is in `bot_arena_exchange/adapters/api/fastapi_app.py`.

Run target:

```bash
uvicorn bot_arena_exchange.adapters.api.fastapi_app:app --host 0.0.0.0 --port 8000
```

### Read endpoints

| Method | Path | Purpose |
|---|---|---|
| `GET` | `/config` | Return active tournament config |
| `GET` | `/snapshot` | Return order book snapshot |
| `GET` | `/market` | Return tick, best bid/ask, snapshot, trades |
| `GET` | `/orders/{order_id}` | Return order state |
| `GET` | `/accounts/{trader_id}` | Return account state |
| `GET` | `/traders` | Return all known trader account states |
| `GET` | `/events` | Return event log |
| `GET` | `/scores` | Return raw score objects |
| `GET` | `/leaderboard` | Return ranked leaderboard |
| `GET` | `/tournaments` | List scheduled tournaments |
| `GET` | `/tournaments/{tournament_id}` | Return tournament detail |
| `GET` | `/starter-kit` | Return starter kit information |
| `GET` | `/bots/{owner_id}/versions` | List submitted bot versions, optionally filtered by bot name |
| `GET` | `/bots/{owner_id}/{bot_name}/versions/{version}` | Return one bot version |
| `GET` | `/liquidity/bots` | List registered liquidity bots with status |
| `GET` | `/trading/bots` | List registered trading bots with status |

### Write endpoints

| Method | Path | Purpose |
|---|---|---|
| `POST` | `/bots/validate` | Validate submitted bot files |
| `POST` | `/bots/submit` | Validate and save a bot version |
| `POST` | `/tournaments/{tournament_id}/entries` | Enter a bot version into a tournament |
| `POST` | `/tournaments/{tournament_id}/run` | Run scheduled tournament and publish leaderboard |
| `POST` | `/order` | Place an order (instant execution) |
| `POST` | `/orders/{order_id}/cancel` | Cancel an order owned by a trader |
| `POST` | `/reset` | Reset all exchange state and relaunch bots from a clean slate |

### WebSocket endpoint

| Path | Purpose |
|---|---|
| `ws://127.0.0.1:8000/ws/stream` | Real-time streaming of `ORDERBOOK_UPDATE`, `FILL`, `LEADERBOARD_UPDATE`, and initial `snapshot` |

## 6. Frontend

The frontend is a React app in `frontend/src/main.jsx`.

It uses React, React DOM, Vite, and connects to the backend via both REST and WebSocket.

The live dashboard shows:

- **Top bar**: tournament ID, WebSocket connection status indicator (green/red dot)
- **Metrics bar**: best bid, best ask, tournament status
- **Order Book panel**: bid/ask ladders with cumulative depth bars and spread badge
- **Recent Trades panel**: last 10 fills with timestamp, price trend arrows, buyer/seller highlighting (bot accounts get a distinct style)
- **Leaderboard panel**: ranked table with PnL, adjusted score, inline sparklines, and rank-change flash animations (green flash for rank up, red for rank down)
- **Trader Detail panel**: click a trader to see a PnL area chart with gradient fill, plus current PnL, score, and rank cards

Frontend scripts (from `frontend/package.json`):

```bash
npm run dev
npm run build
npm run preview
```

Environment variables:

- `VITE_API_BASE_URL` — REST API base URL (default: `http://127.0.0.1:8000`)
- `VITE_WS_URL` — WebSocket URL (auto-derived from API URL, default: `ws://127.0.0.1:8000/ws/stream`)

## 7. Running locally with Docker Compose

```bash
docker compose up
```

This starts:

- API at `http://127.0.0.1:8000`
- Vite frontend at `http://127.0.0.1:5173`

## 8. Running locally without Docker

### Backend

Install runtime dependencies:

```bash
python3 -m pip install fastapi uvicorn pydantic pytest websockets
```

Start the API:

```bash
uvicorn bot_arena_exchange.adapters.api.fastapi_app:app --host 0.0.0.0 --port 8000
```

On startup, the LiquidityEngine launches all bots listed in `liquidity_bots.enabled` as background asyncio tasks. The DataLogger begins writing to `data/quotes.csv` and `data/trades.csv`. The market is live immediately.

### Frontend

From the frontend directory:

```bash
cd frontend
npm install
npm run dev
```

### Market Spy (optional CLI monitor)

In a separate terminal:

```bash
python market_spy.py --duration 15
```

### Analytics (post-simulation)

After the exchange runs:

```bash
# Export a unified market tape
python analytics/tape_export.py

# Generate a realism report
python analytics/realism_report.py
python analytics/realism_report.py --format md --output analytics/output/realism_report.md
```

## 9. Step-by-step demo flow

1. Start the API: `uvicorn bot_arena_exchange.adapters.api.fastapi_app:app --host 0.0.0.0 --port 8000`
2. Start the frontend: `cd frontend && npm run dev`
3. Open `http://127.0.0.1:5173`.
4. **Immediately**, you will see:
   - All 22 registered bots trading in the background (random traders, mean reverters, market makers, trend followers, arbitrageurs, spoofers, and noise traders)
   - Live order book depth updating in real time
   - Recent trades populating with bot-buyer/seller highlights
   - Leaderboard updating every 2 seconds with sparklines and rank-change flashes
5. Click a trader in the leaderboard to open the **Trader Detail** panel with a PnL area chart.
6. (Optional) Run `python market_spy.py --duration 10` in a terminal to see a text summary of activity.
7. (Optional) Run `python analytics/tape_export.py` to export the market tape and `python analytics/realism_report.py` to assess simulation realism.
8. Use the REST API to submit a bot, enter a tournament, and run it:
   - `POST /bots/submit` — submit your strategy
   - `POST /tournaments/default-phase-0/entries` — enter the tournament
   - `POST /tournaments/default-phase-0/run` — run the tournament loop

## 10. Manual API examples

### Get tournament config

```bash
curl http://127.0.0.1:8000/config
```

### Get market state

```bash
curl http://127.0.0.1:8000/market
```

### List bot statuses

```bash
curl http://127.0.0.1:8000/liquidity/bots
curl http://127.0.0.1:8000/trading/bots
```

### Submit a bot

```bash
curl -X POST http://127.0.0.1:8000/bots/submit \
  -H 'Content-Type: application/json' \
  -d '{
    "owner_id": "user-1",
    "bot_name": "demo-bot",
    "files": {
      "bot.py": "class Bot:\n    def on_tick(self, api):\n        return None\n\ndef create_bot():\n    return Bot()\n"
    }
  }'
```

### Enter a tournament

```bash
curl -X POST http://127.0.0.1:8000/tournaments/default-phase-0/entries \
  -H 'Content-Type: application/json' \
  -d '{
    "owner_id": "user-1",
    "bot_name": "demo-bot",
    "version": 1
  }'
```

### Run the tournament

```bash
curl -X POST http://127.0.0.1:8000/tournaments/default-phase-0/run
```

### Place an order

```bash
curl -X POST http://127.0.0.1:8000/order \
  -H 'Content-Type: application/json' \
  -d '{
    "side": "BUY",
    "price": 10000,
    "quantity": 1,
    "trader_id": "manual-trader",
    "symbol": "AAPL",
    "venue": "VENUE_1"
  }'
```

### Cancel an order

```bash
curl -X POST http://127.0.0.1:8000/orders/buy-000001/cancel \
  -H 'Content-Type: application/json' \
  -d '{
    "trader_id": "manual-trader"
  }'
```

### Reset the exchange

```bash
curl -X POST http://127.0.0.1:8000/reset
```

### WebSocket (using wscat or similar)

```bash
wscat -c ws://127.0.0.1:8000/ws/stream
```

## 11. Testing

Pytest is configured by `pytest.ini`:

```ini
[pytest]
testpaths = tests
python_files = test_*.py
python_classes = Test*
python_functions = test_*
```

Run tests with:

```bash
make test
```

or directly:

```bash
python3 -m pytest
```

The current tests cover:

- config loading and validation (including bot registry, liquidity/trading bot configs, regime parameters)
- API gateway validation
- bot validation/submission/versioning
- order book matching, cancellation, fills, and order status
- exchange service order flow, fees, account state, and events
- tournament listing, entry, scheduled run, and leaderboard ranking

## 12. Core domain concepts

### Price units

Prices are positive integers. The order book rejects non-integer or non-positive prices. The default config uses `10000` as the initial reference price for `AAPL`, which can be interpreted as a minor-unit price such as cents or pips.

### Quantity units

Quantities are positive integers and must match the configured lot size.

### Symbols and venues

A symbol must exist in tournament markets. A venue must exist in tournament venues and support the requested symbol. Current default: `AAPL` on `VENUE_1` and `VENUE_2`.

### Fees

Fees are configured per venue in basis points. `TournamentManager.process_trades()` applies fees to both buyer and seller account updates.

### Spreads

Spreads are configured per venue and used in scoring penalty calculations. Market-making bots use venue spread to quote around the mid-price.

### Position limits

`TournamentManager` defaults to a position limit of `100`. Gateway validation rejects orders that would exceed the limit, and account processing can disconnect accounts that breach the hard limit after fills. System accounts bypass these limits.

### System accounts

Configured via `system_account_ids` in the tournament config. System accounts:
- Bypass position limit checks
- Are never disconnected for risk breaches
- Are excluded from leaderboards and scoring

### Bot registry

The `bot_registry` section maps `trader_id` → Python class name. At startup, the LiquidityEngine instantiates each bot and launches it as a background asyncio task. A factory function system configures venue assignment and dimension parameters per bot ID (e.g., `Bot_MM_V2_Alpha` routes to VENUE_2 with thinner, more elastic liquidity settings).

### Bot categories

- **Liquidity bots**: Provide continuous quotes to keep the book filled. Listed in `liquidity_bots.enabled`. Queried via `GET /liquidity/bots`.
- **Trading bots**: Compete for PnL and appear on the leaderboard. Listed in `trading_bots.enabled`. Queried via `GET /trading/bots`.

### Delta penalty

If enabled, score calculation penalizes remaining exposure using:

```text
abs(position) * reference_price * (spread_bps + liquidation_fee_bps) // 10000
```

Adjusted score is:

```text
raw_pnl - liquidation_penalty
```

### Regime visibility

Each regime has a `visible_before_tournament` flag. Two regimes (`liquidity_shock` and `black_swan`) are hidden from participants before the tournament starts, creating surprise market conditions. Regimes also carry `volatility_bps`, `liquidity_multiplier`, and `spread_multiplier` parameters for future market simulation.

### Execution model

Orders execute immediately — there is no latency queue. All mutations to the order book and tournament manager are serialized through a single `asyncio.Lock`. The `LiquidityEngine` acquires this lock before every bot tick, so synchronous internal methods are safe to call from bot code.

### WebSocket events

| Event type | Payload | Frequency |
|---|---|---|
| `snapshot` | Full market state (best bid/ask, snapshot) | On connect only |
| `ORDERBOOK_UPDATE` | Full market state | After every order/cancel |
| `FILL` | Trade details (buyer, seller, price, quantity) | On every match |
| `LEADERBOARD_UPDATE` | Ranked leaderboard array | Every 2 seconds |

## 13. Current limitations

The repo is a prototype, so several items are not yet fully implemented:

- Submitted bot code is `exec()`'d but not sandboxed as untrusted executable code
- Data is in memory, not persisted to a database (only CSV writing for quotes/trades)
- There is no authentication or ownership enforcement beyond request fields
- CORS currently allows all origins
- Futures support is represented in config validation but not fully implemented as a separate accounting model
- Regime parameters exist in config but do not yet drive market simulation behavior (liquidity shock, volatility, etc.)
- Replay and diagnostics are not implemented as dedicated features
- Frontend `dist/` and `node_modules/` are generated/dependency artifacts
- Bot enable/disable methods exist in the LiquidityEngine but are not yet exposed as HTTP endpoints in `fastapi_app.py`
- The built-in `RandomTrader` and `MeanRevertingTrader` in `domain/bots.py` use a `think()` interface rather than `on_tick()`, while starter kit bots use the newer `on_tick()` contract

## 14. Development notes

Useful files to start with:

- Product plan: `bot-arena-exchange.md`
- API surface & startup: `bot_arena_exchange/adapters/api/fastapi_app.py`
- Bot runtime: `bot_arena_exchange/adapters/api/liquidity_engine.py`
- Main orchestration: `bot_arena_exchange/application/exchange_service.py`
- Order matching: `bot_arena_exchange/domain/order_book.py`
- Account/PnL/risk: `bot_arena_exchange/domain/tournament.py`
- Scoring: `bot_arena_exchange/domain/scoring.py`
- Built-in bots (RandomTrader, MeanRevertingTrader): `bot_arena_exchange/domain/bots.py`
- Tournament scheduling: `bot_arena_exchange/application/tournaments.py`
- Event log with pub/sub: `bot_arena_exchange/application/event_log.py`
- Data logger (CSV persistence): `bot_arena_exchange/application/data_logger.py`
- Default config: `bot_arena_exchange/config/default_tournament.json`
- Starter kit bots: `bot_arena_exchange/starter_kit/`
- CLI monitor: `market_spy.py`
- Analytics tools: `analytics/tape_export.py`, `analytics/realism_report.py`
- Frontend app: `frontend/src/main.jsx`
- Test suite: `tests/`

A good way to understand or change the project is:

1. Read `bot-arena-exchange.md` for product intent.
2. Read `default_tournament.json` to understand the active simulation rules and bot registry.
3. Read `ExchangeService` to understand the application flow.
4. Read `LiquidityEngine` to understand how bots run as background tasks.
5. Read `OrderBook` to understand matching behavior.
6. Read `TournamentManager` and `scoring.py` to understand account updates and leaderboard logic.
7. Read `bots.py` and `starter_kit/` to understand built-in and starter kit bot strategies.
8. Read `DataLogger` to understand how market data is persisted to CSV.
9. Read `analytics/tape_export.py` and `analytics/realism_report.py` for post-simulation analysis.
10. Read `fastapi_app.py` to see how the backend is exposed over HTTP and WebSocket.
11. Read `frontend/src/main.jsx` to see the live dashboard.
12. Run `python3 -m pytest` before and after changes.

## 15. MVP roadmap from the product brief

The product brief breaks the work into phases:

1. **Phase 0: Product Decisions and Configurable Rules** — config schema, default tournament rules, gateway rules, event log requirements.
2. **Phase 1: Builder Onboarding and Bot Lifecycle** — starter kit, validation, submission/versioning, gateway v1.
3. **Phase 2: Core Exchange Simulation** — limit orders, price-time matching, events, account/market state, fees, latency.
4. **Phase 3: Scheduled Tournament Loop** — tournament listing, entry, scheduled runner, liquidity bot, leaderboard.
5. **Phase 4: Strategic Market Depth** — futures, dual venues, margin/liquidation, regimes, regime-aware liquidity bots.
6. **Phase 5: Post-Tournament Learning Loop** — replay, diagnostics, leaderboard history.

The current codebase has working slices through phases 0–4, with the following additions beyond the original MVP:

- **Multi-bot arena**: 22 bots from 13 strategy classes run concurrently as background asyncio tasks via the LiquidityEngine.
- **Real-time streaming**: WebSocket endpoint pushes order book updates, fills, and leaderboard changes to all connected clients.
- **Dual venues**: VENUE_1 (fast, zero-fee) and VENUE_2 (slower, 15 bps fee, higher spread) with cross-venue bots.
- **Bot registry with factory functions**: JSON-driven mapping of trader IDs to Python bot classes, with per-ID venue routing and parameter tuning.
- **System accounts**: 22 configurable accounts that bypass risk limits and are excluded from leaderboards.
- **Live dashboard**: React frontend with order book depth, recent trades, leaderboard sparklines, trader detail PnL charts, and rank-change animations.
- **CLI monitor**: `market_spy.py` for terminal-based activity observation.
- **Bot execution**: Real Python `exec()` of submitted strategies with `ApiProxy` injection during tournament loops.
- **Data persistence**: `DataLogger` writes all order book updates and fills to `data/quotes.csv` and `data/trades.csv` in real time.
- **Analytics pipeline**: `tape_export.py` merges CSVs into unified market tapes; `realism_report.py` generates microstructure realism assessments (return distribution, Hurst exponent, price impact, cross-venue correlation, and more).