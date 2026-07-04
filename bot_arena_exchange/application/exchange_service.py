from pathlib import Path
from collections import deque

from bot_arena_exchange.application.api_gateway import ApiGateway
from bot_arena_exchange.application.bot_lifecycle import BotLifecycleService
from bot_arena_exchange.application.event_log import InMemoryEventLog
from bot_arena_exchange.application.tournaments import TournamentScheduler
from bot_arena_exchange.config.tournament_config import DEFAULT_TOURNAMENT_CONFIG, TournamentConfig, load_tournament_config
from bot_arena_exchange.domain.bots import SimpleMarketMaker
from bot_arena_exchange.domain.order_book import OrderBook
from bot_arena_exchange.domain.scoring import score_account
from bot_arena_exchange.domain.tournament import TournamentManager


class ExchangeService:
    def __init__(self, config=None, book=None, manager=None, event_log=None, bot_lifecycle=None, tournament_status="RUNNING"):
        self.config = config or DEFAULT_TOURNAMENT_CONFIG
        self.book = book or OrderBook()
        self.manager = manager or TournamentManager(position_limit=100)
        self.event_log = event_log or InMemoryEventLog()
        self.bot_lifecycle = bot_lifecycle or BotLifecycleService()
        self.gateway = ApiGateway(self.config, self.manager)
        self.scheduler = TournamentScheduler(self.config)
        self.tournament_status = tournament_status
        self.current_tick = 0
        self.latency_queue = deque()

    @classmethod
    def from_config_file(cls, path):
        return cls(config=load_tournament_config(Path(path)))

    @classmethod
    def with_default_liquidity(cls):
        service = cls()
        service.seed_default_liquidity()
        return service

    def seed_default_liquidity(self):
        market = self.config.markets[0]
        venue = self.config.venues[0]
        bot = SimpleMarketMaker(
            trader_id="Bot_MM",
            symbol=market.symbol,
            venue=venue.venue_id,
            edge=max(1, market.initial_reference_price * venue.spread_bps // 20000),
            size=10,
        )
        for quote in bot.generate_quotes(market.initial_reference_price):
            self.book.place_order(
                quote["side"],
                quote["price"],
                quote["quantity"],
                bot.trader_id,
                bot.symbol,
                bot.venue,
            )

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

    def get_market_snapshot(self):
        return self.book.get_snapshot()

    def get_market_state(self):
        snapshot = self.book.get_snapshot()
        return {
            "tick": self.current_tick,
            "best_bid": self.book.best_bid(),
            "best_ask": self.book.best_ask(),
            "snapshot": snapshot,
            "recent_trades": self.book.get_trades(),
            "pending_orders": len(self.latency_queue),
        }

    def get_order_state(self, order_id):
        order = self.book.orders.get(order_id)
        if order is None:
            return None
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

    def place_order(self, side, price, quantity, trader_id, symbol=None, venue=None):
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

        venue_config = self.config.venue_for(venue)
        if venue_config.latency_ticks > 0:
            due_tick = self.current_tick + venue_config.latency_ticks
            self.latency_queue.append((due_tick, payload))
            self.event_log.record(
                event_type="ORDER_ACCEPTED",
                bot_id=trader_id,
                tournament_id=self.config.tournament_id,
                payload=payload,
                validation_result="ACCEPTED",
                final_action="QUEUE_ORDER",
            )
            return {
                "status": "QUEUED",
                "due_tick": due_tick,
                "trades_executed": 0,
                "disconnections_triggered": [],
            }

        return self._execute_order(payload)

    def _execute_order(self, payload):
        self.book.trades.clear()
        order_id = self.book.place_order(
            side=payload["side"],
            price=payload["price"],
            quantity=payload["quantity"],
            trader_id=payload["trader_id"],
            symbol=payload["symbol"],
            venue=payload["venue"],
        )
        trades = self.book.get_trades()
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
        return {
            "status": "PROCESSED",
            "order_id": order_id,
            "order_state": order_state,
            "trades": trades,
            "trades_executed": len(trades),
            "disconnections_triggered": disconnections,
        }

    def cancel_order(self, order_id, trader_id):
        order = self.book.orders.get(order_id)
        payload = {"order_id": order_id, "trader_id": trader_id}
        if order is None:
            reason = "order not found"
        elif order.trader_id != trader_id:
            reason = "order does not belong to trader"
        elif not self.book.cancel_order(order_id):
            reason = "order cannot be cancelled"
        else:
            self.event_log.record(
                event_type="CANCEL",
                bot_id=trader_id,
                tournament_id=self.config.tournament_id,
                payload={**payload, "order_state": self.get_order_state(order_id)},
                validation_result="ACCEPTED",
                final_action="CANCEL_ORDER",
            )
            return {"status": "CANCELLED", "order_id": order_id, "order_state": self.get_order_state(order_id)}

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

    def advance_tick(self, ticks=1):
        results = []
        for _ in range(ticks):
            self.current_tick += 1
            ready = []
            while self.latency_queue and self.latency_queue[0][0] <= self.current_tick:
                ready.append(self.latency_queue.popleft()[1])
            for payload in ready:
                results.append(self._execute_order(payload))
        return results

    def score_traders(self):
        reference_prices = {market.symbol: market.initial_reference_price for market in self.config.markets}
        spread_bps_by_symbol = {}
        for market in self.config.markets:
            spreads = [venue.spread_bps for venue in self.config.venues if market.symbol in venue.supported_symbols]
            spread_bps_by_symbol[market.symbol] = max(spreads) if spreads else 0
        return [
            score_account(account, self.config.scoring, reference_prices, spread_bps_by_symbol).__dict__
            for account in self.manager.accounts.values()
        ]

    def get_leaderboard(self):
        rows = []
        scores = sorted(self.score_traders(), key=lambda score: score["adjusted_score"], reverse=True)
        for index, score in enumerate(scores, start=1):
            rows.append({"rank": index, **score})
        return rows

    def run_scheduled_tournament(self, tournament_id=None):
        tournament_id = tournament_id or self.config.tournament_id
        tournament = self.scheduler.get(tournament_id)
        if tournament is None:
            return {"status": "REJECTED", "reason": "tournament not found"}
        self.scheduler.mark_running(tournament_id)
        self.tournament_status = "RUNNING"
        self.seed_default_liquidity()
        for entry in tournament.entries.values():
            self._run_entered_bot_once(entry)
        self.advance_tick(self.config.rules.duration_ticks)
        leaderboard = self.get_leaderboard()
        result = self.scheduler.publish_results(tournament_id, leaderboard)
        self.tournament_status = "COMPLETED"
        return {"status": "COMPLETED", "tournament": result, "leaderboard": leaderboard}

    def _run_entered_bot_once(self, entry):
        market = self.config.markets[0]
        venue = self.config.venues[0]
        trader_id = f"{entry.owner_id}:{entry.bot_name}:v{entry.version}"
        reference_price = market.initial_reference_price
        self.place_order("BUY", reference_price, market.lot_size, trader_id, symbol=market.symbol, venue=venue.venue_id)
