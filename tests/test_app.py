import math
from datetime import date, datetime, timedelta, timezone

import pytest

pytest.importorskip("fastapi")
pytest.importorskip("httpx")

from fastapi.testclient import TestClient  # noqa: E402

from cryptoptions.app.server import create_app  # noqa: E402
from cryptoptions.market import BybitSource, DemoSource  # noqa: E402

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


def test_analyze_gamma_trading_for_a_bought_call():
    _, above = pick()
    a = client.post("/api/analyze", json={"coin": "BTC", "legs": [
        {"symbol": above[0].call.symbol, "side": "buy", "quantity": 1}]}).json()
    g = a["gamma_trading"]
    assert g["long_gamma"] is True
    assert g["gamma"] > 0 > g["theta"]
    assert g["breakeven_move"] == pytest.approx(g["breakeven_move_pct"] / 100 * a["forward"])
    # one option's break-even daily move is its implied volatility as a daily move
    iv = a["legs"][0]["mark_iv"]
    assert g["breakeven_move_pct"] == pytest.approx(100 * iv / math.sqrt(365), rel=1e-6)


def test_analyze_gamma_trading_is_read_at_todays_price_whatever_the_what_if():
    _, above = pick()
    legs = [{"symbol": above[0].call.symbol, "side": "buy", "quantity": 1}]
    today = client.post("/api/analyze", json={"coin": "BTC", "legs": legs}).json()
    what_if = client.post("/api/analyze", json={
        "coin": "BTC", "legs": legs, "move_pct": 10, "days_elapsed": 5, "iv_change": 10}).json()
    assert what_if["scenario"]["gamma"] != pytest.approx(today["scenario"]["gamma"], rel=1e-3)
    g, g_if = today["gamma_trading"], what_if["gamma_trading"]
    assert g_if["long_gamma"] is g["long_gamma"] is True
    # the demo price drifts a little between requests, far less than the levers move it
    for key in ("breakeven_move", "breakeven_move_pct", "gamma", "theta"):
        assert g_if[key] == pytest.approx(g[key], rel=1e-3)


def test_analyze_gamma_trading_off_when_not_long_gamma():
    exp, _ = pick()
    below = [r for r in exp.rows if r.strike < exp.forward]
    # a debit call spread with the price above its short strike is short gamma
    a = client.post("/api/analyze", json={"coin": "BTC", "legs": [
        {"symbol": below[0].call.symbol, "side": "buy", "quantity": 1},
        {"symbol": below[-1].call.symbol, "side": "sell", "quantity": 1},
    ]}).json()
    g = a["gamma_trading"]
    assert g["gamma"] < 0
    assert g["long_gamma"] is False
    assert g["breakeven_move"] is None and g["breakeven_move_pct"] is None


def test_analyze_gamma_trading_off_when_the_legs_nearly_cancel():
    exp, above = pick()
    below = [r for r in exp.rows if r.strike < exp.forward]
    bought = below[-2].call
    sold = min(exp.rows, key=lambda r: abs(r.strike - exp.forward)).call
    # sell just enough of the option nearer the money to cancel 95% of the bought option's gamma
    q = 0.95 * bought.gamma / sold.gamma
    a = client.post("/api/analyze", json={"coin": "BTC", "legs": [
        {"symbol": bought.symbol, "side": "buy", "quantity": 1},
        {"symbol": sold.symbol, "side": "sell", "quantity": q},
    ]}).json()
    g = a["gamma_trading"]
    assert g["gamma"] > 0
    assert g["long_gamma"] is False
    assert g["breakeven_move"] is None and g["breakeven_move_pct"] is None


def test_history_shape():
    data = client.get("/api/history/btc").json()
    assert (data["coin"], data["symbol"], data["source"]) == ("BTC", "BTCUSDT", "demo")
    datetime.fromisoformat(data["as_of"])
    days = data["days"]
    assert len(days) == 30
    dates = [date.fromisoformat(d["date"]) for d in days]
    assert all(b - a == timedelta(days=1) for a, b in zip(dates, dates[1:]))
    assert dates[-1] == datetime.now(timezone.utc).date() - timedelta(days=1)
    for prev, day in zip(days, days[1:]):
        assert day["move_pct"] == pytest.approx(100 * (day["close"] / prev["close"] - 1))
    returns = [math.log(1 + d["move_pct"] / 100) for d in days]
    assert data["realized_vol_30d"] == pytest.approx(math.sqrt(365 * sum(r * r for r in returns) / 30))
    assert data["realized_vol_7d"] == pytest.approx(math.sqrt(365 * sum(r * r for r in returns[-7:]) / 7))
    moves = [d["move_pct"] for d in days]
    assert data["rms_daily_move_pct_30d"] == pytest.approx(math.sqrt(sum(m * m for m in moves) / 30))


def test_history_unknown_coin():
    r = client.get("/api/history/NOPE")
    assert r.status_code == 404
    assert "NOPE" in r.json()["detail"]


@pytest.fixture
def live_client(fake_bybit, monkeypatch):
    """The app in live mode, talking to the local fake Bybit instead of the real one."""
    url, state = fake_bybit
    monkeypatch.setattr("cryptoptions.app.server.BybitSource", lambda: BybitSource(base_url=url))
    return TestClient(create_app(demo=False)), state


@pytest.mark.parametrize("path", ["/api/history/BTC", "/api/chain/BTC"])
def test_bybit_answering_with_an_error_status_is_a_bad_gateway(live_client, path):
    live, state = live_client
    state["status"], state["body"] = 403, {"retCode": 10006, "retMsg": "access too frequent"}
    r = live.get(path)
    # Bybit was reached, so this must not read as a lost internet connection
    assert r.status_code == 502
    assert "403" in r.json()["detail"]


@pytest.mark.parametrize("path", ["/api/history/BTC", "/api/chain/BTC"])
def test_bybit_answering_with_a_web_page_is_a_bad_gateway(live_client, path):
    live, state = live_client
    state["body"] = b"<html><body>502 Bad Gateway</body></html>"
    r = live.get(path)
    assert r.status_code == 502
    assert "JSON" in r.json()["detail"]
