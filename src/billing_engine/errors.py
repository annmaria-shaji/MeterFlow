class BillingError(Exception):
    """Base class for all billing-engine errors."""


class IngestionError(BillingError):
    """An usage event was rejected (unknown meter, bad quantity, id conflict...)."""


class PricingError(BillingError):
    """A plan or price component is misconfigured."""


class LedgerError(BillingError):
    """A journal entry would violate double-entry invariants, or the chain is broken."""
