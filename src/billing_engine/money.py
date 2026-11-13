"""Money helpers.

Convention: every monetary amount that is *stored* is an ``int`` in minor units
(cents). Unit prices and intermediate rating results are ``Decimal`` / ``Fraction``
in minor units and are rounded exactly once, at the invoice-line level, using
banker's rounding. Floats are never used.
"""

from __future__ import annotations

from decimal import ROUND_HALF_EVEN, Decimal
from fractions import Fraction


def to_minor(amount: Decimal | Fraction | int) -> int:
    """Round an exact amount (in minor units) to an integer, half-even."""
    if isinstance(amount, int):
        return amount
    if isinstance(amount, Fraction):
        return round(amount)  # Fraction.__round__ is half-even and returns int
    return int(amount.quantize(Decimal(1), rounding=ROUND_HALF_EVEN))
