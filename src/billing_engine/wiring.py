"""Convenience composition root: builds a fully wired in-memory engine.
Used by the demo and the tests; real deployments wire their own."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Mapping

from .clock import Clock, FixedClock
from .domain import Meter
from .dunning import DunningPolicy, DunningService, PaymentGateway, ScriptedGateway
from .ingestion import IngestionService
from .invoicing import InvoiceBuilder, TaxCalculator
from .ledger import Ledger
from .runner import BillingRunner
from .store import Store


@dataclass
class Engine:
    clock: Clock
    store: Store
    ledger: Ledger
    ingestion: IngestionService
    runner: BillingRunner
    dunning: DunningService


def build_engine(
    meters: Mapping[str, Meter],
    *,
    clock: Clock | None = None,
    gateway: PaymentGateway | None = None,
    tax: TaxCalculator | None = None,
    dunning_policy: DunningPolicy | None = None,
) -> Engine:
    clock = clock or FixedClock(datetime(2026, 1, 1, tzinfo=timezone.utc))
    store = Store(clock)
    ledger = Ledger()
    builder = InvoiceBuilder(store.plans, meters, tax=tax)
    return Engine(
        clock=clock,
        store=store,
        ledger=ledger,
        ingestion=IngestionService(store.events, meters),
        runner=BillingRunner(store, builder, ledger, clock),
        dunning=DunningService(store, ledger, gateway or ScriptedGateway(), clock, dunning_policy),
    )
