from bot_arena_exchange.application.exchange_service import ExchangeService
from bot_arena_exchange.config.tournament_config import DEFAULT_TOURNAMENT_CONFIG


def test_phase2_trade_processing_applies_venue_fees_to_both_accounts():
    service = ExchangeService()

    service.place_order("SELL", 10000, 10, "Seller", symbol="AAPL", venue="VENUE_1")
    result = service.place_order("BUY", 10000, 10, "Buyer", symbol="AAPL", venue="VENUE_1")

    assert result["status"] == "PROCESSED"
    assert result["trades_executed"] == 1
    assert service.get_account_state("Buyer")["fees_paid"] == 100
    assert service.get_account_state("Seller")["fees_paid"] == 100
    assert service.get_account_state("Buyer")["realized_pnl"] == -100
    assert service.get_account_state("Seller")["realized_pnl"] == -100


def test_phase2_cancel_order_updates_state_and_records_event():
    service = ExchangeService()
    order_id = service.place_order("BUY", 9900, 5, "Trader_1", symbol="AAPL", venue="VENUE_1")["order_id"]

    result = service.cancel_order(order_id, "Trader_1")

    assert result["status"] == "CANCELLED"
    assert service.get_order_state(order_id)["status"] == "cancelled"
    assert service.get_market_snapshot() == {"bids": [], "asks": []}
    assert service.get_event_log()[-1]["event_type"] == "CANCEL"


def test_phase2_rejects_cancel_for_other_trader():
    service = ExchangeService()
    order_id = service.place_order("BUY", 9900, 5, "Owner", symbol="AAPL", venue="VENUE_1")["order_id"]

    result = service.cancel_order(order_id, "Intruder")

    assert result == {"status": "REJECTED", "reason": "order does not belong to trader"}
    assert service.get_order_state(order_id)["status"] == "open"
    assert service.get_event_log()[-1]["event_type"] == "CANCEL_REJECTED"


def test_phase2_market_state_includes_book_trades_and_pending_orders():
    service = ExchangeService()
    service.place_order("SELL", 10000, 3, "Seller", symbol="AAPL", venue="VENUE_1")
    service.place_order("BUY", 10000, 1, "Buyer", symbol="AAPL", venue="VENUE_1")

    state = service.get_market_state()

    assert state["tick"] == 0
    assert state["best_ask"] == 10000
    assert state["snapshot"]["asks"] == [{"price": 10000, "quantity": 2}]
    assert state["recent_trades"][0]["quantity"] == 1
    assert state["pending_orders"] == 0


def test_phase2_latency_queue_delays_orders_until_tick_due():
    service = ExchangeService(config=DEFAULT_TOURNAMENT_CONFIG)

    result = service.place_order("BUY", 10000, 1, "Trader_1", symbol="AAPL", venue="VENUE_2")

    assert result["status"] == "QUEUED"
    assert result["due_tick"] == 2
    assert service.get_market_snapshot() == {"bids": [], "asks": []}
    assert service.advance_tick(1) == []
    processed = service.advance_tick(1)
    assert processed[0]["status"] == "PROCESSED"
    assert service.get_market_snapshot()["bids"] == [{"price": 10000, "quantity": 1}]


def test_phase2_order_rejections_are_recorded_as_reject_events():
    service = ExchangeService()

    result = service.place_order("BUY", 10000, 101, "Trader_1", symbol="AAPL", venue="VENUE_1")

    assert result["status"] == "REJECTED"
    assert service.get_event_log()[-1]["event_type"] == "ORDER_REJECTED"
