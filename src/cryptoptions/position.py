"""Positions made of option legs, and what-if analysis on them.

A position is one or more legs on the same underlying and the same expiry.
Only long-premium, defined-risk positions are allowed: the most you can lose
is the net premium you paid. That admits single calls and puts, straddles,
strangles and debit spreads, and rejects anything with a naked short leg.

Each leg keeps its own implied volatility (so skew between strikes is kept);
the what-if ``iv_change`` shifts every leg by the same number of vol points.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import datetime, timedelta

from .black76 import DAYS_PER_YEAR, OptionType, greeks, norm_cdf, price
from .symbols import year_fraction

MIN_VOL = 0.01


@dataclass(frozen=True)
class FeeSchedule:
    """Exchange fees, following Bybit's published method.

    Trading fee per contract, charged when opening and when closing early:
        min(trade_rate x index price, trade_cap x option price)
    Settlement (delivery) fee per contract, charged only on options that
    expire in the money:
        min(delivery_rate x index price, delivery_cap x option value at expiry)
    The index price is approximated by the forward of the expiry.
    """

    trade_rate: float = 0.0003       # 0.03%, regular taker rate
    trade_cap: float = 0.07          # 7% of the option price
    delivery_rate: float = 0.00015   # 0.015% for BTC and ETH
    delivery_cap: float = 0.125      # 12.5% of the option value
    charge_delivery: bool = True

    @classmethod
    def bybit(cls, coin: str, expiry: datetime, trade_rate: float = 0.0003) -> "FeeSchedule":
        delivery_rate = 0.00015 if coin.upper() in ("BTC", "ETH") else 0.0002
        # Bybit charges no delivery fee on daily options. Weekly, monthly and
        # quarterly options expire on Fridays, so a non-Friday expiry is a daily.
        is_daily = expiry.weekday() != 4
        return cls(trade_rate=trade_rate, delivery_rate=delivery_rate, charge_delivery=not is_daily)

    def trade(self, index: float, option_price: float, quantity: float) -> float:
        return quantity * min(self.trade_rate * index, self.trade_cap * max(option_price, 0.0))

    def delivery(self, index: float, intrinsic: float, quantity: float) -> float:
        if not self.charge_delivery or intrinsic <= 0:
            return 0.0
        return quantity * min(self.delivery_rate * index, self.delivery_cap * intrinsic)


NO_FEES = FeeSchedule(0.0, 0.0, 0.0, 0.0, False)


@dataclass(frozen=True)
class Leg:
    option_type: OptionType
    strike: float
    expiry: datetime
    quantity: float          # positive number of contracts (1 contract = 1 coin)
    side: int                # +1 buy, -1 sell
    entry_price: float       # premium per contract, USDT
    iv: float                # decimal, used to revalue the leg
    symbol: str = ""

    def __post_init__(self) -> None:
        if self.side not in (1, -1):
            raise ValueError("side must be +1 (buy) or -1 (sell)")
        if self.quantity <= 0:
            raise ValueError("quantity must be positive")
        if self.entry_price < 0 or self.iv <= 0:
            raise ValueError("entry price must be >= 0 and iv > 0")

    @property
    def signed_qty(self) -> float:
        return self.side * self.quantity


@dataclass
class ScenarioPoint:
    forward: float
    days_elapsed: float
    iv_change: float           # vol points, e.g. -10 means 10 points lower
    value: float
    pnl: float                 # after fees when fees are on
    fees: float                # all fees in this result: opening plus closing or settlement
    pnl_pct_of_premium: float
    delta: float
    gamma: float
    vega: float
    theta: float


@dataclass
class Analysis:
    net_premium: float                  # paid, positive, before fees
    entry_fees: float                   # trading fees to open
    exit_fees_now: float                # trading fees to close at today's values
    fees_included: bool
    max_loss: float                     # positive number, after fees
    max_gain: float | None              # None means unlimited
    breakevens: list[float]
    breakeven_moves: list[float]        # percent from current forward
    prob_profit_at_expiry: float        # under the market's implied distribution
    days_to_expiry: float
    now: ScenarioPoint
    scenario: ScenarioPoint
    sensitivities: list[dict] = field(default_factory=list)
    grid_prices: list[float] = field(default_factory=list)
    grid_days: list[float] = field(default_factory=list)
    grid_pnl: list[list[float]] = field(default_factory=list)
    curve_prices: list[float] = field(default_factory=list)
    curve_at_expiry: list[float] = field(default_factory=list)
    curve_at_scenario_day: list[float] = field(default_factory=list)
    curve_today: list[float] = field(default_factory=list)


class PositionError(ValueError):
    pass


class Position:
    def __init__(self, legs: list[Leg], fees: FeeSchedule | None = None, index: float | None = None):
        """``index`` is the underlying price used for the opening fees (the forward at entry)."""
        if not legs:
            raise PositionError("Add at least one option to build a position.")
        expiries = {leg.expiry for leg in legs}
        if len(expiries) > 1:
            raise PositionError("All options in a position must share one expiry date.")
        self.legs = legs
        self.expiry = legs[0].expiry
        self.fees = fees or NO_FEES
        if self.fees is not NO_FEES and index is None:
            raise ValueError("an index price is needed to work out fees")
        self.index = index or 0.0
        self._check_long_premium()

    # ---- basic quantities -------------------------------------------------

    @property
    def net_premium(self) -> float:
        return sum(leg.signed_qty * leg.entry_price for leg in self.legs)

    @property
    def strikes(self) -> list[float]:
        return sorted({leg.strike for leg in self.legs})

    @property
    def fees_on(self) -> bool:
        return self.fees is not NO_FEES

    @property
    def entry_fees(self) -> float:
        return sum(self.fees.trade(self.index, l.entry_price, l.quantity) for l in self.legs)

    @staticmethod
    def _intrinsic(leg: Leg, forward: float) -> float:
        if leg.option_type is OptionType.CALL:
            return max(forward - leg.strike, 0.0)
        return max(leg.strike - forward, 0.0)

    def delivery_fees(self, forward: float) -> float:
        return sum(self.fees.delivery(forward, self._intrinsic(l, forward), l.quantity) for l in self.legs)

    def payoff_at_expiry(self, forward: float, with_fees: bool = True) -> float:
        total = sum(leg.signed_qty * self._intrinsic(leg, forward) for leg in self.legs)
        result = total - self.net_premium
        if with_fees and self.fees_on:
            result -= self.entry_fees + self.delivery_fees(forward)
        return result

    def _net_call_qty(self) -> float:
        return sum(l.signed_qty for l in self.legs if l.option_type is OptionType.CALL)

    def _check_long_premium(self) -> None:
        if self.net_premium <= 0:
            raise PositionError(
                "This position takes in premium rather than paying it. Only positions "
                "where you pay up front, and can lose no more than that, are allowed."
            )
        if self._net_call_qty() < -1e-12:
            raise PositionError(
                "More calls are sold than bought, so the loss is unlimited if the price rises."
            )
        worst = min(self.payoff_at_expiry(x, with_fees=False) for x in self._kinks())
        if worst < -self.net_premium - 1e-9 * max(1.0, self.net_premium):
            raise PositionError(
                "This position can lose more than the premium paid. Each sold option "
                "must be covered by a bought option of the same type."
            )

    def _kinks(self) -> list[float]:
        top = max(self.strikes) * 10
        return [1e-9, *self.strikes, top]

    # ---- valuation --------------------------------------------------------

    def _leg_vol(self, leg: Leg, iv_change: float) -> float:
        return max(leg.iv + iv_change / 100.0, MIN_VOL)

    def _at_expiry(self, at: datetime) -> bool:
        return (self.expiry - at).total_seconds() <= 1.0

    def _fees_at(self, forward: float, at: datetime, leg_prices: list[float]) -> float:
        """Opening fees plus either closing fees (before expiry) or settlement fees (at expiry)."""
        if not self.fees_on:
            return 0.0
        if self._at_expiry(at):
            return self.entry_fees + self.delivery_fees(forward)
        closing = sum(self.fees.trade(forward, p, l.quantity) for l, p in zip(self.legs, leg_prices))
        return self.entry_fees + closing

    def evaluate(self, forward: float, at: datetime, iv_change: float = 0.0, now: datetime | None = None) -> ScenarioPoint:
        t = year_fraction(self.expiry, at)
        value = delta = gamma = vega = theta = 0.0
        prices = []
        for leg in self.legs:
            g = greeks(forward, leg.strike, t, self._leg_vol(leg, iv_change), leg.option_type)
            q = leg.signed_qty
            prices.append(g.price)
            value += q * g.price
            delta += q * g.delta
            gamma += q * g.gamma
            vega += q * g.vega
            theta += q * g.theta
        fees = self._fees_at(forward, at, prices)
        pnl = value - self.net_premium - fees
        elapsed = (at - now).total_seconds() / 86400 if now else 0.0
        return ScenarioPoint(
            forward=forward, days_elapsed=elapsed, iv_change=iv_change,
            value=value, pnl=pnl, fees=fees, pnl_pct_of_premium=100.0 * pnl / self.net_premium,
            delta=delta, gamma=gamma, vega=vega, theta=theta,
        )

    def pnl(self, forward: float, at: datetime, iv_change: float = 0.0) -> float:
        if self._at_expiry(at):
            return self.payoff_at_expiry(forward)
        t = year_fraction(self.expiry, at)
        prices = [price(forward, l.strike, t, self._leg_vol(l, iv_change), l.option_type) for l in self.legs]
        value = sum(l.signed_qty * p for l, p in zip(self.legs, prices))
        return value - self.net_premium - self._fees_at(forward, at, prices)

    # ---- expiry statistics -----------------------------------------------

    def _scan_points(self) -> list[float]:
        """Prices dense enough to catch every kink of the expiry payoff, fees included."""
        lo, hi = min(self.strikes) * 0.02, max(self.strikes) * 5
        n = 1200
        dense = [lo * (hi / lo) ** (i / n) for i in range(n + 1)]
        return sorted({*dense, *self.strikes})

    def breakevens(self) -> list[float]:
        f = self.payoff_at_expiry
        points = self._scan_points()
        values = [f(x) for x in points]
        # the payoff keeps rising past the last point when calls are net long
        top = points[-1]
        while values[-1] < 0 and self._net_call_qty() > 0 and top < max(self.strikes) * 1e4:
            top *= 2
            points.append(top)
            values.append(f(top))
        found: list[float] = []
        for (x0, y0), (x1, y1) in zip(zip(points, values), zip(points[1:], values[1:])):
            if y0 == 0:
                if not found or abs(found[-1] - x0) > 1e-9 * x0:
                    found.append(x0)
            elif y0 * y1 < 0:
                a, b, ya = x0, x1, y0
                for _ in range(80):
                    m = 0.5 * (a + b)
                    ym = f(m)
                    if ym == 0 or b - a < 1e-10 * b:
                        break
                    if (ym < 0) == (ya < 0):
                        a, ya = m, ym
                    else:
                        b = m
                found.append(0.5 * (a + b))
        return found

    def max_gain(self) -> float | None:
        if self._net_call_qty() > 1e-12:
            return None
        return max(self.payoff_at_expiry(x) for x in self._scan_points())

    def max_loss(self) -> float:
        worst = min(self.payoff_at_expiry(x) for x in [1e-9, *self._scan_points()])
        return max(-worst, 0.0)

    def prob_profit_at_expiry(self, forward: float, vol: float, t: float) -> float:
        """Probability that the expiry payoff is positive, lognormal forward with zero drift."""
        if t <= 0:
            return 1.0 if self.payoff_at_expiry(forward) > 0 else 0.0
        sd = vol * math.sqrt(t)

        def above(x: float) -> float:   # P(F_T > x)
            if x <= 0:
                return 1.0
            return norm_cdf((math.log(forward / x) - 0.5 * sd * sd) / sd)

        edges = [0.0, *self.breakevens(), math.inf]
        prob = 0.0
        for a, b in zip(edges, edges[1:]):
            mid = (a + b) / 2 if math.isfinite(b) else (a * 2 if a > 0 else 1.0)
            if self.payoff_at_expiry(mid) > 0:
                prob += above(a) - (above(b) if math.isfinite(b) else 0.0)
        return min(max(prob, 0.0), 1.0)

    def reference_vol(self) -> float:
        """Premium-weighted average IV of the bought legs: what the market charges you."""
        bought = [l for l in self.legs if l.side == 1]
        weights = [l.quantity * max(l.entry_price, 1e-9) for l in bought]
        return sum(w * l.iv for w, l in zip(weights, bought)) / sum(weights)

    # ---- full analysis ---------------------------------------------------

    def analyze(
        self,
        forward: float,
        now: datetime,
        move_pct: float = 0.0,
        days_elapsed: float = 0.0,
        iv_change: float = 0.0,
        grid_cols: int = 41,
        grid_rows: int = 11,
        curve_points: int = 161,
    ) -> Analysis:
        dte = max((self.expiry - now).total_seconds() / 86400.0, 0.0)
        days_elapsed = min(max(days_elapsed, 0.0), dte)
        at = now + timedelta(days=days_elapsed)
        t_now = dte / DAYS_PER_YEAR
        ref_vol = self.reference_vol()

        current = self.evaluate(forward, now, 0.0, now)
        scen_forward = forward * (1 + move_pct / 100.0)
        scenario = self.evaluate(scen_forward, at, iv_change, now)

        breakevens = self.breakevens()
        max_gain = self.max_gain()

        # price range: about three standard deviations, and wide enough to show every strike
        sd = ref_vol * math.sqrt(max(t_now, 1 / DAYS_PER_YEAR))
        lo = min(forward * math.exp(-3 * sd), min(self.strikes) * 0.9, scen_forward * 0.95)
        hi = max(forward * math.exp(3 * sd), max(self.strikes) * 1.1, scen_forward * 1.05)
        lo = max(lo, forward * 0.2)
        hi = min(hi, forward * 3.0)

        def spread(n: int) -> list[float]:
            return [lo + (hi - lo) * i / (n - 1) for i in range(n)]

        grid_prices = spread(grid_cols)
        grid_days = [dte * i / (grid_rows - 1) for i in range(grid_rows)]
        grid_pnl = [
            [round(self.pnl(p, now + timedelta(days=d), iv_change), 6) for p in grid_prices]
            for d in grid_days
        ]

        curve_prices = spread(curve_points)
        curve_at_expiry = [self.payoff_at_expiry(p) for p in curve_prices]
        curve_scenario = [self.pnl(p, at, iv_change) for p in curve_prices]
        curve_today = [self.pnl(p, now, 0.0) for p in curve_prices]

        # Which assumption matters most over the next week, for a typical move in each lever.
        week = min(7.0, dte)
        typical_move = forward * ref_vol * math.sqrt(max(week, 1e-9) / DAYS_PER_YEAR)
        sensitivities = [
            {"lever": "price", "label": f"Price moves {typical_move / forward * 100:.1f}% (a typical week)",
             "pnl": abs(current.delta) * typical_move + 0.5 * current.gamma * typical_move ** 2},
            {"lever": "time", "label": f"{week:.0f} days pass with no price change",
             "pnl": self.pnl(forward, now + timedelta(days=week)) - current.pnl},
            {"lever": "iv", "label": "Implied volatility drops 5 points",
             "pnl": self.pnl(forward, now, -5.0) - current.pnl},
        ]
        sensitivities.sort(key=lambda s: abs(s["pnl"]), reverse=True)

        return Analysis(
            net_premium=self.net_premium,
            entry_fees=self.entry_fees,
            exit_fees_now=current.fees - self.entry_fees,
            fees_included=self.fees_on,
            max_loss=self.max_loss(),
            max_gain=max_gain,
            breakevens=breakevens,
            breakeven_moves=[100.0 * (b / forward - 1) for b in breakevens],
            prob_profit_at_expiry=self.prob_profit_at_expiry(forward, ref_vol, t_now),
            days_to_expiry=dte,
            now=current,
            scenario=scenario,
            sensitivities=sensitivities,
            grid_prices=grid_prices,
            grid_days=grid_days,
            grid_pnl=grid_pnl,
            curve_prices=curve_prices,
            curve_at_expiry=curve_at_expiry,
            curve_at_scenario_day=curve_scenario,
            curve_today=curve_today,
        )
