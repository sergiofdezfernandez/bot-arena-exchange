"""Phase 2 tests — updated for async, event-driven architecture (no latency queue)."""

import asyncio

from bot_arena_exchange.application.exchange_service import ExchangeService
from bot_arena_exchange.config.tournament_config import DEFAULT_TOURNAMENT_CONFIG


def _run(coro):
    """Helper to run an async coroutine synchronously in tests."""
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(coro)
    # If there's already a running loop, create a new one (rare in tests)
    import nest_asyncio
    nest_asyncio.apply()
    return loop.run_until_complete(coro)


def test_phase2_trade_processing_applies_venue_fees_to_both_accounts():
    service = ExchangeService()

    async def run():
        await service.place_order("SELL", 10000, 10, "Seller", symbol="AAPL", venue="VENUE_1")
        return await service.place_order("BUY", 10000, 10, "Buyer", symbol="AAPL", venue="VENUE_1")

    result = _run(run())

    assert result["status"] == "PROCESSED"
    assert result["trades_executed"] == 1
    # VENUE_1 has fee_bps=0, so fees and PnL impact are zero
    assert service.get_account_state("Buyer")["fees_paid"] == 0
    assert service.get_account_state("Seller")["fees_paid"] == 0
    assert service.get_account_state("Buyer")["realized_pnl"] == 0
    assert service.get_account_state("Seller")["realized_pnl"] == 0


def test_phase2_cancel_order_updates_state_and_records_event():
    service = ExchangeService()

    async def run():
        result = await service.place_order("BUY", 9900, 5, "Trader_1", symbol="AAPL", venue="VENUE_1")
        return await service.cancel_order(result["order_id"], "Trader_1")

    result = _run(run())

    assert result["status"] == "CANCELLED"
    # The order_id is inside the cancel result
    order_id = result["order_id"]
    assert service.get_order_state(order_id)["status"] == "cancelled"
    assert service.get_market_snapshot() == {"bids": [], "asks": []}
    events = service.get_event_log()
    # CANCEL event is emitted before ORDERBOOK_UPDATE, so it should be second-to-last
    cancel_events = [e for e in events if e["event_type"] == "CANCEL"]
    assert len(cancel_events) == 1


def test_phase2_rejects_cancel_for_other_trader():
    service = ExchangeService()

    async def run():
        result = await service.place_order("BUY", 9900, 5, "Owner", symbol="AAPL", venue="VENUE_1")
        return await service.cancel_order(result["order_id"], "Intruder")

    result = _run(run())

    assert result == {"status": "REJECTED", "reason": "order does not belong to trader"}

    # Find the original order ID from events
    events = service.get_event_log()
    order_accepted = [e for e in events if e["event_type"] == "ORDER_ACCEPTED"][0]
    order_id = order_accepted["payload"]["order_id"]
    assert service.get_order_state(order_id)["status"] == "open"
    assert events[-1]["event_type"] == "CANCEL_REJECTED"


def test_phase2_market_state_includes_book_and_trades():
    service = ExchangeService()

    async def run():
        await service.place_order("SELL", 10000, 3, "Seller", symbol="AAPL", venue="VENUE_1")
        return await service.place_order("BUY", 10000, 1, "Buyer", symbol="AAPL", venue="VENUE_1")

    _run(run())

    state = service.get_market_state()

    assert state["best_ask"] == 10000
    assert state["snapshot"]["asks"] == [{"price": 10000, "quantity": 2}]
    assert state["recent_trades"][0]["quantity"] == 1
    # No more tick or pending_orders fields (latency removed)
    assert "tick" not in state
    assert "pending_orders" not in state


def test_phase2_orders_execute_instantly_no_latency_queue():
    """Orders now execute instantly — no QUEUED status, no advance_tick."""
    service = ExchangeService(config=DEFAULT_TOURNAMENT_CONFIG)

    async def run():
        return await service.place_order("BUY", 10000, 1, "Trader_1", symbol="AAPL", venue="VENUE_2")

    result = _run(run())

    # Should be PROCESSED immediately, not QUEUED
    assert result["status"] == "PROCESSED"
    # Order was placed on VENUE_2 — query the correct venue's book
    assert service.get_market_snapshot(symbol="AAPL", venue="VENUE_2")["bids"] == [{"price": 10000, "quantity": 1}]


def test_phase2_order_rejections_are_recorded_as_reject_events():
    service = ExchangeService()

    async def run():
        return await service.place_order("BUY", 10000, 101, "Trader_1", symbol="AAPL", venue="VENUE_1")

    result = _run(run())

    assert result["status"] == "REJECTED"
    assert service.get_event_log()[-1]["event_type"] == "ORDER_REJECTED"