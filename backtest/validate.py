#!/usr/bin/env python3
"""
Re-validate Hermes's robust signals on the BNB Hack ELIGIBLE TOKEN UNIVERSE.
R-multiple engine (SL2%/TP6%, no look-ahead, SL checked first), Fixed1% MM +
Volatility-Targeting, Monte Carlo 500x -> risk of ruin.

Goal: pick which signal + MM has an edge on tokens that are ACTUALLY tradable in
the competition (not the BTC/SOL/gold used by the old validation).

Usage: python validate.py            (all tokens in backtest/data/)
Output: validation_bnb.json + a summary table to stdout.
"""
import os
import glob
import json
import numpy as np
import pandas as pd

DATA_DIR = os.path.join(os.path.dirname(__file__), "data")
FEE = 0.0005
SL, TP, MXH = 0.02, 0.06, 48
MIN_TRADES = 10
np.random.seed(7)


def load(path):
    df = pd.DataFrame(json.load(open(path)),
                      columns=["ts", "open", "high", "low", "close", "vol"]).astype(
                      {"open": float, "high": float, "low": float, "close": float, "vol": float})
    df["ts"] = pd.to_datetime(df["ts"].astype("int64"), unit="ms")
    return df.sort_values("ts").drop_duplicates("ts").reset_index(drop=True)


# ── indicators ──
def atr_series(df, p=14):
    tr = pd.concat([df.high - df.low, (df.high - df.close.shift()).abs(),
                    (df.low - df.close.shift()).abs()], axis=1).max(axis=1)
    return tr.rolling(p).mean()


def atr_pct(df, p=14):
    return atr_series(df, p) / df.close


# ── 5 robust signals (Liquidity Sweep DROPPED — rejected in FINDINGS) ──
def sig_volume_breakout(df):
    s = pd.Series(0, index=df.index); va = df.vol.rolling(20).mean()
    hh = df.high.rolling(20).max(); ll = df.low.rolling(20).min()
    for i in range(20, len(df)):
        if df.close.iloc[i] > hh.iloc[i-1] and df.vol.iloc[i] > va.iloc[i]*1.5: s.iloc[i] = 1
        elif df.close.iloc[i] < ll.iloc[i-1] and df.vol.iloc[i] > va.iloc[i]*1.5: s.iloc[i] = -1
    return s


def sig_atr_breakout(df):
    a = atr_series(df); s = pd.Series(0, index=df.index)
    for i in range(1, len(df)):
        if not np.isnan(a.iloc[i]):
            if df.close.iloc[i]-df.close.iloc[i-1] > 1.5*a.iloc[i]: s.iloc[i] = 1
            elif df.close.iloc[i-1]-df.close.iloc[i] > 1.5*a.iloc[i]: s.iloc[i] = -1
    return s


def sig_supertrend(df, p=10, m=3):
    a = (df.high-df.low).rolling(p).mean(); hl2 = (df.high+df.low)/2
    up = hl2+m*a; dn = hl2-m*a; d = pd.Series(index=df.index, dtype=float)
    for i in range(1, len(df)):
        if df.close.iloc[i] > up.iloc[i-1]: d.iloc[i] = 1
        elif df.close.iloc[i] < dn.iloc[i-1]: d.iloc[i] = -1
        else: d.iloc[i] = d.iloc[i-1] if not np.isnan(d.iloc[i-1]) else 0
    s = pd.Series(0, index=df.index)
    for i in range(2, len(df)):
        if d.iloc[i] == 1 and d.iloc[i-1] == -1: s.iloc[i] = 1
        elif d.iloc[i] == -1 and d.iloc[i-1] == 1: s.iloc[i] = -1
    return s


def sig_donchian(df):
    s = pd.Series(0, index=df.index); hh = df.high.rolling(20).max(); ll = df.low.rolling(20).min()
    for i in range(20, len(df)):
        if df.close.iloc[i] > hh.iloc[i-1]: s.iloc[i] = 1
        elif df.close.iloc[i] < ll.iloc[i-1]: s.iloc[i] = -1
    return s


def sig_vcp(df, W=20, K=10):
    a = atr_series(df); hh = df.high.rolling(W).max(); ll = df.low.rolling(W).min()
    s = pd.Series(0, index=df.index)
    for i in range(W+K, len(df)):
        contracting = a.iloc[i] < a.iloc[i-K] and not np.isnan(a.iloc[i])
        if not contracting: continue
        if df.close.iloc[i] > hh.iloc[i-1]: s.iloc[i] = 1
        elif df.close.iloc[i] < ll.iloc[i-1]: s.iloc[i] = -1
    return s


SIGNALS = {
    "Volume Breakout": sig_volume_breakout, "ATR Breakout": sig_atr_breakout,
    "Supertrend": sig_supertrend, "Donchian": sig_donchian, "VCP": sig_vcp,
}


def extract_trades(df, sig):
    ap = atr_pct(df); trades = []; pos = None; fee_R = 2*FEE/SL
    for i in range(1, len(df)):
        p = df.close.iloc[i]; hi = df.high.iloc[i]; lo = df.low.iloc[i]; ts = df.ts.iloc[i]
        if pos:
            xp = None; done = False
            if pos["side"] == 1:
                if lo <= pos["sl"]: xp = pos["sl"]; done = True
                elif hi >= pos["tp"]: xp = pos["tp"]; done = True
            else:
                if hi >= pos["sl"]: xp = pos["sl"]; done = True
                elif lo <= pos["tp"]: xp = pos["tp"]; done = True
            held = (ts - pos["t"]).total_seconds()/3600
            if not done and held >= MXH: xp = p; done = True
            if done:
                mv = (xp/pos["e"]-1) if pos["side"] == 1 else (1-xp/pos["e"])
                trades.append((mv/SL - fee_R, pos["atr"])); pos = None
        if pos is None and sig.iloc[i] != 0:
            pos = {"side": sig.iloc[i], "e": p, "sl": p*(1-SL) if sig.iloc[i] == 1 else p*(1+SL),
                   "tp": p*(1+TP) if sig.iloc[i] == 1 else p*(1-TP), "t": ts,
                   "atr": ap.iloc[i] if not np.isnan(ap.iloc[i]) else SL}
    return trades


def run_mm(Rs, mm, start=10000.0, med_atr=SL):
    bal = start; eq = [bal]
    for R, atr in Rs:
        if mm == "fixed1": risk = 0.01
        else: risk = min(0.02*(med_atr/atr), 0.05) if atr > 0 else 0.02
        bal += bal*risk*R
        bal = max(bal, 0); eq.append(bal)
    peak = eq[0]; mdd = 0
    for v in eq:
        peak = max(peak, v); mdd = max(mdd, (peak-v)/peak*100 if peak > 0 else 0)
    return (bal/start-1)*100, mdd


def monte_carlo(Rs, med_atr, n=500):
    ruins = 0; mdds = []
    for _ in range(n):
        order = Rs.copy(); np.random.shuffle(order)
        bal = 10000.0; peak = bal; mdd = 0; ruined = False
        for R, atr in order:
            risk = min(0.02*(med_atr/atr), 0.05) if atr > 0 else 0.02
            bal += bal*risk*R; bal = max(bal, 0)
            if bal < 2000: ruined = True
            peak = max(peak, bal); mdd = max(mdd, (peak-bal)/peak*100 if peak > 0 else 0)
        mdds.append(mdd); ruins += int(ruined)
    return ruins/n*100, float(np.median(mdds))


def main():
    files = sorted(glob.glob(os.path.join(DATA_DIR, "*.json")))
    files = [f for f in files if not f.endswith("validation_bnb.json")]
    if not files:
        print("No data in backtest/data/. Run fetch_data.py first."); return
    data = {}
    for f in files:
        sym = os.path.splitext(os.path.basename(f))[0]
        df = load(f)
        if len(df) >= 500:
            data[sym] = df
    assets = list(data.keys())
    print("=" * 100)
    print(f"RE-VALIDATION — 5 signals x {len(assets)} BNB Hack eligible tokens (CMC 1H, SL2%/TP6%)")
    print("=" * 100)

    results = {}
    for sname, fn in SIGNALS.items():
        results[sname] = {}
        for aname, df in data.items():
            trades = extract_trades(df, fn(df))
            if len(trades) < MIN_TRADES:
                results[sname][aname] = None; continue
            arr = np.array([r for r, _ in trades]); wins = arr[arr > 0]
            med_atr = float(np.median([a for _, a in trades]))
            pnl_f1, dd_f1 = run_mm(trades, "fixed1", med_atr=med_atr)
            pnl_vt, dd_vt = run_mm(trades, "voltarget", med_atr=med_atr)
            ror, mc_dd = monte_carlo(trades, med_atr)
            results[sname][aname] = dict(n=len(trades), wr=len(wins)/len(arr)*100,
                                         expR=float(arr.mean()), pnl_f1=pnl_f1, dd_f1=dd_f1,
                                         pnl_vt=pnl_vt, dd_vt=dd_vt, ror=ror, mc_dd=mc_dd)

    print(f"\n{'Signal':<18}{'Profit in':>11}{'AvgExp R':>10}{'AvgWR%':>8}"
          f"{'PnL vt(med)':>13}{'MC RoR avg':>12}{'MC DD avg':>11}")
    print("-" * 100)
    summary = {}
    for sname in SIGNALS:
        rows = [r for r in results[sname].values() if r]
        if not rows: continue
        prof = sum(1 for r in rows if r["pnl_vt"] > 0)
        avg_exp = np.mean([r["expR"] for r in rows]); avg_wr = np.mean([r["wr"] for r in rows])
        med_pnl = np.median([r["pnl_vt"] for r in rows]); avg_ror = np.mean([r["ror"] for r in rows])
        avg_mcdd = np.mean([r["mc_dd"] for r in rows])
        summary[sname] = dict(prof=prof, total=len(rows), avg_exp=float(avg_exp),
                              avg_wr=float(avg_wr), med_pnl=float(med_pnl),
                              avg_ror=float(avg_ror), avg_mcdd=float(avg_mcdd))
        print(f"{sname:<18}{f'{prof}/{len(rows)}':>11}{avg_exp:>+10.3f}{avg_wr:>7.1f}%"
              f"{med_pnl:>+12.0f}%{avg_ror:>11.1f}%{avg_mcdd:>10.0f}%")

    out = os.path.join(DATA_DIR, "validation_bnb.json")
    json.dump({"summary": summary,
               "detail": {s: {a: results[s][a] for a in results[s]} for s in results}},
              open(out, "w"), indent=2, default=str)
    print(f"\n✅ {out}")
    if summary:
        best = max(summary.items(), key=lambda kv: kv[1]["avg_exp"])
        print(f"\n👉 Highest edge (Exp R): {best[0]}  "
              f"(profit {best[1]['prof']}/{best[1]['total']}, ExpR {best[1]['avg_exp']:+.3f}, "
              f"MC DD {best[1]['avg_mcdd']:.0f}%)")


if __name__ == "__main__":
    main()
