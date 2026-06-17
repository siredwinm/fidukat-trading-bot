#!/usr/bin/env python3
"""
Risk governor — THE WINNING MODULE for the competition.

Track 1 scoring penalizes blow-ups: drawdown > ~30% = DISQUALIFICATION, no matter
how good the profit is. Backtests prove that vol-targeting drives DD down to ~18%
while remaining profitable. This module enforces that discipline strictly:

1. Vol-targeting position sizing  — volatile setups get a smaller size.
2. Drawdown governor             — de-risk gradually, then HALT entirely well below the gate.
3. SL2% / TP6% / max 48h hold    — fixed exits (optimal per exit-management findings).
4. >=1 trade/day guarantor       — competition qualification requirement.
5. Token allowlist               — only the validated basket may be traded.

No LLM here. Everything is deterministic and auditable.
"""
from dataclasses import dataclass, field, asdict, fields

# ── validated token basket (expR>=+0.13, MC DD<=18%, RoR 0% on re-backtest) ──
ALLOWLIST = [
    "DOGE", "UNI", "DOT", "COMP", "AVAX", "ACH", "ETH", "BCH",
    "FIL", "ZIL", "YFI", "TRX", "1INCH", "AAVE", "XRP",
]

# ── fixed exits ──
SL = 0.02
TP = 0.06
MAX_HOLD_H = 48

# ── vol-targeting ──
BASE_RISK = 0.02     # risk per trade when vol = reference
MAX_RISK = 0.05      # cap risk fraction
MIN_RISK = 0.004     # floor so we still meet >=1 trade/day
REF_ATR = 0.02       # reference atr_pct (≈ SL); used when no per-token ref exists

# ── diversification (avoid single-name concentration under the drawdown gate) ──
MAX_CONCURRENT = 4         # max simultaneous positions
MAX_POSITION_FRAC = 0.34   # cap notional per position to this fraction of equity

# ── drawdown governor (competition gate ~30%) ──
DD_DERISK_START = 0.12   # start shrinking size
DD_HALT = 0.22           # STOP opening new positions — 8% buffer below the 30% gate
DD_RESUME = 0.15         # reopen only after recovering below this (hysteresis)

# ── daily trade guarantor ──
FORCE_TRADE_AFTER_UTC_HOUR = 20  # if no trade yet today & already >= this UTC hour


def is_allowed(symbol: str) -> bool:
    return symbol.upper() in ALLOWLIST


def compute_levels(entry: float, side: int):
    """(stop_loss, take_profit) for side +1 (long) / -1 (short)."""
    if side == 1:
        return entry * (1 - SL), entry * (1 + TP)
    return entry * (1 + SL), entry * (1 - TP)


def size_fraction(atr_pct: float, ref_atr: float = REF_ATR) -> float:
    """Vol-targeting: fraction of equity put at risk. Identical semantics to the backtest:
    risk = clamp(BASE_RISK * ref_atr/atr, MIN, MAX)."""
    if not atr_pct or atr_pct <= 0:
        return BASE_RISK
    frac = BASE_RISK * (ref_atr / atr_pct)
    return max(MIN_RISK, min(frac, MAX_RISK))


@dataclass
class GovernorState:
    start_equity: float
    peak_equity: float
    halted: bool = False          # currently stopping new positions (DD mode)
    day_utc: str = ""             # last recorded UTC date (YYYY-MM-DD)
    trades_today: int = 0
    total_trades: int = 0


class RiskGovernor:
    """Risk state machine for the hourly loop. Persistable via to_dict/from_dict."""

    def __init__(self, start_equity: float):
        self.s = GovernorState(start_equity=start_equity, peak_equity=start_equity)

    # ── persistence ──
    def to_dict(self):
        return asdict(self.s)

    @classmethod
    def from_dict(cls, d):
        g = cls(d.get("start_equity", d.get("peak_equity", 0.0)))
        known = {f.name for f in fields(GovernorState)}   # ignore unknown/legacy keys
        g.s = GovernorState(**{k: v for k, v in d.items() if k in known})
        return g

    # ── daily & equity updates ──
    def roll_day(self, day_utc: str):
        """Call each iteration with the current UTC date; resets the daily count."""
        if day_utc != self.s.day_utc:
            self.s.day_utc = day_utc
            self.s.trades_today = 0

    def update_equity(self, equity: float):
        self.s.peak_equity = max(self.s.peak_equity, equity)
        dd = self.drawdown(equity)
        if dd >= DD_HALT:
            self.s.halted = True
        elif self.s.halted and dd <= DD_RESUME:
            self.s.halted = False   # hysteresis: only resume after recovery

    def drawdown(self, equity: float) -> float:
        if self.s.peak_equity <= 0:
            return 0.0
        return max(0.0, (self.s.peak_equity - equity) / self.s.peak_equity)

    # ── decisions ──
    def can_open(self, equity: float) -> bool:
        """May we open a new position? No, when in drawdown HALT mode."""
        self.update_equity(equity)
        return not self.s.halted

    def risk_scale(self, equity: float) -> float:
        """Size multiplier 0..1 that decreases linearly between DERISK_START and HALT."""
        dd = self.drawdown(equity)
        if dd <= DD_DERISK_START:
            return 1.0
        if dd >= DD_HALT:
            return 0.0
        span = DD_HALT - DD_DERISK_START
        return max(0.0, 1.0 - (dd - DD_DERISK_START) / span)

    def position_size_usd(self, equity: float, atr_pct: float,
                          ref_atr: float = REF_ATR) -> float:
        """USD notional placed: equity * risk_fraction * derisk_scale / SL.
        (risk_fraction = fraction of equity lost if SL is hit; notional = risk/SL.)"""
        frac = size_fraction(atr_pct, ref_atr) * self.risk_scale(equity)
        risk_usd = equity * frac
        notional = risk_usd / SL
        return min(notional, equity * MAX_POSITION_FRAC)   # diversification cap

    def needs_forced_trade(self, hour_utc: int) -> bool:
        """No trade yet today & past the cutoff → must take the best signal
        (still subject to can_open; DD discipline wins over the daily quota)."""
        return self.s.trades_today == 0 and hour_utc >= FORCE_TRADE_AFTER_UTC_HOUR

    def record_trade(self):
        self.s.trades_today += 1
        self.s.total_trades += 1


if __name__ == "__main__":
    # quick demo: simulate a DD curve to confirm halt before the gate
    g = RiskGovernor(10000)
    print(f"allowlist {len(ALLOWLIST)} token | SL{int(SL*100)}%/TP{int(TP*100)}% hold{MAX_HOLD_H}h")
    print(f"sl/tp long @100 -> {compute_levels(100,1)}")
    print(f"size frac atr2% -> {size_fraction(0.02):.3f} | atr5% -> {size_fraction(0.05):.3f}")
    for eq in (10000, 9000, 8800, 8200, 7800, 8600):
        ok = g.can_open(eq)
        print(f"eq={eq} dd={g.drawdown(eq)*100:4.1f}% scale={g.risk_scale(eq):.2f} "
              f"can_open={ok} halted={g.s.halted}")
