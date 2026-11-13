"""Payment collection with retry schedule ("dunning")."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Protocol

from . import accounting
from .clock import Clock
from .domain import Invoice, InvoiceStatus
from .ledger import Ledger
from .store import Store


# ---------------------------------------------------------------- gateway port
@dataclass(frozen=True)
class ChargeRequest:
    customer_id: str
    amount_minor: int
    currency: str
    idempotency_key: str  # gateway must dedupe on this; we reuse it on network retries


@dataclass(frozen=True)
class ChargeResult:
    succeeded: bool
    payment_id: str | None = None
    failure_code: str | None = None  # e.g. "insufficient_funds"
    retryable: bool = True  # False for hard declines (stolen card, closed account)


class PaymentGateway(Protocol):
    def charge(self, req: ChargeRequest) -> ChargeResult: ...


class ScriptedGateway:
    """Test double: pops scripted results per customer; succeeds when script is empty."""

    def __init__(self, scripts: dict[str, list[ChargeResult]] | None = None) -> None:
        self.scripts = {k: list(v) for k, v in (scripts or {}).items()}
        self.calls: list[ChargeRequest] = []

    def charge(self, req: ChargeRequest) -> ChargeResult:
        self.calls.append(req)
        queue = self.scripts.get(req.customer_id)
        if queue:
            return queue.pop(0)
        return ChargeResult(True, payment_id=f"pay_{req.idempotency_key}")


class Notifier(Protocol):
    def payment_failed(self, invoice: Invoice, attempt: "PaymentAttempt") -> None: ...
    def invoice_uncollectible(self, invoice: Invoice) -> None: ...


class NullNotifier:
    def payment_failed(self, invoice, attempt) -> None: ...
    def invoice_uncollectible(self, invoice) -> None: ...


# ------------------------------------------------------------------- policy
@dataclass(frozen=True)
class DunningPolicy:
    # Delay before retry #1, #2, ... after each failure. Attempts = 1 + len(retry_after).
    retry_after: tuple[timedelta, ...] = (timedelta(days=1), timedelta(days=3), timedelta(days=5), timedelta(days=7))
    # TODO: suspend service after N failures, email templates per stage, grace periods.


@dataclass(frozen=True)
class PaymentAttempt:
    invoice_id: str
    attempt_no: int
    at: datetime
    succeeded: bool
    failure_code: str | None
    payment_id: str | None


class DunningService:
    def __init__(
        self,
        store: Store,
        ledger: Ledger,
        gateway: PaymentGateway,
        clock: Clock,
        policy: DunningPolicy | None = None,
        notifier: Notifier | None = None,
    ) -> None:
        self._store = store
        self._ledger = ledger
        self._gateway = gateway
        self._clock = clock
        self._policy = policy or DunningPolicy()
        self._notifier = notifier or NullNotifier()

    def collect(self, invoice_id: str) -> PaymentAttempt | None:
        """Make one collection attempt (initial or retry) for an OPEN invoice."""
        inv = self._store.invoices[invoice_id]
        if inv.status is not InvoiceStatus.OPEN or inv.outstanding_minor <= 0:
            return None

        now = self._clock.now()
        attempt_no = inv.attempt_count + 1
        result = self._gateway.charge(
            ChargeRequest(inv.customer_id, inv.outstanding_minor, inv.currency, f"{inv.id}:{attempt_no}")
        )
        inv.attempt_count = attempt_no
        attempt = PaymentAttempt(inv.id, attempt_no, now, result.succeeded, result.failure_code, result.payment_id)
        self._store.attempts.append(attempt)

        if result.succeeded:
            accounting.post_payment(self._ledger, inv, inv.outstanding_minor, now, result.payment_id or f"{inv.id}:{attempt_no}")
            inv.amount_paid_minor = inv.total_minor
            inv.status = InvoiceStatus.PAID
            inv.next_retry_at = None
            return attempt

        self._notifier.payment_failed(inv, attempt)
        retries_used = attempt_no - 1
        if result.retryable and retries_used < len(self._policy.retry_after):
            inv.next_retry_at = now + self._policy.retry_after[retries_used]
        else:
            inv.next_retry_at = None
            inv.status = InvoiceStatus.UNCOLLECTIBLE
            accounting.post_write_off(self._ledger, inv, now)
            self._notifier.invoice_uncollectible(inv)
        return attempt

    def process_due(self) -> list[PaymentAttempt]:
        """Cron entrypoint: retry every OPEN invoice whose retry time has arrived."""
        now = self._clock.now()
        due = sorted(
            (i for i in self._store.invoices.values()
             if i.status is InvoiceStatus.OPEN and i.next_retry_at is not None and i.next_retry_at <= now),
            key=lambda i: i.id,
        )
        return [a for i in due if (a := self.collect(i.id)) is not None]
