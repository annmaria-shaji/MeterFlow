"""Usage ingestion: validate, dedupe, append."""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from enum import Enum
from typing import Iterable, Mapping

from .domain import Aggregation, Meter, StoredEvent, UsageEvent
from .errors import IngestionError
from .store import EventStore


class IngestStatus(str, Enum):
    ACCEPTED = "accepted"
    DUPLICATE = "duplicate"


@dataclass(frozen=True)
class IngestResult:
    event_id: str
    status: IngestStatus


class IngestionService:
    def __init__(self, events: EventStore, meters: Mapping[str, Meter]) -> None:
        self._events = events
        self._meters = meters

    def ingest(self, event: UsageEvent) -> IngestResult:
        self._validate(event)
        stored, created = self._events.append(event)
        if not created and stored.event != event:
            # Same id, different payload: refuse rather than silently pick one.
            raise IngestionError(f"event_id {event.event_id!r} reused with a different payload")
        return IngestResult(event.event_id, IngestStatus.ACCEPTED if created else IngestStatus.DUPLICATE)

    def ingest_batch(self, events: Iterable[UsageEvent]) -> list[IngestResult]:
        return [self.ingest(e) for e in events]

    def _validate(self, e: UsageEvent) -> None:
        if e.meter_key not in self._meters:
            raise IngestionError(f"unknown meter {e.meter_key!r}")
        if e.occurred_at.tzinfo is None:
            raise IngestionError("occurred_at must be timezone-aware")
        if e.quantity < 0:
            raise IngestionError("quantity must be >= 0 (send a correcting event instead)")
        # TODO: reject events too far in the future / before customer exists,
        # enforce property schemas, per-tenant rate limits.


def aggregate(meter: Meter, events: Iterable[StoredEvent]) -> Decimal:
    qtys = [se.event.quantity for se in events]
    if meter.aggregation is Aggregation.SUM:
        return sum(qtys, Decimal(0))
    if meter.aggregation is Aggregation.MAX:
        return max(qtys, default=Decimal(0))
    if meter.aggregation is Aggregation.COUNT:
        return Decimal(len(qtys))
    raise NotImplementedError(meter.aggregation)
