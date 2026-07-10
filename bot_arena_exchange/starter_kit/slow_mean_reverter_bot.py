from bot_arena_exchange.domain.bots import generate_pareto_size


class SlowMeanReverterBot:
    """Cross-venue mean-reversion arbitrage bot with cooldown.

    Scans VENUE_1 and VENUE_2 order books for passive cross-venue arbitrage
    opportunities. When Bid_V1 > Ask_V2 + fees + profit_threshold, places
    simultaneous orders: BUY on VENUE_2 at best_ask and SELL on VENUE_1 at
    best_bid. After acting, enters a cooldown period to avoid overtrading.

    Fee calculation uses floating-point division to preserve precision before
    final comparison, preventing premature integer truncation that could cause
    the bot to execute unprofitable arbitrage.

    Operates across both venues: reads VENUE_1 and VENUE_2, places orders on
    whichever venue offers the best execution for each leg.
    """

    def __init__(self):
        self.symbol = "AAPL"
        self.trader_id = None            # Set by the factory in fastapi_app.py

        # ── Fee configuration (bps) ────────────────────────────────────
        self.fee_v1_bps = 0              # VENUE_1: 0 bps
        self.fee_v2_bps = 15             # VENUE_2: 15 bps

        # ── Strategy parameters ────────────────────────────────────────
        self.profit_threshold = 5        # Minimum net profit in pips to act
        self.cooldown = 0                # Remaining cooldown ticks
        self.cooldown_duration = 5       # Ticks to wait after each trade

        # ── Active order tracking ──────────────────────────────────────
        self.bid_order_id = None
        self.ask_order_id = None

    def _cancel_existing_orders(self, api):
        """Cancel both active orders before placing new ones."""
        if self.bid_order_id is not None:
            try:
                api.cancel_order(self.bid_order_id)
            except Exception:
                pass
            self.bid_order_id = None

        if self.ask_order_id is not None:
            try:
                api.cancel_order(self.ask_order_id)
            except Exception:
                pass
            self.ask_order_id = None

    def _extract_best_prices(self, book):
        """Extract best_bid and best_ask from the order book snapshot."""
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

    def _compute_fee_pips(self, price, fee_bps):
        """Compute fee cost in pips using floating-point division for precision.

        The result is kept as a float to avoid premature truncation. The final
        comparison uses the float value directly against the profit threshold.
        """
        return (price * fee_bps) / 10000.0

    def on_tick(self, api):
        # 1. If in cooldown, decrement and skip
        if self.cooldown > 0:
            self.cooldown -= 1
            return

        # 2. Cancel any previous orders
        self._cancel_existing_orders(api)

        # 3. Read order books from both venues
        book_v1 = api.get_order_book(symbol=self.symbol, venue="VENUE_1")
        book_v2 = api.get_order_book(symbol=self.symbol, venue="VENUE_2")

        best_bid_v1, best_ask_v1 = self._extract_best_prices(book_v1)
        best_bid_v2, best_ask_v2 = self._extract_best_prices(book_v2)

        # Both venues must have liquidity
        if best_bid_v1 is None or best_ask_v1 is None:
            return
        if best_bid_v2 is None or best_ask_v2 is None:
            return

        # ── Case A: VENUE_1 bid > VENUE_2 ask → buy V2, sell V1 ──────
        # Gross profit = best_bid_v1 - best_ask_v2
        # Fee cost = fee on the buy leg (V2) + fee on the sell leg (V1)
        gross_profit_a = best_bid_v1 - best_ask_v2
        fee_cost_a = (
            self._compute_fee_pips(best_ask_v2, self.fee_v2_bps)   # Buy on V2
            + self._compute_fee_pips(best_bid_v1, self.fee_v1_bps)  # Sell on V1
        )
        net_profit_a = gross_profit_a - fee_cost_a

        # ── Case B: VENUE_2 bid > VENUE_1 ask → buy V1, sell V2 ──────
        gross_profit_b = best_bid_v2 - best_ask_v1
        fee_cost_b = (
            self._compute_fee_pips(best_ask_v1, self.fee_v1_bps)   # Buy on V1
            + self._compute_fee_pips(best_bid_v2, self.fee_v2_bps)  # Sell on V2
        )
        net_profit_b = gross_profit_b - fee_cost_b

        # 4. Execute the best opportunity if it exceeds the threshold
        if net_profit_a >= self.profit_threshold and net_profit_a >= net_profit_b:
            # Arbitrage A: buy cheap on V2, sell expensive on V1
            qty = generate_pareto_size(base_size=10, alpha=2.5, max_limit=200)
            bid_res = api.place_order(
                side="BUY",
                price=best_ask_v2,
                quantity=qty,
                symbol=self.symbol,
                venue="VENUE_2",
            )
            ask_res = api.place_order(
                side="SELL",
                price=best_bid_v1,
                quantity=qty,
                symbol=self.symbol,
                venue="VENUE_1",
            )
            self.bid_order_id = bid_res.get("order_id") if isinstance(bid_res, dict) else getattr(bid_res, "order_id", bid_res)
            self.ask_order_id = ask_res.get("order_id") if isinstance(ask_res, dict) else getattr(ask_res, "order_id", ask_res)
            self.cooldown = self.cooldown_duration

        elif net_profit_b >= self.profit_threshold:
            # Arbitrage B: buy cheap on V1, sell expensive on V2
            qty = generate_pareto_size(base_size=10, alpha=2.5, max_limit=200)
            bid_res = api.place_order(
                side="BUY",
                price=best_ask_v1,
                quantity=qty,
                symbol=self.symbol,
                venue="VENUE_1",
            )
            ask_res = api.place_order(
                side="SELL",
                price=best_bid_v2,
                quantity=qty,
                symbol=self.symbol,
                venue="VENUE_2",
            )
            self.bid_order_id = bid_res.get("order_id") if isinstance(bid_res, dict) else getattr(bid_res, "order_id", bid_res)
            self.ask_order_id = ask_res.get("order_id") if isinstance(ask_res, dict) else getattr(ask_res, "order_id", ask_res)
            self.cooldown = self.cooldown_duration


def create_bot():
    return SlowMeanReverterBot()