"""Option chains: live from Bybit's public API, or synthetic demo data.

Both sources return the same ``Chain`` shape so the app does not care which
one it is talking to. Demo data is priced with the core itself, using a
volatility smile and term structure that look like a crypto market; it is
for learning the tool and for working offline, not for trading.
"""

from __future__ import annotations

import json
import math
import statistics
import threading
import time
import urllib.parse
import urllib.request
from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta, timezone

from .black76 import OptionType, greeks
from .symbols import parse_symbol, year_fraction

BYBIT_URL = "https://api.bybit.com"
CACHE_SECONDS = 15.0


@dataclass
class Quote:
    symbol: str
    bid: float
    ask: float
    mark: float
    mark_iv: float           # decimal
    bid_iv: float
    ask_iv: float
    delta: float
    gamma: float
    vega: float
    theta: float
    open_interest: float
    model_price: float       # the core's price at Bybit's mark IV
    tick: float

    @property
    def spread_iv_points(self) -> float | None:
        if self.bid_iv > 0 and self.ask_iv > 0:
            return (self.ask_iv - self.bid_iv) * 100
        return None


@dataclass
class StrikeRow:
    strike: float
    call: Quote | None = None
    put: Quote | None = None


@dataclass
class Expiry:
    code: str
    expiry: datetime
    days: float
    forward: float
    rows: list[StrikeRow] = field(default_factory=list)


@dataclass
class Chain:
    coin: str
    source: str              # "live" or "demo"
    as_of: datetime
    expiries: list[Expiry]

    def find(self, symbol: str) -> tuple[Expiry, Quote]:
        for exp in self.expiries:
            for row in exp.rows:
                for q in (row.call, row.put):
                    if q is not None and q.symbol == symbol:
                        return exp, q
        raise KeyError(symbol)

    def to_dict(self) -> dict:
        def quote(q: Quote | None):
            if q is None:
                return None
            d = asdict(q)
            d["spread_iv_points"] = q.spread_iv_points
            return d

        return {
            "coin": self.coin,
            "source": self.source,
            "as_of": self.as_of.isoformat(),
            "expiries": [
                {
                    "code": e.code,
                    "expiry": e.expiry.isoformat(),
                    "days": e.days,
                    "forward": e.forward,
                    "rows": [
                        {"strike": r.strike, "call": quote(r.call), "put": quote(r.put)}
                        for r in e.rows
                    ],
                }
                for e in self.expiries
            ],
        }


def _f(row: dict, key: str) -> float:
    try:
        return float(row.get(key) or 0.0)
    except (TypeError, ValueError):
        return 0.0


def build_chain(coin: str, source: str, tickers: list[dict], ticks: dict[str, float], now: datetime) -> Chain:
    """Group raw Bybit-shaped ticker rows into expiries and strikes."""
    by_expiry: dict[datetime, dict] = {}
    for t in tickers:
        try:
            s = parse_symbol(t["symbol"])
        except (KeyError, ValueError):
            continue
        if s.expiry <= now:
            continue
        bucket = by_expiry.setdefault(s.expiry, {"code": s.expiry_code, "forwards": [], "rows": {}})
        fwd = _f(t, "underlyingPrice")
        if fwd > 0:
            bucket["forwards"].append(fwd)
        iv = _f(t, "markIv")
        if iv > 5:           # guard against percent units
            iv /= 100
        years = year_fraction(s.expiry, now)
        model = greeks(fwd, s.strike, years, iv, s.option_type).price if fwd > 0 and iv > 0 else 0.0
        q = Quote(
            symbol=t["symbol"], bid=_f(t, "bid1Price"), ask=_f(t, "ask1Price"), mark=_f(t, "markPrice"),
            mark_iv=iv, bid_iv=_f(t, "bid1Iv"), ask_iv=_f(t, "ask1Iv"),
            delta=_f(t, "delta"), gamma=_f(t, "gamma"), vega=_f(t, "vega"), theta=_f(t, "theta"),
            open_interest=_f(t, "openInterest"), model_price=model, tick=ticks.get(t["symbol"], 0.0),
        )
        row = bucket["rows"].setdefault(s.strike, StrikeRow(s.strike))
        if s.option_type is OptionType.CALL:
            row.call = q
        else:
            row.put = q

    expiries = []
    for exp_dt in sorted(by_expiry):
        b = by_expiry[exp_dt]
        if not b["forwards"]:
            continue
        expiries.append(Expiry(
            code=b["code"], expiry=exp_dt,
            days=(exp_dt - now).total_seconds() / 86400.0,
            forward=statistics.median(b["forwards"]),
            rows=[b["rows"][k] for k in sorted(b["rows"])],
        ))
    return Chain(coin=coin, source=source, as_of=now, expiries=expiries)


# ---------------------------------------------------------------------------
# Live source


class BybitSource:
    name = "live"

    def __init__(self, base_url: str = BYBIT_URL, cache_seconds: float = CACHE_SECONDS):
        self.base_url = base_url
        self.cache_seconds = cache_seconds
        self._cache: dict[str, tuple[float, object]] = {}
        self._lock = threading.Lock()

    def _get(self, path: str, params: dict) -> dict:
        url = f"{self.base_url}{path}?{urllib.parse.urlencode(params)}"
        req = urllib.request.Request(url, headers={"User-Agent": "options101/0.2"})
        with urllib.request.urlopen(req, timeout=15) as resp:
            body = json.load(resp)
        if body.get("retCode") != 0:
            raise RuntimeError(f"Bybit {path} returned {body.get('retCode')}: {body.get('retMsg')}")
        return body

    def _cached(self, key: str, loader):
        with self._lock:
            hit = self._cache.get(key)
            if hit and time.monotonic() - hit[0] < self.cache_seconds:
                return hit[1]
        value = loader()
        with self._lock:
            self._cache[key] = (time.monotonic(), value)
        return value

    def coins(self) -> list[str]:
        def load():
            body = self._get("/v5/market/option-base-coins", {"underlyingType": 0})
            return [c["baseCoin"] for c in body["result"]["list"] if int(c.get("hasSymbol", 0)) == 1]
        return self._cached("coins", load)

    def chain(self, coin: str) -> Chain:
        def load():
            tickers = self._get("/v5/market/tickers", {"category": "option", "baseCoin": coin})
            ticks: dict[str, float] = {}
            cursor = ""
            while True:
                params = {"category": "option", "baseCoin": coin, "limit": 1000}
                if cursor:
                    params["cursor"] = cursor
                page = self._get("/v5/market/instruments-info", params)
                for i in page["result"]["list"]:
                    tick = i.get("priceFilter", {}).get("tickSize")
                    if tick:
                        ticks[i["symbol"]] = float(tick)
                cursor = page["result"].get("nextPageCursor", "")
                if not cursor:
                    break
            now = datetime.fromtimestamp(int(tickers["time"]) / 1000, tz=timezone.utc)
            return build_chain(coin, "live", tickers["result"]["list"], ticks, now)
        return self._cached(f"chain:{coin}", load)


# ---------------------------------------------------------------------------
# Demo source


_DEMO_MARKETS = {
    # coin: (forward, base ATM vol, skew per unit log-moneyness, smile curvature)
    "BTC": (108_250.0, 0.46, 0.10, 0.9),
    "ETH": (3_915.0, 0.62, 0.12, 0.9),
    "SOL": (191.4, 0.78, 0.15, 1.0),
    "XRP": (2.43, 0.82, 0.18, 1.1),
}


def _nice_step(x: float) -> float:
    exp = math.floor(math.log10(x))
    for m in (1, 2, 2.5, 5, 10):
        if m * 10 ** exp >= x:
            return m * 10 ** exp
    return 10 ** (exp + 1)


def _demo_expiries(now: datetime) -> list[datetime]:
    base = now.replace(hour=8, minute=0, second=0, microsecond=0)
    if base <= now:
        base += timedelta(days=1)
    out = {base, base + timedelta(days=1), base + timedelta(days=2)}
    friday = base + timedelta(days=(4 - base.weekday()) % 7)
    out.update(friday + timedelta(weeks=w) for w in range(4))
    for m in range(1, 4):
        year, month = now.year + (now.month + m - 1) // 12, (now.month + m - 1) % 12 + 1
        first_next = datetime(year + (month == 12), month % 12 + 1, 1, 8, tzinfo=timezone.utc)
        last_day = first_next - timedelta(days=1)
        out.add(last_day - timedelta(days=(last_day.weekday() - 4) % 7))
    return sorted(e for e in out if e > now)


class DemoSource:
    name = "demo"

    def coins(self) -> list[str]:
        return list(_DEMO_MARKETS)

    def chain(self, coin: str) -> Chain:
        if coin not in _DEMO_MARKETS:
            raise KeyError(f"No demo market for {coin}")
        spot, atm, skew, curve = _DEMO_MARKETS[coin]
        now = datetime.now(timezone.utc).replace(microsecond=0)
        # a slow, gentle drift so the screen is not frozen
        spot *= 1 + 0.004 * math.sin(time.time() / 300.0)
        rows: list[dict] = []
        for exp in _demo_expiries(now):
            years = year_fraction(exp, now)
            days = years * 365
            forward = spot * math.exp(0.06 * years)       # crypto basis is usually positive
            term_vol = atm * (1 + 0.12 / math.sqrt(max(days, 0.5)))
            width = min(0.6, 0.06 + 2.2 * term_vol * math.sqrt(years))
            step = _nice_step(forward * (0.01 if days < 3 else 0.025))
            first = math.floor(forward * (1 - width) / step)
            last = math.ceil(forward * (1 + width) / step)
            for n in range(max(first, 1), last + 1):
                k = round(n * step, 10)
                if k > 0:
                    lm = math.log(k / forward) / max(math.sqrt(years), 0.05)
                    iv = max(term_vol + skew * lm * 0.1 + curve * 0.01 * lm * lm, 0.15)
                    for kind in (OptionType.CALL, OptionType.PUT):
                        g = greeks(forward, k, years, iv, kind)
                        half = max(g.price * 0.01, forward * 0.0001)
                        tick = _nice_step(forward * 0.00005)
                        bid = max(round((g.price - half) / tick) * tick, 0.0)
                        ask = round((g.price + half) / tick) * tick + tick
                        strike_txt = f"{k:.8f}".rstrip("0").rstrip(".")
                        rows.append({
                            "symbol": f"{coin}-{exp.day}{exp.strftime('%b').upper()}{exp:%y}-{strike_txt}-{kind.value}-USDT",
                            "bid1Price": bid, "ask1Price": ask, "markPrice": round(g.price, 6),
                            "markIv": iv, "bid1Iv": iv - 0.012 if bid > 0 else 0, "ask1Iv": iv + 0.012,
                            "underlyingPrice": forward, "delta": g.delta, "gamma": g.gamma,
                            "vega": g.vega, "theta": g.theta, "openInterest": 0,
                        })
        return build_chain(coin, "demo", rows, {}, now)
