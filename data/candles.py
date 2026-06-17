#!/usr/bin/env python3
"""
Candle store — build 1H OHLC from polling CoinMarketCap quotes.

The CMC free tier does NOT provide historical OHLCV (it is gated), but `quotes/latest`
(real-time price, batching many tokens in 1 call) IS available for free. This store
turns periodic quote polling into our own 1H candles — 100% CMC data, zero third-party
sources. Persisted to disk so the accumulation survives restarts across the live week.

Flow: the agent calls tick() every few minutes (poll quote -> update the 1H bar that is
currently forming). When the hour rolls over, the bar is closed & saved. get_candles()
returns a series [[ts,o,h,l,c,v],...] that feeds directly into signals/core.compute
(Supertrend only needs H/L/C; volume = 0).

Warmup: Supertrend p=10 needs ~13 bars -> run the agent ~1 day before the live window
so candles accumulate. seed_from_cache() is for DEV/offline paper only (loading a local
cache as initial history); for the live competition leave it off so it stays pure CMC.
"""
import os
import json
import time

HOUR_MS = 3600_000


def hour_bucket(ts_ms):
    return (ts_ms // HOUR_MS) * HOUR_MS


class CandleStore:
    def __init__(self, store_dir, max_bars=600):
        self.dir = store_dir
        self.max_bars = max_bars
        self.bars = {}     # {SYM: [[ts,o,h,l,c,v],...]} CLOSED bars
        self.forming = {}  # {SYM: [ts,o,h,l,c,v]} in-progress bar
        os.makedirs(store_dir, exist_ok=True)
        self._load()

    # ── persistence ──
    def _path(self, sym):
        return os.path.join(self.dir, f"{sym}.json")

    def _load(self):
        for f in os.listdir(self.dir):
            if f.endswith(".json"):
                sym = f[:-5]
                try:
                    d = json.load(open(self._path(sym)))
                    self.bars[sym] = d.get("bars", [])
                    self.forming[sym] = d.get("forming")
                except (json.JSONDecodeError, OSError):
                    pass

    def _save(self, sym):
        json.dump({"bars": self.bars.get(sym, []), "forming": self.forming.get(sym)},
                  open(self._path(sym), "w"))

    # ── update from quote ──
    def tick(self, quotes, now_ms=None):
        """quotes: {SYM: {price,...}}. Update the 1H bar for each symbol."""
        now_ms = now_ms if now_ms is not None else int(time.time() * 1000)
        hb = hour_bucket(now_ms)
        for sym, q in quotes.items():
            p = q.get("price")
            if p is None:
                continue
            p = float(p)
            fm = self.forming.get(sym)
            if fm is None or fm[0] != hb:
                # close the old bar (if any & a different hour) then start a new bar
                if fm is not None and fm[0] != hb:
                    self.bars.setdefault(sym, []).append(fm)
                    self.bars[sym] = self.bars[sym][-self.max_bars:]
                self.forming[sym] = [hb, p, p, p, p, 0.0]
            else:
                fm[2] = max(fm[2], p)   # high
                fm[3] = min(fm[3], p)   # low
                fm[4] = p               # close
            self._save(sym)

    # ── read ──
    def get_candles(self, sym, count=400, include_forming=True):
        rows = list(self.bars.get(sym, []))
        if include_forming and self.forming.get(sym):
            rows = rows + [self.forming[sym]]
        return rows[-count:] if count else rows

    def n_bars(self, sym):
        return len(self.bars.get(sym, []))

    def last_price(self, sym):
        """Most recent price: the forming bar's close, else the last closed close."""
        fm = self.forming.get(sym)
        if fm:
            return fm[4]
        bars = self.bars.get(sym)
        return bars[-1][4] if bars else None

    # ── DEV-only seed (local cache as initial history; DO NOT use for live pure-CMC) ──
    def seed_from_cache(self, cache_dir, symbols, max_bars=None):
        mb = max_bars or self.max_bars
        for sym in symbols:
            p = os.path.join(cache_dir, f"{sym}.json")
            if os.path.exists(p) and not self.bars.get(sym):
                try:
                    self.bars[sym] = json.load(open(p))[-mb:]
                    self._save(sym)
                except (json.JSONDecodeError, OSError):
                    pass


if __name__ == "__main__":
    # demo: simulate 3 ticks within 1 hour + hour jump -> 1 closed bar
    import tempfile
    d = tempfile.mkdtemp()
    cs = CandleStore(d)
    t0 = hour_bucket(int(time.time() * 1000))
    cs.tick({"ETH": {"price": 100}}, now_ms=t0 + 60_000)
    cs.tick({"ETH": {"price": 105}}, now_ms=t0 + 120_000)
    cs.tick({"ETH": {"price": 98}}, now_ms=t0 + 180_000)
    print("forming after 3 ticks (1 hour):", cs.forming["ETH"], "(O100 H105 L98 C98)")
    cs.tick({"ETH": {"price": 101}}, now_ms=t0 + HOUR_MS + 60_000)  # next hour
    print("closed bar:", cs.bars["ETH"])
    print("get_candles:", cs.get_candles("ETH"))
