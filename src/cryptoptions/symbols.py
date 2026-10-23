"""Bybit option symbols and time to expiry.

Bybit option symbols come in two shapes:

* ``BTC-30DEC22-18000-C`` (older, seen in the API documentation example)
* ``BTC-14OCT26-100000-C-USDT`` (current, with the settle coin appended)

Strikes can be decimals for low-priced coins (for example ``DOGE-...-0.25-C-USDT``).
Options expire at 08:00 UTC on the expiry date.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime, timezone

from .black76 import DAYS_PER_YEAR, OptionType

EXPIRY_HOUR_UTC = 8

_MONTHS = {
    "JAN": 1, "FEB": 2, "MAR": 3, "APR": 4, "MAY": 5, "JUN": 6,
    "JUL": 7, "AUG": 8, "SEP": 9, "OCT": 10, "NOV": 11, "DEC": 12,
}

_SYMBOL_RE = re.compile(
    r"^(?P<base>[A-Z0-9]+)-"
    r"(?P<day>\d{1,2})(?P<month>[A-Z]{3})(?P<year>\d{2})-"
    r"(?P<strike>\d+(?:\.\d+)?)-"
    r"(?P<kind>[CP])"
    r"(?:-(?P<settle>[A-Z0-9]+))?$"
)


@dataclass(frozen=True)
class OptionSymbol:
    symbol: str
    base: str
    expiry: datetime
    strike: float
    option_type: OptionType
    settle: str | None

    @property
    def expiry_code(self) -> str:
        """Expiry in Bybit's ``expDate`` format, for example ``14OCT26``."""
        month = [m for m, n in _MONTHS.items() if n == self.expiry.month][0]
        return f"{self.expiry.day}{month}{self.expiry.year % 100:02d}"


def parse_symbol(symbol: str) -> OptionSymbol:
    text = symbol.strip().upper()
    match = _SYMBOL_RE.match(text)
    if not match:
        raise ValueError(f"Not a Bybit option symbol: {symbol!r}")
    month = _MONTHS.get(match["month"])
    if month is None:
        raise ValueError(f"Unknown month in symbol: {symbol!r}")
    expiry = datetime(
        2000 + int(match["year"]), month, int(match["day"]),
        EXPIRY_HOUR_UTC, tzinfo=timezone.utc,
    )
    return OptionSymbol(
        symbol=text,
        base=match["base"],
        expiry=expiry,
        strike=float(match["strike"]),
        option_type=OptionType.parse(match["kind"]),
        settle=match["settle"],
    )


def year_fraction(expiry: datetime, now: datetime) -> float:
    """Calendar time to expiry in years (365-day year), floored at zero."""
    if expiry.tzinfo is None or now.tzinfo is None:
        raise ValueError("use timezone-aware datetimes")
    seconds = (expiry - now).total_seconds()
    return max(seconds, 0.0) / (DAYS_PER_YEAR * 86400.0)


def from_millis(ms: int | str) -> datetime:
    """Bybit timestamps are milliseconds since the epoch."""
    return datetime.fromtimestamp(int(ms) / 1000.0, tz=timezone.utc)
