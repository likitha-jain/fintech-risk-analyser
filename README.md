# 🛡️ FinShield

**A multi-agent AI system that detects stablecoin depegs early, decides whether to flee or exploit them, and simulates every action before anything moves.**

> *"A risk-aware agent team that tells your treasury when to run from a depeg, when to profit from it, and proves every move in simulation first."*

![Dashboard](screenshots/1_dashboard.png)

## The Problem

Stablecoins hold over $200B and anchor DeFi, yet they depeg: UST collapsed in 2022 and USDC fell to about $0.87 in March 2023. Treasuries had minutes to hours to react, and most were late. Today's tooling has three gaps:

1. **Fragmented signals.** Peg price, pool imbalance, oracle lag and social chatter live in separate dashboards.
2. **Slow, manual response.** Alerts without a decision framework produce panic, not action.
3. **Blind dip-buying.** Bots can't tell a *temporary* dislocation (arbitrage opportunity) from a *terminal* one (a trap).

## The Solution

Specialized agents each own one part of the decision, and a Risk Governor holds veto power over every action.

| Agent | Role |
|---|---|
| **Monitor** | Peg deviation and price velocity |
| **Liquidity** | Pool imbalance and depth |
| **Sentiment/News** | Social and governance risk signals |
| **Risk Governor** | Fuses signals into a depeg probability, classifies **NORMAL / TEMPORARY / TERMINAL**, vetoes unsafe trades |
| **Scanner/Strategist** | Picks trade route and size in EXPLOIT mode, or the exit in FLEE mode |
| **Simulator** | Computes net result after slippage and gas |
| **Executor** | Runs approved actions (simulated), with a human sign-off gate for treasury moves |

```
Monitor ─┐
Liquidity ├─► Risk Governor ─► NORMAL    → keep watching
Sentiment ┘        │         ► TEMPORARY → EXPLOIT: Strategist → Simulator → Governor recheck → auto-execute (within limits)
                   │         ► TERMINAL  → FLEE: Simulator → Governor recheck → human sign-off → execute
                   ▼
             Audit log + dashboard
```

## Features

- **Scenario replay** of USDC (Mar 2023), UST (May 2022) and a calm control, with a time slider
- **Live mode** using 24h CoinGecko price history
- **Depeg probability** with gauge and per-agent score chart
- **Temporary vs terminal classification** driving FLEE or EXPLOIT mode
- **Governor veto checks**: feed quality, pool depth, classification, simulated profit, size limits
- **Slippage and gas simulation**, plus hindsight PnL and capital preserved vs holding
- **Human approval gate** for FLEE; auto-execution limits for EXPLOIT
- **Backtest tab**: detection lead time vs a baseline threshold alert, and false-alarm rate
- **Audit trail** of each decision with CSV export

| Agent feed & Governor | Backtest |
|---|---|
| ![Feed](screenshots/2_agent_feed.png) | ![Backtest](screenshots/3_backtest.png) |

## Quick Start

```bash
git clone <your-repo-url> && cd finshield
python -m venv .venv
source .venv/bin/activate        # Windows: .\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
streamlit run app.py
```

Opens at `http://localhost:8501`.

## Demo Walkthrough

1. **USDC Mar 2023**, slider at step 60: system reads TEMPORARY, EXPLOIT checks pass, simulated PnL shown.
2. **UST May 2022**: system reads TERMINAL and switches to FLEE; tick the sign-off box and execute.
3. **Backtest tab**: lead time over the baseline alert and calm-market false alarms.
4. Set **Feed quality to Stale**: the Governor blocks action.

## Backtest Results (synthetic scenarios, default thresholds)

| Scenario | Lead time vs 3% baseline alert | Classified |
|---|---|---|
| USDC Mar 2023 | 30 min earlier | TEMPORARY |
| UST May 2022 | 130 min earlier | TERMINAL |
| Calm control | n/a, 0% false alarms | NORMAL |

## Tech Stack

Python, Streamlit, Plotly, Pandas, NumPy, CoinGecko API.

## Project Structure

```
app.py                  Streamlit app: agents, Governor, simulator, UI
requirements.txt
.streamlit/config.toml  Dark theme
screenshots/            GUI screenshots
data/                   Audit log (created at runtime, git-ignored)
```

## Limitations

- Scenarios are **illustrative reconstructions, not exact historical data**; backtest numbers show the pipeline working, not real-world performance.
- Pool imbalance and sentiment are simulated or set manually; no DEX, oracle or news integrations yet.
- Execution is **simulated only**: no wallet connection or on-chain actions.
- Thresholds and weights are demo defaults, not calibrated standards.

## Roadmap

- Real pool data (Curve, Uniswap via The Graph) and DefiLlama peg history
- Oracle feeds (Chainlink, Pyth) and oracle-lag detection
- LLM-powered Sentiment agent and incident summaries
- Foundry/Anvil mainnet-fork simulation and private-relay execution
- Calibrate the Risk Governor on real depeg events

## Disclaimer

Educational prototype, not financial advice. Real MEV and arbitrage markets are highly competitive, so the intended edge is decision quality and risk management, not execution speed.
