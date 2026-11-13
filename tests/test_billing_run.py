import dataclasses
from decimal import Decimal as D

import pytest

from billing_engine import UsageEvent
from billing_engine.errors import IngestionError
from billing_engine.ingestion import IngestStatus
from billing_engine.ledger import Account
from conftest import ev, utc


def _load(engine):
    engine.ingestion.ingest_batch([
        ev("e1", 100, utc(2026, 11, 5)),   # starter: 100 * 1 = 100
        ev("e2", 2500, utc(2026, 11, 20)),  # pro graduated: 1000 free, 1500 * 0.2 = 300
    ])


def test_ingestion_is_idempotent_and_rejects_conflicts(engine):
    e = ev("dup", 1, utc(2026, 11, 5))
    assert engine.ingestion.ingest(e).status is IngestStatus.ACCEPTED
    assert engine.ingestion.ingest(e).status is IngestStatus.DUPLICATE
    assert engine.store.events.max_seq() == 1
    with pytest.raises(IngestionError):
        engine.ingestion.ingest(dataclasses.replace(e, quantity=D(2)))


def test_proration_usage_and_ledger(engine, period):
    _load(engine)
    run = engine.runner.run_period(period)
    inv = engine.store.invoices[run.invoice_ids[0]]

    # fees: 1000*15/30 + 4900*15/30 = 500 + 2450; usage: 100 + 300
    assert [l.amount_minor for l in inv.lines] == [500, 100, 2450, 300]
    assert inv.total_minor == 3350

    assert engine.ledger.balance(Account.ACCOUNTS_RECEIVABLE, customer_id="cust_1") == 3350
    assert engine.ledger.balance(Account.REVENUE) == -3350
    engine.ledger.verify_chain()


def test_run_is_idempotent(engine, period):
    _load(engine)
    r1 = engine.runner.run_period(period)
    r2 = engine.runner.run_period(period)
    assert r1 is r2
    assert len(engine.ledger.entries()) == 1


def test_replay_matches_and_ignores_late_events(engine, period):
    _load(engine)
    run = engine.runner.run_period(period)

    # A late event for the same period arrives after the run.
    engine.ingestion.ingest(ev("late", 5000, utc(2026, 11, 25)))

    result = engine.runner.replay(run.run_id)
    assert result.matches and not result.inputs_changed
    # ...and replay did not touch the ledger
    assert len(engine.ledger.entries()) == 1


def test_replay_detects_tampering(engine, period):
    _load(engine)
    run = engine.runner.run_period(period)
    inv = engine.store.invoices[run.invoice_ids[0]]
    engine.store.invoices[inv.id] = dataclasses.replace(inv, subtotal_minor=inv.subtotal_minor + 1)
    result = engine.runner.replay(run.run_id)
    assert not result.matches and result.differing_invoices == (inv.id,)
