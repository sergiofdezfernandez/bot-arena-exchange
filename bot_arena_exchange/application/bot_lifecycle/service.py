from bot_arena_exchange.application.bot_lifecycle.repository import InMemoryBotRepository
from bot_arena_exchange.application.bot_lifecycle.validation import BotValidator
from bot_arena_exchange.starter_kit import read_starter_bot_source


class BotLifecycleService:
    def __init__(self, validator=None, repository=None):
        self.validator = validator or BotValidator()
        self.repository = repository or InMemoryBotRepository()

    def get_starter_kit(self):
        source = read_starter_bot_source()
        validation = self.validator.validate({"bot.py": source})
        return {
            "language": "python",
            "entry_point": "create_bot()",
            "files": {"bot.py": source},
            "local_check": "python3 -m pytest",
            "validation": validation.__dict__,
        }

    def validate_bot(self, files):
        return self.validator.validate(files).__dict__

    def submit_bot(self, owner_id, bot_name, files):
        validation = self.validator.validate(files)
        if not validation.passed:
            failed = self.repository.save_failed_submission(owner_id, bot_name, files, validation.errors)
            return {
                "status": failed.status,
                "owner_id": failed.owner_id,
                "bot_name": failed.bot_name,
                "submitted_at": failed.submitted_at,
                "errors": failed.errors,
            }
        version = self.repository.save_version(owner_id, bot_name, files)
        return {
            "status": version.status,
            "owner_id": version.owner_id,
            "bot_name": version.bot_name,
            "version": version.version,
            "submitted_at": version.submitted_at,
        }

    def list_versions(self, owner_id, bot_name=None):
        return [version.__dict__ for version in self.repository.list_versions(owner_id, bot_name)]

    def get_version(self, owner_id, bot_name, version):
        bot_version = self.repository.get_version(owner_id, bot_name, version)
        return bot_version.__dict__ if bot_version else None
