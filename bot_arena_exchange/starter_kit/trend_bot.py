import random

from bot_arena_exchange.domain.bots import generate_pareto_size


class TrendBot:
    """Bot that alternates between UP and DOWN phases, creating directional trends.

    Operates EXCLUSIVELY on VENUE_1 with symbol AAPL.
    Alternates between UP and DOWN phases, placing aggressive orders that cross
    the spread to push the mid-price directionally.

    This creates a price on VENUE_1 that diverges from the price on VENUE_2
    (where LaggingMarketMakerBot quotes with a delay), thereby generating
    spatial arbitrage opportunities.
    """

    def __init__(self):
        self.symbol = "AAPL"
        self.venue = "VENUE_1"           # Operates EXCLUSIVELY on VENUE_1
        self.trader_id = None            # Set by the factory in fastapi_app.py

        # ── Trend control ────────────────────────────────────────────────
        self.direction = "UP"            # UP or DOWN
        self._ticks_in_phase = 0
        self.phase_length = 25           # Changes direction every ~25 ticks
        self.aggression_chance = 0.40    # 40% probability of crossing the spread

        # ── Active order control ─────────────────────────────────────────
        self.current_order_id = None

    def _cancel_existing_order(self, api):
        """Cancel any existing order to make room for a fresh one."""
        if self.current_order_id is not None:
            try:
                api.cancel_order(self.current_order_id)
                self.current_order_id = None
            except Exception:
                pass

    def _get_mid_price_v1(self, api):
        """Get the current mid-price from VENUE_1."""
        book = api.get_order_book(symbol=self.symbol, venue=self.venue)
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

        # If the book is empty, seed with reference price
        if best_bid is None and best_ask is None:
            return 10000
        elif best_bid is None:
            return max(1, best_ask - 1)
        elif best_ask is None:
            return best_bid + 1

        return (best_bid + best_ask) // 2

    def on_tick(self, api):
        try:
            # 1. Cancel previous orders
            self._cancel_existing_order(api)

            # 2. Read the order book from VENUE_1
            mid_price = self._get_mid_price_v1(api)

            # 3. Periodically update trend direction
            self._ticks_in_phase += 1
            if self._ticks_in_phase >= self.phase_length:
                # Change direction with some randomness
                if random.random() < 0.7:
                    self.direction = "DOWN" if self.direction == "UP" else "UP"
                # else: keep the direction for longer trends
                self._ticks_in_phase = 0

            # 4. Decide if this tick is aggressive (crosses the spread)
            is_aggressive = random.random() < self.aggression_chance

            if self.direction == "UP":
                # Bull phase: push the price upward with buys
                if is_aggressive:
                    # Aggressive buy: price above best_ask to ensure fill
                    price = mid_price + random.randint(2, 4)
                else:
                    # Passive buy: at current bid or slightly better
                    price = mid_price - random.randint(0, 2)
                side = "BUY"

            else:  # DOWN
                # Bear phase: push the price downward with sells
                if is_aggressive:
                    # Aggressive sell: price below best_bid to ensure fill
                    price = mid_price - random.randint(2, 4)
                else:
                    # Passive sell: at current ask or slightly worse
                    price = mid_price + random.randint(0, 2)
                side = "SELL"

            price = max(1, price)

            # 5. Place the order on VENUE_1
            qty = generate_pareto_size(base_size=10, alpha=2.5, max_limit=200)
            res = api.place_order(
                side=side,
                price=int(price),
                quantity=qty,
                symbol=self.symbol,
                venue=self.venue,
            )
            self.current_order_id = res.get("order_id") if isinstance(res, dict) else getattr(res, "order_id", res)
        except Exception:
            pass


def create_bot():
    return TrendBot()