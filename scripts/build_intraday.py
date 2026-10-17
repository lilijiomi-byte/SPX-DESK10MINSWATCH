#!/usr/bin/env python3
"""MU / SPX / TSLA 每日收盘后日内报告流水线 (纯市场分析, 不含个人交易内容).

用法:
    build_intraday.py <YYYY-MM-DD> [mu|spx|tsla]     # 构建某日报告(默认三标的)
    build_intraday.py 2026-10-16                     # 当日构建
    build_intraday.py 2026-10-14 mu                  # 回补: 按日期回补, 走完整数据拉取

流程(每标的):
    MU:   mu_fetch(完整拉取) -> build_chart_mu.py -> mu_assemble -> 渲染
    TSLA: build_day.py -> analyze_day.py -> gen_reads_daily.py -> auto_meta.py
          -> assemble_daily_generic.py -> build_chart_daily.py -> 渲染
    SPX:  build_day.py -> junkman文件补丁 -> build_chart_spx.py -> 渲染
输出: ~/workspace/spx-desk/{mu,spx-intraday,tsla}/
        <date>.html (单日独立页) / charts/<ticker>_<date>.png / data/<date>.json
      每次构建后重建各标的 index.html (日期倒序 tab; 只追加/重排, 永不删除旧日期)
特性: 幂等(重复跑覆盖不重复); 单标的失败隔离(留空占位,不阻断其他);
      跑完自检三标的当日 HTML, 缺失明确报错退出码非零.
推送: 另行运行 python3 ~/workspace/spx-desk/scripts/gh_push.py (绝不用 git push).
"""
import datetime
import json
import os
import shutil
import subprocess
import sys
import time
import urllib.parse
import urllib.request
from zoneinfo import ZoneInfo

SCRIPTS = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, SCRIPTS)
from intraday_lib import (render_article, render_standalone, build_index,
                          self_check, junkman_lookup, event_lookup, weekday_cn)

WS = os.path.expanduser("~/workspace")
SITE = os.path.join(WS, "spx-desk")
ET = ZoneInfo("America/New_York")
MU_DIR = os.path.join(WS, "mu_report")
TSLA_DIR = os.path.join(WS, "trading_journal", "tsla_daily")
SPX_DIR = os.path.join(WS, "trading_journal", "spx_daily")

TICKERS = {
    "mu": {"site": os.path.join(SITE, "mu"), "label": "MU 日内报告", "name": "MU"},
    "spx": {"site": os.path.join(SITE, "spx-intraday"), "label": "SPX 日内报告", "name": "SPX"},
    "tsla": {"site": os.path.join(SITE, "tsla"), "label": "TSLA 日内报告", "name": "TSLA"},
}
FAILURES = []


def log(msg):
    print(f"[intraday] {msg}", flush=True)


def run(cmd, **kw):
    log("$ " + " ".join(cmd))
    r = subprocess.run(cmd, capture_output=True, text=True, **kw)
    if r.returncode != 0:
        raise RuntimeError(f"命令失败({r.returncode}): {' '.join(cmd)}\n{r.stderr[-2000:]}")
    return r


def _ensure_yhs_key():
    """YHS 凭证: 优先用环境变量, 缺失时经 authd surrogate 注入.
    surrogate 以 Bearer 形式发往 api.yehangshe.com 时由运行时替换为真实 Key,
    子进程(旧脚本)通过环境变量继承, 无需改动旧脚本."""
    if os.environ.get("YEHANGSHE_API_KEY"):
        return
    sys.path.insert(0, "/opt/muse/skills/custom-connector-setup/bin")
    from dynamic_credentials import dynamic_credential_entry
    surr = dynamic_credential_entry("custom.yehangshe")["surrogate"]
    os.environ["YEHANGSHE_API_KEY"] = surr
    log("YHS 凭证已通过 surrogate 注入环境")


def _rows_and_cursor(d):
    """兼容 YHS 两种响应形状: {"data": [...], "_meta": {...}} 或 {"data": {"data": [...], "_meta": {...}}}."""
    dd = d.get("data")
    if isinstance(dd, dict):
        rows = dd.get("data") or []
        meta = dd.get("_meta") or {}
    elif isinstance(dd, list):
        rows = dd
        meta = d.get("_meta") or {}
    else:
        rows, meta = [], {}
    return rows, meta.get("next_cursor")


def yhs(path, params=None):
    import requests
    key = os.environ.get("YEHANGSHE_API_KEY")
    if not key:
        raise RuntimeError("YEHANGSHE_API_KEY 未设置")
    url = "https://api.yehangshe.com" + path
    r = requests.get(url, headers={"Authorization": f"Bearer {key}"},
                     params=params or {}, timeout=60)
    if r.status_code != 200:
        raise RuntimeError(f"YHS {path}: HTTP {r.status_code} {r.text[:200]}")
    return r.json()


def yahoo_chart(symbol, interval="5m", rng="1mo", prepost=True):
    p2 = int(datetime.datetime.now(datetime.timezone.utc).timestamp()) + 86400
    p1 = p2 - 32 * 86400
    url = (f"https://query1.finance.yahoo.com/v8/finance/chart/{symbol}"
           f"?interval={interval}&period1={p1}&period2={p2}"
           + ("&includePrePost=true" if prepost else ""))
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    return json.load(urllib.request.urlopen(req, timeout=40))


# ================= MU: 完整拉取 =================
def mu_fetch(ds):
    mmdd = ds[5:7] + ds[8:10]
    log(f"MU {ds}: 开始完整拉取")
    # 1) 到期日探测: 0DTE(ds) -> 1DTE(ds+1); YHS 只保留 3 天, 超窗报 RANGE_TOO_DEEP
    d0 = datetime.date.fromisoformat(ds)
    expiry, dte_label = ds, "0DTE"
    probe_rows = None
    try:
        probe = yhs("/v1/derived/heatmap/MU/history",
                    {"expiration": ds, "from": f"{ds}T13:30:00Z",
                     "to": f"{ds}T13:35:00Z", "limit": 100})
        probe_rows, _ = _rows_and_cursor(probe)
    except Exception as e:
        log(f"MU {ds}: 0DTE 探测失败({str(e)[:60]}), 试 1DTE")
    if not probe_rows:
        d1 = (d0 + datetime.timedelta(days=1)).isoformat()
        try:
            probe = yhs("/v1/derived/heatmap/MU/history",
                        {"expiration": d1, "from": f"{ds}T13:30:00Z",
                         "to": f"{ds}T13:35:00Z", "limit": 100})
            probe_rows, _ = _rows_and_cursor(probe)
        except Exception as e:
            log(f"MU {ds}: 1DTE 探测失败({str(e)[:60]})")
        if probe_rows:
            expiry, dte_label = d1, "1DTE"
    # 本地存档 fallback (YHS 只保留 3 天, 历史回补用 mu_report 存档)
    local_rows, local_label = None, None
    if not probe_rows:
        for cand, lab in [(f"hm{mmdd}_full.json", "0DTE"),
                          (f"hm{mmdd}_1dte.json", "1DTE")]:
            p = os.path.join(MU_DIR, cand)
            if os.path.exists(p):
                try:
                    d = json.load(open(p))
                    dd = d.get("data")
                    # 兼容两种格式: {"data":[rows]} 或 {"data":{"data":[rows]}}
                    rows = dd if isinstance(dd, list) else (dd.get("data") if isinstance(dd, dict) else None)
                    if rows and len(rows) > 5:
                        local_rows, local_label = rows, lab
                        log(f"MU {ds}: 使用本地存档 {cand} ({len(rows)} 行)")
                        break
                except Exception as e:
                    log(f"MU {ds}: 本地存档 {cand} 读取失败: {e}")
        if local_rows:
            expiry, dte_label = local_rows[0].get("expiration", ds), local_label
        else:
            raise RuntimeError(f"MU {ds}: 0DTE/1DTE 均无 heatmap 快照(本地也无存档)")
    if local_rows:
        all_rows = sorted(local_rows, key=lambda r: r["minute_at"])
        hm_file = f"hm{mmdd}_{dte_label.lower()}.json"
        json.dump({"data": {"data": all_rows}},
                  open(os.path.join(MU_DIR, hm_file), "w"))
        log(f"MU heatmap 本地存档 rows={len(all_rows)} -> {hm_file}")
    else:
        time.sleep(1)
        # 2) heatmap 全部分页
        all_rows, cursor, pages = [], None, 0
        while True:
            params = {"expiration": expiry, "from": f"{ds}T13:30:00Z",
                      "to": f"{ds}T20:10:00Z", "limit": 100}
            if cursor:
                params["cursor"] = cursor
            d = yhs("/v1/derived/heatmap/MU/history", params)
            rows, cursor = _rows_and_cursor(d)
            all_rows.extend(rows)
            pages += 1
            if not cursor or pages > 20:
                break
            time.sleep(1.1)
        all_rows.sort(key=lambda r: r["minute_at"])
        hm_file = f"hm{mmdd}_{dte_label.lower()}.json"
        json.dump({"data": {"data": all_rows}},
                  open(os.path.join(MU_DIR, hm_file), "w"))
        log(f"MU heatmap rows={len(all_rows)} pages={pages} -> {hm_file}")
    time.sleep(1)
    # 3) Yahoo 5m
    y = yahoo_chart("MU", "5m", "1mo", True)
    json.dump(y, open(os.path.join(MU_DIR, f"yahoo_mu_{ds}.json"), "w"))
    log("MU yahoo 5m saved")
    # 4) flow
    all_f, cursor, pages = [], None, 0
    while True:
        params = {"session_date": ds, "tickers": "MU", "kind": "order",
                  "min_premium_usd": 200000, "min_contracts": 50,
                  "sides": "B,M,A", "sort_by": "occurredAt",
                  "direction": "asc", "limit": 25}
        if cursor:
            params["cursor"] = cursor
        d = yhs("/v1/flow/trades", params)
        rows, cursor = _rows_and_cursor(d)
        all_f.extend(rows)
        pages += 1
        if not cursor or pages > 40:
            break
        time.sleep(1.1)
    # 本地只卡 DTE≤1 (side 服务端已按 B/M/A 过滤, 返回值为 at_bid/at_ask/mid)
    kept = [r for r in all_f
            if r.get("kind") == "order"
            and (r.get("min_dte") is not None and r["min_dte"] <= 1)]
    from collections import Counter as _C
    log(f"MU flow min_dte分布: {dict(_C(str(r.get('min_dte')) for r in all_f).most_common(8))}")

    def _strike_of(r):
        if r.get("strike"):
            return r["strike"]
        import re as _re
        m = _re.search(r"([CP])(\d{8})$", r.get("contract_symbol") or "")
        return int(m.group(2)) / 1000 if m else None

    slim = [{"occurred_at": r.get("occurred_at"), "contract": r.get("contract_symbol"),
             "expiration": r.get("expiration"), "right": r.get("right"),
             "side": r.get("side"), "premium_usd": r.get("premium_usd"),
             "contracts": r.get("contracts"), "avg_fill_usd": r.get("avg_fill_usd"),
             "underlying_price_usd": r.get("underlying_price_usd"),
             "strike": _strike_of(r), "min_dte": r.get("min_dte")} for r in kept]
    json.dump({"ticker": "MU", "session_date": ds, "n_raw": len(all_f),
               "n_kept": len(slim), "trades": slim},
              open(os.path.join(MU_DIR, f"flow_mu_{ds}.json"), "w"))
    log(f"MU flow raw={len(all_f)} kept={len(slim)}")
    # 5) levels: 官方开盘/高低/收优先用 YHS 日线 regular 时段 (5m bar open 不是官方开盘价)
    try:
        yd = yhs("/v1/stocks/ohlc/MU", {"candle_size": "1d"})
        ybars = (yd.get("data") or {}).get("bars") or []
        yreg = [b for b in ybars if b.get("trading_date") == ds and b.get("session") == "regular"]
        if yreg:
            official_open = yreg[0]["open_usd"]
            y_high, y_low, y_close = yreg[0]["high_usd"], yreg[0]["low_usd"], yreg[0]["close_usd"]
        else:
            raise RuntimeError("无 regular 日线")
    except Exception as e:
        log(f"MU YHS 日线失败({e})，用 Yahoo 5m 兜底")
        official_open, y_high, y_low, y_close = None, None, None, None
    res = y["chart"]["result"][0]
    ts, q = res["timestamp"], res["indicators"]["quote"][0]
    bars = []
    for i, t in enumerate(ts):
        dt = datetime.datetime.fromtimestamp(t, datetime.timezone.utc).astimezone(ET)
        if dt.strftime("%Y-%m-%d") != ds or q["close"][i] is None:
            continue
        bars.append({"dt": dt, "o": q["open"][i], "h": q["high"][i],
                     "l": q["low"][i], "c": q["close"][i]})
    reg = [b for b in bars if datetime.time(9, 30) <= b["dt"].timetz() < datetime.time(16, 0)]
    if not reg:
        raise RuntimeError(f"MU {ds}: Yahoo 当日无 regular bar")
    op = official_open or reg[0]["o"]
    hi = y_high or max(b["h"] for b in reg)
    lo = y_low or min(b["l"] for b in reg)
    cl = y_close or reg[-1]["c"]
    # 盘前高低: 当日 9:30 前 bar
    pre = [b for b in bars if b["dt"].timetz() < datetime.time(9, 30)]
    pm_h = max([b["h"] for b in pre], default=hi)
    pm_l = min([b["l"] for b in pre], default=op)
    # VWAP (typical price × volume, regular session)
    vw_curve, num, den = [], 0.0, 0.0
    vol_in_quote = q.get("volume") or []
    for i, t in enumerate(ts):
        dt = datetime.datetime.fromtimestamp(t, datetime.timezone.utc).astimezone(ET)
        if dt.strftime("%Y-%m-%d") != ds or q["close"][i] is None:
            continue
        if datetime.time(9, 30) <= dt.timetz() < datetime.time(16, 0):
            v = (vol_in_quote[i] if i < len(vol_in_quote) else 0) or 0
            tp = (q["high"][i] + q["low"][i] + q["close"][i]) / 3
            num += tp * v
            den += v
            vw_curve.append(num / den if den else tp)
    vwap = vw_curve[-1] if vw_curve else cl
    # EMA250: 5d 5m 收盘
    closes_5d = [c for i, c in enumerate(q["close"]) if c]
    k = 2 / (250 + 1)
    ema = closes_5d[0]
    for c in closes_5d[1:]:
        ema = c * k + ema * (1 - k)
    # TOP5: 不同strike最大|GEX| (按峰值|GEX|降序, 与 Library 一致)
    tot, peak = {}, {}
    for s in all_rows:
        try:
            dt = datetime.datetime.fromisoformat(s["minute_at"].replace("Z", "+00:00")).astimezone(ET)
            te = dt.strftime("%H:%M")
        except Exception:
            continue
        for c in s["cells"]:
            sk, v = c["strike_usd"], c["net_dealer_gex_usd"]
            tot[sk] = tot.get(sk, 0) + abs(v)
            if sk not in peak or abs(v) > abs(peak[sk][0]):
                peak[sk] = (v, te)
    top5 = [{"strike": sk, "max_gex_m": round(peak[sk][0] / 1e6, 1), "et": peak[sk][1]}
            for sk in sorted(peak, key=lambda k: -abs(peak[k][0]))[:5]]
    # 墙: junkman 简报优先 (brief JSON -> txt 解析), 否则 heatmap 首档算法
    cw = pw = None
    wall_src = "heatmap首档算法"
    try:
        bj = json.load(open(os.path.join(WS, "yhs_premarket", "daily", "data", ds, "junkman_brief.json")))
        b = bj.get("MU", {})
        if b.get("call_wall") and b.get("put_wall"):
            cw, pw = b["call_wall"], b["put_wall"]
            wall_src = "junk-man"
    except Exception:
        pass
    if cw is None:
        for _d in (os.path.join(WS, "trading_journal"), os.path.join(WS, "mu_report")):
            _p = os.path.join(_d, f"junkman_mu_{ds}.txt")
            if os.path.exists(_p):
                _txt = open(_p, encoding="utf-8").read()
                import re as _re
                _m1 = _re.search(r"([\d,]+)\s*CALL WALL", _txt)
                _m2 = _re.search(r"([\d,]+)\s*PUT WALL", _txt)
                if _m1 and _m2:
                    cw = float(_m1.group(1).replace(",", ""))
                    pw = float(_m2.group(1).replace(",", ""))
                    wall_src = "junk-man"
                break
    if cw is None and all_rows:
        s0 = all_rows[0]
        spot = s0.get("spot_usd") or cl
        cells = s0.get("cells") or []
        pm = max([abs(c["net_dealer_gex_usd"]) for c in cells if c["net_dealer_gex_usd"] > 0], default=0)
        ups = sorted([c["strike_usd"] for c in cells
                      if c["net_dealer_gex_usd"] > 0 and c["net_dealer_gex_usd"] >= 0.25 * pm
                      and c["strike_usd"] >= spot])
        dns = sorted([c["strike_usd"] for c in cells
                      if c["net_dealer_gex_usd"] > 0 and c["net_dealer_gex_usd"] >= 0.25 * pm
                      and c["strike_usd"] <= spot], reverse=True)
        cw = ups[0] if ups else None
        pw = dns[0] if dns else None
    levels = {"open": round(op, 2), "high": round(hi, 2), "low": round(lo, 2),
              "close": round(cl, 2), "chg_pct": round((cl / op - 1) * 100, 2),
              "premarket_H": round(pm_h, 2), "premarket_L": round(pm_l, 2),
              "vwap": round(vwap, 2), "ema250": round(ema, 2),
              "vwap_curve": [round(v, 2) for v in vw_curve],
              "ema_curve": [round(ema, 2)] * len(vw_curve),
              "top5": top5, "call_wall": cw, "put_wall": pw}
    json.dump(levels, open(os.path.join(MU_DIR, f"day_{ds}_levels.json"), "w"),
              ensure_ascii=False, indent=1)
    cfg = {"expiry": expiry, "hm_label": dte_label, "hm_file": hm_file,
           "call_wall": cw, "put_wall": pw, "wall_src": wall_src,
           "official_open": round(op, 2)}
    json.dump(cfg, open(os.path.join(MU_DIR, f"day_{ds}.json"), "w"),
              ensure_ascii=False, indent=1)
    log(f"MU levels: open={op:.2f} H={hi:.2f} L={lo:.2f} C={cl:.2f} "
        f"cw={cw} pw={pw}({wall_src}) top5={[(t['strike'], t['max_gex_m']) for t in top5]}")
    return {"levels": levels, "cfg": cfg, "flow": slim, "hm_rows": all_rows,
            "hm_label": dte_label}

# ================= MU: 组装标准化报告 =================
def mu_assemble(ds, fetched):
    lv, cfg, flow, hm_rows = fetched["levels"], fetched["cfg"], fetched["flow"], fetched["hm_rows"]
    dte_label = fetched["hm_label"]
    n_snaps = len(hm_rows)
    opx = cfg["official_open"]
    cl, hi, lo, chg = lv["close"], lv["high"], lv["low"], lv["chg_pct"]
    cw, pw = cfg.get("call_wall"), cfg.get("put_wall")

    is_event, tag, detail = event_lookup(ds)
    jm_status, jm_meta, jm_text = junkman_lookup("mu", ds)

    # per-strike 时间序列
    series = {}
    for s in hm_rows:
        try:
            dt = datetime.datetime.fromisoformat(s["minute_at"].replace("Z", "+00:00")).astimezone(ET)
            te = dt.strftime("%H:%M")
        except Exception:
            continue
        for c in s["cells"]:
            series.setdefault(c["strike_usd"], []).append((te, c["net_dealer_gex_usd"], s.get("spot_usd")))
    for v in series.values():
        v.sort()

    def wall_of(snap):
        spot = snap.get("spot_usd") or cl
        cells = snap.get("cells") or []
        pm = max([c["net_dealer_gex_usd"] for c in cells if c["net_dealer_gex_usd"] > 0], default=0)
        ups = sorted([c["strike_usd"] for c in cells
                      if c["net_dealer_gex_usd"] >= 0.25 * pm and c["strike_usd"] >= spot])
        dns = sorted([c["strike_usd"] for c in cells
                      if c["net_dealer_gex_usd"] >= 0.25 * pm and c["strike_usd"] <= spot], reverse=True)
        return (ups[0] if ups else None, dns[0] if dns else None)

    w0, w1 = wall_of(hm_rows[0]), wall_of(hm_rows[-1])
    mig = []
    if w0[0] != w1[0] and w0[0] and w1[0]:
        mig.append(f"call wall {w0[0]:.0f}→{w1[0]:.0f}（{'上移' if w1[0] > w0[0] else '下移'}）")
    if w0[1] != w1[1] and w0[1] and w1[1]:
        mig.append(f"put wall {w0[1]:.0f}→{w1[1]:.0f}（{'上移' if w1[1] > w0[1] else '下移'}）")

    # key_reads: 金色带/紫色带/峰值/尾盘变化
    last = hm_rows[-1]
    cells = last.get("cells") or []
    pos = sorted([c for c in cells if c["net_dealer_gex_usd"] > 0],
                 key=lambda c: -c["net_dealer_gex_usd"])[:3]
    neg = sorted([c for c in cells if c["net_dealer_gex_usd"] < 0],
                 key=lambda c: c["net_dealer_gex_usd"])[:3]
    gold_s = "、".join(f"{c['strike_usd']:.0f}(+{c['net_dealer_gex_usd']/1e6:.1f}M)" for c in pos) or "无显著正节点"
    purp_s = "、".join(f"{c['strike_usd']:.0f}({c['net_dealer_gex_usd']/1e6:.1f}M)" for c in neg) or "无显著负节点"
    tail = []
    for t in lv["top5"][:3]:
        pts = series.get(t["strike"], [])
        if len(pts) >= 2:
            v0, v1 = pts[0][1] / 1e6, pts[-1][1] / 1e6
            tail.append(f"{t['strike']:.0f} {v0:+.1f}M→{v1:+.1f}M")
    key_reads = [
        f"金色带（收盘档）：{gold_s}",
        f"紫色带（收盘档）：{purp_s}",
        f"全天最大节点：{'、'.join(f'{t['strike']:.0f}({t['max_gex_m']:+.1f}M@{t['et']})' for t in lv['top5'])}",
        ("尾盘节点变化：" + "；".join(tail)) if tail else "尾盘节点变化：无显著变化",
    ]
    heatmap_intro = (
        f"GEX 地形图（{dte_label}，夜航社 {n_snaps} 档 5 分钟快照）："
        f"金色=正 gamma 区（做市商对冲减速带/墙），紫色=负 gamma 区（加速带）。"
        f"白点为每档现货位置。{'；'.join(mig) if mig else '墙位全天未迁移'}。"
    )

    # TOP5 三段式初稿
    CN = ["一", "二", "三", "四", "五"]
    top5 = []
    for i, t in enumerate(lv["top5"]):
        pts = series.get(t["strike"], [])
        if pts:
            first_v = pts[0][1]
            peak = max(pts, key=lambda p: abs(p[1]))
            pv, pet, pspot = peak[1], peak[0], peak[2]
            if abs(first_v) > 1e5 and pv * first_v > 0:
                chg_s = f"从 {first_v/1e6:+.1f}M 到 {pv/1e6:+.1f}M"
            elif abs(first_v) > 1e5:
                chg_s = f"由 {first_v/1e6:+.1f}M 翻转为 {pv/1e6:+.1f}M"
            else:
                chg_s = f"从接近 0 长到 {pv/1e6:+.1f}M"
            px_s = f"，当时现货约 {pspot:.2f}" if pspot else ""
            kind = "正 gamma 墙/地板" if pv > 0 else "负 gamma 加速带"
            how = ("价格在其附近获得支撑/减速" if pv > 0 else "价格进入后波动放大、加速通过")
            analysis = (f"{CN[i]}、{t['strike']:.0f}（{kind}）：全天变化——{chg_s}；"
                        f"影响时段——{pet} ET 达到峰值{px_s}；影响方式（初稿）——{how}，以实际 K 线为准。")
        else:
            analysis = f"{CN[i]}、{t['strike']:.0f}：当日无有效快照。"
        top5.append({"rank": i + 1, "strike": t["strike"], "gex_m": t["max_gex_m"],
                     "et": t["et"], "pos": t["max_gex_m"] > 0, "analysis": analysis})

    # flow: 逐笔明细表（SPX 10min 同款格式）
    trade_list = []
    try:
        sys.path.insert(0, SCRIPTS)
        import publish as _pub
        raw = _pub.fetch_flow_stock(ds, "MU", min_premium_usd=200000)
        trade_list = _pub.build_trade_list(raw, cl)
    except Exception as e:
        print(f"[mu flow] 逐笔拉取失败: {e}", flush=True)
    n_kept = len(trade_list)
    # 简单 verdict：从 trade_list 汇总
    bull = sum(t["prem_m"] for t in trade_list
               if (t["right"] == "Call" and t["side"] == "ASK")
               or (t["right"] == "Put" and t["side"] == "BID"))
    bear = sum(t["prem_m"] for t in trade_list
               if (t["right"] == "Put" and t["side"] == "ASK")
               or (t["right"] == "Call" and t["side"] == "BID"))
    mid_m = sum(t["prem_m"] for t in trade_list if t["side"] == "MID")
    lean = "偏多" if bull > bear * 1.2 else ("偏空" if bear > bull * 1.2 else "中性")
    flow_verdict = (f"全天{n_kept}笔：主动买入(call ask + put bid)${bull:.2f}M，"
                    f"主动卖出(put ask + call bid)${bear:.2f}M，mid ${mid_m:.2f}M（不计入主动买卖）；"
                    f"主动资金{lean}。")
    flow_items = []  # 旧桶格式已废弃，保留空列表兼容

    # summary 事实型
    if cw and pw:
        wall_pos = (f"开盘 {opx:.2f} 在 call wall {cw:.0f} 上方" if opx > cw else
                    f"开盘 {opx:.2f} 在 put wall {pw:.0f} 下方" if opx < pw else
                    f"开盘 {opx:.2f} 在双墙 {pw:.0f}–{cw:.0f} 之间")
    else:
        wall_pos = f"开盘 {opx:.2f}"
    vwap_pos = "收盘在 VWAP 上方" if cl > lv["vwap"] else "收盘在 VWAP 下方"
    wall_line = f"call {cw:.0f} / put {pw:.0f}（{cfg.get('wall_src', '')}口径）"
    if mig:
        wall_line += f"；heatmap 算法墙首→末：{'; '.join(mig)}"
    summary = [
        f"① {wall_pos}；全天 {hi:.2f}–{lo:.2f}，收 {cl:.2f}（{chg:+.2f}%）。",
        f"② 白点轨迹：开 {opx:.2f} → 高 {hi:.2f} → 低 {lo:.2f} → 收 {cl:.2f}。",
        f"③ 墙位：{wall_line}。",
        f"④ 资金流：{flow_verdict}",
        f"⑤ {vwap_pos}（VWAP {lv['vwap']:.2f}，EMA250 {lv['ema250']:.2f}）。",
    ]

    norm = {
        "ticker": "MU", "date": ds, "mmdd": ds[5:7] + ds[8:10], "weekday": weekday_cn(ds),
        "date_str": f"{ds[:4]}年{int(ds[5:7])}月{int(ds[8:10])}日 · {weekday_cn(ds)} · 日内报告",
        "tape": {"close": cl, "chg_pct": chg, "open": opx, "high": hi, "low": lo},
        "event": {"is_event": is_event, "tag": tag, "detail": detail},
        "junkman": {"status": jm_status, "meta": jm_meta, "full_text": jm_text},
        "chart": {"rel": f"charts/mu_{ds}.png",
                  "alt": f"MU {ds} 5分钟K线与{dte_label} dealer-GEX heatmap",
                  "caption": f"{dte_label}（{cfg['expiry']}到期），夜航社 {n_snaps} 档 5 分钟快照；白点为每档现货位置"},
        "chart_title": f"5 分钟 K 线 × {dte_label} heatmap",
        "heatmap_title": f"heatmap解读（{dte_label}，夜航社 {n_snaps} 档 5 分钟快照）",
        "heatmap_intro": heatmap_intro,
        "key_reads": key_reads,
        "top5": top5,
        "top5_method": f"亮度口径：p99 截断做上限；红框 = 全天该 strike 最大 |GEX| 时刻。",
        "flow_title": f"资金流（{dte_label}，全天{n_kept}笔；premium≥$20万｜size≥50｜DTE≤1｜side=B/M/A）",
        "flow_items": flow_items,
        "flow_trade_list": trade_list,
        "flow_verdict": flow_verdict,
        "summary": summary,
        "summary_title": "分析总结",
        "summary_sub": f"价格行为 × {dte_label} GEX 结构复盘",
        "method_note": "heatmap 亮度=平方根连续渐变（p99 裁剪）；K线=Yahoo Finance 5分钟；heatmap=夜航社 dealer-GEX 5分钟快照。",
        "data_note": "收盘后的静态快照；页面不在打开时更新行情。",
        "_raw": {"levels": lv, "cfg": cfg},
    }
    json.dump({k: v for k, v in norm.items() if not k.startswith("_")},
              open(os.path.join(MU_DIR, f"report_data_mu_{ds}.json"), "w"),
              ensure_ascii=False, indent=1)
    return norm


# ================= TSLA: 现有链 + 适配 =================
def tsla_build(ds):
    mmdd = ds[5:7] + ds[8:10]
    run([sys.executable, f"{TSLA_DIR}/build_day.py", ds])
    run([sys.executable, f"{TSLA_DIR}/analyze_day.py", ds])
    run([sys.executable, f"{TSLA_DIR}/gen_reads_daily.py", ds])
    run([sys.executable, f"{SCRIPTS}/auto_meta.py", ds])
    run([sys.executable, f"{TSLA_DIR}/assemble_daily_generic.py", ds])
    day = json.load(open(f"{TSLA_DIR}/{ds}/day_{ds}.json"))
    cw = day.get("call_wall")
    pw = day.get("put_wall")
    cmd = [sys.executable, f"{TSLA_DIR}/build_chart_daily.py", ds]
    if cw:
        cmd += [str(cw), str(pw or "")]
    run(cmd)
    rd = json.load(open(f"{TSLA_DIR}/{ds}/report_data_{ds}.json"))
    return adapt_tsla(rd, ds)


def adapt_tsla(rd, ds):
    tape, ev, jm = rd["tape"], rd["event"], rd["junkman"]
    hm, fl = rd["heatmap"], rd["flow"]
    top5a = rd.get("top5_analysis", [])
    top5 = []
    for i, n in enumerate(hm["top5"]):
        gex = n.get("max_gex_m", n.get("peak_m", 0))
        top5.append({"rank": i + 1, "strike": n["strike"], "gex_m": gex,
                     "et": n["et"], "pos": gex > 0,
                     "analysis": top5a[i] if i < len(top5a) else ""})
    # flow: 逐笔明细表（SPX 10min 同款格式）
    trade_list = []
    try:
        sys.path.insert(0, SCRIPTS)
        import publish as _pub
        raw = _pub.fetch_flow_stock(ds, "TSLA", min_premium_usd=200000)
        trade_list = _pub.build_trade_list(raw, tape["close"])
    except Exception as e:
        print(f"[tsla flow] 逐笔拉取失败: {e}", flush=True)
    return {
        "ticker": "TSLA", "date": ds, "mmdd": ds[5:7] + ds[8:10], "weekday": weekday_cn(ds),
        "date_str": f"{ds[:4]}年{int(ds[5:7])}月{int(ds[8:10])}日 · {weekday_cn(ds)} · 日内报告",
        "tape": {"close": tape["close"], "chg_pct": tape["chg_pct"], "open": tape["open"],
                 "high": tape["high"], "low": tape["low"]},
        "event": {"is_event": ev["is_event"], "tag": ev["tag"], "detail": ev.get("detail", "")},
        "junkman": {"status": jm["status"],
                    "meta": (f"{jm.get('posted_mdt', ds)} MDT｜Alpha夜航社 #junkman-analysis｜"
                             f"作者 junk_man（APP徽章）") if jm["status"] == "ready" else "",
                    "full_text": jm.get("full_text", "")},
        "chart": {"rel": f"charts/tsla_{ds}.png",
                  "alt": f"TSLA {ds} 5分钟K线与{rd['hm_label']} dealer-GEX heatmap",
                  "caption": f"{rd['hm_label']}，夜航社 {hm['snaps']} 档 5 分钟快照；白点为每档现货位置"},
        "chart_title": f"5 分钟 K 线 × {rd['hm_label']} heatmap",
        "heatmap_title": f"heatmap解读（{rd['hm_label']}，夜航社 {hm['snaps']} 档 5 分钟快照）",
        "heatmap_intro": hm.get("intro", ""),
        "key_reads": (list(hm.get("key_reads", {}).values())
                      if isinstance(hm.get("key_reads"), dict)
                      else list(hm.get("key_reads") or [])),
        "top5": top5,
        "top5_method": f"亮度口径：p99 截断 {hm.get('p99_clip_m', '?')}M；红框 = 全天该 strike 最大 |GEX| 时刻。",
        "flow_title": f"资金流（{rd['hm_label']}，全天{len(trade_list)}笔；premium≥$20万｜size≥50｜DTE≤1｜side=B/M/A）",
        "flow_items": fl["buckets"],
        "flow_trade_list": trade_list,
        "flow_verdict": fl["verdict"],
        "summary": rd["summary"],
        "summary_title": "分析总结",
        "summary_sub": "",
        "method_note": rd.get("method_note", ""),
        "data_note": rd.get("data_note", "收盘后的静态快照；页面不在打开时更新行情。"),
    }


# ================= SPX: 现有链 + 适配 =================
def spx_build(ds):
    mmdd = ds[5:7] + ds[8:10]
    run([sys.executable, f"{SPX_DIR}/build_day.py", ds])
    rd_path = f"{SPX_DIR}/{ds}/report_data_{ds}.json"
    rd = json.load(open(rd_path))
    # junkman: 文件查找补丁 (build_day 默认 pending)
    if rd.get("junkman", {}).get("status") != "ready":
        st, meta_line, text = junkman_lookup("spx", ds)
        if st == "ready":
            posted = meta_line.split(" MDT")[0]
            rd["junkman"] = {"status": "ready", "posted_mdt": posted,
                             "full_text": text,
                             "disclaimer": "盘前预测 · 情况未发生"}
            json.dump(rd, open(rd_path, "w"), ensure_ascii=False, indent=1)
            log(f"SPX {ds}: junkman 已从文件补入")
        else:
            log(f"SPX {ds}: junkman 未找到, 保留 pending 占位")
    wu = rd.get("wall_used") or {}
    cmd = [sys.executable, f"{SPX_DIR}/build_chart_spx.py", ds]
    if wu.get("call_wall"):
        cmd += [str(wu["call_wall"]), str(wu.get("put_wall") or "")]
    run(cmd)
    rd = json.load(open(rd_path))
    return adapt_spx(rd, ds)


def adapt_spx(rd, ds):
    k, ev, jm = rd["kline"], rd["event"], rd["junkman"]
    hm, fl = rd["heatmap"], rd["flow"]
    kr = hm["key_reads"]
    top5 = [{"rank": i + 1, "strike": n["strike"], "gex_m": n["max_gex_m"],
             "et": n["et"], "pos": n["max_gex_m"] > 0, "analysis": ""}
            for i, n in enumerate(hm["top_nodes"][:5])]
    dte = hm.get("dte_label", "0DTE")
    # 全天逐笔 flow（10min 表格式，premium≥$20万）
    trade_list = []
    try:
        sys.path.insert(0, SCRIPTS)
        import publish as _pub
        raw = _pub.fetch_flow_fullday(ds, min_premium_usd=200000)
        trade_list = _pub.build_trade_list(raw, k["close"])
    except Exception as e:
        print(f"[spx flow] 全天逐笔拉取失败: {e}", flush=True)
    return {
        "ticker": "SPX", "date": ds, "mmdd": ds[5:7] + ds[8:10], "weekday": weekday_cn(ds),
        "date_str": f"{ds[:4]}年{int(ds[5:7])}月{int(ds[8:10])}日 · {weekday_cn(ds)} · 日内报告",
        "tape": {"close": k["close"], "chg_pct": k["change_pct"], "open": k["official_open"],
                 "high": k["day_high"], "low": k["day_low"]},
        "event": {"is_event": ev["is_event"], "tag": ev["tag"], "detail": ev.get("detail", "")},
        "junkman": {"status": jm["status"],
                    "meta": (f"{jm.get('posted_mdt', ds)} MDT｜Alpha夜航社 #junkman-analysis｜"
                             f"作者 junk_man（APP徽章）") if jm["status"] == "ready" else "",
                    "full_text": jm.get("full_text", "")},
        "chart": {"rel": f"charts/spx_{ds}.png",
                  "alt": f"SPX {ds} 5分钟K线与{dte} dealer-GEX heatmap",
                  "caption": f"{dte}，夜航社 {hm['n_snapshots']} 档 5 分钟快照；白点为每档现货位置"},
        "chart_title": f"5 分钟 K 线 × {dte} heatmap",
        "heatmap_title": f"heatmap解读（{dte}，夜航社 {hm['n_snapshots']} 档 5 分钟快照）",
        "heatmap_intro": "",
        "key_reads": [kr["gold_bands"], kr["purple_bands"], kr["trajectory"], kr["tail_changes"]],
        "top5": top5,
        "top5_method": f"亮度口径：p99 截断；红框 = 全天该 strike 最大 |GEX| 时刻。",
        "flow_title": f"资金流（0DTE SPXW，全天{len(trade_list)}笔；premium≥$20万｜size≥50｜0DTE｜side=B/M/A）",
        "flow_items": fl["buckets_30min"],
        "flow_trade_list": trade_list,
        "flow_verdict": (f"主动资金{fl['bias']}（{fl['lean']}）：主动买入 ${fl['bull_premium_m']:.2f}M，"
                         f"主动卖出 ${fl['bear_premium_m']:.2f}M，mid ${fl['mid_premium_m']:.2f}M（不计入主动买卖）。"),
        "summary": rd["summary"],
        "summary_title": "分析总结",
        "summary_sub": f"价格行为 × {dte} GEX 结构复盘",
        "method_note": "heatmap 亮度=平方根连续渐变（p99 裁剪）；K线=Yahoo ^GSPC 5分钟＋ES成交量动态basis换算VWAP；heatmap=夜航社 dealer-GEX 5分钟快照。",
        "data_note": "收盘后的静态快照；页面不在打开时更新行情。",
    }

# ================= 构建封装 =================
def mu_build(ds):
    fetched = mu_fetch(ds)
    lv = fetched["levels"]
    cmd = [sys.executable, f"{SCRIPTS}/build_chart_mu.py", ds]
    if fetched["cfg"].get("call_wall"):
        cmd += [str(fetched["cfg"]["call_wall"]), str(fetched["cfg"].get("put_wall") or "")]
    run(cmd)
    return mu_assemble(ds, fetched)


BUILDERS = {"mu": mu_build, "tsla": tsla_build, "spx": spx_build}
CHART_SRC = {
    "mu": lambda ds: os.path.join(MU_DIR, "charts_v2", f"mu_charts_{ds[5:7]+ds[8:10]}.png"),
    "tsla": lambda ds: os.path.join(TSLA_DIR, ds, "charts", f"tsla_charts_{ds[5:7]+ds[8:10]}.png"),
    "spx": lambda ds: os.path.join(SPX_DIR, ds, "charts", f"spx_charts_{ds[5:7]+ds[8:10]}.png"),
}


def write_site(ticker, norm, ds):
    """写单日独立页 + 图表 + 数据归档. 幂等: 覆盖同名文件, 不产生重复."""
    cfg = TICKERS[ticker]
    site = cfg["site"]
    os.makedirs(os.path.join(site, "charts"), exist_ok=True)
    os.makedirs(os.path.join(site, "data"), exist_ok=True)
    # 图表
    src = CHART_SRC[ticker](ds)
    if not os.path.exists(src):
        raise RuntimeError(f"{ticker} {ds}: 图表缺失 {src}")
    dst = os.path.join(site, norm["chart"]["rel"])
    shutil.copyfile(src, dst)
    # 单日独立页
    html_path = os.path.join(site, f"{ds}.html")
    with open(html_path, "w", encoding="utf-8") as f:
        f.write(render_standalone(norm["ticker"], cfg["label"], norm))
    # 数据归档 (JSON)
    archive = {k: v for k, v in norm.items() if not k.startswith("_")}
    archive["chart_file"] = norm["chart"]["rel"]
    with open(os.path.join(site, "data", f"{ds}.json"), "w", encoding="utf-8") as f:
        json.dump(archive, f, ensure_ascii=False, indent=1)
    log(f"{ticker} {ds}: 已写入 {html_path}")
    return html_path


def main():
    if len(sys.argv) < 2:
        print(__doc__)
        sys.exit(2)
    ds = sys.argv[1]
    datetime.date.fromisoformat(ds)  # 校验
    _ensure_yhs_key()
    only = sys.argv[2].lower() if len(sys.argv) > 2 else None
    tickers = [only] if only else ["mu", "tsla", "spx"]
    for t in tickers:
        if t not in TICKERS:
            print(f"未知标的: {t}", file=sys.stderr)
            sys.exit(2)

    results = {}
    for t in tickers:
        try:
            norm = BUILDERS[t](ds)
            write_site(t, norm, ds)
            results[t] = "OK"
        except Exception as e:
            FAILURES.append(f"{t}: {e}")
            log(f"{t} {ds} 失败: {e} (留空占位, 不阻断其他标的)")

    # 重建索引 (只追加/重排, 永不删除旧日期)
    for t in tickers:
        try:
            dates, idx = build_index(TICKERS[t]["site"], TICKERS[t]["name"], TICKERS[t]["label"])
            log(f"{t}: 索引已重建, 共 {len(dates)} 个日期 (最新 {dates[0]})")
        except Exception as e:
            FAILURES.append(f"{t} 索引: {e}")

    # 自检
    missing = self_check({t: TICKERS[t]["site"] for t in tickers}, ds)
    print("\n===== 自检 =====")
    for t in tickers:
        print(f"  {t}: {results.get(t, '失败')}")
    if missing:
        print("缺失报告:")
        for m in missing:
            print(f"  !! {m}")
    if FAILURES:
        print("失败项:")
        for f in FAILURES:
            print(f"  !! {f}")
    if missing or FAILURES:
        print("RESULT: FAIL")
        sys.exit(1)
    print("RESULT: OK")


if __name__ == "__main__":
    main()
