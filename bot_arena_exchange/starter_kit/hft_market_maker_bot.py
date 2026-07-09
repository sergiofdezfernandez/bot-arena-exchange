import math


class HftMarketMakerBot:
    """High-frequency market maker with inventory skewing on VENUE_1.

    Cancels previous quotes every tick and recalculates bid/ask centered
    on the mid-price. Applies inventory skewing to bias quotes toward
    reducing position: long inventory pushes quotes lower to favor sales,
    short inventory pushes quotes higher to favor purchases.

    Operates EXCLUSIVELY on VENUE_1 with symbol AAPL.
    """

    def __init__(self):
        self.symbol = "AAPL"
        self.venue = "VENUE_1"
        self.trader_id = None            # Set by the factory in fastapi_app.py

        # ── Risk parameters ───────────────────────────────────────────
        self.max_inventory = 1000       # Absolute position limit
        self.base_spread = 2             # Base spread in pips
        self.order_size = 50             # Size per order

        # ── Active order tracking ──────────────────────────────────────
        self.bid_order_id = None
        self.ask_order_id = None

        # ── Restocking fallback: track last known fair value ───────────
        self.last_fair_value = 10000

        # ── Inventory risk aversion: maximum ticks to shift price ──────
        self.inventory_risk_aversion = 15

        # ── OFI (Order Flow Imbalance) tracking ────────────────────────
        self.prev_bid_price = None
        self.prev_bid_qty = 0
        self.prev_ask_price = None
        self.prev_ask_qty = 0
        self.ofi_window = []             # Rolling OFI values
        self.ofi_window_size = 10
        self.ofi_sensitivity = 0.1       # Cents to adjust per unit of OFI

        # ── Trade Impact (Adverse Selection) ───────────────────────────
        self.trade_impact_factor = 0.2  # Increased from 0.05 (100 shares now moves fair value by 20 ticks)
        self.last_processed_trade_timestamp = 0  # Cursor for deduplication

    def _cancel_existing_quotes(self, api):
        """Cancel both active bid and ask orders before placing new ones."""
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

    def _calculate_ofi(self, book):
        """Calculate Order Flow Imbalance from the order book snapshot."""
        snapshot = book.get("snapshot", {})
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

    def _apply_trade_impact(self, book):
        """Shift self.last_fair_value based on recent public trades.

        Aggressive BUYs (trade price >= last_fair_value) push fair value UP;
        aggressive SELLs push it DOWN.  Uses timestamp-based deduplication
        to avoid double-counting trades already processed.
        """
        recent_trades = book.get("recent_trades") or []
        for trade in recent_trades:
            ts = trade.get("timestamp", 0)
            if ts <= self.last_processed_trade_timestamp:
                continue

            # Infer aggressor side from trade price vs last known fair value
            impact = trade.get("quantity", 0) * self.trade_impact_factor
            if trade.get("price", 0) >= self.last_fair_value:
                # Aggressive BUY — fair value moves UP
                self.last_fair_value += impact
            else:
                # Aggressive SELL — fair value moves DOWN
                self.last_fair_value -= impact

            self.last_processed_trade_timestamp = ts

    def on_tick(self, api):
        # 1. Cancel previous quotes
        self._cancel_existing_quotes(api)

        # 2. Read market state
        book = api.get_order_book(symbol=self.symbol, venue=self.venue)
        account = api.get_account()
        self.position = account.positions.get(self.symbol, 0) if account else 0

        # 3. Apply trade impact (adverse selection) — shifts last_fair_value
        self._apply_trade_impact(book)

        # 4. EMA Soft Tether: 10% gravitational pull to market consensus
        mid_price = self._mid_price(book)
        if mid_price is not None:
            alpha = 0.1
            self.last_fair_value = (alpha * mid_price) + ((1 - alpha) * self.last_fair_value)

        # 5. OFI skew on the EMA-tethered base
        average_ofi = self._calculate_ofi(book)
        skewed_fair_value = self.last_fair_value + (average_ofi * self.ofi_sensitivity)

        # Cubic Inventory Skew: flat center for tight liquidity,
        # violent reaction near capacity to survive toxic structural flow
        # If position is negative (short), skew is positive (pushes prices UP to discourage buyers and encourage sellers)
        # If position is positive (long), skew is negative (pushes prices DOWN to discourage sellers and encourage buyers)
        norm_pos = self.position / self.max_inventory
        inventory_skew = -(norm_pos ** 3) * self.inventory_risk_aversion
        final_fair_value = skewed_fair_value + inventory_skew

        # Update last known fair value for restocking fallback
        self.last_fair_value = final_fair_value

        # Extract best bid/ask for restocking check
        best_bid, best_ask = self._extract_best_prices(book)

        # 4. Calculate prices using final_fair_value (includes inventory skew)
        # ── Shock-reactive spread widening ──────────────────────────
        shock = api.get_shock_state() if hasattr(api, "get_shock_state") else 0.0
        shock_multiplier = 1.0 + shock * 5.0  # e.g., shock=0.4 → 3× spread
        effective_spread = max(self.base_spread, int(self.base_spread * shock_multiplier))
        half_spread = effective_spread // 2

        # Asymmetric Quote Fading with limits
        fade_sensitivity = 0.15  # Throttled down from 0.5
        max_fade_ticks = 15      # Hard circuit breaker: never fade more than 15 ticks

        # Calculate raw fades
        raw_ask_fade = max(0, average_ofi * fade_sensitivity)
        raw_bid_fade = max(0, -average_ofi * fade_sensitivity)

        # Apply caps
        ask_fade = min(max_fade_ticks, raw_ask_fade)
        bid_fade = min(max_fade_ticks, raw_bid_fade)

        bid_price = max(1, int(math.floor(final_fair_value - half_spread - bid_fade)))
        ask_price = max(bid_price + 1, int(math.ceil(final_fair_value + half_spread + ask_fade)))

        # Restocking fallback: if book is empty on one side, use safe distance from fair value
        if best_bid is None:
            bid_price = max(1, int(self.last_fair_value) - 5)
        if best_ask is None:
            ask_price = int(self.last_fair_value) + 5

        # Dynamic sizing: reduce quoted size when near capacity limits (>80%)
        current_capacity = abs(self.position) / self.max_inventory
        dynamic_size = self.order_size
        if current_capacity > 0.8:
            dynamic_size = max(1, int(self.order_size * 0.2))  # Quote only 20% size when near limits

        # 5. Place quotes on VENUE_1
        bid_res = api.place_order(
            side="BUY",
            price=int(bid_price),
            quantity=int(dynamic_size),
            symbol=self.symbol,
            venue=self.venue,
        )
        ask_res = api.place_order(
            side="SELL",
            price=int(ask_price),
            quantity=int(dynamic_size),
            symbol=self.symbol,
            venue=self.venue,
        )
        self.bid_order_id = bid_res.get("order_id") if isinstance(bid_res, dict) else getattr(bid_res, "order_id", bid_res)
        self.ask_order_id = ask_res.get("order_id") if isinstance(ask_res, dict) else getattr(ask_res, "order_id", ask_res)


def create_bot():
    return HftMarketMakerBot()