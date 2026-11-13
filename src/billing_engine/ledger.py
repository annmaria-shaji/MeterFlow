"""Append-only, double-entry ledger with a tamper-evident hash chain.

Invariants (enforced in ``post``):
  * >= 2 postings, each with exactly one of debit/credit > 0 (integer minor units)
  * sum(debits) == sum(credits), single currency per entry
  * entries are never mutated or deleted; corrections are *reversal* entries
  * idempotent on ``idempotency_key``
  * each entry's hash covers its content and the previous entry's hash
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import Enum

from .errors import LedgerError
from .hashing import fingerprint

GENESIS_HASH = "0" * 64


class Account(str, Enum):
    CASH = "1000"
    ACCOUNTS_RECEIVABLE = "1100"  # debit-normal; sub-ledger by customer_id
    TAX_PAYABLE = "2100"  # credit-normal
    CUSTOMER_CREDIT = "2200"  # credit-normal (credit notes, overpayments)
    REVENUE = "4000"  # credit-normal
    BAD_DEBT_EXPENSE = "6100"  # debit-normal
    # TODO: DEFERRED_REVENUE for in-advance fees, PAYMENT_FEES, REFUNDS


@dataclass(frozen=True)
class Posting:
    account: Account
    debit_minor: int = 0
    credit_minor: int = 0
    customer_id: str | None = None


@dataclass(frozen=True)
class JournalEntry:
    entry_id: str
    seq: int
    occurred_at: datetime
    currency: str
    memo: str
    reference: str  # e.g. invoice id / payment id
    idempotency_key: str
    postings: tuple[Posting, ...]
    reverses: str | None
    prev_hash: str
    hash: str


class Ledger:
    def __init__(self) -> None:
        self._entries: list[JournalEntry] = []
        self._by_key: dict[str, JournalEntry] = {}

    # ------------------------------------------------------------- writing
    def post(
        self,
        *,
        occurred_at: datetime,
        currency: str,
        memo: str,
        reference: str,
        idempotency_key: str,
        postings: list[Posting],
        reverses: str | None = None,
    ) -> JournalEntry:
        if idempotency_key in self._by_key:
            return self._by_key[idempotency_key]
        self._check(postings)
        seq = len(self._entries) + 1
        prev = self._entries[-1].hash if self._entries else GENESIS_HASH
        body = dict(
            entry_id=f"JE-{seq:08d}",
            seq=seq,
            occurred_at=occurred_at,
            currency=currency,
            memo=memo,
            reference=reference,
            idempotency_key=idempotency_key,
            postings=tuple(postings),
            reverses=reverses,
            prev_hash=prev,
        )
        entry = JournalEntry(**body, hash=fingerprint(body))
        self._entries.append(entry)
        self._by_key[idempotency_key] = entry
        return entry

    def reverse(self, entry_id: str, *, occurred_at: datetime, idempotency_key: str, memo: str = "") -> JournalEntry:
        orig = self.get(entry_id)
        flipped = [
            Posting(p.account, debit_minor=p.credit_minor, credit_minor=p.debit_minor, customer_id=p.customer_id)
            for p in orig.postings
        ]
        return self.post(
            occurred_at=occurred_at,
            currency=orig.currency,
            memo=memo or f"Reversal of {orig.entry_id}",
            reference=orig.reference,
            idempotency_key=idempotency_key,
            postings=flipped,
            reverses=orig.entry_id,
        )

    @staticmethod
    def _check(postings: list[Posting]) -> None:
        if len(postings) < 2:
            raise LedgerError("an entry needs at least two postings")
        for p in postings:
            if (p.debit_minor > 0) == (p.credit_minor > 0) or p.debit_minor < 0 or p.credit_minor < 0:
                raise LedgerError(f"posting must have exactly one positive side: {p}")
        if sum(p.debit_minor for p in postings) != sum(p.credit_minor for p in postings):
            raise LedgerError("entry is unbalanced")

    # ------------------------------------------------------------- reading
    def get(self, entry_id: str) -> JournalEntry:
        for e in self._entries:
            if e.entry_id == entry_id:
                return e
        raise LedgerError(f"no such entry {entry_id}")

    def entries(self) -> tuple[JournalEntry, ...]:
        return tuple(self._entries)

    def balance(self, account: Account, *, customer_id: str | None = None, as_of: datetime | None = None) -> int:
        """Debit-minus-credit. Credit-normal accounts (revenue...) are negative."""
        total = 0
        for e in self._entries:
            if as_of is not None and e.occurred_at > as_of:
                continue
            for p in e.postings:
                if p.account is account and (customer_id is None or p.customer_id == customer_id):
                    total += p.debit_minor - p.credit_minor
        return total

    def trial_balance(self) -> dict[Account, int]:
        tb = {a: self.balance(a) for a in Account}
        assert sum(tb.values()) == 0, "ledger out of balance"
        return tb

    def verify_chain(self) -> None:
        prev = GENESIS_HASH
        for e in self._entries:
            body = dict(
                entry_id=e.entry_id, seq=e.seq, occurred_at=e.occurred_at, currency=e.currency,
                memo=e.memo, reference=e.reference, idempotency_key=e.idempotency_key,
                postings=e.postings, reverses=e.reverses, prev_hash=prev,
            )
            if e.prev_hash != prev or e.hash != fingerprint(body):
                raise LedgerError(f"hash chain broken at {e.entry_id}")
            prev = e.hash
        # Production: persist with INSERT-only grants, no UPDATE/DELETE, and
        # periodically anchor the head hash somewhere external.
