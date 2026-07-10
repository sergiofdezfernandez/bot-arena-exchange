import collections
import random

from bot_arena_exchange.domain.bots import generate_pareto_size


class LaggingMarketMakerBot:
    """Market maker that operates on VENUE_2 with an artificial delay of 15 ticks.

    On each tick:
    1. Queries the CURRENT mid-price from VENUE_1.
    2. Stores it in a fixed-size deque (maxlen=15).
    3. When the deque is full, takes the OLDEST price (price_history[0])
       as reference to quote bid/ask on VENUE_2.

    This simulates latency: when TrendBot pushes the price on VENUE_1,
    this bot takes 15 ticks to reflect the change on VENUE_2, creating
    an arbitrage window that SpatialArbitrageBot can exploit.

    The latency is simulated purely at the bot behavior level,
    WITHOUT modifying the matching engine or introducing asyncio.sleep.
    """

    def __init__(self):
        self.symbol = "AAPL"
        self.venue = "VENUE_2"                           # Operates EXCLUSIVELY on VENUE_2
        self.trader_id = None                            # Set by the factory

        # ── Price memory (simulates 15-tick latency) ─────────────────────
        self.price_history = collections.deque(maxlen=15)

        # ── Market-making parameters ─────────────────────────────────────
        self.base_spread = 4                             # Spread in pips
        self.max_position = 20                           # Inventory limit

        # ── Active order control ─────────────────────────────────────────
        self.bid_order_id = None
        self.ask_order_id = None

        # ── Tick counter for debug ───────────────────────────────────────
        self._tick_count = 0

        # ── OFI (Order Flow Imbalance) tracking from VENUE_1 ─────────────
        self.prev_bid_price = None
        self.prev_bid_qty = 0
        self.prev_ask_price = None
        self.prev_ask_qty = 0
        self.ofi_window = []                             # Rolling OFI values
        self.ofi_window_size = 10
        self.ofi_sensitivity = 0.1                       # Cents to adjust per unit of OFI

    def _cancel_existing_quotes(self, api):
        """Cancel active orders on VENUE_2 before sending new ones."""
        if self.bid_order_id is not None:
            try:
                api.cancel_order(self.bid_order_id)
            except Exception:
                import traceback
                print(f"[CANCEL ERROR] {self.trader_id if hasattr(self, 'trader_id') else '?'} failed to cancel bid {self.bid_order_id}")
                traceback.print_exc()
            finally:
                self.bid_order_id = None

        if self.ask_order_id is not None:
            try:
                api.cancel_order(self.ask_order_id)
            except Exception:
                import traceback
                print(f"[CANCEL ERROR] {self.trader_id if hasattr(self, 'trader_id') else '?'} failed to cancel ask {self.ask_order_id}")
                traceback.print_exc()
            finally:
                self.ask_order_id = None

    def _get_mid_price_v1(self, api):
        """Get the current mid-price from VENUE_1."""
        book_v1 = api.get_order_book(symbol=self.symbol, venue="VENUE_1")
        best_bid = book_v1.get("best_bid")
        best_ask = book_v1.get("best_ask")

        # Fallback to snapshot
        if best_bid is None or best_ask is None:
            snapshot = book_v1.get("snapshot", {})
            bids = snapshot.get("bids", [])
            asks = snapshot.get("asks", [])
            if bids and best_bid is None:
                best_bid = bids[0].get("price") if isinstance(bids[0], dict) else bids[0]
            if asks and best_ask is None:
                best_ask = asks[0].get("price") if isinstance(asks[0], dict) else asks[0]

        # If no data on VENUE_1, use reference price
        if best_bid is None and best_ask is None:
            return 10000
        elif best_bid is None:
            return max(1, best_ask - 1)
        elif best_ask is None:
            return best_bid + 1

        return (best_bid + best_ask) // 2

    def _calculate_ofi(self, api):
        """Calculate Order Flow Imbalance from VENUE_1's order book snapshot."""
        book_v1 = api.get_order_book(symbol=self.symbol, venue="VENUE_1")
        snapshot = book_v1.get("snapshot", {})
        bids = snapshot.get("bids", [])
        asks = snapshot.get("asks", [])

        # Extract current best bid/ask prices and quantities
        current_bid_price = bids[0]["price"] if bids else None
        current_bid_qty = bids[0]["quantity"] if bids else 0
        current_ask_price = asks[0]["price"] if asks else None
        current_ask_qty = asks[0]["quantity"] if asks else 0

        # 1. Calculate Bid Flow (handle None prices when book is empty)
        if self.prev_bid_price is None or current_bid_price is None:
            bid_flow = 0
        elif current_bid_price > self.prev_bid_price:
            bid_flow = current_bid_qty
        elif current_bid_price == self.prev_bid_price:
            bid_flow = current_bid_qty - self.prev_bid_qty
        else:
            bid_flow = -self.prev_bid_qty

        # 2. Calculate Ask Flow (handle None prices when book is empty)
        if self.prev_ask_price is None or current_ask_price is None:
            ask_flow = 0
        elif current_ask_price < self.prev_ask_price:
            ask_flow = current_ask_qty
        elif current_ask_price == self.prev_ask_price:
            ask_flow = current_ask_qty - self.prev_ask_qty
        else:
            ask_flow = -self.prev_ask_qty

        # 3. Net OFI and Rolling Average
        current_ofi = bid_flow - ask_flow
        self.ofi_window.append(current_ofi)
        if len(self.ofi_window) > self.ofi_window_size:
            self.ofi_window.pop(0)

        average_ofi = sum(self.ofi_window) / len(self.ofi_window) if self.ofi_window else 0

        # 4. Update previous state for the next tick
        self.prev_bid_price = current_bid_price
        self.prev_bid_qty = current_bid_qty
        self.prev_ask_price = current_ask_price
        self.prev_ask_qty = current_ask_qty

        return average_ofi

    def on_tick(self, api):
        try:
            self._tick_count += 1

            # 1. Cancel previous orders on VENUE_2
            self._cancel_existing_quotes(api)

            # 2. Get the CURRENT mid-price from VENUE_1
            current_mid_v1 = self._get_mid_price_v1(api)

            # 3. Add to history (deque with maxlen=15)
            self.price_history.append(current_mid_v1)

            # 4. If the deque is NOT full yet, seed with current price
            #    so VENUE_2 has liquidity from the start
            if len(self.price_history) < self.price_history.maxlen:
                reference_price = current_mid_v1
            else:
                # The deque is full: use the oldest price (15 ticks delayed)
                reference_price = self.price_history[0]

            # 5. Get current position for inventory skew
            account = api.get_account()
            position = account.positions.get(self.symbol, 0) if account else 0

            # 6. Calculate OFI from VENUE_1 and skew the reference price
            average_ofi = self._calculate_ofi(api)
            skewed_reference = reference_price + (average_ofi * self.ofi_sensitivity)

            # 7. Calculate bid/ask with inventory skew and shock-reactive spread
            shock = api.get_shock_state() if hasattr(api, "get_shock_state") else 0.0
            shock_multiplier = 1.0 + shock * 5.0  # e.g., shock=0.4 → 3× spread
            effective_spread = max(self.base_spread, int(self.base_spread * shock_multiplier))
            half_spread = effective_spread // 2
            inventory_skew = max(-3, min(3, position // 3))

            bid_price = max(1, skewed_reference - half_spread - inventory_skew)
            ask_price = max(bid_price + 1, skewed_reference + half_spread - inventory_skew)

            # Risk control: if position is at the limit, widen the spread
            if position >= self.max_position:
                bid_price = max(1, bid_price - self.base_spread * 2)
            if position <= -self.max_position:
                ask_price = ask_price + self.base_spread * 2

            # 8. Place orders on VENUE_2
            qty = generate_pareto_size(base_size=100, alpha=3.0, max_limit=400)
            bid_res = api.place_order(
                side="BUY",
                price=int(bid_price),
                quantity=qty,
                symbol=self.symbol,
                venue=self.venue,
            )
            ask_res = api.place_order(
                side="SELL",
                price=int(ask_price),
                quantity=qty,
                symbol=self.symbol,
                venue=self.venue,
            )
            self.bid_order_id = bid_res.get("order_id") if isinstance(bid_res, dict) else getattr(bid_res, "order_id", bid_res)
            self.ask_order_id = ask_res.get("order_id") if isinstance(ask_res, dict) else getattr(ask_res, "order_id", ask_res)
        except Exception as e:
            import traceback
            print(f"[BOT CRASH] {self.trader_id} crashed on tick {self._tick_count}: {e}")
            traceback.print_exc()


def create_bot():
    return LaggingMarketMakerBot()