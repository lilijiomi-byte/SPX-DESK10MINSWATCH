#!/usr/bin/env python3
"""MU 日内报告图表(通用日期版): 5m K线 + DTE heatmap, x轴对齐.
由 mu_report/build_charts.py (2026-10-14 已验证) 参数化而来, 绘图逻辑不变.
线型标准: 开盘=粉色虚线加粗, 盘前高=绿色虚线, 盘前低=红色虚线,
VWAP=紫色实线, EMA250=蓝色实线, call wall=浅金色实线, put wall=浅紫色实线.
heatmap: 夜航社配色 正=金(255,196,0) 负=紫(168,85,247),
alpha=0.16+0.84*sqrt(|v|/p99全天max), 白点=现货.

用法: build_chart_mu.py <YYYY-MM-DD> [call_wall] [put_wall]
输入 (~/workspace/mu_report/):
  yahoo_mu_<ds>.json, day_<ds>.json, day_<ds>_levels.json(含 vwap_curve/ema_curve/top5), heatmap 文件
输出: ~/workspace/mu_report/charts_v2/mu_charts_<MMDD>.png
"""
import json
import math
import os
import sys
import datetime
from zoneinfo import ZoneInfo
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import matplotlib.patches as mpatches

BASE = os.path.expanduser("~/workspace/mu_report")
ET = ZoneInfo("America/New_York")

DS = sys.argv[1]
MMDD = DS[5:7] + DS[8:10]
D = datetime.date.fromisoformat(DS)

cfg = json.load(open(f"{BASE}/day_{DS}.json"))
EXPIRY = cfg["expiry"]
HM_FILE = os.path.join(BASE, cfg["hm_file"])
DTE_LABEL = cfg.get("hm_label", "0DTE")

lv = json.load(open(f"{BASE}/day_{DS}_levels.json"))
OFFICIAL_OPEN = cfg.get("official_open") or lv["open"]
PM_H, PM_L = lv["premarket_H"], lv["premarket_L"]

if len(sys.argv) >= 4:
    CALL_WALL, PUT_WALL = float(sys.argv[2]), float(sys.argv[3])
else:
    CALL_WALL, PUT_WALL = cfg.get("call_wall"), cfg.get("put_wall")
    if CALL_WALL is None:
        CALL_WALL, PUT_WALL = lv.get("call_wall"), lv.get("put_wall")

# ---------- Yahoo 5m K ----------
d = json.load(open(f"{BASE}/yahoo_mu_{DS}.json"))["chart"]["result"][0]
ts, q = d["timestamp"], d["indicators"]["quote"][0]
bars = []
for i, t in enumerate(ts):
    dt = datetime.datetime.fromtimestamp(t, datetime.timezone.utc).astimezone(ET)
    if dt.strftime("%Y-%m-%d") != DS or q["close"][i] is None:
        continue
    # 只要常规时段 09:30-16:00 (15:55 为最后一根; 16:00 起为盘后)
    if not (datetime.time(9, 30) <= dt.timetz() < datetime.time(16, 0)):
        continue
    bars.append(dict(dt=dt, o=q["open"][i], h=q["high"][i], l=q["low"][i], c=q["close"][i]))
if not bars:
    sys.exit(f"build_chart_mu: {DS} 当日无 Yahoo 5m bar, 退出")

# ---------- 前日高低 (同一份 Yahoo 文件里的前一个交易日, 常规时段) ----------
def _should_draw_level(level, day_lo, day_hi, is_high):
    """制图核心原则: 只画有参考价值的线 (碰过或在附近<=50%日波幅);
    图例文字永远保留数值, 与线是否画出无关."""
    day_range = day_hi - day_lo
    if day_range <= 0:
        return True
    if is_high:
        return day_hi >= level * 0.999 or (level - day_hi) <= day_range * 0.5
    return day_lo <= level * 1.001 or (day_lo - level) <= day_range * 0.5

PREV_H, PREV_L = None, None
try:
    prev_day = None
    for t in ts:
        pds = datetime.datetime.fromtimestamp(t, datetime.timezone.utc).astimezone(ET).strftime("%Y-%m-%d")
        if pds < DS and (prev_day is None or pds > prev_day):
            prev_day = pds
    if prev_day:
        ph, pl = None, None
        for i, t in enumerate(ts):
            dt = datetime.datetime.fromtimestamp(t, datetime.timezone.utc).astimezone(ET)
            if dt.strftime("%Y-%m-%d") != prev_day or q["close"][i] is None:
                continue
            if not (datetime.time(9, 30) <= dt.timetz() < datetime.time(16, 0)):
                continue
            ph = max(ph, q["high"][i]) if ph is not None else q["high"][i]
            pl = min(pl, q["low"][i]) if pl is not None else q["low"][i]
        PREV_H, PREV_L = ph, pl
except Exception as e:
    print(f"build_chart_mu: 前日高低计算失败({e}), 跳过", flush=True)
day_lo = min(b["l"] for b in bars)
day_hi = max(b["h"] for b in bars)

# ---------- heatmap ----------
dd = json.load(open(HM_FILE))
snaps = sorted((s for s in dd["data"]["data"] if s["expiration"] == EXPIRY),
               key=lambda s: s["minute_at"])
if not snaps:
    sys.exit(f"build_chart_mu: {DS} heatmap 无 {EXPIRY} 快照, 退出")
_allv = sorted(abs(c["net_dealer_gex_usd"]) for s in snaps for c in s["cells"])
p99 = _allv[int(len(_allv) * 0.99)]
SCALEMAX = max(p99, 1.0)
print("scale_max_M:", round(SCALEMAX / 1e6, 1))
daymax = SCALEMAX
strikes = sorted({c["strike_usd"] for s in snaps for c in s["cells"]})
tot = {}
for s in snaps:
    for c in s["cells"]:
        tot[c["strike_usd"]] = tot.get(c["strike_usd"], 0) + abs(c["net_dealer_gex_usd"])
STRIKES10 = sorted(sorted(tot, key=lambda k: -tot[k])[:10])

GOLD = (255 / 255, 196 / 255, 0 / 255)
PURP = (168 / 255, 85 / 255, 247 / 255)


def hm_rgba(v):
    base = GOLD if v > 0 else PURP
    a = 0.16 + 0.84 * math.sqrt(min(abs(v) / daymax, 1.0))
    return (*base, a)


# ---------- curves / top5 ----------
VWAP_CURVE = lv["vwap_curve"]
EMA_CURVE = lv["ema_curve"]
TOP5 = lv["top5"]  # [{strike, max_gex_m, et}]

# ---------- draw ----------
import matplotlib.font_manager as fm
_cjk = fm.FontProperties(fname="/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc")
plt.rcParams["font.family"] = _cjk.get_name()
plt.rcParams["axes.unicode_minus"] = False


def _emphasize_round_levels(ax, force_round_ticks=False):
    """纵坐标逢整百/半百 (…00/…50) 的刻度加粗标红，作为提醒。
    force_round_ticks=True（仅价格轴）：把轴范围内所有 xx00/xx50 强制加为刻度，
    即使 matplotlib 自动刻度没选中它们。"""
    if force_round_ticks:
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
            v = float(lbl.get_text().replace(",", ""))
        except ValueError:
            continue
        if round(v) % 100 in (0, 50):
            lbl.set_color("#dc2626")
            lbl.set_weight("bold")
fig, (ax1, axmid, ax2) = plt.subplots(3, 1, figsize=(14, 10.8), sharex=True,
                                      gridspec_kw={"height_ratios": [3, 0.34, 2]})
fig.patch.set_facecolor("#0d1117")
for ax in (ax1, axmid, ax2):
    ax.set_facecolor("#0d1117")
    ax.tick_params(colors="#8b949e", labelsize=9)
    for spine in ax.spines.values():
        spine.set_color("#30363d")
axmid.set_axis_off()
axmid.set_facecolor("#131a23")
axmid.plot([0, 1], [1, 1], transform=axmid.transAxes, color="#3d444d", lw=1.4, clip_on=False)
axmid.plot([0, 1], [0, 0], transform=axmid.transAxes, color="#3d444d", lw=1.4, clip_on=False)

x = list(range(len(bars)))
ticks = [i for i, b in enumerate(bars) if b["dt"].minute % 30 == 0]
ax1.set_xticks(ticks)
ax1.set_xticklabels([bars[i]["dt"].strftime("%H:%M") for i in ticks], fontsize=8)
ax1.tick_params(labelbottom=True, colors="#8b949e")

UP, DN = "#22c55e", "#ef4444"
for i, b in enumerate(bars):
    col = UP if b["c"] >= b["o"] else DN
    ax1.plot([i, i], [b["l"], b["h"]], color=col, lw=1)
    ax1.add_patch(mpatches.Rectangle((i - 0.35, min(b["o"], b["c"])), 0.7,
                                     abs(b["c"] - b["o"]) or 0.01,
                                     facecolor=col, edgecolor=col))

ax1.axhline(OFFICIAL_OPEN, color="#ff6ec4", ls="--", lw=2.2)
if PM_H and PM_L:
    ax1.axhspan(PM_L, PM_H, color="#9ca3af", alpha=0.18)
ax1.axhline(PM_H, color="#22c55e", ls="--", lw=1.4)
ax1.axhline(PM_L, color="#ef4444", ls="--", lw=1.4)
# 前日高低: 智能显隐——K线扫过的范围内才画线 (SPX 格式: 绿/红虚线 lw=1.5);
# 太远不画线, 但图例文字保留数值
if PREV_H and _should_draw_level(PREV_H, day_lo, day_hi, True):
    ax1.axhline(PREV_H, color="#22c55e", ls="--", lw=1.5)
if PREV_L and _should_draw_level(PREV_L, day_lo, day_hi, False):
    ax1.axhline(PREV_L, color="#ef4444", ls="--", lw=1.5)
n = len(bars)
ax1.plot(x, (VWAP_CURVE + [VWAP_CURVE[-1]] * n)[:n], color="#a855f7", ls="-", lw=1.8)
ax1.plot(x, (EMA_CURVE + [EMA_CURVE[-1]] * n)[:n], color="#3b82f6", ls="-", lw=1.8)
if CALL_WALL:
    ax1.axhline(CALL_WALL, color="#ffd76a", ls="-", lw=2.4)
if PUT_WALL:
    ax1.axhline(PUT_WALL, color="#c4a5ff", ls="-", lw=2.4)

close = bars[-1]["c"]
chg = (close / OFFICIAL_OPEN - 1) * 100
ax1.set_ylabel("MU 价格 (USD)", color="#c9d1d9", fontsize=11)
ax1.set_title(f"MU {DS} 5分钟K线 (Yahoo Finance) — 开盘{OFFICIAL_OPEN:.2f} ｜ 收{close:.2f} ({chg:+.2f}%)",
              color="#f0f6fc", fontsize=14, pad=10)

row = [("开盘 %.2f" % OFFICIAL_OPEN, "#ff6ec4"), ("盘前高 %.2f" % PM_H, "#22c55e"),
       ("盘前低 %.2f" % PM_L, "#ef4444"), ("VWAP", "#a855f7"), ("EMA250", "#3b82f6")]
if CALL_WALL:
    row.append(("Call Wall %.0f" % CALL_WALL, "#ffd76a"))
if PUT_WALL:
    row.append(("Put Wall %.0f" % PUT_WALL, "#c4a5ff"))
# 图例永远保留前日高低数值, 无论线画没画出来
if PREV_H:
    row.append(("前日高 %.2f" % PREV_H, "#22c55e"))
if PREV_L:
    row.append(("前日低 %.2f" % PREV_L, "#ef4444"))
for j, (txt, col) in enumerate(row):
    axmid.text(0.065 + j * (0.87 / max(len(row) - 1, 1)), 0.5, txt, color=col, fontsize=11,
               weight="bold", ha="center", va="center", transform=axmid.transAxes)


def bar_idx_of(minute_at):
    dt = datetime.datetime.fromisoformat(minute_at.replace("Z", "+00:00")).astimezone(ET)
    for i, b in enumerate(bars):
        if b["dt"] >= dt:
            return i
    return len(bars) - 1


col_w = 1.0
for s in snaps:
    bi = bar_idx_of(s["minute_at"])
    for c in s["cells"]:
        k = c["strike_usd"]
        if k not in STRIKES10:
            continue
        yi = STRIKES10.index(k)
        ax2.add_patch(mpatches.Rectangle((bi - 0.5, yi - 0.5), col_w, 1.0,
                                         facecolor=hm_rgba(c["net_dealer_gex_usd"]),
                                         edgecolor="none"))
    ax2.plot(bi, __import__("numpy").interp(s["spot_usd"], STRIKES10, range(len(STRIKES10))),
             marker="o", color="white", ms=3.5, zorder=5)

ax2.set_yticks(range(len(STRIKES10)))
ax2.set_yticklabels([f"{k:.0f}" for k in STRIKES10])
ax2.set_ylabel("Strike", color="#c9d1d9", fontsize=11)
ax2.set_title(f"{DTE_LABEL} dealer-GEX heatmap (夜航社, 5分钟快照, 金=正/紫=负, 白点=现货, 红框=全天该strike最大|GEX|时刻)",
              color="#f0f6fc", fontsize=13, pad=8)

bar_by_hm = {b["dt"].strftime("%H:%M"): i for i, b in enumerate(bars)}
for e in TOP5:
    bi = bar_by_hm.get(e["et"])
    if bi is None or e["strike"] not in STRIKES10:
        continue
    yi = STRIKES10.index(e["strike"])
    ax2.add_patch(mpatches.Rectangle((bi - 0.5, yi - 0.5), col_w, 1.0,
                                     facecolor="none", edgecolor="#ff2d2d", lw=2.2, zorder=6))
    ax2.plot(bi, len(STRIKES10) - 0.35, marker="v", color="#ff2d2d", ms=9, zorder=7)

ax2.set_xticks(ticks)
ax2.set_xticklabels([bars[i]["dt"].strftime("%H:%M") for i in ticks])
ax2.set_xlabel("时间 (ET)", color="#c9d1d9", fontsize=11)
ax2.set_xlim(-0.6, len(bars) - 0.4)

fig.tight_layout(h_pad=1.2)
fig.canvas.draw()  # 先渲染生成刻度
_emphasize_round_levels(ax1, force_round_ticks=True)
_emphasize_round_levels(ax2)
out = f"{BASE}/charts_v2/mu_charts_{MMDD}.png"
fig.savefig(out, dpi=150, facecolor=fig.get_facecolor())
print("saved", out)
