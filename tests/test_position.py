from datetime import datetime, timedelta, timezone

import pytest

from cryptoptions import OptionType, price
from cryptoptions.position import Leg, Position, PositionError

NOW = datetime(2026, 10, 24, 8, tzinfo=timezone.utc)
EXP = NOW + timedelta(days=30)
F = 100_000.0
T = 30 / 365


def leg(kind, strike, side=1, qty=1.0, iv=0.5):
    k = OptionType.parse(kind)
    return Leg(k, strike, EXP, qty, side, price(F, strike, T, iv, k), iv)


def test_long_call_basics():
    p = Position([leg("C", 110_000)])
    a = p.analyze(F, NOW)
    assert a.max_loss == pytest.approx(p.net_premium)
    assert a.max_gain is None
    assert a.breakevens == [pytest.approx(110_000 + p.net_premium)]
    assert a.now.pnl == pytest.approx(0.0, abs=1e-6)
    assert 0 < a.prob_profit_at_expiry < 0.5
    assert len(a.grid_pnl) == len(a.grid_days) and len(a.grid_pnl[0]) == len(a.grid_prices)
    # last grid row is expiry, which matches the payoff
    for px, v in zip(a.grid_prices, a.grid_pnl[-1]):
        assert v == pytest.approx(p.payoff_at_expiry(px), abs=1e-6)


def test_debit_spread_capped():
    p = Position([leg("C", 100_000), leg("C", 120_000, side=-1, iv=0.52)])
    assert p.max_gain() == pytest.approx(20_000 - p.net_premium)
    assert len(p.breakevens()) == 1


def test_straddle_two_breakevens_and_time_decay():
    p = Position([leg("C", 100_000), leg("P", 100_000)])
    b = p.breakevens()
    assert b[0] == pytest.approx(100_000 - p.net_premium)
    assert b[1] == pytest.approx(100_000 + p.net_premium)
    a = p.analyze(F, NOW, days_elapsed=15)
    assert a.scenario.pnl < 0
    assert a.now.theta < 0
    assert a.sensitivities[0]["lever"] in {"price", "time", "iv"}


def test_iv_change_moves_value():
    p = Position([leg("C", 100_000)])
    up = p.analyze(F, NOW, iv_change=10).scenario.pnl
    down = p.analyze(F, NOW, iv_change=-10).scenario.pnl
    assert up > 0 > down


def test_probability_matches_lognormal_for_single_call():
    from cryptoptions.black76 import norm_cdf
    import math
    p = Position([leg("C", 110_000)])
    be = p.breakevens()[0]
    sd = 0.5 * math.sqrt(T)
    expected = norm_cdf((math.log(F / be) - 0.5 * sd * sd) / sd)
    assert p.prob_profit_at_expiry(F, 0.5, T) == pytest.approx(expected, rel=1e-9)


@pytest.mark.parametrize("legs, msg", [
    ([("C", 100_000, -1)], "premium"),
    ([("P", 80_000, -1), ("C", 100_000, 1)], "more than the premium"),
    ([("C", 100_000, 1), ("C", 110_000, -1, 2.0)], "calls are sold"),
])
def test_rejects_short_premium(legs, msg):
    built = [leg(k, s, side, *(rest or [])) for k, s, side, *rest in legs]
    with pytest.raises(PositionError, match=msg):
        Position(built)


def test_rejects_mixed_expiries():
    other = Leg(OptionType.CALL, 100_000, EXP + timedelta(days=7), 1, 1, 1000, 0.5)
    with pytest.raises(PositionError, match="expiry"):
        Position([leg("C", 100_000), other])


# ---- fees -------------------------------------------------------------------

from cryptoptions.position import FeeSchedule  # noqa: E402


def test_fee_formulas_match_bybit_examples():
    maker = FeeSchedule(trade_rate=0.0002)
    # Bybit help centre: 0.3 BTC call, index 42,000
    assert maker.trade(42_000, 3_000, 0.3) == pytest.approx(2.52)   # rate x index binds
    assert maker.trade(42_000, 30, 0.3) == pytest.approx(0.63)      # 7% of premium binds
    fees = FeeSchedule(delivery_rate=0.00015)
    assert fees.delivery(46_000, 1_050, 0.3) == pytest.approx(2.07)
    assert fees.delivery(40_000, 2_950, 0.3) == pytest.approx(1.8)
    assert fees.delivery(40_000, 0.0, 0.3) == 0.0                    # expired worthless: no fee


def test_bybit_schedule_by_coin_and_expiry():
    friday = datetime(2026, 11, 6, 8, tzinfo=timezone.utc)
    tuesday = datetime(2026, 11, 3, 8, tzinfo=timezone.utc)
    assert FeeSchedule.bybit("BTC", friday).delivery_rate == 0.00015
    assert FeeSchedule.bybit("SOL", friday).delivery_rate == 0.0002
    assert FeeSchedule.bybit("BTC", friday).charge_delivery
    assert not FeeSchedule.bybit("BTC", tuesday).charge_delivery


def test_fees_reduce_pnl_and_move_breakeven():
    legs = [leg("C", 110_000)]
    plain = Position(legs)
    fees = FeeSchedule()
    paid = Position(legs, fees, index=F)
    entry = fees.trade(F, legs[0].entry_price, 1)
    assert paid.entry_fees == pytest.approx(entry)
    assert paid.max_loss() == pytest.approx(plain.net_premium + entry)
    assert paid.breakevens()[0] > plain.breakevens()[0]
    a = paid.analyze(F, NOW)
    # closing straight away costs the opening and closing fees
    assert a.now.pnl == pytest.approx(-(entry + a.exit_fees_now), rel=1e-9)
    # expiry row of the grid includes the settlement fee
    for px, v in zip(a.grid_prices, a.grid_pnl[-1]):
        assert v == pytest.approx(paid.payoff_at_expiry(px), abs=1e-6)
    deep = 150_000
    assert paid.payoff_at_expiry(deep) == pytest.approx(
        plain.payoff_at_expiry(deep) - entry - fees.delivery(deep, deep - 110_000, 1))


def test_fees_need_an_index():
    with pytest.raises(ValueError):
        Position([leg("C", 110_000)], FeeSchedule())
