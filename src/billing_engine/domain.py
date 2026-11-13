"""Core domain types. All frozen/immutable except Invoice lifecycle fields.

Times are always timezone-aware UTC. Billing periods are half-open: [start, end).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from decimal import Decimal
from enum import Enum
from typing import Any, Mapping

from .errors import PricingError
from .hashing import fingerprint


# --------------------------------------------------------------------- usage
class Aggregation(str, Enum):
    SUM = "sum"
    MAX = "max"
    COUNT = "count"
    # TODO: UNIQUE_COUNT (distinct property value), LATEST, TIME_WEIGHTED


@dataclass(frozen=True)
class Meter:
    key: str
    aggregation: Aggregation = Aggregation.SUM
    unit: str = "unit"


@dataclass(frozen=True)
class UsageEvent:
    event_id: str  # client-supplied idempotency key, unique per event
    customer_id: str
    meter_key: str
    quantity: Decimal
    occurred_at: datetime  # event time (when it happened)
    properties: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class StoredEvent:
    """An event plus its *ingestion* coordinates (the second time axis).

    ``ingest_seq`` is a gapless, monotonically increasing number. A billing run
    pins a ``cutoff_seq`` so that late-arriving events can never silently change
    a past run -- they are picked up by an adjustment run instead.
    """

    event: UsageEvent
    ingest_seq: int
    ingested_at: datetime


# ------------------------------------------------------------------- periods
@dataclass(frozen=True)
class BillingPeriod:
    start: datetime
    end: datetime

    def __post_init__(self) -> None:
        if self.start >= self.end:
            raise ValueError("period start must be before end")

    @staticmethod
    def calendar_month(year: int, month: int) -> "BillingPeriod":
        start = datetime(year, month, 1, tzinfo=timezone.utc)
        ny, nm = (year + 1, 1) if month == 12 else (year, month + 1)
        return BillingPeriod(start, datetime(ny, nm, 1, tzinfo=timezone.utc))

    @property
    def seconds(self) -> int:
        return int((self.end - self.start).total_seconds())

    @property
    def key(self) -> str:
        return f"{self.start.isoformat()}/{self.end.isoformat()}"

    def contains(self, t: datetime) -> bool:
        return self.start <= t < self.end


# ------------------------------------------------------------------- pricing
class PriceModel(str, Enum):
    PER_UNIT = "per_unit"
    GRADUATED = "graduated"  # each tier's units priced at that tier's rate
    VOLUME = "volume"  # all units priced at the rate of the tier reached
    PACKAGE = "package"  # priced per block of N units, rounded up


@dataclass(frozen=True)
class Tier:
    up_to: Decimal | None  # inclusive upper bound; None = unbounded (last tier only)
    unit_price: Decimal  # minor units per unit (may be fractional, e.g. 0.2)
    flat_fee: Decimal = Decimal(0)  # minor units, charged once when the tier is reached


@dataclass(frozen=True)
class RecurringComponent:
    """Fixed fee billed each period (in advance by convention), prorated by time."""

    name: str
    amount_minor: int


@dataclass(frozen=True)
class UsageComponent:
    name: str
    meter_key: str
    model: PriceModel
    unit_price: Decimal | None = None  # PER_UNIT / PACKAGE
    tiers: tuple[Tier, ...] = ()  # GRADUATED / VOLUME
    package_size: Decimal | None = None  # PACKAGE
    included_units: Decimal = Decimal(0)  # free allowance deducted before pricing

    def __post_init__(self) -> None:
        if self.model in (PriceModel.PER_UNIT, PriceModel.PACKAGE) and self.unit_price is None:
            raise PricingError(f"{self.name}: unit_price required for {self.model.value}")
        if self.model is PriceModel.PACKAGE and not self.package_size:
            raise PricingError(f"{self.name}: package_size required")
        if self.model in (PriceModel.GRADUATED, PriceModel.VOLUME):
            if not self.tiers:
                raise PricingError(f"{self.name}: tiers required for {self.model.value}")
            if self.tiers[-1].up_to is not None:
                raise PricingError(f"{self.name}: last tier must be unbounded (up_to=None)")
            bounds = [t.up_to for t in self.tiers[:-1]]
            if None in bounds or bounds != sorted(bounds):  # type: ignore[type-var]
                raise PricingError(f"{self.name}: tier bounds must be ascending")


@dataclass(frozen=True)
class PlanVersion:
    """Immutable once published. Price changes create a new version; existing
    subscriptions keep pointing at the version they signed up for."""

    plan_id: str
    version: int
    currency: str
    recurring: tuple[RecurringComponent, ...] = ()
    usage: tuple[UsageComponent, ...] = ()


# ------------------------------------------------------------- subscriptions
@dataclass(frozen=True)
class Phase:
    """A span of a subscription on one plan version. Upgrades/downgrades/cancels
    are modelled as phase boundaries, which is what drives proration."""

    plan_id: str
    version: int
    start: datetime
    end: datetime | None = None  # None = open-ended


@dataclass(frozen=True)
class Subscription:
    id: str
    customer_id: str
    phases: tuple[Phase, ...]


# ------------------------------------------------------------------ invoices
class InvoiceStatus(str, Enum):
    DRAFT = "draft"
    OPEN = "open"
    PAID = "paid"
    UNCOLLECTIBLE = "uncollectible"
    VOID = "void"


@dataclass(frozen=True)
class InvoiceLine:
    line_id: str
    kind: str  # "recurring" | "usage"
    description: str
    quantity: Decimal
    amount_minor: int
    segment_start: datetime
    segment_end: datetime
    meter_key: str | None = None


@dataclass
class Invoice:
    id: str
    customer_id: str
    subscription_id: str
    period: BillingPeriod
    currency: str
    lines: tuple[InvoiceLine, ...]
    subtotal_minor: int
    tax_minor: int
    issued_at: datetime
    due_at: datetime
    run_id: str
    # lifecycle (excluded from the fingerprint)
    status: InvoiceStatus = InvoiceStatus.DRAFT
    amount_paid_minor: int = 0
    attempt_count: int = 0
    next_retry_at: datetime | None = None

    @property
    def total_minor(self) -> int:
        return self.subtotal_minor + self.tax_minor

    @property
    def outstanding_minor(self) -> int:
        return self.total_minor - self.amount_paid_minor

    def fingerprint(self) -> str:
        """Hash of the *computed content* only; used to prove a replay matches."""
        return fingerprint(
            {
                "id": self.id,
                "customer_id": self.customer_id,
                "subscription_id": self.subscription_id,
                "period": self.period,
                "currency": self.currency,
                "lines": self.lines,
                "subtotal": self.subtotal_minor,
                "tax": self.tax_minor,
                "issued_at": self.issued_at,
                "due_at": self.due_at,
            }
        )
