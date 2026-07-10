import asyncio
from pathlib import Path
from datetime import datetime, timezone

from bot_arena_exchange.application.api_gateway import ApiGateway
from bot_arena_exchange.application.bot_lifecycle import BotLifecycleService
from bot_arena_exchange.application.event_log import InMemoryEventLog
from bot_arena_exchange.application.tournaments import TournamentScheduler
from bot_arena_exchange.config.tournament_config import DEFAULT_TOURNAMENT_CONFIG, TournamentConfig, load_tournament_config
from bot_arena_exchange.domain.bots import SimpleMarketMaker
from bot_arena_exchange.domain.order_book import OrderBook, WashTradeError
from bot_arena_exchange.domain.scoring import score_account
from bot_arena_exchange.domain.tournament import TournamentManager

# ── Shock detection constants ──────────────────────────────────────────
# Volume Shock (Trigger A): order consumes > 50% of best-level resting qty
SHOCK_VOLUME_THRESHOLD = 0.50
# Liquidity Shock (Trigger B): best-level volume drops > 50% from cancellation
SHOCK_LIQUIDITY_THRESHOLD = 0.50
# Hawkes jump scaling: alpha(V) = K_ALPHA * V
K_ALPHA = 0.004
# Hawkes decay scaling: beta(V) = K_BETA / (1 + V / V_REF)
K_BETA = 2.5
V_REF = 100


def _shock_severity(fraction: float) -> str:
    """Map the fraction of liquidity consumed to a severity enum."""
    if fraction >= 0.90:
        return "SEVERE"
    if fraction >= 0.75:
        return "MODERATE"
    return "MINOR"


def _shock_params(volume: int) -> tuple:
    """Compute Hawkes alpha and beta from a shock volume V."""
    alpha = K_ALPHA * volume
    beta = K_BETA / (1.0 + volume / V_REF)
    if alpha >= beta:
        # Ensure alpha < beta for stability of Hawkes process
        alpha = beta * 0.85
    return alpha, beta


class ExchangeService:
    def __init__(self, config=None, book=None, manager=None, event_log=None, bot_lifecycle=None, tournament_status="RUNNING"):
        self.config = config or DEFAULT_TOURNAMENT_CONFIG
        # Per-(symbol, venue) order books for true venue isolation
        self.books: dict = {}
        if book is not None:
            # Legacy single-book constructor: map it to default (symbol, venue)
            default_symbol = self.config.markets[0].symbol
            default_venue = self.config.venues[0].venue_id
            self.books[(default_symbol, default_venue)] = book
        self._book = book  # Keep backward compat reference for tests that access .book directly
        system_ids = set(getattr(self.config, 'system_account_ids', []))
        self.manager = manager or TournamentManager(position_limit=100, system_account_ids=system_ids)
        self.event_log = event_log or InMemoryEventLog()
        self.bot_lifecycle = bot_lifecycle or BotLifecycleService()
        self.gateway = ApiGateway(self.config, self.manager)
        self.scheduler = TournamentScheduler(self.config)
        self.tournament_status = tournament_status
        # Global lock for serializing mutations to OrderBook + TournamentManager
        self._lock = asyncio.Lock()
        # Holds the currently active asyncio.Task for any background liquidity bots
        self._liquidity_tasks: list = []
        # Shock cooldown: last time _emit_shock was called (event loop monotonic time)
        self._last_shock_emitted_time: float = 0.0

    # ------------------------------------------------------------------
    # Reset state — wipes everything for a fresh simulator launch
    # ------------------------------------------------------------------
    def reset_state(self):
        """Tear down and recreate all mutable state for a clean launch.

        - Clears all order books (resting orders wiped)
        - Recreates TournamentManager (all positions, PnL, costs, fees reset)
        - Recreates event log (stale events cleared)
        - Resets tournament status to RUNNING
        """
        self.books.clear()
        system_ids = set(getattr(self.config, 'system_account_ids', []))
        self.manager = TournamentManager(position_limit=100, system_account_ids=system_ids)
        self.event_log = InMemoryEventLog()
        self.gateway = ApiGateway(self.config, self.manager)
        self.tournament_status = "RUNNING"
        self._book = None
        print("[ExchangeService] State fully reset for fresh launch")

    # ------------------------------------------------------------------
    # Order book routing
    # ------------------------------------------------------------------
    def _resolve_default_key(self):
        """Return the default (symbol, venue) key from config."""
        return (self.config.markets[0].symbol, self.config.venues[0].venue_id)

    def _get_book(self, symbol=None, venue=None):
        """Get or create the OrderBook for a given (symbol, venue) pair."""
        symbol = symbol or self.config.markets[0].symbol
        venue = venue or self.config.venues[0].venue_id
        key = (symbol, venue)
        if key not in self.books:
            self.books[key] = OrderBook(self_matching_banned=True, venue=venue)
        return self.books[key]

    @property
    def book(self):
        """Backward-compat: returns the default (symbol, venue) OrderBook."""
        return self._get_book()

    @classmethod
    def from_config_file(cls, path):
        return cls(config=load_tournament_config(Path(path)))

    @classmethod
    def with_default_liquidity(cls):
        service = cls()
        return service

    # ------------------------------------------------------------------
    # Synchronous helpers — only called while self._lock is held
    # ------------------------------------------------------------------
    def _seed_default_liquidity_sync(self):
        """Seed the order book with liquidity from a SimpleMarketMaker (sync)."""
        market = self.config.markets[0]
        venue = self.config.venues[0]
        bot = SimpleMarketMaker(
            trader_id="Bot_MM",
            symbol=market.symbol,
            venue=venue.venue_id,
            edge=max(1, market.initial_reference_price * venue.spread_bps // 20000),
            size=10,
        )
        book = self._get_book(bot.symbol, bot.venue)
        for quote in bot.generate_quotes(market.initial_reference_price):
            book.place_order(
                quote["side"],
                quote["price"],
                quote["quantity"],
                bot.trader_id,
                bot.symbol,
                bot.venue,
            )

    # ------------------------------------------------------------------
    # Configuration & bot lifecycle (remain mostly synchronous / thin wrappers)
    # ------------------------------------------------------------------
    def get_tournament_config(self):
        return self.config

    def get_starter_kit(self):
        return self.bot_lifecycle.get_starter_kit()

    def validate_bot(self, files):
        return self.bot_lifecycle.validate_bot(files)

    def submit_bot(self, owner_id, bot_name, files):
        return self.bot_lifecycle.submit_bot(owner_id, bot_name, files)

    def list_bot_versions(self, owner_id, bot_name=None):
        return self.bot_lifecycle.list_versions(owner_id, bot_name)

    def get_bot_version(self, owner_id, bot_name, version):
        return self.bot_lifecycle.get_version(owner_id, bot_name, version)

    def list_tournaments(self):
        return self.scheduler.list_tournaments()

    def get_tournament(self, tournament_id):
        return self.scheduler.get_tournament(tournament_id)

    def enter_tournament(self, tournament_id, owner_id, bot_name, version):
        bot_version = self.bot_lifecycle.repository.get_version(owner_id, bot_name, version)
        if bot_version is None:
            return {"status": "REJECTED", "reason": "bot version not found"}
        return self.scheduler.enter_bot(tournament_id, owner_id, bot_name, version)

    # ------------------------------------------------------------------
    # Read-only state queries (safe without lock)
    # ------------------------------------------------------------------
    def get_market_snapshot(self, symbol=None, venue=None):
        return self._get_book(symbol, venue).get_snapshot()

    def get_market_state(self, symbol=None, venue=None):
        book = self._get_book(symbol, venue)
        snapshot = book.get_snapshot()
        symbol = symbol or self.config.markets[0].symbol
        venue = venue or self.config.venues[0].venue_id
        return {
            "symbol": symbol,
            "venue": venue,
            "best_bid": book.best_bid(),
            "best_ask": book.best_ask(),
            "snapshot": snapshot,
            "recent_trades": book.get_trades(),
        }

    def get_order_state(self, order_id):
        # Search across all books since order_id is globally unique
        for book in self.books.values():
            order = book.orders.get(order_id)
            if order is not None:
                return {
                    "order_id": order.order_id,
                    "side": order.side,
                    "price": order.price,
                    "quantity": order.quantity,
                    "remaining": order.remaining,
                    "trader_id": order.trader_id,
                    "symbol": order.symbol,
                    "venue": order.venue,
                    "tif": order.tif,
                    "status": order.status,
                    "timestamp": order.timestamp,
                }
        return None

    def get_account_state(self, trader_id):
        account = self.manager.get_account(trader_id)
        return {
            "trader_id": account.trader_id,
            "positions": dict(account.positions),
            "avg_costs": dict(account.avg_costs),
            "realized_pnl": account.realized_pnl,
            "fees_paid": account.fees_paid,
            "status": account.status,
        }

    def get_event_log(self):
        return self.event_log.as_dicts()

    def get_traders_status(self):
        return {
            trader_id: {
                "trader_id": account.trader_id,
                "positions": dict(account.positions),
                "avg_costs": dict(account.avg_costs),
                "realized_pnl": account.realized_pnl,
                "fees_paid": account.fees_paid,
                "status": account.status,
            }
            for trader_id, account in self.manager.accounts.items()
        }

    # ------------------------------------------------------------------
    # Handle wash trade violations — system accounts get a warning,
    # user accounts get disconnected.
    # ------------------------------------------------------------------
    def _handle_wash_trade(self, trader_id: str) -> dict:
        """Handle a WashTradeError: disconnect user accounts, warn for system."""
        account = self.manager.get_account(trader_id)
        if account.is_system:
            print(f"[WASH-TRADE WARNING] System account '{trader_id}' attempted self-match — order rejected")
            return None
        print(f"[WASH-TRADE BAN] User account '{trader_id}' self-matched — disconnecting")
        return self.manager.disconnect_account(trader_id, reason="Wash trade violation (self-matching)")

    # ------------------------------------------------------------------
    # Shock detection & Hawkes notification
    # ------------------------------------------------------------------
    def _engine_ref(self):
        """Late-bound reference to LiquidityEngine, set by fastapi_app.py."""
        return getattr(self, "_engine", None)

    def _set_engine(self, engine):
        self._engine = engine

    def _notify_shock(self, shock_payload: dict):
        """Notify the LiquidityEngine of a shock event so it can wake bots."""
        engine = self._engine_ref()
        if engine is not None:
            alpha = shock_payload["alpha"]
            beta = shock_payload["beta"]
            engine.publish_shock(alpha, beta)

    def _emit_shock(self, book, symbol: str, venue: str, shock_type: str,
                    side: str, volume_affected: int, resting_before: int):
        """Record a MARKET_SHOCK event and notify the liquidity engine."""
        # ── Shock cooldown: enforce a 100ms minimum interval ──────────
        now = asyncio.get_event_loop().time()
        if now - self._last_shock_emitted_time < 0.1:
            return
        self._last_shock_emitted_time = now

        if resting_before <= 0:
            return
        fraction = min(volume_affected / resting_before, 1.0)
        alpha, beta = _shock_params(volume_affected)
        severity = _shock_severity(fraction)

        shock_payload = {
            "shock_type": shock_type,
            "symbol": symbol,
            "venue": venue,
            "side": side,
            "volume_affected": volume_affected,
            "resting_volume_before": resting_before,
            "fraction": round(fraction, 4),
            "severity": severity,
            "alpha": round(alpha, 4),
            "beta": round(beta, 4),
        }

        self.event_log.record(
            event_type="MARKET_SHOCK",
            bot_id="SYSTEM",
            tournament_id=self.config.tournament_id,
            payload=shock_payload,
            validation_result="ACCEPTED",
            final_action="SHOCK_DETECTED",
        )

        self._notify_shock(shock_payload)

    def _check_volume_shock(self, book, symbol: str, venue: str,
                            side: str, best_vol_before: int):
        """Trigger A: check if best-level volume was consumed > threshold."""
        if side == "BUY":
            best_vol_after = book.best_ask_volume()
        else:
            best_vol_after = book.best_bid_volume()

        consumed = best_vol_before - best_vol_after
        if consumed > 0 and best_vol_before > 0:
            fraction = consumed / best_vol_before
            if fraction > SHOCK_VOLUME_THRESHOLD:
                self._emit_shock(book, symbol, venue, "VOLUME_SHOCK",
                                 side, consumed, best_vol_before)

    def _check_liquidity_shock(self, book, symbol: str, venue: str,
                               side: str, best_vol_before: int):
        """Trigger B: check if best-level volume evaporated > threshold."""
        if side == "BUY":
            best_vol_after = book.best_bid_volume()
        else:
            best_vol_after = book.best_ask_volume()

        evaporated = best_vol_before - best_vol_after
        if evaporated > 0 and best_vol_before > 0:
            fraction = evaporated / best_vol_before
            if fraction > SHOCK_LIQUIDITY_THRESHOLD:
                self._emit_shock(book, symbol, venue, "LIQUIDITY_SHOCK",
                                 side, evaporated, best_vol_before)

    # ------------------------------------------------------------------
    # Core order execution (async, lock-protected)
    # ------------------------------------------------------------------
    async def place_order(self, side, price, quantity, trader_id, symbol=None, venue=None):
        symbol = symbol or self.config.markets[0].symbol
        venue = venue or self.config.venues[0].venue_id
        payload = {
            "side": side,
            "price": price,
            "quantity": quantity,
            "trader_id": trader_id,
            "symbol": symbol,
            "venue": venue,
        }

        # Validate outside the lock (ApiGateway does not mutate global state
        # beyond reading account positions; we accept a brief stale read
        # because the lock-protected _execute_order will re-check via
        # TournamentManager.process_trades which handles the position limit).
        validation = self.gateway.validate_order_request(payload, self.tournament_status)
        if not validation.accepted:
            self.event_log.record(
                event_type="ORDER_REJECTED",
                bot_id=trader_id,
                tournament_id=self.config.tournament_id,
                payload=payload,
                validation_result="REJECTED",
                final_action=None,
                reason=validation.reason,
            )
            return {
                "status": "REJECTED",
                "reason": validation.reason,
                "trades_executed": 0,
                "disconnections_triggered": [],
            }

        # Execute immediately under lock — no latency queue
        return await self._execute_order(payload)

    async def _execute_order(self, payload):
        """Execute an order synchronously under the global lock.

        Returns a result dict with status, order_id, trades, and disconnections.
        """
        async with self._lock:
            book = self._get_book(payload["symbol"], payload["venue"])
            # Grab book lock first, then manager lock, to avoid deadlocks.
            disconnections = []

            try:
                async with book._lock:
                    # ── Shock detection: capture best-level volume before matching ──
                    if payload["side"] == "BUY":
                        best_vol_before = book.best_ask_volume()
                    else:
                        best_vol_before = book.best_bid_volume()

                    book.trades.clear()
                    async with self.manager._lock:
                        try:
                            order_id = book.place_order(
                                side=payload["side"],
                                price=payload["price"],
                                quantity=payload["quantity"],
                                trader_id=payload["trader_id"],
                                symbol=payload["symbol"],
                                venue=payload["venue"],
                            )
                        except WashTradeError as e:
                            # Self-match blocked — handle per account type
                            disconnection = self._handle_wash_trade(e.trader_id)
                            if disconnection:
                                disconnections.append(disconnection)

                            self.event_log.record(
                                event_type="ORDER_REJECTED",
                                bot_id=payload["trader_id"],
                                tournament_id=self.config.tournament_id,
                                payload=payload,
                                validation_result="REJECTED",
                                final_action=None,
                                reason=f"Wash trade violation (self-matching)",
                            )
                            return {
                                "status": "REJECTED",
                                "reason": "Wash trade violation (self-matching)",
                                "trades_executed": 0,
                                "disconnections_triggered": disconnections,
                            }

                        trades = book.get_trades()
                        fee_bps_by_venue = {venue.venue_id: venue.fee_bps for venue in self.config.venues}
                        position_disconnections = self.manager.process_trades(trades, fee_bps_by_venue)
                        disconnections.extend(position_disconnections)

                order_state = self.get_order_state(order_id)

                # Record events (outside book lock but inside exchange lock)
                self.event_log.record(
                    event_type="ORDER_ACCEPTED",
                    bot_id=payload["trader_id"],
                    tournament_id=self.config.tournament_id,
                    payload={**payload, "order_id": order_id, "order_state": order_state},
                    validation_result="ACCEPTED",
                    final_action="PLACE_ORDER",
                )
                for trade in trades:
                    self.event_log.record(
                        event_type="FILL",
                        bot_id=str(trade["buyer_id"]),
                        tournament_id=self.config.tournament_id,
                        payload=trade,
                        validation_result="ACCEPTED",
                        final_action="EXECUTE_TRADE",
                    )
                for disconnection in disconnections:
                    self.event_log.record(
                        event_type="DISCONNECTION",
                        bot_id=str(disconnection["trader_id"]),
                        tournament_id=self.config.tournament_id,
                        payload=disconnection,
                        validation_result="ACCEPTED",
                        final_action="DISCONNECT_TRADER",
                    )

                # Emit ORDERBOOK_UPDATE event so WebSocket subscribers get fresh state
                self.event_log.record(
                    event_type="ORDERBOOK_UPDATE",
                    bot_id="SYSTEM",
                    tournament_id=self.config.tournament_id,
                    payload=self.get_market_state(payload["symbol"], payload["venue"]),
                    validation_result="ACCEPTED",
                    final_action="STATE_BROADCAST",
                )

                # ── Trigger A: Volume Shock detection ──
                self._check_volume_shock(book, payload["symbol"], payload["venue"],
                                         payload["side"], best_vol_before)

            except WashTradeError as e:
                # Catch wash trade on the book-level lock path (unlikely but safe)
                disconnection = self._handle_wash_trade(e.trader_id)
                if disconnection:
                    disconnections.append(disconnection)

                self.event_log.record(
                    event_type="ORDER_REJECTED",
                    bot_id=payload["trader_id"],
                    tournament_id=self.config.tournament_id,
                    payload=payload,
                    validation_result="REJECTED",
                    final_action=None,
                    reason=f"Wash trade violation (self-matching)",
                )
                return {
                    "status": "REJECTED",
                    "reason": "Wash trade violation (self-matching)",
                    "trades_executed": 0,
                    "disconnections_triggered": disconnections,
                }

        return {
            "status": "PROCESSED",
            "order_id": order_id,
            "order_state": order_state,
            "trades": trades,
            "trades_executed": len(trades),
            "disconnections_triggered": disconnections,
        }

    async def cancel_order(self, order_id, trader_id):
        payload = {"order_id": order_id, "trader_id": trader_id}

        async with self._lock:
            # Find which book the order lives in — must match both order_id AND trader_id.
            # If order_id exists in one book but belongs to a different trader, continue
            # searching other books (handles ID collisions across venues).
            order = None
            owner_book = None
            found_but_wrong_trader = False
            for book in self.books.values():
                candidate = book.orders.get(order_id)
                if candidate is not None:
                    if candidate.trader_id == trader_id:
                        order = candidate
                        owner_book = book
                        break
                    else:
                        found_but_wrong_trader = True

            if order is None:
                reason = "order does not belong to trader" if found_but_wrong_trader else "order not found"
            else:
                # ── Trigger B: Liquidity Shock — capture best-level before cancel ──
                if order.side == "BUY":
                    best_vol_before = owner_book.best_bid_volume()
                else:
                    best_vol_before = owner_book.best_ask_volume()

                async with owner_book._lock:
                    if not owner_book.cancel_order(order_id):
                        reason = "order cannot be cancelled"
                    else:
                        # ── Trigger B: Liquidity Shock detection ──
                        self._check_liquidity_shock(owner_book, order.symbol, order.venue,
                                                    order.side, best_vol_before)
                        order_state = self.get_order_state(order_id)
                        self.event_log.record(
                            event_type="CANCEL",
                            bot_id=trader_id,
                            tournament_id=self.config.tournament_id,
                            payload={**payload, "order_state": order_state},
                            validation_result="ACCEPTED",
                            final_action="CANCEL_ORDER",
                        )
                        # Emit ORDERBOOK_UPDATE after cancellation
                        self.event_log.record(
                            event_type="ORDERBOOK_UPDATE",
                            bot_id="SYSTEM",
                            tournament_id=self.config.tournament_id,
                            payload=self.get_market_state(order.symbol, order.venue),
                            validation_result="ACCEPTED",
                            final_action="STATE_BROADCAST",
                        )
                        return {"status": "CANCELLED", "order_id": order_id, "order_state": order_state}

            self.event_log.record(
                event_type="CANCEL_REJECTED",
                bot_id=trader_id,
                tournament_id=self.config.tournament_id,
                payload=payload,
                validation_result="REJECTED",
                final_action=None,
                reason=reason,
            )
            return {"status": "REJECTED", "reason": reason}

    # ------------------------------------------------------------------
    # Scoring & leaderboard
    # ------------------------------------------------------------------
    def score_traders(self):
        reference_prices = {market.symbol: market.initial_reference_price for market in self.config.markets}
        spread_bps_by_symbol = {}
        for market in self.config.markets:
            spreads = [venue.spread_bps for venue in self.config.venues if market.symbol in venue.supported_symbols]
            spread_bps_by_symbol[market.symbol] = max(spreads) if spreads else 0
        return [
            score_account(account, self.config.scoring, reference_prices, spread_bps_by_symbol).__dict__
            for account in self.manager.accounts.values()
            if not account.is_system
        ]

    def get_leaderboard(self):
        rows = []
        scores = sorted(self.score_traders(), key=lambda score: score["adjusted_score"], reverse=True)
        for index, score in enumerate(scores, start=1):
            rows.append({"rank": index, **score})
        return rows

    # ------------------------------------------------------------------
    # Tournament runner (time-based, not tick-based)
    # ------------------------------------------------------------------
    async def run_scheduled_tournament(self, tournament_id=None):
        tournament_id = tournament_id or self.config.tournament_id
        tournament = self.scheduler.get(tournament_id)
        if tournament is None:
            return {"status": "REJECTED", "reason": "tournament not found"}

        self.scheduler.mark_running(tournament_id)
        self.tournament_status = "RUNNING"

        # Seed initial liquidity (DISABLED — see fastapi_app.py for rationale)
        # async with self._lock:
        #     self._seed_default_liquidity_sync()

        # Compile and instantiate user bots
        active_bots = []
        for entry in tournament.entries.values():
            trader_id = f"{entry.owner_id}:{entry.bot_name}:v{entry.version}"
            try:
                bot_version = self.bot_lifecycle.get_version(entry.owner_id, entry.bot_name, entry.version)
                files = bot_version.files if hasattr(bot_version, "files") else bot_version["files"]
                code_str = files.get("bot.py", files.get("main.py", ""))
                local_scope = {}
                try:
                    exec(code_str, local_scope, local_scope)
                    bot_instance = local_scope["create_bot"]()
                    active_bots.append({"id": trader_id, "instance": bot_instance})
                except Exception as e:
                    print(f"Error loading bot {trader_id}: {e}")
            except Exception as e:
                print(f"Error loading bot {trader_id}: {e}")

        # Duration from config: use duration_seconds if explicitly set (> 0),
        # otherwise fall back to duration_ticks assuming 10 ticks/sec.
        duration_seconds = getattr(self.config.rules, "duration_seconds", 0) or 0
        if duration_seconds <= 0:
            duration_seconds = self.config.rules.duration_ticks / 10

        start_time = datetime.now(timezone.utc)
        tick_interval = 0.1  # 100ms per tick = 10 ticks/sec

        class ApiProxy:
            def __init__(self, service, trader_id):
                self.s = service
                self.t_id = trader_id

            def get_order_book(self, symbol, venue):
                state = self.s.get_market_state(symbol=symbol, venue=venue)
                return {
                    "best_bid": state.get("best_bid"),
                    "best_ask": state.get("best_ask"),
                    "snapshot": state.get("snapshot"),
                }

            def get_account(self):
                return self.s.manager.get_account(self.t_id)

            def get_position(self, symbol):
                account = self.s.manager.get_account(self.t_id)
                return account.positions.get(symbol, 0)

            def place_order(self, side, price, quantity, symbol, venue):
                res = self.s._place_order_sync(side, price, quantity, self.t_id, symbol, venue)
                if "order_id" in res:
                    return res["order_id"]
                return None

            def cancel_order(self, order_id):
                if isinstance(order_id, dict):
                    print("[API WARNING] cancel_order received a dict instead of a string ID. Auto-extracting. Fix your bot code.")
                    order_id = order_id.get("order_id", "")
                res = self.s._cancel_order_sync(order_id, self.t_id)
                if isinstance(res, dict) and res.get("status") == "REJECTED":
                    reason = res.get("reason", "unknown")
                    # "order not found" / "order cannot be cancelled" = already
                    # filled, cancelled, or otherwise harmless — no action needed.
                    # "order does not belong to trader" = programming error → raise.
                    if reason not in ("order not found", "order cannot be cancelled"):
                        raise RuntimeError(f"Cancel rejected: {reason}")
                return res

        # Synchronous wrappers for ApiProxy (used within the tournament loop
        # which runs inside an already-held lock context or single-threaded)
        async def _run_tournament_loop():
            elapsed = 0.0
            while elapsed < duration_seconds:
                await asyncio.sleep(tick_interval)
                elapsed = (datetime.now(timezone.utc) - start_time).total_seconds()

                async with self._lock:
                    for bot_data in active_bots:
                        trader_id = bot_data["id"]
                        account = self.manager.get_account(trader_id)
                        if account.status == "DISCONNECTED":
                            continue
                        try:
                            bot_data["instance"].on_tick(ApiProxy(self, trader_id))
                        except Exception as e:
                            print(f"Error executing bot {trader_id}: {e}")

        await _run_tournament_loop()

        leaderboard = self.get_leaderboard()
        result = self.scheduler.publish_results(tournament_id, leaderboard)
        self.tournament_status = "COMPLETED"

        return {"status": "COMPLETED", "tournament": result, "leaderboard": leaderboard}

    # Synchronous place_order for internal use (ApiProxy in tournament loop)
    def _place_order_sync(self, side, price, quantity, trader_id, symbol=None, venue=None):
        """Synchronous version for use inside already-locked contexts."""
        symbol = symbol or self.config.markets[0].symbol
        venue = venue or self.config.venues[0].venue_id
        payload = {
            "side": side,
            "price": price,
            "quantity": quantity,
            "trader_id": trader_id,
            "symbol": symbol,
            "venue": venue,
        }
        validation = self.gateway.validate_order_request(payload, self.tournament_status)
        if not validation.accepted:
            return {"status": "REJECTED", "reason": validation.reason}
        return self._execute_order_sync(payload)

    def _execute_order_sync(self, payload):
        """Executes an order synchronously. Caller must hold self._lock."""
        book = self._get_book(payload["symbol"], payload["venue"])
        # ── Shock detection: capture best-level volume before matching ──
        if payload["side"] == "BUY":
            best_vol_before = book.best_ask_volume()
        else:
            best_vol_before = book.best_bid_volume()
        book.trades.clear()

        try:
            order_id = book.place_order(
                side=payload["side"],
                price=payload["price"],
                quantity=payload["quantity"],
                trader_id=payload["trader_id"],
                symbol=payload["symbol"],
                venue=payload["venue"],
            )
        except WashTradeError as e:
            # Self-match blocked — handle per account type
            disconnection = self._handle_wash_trade(e.trader_id)
            disconnections = [disconnection] if disconnection else []

            self.event_log.record(
                event_type="ORDER_REJECTED",
                bot_id=payload["trader_id"],
                tournament_id=self.config.tournament_id,
                payload=payload,
                validation_result="REJECTED",
                final_action=None,
                reason=f"Wash trade violation (self-matching)",
            )
            return {
                "status": "REJECTED",
                "reason": "Wash trade violation (self-matching)",
                "trades_executed": 0,
                "disconnections_triggered": disconnections,
            }

        trades = book.get_trades()
        fee_bps_by_venue = {venue.venue_id: venue.fee_bps for venue in self.config.venues}
        disconnections = self.manager.process_trades(trades, fee_bps_by_venue)
        order_state = self.get_order_state(order_id)
        self.event_log.record(
            event_type="ORDER_ACCEPTED",
            bot_id=payload["trader_id"],
            tournament_id=self.config.tournament_id,
            payload={**payload, "order_id": order_id, "order_state": order_state},
            validation_result="ACCEPTED",
            final_action="PLACE_ORDER",
        )
        for trade in trades:
            self.event_log.record(
                event_type="FILL",
                bot_id=str(trade["buyer_id"]),
                tournament_id=self.config.tournament_id,
                payload=trade,
                validation_result="ACCEPTED",
                final_action="EXECUTE_TRADE",
            )
        for disconnection in disconnections:
            self.event_log.record(
                event_type="DISCONNECTION",
                bot_id=str(disconnection["trader_id"]),
                tournament_id=self.config.tournament_id,
                payload=disconnection,
                validation_result="ACCEPTED",
                final_action="DISCONNECT_TRADER",
            )
        self.event_log.record(
            event_type="ORDERBOOK_UPDATE",
            bot_id="SYSTEM",
            tournament_id=self.config.tournament_id,
            payload=self.get_market_state(payload["symbol"], payload["venue"]),
            validation_result="ACCEPTED",
            final_action="STATE_BROADCAST",
        )
        # ── Trigger A: Volume Shock detection (sync path) ──
        self._check_volume_shock(book, payload["symbol"], payload["venue"],
                                 payload["side"], best_vol_before)
        return {
            "status": "PROCESSED",
            "order_id": order_id,
            "order_state": order_state,
            "trades": trades,
            "trades_executed": len(trades),
            "disconnections_triggered": disconnections,
        }

    def _cancel_order_sync(self, order_id, trader_id):
        """Synchronous version for use inside already-locked contexts."""
        payload = {"order_id": order_id, "trader_id": trader_id}

        # Find which book the order lives in — must match both order_id AND trader_id.
        # If order_id exists in one book but belongs to a different trader, continue
        # searching other books (handles ID collisions across venues).
        order = None
        owner_book = None
        found_but_wrong_trader = False
        for book in self.books.values():
            candidate = book.orders.get(order_id)
            if candidate is not None:
                if candidate.trader_id == trader_id:
                    order = candidate
                    owner_book = book
                    break
                else:
                    found_but_wrong_trader = True

        if order is None:
            reason = "order does not belong to trader" if found_but_wrong_trader else "order not found"
        else:
            # ── Trigger B: Liquidity Shock — capture best-level before cancel ──
            if order.side == "BUY":
                best_vol_before = owner_book.best_bid_volume()
            else:
                best_vol_before = owner_book.best_ask_volume()

            if not owner_book.cancel_order(order_id):
                reason = "order cannot be cancelled"
            else:
                # ── Trigger B: Liquidity Shock detection (sync path) ──
                self._check_liquidity_shock(owner_book, order.symbol, order.venue,
                                            order.side, best_vol_before)

                order_state = self.get_order_state(order_id)
                self.event_log.record(
                    event_type="CANCEL",
                    bot_id=trader_id,
                    tournament_id=self.config.tournament_id,
                    payload={**payload, "order_state": order_state},
                    validation_result="ACCEPTED",
                    final_action="CANCEL_ORDER",
                )
                self.event_log.record(
                    event_type="ORDERBOOK_UPDATE",
                    bot_id="SYSTEM",
                    tournament_id=self.config.tournament_id,
                    payload=self.get_market_state(order.symbol, order.venue),
                    validation_result="ACCEPTED",
                    final_action="STATE_BROADCAST",
                )
                return {"status": "CANCELLED", "order_id": order_id, "order_state": order_state}

        self.event_log.record(
            event_type="CANCEL_REJECTED",
            bot_id=trader_id,
            tournament_id=self.config.tournament_id,
            payload=payload,
            validation_result="REJECTED",
            final_action=None,
            reason=reason,
        )
        return {"status": "REJECTED", "reason": reason}

    # ------------------------------------------------------------------
    # Legacy tick methods removed. advance_tick() and latency_queue are gone.
    # Order execution is now instantaneous via async place_order().
    # ------------------------------------------------------------------