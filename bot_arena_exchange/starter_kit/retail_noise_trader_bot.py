import random


class RetailNoiseTraderBot:
    """Retail noise trader that generates random passive/aggressive orders on VENUE_1.

    Has a 15% chance per tick of acting. When active, randomly chooses BUY or SELL
    with size 1-2 lots. Orders are either passive (at best_bid/best_ask) or slightly
    aggressive (±1-2 pips) to inject realistic retail noise into the order book.

    To prevent order book pollution, each placed order is tracked with its age.
    Orders that survive more than max_order_lifetime ticks (randomized 5-10) are
    explicitly cancelled in subsequent on_tick calls.

    Operates EXCLUSIVELY on VENUE_1 with symbol AAPL.
    """

    def __init__(self):
        self.symbol = "AAPL"
        self.venue = "VENUE_1"
        self.trader_id = None            # Set by the factory in fastapi_app.py

        # ── Action probability ─────────────────────────────────────────
        self.action_chance = 0.15        # 15% chance per tick of placing an order

        # ── Order lifetime tracking (prevents order book pollution) ────
        # List of dicts: {"order_id": str, "age": int, "max_lifetime": int}
        self.pending_orders = []

    def _cancel_stale_orders(self, api):
        """Cancel any order that has exceeded its max lifetime.

        Each order is allowed to live for a random 5-10 ticks. Once expired,
        it is cancelled to prevent infinite accumulation of unfilled passive
        orders in the order book.
        """
        still_alive = []
        for entry in self.pending_orders:
            entry["age"] += 1
            if entry["age"] >= entry["max_lifetime"]:
                try:
                    api.cancel_order(entry["order_id"])
                except Exception:
                    pass
            else:
                still_alive.append(entry)
        self.pending_orders = still_alive

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
        # 1. Cancel any orders that have exceeded their lifetime
        self._cancel_stale_orders(api)

        # 2. Decide whether to act this tick (15% chance)
        if random.random() >= self.action_chance:
            return

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

        # 4. Randomly choose side and size
        side = random.choice(["BUY", "SELL"])
        quantity = random.randint(1, 2)

        # 5. Choose passive vs slightly aggressive (50/50)
        is_aggressive = random.random() < 0.5

        if side == "BUY":
            if is_aggressive:
                # Slightly aggressive: pay a bit above best_ask
                price = best_ask + random.randint(0, 2)
            else:
                # Passive: join the best_bid
                price = max(1, best_bid)
        else:  # SELL
            if is_aggressive:
                # Slightly aggressive: sell a bit below best_bid
                price = max(1, best_bid - random.randint(0, 2))
            else:
                # Passive: join the best_ask
                price = best_ask

        # 6. Place the order
        order_id = api.place_order(
            side=side,
            price=int(price),
            quantity=quantity,
            symbol=self.symbol,
            venue=self.venue,
        )

        # 7. Track the order with a random lifetime (5-10 ticks)
        if order_id is not None:
            self.pending_orders.append({
                "order_id": order_id,
                "age": 0,
                "max_lifetime": random.randint(5, 10),
            })


def create_bot():
    return RetailNoiseTraderBot()