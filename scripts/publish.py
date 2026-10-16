#!/usr/bin/env python3
"""SPX 尾盘10分钟面板 — 数据发布脚本.

用法: python3 scripts/publish.py YYYY-MM-DD   (幂等, 可重复跑)
输出: data/<DS>.json (+ data/charts/spx_1m_<MMDD>.png), 更新 data/days.json

数据口径 (2026-10-15 用户亲定):
- K线: Yahoo ^GSPC 1m, 15:40-16:00 ET 共20根 (bar-start 15:40..15:59)
- VWAP: ES=F 1m 成交量(12x中位数上限) + 逐bar动态basis(ES收-SPX收)
        换算出等效SPX典型价再算 (沿用 trading_journal 口径)
- EMA250: ES 1m 收盘的 EMA250, 再减当时 basis
- flow: YHS /v1/flow/trades, server过滤 premium>=$200k/size>=50/
        sides=B,M,A/kind=order, 本地 min_dte==0（0DTE）, 时段 15:30-16:00 ET
- heatmap: 占位, data/heatmap_<DS>.png 由用户稍后提供

认证: YHS 经 skill CLI (Secure Vault 注入), 本脚本不接触任何 key/token.
"""
import json
import os
import re
import subprocess
import sys
import time
import urllib.parse
import urllib.request
import datetime
from zoneinfo import ZoneInfo

ET = ZoneInfo("America/New_York")
HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
PAGE = os.path.join(ROOT, "spx-10min")
DATA = os.path.join(PAGE, "data")
CHARTS = os.path.join(DATA, "charts")
CLI = os.path.expanduser("~/workspace/skills/yehangshe/bin/yhs.py")

WEEKDAY = ["周一", "周二", "周三", "周四", "周五", "周六", "周日"]


# ---------------- Yahoo 1m ----------------
def yahoo_1m(symbol, ds):
    """返回 {epoch_ts: {o,h,l,c,v}} 全天(ET 00:00-24:00)."""
    start = int(datetime.datetime.fromisoformat(ds + "T00:00:00")
                .replace(tzinfo=ET).timestamp())
    end = start + 86400
    url = (f"https://query1.finance.yahoo.com/v8/finance/chart/{symbol}"
           f"?interval=1m&period1={start}&period2={end}&includePrePost=true")
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    d = json.load(urllib.request.urlopen(req, timeout=30))
    r = d["chart"]["result"][0]
    ts, q = r["timestamp"], r["indicators"]["quote"][0]
    out = {}
    for i, t in enumerate(ts):
        if q["close"][i] is None:
            continue
        out[int(t)] = {"o": q["open"][i], "h": q["high"][i],
                       "l": q["low"][i], "c": q["close"][i],
                       "v": q["volume"][i] or 0}
    return out


def et_hm(ts):
    return datetime.datetime.fromtimestamp(ts, datetime.timezone.utc) \
        .astimezone(ET).strftime("%H:%M")


# ---------------- VWAP / EMA250 (SPX 口径) ----------------
def spx_series(spx, es):
    """对齐时间戳, 算全天 regular 时段的 VWAP/EMA250 序列.
    返回 (vwap_curve, ema_curve) 均为 [(ts, val)], ts 为 epoch.
    口径: basis_i = ES收-SPX收 (逐bar动态);
          VWAP 用 ES典型价-basis 换算+ES成交量(12x中位数上限);
          EMA250 先对 ES收盘算, 再减 basis."""
    common = sorted(set(spx) & set(es))
    reg = [t for t in common
           if "09:30" <= et_hm(t) <= "16:00"]
    if not reg:
        return [], []
    vols = sorted(es[t]["v"] for t in reg if es[t]["v"] > 0)
    cap = 12 * vols[len(vols) // 2] if vols else float("inf")
    num = den = 0.0
    vwap_curve = []
    for t in reg:
        e, s = es[t], spx[t]
        basis = e["c"] - s["c"]
        tp_spx = (e["h"] + e["l"] + e["c"]) / 3 - basis
        v = min(e["v"], cap)
        num += tp_spx * v
        den += v
        vwap_curve.append((t, num / den if den else tp_spx))
    # EMA250 warmup: 当日 09:30 之前的 ES 收盘 (隔夜)
    warm = sorted(t for t in es if t < reg[0])
    hist = [es[t]["c"] for t in warm[-600:]]
    k = 2 / 251
    ema = sum(hist[:250]) / 250 if len(hist) >= 250 else (
        sum(hist) / len(hist) if hist else es[reg[0]]["c"])
    for c in hist[250:]:
        ema = c * k + ema * (1 - k)
    ema_curve = []
    for t in reg:
        ema = es[t]["c"] * k + ema * (1 - k)
        basis = es[t]["c"] - spx[t]["c"]
        ema_curve.append((t, ema - basis))
    return vwap_curve, ema_curve


# ---------------- K线图 PNG (白底) ----------------
def draw_chart(ds, bars, vwap_seg, ema_seg, out_path):
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    import matplotlib.patches as mpatches
    import matplotlib.font_manager as fm

    _cjk = fm.FontProperties(
        fname="/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc")
    plt.rcParams["font.family"] = _cjk.get_name()
    plt.rcParams["axes.unicode_minus"] = False

    UP, DN = "#158353", "#c83c43"
    VWAP_C, EMA_C = "#7a42cf", "#3275c5"
    fig, ax = plt.subplots(figsize=(12, 5.6))
    fig.patch.set_facecolor("white")
    ax.set_facecolor("white")

    x = list(range(len(bars)))
    # 15:40-15:50 灰底
    ax.axvspan(-0.5, 9.5, color="#e9ebee", zorder=0)
    ax.text(4.5, 0.96, "15:40–15:50", transform=ax.transAxes, ha="center",
            va="top", fontsize=10, color="#5e6570")
    for i, b in enumerate(bars):
        col = UP if b["c"] >= b["o"] else DN
        ax.plot([i, i], [b["l"], b["h"]], color=col, lw=1.2, zorder=3)
        ax.add_patch(mpatches.Rectangle(
            (i - 0.35, min(b["o"], b["c"])), 0.7,
            abs(b["c"] - b["o"]) or 0.01,
            facecolor=col, edgecolor=col, zorder=3))
    if vwap_seg:
        ax.plot(x, [v for _, v in vwap_seg], color=VWAP_C, lw=2.0,
                label="VWAP", zorder=4)
    if ema_seg:
        ax.plot(x, [v for _, v in ema_seg], color=EMA_C, lw=2.0,
                label="EMA250", zorder=4)
    ax.set_xlim(-0.6, len(bars) - 0.4)
    ax.set_xticks(list(range(0, len(bars), 2)))
    ax.set_xticklabels([bars[i]["t"] for i in range(0, len(bars), 2)],
                       fontsize=10)
    ax.set_xlabel("时间 (ET)", fontsize=11)
    ax.set_ylabel("价格", fontsize=11)
    ax.tick_params(labelsize=10)
    ax.grid(True, color="#eef1f4", lw=0.8, zorder=0)
    for spine in ax.spines.values():
        spine.set_color("#d8dde4")
    ax.legend(loc="best", fontsize=10, frameon=True, facecolor="white",
              edgecolor="#d8dde4")
    ax.set_title(f"SPX 1分钟K线 {ds}（ET）", fontsize=13, pad=10)
    # 图下方数据行
    vwap_last = vwap_seg[-1][1] if vwap_seg else None
    ema_last = ema_seg[-1][1] if ema_seg else None
    dataline = (
        f"VWAP {vwap_last:.1f} ｜ EMA250 {ema_last:.1f}（16:00值）"
        if vwap_last is not None and ema_last is not None
        else "VWAP/EMA250 暂无")
    fig.text(0.5, 0.03, dataline, ha="center", fontsize=11, color="#17191d",
             bbox=dict(boxstyle="round,pad=0.4", fc="#eceff3", ec="#d8dde4"))
    fig.subplots_adjust(bottom=0.16, top=0.90, left=0.075, right=0.97)
    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    fig.savefig(out_path, dpi=110, facecolor="white")
    plt.close(fig)
    return vwap_last, ema_last


# ---------------- YHS flow ----------------
def _yhs_cli_get(path):
    tmp = f"/tmp/spx_desk_flow_{os.getpid()}.json"
    subprocess.run([sys.executable, CLI, "get", path, "--out", tmp],
                   capture_output=True, text=True, timeout=90)
    try:
        d = json.load(open(tmp))
    finally:
        try:
            os.unlink(tmp)
        except OSError:
            pass
    return d.get("status"), d


def fetch_flow(ds):
    """SPX 当日 flow: server过滤 premium>=2万/size>=50/sides=B,M,A/kind=order,
    本地只留 0DTE 的 SPXW (tickers=SPXW 口径 + min_dte==0)，
    size>=50, 单腿 single (leg_count<=1 且不含股票腿),
    时段 15:30-16:00 ET（尾盘半小时）. 返回 trades 列表."""
    all_rows, cursor, pages = [], None, 0
    while True:
        q = (f"/v1/flow/trades?session_date={ds}&tickers=SPXW&kind=order"
             f"&min_premium_usd=20000&min_contracts=50&sides=B,M,A"
             f"&sort_by=occurredAt&direction=asc&limit=25")
        if cursor:
            q += "&cursor=" + urllib.parse.quote(cursor, safe="")
        st, d = _yhs_cli_get(q)
        if st != 200:
            print(f"[flow] YHS {st}, flow 置空", flush=True)
            return []
        dd = d.get("data") or {}
        all_rows.extend(dd.get("data") or [])
        pages += 1
        cursor = (dd.get("_meta") or {}).get("next_cursor")
        if not cursor or pages > 20:
            break
        time.sleep(1.1)
    kept = []
    for r in all_rows:
        if r.get("min_dte") != 0:          # 只要 0DTE
            continue
        if not (r.get("contract_symbol") or "").startswith("O:SPXW"):  # 只要 SPXW
            continue
        if not (r.get("contracts") or 0) >= 50:  # size >= 50
            continue
        if (r.get("leg_count") or 0) > 1 or r.get("contains_stock_leg"):  # 只要单腿
            continue
        occ_et = (datetime.datetime.fromisoformat(
            (r.get("occurred_at") or "").replace("Z", "+00:00"))
            .astimezone(ET))
        if occ_et.strftime("%Y-%m-%d") != ds:
            continue
        hm = occ_et.strftime("%H:%M")
        if not ("15:30" <= hm <= "16:00"):  # 尾盘半小时
            continue
        kept.append(r)
    return kept


def _bucket(side):
    s = (side or "").lower()
    if s in ("at_ask", "above_ask"):
        return "ask"
    if s in ("at_bid", "below_bid"):
        return "bid"
    if s in ("mid", "above_mid", "below_mid"):
        return "mid"
    return "unknown"


def _moneyness(right, strike, spot):
    if not strike or not spot:
        return ""
    if right == "C":
        if strike > spot * 1.001:
            return "OTM"
        if strike < spot * 0.999:
            return "ITM"
        return "ATM"
    if strike < spot * 0.999:
        return "OTM"
    if strike > spot * 1.001:
        return "ITM"
    return "ATM"


def _strike_of(r):
    m = re.search(r"[CP](\d{8})$", r.get("contract_symbol") or r.get("contract") or "")
    if m:
        return float(m.group(1)) / 1000
    return r.get("max_strike_usd") or r.get("min_strike_usd")


def summarize_flow(trades, spot_by_min, day_close):
    """按 call/put x ask/bid/mid 汇总 premium; 每格标主要strike+OTM/ATM/ITM."""
    buckets = {}
    by_strike = {}
    for r in trades:
        right = (r.get("right") or "").upper()
        if right not in ("C", "P"):
            continue
        b = _bucket(r.get("side"))
        prem = float(r.get("premium_usd") or 0)
        key = ("call" if right == "C" else "put") + "_" + b
        e = buckets.setdefault(key, {"premium": 0.0, "n": 0})
        e["premium"] += prem
        e["n"] += 1
        strike = r.get("max_strike_usd") or r.get("min_strike_usd")
        occ = (r.get("occurred_at") or "")[:16].replace("T", " ")[11:16]
        sk = (key, strike)
        se = by_strike.setdefault(sk, {"premium": 0.0, "n": 0, "spots": []})
        se["premium"] += prem
        se["n"] += 1
        se["spots"].append(spot_by_min.get(occ) or day_close)

    def cell(key, right):
        info = buckets.get(key, {"premium": 0.0, "n": 0})
        # 该格主要 strike
        cands = [(v["premium"], k[1], v) for k, v in by_strike.items()
                 if k[0] == key and k[1]]
        if not cands:
            return {"premium_m": round(info["premium"] / 1e6, 2),
                    "n": info["n"], "strike": None, "moneyness": ""}
        _, strike, v = max(cands)
        spot = (sum(v["spots"]) / len(v["spots"])) if v["spots"] else day_close
        return {"premium_m": round(info["premium"] / 1e6, 2), "n": info["n"],
                "strike": strike,
                "moneyness": _moneyness(right, strike, spot)}

    g = lambda k: buckets.get(k, {"premium": 0.0})["premium"]
    bull = g("call_ask") + g("put_bid")
    bear = g("put_ask") + g("call_bid")
    total = bull + bear
    bias = (bull - bear) / total if total > 0 else 0.0
    lean = "中性" if total <= 0 else (
        "偏多" if bias >= 0.3 else ("偏空" if bias <= -0.3 else "中性"))
    mid_m = (g("call_mid") + g("put_mid")) / 1e6
    # 逐笔时间顺序列表（trades 已按 occurred_at 升序）— 全字段明细
    trade_list = []
    for r in trades:
        right = (r.get("right") or "").upper()
        if right not in ("C", "P"):
            continue
        b = _bucket(r.get("side"))
        try:
            occ_et = datetime.datetime.fromisoformat(
                (r.get("occurred_at") or "").replace("Z", "+00:00")).astimezone(ET)
            tstr = occ_et.strftime("%H:%M:%S")
        except Exception:
            tstr = ""
        strike = _strike_of(r)
        spot = r.get("underlying_price_usd") or day_close
        bid = r.get("nbbo_bid_usd")
        ask = r.get("nbbo_ask_usd")
        trade_list.append({
            "t": tstr,
            "strike": round(strike, 1) if strike else None,
            "right": "Call" if right == "C" else "Put",
            "dte": r.get("min_dte"),
            "fill": round(float(r.get("avg_fill_usd") or 0), 2) or None,
            "bid": round(float(bid), 2) if bid else None,
            "ask": round(float(ask), 2) if ask else None,
            "side": {"ask": "ASK", "bid": "BID", "mid": "MID"}.get(b, b),
            "size": r.get("contracts"),
            "prem_m": round(float(r.get("premium_usd") or 0) / 1e6, 3),
            "net_m": (round(float(r.get("net_premium_usd")) / 1e6, 3)
                      if r.get("net_premium_usd") is not None else None),
            "vol": r.get("session_volume"),
            "oi": r.get("open_interest"),
            "spot": round(float(spot), 1) if spot else None,
            "sweep": bool(r.get("has_sweep")),
            "moneyness": _moneyness(right, strike, spot),
        })
    summary = {
        "window_et": "15:30-16:00",
        "n": len(trades),
        "trade_list": trade_list,
        "call_ask": cell("call_ask", "C"),
        "call_bid": cell("call_bid", "C"),
        "put_ask": cell("put_ask", "P"),
        "put_bid": cell("put_bid", "P"),
        "mid_premium_m": round(mid_m, 2),
        "mid_note": "mid 单独列示，不计入主动买卖",
        "bull_premium_m": round(bull / 1e6, 2),
        "bear_premium_m": round(bear / 1e6, 2),
        "lean": lean,
    }
    if total <= 0:
        verdict = "flow中性：15:30–16:00 无符合筛选的大单"
    else:
        parts = []
        for key, label in [("call_ask", "call ask主动买"), ("put_ask", "put ask主动买"),
                           ("call_bid", "call bid主动卖"), ("put_bid", "put bid主动卖")]:
            c = summary[key]
            if c["premium_m"] > 0:
                parts.append(f"{label} {c['premium_m']:.2f}M" +
                             (f"（{c['strike']:.0f} {c['moneyness']}）" if c["strike"] else ""))
        verdict = (f"flow{lean}：" + "｜".join(parts) +
                   f"，多空资金 {summary['bull_premium_m']:.2f}M vs {summary['bear_premium_m']:.2f}M")
    return summary, verdict


# ---------------- 策略判断 ----------------
def build_strategy(px, vwap, ema250, lean, verdict):
    lines = []
    if px is None:
        return ["数据缺失，今日不判断"]
    if vwap is not None and ema250 is not None:
        # 口径（2026-09-30 定）：（上方/下方）描述的是参考线相对现价的位置
        pv = "上方" if vwap > px else "下方"
        pe = "上方" if ema250 > px else "下方"
        tgt, tname = (vwap, "VWAP") if abs(px - vwap) <= abs(px - ema250) \
            else (ema250, "EMA250")
        lines.append(f"现价 {px:.1f}：VWAP {vwap:.1f}（{pv}）｜EMA250 {ema250:.1f}（{pe}）")
        lines.append(f"尾盘目标可能往{tname} {tgt:.1f} 一带去")
    else:
        lines.append(f"现价 {px:.1f}（VWAP/EMA250 缺失，只看 flow）")
    lines.append(verdict)
    lines.append("时间衰减大，只看快进快出机会")
    return lines


# ---------------- 主流程 ----------------
def main(ds):
    try:
        datetime.date.fromisoformat(ds)
    except ValueError:
        raise SystemExit(f"日期格式错误: {ds}, 用 YYYY-MM-DD")
    mmdd = ds[5:].replace("-", "")
    weekday = WEEKDAY[datetime.date.fromisoformat(ds).weekday()]

    out = {"date": ds, "weekday": weekday, "chart_png": None,
           "bars_1m": [], "table_10": [], "vwap": None, "ema250": None,
           "flow": None, "strategy": [], "heatmap_png": None, "note": ""}

    # 旧 note 保留 (GitHub 网页端人工填写的不被覆盖)
    old_path = os.path.join(DATA, f"{ds}.json")
    if os.path.exists(old_path):
        try:
            out["note"] = json.load(open(old_path)).get("note", "") or ""
        except Exception:
            pass

    # --- K线 + VWAP/EMA250 ---
    px = None
    try:
        spx = yahoo_1m("%5EGSPC", ds)
        es = yahoo_1m("ES=F", ds)
        vwap_curve, ema_curve = spx_series(spx, es)
        vw = dict(vwap_curve)
        em = dict(ema_curve)
        win_ts = sorted(t for t in spx if "15:40" <= et_hm(t) <= "15:59")
        bars = [{"t": et_hm(t), **{k: round(spx[t][k], 1)
                                   for k in ("o", "h", "l", "c")}}
                for t in win_ts]
        out["bars_1m"] = bars
        out["table_10"] = [b for b in bars if b["t"] >= "15:50"]
        vwap_seg = [(t, vw[t]) for t in win_ts if t in vw]
        ema_seg = [(t, em[t]) for t in win_ts if t in em]
        chart_rel = f"data/charts/spx_1m_{mmdd}.png"
        if bars:
            vl, el = draw_chart(ds, bars, vwap_seg, ema_seg,
                               os.path.join(ROOT, chart_rel))
            out["chart_png"] = chart_rel
            out["vwap"] = round(vl, 1) if vl else None
            out["ema250"] = round(el, 1) if el else None
            px = bars[-1]["c"]
    except Exception as e:
        print(f"[warn] K线/VWAP 失败: {e}", flush=True)

    # --- flow ---
    try:
        trades = fetch_flow(ds)
        spot_by_min = {b["t"]: b["c"] for b in out["bars_1m"]}
        summary, verdict = summarize_flow(trades, spot_by_min, px)
        out["flow"] = {"summary": summary, "verdict": verdict}
        lean = summary["lean"]
    except Exception as e:
        print(f"[warn] flow 失败: {e}", flush=True)
        out["flow"] = None
        lean, verdict = "中性", "flow中性：数据缺失"

    # --- 策略 ---
    out["strategy"] = build_strategy(px, out["vwap"], out["ema250"],
                                    lean, verdict)

    json.dump(out, open(old_path, "w"), ensure_ascii=False, indent=1)
    # days.json 去重倒序
    dp = os.path.join(DATA, "days.json")
    days = []
    if os.path.exists(dp):
        try:
            days = json.load(open(dp)).get("days", [])
        except Exception:
            days = []
    if ds not in days:
        days.append(ds)
    days = sorted(set(days), reverse=True)
    json.dump({"days": days}, open(dp, "w"), ensure_ascii=False)
    print(f"[ok] {ds}: bars={len(out['bars_1m'])} flow_n="
          f"{out['flow']['summary']['n'] if out['flow'] else 'NA'} "
          f"-> {old_path}", flush=True)


if __name__ == "__main__":
    if len(sys.argv) != 2:
        raise SystemExit("用法: python3 scripts/publish.py YYYY-MM-DD")
    main(sys.argv[1])
