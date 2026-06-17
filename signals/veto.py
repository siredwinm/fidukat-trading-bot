#!/usr/bin/env python3
"""
LLM veto — a VETO layer, not a decision-maker.

Philosophy (proven by backtest): trade decisions must be deterministic (Supertrend +
risk governor). An LLM that "decides direction" actually loses money and is bullish-
biased. So the LLM here may ONLY VETO an entry that has already passed the rules, when
the CMC market context shows clear macro risk. Default: DO NOT veto (allow) — the
rule-based entry still runs if the LLM is unsure, errors out, or the key is missing.
The veto is an extra brake, not the gas.

Context from the CoinMarketCap Agent Hub (per the hackathon build example: "funding
rates + Fear & Greed"): Fear & Greed Index + global derivatives (open interest, funding
bias). Cheap: 2 global calls, cached hourly by the caller — not per token.

LLM: Anthropic Messages API (claude-haiku-4-5 — fast and cheap for veto). Set
ANTHROPIC_API_KEY to enable it; without it the veto is a no-op (always allows).
"""
import os
import json
import ssl
import urllib.request

API_URL = "https://api.anthropic.com/v1/messages"
MODEL = os.environ.get("VETO_MODEL", "claude-haiku-4-5-20251001")
_CTX = ssl.create_default_context(); _CTX.check_hostname = False; _CTX.verify_mode = ssl.CERT_NONE

SYSTEM = (
    "You are a RISK VETO module for a rule-based crypto trading agent. A deterministic "
    "Supertrend system has already decided to open a LONG. Your ONLY job is to VETO "
    "(skip) the entry when market context shows a CLEAR elevated risk of immediate "
    "downside — e.g. extreme greed with open interest dropping hard, or obvious blow-off. "
    "You cannot open or modify trades. Bias strongly toward ALLOWING (veto=false): only "
    "veto on clear, specific risk. Reply ONLY compact JSON: {\"veto\":bool,\"reason\":\"<=12 words\"}."
)


def macro_context(cmc):
    """Fetch CMC macro context (cached hourly by the caller). Safe if parts fail."""
    ctx = {}
    try:
        ctx["fear_greed"] = cmc.fear_greed()
    except Exception as e:
        ctx["fear_greed"] = {"error": str(e)}
    try:
        ctx["derivatives"] = cmc.derivatives()   # global OI / funding / volume
    except Exception as e:
        ctx["derivatives"] = {"error": str(e)}
    return ctx


def veto_entry(symbol, snap, macro, api_key=None, model=MODEL):
    """-> (veto: bool, reason: str). Defaults to (False, ...) on missing key / error."""
    api_key = api_key or os.environ.get("ANTHROPIC_API_KEY", "")
    if not api_key:
        return False, "no-llm"
    user = json.dumps({
        "action": f"OPEN LONG {symbol}",
        "atr_pct": round(snap.atr_pct * 100, 2),
        "supertrend_dir": snap.direction,
        "fear_greed": macro.get("fear_greed"),
        "derivatives_global": macro.get("derivatives"),
    }, default=str)[:3000]
    body = json.dumps({
        "model": model, "max_tokens": 80, "temperature": 0,
        "system": SYSTEM,
        "messages": [{"role": "user", "content": user}],
    }).encode()
    req = urllib.request.Request(API_URL, data=body, headers={
        "x-api-key": api_key, "anthropic-version": "2023-06-01",
        "content-type": "application/json",
    })
    try:
        with urllib.request.urlopen(req, timeout=20, context=_CTX) as r:
            d = json.loads(r.read())
        text = "".join(b.get("text", "") for b in d.get("content", []) if b.get("type") == "text")
        s, e = text.find("{"), text.rfind("}")
        obj = json.loads(text[s:e + 1])
        return bool(obj.get("veto", False)), str(obj.get("reason", ""))[:60]
    except Exception as e:
        return False, f"veto-error:{e}"  # on failure -> do not block the rule-based entry


if __name__ == "__main__":
    # without ANTHROPIC_API_KEY -> no-op
    from types import SimpleNamespace
    snap = SimpleNamespace(atr_pct=0.02, direction=1)
    print(veto_entry("ETH", snap, {"fear_greed": {"value": 78}, "derivatives": {}}))
