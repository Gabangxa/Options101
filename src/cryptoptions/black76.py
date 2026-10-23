"""Black-76 pricing for European options on a forward.

Bybit options are European and cash-settled in USDT, and the venue quotes an
``underlyingPrice`` per expiry, which is the forward the option is priced on.
Black-76 on that forward is therefore the base model.

Conventions (chosen to match how Bybit reports Greeks; the validation script
checks this against live data):

* ``forward``, ``strike`` and prices are in the quote currency (USDT).
* ``vol`` is annualised and expressed as a decimal (0.55 means 55%).
* ``t`` is time to expiry in years, on a 365-day calendar (crypto trades every day).
* ``rate`` is a continuously compounded discount rate; it defaults to 0.
* ``delta`` is per 1 unit move in the forward.
* ``gamma`` is per 1 unit move in the forward, per 1 unit move.
* ``vega`` is per 1 volatility point (0.01 in decimal terms).
* ``theta`` is per calendar day, and is negative for a long option.

Only the standard library is used, so the core has no dependencies.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from enum import Enum

DAYS_PER_YEAR = 365.0
_SQRT_2PI = math.sqrt(2.0 * math.pi)


class OptionType(str, Enum):
    CALL = "C"
    PUT = "P"

    @classmethod
    def parse(cls, value: "OptionType | str") -> "OptionType":
        if isinstance(value, OptionType):
            return value
        text = str(value).strip().upper()
        if text in ("C", "CALL"):
            return cls.CALL
        if text in ("P", "PUT"):
            return cls.PUT
        raise ValueError(f"Unknown option type: {value!r}")


@dataclass(frozen=True)
class Greeks:
    price: float
    delta: float
    gamma: float
    vega: float
    theta: float


def norm_cdf(x: float) -> float:
    return 0.5 * math.erfc(-x / math.sqrt(2.0))


def norm_pdf(x: float) -> float:
    return math.exp(-0.5 * x * x) / _SQRT_2PI


def _validate(forward: float, strike: float, t: float, vol: float) -> None:
    if forward <= 0 or strike <= 0:
        raise ValueError("forward and strike must be positive")
    if t < 0:
        raise ValueError("time to expiry cannot be negative")
    if vol < 0:
        raise ValueError("volatility cannot be negative")


def intrinsic(forward: float, strike: float, option_type: OptionType | str, rate: float = 0.0, t: float = 0.0) -> float:
    """Discounted intrinsic value on the forward."""
    kind = OptionType.parse(option_type)
    df = math.exp(-rate * t)
    if kind is OptionType.CALL:
        return df * max(forward - strike, 0.0)
    return df * max(strike - forward, 0.0)


def _d1_d2(forward: float, strike: float, t: float, vol: float) -> tuple[float, float]:
    sd = vol * math.sqrt(t)
    d1 = (math.log(forward / strike) + 0.5 * sd * sd) / sd
    return d1, d1 - sd


def price(
    forward: float,
    strike: float,
    t: float,
    vol: float,
    option_type: OptionType | str,
    rate: float = 0.0,
) -> float:
    """Black-76 premium of one option on one unit of the underlying."""
    _validate(forward, strike, t, vol)
    kind = OptionType.parse(option_type)
    if t == 0 or vol == 0:
        return intrinsic(forward, strike, kind, rate, t)
    df = math.exp(-rate * t)
    d1, d2 = _d1_d2(forward, strike, t, vol)
    if kind is OptionType.CALL:
        return df * (forward * norm_cdf(d1) - strike * norm_cdf(d2))
    return df * (strike * norm_cdf(-d2) - forward * norm_cdf(-d1))


def greeks(
    forward: float,
    strike: float,
    t: float,
    vol: float,
    option_type: OptionType | str,
    rate: float = 0.0,
) -> Greeks:
    """Price and Greeks in the units listed in the module docstring."""
    _validate(forward, strike, t, vol)
    kind = OptionType.parse(option_type)
    value = price(forward, strike, t, vol, kind, rate)

    if t == 0 or vol == 0:
        df = math.exp(-rate * t)
        in_the_money = forward > strike if kind is OptionType.CALL else forward < strike
        delta = (df if kind is OptionType.CALL else -df) if in_the_money else 0.0
        return Greeks(value, delta, 0.0, 0.0, 0.0)

    df = math.exp(-rate * t)
    sqrt_t = math.sqrt(t)
    d1, _ = _d1_d2(forward, strike, t, vol)
    pdf = norm_pdf(d1)

    delta = df * norm_cdf(d1) if kind is OptionType.CALL else -df * norm_cdf(-d1)
    gamma = df * pdf / (forward * vol * sqrt_t)
    vega_per_unit_vol = df * forward * pdf * sqrt_t
    # Theta is minus the derivative with respect to time to expiry.
    # For Black-76: dV/dT = -rate * V + df * F * pdf * vol / (2 * sqrt(T)).
    theta_per_year = rate * value - df * forward * pdf * vol / (2.0 * sqrt_t)

    return Greeks(
        price=value,
        delta=delta,
        gamma=gamma,
        vega=vega_per_unit_vol / 100.0,
        theta=theta_per_year / DAYS_PER_YEAR,
    )


def implied_vol(
    premium: float,
    forward: float,
    strike: float,
    t: float,
    option_type: OptionType | str,
    rate: float = 0.0,
    lower: float = 1e-6,
    upper: float = 10.0,
    tol: float = 1e-10,
    max_iter: int = 200,
) -> float:
    """Volatility that reproduces ``premium``, found by a safeguarded Newton search.

    Raises ``ValueError`` when the premium is outside the no-arbitrage range,
    for example below discounted intrinsic value. Returns 0.0 when the premium
    equals intrinsic value.
    """
    _validate(forward, strike, t, 0.0)
    if t <= 0:
        raise ValueError("implied volatility is undefined at expiry")
    kind = OptionType.parse(option_type)
    df = math.exp(-rate * t)
    floor = intrinsic(forward, strike, kind, rate, t)
    cap = df * (forward if kind is OptionType.CALL else strike)

    if premium < floor - tol or premium >= cap:
        raise ValueError(
            f"premium {premium} is outside the arbitrage-free range [{floor}, {cap})"
        )
    if premium <= floor + tol:
        return 0.0

    lo, hi = lower, upper
    if price(forward, strike, t, hi, kind, rate) < premium:
        raise ValueError("premium implies a volatility above the search ceiling")

    # Start near the Brenner-Subrahmanyam approximation, clamped to the bracket.
    guess = math.sqrt(2.0 * math.pi / t) * premium / (df * forward)
    vol = min(max(guess, lo * 2), hi / 2)

    for _ in range(max_iter):
        diff = price(forward, strike, t, vol, kind, rate) - premium
        if abs(diff) < tol:
            return vol
        if diff > 0:
            hi = vol
        else:
            lo = vol
        vega = df * forward * norm_pdf(_d1_d2(forward, strike, t, vol)[0]) * math.sqrt(t)
        step_ok = False
        if vega > 1e-12:
            candidate = vol - diff / vega
            if lo < candidate < hi:
                vol = candidate
                step_ok = True
        if not step_ok:
            vol = 0.5 * (lo + hi)
        if hi - lo < tol:
            return vol
    return vol
