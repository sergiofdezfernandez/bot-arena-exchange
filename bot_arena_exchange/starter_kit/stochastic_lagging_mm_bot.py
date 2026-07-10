import collections
import random

from bot_arena_exchange.domain.bots import generate_pareto_size


class StochasticLaggingMMBot:
    """Stochastic-latency market maker that operates on VENUE_2 using lagged VENUE_1 prices.

    Reads the mid-price from VENUE_1 every tick and appends it to a bounded deque
    (maxlen=60). A dynamic delay is sampled from a Gaussian distribution centered at
    15 ticks (σ=3, clamped to [1, deque_len]). The reference price for quoting on
    VENUE_2 is taken from that many ticks in the past, simulating stochastic latency.

    Cancels previous quotes each tick and re-places bid/ask on VENUE_2 centered on
    the lagged price with a fixed spread of 4 pips.

    Operates EXCLUSIVELY on VENUE_2 (venue forced by factory in fastapi_app.py).
    """

    def __init__(self):
        self.symbol = "AAPL"
        self.venue = "VENUE_2"                           # Overridden by factory
        self.trader_id = None                            # Set by the factory

        # ── Price memory (simulates stochastic latency) ────────────────
        self.price_history = collections.deque(maxlen=60)

        # ── Market-making parameters ───────────────────────────────────
        self.base_spread = 4                             # Fixed spread in pips
        self.max_position = 30                           # Inventory limit

        # ── Active order tracking ──────────────────────────────────────
        self.bid_order_id = None
        self.ask_order_id = None

        # ── OFI (Order Flow Imbalance) tracking from VENUE_1 ───────────
        self.prev_bid_price = None
        self.prev_bid_qty = 0
        self.prev_ask_price = None
        self.prev_ask_qty = 0
        self.ofi_window = []                             # Rolling OFI values
        self.ofi_window_size = 10
        self.ofi_sensitivity = 0.1                       # Cents to adjust per unit of OFI

    def _cancel_existing_quotes(self, api):
        """Cancel both active orders on VENUE_2 before placing new ones."""
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
        """Fetch the current mid-price from VENUE_1."""
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
        # 1. Cancel previous quotes on VENUE_2
        self._cancel_existing_quotes(api)

        # 2. Get current mid-price from VENUE_1 and store it
        current_mid_v1 = self._get_mid_price_v1(api)
        self.price_history.append(current_mid_v1)

        # 3. Sample stochastic delay: Gaussian centered at 15, σ=3
        deque_len = len(self.price_history)
        raw_delay = int(random.gauss(15, 3))
        delay = max(1, min(raw_delay, deque_len))

        # 4. Retrieve the lagged price (index from the end)
        lagged_price = self.price_history[-delay]

        # 5. Get current position for inventory control
        account = api.get_account()
        position = account.positions.get(self.symbol, 0) if account else 0

        # 6. Calculate OFI from VENUE_1 and skew the lagged price
        average_ofi = self._calculate_ofi(api)
        skewed_lagged_price = lagged_price + (average_ofi * self.ofi_sensitivity)

        # 7. Calculate bid/ask centered on the skewed lagged price with shock-reactive spread
        shock = api.get_shock_state() if hasattr(api, "get_shock_state") else 0.0
        shock_multiplier = 1.0 + shock * 5.0  # e.g., shock=0.4 → 3× spread
        effective_spread = max(self.base_spread, int(self.base_spread * shock_multiplier))
        half_spread = effective_spread // 2
        bid_price = max(1, skewed_lagged_price - half_spread)
        ask_price = max(bid_price + 1, skewed_lagged_price + half_spread)

        # Light inventory skew for risk control
        inventory_skew = max(-2, min(2, position // 5))
        bid_price = max(1, bid_price - inventory_skew)
        ask_price = max(bid_price + 1, ask_price - inventory_skew)

        # Hard risk limits
        if position >= self.max_position:
            bid_price = max(1, bid_price - self.base_spread * 3)
        if position <= -self.max_position:
            ask_price = ask_price + self.base_spread * 3

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


def create_bot():
    return StochasticLaggingMMBot()