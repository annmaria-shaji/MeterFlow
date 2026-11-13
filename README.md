# billing-engine (skeleton)

A usage-based billing engine in plain Python (stdlib only). It ingests metered
usage, rates it against versioned plans, produces invoices, books everything in a
double-entry ledger, collects payment with dunning retries, and can deterministically
replay any past billing period.

```
pip install -e ".[dev]"
pytest
python examples/demo.py
```

## Layout

| Module | Responsibility |
|---|---|
| `domain.py` | Immutable types: events, meters, plans (versioned), subscriptions/phases, periods, invoices |
| `ingestion.py` | Validate + dedupe (idempotent on `event_id`), aggregation (sum/max/count) |
| `pricing.py` | Plan registry; pure rating: per-unit, graduated, volume, package, included allowance |
| `invoicing.py` | Phases + usage -> invoice lines; time-based proration; tax port |
| `ledger.py` | Append-only double-entry journal, hash chain, reversals, trial balance |
| `accounting.py` | Business events -> journal entries (chart of accounts lives here only) |
| `runner.py` | `run_period` (finalize) and `replay` (re-rate, read-only, compare hashes) |
| `dunning.py` | Gateway port, retry schedule, write-off, notifier port |
| `store.py` | In-memory persistence; swap for a real DB |
| `wiring.py` | Composition root for demo/tests |

## How determinism works

A run is a pure function of `(period, cutoff_seq)`:

* **Two time axes.** Events carry `occurred_at` (event time) and a gapless `ingest_seq`.
  A run pins `cutoff_seq`, so late events can't change a finished run.
* **Immutable inputs.** Plan versions never change; subscriptions keep phase history.
* **Pure builder.** No clock reads, no floats, ordered iteration, UUIDv5 ids, one
  half-even rounding per line.
* **Proof.** Each run stores an `inputs_fingerprint` and `output_fingerprint`;
  `replay()` rebuilds from scratch and compares. A mismatch with unchanged inputs
  means the rating *code* changed; a changed inputs hash means data was tampered with.

## Proration

Subscription changes (upgrade, downgrade, cancel) are phase boundaries. Each phase's
recurring fees are scaled by `segment_seconds / period_seconds` (exact `Fraction`),
and usage is rated per phase with that phase's plan.

## Skeleton gaps / next steps

- `BillingRunner.reconcile`: bill late events as a delta invoice / credit note.
- Credit-on-downgrade for fees billed in advance; deferred-revenue accounting.
- Negative invoices and credit notes; refunds; partial payments.
- Real persistence (Postgres: `UNIQUE(event_id)`, insert-only ledger table, safe cutoff
  selection under concurrent writers), outbox for events/webhooks.
- Tax provider integration (record its response as a run input).
- Billing-anchor logic other than calendar months, multi-currency, FX.
- Dunning: per-stage emails, service suspension, payment-method updates.
- Rating code versioning (stamp a rater version on each run so a replay can tell
  "inputs changed" from "code changed").
