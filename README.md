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

### Gamma trading (optional)

This is off by default, and you can ignore it completely: buying an option works the same with it on or off. Turning it on changes nothing about your position. It adds two readings, places no orders and adds no hedge legs.

- **Break-even daily move.** If you hedged away the direction each day, a bought option would make money from the size of daily price moves and lose a little to time decay every day. This is the daily move at which the two balance, before trading fees and funding on the perpetual. For a single option it is the implied volatility written as a daily move, about 2.5% a day at 48% volatility. It does not apply when the position gains little or nothing from bigger moves, for example a debit spread with the price near or above the strike you sold.
- **Realized against implied.** The coin's last 30 daily moves on its USDT perpetual, drawn against the break-even line, with the volatility the price actually showed over 7 and 30 days next to the implied volatility your break-even stands for. Past moves are not a forecast.

### Fees

Bybit's option fees are included by default, using the method on Bybit's [fee help page](https://www.bybit.com/en/help-center/article/Bybit-Option-Fees-Explained):

- Trading fee, when you open and when you close early: the smaller of (fee rate x index price) and (7% x option price), per contract. The default rate is 0.03%, the regular rate for orders that fill immediately; set 0.02% if you use limit orders that wait on the book, or your own VIP rate.
- Settlement fee, only on options that expire in the money: the smaller of (0.015% for BTC and ETH, 0.02% for other coins, x index price) and (12.5% x the option's value at expiry). Daily options have none; the app treats any expiry that is not on a Friday as a daily.

Every result (what you pay, the most you can lose and gain, break-evens, charts, the heatmap and the what-if sentence) is after fees. Untick "Include Bybit fees" to see results without them. The index price is approximated by the forward for that expiry.

## What is in the package

| Module | Does |
| --- | --- |
| `cryptoptions.black76` | Black-76 price on the forward, Greeks, implied vol solver |
| `cryptoptions.symbols` | Parses Bybit option symbols, time to the 08:00 UTC expiry |
| `cryptoptions.position` | Positions of legs, the long-premium rule, P&L grids, break-evens, probability of profit |
| `cryptoptions.market` | Live chains and daily closes from Bybit's public API (no key needed), and demo data |
| `cryptoptions.gamma` | Optional gamma-trading analysis: break-even daily move, realized volatility from daily closes |
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
- A spot-to-volatility link: the volatility lever moves only when you move it
- Saved and compared scenarios
- A hedge simulator for gamma trading: the optional overlay shows the break-even move and past moves, but does not simulate delta hedging

This is a learning and analysis tool, not trading advice. Check every number against Bybit before you trade.
