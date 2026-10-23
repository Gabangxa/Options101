from datetime import datetime, timezone

from cryptoptions import greeks, parse_symbol, year_fraction
from cryptoptions.validation import compare

NOW = datetime(2026, 10, 23, 12, tzinfo=timezone.utc)
NOW_MS = int(NOW.timestamp() * 1000)


def synthetic_row(symbol: str, forward: float, iv: float, scale=(1.0, 1.0, 1.0, 1.0)) -> dict:
    """A ticker row that the core prices exactly, with optional unit scaling on the Greeks."""
    s = parse_symbol(symbol)
    g = greeks(forward, s.strike, year_fraction(s.expiry, NOW), iv, s.option_type)
    return {
        "symbol": symbol,
        "underlyingPrice": str(forward),
        "markIv": str(iv),
        "markPrice": str(round(g.price, 1)),
        "delta": str(g.delta * scale[0]),
        "gamma": str(g.gamma * scale[1]),
        "vega": str(g.vega * scale[2]),
        "theta": str(g.theta * scale[3]),
    }


ROWS = [
    ("BTC-30OCT26-110000-C-USDT", 108_000.0, 0.48),
    ("BTC-30OCT26-100000-P-USDT", 108_000.0, 0.52),
    ("BTC-27NOV26-120000-C-USDT", 108_500.0, 0.50),
    ("BTC-25DEC26-90000-P-USDT", 109_000.0, 0.55),
]


def test_exact_rows_pass():
    report = compare([synthetic_row(*r) for r in ROWS], NOW_MS, rate=0.0)
    assert len(report.rows) == len(ROWS)
    assert report.price_pass_rate == 1.0
    assert report.unit_warnings() == []
    assert all(abs(r.iv_back - r.mark_iv) < 1e-3 for r in report.rows)


def test_unit_mismatch_is_flagged():
    tickers = [synthetic_row(*r, scale=(1.0, 1.0, 100.0, 365.0)) for r in ROWS]
    notes = compare(tickers, NOW_MS).unit_warnings()
    assert any(n.startswith("vega") for n in notes)
    assert any(n.startswith("theta") for n in notes)


def test_percent_iv_is_detected():
    tickers = [synthetic_row(*r) for r in ROWS]
    for t in tickers:
        t["markIv"] = str(float(t["markIv"]) * 100)
    report = compare(tickers, NOW_MS)
    assert report.iv_scale_note is not None
    assert report.price_pass_rate == 1.0


def test_expired_and_bad_rows_are_skipped():
    tickers = [
        synthetic_row(*ROWS[0]),
        {"symbol": "BTC-1OCT26-100000-C-USDT", "underlyingPrice": "1", "markIv": "0.5", "markPrice": "1"},
        {"symbol": "BTCUSDT"},
        {"symbol": "BTC-30OCT26-100000-C-USDT", "underlyingPrice": "", "markIv": "0", "markPrice": "5"},
    ]
    report = compare(tickers, NOW_MS)
    assert len(report.rows) == 1
    assert len(report.skipped) == 3
