"""Injectable clock. Nothing in the engine calls datetime.now() directly,
which is what makes replays deterministic and tests easy."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Protocol


class Clock(Protocol):
    def now(self) -> datetime: ...


class SystemClock:
    def now(self) -> datetime:
        return datetime.now(timezone.utc)


class FixedClock:
    def __init__(self, at: datetime):
        self._at = at

    def now(self) -> datetime:
        return self._at

    def set(self, at: datetime) -> None:
        self._at = at

    def advance(self, delta: timedelta) -> None:
        self._at += delta
