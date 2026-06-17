# DESIGN — Fidukat

> This document explains WHAT Fidukat is, WHY it is designed this way, and HOW each
> part works — so an AI agent or contributor can get up to speed without reading all
> the code. For the overview and quickstart, see `README.md`. For setup, see
> `docs/SETUP.md`. For the backtest methodology, see `docs/STRATEGY.md`.

---

## 1. Competition context

**BNB Hack: AI Trading Agent Edition** (CoinMarketCap × Trust Wallet × BNB Chain),
**Track 1 — Autonomous Trading Agents** ($24k, 5 winners). Live trading 22–28 June
2026 on BNB Chain, scored on **live PnL**.

The rules shape the ENTIRE design:

| Rule | Design implication |
|---|---|
| Ranked by total return, BUT **drawdown > ~30% = disqualified** | Drawdown is enemy #1. The governor brakes well before 30%. |
| **≥1 trade/day** (7 over the week) | Daily-trade guarantee in the governor. |
| Only **149 specific BEP-20 tokens** count | Token allowlist; backtested on this universe. |
| Must hold in-scope balance at start; return measured hourly | Capital stays deployed; hourly loop. |
| Simulated transaction costs | Backtest uses a 0.05% fee. |
| Agent registered **on-chain** before 22 June | `twak compete register` to `0x212c…aed5`. |

**Bottom line:** *"most profit without blowing up."* The winner is steady profit
without blowing up — not the most aggressive bot. Most entrants will ship a greedy
agent that trips the drawdown gate. Fidukat wins through **discipline**.

---

## 2. Core thesis & philosophy

**Trade decisions are 100% DETERMINISTIC; the LLM may only VETO.**

A 2-year backtest shows that a simple rule-based strategy plus strict drawdown
management beats an "LLM decides the direction" agent (which tends to lose and skews
bullish). Therefore:

- **Entry signal** → deterministic Supertrend (validated).
- **Sizing, exits, drawdown** → deterministic governor.
- **LLM (Claude)** → only answers *"is there a strong reason to SKIP this entry?"*
  using CMC market context. Default: do not veto. The LLM is an extra brake, not a
  throttle.

This is also the **differentiating narrative** for the judges: an agent that
deliberately refuses to let an LLM gamble, backed by quantitative evidence. The name
**Fidukat** (*fidusia* + *berkat*, fiduciary + grace) captures it: act in the
principal's interest, preserve capital, hold in trust.

---

## 3. Strategy (re-backtest result)

Re-backtest at 1H over 2 years on the eligible token universe (5 robust signals × MM
× Monte Carlo ×500). Clear winner: **Supertrend** — Exp R +0.108, profitable on 20/29
tokens, Monte Carlo drawdown 18% (under the gate), risk of ruin 0%. The others are
discarded for weak edge or drawdown that breaches the gate. See `docs/STRATEGY.md`.

**Live config:** Supertrend (period 10, mult 3), **SL 2% / TP 6% / max hold 48h**,
**volatility-targeted** sizing. **15-token basket** (Exp R ≥ +0.13, MC DD ≤ 18%):
`DOGE, UNI, DOT, COMP, AVAX, ACH, ETH, BCH, FIL, ZIL, YFI, TRX, 1INCH, AAVE, XRP`.

**Execution = SPOT LONG-ONLY.** TWAK supports spot swaps only (no perps/futures). On
a down-signal the agent goes flat (holds USDT). This also keeps self-custody clean.

---

## 4. Data reality (important)

The CoinMarketCap **free tier** does NOT serve OHLCV/candlesticks (gated). What it
does serve: real-time batch quotes, Fear & Greed, and the Agent Hub MCP (technical
analysis, derivatives, narratives). Since Supertrend needs 1H candles:

> **The agent builds its own 1H candles** from CMC `quotes/latest` polling
> (`data/candles.py`). One batch call (15 tokens) every ~5 minutes forms OHLC →
> ~2,000 credits/week (well under the 15,000/month free quota). 100% CoinMarketCap
> data, zero third-party exchanges.

**Warmup:** Supertrend needs ~13 bars, so run the agent ~1 day before the window to
accumulate candles. (DEV can use `SEED_CACHE=1` to warm up from local cache.)

---

## 5. Module architecture

```
data/cmc.py        CMC client: Pro REST (free batch quotes, Fear & Greed) + Agent Hub
                   MCP (get_global_crypto_derivatives_metrics,
                   get_crypto_technical_analysis [needs numeric id], trending_crypto_narratives).
                   Without a key -> reads local cache (dev / paper offline).
data/candles.py    CandleStore: poll quotes -> 1H OHLC, persists across restarts.
signals/core.py    Deterministic Supertrend. Identical to the backtest engine
                   (cross-checked on 29 tokens: 0 mismatch -> live == validated).
signals/veto.py    LLM veto: Claude (Haiku) + CMC context (F&G, derivatives).
                   Vetoes only; default allow; safe fallback without a key.
risk/governor.py   Vol-targeted sizing, drawdown governor (de-risk @12%, HALT @22% <
                   30% gate, hysteresis), SL/TP/hold, >=1 trade/day, allowlist.
                   Deterministic, auditable, serializable state.
execution/twak.py  Trust Wallet Agent Kit = the sole execution layer.
                   Spot swap (USDT<->TOKEN) on BSC, wallet, price, x402, register.
                   Self-custody local signing; dry-run by default.
integration/identity.py  ERC-8004 on-chain agent identity via the BNB AI Agent SDK.
loop/agent.py      Loop: poll quotes (build candles) -> Supertrend -> LLM veto ->
                   risk gate -> TWAK swap. Persists positions + governor + candles.
backtest/          Validation harness: eligible.py, validate.py (source-agnostic,
                   reads cache), fetch_data.py (CMC OHLCV; needs a paid tier).
```

**One cycle (`run_once`):**
1. poll CMC quotes → update candle store
2. mark-to-market equity → update drawdown governor
3. manage open positions: SL / TP / timeout / Supertrend flips down → swap out
4. find entries: Supertrend LONG → LLM veto (may reject) → governor can_open + sizing → swap in
5. daily-trade guarantee if needed (still subject to the drawdown HALT)
6. persist all state

---

## 6. Sponsor stack mapping (three layers → three special prizes)

- **CoinMarketCap Agent Hub** — signal data (quotes → 1H candles) + veto context
  (Fear & Greed, derivatives, technicals via MCP). → *Best Use of Agent Hub*.
- **Trust Wallet Agent Kit** — the sole execution layer: self-custody local signing +
  autonomous mode + native x402 + guardrails (drawdown cap, allowlist, per-trade and
  daily limits, slippage). → *Best Use of TWAK*.
- **BNB AI Agent SDK** — NOT a trading layer; used to register the agent's on-chain
  identity (ERC-8004), gas-free on testnet. → *Best Use of BNB SDK*.
- **BNB Chain** — execution venue (PancakeSwap via TWAK on BSC) + registration contract.

---

## 7. Guardrails (deterministic, in `risk/governor.py`)

- **Drawdown governor**: size scales down linearly from 12%→22% drawdown, **HALT** at
  22% (8% buffer below the 30% gate), resumes only after recovery < 15% (hysteresis).
- **Vol-targeting**: `risk = clamp(2% · ref_atr/atr, 0.4%, 5%)` — volatile setups get
  smaller size.
- **Allowlist**: only the 15 validated tokens.
- **Daily-trade guarantee**: take the best LONG signal if no trade yet and past 20:00
  UTC — still subject to the drawdown HALT (discipline beats quota).

---

## 8. Status & remaining work

**Done & tested (paper):** backtest, all modules, candle store from real CMC quotes,
veto (real CMC inputs verified; only needs `ANTHROPIC_API_KEY` for the Claude call),
TWAK adapter with the real CLI syntax (dry-run), ERC-8004 identity wrapper.

**Remaining:** (1) set up TWAK credentials + test one testnet swap; (2) register
ERC-8004 identity (BNB SDK) for the special prize; (3) warm up live + register for the
competition before 22 June; (4) demo (show the self-custody + autonomous-signing loop
with a BSC tx hash).

**Repo note:** the public repo is 100% CoinMarketCap (zero references to any other
exchange). The backtest is private research; the third-party data fetcher lives in a
gitignored file (`*_private.py`). The full edge research is not included.
