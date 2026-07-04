from bot_arena_exchange.application.bot_lifecycle import BotLifecycleService, BotValidator
from bot_arena_exchange.application.exchange_service import ExchangeService


VALID_BOT = {
    "bot.py": """
class MyBot:
    def on_tick(self, api):
        book = api.get_order_book(symbol=\"AAPL\", venue=\"VENUE_1\")
        order_id = api.place_order(symbol=\"AAPL\", venue=\"VENUE_1\", side=\"BUY\", price=10000, quantity=1)
        api.cancel_order(order_id)
        return api.get_position(symbol=\"AAPL\")


def create_bot():
    return MyBot()
"""
}


def test_starter_kit_contains_working_python_example():
    service = BotLifecycleService()

    starter = service.get_starter_kit()

    assert starter["language"] == "python"
    assert starter["entry_point"] == "create_bot()"
    assert "bot.py" in starter["files"]
    assert "api.place_order" in starter["files"]["bot.py"]
    assert "api.cancel_order" in starter["files"]["bot.py"]
    assert "api.get_position" in starter["files"]["bot.py"]
    assert starter["validation"]["passed"] is True


def test_validator_accepts_required_entry_point_and_allowed_imports():
    result = BotValidator().validate({
        "bot.py": """
import math

class Bot:
    pass


def create_bot():
    return Bot()
"""
    })

    assert result.passed is True
    assert result.errors == []


def test_validator_rejects_missing_entry_point():
    result = BotValidator().validate({"bot.py": "class Bot:\n    pass\n"})

    assert result.passed is False
    assert "bot must define create_bot()" in result.errors


def test_validator_rejects_unsupported_dependencies():
    result = BotValidator().validate({
        "bot.py": """
import requests


def create_bot():
    return object()
"""
    })

    assert result.passed is False
    assert "bot.py: unsupported dependency 'requests'" in result.errors


def test_validator_rejects_syntax_errors():
    result = BotValidator().validate({"bot.py": "def create_bot(:\n    pass"})

    assert result.passed is False
    assert result.errors[0].startswith("syntax error")


def test_submission_creates_incrementing_versions():
    service = BotLifecycleService()

    first = service.submit_bot("user-1", "mean-reverter", VALID_BOT)
    second = service.submit_bot("user-1", "mean-reverter", VALID_BOT)

    assert first["status"] == "ACCEPTED"
    assert first["version"] == 1
    assert second["version"] == 2
    versions = service.list_versions("user-1", "mean-reverter")
    assert [version["version"] for version in versions] == [1, 2]


def test_failed_submission_is_saved_as_error_not_version():
    service = BotLifecycleService()

    result = service.submit_bot("user-1", "broken", {"bot.py": "class Bot:\n    pass\n"})

    assert result["status"] == "ERROR"
    assert result["errors"] == ["bot must define create_bot()"]
    assert service.list_versions("user-1", "broken") == []
    assert len(service.repository.failed_submissions) == 1


def test_exchange_service_exposes_bot_lifecycle():
    service = ExchangeService()

    validation = service.validate_bot(VALID_BOT)
    submission = service.submit_bot("user-1", "bot", VALID_BOT)
    version = service.get_bot_version("user-1", "bot", 1)

    assert validation["passed"] is True
    assert submission["status"] == "ACCEPTED"
    assert version["version"] == 1
    assert service.list_bot_versions("user-1", "bot")[0]["bot_name"] == "bot"
