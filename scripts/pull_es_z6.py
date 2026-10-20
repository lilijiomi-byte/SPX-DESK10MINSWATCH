#!/usr/bin/env python3
"""Databento 拉取 ESU6 1m 三段窗口 (8月初 / 9月 / 10月中), 供 ES/SPX 对照图.
key 从 stdin 第一行读入, 只在内存中使用.
每段先 metadata.get_cost 核算, 单段 >$5 中止.
输出: ~/workspace/trading_journal/spx_daily/raw_es/es_2026-08.csv 等.
"""
import sys, os, base64, urllib.request, urllib.parse, json, time

WINDOWS = [
    ("2026-09-09T00:00:00Z", "2026-09-23T00:00:00Z", "es_2026-09z.csv"),
    ("2026-10-06T00:00:00Z", "2026-10-17T04:00:00Z", "es_2026-10.csv"),
]
OUTDIR = os.path.expanduser("~/workspace/trading_journal/spx_daily/raw_es")

def call(url, key, params, retries=3):
    body = urllib.parse.urlencode(params).encode()
    auth = base64.b64encode(f"{key}:".encode()).decode()
    last = None
    for i in range(retries):
        try:
            req = urllib.request.Request(url, data=body, method="POST")
            req.add_header("Authorization", f"Basic {auth}")
            req.add_header("Content-Type", "application/x-www-form-urlencoded")
            with urllib.request.urlopen(req, timeout=300) as r:
                return r.read()
        except Exception as e:
            last = e
            print(f"retry {i+1}: {type(e).__name__}", flush=True)
            time.sleep(3)
    raise last

def main():
    key = sys.stdin.readline().strip()
    if not key:
        sys.exit("no key on stdin")
    os.makedirs(OUTDIR, exist_ok=True)
    total = 0.0
    for start, end, fname in WINDOWS:
        params = {"dataset": "GLBX.MDP3", "symbols": "ESZ6", "schema": "ohlcv-1m",
                  "start": start, "end": end, "stype_in": "raw_symbol"}
        cost = json.loads(call("https://hist.databento.com/v0/metadata.get_cost", key, params))
        c = cost if isinstance(cost, (int, float)) else (cost.get("cost_usd") or 0)
        print(f"{fname}: cost ${c:.4f}", flush=True)
        if float(c) > 5:
            sys.exit(f"成本异常 (${c}), 中止")
        total += float(c)
        raw = call("https://hist.databento.com/v0/timeseries.get_range", key,
                   {**params, "encoding": "csv"})
        with open(os.path.join(OUTDIR, fname), "wb") as fh:
            fh.write(raw)
        print(f"  saved {fname}: {len(raw)} bytes, ~{raw.count(bytes([10]))} lines", flush=True)
    print(f"total cost: ${total:.4f}", flush=True)

if __name__ == "__main__":
    main()
