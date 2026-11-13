"""Invoice generation: subscription phases + usage -> invoice lines.

Determinism rules (so a replay yields byte-identical output):
  * no wall-clock reads (``issued_at`` is derived from the period)
  * no floats; Decimal/Fraction only, rounded once per line
  * all iteration over sorted/ordered inputs
  * ids come from ``deterministic_id``
"""

from __future__ import annotations

from datetime import datetime, timedelta
from decimal import Decimal
from fractions import Fraction
from typing import Mapping, Protocol, Sequence

from .domain import (
    BillingPeriod,
    Invoice,
    InvoiceLine,
    Meter,
    Phase,
    StoredEvent,
    Subscription,
)
from .errors import PricingError
from .hashing import deterministic_id
from .ingestion import aggregate
from .money import to_minor
from .pricing import PlanRegistry, rate_usage


class TaxCalculator(Protocol):
    def tax_minor(self, customer_id: str, subtotal_minor: int, currency: str) -> int: ...


class NoTax:
    def tax_minor(self, customer_id: str, subtotal_minor: int, currency: str) -> int:
        return 0


class FlatRateTax:
    """Placeholder. Real tax needs jurisdiction, product tax codes, exemptions
    (delegate to a tax provider and record its response as an input to the run)."""

    def __init__(self, rate: Decimal) -> None:
        self._rate = rate

    def tax_minor(self, customer_id: str, subtotal_minor: int, currency: str) -> int:
        return to_minor(Decimal(subtotal_minor) * self._rate)


def segment_of(phase: Phase, period: BillingPeriod) -> tuple[datetime, datetime] | None:
    """The slice of ``period`` during which ``phase`` is active, or None."""
    start = max(phase.start, period.start)
    end = min(phase.end or period.end, period.end)
    return (start, end) if start < end else None


def proration_fraction(period: BillingPeriod, seg: tuple[datetime, datetime]) -> Fraction:
    """Exact time-based share of the period (second granularity)."""
    return Fraction(int((seg[1] - seg[0]).total_seconds()), period.seconds)


class InvoiceBuilder:
    def __init__(
        self,
        plans: PlanRegistry,
        meters: Mapping[str, Meter],
        tax: TaxCalculator | None = None,
        net_terms: timedelta = timedelta(days=14),
    ) -> None:
        self._plans = plans
        self._meters = meters
        self._tax = tax or NoTax()
        self._net_terms = net_terms

    def build(
        self,
        *,
        subscription: Subscription,
        period: BillingPeriod,
        events: Sequence[StoredEvent],
        run_id: str,
    ) -> Invoice | None:
        lines: list[InvoiceLine] = []
        currency: str | None = None

        for phase in sorted(subscription.phases, key=lambda p: p.start):
            seg = segment_of(phase, period)
            if seg is None:
                continue
            plan = self._plans.get(phase.plan_id, phase.version)
            if currency is not None and plan.currency != currency:
                raise PricingError("currency changed mid-period; not supported")
            currency = plan.currency
            lines += self._recurring_lines(subscription, plan, seg, period)
            lines += self._usage_lines(subscription, plan, seg, events)

        if not lines or currency is None:
            return None

        subtotal = sum(l.amount_minor for l in lines)
        issued_at = period.end
        return Invoice(
            id=deterministic_id("inv", run_id, subscription.id),
            customer_id=subscription.customer_id,
            subscription_id=subscription.id,
            period=period,
            currency=currency,
            lines=tuple(lines),
            subtotal_minor=subtotal,
            tax_minor=self._tax.tax_minor(subscription.customer_id, subtotal, currency),
            issued_at=issued_at,
            due_at=issued_at + self._net_terms,
            run_id=run_id,
        )

    # -- recurring fees ---------------------------------------------------
    def _recurring_lines(self, sub, plan, seg, period) -> list[InvoiceLine]:
        frac = proration_fraction(period, seg)
        lines = []
        for comp in plan.recurring:
            label = comp.name if frac == 1 else f"{comp.name} (prorated {frac.numerator}/{frac.denominator})"
            lines.append(
                InvoiceLine(
                    line_id=deterministic_id("ln", sub.id, period.key, plan.plan_id, plan.version, comp.name, seg[0]),
                    kind="recurring",
                    description=f"{label} [{plan.plan_id} v{plan.version}]",
                    quantity=Decimal(1),
                    amount_minor=to_minor(Fraction(comp.amount_minor) * frac),
                    segment_start=seg[0],
                    segment_end=seg[1],
                )
            )
        # TODO: credit for unused time on mid-period downgrade/cancel when fees are
        # billed in advance (needs the prior invoice); this skeleton bills each
        # phase's share in the period it occurs.
        return lines

    # -- usage ------------------------------------------------------------
    def _usage_lines(self, sub, plan, seg, events: Sequence[StoredEvent]) -> list[InvoiceLine]:
        lines = []
        for comp in plan.usage:
            meter = self._meters[comp.meter_key]
            in_seg = [
                e
                for e in events
                if e.event.meter_key == comp.meter_key and seg[0] <= e.event.occurred_at < seg[1]
            ]
            qty = aggregate(meter, in_seg)
            if qty == 0:
                continue
            lines.append(
                InvoiceLine(
                    line_id=deterministic_id("ln", sub.id, plan.plan_id, plan.version, comp.name, seg[0]),
                    kind="usage",
                    description=f"{comp.name}: {qty} {meter.unit} [{plan.plan_id} v{plan.version}]",
                    quantity=qty,
                    amount_minor=to_minor(rate_usage(comp, qty)),
                    segment_start=seg[0],
                    segment_end=seg[1],
                    meter_key=comp.meter_key,
                )
            )
        # NOTE: each phase is rated independently, so tiers/allowances reset at a
        # plan change. Carrying cumulative usage across phases is a policy decision.
        return lines
