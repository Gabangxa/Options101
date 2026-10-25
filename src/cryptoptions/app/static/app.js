"use strict";

const state = {
  coin: null,
  chain: null,
  expiry: null,          // selected expiry code
  legs: [],              // {symbol, side, quantity}
  legExpiry: null,       // expiry code shared by all legs
  priceAt: "market",
  feesOn: safeGet("feesOn") !== "false",
  feeRate: Number(safeGet("feeRate") || "0.03"),
  analysis: null,
  scrolledFor: null,
  gammaOn: safeGet("gamma") === "on",  // optional overlay; off unless the user turned it on
  history: {},           // per coin: {data, error, fetchedAt} from /api/history
};

const $ = (id) => document.getElementById(id);
const REFRESH_MS = 15000;
const SVG = "http://www.w3.org/2000/svg";

/* ---------- formatting ---------- */

function decimalsFor(ref) {
  const a = Math.abs(ref);
  if (a >= 1000) return 0;
  if (a >= 10) return 2;
  if (a >= 1) return 3;
  return 4;
}
function num(n, d = 0) {
  return Number(n).toLocaleString("en-US", { minimumFractionDigits: d, maximumFractionDigits: d });
}
function price(n, ref) {
  return num(n, decimalsFor(ref ?? n));
}
function money(n, ref, signed = false) {
  const d = Math.max(decimalsFor(ref ?? n), Math.abs(ref ?? n) < 50 ? 2 : 0);
  const text = num(Math.abs(n), d);
  if (!signed) return (n < 0 ? "-" : "") + text;
  return (n > 0 ? "+" : n < 0 ? "-" : "") + text;
}
function pct(n, d = 1, signed = true) {
  const s = signed && n > 0 ? "+" : "";
  return `${s}${n.toFixed(d)}%`;
}
function dayLabel(days) {
  if (days < 1) return `${Math.round(days * 24)} hours`;
  const rounded = Math.round(days * 4) / 4;
  return `${rounded % 1 === 0 ? rounded.toFixed(0) : rounded} day${rounded === 1 ? "" : "s"}`;
}
function dateLabel(date) {
  return date.toLocaleDateString("en-GB", { weekday: "short", day: "numeric", month: "short" });
}
function el(tag, attrs = {}, ...children) {
  const node = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs)) {
    if (k === "class") node.className = v;
    else if (k.startsWith("on")) node.addEventListener(k.slice(2), v);
    else if (v !== null && v !== undefined && v !== false) node.setAttribute(k, v === true ? "" : v);
  }
  for (const c of children.flat()) if (c !== null && c !== undefined) node.append(c.nodeType ? c : String(c));
  return node;
}
/* theme colours, read from the CSS tokens so charts follow light and dark mode */
function C(name) {
  return getComputedStyle(document.documentElement).getPropertyValue(`--${name}`).trim();
}
function rgb(hex) {
  const h = hex.replace("#", "");
  const full = h.length === 3 ? h.split("").map((c) => c + c).join("") : h;
  return [0, 2, 4].map((i) => parseInt(full.slice(i, i + 2), 16));
}
function svg(tag, attrs = {}) {
  const node = document.createElementNS(SVG, tag);
  for (const [k, v] of Object.entries(attrs)) node.setAttribute(k, v);
  return node;
}

/* ---------- api ---------- */

async function api(path, body) {
  const res = await fetch(path, body ? {
    method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body),
  } : undefined);
  const data = await res.json().catch(() => ({}));
  if (!res.ok) {
    const detail = typeof data.detail === "string" ? data.detail
      : Array.isArray(data.detail) ? data.detail.map((d) => d.msg).join("; ") : `Request failed (${res.status})`;
    throw new Error(detail);
  }
  return data;
}

/* ---------- feed status ---------- */

function setFeed(kind, text) {
  const feed = $("feed");
  feed.className = `feed ${kind}`;
  feed.replaceChildren(el("span", { class: "dot", "aria-hidden": "true" }), text);
}

/* ---------- coins ---------- */

async function loadCoins() {
  try {
    const data = await api("/api/coins");
    const saved = safeGet("coin");
    const coins = data.coins.length ? data.coins : ["BTC"];
    $("coins").replaceChildren(...coins.map((c) =>
      el("button", { type: "button", "aria-pressed": "false", "data-coin": c, onclick: () => selectCoin(c) }, c)));
    selectCoin(coins.includes(saved) ? saved : coins.includes("BTC") ? "BTC" : coins[0]);
  } catch (e) {
    setFeed("down", e.message);
  }
}

function selectCoin(coin) {
  if (coin === state.coin) return;
  if (state.legs.length && !confirm(`Switching to ${coin} clears your current position. Continue?`)) return;
  state.coin = coin;
  state.legs = [];
  state.legExpiry = null;
  state.expiry = null;
  state.analysis = null;
  safeSet("coin", coin);
  for (const b of $("coins").children) b.setAttribute("aria-pressed", String(b.dataset.coin === coin));
  resetLevers();
  renderPosition();
  loadChain();
}

function safeGet(k) { try { return localStorage.getItem(k); } catch { return null; } }
function safeSet(k, v) { try { localStorage.setItem(k, v); } catch { /* storage unavailable */ } }

/* ---------- chain ---------- */

let chainTimer = null;

async function loadChain() {
  clearTimeout(chainTimer);
  const coin = state.coin;
  try {
    const chain = await api(`/api/chain/${coin}`);
    if (coin !== state.coin) return;
    state.chain = chain;
    const asOf = new Date(chain.as_of).toLocaleTimeString("en-GB");
    if (chain.source === "demo") setFeed("demo", `Demo data, not live prices (${asOf})`);
    else setFeed("live", `Live from Bybit, updated ${asOf}`);
    const codes = chain.expiries.map((e) => e.code);
    if (!codes.includes(state.expiry)) state.expiry = state.legExpiry && codes.includes(state.legExpiry)
      ? state.legExpiry : defaultExpiry(chain);
    renderExpiries();
    renderChain();
    if (state.legs.length) analyze(); else renderPosition();
  } catch (e) {
    setFeed("down", e.message);
  } finally {
    chainTimer = setTimeout(loadChain, REFRESH_MS);
  }
}

function defaultExpiry(chain) {
  // the first expiry at least a week out, which suits a first trade idea
  const pick = chain.expiries.find((e) => e.days >= 7) || chain.expiries[0];
  return pick ? pick.code : null;
}

function currentExpiry() {
  return state.chain?.expiries.find((e) => e.code === state.expiry) || null;
}

function renderExpiries() {
  const box = $("expiries");
  box.replaceChildren(...state.chain.expiries.map((e) => {
    const locked = state.legExpiry && state.legExpiry !== e.code;
    const d = new Date(e.expiry);
    return el("button", {
      type: "button", role: "tab", "aria-selected": String(e.code === state.expiry),
      disabled: locked, title: locked ? "Your position uses another expiry. Clear it to switch." : null,
      onclick: () => { state.expiry = e.code; state.scrolledFor = null; renderExpiries(); renderChain(); if (!state.legs.length) renderPosition(); },
    }, d.toLocaleDateString("en-GB", { day: "numeric", month: "short" }), el("small", {}, dayLabel(e.days)));
  }));
}

function renderChain() {
  const exp = currentExpiry();
  const body = $("chain-body");
  if (!exp) { body.replaceChildren(); return; }
  const fwd = exp.forward;
  $("forward").replaceChildren(
    `${state.coin} forward for this expiry: `, el("strong", {}, price(fwd)),
    ` USDT. Expires ${dateLabel(new Date(exp.expiry))}, 08:00 UTC.`);

  const held = new Map(state.legs.map((l) => [l.symbol, l.side]));
  let atmIndex = 0, best = Infinity;
  exp.rows.forEach((r, i) => { const d = Math.abs(r.strike - fwd); if (d < best) { best = d; atmIndex = i; } });

  const quoteCells = (q, kind) => {
    const cls = kind === "call" ? "c" : "p";
    if (!q) return [el("td", { class: `${cls} muted` }, ""), el("td", { class: cls }), el("td", { class: cls }), el("td", { class: cls })];
    const side = held.get(q.symbol);
    const btn = (s, value) => el("td", { class: `${cls} pick ${s === "buy" ? "ask" : "bid"}${side === s ? " held" : ""}` },
      el("button", {
        type: "button", disabled: !(value > 0),
        title: value > 0 ? `${s === "buy" ? "Buy" : "Sell"} ${q.symbol} at ${price(value, fwd)}` : "No price available",
        onclick: () => addLeg(q.symbol, s),
      }, value > 0 ? price(value, fwd) : "none"));
    const iv = el("td", { class: `${cls} muted`, title: q.spread_iv_points != null ? `Bid to ask spread: ${q.spread_iv_points.toFixed(1)} vol points` : null }, `${(q.mark_iv * 100).toFixed(1)}%`);
    const delta = el("td", { class: `${cls} muted` }, (Math.abs(q.delta) < 0.005 ? 0 : q.delta).toFixed(2));
    return kind === "call" ? [btn("sell", q.bid), btn("buy", q.ask), iv, delta] : [delta, iv, btn("sell", q.bid), btn("buy", q.ask)];
  };

  body.replaceChildren(...exp.rows.map((r, i) => el("tr", {
    class: [i === atmIndex ? "atm" : "", r.strike < fwd ? "itm-call" : "", r.strike > fwd ? "itm-put" : ""].join(" "),
  }, ...quoteCells(r.call, "call"), el("td", { class: "strike" }, price(r.strike, fwd)), ...quoteCells(r.put, "put"))));

  if (state.scrolledFor !== exp.code) {
    state.scrolledFor = exp.code;
    const row = body.children[atmIndex];
    const scroller = $("chain-scroll");
    if (row) scroller.scrollTop = Math.max(row.offsetTop - scroller.clientHeight / 2, 0);
  }
}

/* ---------- position ---------- */

function addLeg(symbol, side) {
  hideError();
  if (state.legExpiry && state.legExpiry !== state.expiry) {
    showError("All options in a position must share one expiry. Clear the position to use another date.");
    return;
  }
  const existing = state.legs.find((l) => l.symbol === symbol);
  if (existing) {
    if (existing.side === side) existing.quantity += 1;
    else existing.side = side;
  } else {
    state.legs.push({ symbol, side, quantity: 1 });
  }
  state.legExpiry = state.expiry;
  renderExpiries();
  renderChain();
  renderPosition();
  analyze({ reveal: true });
}

function revealWork() {
  // on narrow screens the position sits below the chain; bring it into view
  // the sticky header wraps to different heights, so measure it rather than guess
  if (window.matchMedia("(max-width: 1100px)").matches) {
    const header = document.querySelector(".bar").offsetHeight;
    const top = $("pos-title").getBoundingClientRect().top + window.scrollY - header - 12;
    window.scrollTo({ top, behavior: "smooth" });
  }
}

function removeLeg(symbol) {
  state.legs = state.legs.filter((l) => l.symbol !== symbol);
  if (!state.legs.length) { state.legExpiry = null; state.analysis = null; resetLevers(); }
  renderExpiries();
  renderChain();
  renderPosition();
  if (state.legs.length) analyze();
}

/* ---------- start panel and example trades ---------- */

function exampleTrades() {
  const exp = currentExpiry();
  if (!exp) return [];
  const rows = exp.rows.filter((r) => r.call && r.put);
  const fwd = exp.forward;
  const ok = (q, side) => q && (side === "buy" ? q.ask > 0 : q.bid > 0);
  const atm = rows.reduce((best, r) => (!best || Math.abs(r.strike - fwd) < Math.abs(best.strike - fwd) ? r : best), null);
  const above = rows.filter((r) => r.strike > fwd);
  const below = rows.filter((r) => r.strike < fwd).reverse();
  const out = [];
  if (above[0] && ok(above[0].call, "buy")) out.push({
    title: "Buy a call", why: "You expect the price to rise.",
    legs: [{ symbol: above[0].call.symbol, side: "buy", quantity: 1 }],
  });
  if (below[0] && ok(below[0].put, "buy")) out.push({
    title: "Buy a put", why: "You expect the price to fall.",
    legs: [{ symbol: below[0].put.symbol, side: "buy", quantity: 1 }],
  });
  if (atm && ok(atm.call, "buy") && ok(atm.put, "buy")) out.push({
    title: "Buy a straddle", why: "You expect a big move but do not know which way.",
    legs: [{ symbol: atm.call.symbol, side: "buy", quantity: 1 }, { symbol: atm.put.symbol, side: "buy", quantity: 1 }],
  });
  const far = above.slice(2).find((r) => ok(r.call, "sell"));
  if (above[0] && far && ok(above[0].call, "buy")) out.push({
    title: "Buy a call spread", why: "You expect a moderate rise and want to pay less.",
    legs: [{ symbol: above[0].call.symbol, side: "buy", quantity: 1 }, { symbol: far.call.symbol, side: "sell", quantity: 1 }],
  });
  return out;
}

function useExample(ex) {
  hideError();
  state.legs = ex.legs.map((l) => ({ ...l }));
  state.legExpiry = state.expiry;
  resetLevers();
  renderExpiries();
  renderChain();
  renderPosition();
  analyze({ reveal: true });
}

function renderStart() {
  const examples = exampleTrades();
  return el("div", { class: "start" },
    el("p", { class: "start-lead" }, "Pick an option in the chain to see what it costs, what you could make or lose, and how that changes with price, time and volatility."),
    el("ol", { class: "steps" },
      el("li", {}, "Choose an expiry date above the chain."),
      el("li", {}, "Click an ", el("strong", {}, "ask"), " price to buy that option."),
      el("li", {}, "Add a second option if you want a straddle, strangle or spread.")),
    examples.length ? el("div", { class: "examples" },
      el("p", { class: "examples-title" }, "Or start from an example for this expiry:"),
      el("div", { class: "example-list" }, ...examples.map((ex) =>
        el("button", { type: "button", class: "example", onclick: () => useExample(ex) },
          el("span", { class: "ex-title" }, ex.title), el("span", { class: "ex-why" }, ex.why))))) : null,
    el("div", { class: "preview" },
      el("p", { class: "examples-title" }, "Once you pick, this side of the screen shows:"),
      el("ul", {},
        el("li", {}, "What you pay, the most you can lose and gain, and your break-even price"),
        el("li", {}, "What-if levers for price, days and volatility, explained in plain words"),
        el("li", {}, "A profit chart and a price-by-day heatmap"),
        el("li", {}, "Which assumption your result depends on most"))));
}

function describeLeg(symbol) {
  const parts = symbol.split("-");
  const kind = parts[3] === "C" ? "call" : "put";
  return { strike: Number(parts[2]), kind };
}

function renderPosition() {
  const box = $("legs");
  const showWork = state.legs.length > 0 && state.analysis;
  for (const id of ["whatif-sheet", "charts-sheet", "sens-sheet", "gamma-sheet"]) $(id).hidden = !showWork;
  $("facts").hidden = !showWork;

  document.querySelector(".desk").classList.toggle("has-position", state.legs.length > 0);
  if (!state.legs.length) {
    box.replaceChildren(renderStart());
    return;
  }
  const details = new Map((state.analysis?.legs || []).map((d) => [d.symbol, d]));
  const ref = currentExpiry()?.forward ?? 1;
  box.replaceChildren(...state.legs.map((leg) => {
    const { strike, kind } = describeLeg(leg.symbol);
    const d = details.get(leg.symbol);
    const total = d ? d.entry_price * leg.quantity : null;
    const sideBtn = (s) => el("button", {
      type: "button", class: s, "aria-pressed": String(leg.side === s),
      onclick: () => { leg.side = s; renderPosition(); renderChain(); analyze(); },
    }, s === "buy" ? "Buy" : "Sell");
    const warn = d && d.spread_iv_points != null && d.spread_iv_points > 5
      ? el("p", { class: "warn" }, `Wide market: bid and ask are ${d.spread_iv_points.toFixed(1)} vol points apart, so getting in and out is costly.`)
      : null;
    return el("div", { class: "leg" },
      el("div", { class: "side", role: "group", "aria-label": "Buy or sell" }, sideBtn("buy"), sideBtn("sell")),
      el("div", { class: "what" }, `${price(strike, ref)} ${kind}`, el("small", {}, leg.symbol)),
      el("label", { class: "qty" }, "Qty",
        el("input", {
          type: "number", min: "0.01", step: "0.01", value: String(leg.quantity),
          "aria-label": `Quantity for ${leg.symbol}`,
          onchange: (ev) => {
            const v = Number(ev.target.value);
            if (v > 0) { leg.quantity = v; analyze(); } else ev.target.value = String(leg.quantity);
          },
        })),
      el("div", { class: "cost" }, total == null ? "" : money(total, ref),
        el("small", {}, total == null ? "" : leg.side === "buy" ? "you pay" : "you receive")),
      el("button", { type: "button", class: "remove", "aria-label": `Remove ${leg.symbol}`, onclick: () => removeLeg(leg.symbol) }, "×"),
      warn);
  }));
}

function showError(msg) { const e = $("error"); e.textContent = msg; e.hidden = false; }
function hideError() { $("error").hidden = true; }

/* ---------- analysis ---------- */

let analyzeTimer = null;
let analyzeSeq = 0;
// scrolling before the result renders lands short: the page is not full height yet
let revealAfterAnalyze = false;

function analyze({ reveal = false } = {}) {
  if (reveal) revealAfterAnalyze = true;
  clearTimeout(analyzeTimer);
  analyzeTimer = setTimeout(runAnalyze, 60);
}

async function runAnalyze() {
  if (!state.legs.length) return;
  const seq = ++analyzeSeq;
  try {
    const result = await api("/api/analyze", {
      coin: state.coin, legs: state.legs, price_at: state.priceAt,
      include_fees: state.feesOn, fee_rate_pct: state.feeRate,
      move_pct: Number($("move").value), days_elapsed: Number($("days").value), iv_change: Number($("iv").value),
    });
    if (seq !== analyzeSeq) return;
    state.analysis = result;
    hideError();
    syncDaySlider(result.days_to_expiry);
    renderPosition();
    renderFacts(result);
    renderReadout(result);
    renderPayoff(result);
    renderHeat(result);
    renderSens(result);
    renderGamma();
  } catch (e) {
    if (seq !== analyzeSeq) return;
    state.analysis = null;
    renderPosition();
    showError(e.message);
  }
  // reveal on errors too, so the message is not left off-screen
  if (revealAfterAnalyze) { revealAfterAnalyze = false; revealWork(); }
}

function syncDaySlider(dte) {
  const s = $("days");
  s.max = String(Math.max(dte, 0.01));
  s.step = dte < 3 ? String(1 / 24) : "0.25";
  if (Number(s.value) > dte) s.value = String(dte);
  updateLeverLabels();
}

function updateLeverLabels() {
  $("move-out").textContent = pct(Number($("move").value), 1);
  const days = Number($("days").value);
  $("days-out").textContent = days === 0 ? "today" : dayLabel(days);
  const iv = Number($("iv").value);
  $("iv-out").textContent = iv === 0 ? "unchanged" : `${iv > 0 ? "+" : ""}${iv} points`;
}

function resetLevers() {
  $("move").value = "0"; $("days").value = "0"; $("iv").value = "0";
  updateLeverLabels();
}

function renderFacts(a) {
  const ref = a.forward;
  const fees = a.fees_included;
  const be = a.breakevens.map((b, i) => `${price(b, ref)} (${pct(a.breakeven_moves[i])})`).join(" or ");
  const exitCost = -a.now.pnl;
  const settlement = !fees ? ""
    : a.delivery_fee_applies ? ` If it expires in the money, a ${a.delivery_fee_rate_pct.toFixed(3).replace(/0+$/, "")}% settlement fee applies instead.`
    : " No settlement fee on daily options.";
  const items = [
    ["You pay", `${money(a.net_premium + a.entry_fees, ref)} USDT`,
      fees ? `premium ${money(a.net_premium, ref)} plus fees ${money(a.entry_fees, ref)}`
        : `${pct(100 * a.net_premium / (a.forward * Math.max(...a.legs.map((l) => l.quantity))), 2, false)} of the ${state.coin} price`],
    ["Most you can lose", `${money(a.max_loss, ref)} USDT`, fees ? "if the options expire worthless, fees included" : "if the options expire worthless"],
    ["Most you can gain", a.max_gain == null ? "No limit" : `${money(a.max_gain, ref)} USDT`,
      a.max_gain == null ? "grows as the price keeps rising" : fees ? "capped by the option you sold, after fees" : "capped by the option you sold"],
    ["Break-even at expiry", be || "none", fees ? "price needed at expiry to get your money back, after fees" : "price needed at expiry to get your money back"],
    ["Chance of profit at expiry", `${(a.prob_profit_at_expiry * 100).toFixed(0)}%`, `as priced by the market, at ${(a.reference_iv * 100).toFixed(0)}% implied volatility`],
  ];
  if (fees) items.push(["Trading fees", `${money(a.entry_fees, ref)} USDT`,
    `to open, at ${a.fee_rate_pct}%. About ${money(a.exit_fees_now, ref)} more to close before expiry.${settlement}`]);
  if (state.priceAt === "market" || fees) items.push(["Cost to get out today", `${money(exitCost, ref)} USDT`,
    state.priceAt === "market" && fees ? "bid to ask spread plus fees to open and close"
      : fees ? "fees to open and close" : "the bid to ask spread"]);
  $("facts").replaceChildren(...items.map(([dt, dd, small]) =>
    el("div", {}, el("dt", {}, dt), el("dd", {}, dd, el("small", {}, small)))));
}

function renderReadout(a) {
  const s = a.scenario;
  const ref = a.forward;
  const when = new Date(new Date(a.as_of).getTime() + s.days_elapsed * 86400000);
  const atExpiry = Math.abs(s.days_elapsed - a.days_to_expiry) < 1e-6;
  const whenText = s.days_elapsed === 0 ? "today" : atExpiry ? `at expiry (${dateLabel(when)})` : `on ${dateLabel(when)}, ${dayLabel(s.days_elapsed)} from now`;
  const ivText = s.iv_change === 0 ? "implied volatility is unchanged"
    : `implied volatility is ${Math.abs(s.iv_change)} points ${s.iv_change > 0 ? "higher" : "lower"}`;
  const gain = s.pnl >= 0;
  $("readout").replaceChildren(
    `If ${state.coin} is at `, el("b", {}, price(s.forward, ref)), ` (${pct(100 * (s.forward / a.forward - 1))}) ${whenText}`,
    atExpiry ? "" : ` and ${ivText}`,
    `, the position is worth `, el("b", {}, `${money(s.value, ref)} USDT`), `: a `,
    el("b", { class: gain ? "pos" : "neg" }, `${gain ? "gain" : "loss"} of ${money(Math.abs(s.pnl), ref)}`),
    a.fees_included ? " after fees" : "",
    ` (${pct(s.pnl_pct_of_premium, 0)} of what you paid).`);
}

/* ---------- charts ---------- */

function niceTicks(lo, hi, count = 5) {
  const span = hi - lo || 1;
  const raw = span / count;
  const mag = 10 ** Math.floor(Math.log10(raw));
  const step = [1, 2, 2.5, 5, 10].map((m) => m * mag).find((s) => s >= raw);
  const out = [];
  for (let v = Math.ceil(lo / step) * step; v <= hi + 1e-9; v += step) out.push(Number(v.toPrecision(12)));
  return out;
}

function renderPayoff(a) {
  const W = 760, H = 320, m = { l: 70, r: 18, t: 14, b: 40 };
  const xs = a.curve_prices;
  const series = [a.curve_today, a.curve_at_scenario_day, a.curve_at_expiry];
  let lo = Math.min(...series.flat(), 0), hi = Math.max(...series.flat(), 0);
  // with unlimited upside, cap the view so the shape near the strikes stays readable
  if (a.max_gain == null) hi = Math.min(hi, a.net_premium * 6);
  const pad = (hi - lo) * 0.08; lo -= pad; hi += pad;
  const x = (v) => m.l + (v - xs[0]) / (xs[xs.length - 1] - xs[0]) * (W - m.l - m.r);
  const y = (v) => m.t + (hi - Math.min(Math.max(v, lo), hi)) / (hi - lo) * (H - m.t - m.b);
  const root = svg("svg", { viewBox: `0 0 ${W} ${H}` });

  const gx = svg("g", { class: "axis" });
  for (const t of niceTicks(lo, hi, 5)) {
    gx.append(svg("line", { x1: m.l, x2: W - m.r, y1: y(t), y2: y(t), stroke: t === 0 ? C("chart-zero") : C("chart-grid") }));
    const lab = svg("text", { x: m.l - 8, y: y(t) + 4, "text-anchor": "end" }); lab.textContent = money(t, a.forward, true); gx.append(lab);
  }
  for (const t of niceTicks(xs[0], xs[xs.length - 1], 6)) {
    const lab = svg("text", { x: x(t), y: H - m.b + 18, "text-anchor": "middle" }); lab.textContent = price(t, a.forward); gx.append(lab);
  }
  const xl = svg("text", { x: (m.l + W - m.r) / 2, y: H - 4, "text-anchor": "middle" }); xl.textContent = `${state.coin} price (USDT)`; gx.append(xl);
  root.append(gx);

  // shade the area where the expiry payoff is positive
  const profitBand = svg("g");
  for (let i = 1; i < xs.length; i++) {
    if (a.curve_at_expiry[i] > 0 && a.curve_at_expiry[i - 1] > 0) {
      profitBand.append(svg("rect", { x: x(xs[i - 1]), y: m.t, width: x(xs[i]) - x(xs[i - 1]) + 0.5, height: H - m.t - m.b, fill: C("gain-soft"), opacity: "0.55" }));
    }
  }
  root.insertBefore(profitBand, gx);

  const marker = (v, label, color, dash) => {
    root.append(svg("line", { x1: x(v), x2: x(v), y1: m.t, y2: H - m.b, stroke: color, "stroke-dasharray": dash || "", "stroke-width": 1 }));
    const t = svg("text", { x: x(v) + 4, y: m.t + 11, fill: color }); t.textContent = label; root.append(t);
  };
  marker(a.forward, "Now", C("muted"), "2 3");
  if (Math.abs(a.scenario.forward - a.forward) / a.forward > 0.002) marker(a.scenario.forward, "What-if", C("accent"), "4 3");

  const path = (vals, attrs) => {
    const d = vals.map((v, i) => `${i ? "L" : "M"}${x(xs[i]).toFixed(1)},${y(v).toFixed(1)}`).join("");
    root.append(svg("path", { d, fill: "none", ...attrs }));
  };
  path(a.curve_today, { stroke: C("muted"), "stroke-width": 1.5, "stroke-dasharray": "5 4" });
  path(a.curve_at_expiry, { stroke: C("ink"), "stroke-width": 1.75 });
  path(a.curve_at_scenario_day, { stroke: C("accent"), "stroke-width": 2.75 });

  for (const b of a.breakevens) {
    if (b < xs[0] || b > xs[xs.length - 1]) continue;
    root.append(svg("circle", { cx: x(b), cy: y(0), r: 3.5, fill: C("panel"), stroke: C("ink"), "stroke-width": 1.5 }));
  }
  root.append(svg("circle", { cx: x(a.scenario.forward), cy: y(a.scenario.pnl), r: 5, fill: C("accent"), stroke: C("dot-ring"), "stroke-width": 2 }));
  $("payoff").replaceChildren(root);
}

function heatColor(t) {
  const mid = rgb(C("heat-mid")), gain = rgb(C("heat-gain")), loss = rgb(C("heat-loss"));
  const s = Math.sign(t) * Math.abs(t) ** 0.7;
  const to = s >= 0 ? gain : loss;
  const k = Math.min(Math.abs(s), 1);
  return `rgb(${mid.map((c, i) => Math.round(c + (to[i] - c) * k)).join(",")})`;
}

function renderHeat(a) {
  const rows = a.grid_days.length, cols = a.grid_prices.length;
  const W = 760, m = { l: 78, r: 18, t: 8, b: 40 };
  const cw = (W - m.l - m.r) / cols, ch = 22;
  const H = m.t + rows * ch + m.b;
  const flat = a.grid_pnl.flat();
  const scale = Math.max(a.net_premium, Math.min(Math.max(...flat), a.net_premium * 4));
  const root = svg("svg", { viewBox: `0 0 ${W} ${H}` });
  const asOf = new Date(a.as_of).getTime();

  a.grid_pnl.forEach((row, r) => {
    row.forEach((v, c) => {
      const rect = svg("rect", {
        class: "cell", x: m.l + c * cw, y: m.t + r * ch, width: cw + 0.8, height: ch + 0.8, fill: heatColor(v / scale), "shape-rendering": "crispEdges",
      });
      const title = svg("title");
      title.textContent = `${price(a.grid_prices[c], a.forward)} on ${dateLabel(new Date(asOf + a.grid_days[r] * 86400000))}: ${money(v, a.forward, true)} USDT`;
      rect.append(title);
      rect.addEventListener("click", () => {
        $("move").value = String(Math.round(1000 * (a.grid_prices[c] / a.forward - 1)) / 10);
        $("days").value = String(a.grid_days[r]);
        updateLeverLabels();
        analyze();
      });
      root.append(rect);
    });
    const lab = svg("text", { x: m.l - 8, y: m.t + r * ch + ch / 2 + 4, "text-anchor": "end" });
    lab.textContent = r === 0 ? "Today" : r === rows - 1 ? "Expiry" : dateLabel(new Date(asOf + a.grid_days[r] * 86400000)).replace(/^\w+ /, "");
    root.append(lab);
  });

  const x = (v) => m.l + (v - a.grid_prices[0]) / (a.grid_prices[cols - 1] - a.grid_prices[0]) * (cols - 1) * cw + cw / 2;
  for (const t of niceTicks(a.grid_prices[0], a.grid_prices[cols - 1], 6)) {
    const lab = svg("text", { x: x(t), y: m.t + rows * ch + 18, "text-anchor": "middle" }); lab.textContent = price(t, a.forward); root.append(lab);
  }
  const xl = svg("text", { x: (m.l + W - m.r) / 2, y: H - 4, "text-anchor": "middle" }); xl.textContent = `${state.coin} price (USDT)`; root.append(xl);

  // current price and what-if crosshair
  root.append(svg("line", { x1: x(a.forward), x2: x(a.forward), y1: m.t, y2: m.t + rows * ch, stroke: C("ink"), "stroke-dasharray": "2 3", "pointer-events": "none" }));
  const sx = Math.min(Math.max(x(a.scenario.forward), m.l), W - m.r);
  const sy = m.t + (a.scenario.days_elapsed / Math.max(a.days_to_expiry, 1e-9)) * (rows - 1) * ch + ch / 2;
  root.append(svg("line", { x1: sx, x2: sx, y1: m.t, y2: m.t + rows * ch, stroke: C("accent"), "stroke-width": 1.5, "pointer-events": "none" }));
  root.append(svg("line", { x1: m.l, x2: W - m.r, y1: sy, y2: sy, stroke: C("accent"), "stroke-width": 1.5, "pointer-events": "none" }));
  root.append(svg("circle", { cx: sx, cy: sy, r: 6, fill: C("accent"), stroke: C("dot-ring"), "stroke-width": 2, "pointer-events": "none" }));
  $("heat").replaceChildren(root);
}

function renderSens(a) {
  const max = Math.max(...a.sensitivities.map((s) => Math.abs(s.pnl)), 1e-9);
  $("sens").replaceChildren(...a.sensitivities.map((s) => {
    const isPrice = s.lever === "price";
    const color = isPrice ? C("accent") : s.pnl >= 0 ? C("gain") : C("loss");
    const value = isPrice ? `±${money(s.pnl, a.forward)}` : money(s.pnl, a.forward, true);
    return el("div", { class: "row" },
      el("span", {}, s.label),
      el("div", { class: "track", "aria-hidden": "true" }, el("div", { class: "fill", style: `width:${(100 * Math.abs(s.pnl) / max).toFixed(1)}%;background:${color}` })),
      el("span", { class: "val" }, `${value} USDT`));
  }));
}

/* ---------- gamma trading (optional) ---------- */

// Analysis only: it never changes the position, and while it is off it makes no requests.
// A new daily close appears once a day, so ten minutes old is still fresh enough.
const HISTORY_MAX_AGE_MS = 10 * 60 * 1000;
// renderGamma runs after every analyze, so a failed fetch waits a while before trying again
const HISTORY_RETRY_MS = 60 * 1000;
let historySeq = 0;
let historyLoading = null;  // coin with a history request in flight

function historyIsStale(coin) {
  const entry = state.history[coin];
  if (!entry) return true;
  return Date.now() - entry.fetchedAt > (entry.error ? HISTORY_RETRY_MS : HISTORY_MAX_AGE_MS);
}

async function loadHistory(coin) {
  if (historyLoading === coin) return;
  historyLoading = coin;
  const seq = ++historySeq;
  let entry;
  try {
    entry = { data: await api(`/api/history/${coin}`), error: null, fetchedAt: Date.now() };
  } catch (e) {
    // keep the last good days on screen, so a failed refresh does not blank the chart
    entry = { data: state.history[coin]?.data ?? null, error: e.message, fetchedAt: Date.now() };
  }
  if (seq !== historySeq) return;  // a request for another coin has started since
  historyLoading = null;
  state.history[coin] = entry;
  if (coin === state.coin) renderGamma();
}

function renderGamma() {
  // a fault in the optional overlay stays in its own sheet: it must never clear the core analysis
  try {
    drawGamma();
  } catch (e) {
    console.error(e);
    $("gamma-readout").replaceChildren(`Gamma trading could not be shown: ${e.message}`);
    $("gamma-history").replaceChildren();
  }
}

function drawGamma() {
  const on = state.gammaOn;
  const toggle = $("gamma-toggle");
  // the label says what a press does, so the state is announced as shown or hidden, not as pressed
  toggle.setAttribute("aria-expanded", String(on));
  toggle.textContent = on ? "Turn off" : "Turn on";
  $("gamma-body").hidden = !on;
  const readout = $("gamma-readout"), past = $("gamma-history");
  const a = state.analysis;
  if (!on || !a) { readout.replaceChildren(); past.replaceChildren(); return; }
  const g = a.gamma_trading;
  if (!g) {
    // the page is served fresh from disk, so it can be newer than a server started earlier
    readout.replaceChildren("Restart Options101 to see gamma trading: the server running now is older than this page.");
    past.replaceChildren();
    return;
  }
  if (!g.long_gamma) {
    readout.replaceChildren(
      "Gamma trading does not apply to this position at today's price: once hedged, a move in either ",
      "direction would earn it little or nothing, or cost it. It works with bought calls, puts, straddles and strangles.");
    past.replaceChildren();
    return;
  }
  if (g.breakeven_move === 0) {
    // with no break-even line there is nothing to hold past moves against
    readout.replaceChildren("At today's price this position is not losing value to time decay, so there is no daily move it has to beat.");
    past.replaceChildren();
    return;
  }
  // theta is a rate: with under a day left, a full day of it can be more than the option is still worth
  const lastDay = a.days_to_expiry < 1;
  readout.replaceChildren(
    `${lastDay ? "To keep up with time decay" : "To pay for one day of time decay"}, ${state.coin} needs to move about `,
    el("b", {}, `±${money(g.breakeven_move, a.forward)} USDT`), ` (±${pct(g.breakeven_move_pct, 2, false)}) a day. `,
    lastDay ? "Less than a day is left before expiry, so this is how fast the option is losing value now, not a full day's cost. "
      : "This assumes you re-hedge once a day with the perpetual future: bigger days pay, smaller days cost. ",
    "Trading fees on each re-hedge and funding on the perpetual are not included.");
  const coin = state.coin;
  if (historyIsStale(coin)) loadHistory(coin);
  const entry = state.history[coin];
  past.replaceChildren(...(entry ? historyView(coin, entry, a, g)
    : [el("p", { class: "sub" }, `Loading the last 30 days of ${coin} prices…`)]));
}

function historyView(coin, entry, a, g) {
  const out = [];
  const h = entry.data;
  const be = g.breakeven_move_pct;
  // The implied volatility the break-even line stands for: one option's own IV, and for a spread
  // each leg's IV weighted by its gamma. reference_iv weights the bought legs by premium, so with
  // skew it can land on the other side of realized from the line drawn below.
  const beVol = be / 100 * Math.sqrt(365);
  if (h && h.days.length) {
    const vol = (v) => `${(100 * v).toFixed(1)}%`;
    const n = h.days.length;
    const bigger = h.days.filter((d) => Math.abs(d.move_pct) > be).length;
    out.push(
      el("p", {}, "Your break-even works out to ", el("b", {}, vol(beVol)), " implied volatility. ",
        h.realized_vol_30d == null ? `There is not yet a full 30 days of ${coin} prices to compare it with.`
          : [`Over the last 30 days ${coin} realized `, el("b", {}, vol(h.realized_vol_30d)),
            h.realized_vol_7d == null ? "." : ` (last 7 days: ${vol(h.realized_vol_7d)}).`]),
      el("p", {}, "On ", el("b", {}, `${bigger} of the last ${n} days`), " it moved more than your break-even.",
        h.rms_daily_move_pct_30d == null ? "" : ` A typical day moved about ±${pct(h.rms_daily_move_pct_30d, 2, false)}.`),
      // each key wraps together with its label on narrow screens
      el("p", { class: "legend" },
        el("span", { class: "legend-item" }, el("span", { class: "key k-bigger" }), "Moved more than break-even"),
        el("span", { class: "legend-item" }, el("span", { class: "key k-smaller" }), "Moved less"),
        el("span", { class: "legend-item" }, el("span", { class: "key k-be" }), `Break-even ±${pct(be, 2, false)}`)),
      el("div", {
        class: "chart moves", role: "img",
        "aria-label": `How far ${coin} moved each day over the last ${n} days: ${bigger} moved more than your break-even of ±${pct(be, 2, false)}`,
      }, movesChart(h.days, be)),
      el("p", { class: "sub" },
        `Each bar is one day's move of the ${h.symbol} perpetual future, up or down, from one daily close to the next (UTC). `,
        "These are past moves, not a forecast.",
        h.source === "demo" ? " Demo data: a made-up price path, not real history." : ""));
  } else if (h) {
    out.push(el("p", { class: "sub" }, `No daily prices are available for ${coin} yet.`));
  }
  if (entry.error) {
    out.push(el("p", { class: "error" }, `Recent daily prices did not load: ${entry.error} `,
      el("button", {
        type: "button", class: "retry",
        onclick: (ev) => { ev.currentTarget.disabled = true; loadHistory(coin); },
      }, "Try again")));
  }
  return out;
}

// history dates are UTC days; formatting them in local time could show the day before
function utcDayLabel(iso, opts = { day: "numeric", month: "short" }) {
  return new Date(`${iso}T00:00:00Z`).toLocaleDateString("en-GB", { ...opts, timeZone: "UTC" });
}

// rounded at the tip, square at the baseline
function barPath(x, top, w, base) {
  const r = Math.min(4, w / 2, base - top);
  return `M${x},${base}V${top + r}A${r},${r} 0 0 1 ${x + r},${top}H${x + w - r}A${r},${r} 0 0 1 ${x + w},${top + r}V${base}Z`;
}

function movesChart(days, be) {
  const W = 760, H = 230, m = { l: 52, r: 64, t: 12, b: 32 };
  const sizes = days.map((d) => Math.abs(d.move_pct));
  const hi = Math.max(...sizes, be, 0.1) * 1.12;
  const base = H - m.b;
  const slot = (W - m.l - m.r) / days.length;
  const bw = Math.min(slot * 0.68, 24);
  const y = (v) => m.t + (hi - v) / hi * (base - m.t);
  const root = svg("svg", { viewBox: `0 0 ${W} ${H}` });

  for (const t of niceTicks(0, hi, 4)) {
    root.append(svg("line", { x1: m.l, x2: W - m.r, y1: y(t), y2: y(t), stroke: t === 0 ? C("chart-zero") : C("chart-grid") }));
    const lab = svg("text", { x: m.l - 8, y: y(t) + 4, "text-anchor": "end" }); lab.textContent = `${t}%`; root.append(lab);
  }

  // the quiet bars reuse the recessive grey of the zero line, so the days that beat break-even stand out
  const gain = C("gain"), quiet = C("chart-zero");
  days.forEach((d, i) => {
    const size = sizes[i];
    const x0 = m.l + i * slot;
    const day = svg("g", { class: "day" });
    const tip = svg("title");
    tip.textContent = `${utcDayLabel(d.date, { weekday: "short", day: "numeric", month: "short" })}: `
      + `${d.move_pct >= 0 ? "up" : "down"} ${size.toFixed(2)}%, closed at ${price(d.close)} USDT`;
    // the whole column answers the pointer, not just the painted bar
    day.append(tip,
      svg("rect", { class: "hit", x: x0, y: m.t, width: slot, height: base - m.t, fill: "transparent" }),
      svg("path", { d: barPath(x0 + (slot - bw) / 2, y(size), bw, base), fill: size > be ? gain : quiet }));
    root.append(day);
    // a date every week, counted back from the newest day so it is always labelled
    if ((days.length - 1 - i) % 7 === 0) {
      const lab = svg("text", { x: x0 + slot / 2, y: base + 18, "text-anchor": "middle" }); lab.textContent = utcDayLabel(d.date); root.append(lab);
    }
  });

  root.append(svg("line", { x1: m.l, x2: W - m.r, y1: y(be), y2: y(be), stroke: C("ink"), "stroke-width": 1.5, "stroke-dasharray": "5 4", "pointer-events": "none" }));
  const lab = svg("text", { x: W - m.r + 6, y: y(be) + 4 }); lab.textContent = `±${pct(be, 2, false)}`; root.append(lab);
  return root;
}

/* ---------- wiring ---------- */

for (const id of ["move", "days", "iv"]) {
  $(id).addEventListener("input", () => { updateLeverLabels(); analyze(); });
}
$("reset").addEventListener("click", () => { resetLevers(); analyze(); });
$("fees-on").checked = state.feesOn;
$("fee-rate").value = String(state.feeRate);
$("fee-rate").disabled = !state.feesOn;
$("fees-on").addEventListener("change", (ev) => {
  state.feesOn = ev.target.checked;
  $("fee-rate").disabled = !state.feesOn;
  safeSet("feesOn", String(state.feesOn));
  analyze();
});
$("fee-rate").addEventListener("change", (ev) => {
  const v = Number(ev.target.value);
  if (v >= 0 && v <= 1) { state.feeRate = v; safeSet("feeRate", String(v)); analyze(); }
  else ev.target.value = String(state.feeRate);
});
for (const r of document.querySelectorAll('input[name="price_at"]')) {
  r.addEventListener("change", () => { state.priceAt = r.value; analyze(); });
}
$("gamma-toggle").addEventListener("click", () => {
  state.gammaOn = !state.gammaOn;
  safeSet("gamma", state.gammaOn ? "on" : "off");
  renderGamma();
});

/* light and dark mode */
const darkQuery = window.matchMedia("(prefers-color-scheme: dark)");
function isDark() {
  const forced = document.documentElement.dataset.theme;
  return forced ? forced === "dark" : darkQuery.matches;
}
function renderTheme() {
  const dark = isDark();
  const btn = $("theme-toggle");
  btn.textContent = dark ? "Light mode" : "Dark mode";
  btn.setAttribute("aria-label", dark ? "Switch to light mode" : "Switch to dark mode");
  const a = state.analysis;
  if (a) { renderPayoff(a); renderHeat(a); renderSens(a); renderGamma(); }
}
$("theme-toggle").addEventListener("click", () => {
  const next = isDark() ? "light" : "dark";
  document.documentElement.dataset.theme = next;
  safeSet("theme", next);
  renderTheme();
});
darkQuery.addEventListener("change", () => { if (!document.documentElement.dataset.theme) renderTheme(); });

renderTheme();
updateLeverLabels();
renderGamma();
renderPosition();
loadCoins();
