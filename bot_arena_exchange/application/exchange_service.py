from bot_arena_exchange.domain.bots import SimpleMarketMaker
from bot_arena_exchange.domain.order_book import OrderBook
from bot_arena_exchange.domain.tournament import TournamentManager


class ExchangeService:
    def __init__(self, book=None, manager=None):
        self.book = book or OrderBook()
        self.manager = manager or TournamentManager(position_limit=100)

    @classmethod
    def with_default_liquidity(cls):
        service = cls()
        bot = SimpleMarketMaker(trader_id="Bot_MM", symbol="AAPL", venue="VENUE_1", edge=5, size=10)
        for quote in bot.generate_quotes(10000):
            service.book.place_order(
                quote["side"],
                quote["price"],
                quote["quantity"],
                bot.trader_id,
                bot.symbol,
                bot.venue,
            )
        return service

    def get_market_snapshot(self):
        return self.book.get_snapshot()

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

    def place_order(self, side, price, quantity, trader_id, symbol="AAPL", venue="VENUE_1"):
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
        return {
            "status": "PROCESSED",
            "order_id": order_id,
            "trades_executed": len(trades),
            "disconnections_triggered": disconnections,
        }
