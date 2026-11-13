from datetime import timedelta

from billing_engine import InvoiceStatus
from billing_engine.dunning import ChargeResult, ScriptedGateway
from billing_engine.ledger import Account
from billing_engine.wiring import build_engine
from conftest import ev, utc
from billing_engine import Meter


def _engine_with_invoice(engine_fixture, period, script):
    gw = ScriptedGateway({"cust_1": script})
    engine_fixture.dunning._gateway = gw
    engine_fixture.ingestion.ingest(ev("e1", 100, utc(2026, 11, 5)))
    run = engine_fixture.runner.run_period(period)
    return run.invoice_ids[0], gw


FAIL = ChargeResult(False, failure_code="insufficient_funds")


def test_retry_then_success(engine, period):
    inv_id, gw = _engine_with_invoice(engine, period, [FAIL, FAIL])
    inv = engine.store.invoices[inv_id]

    engine.dunning.collect(inv_id)
    assert inv.status is InvoiceStatus.OPEN and inv.next_retry_at == engine.clock.now() + timedelta(days=1)

    assert engine.dunning.process_due() == []  # not yet due
    engine.clock.advance(timedelta(days=1))
    engine.dunning.process_due()
    engine.clock.advance(timedelta(days=3))
    engine.dunning.process_due()  # third attempt succeeds

    assert inv.status is InvoiceStatus.PAID
    assert engine.ledger.balance(Account.ACCOUNTS_RECEIVABLE, customer_id="cust_1") == 0
    assert len({c.idempotency_key for c in gw.calls}) == 3


def test_exhausted_retries_write_off(engine, period):
    inv_id, _ = _engine_with_invoice(engine, period, [FAIL] * 10)
    inv = engine.store.invoices[inv_id]
    engine.dunning.collect(inv_id)
    for _ in range(10):
        engine.clock.advance(timedelta(days=7))
        engine.dunning.process_due()
    assert inv.status is InvoiceStatus.UNCOLLECTIBLE
    assert inv.attempt_count == 5
    assert engine.ledger.balance(Account.BAD_DEBT_EXPENSE) == inv.total_minor
    assert engine.ledger.balance(Account.ACCOUNTS_RECEIVABLE) == 0


def test_hard_decline_stops_immediately(engine, period):
    inv_id, _ = _engine_with_invoice(engine, period, [ChargeResult(False, failure_code="stolen_card", retryable=False)])
    engine.dunning.collect(inv_id)
    assert engine.store.invoices[inv_id].status is InvoiceStatus.UNCOLLECTIBLE
