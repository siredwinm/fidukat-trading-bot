# Setup & Run

End-to-end setup for Fidukat: environment, credentials, paper mode, and going live.

## 1. Prerequisites

- Python 3.11 (the repo uses [`uv`](https://github.com/astral-sh/uv); plain `venv` + `pip` also works)
- A CoinMarketCap API key (free tier is enough — see §3)
- For live trading: a Trust Wallet AgentKit (TWAK) account + credentials (§5)

## 2. Environment

```bash
uv venv --python 3.11 .venv
uv pip install --python .venv/bin/python -r requirements.txt
cp .env.example .env
```

Every command below uses `.venv/bin/python`. The agent auto-loads `.env`.

## 3. CoinMarketCap (data)

1. Create a free key at <https://pro.coinmarketcap.com>.
2. Put it in `.env` as `CMC_API_KEY=...` (it is also used as `CMC_MCP_API_KEY` by default).

What the free tier provides and how Fidukat uses it:

| Capability | Free tier | Used for |
|---|---|---|
| `quotes/latest` (batch) | ✅ | Build 1H candles for the Supertrend signal |
| Fear & Greed (`/v3/fear-and-greed`) | ✅ | LLM veto context |
| Agent Hub MCP (derivatives, technicals, narratives) | ✅ | LLM veto context |
| Historical OHLCV | ❌ gated | Backtest only (needs a paid tier) |

Because OHLCV is gated, Fidukat **builds its own 1H candles** from quote polling — see
[DESIGN.md §4](../DESIGN.md). No third-party exchange is used.

## 4. LLM veto (optional)

Set `ANTHROPIC_API_KEY` in `.env` to enable the Claude veto (model `VETO_MODEL`,
default `claude-haiku-4-5-20251001`). Without it, the veto is a safe no-op and the
pure rule-based strategy runs. The LLM can only *skip* an entry, never create one.

## 5. Trust Wallet Agent Kit (execution)

```bash
curl -fsSL https://agent-kit.trustwallet.com/install.sh | bash
```

The installer installs the `twak` CLI, prompts for your **Access ID** and **HMAC
Secret** (from <https://portal.trustwallet.com/dashboard/apps>), saves them to
`~/.twak/` + the OS keychain, and offers to create a self-custody agent wallet.

Verify and configure:

```bash
twak wallet address --chain bsc          # your agent wallet on BSC
twak price ETH --chain bsc               # sanity check
twak swap 10 USDT ETH --chain bsc --quote-only   # preview a swap, no signing
```

In `.env`: keep `TWAK_LIVE=0` (dry-run) until you are ready. Set `TWAK_PASSWORD` if
you are not using the OS keychain for signing.

## 6. BNB AI Agent SDK — on-chain identity (optional, special prize)

```bash
pip install bnbagent
```

Set `WALLET_PASSWORD` (and `AGENT_PRIVATE_KEY` on first run) in `.env`, then:

```bash
.venv/bin/python integration/identity.py
```

This registers an ERC-8004 agent identity (gas-free on `bsc-testnet`) and prints the
`agentId` + tx hash. Identity only — trading still goes through TWAK.

## 7. Running

```bash
# One evaluation cycle (paper if TWAK_LIVE=0)
.venv/bin/python loop/agent.py --once

# One quote poll -> update the candle store (useful to warm up / test connectivity)
.venv/bin/python loop/agent.py --poll

# Continuous loop: poll quotes every POLL_SECONDS, evaluate + trade on each new hour
.venv/bin/python loop/agent.py --loop
```

State persists under `state/` (positions, governor, candles, cash) so restarts resume
cleanly.

## 8. Going live (competition)

1. Set `TWAK_LIVE=1` in `.env` and fund the agent wallet with in-scope tokens (hold a
   non-zero balance at the start — see the rules).
2. Register on-chain **before 22 June**: `twak compete register` (contract
   `0x212c61b9b72c95d95bf29cf032f5e5635629aed5`), and submit the agent address on
   DoraHacks.
3. Start `--loop` **~1 day early** so the 1H candle history warms up (Supertrend needs
   ~13 bars before it emits signals).
4. Let it run for the window. The governor enforces ≥1 trade/day, the allowlist, and
   the drawdown brake automatically.

## 9. Backtest (optional, research)

`backtest/validate.py` reads cached 1H OHLCV from `backtest/data/` and reproduces the
signal selection. `backtest/fetch_data.py` populates that cache from CMC (needs a tier
with historical OHLCV). See [docs/STRATEGY.md](STRATEGY.md).
