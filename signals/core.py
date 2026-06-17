#!/usr/bin/env python3
"""
Deterministic entry signal for the BNB Hack agent.

Only ONE signal is used live: **Supertrend** — winner of the re-backtest on the
universe of eligible BSC tokens (ExpR +0.108, profit 20/29, Monte Carlo DD 18%,
RoR 0%). The other signals (Volume/ATR/Donchian/VCP) are deliberately NOT used:
their edge is weak/negative or their drawdown breaches the competition gate (~30%).

The logic here is EXACTLY THE SAME as backtest/validate.py:sig_supertrend so that
live behavior == the already-validated behavior (no implementation drift).

Candle input: list [ts_ms, open, high, low, close, vol] in ascending order
(oldest -> newest); the last candle is the 1H bar that has JUST closed. Output:
the signal snapshot for that bar.
"""
from dataclasses import dataclass

ST_PERIOD = 10   # Supertrend band window
ST_MULT = 3      # band multiplier
ATR_PERIOD = 14  # for atr_pct (used for vol-targeting in the risk governor)


@dataclass
class SignalSnapshot:
    signal: int        # +1 long entry, -1 short entry, 0 no new entry
    direction: int     # current Supertrend direction (+1/-1), for LLM context/veto
    close: float       # close price of the last bar
    atr_pct: float     # ATR(14)/close — used for vol-target sizing
    bars: int          # number of valid candles processed


def _rolling_mean(xs, p):
    """Simple rolling mean; None for index < p-1 (matching pandas .rolling)."""
    out = [None] * len(xs)
    if len(xs) < p:
        return out
    s = sum(xs[:p])
    out[p - 1] = s / p
    for i in range(p, len(xs)):
        s += xs[i] - xs[i - p]
        out[i] = s / p
    return out


def _atr_series(highs, lows, closes, p=ATR_PERIOD):
    """ATR = rolling mean of True Range (same as validate.py:atr_series)."""
    tr = [None] * len(closes)
    for i in range(len(closes)):
        if i == 0:
            tr[i] = highs[i] - lows[i]
        else:
            tr[i] = max(highs[i] - lows[i],
                        abs(highs[i] - closes[i - 1]),
                        abs(lows[i] - closes[i - 1]))
    return _rolling_mean(tr, p)


def compute(candles) -> SignalSnapshot:
    """Compute the Supertrend snapshot for the last candle."""
    if len(candles) < max(ST_PERIOD, ATR_PERIOD) + 3:
        raise ValueError(f"need >= {max(ST_PERIOD, ATR_PERIOD) + 3} candles, got {len(candles)}")

    highs = [float(c[2]) for c in candles]
    lows = [float(c[3]) for c in candles]
    closes = [float(c[4]) for c in candles]
    n = len(closes)

    # Supertrend band: a = rolling mean (high-low), hl2 = (high+low)/2
    hl = [highs[i] - lows[i] for i in range(n)]
    a = _rolling_mean(hl, ST_PERIOD)
    hl2 = [(highs[i] + lows[i]) / 2 for i in range(n)]
    up = [None if a[i] is None else hl2[i] + ST_MULT * a[i] for i in range(n)]
    dn = [None if a[i] is None else hl2[i] - ST_MULT * a[i] for i in range(n)]

    # Direction: carry-forward when price is between the bands (identical to validate.py)
    d = [0] * n
    for i in range(1, n):
        if up[i - 1] is not None and closes[i] > up[i - 1]:
            d[i] = 1
        elif dn[i - 1] is not None and closes[i] < dn[i - 1]:
            d[i] = -1
        else:
            d[i] = d[i - 1]

    # Entry signal = direction flip
    sig = 0
    if n >= 3:
        if d[-1] == 1 and d[-2] == -1:
            sig = 1
        elif d[-1] == -1 and d[-2] == 1:
            sig = -1

    atr = _atr_series(highs, lows, closes)
    atr_last = atr[-1]
    atr_pct = (atr_last / closes[-1]) if (atr_last and closes[-1]) else 0.02

    return SignalSnapshot(signal=sig, direction=d[-1], close=closes[-1],
                          atr_pct=atr_pct, bars=n)


if __name__ == "__main__":
    # Sanity check against backtest data: compute the last-bar signal for each token.
    import os, glob, json
    data_dir = os.path.join(os.path.dirname(__file__), "..", "backtest", "data")
    for f in sorted(glob.glob(os.path.join(data_dir, "*.json"))):
        if f.endswith("validation_bnb.json"):
            continue
        sym = os.path.splitext(os.path.basename(f))[0]
        candles = json.load(open(f))
        snap = compute(candles)
        tag = {1: "LONG", -1: "SHORT", 0: "-"}[snap.signal]
        print(f"{sym:<8} signal={tag:<6} dir={snap.direction:+d} "
              f"atr%={snap.atr_pct*100:4.2f} bars={snap.bars}")
