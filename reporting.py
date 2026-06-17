#!/usr/bin/env python3
"""
Fidukat HTML dashboard — a human-readable report for the operator and the judges.

Reads the trade journal (state/journal.jsonl) plus persisted state and renders a
self-contained HTML file (no external assets/JS libraries): summary cards, an
equity curve (inline SVG), a daily profit/loss calendar heatmap, and a trade table.

Run:  python reporting.py            -> writes state/report.html
      python loop/agent.py --report-html
Open state/report.html in any browser.
"""
import os
import json
from datetime import datetime, timezone

STATE_DIR = os.path.join(os.path.dirname(__file__), "state")
JOURNAL = os.path.join(STATE_DIR, "journal.jsonl")
OUT = os.path.join(STATE_DIR, "report.html")


def _load(path, default):
    try:
        return json.load(open(path))
    except (FileNotFoundError, json.JSONDecodeError):
        return default


def _events():
    out = []
    if os.path.exists(JOURNAL):
        for line in open(JOURNAL):
            line = line.strip()
            if line:
                try:
                    out.append(json.loads(line))
                except json.JSONDecodeError:
                    pass
    return out


def _day(ts):
    return ts[:10]  # ISO date prefix


def build(out_path=OUT):
    events = _events()
    closes = [e for e in events if e.get("event") == "CLOSE"]
    opens = [e for e in events if e.get("event") == "OPEN"]
    gov = _load(os.path.join(STATE_DIR, "governor.json"), {}) or {}
    cash = _load(os.path.join(STATE_DIR, "cash.json"), {"usd": 0}).get("usd", 0)
    positions = _load(os.path.join(STATE_DIR, "positions.json"), {})

    start = gov.get("start_equity", 1000.0)
    peak = gov.get("peak_equity", start)
    realized = sum(e.get("pnl", 0) for e in closes)
    wins = [e for e in closes if e.get("pnl", 0) > 0]
    win_rate = (len(wins) / len(closes) * 100) if closes else 0
    ret = (realized / start * 100) if start else 0

    # daily realized PnL
    daily = {}
    for e in closes:
        daily[_day(e["ts"])] = daily.get(_day(e["ts"]), 0) + e.get("pnl", 0)

    # equity curve points (start + cumulative realized PnL over close sequence)
    eq, curve = start, [start]
    for e in sorted(closes, key=lambda x: x["ts"]):
        eq += e.get("pnl", 0)
        curve.append(eq)

    html = _render(start, cash, peak, realized, ret, win_rate, len(closes),
                   len(opens), positions, daily, curve, closes)
    os.makedirs(STATE_DIR, exist_ok=True)
    open(out_path, "w").write(html)
    return out_path


def _svg_curve(curve, w=720, h=160, pad=8):
    if len(curve) < 2:
        return '<p style="color:#888">No closed trades yet — equity curve appears after the first exit.</p>'
    lo, hi = min(curve), max(curve)
    rng = (hi - lo) or 1
    n = len(curve) - 1
    pts = []
    for i, v in enumerate(curve):
        x = pad + (w - 2 * pad) * i / n
        y = pad + (h - 2 * pad) * (1 - (v - lo) / rng)
        pts.append(f"{x:.1f},{y:.1f}")
    color = "#16a34a" if curve[-1] >= curve[0] else "#dc2626"
    return (f'<svg viewBox="0 0 {w} {h}" width="100%" height="{h}" '
            f'style="background:#0d1117;border-radius:8px">'
            f'<polyline fill="none" stroke="{color}" stroke-width="2" points="{" ".join(pts)}"/>'
            f'</svg>')


def _calendar(daily):
    """Group days by month, render a heatmap of daily PnL (green profit / red loss)."""
    if not daily:
        return '<p style="color:#888">No trades recorded yet.</p>'
    mx = max((abs(v) for v in daily.values()), default=1) or 1
    by_month = {}
    for d, pnl in daily.items():
        by_month.setdefault(d[:7], {})[d] = pnl
    out = []
    for month in sorted(by_month):
        y, m = int(month[:4]), int(month[5:7])
        first_wd = datetime(y, m, 1, tzinfo=timezone.utc).weekday()  # Mon=0
        days_in = (datetime(y + (m == 12), (m % 12) + 1, 1, tzinfo=timezone.utc)
                   - datetime(y, m, 1, tzinfo=timezone.utc)).days
        cells = ['<div class="cal-h">Mon</div><div class="cal-h">Tue</div><div class="cal-h">Wed</div>'
                 '<div class="cal-h">Thu</div><div class="cal-h">Fri</div><div class="cal-h">Sat</div>'
                 '<div class="cal-h">Sun</div>']
        cells += ['<div class="cal-e"></div>'] * first_wd
        for dnum in range(1, days_in + 1):
            ds = f"{month}-{dnum:02d}"
            pnl = by_month[month].get(ds)
            if pnl is None:
                cells.append(f'<div class="cal-d">{dnum}</div>')
            else:
                pos = pnl >= 0
                alpha = 0.2 + 0.8 * min(abs(pnl) / mx, 1)
                bg = f"rgba(22,163,74,{alpha:.2f})" if pos else f"rgba(220,38,38,{alpha:.2f})"
                cells.append(f'<div class="cal-d" style="background:{bg}" '
                             f'title="{ds}: ${pnl:+.2f}">{dnum}<span>{pnl:+.0f}</span></div>')
        out.append(f'<h3>{month}</h3><div class="cal">{"".join(cells)}</div>')
    return "".join(out)


def _render(start, cash, peak, realized, ret, win_rate, n_closed, n_open,
            positions, daily, curve, closes):
    pos_rows = "".join(
        f"<tr><td>{s}</td><td>{p['entry']:.6f}</td><td>{p['qty']:.4f}</td>"
        f"<td>{p['sl']:.6f}</td><td>{p['tp']:.6f}</td></tr>"
        for s, p in positions.items()) or '<tr><td colspan="5" style="color:#888">none</td></tr>'
    trade_rows = "".join(
        f"<tr><td>{e['ts'][:16].replace('T',' ')}</td><td>{e['sym']}</td>"
        f"<td>{e.get('reason','')}</td>"
        f"<td class='{'g' if e.get('pnl',0)>=0 else 'r'}'>${e.get('pnl',0):+.2f}</td>"
        f"<td class='{'g' if e.get('pnl_pct',0)>=0 else 'r'}'>{e.get('pnl_pct',0):+.1f}%</td></tr>"
        for e in sorted(closes, key=lambda x: x["ts"], reverse=True)[:50]
    ) or '<tr><td colspan="5" style="color:#888">no closed trades yet</td></tr>'
    ret_c = "g" if ret >= 0 else "r"
    return f"""<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Fidukat — Trading Dashboard</title><style>
*{{box-sizing:border-box}}body{{font:14px/1.5 -apple-system,Segoe UI,Roboto,sans-serif;
margin:0;background:#010409;color:#e6edf3}}.wrap{{max-width:880px;margin:0 auto;padding:28px}}
h1{{font-size:22px;margin:0 0 2px}}.sub{{color:#8b949e;margin:0 0 22px}}
.cards{{display:grid;grid-template-columns:repeat(auto-fit,minmax(150px,1fr));gap:12px;margin-bottom:24px}}
.card{{background:#0d1117;border:1px solid #21262d;border-radius:10px;padding:14px}}
.card .l{{color:#8b949e;font-size:12px;text-transform:uppercase;letter-spacing:.04em}}
.card .v{{font-size:22px;font-weight:600;margin-top:4px}}
.g{{color:#3fb950}}.r{{color:#f85149}}
h2{{font-size:15px;margin:26px 0 10px;color:#c9d1d9}}h3{{font-size:12px;color:#8b949e;margin:14px 0 6px}}
table{{width:100%;border-collapse:collapse;font-size:13px}}
th,td{{text-align:left;padding:7px 10px;border-bottom:1px solid #21262d}}th{{color:#8b949e;font-weight:500}}
.cal{{display:grid;grid-template-columns:repeat(7,1fr);gap:4px}}
.cal-h{{color:#8b949e;font-size:11px;text-align:center;padding:2px}}
.cal-d{{aspect-ratio:1;border-radius:6px;background:#0d1117;border:1px solid #21262d;
padding:4px;font-size:11px;color:#8b949e;position:relative}}
.cal-d span{{position:absolute;bottom:3px;right:4px;font-size:10px;font-weight:600;color:#e6edf3}}
.cal-e{{aspect-ratio:1}}.foot{{color:#6e7681;font-size:12px;margin-top:28px}}
</style></head><body><div class="wrap">
<h1>Fidukat — Trading Dashboard</h1>
<p class="sub">Disciplined self-custody trading agent on BNB Chain · realized figures from the trade journal</p>
<div class="cards">
<div class="card"><div class="l">Start equity</div><div class="v">${start:,.0f}</div></div>
<div class="card"><div class="l">Realized PnL</div><div class="v {ret_c}">${realized:+,.2f}</div></div>
<div class="card"><div class="l">Return</div><div class="v {ret_c}">{ret:+.1f}%</div></div>
<div class="card"><div class="l">Peak equity</div><div class="v">${peak:,.0f}</div></div>
<div class="card"><div class="l">Win rate</div><div class="v">{win_rate:.0f}%</div></div>
<div class="card"><div class="l">Closed / Open</div><div class="v">{n_closed} / {n_open}</div></div>
<div class="card"><div class="l">Free cash</div><div class="v">${cash:,.0f}</div></div>
</div>
<h2>Equity curve (realized)</h2>{_svg_curve(curve)}
<h2>Daily profit &amp; loss</h2>{_calendar(daily)}
<h2>Open positions</h2><table><tr><th>Token</th><th>Entry</th><th>Qty</th><th>SL</th><th>TP</th></tr>{pos_rows}</table>
<h2>Recent trades</h2><table><tr><th>Closed (UTC)</th><th>Token</th><th>Reason</th><th>PnL</th><th>%</th></tr>{trade_rows}</table>
<p class="foot">Long-only spot via Trust Wallet Agent Kit · drawdown governor halts at 22% (gate 30%) ·
LLM veto-only. Past performance is not a guarantee of future results.</p>
</div></body></html>"""


if __name__ == "__main__":
    print("wrote", build())
