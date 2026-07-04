from pathlib import Path

STARTER_BOT_PATH = Path(__file__).with_name("example_bot.py")


def read_starter_bot_source():
    return STARTER_BOT_PATH.read_text()


__all__ = ["STARTER_BOT_PATH", "read_starter_bot_source"]
