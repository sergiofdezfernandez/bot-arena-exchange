# Bot Arena Exchange

## Problem Statement
How might we let serious quant builders compete with trading bots in a simulated exchange where market behavior is realistic enough that winning actually means something?

## Recommended Direction
Build a competitive bot-submission platform: “Kaggle for trading bots,” but instead of static datasets, bots compete inside a live simulated exchange. The product’s core loop is simple: submit bot, run tournament, see ranking, inspect failures, improve bot.

The MVP should focus on one strong simulation loop, not a full financial universe. Start with spot trading, futures, and dual-listed assets across a few predefined regimes. This creates enough strategic depth: market making, arbitrage, hedging, latency games, inventory risk, margin pressure, and crash survival.

Options are valuable but should come later. They add volatility surfaces, Greeks, expiry behavior, implied volatility dynamics, and complex hedging. If added too early, they risk turning the MVP into an unfinished quant library instead of a competitive product.

## Key Assumptions to Validate
- [ ] Builders want to compete via code, not just backtest privately — test with 10-20 quant/dev users and ask if they would submit bots weekly.
- [ ] A bounded simulator can feel credible without perfectly matching real markets — test with expert review of matching, fees, latency, and margin rules.
- [ ] Leaderboards motivate improvement — test whether users care about rankings, tournament history, and bot diagnostics.
- [ ] Bot interaction is more compelling than static historical backtests — compare user reaction to live multi-agent simulation vs dataset backtest.
- [ ] Built-in liquidity-provider bots create enough market depth for competitions before user participation scales — test whether tournaments remain tradable with only baseline bots plus a few user bots.

## MVP Scope
Users submit Python bots through a defined API. Bots trade in scheduled simulated tournaments against other user bots and built-in liquidity-provider agents.

In scope:
- Python-only bot SDK/API
- Scheduled competitions
- Limit order book matching engine
- One or two spot assets
- Futures on those assets
- Dual listing across two venues
- Latency, fees, spreads, margin, liquidation
- Built-in liquidity-provider bots that quote markets, absorb flow, and create minimum viable liquidity
- 3-5 regimes: sideways, trending, high volatility, liquidity shock, black swan
- Leaderboard ranked by PnL adjusted by delta exposure
- Basic replay/analytics after each tournament

Out of scope for MVP:
- Full options market
- Real-money trading
- Perfect institutional-grade market realism
- User-created regimes
- Dozens of assets
- Complex consensus mechanisms unless directly tied to tournament design
- Multiple bot programming languages
- Continuous always-on tournaments

## Not Doing (and Why)
- Options in v1 — too much complexity before the core engine is proven.
- Real exchange connectivity — changes the product from simulation arena to regulated trading infrastructure.
- “All users” positioning — the first audience is serious quant builders who can code bots.
- Infinite market customization — fixed regimes are better for fair competition and faster iteration.
- Perfect realism — the goal is credible competitive realism, not a Bloomberg-grade market simulator.
- Multiple languages in v1 — Python is enough for the target user and keeps sandboxing/API design simpler.

## Open Questions
- What exact formula should define PnL adjusted by delta exposure?
- How long should each scheduled competition run?
- Should users see full market replay, bot-level diagnostics, or only aggregate results?
- How strict should sandboxing be for submitted Python bots?
- Should built-in liquidity bots be transparent, partially documented, or hidden?

## MVP User Stories

### 1. Create First Python Bot

**Card:** As a Quant Builder, I want a Python starter kit, so that I can create my first working bot quickly.

**Design:** TBD

**Conversation:** The starter kit is the first builder experience. It should show the required bot shape, one small working example, and the basic actions a bot can take. The goal is not to teach trading. The goal is to remove setup confusion.

**Confirmation:**
1. The starter kit includes one working Python example bot.
2. The example bot reads market data, places an order, cancels an order, and reads its position.
3. The starter kit explains the required bot entry point in plain language.
4. A user can run a local check that confirms the bot structure is valid.
5. The starter kit supports Python only.
6. The example uses at least one spot market.

### 2. Validate Bot Code

**Card:** As a Quant Builder, I want my bot checked before submission, so that I can fix simple errors early.

**Design:** TBD

**Conversation:** Builders should know if their bot can run before they enter a tournament. Validation should catch structural problems, unsupported dependencies, and basic API misuse. It should not judge whether the trading strategy is good.

**Confirmation:**
1. The system checks that the submitted files use the required Python format.
2. The system checks that the bot has the required entry point.
3. The system checks that dependencies are allowed for the MVP sandbox.
4. The system returns clear errors for failed checks.
5. The system confirms when validation passes.
6. Only a validated bot can be submitted as a tournament-ready version.

### 3. Submit Versioned Bot

**Card:** As a Quant Builder, I want to save each accepted bot as a version, so that I can track what changed between tournaments.

**Design:** TBD

**Conversation:** Serious builders will submit many versions. Results must point back to the exact code that ran. A failed upload should not create a valid version.

**Confirmation:**
1. A user can submit a bot after validation passes.
2. Each accepted submission creates a new version number.
3. Each version stores owner, bot name, version number, and submission time.
4. A user can view their own bot versions.
5. A user can select a version for tournament entry.
6. Failed submissions are saved as errors, not tournament-ready versions.

### 4. Route Bot Requests Through API Gateway

**Card:** As a Platform Operator, I want bot requests to pass through an API gateway, so that only valid actions reach the order book.

**Design:** TBD

**Conversation:** In the MVP, bots interact with the platform by sending API requests. The platform should validate each request, record it, apply tournament rules, and only then forward valid actions to the order book. A full code sandbox is only needed later if the platform executes untrusted bot code directly.

**Confirmation:**
1. A bot sends trading actions through the platform API.
2. The API validates asset, venue, side, price, size, permissions, margin, and tournament status before accepting an action.
3. Invalid requests are rejected with a clear reason.
4. Every request is recorded with bot, tournament, timestamp, payload, validation result, and final action.
5. Only accepted actions are forwarded to the order book.
6. The tournament continues when a bot sends invalid requests.

### 5. View Upcoming Tournaments

**Card:** As a Quant Builder, I want to see upcoming tournaments, so that I can choose where to compete.

**Design:** TBD

**Conversation:** The MVP uses scheduled tournaments, not always-on trading. Builders need enough information to decide if a tournament is worth entering.

**Confirmation:**
1. A user can view a list of upcoming tournaments.
2. Each tournament shows start time, expected duration, entry deadline, markets, and scoring method.
3. Each tournament shows whether entries are open or closed.
4. A user can open a tournament detail page.
5. The detail page shows the rules that will be used during the tournament.
6. Closed tournaments cannot accept new entries.

### 6. Enter Bot into Tournament

**Card:** As a Quant Builder, I want to enter one bot version into a tournament, so that it can compete when the tournament starts.

**Design:** TBD

**Conversation:** Entry should be simple and strict. The user should always know which version is entered. Changes are allowed before the deadline, but not after.

**Confirmation:**
1. A user can select one accepted bot version for an open tournament.
2. The system confirms the selected bot version and tournament.
3. A user can change the selected bot version before the deadline.
4. The system blocks entries and changes after the deadline.
5. The tournament starts with all valid user entries.
6. Built-in liquidity bots are added when the tournament starts.

### 7. Place and Cancel Limit Orders

**Card:** As a Quant Builder, I want my bot to place and cancel limit orders, so that it can compete through realistic market actions.

**Design:** TBD

**Conversation:** Limit orders are the core trading action for the MVP. Bots should state what they want to buy or sell, at what price, and on which market.

**Confirmation:**
1. A bot can place a limit order with asset, venue, side, price, and size.
2. A bot can cancel its own open order.
3. The system rejects orders with invalid asset, venue, side, price, or size.
4. The bot receives accepted, rejected, cancelled, partial fill, and full fill events.
5. The system applies fees when trades execute.
6. A bot cannot cancel another bot’s order.

### 8. Match Orders Fairly

**Card:** As a Quant Builder, I want orders matched by clear rules, so that tournament results feel fair.

**Design:** TBD

**Conversation:** The matching engine should be simple, visible, and consistent. Price-time priority is enough for MVP credibility.

**Confirmation:**
1. Buy orders match with the lowest available sell price at or below the buy price.
2. Sell orders match with the highest available buy price at or above the sell price.
3. When prices are equal, the oldest order fills first.
4. Partial fills leave the remaining quantity on the book unless cancelled.
5. Every fill records price, size, time, asset, venue, maker, and taker.
6. Matching rules are the same for all user bots.

### 9. Read Market and Account State

**Card:** As a Quant Builder, I want my bot to read current market and account state, so that it can make trading decisions.

**Design:** TBD

**Conversation:** Bots need useful information, but not hidden future information. The API should expose current allowed state only.

**Confirmation:**
1. A bot can read the current order book for supported markets.
2. A bot can read its open orders and recent fills.
3. A bot can read cash, positions, margin usage, and PnL.
4. The API does not expose future prices.
5. The API does not expose private state from other user bots.
6. State updates follow tournament latency rules.

### 10. Trade Spot and Futures

**Card:** As a Quant Builder, I want spot and futures markets in the same tournament, so that my bot can hedge and take directional risk.

**Design:** TBD

**Conversation:** The MVP should be deep enough for real strategy without becoming a full market simulator. One or two spot assets plus futures gives enough room for market making, hedging, and risk management.

**Confirmation:**
1. Each MVP tournament includes one or two spot assets.
2. Each supported spot asset has at least one futures market.
3. Bots can place orders in spot and futures markets.
4. The system tracks spot and futures positions separately.
5. The system calculates realized and unrealized PnL for both market types.
6. Market definitions are visible before tournament entry.

### 11. Trade Across Two Venues

**Card:** As a Quant Builder, I want the same asset on two venues, so that my bot can trade price differences between venues.

**Design:** TBD

**Conversation:** Dual listing creates strategy without adding many assets. It supports arbitrage, latency games, fee-aware routing, and inventory management.

**Confirmation:**
1. At least one asset is listed on two simulated venues.
2. Each venue has its own order book.
3. Each venue can have different fees, spreads, and latency.
4. Bots can place and cancel orders on either venue.
5. Fills are tracked by venue and asset.
6. Replay shows which venue each trade happened on.

### 12. Apply Trading Frictions

**Card:** As a Quant Builder, I want latency, fees, and spreads included, so that unrealistic instant-profit bots do not dominate.

**Design:** TBD

**Conversation:** Friction makes the arena credible. The MVP should make every bot deal with delayed actions, trading costs, and spreads.

**Confirmation:**
1. Each tournament defines latency, fee, and spread rules before it starts.
2. Bot actions are delayed according to tournament latency rules.
3. Trade fees are deducted from account value.
4. Spreads affect quoted prices and execution opportunities.
5. The same friction rules apply to all user bots in the tournament.
6. Results show total fees paid by each bot.

### 13. Enforce Margin and Liquidation

**Card:** As a Quant Builder, I want margin and liquidation rules, so that risky strategies have clear limits.

**Design:** TBD

**Conversation:** Bots should be allowed to take risk, especially in futures, but not unlimited risk. The platform should block impossible orders and liquidate accounts that fall below requirements.

**Confirmation:**
1. Each bot starts with defined cash, margin, and position limits.
2. Futures positions increase margin usage.
3. Orders that would break margin rules are rejected.
4. A bot is liquidated when account value falls below the required margin level.
5. Liquidation events are sent to the bot and saved in results.
6. The tournament continues after liquidation.

### 14. Run Fixed Market Regimes

**Card:** As a Platform Operator, I want fixed market regimes, so that tournaments are fair, repeatable, and varied.

**Design:** TBD

**Conversation:** The MVP should not let users create custom markets. A small set of fixed regimes keeps the arena easier to trust and compare.

**Confirmation:**
1. The MVP supports sideways, trending, high volatility, liquidity shock, and black swan regimes.
2. Each tournament uses one or more predefined regimes.
3. Users cannot create or edit regimes in the MVP.
4. Regimes can affect price movement, spread, liquidity, and volatility.
5. The system records which regimes were used in each tournament.
6. The platform can rerun a tournament with the same regime setup.

### 15. Provide Built-In Liquidity Bots

**Card:** As a Quant Builder, I want built-in liquidity bots, so that markets are tradable even when few users join.

**Design:** TBD

**Conversation:** Early tournaments may have low user participation. Built-in liquidity bots should provide minimum depth without knowing user bot logic or future events.

**Confirmation:**
1. Built-in liquidity bots participate in every MVP tournament.
2. They quote buy and sell prices for each supported market.
3. They provide minimum market depth during normal conditions.
4. Their behavior can change during high volatility, liquidity shock, and black swan regimes.
5. Their orders and trades appear in market data and replay.
6. They cannot access private user bot logic or future market events.

### 16. Publish Risk-Adjusted Leaderboard

**Card:** As a Quant Builder, I want rankings based on PnL adjusted by delta exposure, so that winners are not only the bots that took the biggest directional bet.

**Design:** TBD

**Conversation:** The leaderboard is the main motivation loop. It should be easy to understand, trusted by builders, and saved as tournament history.

**Confirmation:**
1. A completed tournament produces a ranked leaderboard.
2. The leaderboard shows rank, bot name, owner, raw PnL, delta exposure, adjusted score, and status.
3. Failed, liquidated, and rule-breaking bots are clearly marked.
4. The scoring formula is shown with the leaderboard.
5. A user can see their own result and top competitors.
6. Results are saved in tournament history.

### 17. Review Tournament Replay

**Card:** As a Quant Builder, I want to replay a completed tournament, so that I can understand how my bot behaved.

**Design:** TBD

**Conversation:** Replay helps users trust the result and improve their bot. MVP replay should focus on prices, orders, fills, positions, PnL, and major events.

**Confirmation:**
1. A user can open replay for a completed tournament.
2. Replay shows price movement over time.
3. Replay shows the user bot’s orders, fills, positions, and PnL over time.
4. Replay marks rejected orders, liquidations, and large losses.
5. A user can filter replay to their own bot.
6. Replay is available after leaderboard publication.

### 18. View Bot Diagnostics

**Card:** As a Quant Builder, I want basic diagnostics after a tournament, so that I know what to improve next.

**Design:** TBD

**Conversation:** Diagnostics should answer simple questions: did the bot trade, did it miss fills, did it pay too much in fees, did it fail, and where did it lose money?

**Confirmation:**
1. A user can view diagnostics for their own bot after a tournament.
2. Diagnostics show total trades, rejected orders, fees paid, final position, final PnL, and liquidation status.
3. Runtime failures are shown separately from trading losses.
4. The largest loss event is identified.
5. Users cannot view private diagnostics for other users’ bots.
6. Diagnostics are linked from the leaderboard and replay.

## MVP Estimation

Shirt size scale:
- **XS:** Very small; mostly configuration, copy, or simple UI/API work.
- **S:** Small; clear scope, low unknowns, one main component.
- **M:** Medium; several moving parts or moderate domain logic.
- **L:** Large; complex logic, integrations, or important reliability concerns.
- **XL:** Extra large; high uncertainty, deep engine work, or security-sensitive work.

| # | User Story | Size | Rationale |
|---|---|---:|---|
| 1 | Create First Python Bot | S | Starter kit, example bot, and local structure check are bounded. |
| 2 | Validate Bot Code | M | Needs static checks, dependency rules, and clear validation feedback. |
| 3 | Submit Versioned Bot | M | Requires storage, ownership, versioning, and submission states. |
| 4 | Run Bot Through API Gateway | L | Requires request validation, permission checks, event logging, and safe order-book handoff. |
| 5 | View Upcoming Tournaments | S | Mostly tournament listing, details, and status display. |
| 6 | Enter Bot into Tournament | M | Needs deadline rules, bot-version selection, and tournament entry state. |
| 7 | Place and Cancel Limit Orders | L | Core trading API, order lifecycle, events, and validations. |
| 8 | Match Orders Fairly | XL | Core matching engine with price-time priority, fills, and audit records. |
| 9 | Read Market and Account State | L | Requires consistent state model across orders, positions, margin, and PnL. |
| 10 | Trade Spot and Futures | XL | Adds market types, futures accounting, and PnL complexity. |
| 11 | Trade Across Two Venues | L | Adds venue-specific books, fees, latency, and tracking. |
| 12 | Apply Trading Frictions | L | Latency, fees, and spreads touch execution, scoring, and replay. |
| 13 | Enforce Margin and Liquidation | XL | Risk engine, order blocking, liquidation, and account state updates. |
| 14 | Run Fixed Market Regimes | L | Market behavior simulation and repeatable regime configuration. |
| 15 | Provide Built-In Liquidity Bots | L | Requires baseline agents that create usable market depth. |
| 16 | Publish Risk-Adjusted Leaderboard | M | Results, scoring, ranking, and history are clear but depend on engine outputs. |
| 17 | Review Tournament Replay | L | Requires event capture, timeline reconstruction, filtering, and UI. |
| 18 | View Bot Diagnostics | M | Aggregates existing tournament data into useful bot-level summaries. |

## MVP Delivery Plan

### Phase 0: Product Decisions and Configurable Rules

**Goal:** Define the first configurable tournament rules before heavy build work.

**Stories:** Supports all stories, especially 8, 10, 11, 12, 13, 14, and 16.

**Decisions:**
1. PnL is computed directly from each user bot’s trades.
2. Delta exposure is penalized by forcing position liquidation across the spread, plus a configurable liquidation fee such as 1% or 2%.
3. Tournament duration, entry deadline rules, and minimum participant rules are configurable.
4. The first market set is configurable, starting with one spot asset, one future, and two venues.
5. The first regime set is configurable, including which regimes are visible before a tournament.
6. Bot safety for MVP is treated as an API gateway concern: bots send requests to the platform API, and the platform validates, records, and forwards valid actions to the order book.

**Deliverables:**
1. Config schema for scoring, liquidation penalty, fees, spread, latency, markets, venues, and regimes.
2. First default tournament configuration.
3. API gateway rules for bot requests.
4. Event log requirements for all bot requests, validation results, and accepted order-book actions.

**Exit Criteria:**
1. Team can run a tournament from one config file or admin setup.
2. PnL, delta liquidation penalty, fees, spread, latency, markets, venues, and regimes can be changed without code changes.
3. Bot requests are validated and logged before they reach the order book.

### Phase 1: Builder Onboarding and Bot Lifecycle

**Goal:** Let a builder create, validate, submit, and version a Python bot.

**Stories:**
- 1. Create First Python Bot — S
- 2. Validate Bot Code — M
- 3. Submit Versioned Bot — M
- 4. Route Bot Requests Through API Gateway — L

**Deliverables:**
1. Python starter kit and example bot.
2. Bot validation flow.
3. Bot submission and version storage.
4. Bot API gateway with request validation and event logging.

**Exit Criteria:**
1. A user can create a simple bot locally.
2. A user can validate and submit the bot.
3. Bot requests are validated before they reach the order book.
4. Invalid bot requests do not crash the platform or tournament.

### Phase 2: Core Exchange Simulation

**Goal:** Build the smallest credible trading loop.

**Stories:**
- 7. Place and Cancel Limit Orders — L
- 8. Match Orders Fairly — XL
- 9. Read Market and Account State — L
- 12. Apply Trading Frictions — L, basic fees first, latency second

**Deliverables:**
1. Limit order API.
2. Price-time matching engine.
3. Fill, cancel, reject, and order-state events.
4. Account and market-state API.
5. Basic fee and spread handling.
6. Initial latency model.

**Exit Criteria:**
1. Two bots can trade against each other in one spot market.
2. Orders match deterministically by price-time priority.
3. Bots receive correct state and order events.
4. Fees and basic latency affect execution.

### Phase 3: Scheduled Tournament Loop

**Goal:** Turn the simulator into a repeatable competition.

**Stories:**
- 5. View Upcoming Tournaments — S
- 6. Enter Bot into Tournament — M
- 15. Provide Built-In Liquidity Bots — L, simple baseline version
- 16. Publish Risk-Adjusted Leaderboard — M, first scoring version

**Deliverables:**
1. Tournament listing and detail view.
2. Bot version entry flow.
3. Scheduled tournament runner.
4. Built-in liquidity bot v1.
5. Leaderboard with raw PnL, delta exposure, adjusted score, and status.

**Exit Criteria:**
1. Users can enter bot versions before a deadline.
2. A scheduled tournament can start with user bots and built-in liquidity bots.
3. The tournament produces saved results.
4. A leaderboard is published after completion.

### Phase 4: Strategic Market Depth

**Goal:** Add the features that make winning meaningful for serious quant builders.

**Stories:**
- 10. Trade Spot and Futures — XL
- 11. Trade Across Two Venues — L
- 13. Enforce Margin and Liquidation — XL
- 14. Run Fixed Market Regimes — L
- 15. Provide Built-In Liquidity Bots — L, regime-aware version

**Deliverables:**
1. Futures market support.
2. Dual-venue support.
3. Margin checks and liquidation flow.
4. Fixed regimes: sideways, trending, high volatility, liquidity shock, black swan.
5. Liquidity bots that react to regimes.

**Exit Criteria:**
1. Bots can trade spot and futures in the same tournament.
2. Bots can trade the same asset across two venues.
3. Risky bots can be blocked or liquidated by margin rules.
4. Tournaments can run through predefined regimes.
5. Built-in liquidity bots maintain minimum tradability across normal and stressed conditions.

### Phase 5: Post-Tournament Learning Loop

**Goal:** Help builders understand results and improve bots.

**Stories:**
- 17. Review Tournament Replay — L
- 18. View Bot Diagnostics — M
- 16. Publish Risk-Adjusted Leaderboard — M, polish and history

**Deliverables:**
1. Replay event capture and timeline view.
2. Bot-level order, fill, position, PnL, and event replay.
3. Bot diagnostics summary.
4. Tournament history links from leaderboard to replay and diagnostics.

**Exit Criteria:**
1. A user can inspect their bot after a tournament.
2. A user can see major losses, rejected orders, liquidation events, and fees.
3. Replay and diagnostics are linked from leaderboard results.
4. Tournament history remains available after completion.

### Suggested MVP Cut Line

For the first private beta, ship through **Phase 3** with simplified versions of the API gateway, latency, and liquidity bots. This validates whether builders want to submit bots and compete.

For the full MVP promise in the problem statement, ship through **Phase 5**. This validates credible market behavior, strategic depth, ranking motivation, and the improvement loop.

### Delivery Risks

1. **API gateway risk:** If request validation and event logging are weak, invalid bot actions can corrupt tournament results.
2. **Simulation credibility risk:** If matching, fees, latency, and margin feel fake, serious builders may not trust results.
3. **Liquidity risk:** Bad built-in liquidity bots can make tournaments boring or impossible to trade.
4. **Scoring risk:** A weak liquidation penalty or adjusted PnL setup may reward unwanted behavior.
5. **Replay data risk:** If events are not captured early, replay and diagnostics become hard to add later.

## 4 Hours per Week Milestone Plan

Assumption: one person works **4 hours per week** starting the week of **2026-07-04**.

Shirt-size effort mapping:
- **XS:** 2 hours
- **S:** 4 hours
- **M:** 8 hours
- **L:** 16 hours
- **XL:** 32 hours

### Estimated Timeline

| Milestone | Scope | Effort | Duration at 4h/week | Target Date |
|---|---|---:|---:|---|
| Phase 0 Complete | Configurable rules, scoring, liquidation penalty, API gateway rules, first tournament config | 8h | 2 weeks | 2026-07-18 |
| Phase 1 Complete | Starter kit, validation, submission/versioning, API gateway v1 | 36h | 9 weeks | 2026-09-19 |
| Phase 2 Complete | Limit orders, matching engine, market/account state, basic frictions | 80h | 20 weeks | 2027-02-06 |
| Phase 3 Complete / Private Beta | Tournament listing, entry, scheduled runner, liquidity bot v1, leaderboard v1 | 36h | 9 weeks | 2027-04-10 |
| Phase 4 Complete | Futures, dual venues, margin/liquidation, regimes, regime-aware liquidity bots | 104h | 26 weeks | 2027-10-09 |
| Phase 5 Complete / Full MVP | Replay, diagnostics, leaderboard polish, tournament history | 40h | 10 weeks | 2027-12-18 |

### Key Milestones

1. **2026-07-18:** MVP rules and configuration model agreed.
2. **2026-09-19:** A builder can create, validate, submit, and version a bot.
3. **2027-02-06:** Two bots can trade through the core exchange simulation.
4. **2027-04-10:** Private beta is ready with scheduled tournaments and leaderboard.
5. **2027-10-09:** Full strategic market depth is ready.
6. **2027-12-18:** Full MVP is ready with replay and diagnostics.

### Planning Note

At **4 hours per week**, this is roughly a **17.5-month full MVP**. The best early validation target is the **private beta on 2027-04-10**, because it tests the core loop: submit bot, run tournament, see ranking, improve bot.
