#!/usr/bin/env python3
"""SPX 2026 年 7 月回补 (7/22-7/31): 与八月回补同一套方法.
数据源: Databento ESU6 1m (GLBX.MDP3 ohlcv-1m), ES-60 换算 SPX.
输入 CSV: ~/workspace/trading_journal/spx_daily/raw_es/es_2026-07.csv
  (拉取窗口 2026-07-21T00:00Z -> 2026-08-01T00:00Z, 含 7/21 全天与 7/31 全天)
产出: 站点图表 spx-intraday/charts/spx_<ds>.png + 独立页 spx-intraday/<ds>.html
  (页面外壳与八月回补页完全一致), 最后自动 build_index.
规则: 亚洲灰底永远保留; 亚洲/前日线智能显隐 (碰过或距当日波幅<=50%才画),
  图例文字永远保留全部数值; 前日线绿/红虚线 lw=1.5 (SPX 格式).
"""
import csv, os, sys, math, datetime, re
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
CSV_PATH = os.path.expanduser("~/workspace/trading_journal/spx_daily/raw_es/es_2026-07.csv")
DATES = ["2026-07-22", "2026-07-23", "2026-07-24", "2026-07-27",
         "2026-07-28", "2026-07-30", "2026-07-31"]
BASIS = 60
WEEK_CN = ["周一", "周二", "周三", "周四", "周五", "周六", "周日"]

_cjk = fm.FontProperties(fname="/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc")
plt.rcParams["font.family"] = _cjk.get_name()
plt.rcParams["axes.unicode_minus"] = False


def _should_draw_level(level, day_lo, day_hi, is_high):
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
    by_date = defaultdict(list)
    with open(csv_path) as fh:
        for row in csv.DictReader(fh):
            ts = int(row["ts_event"]) / 1e9
            dt = datetime.datetime.fromtimestamp(ts, tz=datetime.timezone.utc).astimezone(ET)
            ds = dt.strftime("%Y-%m-%d")
            o = int(row["open"]) / 1e9 - BASIS
            h = int(row["high"]) / 1e9 - BASIS
            l = int(row["low"]) / 1e9 - BASIS
            c = int(row["close"]) / 1e9 - BASIS
            v = int(row["volume"])
            by_date[ds].append((dt, o, h, l, c, v))
    return by_date


def session_5m(day_bars):
    buckets = defaultdict(list)
    for dt, o, h, l, c, v in day_bars:
        if time(9, 30) <= dt.timetz() < time(16, 0):
            key = dt.replace(minute=(dt.minute // 5) * 5, second=0, microsecond=0)
            buckets[key].append((o, h, l, c, v))
    bars = []
    for key in sorted(buckets):
        bs = buckets[key]
        bars.append((key, bs[0][0], max(b[1] for b in bs),
                     min(b[2] for b in bs), bs[-1][3], sum(b[4] for b in bs)))
    return bars


def page_shell():
    """从八月回补页提取外壳 (head+style), 保证格式完全一致."""
    src = open(os.path.join(SITE, "spx-intraday", "2026-08-03.html"), encoding="utf-8").read()
    head = src.split('<article class="day-panel"')[0]
    head = re.sub(r"<title>.*?</title>", "<title>SPX 日内报告 {ds}</title>", head, flags=re.S)
    # head 里在 article 前还有 <main> 等结构: 只取到 <main> 内 article 之前
    return head


def main():
    if not os.path.exists(CSV_PATH):
        sys.exit(f"缺少 ES 数据: {CSV_PATH} (先用 Databento 拉 7/21-8/1 ESU6 1m)")
    by_date = load_es(CSV_PATH)
    out_dir = os.path.join(SITE, "spx-intraday", "charts")
    head_tpl = page_shell()

    for ds in DATES:
        raw = by_date.get(ds, [])
        bars = session_5m(raw)
        if not bars:
            print(f"SPX {ds}: 无数据, 跳过", flush=True)
            continue
        d = datetime.date.fromisoformat(ds)
        # 亚洲高低 (前一日 20:00 -> 当日 06:00 ET)
        asian_h, asian_l = None, None
        for check_ds in [(d - datetime.timedelta(days=1)).isoformat(), ds]:
            for dt, o, h, l, c, v in by_date.get(check_ds, []):
                t = dt.timetz()
                is_asian = (check_ds != ds and time(20, 0) <= t) or \
                           (check_ds == ds and t < time(6, 0))
                if is_asian:
                    asian_h = max(asian_h, h) if asian_h else h
                    asian_l = min(asian_l, l) if asian_l else l
        # 前日高低 (前一交易日全天常规时段)
        prev_h = prev_l = None
        pd = d - datetime.timedelta(days=1)
        for _ in range(5):
            pb = session_5m(by_date.get(pd.isoformat(), []))
            if pb:
                prev_h = max(b[2] for b in pb)
                prev_l = min(b[3] for b in pb)
                break
            pd -= datetime.timedelta(days=1)
        # VWAP
        num, den, vwap_curve = 0.0, 0.0, []
        for _, o, h, l, c, v in bars:
            if v > 0:
                tp = (h + l + c) / 3
                num += tp * v
                den += v
            vwap_curve.append(num / den if den else None)
        vwap_final = next((x for x in reversed(vwap_curve) if x), None)

        n = len(bars)
        op, cl = bars[0][1], bars[-1][4]
        chg = (cl / op - 1) * 100
        day_lo = min(b[3] for b in bars)
        day_hi = max(b[2] for b in bars)

        # ---------- 图 (白色底, 与八月回补同规格) ----------
        fig, ax1 = plt.subplots(1, 1, figsize=(14, 7.5))
        fig.patch.set_facecolor("white")
        ax1.set_facecolor("white")
        ax1.tick_params(colors="#333", labelsize=9)
        x = list(range(n))
        ticks = [i for i, (dt, *_r) in enumerate(bars) if dt.minute % 30 == 0]
        ax1.set_xticks(ticks)
        ax1.set_xticklabels([bars[i][0].strftime("%H:%M") for i in ticks], fontsize=8)
        UP, DN = "#158353", "#c83c43"
        for i, (dt, o, h, l, c, v) in enumerate(bars):
            col = UP if c >= o else DN
            ax1.plot([i, i], [l, h], color=col, lw=1)
            ax1.add_patch(mpatches.Rectangle((i - 0.35, min(o, c)), 0.7, abs(c - o) or 0.01,
                                             facecolor=col, edgecolor=col))
        ax1.axhline(op, color="#ff6ec4", ls="--", lw=2.0)
        if asian_h and asian_l:
            ax1.axhspan(asian_l, asian_h, color="#9ca3af", alpha=0.18)
            if _should_draw_level(asian_h, day_lo, day_hi, True):
                ax1.axhline(asian_h, color="#22c55e", ls="--", lw=1.4)
            if _should_draw_level(asian_l, day_lo, day_hi, False):
                ax1.axhline(asian_l, color="#ef4444", ls="--", lw=1.4)
        if prev_h and _should_draw_level(prev_h, day_lo, day_hi, True):
            ax1.axhline(prev_h, color="#22c55e", ls="--", lw=1.5)
        if prev_l and _should_draw_level(prev_l, day_lo, day_hi, False):
            ax1.axhline(prev_l, color="#ef4444", ls="--", lw=1.5)
        if vwap_final:
            ax1.plot(x, [(v if v else vwap_final) for v in vwap_curve],
                     color="#a855f7", ls="-", lw=1.8)
        _emphasize_round_levels(ax1)
        ax1.set_ylabel("SPX", fontsize=11)
        ax1.set_title(f"SPX {ds} 5分钟K线 (ES换算) — 开盘{op:.2f} | 收{cl:.2f} ({chg:+.2f}%)",
                      fontsize=14, pad=10)
        legend_items = [(f"开盘 {op:.2f}", "#ff6ec4")]
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

        # ---------- 页 (八月回补同款简化格式) ----------
        cls = "up" if chg >= 0 else "down"
        mmdd = ds[5:7] + ds[8:10]
        asian_line = (f"<p>亚洲前高≈{asian_h:.1f} ｜ 亚洲前低≈{asian_l:.1f}（ES换算）</p>"
                      if asian_h else "")
        vwap_line = f"<p>VWAP {vwap_final:.2f}</p>" if vwap_final else ""
        prev_line = (f"<p>前日高 {prev_h:.1f} ｜ 前日低 {prev_l:.1f}</p>"
                     if prev_h else "")
        article = f"""<article class="day-panel" id="day-{mmdd}">
<header class="mast">
<h1>SPX</h1>
<p class="date">{ds} · {WEEK_CN[d.weekday()]} · 日内报告</p>
<div class="tape"><strong>{cl:.2f}</strong> <span class="{cls}">({chg:+.2f}%)</span></div>
</header>
<figure class="chart-shell"><img src="charts/spx_{ds}.png" alt="SPX {ds} 5分钟K线图"></figure>
<div class="info">
<p>开盘 {op:.2f} ｜ 最高 {day_hi:.2f} ｜ 最低 {day_lo:.2f} ｜ 收盘 {cl:.2f} ({chg:+.2f}%)</p>
{asian_line}
{vwap_line}
{prev_line}
<p style="margin-top:12px">注：本日 K 线为 ES 期货 1 分钟数据按 ES-60 换算为 SPX；亚洲高低为 ES 20:00-06:00 ET 换算；无 heatmap 快照（数据已过期）。</p>
</div>
<p class="data-note">历史数据库：每日收盘后追加当日报告，历史日期永久保留。</p>
</article>"""
        head = head_tpl.replace("{ds}", ds)
        html = head + article + "\n</main>\n</body>\n</html>\n"
        with open(os.path.join(SITE, "spx-intraday", f"{ds}.html"), "w", encoding="utf-8") as fh:
            fh.write(html)
        print(f"✓ SPX {ds} (开{op:.0f} 高{day_hi:.0f} 低{day_lo:.0f} 收{cl:.0f} {chg:+.2f}%)", flush=True)

    # 重建索引
    sys.path.insert(0, os.path.join(SITE, "scripts"))
    from build_intraday import TICKERS
    from intraday_lib import build_index
    t = TICKERS["spx"]
    dates, _ = build_index(t["site"], t["name"], t["label"])
    print(f"索引重建: 共 {len(dates)} 天, 最新 {dates[0]}, 最早 {dates[-1]}", flush=True)


if __name__ == "__main__":
    main()
