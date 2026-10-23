from datetime import datetime, timedelta, timezone

import pytest

from cryptoptions import OptionType, from_millis, parse_symbol, year_fraction


def test_current_format_with_settle_coin():
    s = parse_symbol("BTC-14OCT26-100000-C-USDT")
    assert s.base == "BTC"
    assert s.expiry == datetime(2026, 10, 14, 8, tzinfo=timezone.utc)
    assert s.strike == 100_000.0
    assert s.option_type is OptionType.CALL
    assert s.settle == "USDT"
    assert s.expiry_code == "14OCT26"


def test_older_format_without_settle_coin():
    s = parse_symbol("BTC-30DEC22-18000-C")
    assert s.settle is None
    assert s.expiry == datetime(2022, 12, 30, 8, tzinfo=timezone.utc)


def test_single_digit_day_decimal_strike_and_put():
    s = parse_symbol("DOGE-3NOV26-0.25-P-USDT")
    assert s.expiry.day == 3
    assert s.strike == 0.25
    assert s.option_type is OptionType.PUT
    assert s.expiry_code == "3NOV26"


@pytest.mark.parametrize("bad", ["BTCUSDT", "BTC-14XYZ26-100000-C", "BTC-14OCT26-100000-X-USDT", ""])
def test_rejects_non_option_symbols(bad):
    with pytest.raises(ValueError):
        parse_symbol(bad)


def test_year_fraction():
    expiry = datetime(2026, 11, 1, 8, tzinfo=timezone.utc)
    assert year_fraction(expiry, expiry - timedelta(days=365)) == pytest.approx(1.0)
    assert year_fraction(expiry, expiry - timedelta(hours=12)) == pytest.approx(0.5 / 365)
    assert year_fraction(expiry, expiry + timedelta(days=1)) == 0.0
    with pytest.raises(ValueError):
        year_fraction(expiry.replace(tzinfo=None), expiry)


def test_from_millis():
    assert from_millis("1672376592395") == datetime(2022, 12, 30, 5, 3, 12, 395000, tzinfo=timezone.utc)
