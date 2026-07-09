import random


class PhantomSpooferBot:
    """Spoofing bot that places large fake orders away from mid-price on VENUE_2.

    When idle, flips a coin (50/50) each tick to decide whether to place a giant
    spoof order (100 lots) far from the mid-price to create the illusion of deep
    liquidity. The order can be either:

    - BUY spoof: placed well below the mid-price (mid - spoof_distance)
    - SELL spoof: placed well above the mid-price (mid + spoof_distance)

    If the real mid-price moves close to the spoof order (within danger_zone pips),
    the order is immediately cancelled to avoid being filled. This simulates the
    classic "layering" manipulation tactic without actually executing.

    Operates EXCLUSIVELY on VENUE_2 (venue forced by factory in fastapi_app.py).
    """

    def __init__(self):
        self.symbol = "AAPL"
        self.venue = "VENUE_2"                           # Overridden by factory
        self.trader_id = None                            # Set by the factory

        # ── Spoofing parameters ────────────────────────────────────────
        self.spoof_distance = 10      # Pips away from mid to place the fake order
        self.danger_zone = 3          # If mid comes within this many pips → cancel
        self.spoof_size = 100         # Giant size to intimidate other participants

        # ── Spoof order tracking ───────────────────────────────────────
        self.spoof_order_id = None    # ID of the currently active spoof order
        self.spoof_price = None       # Price at which the spoof order was placed
        self.spoof_side = None        # "BUY" or "SELL" — which side is being spoofed

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

    def _mid_price(self, book):
        """Compute the mid-price from the order book."""
        best_bid, best_ask = self._extract_best_prices(book)

        if best_bid is not None and best_ask is not None:
            return (best_bid + best_ask) // 2
        if best_bid is not None:
            return best_bid + 1
        if best_ask is not None:
            return max(1, best_ask - 1)
        return 10000

    def on_tick(self, api):
        # 1. Read VENUE_2 order book
        book = api.get_order_book(symbol=self.symbol, venue=self.venue)
        mid = self._mid_price(book)

        # 2. If we have an active spoof order, check danger zone
        if self.spoof_order_id is not None:
            # Distance from current mid to the spoof price
            distance = abs(mid - self.spoof_price)

            if distance < self.danger_zone:
                # Danger! Mid is approaching — cancel immediately to avoid fill
                try:
                    api.cancel_order(self.spoof_order_id)
                except Exception:
                    pass
                self.spoof_order_id = None
                self.spoof_price = None
                self.spoof_side = None
            # If still safe, leave the order alive and do nothing this tick
            return

        # 3. No active spoof order: decide whether to spoof this tick (50/50)
        if random.random() < 0.5:
            return

        # 4. Flip a coin for spoof direction (50/50)
        self.spoof_side = random.choice(["BUY", "SELL"])

        if self.spoof_side == "BUY":
            # BUY spoof: place a giant bid well below mid (looks like deep support)
            self.spoof_price = max(1, mid - self.spoof_distance)
        else:
            # SELL spoof: place a giant ask well above mid (looks like deep resistance)
            self.spoof_price = mid + self.spoof_distance

        # 5. Place the spoof order on VENUE_2
        res = api.place_order(
            side=self.spoof_side,
            price=self.spoof_price,
            quantity=self.spoof_size,
            symbol=self.symbol,
            venue=self.venue,
        )
        self.spoof_order_id = res.get("order_id") if isinstance(res, dict) else getattr(res, "order_id", res)


def create_bot():
    return PhantomSpooferBot()