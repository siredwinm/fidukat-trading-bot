import time
from signals import core


def _candles(closes):
    """Build OHLC rows from a close series (high/low ±0.1% around close)."""
    t0 = 1_700_000_000_000
    rows = []
    for i, c in enumerate(closes):
        rows.append([t0 + i * 3600_000, c, c * 1.001, c * 0.999, c, 0.0])
    return rows


def test_uptrend_direction_positive():
    snap = core.compute(_candles([100 + i for i in range(60)]))  # steadily rising
    assert snap.direction == 1
    assert snap.atr_pct > 0


def test_downtrend_direction_negative():
    snap = core.compute(_candles([200 - i for i in range(60)]))  # steadily falling
    assert snap.direction == -1


def test_signal_in_range_and_too_few_raises():
    snap = core.compute(_candles([100 + (i % 3) for i in range(60)]))
    assert snap.signal in (-1, 0, 1)
    try:
        core.compute(_candles([100, 101, 102]))   # < required bars
        assert False, "expected ValueError"
    except ValueError:
        pass


def test_flip_emits_signal():
    # fall for 30 bars then rise for 30 -> Supertrend should flip up (signal == 1) somewhere
    closes = [200 - i * 2 for i in range(30)] + [140 + i * 3 for i in range(30)]
    seen = set()
    for cut in range(20, len(closes) + 1):   # scan every bar so we land on the flip bar
        seen.add(core.compute(_candles(closes[:cut])).signal)
    assert 1 in seen   # an up-flip (signal == 1) occurs when the rise overtakes the band
