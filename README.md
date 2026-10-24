# Options101

A personal options desk for buying crypto options on Bybit: an option chain with live prices, a position builder, and a what-if tool that shows how price, time and volatility change the result. Positions are long premium only (single calls and puts, straddles, strangles and debit spreads), so the most you can lose is always the premium you paid.

## Launch the app

You need Python 3.11 or newer. On Ubuntu and Debian, use `python3`.

```bash
git clone https://github.com/Gabangxa/Options101 && cd Options101
python3 -m venv .venv            # if this fails: sudo apt install python3-venv
source .venv/bin/activate
pip install -e ".[app]"
options101                       # live Bybit prices; opens http://127.0.0.1:8000
```

Other ways to start it:

```bash
options101 --demo                # sample prices, works offline
options101 --port 8080 --no-browser
python3 -m cryptoptions.app      # same as options101
```

Next time, only `cd Options101`, `source .venv/bin/activate` and `options101` are needed. Stop the app with Ctrl+C.

## Using it

1. Choose an asset at the top and an expiry date on the left.
2. Click an **ask** price to buy that option. Click a **bid** to sell one, which is only allowed as part of a spread where a bought option covers it.
3. Your position shows what you pay, the most you can lose and gain, the break-even price, the market's implied chance of profit, and what crossing the bid to ask spread costs you.
4. Move the **What if** levers (price change, days from now, volatility change), or click any cell in the price-by-day heatmap, and the sentence at the top explains the result in plain words.
5. **What matters most this week** ranks which assumption your result depends on most.

Prices refresh every 15 seconds. By default, buys are priced at the ask and sells at the bid, which is what you would actually trade at. Switch to "Price at mark" to see mid-market values.

## What is in the package

| Module | Does |
| --- | --- |
| `cryptoptions.black76` | Black-76 price on the forward, Greeks, implied vol solver |
| `cryptoptions.symbols` | Parses Bybit option symbols, time to the 08:00 UTC expiry |
| `cryptoptions.position` | Positions of legs, the long-premium rule, P&L grids, break-evens, probability of profit |
| `cryptoptions.market` | Live chains from Bybit's public API (no key needed), and demo chains |
| `cryptoptions.app` | FastAPI server and web page |
| `cryptoptions.validation` and `scripts/validate_bybit.py` | Checks the core's prices and Greeks against Bybit's marks |

Units, chosen to match Bybit: prices in USDT, volatility as a decimal, time on a 365-day calendar, vega per volatility point, theta per calendar day.

## Checking the maths against Bybit

```bash
python3 scripts/validate_bybit.py --coins BTC ETH --csv misses.csv --save-raw raw/
```

It prices every listed option with Bybit's own mark IV and checks that the result is within one tick of Bybit's mark price, and that the Greeks agree. It prints `PASS` or `FAIL`, plus a `UNITS?` line if a Greek differs by a fixed factor such as 100 or 365.

## Development

```bash
pip install -e ".[dev]"
pytest
```

## Not yet included

- The dynamic premium cap from the project brief
- Trading fees in P&L
- A spot-to-volatility link: the volatility lever moves only when you move it
- Saved and compared scenarios

This is a learning and analysis tool, not trading advice. Check every number against Bybit before you trade.
