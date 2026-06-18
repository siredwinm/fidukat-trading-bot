#!/usr/bin/env python3
"""
Trust Wallet Agent Kit (TWAK) execution adapter — the sole execution layer.

Requirement for the "Best Use of TWAK" special prize: TWAK is the heart of the trader
(self-custody local signing + autonomous mode + native x402 + guardrails), not a single
bolted-on swap call.

TWAK surface (confirmed from the official CLI reference) = SPOT ONLY:
  twak swap <amount> <from> <to> --chain bsc --slippage <pct> [--quote-only] [--json]
  twak wallet address|portfolio|balance --chain bsc --json
  twak price <token> --chain bsc
  twak x402 request <url> --max-payment <atomic>      # native x402, pay per-call
  twak serve --rest                                   # MCP/REST server
There are NO perps/futures/leverage in TWAK -> SPOT LONG-ONLY strategy:
  - LONG signal  -> swap USDT -> TOKEN (open)
  - exit         -> swap TOKEN -> USDT (close)
  - SHORT signal -> flat (hold USDT); a true short is not supported by TWAK.

Auth: Access ID + HMAC Secret from portal.trustwallet.com, set up by the installer into
~/.twak/ + the OS keychain. This wrapper only needs to call `twak`; it never touches keys.
DRY_RUN mode (default) only prints the command — set TWAK_LIVE=1 for real txs.
"""
import os
import json
import shlex
import subprocess

COMP_CONTRACT = "0x212c61b9b72c95d95bf29cf032f5e5635629aed5"  # BSC competition contract
USDT = "USDT"
CHAIN = os.environ.get("TWAK_CHAIN", "bsc")
DRY_RUN = os.environ.get("TWAK_LIVE", "0") != "1"            # default: do not send txs
CLI_BIN = os.environ.get("TWAK_CLI", "twak")                 # installer installs `twak`
PASSWORD = os.environ.get("TWAK_PASSWORD", "")               # wallet password (signing)


class TWAKError(Exception):
    pass


class TWAK:
    def __init__(self, chain=CHAIN, slippage=0.5, dry_run=None, use_x402=True):
        self.chain = chain
        self.slippage = slippage          # percent (CLI: --slippage, default 1, max 50)
        self.use_x402 = use_x402
        self.dry_run = DRY_RUN if dry_run is None else dry_run

    # ── CLI runner ──
    # Flags verified against `twak` v0.19.1: only append --json / --password to commands
    # that accept them (swap/wallet/price/compete take --json; swap & compete register
    # need --password; x402 takes neither). CLI_BIN may be multi-word ("npx @trustwallet/cli").
    def _run(self, parts, want_json=True, password=False):
        cmd = shlex.split(CLI_BIN) + parts
        if want_json:
            cmd += ["--json"]
        if password and PASSWORD:
            cmd += ["--password", PASSWORD]
        printable = " ".join(shlex.quote(p) for p in cmd)
        if self.dry_run:
            print(f"[DRY] {printable}")
            return {"dry_run": True, "cmd": printable}
        try:
            out = subprocess.run(cmd, capture_output=True, text=True, timeout=180)
        except FileNotFoundError:
            raise TWAKError(f"`{CLI_BIN}` not found — run the TWAK installer first")
        except subprocess.TimeoutExpired:
            raise TWAKError(f"timeout: {printable}")
        if out.returncode != 0:
            raise TWAKError(f"CLI failed ({out.returncode}): {out.stderr.strip() or out.stdout.strip()}")
        try:
            return json.loads(out.stdout)
        except json.JSONDecodeError:
            return {"raw": out.stdout.strip()}

    # ── competition registration: `twak compete register` (BSC-only — no --chain; needs --password) ──
    def register_competition(self):
        return self._run(["compete", "register"], password=True)

    def compete_status(self):
        return self._run(["compete", "status"])

    # ── wallet ──
    def wallet_address(self):
        return self._run(["wallet", "address", "--chain", self.chain])

    def portfolio(self, chains=None):
        parts = ["wallet", "portfolio"]
        if chains:
            parts += ["--chains", chains]
        return self._run(parts)

    def balance(self, all_chains=False):
        parts = ["wallet", "balance", "--chain", self.chain]
        if all_chains:
            parts = ["wallet", "balance", "--all"]
        return self._run(parts)

    def price(self, token):
        return self._run(["price", token, "--chain", self.chain])

    # ── spot swap (execution core) ──
    def swap(self, amount, from_token, to_token, quote_only=False):
        """twak swap <amount> <from> <to> --chain bsc --slippage <pct> [--quote-only]."""
        parts = ["swap", str(amount), from_token, to_token,
                 "--chain", self.chain, "--slippage", str(self.slippage)]
        if quote_only:
            parts.append("--quote-only")
        return self._run(parts, password=not quote_only)   # signing needs --password

    def open_long(self, token, usd_amount):
        """LONG: USDT -> TOKEN."""
        return self.swap(round(usd_amount, 2), USDT, token)

    def close_long(self, token, token_amount):
        """Close LONG: TOKEN -> USDT."""
        return self.swap(token_amount, token, USDT)

    # ── native x402 (special prize point: pay per-call). Flags per `twak x402 info`:
    #    --max-payment (atomic units), --method, --body, --prefer-network. No --json/--password. ──
    def x402_request(self, url, max_payment, prefer_network=None, method=None, body=None):
        parts = ["x402", "request", url, "--max-payment", str(max_payment)]
        if prefer_network:
            parts += ["--prefer-network", prefer_network]
        if method:
            parts += ["--method", method]
        if body:
            parts += ["--body", body]
        return self._run(parts, want_json=False, password=False)


if __name__ == "__main__":
    t = TWAK()  # dry-run default — safe, no txs
    print(f"DRY_RUN={t.dry_run} chain={t.chain} slippage={t.slippage}% x402={t.use_x402}")
    t.register_competition()
    t.wallet_address()
    t.balance()
    t.open_long("ETH", 250)
    t.close_long("ETH", 0.1)
