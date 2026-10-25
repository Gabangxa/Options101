"""Gamma trading: an optional second look at a bought option.

Every bought option is long gamma. If its delta were hedged away each day,
what a move of dS in the forward leaves behind is about 0.5 * gamma * dS**2,
while time decay costs theta every day. The daily move at which those two
balance is the break-even daily move. Comparing it with how far the price
has actually been moving each day (realized volatility) shows whether recent
moves would have paid for the decay.

This is analysis only. Nothing here places orders or adds hedge legs, and a
position is exactly the same whether or not anyone looks at these numbers.

Units follow ``black76``: gamma is per 1 USDT move in the forward, theta is
per calendar day, volatility is an annualised decimal on a 365-day year.
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from datetime import datetime

from .black76 import DAYS_PER_YEAR, greeks
from .position import MIN_VOL, Position
from .symbols import year_fraction

# Below this share of its gross gamma, a position's legs nearly cancel. Its net
# gamma is then a small difference of large numbers that changes sign within a
# short move, so 0.5 * gamma * dS**2 stops describing a day's move well before
# the break-even it gives (12% to 74% a day on some live BTC and ETH spreads).
# On live debit spreads checked against a one-day revaluation, the quadratic was
# rarely more than 25% off above this share.
MIN_GAMMA_SHARE = 0.1


def breakeven_move(gamma: float, theta: float, gross: float | None = None) -> float | None:
    """Daily move in the forward (USDT) whose gamma gain pays one day of theta.

    Solves 0.5 * gamma * dS**2 = -theta. Returns None when the position is not
    long gamma, because then a bigger move does not reliably help it: gamma is
    not positive, or it is under MIN_GAMMA_SHARE of ``gross``, the position's
    gross gamma (see ``gross_gamma``). Returns 0.0 when the position is long
    gamma but not losing value to time decay. For a single option at zero rate
    this equals forward * iv / sqrt(365): the implied volatility expressed as a
    daily move.
    """
    if not gamma > 0:
        return None
    if gross is not None and gamma < MIN_GAMMA_SHARE * gross:
        return None
    # 0.0 first: max keeps its first argument on a tie, and -theta of a zero theta is -0.0
    return math.sqrt(2.0 * max(0.0, -theta) / gamma)


def gross_gamma(position: Position, forward: float, at: datetime) -> float:
    """The legs' gamma added up as if every leg were bought.

    With nothing sold it equals the net gamma; with sold legs it is how much
    gamma the legs hold before they offset each other.
    """
    t = year_fraction(position.expiry, at)
    return sum(
        leg.quantity * greeks(forward, leg.strike, t, max(leg.iv, MIN_VOL), leg.option_type).gamma
        for leg in position.legs
    )


def daily_moves_pct(closes: Sequence[float]) -> list[float]:
    """Close-to-close moves in percent, one fewer than there are closes."""
    return [100.0 * (b / a - 1.0) for a, b in zip(closes, closes[1:])]


def realized_vol(closes: Sequence[float], days: int) -> float | None:
    """Annualised volatility of the last ``days`` daily log returns.

    Zero-mean, sqrt(365 * mean(r**2)), rather than a demeaned standard
    deviation: what a long-gamma position earns depends on the sum of squared
    moves, and a steady trend pays it just as much as back-and-forth chop.
    Returns None when there are fewer than ``days`` returns.
    """
    recent = _last_returns_window(closes, days)
    if recent is None:
        return None
    returns = [math.log(b / a) for a, b in zip(recent, recent[1:])]
    return math.sqrt(DAYS_PER_YEAR * sum(r * r for r in returns) / days)


def rms_move_pct(closes: Sequence[float], days: int) -> float | None:
    """Root-mean-square of the last ``days`` daily moves, in percent.

    Simple (not log) moves, so it sits on the same scale as the break-even
    daily move in percent of the forward. Returns None when there are fewer
    than ``days`` moves.
    """
    recent = _last_returns_window(closes, days)
    if recent is None:
        return None
    moves = daily_moves_pct(recent)
    return math.sqrt(sum(m * m for m in moves) / days)


def _last_returns_window(closes: Sequence[float], days: int) -> Sequence[float] | None:
    # ``days`` returns need ``days + 1`` closes.
    if days < 1:
        raise ValueError("days must be at least 1")
    if len(closes) < days + 1:
        return None
    return closes[-(days + 1):]
