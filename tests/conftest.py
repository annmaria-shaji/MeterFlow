from datetime import datetime, timezone
from decimal import Decimal

import pytest

from billing_engine import (
    BillingPeriod, Meter, Phase, PlanVersion, PriceModel, RecurringComponent,
    Subscription, Tier, UsageComponent, UsageEvent,
)
from billing_engine.clock import FixedClock
from billing_engine.wiring import build_engine

D = Decimal


def utc(y, m, d, h=0):
    return datetime(y, m, d, h, tzinfo=timezone.utc)


@pytest.fixture
def period():
    return BillingPeriod.calendar_month(2026, 11)  # 30 days


@pytest.fixture
def engine():
    meters = {"api_calls": Meter("api_calls", unit="calls")}
    eng = build_engine(meters, clock=FixedClock(utc(2026, 12, 1)))
    eng.store.plans.add(PlanVersion(
        "starter", 1, "EUR",
        recurring=(RecurringComponent("Platform fee", 1000),),
        usage=(UsageComponent("API calls", "api_calls", PriceModel.PER_UNIT, unit_price=D(1)),),
    ))
    eng.store.plans.add(PlanVersion(
        "pro", 1, "EUR",
        recurring=(RecurringComponent("Platform fee", 4900),),
        usage=(UsageComponent(
            "API calls", "api_calls", PriceModel.GRADUATED,
            tiers=(Tier(D(1000), D(0)), Tier(D(10000), D("0.2")), Tier(None, D("0.1"))),
        ),),
    ))
    # upgrade on Nov 16 -> 15/30 of the month on each plan
    eng.store.subscriptions["sub_1"] = Subscription("sub_1", "cust_1", (
        Phase("starter", 1, utc(2026, 1, 1), utc(2026, 11, 16)),
        Phase("pro", 1, utc(2026, 11, 16)),
    ))
    return eng


def ev(event_id, qty, at, customer="cust_1"):
    return UsageEvent(event_id, customer, "api_calls", D(qty), at)
