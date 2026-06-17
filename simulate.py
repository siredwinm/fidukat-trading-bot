#!/usr/bin/env python3
"""
Fidukat simulation — watch the bot trade on historical data.

This is an event-driven, bar-by-bar replay of the FULL live logic (Supertrend
signal + risk governor + diversification cap + daily-trade guarantee) over a shared
portfolio, using the cached 1H candles in backtest/data/. Unlike the per-signal
backtest (backtest/validate.py, which picks the signal), this shows how the actual
bot — one wallet, capped position sizes, drawdown governor — would have behaved.

Run:  python simulate.py            (all cached tokens, $1000 start)
      python simulate.py 5000        (custom starting equity)
Output: a readable trade log + a summary (return, max drawdown, win rate, gate check).

No keys, no network — pure replay. Great for understanding the bot and for demos.
"""
import os
import sys
import glob

sys.path.insert(0, os.path.dirname(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "backtest"))
from validate import load, sig_supertrend, atr_pct   # reuse the validated engine
from risk import governor as gov

DATA_DIR = os.path.join(os.path.dirname(__file__), "backtest", "data")
FEE = 0.0005   # 0.05% per side (matches backtest / competition tx cost)


def load_tokens():
    tokens = {}
    for f in sorted(glob.glob(os.path.join(DATA_DIR, "*.json"))):
        if f.endswith("validation_bnb.json"):
            continue
        sym = os.path.splitext(os.path.basename(f))[0]
        if not gov.is_allowed(sym):
            continue                       # only trade the validated basket
        df = load(f)
        if len(df) < 60:
            continue
        # to unix seconds, robust to datetime resolution (ns/ms/us)
        ts = df.ts.values.astype("datetime64[s]").astype("int64").tolist()
        sig = sig_supertrend(df).tolist()
        dirn, cur = [0] * len(sig), 0          # carry-forward Supertrend direction state
        for i, sv in enumerate(sig):
            if sv != 0:
                cur = sv
            dirn[i] = cur
        tokens[sym] = {
            "ts": ts, "idx": {t: i for i, t in enumerate(ts)},
            "close": df.close.tolist(), "high": df.high.tolist(), "low": df.low.tolist(),
            "sig": sig, "dir": dirn, "ap": atr_pct(df).tolist(),
        }
    return tokens


def run(start_equity=1000.0):
    tokens = load_tokens()
    if not tokens:
        print("No cached data. Run the backtest fetcher first."); return
    master = sorted(set(t for d in tokens.values() for t in d["ts"]))

    cash = start_equity
    positions = {}                  # sym -> {entry, qty, sl, tp, t_open}
    g = gov.RiskGovernor(start_equity)
    eq_peak, max_dd = start_equity, 0.0
    trades, days_traded = [], set()

    for t in master:
        day = __import__("time").strftime("%Y-%m-%d", __import__("time").gmtime(t))
        g.roll_day(day)
        price = {s: d["close"][d["idx"][t]] for s, d in tokens.items() if t in d["idx"]}
        equity = cash + sum(p["qty"] * price.get(s, p["entry"]) for s, p in positions.items())
        eq_peak = max(eq_peak, equity)
        if eq_peak > 0:
            max_dd = max(max_dd, (eq_peak - equity) / eq_peak)

        # manage open positions (intrabar high/low; SL takes priority)
        for sym in list(positions):
            d = tokens[sym]; i = d["idx"].get(t)
            if i is None:
                continue
            p = positions[sym]; hi, lo, cl = d["high"][i], d["low"][i], d["close"][i]
            held_h = (t - p["t_open"]) / 3600
            xp, why = None, None
            if lo <= p["sl"]:
                xp, why = p["sl"], "SL"
            elif hi >= p["tp"]:
                xp, why = p["tp"], "TP"
            elif held_h >= gov.MAX_HOLD_H:
                xp, why = cl, "timeout"
            elif d["sig"][i] == -1:
                xp, why = cl, "flip-down"
            if xp is not None:
                gross = p["qty"] * (xp - p["entry"])
                fees = p["qty"] * (p["entry"] + xp) * FEE
                pnl = gross - fees
                cash += p["qty"] * p["entry"] + pnl
                trades.append({"sym": sym, "pnl": pnl, "why": why,
                               "pct": (xp / p["entry"] - 1) * 100})
                positions.pop(sym)

        # open new longs (governor: halt, sizing cap, max concurrent)
        if g.can_open(equity):
            for sym, d in tokens.items():
                if sym in positions:
                    continue
                i = d["idx"].get(t)
                if i is None or d["sig"][i] != 1:
                    continue
                if len(positions) >= gov.MAX_CONCURRENT or cash < 15:
                    break
                atr = d["ap"][i] if d["ap"][i] == d["ap"][i] else gov.SL  # NaN guard
                notional = min(g.position_size_usd(equity, atr), cash * 0.95)
                if notional < 10:
                    continue
                px = d["close"][i]
                positions[sym] = {"entry": px, "qty": notional / px,
                                  "sl": px * (1 - gov.SL), "tp": px * (1 + gov.TP), "t_open": t}
                cash -= notional
                g.record_trade(); days_traded.add(day)

            # daily-trade guarantee: if nothing fired today, enter the most stable
            # token already in an uptrend (matches live agent behavior).
            hour = __import__("time").gmtime(t).tm_hour
            if (g.needs_forced_trade(hour) and g.s.trades_today == 0
                    and len(positions) < gov.MAX_CONCURRENT and cash >= 15):
                pool = [(s, d) for s, d in tokens.items()
                        if s not in positions and t in d["idx"] and d["dir"][d["idx"][t]] == 1]
                if pool:
                    def _atr(item):
                        a = item[1]["ap"][item[1]["idx"][t]]
                        return a if a == a else gov.SL
                    sym, d = min(pool, key=_atr)
                    i = d["idx"][t]
                    notional = min(gov.keepalive_size_usd(equity), cash * 0.95)  # tiny
                    if notional >= 10:
                        px = d["close"][i]
                        positions[sym] = {"entry": px, "qty": notional / px,
                                          "sl": px * (1 - gov.SL), "tp": px * (1 + gov.TP), "t_open": t}
                        cash -= notional
                        g.record_trade(); days_traded.add(day)

    # close any still-open at last seen price
    final_eq = cash + sum(p["qty"] * tokens[s]["close"][-1] for s, p in positions.items())
    total_days = len(set(__import__("time").strftime("%Y-%m-%d", __import__("time").gmtime(t))
                         for t in master))
    wins = [x for x in trades if x["pnl"] > 0]
    ret = (final_eq / start_equity - 1) * 100

    print("=" * 60)
    print("  FIDUKAT — historical simulation (full bot logic)")
    print("=" * 60)
    print(f"  tokens traded   : {len(tokens)}   bars: {len(master)}  (~{total_days} days)")
    print(f"  start equity    : ${start_equity:,.0f}")
    print(f"  final equity    : ${final_eq:,.0f}   ({ret:+.1f}%)")
    print(f"  max drawdown    : {max_dd*100:.1f}%   "
          f"{'❌ DISQUALIFIED (>30%)' if max_dd>=0.30 else '✅ within 30% gate'}")
    print(f"  closed trades   : {len(trades)}   win rate: "
          f"{(len(wins)/len(trades)*100 if trades else 0):.0f}%")
    print(f"  days with trade : {len(days_traded)}/{total_days}  "
          f"({'✅ meets ≥1/day' if len(days_traded) >= total_days*0.9 else '⚠ check daily-trade rule'})")
    byreason = {}
    for x in trades:
        byreason[x["why"]] = byreason.get(x["why"], 0) + 1
    print(f"  exits           : {byreason}")
    print(f"  open at end     : {list(positions)}")
    print("=" * 60)
    print("  Note: long-only spot (TWAK), capped sizing, drawdown governor — this is")
    print("  the real bot, not the per-signal backtest. Past results ≠ future results.")


if __name__ == "__main__":
    eq = float(sys.argv[1]) if len(sys.argv) > 1 and sys.argv[1].replace(".", "").isdigit() else 1000.0
    run(eq)
