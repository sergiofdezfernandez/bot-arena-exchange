from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Dict, List, Optional


@dataclass(frozen=True)
class BotVersion:
    owner_id: str
    bot_name: str
    version: int
    submitted_at: str
    files: Dict[str, str]
    status: str = "ACCEPTED"


@dataclass(frozen=True)
class FailedSubmission:
    owner_id: str
    bot_name: str
    submitted_at: str
    files: Dict[str, str]
    errors: List[str]
    status: str = "ERROR"


@dataclass
class InMemoryBotRepository:
    versions: Dict[str, List[BotVersion]] = field(default_factory=dict)
    failed_submissions: List[FailedSubmission] = field(default_factory=list)

    def save_version(self, owner_id: str, bot_name: str, files: Dict[str, str]) -> BotVersion:
        key = self._key(owner_id, bot_name)
        current_versions = self.versions.setdefault(key, [])
        version = BotVersion(
            owner_id=owner_id,
            bot_name=bot_name,
            version=len(current_versions) + 1,
            submitted_at=self._now(),
            files=dict(files),
        )
        current_versions.append(version)
        return version

    def save_failed_submission(self, owner_id: str, bot_name: str, files: Dict[str, str], errors: List[str]) -> FailedSubmission:
        failed = FailedSubmission(
            owner_id=owner_id,
            bot_name=bot_name,
            submitted_at=self._now(),
            files=dict(files),
            errors=list(errors),
        )
        self.failed_submissions.append(failed)
        return failed

    def list_versions(self, owner_id: str, bot_name: Optional[str] = None) -> List[BotVersion]:
        if bot_name is not None:
            return list(self.versions.get(self._key(owner_id, bot_name), []))
        result = []
        for key, versions in self.versions.items():
            if key.startswith(f"{owner_id}:"):
                result.extend(versions)
        return result

    def get_version(self, owner_id: str, bot_name: str, version: int) -> Optional[BotVersion]:
        for bot_version in self.versions.get(self._key(owner_id, bot_name), []):
            if bot_version.version == version:
                return bot_version
        return None

    def _key(self, owner_id: str, bot_name: str) -> str:
        return f"{owner_id}:{bot_name}"

    def _now(self) -> str:
        return datetime.now(timezone.utc).isoformat()
