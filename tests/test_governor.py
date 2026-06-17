from risk import governor as gov


def test_levels_long_short():
    sl, tp = gov.compute_levels(100.0, 1)
    assert sl == 100 * (1 - gov.SL) and tp == 100 * (1 + gov.TP)
    sl, tp = gov.compute_levels(100.0, -1)
    assert sl == 100 * (1 + gov.SL) and tp == 100 * (1 - gov.TP)


def test_allowlist():
    assert gov.is_allowed("eth") and gov.is_allowed("DOGE")
    assert not gov.is_allowed("BTC")  # not in the eligible basket


def test_vol_targeting_smaller_for_volatile():
    base = gov.size_fraction(gov.REF_ATR)          # at reference vol -> base risk
    volatile = gov.size_fraction(gov.REF_ATR * 3)  # 3x vol -> smaller
    assert base > volatile
    assert gov.MIN_RISK <= volatile <= gov.MAX_RISK


def test_position_size_capped():
    g = gov.RiskGovernor(1000)
    n = g.position_size_usd(1000, gov.REF_ATR)
    assert n <= 1000 * gov.MAX_POSITION_FRAC + 1e-6   # diversification cap


def test_drawdown_halt_and_hysteresis():
    g = gov.RiskGovernor(1000)
    assert g.can_open(1000)                 # no drawdown
    assert g.can_open(900)                  # 10% < derisk start
    assert not g.can_open(770)              # 23% >= 22% halt
    assert not g.can_open(840)              # 16% — still halted (not yet < resume 15%)
    assert g.can_open(870)                  # 13% < 15% resume -> reopened


def test_risk_scale_decreases_in_drawdown():
    g = gov.RiskGovernor(1000)
    assert g.risk_scale(1000) == 1.0
    mid = g.risk_scale(820)                 # 18% drawdown, between 12% and 22%
    assert 0.0 < mid < 1.0


def test_keepalive_tiny():
    assert gov.keepalive_size_usd(1000) == max(gov.KEEPALIVE_MIN_USD, 1000 * gov.KEEPALIVE_FRAC)
    assert gov.keepalive_size_usd(1000) < 1000 * gov.MAX_POSITION_FRAC   # much smaller than a real trade


def test_from_dict_ignores_legacy_keys():
    g = gov.RiskGovernor(1000)
    g.record_trade()
    d = g.to_dict()
    d["LEGACY_UNKNOWN"] = 123                # simulate schema drift
    g2 = gov.RiskGovernor.from_dict(d)
    assert g2.s.total_trades == 1


def test_daily_trade_guarantor():
    g = gov.RiskGovernor(1000)
    g.roll_day("2026-06-22")
    assert g.needs_forced_trade(21)         # past 20:00 UTC, no trade today
    assert not g.needs_forced_trade(10)     # too early
    g.record_trade()
    assert not g.needs_forced_trade(21)     # already traded today
