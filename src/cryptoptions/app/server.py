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

from ..market import BybitSource, DemoSource
from ..position import Leg, Position, PositionError
from ..symbols import parse_symbol

STATIC = Path(__file__).parent / "static"


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


def create_app(demo: bool = False) -> FastAPI:
    source = DemoSource() if demo else BybitSource()
    app = FastAPI(title="Options101", docs_url="/api/docs", redoc_url=None)

    def load_chain(coin: str):
        try:
            return source.chain(coin.upper())
        except KeyError as exc:
            raise HTTPException(404, f"No options found for {coin}.") from exc
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            raise HTTPException(
                503,
                "Could not reach Bybit. Check your internet connection, or start the app "
                "with --demo to use sample data.",
            ) from exc
        except RuntimeError as exc:
            raise HTTPException(502, str(exc)) from exc

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
        try:
            position = Position(legs)
        except PositionError as exc:
            raise HTTPException(422, str(exc)) from exc

        result = position.analyze(
            forward, chain.as_of, move_pct=body.move_pct,
            days_elapsed=body.days_elapsed, iv_change=body.iv_change,
        )
        out = asdict(result)
        out.update({
            "legs": details,
            "forward": forward,
            "expiry": position.expiry.astimezone(timezone.utc).isoformat(),
            "as_of": chain.as_of.isoformat(),
            "reference_iv": position.reference_vol(),
            "source": chain.source,
        })
        return out

    app.mount("/static", StaticFiles(directory=STATIC), name="static")

    @app.get("/")
    def index():
        return FileResponse(STATIC / "index.html")

    return app
