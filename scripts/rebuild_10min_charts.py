#!/usr/bin/env python3
"""重画 9/1-10/19 全部 10min 图: 紧凑纵坐标 + 前日线"""
import json, os, sys
sys.path.insert(0, os.path.expanduser("~/workspace/spx-desk/scripts"))
from publish import draw_chart

DATA = os.path.expanduser("~/workspace/spx-desk/spx-10min/data")
CHARTS = os.path.join(DATA, "charts")

# 获取所有日期
import glob
dates = []
for f in glob.glob(os.path.join(DATA, "2026-*.json")):
    ds = os.path.basename(f)[:-5]
    if ds >= "2026-09-01":
        dates.append(ds)
dates.sort()

print(f"共 {len(dates)} 天", flush=True)
for ds in dates:
    try:
        d = json.load(open(os.path.join(DATA, f"{ds}.json")))
        bars = d.get("bars_1m", [])
        if not bars:
            print(f"{ds}: 无 bars", flush=True)
            continue
        # 重建 vwap/ema 为水平线 (JSON 里只有终值)
        n = len(bars)
        vwap_seg = []
        ema_seg = []
        if d.get("vwap"):
            vwap_seg = [(i, d["vwap"]) for i in range(n)]
        if d.get("ema250"):
            ema_seg = [(i, d["ema250"]) for i in range(n)]
        mmdd = ds[5:7] + ds[8:10]
        out_path = os.path.join(CHARTS, f"spx_1m_{mmdd}.png")
        draw_chart(ds, bars, vwap_seg, ema_seg,
                   out_path,
                   prev_h=d.get("prev_h"), prev_l=d.get("prev_l"))
        print(f"✓ {ds}", flush=True)
    except Exception as e:
        print(f"{ds}: 失败 {e}", flush=True)
