from decimal import Decimal as D

import pytest

from billing_engine import PriceModel, Tier, UsageComponent
from billing_engine.errors import PricingError
from billing_engine.pricing import rate_usage

TIERS = (Tier(D(100), D(1)), Tier(D(500), D("0.5")), Tier(None, D("0.1")))


def comp(model, **kw):
    return UsageComponent("x", "m", model, **kw)


def test_per_unit_with_allowance():
    c = comp(PriceModel.PER_UNIT, unit_price=D(2), included_units=D(10))
    assert rate_usage(c, D(15)) == D(10)
    assert rate_usage(c, D(5)) == D(0)


def test_graduated():
    c = comp(PriceModel.GRADUATED, tiers=TIERS)
    # 100*1 + 400*0.5 + 100*0.1
    assert rate_usage(c, D(600)) == D(100) + D(200) + D(10)


def test_volume_prices_all_units_at_reached_tier():
    c = comp(PriceModel.VOLUME, tiers=TIERS)
    assert rate_usage(c, D(250)) == D(125)


def test_package_rounds_up_blocks():
    c = comp(PriceModel.PACKAGE, unit_price=D(300), package_size=D(1000))
    assert rate_usage(c, D(1)) == D(300)
    assert rate_usage(c, D(1001)) == D(600)


def test_last_tier_must_be_unbounded():
    with pytest.raises(PricingError):
        comp(PriceModel.GRADUATED, tiers=(Tier(D(10), D(1)),))
