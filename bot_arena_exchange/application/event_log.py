import asyncio
from dataclasses import dataclass, field
from datetime import datetime, timezone
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
    timestamp: str = ""

    def as_dict(self) -> Dict[str, Any]:
        return {
            "sequence": self.sequence,
            "event_type": self.event_type,
            "bot_id": self.bot_id,
            "tournament_id": self.tournament_id,
            "payload": self.payload,
            "validation_result": self.validation_result,
            "final_action": self.final_action,
            "reason": self.reason,
            "timestamp": self.timestamp,
        }


@dataclass
class InMemoryEventLog:
    entries: List[EventLogEntry] = field(default_factory=list)
    _subscribers: List[asyncio.Queue] = field(default_factory=list)

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
        now = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
        entry = EventLogEntry(
            sequence=len(self.entries) + 1,
            event_type=event_type,
            bot_id=bot_id,
            tournament_id=tournament_id,
            payload=dict(payload),
            validation_result=validation_result,
            final_action=final_action,
            reason=reason,
            timestamp=now,
        )
        self.entries.append(entry)
        # Broadcast to all active subscribers
        event_dict = entry.as_dict()
        for queue in self._subscribers:
            try:
                queue.put_nowait(event_dict)
            except asyncio.QueueFull:
                pass
        return entry

    def subscribe(self) -> asyncio.Queue:
        """Create a subscription queue for real-time event streaming via WebSocket."""
        queue: asyncio.Queue = asyncio.Queue(maxsize=256)
        self._subscribers.append(queue)
        return queue

    def unsubscribe(self, queue: asyncio.Queue) -> None:
        """Remove a subscription queue."""
        if queue in self._subscribers:
            self._subscribers.remove(queue)

    def as_dicts(self) -> List[Dict[str, Any]]:
        return [entry.as_dict() for entry in self.entries]
