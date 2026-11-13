"""End-to-end walkthrough: ingest -> bill -> collect (with a failure) -> replay.

    python examples/demo.py
"""

from datetime import datetime, timedelta, timezone
from decimal import Decimal as D

from billing_engine import (
    BillingPeriod, Meter, Phase, PlanVersion, PriceModel, RecurringComponent,
    Subscription, Tier, UsageComponent, UsageEvent,
)
from billing_engine.clock import FixedClock
from billing_engine.dunning import ChargeResult, ScriptedGateway
from billing_engine.wiring import build_engine


def utc(y, m, d):
    return datetime(y, m, d, tzinfo=timezone.utc)


gateway = ScriptedGateway({"acme": [ChargeResult(False, failure_code="insufficient_funds")]})
eng = build_engine({"api_calls": Meter("api_calls", unit="calls")}, clock=FixedClock(utc(2026, 12, 1)), gateway=gateway)

eng.store.plans.add(PlanVersion(
    "pro", 1, "EUR",
    recurring=(RecurringComponent("Platform fee", 4900),),
    usage=(UsageComponent("API calls", "api_calls", PriceModel.GRADUATED,
                          tiers=(Tier(D(1000), D(0)), Tier(D(10000), D("0.2")), Tier(None, D("0.1")))),),
))
# Joined mid-month -> first invoice is prorated.
eng.store.subscriptions["sub_acme"] = Subscription("sub_acme", "acme", (Phase("pro", 1, utc(2026, 11, 11)),))

eng.ingestion.ingest_batch([
    UsageEvent("evt-1", "acme", "api_calls", D(4000), utc(2026, 11, 12)),
    UsageEvent("evt-2", "acme", "api_calls", D(1500), utc(2026, 11, 28)),
])

run = eng.runner.run_period(BillingPeriod.calendar_month(2026, 11))
inv = eng.store.invoices[run.invoice_ids[0]]
print(f"Invoice {inv.id}")
for line in inv.lines:
    print(f"  {line.amount_minor / 100:>9.2f}  {line.description}")
print(f"  {inv.total_minor / 100:>9.2f}  TOTAL {inv.currency}\n")

eng.dunning.collect(inv.id)
print(f"after attempt 1: {inv.status.value}, next retry {inv.next_retry_at:%Y-%m-%d}")
eng.clock.advance(timedelta(days=1))
eng.dunning.process_due()
print(f"after retry:     {inv.status.value}\n")

print("replay matches original run:", eng.runner.replay(run.run_id).matches)
eng.ledger.verify_chain()
print("ledger hash chain OK; trial balance:", {a.name: v for a, v in eng.ledger.trial_balance().items() if v})
