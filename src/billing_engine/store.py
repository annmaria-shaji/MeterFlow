"""In-memory persistence. Replace with Postgres (or similar) behind the same
method signatures. Key production requirements are noted inline."""

from __future__ import annotations

from datetime import datetime

from .clock import Clock
from .domain import Invoice, StoredEvent, Subscription, UsageEvent
from .pricing import PlanRegistry


class EventStore:
    """Append-only. Never update or delete rows (corrections = new events)."""

    def __init__(self, clock: Clock) -> None:
        self._clock = clock
        self._rows: list[StoredEvent] = []
        self._by_id: dict[str, StoredEvent] = {}

    def append(self, event: UsageEvent) -> tuple[StoredEvent, bool]:
        """Returns (stored, created). Idempotent on event_id.

        Production: UNIQUE(event_id) + a sequence/identity column for ingest_seq.
        Beware: a sequence can commit out of order across transactions, so a run's
        cutoff must be taken from a value that's safe to read (e.g. advisory lock,
        or the highest seq below the lowest in-flight transaction).
        """
        existing = self._by_id.get(event.event_id)
        if existing is not None:
            return existing, False
        stored = StoredEvent(event, len(self._rows) + 1, self._clock.now())
        self._rows.append(stored)
        self._by_id[event.event_id] = stored
        return stored, True

    def max_seq(self) -> int:
        return len(self._rows)

    def for_customer(self, customer_id: str, cutoff_seq: int) -> list[StoredEvent]:
        return [
            r for r in self._rows if r.event.customer_id == customer_id and r.ingest_seq <= cutoff_seq
        ]


class Store:
    def __init__(self, clock: Clock) -> None:
        self.events = EventStore(clock)
        self.plans = PlanRegistry()
        self.subscriptions: dict[str, Subscription] = {}
        self.invoices: dict[str, Invoice] = {}
        self.runs: dict[str, "BillingRun"] = {}  # noqa: F821 (defined in runner.py)
        self.attempts: list["PaymentAttempt"] = []  # noqa: F821 (defined in dunning.py)

    def subscriptions_overlapping(self, start: datetime, end: datetime) -> list[Subscription]:
        out = []
        for sub in self.subscriptions.values():
            if any(p.start < end and (p.end is None or p.end > start) for p in sub.phases):
                out.append(sub)
        return sorted(out, key=lambda s: s.id)  # stable order => deterministic runs
