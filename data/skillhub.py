"""CoinMarketCap Skill Hub client — once-daily market-regime gate.

The agent leans on one platform-hosted CMC skill, `daily_market_overview`, as a
market-regime gate: in a defensive / risk-off regime it stands aside from *new
discretionary* longs instead of buying into a tightening tape (the mandatory daily
keepalive still fires, so competition eligibility is never at risk). This is a real
decision input, not a data dump — the Skill Hub's regime read steers whether the agent
takes risk today.

Transport: MCP Streamable HTTP (JSON-RPC over POST, SSE-framed response). The endpoint is
stateless, so a single `tools/call` needs no session handshake. Auth is the CMC API key
header (no x402 payment for the Skill Hub). The read is cached per UTC day — the overview
is a daily product and one call runs ~100s, so it must not be hit per trade.

Fail-open everywhere: if the Hub is unreachable, times out, or returns a `blocked`
read, the regime is reported as non-defensive `unknown` and trading proceeds unchanged.
A Hub outage must never halt the bot.
"""
import os
import json
import urllib.request
from datetime import datetime, timezone

HUB_URL = "https://mcp.coinmarketcap.com/skill-hub/stream"


def _rpc(method, params, api_key, timeout):
    body = json.dumps({"jsonrpc": "2.0", "id": 1,
                       "method": method, "params": params}).encode()
    req = urllib.request.Request(HUB_URL, data=body, method="POST", headers={
        "Content-Type": "application/json",
        "Accept": "application/json, text/event-stream",
        "X-CMC-MCP-API-KEY": api_key})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        for line in r.read().decode().splitlines():
            if line.startswith("data:"):           # SSE: one `data:{json}` frame
                return json.loads(line[5:].strip())
    raise RuntimeError("skillhub: no SSE data frame in response")


def execute_skill(unique_name, parameters, api_key, timeout=180):
    """Run one Skill Hub skill; return its parsed evidence_pack ({ok, data, ...}). Raises
    on transport/RPC error so callers can decide; daily_regime() wraps this fail-open."""
    out = _rpc("tools/call",
               {"name": "execute_skill",
                "arguments": {"unique_name": unique_name, "parameters": parameters}},
               api_key, timeout)
    if "error" in out:
        raise RuntimeError(f"skillhub rpc error: {out['error']}")
    text = out["result"]["content"][0]["text"]   # tool output is a JSON string
    obj = json.loads(text)
    return obj.get("result", obj)


def _classify(data):
    """Map a daily_market_overview read to (regime, risk_bias, defensive)."""
    mr = (data or {}).get("market_read", {}) or {}
    regime = str(mr.get("regime", "") or "")
    risk_bias = str(mr.get("risk_bias", "") or "")
    bias = str(((data or {}).get("action_guidance", {}) or {}).get("bias", "") or "")
    # Rely on the skill's authoritative judgment fields (risk_bias / action bias),
    # not on broad regime-name keywords, so the gate reflects the read rather than a
    # net wide enough to always trip.
    blob = " ".join((risk_bias, bias)).lower()
    defensive = any(k in blob for k in
                    ("defensive", "no_trade", "risk_off", "reduce_risk", "de_risk"))
    return regime, risk_bias, defensive


def daily_regime(api_key, state_dir, timeout=180):
    """Market-regime gate, cached per UTC day. Returns
    {day_utc, regime, risk_bias, defensive, source}. Never raises: on any failure it
    returns a non-defensive `unknown` read so a Hub outage cannot block trading."""
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    path = os.path.join(state_dir, "regime.json")
    try:
        cached = json.load(open(path))
        if cached.get("day_utc") == today:
            return cached
    except Exception:
        pass

    out = {"day_utc": today, "regime": "unknown", "risk_bias": "",
           "defensive": False, "source": "no_key" if not api_key else "unknown"}
    if api_key:
        try:
            res = execute_skill("daily_market_overview", {"preview": True}, api_key, timeout)
            data = res.get("data", res)
            if str(data.get("status", "")).lower() == "blocked":
                out["source"] = "blocked"
            else:
                regime, risk_bias, defensive = _classify(data)
                out.update(regime=regime, risk_bias=risk_bias,
                           defensive=defensive, source="daily_market_overview")
        except Exception as e:
            out["source"] = f"error:{type(e).__name__}"

    # Cache only settled states (a real read or a server-side block); let transient
    # network/timeout errors retry on the next cycle rather than stick all day.
    if out["source"] in ("daily_market_overview", "blocked"):
        try:
            os.makedirs(state_dir, exist_ok=True)
            json.dump(out, open(path, "w"), indent=2)
        except Exception:
            pass
    return out
