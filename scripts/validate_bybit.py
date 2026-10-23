"""Check the pricing core against live Bybit option marks.

Usage (from the repository root):

    python scripts/validate_bybit.py                 # BTC and ETH
    python scripts/validate_bybit.py --coins BTC ETH SOL --csv misses.csv
    python scripts/validate_bybit.py --save-raw raw/  # keep the API responses
    python scripts/validate_bybit.py --from-raw raw/  # rerun offline on saved responses

For every option it feeds Bybit's mark IV and underlying price into the core,
then compares the core's price with Bybit's mark price (pass = within one tick)
and the core's Greeks with Bybit's. It also inverts Bybit's mark price back to
an IV. Uses only public endpoints; no API key is needed.

Exit code 0 when every coin's price pass rate is at or above --min-pass.
"""

from __future__ import annotations

import argparse
import csv
import json
import sys
import urllib.parse
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from cryptoptions.validation import Report, compare  # noqa: E402

BASE_URL = "https://api.bybit.com"


def get(path: str, params: dict) -> dict:
    url = f"{BASE_URL}{path}?{urllib.parse.urlencode(params)}"
    req = urllib.request.Request(url, headers={"User-Agent": "cryptoptions-validate/0.1"})
    with urllib.request.urlopen(req, timeout=20) as resp:
        body = json.load(resp)
    if body.get("retCode") != 0:
        raise RuntimeError(f"{path} returned {body.get('retCode')}: {body.get('retMsg')}")
    return body


def fetch(coin: str) -> tuple[dict, list[dict]]:
    tickers = get("/v5/market/tickers", {"category": "option", "baseCoin": coin})
    instruments: list[dict] = []
    cursor = ""
    while True:
        params = {"category": "option", "baseCoin": coin, "limit": 1000}
        if cursor:
            params["cursor"] = cursor
        page = get("/v5/market/instruments-info", params)
        instruments.extend(page["result"]["list"])
        cursor = page["result"].get("nextPageCursor", "")
        if not cursor:
            break
    return tickers, instruments


def tick_map(instruments: list[dict]) -> dict[str, float]:
    return {
        i["symbol"]: float(i["priceFilter"]["tickSize"])
        for i in instruments
        if i.get("priceFilter", {}).get("tickSize")
    }


def summarise(coin: str, report: Report) -> None:
    rows = report.rows
    print(f"\n=== {coin}: {len(rows)} options compared, {len(report.skipped)} skipped ===")
    if report.iv_scale_note:
        print(f"note: {report.iv_scale_note}")
    if not rows:
        return
    misses = [r for r in rows if not r.price_ok]
    print(f"price within one tick: {report.price_pass_rate:.1%} ({len(misses)} misses)")
    if misses:
        worst = max(misses, key=lambda r: abs(r.price_diff))
        print(f"  worst: {worst.symbol} core {worst.core_price:.4f} vs Bybit {worst.bybit_price:.4f} (tick {worst.tick})")
    iv_gaps = [abs(r.iv_back - r.mark_iv) * 100 for r in rows if r.iv_back is not None]
    if iv_gaps:
        print(f"mark price inverted to IV: max gap {max(iv_gaps):.3f} vol points")
    for attr in ("deltas", "gammas", "vegas", "thetas"):
        ratio = report.median_ratio(attr)
        ratio_text = f"{ratio:.4f}" if ratio is not None else "n/a"
        print(f"{attr[:-1]:>6}: median core/Bybit {ratio_text}, max abs diff {report.max_abs_diff(attr):.6g}")
    for note in report.unit_warnings():
        print(f"UNITS? {note}")


def write_csv(path: Path, reports: dict[str, Report]) -> None:
    with path.open("w", newline="") as fh:
        w = csv.writer(fh)
        w.writerow([
            "coin", "symbol", "days", "forward", "strike", "mark_iv", "bybit_price", "core_price",
            "price_diff", "tick", "price_ok", "iv_back",
            "delta_core", "delta_bybit", "gamma_core", "gamma_bybit",
            "vega_core", "vega_bybit", "theta_core", "theta_bybit",
        ])
        for coin, rep in reports.items():
            for r in rep.rows:
                w.writerow([
                    coin, r.symbol, f"{r.days:.4f}", r.forward, r.strike, r.mark_iv, r.bybit_price,
                    f"{r.core_price:.8f}", f"{r.price_diff:.8f}", r.tick, r.price_ok,
                    "" if r.iv_back is None else f"{r.iv_back:.6f}",
                    *r.deltas, *r.gammas, *r.vegas, *r.thetas,
                ])


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--coins", nargs="+", default=["BTC", "ETH"])
    ap.add_argument("--rate", type=float, default=0.0, help="discount rate, default 0")
    ap.add_argument("--min-pass", type=float, default=0.99, help="required price pass rate")
    ap.add_argument("--csv", type=Path, help="write every compared option to this CSV")
    ap.add_argument("--save-raw", type=Path, help="save API responses to this folder")
    ap.add_argument("--from-raw", type=Path, help="read saved responses instead of calling the API")
    args = ap.parse_args()

    reports: dict[str, Report] = {}
    for coin in [c.upper() for c in args.coins]:
        if args.from_raw:
            tickers = json.loads((args.from_raw / f"{coin}_tickers.json").read_text())
            instruments = json.loads((args.from_raw / f"{coin}_instruments.json").read_text())
        else:
            tickers, instruments = fetch(coin)
            if args.save_raw:
                args.save_raw.mkdir(parents=True, exist_ok=True)
                (args.save_raw / f"{coin}_tickers.json").write_text(json.dumps(tickers))
                (args.save_raw / f"{coin}_instruments.json").write_text(json.dumps(instruments))

        first = (tickers["result"]["list"] or [{}])[0]
        print(f"\n{coin} ticker fields: {', '.join(sorted(first))}")
        reports[coin] = compare(tickers["result"]["list"], tickers["time"], tick_map(instruments), args.rate)
        summarise(coin, reports[coin])

    if args.csv:
        write_csv(args.csv, reports)
        print(f"\nwrote {args.csv}")

    failed = [c for c, r in reports.items() if r.price_pass_rate < args.min_pass]
    if failed:
        print(f"\nFAIL: price pass rate below {args.min_pass:.0%} for {', '.join(failed)}")
        return 1
    print(f"\nPASS: every coin at or above {args.min_pass:.0%} of prices within one tick")
    return 0


if __name__ == "__main__":
    sys.exit(main())
