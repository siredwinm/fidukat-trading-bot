import tempfile
from data.candles import CandleStore, hour_bucket, HOUR_MS


def test_tick_builds_ohlc_and_rolls_over():
    cs = CandleStore(tempfile.mkdtemp())
    t0 = hour_bucket(1_700_000_000_000)
    cs.tick({"ETH": {"price": 100}}, now_ms=t0 + 60_000)
    cs.tick({"ETH": {"price": 105}}, now_ms=t0 + 120_000)
    cs.tick({"ETH": {"price": 98}}, now_ms=t0 + 180_000)
    fm = cs.forming["ETH"]
    assert fm[1] == 100 and fm[2] == 105 and fm[3] == 98 and fm[4] == 98  # O H L C
    assert cs.n_bars("ETH") == 0                                          # nothing closed yet

    cs.tick({"ETH": {"price": 101}}, now_ms=t0 + HOUR_MS + 60_000)        # next hour
    assert cs.n_bars("ETH") == 1                                          # previous bar closed
    assert cs.last_price("ETH") == 101                                    # forming bar close


def test_get_candles_closed_vs_forming():
    cs = CandleStore(tempfile.mkdtemp())
    t0 = hour_bucket(1_700_000_000_000)
    cs.tick({"X": {"price": 10}}, now_ms=t0 + 10_000)
    cs.tick({"X": {"price": 11}}, now_ms=t0 + HOUR_MS + 10_000)   # closes bar 1, forms bar 2
    assert len(cs.get_candles("X", include_forming=False)) == 1
    assert len(cs.get_candles("X", include_forming=True)) == 2


def test_persistence_round_trip():
    d = tempfile.mkdtemp()
    cs = CandleStore(d)
    t0 = hour_bucket(1_700_000_000_000)
    cs.tick({"X": {"price": 10}}, now_ms=t0 + 10_000)
    cs.tick({"X": {"price": 12}}, now_ms=t0 + HOUR_MS + 10_000)
    cs2 = CandleStore(d)                          # reload from disk
    assert cs2.n_bars("X") == 1 and cs2.last_price("X") == 12
