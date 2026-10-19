#!/usr/bin/env python3
"""MU/META/TSLA 8月图表: 模板标配版 (灰底+前日高低+彩色图例)"""
import csv, os, sys, math, datetime
from datetime import time
from zoneinfo import ZoneInfo
from collections import defaultdict
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import matplotlib.patches as mpatches
import matplotlib.font_manager as fm

ET = ZoneInfo("America/New_York")
SITE = os.path.expanduser("~/workspace/spx-desk")
_cjk = fm.FontProperties(fname="/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc")
plt.rcParams["font.family"] = _cjk.get_name()
plt.rcParams["axes.unicode_minus"] = False

DATES = ["2026-08-03","2026-08-04","2026-08-05","2026-08-06","2026-08-07",
         "2026-08-10","2026-08-11","2026-08-12","2026-08-13","2026-08-14",
         "2026-08-17","2026-08-18","2026-08-19"]

def _should_draw_level(level, day_lo, day_hi, is_high):
    """制图核心原则: 只画有参考价值的线.
    - 价格碰过 (容差 0.1%) → 画
    - 在附近 (距离 ≤ 日内波幅 50%) → 画
    - 很远且没碰过 → 不画 (图例文字保留, 灰底保留)
    """
    day_range = day_hi - day_lo
    if day_range <= 0:
        return True
    if is_high:
        touched = day_hi >= level * 0.999
        nearby = (level - day_hi) <= day_range * 0.5
    else:
        touched = day_lo <= level * 1.001
        nearby = (day_lo - level) <= day_range * 0.5
    return touched or nearby

def _emphasize_round_levels(ax):
    lo, hi = ax.get_ylim()
    ticks = [round(float(t), 2) for t in ax.get_yticks()]
    v = math.ceil(lo / 50) * 50
    add = []
    while v <= hi:
        if round(v) % 100 in (0, 50):
            add.append(float(v))
        v += 50
    if add:
        ax.set_yticks(sorted(set(ticks) | set(add)))
        ax.figure.canvas.draw()
    for lbl in ax.get_yticklabels():
        try:
            vv = float(lbl.get_text().replace(",", ""))
        except ValueError:
            continue
        if round(vv) % 100 in (0, 50):
            lbl.set_color("#dc2626")
            lbl.set_weight("bold")

def load_all(csv_path):
    buckets = defaultdict(list)
    pm = defaultdict(list)
    with open(csv_path) as fh:
        for row in csv.DictReader(fh):
            ts = int(row["ts_event"]) / 1e9
            dt = datetime.datetime.fromtimestamp(ts, tz=datetime.timezone.utc).astimezone(ET)
            ds = dt.strftime("%Y-%m-%d")
            o, h, l, c = int(row["open"])/1e9, int(row["high"])/1e9, int(row["low"])/1e9, int(row["close"])/1e9
            v = int(row["volume"])
            t = dt.timetz()
            if time(4, 0) <= t < time(9, 30):
                pm[ds].append((h, l))
                continue
            if not (time(9, 30) <= t < time(16, 0)):
                continue
            key = dt.replace(minute=(dt.minute//5)*5, second=0, microsecond=0)
            buckets[(ds, key)].append((o, h, l, c, v))
    by_date = defaultdict(list)
    for (ds, key), bs in sorted(buckets.items()):
        by_date[ds].append((key, bs[0][0], max(b[1] for b in bs), min(b[2] for b in bs), bs[-1][3], sum(b[4] for b in bs)))
    return by_date, pm

def main():
    ticker = sys.argv[1]
    csv_path = f"/tmp/databento/{ticker.lower()}_aug.csv"
    by_date, pm = load_all(csv_path)
    out_dir = os.path.join(SITE, ticker.lower(), "charts")
    
    for ds in DATES:
        bars = by_date.get(ds, [])
        if not bars:
            print(f"{ticker} {ds}: 无数据", flush=True)
            continue
        d = datetime.date.fromisoformat(ds) - datetime.timedelta(days=1)
        prev_h, prev_l = None, None
        for _ in range(5):
            pds = d.isoformat()
            if pds in by_date and by_date[pds]:
                pb = by_date[pds]
                prev_h = max(b[3] for b in pb)
                prev_l = min(b[4] for b in pb)
                break
            d -= datetime.timedelta(days=1)
        pm_h = max((h for h, l in pm.get(ds, [])), default=None)
        pm_l = min((l for h, l in pm.get(ds, [])), default=None)
        vols = [b[5] for b in bars]
        num, den, vwap_curve = 0.0, 0.0, []
        for _, o, h, l, c, v in bars:
            if v > 0:
                tp = (h + l + c) / 3
                num += tp * v
                den += v
            vwap_curve.append(num/den if den else None)
        vwap_final = vwap_curve[-1] if vwap_curve and vwap_curve[-1] else None
        
        n = len(bars)
        op, cl = bars[0][1], bars[-1][4]
        chg = (cl / op - 1) * 100
        
        fig, ax1 = plt.subplots(1, 1, figsize=(14, 7.5))
        fig.patch.set_facecolor("#0d1117")
        ax1.set_facecolor("#0d1117")
        ax1.tick_params(colors="#8b949e", labelsize=9)
        for spine in ax1.spines.values():
            spine.set_color("#30363d")
        x = list(range(n))
        ticks = [i for i, (dt, _, _, _, _, _) in enumerate(bars) if dt.minute % 30 == 0]
        ax1.set_xticks(ticks)
        ax1.set_xticklabels([bars[i][0].strftime("%H:%M") for i in ticks], fontsize=8)
        UP, DN = "#22c55e", "#ef4444"
        for i, (dt, o, h, l, c, v) in enumerate(bars):
            col = UP if c >= o else DN
            ax1.plot([i, i], [l, h], color=col, lw=1)
            ax1.add_patch(mpatches.Rectangle((i-0.35, min(o, c)), 0.7, abs(c-o) or 0.01,
                                             facecolor=col, edgecolor=col))
        PINK = "#ff6ec4"
        ax1.axhline(op, color=PINK, ls="--", lw=2.2)
        # 灰底永远保留 (模板标配)
        if pm_h and pm_l:
            ax1.axhspan(pm_l, pm_h, color="#9ca3af", alpha=0.35)
        # 前高/前低线: 只画有参考价值的 (制图核心原则)
        day_lo = min(b[4] for b in bars)
        day_hi = max(b[3] for b in bars)
        if pm_h and _should_draw_level(pm_h, day_lo, day_hi, True):
            ax1.axhline(pm_h, color="#22c55e", ls="--", lw=1.4)
        if pm_l and _should_draw_level(pm_l, day_lo, day_hi, False):
            ax1.axhline(pm_l, color="#ef4444", ls="--", lw=1.4)
        if prev_h:
            ax1.axhline(prev_h, color="#22c55e", ls="--", lw=1.5)
        if prev_l:
            ax1.axhline(prev_l, color="#ef4444", ls="--", lw=1.5)
        if vwap_final:
            ax1.plot(x, (vwap_curve + [vwap_final]*n)[:n], color="#a855f7", ls="-", lw=1.8)
        _emphasize_round_levels(ax1)
        ax1.set_ylabel(f"{ticker} 价格 (USD)", color="#c9d1d9", fontsize=11)
        ax1.set_title(f"{ticker} {ds} 5分钟K线 — 开盘{op:.2f} ｜ 收{cl:.2f} ({chg:+.2f}%)",
                      color="#f0f6fc", fontsize=14, pad=10)
        legend_items = [(f"开盘 {op:.2f}", PINK)]
        if pm_h and pm_l:
            legend_items += [(f"盘前高 {pm_h:.2f}", "#22c55e"), (f"盘前低 {pm_l:.2f}", "#ef4444")]
        if vwap_final:
            legend_items.append((f"VWAP {vwap_final:.2f}", "#a855f7"))
        if prev_h:
            legend_items.append((f"前日高 {prev_h:.2f}", "#22c55e"))
        if prev_l:
            legend_items.append((f"前日低 {prev_l:.2f}", "#ef4444"))
        n_items = len(legend_items)
        for j, (txt, col) in enumerate(legend_items):
            x_pos = 0.06 + j * (0.88 / max(n_items - 1, 1))
            fig.text(x_pos, 0.02, txt, ha="center", color=col,
                     fontsize=11, weight="bold", fontproperties=_cjk)
        plt.tight_layout(rect=[0, 0.05, 1, 0.95])
        plt.savefig(os.path.join(out_dir, f"{ticker.lower()}_{ds}.png"), dpi=100, facecolor="#0d1117")
        plt.close()
        print(f"✓ {ticker} {ds}", flush=True)

if __name__ == "__main__":
    main()
