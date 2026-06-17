#!/usr/bin/env python3
"""
CoinMarketCap AI Agent Hub client — THE SOLE market-data source.

Two surfaces, two purposes:
1. **Pro REST** (pro-api.coinmarketcap.com, header X-CMC_PRO_API_KEY)
   -> 1H OHLCV + quote: DETERMINISTIC inputs for Supertrend & sizing.
2. **Agent Hub MCP** (mcp.coinmarketcap.com/mcp, header X-CMC-MCP-API-KEY)
   -> Fear&Greed, funding/derivatives, technical analysis, narratives:
      context for the LLM VETO (not the decision-maker) + the "Best Use of Agent Hub" claim.

Note: the exact CMC response schema must be verified against a live API key; parsing
here is defensive. Without a CMC key (dev/offline paper mode), OHLCV is read from a
LOCAL CACHE produced by a previous backtest run (backtest/data/{SYM}.json) so the loop
can still be tested without a network — NO other third-party source. In production set CMC_API_KEY.

Env: CMC_API_KEY (Pro REST), CMC_MCP_API_KEY (MCP; default = CMC_API_KEY).
"""
import os
import ssl
import json
import time
import urllib.request
import urllib.parse

PRO_BASE = "https://pro-api.coinmarketcap.com"
MCP_URL = "https://mcp.coinmarketcap.com/mcp"
CACHE_DIR = os.path.join(os.path.dirname(__file__), "..", "backtest", "data")
_CTX = ssl.create_default_context()  # verified TLS (certificate + hostname)


class CMCError(Exception):
    pass


class CMCClient:
    def __init__(self, api_key=None, mcp_api_key=None, allow_cache_fallback=True):
        self.api_key = api_key or os.environ.get("CMC_API_KEY", "")
        self.mcp_api_key = mcp_api_key or os.environ.get("CMC_MCP_API_KEY", "") or self.api_key
        self.allow_cache_fallback = allow_cache_fallback
        self._mcp_id = 0

    # ── low-level HTTP ──
    def _get(self, base, path, params, headers):
        url = f"{base}{path}?" + urllib.parse.urlencode(params)
        req = urllib.request.Request(url, headers=headers)
        with urllib.request.urlopen(req, timeout=25, context=_CTX) as r:
            return json.loads(r.read())

    def _pro(self, path, params):
        if not self.api_key:
            raise CMCError("CMC_API_KEY is not set")
        headers = {"X-CMC_PRO_API_KEY": self.api_key, "Accept": "application/json"}
        d = self._get(PRO_BASE, path, params, headers)
        status = d.get("status", {})
        ec = status.get("error_code")
        if ec not in (None, 0, "0"):   # CMC success = error_code 0 / "0"
            raise CMCError(f"CMC {ec}: {status.get('error_message')}")
        return d.get("data", d)        # F&G v3 puts the result at top-level, not under 'data'

    # ── MCP tool call (JSON-RPC 2.0) for the agent-native surface ──
    def call_mcp_tool(self, name, arguments=None):
        if not self.mcp_api_key:
            raise CMCError("CMC_MCP_API_KEY is not set")
        self._mcp_id += 1
        body = json.dumps({
            "jsonrpc": "2.0", "id": self._mcp_id, "method": "tools/call",
            "params": {"name": name, "arguments": arguments or {}},
        }).encode()
        req = urllib.request.Request(MCP_URL, data=body, headers={
            "X-CMC-MCP-API-KEY": self.mcp_api_key,
            "Content-Type": "application/json",
            "Accept": "application/json, text/event-stream",
        })
        with urllib.request.urlopen(req, timeout=30, context=_CTX) as r:
            raw = r.read().decode()
        # MCP may reply with JSON directly or as SSE ("data: {...}")
        for line in raw.splitlines():
            line = line.strip()
            if line.startswith("data:"):
                line = line[5:].strip()
            if line.startswith("{"):
                try:
                    msg = json.loads(line)
                    if "result" in msg or "error" in msg:
                        if msg.get("error"):
                            raise CMCError(f"MCP: {msg['error']}")
                        return msg["result"]
                except json.JSONDecodeError:
                    continue
        raise CMCError("MCP: unreadable response")

    # ── 1H OHLCV (deterministic) ──
    def get_ohlcv_1h(self, symbol, count=400):
        """Candles [[ts_ms,o,h,l,c,v],...] in ascending order. CMC first; local cache in dev."""
        if self.api_key:
            try:
                return self._cmc_ohlcv_1h(symbol, count)
            except CMCError as e:
                if not self.allow_cache_fallback:
                    raise
                print(f"  ! CMC OHLCV {symbol} failed ({e}); using local cache")
        if not self.allow_cache_fallback:
            raise CMCError("no CMC key & cache fallback disabled")
        return self._cache_ohlcv_1h(symbol, count)

    def _cmc_ohlcv_1h(self, symbol, count):
        data = self._pro("/v2/cryptocurrency/ohlcv/historical", {
            "symbol": symbol.upper(), "time_period": "hourly", "interval": "1h",
            "count": count, "convert": "USD",
        })
        quotes = data.get("quotes", []) if isinstance(data, dict) else []
        if not quotes and isinstance(data, dict):
            for v in data.values():  # response is sometimes wrapped per-symbol
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
            rows.append([ts_ms, float(o["open"]), float(o["high"]),
                         float(o["low"]), float(o["close"]), float(o.get("volume", 0) or 0)])
        rows.sort(key=lambda r: r[0])
        if len(rows) < 60:
            raise CMCError(f"OHLCV {symbol} only {len(rows)} bars")
        return rows

    def _cache_ohlcv_1h(self, symbol, count):
        path = os.path.join(CACHE_DIR, f"{symbol.upper()}.json")
        if not os.path.exists(path):
            raise CMCError(f"local cache {symbol} missing — set CMC_API_KEY for live data")
        rows = json.load(open(path))
        return rows[-count:] if count else rows

    # ── quote ──
    def get_quote(self, symbol):
        return self.quotes_latest([symbol]).get(symbol.upper(), {})

    def quotes_latest(self, symbols):
        """Real-time batch quote (1 call for many tokens). Available on the FREE tier.
        Used by the candle store to build its own 1H OHLC (historical OHLCV is gated).
        -> {SYM: {price, pct_1h, pct_24h, vol_24h}}."""
        syms = ",".join(s.upper() for s in symbols)
        data = self._pro("/v1/cryptocurrency/quotes/latest",
                         {"symbol": syms, "convert": "USD"})
        out = {}
        for sym in symbols:
            c = data.get(sym.upper())
            if isinstance(c, list):
                c = c[0]
            if not c:
                continue
            usd = c["quote"]["USD"]
            out[sym.upper()] = {"price": usd["price"],
                                "pct_1h": usd.get("percent_change_1h"),
                                "pct_24h": usd.get("percent_change_24h"),
                                "vol_24h": usd.get("volume_24h")}
        return out

    # ── agent-native context (for the LLM veto / demo). MCP tool names = exactly tools/list ──
    def fear_greed(self):
        d = self._pro("/v3/fear-and-greed/latest", {})
        return {"value": d.get("value"), "classification": d.get("value_classification")}

    def derivatives(self):
        """GLOBAL open interest / funding / volume via MCP."""
        return self._mcp_safe("get_global_crypto_derivatives_metrics", {})

    def technicals(self, cmc_id):
        """RSI/MACD/MA/Fib via MCP (needs the numeric CMC ID, e.g. ETH=1027)."""
        return self._mcp_safe("get_crypto_technical_analysis", {"id": str(cmc_id)})

    def narratives(self):
        return self._mcp_safe("trending_crypto_narratives", {})

    def _mcp_safe(self, tool, args):
        try:
            return self.call_mcp_tool(tool, args)
        except Exception as e:
            return {"error": str(e)}


if __name__ == "__main__":
    # Smoke test: without a key, OHLCV is read from the local cache.
    c = CMCClient()
    rows = c.get_ohlcv_1h("ETH", count=120)
    print(f"OHLCV ETH: {len(rows)} 1H bars, last close={rows[-1][4]}")
    print(f"CMC key set: {bool(c.api_key)} | MCP: {bool(c.mcp_api_key)}")
