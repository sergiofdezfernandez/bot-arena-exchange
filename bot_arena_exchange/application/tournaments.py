from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Dict, List, Optional

from bot_arena_exchange.config.tournament_config import TournamentConfig


@dataclass(frozen=True)
class TournamentEntry:
    owner_id: str
    bot_name: str
    version: int
    entered_at: str


@dataclass
class ScheduledTournament:
    tournament_id: str
    config: TournamentConfig
    starts_at: str
    entry_deadline: str
    status: str = "UPCOMING"
    entries: Dict[str, TournamentEntry] = field(default_factory=dict)
    leaderboard: Optional[List[dict]] = None

    def is_entry_open(self, now: datetime) -> bool:
        return self.status == "UPCOMING" and now < datetime.fromisoformat(self.entry_deadline)

    def as_dict(self) -> dict:
        return {
            "tournament_id": self.tournament_id,
            "starts_at": self.starts_at,
            "entry_deadline": self.entry_deadline,
            "status": self.status,
            "entries_open": self.is_entry_open(datetime.now(timezone.utc)),
            "markets": [market.__dict__ for market in self.config.markets],
            "venues": [venue.__dict__ for venue in self.config.venues],
            "scoring": self.config.scoring.__dict__,
            "entries": [entry.__dict__ for entry in self.entries.values()],
            "leaderboard": self.leaderboard,
        }


class TournamentScheduler:
    def __init__(self, config: TournamentConfig):
        starts_at = datetime.now(timezone.utc) + timedelta(seconds=config.rules.entry_deadline_seconds_before_start, minutes=10)
        self.tournaments: Dict[str, ScheduledTournament] = {
            config.tournament_id: ScheduledTournament(
                tournament_id=config.tournament_id,
                config=config,
                starts_at=starts_at.isoformat(),
                entry_deadline=(starts_at - timedelta(seconds=config.rules.entry_deadline_seconds_before_start)).isoformat(),
            )
        }

    def list_tournaments(self) -> List[dict]:
        return [tournament.as_dict() for tournament in self.tournaments.values()]

    def get_tournament(self, tournament_id: str) -> Optional[dict]:
        tournament = self.tournaments.get(tournament_id)
        return tournament.as_dict() if tournament else None

    def get(self, tournament_id: str) -> Optional[ScheduledTournament]:
        return self.tournaments.get(tournament_id)

    def enter_bot(self, tournament_id: str, owner_id: str, bot_name: str, version: int) -> dict:
        tournament = self.tournaments.get(tournament_id)
        if tournament is None:
            return {"status": "REJECTED", "reason": "tournament not found"}
        if not tournament.is_entry_open(datetime.now(timezone.utc)):
            return {"status": "REJECTED", "reason": "entries are closed"}
        entry = TournamentEntry(
            owner_id=owner_id,
            bot_name=bot_name,
            version=version,
            entered_at=datetime.now(timezone.utc).isoformat(),
        )
        tournament.entries[f"{owner_id}:{bot_name}"] = entry
        return {"status": "ENTERED", **entry.__dict__}

    def mark_running(self, tournament_id: str) -> None:
        tournament = self.tournaments[tournament_id]
        tournament.status = "RUNNING"

    def publish_results(self, tournament_id: str, leaderboard: List[dict]) -> dict:
        tournament = self.tournaments[tournament_id]
        tournament.status = "COMPLETED"
        tournament.leaderboard = leaderboard
        return tournament.as_dict()
