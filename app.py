import json
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd
import plotly.graph_objects as go
import streamlit as st

BASE = Path(__file__).parent
DATA = BASE / "data"
AUDIT = DATA / "audit_log.jsonl"
DATA.mkdir(exist_ok=True)
N, STEP_MIN = 120, 5

st.set_page_config(page_title="FinShield | Multi-Agent Depeg System", page_icon="🛡️", layout="wide")
st.markdown("""<style>
.stApp{background:#0b0b12;color:#f4edf3}[data-testid="stSidebar"]{background:#17101b}
h1,h2,h3{color:#f5c5dc}
div[data-testid="stMetric"]{background:#1b1420;border:1px solid #51304a;padding:14px;border-radius:12px}
</style>""", unsafe_allow_html=True)

COINS = {"USDC": "usd-coin", "USDT": "tether", "DAI": "dai", "USDE": "ethena-usde"}
BACKING = {"Fully reserved": 0.10, "Fiat reserves, partial bank exposure": 0.35,
           "Crypto-collateralized": 0.30, "Algorithmic / endogenous": 0.90}
SCENARIOS = {
    "USDC Mar 2023 (temporary)": dict(
        px=([0, 38, 45, 60, 75, 95, 119], [1, .999, .97, .88, .93, .99, .998]),
        imb=([0, 20, 40, 60, 90, 119], [.5, .55, .72, .85, .6, .52]),
        sent=([0, 20, 40, 60, 90, 119], [.1, -.2, -.5, -.8, -.2, .1]),
        backing="Fiat reserves, partial bank exposure", replay=60),
    "UST May 2022 (terminal)": dict(
        px=([0, 30, 45, 60, 75, 90, 105, 119], [1, .995, .985, .95, .7, .4, .2, .12]),
        imb=([0, 20, 45, 60, 75, 90, 119], [.5, .55, .7, .85, .95, .98, .99]),
        sent=([0, 20, 45, 60, 75, 90, 119], [.1, -.2, -.5, -.8, -.9, -.95, -.95]),
        backing="Algorithmic / endogenous", replay=60),
    "Calm market (control)": dict(
        px=([0, 119], [1, 1]), imb=([0, 119], [.5, .5]), sent=([0, 119], [.1, .1]),
        backing="Fully reserved", replay=N - 1),
}

def make_scenario(name):
    s, rng, x = SCENARIOS[name], np.random.default_rng(7), np.arange(N)
    noise = 0.0004 if "Calm" in name else 0.0008
    return pd.DataFrame({
        "price": np.interp(x, *s["px"]) + rng.normal(0, noise, N),
        "imbalance": np.clip(np.interp(x, *s["imb"]) + rng.normal(0, .015, N), 0, 1),
        "sentiment": np.clip(np.interp(x, *s["sent"]) + rng.normal(0, .05, N), -1, 1)})

@st.cache_data(ttl=120)
def fetch_live(cid):
    import requests
    r = requests.get(f"https://api.coingecko.com/api/v3/coins/{cid}/market_chart",
                     params={"vs_currency": "usd", "days": 1}, timeout=8)
    r.raise_for_status()
    return [p[1] for p in r.json()["prices"]][-N:]

# ---------------- Agents ----------------
def run_agents(df, target, backing_risk, feed):
    """Monitor, Liquidity, Sentiment agents -> Risk Governor probability per step."""
    p = df["price"].to_numpy()
    dev = np.abs(p - target) / target * 100
    prev = np.roll(p, 6); prev[:6] = p[0]
    vel = np.clip((prev - p) / target * 100, 0, None)
    out = df.copy()
    out["deviation"] = dev
    out["monitor"] = np.clip(dev / 4 + vel / 3, 0, 1)
    out["liquidity"] = np.clip(np.abs(df["imbalance"] - .5) * 2, 0, 1)
    out["sentiment_risk"] = np.clip(-df["sentiment"], 0, 1)
    prob = .35 * out["monitor"] + .25 * out["liquidity"] + .20 * out["sentiment_risk"] + .20 * backing_risk
    out["prob"] = np.clip(prob * (0.7 if feed == "Stale" else 1.0), 0, 1)
    return out

def classify(prob, thr, backing_risk):
    if prob < thr: return "NORMAL"
    return "TERMINAL" if backing_risk >= 0.6 else "TEMPORARY"

# ---------------- Sidebar ----------------
with st.sidebar:
    st.header("Data & scenario")
    mode = st.radio("Data source", ["Scenario replay (safe demo)", "Live CoinGecko (24h)"])
    scn = st.selectbox("Scenario", list(SCENARIOS)) if mode.startswith("Scenario") else None
    coin = st.selectbox("Stablecoin", list(COINS))
    target = st.number_input("Target peg ($)", 0.01, value=1.0, step=0.01, format="%.4f")
    feed = st.selectbox("Feed quality", ["Confirmed", "Disputed", "Stale"])
    default_b = list(BACKING).index(SCENARIOS[scn]["backing"]) if scn else 0
    backing = st.selectbox("Reserve backing", list(BACKING), index=default_b, key=f"b_{scn}")
    backing_risk = BACKING[backing]
    if scn is None:
        live_imb = st.slider("Pool imbalance (0.5 = balanced)", 0.0, 1.0, 0.5, 0.01)
        live_sent = st.slider("Sentiment (-1 bearish, +1 bullish)", -1.0, 1.0, 0.0, 0.05)
    st.header("Policy & simulation")
    thr = st.slider("Alert threshold (depeg probability)", 0.2, 0.8, 0.35, 0.05)
    base_thr = st.slider("Baseline alert: deviation %", 0.5, 10.0, 3.0, 0.5)
    treasury = st.number_input("Treasury in stablecoin ($M)", 0.1, 1000.0, 10.0) * 1e6
    trade = st.number_input("Arbitrage trade size ($)", 1000.0, 5e7, 250000.0, step=10000.0)
    depth = st.number_input("Pool depth ($M)", 0.5, 2000.0, 50.0) * 1e6
    gas = st.number_input("Gas + relay cost ($)", 0.0, 5000.0, 60.0)
    max_auto = st.number_input("Max auto-execute size ($)", 1000.0, 5e7, 500000.0, step=10000.0)

# ---------------- Data ----------------
source = "Scenario replay"
if scn:
    df = make_scenario(scn)
else:
    try:
        series = fetch_live(COINS[coin]); source = "CoinGecko API"
    except Exception as e:
        st.warning(f"Live data unavailable ({type(e).__name__}); using calm simulated series.")
        series = make_scenario("Calm market (control)")["price"].tolist(); source = "Simulation fallback"
    df = pd.DataFrame({"price": series, "imbalance": live_imb, "sentiment": live_sent})
n = len(df)
df["time"] = pd.date_range(end=pd.Timestamp.now().floor("5min"), periods=n, freq=f"{STEP_MIN}min")
res = run_agents(df, target, backing_risk, feed)

st.title("🛡️ FinShield")
st.subheader("Multi-Agent Depeg Early-Warning, Response & Arbitrage System")
st.caption(f"Source: {source} • scenarios are illustrative reconstructions, not exact market data • "
           "all execution is simulated, no wallet or on-chain actions")

idx = st.slider("Replay position (step)", 6, n - 1,
                SCENARIOS[scn]["replay"] if scn else n - 1, key=f"r_{scn}",
                help="Move through time to see how agents react as the event unfolds.")
row = res.iloc[idx]
price, dev, prob = float(row["price"]), float(row["deviation"]), float(row["prob"])
state = classify(prob, thr, backing_risk)
alert_idx = next((i for i in range(n) if res["prob"].iat[i] >= thr), None)
base_idx = next((i for i in range(n) if res["deviation"].iat[i] >= base_thr), None)
final_price = float(res["price"].iat[-1]) if scn else price

# ---------------- Governor ----------------
slip_x = trade / (2 * depth)
exec_px = price * (1 + slip_x)
exploit_net = trade / exec_px * target - trade - gas
flee_slip = treasury / (2 * depth)
flee_value = treasury * price * (1 - flee_slip) - gas
hold_value = treasury * final_price
checks = [
    ("Feed confirmed", feed == "Confirmed", f"Feed is {feed.lower()}"),
    ("Depth ≥ 5× trade", depth >= 5 * trade, f"Depth/trade = {depth / trade:.0f}×"),
    ("Not terminal", state != "TERMINAL", f"Classification {state}"),
    ("Net profit > 0 in sim", exploit_net > 0, f"Net ${exploit_net:,.0f}"),
    ("Within auto limit", trade <= max_auto, f"Size ${trade:,.0f} vs limit ${max_auto:,.0f}"),
]
if state == "TEMPORARY":
    mode_name = "EXPLOIT"; approved = all(c[1] for c in checks)
elif state == "TERMINAL":
    mode_name = "FLEE"; approved = feed != "Stale" and flee_value > 0
else:
    mode_name = "WATCH"; approved = False

a, b, c, d = st.columns(4)
a.metric("Market price", f"${price:.4f}", f"{price - target:+.4f}")
b.metric("Deviation", f"{dev:.2f}%")
c.metric("Depeg probability", f"{prob * 100:.0f}%")
d.metric("Classification", state)
if state == "TERMINAL": st.error("Terminal depeg suspected: FLEE mode. Treasury moves need human sign-off.")
elif state == "TEMPORARY": st.warning("Temporary dislocation: EXPLOIT mode evaluated, Governor can veto.")
elif feed != "Confirmed": st.info("Data quality issue. Treat as unconfirmed until sources agree.")
else: st.success("No alert under current policy. Monitoring continues.")

tab1, tab2, tab3, tab4 = st.tabs(["Dashboard", "Agent feed & decision", "Backtest", "Audit log"])

with tab1:
    fig = go.Figure(go.Scatter(x=res["time"], y=res["price"], mode="lines", name=coin,
                               line=dict(color="#f2a8cb", width=3)))
    fig.add_hline(y=target, line_dash="dash", line_color="#71dbc7", annotation_text="Target peg")
    for i, lab, col in [(alert_idx, "FinShield alert", "#71dbc7"), (base_idx, "Baseline alert", "#f5a623")]:
        if i is not None:
            fig.add_vline(x=res["time"].iat[i], line_color=col, line_dash="dot", annotation_text=lab,
                          annotation_position="top left" if lab.startswith("FinShield") else "top right")
    fig.add_vline(x=res["time"].iat[idx], line_color="#ffffff", opacity=0.5)
    fig.update_layout(template="plotly_dark", height=340, margin=dict(l=10, r=10, t=25, b=10),
                      paper_bgcolor="#0b0b12", plot_bgcolor="#0b0b12", yaxis_title="Price (USD)", showlegend=False)
    st.plotly_chart(fig, width="stretch")
    l, r = st.columns([2, 1])
    with l:
        f2 = go.Figure()
        for k, col in [("monitor", "#f2a8cb"), ("liquidity", "#71dbc7"), ("sentiment_risk", "#f5a623"), ("prob", "#ffffff")]:
            f2.add_trace(go.Scatter(x=res["time"], y=res[k], name=k, line=dict(color=col, width=3 if k == "prob" else 1.5)))
        f2.add_hline(y=thr, line_dash="dash", line_color="#ff5c8a")
        f2.update_layout(template="plotly_dark", height=280, margin=dict(l=10, r=10, t=25, b=10),
                         paper_bgcolor="#0b0b12", plot_bgcolor="#0b0b12", yaxis_title="Agent risk score (0-1)")
        st.plotly_chart(f2, width="stretch")
    with r:
        g = go.Figure(go.Indicator(mode="gauge+number", value=prob * 100, number={"suffix": "%"},
            title={"text": "Depeg probability"},
            gauge={"axis": {"range": [0, 100]}, "bar": {"color": "#f2a8cb"},
                   "threshold": {"line": {"color": "#ff5c8a", "width": 4}, "value": thr * 100}}))
        g.update_layout(height=280, margin=dict(l=20, r=20, t=40, b=10), paper_bgcolor="#0b0b12", font_color="#f4edf3")
        st.plotly_chart(g, width="stretch")

with tab2:
    st.markdown("### Agent activity feed")
    feed_rows = [
        ("Monitor", f"Price ${price:.4f}, deviation {dev:.2f}%, score {row['monitor']:.2f}"),
        ("Liquidity", f"Pool imbalance {row['imbalance']:.2f} (0.5 balanced), score {row['liquidity']:.2f}"),
        ("Sentiment/News", f"Sentiment {row['sentiment']:+.2f}, risk {row['sentiment_risk']:.2f}"),
        ("Risk Governor", f"Backing '{backing}'. P(depeg) {prob:.0%} → {state}. Mode: {mode_name}"),
    ]
    if state == "TEMPORARY":
        feed_rows += [("Scanner/Strategist", f"Buy ${trade:,.0f} at ${exec_px:.4f} (slippage {slip_x * 100:.3f}%), target exit ${target:.2f}"),
                      ("Simulator", f"Net after slippage and gas: ${exploit_net:,.0f}")]
    elif state == "TERMINAL":
        feed_rows += [("Strategist", f"Swap ${treasury / 1e6:.1f}M treasury to safer asset"),
                      ("Simulator", f"Value after slippage+gas ${flee_value:,.0f}")]
    for ag, msg in feed_rows: st.markdown(f"**{ag}**: " + msg.replace("$", "\\$"))

    st.markdown("### Governor checks")
    if state == "TERMINAL":
        st.dataframe(pd.DataFrame([{"Check": "Feed not stale", "Pass": feed != "Stale", "Detail": feed},
                                   {"Check": "Flee value positive", "Pass": flee_value > 0, "Detail": f"${flee_value:,.0f}"}]),
                     hide_index=True, width="stretch")
    elif state == "TEMPORARY":
        st.dataframe(pd.DataFrame([{"Check": n_, "Pass": "✅" if ok else "❌ VETO", "Detail": dt} for n_, ok, dt in checks]),
                     hide_index=True, width="stretch")
    else:
        st.caption("Governor idle: no alert.")

    if mode_name != "WATCH":
        m1, m2 = st.columns(2)
        if mode_name == "FLEE":
            m1.metric("Capital preserved vs holding (hindsight)" if scn else "Cost of exit", f"${flee_value - hold_value:,.0f}")
            m2.metric("Hold value at end", f"${hold_value:,.0f}")
        else:
            m1.metric("Simulated net PnL", f"${exploit_net:,.0f}")
            if scn:
                m2.metric("Hindsight PnL if held to end", f"${trade / exec_px * final_price - trade - gas:,.0f}")

    st.markdown("### Executor (simulated)")
    ok_sign = True
    if mode_name == "FLEE":
        ok_sign = st.checkbox("Multisig human sign-off granted")
    note = st.text_input("Operator note", placeholder="e.g. Reviewed pool liquidity")
    can_run = approved and ok_sign
    if mode_name == "WATCH": st.caption("Nothing to execute.")
    elif not approved: st.error("Governor vetoed. Execution blocked.")
    if st.button("Execute on forked chain (simulated)", type="primary", disabled=not can_run):
        rec = {"timestamp_utc": datetime.now(timezone.utc).isoformat(), "asset": coin, "source": source,
               "scenario": scn, "step": int(idx), "price": round(price, 6), "deviation_pct": round(dev, 4),
               "depeg_probability": round(prob, 4), "classification": state, "mode": mode_name,
               "feed": feed, "backing": backing,
               "sim_result_usd": round(exploit_net if mode_name == "EXPLOIT" else flee_value - hold_value, 2),
               "operator_note": note, "action": f"simulated_{mode_name.lower()}_no_onchain_action"}
        with AUDIT.open("a", encoding="utf-8") as f: f.write(json.dumps(rec) + "\n")
        st.success("Simulated execution logged.")

with tab3:
    st.markdown("### Backtest across all scenarios")
    rows = []
    for name, spec in SCENARIOS.items():
        s = run_agents(make_scenario(name), target, BACKING[spec["backing"]], "Confirmed")
        ai = next((i for i in range(N) if s["prob"].iat[i] >= thr), None)
        bi = next((i for i in range(N) if s["deviation"].iat[i] >= base_thr), None)
        lead = (bi - ai) * STEP_MIN if ai is not None and bi is not None else None
        rows.append({"Scenario": name, "Truth": "terminal" if "terminal" in name else "temporary" if "temporary" in name else "none",
                     "FinShield alert (min)": None if ai is None else ai * STEP_MIN,
                     "Baseline alert (min)": None if bi is None else bi * STEP_MIN,
                     "Lead time (min)": lead,
                     "Classified": classify(float(s["prob"].iloc[min(bi, N - 1)] if bi else s["prob"].max()), thr, BACKING[spec["backing"]]),
                     "Alert steps": int((s["prob"] >= thr).sum()),
                     "Alert rate": f"{(s['prob'] >= thr).mean() * 100:.0f}%"})
    bt = pd.DataFrame(rows)
    st.dataframe(bt, hide_index=True, width="stretch")
    calm = bt[bt["Scenario"].str.contains("Calm")].iloc[0]
    st.metric("False-alarm rate (calm control)", calm["Alert rate"])
    st.caption("Truth labels come from the scenario definitions. Lead time = baseline alert minus FinShield alert; "
               "None means that alert never fired. Tune the thresholds in the sidebar.")

with tab4:
    recs = []
    if AUDIT.exists():
        for line in AUDIT.read_text(encoding="utf-8").splitlines()[-200:]:
            try: recs.append(json.loads(line))
            except json.JSONDecodeError: pass
    if recs:
        adf = pd.DataFrame(recs[::-1])
        st.dataframe(adf, hide_index=True, width="stretch")
        st.download_button("Download audit log (CSV)", adf.to_csv(index=False), "audit_log.csv", "text/csv")
    else:
        st.info("No records yet. Execute a simulated action from the Agent feed tab.")
st.caption("Prototype only. Simulated execution; not financial advice.")
