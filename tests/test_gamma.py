import math
from datetime import date, datetime, timedelta, timezone

import pytest

from cryptoptions import OptionType, price
from cryptoptions.gamma import breakeven_move, daily_moves_pct, gross_gamma, realized_vol, rms_move_pct
from cryptoptions.market import BybitSource, DemoSource, parse_daily_klines
from cryptoptions.position import Leg, Position

NOW = datetime(2026, 10, 24, 8, tzinfo=timezone.utc)
EXP = NOW + timedelta(days=30)
F = 100_000.0
T = 30 / 365
IV = 0.48


def leg(kind, strike, side=1, iv=IV):
    k = OptionType.parse(kind)
    return Leg(k, strike, EXP, 1.0, side, price(F, strike, T, iv, k), iv)


def alternating(n_returns, step=0.01, start=100.0):
    """Closes that go up ``step`` then down ``step``, repeatedly."""
    closes = [start]
    for i in range(n_returns):
        closes.append(closes[-1] * (1 + step if i % 2 == 0 else 1 - step))
    return closes


# ---- break-even daily move ------------------------------------------------


def test_atm_straddle_breakeven_is_the_implied_daily_move():
    p = Position([leg("C", F), leg("P", F)])
    a = p.analyze(F, NOW)
    # nothing is sold, so no gamma is offset and the gross gamma changes nothing
    assert gross_gamma(p, F, NOW) == pytest.approx(a.now.gamma)
    move = breakeven_move(a.now.gamma, a.now.theta, gross_gamma(p, F, NOW))
    # Exact rather than approximate: with zero rate, Black-76 theta is
    # -0.5 * iv**2 * F**2 * gamma per year, so the ratio leaves F * iv / sqrt(365).
    assert move == pytest.approx(F * IV / math.sqrt(365), rel=1e-6)
    assert move / F == pytest.approx(0.025, abs=0.001)    # about 2.5% a day at 48% IV
    # at that move, half gamma times the move squared pays exactly one day of theta
    assert 0.5 * a.now.gamma * move ** 2 == pytest.approx(-a.now.theta)


def test_debit_spread_above_its_short_strike_is_not_long_gamma():
    p = Position([leg("C", 90_000), leg("C", 98_000, side=-1)])
    a = p.analyze(F, NOW)
    assert a.now.gamma < 0
    assert breakeven_move(a.now.gamma, a.now.theta) is None
    assert breakeven_move(0.0, -10.0) is None


def test_spread_whose_legs_nearly_cancel_is_not_long_gamma():
    # Skew leaves the sold call's gamma a hair under the bought one's: net gamma is
    # positive but tiny, so the quadratic alone asks for an absurd daily move.
    p = Position([leg("C", 100_000, iv=0.48), leg("C", 105_000, side=-1, iv=0.46)])
    a = p.analyze(F, NOW)
    assert 0 < a.now.gamma < 0.01 * gross_gamma(p, F, NOW)
    assert breakeven_move(a.now.gamma, a.now.theta) / F > 0.15
    assert breakeven_move(a.now.gamma, a.now.theta, gross_gamma(p, F, NOW)) is None


def test_spread_with_its_sold_strike_well_away_is_long_gamma():
    p = Position([leg("C", 104_000), leg("C", 115_000, side=-1)])
    a = p.analyze(F, NOW)
    move = breakeven_move(a.now.gamma, a.now.theta, gross_gamma(p, F, NOW))
    # with one volatility for both legs, any mix of options breaks even at the implied daily move
    assert move == pytest.approx(F * IV / math.sqrt(365), rel=1e-6)


def test_no_time_decay_means_a_zero_breakeven():
    assert breakeven_move(1e-6, 5.0) == 0.0
    # a positive zero: JSON would show a negative one as -0.0
    assert math.copysign(1.0, breakeven_move(1e-6, 0.0)) == 1.0


# ---- realized volatility and daily moves ---------------------------------


def test_realized_vol_of_alternating_one_percent_moves():
    closes = alternating(30)
    expected = math.sqrt(365 * (math.log(1.01) ** 2 + math.log(0.99) ** 2) / 2)
    assert realized_vol(closes, 30) == pytest.approx(expected, rel=1e-12)
    assert realized_vol(closes, 30) == pytest.approx(0.01 * math.sqrt(365), rel=0.001)


def test_realized_vol_uses_only_the_last_window():
    calm_then_wild = [100.0] * 20 + alternating(7, step=0.03)
    expected = realized_vol(alternating(7, step=0.03), 7)
    assert realized_vol(calm_then_wild, 7) == pytest.approx(expected, rel=1e-12)
    assert realized_vol(calm_then_wild, 26) < expected


def test_too_little_history_gives_none():
    assert realized_vol([100.0] * 30, 30) is None     # 30 closes are only 29 returns
    assert rms_move_pct([100.0] * 30, 30) is None
    assert realized_vol([100.0] * 31, 30) == 0.0


def test_daily_moves_and_their_rms():
    closes = [100.0, 110.0, 99.0, 99.0]
    assert daily_moves_pct(closes) == pytest.approx([10.0, -10.0, 0.0])
    assert rms_move_pct(closes, 3) == pytest.approx(math.sqrt(200 / 3))
    assert rms_move_pct(closes, 2) == pytest.approx(math.sqrt(100 / 2))


# ---- daily closes from Bybit ---------------------------------------------

DAY_MS = 86_400_000
TODAY_MS = int(datetime(2026, 10, 25, tzinfo=timezone.utc).timestamp() * 1000)


def kline_body(closes, retcode=0, msg="OK"):
    """A Bybit kline body: one completed day per close, then today's forming candle, newest first."""
    rows = [[str(TODAY_MS), "1", "1", "1", "999.0", "0", "0"]]
    for age, close in enumerate(reversed(closes), start=1):
        rows.append([str(TODAY_MS - age * DAY_MS), "1", "1", "1", str(close), "0", "0"])
    return {"retCode": retcode, "retMsg": msg, "result": {"list": rows}, "time": TODAY_MS + 14 * 3_600_000}


def test_kline_parsing_drops_the_forming_day_and_orders_oldest_first():
    body = {"retCode": 0, "retMsg": "OK", "time": TODAY_MS + 50_000_000, "result": {"list": [
        [str(TODAY_MS), "102", "104", "101", "103.5", "10", "1000"],             # today, still forming
        [str(TODAY_MS - DAY_MS), "101", "103", "100", "102", "10", "1000"],
        [str(TODAY_MS - 2 * DAY_MS), "100", "102", "99", "101", "10", "1000"],
    ]}}
    closes = parse_daily_klines(body)
    assert [c.date for c in closes] == [date(2026, 10, 23), date(2026, 10, 24)]
    assert [c.close for c in closes] == [101.0, 102.0]


def test_kline_parsing_keeps_every_candle_once_the_newest_day_is_over():
    body = {"retCode": 0, "retMsg": "OK", "time": TODAY_MS + DAY_MS, "result": {"list": [
        [str(TODAY_MS), "102", "104", "101", "103.5", "10", "1000"],             # ended at Bybit's clock
        [str(TODAY_MS - DAY_MS), "101", "103", "100", "102", "10", "1000"],
    ]}}
    assert [c.close for c in parse_daily_klines(body)] == [102.0, 103.5]


def test_kline_parsing_reports_a_malformed_body_as_a_bybit_error():
    with pytest.raises(RuntimeError, match="expected shape"):
        parse_daily_klines({"retCode": 0, "result": {"list": [["oops"]]}, "time": TODAY_MS})


def test_live_history_returns_completed_days_and_is_cached(fake_bybit):
    url, state = fake_bybit
    state["body"] = kline_body([100.0, 101.0, 102.0, 103.0, 104.0])
    source = BybitSource(base_url=url)
    past = source.history("BTC", days=3)
    assert (past.coin, past.symbol, past.source) == ("BTC", "BTCUSDT", "live")
    # the fake answers with only as many rows as asked for, so this also shows the forming day is fetched on top
    assert [c.close for c in past.closes] == [102.0, 103.0, 104.0]
    assert past.closes[-1].date == date(2026, 10, 24)
    assert past.as_of == datetime(2026, 10, 25, 14, tzinfo=timezone.utc)
    assert "symbol=BTCUSDT" in state["requests"][0]
    source.history("BTC", days=3)
    assert len(state["requests"]) == 1


def test_live_history_for_a_coin_without_a_perpetual_is_a_key_error(fake_bybit):
    url, state = fake_bybit
    state["body"] = kline_body([], retcode=10001, msg="params error: Symbol Is Invalid")
    with pytest.raises(KeyError):
        BybitSource(base_url=url).history("NOPE")


def test_live_history_keeps_other_bybit_errors(fake_bybit):
    url, state = fake_bybit
    state["body"] = kline_body([], retcode=10006, msg="Too many visits")
    with pytest.raises(RuntimeError, match="10006"):
        BybitSource(base_url=url).history("BTC")


def test_an_answer_that_is_not_json_is_a_bybit_error(fake_bybit):
    url, state = fake_bybit
    state["body"] = b"<html><body>502 Bad Gateway</body></html>"
    with pytest.raises(RuntimeError, match="JSON"):
        BybitSource(base_url=url).history("BTC")


def test_demo_history_is_steady_current_and_near_the_chain():
    first, again = DemoSource().history("BTC"), DemoSource().history("BTC")
    assert first.closes == again.closes
    assert len(first.closes) == 31
    yesterday = datetime.now(timezone.utc).date() - timedelta(days=1)
    assert first.closes[-1].date == yesterday
    assert first.closes[0].date == yesterday - timedelta(days=30)
    nearest = DemoSource().chain("BTC").expiries[0]
    assert first.closes[-1].close == pytest.approx(nearest.forward, rel=0.01)
    atm_iv = min(nearest.rows, key=lambda r: abs(r.strike - nearest.forward)).call.mark_iv
    assert 0.5 * atm_iv < realized_vol([c.close for c in first.closes], 30) < 1.5 * atm_iv
    with pytest.raises(KeyError):
        DemoSource().history("NOPE")
