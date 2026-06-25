#!/usr/bin/env python3
"""
Trust Wallet Agent Kit (TWAK) execution adapter — the sole execution layer.

TWAK is the heart of the trader: self-custody local signing, autonomous-mode swaps,
native x402 settlement for pay-per-call data, and guardrails — not a single bolted-on
swap call. Keys never leave the machine.

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

# Verified BEP-20 contract addresses on BSC. CRITICAL: bare symbols are NOT safe on
# TWAK's BSC token registry — most allowlist symbols return "Unknown token", and a few
# (DOGE/AVAX/TRX) SILENTLY MIS-ROUTE to BNB instead of erroring. So we always swap by
# contract address and refuse unknown symbols. Verified 18 Jun 2026 via
# `twak swap 50 USDT <addr> --chain bsc --quote-only` (output symbol matched 16/16).
BSC_TOKENS = {
    "USDT":  "0x55d398326f99059fF775485246999027B3197955",  # BSC-USD (Binance-Peg)
    "ETH":   "0x2170Ed0880ac9A755fd29B2688956BD959F933F8",
    "DOGE":  "0xbA2aE424d960c26247Dd6c32edC70B295c744C43",
    "UNI":   "0xBf5140A22578168FD562DCcF235E5D43A02ce9B1",
    "DOT":   "0x7083609fCE4d1d8Dc0C979AAb8c869Ea2C873402",
    "COMP":  "0x52CE071Bd9b1C4B00A0b92D298c512478CaD67e8",
    "AVAX":  "0x1CE0c2827e2eF14D5C4f29a091d735A204794041",
    "ACH":   "0xBc7d6B50616989655AfD682fb42743507003056D",
    "BCH":   "0x8fF795a6F4D97E7887C79beA79aba5cc76444aDf",
    "FIL":   "0x0D8Ce2A99Bb6e3B7Db580eD848240e4a0F9aE153",
    "ZIL":   "0xb86AbCb37C3A4B64f74f59301AFF131a1BEcC787",
    "YFI":   "0x88f1A5ae2A3BF98AEAF342D26B30a79438c9142e",
    "TRX":   "0xCE7de646e7208a4Ef112cb6ed5038FA6cC6b12e3",
    "1INCH": "0x111111111117dC0aa78b770fA6A738034120C302",
    "AAVE":  "0xfb6115445Bff7b52FeB98650C87f44907E58f802",
    "XRP":   "0x1D2F0da169ceB9fC7B3144628dB156f3F6c60dBE",
}

# On-chain token decimals (verified 23 Jun 2026 via balanceOf/decimals() against BSC RPC).
# CRITICAL: TWAK assumes 18 decimals for non-registry tokens, so SELLING a token with
# fewer decimals makes it compute qty*1e18 base units >> real balance -> the swap reverts
# (quotes still succeed since they price off an API, not the on-chain balance). We must
# pass --decimals for any source token that is not 18. Only non-18 tokens need listing.
BSC_DECIMALS = {
    "DOGE": 8,
    "ACH":  8,
    "ZIL":  12,
    "TRX":  6,
}

CHAIN = os.environ.get("TWAK_CHAIN", "bsc")
DRY_RUN = os.environ.get("TWAK_LIVE", "0") != "1"            # default: do not send txs
CLI_BIN = os.environ.get("TWAK_CLI", "twak")                 # installer installs `twak`
PASSWORD = os.environ.get("TWAK_PASSWORD", "")               # wallet password (signing)

# ── x402 pay-per-call data settlement ──
# CoinMarketCap exposes its data behind HTTP 402: the agent pays a small on-chain
# stablecoin amount per request instead of holding a monthly API subscription or a
# stored API key ("payment is authentication"). The published spec settles in USDC on
# Base, but the live endpoint also advertises a BSC-USDT route, so the agent pays from
# the same USDT it already trades with — no second chain or bridged balance to manage.
# Gasless after a one-time Permit2 approval; ~$0.01 per request.
X402_BASE = "https://pro-api.coinmarketcap.com"
X402_USDT_BSC = "0x55d398326f99059fF775485246999027B3197955"
X402_MAX_ATOMIC = "10000000000000000"   # 0.01 USDT (18dp) — per-request auto-approve cap


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

    # ── token resolution (BSC) ──
    def _resolve(self, token):
        """Map a symbol to its verified BSC contract address. Pass through anything that
        already looks like an address (0x…). On BSC, REFUSE unknown symbols rather than
        letting TWAK silently mis-route them (e.g. DOGE/AVAX/TRX -> BNB)."""
        if token.startswith("0x") and len(token) == 42:
            return token
        if self.chain != "bsc":
            return token   # map is BSC-specific; other chains use symbols
        addr = BSC_TOKENS.get(token.upper())
        if not addr:
            raise TWAKError(f"no verified BSC contract for {token!r} — refusing to swap "
                            f"(bare symbols mis-route on TWAK BSC). Add it to BSC_TOKENS.")
        return addr

    # ── spot swap (execution core) ──
    def swap(self, amount, from_token, to_token, quote_only=False):
        """twak swap <amount> <from> <to> --chain bsc --slippage <pct> [--quote-only].
        Tokens are resolved to verified BSC contract addresses before the call."""
        parts = ["swap", str(amount), self._resolve(from_token), self._resolve(to_token),
                 "--chain", self.chain, "--slippage", str(self.slippage)]
        # Source token amount is denominated in the source token's units. For non-18
        # tokens TWAK would otherwise assume 18 decimals and revert the swap (see
        # BSC_DECIMALS note). Only the SOURCE token's decimals matter for the amount.
        if self.chain == "bsc":
            dec = BSC_DECIMALS.get(str(from_token).upper())
            if dec is not None and dec != 18:
                parts += ["--decimals", str(dec)]
        if quote_only:
            parts.append("--quote-only")
        return self._run(parts, password=not quote_only)   # signing needs --password

    def open_long(self, token, usd_amount):
        """LONG: USDT -> TOKEN."""
        return self.swap(round(usd_amount, 2), USDT, token)

    def close_long(self, token, token_amount):
        """Close LONG: TOKEN -> USDT."""
        return self.swap(token_amount, token, USDT)

    # ── x402 pay-per-call data (HTTP 402 micropayment over the wallet) ──
    # Low-level escape hatch: hit any x402-gated URL with explicit settlement terms.
    def x402_request(self, url, max_payment, prefer_network=None, method=None, body=None):
        parts = ["x402", "request", url, "--max-payment", str(max_payment)]
        if prefer_network:
            parts += ["--prefer-network", prefer_network]
        if method:
            parts += ["--method", method]
        if body:
            parts += ["--body", body]
        return self._run(parts, want_json=False, password=False)

    def x402_get(self, path, params=None):
        """Fetch a CoinMarketCap x402-gated endpoint, paying per request from on-chain
        USDT (BSC). The agent reaches for this at the moment it commits capital — pulling
        fresh, paid-for confirmation data exactly when a stale free quote would be most
        costly. Pay-per-use fits a low-frequency unattended trader better than a monthly
        subscription, and no API key is stored for this path. Returns parsed JSON;
        dry-run returns a marker and pays nothing."""
        from urllib.parse import urlencode
        url = X402_BASE + path + (("?" + urlencode(params)) if params else "")
        parts = ["x402", "request", url,
                 "--max-payment", X402_MAX_ATOMIC,
                 "--prefer-network", "bsc",
                 "--prefer-asset", X402_USDT_BSC,
                 "--prefer-method", "permit2-exact",
                 "--yes", "--json"]
        if self.dry_run:
            print(f"[DRY] {CLI_BIN} {' '.join(parts)}")
            return {"dry_run": True}
        cmd = shlex.split(CLI_BIN) + parts
        try:
            out = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
        except subprocess.TimeoutExpired:
            raise TWAKError("x402 request timed out")
        if out.returncode != 0:
            raise TWAKError(f"x402 failed ({out.returncode}): "
                            f"{out.stderr.strip() or out.stdout.strip()}")
        text = out.stdout.strip()
        try:
            return json.loads(text)
        except json.JSONDecodeError:
            # tolerate any log lines before the JSON body; the body may be {…} or […]
            starts = [i for i in (text.find("{"), text.find("[")) if i >= 0]
            if starts:
                return json.loads(text[min(starts):])
            raise TWAKError("x402: could not parse JSON response")


if __name__ == "__main__":
    t = TWAK()  # dry-run default — safe, no txs
    print(f"DRY_RUN={t.dry_run} chain={t.chain} slippage={t.slippage}% x402={t.use_x402}")
    t.register_competition()
    t.wallet_address()
    t.balance()
    t.open_long("ETH", 250)
    t.close_long("ETH", 0.1)
