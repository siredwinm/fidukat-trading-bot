#!/usr/bin/env python3
"""
1H OHLCV fetcher from the CoinMarketCap Pro API (ohlcv/historical, time_period=hourly).
Paginates backward per time window up to ~730 days. Caches to backtest/data/{SYM}.json.

Requires CMC_API_KEY (set in .env or env). Note: historical hourly OHLCV may be
gated on certain tiers — if your tier is daily-only, reduce coverage or use a
tier that supports hourly (the competition grants CMC Pro credits to top projects).

Usage:
  python fetch_data.py                 # fetch LIQUID_CORE
  python fetch_data.py ETH XRP DOGE    # fetch specific tokens
  python fetch_data.py --all           # fetch the whole BACKTEST_UNIVERSE
Cache format: [[ts_ms, open, high, low, close, vol], ...] in ascending order.
"""
import os
import sys
import json
import time
import ssl
import urllib.request
import urllib.parse
from datetime import datetime, timedelta, timezone

from eligible import BACKTEST_UNIVERSE, LIQUID_CORE

PRO_BASE = "https://pro-api.coinmarketcap.com"
DATA_DIR = os.path.join(os.path.dirname(__file__), "data")
TARGET_DAYS = 730
WINDOW_DAYS = 25          # pagination window (~600 1H candles per call)
API_KEY = os.environ.get("CMC_API_KEY", "")
_CTX = ssl.create_default_context()  # verified TLS (certificate + hostname)


def _get(path, params):
    url = f"{PRO_BASE}{path}?" + urllib.parse.urlencode(params)
    req = urllib.request.Request(url, headers={
        "X-CMC_PRO_API_KEY": API_KEY, "Accept": "application/json"})
    with urllib.request.urlopen(req, timeout=30, context=_CTX) as r:
        return json.loads(r.read())


def _parse_quotes(data, sym):
    quotes = data.get("quotes", []) if isinstance(data, dict) else []
    if not quotes and isinstance(data, dict):
        for v in data.values():
            if isinstance(v, dict) and v.get("quotes"):
                quotes = v["quotes"]; break
    rows = []
    for q in quotes:
        o = q.get("quote", {}).get("USD", q.get("quote", {}))
        ts = q.get("time_open") or q.get("timestamp")
        try:
            ts_ms = int(time.mktime(time.strptime(ts[:19], "%Y-%m-%dT%H:%M:%S"))) * 1000
        except Exception:
            continue
        try:
            rows.append([ts_ms, float(o["open"]), float(o["high"]),
                         float(o["low"]), float(o["close"]), float(o.get("volume", 0) or 0)])
        except (KeyError, TypeError):
            continue
    return rows


def fetch_one(sym):
    rows = {}
    end = datetime.now(timezone.utc)
    start_limit = end - timedelta(days=TARGET_DAYS)
    while end > start_limit:
        start = max(end - timedelta(days=WINDOW_DAYS), start_limit)
        params = {
            "symbol": sym.upper(), "time_period": "hourly", "interval": "1h",
            "time_start": start.strftime("%Y-%m-%dT%H:%M:%S"),
            "time_end": end.strftime("%Y-%m-%dT%H:%M:%S"),
            "convert": "USD",
        }
        try:
            d = _get("/v2/cryptocurrency/ohlcv/historical", params)
        except Exception as e:
            print(f"  ! {sym}: {e}; retry 2s"); time.sleep(2); continue
        status = d.get("status", {})
        if status.get("error_code"):
            print(f"  ! {sym}: CMC {status.get('error_code')} {status.get('error_message')}"); break
        batch = _parse_quotes(d.get("data", {}), sym)
        if not batch:
            break
        for r in batch:
            rows[r[0]] = r
        end = start - timedelta(seconds=1)
        time.sleep(0.4)  # respect rate limit
    return sorted(rows.values(), key=lambda r: r[0])


def main():
    if not API_KEY:
        print("CMC_API_KEY is not set. Set it in .env / env and run again.")
        return
    os.makedirs(DATA_DIR, exist_ok=True)
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    if "--all" in sys.argv:
        syms = BACKTEST_UNIVERSE
    elif args:
        syms = [s.upper() for s in args]
    else:
        syms = LIQUID_CORE
    print(f"Fetch {len(syms)} tokens (CMC OHLCV 1H, target ~{TARGET_DAYS}d)\n")
    for i, sym in enumerate(syms, 1):
        out = os.path.join(DATA_DIR, f"{sym}.json")
        if os.path.exists(out):
            n = len(json.load(open(out)))
            print(f"[{i}/{len(syms)}] {sym}: already present ({n} candles), skip"); continue
        t0 = time.time()
        rows = fetch_one(sym)
        if len(rows) < 2000:
            print(f"[{i}/{len(syms)}] {sym}: only {len(rows)} candles (thin data) — saving anyway")
        json.dump(rows, open(out, "w"))
        days = (rows[-1][0] - rows[0][0]) / 86400000 if rows else 0
        print(f"[{i}/{len(syms)}] {sym}: {len(rows)} candles, ~{days:.0f}d, {time.time()-t0:.0f}s")
    print("\n✅ done")


if __name__ == "__main__":
    main()
