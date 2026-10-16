# SPX-DESK10MINSWATCH

SPX 尾盘10分钟总结面板：每个交易日收盘后，自动发布当日 15:40–16:00 ET（美东）
的 SPX 走势总结。纯静态站（GitHub Pages），无构建步骤，白底风格（对标 MU 日内报告）。

页面结构（index.html，数据来自 `data/<YYYY-MM-DD>.json`）：
- ① 1分钟K线图：PNG（`data/charts/spx_1m_<MMDD>.png`），15:40–15:50 灰底，紫色 VWAP 实线＋蓝色 EMA250 实线，图下数据行列数值
- ② 10分钟明细表：15:50–16:00，10列×4行（O/H/L/C），15:55 处分两波
- ③ heatmap 快照：占位，有 `data/heatmap_<DS>.png` 即显示，否则显示"待补充"
- ④ flow：15:30–16:00 call/put×ask/bid premium 汇总（YHS /v1/flow/trades，premium≥$200k｜size≥50｜DTE≤1｜side=B/M/A），mid 单独列
- ⑤ 策略判断：现价 vs VWAP/EMA250 → 尾盘目标方向 ＋ flow 偏向（辅助判断，非入场指令）
- ⑥ 一句话总结：`note` 字段，GitHub 网页端可改

- `data/days.json` — 日期列表；`data/<YYYY-MM-DD>.json` — 当日数据
- `scripts/publish.py` — 生成当日数据：`python3 scripts/publish.py YYYY-MM-DD`（幂等）
- `MAINTENANCE.md` — 中文运维手册

## 隐私说明

本站为**公开但未宣传（unlisted）**：仓库是公开的（GitHub 免费 Pages 的要求），
但靠"不分享链接 ＋ 不起眼的库名 ＋ noindex（禁止搜索引擎收录）"实现低调访问。
报告内容仅为 SPX 点位与市场结构，不含账户/金额等敏感信息。请勿对外分享链接。
