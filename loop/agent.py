#!/usr/bin/env python3
"""
Hourly agent loop — deterministic brain, self-custody execution.

Flow each hour (1H bar close):
  1. roll UTC day + mark-to-market equity -> update drawdown governor
  2. manage open positions: check SL/TP/timeout/direction flip -> close via TWAK
  3. find entry: Supertrend LONG + governor.can_open -> (LLM veto) -> size -> open
  4. guarantee >=1 trade/day: if no trade yet & past the hour cutoff -> take
     the best available LONG signal (still subject to drawdown halt)
  5. persist state (positions + governor) -> survive restarts through the live week

Principle: trade decisions are 100% deterministic (Supertrend + risk governor). The LLM
may ONLY VETO (reject) an entry using CMC context (Fear & Greed, funding) — it never
creates an entry. Backtests prove that rule-based + drawdown discipline is what wins; an LLM
that "decides" actually loses money & is bullish-biased.

Default mode = PAPER (DRY): local cash+position accounting, no tx. Set TWAK_LIVE=1
(+ CMC_API_KEY) for live. Run: python loop/agent.py --once  |  --loop
"""
import os
import sys
import json
import time
from datetime import datetime, timezone

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

# Load .env BEFORE importing modules that read env (TWAK_LIVE/TWAK_CHAIN are read at import time).
try:
    from dotenv import load_dotenv
    load_dotenv(os.path.join(os.path.dirname(__file__), "..", ".env"))
except ImportError:
    pass

from signals import core
from signals import veto
from risk import governor as gov
from data.cmc import CMCClient
from data.candles import CandleStore
from execution.twak import TWAK

STATE_DIR = os.path.join(os.path.dirname(__file__), "..", "state")
POS_FILE = os.path.join(STATE_DIR, "positions.json")
GOV_FILE = os.path.join(STATE_DIR, "governor.json")
JOURNAL_FILE = os.path.join(STATE_DIR, "journal.jsonl")
CACHE_DIR = os.path.join(os.path.dirname(__file__), "..", "backtest", "data")
START_EQUITY = float(os.environ.get("START_EQUITY", "1000"))
POLL_SECONDS = int(os.environ.get("POLL_SECONDS", "300"))   # poll quotes every 5 minutes
SEED_CACHE = os.environ.get("SEED_CACHE", "0") == "1"       # dev warmup from local cache


def now_utc():
    return datetime.now(timezone.utc)


def _load(path, default):
    try:
        return json.load(open(path))
    except (FileNotFoundError, json.JSONDecodeError):
        return default


def _save(path, obj):
    os.makedirs(STATE_DIR, exist_ok=True)
    json.dump(obj, open(path, "w"), indent=2)


class Agent:
    def __init__(self):
        self.cmc = CMCClient()
        self.twak = TWAK()
        self.paper = self.twak.dry_run  # paper-accounting when dry-run
        self.positions = _load(POS_FILE, {})            # {sym: {side,entry,qty,sl,tp,t_open}}
        gstate = _load(GOV_FILE, None)
        self.gov = gov.RiskGovernor.from_dict(gstate) if gstate else gov.RiskGovernor(START_EQUITY)
        self.cash = _load(os.path.join(STATE_DIR, "cash.json"), {"usd": START_EQUITY})["usd"]
        # Candle source: live (CMC key) -> built from quote polling; offline -> cache.
        self.use_store = bool(self.cmc.api_key)
        self.store = CandleStore(os.path.join(STATE_DIR, "candles"))
        if self.use_store and SEED_CACHE:
            self.store.seed_from_cache(CACHE_DIR, gov.ALLOWLIST)
        # LLM veto (optional): active when ANTHROPIC_API_KEY is present
        self.llm_key = os.environ.get("ANTHROPIC_API_KEY", "")
        self._macro_hour = None
        self._macro_ctx = {}

    def poll(self):
        """Poll CMC quotes (batch) -> update candle store. Called frequently (live).
        One retry on transient failure so a single hiccup doesn't drop a candle."""
        if not self.use_store:
            return
        for attempt in (1, 2):
            try:
                self.store.tick(self.cmc.quotes_latest(gov.ALLOWLIST))
                return
            except Exception as e:
                if attempt == 2:
                    print(f"  ! quote poll failed: {e}")
                else:
                    time.sleep(2)

    def _candles(self, sym, count=400):
        """CLOSED 1H candles for signals (exclude the still-forming bar so the live
        signal matches the backtest, which uses closed bars only)."""
        if self.use_store:
            return self.store.get_candles(sym, count, include_forming=False)
        return self.cmc.get_ohlcv_1h(sym, count)

    def _price(self, sym, closed):
        """Current price for MTM / SL-TP / entry: latest quote (live) or last close."""
        if self.use_store:
            p = self.store.last_price(sym)
            if p is not None:
                return p
        return closed[-1][4]

    # ── equity (paper: cash + mark-to-market of LONG positions) ──
    def mark_to_market(self, prices):
        eq = self.cash
        for sym, p in self.positions.items():
            eq += p["qty"] * prices.get(sym, p["entry"])
        return eq

    # ── LLM veto (optional; passes by default). The LLM may ONLY VETO. ──
    def _macro(self):
        """CMC macro context, cached per hour (2 global calls)."""
        h = now_utc().strftime("%Y-%m-%d-%H")
        if self._macro_hour != h:
            self._macro_ctx = veto.macro_context(self.cmc) if self.llm_key else {}
            self._macro_hour = h
        return self._macro_ctx

    def veto(self, sym, snap):
        if not self.llm_key:
            return False
        v, reason = veto.veto_entry(sym, snap, self._macro(), self.llm_key)
        if v:
            print(f"  VETO {sym}: {reason}")
        return v

    def _journal(self, event, sym, **fields):
        """Append one human-readable trade event to state/journal.jsonl."""
        os.makedirs(STATE_DIR, exist_ok=True)
        rec = {"ts": now_utc().isoformat(), "event": event, "sym": sym, **fields}
        with open(JOURNAL_FILE, "a") as f:
            f.write(json.dumps(rec) + "\n")

    def _close(self, sym, px, reason):
        p = self.positions.pop(sym)
        notional = p["qty"] * p["entry"]
        pnl = p["qty"] * (px - p["entry"])          # LONG only
        pnl_pct = (px / p["entry"] - 1) * 100 if p["entry"] else 0.0
        if not self.paper:
            self.twak.close_long(sym, p["qty"])     # TOKEN -> USDT (real swap)
        self.cash += notional + pnl                 # cash accounting in both modes
        self._journal("CLOSE", sym, price=px, qty=p["qty"], entry=p["entry"],
                      pnl=round(pnl, 2), pnl_pct=round(pnl_pct, 2), reason=reason)
        print(f"  CLOSE {sym} @ {px:.6f} ({reason}) pnl=${pnl:.2f} ({pnl_pct:+.1f}%)")

    def _open_long(self, sym, price, atr_pct, equity, keepalive=False):
        """Spot LONG: USDT -> TOKEN (TWAK = spot only, no shorts). Entry at the
        current price. Normal trades use vol-targeted+capped size; keepalive trades
        (daily-rule) use a tiny size so they don't drag PnL."""
        notional = (gov.keepalive_size_usd(equity) if keepalive
                    else self.gov.position_size_usd(equity, atr_pct))
        notional = min(notional, self.cash * 0.95)
        if notional < 10:
            return False
        qty = notional / price
        sl, tp = gov.compute_levels(price, 1)
        if not self.paper:
            self.twak.open_long(sym, notional)      # USDT -> TOKEN (real swap)
        self.cash -= notional                       # cash accounting in both modes
        self.positions[sym] = {"side": 1, "entry": price, "qty": qty,
                               "sl": sl, "tp": tp, "t_open": now_utc().isoformat()}
        self.gov.record_trade()
        self._journal("OPEN", sym, price=price, qty=qty, notional=notional,
                      reason="keepalive" if keepalive else "supertrend-long", atr_pct=atr_pct)
        print(f"  OPEN {'KEEPALIVE' if keepalive else 'LONG'} {sym} @ {price:.6f} "
              f"notional=${notional:.0f} sl={sl:.6f} tp={tp:.6f} atr%={atr_pct*100:.2f}")
        return True

    def run_once(self):
        t = now_utc()
        day, hour = t.strftime("%Y-%m-%d"), t.hour
        self.gov.roll_day(day)
        print(f"\n=== {t.isoformat()} | day_trades={self.gov.s.trades_today} "
              f"paper={self.paper} ===")

        # poll the latest quotes (live), then compute snapshots from 1H candles
        self.poll()
        snaps, prices, warming = {}, {}, 0
        for sym in gov.ALLOWLIST:
            try:
                candles = self._candles(sym, count=400)   # closed bars only
                snaps[sym] = core.compute(candles)
                prices[sym] = self._price(sym, candles)   # current price
            except ValueError:
                warming += 1   # not enough candles yet (warmup)
            except Exception as e:
                print(f"  ! {sym}: data failed ({e})")
        if warming:
            print(f"  (warmup: {warming} tokens lack enough candles for a signal)")

        equity = self.mark_to_market(prices)
        can_open = self.gov.can_open(equity)
        dd = self.gov.drawdown(equity)
        print(f"  equity=${equity:.0f} dd={dd*100:.1f}% can_open={can_open} "
              f"open_pos={list(self.positions)}")

        # 1) manage open positions (exit at current price)
        for sym in list(self.positions):
            p = self.positions[sym]
            snap = snaps.get(sym)
            px = prices.get(sym)
            if snap is None or px is None:
                continue
            held_h = (t - datetime.fromisoformat(p["t_open"])).total_seconds() / 3600
            if px <= p["sl"]:
                self._close(sym, p["sl"], "SL")
            elif px >= p["tp"]:
                self._close(sym, p["tp"], "TP")
            elif held_h >= gov.MAX_HOLD_H:
                self._close(sym, px, "timeout")
            elif snap.direction == -1:
                self._close(sym, px, "flip-down")   # Supertrend flips down -> exit

        # 2) find new entries — LONG only (TWAK spot; SHORT signals ignored)
        candidates = []
        for sym, snap in snaps.items():
            if sym in self.positions:
                continue
            if snap.signal == 1 and not self.veto(sym, snap):
                candidates.append((sym, snap))

        if can_open:
            for sym, snap in candidates:
                if len(self.positions) >= gov.MAX_CONCURRENT or self.cash < 15:
                    break
                self._open_long(sym, prices[sym], snap.atr_pct, equity)

            # 3) guarantee >=1 trade/day (competition rule). Supertrend flips are sparse,
            #    so if none fired today, enter the most stable token already in an
            #    uptrend (direction +1) — a valid trend-following entry, not a fresh flip.
            if (self.gov.needs_forced_trade(hour) and self.gov.s.trades_today == 0
                    and len(self.positions) < gov.MAX_CONCURRENT and self.cash >= 15):
                pool = candidates or [
                    (s, snaps[s]) for s in snaps
                    if snaps[s].direction == 1 and s not in self.positions
                    and s in prices and not self.veto(s, snaps[s])
                ]
                if pool:
                    sym, snap = min(pool, key=lambda x: x[1].atr_pct)  # most stable
                    print(f"  [daily keepalive] {sym}")
                    self._open_long(sym, prices[sym], snap.atr_pct, equity, keepalive=True)

        # 4) persist
        self.gov.update_equity(equity)
        _save(POS_FILE, self.positions)
        _save(GOV_FILE, self.gov.to_dict())
        _save(os.path.join(STATE_DIR, "cash.json"), {"usd": self.cash})
        return equity


def report():
    """Human-readable status report from persisted state + the trade journal."""
    pos = _load(POS_FILE, {})
    gstate = _load(GOV_FILE, None)
    cash = _load(os.path.join(STATE_DIR, "cash.json"), {"usd": START_EQUITY})["usd"]
    events = []
    if os.path.exists(JOURNAL_FILE):
        for line in open(JOURNAL_FILE):
            line = line.strip()
            if line:
                events.append(json.loads(line))
    closes = [e for e in events if e["event"] == "CLOSE"]
    wins = [e for e in closes if e.get("pnl", 0) > 0]
    realized = sum(e.get("pnl", 0) for e in closes)
    start = gstate["start_equity"] if gstate else START_EQUITY
    peak = gstate["peak_equity"] if gstate else start
    print("=" * 56)
    print("  FIDUKAT — status report")
    print("=" * 56)
    print(f"  start equity   : ${start:,.2f}")
    print(f"  cash (free)    : ${cash:,.2f}")
    print(f"  peak equity    : ${peak:,.2f}")
    if gstate:
        print(f"  total trades   : {gstate['total_trades']}  | today: {gstate['trades_today']}")
        print(f"  drawdown HALT  : {'YES' if gstate['halted'] else 'no'}")
    print(f"  closed trades  : {len(closes)}  | win rate: "
          f"{(len(wins)/len(closes)*100 if closes else 0):.0f}%")
    print(f"  realized PnL   : ${realized:+,.2f}")
    print(f"  open positions : {len(pos)}")
    for sym, p in pos.items():
        print(f"     {sym:<6} entry={p['entry']:.6f} qty={p['qty']:.4f} "
              f"sl={p['sl']:.6f} tp={p['tp']:.6f}")
    if closes:
        print("  recent closes  :")
        for e in closes[-5:]:
            print(f"     {e['ts'][:16]} {e['sym']:<6} {e.get('reason',''):<10} "
                  f"${e.get('pnl',0):+.2f} ({e.get('pnl_pct',0):+.1f}%)")
    print("=" * 56)


def main():
    a = Agent()
    if "--report" in sys.argv:
        report()
        return
    if "--poll" in sys.argv:   # single quote poll -> update candle store, then exit
        a.poll()
        print(f"poll done. sample n_bars: " +
              ", ".join(f"{s}={a.store.n_bars(s)}" for s in gov.ALLOWLIST[:5]))
        return
    if "--loop" not in sys.argv:
        a.run_once()
        return

    # Live loop: poll quotes frequently (build candles), evaluate+trade when the hour rolls over.
    print(f"Loop active (poll {POLL_SECONDS}s, eval per hour, use_store={a.use_store}). Ctrl-C to stop.")
    last_eval_hour = None
    while True:
        try:
            a.poll()
            h = now_utc().strftime("%Y-%m-%d-%H")
            if h != last_eval_hour:
                a.run_once()      # run_once also polls once (idempotent enough)
                last_eval_hour = h
        except Exception as e:
            print(f"  ! loop error: {e}")
        time.sleep(POLL_SECONDS if a.use_store else
                   max(60, 3600 - (now_utc().minute * 60 + now_utc().second)))


if __name__ == "__main__":
    main()
