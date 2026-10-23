# cryptoptions

Pricing core for buying crypto options on Bybit, the first layer of an options calculator and a what-if scenario tool. Positions are long premium only: single calls and puts, straddles, strangles and debit spreads, so the maximum loss is always the premium paid.

## What is here (milestone 2: pricing core)

| Module | Does |
| --- | --- |
| `cryptoptions.black76` | Black-76 price on the forward, Greeks, implied vol solver |
| `cryptoptions.symbols` | Parses Bybit option symbols (with or without the `-USDT` suffix), time to the 08:00 UTC expiry |
| `cryptoptions.validation` | Compares the core with Bybit's marks, with no network code |
| `scripts/validate_bybit.py` | Pulls live chains from Bybit's public API and runs the comparison |

Units, chosen to match how Bybit reports Greeks:

- prices, forward and strike in USDT
- volatility as a decimal (0.55 = 55%)
- time in years on a 365-day calendar
- delta and gamma per 1 USDT move in the forward
- vega per 1 volatility point
- theta per calendar day

The core uses only the Python standard library.

## Quick start

```bash
python -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"
pytest
```

```python
from cryptoptions import greeks, implied_vol, parse_symbol, year_fraction
from datetime import datetime, timezone

s = parse_symbol("BTC-27NOV26-120000-C-USDT")
t = year_fraction(s.expiry, datetime.now(timezone.utc))
g = greeks(forward=108_000, strike=s.strike, t=t, vol=0.50, option_type=s.option_type)
print(g.price, g.delta, g.vega, g.theta)
```

## Validating against Bybit (the milestone's acceptance test)

```bash
python scripts/validate_bybit.py --coins BTC ETH --csv misses.csv --save-raw raw/
```

For every listed option the script feeds Bybit's mark IV and underlying price into the core and checks:

1. the core's price is within one tick of Bybit's mark price,
2. Bybit's mark price inverts back to Bybit's mark IV,
3. the core's delta, gamma, vega and theta match Bybit's.

It prints the ticker field names it received, a per-coin summary, and a `UNITS?` line if a Greek differs by a fixed factor such as 100 or 365, which would mean a unit convention to adjust rather than a pricing bug. It exits with code 1 if fewer than 99% of prices are within one tick. `--save-raw` keeps the responses so a run can be repeated offline with `--from-raw raw/`.

## Next

Milestone 3 is the scenario engine: positions made of legs, P&L and Greeks across spot, time and IV, breakevens, required move, implied probability and the dynamic premium cap.
