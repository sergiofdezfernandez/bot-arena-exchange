import random

from bot_arena_exchange.domain.bots import generate_pareto_size


class InstitutionalTrendBot:
    """Institutional trend follower that operates aggressively on VENUE_1.

    Alternates between BULL, BEAR, and FLAT phases. In directional phases
    (BULL/BEAR), has a 20% chance per tick of firing an aggressive order
    that crosses the spread to generate directional price pressure.

    Operates EXCLUSIVELY on VENUE_1 with symbol AAPL.
    """

    def __init__(self):
        self.symbol = "AAPL"
        self.venue = "VENUE_1"
        self.trader_id = None            # Set by the factory in fastapi_app.py

        # ── Phase control ──────────────────────────────────────────────
        self.current_phase = random.choice(["BULL", "BEAR", "FLAT"])
        self.ticks_remaining = random.randint(50, 150)

        # ── Active order tracking ──────────────────────────────────────
        self.active_order_id = None

        # ── Probabilities ──────────────────────────────────────────────
        self.aggression_chance = 0.02   # 1% per tick chance of firing an aggressive order

    def _cancel_existing_order(self, api):
        """Cancel the active order if one exists."""
        if self.active_order_id is not None:
            try:
                api.cancel_order(self.active_order_id)
            except Exception:
                pass
            self.active_order_id = None

    def _extract_best_prices(self, book):
        """Extract best_bid and best_ask from the order book snapshot."""
        best_bid = book.get("best_bid")
        best_ask = book.get("best_ask")

        # Fallback to snapshot if best_bid/best_ask are not available
        if best_bid is None or best_ask is None:
            snapshot = book.get("snapshot", {})
            bids = snapshot.get("bids", [])
            asks = snapshot.get("asks", [])
            if bids and best_bid is None:
                best_bid = bids[0].get("price") if isinstance(bids[0], dict) else bids[0]
            if asks and best_ask is None:
                best_ask = asks[0].get("price") if isinstance(asks[0], dict) else asks[0]

        return best_bid, best_ask

    def on_tick(self, api):
        # 1. Cancel previous order (if any)
        self._cancel_existing_order(api)

        # 2. Decrement phase counter and rotate if it hits 0
        self.ticks_remaining -= 1
        if self.ticks_remaining <= 0:
            self.current_phase = random.choice(["BULL", "BEAR", "FLAT"])
            self.ticks_remaining = random.randint(50, 150)

        # 3. Read the order book from VENUE_1
        book = api.get_order_book(symbol=self.symbol, venue=self.venue)
        best_bid, best_ask = self._extract_best_prices(book)

        # If the book is empty, seed with reference price
        if best_bid is None and best_ask is None:
            best_bid = 9999
            best_ask = 10001
        elif best_bid is None:
            best_bid = best_ask - 2
        elif best_ask is None:
            best_ask = best_bid + 2

        # 4. Phase logic
        if self.current_phase == "FLAT":
            # Flat phase: do nothing
            return

        # In directional phases: 20% chance of acting per tick
        if random.random() >= self.aggression_chance:
            return

        order_size = generate_pareto_size(base_size=150, alpha=1.8, max_limit=5000)

        if self.current_phase == "BULL":
            # Bull phase: aggressive buy crossing the spread
            buy_price = best_ask + 15
            res = api.place_order(
                side="BUY",
                price=int(buy_price),
                quantity=order_size,
                symbol=self.symbol,
                venue=self.venue,
            )
            self.active_order_id = res.get("order_id") if isinstance(res, dict) else getattr(res, "order_id", res)

        elif self.current_phase == "BEAR":
            # Bear phase: aggressive sell crossing the spread
            sell_price = max(1, best_bid - 15)
            res = api.place_order(
                side="SELL",
                price=int(sell_price),
                quantity=order_size,
                symbol=self.symbol,
                venue=self.venue,
            )
            self.active_order_id = res.get("order_id") if isinstance(res, dict) else getattr(res, "order_id", res)


def create_bot():
    return InstitutionalTrendBot()