import pytest

from bot_arena_exchange.domain.order_book import OrderBook, WashTradeError


class TestOrderBookMassiveTestSuite:
    def setup_method(self, method):
        """Executes before each test to provide a clean state."""
        self.book = OrderBook()

    # ==========================================
    # GROUP 1: VALIDATIONS AND DATA TYPES (12 tests)
    # ==========================================

    def test_place_order_extreme_high_price(self):
        id1 = self.book.place_order("BUY", 999999999999, 10, "trader_rich")
        assert id1 is not None
        assert self.book.best_bid() == 999999999999

    def test_place_order_extreme_high_quantity(self):
        id1 = self.book.place_order("SELL", 100, 999999999999, "trader_whale")
        assert id1 is not None
        assert self.book.get_snapshot()["asks"][0]["quantity"] == 999999999999

    def test_place_order_float_quantity_rejected(self):
        with pytest.raises((ValueError, TypeError)):
            self.book.place_order("BUY", 100, 10.5, "trader1")

    def test_place_order_none_trader_id_rejected(self):
        with pytest.raises(ValueError):
            self.book.place_order("BUY", 100, 10, None)

    def test_place_order_empty_trader_id_rejected(self):
        with pytest.raises(ValueError):
            self.book.place_order("BUY", 100, 10, "")

    def test_place_order_float_price_rejected(self):
        with pytest.raises(ValueError):
            self.book.place_order("BUY", 100.5, 10, "trader1")

    def test_place_order_invalid_side_casing(self):
        with pytest.raises(ValueError):
            self.book.place_order("buy", 100, 10, "trader1")

    def test_place_order_special_chars_trader_id(self):
        id1 = self.book.place_order("BUY", 100, 10, "trader_@#_$!")
        assert id1 is not None

    def test_price_must_be_positive_number(self):
        with pytest.raises(ValueError):
            self.book.place_order("SELL", -10, 10, "trader1")

    def test_ioc_order_cancels_unfilled_remainder(self):
        self.book.place_order("BUY", 100, 5, "buyer")
        id_sell = self.book.place_order("SELL", 100, 10, "seller", tif="IOC")
        assert self.book.get_order_status(id_sell) == "cancelled"
        assert len(self.book.get_trades()) == 1
        assert self.book.best_bid() is None

    def test_fok_order_rejected_when_not_fully_fillable(self):
        self.book.place_order("BUY", 100, 5, "buyer")
        id_sell = self.book.place_order("SELL", 100, 10, "seller", tif="FOK")
        assert self.book.get_order_status(id_sell) == "cancelled"
        assert len(self.book.get_trades()) == 0

    def test_get_order_status_after_fill_and_cancel(self):
        id_buy = self.book.place_order("BUY", 100, 5, "trader1")
        self.book.place_order("SELL", 100, 5, "trader2")
        assert self.book.get_order_status(id_buy) == "filled"
        id_sell = self.book.place_order("SELL", 101, 5, "trader3")
        self.book.cancel_order(id_sell)
        assert self.book.get_order_status(id_sell) == "cancelled"

    def test_trade_timestamps_increase_for_sequential_matches(self):
        self.book.place_order("SELL", 100, 2, "s1")
        self.book.place_order("SELL", 100, 2, "s2")
        self.book.place_order("BUY", 100, 4, "b1")
        trades = self.book.get_trades()
        assert trades[0]["timestamp"] < trades[1]["timestamp"]

    def test_trade_records_venue_from_order(self):
        self.book.place_order("SELL", 100, 5, "maker", venue="VENUE_A")
        self.book.place_order("BUY", 100, 5, "taker", venue="VENUE_A")
        trade = self.book.get_trades()[0]
        assert trade["venue"] == "VENUE_A"

    def test_dual_listing_uses_separate_books(self):
        venue_a = OrderBook()
        venue_b = OrderBook()

        venue_a.place_order("SELL", 100, 5, "maker_a", symbol="ABC", venue="VENUE_A")
        venue_b.place_order("SELL", 95, 5, "maker_b", symbol="ABC", venue="VENUE_B")

        venue_a.place_order("BUY", 100, 5, "taker_a", symbol="ABC", venue="VENUE_A")

        assert len(venue_a.get_trades()) == 1
        assert len(venue_b.get_trades()) == 0
        assert venue_a.get_trades()[0]["venue"] == "VENUE_A"
        assert venue_b.best_ask() == 95

    # ==========================================
    # GROUP 2: ADVANCED CANCELLATIONS (8 tests)
    # ==========================================

    def test_cancel_already_cancelled_order(self):
        id1 = self.book.place_order("BUY", 100, 10, "trader1")
        assert self.book.cancel_order(id1)
        assert not self.book.cancel_order(id1)

    def test_cancel_partially_filled_order(self):
        id_buy = self.book.place_order("BUY", 100, 10, "trader1")
        self.book.place_order("SELL", 100, 4, "trader2")
        assert self.book.cancel_order(id_buy)
        assert self.book.get_open_order_quantity(id_buy) == 0

    def test_cancel_order_after_full_execution(self):
        id_buy = self.book.place_order("BUY", 100, 10, "trader1")
        self.book.place_order("SELL", 100, 10, "trader2")
        assert not self.book.cancel_order(id_buy)

    def test_cancel_with_none_id(self):
        assert not self.book.cancel_order(None)

    def test_cancel_with_empty_string_id(self):
        assert not self.book.cancel_order("")

    def test_lazy_cancel_head_of_queue(self):
        id1 = self.book.place_order("BUY", 100, 10, "t1")
        self.book.place_order("BUY", 100, 10, "t2")
        self.book.cancel_order(id1)
        assert self.book.get_snapshot()["bids"][0]["quantity"] == 10

    def test_lazy_cancel_tail_of_queue(self):
        self.book.place_order("BUY", 100, 10, "t1")
        id2 = self.book.place_order("BUY", 100, 10, "t2")
        self.book.cancel_order(id2)
        assert self.book.get_snapshot()["bids"][0]["quantity"] == 10

    def test_lazy_cancel_entire_queue_removes_price_level(self):
        id1 = self.book.place_order("BUY", 100, 10, "t1")
        id2 = self.book.place_order("BUY", 100, 10, "t2")
        self.book.cancel_order(id1)
        self.book.cancel_order(id2)
        assert self.book.best_bid() is None

    # ==========================================
    # GROUP 3: SNAPSHOTS AND BOOK STATE (8 tests)
    # ==========================================

    def test_snapshot_empty_book(self):
        snap = self.book.get_snapshot()
        assert len(snap["bids"]) == 0
        assert len(snap["asks"]) == 0

    def test_snapshot_bids_sorted_descending(self):
        self.book.place_order("BUY", 90, 10, "t1")
        self.book.place_order("BUY", 110, 10, "t2")
        self.book.place_order("BUY", 100, 10, "t3")
        snap = self.book.get_snapshot()
        assert snap["bids"][0]["price"] == 110
        assert snap["bids"][1]["price"] == 100
        assert snap["bids"][2]["price"] == 90

    def test_snapshot_asks_sorted_ascending(self):
        self.book.place_order("SELL", 110, 10, "t1")
        self.book.place_order("SELL", 90, 10, "t2")
        self.book.place_order("SELL", 100, 10, "t3")
        snap = self.book.get_snapshot()
        assert snap["asks"][0]["price"] == 90
        assert snap["asks"][1]["price"] == 100
        assert snap["asks"][2]["price"] == 110

    def test_snapshot_excludes_cancelled_orders(self):
        id1 = self.book.place_order("BUY", 100, 15, "t1")
        self.book.cancel_order(id1)
        snap = self.book.get_snapshot()
        assert len(snap["bids"]) == 0

    def test_best_bid_empty_is_none(self):
        assert self.book.best_bid() is None

    def test_best_ask_empty_is_none(self):
        assert self.book.best_ask() is None

    def test_spread_calculation_normal(self):
        self.book.place_order("BUY", 100, 10, "t1")
        self.book.place_order("SELL", 105, 10, "t2")
        assert self.book.best_ask() - self.book.best_bid() == 5

    def test_spread_fails_or_none_if_one_side_empty(self):
        self.book.place_order("BUY", 100, 10, "t1")
        assert self.book.best_ask() is None

    # ==========================================
    # GROUP 4: PRIORITY AND MATCHING ENGINE (10 tests)
    # ==========================================

    def test_matching_large_sell_sweeps_bids(self):
        self.book.place_order("BUY", 100, 5, "b1")
        self.book.place_order("BUY", 95, 5, "b2")
        self.book.place_order("BUY", 90, 5, "b3")
        self.book.place_order("SELL", 80, 15, "s1")
        trades = self.book.get_trades()
        assert len(trades) == 3
        assert self.book.best_bid() is None

    def test_matching_large_buy_sweeps_asks(self):
        self.book.place_order("SELL", 100, 2, "s1")
        self.book.place_order("SELL", 105, 2, "s2")
        self.book.place_order("SELL", 110, 2, "s3")
        self.book.place_order("BUY", 120, 6, "b1")
        assert len(self.book.get_trades()) == 3
        assert self.book.best_ask() is None

    def test_matching_exact_quantity_multiple_levels(self):
        self.book.place_order("SELL", 100, 5, "s1")
        self.book.place_order("SELL", 100, 5, "s2")
        self.book.place_order("BUY", 105, 10, "b1")
        assert self.book.best_ask() is None
        assert self.book.best_bid() is None

    def test_matching_leaves_1_quantity_resting(self):
        self.book.place_order("SELL", 100, 5, "s1")
        self.book.place_order("BUY", 100, 6, "b1")
        assert self.book.best_bid() == 100
        assert self.book.get_snapshot()["bids"][0]["quantity"] == 1

    def test_execution_price_is_passive_maker_price(self):
        self.book.place_order("SELL", 50, 10, "maker")
        self.book.place_order("BUY", 100, 10, "taker")
        trade = self.book.get_trades()[0]
        assert trade["price"] == 50

    def test_execution_price_when_aggressor_crosses_deeply(self):
        self.book.place_order("SELL", 50, 5, "m1")
        self.book.place_order("SELL", 60, 5, "m2")
        self.book.place_order("BUY", 100, 10, "taker")
        trades = self.book.get_trades()
        assert trades[0]["price"] == 50
        assert trades[1]["price"] == 60

    def test_best_bid_updates_when_top_level_consumed(self):
        self.book.place_order("BUY", 100, 5, "b1")
        self.book.place_order("BUY", 90, 5, "b2")
        self.book.place_order("SELL", 100, 5, "s1")
        assert self.book.best_bid() == 90

    def test_best_ask_updates_when_top_level_consumed(self):
        self.book.place_order("SELL", 100, 5, "s1")
        self.book.place_order("SELL", 110, 5, "s2")
        self.book.place_order("BUY", 100, 5, "b1")
        assert self.book.best_ask() == 110

    def test_market_maker_adds_liquidity_both_sides(self):
        self.book.place_order("BUY", 99, 100, "mm")
        self.book.place_order("SELL", 101, 100, "mm")
        assert self.book.best_bid() == 99
        assert self.book.best_ask() == 101
        assert len(self.book.get_trades()) == 0

    def test_self_matching_blocked_raises_wash_trade_error(self):
        """Self-matching is now banned — crossing your own order raises WashTradeError."""
        self.book.place_order("BUY", 100, 5, "trader_A")
        with pytest.raises(WashTradeError) as exc_info:
            self.book.place_order("SELL", 100, 10, "trader_A")
        assert exc_info.value.trader_id == "trader_A"
        # The resting BUY order must still be intact on the book
        assert self.book.best_bid() == 100
        assert self.book.get_snapshot()["bids"][0]["quantity"] == 5
        assert len(self.book.get_trades()) == 0

    def test_self_matching_can_be_disabled(self):
        """When self_matching_banned=False, wash trading is allowed again."""
        permissive_book = OrderBook(self_matching_banned=False)
        permissive_book.place_order("BUY", 100, 5, "trader_A")
        permissive_book.place_order("SELL", 100, 10, "trader_A")
        assert len(permissive_book.get_trades()) == 1
        assert permissive_book.get_snapshot()["asks"][0]["quantity"] == 5

    # ==========================================
    # GROUP 5: QUERIES AND EDGE CASES (8 tests)
    # ==========================================

    def test_get_open_order_quantity_non_existent(self):
        with pytest.raises(KeyError):
            self.book.get_open_order_quantity("fake-id")

    def test_get_open_order_quantity_none_id_raises_key_error(self):
        with pytest.raises(KeyError):
            self.book.get_open_order_quantity(None)

    def test_get_open_order_quantity_empty_string_id_raises_key_error(self):
        with pytest.raises(KeyError):
            self.book.get_open_order_quantity("")

    def test_get_open_order_quantity_fully_filled(self):
        id_buy = self.book.place_order("BUY", 100, 10, "t1")
        self.book.place_order("SELL", 100, 10, "t2")
        assert self.book.get_open_order_quantity(id_buy) == 0

    def test_get_open_order_quantity_partially_filled(self):
        id_buy = self.book.place_order("BUY", 100, 10, "t1")
        self.book.place_order("SELL", 100, 3, "t2")
        assert self.book.get_open_order_quantity(id_buy) == 7

    def test_get_open_order_quantity_cancelled(self):
        id_buy = self.book.place_order("BUY", 100, 10, "t1")
        self.book.cancel_order(id_buy)
        assert self.book.get_open_order_quantity(id_buy) == 0

    def test_book_clears_correctly_after_massive_sweep(self):
        for i in range(1, 101):
            self.book.place_order("SELL", 100 + i, 1, f"seller_{i}")
        self.book.place_order("BUY", 300, 100, "whale")
        assert self.book.best_ask() is None
        assert len(self.book.get_trades()) == 100

    def test_trades_history_records_correct_volume(self):
        self.book.place_order("SELL", 100, 20, "s1")
        self.book.place_order("BUY", 100, 5, "b1")
        self.book.place_order("BUY", 100, 5, "b2")
        trades = self.book.get_trades()
        total_volume = sum(t["quantity"] for t in trades)
        assert total_volume == 10

    # ==========================================
    # GROUP 6: FIFO / TIME PRIORITY (6 tests)
    # ==========================================

    def test_fifo_priority_two_orders_same_price(self):
        id1 = self.book.place_order("BUY", 100, 5, "t1")
        id2 = self.book.place_order("BUY", 100, 5, "t2")
        self.book.place_order("SELL", 100, 5, "s1")
        assert self.book.get_open_order_quantity(id1) == 0
        assert self.book.get_open_order_quantity(id2) == 5

    def test_fifo_priority_three_orders_partial_sweep(self):
        id1 = self.book.place_order("SELL", 100, 3, "t1")
        id2 = self.book.place_order("SELL", 100, 3, "t2")
        id3 = self.book.place_order("SELL", 100, 3, "t3")
        self.book.place_order("BUY", 100, 5, "b1")
        assert self.book.get_open_order_quantity(id1) == 0
        assert self.book.get_open_order_quantity(id2) == 1
        assert self.book.get_open_order_quantity(id3) == 3

    def test_fifo_priority_preserved_after_cancel_reinsert(self):
        id1 = self.book.place_order("BUY", 100, 5, "t1")
        self.book.cancel_order(id1)
        id1_new = self.book.place_order("BUY", 100, 5, "t1")
        id2 = self.book.place_order("BUY", 100, 5, "t2")
        self.book.place_order("SELL", 100, 5, "s1")
        assert self.book.get_open_order_quantity(id2) == 5
        assert self.book.get_open_order_quantity(id1_new) == 0

    def test_fifo_priority_does_not_change_on_partial_fill(self):
        id1 = self.book.place_order("SELL", 100, 5, "t1")
        self.book.place_order("BUY", 100, 2, "b1")
        id2 = self.book.place_order("SELL", 100, 5, "t2")
        self.book.place_order("BUY", 100, 3, "b2")
        assert self.book.get_open_order_quantity(id1) == 0
        assert self.book.get_open_order_quantity(id2) == 5

    def test_snapshot_reflects_fifo_order_at_level(self):
        self.book.place_order("BUY", 100, 3, "t1")
        self.book.place_order("BUY", 100, 7, "t2")
        snap = self.book.get_snapshot()
        assert snap["bids"][0]["quantity"] == 10

    def test_new_order_joins_back_of_existing_queue(self):
        id1 = self.book.place_order("BUY", 100, 5, "t1")
        self.book.place_order("SELL", 100, 3, "s1")
        id2 = self.book.place_order("BUY", 100, 5, "t2")
        self.book.place_order("SELL", 100, 4, "s2")
        assert self.book.get_open_order_quantity(id1) == 0
        assert self.book.get_open_order_quantity(id2) == 3

    # ==========================================
    # GROUP 7: ORDER ID INTEGRITY (4 tests)
    # ==========================================

    def test_order_ids_are_unique(self):
        ids = [self.book.place_order("BUY", 100, 1, "t1") for _ in range(50)]
        assert len(ids) == len(set(ids))

    def test_order_id_stable_across_partial_fills(self):
        id1 = self.book.place_order("BUY", 100, 10, "t1")
        self.book.place_order("SELL", 100, 3, "s1")
        assert self.book.get_open_order_quantity(id1) == 7
        self.book.place_order("SELL", 100, 3, "s2")
        assert self.book.get_open_order_quantity(id1) == 4

    def test_order_id_unique_across_sides(self):
        id1 = self.book.place_order("BUY", 100, 1, "t1")
        id2 = self.book.place_order("SELL", 200, 1, "t2")
        assert id1 != id2

    def test_order_id_not_reused_after_cancel(self):
        id1 = self.book.place_order("BUY", 100, 1, "t1")
        self.book.cancel_order(id1)
        id2 = self.book.place_order("BUY", 100, 1, "t1")
        assert id1 != id2

    # ==========================================
    # GROUP 8: BOUNDARY QUANTITIES AND PRICES (6 tests)
    # ==========================================

    def test_place_order_zero_quantity_rejected(self):
        with pytest.raises(ValueError):
            self.book.place_order("BUY", 100, 0, "t1")

    def test_place_order_negative_quantity_rejected(self):
        with pytest.raises(ValueError):
            self.book.place_order("BUY", 100, -5, "t1")

    def test_place_order_zero_price_rejected(self):
        with pytest.raises(ValueError):
            self.book.place_order("BUY", 0, 10, "t1")

    def test_place_order_minimum_valid_price_and_quantity(self):
        id1 = self.book.place_order("BUY", 1, 1, "t1")
        assert id1 is not None
        assert self.book.best_bid() == 1

    def test_matching_at_exact_boundary_price_equal(self):
        self.book.place_order("SELL", 100, 5, "s1")
        self.book.place_order("BUY", 100, 5, "b1")
        assert len(self.book.get_trades()) == 1

    def test_no_match_when_buy_price_below_ask(self):
        self.book.place_order("SELL", 105, 5, "s1")
        self.book.place_order("BUY", 100, 5, "b1")
        assert len(self.book.get_trades()) == 0
        assert self.book.best_bid() == 100
        assert self.book.best_ask() == 105

    # ==========================================
    # GROUP 9: NO-MATCH RESTING BEHAVIOR (4 tests)
    # ==========================================

    def test_no_match_when_sell_price_above_bid(self):
        self.book.place_order("BUY", 95, 5, "b1")
        self.book.place_order("SELL", 100, 5, "s1")
        assert len(self.book.get_trades()) == 0

    def test_resting_order_survives_unrelated_trades(self):
        id1 = self.book.place_order("BUY", 90, 5, "b1")
        self.book.place_order("SELL", 100, 5, "s1")
        self.book.place_order("BUY", 100, 5, "b2")
        assert self.book.get_open_order_quantity(id1) == 5

    def test_multiple_resting_levels_no_cross(self):
        self.book.place_order("BUY", 90, 5, "b1")
        self.book.place_order("BUY", 95, 5, "b2")
        self.book.place_order("SELL", 100, 5, "s1")
        self.book.place_order("SELL", 105, 5, "s2")
        assert len(self.book.get_trades()) == 0
        assert self.book.best_bid() == 95
        assert self.book.best_ask() == 100

    def test_order_resting_after_book_fully_cleared(self):
        self.book.place_order("SELL", 100, 5, "s1")
        self.book.place_order("BUY", 100, 5, "b1")
        id2 = self.book.place_order("BUY", 90, 5, "b2")
        assert self.book.best_bid() == 90
        assert self.book.get_open_order_quantity(id2) == 5

    # ==========================================
    # GROUP 10: TRADE HISTORY CONSISTENCY (4 tests)
    # ==========================================

    def test_trades_recorded_in_chronological_order(self):
        self.book.place_order("SELL", 100, 5, "s1")
        self.book.place_order("BUY", 100, 2, "b1")
        self.book.place_order("BUY", 100, 3, "b2")
        trades = self.book.get_trades()
        assert trades[0]["quantity"] == 2
        assert trades[1]["quantity"] == 3

    def test_no_trades_recorded_for_cancelled_orders(self):
        id1 = self.book.place_order("BUY", 100, 5, "t1")
        self.book.cancel_order(id1)
        self.book.place_order("SELL", 100, 5, "s1")
        assert len(self.book.get_trades()) == 0

    def test_trade_quantity_never_exceeds_smaller_side(self):
        self.book.place_order("SELL", 100, 3, "s1")
        self.book.place_order("BUY", 100, 10, "b1")
        trade = self.book.get_trades()[0]
        assert trade["quantity"] == 3

    def test_total_traded_volume_matches_filled_quantity(self):
        self.book.place_order("SELL", 100, 10, "s1")
        id_buy = self.book.place_order("BUY", 100, 10, "b1")
        traded = sum(t["quantity"] for t in self.book.get_trades())
        assert traded == 10
        assert self.book.get_open_order_quantity(id_buy) == 0

    # ==========================================
    # GROUP 11: CUSTOM ORDER IDS (4 tests)
    # ==========================================

    def test_custom_order_id_used_when_provided(self):
        returned_id = self.book.place_order("BUY", 100, 5, "t1", order_id="my-order-1")
        assert returned_id == "my-order-1"
        assert self.book.get_order_status("my-order-1") == "open"

    def test_duplicate_custom_order_id_rejected(self):
        self.book.place_order("BUY", 100, 5, "t1", order_id="dup-1")
        with pytest.raises(ValueError):
            self.book.place_order("SELL", 200, 3, "t2", order_id="dup-1")

    def test_custom_order_id_coexists_with_auto_generated(self):
        custom_id = self.book.place_order("BUY", 100, 5, "t1", order_id="custom-1")
        auto_id = self.book.place_order("BUY", 100, 5, "t2")
        assert custom_id != auto_id

    def test_custom_order_id_can_be_cancelled(self):
        self.book.place_order("BUY", 100, 5, "t1", order_id="cancel-me")
        assert self.book.cancel_order("cancel-me")
        assert self.book.get_order_status("cancel-me") == "cancelled"

    # ==========================================
    # GROUP 12: REGRESSION - STALE PRICE LEVELS (4 tests)
    # Covers a real bug: a price level whose only orders were cancelled
    # must correctly reappear when a new order arrives at the same price.
    # ==========================================

    def test_price_level_reappears_after_full_cancel_and_requery_bid_side(self):
        id1 = self.book.place_order("BUY", 100, 5, "t1")
        self.book.cancel_order(id1)
        assert self.book.best_bid() is None
        id2 = self.book.place_order("BUY", 100, 5, "t2")
        assert self.book.best_bid() == 100
        assert self.book.get_open_order_quantity(id2) == 5

    def test_price_level_reappears_after_full_cancel_and_requery_ask_side(self):
        id1 = self.book.place_order("SELL", 100, 5, "t1")
        self.book.cancel_order(id1)
        assert self.book.best_ask() is None
        id2 = self.book.place_order("SELL", 100, 5, "t2")
        assert self.book.best_ask() == 100
        assert self.book.get_open_order_quantity(id2) == 5

    def test_matching_finds_reinserted_level_after_cancel(self):
        id1 = self.book.place_order("BUY", 100, 5, "t1")
        self.book.cancel_order(id1)
        self.book.best_bid()
        self.book.place_order("BUY", 100, 5, "t2")
        self.book.place_order("SELL", 100, 5, "s1")
        assert len(self.book.get_trades()) == 1
        assert self.book.best_bid() is None

    def test_open_quantity_accumulates_correctly_after_reinsertion(self):
        id1 = self.book.place_order("BUY", 100, 3, "t1")
        self.book.cancel_order(id1)
        self.book.best_bid()
        self.book.place_order("BUY", 100, 4, "t2")
        self.book.place_order("BUY", 100, 2, "t3")
        snap = self.book.get_snapshot()
        assert snap["bids"][0]["quantity"] == 6

    # ==========================================
    # GROUP 13: FILL-OR-KILL ADVANCED (5 tests)
    # ==========================================

    def test_fok_fills_completely_across_multiple_levels(self):
        self.book.place_order("SELL", 100, 3, "s1")
        self.book.place_order("SELL", 101, 4, "s2")
        id_buy = self.book.place_order("BUY", 101, 7, "buyer", tif="FOK")
        assert self.book.get_order_status(id_buy) == "filled"
        assert len(self.book.get_trades()) == 2

    def test_fok_exact_match_single_level(self):
        self.book.place_order("SELL", 100, 5, "s1")
        id_buy = self.book.place_order("BUY", 100, 5, "buyer", tif="FOK")
        assert self.book.get_order_status(id_buy) == "filled"
        assert len(self.book.get_trades()) == 1

    def test_fok_no_trades_leaves_book_unchanged_when_rejected(self):
        self.book.place_order("SELL", 100, 3, "s1")
        id_buy = self.book.place_order("BUY", 100, 10, "buyer", tif="FOK")
        assert self.book.get_order_status(id_buy) == "cancelled"
        assert len(self.book.get_trades()) == 0
        assert self.book.get_snapshot()["asks"][0]["quantity"] == 3

    def test_fok_does_not_rest_partial_remainder(self):
        self.book.place_order("SELL", 100, 3, "s1")
        id_buy = self.book.place_order("BUY", 100, 10, "buyer", tif="FOK")
        assert self.book.get_open_order_quantity(id_buy) == 0
        assert self.book.get_snapshot()["bids"] == []

    def test_fok_boundary_price_exact_quantity_available(self):
        self.book.place_order("SELL", 100, 5, "s1")
        self.book.place_order("SELL", 101, 5, "s2")
        id_buy = self.book.place_order("BUY", 101, 10, "buyer", tif="FOK")
        assert self.book.get_order_status(id_buy) == "filled"
        traded = sum(t["quantity"] for t in self.book.get_trades())
        assert traded == 10

    # ==========================================
    # GROUP 14: IMMEDIATE-OR-CANCEL ADVANCED (4 tests)
    # ==========================================

    def test_ioc_full_fill_reports_filled_not_cancelled(self):
        self.book.place_order("BUY", 100, 5, "buyer")
        id_sell = self.book.place_order("SELL", 100, 5, "seller", tif="IOC")
        assert self.book.get_order_status(id_sell) == "filled"

    def test_ioc_no_match_at_all_immediately_cancelled(self):
        id_sell = self.book.place_order("SELL", 100, 5, "seller", tif="IOC")
        assert self.book.get_order_status(id_sell) == "cancelled"
        assert len(self.book.get_trades()) == 0

    def test_ioc_partial_fill_across_multiple_levels(self):
        self.book.place_order("BUY", 100, 3, "b1")
        self.book.place_order("BUY", 99, 3, "b2")
        id_sell = self.book.place_order("SELL", 99, 10, "seller", tif="IOC")
        trades = self.book.get_trades()
        assert len(trades) == 2
        assert sum(t["quantity"] for t in trades) == 6
        assert self.book.get_order_status(id_sell) == "cancelled"

    def test_ioc_does_not_leave_resting_order(self):
        id_sell = self.book.place_order("SELL", 100, 5, "seller", tif="IOC")
        assert self.book.get_open_order_quantity(id_sell) == 0
        assert self.book.get_snapshot()["asks"] == []

    # ==========================================
    # GROUP 15: ORDER STATUS QUERIES (4 tests)
    # ==========================================

    def test_get_order_status_open(self):
        id1 = self.book.place_order("BUY", 100, 5, "t1")
        assert self.book.get_order_status(id1) == "open"

    def test_get_order_status_nonexistent_returns_none(self):
        assert self.book.get_order_status("no-such-id") is None

    def test_get_order_status_none_id_returns_none(self):
        assert self.book.get_order_status(None) is None

    def test_get_order_status_filled_via_multiple_partial_trades(self):
        id_sell = self.book.place_order("SELL", 100, 10, "s1")
        self.book.place_order("BUY", 100, 4, "b1")
        self.book.place_order("BUY", 100, 6, "b2")
        assert self.book.get_order_status(id_sell) == "filled"

    # ==========================================
    # GROUP 16: INVARIANTS AND CONSISTENCY (3 tests)
    # ==========================================

    def test_snapshot_quantity_matches_sum_of_open_order_quantities(self):
        ids = [self.book.place_order("BUY", 100, i + 1, f"t{i}") for i in range(5)]
        self.book.cancel_order(ids[2])
        snap_qty = self.book.get_snapshot()["bids"][0]["quantity"]
        expected = sum(self.book.get_open_order_quantity(i) for i in ids)
        assert snap_qty == expected

    def test_cancel_all_orders_leaves_empty_snapshot(self):
        ids = [
            self.book.place_order("BUY", 100, 5, "b1"),
            self.book.place_order("BUY", 95, 3, "b2"),
            self.book.place_order("SELL", 110, 4, "s1"),
            self.book.place_order("SELL", 120, 2, "s2"),
        ]
        for order_id in ids:
            self.book.cancel_order(order_id)
        assert self.book.get_snapshot() == {"bids": [], "asks": []}

    def test_repeated_cancel_and_reinsert_cycles_maintain_consistency(self):
        for _ in range(5):
            order_id = self.book.place_order("BUY", 100, 1, "t1")
            self.book.cancel_order(order_id)
            self.book.best_bid()
        assert self.book.best_bid() is None
        final_id = self.book.place_order("BUY", 100, 7, "t2")
        assert self.book.best_bid() == 100
        assert self.book.get_open_order_quantity(final_id) == 7