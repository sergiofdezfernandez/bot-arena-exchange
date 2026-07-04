from datetime import datetime, timedelta, timezone

from bot_arena_exchange.application.exchange_service import ExchangeService


VALID_BOT = {
    "bot.py": """
class Bot:
    def on_tick(self, api):
        return None


def create_bot():
    return Bot()
"""
}


def test_phase3_lists_upcoming_tournament_with_details():
    service = ExchangeService()

    tournaments = service.list_tournaments()

    assert len(tournaments) == 1
    assert tournaments[0]["tournament_id"] == service.config.tournament_id
    assert tournaments[0]["status"] == "UPCOMING"
    assert tournaments[0]["markets"][0]["symbol"] == "AAPL"
    assert tournaments[0]["scoring"]["delta_penalty_enabled"] is True


def test_phase3_enters_accepted_bot_version_before_deadline():
    service = ExchangeService()
    service.submit_bot("user-1", "bot", VALID_BOT)

    result = service.enter_tournament(service.config.tournament_id, "user-1", "bot", 1)

    assert result["status"] == "ENTERED"
    detail = service.get_tournament(service.config.tournament_id)
    assert detail["entries"][0]["owner_id"] == "user-1"
    assert detail["entries"][0]["bot_name"] == "bot"
    assert detail["entries"][0]["version"] == 1


def test_phase3_rejects_unknown_bot_version_entry():
    service = ExchangeService()

    result = service.enter_tournament(service.config.tournament_id, "user-1", "missing", 1)

    assert result == {"status": "REJECTED", "reason": "bot version not found"}


def test_phase3_blocks_entries_after_deadline():
    service = ExchangeService()
    service.submit_bot("user-1", "bot", VALID_BOT)
    tournament = service.scheduler.get(service.config.tournament_id)
    tournament.entry_deadline = (datetime.now(timezone.utc) - timedelta(seconds=1)).isoformat()

    result = service.enter_tournament(service.config.tournament_id, "user-1", "bot", 1)

    assert result == {"status": "REJECTED", "reason": "entries are closed"}


def test_phase3_runner_adds_liquidity_publishes_saved_leaderboard():
    service = ExchangeService()
    service.submit_bot("user-1", "bot", VALID_BOT)
    service.enter_tournament(service.config.tournament_id, "user-1", "bot", 1)

    result = service.run_scheduled_tournament(service.config.tournament_id)

    assert result["status"] == "COMPLETED"
    assert result["leaderboard"]
    assert result["leaderboard"][0]["rank"] == 1
    detail = service.get_tournament(service.config.tournament_id)
    assert detail["status"] == "COMPLETED"
    assert detail["leaderboard"] == result["leaderboard"]
    assert service.get_market_snapshot()["bids"] or service.get_market_snapshot()["asks"]


def test_phase3_leaderboard_ranks_by_adjusted_score():
    service = ExchangeService()
    winner = service.manager.get_account("winner")
    loser = service.manager.get_account("loser")
    winner.realized_pnl = 5000
    loser.realized_pnl = 1000

    leaderboard = service.get_leaderboard()

    assert leaderboard[0]["trader_id"] == "winner"
    assert leaderboard[0]["rank"] == 1
    assert leaderboard[1]["rank"] == 2
