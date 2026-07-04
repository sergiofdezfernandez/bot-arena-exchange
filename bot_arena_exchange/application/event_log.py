from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional


@dataclass(frozen=True)
class EventLogEntry:
    sequence: int
    event_type: str
    bot_id: str
    tournament_id: str
    payload: Dict[str, Any]
    validation_result: str
    final_action: Optional[str]
    reason: Optional[str] = None


@dataclass
class InMemoryEventLog:
    entries: List[EventLogEntry] = field(default_factory=list)

    def record(
        self,
        event_type: str,
        bot_id: str,
        tournament_id: str,
        payload: Dict[str, Any],
        validation_result: str,
        final_action: Optional[str],
        reason: Optional[str] = None,
    ) -> EventLogEntry:
        entry = EventLogEntry(
            sequence=len(self.entries) + 1,
            event_type=event_type,
            bot_id=bot_id,
            tournament_id=tournament_id,
            payload=dict(payload),
            validation_result=validation_result,
            final_action=final_action,
            reason=reason,
        )
        self.entries.append(entry)
        return entry

    def as_dicts(self) -> List[Dict[str, Any]]:
        return [entry.__dict__ for entry in self.entries]
