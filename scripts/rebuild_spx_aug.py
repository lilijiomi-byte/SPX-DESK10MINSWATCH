#!/usr/bin/env python3
"""SPX 8月图表: ES换算 + 智能显隐 + 模板标配"""
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
BASIS = 60  # ES-60 = SPX

def _should_draw_level(level, day_lo, day_hi, is_high):
    """制图核心原则: 只画有参考价值的线."""
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

def load_es(csv_path):
    """加载 ES 1m, 返回 {date: [(dt, o, h, l, c, v)]} (ET)"""
    by_date = defaultdict(list)
    with open(csv_path) as fh:
        for row in csv.DictReader(fh):
            ts = int(row["ts_event"]) / 1e9
            dt = datetime.datetime.fromtimestamp(ts, tz=datetime.timezone.utc).astimezone(ET)
            ds = dt.strftime("%Y-%m-%d")
            # ES 换算 SPX: -60
            o = int(row["open"])/1e9 - BASIS
            h = int(row["high"])/1e9 - BASIS
            l = int(row["low"])/1e9 - BASIS
            c = int(row["close"])/1e9 - BASIS
            v = int(row["volume"])
            by_date[ds].append((dt, o, h, l, c, v))
    return by_date

def main():
    by_date = load_es("/tmp/databento/es_aug.csv")
    out_dir = os.path.join(SITE, "spx-intraday", "charts")
    
    for ds in DATES:
        # 当日 09:30-16:00 5m bars
        day_bars = [b for b in by_date.get(ds, [])
                    if time(9, 30) <= b[0].timetz() < time(16, 0)]
        if not day_bars:
            print(f"SPX {ds}: 无数据", flush=True)
            continue
        # 聚合成 5m
        buckets = defaultdict(list)
        for dt, o, h, l, c, v in day_bars:
            key = dt.replace(minute=(dt.minute//5)*5, second=0, microsecond=0)
            buckets[key].append((o, h, l, c, v))
        bars = []
        for key in sorted(buckets):
            bs = buckets[key]
            bars.append((key, bs[0][0], max(b[1] for b in bs),
                        min(b[2] for b in bs), bs[-1][3], sum(b[4] for b in bs)))
        
        # 亚洲高低 (20:00-06:00 ET, 用 ES 换算)
        d = datetime.date.fromisoformat(ds)
        asian_h, asian_l = None, None
        # 前一日 20:00 到当日 06:00
        for check_ds in [(d - datetime.timedelta(days=1)).isoformat(), ds]:
            for dt, o, h, l, c, v in by_date.get(check_ds, []):
                t = dt.timetz()
                # 20:00-23:59 (前一日) 或 00:00-06:00 (当日)
                is_asian = (check_ds != ds and time(20, 0) <= t) or \
                           (check_ds == ds and t < time(6, 0))
                if is_asian:
                    asian_h = max(asian_h, h) if asian_h else h
                    asian_l = min(asian_l, l) if asian_l else l
        
        # 前日高低 (前一个交易日全天)
        prev_h, prev_l = None, None
        pd = d - datetime.timedelta(days=1)
        for _ in range(5):
            pds = pd.isoformat()
            pb = [b for b in by_date.get(pds, [])
                  if time(9, 30) <= b[0].timetz() < time(16, 0)]
            if pb:
                prev_h = max(b[3] for b in pb)
                prev_l = min(b[4] for b in pb)
                break
            pd -= datetime.timedelta(days=1)
        
        # VWAP (ES成交量)
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
        day_lo = min(b[4] for b in bars)
        day_hi = max(b[3] for b in bars)
        
        # 画图 (白色底)
        fig, ax1 = plt.subplots(1, 1, figsize=(14, 7.5))
        fig.patch.set_facecolor("white")
        ax1.set_facecolor("white")
        ax1.tick_params(colors="#333", labelsize=9)
        x = list(range(n))
        ticks = [i for i, (dt, _, _, _, _, _) in enumerate(bars) if dt.minute % 30 == 0]
        ax1.set_xticks(ticks)
        ax1.set_xticklabels([bars[i][0].strftime("%H:%M") for i in ticks], fontsize=8)
        UP, DN = "#158353", "#c83c43"
        for i, (dt, o, h, l, c, v) in enumerate(bars):
            col = UP if c >= o else DN
            ax1.plot([i, i], [l, h], color=col, lw=1)
            ax1.add_patch(mpatches.Rectangle((i-0.35, min(o, c)), 0.7, abs(c-o) or 0.01,
                                             facecolor=col, edgecolor=col))
        PINK = "#ff6ec4"
        ax1.axhline(op, color=PINK, ls="--", lw=2.0)
        # 亚洲灰底永远保留
        if asian_h and asian_l:
            ax1.axhspan(asian_l, asian_h, color="#9ca3af", alpha=0.18)
        # 亚洲高低线: 智能显隐
        if asian_h and _should_draw_level(asian_h, day_lo, day_hi, True):
            ax1.axhline(asian_h, color="#22c55e", ls="--", lw=1.4)
        if asian_l and _should_draw_level(asian_l, day_lo, day_hi, False):
            ax1.axhline(asian_l, color="#ef4444", ls="--", lw=1.4)
        # 前日高低线: 永远画 (用户要求清晰可见)
        if prev_h:
            ax1.axhline(prev_h, color="#22c55e", ls="--", lw=1.5)
        if prev_l:
            ax1.axhline(prev_l, color="#ef4444", ls="--", lw=1.5)
        if vwap_final:
            ax1.plot(x, (vwap_curve + [vwap_final]*n)[:n], color="#a855f7", ls="-", lw=1.8)
        _emphasize_round_levels(ax1)
        ax1.set_ylabel("SPX", fontsize=11)
        ax1.set_title(f"SPX {ds} 5分钟K线 (ES换算) — 开盘{op:.2f} | 收{cl:.2f} ({chg:+.2f}%)",
                      fontsize=14, pad=10)
        # 彩色图例
        legend_items = [(f"开盘 {op:.2f}", PINK)]
        if asian_h and asian_l:
            legend_items += [(f"亚洲前高≈{asian_h:.1f}", "#22c55e"),
                           (f"亚洲前低≈{asian_l:.1f}", "#ef4444")]
        if vwap_final:
            legend_items.append((f"VWAP {vwap_final:.2f}", "#a855f7"))
        if prev_h:
            legend_items.append((f"前日高 {prev_h:.1f}", "#22c55e"))
        if prev_l:
            legend_items.append((f"前日低 {prev_l:.1f}", "#ef4444"))
        n_items = len(legend_items)
        for j, (txt, col) in enumerate(legend_items):
            x_pos = 0.06 + j * (0.88 / max(n_items - 1, 1))
            fig.text(x_pos, 0.02, txt, ha="center", color=col,
                     fontsize=11, weight="bold", fontproperties=_cjk)
        plt.tight_layout(rect=[0, 0.05, 1, 0.95])
        plt.savefig(os.path.join(out_dir, f"spx_{ds}.png"), dpi=100, facecolor="white")
        plt.close()
        print(f"✓ SPX {ds}", flush=True)

if __name__ == "__main__":
    main()
