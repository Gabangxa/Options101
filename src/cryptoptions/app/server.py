"""FastAPI server: serves the web app and a small JSON API over the core."""

from __future__ import annotations

import urllib.error
from dataclasses import asdict
from datetime import timezone
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from ..gamma import breakeven_move, daily_moves_pct, gross_gamma, realized_vol, rms_move_pct
from ..market import BybitSource, DemoSource
from ..position import FeeSchedule, Leg, Position, PositionError
from ..symbols import parse_symbol

STATIC = Path(__file__).parent / "static"
# Daily moves shown against the break-even line; one more close than that is fetched.
HISTORY_MOVES = 30


class LegIn(BaseModel):
    symbol: str
    side: str = Field(pattern="^(buy|sell)$")
    quantity: float = Field(gt=0, le=10_000)


class AnalyzeIn(BaseModel):
    coin: str
    legs: list[LegIn]
    move_pct: float = Field(0.0, ge=-95, le=500)
    days_elapsed: float = Field(0.0, ge=0)
    iv_change: float = Field(0.0, ge=-90, le=200)
    price_at: str = Field("market", pattern="^(market|mark)$")
    include_fees: bool = True
    fee_rate_pct: float = Field(0.03, ge=0, le=1)   # trading fee rate, percent of the index price


def create_app(demo: bool = False) -> FastAPI:
    source = DemoSource() if demo else BybitSource()
    app = FastAPI(title="Options101", docs_url="/api/docs", redoc_url=None)

    def from_source(fetch, not_found: str):
        # One mapping from source errors to HTTP errors, shared by the chain, analyze and history endpoints.
        try:
            return fetch()
        except KeyError as exc:
            raise HTTPException(404, not_found) from exc
        except urllib.error.HTTPError as exc:
            # Before URLError, which it subclasses: Bybit was reached and answered with an error status.
            raise HTTPException(502, f"Bybit answered with an error: HTTP {exc.code} {exc.reason}.") from exc
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            raise HTTPException(
                503,
                "Could not reach Bybit. Check your internet connection, or start the app "
                "with --demo to use sample data.",
            ) from exc
        except RuntimeError as exc:
            raise HTTPException(502, str(exc)) from exc

    def load_chain(coin: str):
        return from_source(lambda: source.chain(coin.upper()), f"No options found for {coin}.")

    @app.get("/api/status")
    def status():
        return {"source": source.name}

    @app.get("/api/coins")
    def coins():
        try:
            return {"source": source.name, "coins": source.coins()}
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            raise HTTPException(503, "Could not reach Bybit. Start the app with --demo to use sample data.") from exc

    @app.get("/api/chain/{coin}")
    def chain(coin: str):
        return load_chain(coin).to_dict()

    @app.get("/api/history/{coin}")
    def history(coin: str):
        coin = coin.upper()
        past = from_source(
            lambda: source.history(coin, days=HISTORY_MOVES + 1),
            f"No daily price history found for {coin}.",
        )
        closes = [c.close for c in past.closes]
        moves = daily_moves_pct(closes)
        # Each move belongs to the day it ended on, so the oldest close only anchors the first move.
        shown = list(zip(past.closes[1:], moves))[-HISTORY_MOVES:]
        return {
            "coin": past.coin,
            "symbol": past.symbol,
            "source": past.source,
            "as_of": past.as_of.isoformat(),
            "days": [{"date": c.date.isoformat(), "close": c.close, "move_pct": m} for c, m in shown],
            "realized_vol_7d": realized_vol(closes, 7),
            "realized_vol_30d": realized_vol(closes, 30),
            "rms_daily_move_pct_30d": rms_move_pct(closes, 30),
        }

    @app.post("/api/analyze")
    def analyze(body: AnalyzeIn):
        chain = load_chain(body.coin)
        legs: list[Leg] = []
        forward = None
        details = []
        for item in body.legs:
            try:
                expiry, quote = chain.find(item.symbol)
            except KeyError as exc:
                raise HTTPException(
                    409, f"{item.symbol} is no longer listed. Remove it and pick another strike."
                ) from exc
            parsed = parse_symbol(item.symbol)
            side = 1 if item.side == "buy" else -1
            if body.price_at == "mark":
                entry = quote.mark
            else:
                entry = quote.ask if side == 1 else quote.bid
                if entry <= 0:
                    raise HTTPException(
                        409,
                        f"Nobody is currently {'selling' if side == 1 else 'bidding for'} "
                        f"{item.symbol}, so there is no price to {item.side} at. Try another strike "
                        "or switch pricing to mark.",
                    )
            legs.append(Leg(parsed.option_type, parsed.strike, parsed.expiry, item.quantity,
                            side, entry, quote.mark_iv, item.symbol))
            forward = expiry.forward
            details.append({
                "symbol": item.symbol, "side": item.side, "quantity": item.quantity,
                "strike": parsed.strike, "type": parsed.option_type.value,
                "entry_price": entry, "mark": quote.mark, "mark_iv": quote.mark_iv,
                "spread_iv_points": quote.spread_iv_points,
            })
        fees = FeeSchedule.bybit(body.coin, legs[0].expiry, body.fee_rate_pct / 100) if body.include_fees and legs else None
        try:
            position = Position(legs, fees, index=forward)
        except PositionError as exc:
            raise HTTPException(422, str(exc)) from exc

        result = position.analyze(
            forward, chain.as_of, move_pct=body.move_pct,
            days_elapsed=body.days_elapsed, iv_change=body.iv_change,
        )
        # Optional overlay, read from today's Greeks. It only adds numbers; the position is unchanged.
        today = result.now
        move = breakeven_move(today.gamma, today.theta, gross_gamma(position, forward, chain.as_of))
        out = asdict(result)
        out.update({
            "legs": details,
            "forward": forward,
            "expiry": position.expiry.astimezone(timezone.utc).isoformat(),
            "as_of": chain.as_of.isoformat(),
            "reference_iv": position.reference_vol(),
            "source": chain.source,
            "gamma_trading": {
                "long_gamma": move is not None,
                "breakeven_move": move,
                "breakeven_move_pct": None if move is None else 100.0 * move / forward,
                "gamma": today.gamma,
                "theta": today.theta,
            },
            "fee_rate_pct": body.fee_rate_pct if fees else 0.0,
            "delivery_fee_rate_pct": fees.delivery_rate * 100 if fees else 0.0,
            "delivery_fee_applies": bool(fees and fees.charge_delivery),
        })
        return out

    app.mount("/static", StaticFiles(directory=STATIC), name="static")

    @app.get("/")
    def index():
        return FileResponse(STATIC / "index.html")

    return app
