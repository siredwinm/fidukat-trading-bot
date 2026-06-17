#!/usr/bin/env python3
"""
Generate a backtestable strategy spec (Track 2 deliverable) for a token.

The spec is built from the SAME constants the live agent uses (risk/governor.py,
signals/core.py) so the research artifact never drifts from the running strategy.
Optionally enriches with live CoinMarketCap context (technicals / Fear & Greed) when
CMC_API_KEY is set — purely informational, it does not change the rules.

Run:  python track2/generate_spec.py ETH            -> writes track2/strategy_spec.json
      CMC_API_KEY=... python track2/generate_spec.py ETH   (adds a CMC context note)
"""
import os
import sys
import json

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from risk import governor as gov
from signals import core

OUT = os.path.join(os.path.dirname(__file__), "strategy_spec.json")

# Validation evidence from the 2-year re-backtest (see docs/STRATEGY.md).
REJECTED = [
    {"signal": "ATR Breakout", "exp_r": 0.045, "mc_drawdown_pct": 51, "why": "drawdown risks the 30% gate"},
    {"signal": "Volume Breakout", "exp_r": 0.015, "mc_drawdown_pct": 60, "why": "weak edge, high drawdown"},
    {"signal": "Donchian", "exp_r": 0.005, "mc_drawdown_pct": 66, "why": "high drawdown"},
    {"signal": "VCP", "exp_r": -0.033, "mc_drawdown_pct": 57, "why": "negative expectancy"},
]


def cmc_context(symbol):
    """Optional, informational CMC enrichment (Fear & Greed). Empty if no key."""
    if not os.environ.get("CMC_API_KEY"):
        return {}
    try:
        from data.cmc import CMCClient
        return {"fear_greed": CMCClient().fear_greed()}
    except Exception as e:
        return {"error": str(e)}


def build(symbol="ETH"):
    symbol = symbol.upper()
    spec = {
        "name": f"Fidukat Supertrend — {symbol}",
        "symbol": symbol,
        "timeframe": "1h",
        "direction": "long_only",
        "signal": {"type": "supertrend", "params": {"period": core.ST_PERIOD, "multiplier": core.ST_MULT}},
        "entry": {"on": "supertrend_flip_up", "side": "long"},
        "exit": {
            "stop_loss_pct": gov.SL * 100,
            "take_profit_pct": gov.TP * 100,
            "max_hold_hours": gov.MAX_HOLD_H,
            "on": ["supertrend_flip_down"],
        },
        "sizing": {
            "method": "volatility_target",
            "base_risk": gov.BASE_RISK,
            "reference_atr_pct": gov.REF_ATR * 100,
            "min_risk": gov.MIN_RISK,
            "max_risk": gov.MAX_RISK,
            "max_position_fraction": gov.MAX_POSITION_FRAC,
        },
        "risk": {
            "drawdown_derisk_start": gov.DD_DERISK_START,
            "drawdown_halt": gov.DD_HALT,
            "drawdown_resume": gov.DD_RESUME,
            "max_concurrent": gov.MAX_CONCURRENT,
            "allowlist": gov.ALLOWLIST,
            "min_trades_per_day": 1,
        },
        "costs": {"fee_per_side_pct": 0.05},
        "validation": {
            "method": "2-year 1H re-backtest, Monte Carlo x500 on the eligible universe",
            "expectancy_r": 0.108,
            "monte_carlo_drawdown_pct": 18,
            "selected_over": "ATR/Volume/Donchian/VCP breakouts",
            "rejected_signals": REJECTED,
        },
        "data_inputs": [
            "get_crypto_quotes_latest (1H candles built in-agent)",
            "get_crypto_technical_analysis (context)",
            "get_global_crypto_derivatives_metrics / fear-and-greed (context)",
        ],
    }
    ctx = cmc_context(symbol)
    if ctx:
        spec["cmc_context"] = ctx
    return spec


if __name__ == "__main__":
    sym = sys.argv[1] if len(sys.argv) > 1 else "ETH"
    spec = build(sym)
    json.dump(spec, open(OUT, "w"), indent=2)
    print(f"wrote {OUT} for {sym.upper()}"
          + (" (with CMC context)" if "cmc_context" in spec else ""))
