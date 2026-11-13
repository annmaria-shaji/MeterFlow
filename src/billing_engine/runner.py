"""Billing runs and deterministic replay.

A run is fully described by (period, cutoff_seq):
    * usage visible to the run = events with ingest_seq <= cutoff_seq
    * plans are immutable versions; subscriptions keep phase history
    * invoice builder is a pure function of the above
So ``replay(run_id)`` can rebuild the invoices from scratch (no side effects) and
prove they hash identically. Late events (ingest_seq > cutoff) never alter a past
run; they are billed via ``reconcile`` as an adjustment.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from . import accounting
from .clock import Clock
from .domain import BillingPeriod, Invoice, InvoiceStatus, Subscription
from .hashing import deterministic_id, fingerprint
from .invoicing import InvoiceBuilder
from .ledger import Ledger
from .store import Store


@dataclass(frozen=True)
class BillingRun:
    run_id: str
    period: BillingPeriod
    cutoff_seq: int
    inputs_fingerprint: str  # hash of everything the run read
    output_fingerprint: str  # hash of every invoice produced
    invoice_ids: tuple[str, ...]
    created_at: datetime


@dataclass(frozen=True)
class ReplayResult:
    run_id: str
    matches: bool
    inputs_changed: bool  # True => stored data was altered after the run (should never happen)
    differing_invoices: tuple[str, ...]


class BillingRunner:
    def __init__(self, store: Store, builder: InvoiceBuilder, ledger: Ledger, clock: Clock) -> None:
        self._store = store
        self._builder = builder
        self._ledger = ledger
        self._clock = clock

    # ---------------------------------------------------------------- run
    def run_period(self, period: BillingPeriod, cutoff_seq: int | None = None) -> BillingRun:
        """Finalize a period. Idempotent: same (period, cutoff) returns the same run."""
        cutoff = self._store.events.max_seq() if cutoff_seq is None else cutoff_seq
        run_id = deterministic_id("run", period.key, cutoff)
        if run_id in self._store.runs:
            return self._store.runs[run_id]

        invoices = self._build_all(period, cutoff, run_id)
        for inv in invoices:
            inv.status = InvoiceStatus.OPEN
            self._store.invoices[inv.id] = inv
            accounting.post_invoice_issued(self._ledger, inv)

        run = BillingRun(
            run_id=run_id,
            period=period,
            cutoff_seq=cutoff,
            inputs_fingerprint=self._inputs_fingerprint(period, cutoff),
            output_fingerprint=fingerprint([i.fingerprint() for i in invoices]),
            invoice_ids=tuple(i.id for i in invoices),
            created_at=self._clock.now(),
        )
        self._store.runs[run_id] = run
        return run

    # ------------------------------------------------------------- replay
    def replay(self, run_id: str) -> ReplayResult:
        """Re-rate a past period from stored inputs. Read-only; touches neither
        the invoice store nor the ledger."""
        run = self._store.runs[run_id]
        rebuilt = self._build_all(run.period, run.cutoff_seq, run.run_id)
        rebuilt_by_id = {i.id: i for i in rebuilt}

        differing = sorted(
            {
                iid
                for iid in set(rebuilt_by_id) | set(run.invoice_ids)
                if iid not in rebuilt_by_id
                or iid not in self._store.invoices
                or rebuilt_by_id[iid].fingerprint() != self._store.invoices[iid].fingerprint()
            }
        )
        inputs_changed = self._inputs_fingerprint(run.period, run.cutoff_seq) != run.inputs_fingerprint
        output_ok = fingerprint([i.fingerprint() for i in rebuilt]) == run.output_fingerprint
        return ReplayResult(run_id, not differing and output_ok and not inputs_changed, inputs_changed, tuple(differing))

    def reconcile(self, run_id: str) -> BillingRun:
        """TODO: bill late-arriving usage for an already-finalized period.

        Plan: new_cutoff = events.max_seq(); rebuild the period at new_cutoff;
        diff against the original run's invoices per subscription; emit only the
        *delta* as a supplementary invoice (or credit note if negative), with its
        own run_id, leaving the original run and its ledger entries untouched.
        """
        raise NotImplementedError

    # ------------------------------------------------------------ helpers
    def _build_all(self, period: BillingPeriod, cutoff: int, run_id: str) -> list[Invoice]:
        out = []
        for sub in self._store.subscriptions_overlapping(period.start, period.end):
            events = self._store.events.for_customer(sub.customer_id, cutoff)
            inv = self._builder.build(subscription=sub, period=period, events=events, run_id=run_id)
            if inv is not None:
                out.append(inv)
        return out

    def _inputs_fingerprint(self, period: BillingPeriod, cutoff: int) -> str:
        subs = self._store.subscriptions_overlapping(period.start, period.end)
        plans = sorted(
            {(p.plan_id, p.version) for s in subs for p in s.phases}
        )
        plan_defs = [self._store.plans.get(pid, ver) for pid, ver in plans]
        events = [
            (se.ingest_seq, se.event)
            for s in subs
            for se in self._store.events.for_customer(s.customer_id, cutoff)
            if period.contains(se.event.occurred_at)
        ]
        return fingerprint({"cutoff": cutoff, "subs": subs, "plans": plan_defs, "events": sorted(events, key=lambda x: x[0])})
