# SPX 尾盘10分钟 · 运维手册

给同事看的：不需要懂代码，按下面做就行。

## 这个网站是什么

每天美东收盘后，自动生成一份"SPX 尾盘10分钟（15:50–16:00 ET）"总结，
发布到这个 GitHub 仓库，网站自动更新。地址形如
`https://<用户名>.github.io/SPX-DESK10MINSWATCH/`。

## ① 日常：不用管

数据每天收盘后由定时任务自动生成、自动推送，你什么都不用做。
只要网站上出现了当天的日期 tab，就是正常的。

## ② 如果某天网站没更新（没出现当天日期），三步手动补

在 GitHub 网页上操作，不用装任何软件：

**第 1 步：确认是不是真缺数据**
- 打开仓库 → 点进 `data` 文件夹 → 打开 `days.json`
- 看 `"days"` 列表里有没有当天日期（如 `"2026-10-15"`）
- 有 = 数据已发布，只是网站缓存，强制刷新浏览器（Ctrl+Shift+R）即可
- 没有 = 继续第 2 步

**第 2 步：找负责人跑一次生成脚本**
（需要能操作那台跑数据的电脑的人执行）
```bash
python3 scripts/publish.py 2026-10-15   # 把日期换成缺失的那天
```
跑完检查 `data/2026-10-15.json` 是否生成、`data/days.json` 里是否多了该日期。

**第 3 步：推送到 GitHub**（推送走 GitHub API，不是 git push——见⑥）
```bash
git -C ~/workspace/spx-desk add -A
git -C ~/workspace/spx-desk commit -m "spx 10min 2026-10-15"
python3 ~/workspace/spx-desk/scripts/gh_push.py
```
推送后等 1–2 分钟，网站自动更新。

## ③ 怎么改"一句话总结"

总结文字存在 `data/<日期>.json` 的 `note` 字段里，纯网页操作：

1. 打开仓库 → `data` 文件夹 → 点开对应日期的 json 文件（如 `data/2026-10-15.json`）
2. 右上角 ✏️（铅笔）点编辑
3. 找到 `"note":""`，把引号中间填上总结文字，例如 `"note":"尾盘缩量磨顶，flow 偏多但没量"`
   - 注意：引号用英文双引号，文字里不要出现英文双引号
4. 页面下方点 **Commit changes** → 网站 1–2 分钟后更新

## ④ heatmap 快照：用户提供后这样传

heatmap 区现在是占位（显示"待补充"）。用户把 PNG 图做好后：

1. 仓库首页点 **Add file → Upload files**，把 PNG 拖进去
2. 传之前先把文件名改成 `heatmap_<日期>.png`，例如 `heatmap_2026-10-15.png`
3. Commit 后网站对应日期的③区自动显示这张图（不用改代码）

## ⑤ flow 区说明

flow 数据是生成脚本每天收盘后自动从夜航社（YHS）拉的，不用手动管：
- 筛选：premium ≥ $20万 ｜ size ≥ 50 ｜ 0DTE ｜ SPXW ｜ 单腿 ｜ side=B/M/A ｜ kind=order
- 时段：15:30–16:00 ET，符合条件的**逐笔按时间顺序列出**（Call 行浅绿、Put 行浅红）
- 每笔含时间/方向/ask-bid/strike＋OTM/ATM/ITM/权利金/张数/均价/DTE；mid 成交单独列、不计入主动买卖
- 如果某天显示"无符合筛选的大单"，是那 30 分钟里真没有达标的大单，不是故障

## ⑥ 几个名词解释（一句话版）

- **推送认证**：跑数据的电脑用 PAT（个人访问令牌）往仓库推送，令牌存在安全保险库里，不在仓库里。推送失败 → 找负责人重配令牌。
- **推送方式**：`scripts/gh_push.py` 走 GitHub API（blobs→tree→commit→更新 main）推送，不用 `git push`——因为保险库的凭证中转标识（hsurr）在 git HTTPS 协议下不被接受，会 401。以后修任何推送问题先看这个脚本，不要换回 git push。
- **用户名拼写陷阱**：正确账号是 `lilijiomi-byte`（l-i-l-i-j-i-o-m-i），曾有人手误写成 `liljiomi-byte`（少一个 i），导致 API 全 404。排查时永远用 `/user/repos` 接口机械地取 `full_name`，不要手打用户名。
- **GitHub Pages**：仓库 Settings → Pages 里开的免费网站托管。Source 选 **Deploy from a branch**，Branch 选 **main** / **(root)**，保存后等 1–2 分钟生效。
- **noindex**：网站 head 里有一行不让搜索引擎收录的代码，本站靠"不分享链接"保持低调，不要把链接发到公开地方。

## ⑦ 常见问题

| 现象 | 原因 / 处理 |
|---|---|
| 某天 flow 显示"无符合筛选的大单" | 15:30–16:00 ET 内真没有达标大单，不是故障（见⑤） |
| 某天 K线图没出来 | Yahoo 当天 1m 数据没拉到；等第二天自动恢复，或按②手动补 |
| 某天只有 K线、VWAP/EMA250 是"—" | ES=F 数据缺失导致口径算不出；K线本身不受影响 |
| heatmap 区一直显示"待补充" | 还没传图，按④传 `heatmap_<日期>.png` 即可 |
| 改了 note 网站没变 | 等 1–2 分钟；还不行就强制刷新（Ctrl+Shift+R） |
| push 被拒绝 | 认证凭证可能过期，按⑥重配 |
