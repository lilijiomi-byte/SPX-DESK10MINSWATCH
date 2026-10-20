#!/usr/bin/env python3
"""Databento 拉取 ESU6 1m (2026-07-21 -> 2026-08-01 UTC).
key 从 stdin 第一行读入, 只在内存中使用, 不落盘/不打印.
流程: 先 metadata.get_cost 核算, 成本异常高则中止; 再 timeseries.get_range 存 CSV.
输出: ~/workspace/trading_journal/spx_daily/raw_es/es_2026-07.csv
"""
import sys, os, base64, urllib.request, urllib.parse, json, time

START = "2026-07-21T00:00:00Z"
END = "2026-08-01T00:00:00Z"
OUT = os.path.expanduser("~/workspace/trading_journal/spx_daily/raw_es/es_2026-07.csv")
PARAMS = {
    "dataset": "GLBX.MDP3",
    "symbols": "ESU6",
    "schema": "ohlcv-1m",
    "start": START,
    "end": END,
    "stype_in": "raw_symbol",
}

def call(url, key, extra=None, retries=3):
    data = dict(PARAMS)
    if extra:
        data.update(extra)
    body = urllib.parse.urlencode(data).encode()
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
    cost_raw = call("https://hist.databento.com/v0/metadata.get_cost", key)
    cost = json.loads(cost_raw)
    print("get_cost:", cost, flush=True)
    c = cost if isinstance(cost, (int, float)) else \
        (cost.get("cost_usd") or cost.get("cost") or 0)
    if float(c) > 5:
        sys.exit(f"成本异常 (${c}), 中止, 未拉数")
    raw = call("https://hist.databento.com/v0/timeseries.get_range", key,
               extra={"encoding": "csv"})
    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    with open(OUT, "wb") as fh:
        fh.write(raw)
    lines = raw.count(b"\n")
    print(f"saved {OUT}: {len(raw)} bytes, ~{lines} lines", flush=True)

if __name__ == "__main__":
    main()
