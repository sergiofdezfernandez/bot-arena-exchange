import asyncio
import json

import pytest

from bot_arena_exchange.adapters.cli.local_simulation import create_exchange_from_config
from bot_arena_exchange.application.exchange_service import ExchangeService
from bot_arena_exchange.config.tournament_config import DEFAULT_TOURNAMENT_CONFIG, TournamentConfig, load_tournament_config
from bot_arena_exchange.domain.scoring import calculate_delta_liquidation_penalty


def _run(coro):
    """Helper to run an async coroutine synchronously in tests."""
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(coro)
    import nest_asyncio
    nest_asyncio.apply()
    return loop.run_until_complete(coro)


def test_default_tournament_config_has_phase_zero_rules():
    config = DEFAULT_TOURNAMENT_CONFIG

    assert config.tournament_id == "default-phase-0"
    assert config.rules.duration_ticks > 0
    assert config.scoring.liquidation_fee_bps == 100
    assert {market.symbol for market in config.markets} == {"AAPL"}
    assert {venue.venue_id for venue in config.venues} == {"VENUE_1", "VENUE_2"}
    assert {regime.name for regime in config.regimes} == {
        "sideways",
        "trending",
        "high_volatility",
        "liquidity_shock",
        "black_swan",
    }


def test_config_rejects_venue_with_unsupported_symbol():
    data = {
        "tournament_id": "bad-config",
        "rules": {
            "duration_ticks": 10,
            "entry_deadline_seconds_before_start": 0,
            "minimum_participants": 1,
        },
        "scoring": {"liquidation_fee_bps": 100, "delta_penalty_enabled": True},
        "markets": [
            {
                "symbol": "AAPL",
                "market_type": "spot",
                "tick_size": 1,
                "lot_size": 1,
                "initial_reference_price": 10000,
            }
        ],
        "venues": [
            {
                "venue_id": "VENUE_1",
                "fee_bps": 10,
                "spread_bps": 20,
                "latency_ticks": 1,
                "supported_symbols": ["MSFT"],
            }
        ],
        "regimes": [
            {
                "name": "sideways",
                "visible_before_tournament": True,
                "volatility_bps": 50,
                "liquidity_multiplier": 1.0,
                "spread_multiplier": 1.0,
            }
        ],
    }

    with pytest.raises(ValueError, match="unsupported symbols"):
        TournamentConfig.from_dict(data)


def test_gateway_rejects_invalid_request_and_records_event():
    service = ExchangeService()

    async def run():
        return await service.place_order("BUY", 10000, 10, "Trader_1", symbol="MSFT", venue="VENUE_1")

    result = _run(run())

    assert result["status"] == "REJECTED"
    assert result["reason"] == "unsupported symbol"
    assert service.get_event_log()[0]["validation_result"] == "REJECTED"
    assert service.get_event_log()[0]["final_action"] is None


def test_gateway_accepts_valid_request_and_records_event():
    service = ExchangeService()

    async def run():
        return await service.place_order("BUY", 10000, 10, "Trader_1", symbol="AAPL", venue="VENUE_1")

    result = _run(run())

    assert result["status"] == "PROCESSED"
    assert result["order_id"]
    assert service.get_event_log()[0]["validation_result"] == "ACCEPTED"
    assert service.get_event_log()[0]["final_action"] == "PLACE_ORDER"


def test_gateway_blocks_position_limit_before_order_book():
    service = ExchangeService()

    async def run():
        return await service.place_order("BUY", 10000, 101, "Trader_1", symbol="AAPL", venue="VENUE_1")

    result = _run(run())

    assert result["status"] == "REJECTED"
    assert result["reason"] == "position limit would be exceeded"
    assert service.book.get_snapshot() == {"bids": [], "asks": []}


def test_delta_liquidation_penalty_uses_spread_and_fee():
    penalty = calculate_delta_liquidation_penalty(
        positions={"AAPL": 10},
        reference_prices={"AAPL": 10000},
        spread_bps=20,
        liquidation_fee_bps=100,
    )

    assert penalty == 1200


def test_score_traders_applies_delta_penalty():
    service = ExchangeService()
    account = service.manager.get_account("Trader_1")
    account.positions["AAPL"] = 10
    account.realized_pnl = 5000

    scores = service.score_traders()

    assert scores[0]["realized_pnl"] == 5000
    assert scores[0]["delta_exposure"] == 10
    assert scores[0]["liquidation_penalty"] == 1300
    assert scores[0]["adjusted_score"] == 3700


def test_service_can_be_created_from_config_file(tmp_path):
    config_path = tmp_path / "tournament.json"
    config_path.write_text(json.dumps({
        "tournament_id": "from-file",
        "rules": {
            "duration_ticks": 5,
            "entry_deadline_seconds_before_start": 0,
            "minimum_participants": 1,
        },
        "scoring": {"liquidation_fee_bps": 50, "delta_penalty_enabled": True},
        "markets": [
            {
                "symbol": "ABC",
                "market_type": "spot",
                "tick_size": 1,
                "lot_size": 1,
                "initial_reference_price": 5000,
            }
        ],
        "venues": [
            {
                "venue_id": "SIM",
                "fee_bps": 5,
                "spread_bps": 10,
                "latency_ticks": 0,
                "supported_symbols": ["ABC"],
            }
        ],
        "regimes": [
            {
                "name": "sideways",
                "visible_before_tournament": True,
                "volatility_bps": 10,
                "liquidity_multiplier": 1.0,
                "spread_multiplier": 1.0,
            }
        ],
    }))

    service = create_exchange_from_config(config_path)

    async def run():
        return await service.place_order("BUY", 5000, 1, "Trader_1", symbol="ABC", venue="SIM")

    result = _run(run())

    assert service.config.tournament_id == "from-file"
    assert result["status"] == "PROCESSED"
    assert load_tournament_config(config_path).tournament_id == "from-file"
