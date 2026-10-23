"""Compare the pricing core with Bybit's own marks.

This module has no network code: it takes Bybit ticker rows (as returned by
``/v5/market/tickers?category=option``) and reports how far the core's price
and Greeks are from Bybit's, given Bybit's own mark IV and underlying price.
``scripts/validate_bybit.py`` fetches the data and calls it.
"""

from __future__ import annotations

import math
import statistics
from dataclasses import dataclass, field

from .black76 import greeks, implied_vol
from .symbols import from_millis, parse_symbol, year_fraction


@dataclass
class Row:
    symbol: str
    days: float
    forward: float
    strike: float
    mark_iv: float
    bybit_price: float
    core_price: float
    tick: float
    price_diff: float
    iv_back: float | None
    deltas: tuple[float, float]
    gammas: tuple[float, float]
    vegas: tuple[float, float]
    thetas: tuple[float, float]

    @property
    def price_ok(self) -> bool:
        return abs(self.price_diff) <= self.tick + 1e-12


@dataclass
class Report:
    rows: list[Row] = field(default_factory=list)
    skipped: list[tuple[str, str]] = field(default_factory=list)
    iv_scale_note: str | None = None

    @property
    def price_pass_rate(self) -> float:
        return sum(r.price_ok for r in self.rows) / len(self.rows) if self.rows else 0.0

    def median_ratio(self, attr: str) -> float | None:
        ratios = [
            core / bybit
            for core, bybit in (getattr(r, attr) for r in self.rows)
            if abs(bybit) > 1e-12 and abs(core) > 1e-12
        ]
        return statistics.median(ratios) if ratios else None

    def max_abs_diff(self, attr: str) -> float:
        diffs = [abs(core - bybit) for core, bybit in (getattr(r, attr) for r in self.rows)]
        return max(diffs) if diffs else 0.0

    def unit_warnings(self) -> list[str]:
        """Flag a Greek whose core/Bybit ratio sits near a unit-convention factor."""
        notes = []
        factors = {100.0: "x100 (per 1.00 vol vs per vol point?)", 0.01: "x0.01",
                   365.0: "x365 (per year vs per day?)", 1 / 365.0: "x1/365"}
        for attr in ("vegas", "thetas", "gammas", "deltas"):
            ratio = self.median_ratio(attr)
            if ratio is None:
                continue
            for factor, label in factors.items():
                if abs(math.log(abs(ratio) / factor)) < 0.05:
                    notes.append(f"{attr[:-1]}: core/Bybit median ratio {ratio:.4g}, looks like {label}")
        return notes


def _num(row: dict, key: str) -> float:
    value = row.get(key, "")
    return float(value) if value not in ("", None) else math.nan


def compare(
    tickers: list[dict],
    server_time_ms: int | str,
    tick_sizes: dict[str, float] | None = None,
    rate: float = 0.0,
    default_tick: float = 0.1,
) -> Report:
    tick_sizes = tick_sizes or {}
    now = from_millis(server_time_ms)
    report = Report()

    raw_ivs = [_num(t, "markIv") for t in tickers]
    raw_ivs = [v for v in raw_ivs if v > 0 and not math.isnan(v)]
    # Bybit documents markIv as a decimal (0.7567 = 75.67%). Guard against percent units.
    iv_scale = 1.0
    if raw_ivs and statistics.median(raw_ivs) > 5:
        iv_scale = 0.01
        report.iv_scale_note = "markIv looked like percent; divided by 100"

    for t in tickers:
        sym = t.get("symbol", "?")
        try:
            parsed = parse_symbol(sym)
        except ValueError as exc:
            report.skipped.append((sym, str(exc)))
            continue
        years = year_fraction(parsed.expiry, now)
        forward = _num(t, "underlyingPrice")
        mark_iv = _num(t, "markIv") * iv_scale
        mark_price = _num(t, "markPrice")
        if years <= 0:
            report.skipped.append((sym, "expired or delivering"))
            continue
        if not (forward > 0 and mark_iv > 0) or math.isnan(mark_price):
            report.skipped.append((sym, "missing forward, mark IV or mark price"))
            continue

        g = greeks(forward, parsed.strike, years, mark_iv, parsed.option_type, rate)
        try:
            iv_back = implied_vol(mark_price, forward, parsed.strike, years, parsed.option_type, rate)
        except ValueError:
            iv_back = None

        report.rows.append(Row(
            symbol=sym,
            days=years * 365.0,
            forward=forward,
            strike=parsed.strike,
            mark_iv=mark_iv,
            bybit_price=mark_price,
            core_price=g.price,
            tick=tick_sizes.get(sym, default_tick),
            price_diff=g.price - mark_price,
            iv_back=iv_back,
            deltas=(g.delta, _num(t, "delta")),
            gammas=(g.gamma, _num(t, "gamma")),
            vegas=(g.vega, _num(t, "vega")),
            thetas=(g.theta, _num(t, "theta")),
        ))
    return report
