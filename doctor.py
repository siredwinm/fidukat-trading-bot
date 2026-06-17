#!/usr/bin/env python3
"""
Fidukat preflight doctor — one command to check the bot is ready before going live.

Verifies what it safely can without sending real transactions: data connectivity
(CoinMarketCap), the LLM veto provider chain, the Trust Wallet CLI, candle warmup,
config sanity, and the dry-run/live flag. Prints [OK] / [!] / [X] per check and an
overall verdict.

Run:  python doctor.py        or        python loop/agent.py --doctor
"""
import os
import sys
import shutil

sys.path.insert(0, os.path.dirname(__file__))
from data.cmc import CMCClient
from data.candles import CandleStore
from signals import veto
from risk import governor as gov
from execution import twak as twakmod

OK, WARN, FAIL = "[OK]", "[! ]", "[X]"
STATE_DIR = os.path.join(os.path.dirname(__file__), "state")


def _mask(k):
    return (k[:4] + "…" + k[-3:]) if k and len(k) > 8 else ("set" if k else "")


def main():
    rows = []   # (status, label, detail)

    # 1. CoinMarketCap data
    cmc = CMCClient()
    if cmc.api_key:
        try:
            q = cmc.quotes_latest(["ETH"])
            px = q.get("ETH", {}).get("price")
            rows.append((OK, "CoinMarketCap data", f"key {_mask(cmc.api_key)} · ETH=${px:,.2f}") if px
                        else (FAIL, "CoinMarketCap data", "quote returned no price"))
        except Exception as e:
            rows.append((FAIL, "CoinMarketCap data", f"quotes call failed: {e}"))
    else:
        rows.append((WARN, "CoinMarketCap data", "no CMC_API_KEY — live candles need it (cache only)"))

    # 2. LLM veto provider chain
    chain = veto._chain()
    if chain:
        names = " → ".join(b or veto._DEFAULT_BASE.get(s, s) for s, b, m, k in chain)
        rows.append((OK, "LLM veto chain", f"{len(chain)} provider(s): {names}"))
    else:
        rows.append((WARN, "LLM veto", "no provider key — veto disabled (rule-based strategy still runs)"))

    # 3. Trust Wallet Agent Kit CLI
    cli = twakmod.CLI_BIN.split()[0]
    if shutil.which(cli) or (cli == "npx" and shutil.which("npx")):
        rows.append((OK, "TWAK CLI", f"`{twakmod.CLI_BIN}` found — verify auth: twak wallet address --chain {twakmod.CHAIN}"))
    else:
        rows.append((WARN, "TWAK CLI", f"`{cli}` not on PATH — run the installer (see docs/SETUP.md)"))

    # 4. Live vs dry-run flag
    if twakmod.DRY_RUN:
        rows.append((OK, "Execution mode", "DRY-RUN (no real transactions) — set TWAK_LIVE=1 to go live"))
    else:
        rows.append((WARN, "Execution mode", "LIVE — real transactions will be signed"))

    # 5. Candle warmup
    store = CandleStore(os.path.join(STATE_DIR, "candles"))
    counts = {s: store.n_bars(s) for s in gov.ALLOWLIST}
    ready = sum(1 for n in counts.values() if n >= 13)
    mn = min(counts.values()) if counts else 0
    if ready == len(gov.ALLOWLIST):
        rows.append((OK, "Candle warmup", f"all {len(gov.ALLOWLIST)} tokens have ≥13 closed bars"))
    elif ready:
        rows.append((WARN, "Candle warmup", f"{ready}/{len(gov.ALLOWLIST)} tokens ready (min {mn} bars) — keep --loop running"))
    else:
        rows.append((WARN, "Candle warmup", "no candles yet — run `--loop` ~1 day before the window (Supertrend needs ~13 bars)"))

    # 6. Config sanity
    eq = os.environ.get("START_EQUITY", "1000")
    rows.append((OK, "Config", f"START_EQUITY=${eq} · allowlist {len(gov.ALLOWLIST)} tokens · "
                 f"MAX_CONCURRENT={gov.MAX_CONCURRENT} · DD halt {int(gov.DD_HALT*100)}%"))

    # ── print ──
    print("=" * 64)
    print("  FIDUKAT — preflight doctor")
    print("=" * 64)
    for st, label, detail in rows:
        print(f"  {st} {label:<20} {detail}")
    print("=" * 64)
    fails = sum(1 for st, *_ in rows if st == FAIL)
    warns = sum(1 for st, *_ in rows if st == WARN)
    if fails:
        print(f"  ❌ {fails} blocking issue(s) — fix before running.")
    elif warns:
        print(f"  ⚠️  {warns} warning(s) — review, but the bot can run (paper mode is safe).")
    else:
        print("  ✅ All checks passed.")
    print("  Note: a real swap via TWAK is the only thing this can't verify — test it on testnet.")
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
