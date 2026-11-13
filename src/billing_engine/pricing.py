"""Plan registry and the rating functions (quantity -> price). Pure functions."""

from __future__ import annotations

import math
from decimal import Decimal

from .domain import PlanVersion, PriceModel, Tier, UsageComponent
from .errors import PricingError


class PlanRegistry:
    def __init__(self) -> None:
        self._plans: dict[tuple[str, int], PlanVersion] = {}

    def add(self, plan: PlanVersion) -> None:
        key = (plan.plan_id, plan.version)
        if key in self._plans:
            raise PricingError(f"plan {key} already published; versions are immutable")
        self._plans[key] = plan

    def get(self, plan_id: str, version: int) -> PlanVersion:
        try:
            return self._plans[(plan_id, version)]
        except KeyError:
            raise PricingError(f"unknown plan {plan_id} v{version}") from None


def _graduated(tiers: tuple[Tier, ...], qty: Decimal) -> Decimal:
    total, lower = Decimal(0), Decimal(0)
    for tier in tiers:
        if qty <= lower:
            break
        upper = qty if tier.up_to is None else tier.up_to
        in_tier = min(qty, upper) - lower
        if in_tier > 0:
            total += in_tier * tier.unit_price + tier.flat_fee
        lower = upper
    return total


def _volume(tiers: tuple[Tier, ...], qty: Decimal) -> Decimal:
    for tier in tiers:
        if tier.up_to is None or qty <= tier.up_to:
            return qty * tier.unit_price + tier.flat_fee
    raise PricingError("unreachable: last tier is unbounded")


def rate_usage(component: UsageComponent, quantity: Decimal) -> Decimal:
    """Price ``quantity`` units. Returns an *unrounded* amount in minor units."""
    billable = max(Decimal(0), quantity - component.included_units)
    if billable == 0:
        return Decimal(0)
    if component.model is PriceModel.PER_UNIT:
        return billable * component.unit_price  # type: ignore[operator]
    if component.model is PriceModel.PACKAGE:
        blocks = math.ceil(billable / component.package_size)  # type: ignore[operator]
        return Decimal(blocks) * component.unit_price  # type: ignore[operator]
    if component.model is PriceModel.GRADUATED:
        return _graduated(component.tiers, billable)
    if component.model is PriceModel.VOLUME:
        return _volume(component.tiers, billable)
    raise PricingError(f"unsupported model {component.model}")
