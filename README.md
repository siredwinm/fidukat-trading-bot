# Fidukat — A Disciplined Self-Custody Trading Agent on BNB Chain

> **Fidukat** = *fidusia* (fiduciary) + *berkat* (grace). A fiduciary acts in the
> principal's best interest and holds assets in trust — never gambling with what
> isn't theirs to risk. That is the whole design: **your keys, your rules, capital
> preserved first.** The agent trades on disciplined, pre-validated rules and refuses
> to let an LLM gamble with your money.

Submission for **BNB Hack: AI Trading Agent Edition** (CoinMarketCap × Trust Wallet ×
BNB Chain) — **Track 1, Autonomous Trading Agents**.

---

## The thesis

Track 1 is scored on **live PnL with a hard drawdown gate**: exceed ~30% max
drawdown and you are disqualified, no matter how high the headline return. The motto
is literally *"most profit without blowing up."*

Most entries will ship a greedy LLM-driven agent, post a big number, hit the drawdown
gate, and get cut. **Fidukat is built for the gate, not against it:**

- **Trade decisions are 100% deterministic** — a Supertrend signal that won a 2-year
  backtest (highest expectancy *and* lowest drawdown of five candidates).
- **The LLM may only VETO, never decide.** Research shows an LLM that "picks the
  direction" tends to lose and skews bullish. Here Claude only answers *"is there a
  clear reason to skip this rule-based entry?"* using CoinMarketCap context.
- **A drawdown governor brakes hard at 22%** — an 8% buffer below the 30% gate.

Fidukat wins by restraint, not aggression.

## Validated strategy

Re-backtest of five robust signals on the competition's eligible token universe (1H,
2 years, SL 2% / TP 6%, Monte Carlo ×500). Clear winner:

| Signal | Avg Exp R | Profitable in | Monte Carlo DD | Risk of Ruin |
|---|---|---|---|---|
| **Supertrend** | **+0.108** | **20/29** | **18%** | **0.0%** |
| ATR Breakout | +0.045 | 18/29 | 51% ⚠️ | 0.2% |
| Volume Breakout | +0.015 | 15/29 | 60% ⚠️ | 7.5% |
| Donchian | +0.005 | 12/29 | 66% ⚠️ | 10% |
| VCP | −0.033 | 10/29 | 57% ⚠️ | 7.5% |

Supertrend is the only one combining the **highest edge** with the **lowest
drawdown** (18% < the 30% gate). Everything else is discarded.

**Live config:** Supertrend (period 10, mult 3), SL 2% / TP 6% / max hold 48h,
volatility-targeted sizing. **15-token basket** (Exp R ≥ +0.13, MC DD ≤ 18%):
`DOGE, UNI, DOT, COMP, AVAX, ACH, ETH, BCH, FIL, ZIL, YFI, TRX, 1INCH, AAVE, XRP`.
Execution is **spot long-only** (Trust Wallet Agent Kit supports spot swaps, not
perps): on a down-signal the agent goes flat (holds USDT).

## Architecture

```
data/cmc.py        CoinMarketCap client: Pro REST (batch quotes, Fear & Greed) +
                   Agent Hub MCP (derivatives, technical analysis, narratives).
data/candles.py    Candle store: builds its own 1H OHLC from CMC quote polling
                   (free-tier CMC has no historical OHLCV). Persists across restarts.
signals/core.py    Deterministic Supertrend — identical to the backtest engine
                   (cross-checked on 29 tokens: 0 mismatch -> live == validated).
signals/veto.py    LLM veto (Claude Haiku) + CMC context. Vetoes only; safe no-op
                   without ANTHROPIC_API_KEY.
risk/governor.py   Volatility-targeted sizing, drawdown governor (de-risk @12%,
                   HALT @22%, hysteresis), SL/TP/hold, >=1 trade/day, token allowlist.
execution/twak.py  Trust Wallet Agent Kit = the sole execution layer. Self-custody
                   local signing, autonomous mode, native x402, slippage guard.
                   Spot swaps on BSC (USDT <-> token).
integration/identity.py  ERC-8004 on-chain agent identity via the BNB AI Agent SDK.
loop/agent.py      Loop: poll quotes (build candles) -> Supertrend -> LLM veto ->
                   risk gate -> TWAK swap. All state persists across restarts.
backtest/          Validation harness (eligible.py, validate.py, fetch_data.py).
```

Full rationale and data-layer details: see **[DESIGN.md](DESIGN.md)**.
Setup and run instructions: see **[docs/SETUP.md](docs/SETUP.md)**.
Strategy methodology and results: see **[docs/STRATEGY.md](docs/STRATEGY.md)**.

## Sponsor stack (all three layers → three special prizes)

- **CoinMarketCap Agent Hub** — signal data (quotes → in-agent 1H candles) plus
  agent-native veto context (Fear & Greed, derivatives, technicals via MCP).
- **Trust Wallet Agent Kit** — the sole execution layer: self-custody signing +
  autonomous mode + native x402 + guardrails (drawdown cap, allowlist, per-trade and
  daily limits, slippage).
- **BNB AI Agent SDK** — ERC-8004 on-chain agent identity (gas-free on testnet).
- **BNB Chain** — execution venue (PancakeSwap via TWAK) + on-chain competition
  registration (`0x212c61b9b72c95d95bf29cf032f5e5635629aed5`).

## Quickstart

```bash
uv venv --python 3.11 .venv
uv pip install --python .venv/bin/python -r requirements.txt
cp .env.example .env            # add CMC_API_KEY (free tier is enough); keep TWAK_LIVE=0

.venv/bin/python loop/agent.py --poll     # test connectivity + start building candles
.venv/bin/python loop/agent.py --loop     # poll every 5 min, evaluate hourly (paper if TWAK_LIVE=0)
.venv/bin/python loop/agent.py --report   # human-readable status (equity, drawdown, win rate, trades)
```

Going live: install TWAK (`curl -fsSL https://agent-kit.trustwallet.com/install.sh |
bash`, paste your Access ID + HMAC from portal.trustwallet.com), register the agent
(`twak compete register`), set `TWAK_LIVE=1`, and run `--loop` ~1 day before the
window opens so the 1H candle history warms up. Full details in
[docs/SETUP.md](docs/SETUP.md).

## Safety & guardrails

- **Dry-run by default** — no real transactions until `TWAK_LIVE=1`.
- **Self-custody** — keys never leave the user; TWAK signs locally throughout.
- **Drawdown governor** — size scales down from 12% drawdown, halts at 22%, resumes
  only after recovery below 15%.
- **Diversification** — per-position notional capped (≤34% of equity) and at most
  4 concurrent positions, so a single-name gap can't blow the gate.
- **Allowlist + daily-trade guarantee + slippage protection** — all enforced
  deterministically in `risk/governor.py`.
- **Verified TLS** on all API calls; the LLM veto treats market data as untrusted
  input (prompt-injection guard) and can only skip a trade, never create one.
- **Trade journal** (`state/journal.jsonl`) records every open/close with PnL and
  reason; `--report` summarizes it for humans / judges.

## License

MIT — see [LICENSE](LICENSE).
