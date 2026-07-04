from pathlib import Path

from bot_arena_exchange.application.api_gateway import ApiGateway
from bot_arena_exchange.application.event_log import InMemoryEventLog
from bot_arena_exchange.config.tournament_config import DEFAULT_TOURNAMENT_CONFIG, TournamentConfig, load_tournament_config
from bot_arena_exchange.domain.bots import SimpleMarketMaker
from bot_arena_exchange.domain.order_book import OrderBook
from bot_arena_exchange.domain.scoring import score_account
from bot_arena_exchange.domain.tournament import TournamentManager


class ExchangeService:
    def __init__(self, config=None, book=None, manager=None, event_log=None, tournament_status="RUNNING"):
        self.config = config or DEFAULT_TOURNAMENT_CONFIG
        self.book = book or OrderBook()
        self.manager = manager or TournamentManager(position_limit=100)
        self.event_log = event_log or InMemoryEventLog()
        self.gateway = ApiGateway(self.config, self.manager)
        self.tournament_status = tournament_status

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

    def get_market_snapshot(self):
        return self.book.get_snapshot()

    def get_event_log(self):
        return self.event_log.as_dicts()

    def get_traders_status(self):
        return {
            trader_id: {
                "trader_id": account.trader_id,
                "positions": dict(account.positions),
                "avg_costs": dict(account.avg_costs),
                "realized_pnl": account.realized_pnl,
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
                event_type="ORDER_REQUEST",
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

        self.book.trades.clear()
        order_id = self.book.place_order(
            side=side,
            price=price,
            quantity=quantity,
            trader_id=trader_id,
            symbol=symbol,
            venue=venue,
        )
        trades = self.book.get_trades()
        disconnections = self.manager.process_trades(trades)
        self.event_log.record(
            event_type="ORDER_REQUEST",
            bot_id=trader_id,
            tournament_id=self.config.tournament_id,
            payload=payload,
            validation_result="ACCEPTED",
            final_action="PLACE_ORDER",
        )
        return {
            "status": "PROCESSED",
            "order_id": order_id,
            "trades_executed": len(trades),
            "disconnections_triggered": disconnections,
        }

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
