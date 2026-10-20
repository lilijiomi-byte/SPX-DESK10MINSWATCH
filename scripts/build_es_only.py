#!/usr/bin/env python3
"""ES 单图 (只画 ES, 不含 SPX): 放在 SPX 日内页主图上方作先行参考.
内容: ES 5m K线 + 9:30开盘线 + VWAP + EMA250 + 亚洲高低灰底/线 + 彩色图例.
口径与 build_es_spx_compare.py 相同 (复用其 helper).
同时把 9 个日页里旧的对照图块替换为 ES 参考图块.
"""
import os, sys
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import matplotlib.patches as mpatches

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import build_es_spx_compare as C

SITE = C.SITE


def draw_es_chart(ds, by_date):
    es_bars = C.agg_5m(by_date.get(ds, []))
    if not es_bars:
        return False
    es_ema = C.ema250_map(by_date, ds)
    es_vwap, _ = C.vwap_curve(es_bars, es_bars)
    ah, al = C.asian_es(by_date, ds)
    n = len(es_bars)
    day_lo = min(b["l"] for b in es_bars)
    day_hi = max(b["h"] for b in es_bars)
    op = es_bars[0]["o"]
    cl = es_bars[-1]["c"]
    chg = (cl / op - 1) * 100

    fig, ax = plt.subplots(1, 1, figsize=(14, 7))
    fig.patch.set_facecolor("white")
    ax.set_facecolor("white")
    ax.tick_params(colors="#333", labelsize=9)
    if ah and al:
        ax.axhspan(al, ah, color="#9ca3af", alpha=0.18)
        if C.should_draw(ah, day_lo, day_hi, True):
            ax.axhline(ah, color="#22c55e", ls="--", lw=1.4)
        if C.should_draw(al, day_lo, day_hi, False):
            ax.axhline(al, color="#ef4444", ls="--", lw=1.4)
    UP, DN = "#158353", "#c83c43"
    for i, b in enumerate(es_bars):
        col = UP if b["c"] >= b["o"] else DN
        ax.plot([i, i], [b["l"], b["h"]], color=col, lw=1)
        ax.add_patch(mpatches.Rectangle((i - 0.35, min(b["o"], b["c"])), 0.7,
                                        abs(b["c"] - b["o"]) or 0.01,
                                        facecolor=col, edgecolor=col))
    ax.axhline(op, color="#ff6ec4", ls="--", lw=1.8)
    xs = list(range(n))
    vf = next((v for v in reversed(es_vwap) if v), None)
    ax.plot(xs, [(v if v else vf) for v in es_vwap], color="#a855f7", lw=1.8)
    ema_vals = [es_ema.get(b["t"]) for b in es_bars]
    if any(ema_vals):
        ax.plot(xs, ema_vals, color="#3b82f6", lw=1.5)
    C.emphasize(ax)
    ax.set_ylabel("ES", fontsize=11)
    ax.set_title(f"ES 期货 {ds} 5分钟K线（原始价） — 开盘{op:.2f} | 收{cl:.2f} ({chg:+.2f}%)",
                 fontsize=13.5, pad=10)
    ticks = [i for i, b in enumerate(es_bars) if b["t"][3:] in ("00", "30")]
    ax.set_xticks(ticks)
    ax.set_xticklabels([es_bars[i]["t"] for i in ticks], fontsize=8)
    ef = next((v for v in reversed(ema_vals) if v), None)
    items = [(f"开盘 {op:.1f}", "#ff6ec4"),
             (f"亚洲高 {ah:.1f}" if ah else "亚洲高 —", "#22c55e"),
             (f"亚洲低 {al:.1f}" if al else "亚洲低 —", "#ef4444"),
             (f"VWAP {vf:.1f}" if vf else "VWAP —", "#a855f7"),
             (f"EMA250 {ef:.1f}" if ef else "EMA250 —", "#3b82f6")]
    plt.tight_layout(rect=[0, 0.05, 1, 0.96])
    for j, (txt, col) in enumerate(items):
        x = 0.08 + j * (0.86 / max(len(items) - 1, 1))
        fig.text(x, 0.012, txt, ha="center", color=col, fontsize=11,
                 weight="bold", fontproperties=C._cjk)
    out = os.path.join(SITE, "spx-intraday", "charts", f"es_{ds}.png")
    plt.savefig(out, dpi=100, facecolor="white")
    plt.close()
    return True


def main():
    cache = {}
    for ds, fname in C.DAYS.items():
        if fname not in cache:
            cache[fname] = C.load_es(fname)
        by_date = cache[fname]
        if not draw_es_chart(ds, by_date):
            print(f"{ds}: ES 无数据, 跳过")
            continue
        page = os.path.join(SITE, "spx-intraday", f"{ds}.html")
        html = open(page, encoding="utf-8").read()
        start = html.find('<p style="margin:18px 0 6px;font-weight:600">🔍 ES / SPX 对照图')
        if start < 0:
            print(f"{ds}: 未找到旧对照图块, 跳过替换")
            continue
        end = html.find("</figure>", start) + len("</figure>")
        block = (f'<p style="margin:18px 0 6px;font-weight:600">🔍 ES 参考图'
                 f'（期货先行指标）：看 ES 是否先碰亚洲高低、先转向</p>\n'
                 f'<figure class="chart-shell" id="es-ref">'
                 f'<img src="charts/es_{ds}.png" alt="ES 期货K线图 {ds}"></figure>')
        html = html[:start] + block + html[end:]
        open(page, "w", encoding="utf-8").write(html)
        print(f"✓ {ds} ES 单图 + 页面已替换")

    sys.path.insert(0, os.path.join(SITE, "scripts"))
    from build_intraday import TICKERS
    from intraday_lib import build_index
    t = TICKERS["spx"]
    dates, _ = build_index(t["site"], t["name"], t["label"])
    print(f"索引重建: 共 {len(dates)} 天")


if __name__ == "__main__":
    main()
