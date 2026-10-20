#!/usr/bin/env python3
"""ES / SPX 对照图 (上 ES 原始价, 下 SPX 现货, 同一时间轴对齐).
用途: 看 ES 是否先碰亚洲高低、先转向, SPX 随后跟随.
9 天 = SPX 单边 80 点名单 (主名单+擦边, 去重):
  2026-07-29, 07-31, 08-03, 08-04, 09-16, 09-21, 10-14, 10-15, 10-16
口径 (与生产 build_day.py 一致):
- EMA250: 5m 收盘, 前 250 根均值起算, k=2/251, 跨日续算 (仅常规时段 bar 入序)
- VWAP: 典型价 (h+l+c)/3 × ES 5m 成交量 (权重上限 12×中位数)
- SPX 早期 4 天无 report_data 的 EMA250/亚洲: 用 ES 值减当日平均 basis 近似
产出: spx-intraday/charts/es_spx_compare_<ds>.png, 并把图插入当日页主图上方.
"""
import csv, os, sys, json, math, datetime
from datetime import time
from zoneinfo import ZoneInfo
from collections import defaultdict
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import matplotlib.patches as mpatches
import matplotlib.font_manager as fm

ET = ZoneInfo("America/New_York")
WS = os.path.expanduser("~")
SITE = os.path.join(WS, "workspace", "spx-desk")
RAW = os.path.join(WS, "workspace", "trading_journal", "spx_daily", "raw_es")

DAYS = {
    "2026-07-29": "es_2026-07.csv",
    "2026-07-31": "es_2026-07.csv",
    "2026-08-03": "es_2026-08.csv",
    "2026-08-04": "es_2026-08.csv",
    "2026-09-16": "es_2026-09.csv",
    "2026-09-21": "es_2026-09z.csv",
    "2026-10-14": "es_2026-10.csv",
    "2026-10-15": "es_2026-10.csv",
    "2026-10-16": "es_2026-10.csv",
}

_cjk = fm.FontProperties(fname="/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc")
plt.rcParams["font.family"] = _cjk.get_name()
plt.rcParams["axes.unicode_minus"] = False


def load_es(fname):
    """返回 (by_date {ds: [1m bars]}, all_session_5m_closes [(ds, t, close)])"""
    by_date = defaultdict(list)
    with open(os.path.join(RAW, fname)) as fh:
        for row in csv.DictReader(fh):
            ts = int(row["ts_event"]) / 1e9
            dt = datetime.datetime.fromtimestamp(ts, tz=datetime.timezone.utc).astimezone(ET)
            by_date[dt.strftime("%Y-%m-%d")].append(
                (dt, int(row["open"]) / 1e9, int(row["high"]) / 1e9,
                 int(row["low"]) / 1e9, int(row["close"]) / 1e9, int(row["volume"])))
    return by_date


def agg_5m(day_bars):
    buckets = defaultdict(list)
    for dt, o, h, l, c, v in day_bars:
        if time(9, 30) <= dt.timetz() < time(16, 0):
            key = dt.replace(minute=(dt.minute // 5) * 5, second=0, microsecond=0)
            buckets[key].append((o, h, l, c, v))
    bars = []
    for key in sorted(buckets):
        bs = buckets[key]
        bars.append({"t": key.strftime("%H:%M"), "o": bs[0][0],
                     "h": max(b[1] for b in bs), "l": min(b[2] for b in bs),
                     "c": bs[-1][3], "v": sum(b[4] for b in bs)})
    return bars


def ema250_map(by_date, target_ds):
    """对全部日期的常规时段 5m 收盘续算 EMA250, 返回目标日 {t: ema}."""
    seq = []
    for ds in sorted(by_date):
        for b in agg_5m(by_date[ds]):
            seq.append((ds, b["t"], b["c"]))
    closes = [c for _, _, c in seq]
    if len(closes) < 260:
        return {}
    k = 2 / 251
    ema = sum(closes[:250]) / 250
    out = {}
    for (ds, t, c) in seq[250:]:
        ema = c * k + ema * (1 - k)
        if ds == target_ds:
            out[t] = ema
    return out


def vwap_curve(bars, weights_from=None):
    """bars: 目标标的 5m; weights_from: ES 5m bars (量). 返回 (curve, final)."""
    vols = sorted(b["v"] for b in weights_from if b["v"] > 0)
    med = vols[len(vols) // 2] if vols else 0
    cap = 12 * med if med else float("inf")
    wmap = {b["t"]: min(b["v"], cap) for b in weights_from}
    num = den = 0.0
    curve = []
    for b in bars:
        w = wmap.get(b["t"], 0)
        if w > 0:
            num += (b["h"] + b["l"] + b["c"]) / 3 * w
            den += w
        curve.append(num / den if den else None)
    return curve, (num / den if den else None)


def asian_es(by_date, ds):
    d = datetime.date.fromisoformat(ds)
    prev = (d - datetime.timedelta(days=1)).isoformat()
    ah = al = None
    for check_ds, is_prev in [(prev, True), (ds, False)]:
        for dt, o, h, l, c, v in by_date.get(check_ds, []):
            t = dt.timetz()
            if (is_prev and time(20, 0) <= t) or (not is_prev and t < time(6, 0)):
                ah = max(ah, h) if ah else h
                al = min(al, l) if al else l
    return ah, al


def should_draw(level, day_lo, day_hi, is_high):
    rng = day_hi - day_lo
    if rng <= 0:
        return True
    if is_high:
        return day_hi >= level * 0.999 or (level - day_hi) <= rng * 0.5
    return day_lo <= level * 1.001 or (day_lo - level) <= rng * 0.5


def emphasize(ax):
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


def draw_panel(ax, bars, vwap, ema_map, asian_h, asian_l, ylabel, title):
    n = len(bars)
    day_lo = min(b["l"] for b in bars)
    day_hi = max(b["h"] for b in bars)
    op = bars[0]["o"]
    ax.set_facecolor("white")
    ax.tick_params(colors="#333", labelsize=9)
    if asian_h and asian_l:
        ax.axhspan(asian_l, asian_h, color="#9ca3af", alpha=0.18)
        if should_draw(asian_h, day_lo, day_hi, True):
            ax.axhline(asian_h, color="#22c55e", ls="--", lw=1.4)
        if should_draw(asian_l, day_lo, day_hi, False):
            ax.axhline(asian_l, color="#ef4444", ls="--", lw=1.4)
    UP, DN = "#158353", "#c83c43"
    for i, b in enumerate(bars):
        col = UP if b["c"] >= b["o"] else DN
        ax.plot([i, i], [b["l"], b["h"]], color=col, lw=1)
        ax.add_patch(mpatches.Rectangle((i - 0.35, min(b["o"], b["c"])), 0.7,
                                        abs(b["c"] - b["o"]) or 0.01,
                                        facecolor=col, edgecolor=col))
    ax.axhline(op, color="#ff6ec4", ls="--", lw=1.8)
    xs = list(range(n))
    if vwap:
        last = next((v for v in reversed(vwap) if v), None)
        ax.plot(xs, [(v if v else last) for v in vwap], color="#a855f7", lw=1.8)
    ema_vals = [ema_map.get(b["t"]) for b in bars]
    if any(ema_vals):
        ax.plot(xs, ema_vals, color="#3b82f6", lw=1.5)
    emphasize(ax)
    ax.set_ylabel(ylabel, fontsize=11)
    ax.set_title(title, fontsize=12.5, pad=6)
    ticks = [i for i, b in enumerate(bars) if b["t"][3:] in ("00", "30")]
    ax.set_xticks(ticks)
    ax.set_xticklabels([bars[i]["t"] for i in ticks], fontsize=8)
    vf = next((v for v in reversed(vwap) if v), None) if vwap else None
    ef = next((v for v in reversed(ema_vals) if v), None)
    return [
        (f"开盘 {op:.1f}", "#ff6ec4"),
        (f"亚洲高 {asian_h:.1f}" if asian_h else "亚洲高 —", "#22c55e"),
        (f"亚洲低 {asian_l:.1f}" if asian_l else "亚洲低 —", "#ef4444"),
        (f"VWAP {vf:.1f}" if vf else "VWAP —", "#a855f7"),
        (f"EMA250 {ef:.1f}" if ef else "EMA250 —", "#3b82f6"),
    ]


def legend_row(fig, items, y):
    n = len(items)
    for j, (txt, col) in enumerate(items):
        x = 0.08 + j * (0.86 / max(n - 1, 1))
        fig.text(x, y, txt, ha="center", color=col, fontsize=10.5,
                 weight="bold", fontproperties=_cjk)


def spx_bars_for(ds):
    """优先本地 report_data, 否则 Yahoo 归档 json."""
    p = os.path.join(WS, "workspace", "trading_journal", "spx_daily", ds,
                     f"report_data_{ds}.json")
    if os.path.exists(p):
        d = json.load(open(p))
        kl = d.get("kline", {})
        bars = [{"t": b["t"], "o": b["o"], "h": b["h"], "l": b["l"], "c": b["c"]}
                for b in kl.get("bars", [])]
        if bars:
            vc = kl.get("vwap_curve")
            ec = kl.get("ema_curve")
            return (bars,
                    vc if isinstance(vc, list) else None,
                    ec if isinstance(ec, list) else None,
                    kl.get("approx_spx_h"), kl.get("approx_spx_l"))
    y = json.load(open(os.path.join(WS, "workspace", "spx_aug_yahoo5m.json")))
    raw = y.get(ds)
    if raw:
        bars = [{"t": b["t"], "o": b["o"], "h": b["h"], "l": b["l"], "c": b["c"]} for b in raw]
        return bars, None, None, None, None
    return None, None, None, None, None


def main():
    cache = {}
    for ds, fname in DAYS.items():
        if fname not in cache:
            cache[fname] = load_es(fname)
        by_date = cache[fname]
        es_bars = agg_5m(by_date.get(ds, []))
        if not es_bars:
            print(f"{ds}: ES 无数据, 跳过")
            continue
        es_ema = ema250_map(by_date, ds)
        es_vwap, _ = vwap_curve(es_bars, es_bars)
        es_ah, es_al = asian_es(by_date, ds)

        spx_bars, spx_vwap, spx_ema_list, spx_ah, spx_al = spx_bars_for(ds)
        if not spx_bars:
            print(f"{ds}: SPX 无数据, 跳过")
            continue
        if spx_vwap is None:
            spx_vwap, _ = vwap_curve(spx_bars, es_bars)
        es_by_t = {b["t"]: b for b in es_bars}
        spx_ema = {}
        if spx_ema_list:
            spx_ema = {b["t"]: v for b, v in zip(spx_bars, spx_ema_list) if v}
        if not spx_ema or spx_ah is None:
            basis = [es_by_t[b["t"]]["c"] - b["c"] for b in spx_bars if b["t"] in es_by_t]
            mb = sum(basis) / len(basis) if basis else 60.0
            if not spx_ema:
                spx_ema = {t: (v - mb) for t, v in es_ema.items()}
            if spx_ah is None and es_ah:
                spx_ah, spx_al = es_ah - mb, es_al - mb

        fig, (ax1, ax2) = plt.subplots(2, 1, figsize=(14, 10.5), sharex=True)
        fig.patch.set_facecolor("white")
        items1 = draw_panel(ax1, es_bars, es_vwap, es_ema, es_ah, es_al,
                            "ES", f"ES 期货（原始价） {ds}")
        items2 = draw_panel(ax2, spx_bars, spx_vwap, spx_ema, spx_ah, spx_al,
                            "SPX", f"SPX 现货 {ds}")
        fig.suptitle(f"ES / SPX 对照图 {ds}（同一时间轴：看 ES 是否先碰亚洲高低、先转向）",
                     fontsize=14, y=0.98)
        plt.tight_layout(rect=[0, 0.06, 1, 0.96], h_pad=3.0)
        legend_row(fig, items1, 0.475)
        legend_row(fig, items2, 0.012)
        out = os.path.join(SITE, "spx-intraday", "charts", f"es_spx_compare_{ds}.png")
        plt.savefig(out, dpi=100, facecolor="white")
        plt.close()
        print(f"✓ {ds} 对照图 (ES开{es_bars[0]['o']:.0f} / SPX开{spx_bars[0]['o']:.0f})")

        # 插入当日页主图上方 (幂等)
        page = os.path.join(SITE, "spx-intraday", f"{ds}.html")
        html = open(page, encoding="utf-8").read()
        if "es-spx-compare" not in html:
            block = (f'<p style="margin:18px 0 6px;font-weight:600">🔍 ES / SPX 对照图'
                     f'（上 ES · 下 SPX，同一时间轴）：看 ES 是否先碰亚洲高低、先转向</p>\n'
                     f'<figure class="chart-shell" id="es-spx-compare">'
                     f'<img src="charts/es_spx_compare_{ds}.png" '
                     f'alt="ES SPX 对照图 {ds}"></figure>\n')
            idx = html.find('<figure class="chart-shell">')
            if idx >= 0:
                html = html[:idx] + block + html[idx:]
                open(page, "w", encoding="utf-8").write(html)
                print(f"  已插入 {ds}.html")
            else:
                print(f"  ⚠️ {ds}.html 未找到主图 figure, 未插入")
        else:
            print(f"  {ds}.html 已有对照图, 跳过插入")

    sys.path.insert(0, os.path.join(SITE, "scripts"))
    from build_intraday import TICKERS
    from intraday_lib import build_index
    t = TICKERS["spx"]
    dates, _ = build_index(t["site"], t["name"], t["label"])
    print(f"索引重建: 共 {len(dates)} 天")


if __name__ == "__main__":
    main()
