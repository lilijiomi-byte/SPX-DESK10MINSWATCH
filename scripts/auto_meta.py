#!/usr/bin/env python3
"""TSLA 每日报告 auto-meta 生成器.
在 gen_reads_daily.py 之后、assemble_daily_generic.py 之前运行,
自动生成原来需要手工填写的 meta_<ds>.json:
  junkman (文件查找→pending占位, 不编造) / event / magnet / flow_verdict /
  summary (事实型①-④) / top5_analysis (规则型三段式初稿)

用法: auto_meta.py <YYYY-MM-DD>
"""
import json
import os
import re
import sys
import datetime
from zoneinfo import ZoneInfo

BASE = os.path.expanduser("~/workspace/trading_journal/tsla_daily")
sys.path.insert(0, os.path.expanduser("~/workspace/spx-desk/scripts"))
from intraday_lib import junkman_lookup, event_lookup

DS = sys.argv[1]
D = os.path.join(BASE, DS)
ET = ZoneInfo("America/New_York")

day = json.load(open(f"{D}/day_{DS}.json"))
reads = json.load(open(f"{D}/day_{DS}_reads.json"))
flow = json.load(open(f"{D}/flow_TSLA_{DS}.json"))["trades"]
hm = json.load(open(f"{D}/heatmap_TSLA_{DS}.json"))


def _hm_rows_all():
    return hm.get("rows") or ((hm.get("data") or {}).get("data") or [])

opx, hi, lo, cl = day["official_open"], day["day_H"], day["day_L"], day["close"]
chg = day.get("chg_pct", (cl / opx - 1) * 100)

# ---------- junkman ----------
status, meta_line, full_text = junkman_lookup("tsla", DS)
posted = meta_line.split(" MDT")[0].split()[-1] if status == "ready" and "MDT" in meta_line else ""
junkman = {"status": status}
if status == "ready":
    junkman.update({"posted_mdt": posted, "full_text": full_text})

# ---------- event ----------
is_event, tag, detail, bias = event_lookup(DS, chg)
event = {"is_event": is_event, "tag": tag, "detail": detail, "bias": bias}

# ---------- magnet ----------
magnet_strike, magnet_src = None, "heatmap"
try:
    sys.path.insert(0, os.path.expanduser("~/workspace/yhs_premarket/daily"))
    from lib import event_day  # noqa
    import json as _j
    bj = _j.load(open(os.path.expanduser(f"~/workspace/yhs_premarket/daily/data/{DS}/junkman_brief.json")))
    b = bj.get("TSLA", {})
    if b.get("magnet"):
        magnet_strike, magnet_src = b["magnet"], "junk-man"
except Exception:
    pass
if magnet_strike is None:
    # heatmap 全天最大正 GEX strike 兜底
    tot = {}
    for s in _hm_rows_all():
        for c in s["cells"]:
            v = c["net_dealer_gex_usd"]
            if v > 0:
                tot[c["strike_usd"]] = tot.get(c["strike_usd"], 0) + v
    if tot:
        magnet_strike = max(tot, key=tot.get)

# ---------- flow_verdict ----------
def _strike_of(t):
    if t.get("strike"):
        return t["strike"]
    c = t.get("contract") or ""
    m = re.search(r"([CP])(\d{8})$", c)
    return int(m.group(2)) / 1000 if m else 0


def moneyness(strike, spot, right):
    if spot <= 0:
        return ""
    r = strike / spot
    if 0.995 <= r <= 1.005:
        return "ATM"
    if right == "call":
        return "OTM" if r > 1.005 else "ITM"
    return "OTM" if r < 0.995 else "ITM"


buckets = {"call_ask": 0, "call_bid": 0, "call_mid": 0,
           "put_ask": 0, "put_bid": 0, "put_mid": 0, "n": 0}
ask_lines, bid_lines = [], []
for t in flow:
    if t.get("kind") != "order" and "kind" in t:
        continue
    side_raw = t.get("side")
    if side_raw not in ("at_bid", "at_ask", "mid", "B", "M", "A"):
        continue
    dte = t.get("min_dte")
    if dte is None or dte > 1:
        continue
    prem = t.get("premium_usd", 0) or 0
    if prem < 200_000:
        continue
    if (t.get("contracts") or t.get("size") or 0) < 50:
        continue
    side = {"at_bid": "bid", "mid": "mid", "at_ask": "ask",
            "B": "bid", "M": "mid", "A": "ask"}[side_raw]
    right = (t.get("right") or "").lower()
    right = "call" if right.startswith("c") else "put"
    buckets[f"{right}_{side}"] += prem
    buckets["n"] += 1
    try:
        dt = datetime.datetime.fromisoformat(t["occurred_at"].replace("Z", "+00:00")).astimezone(ET)
        tm = dt.strftime("%H:%M")
        spot = t.get("underlying_price_usd") or cl
        sk = _strike_of(t)
        mn = moneyness(sk, spot, right)
        line = (f'{tm} {right} {side} {sk:.0f}{mn} '
                f'${prem/1e6:.2f}M×{t.get("contracts") or t.get("size")}')
        (ask_lines if side == "ask" else bid_lines).append((line, prem))
    except Exception:
        pass
bull = buckets["call_ask"] + buckets["put_bid"]
bear = buckets["put_ask"] + buckets["call_bid"]
mid_m = (buckets["call_mid"] + buckets["put_mid"]) / 1e6
if bull > bear * 1.2:
    lean = "偏多"
elif bear > bull * 1.2:
    lean = "偏空"
else:
    lean = "中性"
top_ask = sorted(ask_lines, key=lambda x: -x[1])[:3]
top_bid = sorted(bid_lines, key=lambda x: -x[1])[:3]
flow_verdict = (
    f"全天{buckets['n']}笔：call ask ${buckets['call_ask']/1e6:.2f}M / call bid ${buckets['call_bid']/1e6:.2f}M，"
    f"put ask ${buckets['put_ask']/1e6:.2f}M / put bid ${buckets['put_bid']/1e6:.2f}M，"
    f"mid ${mid_m:.2f}M（不计入主动买卖）；主动资金{lean}。"
    + (f" 主动买前列：{'; '.join(x[0] for x in top_ask)}。" if top_ask else "")
    + (f" 主动卖前列：{'; '.join(x[0] for x in top_bid)}。" if top_bid else "")
)

# ---------- summary (事实型) ----------
cw = day.get("call_wall") or (day.get("walls") or {}).get("call_wall")
pw = day.get("put_wall") or (day.get("walls") or {}).get("put_wall")
wall_pos = ""
if cw and pw:
    if opx > cw:
        wall_pos = f"开盘 {opx:.2f} 在 call wall {cw:.0f} 上方"
    elif opx < pw:
        wall_pos = f"开盘 {opx:.2f} 在 put wall {pw:.0f} 下方"
    else:
        wall_pos = f"开盘 {opx:.2f} 在双墙 {pw:.0f}–{cw:.0f} 之间"
kr = reads.get("key_reads", [])
kr_gold = next((x for x in kr if "金色" in x or "强节点" in x), "")
kr_tail = next((x for x in kr if "尾盘" in x), "")
summary = [
    f"① {wall_pos}；全天 {hi:.2f}–{lo:.2f}，收 {cl:.2f}（{chg:+.2f}%）。",
    f"② 白点轨迹：开 {opx:.2f} → 高 {hi:.2f} → 低 {lo:.2f} → 收 {cl:.2f}。",
    ("③ " + kr_gold) if kr_gold else "③ " + (kr[0] if kr else "heatmap 解读见上。"),
    f"④ 资金流：{flow_verdict}",
]

# ---------- top5_analysis (规则型三段式初稿) ----------
CN = ["一", "二", "三", "四", "五"]
_hm_rows = _hm_rows_all()
series = {}
for s in _hm_rows:
    try:
        dt = datetime.datetime.fromisoformat(s["minute_at"].replace("Z", "+00:00")).astimezone(ET)
        te = dt.strftime("%H:%M")
    except Exception:
        continue
    for c in s["cells"]:
        series.setdefault(c["strike_usd"], []).append((te, c["net_dealer_gex_usd"], s.get("spot_usd")))
top5_analysis = []
for i, n in enumerate(reads.get("top5", [])[:5]):
    sk = n["strike"]
    pts = sorted(series.get(sk, []))
    if not pts:
        top5_analysis.append(f"{CN[i]}、{sk}：当日无有效快照。")
        continue
    first_v, last_v = pts[0][1], pts[-1][1]
    peak = max(pts, key=lambda p: abs(p[1]))
    pv, pet, pspot = peak[1], peak[0], peak[2]
    if abs(first_v) > 1e5:
        chg_s = f"从 {first_v/1e6:+.1f}M 到 {pv/1e6:+.1f}M（约{abs(pv/max(first_v,1)):,.0f}倍）" if pv * first_v > 0 else f"由 {first_v/1e6:+.1f}M 翻转为 {pv/1e6:+.1f}M"
    else:
        chg_s = f"从接近 0 长到 {pv/1e6:+.1f}M"
    px_s = f"，当时现货约 {pspot:.2f}" if pspot else ""
    kind = "正 gamma 墙/地板" if pv > 0 else "负 gamma 加速带"
    top5_analysis.append(
        f"{CN[i]}、{sk}（{kind}，峰值 {pv/1e6:+.1f}M）：全天变化——{chg_s}；"
        f"影响时段——{pet} ET 达到峰值{px_s}；影响方式（初稿）——"
        f"{'价格在其附近获得支撑/减速' if pv > 0 else '价格进入后波动放大、加速通过'}，以实际 K 线为准。"
    )

meta = {
    "junkman": junkman,
    "event": event,
    "magnet_strike": magnet_strike,
    "magnet_src": magnet_src,
    "flow_verdict": flow_verdict,
    "summary": summary,
    "top5_analysis": top5_analysis,
}
out = f"{D}/meta_{DS}.json"
json.dump(meta, open(out, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
print("wrote", out, "| junkman:", status, "| event:", tag, "| flow_n:", buckets["n"])
