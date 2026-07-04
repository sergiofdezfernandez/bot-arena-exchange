# Bot Arena Exchange

Bot Arena Exchange is an MVP prototype for a competitive trading-bot arena: users submit Python bots, enter them into scheduled simulated tournaments, run a limit-order-book exchange loop, and inspect market state, events, scores, and leaderboard results.

The product direction is described in `bot-arena-exchange.md`: a “Kaggle for trading bots” where serious quant builders compete inside a simulated exchange rather than against static historical datasets.

## 1. What this repo contains

This repository has three main parts:

1. **Python backend domain and application code** in `bot_arena_exchange/`
2. **FastAPI HTTP adapter** in `bot_arena_exchange/adapters/api/fastapi_app.py`
3. **React/Vite frontend** in `frontend/`

It also includes:

- `tests/` — pytest test suite for configuration, bot lifecycle, exchange simulation, order book behavior, and tournament flow
- `bot_arena_exchange/config/default_tournament.json` — default tournament rules, markets, venues, scoring, latency, fees, spreads, and regimes
- `docker-compose.yml` — local API + web startup using Python and Node containers
- `Makefile` — convenience `make test` command
- `bot-arena-exchange.md` — product brief, user stories, MVP phases, milestones, and delivery risks

## 2. Current product scope

The implemented prototype covers the early MVP flow:

1. Load configurable tournament rules.
2. Validate and submit a Python bot.
3. Store accepted bot versions in memory.
4. List an upcoming scheduled tournament.
5. Enter a submitted bot version into that tournament.
6. Seed default exchange liquidity through a simple market maker.
7. Place, queue, match, fill, cancel, or reject limit orders.
8. Track trader positions, average costs, realized PnL, fees, and risk status.
9. Score accounts using raw PnL minus optional delta liquidation penalties.
10. Publish a ranked leaderboard after a scheduled tournament run.
11. Display the core flow in a React UI.

The broader product plan includes futures, dual venues, richer regimes, margin/liquidation, replay, and diagnostics, but the current codebase is still a prototype focused on the core loop.

## 3. Repository structure

```text
.
├── README.md
├── bot-arena-exchange.md
├── docker-compose.yml
├── Makefile
├── pytest.ini
├── bot_arena_exchange/
│   ├── adapters/
│   │   └── api/
│   │       └── fastapi_app.py
│   ├── application/
│   │   ├── api_gateway.py
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
│       └── example_bot.py
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

- tournament id: `default-phase-0`
- rules: duration ticks, entry deadline offset, minimum participants
- scoring: liquidation fee and delta penalty toggle
- markets: currently `AAPL` spot with integer prices and lot sizes
- venues: `VENUE_1` and `VENUE_2`, each with fee, spread, latency, and supported symbols
- regimes: sideways, trending, high volatility, liquidity shock, and black swan

`bot_arena_exchange/config/tournament_config.py` converts that JSON into dataclasses and validates the values. It rejects invalid tournament ids, empty markets, empty venues, invalid market types, duplicate symbols, duplicate venues, negative fees/spreads/latency, unsupported venue symbols, and invalid regimes.

### Step 2: ExchangeService wires the application together

`bot_arena_exchange/application/exchange_service.py` is the main application service.

It creates or receives:

- `TournamentConfig`
- `OrderBook`
- `TournamentManager`
- `InMemoryEventLog`
- `BotLifecycleService`
- `ApiGateway`
- `TournamentScheduler`

Most API endpoints delegate to this service.

Important responsibilities:

- return tournament config
- return starter kit content
- validate and submit bots
- list bot versions
- list and inspect tournaments
- enter bots into tournaments
- place and cancel orders
- advance latency ticks
- return market/account/event state
- score traders
- publish a leaderboard
- run the scheduled tournament loop

### Step 3: Bot lifecycle validates and versions submitted bots

Bot lifecycle code lives under `bot_arena_exchange/application/bot_lifecycle/`.

The intended bot shape is shown in `bot_arena_exchange/starter_kit/example_bot.py`:

```python
class ExampleBot:
    def on_tick(self, api):
        ...


def create_bot():
    return ExampleBot()
```

A submitted bot is expected to include Python source files, including a bot entry point. Accepted submissions are stored as versions so a tournament entry can point to the exact submitted bot version.

The React UI submits a simple demo bot with:

```python
class Bot:
    def on_tick(self, api):
        return None


def create_bot():
    return Bot()
```

### Step 4: API gateway validates trading requests

`bot_arena_exchange/application/api_gateway.py` validates order payloads before they reach the order book.

It checks:

- tournament status is `RUNNING`
- required fields are present
- side is `BUY` or `SELL`
- price is a positive integer
- quantity is a positive integer
- trader id is a non-empty string
- symbol exists in configured markets
- venue exists in configured venues
- venue supports the requested symbol
- price matches market tick size
- quantity matches market lot size
- trader account is active
- order would not exceed the position limit

Rejected requests are recorded in the event log by `ExchangeService.place_order`.

### Step 5: OrderBook matches orders by price-time priority

`bot_arena_exchange/domain/order_book.py` implements the limit order book.

Core concepts:

- `Order` is a dataclass with id, side, price, quantity, trader id, timestamp, symbol, venue, time-in-force, remaining quantity, and status.
- bids and asks are grouped by price level.
- heaps track best bid and best ask efficiently.
- open quantity maps track active liquidity at each price level.
- orders remain in `self.orders` for audit/history even after fill or cancel.

Supported order behavior:

- `GTC` — good till cancelled; unfilled remainder rests on the book
- `IOC` — immediate or cancel; unfilled remainder is cancelled
- `FOK` — fill or kill; cancelled if full quantity cannot be filled immediately

Matching rules:

- buy orders match the lowest ask at or below the buy price
- sell orders match the highest bid at or above the sell price
- resting orders at the same price fill in queue order
- execution price is the resting maker order price
- partial fills reduce remaining quantities
- fully filled orders become `filled`
- cancelled orders are lazily removed when their price level is inspected

### Step 6: TournamentManager updates accounts from trades

`bot_arena_exchange/domain/tournament.py` tracks trader accounts.

Each `TraderAccount` contains:

- trader id
- positions by symbol
- cost basis totals
- average costs
- realized PnL
- fees paid
- status (`ACTIVE` or `DISCONNECTED`)

`TournamentManager.process_trades()` reads trades emitted by the order book and updates buyer/seller accounts. It applies venue fees, updates inventory, calculates realized PnL when positions are reduced or closed, and disconnects accounts that breach the configured position limit.

### Step 7: Event log records important actions

`ExchangeService` records events through `InMemoryEventLog`.

Events include:

- accepted orders
- rejected orders
- fills
- cancels
- rejected cancels
- disconnections

The API exposes these events through `GET /events`, and the frontend displays the latest events.

### Step 8: Latency is modeled with ticks

Venues can define `latency_ticks` in the tournament config.

When an accepted order targets a venue with latency:

1. `ExchangeService.place_order()` queues it with a due tick.
2. The response status is `QUEUED`.
3. `ExchangeService.advance_tick()` increments time.
4. When the due tick arrives, the order is executed.

The API exposes this through `POST /tick`.

### Step 9: Scoring computes leaderboard rows

`bot_arena_exchange/domain/scoring.py` scores accounts.

A score includes:

- trader id
- raw PnL
- delta exposure
- liquidation penalty
- adjusted score
- status

When delta penalty is enabled, remaining positions are penalized by reference price, spread bps, and liquidation fee bps. `ExchangeService.get_leaderboard()` sorts scores by adjusted score descending and adds rank numbers.

### Step 10: TournamentScheduler manages the scheduled competition

`bot_arena_exchange/application/tournaments.py` creates one scheduled tournament from the default config.

A scheduled tournament includes:

- tournament id
- start time
- entry deadline
- status
- entries
- leaderboard after completion

The scheduler can:

- list tournaments
- return tournament detail
- accept entries before the deadline
- mark a tournament as running
- publish completed results

### Step 11: Running a scheduled tournament

`ExchangeService.run_scheduled_tournament()` performs the prototype tournament loop:

1. Find the scheduled tournament.
2. Mark it as `RUNNING`.
3. Set service tournament status to `RUNNING`.
4. Seed default liquidity.
5. Run each entered bot once through `_run_entered_bot_once()`.
6. Advance ticks for the configured tournament duration.
7. Compute the leaderboard.
8. Publish results to the scheduler.
9. Mark service tournament status as `COMPLETED`.
10. Return tournament detail and leaderboard.

The current bot execution is intentionally minimal: an entered bot is represented by a generated trader id and places one buy order at the initial reference price.

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
| `GET` | `/market` | Return tick, best bid/ask, snapshot, trades, pending orders |
| `GET` | `/orders/{order_id}` | Return order state |
| `GET` | `/accounts/{trader_id}` | Return account state |
| `GET` | `/traders` | Return all known trader account states |
| `GET` | `/events` | Return event log |
| `GET` | `/scores` | Return raw score objects |
| `GET` | `/leaderboard` | Return ranked leaderboard |
| `GET` | `/tournaments` | List scheduled tournaments |
| `GET` | `/tournaments/{tournament_id}` | Return tournament detail |
| `GET` | `/starter-kit` | Return starter kit information |
| `GET` | `/bots/{owner_id}/versions` | List submitted bot versions for owner, optionally filtered by bot name |
| `GET` | `/bots/{owner_id}/{bot_name}/versions/{version}` | Return one bot version |

### Write endpoints

| Method | Path | Purpose |
|---|---|---|
| `POST` | `/bots/validate` | Validate submitted bot files |
| `POST` | `/bots/submit` | Validate and save a bot version |
| `POST` | `/tournaments/{tournament_id}/entries` | Enter a bot version into a tournament |
| `POST` | `/tournaments/{tournament_id}/run` | Run scheduled tournament and publish leaderboard |
| `POST` | `/order` | Place an order |
| `POST` | `/orders/{order_id}/cancel` | Cancel an order owned by a trader |
| `POST` | `/tick` | Advance exchange ticks and process queued latency orders |

## 6. Frontend

The frontend is a small React app in `frontend/src/main.jsx`.

It uses:

- React
- React DOM
- Vite
- `VITE_API_BASE_URL`, defaulting to `http://127.0.0.1:8000`

The UI shows:

- tournament id and status
- best bid
- best ask
- pending orders
- a three-step flow: submit bot, enter tournament, run tournament
- submission/entry/run responses
- leaderboard table
- recent event JSON
- market state JSON

Frontend scripts are defined in `frontend/package.json`:

```bash
npm run dev
npm run build
npm run preview
```

## 7. Running locally with Docker Compose

The easiest way to run both API and frontend is Docker Compose:

```bash
docker compose up
```

This starts:

- API at `http://127.0.0.1:8000`
- Vite frontend at `http://127.0.0.1:5173`

The compose file uses:

- `python:3.11-slim` for the API
- `node:22-slim` for the frontend
- `pip install fastapi uvicorn pydantic` inside the API container
- `npm install && npm run dev` inside the frontend container

## 8. Running locally without Docker

### Backend

Install runtime dependencies in your preferred Python environment:

```bash
python3 -m pip install fastapi uvicorn pydantic pytest
```

Start the API:

```bash
uvicorn bot_arena_exchange.adapters.api.fastapi_app:app --host 0.0.0.0 --port 8000
```

### Frontend

From the frontend directory:

```bash
cd frontend
npm install
npm run dev
```

If the API is not at the default URL, set:

```bash
VITE_API_BASE_URL=http://127.0.0.1:8000 npm run dev
```

## 9. Step-by-step demo flow

After starting the API and frontend:

1. Open the frontend at `http://127.0.0.1:5173`.
2. Click **Refresh** to load config, tournament, market, leaderboard, and events.
3. Click **1. Submit Bot**.
   - The frontend submits a minimal Python bot.
   - The backend validates it and stores a version.
4. Click **2. Enter Tournament**.
   - The frontend enters the submitted bot version into the configured scheduled tournament.
5. Click **3. Run Tournament**.
   - The backend marks the tournament running.
   - Seeds default liquidity.
   - Runs entered bots once.
   - Advances configured ticks.
   - Scores accounts.
   - Publishes a leaderboard.
6. Inspect:
   - leaderboard table
   - recent events
   - market state JSON

## 10. Manual API examples

### Get tournament config

```bash
curl http://127.0.0.1:8000/config
```

### Get market state

```bash
curl http://127.0.0.1:8000/market
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

### Advance ticks

```bash
curl -X POST http://127.0.0.1:8000/tick \
  -H 'Content-Type: application/json' \
  -d '{"ticks": 2}'
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

- config loading and validation
- API gateway validation
- bot validation/submission/versioning
- order book matching, cancellation, fills, and order status
- exchange service order flow, fees, latency, account state, and events
- tournament listing, entry, scheduled run, and leaderboard ranking

## 12. Core domain concepts

### Price units

Prices are positive integers. The order book rejects non-integer or non-positive prices. The default config uses `10000` as the initial reference price for `AAPL`, which can be interpreted as a minor-unit price such as cents or pips.

### Quantity units

Quantities are positive integers and must match the configured lot size.

### Symbols and venues

A symbol must exist in tournament markets. A venue must exist in tournament venues and support the requested symbol.

Current default:

- symbol: `AAPL`
- venues: `VENUE_1`, `VENUE_2`

### Fees

Fees are configured per venue in basis points. `TournamentManager.process_trades()` applies fees to both buyer and seller account updates.

### Spreads

Spreads are configured per venue and used in scoring penalty calculations. The default seeded market maker also uses the first venue spread to quote around the initial reference price.

### Latency

Latency is configured per venue in ticks. A venue with zero latency executes immediately. A venue with positive latency queues accepted orders until enough ticks have advanced.

### Position limits

`TournamentManager` defaults to a position limit of `100`. Gateway validation rejects orders that would exceed the limit, and account processing can disconnect accounts that breach the hard limit after fills.

### Delta penalty

If enabled, score calculation penalizes remaining exposure using:

```text
abs(position) * reference_price * (spread_bps + liquidation_fee_bps) // 10000
```

Adjusted score is:

```text
raw_pnl - liquidation_penalty
```

## 13. Current limitations

The repo is a prototype, so several product-plan items are not fully implemented yet:

- submitted bot code is validated/stored, but not sandboxed as untrusted executable code
- scheduled tournament bot execution is simplified to one generated order per entered bot
- data is in memory, not persisted to a database
- there is no authentication or ownership enforcement beyond request fields
- CORS currently allows all origins
- frontend is a demo workflow, not a complete product UI
- futures support is represented in config validation but not fully implemented as a separate accounting model
- regimes exist in config but do not yet drive market simulation behavior
- replay and diagnostics are not implemented as dedicated features
- frontend `dist/` and `node_modules/` are present locally but are generated/dependency artifacts, not source architecture

## 14. Development notes

Useful files to start with:

- Product plan: `bot-arena-exchange.md`
- API surface: `bot_arena_exchange/adapters/api/fastapi_app.py`
- Main orchestration: `bot_arena_exchange/application/exchange_service.py`
- Order matching: `bot_arena_exchange/domain/order_book.py`
- Account/PnL/risk: `bot_arena_exchange/domain/tournament.py`
- Scoring: `bot_arena_exchange/domain/scoring.py`
- Tournament scheduling: `bot_arena_exchange/application/tournaments.py`
- Default config: `bot_arena_exchange/config/default_tournament.json`
- Frontend app: `frontend/src/main.jsx`
- Test suite: `tests/`

A good way to understand or change the project is:

1. Read `bot-arena-exchange.md` for product intent.
2. Read `default_tournament.json` to understand the active simulation rules.
3. Read `ExchangeService` to understand the application flow.
4. Read `OrderBook` to understand matching behavior.
5. Read `TournamentManager` and `scoring.py` to understand account updates and leaderboard logic.
6. Read `fastapi_app.py` to see how the backend is exposed over HTTP.
7. Read `frontend/src/main.jsx` to see the current user-facing demo.
8. Run `python3 -m pytest` before and after changes.

## 15. MVP roadmap from the product brief

The product brief breaks the work into phases:

1. **Phase 0: Product Decisions and Configurable Rules** — config schema, default tournament rules, gateway rules, event log requirements.
2. **Phase 1: Builder Onboarding and Bot Lifecycle** — starter kit, validation, submission/versioning, gateway v1.
3. **Phase 2: Core Exchange Simulation** — limit orders, price-time matching, events, account/market state, fees, latency.
4. **Phase 3: Scheduled Tournament Loop** — tournament listing, entry, scheduled runner, liquidity bot, leaderboard.
5. **Phase 4: Strategic Market Depth** — futures, dual venues, margin/liquidation, regimes, regime-aware liquidity bots.
6. **Phase 5: Post-Tournament Learning Loop** — replay, diagnostics, leaderboard history.

The current codebase has working slices through phases 0–3, with phase 4/5 concepts mostly represented as configuration, product plan, or future work.
