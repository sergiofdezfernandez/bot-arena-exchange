"""Spatial Arbitrage Apex Predator — OFI-Sniper Edition.

This bot operates as a state machine with multiple exploitation strategies:

Strategy A — OFI-Sniper (Primary Active Strategy):
    Predicts VENUE_1 price movements using Order Flow Imbalance (OFI) at the
    top of the book. When OFI strongly predicts upward movement on VENUE_1,
    the bot preemptively snipes the Ask on VENUE_2 before the LaggingMM reacts.
    When OFI predicts downward movement, it snipes the Bid on VENUE_2.

Strategy B — Inventory Unloading (Mean Reversion):
    When no OFI signal fires but the bot holds a position, it passively unwinds
    inventory on VENUE_1 by placing limit orders at the best available prices.

Strategy C — Active Mid-Price Spoofing (The 15-Tick Trap):
    When no passive arbitrage exists and inventory is flat, the bot manufactures
    its own opportunity by placing a massive fake BUY on VENUE_1 to inflate the
    mid-price. After waiting 15+ ticks for the LaggingMM to absorb the fake
    price on VENUE_2, the bot aggressively sells into the LaggingMM's elevated
    bids, then cancels the spoof order before anyone can fill it.

State Machine:
    SNIPING → (OFI signal) → execute snipe on V2 → stay in SNIPING
    SNIPING → (no signal, has position) → INVENTORY_UNLOAD → SNIPING
    SNIPING → (no signal, flat) → SPOOFING_UP → WAITING_FOR_LAG → DUMPING → SNIPING
"""


class SpatialArbitrageBot:
    """Apex predator that hunts the LaggingMarketMakerBot using OFI prediction."""

    def __init__(self):
        self.symbol = "AAPL"
        self.trader_id = None  # Set by the factory in fastapi_app.py

        # ── State Machine ─────────────────────────────────────────────────
        self.state = "SNIPING"

        # ── Spoofing state ────────────────────────────────────────────────
        self.spoof_order_id = None
        self.lag_ticks_waited = 0
        self.spoof_price = None  # Price at which the spoof BUY was placed

        # ── Arbitrage order tracking ──────────────────────────────────────
        self.bid_order_id = None
        self.ask_order_id = None

        # ── Configuration ─────────────────────────────────────────────────
        self.spoof_size = 15  # Fake order to move VENUE_1 mid-price (reduced from 50 to avoid toxic flow)
        self.arb_size = 2  # Real arbitrage leg size
        self.order_size = 2  # Shares per OFI-triggered snipe
        self.lag_window = 15  # Match LaggingMM's deque maxlen
        self.fee_v1_bps = 0  # VENUE_1: 0 bps
        self.fee_v2_bps = 15  # VENUE_2: 15 bps
        self.min_profit = 3  # Minimum net profit (pips) to trigger arb

        # ── OFI State Tracking (VENUE_1 only) ─────────────────────────────
        self.v1_prev_bid_price = None
        self.v1_prev_bid_size = 0
        self.v1_prev_ask_price = None
        self.v1_prev_ask_size = 0
        self.ofi_threshold = 200  # Lower base threshold to take the first trade easier
        self.inventory_penalty = 50  # OFI threshold increases by 50 per share held
        self.max_acceptable_spread_v2 = 10  # Max spread (pips) on VENUE_2 to allow sniping

        # ── Position limits ───────────────────────────────────────────────
        self.max_position = 20  # Maximum absolute position before blocking new snipes

        # ── Inventory unloading ───────────────────────────────────────────
        self.inventory_unload_qty = 2  # Size to unwind per tick during mean reversion

        # ── Inventory tracking ────────────────────────────────────────────
        self.position = 0  # Current position in self.symbol (updated each tick)

        # ── Debug ─────────────────────────────────────────────────────────
        self._tick_count = 0

    # -----------------------------------------------------------------------
    # Safe order ID extraction
    # -----------------------------------------------------------------------
    @staticmethod
    def _extract_order_id(res):
        """Safely extract order_id from place_order response."""
        if res is None:
            return None
        if isinstance(res, dict):
            return res.get("order_id")
        return getattr(res, "order_id", res)

    # -----------------------------------------------------------------------
    # Safe cancellation with finally-block cleanup
    # -----------------------------------------------------------------------
    def _safe_cancel(self, api, attr_name):
        """Cancel an order and clear the ID attribute in a finally block.

        Args:
            api: The SyncApiProxy from LiquidityEngine.
            attr_name: Name of the attribute holding the order ID (e.g. 'spoof_order_id').
        """
        order_id = getattr(self, attr_name, None)
        if order_id is None:
            return
        try:
            api.cancel_order(order_id)
        except Exception:
            pass
        finally:
            setattr(self, attr_name, None)

    def _cancel_arb_orders(self, api):
        """Cancel both arbitrage leg orders safely."""
        self._safe_cancel(api, "bid_order_id")
        self._safe_cancel(api, "ask_order_id")

    # -----------------------------------------------------------------------
    # Price extraction from order book snapshot
    # -----------------------------------------------------------------------
    @staticmethod
    def _extract_best_prices(book):
        """Extract best_bid and best_ask from an order book snapshot."""
        best_bid = book.get("best_bid")
        best_ask = book.get("best_ask")

        if best_bid is None or best_ask is None:
            snapshot = book.get("snapshot", {})
            bids = snapshot.get("bids", [])
            asks = snapshot.get("asks", [])
            if bids and best_bid is None:
                best_bid = bids[0].get("price") if isinstance(bids[0], dict) else bids[0]
            if asks and best_ask is None:
                best_ask = asks[0].get("price") if isinstance(asks[0], dict) else asks[0]

        return best_bid, best_ask

    # -----------------------------------------------------------------------
    # Size extraction from order book snapshot
    # -----------------------------------------------------------------------
    @staticmethod
    def _extract_best_sizes(book, best_bid, best_ask):
        """Return (best_bid_size, best_ask_size) from snapshot.

        Looks up the quantity at the best bid and best ask price levels
        from the order book snapshot.
        """
        snapshot = book.get("snapshot", {})
        bids = snapshot.get("bids", [])
        asks = snapshot.get("asks", [])

        best_bid_size = 0
        best_ask_size = 0

        for bid_entry in bids:
            if bid_entry.get("price") == best_bid:
                best_bid_size = bid_entry.get("quantity", 0)
                break

        for ask_entry in asks:
            if ask_entry.get("price") == best_ask:
                best_ask_size = ask_entry.get("quantity", 0)
                break

        return best_bid_size, best_ask_size

    # -----------------------------------------------------------------------
    # OFI Calculation for VENUE_1
    # -----------------------------------------------------------------------
    def _calculate_ofi_v1(self, best_bid, best_bid_size, best_ask, best_ask_size):
        """Compute Order Flow Imbalance and update previous state trackers.

        OFI measures the net pressure on the top of the book:
        - Positive OFI: bid side is strengthening (price likely to go UP)
        - Negative OFI: ask side is strengthening (price likely to go DOWN)

        Returns:
            int: The OFI value (e_bid - e_ask)
        """
        if self.v1_prev_bid_price is None:
            # First tick — initialize trackers, no OFI signal yet
            self.v1_prev_bid_price = best_bid
            self.v1_prev_bid_size = best_bid_size
            self.v1_prev_ask_price = best_ask
            self.v1_prev_ask_size = best_ask_size
            return 0

        # Bid-side contribution (e_bid)
        if best_bid > self.v1_prev_bid_price:
            # Bid price moved up — new buyers are aggressive
            e_bid = best_bid_size
        elif best_bid == self.v1_prev_bid_price:
            # Bid price unchanged — measure size delta
            e_bid = best_bid_size - self.v1_prev_bid_size
        else:
            # Bid price moved down — no bullish pressure
            e_bid = 0

        # Ask-side contribution (e_ask)
        if best_ask < self.v1_prev_ask_price:
            # Ask price moved down — new sellers are aggressive
            e_ask = best_ask_size
        elif best_ask == self.v1_prev_ask_price:
            # Ask price unchanged — measure size delta
            e_ask = best_ask_size - self.v1_prev_ask_size
        else:
            # Ask price moved up — no bearish pressure
            e_ask = 0

        ofi = e_bid - e_ask

        # Update previous state for next tick
        self.v1_prev_bid_price = best_bid
        self.v1_prev_bid_size = best_bid_size
        self.v1_prev_ask_price = best_ask
        self.v1_prev_ask_size = best_ask_size

        return ofi

    # -----------------------------------------------------------------------
    # Fee calculation
    # -----------------------------------------------------------------------
    def _fee_cost(self, price, fee_bps):
        """Calculate the fee cost for a given price in bps."""
        return (price * fee_bps) // 10000

    # -----------------------------------------------------------------------
    # State transition helper
    # -----------------------------------------------------------------------
    def _transition(self, new_state):
        """Log and execute a state transition."""
        old_state = self.state
        self.state = new_state
        print(f"[ARB BOT] Transitioning to {new_state} (was {old_state}, tick {self._tick_count})")

    # -----------------------------------------------------------------------
    # Main tick dispatcher — State Machine
    # -----------------------------------------------------------------------
    def on_tick(self, api):
        try:
            self._tick_count += 1

            # ── Inventory awareness ───────────────────────────────────────
            account = api.get_account()
            self.position = account.positions.get(self.symbol, 0)

            # ── PANIC UNLOAD: abs(position) > 20 → emergency flatten ──────
            if abs(self.position) > 20 and self.state != "PANIC_UNLOAD":
                print(f"[ARB BOT] PANIC: position={self.position}, forcing unload "
                      f"(tick {self._tick_count})")
                # Cancel everything before entering panic
                self._cancel_arb_orders(api)
                self._safe_cancel(api, "spoof_order_id")
                self._transition("PANIC_UNLOAD")

            if self.state == "SNIPING":
                self._state_ofi_sniping(api)
            elif self.state == "SPOOFING_UP":
                self._state_spoofing_up(api)
            elif self.state == "WAITING_FOR_LAG":
                self._state_waiting_for_lag(api)
            elif self.state == "DUMPING":
                self._state_dumping(api)
            elif self.state == "PANIC_UNLOAD":
                self._state_panic_unload(api)
        except Exception as e:
            import traceback
            print(f"[ARB BOT] Crash on tick {self._tick_count} in state {self.state}: {e}")
            traceback.print_exc()
            # Emergency: cancel all orders and return to SNIPING
            self._cancel_arb_orders(api)
            self._safe_cancel(api, "spoof_order_id")
            self.state = "SNIPING"

    # -----------------------------------------------------------------------
    # STATE: SNIPING — Strategy A: OFI-Sniper (Primary Active Strategy)
    # -----------------------------------------------------------------------
    def _state_ofi_sniping(self, api):
        """OFI-Sniper: predict VENUE_1 moves via OFI, snipe VENUE_2 preemptively.

        Decision hierarchy:
        1. If OFI > threshold → Buy V2 (predicting V1 UP)
        2. If OFI < -threshold → Sell V2 (predicting V1 DOWN)
        3. If no signal but has position → Inventory Unload on V1
        4. If no signal and flat → Fall back to Spoofing
        """
        # Cancel lingering orders from previous tick
        self._cancel_arb_orders(api)

        # Get order books from both venues
        book_v1 = api.get_order_book(symbol=self.symbol, venue="VENUE_1")
        book_v2 = api.get_order_book(symbol=self.symbol, venue="VENUE_2")

        best_bid_v1, best_ask_v1 = self._extract_best_prices(book_v1)
        best_bid_v2, best_ask_v2 = self._extract_best_prices(book_v2)

        # Need VENUE_1 data for OFI calculation
        if None in (best_bid_v1, best_ask_v1):
            # No VENUE_1 data — try inventory unloading if we have V2 data
            if self.position != 0 and best_bid_v2 is not None and best_ask_v2 is not None:
                self._state_inventory_unload(api)
            elif abs(self.position) <= 5:
                self._transition("SPOOFING_UP")
            return

        # Extract sizes and compute OFI
        best_bid_size_v1, best_ask_size_v1 = self._extract_best_sizes(
            book_v1, best_bid_v1, best_ask_v1
        )
        ofi_v1 = self._calculate_ofi_v1(
            best_bid_v1, best_bid_size_v1, best_ask_v1, best_ask_size_v1
        )

        # ── Dynamic OFI Threshold based on current inventory risk ────────
        dynamic_buy_threshold = self.ofi_threshold + (max(0, self.position) * self.inventory_penalty)
        dynamic_sell_threshold = self.ofi_threshold + (abs(min(0, self.position)) * self.inventory_penalty)

        # Compute VENUE_2 spread for the spread guard
        v2_spread = (best_ask_v2 - best_bid_v2) if (best_bid_v2 is not None and best_ask_v2 is not None) else 999

        # ── OFI predicts UPWARD movement on V1 → Buy V2 now ─────────────
        if ofi_v1 > dynamic_buy_threshold and v2_spread <= self.max_acceptable_spread_v2:
            if self.position < self.max_position and best_ask_v2 is not None:
                res = api.place_order(
                    side="BUY",
                    price=best_ask_v2,
                    quantity=self.order_size,
                    symbol=self.symbol,
                    venue="VENUE_2",
                )
                self.bid_order_id = self._extract_order_id(res)
                print(f"[ARB BOT] OFI-SNIPE BUY: ofi={ofi_v1} > {dynamic_buy_threshold} (dyn), "
                      f"buying V2@{best_ask_v2} (tick {self._tick_count})")
                # Stay in SNIPING — check again next tick

        # ── OFI predicts DOWNWARD movement on V1 → Sell V2 now ──────────
        elif ofi_v1 < -dynamic_sell_threshold and v2_spread <= self.max_acceptable_spread_v2:
            if self.position > -self.max_position and best_bid_v2 is not None:
                res = api.place_order(
                    side="SELL",
                    price=best_bid_v2,
                    quantity=self.order_size,
                    symbol=self.symbol,
                    venue="VENUE_2",
                )
                self.ask_order_id = self._extract_order_id(res)
                print(f"[ARB BOT] OFI-SNIPE SELL: ofi={ofi_v1} < -{dynamic_sell_threshold} (dyn), "
                      f"selling V2@{best_bid_v2} (tick {self._tick_count})")
                # Stay in SNIPING

        # ── No OFI signal: try inventory unloading or fall back to spoofing ──
        else:
            if self.position != 0:
                # Have position but no signal — try to unload on VENUE_1
                if best_bid_v1 is not None and best_ask_v1 is not None:
                    self._state_inventory_unload(api)
                else:
                    # Can't unload without V1 data, try spoofing if flat enough
                    if abs(self.position) <= 5:
                        self._transition("SPOOFING_UP")
            else:
                # Flat position, no signal — manufacture opportunity via spoofing
                if abs(self.position) <= 5:
                    self._transition("SPOOFING_UP")
                # else: stay in SNIPING, wait for passive arb to flatten position

    # -----------------------------------------------------------------------
    # STATE: INVENTORY_UNLOAD — Queue-Jumping (Pennying) Unwinding
    # -----------------------------------------------------------------------
    def _state_inventory_unload(self, api):
        """Aggressively unwind inventory on VENUE_1 by pennying the MMs.

        Improves the price by 1 tick to guarantee Top-of-Book execution,
        sacrificing 1 tick of profit for guaranteed fill speed before the
        market reverses.
        """
        self._cancel_arb_orders(api)

        book_v1 = api.get_order_book(symbol=self.symbol, venue="VENUE_1")
        best_bid_v1, best_ask_v1 = self._extract_best_prices(book_v1)

        if best_bid_v1 is None or best_ask_v1 is None:
            return  # Can't unload without VENUE_1 book

        spread_v1 = best_ask_v1 - best_bid_v1

        if self.position > 0:
            # Long position → Penny the ask (sell 1 tick cheaper than MMs) if spread > 1
            unload_price = best_ask_v1 - 1 if spread_v1 > 1 else best_ask_v1
            qty = min(self.inventory_unload_qty, self.position)
            res = api.place_order(
                side="SELL",
                price=unload_price,
                quantity=qty,
                symbol=self.symbol,
                venue="VENUE_1",
            )
            self.ask_order_id = self._extract_order_id(res)
            print(f"[ARB BOT] PENNY UNLOAD SELL: {qty}@{unload_price} on VENUE_1 "
                  f"(pos={self.position}, tick {self._tick_count})")

        elif self.position < 0:
            # Short position → Penny the bid (buy 1 tick higher than MMs) if spread > 1
            unload_price = best_bid_v1 + 1 if spread_v1 > 1 else best_bid_v1
            qty = min(self.inventory_unload_qty, abs(self.position))
            res = api.place_order(
                side="BUY",
                price=unload_price,
                quantity=qty,
                symbol=self.symbol,
                venue="VENUE_1",
            )
            self.bid_order_id = self._extract_order_id(res)
            print(f"[ARB BOT] PENNY UNLOAD BUY: {qty}@{unload_price} on VENUE_1 "
                  f"(pos={self.position}, tick {self._tick_count})")

    # -----------------------------------------------------------------------
    # STATE: SPOOFING_UP — Place massive fake BUY on VENUE_1
    # -----------------------------------------------------------------------
    def _state_spoofing_up(self, api):
        """Place a massive fake BUY limit order on VENUE_1 at best_bid + 1
        to artificially inflate the mid-price. The LaggingMM reads VENUE_1
        and will eventually quote higher on VENUE_2.
        """
        # Cancel any previous spoof order (shouldn't exist, but be safe)
        self._safe_cancel(api, "spoof_order_id")

        book_v1 = api.get_order_book(symbol=self.symbol, venue="VENUE_1")
        best_bid_v1, best_ask_v1 = self._extract_best_prices(book_v1)

        if best_bid_v1 is None:
            # No VENUE_1 data — can't spoof, go back to sniping
            print(f"[ARB BOT] No VENUE_1 data, aborting spoof (tick {self._tick_count})")
            self._transition("SNIPING")
            return

        # Place massive BUY 1 pip above current best bid
        self.spoof_price = best_bid_v1 + 1
        spoof_res = api.place_order(
            side="BUY",
            price=self.spoof_price,
            quantity=self.spoof_size,
            symbol=self.symbol,
            venue="VENUE_1",
        )
        self.spoof_order_id = self._extract_order_id(spoof_res)

        print(f"[ARB BOT] SPOOF placed: BUY {self.spoof_size}@{self.spoof_price} on VENUE_1 "
              f"(tick {self._tick_count})")

        # Reset lag counter and transition
        self.lag_ticks_waited = 0
        self._transition("WAITING_FOR_LAG")

    # -----------------------------------------------------------------------
    # STATE: WAITING_FOR_LAG — Count ticks until LaggingMM absorbs fake price
    # -----------------------------------------------------------------------
    def _state_waiting_for_lag(self, api):
        """Wait patiently for the LaggingMM's 15-tick deque to fill with
        the inflated VENUE_1 mid-price. Once the lag window expires,
        transition to DUMPING.
        """
        self.lag_ticks_waited += 1

        # Check if our spoof order is still alive (it might have been filled)
        if self.spoof_order_id is None:
            # Spoof was filled or cancelled externally — abort the trap
            print(f"[ARB BOT] Spoof order gone after {self.lag_ticks_waited} ticks, "
                  f"aborting trap (tick {self._tick_count})")
            self._transition("SNIPING")
            return

        if self.lag_ticks_waited >= self.lag_window:
            print(f"[ARB BOT] Lag window expired ({self.lag_ticks_waited} ticks), "
                  f"LaggingMM should now be quoting high on VENUE_2 (tick {self._tick_count})")
            self._transition("DUMPING")
        # Otherwise, keep waiting — do nothing this tick

    # -----------------------------------------------------------------------
    # STATE: DUMPING — Sell into elevated VENUE_2 bids, cancel spoof
    # -----------------------------------------------------------------------
    def _state_dumping(self, api):
        """The LaggingMM on VENUE_2 has just updated its quotes higher,
        believing the fake VENUE_1 price is real. Aggressively SELL into
        the LaggingMM's elevated bids on VENUE_2, then cancel the spoof
        order on VENUE_1 before anyone can fill it.
        """
        # 1. Read VENUE_2 book — the LaggingMM should be quoting high
        book_v2 = api.get_order_book(symbol=self.symbol, venue="VENUE_2")
        best_bid_v2, best_ask_v2 = self._extract_best_prices(book_v2)

        if best_bid_v2 is not None:
            # 2. Aggressively SELL into the elevated bid on VENUE_2
            dump_res = api.place_order(
                side="SELL",
                price=best_bid_v2,
                quantity=self.arb_size,
                symbol=self.symbol,
                venue="VENUE_2",
            )
            dump_id = self._extract_order_id(dump_res)
            print(f"[ARB BOT] DUMP: SELL {self.arb_size}@{best_bid_v2} on VENUE_2 "
                  f"(tick {self._tick_count})")
        else:
            print(f"[ARB BOT] DUMP: No VENUE_2 bid to sell into (tick {self._tick_count})")

        # 3. Cancel the spoof order on VENUE_1 — CRITICAL: use finally block
        self._safe_cancel(api, "spoof_order_id")
        print(f"[ARB BOT] Spoof cancelled, trap complete (tick {self._tick_count})")

        # 4. Return to SNIPING
        self._transition("SNIPING")

    # -----------------------------------------------------------------------
    # STATE: PANIC_UNLOAD — Emergency position flatten
    # -----------------------------------------------------------------------
    def _state_panic_unload(self, api):
        """Emergency state: aggressively flatten position by crossing the
        spread on whichever venue offers the best price. Dumps 5 shares
        per tick until abs(position) <= 5.
        """
        # Cancel all active orders first
        self._cancel_arb_orders(api)
        self._safe_cancel(api, "spoof_order_id")

        # Re-read position (may have changed from fills)
        account = api.get_account()
        self.position = account.positions.get(self.symbol, 0)

        if abs(self.position) <= 5:
            print(f"[ARB BOT] PANIC resolved: position={self.position}, returning to SNIPING "
                  f"(tick {self._tick_count})")
            self._transition("SNIPING")
            return

        # Get books from both venues to find best exit price
        book_v1 = api.get_order_book(symbol=self.symbol, venue="VENUE_1")
        book_v2 = api.get_order_book(symbol=self.symbol, venue="VENUE_2")

        best_bid_v1, best_ask_v1 = self._extract_best_prices(book_v1)
        best_bid_v2, best_ask_v2 = self._extract_best_prices(book_v2)

        dump_qty = 5  # Shares to dump per tick

        if self.position > 0:
            # Need to SELL — pick the venue with the highest bid
            candidates = []
            if best_bid_v1 is not None:
                candidates.append(("VENUE_1", best_bid_v1))
            if best_bid_v2 is not None:
                candidates.append(("VENUE_2", best_bid_v2))

            if not candidates:
                print(f"[ARB BOT] PANIC: No bids on either venue, waiting (tick {self._tick_count})")
                return

            venue, price = max(candidates, key=lambda x: x[1])
            qty = min(dump_qty, self.position)
            res = api.place_order(
                side="SELL",
                price=price,
                quantity=qty,
                symbol=self.symbol,
                venue=venue,
            )
            order_id = self._extract_order_id(res)
            print(f"[ARB BOT] PANIC SELL: {qty}@{price} on {venue}, "
                  f"position={self.position} (tick {self._tick_count})")

        elif self.position < 0:
            # Need to BUY — pick the venue with the lowest ask
            candidates = []
            if best_ask_v1 is not None:
                candidates.append(("VENUE_1", best_ask_v1))
            if best_ask_v2 is not None:
                candidates.append(("VENUE_2", best_ask_v2))

            if not candidates:
                print(f"[ARB BOT] PANIC: No asks on either venue, waiting (tick {self._tick_count})")
                return

            venue, price = min(candidates, key=lambda x: x[1])
            qty = min(dump_qty, abs(self.position))
            res = api.place_order(
                side="BUY",
                price=price,
                quantity=qty,
                symbol=self.symbol,
                venue=venue,
            )
            order_id = self._extract_order_id(res)
            print(f"[ARB BOT] PANIC BUY: {qty}@{price} on {venue}, "
                  f"position={self.position} (tick {self._tick_count})")


def create_bot():
    return SpatialArbitrageBot()