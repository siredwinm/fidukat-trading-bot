# Runbook — running Fidukat for the competition week

Operational guide to take Fidukat from "configured" to "trading live for 7 days and
surviving restarts." Read [docs/SETUP.md](SETUP.md) first for keys and installation.

---

## 0. Preflight

```bash
.venv/bin/python loop/agent.py --doctor
```

Fix any `[X]` (blocking). `[! ]` warnings are fine for paper mode; before going live you
want CoinMarketCap, the LLM veto chain, and the TWAK CLI all `[OK]`. The doctor can't
verify a real swap — do that on testnet (§2).

## 1. Timeline (competition)

| When | Action |
|---|---|
| **~1 day before the window** | Start `--loop` (TWAK_LIVE=0) so the 1H candle store warms up (Supertrend needs ~13 closed bars). |
| **Before 22 June** | Register the agent on-chain: `twak compete register` (contract `0x212c…aed5`) + submit the address on DoraHacks. |
| **At window open** | Set `TWAK_LIVE=1`, fund the wallet with in-scope tokens, restart the loop. |
| **During** | Watch `--report` / `--report-html`; ensure ≥1 trade/day (the keepalive handles dry days). |

## 2. Test one real swap on testnet first

The only thing the doctor can't check is a live swap. Before mainnet:

```bash
twak wallet address --chain bsc                 # confirm the agent wallet
twak swap 10 USDT ETH --chain bsc --quote-only  # preview, no signing
# fund the testnet wallet, then a tiny real swap to confirm end-to-end:
TWAK_LIVE=1 .venv/bin/python -c "import sys;sys.path.insert(0,'.'); from execution.twak import TWAK; print(TWAK().open_long('ETH', 5))"
```

If the JSON shape differs from what the adapter expects, adjust the `CMD_*` constants in
`execution/twak.py`, then re-test.

## 3. Run it (pick one)

### A. systemd (Linux server / VPS — recommended)

`/etc/systemd/system/fidukat.service`:

```ini
[Unit]
Description=Fidukat trading agent
After=network-online.target
Wants=network-online.target

[Service]
WorkingDirectory=/opt/fidukat-trading-bot
EnvironmentFile=/opt/fidukat-trading-bot/.env
ExecStart=/opt/fidukat-trading-bot/.venv/bin/python loop/agent.py --loop
Restart=always
RestartSec=10
StandardOutput=append:/var/log/fidukat.log
StandardError=append:/var/log/fidukat.log

[Install]
WantedBy=multi-user.target
```

```bash
sudo systemctl daemon-reload
sudo systemctl enable --now fidukat
journalctl -u fidukat -f          # live logs
```

`Restart=always` + the persisted `state/` directory means a crash or reboot resumes
cleanly (positions, governor, cash, and candle store all survive).

### B. Docker

`Dockerfile`:

```dockerfile
FROM python:3.11-slim
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY . .
VOLUME ["/app/state"]            # persist state across restarts
CMD ["python", "loop/agent.py", "--loop"]
```

```bash
docker build -t fidukat .
docker run -d --name fidukat --restart unless-stopped \
  --env-file .env -v "$PWD/state:/app/state" fidukat
docker logs -f fidukat
```

> TWAK runs as the `twak` CLI. In Docker you must install it in the image (add the
> installer to the Dockerfile) or run TWAK on the host and the bot in dry-run. The
> simplest reliable setup for the week is systemd on a small VPS with `twak` installed.

### C. Quick (laptop / tmux)

```bash
tmux new -s fidukat
.venv/bin/python loop/agent.py --loop      # Ctrl-b d to detach
```

Fine for testing; for the live week prefer A or B so it restarts on crash.

## 4. Monitor

```bash
.venv/bin/python loop/agent.py --report       # text status
.venv/bin/python loop/agent.py --report-html  # state/report.html (PnL calendar, equity curve)
tail -f /var/log/fidukat.log                   # raw loop output
```

Watch for: drawdown approaching 22% (governor halts there), ≥1 trade/day, and any
repeated `! quote poll failed` or `veto-unavailable` lines (data/LLM degraded — the
strategy still runs, but investigate connectivity).

## 5. Stop / restart

```bash
sudo systemctl stop fidukat        # or: docker stop fidukat
# state/ is preserved — starting again resumes positions and the candle store.
```

To reset for a fresh run: stop, then `rm -rf state/` (clears positions, governor, cash,
candles, journal). Only do this when you intend to start over.

## 6. Failure modes & responses

| Symptom | Cause | Response |
|---|---|---|
| `! quote poll failed` repeating | CMC down / rate limit / network | Bot retries; candles may gap. Check CMC status / key quota. |
| `veto-unavailable` | All LLM providers unreachable | Harmless — veto fails open, rule-based entries proceed. Check OpenCode/OpenRouter keys. |
| No trades for a day | No Supertrend flips and keepalive didn't fire | Confirm the loop is running and past 20:00 UTC; check `--report` for `trades_today`. |
| Drawdown halted | DD ≥ 22% | By design — protects the 30% gate. It resumes after recovery < 15%. |
| Process died | Crash | systemd/Docker `Restart` brings it back; state resumes. Check the log for the traceback. |
