---
name: fidukat-strategy-skill
description: >
  Turn CoinMarketCap market data into a backtestable, drawdown-aware trading-strategy
  spec for a crypto token. Use when the user wants a quant-style strategy spec (entry,
  exit, sizing, risk gate) grounded in validation — not a live trade. Produces a JSON
  spec a backtester can run, plus the validation evidence and a rejected-signals log.
---

# Fidukat Strategy Skill — CMC market data → backtestable strategy spec

This is the **Track 2** deliverable for the BNB Hack (CoinMarketCap track): a CMC Skill
that generates a *backtestable strategy spec*, not a live agent. It encodes the same
methodology that powers the [Fidukat](../README.md) Track-1 agent.

## What it does

Given a token (and, optionally, live CMC context), it emits a `strategy_spec` (see
`strategy_spec.schema.json`) describing a fully specified, reproducible strategy:

- **signal** — Supertrend (period 10, multiplier 3) on 1H candles
- **entry** — long on a Supertrend up-flip
- **exit** — stop-loss 2%, take-profit 6%, max hold 48h, or trend flip-down
- **sizing** — volatility-targeting (risk ∝ reference_atr / current_atr), capped
- **risk gate** — drawdown governor: de-risk from 12%, halt at 22% (below a 30% gate)
- **universe** — the eligible BEP-20 basket
- **costs** — 0.05% per side

## Why this spec (the quant research)

The methodology is selection, not invention. Five robust trend/breakout signals were
re-validated over **2 years of 1H data** on the eligible universe (Monte Carlo ×500):

| Signal | Avg Exp R | Profitable | Monte-Carlo DD | Verdict |
|---|---|---|---|---|
| **Supertrend** | **+0.108** | 20/29 | **18%** | **selected** |
| ATR Breakout | +0.045 | 18/29 | 51% | rejected — drawdown risks the gate |
| Volume Breakout | +0.015 | 15/29 | 60% | rejected — weak edge, high DD |
| Donchian | +0.005 | 12/29 | 66% | rejected — high DD |
| VCP | −0.033 | 10/29 | 57% | rejected — negative expectancy |

The differentiator is **honesty + drawdown-awareness**: the spec ships the *rejected*
signals and *why*, and is optimized for "most profit without blowing up," not the
highest headline return. Most strategy skills chase return; this one is built around the
drawdown gate that actually decides the competition.

## How an agent uses it

1. Read the token and (optionally) CMC technicals/derivatives/Fear&Greed for context.
2. Emit a `strategy_spec` JSON for that token using the rules above.
3. Hand the spec to any backtester (the spec is engine-agnostic; field meanings are in
   `strategy_spec.schema.json`).

Generate an example locally:

```bash
python track2/generate_spec.py ETH            # writes track2/strategy_spec.json
CMC_API_KEY=... python track2/generate_spec.py ETH   # enriches with live CMC context
```

A ready-made example output is in `strategy_spec.json`.

## Inputs (CoinMarketCap Agent Hub)

- `get_crypto_quotes_latest` — price/volume (and the 1H candle series the live agent builds)
- `get_crypto_technical_analysis` — RSI/MACD/EMA context (optional enrichment / regime note)
- `get_global_crypto_derivatives_metrics`, Fear & Greed — macro context note

## Limitations

Long-only, spot, trend-following. Past performance is not a guarantee. The spec is a
research artifact; live execution (Track 1) adds self-custody signing via Trust Wallet
Agent Kit and an LLM veto. See [docs/STRATEGY.md](../docs/STRATEGY.md).
