"""Business events -> journal entries. The only place that knows the chart of accounts."""

from __future__ import annotations

from datetime import datetime

from .domain import Invoice
from .ledger import Account, JournalEntry, Ledger, Posting


def post_invoice_issued(ledger: Ledger, inv: Invoice) -> JournalEntry | None:
    """Dr Accounts Receivable / Cr Revenue (+ Cr Tax Payable)."""
    if inv.total_minor <= 0:
        return None  # TODO: negative invoices -> credit-note flow (Cr AR / Dr Revenue)
    postings = [Posting(Account.ACCOUNTS_RECEIVABLE, debit_minor=inv.total_minor, customer_id=inv.customer_id)]
    if inv.subtotal_minor > 0:
        postings.append(Posting(Account.REVENUE, credit_minor=inv.subtotal_minor, customer_id=inv.customer_id))
    if inv.tax_minor > 0:
        postings.append(Posting(Account.TAX_PAYABLE, credit_minor=inv.tax_minor, customer_id=inv.customer_id))
    return ledger.post(
        occurred_at=inv.issued_at,
        currency=inv.currency,
        memo=f"Invoice {inv.id} issued",
        reference=inv.id,
        idempotency_key=f"invoice-issued:{inv.id}",
        postings=postings,
    )


def post_payment(ledger: Ledger, inv: Invoice, amount_minor: int, at: datetime, payment_id: str) -> JournalEntry:
    """Dr Cash / Cr Accounts Receivable."""
    return ledger.post(
        occurred_at=at,
        currency=inv.currency,
        memo=f"Payment {payment_id} for invoice {inv.id}",
        reference=inv.id,
        idempotency_key=f"payment:{payment_id}",
        postings=[
            Posting(Account.CASH, debit_minor=amount_minor),
            Posting(Account.ACCOUNTS_RECEIVABLE, credit_minor=amount_minor, customer_id=inv.customer_id),
        ],
    )


def post_write_off(ledger: Ledger, inv: Invoice, at: datetime) -> JournalEntry:
    """Dr Bad Debt Expense / Cr Accounts Receivable for the outstanding balance.
    TODO: reverse the tax portion where the jurisdiction allows bad-debt relief."""
    amount = inv.outstanding_minor
    return ledger.post(
        occurred_at=at,
        currency=inv.currency,
        memo=f"Write-off of invoice {inv.id}",
        reference=inv.id,
        idempotency_key=f"write-off:{inv.id}",
        postings=[
            Posting(Account.BAD_DEBT_EXPENSE, debit_minor=amount),
            Posting(Account.ACCOUNTS_RECEIVABLE, credit_minor=amount, customer_id=inv.customer_id),
        ],
    )
