# Strategy & Methodology

How Fidukat chose its signal and risk model, and why it fits the competition.

## 1. Objective function

Track 1 is scored on **total return with a hard max-drawdown gate** (~30% → instant
disqualification), a minimum of one trade per day, and simulated transaction costs.
So the objective is not "maximize return" — it is **maximize return subject to never
breaching the drawdown gate.** A strategy that returns +400% but touches 35% drawdown
scores zero. This reframes everything: drawdown control is the primary optimization
target, return is secondary.

## 2. Universe

Backtesting runs on the competition's eligible BEP-20 tokens (a fixed list of 149 on
CoinMarketCap) that have enough 1H history to validate over two years. Stablecoins and
brand-new tokens are dropped from the backtest (they can still be traded live but
carry no measurable breakout edge). See `backtest/eligible.py`.

## 3. Backtest engine

`backtest/validate.py`, R-multiple accounting:

- **Fixed exits:** stop-loss 2%, take-profit 6%, max hold 48 bars (hours). Stop is
  checked before target within a bar (no look-ahead, conservative fills).
- **Fee:** 0.05% per side, charged in R units.
- **Two money-management modes:** fixed 1% risk, and volatility-targeting
  (`risk = clamp(2% · median_atr/atr, …)`).
- **Monte Carlo ×500:** trades are reshuffled to estimate risk of ruin and the
  distribution of drawdowns — the headline number is robustness, not a single curve.

## 4. Signals tested & result

Five robust, non-overfit signals were validated (1H, 2 years):

| Signal | Avg Exp R | Profitable in | Monte Carlo DD | Risk of Ruin |
|---|---|---|---|---|
| **Supertrend** | **+0.108** | **20/29** | **18%** | **0.0%** |
| ATR Breakout | +0.045 | 18/29 | 51% | 0.2% |
| Volume Breakout | +0.015 | 15/29 | 60% | 7.5% |
| Donchian | +0.005 | 12/29 | 66% | 10% |
| VCP | −0.033 | 10/29 | 57% | 7.5% |

**Supertrend wins decisively** — the highest expectancy *and* the lowest drawdown.
ATR Breakout has acceptable expectancy but a 51% Monte Carlo drawdown that would risk
the gate. The rest are weak or negative on this universe. Given the objective in §1,
the choice is unambiguous.

## 5. Token basket

From Supertrend's per-token results, the live basket keeps tokens with Exp R ≥ +0.13
and Monte Carlo DD ≤ 18%:

`DOGE, UNI, DOT, COMP, AVAX, ACH, ETH, BCH, FIL, ZIL, YFI, TRX, 1INCH, AAVE, XRP`

Fifteen tokens give enough signal flow to satisfy the ≥1-trade/day rule comfortably
while keeping drawdown low through diversification. Tokens with negative expectancy or
gate-risking drawdown (e.g. SNX, ETC, INJ) are excluded.

## 6. Risk model (the real edge)

The signal is necessary but not sufficient — the money management is where the gate is
won. `risk/governor.py` enforces:

- **Volatility-targeting** — risk per trade scales inversely with current ATR, so
  volatile setups get smaller size. In the backtest this cut Monte-Carlo drawdown
  roughly in half versus naive fixed sizing.
- **Drawdown governor** — position size scales down from 12% drawdown and **halts new
  entries at 22%** (an 8% buffer below the 30% gate); it resumes only after recovery
  below 15% (hysteresis prevents flip-flopping at the threshold).
- **Daily-trade guarantee** — if no trade has fired and it is past 20:00 UTC, take the
  best available LONG signal — but still subject to the drawdown halt. Discipline beats
  quota.

## 7. The LLM's role

The LLM does **not** generate or direct trades. It only receives an already-approved
rule-based entry plus CoinMarketCap context (Fear & Greed, global derivatives) and may
**veto** it when there is a clear elevated-risk reason. Default behavior is to allow.
This matches the backtest finding that rule-based discipline beats LLM discretion, and
it keeps the strategy reproducible and auditable.

## 8. Live vs backtest consistency

`signals/core.py` (the live Supertrend) is cross-checked against the backtest engine
on all 29 tokens with **0 mismatches** — the live signal is byte-for-byte the
validated one. Live 1H candles are built from CoinMarketCap quote polling
(see [DESIGN.md §4](../DESIGN.md)).

## 9. Known limitations

- **Long-only:** TWAK executes spot swaps, not perpetuals, so down-signals are sat out
  (flat in USDT) rather than shorted. Roughly half of Supertrend's signals are captured.
- **Warmup:** live candles must accumulate (~13 hours) before the first signal — start
  the agent ahead of the window.
- **Backtest data tier:** regenerating the backtest from CoinMarketCap requires a tier
  with historical OHLCV; the free tier serves live quotes only.
- **Past performance is not a guarantee.** Trading agents act on-chain and can incur
  real loss. The drawdown governor mitigates but does not eliminate risk.
