# Track 2 — CMC Strategy Skill

This folder is the **Track 2** submission (CoinMarketCap track): a CMC Skill that turns
market data into a **backtestable strategy spec**, not a live agent.

## Contents

| File | What it is |
|---|---|
| `SKILL.md` | The Skill definition — methodology, CMC inputs, and the quant research behind it |
| `strategy_spec.schema.json` | JSON Schema for the strategy spec (engine-agnostic, backtestable) |
| `generate_spec.py` | Generates a spec for a token from the live strategy's own constants (+ optional CMC enrichment) |
| `strategy_spec.json` | A ready-made example spec (ETH) |

## Run

```bash
python track2/generate_spec.py ETH                 # -> track2/strategy_spec.json
CMC_API_KEY=... python track2/generate_spec.py ETH # adds a live CMC context note
```

## Why it's different

Most strategy skills chase the highest return. This one is built around the metric that
actually decides a live-PnL competition: **drawdown**. It ships:

- a single, **validated** strategy (Supertrend, 2-year re-backtest: Exp R +0.108, Monte-Carlo
  drawdown 18%), and
- the **rejected-signals log** (ATR/Volume/Donchian/VCP — with the drawdown/expectancy
  reason each was dropped).

That honesty — selecting on robustness and publishing what *didn't* work — is the quant
research the track asks for. The same methodology powers the Track-1 agent
([../README.md](../README.md)); see [../docs/STRATEGY.md](../docs/STRATEGY.md) for the full
methodology.
