from bot_arena_exchange.domain.bots import generate_pareto_size

"""Statistical Arbitrage Bot — Passive Maker-Taker Arbitrage.

Monitors mid-price divergence between VENUE_1 and VENUE_2. When the
divergence exceeds a threshold, the bot posts PASSIVE limit orders
INSIDE the wide spreads (pennying the best bid/ask) to force convergence
and absorb noise from defensive Market Maker quoting.

Unlike the SpatialArbitrageBot (Apex Predator), this bot is a pure
statistical arbitrageur — it provides passive liquidity inside the
spread rather than crossing it, acting as a "rubber band" that tethers
the two venues together.
"""


class StatArbBot:
    """Passive statistical arbitrageur that syncs VENUE_1 and VENUE_2 prices."""

    def __init__(self):
        self.symbol = "AAPL"
        self.trader_id = None  # Set by the factory in fastapi_app.py

        # ── Configuration ─────────────────────────────────────────────────
        self.max_position = 1000   # Absolute position limit per side
        self.arb_threshold = 3.0   # Ticks of mid-price divergence to activate

        # ── Order tracking for both venues ────────────────────────────────
        self.v1_order_id = None
        self.v2_order_id = None

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
        """Cancel an order and clear the ID attribute in a finally block."""
        order_id = getattr(self, attr_name, None)
        if order_id is None:
            return
        try:
            api.cancel_order(order_id)
        except Exception:
            import traceback
            print(f"[STAT_ARB] {self.trader_id if hasattr(self, 'trader_id') else '?'} "
                  f"failed to cancel {attr_name} {order_id}")
            traceback.print_exc()
        finally:
            setattr(self, attr_name, None)

    def _cancel_all_orders(self, api):
        """Cancel orders on both venues safely."""
        self._safe_cancel(api, "v1_order_id")
        self._safe_cancel(api, "v2_order_id")

    # -----------------------------------------------------------------------
    # Price extraction from order book snapshot
    # -----------------------------------------------------------------------
    @staticmethod
    def _extract_best_prices(book):
        """Extract best_bid, best_ask, best_bid_qty, best_ask_qty from order book."""
        best_bid = book.get("best_bid")
        best_ask = book.get("best_ask")
        best_bid_qty = 0
        best_ask_qty = 0

        snapshot = book.get("snapshot", {})
        bids = snapshot.get("bids", [])
        asks = snapshot.get("asks", [])

        if bids:
            if best_bid is None:
                best_bid = bids[0].get("price") if isinstance(bids[0], dict) else bids[0]
            best_bid_qty = bids[0].get("quantity", 0) if isinstance(bids[0], dict) else 0
        if asks:
            if best_ask is None:
                best_ask = asks[0].get("price") if isinstance(asks[0], dict) else asks[0]
            best_ask_qty = asks[0].get("quantity", 0) if isinstance(asks[0], dict) else 0

        return best_bid, best_ask, best_bid_qty, best_ask_qty

    # -----------------------------------------------------------------------
    # Main tick dispatcher
    # -----------------------------------------------------------------------
    def on_tick(self, api):
        try:
            self._tick_count += 1

            # Cancel any lingering orders from previous tick
            self._cancel_all_orders(api)

            # Get order books from both venues
            book_v1 = api.get_order_book(symbol=self.symbol, venue="VENUE_1")
            book_v2 = api.get_order_book(symbol=self.symbol, venue="VENUE_2")

            best_bid_v1, best_ask_v1, _, _ = self._extract_best_prices(book_v1)
            best_bid_v2, best_ask_v2, _, _ = self._extract_best_prices(book_v2)

            # Need full two-sided liquidity on both venues
            if None in (best_bid_v1, best_ask_v1, best_bid_v2, best_ask_v2):
                return

            # ── Compute mid-prices ────────────────────────────────────────
            mid_v1 = (best_bid_v1 + best_ask_v1) / 2
            mid_v2 = (best_bid_v2 + best_ask_v2) / 2
            mid_diff = mid_v1 - mid_v2

            # Get current position for inventory guard
            account = api.get_account()
            self.position = account.positions.get(self.symbol, 0) if account else 0

            # ── Cubic Inventory Skew: flat center for tight liquidity, ──
            # violent reaction near capacity to survive toxic structural flow
            # Long position → negative skew → shifts quotes DOWN (encourages selling)
            # Short position → positive skew → shifts quotes UP (encourages buying)
            norm_pos = self.position / self.max_position
            max_skew_ticks = 6
            inventory_skew = round(-(norm_pos ** 3) * max_skew_ticks)

            # ── Passive Maker-Taker Arbitrage ─────────────────────────────
            # V1 is trading HIGHER than V2 → Sell V1, Buy V2
            if mid_diff > self.arb_threshold:
                if self.position < self.max_position:
                    # Post passive BUY on V2, improving the bid by 1 (penny)
                    spread_v2 = best_ask_v2 - best_bid_v2
                    base_buy = best_bid_v2 + 1 if spread_v2 > 1 else best_bid_v2
                    passive_buy_price = base_buy + inventory_skew
                    v2_res = api.place_order(
                        side="BUY",
                        price=passive_buy_price,
                        quantity=generate_pareto_size(base_size=100, alpha=3.0, max_limit=400),
                        symbol=self.symbol,
                        venue="VENUE_2",
                    )
                    self.v2_order_id = self._extract_order_id(v2_res)

                if self.position > -self.max_position:
                    # Post passive SELL on V1, improving the ask by 1 (penny)
                    spread_v1 = best_ask_v1 - best_bid_v1
                    base_sell = best_ask_v1 - 1 if spread_v1 > 1 else best_ask_v1
                    passive_sell_price = base_sell + inventory_skew
                    v1_res = api.place_order(
                        side="SELL",
                        price=passive_sell_price,
                        quantity=generate_pareto_size(base_size=100, alpha=3.0, max_limit=400),
                        symbol=self.symbol,
                        venue="VENUE_1",
                    )
                    self.v1_order_id = self._extract_order_id(v1_res)

                if self.v1_order_id or self.v2_order_id:
                    print(f"[STAT_ARB] PASSIVE: Sell V1@{passive_sell_price} "
                          f"/ Buy V2@{passive_buy_price} "
                          f"mid_diff={mid_diff:.1f} skew={inventory_skew} (tick {self._tick_count})")

            # V2 is trading HIGHER than V1 → Buy V1, Sell V2
            elif mid_diff < -self.arb_threshold:
                if self.position < self.max_position:
                    # Post passive BUY on V1, improving the bid by 1 (penny)
                    spread_v1 = best_ask_v1 - best_bid_v1
                    base_buy = best_bid_v1 + 1 if spread_v1 > 1 else best_bid_v1
                    passive_buy_price = base_buy + inventory_skew
                    v1_res = api.place_order(
                        side="BUY",
                        price=passive_buy_price,
                        quantity=generate_pareto_size(base_size=100, alpha=3.0, max_limit=400),
                        symbol=self.symbol,
                        venue="VENUE_1",
                    )
                    self.v1_order_id = self._extract_order_id(v1_res)

                if self.position > -self.max_position:
                    # Post passive SELL on V2, improving the ask by 1 (penny)
                    spread_v2 = best_ask_v2 - best_bid_v2
                    base_sell = best_ask_v2 - 1 if spread_v2 > 1 else best_ask_v2
                    passive_sell_price = base_sell + inventory_skew
                    v2_res = api.place_order(
                        side="SELL",
                        price=passive_sell_price,
                        quantity=generate_pareto_size(base_size=100, alpha=3.0, max_limit=400),
                        symbol=self.symbol,
                        venue="VENUE_2",
                    )
                    self.v2_order_id = self._extract_order_id(v2_res)

                if self.v1_order_id or self.v2_order_id:
                    print(f"[STAT_ARB] PASSIVE: Buy V1@{passive_buy_price} "
                          f"/ Sell V2@{passive_sell_price} "
                          f"mid_diff={mid_diff:.1f} skew={inventory_skew} (tick {self._tick_count})")

        except Exception as e:
            import traceback
            print(f"[STAT_ARB] Crash on tick {self._tick_count}: {e}")
            traceback.print_exc()
            # Emergency: cancel all orders
            self._cancel_all_orders(api)


def create_bot():
    return StatArbBot()