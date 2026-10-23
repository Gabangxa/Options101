import math

import pytest

from cryptoptions import DAYS_PER_YEAR, OptionType, greeks, implied_vol, intrinsic, price
from cryptoptions.black76 import norm_cdf

CASES = [
    # forward, strike, t (years), vol, rate
    (100_000.0, 100_000.0, 30 / 365, 0.55, 0.0),
    (100_000.0, 120_000.0, 7 / 365, 0.45, 0.0),
    (3_500.0, 3_000.0, 90 / 365, 0.70, 0.03),
    (150.0, 180.0, 1 / 365, 0.90, 0.0),
    (0.20, 0.25, 14 / 365, 1.10, 0.0),
]


@pytest.mark.parametrize("f,k,t,v,r", CASES)
def test_put_call_parity(f, k, t, v, r):
    call = price(f, k, t, v, "C", r)
    put = price(f, k, t, v, "P", r)
    assert call - put == pytest.approx(math.exp(-r * t) * (f - k), rel=1e-10, abs=1e-9)


def test_atm_closed_form():
    # With rate 0 and strike equal to forward, the call is F * (2 N(vol sqrt(T) / 2) - 1).
    f, t, v = 100_000.0, 0.25, 0.6
    expected = f * (2 * norm_cdf(v * math.sqrt(t) / 2) - 1)
    assert price(f, f, t, v, OptionType.CALL) == pytest.approx(expected, rel=1e-12)


def test_textbook_futures_option():
    # Hull, futures option example: F=620, K=600, r=5%, vol=20%, T=0.5, call about 44.19.
    assert price(620.0, 600.0, 0.5, 0.20, "C", 0.05) == pytest.approx(44.19, abs=0.01)


@pytest.mark.parametrize("f,k,t,v,r", CASES)
@pytest.mark.parametrize("kind", ["C", "P"])
def test_greeks_match_finite_differences(f, k, t, v, r, kind):
    g = greeks(f, k, t, v, kind, r)
    hf = f * 1e-4
    up = price(f + hf, k, t, v, kind, r)
    down = price(f - hf, k, t, v, kind, r)
    mid = price(f, k, t, v, kind, r)
    assert g.price == pytest.approx(mid)
    assert g.delta == pytest.approx((up - down) / (2 * hf), rel=1e-5, abs=1e-9)
    assert g.gamma == pytest.approx((up - 2 * mid + down) / (hf * hf), rel=1e-3, abs=1e-12)

    hv = 1e-5
    dv = (price(f, k, t, v + hv, kind, r) - price(f, k, t, v - hv, kind, r)) / (2 * hv)
    assert g.vega == pytest.approx(dv / 100.0, rel=1e-5, abs=1e-12)

    one_day = 1 / DAYS_PER_YEAR
    ht = min(one_day / 100, t / 1000)
    dt = (price(f, k, t - ht, v, kind, r) - price(f, k, t + ht, v, kind, r)) / (2 * ht)
    assert g.theta == pytest.approx(dt / DAYS_PER_YEAR, rel=1e-4, abs=1e-12)


def test_long_option_decays():
    g = greeks(100_000.0, 110_000.0, 30 / 365, 0.5, "C")
    assert g.theta < 0
    assert g.vega > 0
    assert 0 < g.delta < 1


def test_expiry_and_zero_vol_return_intrinsic():
    assert price(110.0, 100.0, 0.0, 0.5, "C") == 10.0
    assert price(90.0, 100.0, 0.0, 0.5, "C") == 0.0
    assert price(90.0, 100.0, 0.5, 0.0, "P") == pytest.approx(10.0)
    g = greeks(110.0, 100.0, 0.0, 0.5, "C")
    assert (g.delta, g.gamma, g.vega, g.theta) == (1.0, 0.0, 0.0, 0.0)


@pytest.mark.parametrize("f,k,t,v,r", CASES)
@pytest.mark.parametrize("kind", ["C", "P"])
def test_implied_vol_round_trip(f, k, t, v, r, kind):
    premium = price(f, k, t, v, kind, r)
    if premium - intrinsic(f, k, kind, r, t) < 1e-9 * f:
        pytest.skip("no time value left to invert")
    assert implied_vol(premium, f, k, t, kind, r) == pytest.approx(v, rel=1e-6)


@pytest.mark.parametrize("vol", [0.05, 0.3, 1.0, 2.5, 5.0])
def test_implied_vol_across_vol_levels(vol):
    f, k, t = 100_000.0, 105_000.0, 20 / 365
    premium = price(f, k, t, vol, "C")
    assert implied_vol(premium, f, k, t, "C") == pytest.approx(vol, rel=1e-6)


def test_implied_vol_rejects_arbitrage():
    f, k, t = 100.0, 90.0, 0.5
    with pytest.raises(ValueError):
        implied_vol(5.0, f, k, t, "C")  # below intrinsic of 10
    with pytest.raises(ValueError):
        implied_vol(100.0, f, k, t, "C")  # a call cannot be worth the forward
    assert implied_vol(10.0, f, k, t, "C") == 0.0


def test_bad_inputs():
    with pytest.raises(ValueError):
        price(-1.0, 100.0, 0.5, 0.5, "C")
    with pytest.raises(ValueError):
        price(100.0, 100.0, -0.1, 0.5, "C")
    with pytest.raises(ValueError):
        price(100.0, 100.0, 0.5, 0.5, "X")
