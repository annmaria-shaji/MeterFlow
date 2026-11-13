import dataclasses

import pytest

from billing_engine.errors import LedgerError
from billing_engine.ledger import Account, Ledger, Posting
from conftest import utc


def _post(ledger, key="k1", amt=100):
    return ledger.post(
        occurred_at=utc(2026, 1, 1), currency="EUR", memo="m", reference="r", idempotency_key=key,
        postings=[Posting(Account.ACCOUNTS_RECEIVABLE, debit_minor=amt), Posting(Account.REVENUE, credit_minor=amt)],
    )


def test_unbalanced_rejected():
    with pytest.raises(LedgerError):
        Ledger().post(
            occurred_at=utc(2026, 1, 1), currency="EUR", memo="", reference="", idempotency_key="x",
            postings=[Posting(Account.CASH, debit_minor=5), Posting(Account.REVENUE, credit_minor=4)],
        )


def test_idempotent_and_balanced():
    l = Ledger()
    assert _post(l) is _post(l)
    assert len(l.entries()) == 1
    assert sum(l.trial_balance().values()) == 0


def test_reversal_nets_to_zero():
    l = Ledger()
    e = _post(l)
    l.reverse(e.entry_id, occurred_at=utc(2026, 1, 2), idempotency_key="rev")
    assert l.balance(Account.REVENUE) == 0


def test_tampering_detected():
    l = Ledger()
    _post(l, "a")
    _post(l, "b", 50)
    l.verify_chain()
    l._entries[0] = dataclasses.replace(l._entries[0], memo="edited")
    with pytest.raises(LedgerError):
        l.verify_chain()
