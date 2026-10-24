import pytest

pytest.importorskip("fastapi")
pytest.importorskip("httpx")

from fastapi.testclient import TestClient  # noqa: E402

from cryptoptions.app.server import create_app  # noqa: E402
from cryptoptions.market import DemoSource  # noqa: E402

client = TestClient(create_app(demo=True))


def pick(coin="BTC"):
    chain = DemoSource().chain(coin)
    exp = next(e for e in chain.expiries if e.days >= 7)
    above = [r for r in exp.rows if r.strike > exp.forward]
    return exp, above


def test_page_and_status():
    assert client.get("/").status_code == 200
    assert client.get("/static/app.js").status_code == 200
    assert client.get("/api/status").json() == {"source": "demo"}
    assert "BTC" in client.get("/api/coins").json()["coins"]


def test_chain_shape():
    data = client.get("/api/chain/ETH").json()
    assert data["source"] == "demo"
    e = data["expiries"][0]
    assert e["forward"] > 0 and e["rows"]
    q = e["rows"][0]["call"]
    assert {"bid", "ask", "mark", "mark_iv", "delta", "model_price"} <= set(q)


def test_analyze_debit_spread():
    _, above = pick()
    body = {"coin": "BTC", "legs": [
        {"symbol": above[0].call.symbol, "side": "buy", "quantity": 1},
        {"symbol": above[3].call.symbol, "side": "sell", "quantity": 1},
    ], "move_pct": 5, "days_elapsed": 3, "iv_change": -5}
    a = client.post("/api/analyze", json=body).json()
    assert a["net_premium"] == pytest.approx(above[0].call.ask - above[3].call.bid)
    assert a["max_gain"] is not None
    assert len(a["grid_pnl"]) == len(a["grid_days"])
    assert a["scenario"]["iv_change"] == -5


def test_analyze_rejects_naked_short():
    _, above = pick()
    r = client.post("/api/analyze", json={"coin": "BTC", "legs": [
        {"symbol": above[0].call.symbol, "side": "sell", "quantity": 1}]})
    assert r.status_code == 422
    assert "premium" in r.json()["detail"]


def test_unknown_symbol_and_coin():
    r = client.post("/api/analyze", json={"coin": "BTC", "legs": [
        {"symbol": "BTC-1JAN20-1000-C-USDT", "side": "buy", "quantity": 1}]})
    assert r.status_code == 409
    assert client.get("/api/chain/NOPE").status_code == 404
