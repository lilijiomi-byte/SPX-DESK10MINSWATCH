#!/usr/bin/env python3
"""intraday_lib: MU / SPX / TSLA 每日日内报告流水线的共享库.

- 模板样式: Library 三份导出 HTML 的白底版 (CSS 存于 scripts/intraday.css)
- render_article(norm): 把标准化报告 dict 渲染为 <article class="day-panel"> 片段
- render_standalone(ticker, label, norm): 单日独立页面
- build_index(ticker_dir, ticker, label): 扫描目录下 <date>.html, 按日期倒序生成 tab 索引页
  (只追加/重排, 永不删除旧日期文件)
- junkman_lookup(ticker, ds): 从已知位置查找 Junk-man 早盘原文, 找不到返回 pending
- event_lookup(ds): 事件日判定 (宏观日历 + 月度 OPEX)
- self_check(dirs, ds): 自检三个标的当日 HTML 是否生成
"""
import datetime
import html
import json
import os
import re
import sys

SCRIPTS = os.path.dirname(os.path.abspath(__file__))
WS = os.path.expanduser("~/workspace")

WEEK_CN = ["周一", "周二", "周三", "周四", "周五", "周六", "周日"]


def weekday_cn(ds):
    return WEEK_CN[datetime.date.fromisoformat(ds).weekday()]


def esc(s):
    return html.escape(str(s), quote=True)


def css_text():
    with open(os.path.join(SCRIPTS, "intraday.css"), encoding="utf-8") as f:
        return f.read()


# ---------- Junk-man 早盘原文解析 ----------
_JUNK_DIRS = [
    os.path.join(WS, "trading_journal"),
    os.path.join(WS, "mu_report"),
]


def junkman_lookup(ticker, ds):
    """返回 (status, meta_line, full_text).
    status: 'ready' | 'pending'. 找不到文件时绝不编造, 返回 pending.
    """
    tl = ticker.lower()
    for d in _JUNK_DIRS:
        p = os.path.join(d, f"junkman_{tl}_{ds}.txt")
        if os.path.exists(p):
            txt = open(p, encoding="utf-8").read().strip()
            lines = txt.splitlines()
            head = lines[0] if lines else ""
            body = "\n".join(lines[1:]).strip() if len(lines) > 1 else txt
            m = re.search(r"(\d{2}:\d{2})\s*MDT", head)
            posted = f"{ds} {m.group(1)}" if m else ds
            author = "junk_man" if "junk_man" in head else "Junk-man"
            meta = f"{posted} MDT｜Alpha夜航社 #junkman-analysis｜作者 {author}（APP徽章）"
            return "ready", meta, body or "（文件为空）"
    # SPX / TSLA 历史硬编码口径兜底 (已验证数据, 非编造)
    try:
        if tl == "spx":
            sys.path.insert(0, os.path.join(WS, "trading_journal", "spx_daily"))
            from fill_junkman_spx import J
            if ds in J:
                e = J[ds]
                meta = f"{e.get('posted_mdt', ds)} MDT｜Alpha夜航社 #junkman-analysis｜作者 junk_man（APP徽章）"
                return "ready", meta, e.get("full_text", "")
        if tl == "tsla":
            sys.path.insert(0, os.path.join(WS, "trading_journal", "tsla_3day"))
            from fill_junkman import J
            if ds in J:
                e = J[ds]
                meta = f"{e.get('posted_mdt', ds)} MDT｜Alpha夜航社 #junkman-analysis｜作者 junk_man（APP徽章）"
                return "ready", meta, e.get("full_text", "")
    except Exception:
        pass
    return "pending", "", ""


# ---------- 事件日 ----------
def _is_monthly_opex(ds):
    d = datetime.date.fromisoformat(ds)
    if d.weekday() != 4:  # 周五
        return False
    # 第三周五
    first = d.replace(day=1)
    offset = (4 - first.weekday()) % 7
    third_friday = first + datetime.timedelta(days=offset + 14)
    return d == third_friday


def bias_of(chg_pct):
    """当日涨跌 → 利多/利空/中性 (阈值 ±0.3%). chg_pct 为 None 时返回空串."""
    if chg_pct is None:
        return ""
    if chg_pct > 0.3:
        return "利多"
    if chg_pct < -0.3:
        return "利空"
    return "中性"


def event_lookup(ds, chg_pct=None):
    """返回 (is_event, tag, detail, bias). bias: 利多/利空/中性 (按当日实际涨跌判定)."""
    bias = bias_of(chg_pct)
    try:
        sys.path.insert(0, os.path.join(WS, "yhs_premarket", "daily"))
        from lib import event_day
        ed = event_day(ds)
        if ed:
            tag = ed.get("scan_banner", "事件日").replace("📌 事件日：", "").replace("📌 事件日", "事件日")
            tag = tag.split("；")[0].split("，")[0][:24] or "事件日"
            return True, "事件日", ed.get("brief_macro", ""), bias
    except Exception:
        pass
    if _is_monthly_opex(ds):
        d = datetime.date.fromisoformat(ds)
        return True, f"{d.month}月OPEX（月度期权到期日）", "月度期权到期日；大量期权到期行权，gamma钉住效应解除，尾盘波动可能放大。", bias
    return False, "非事件日", "", ""


# ---------- 渲染 ----------
def _updown(chg):
    return "up" if (chg or 0) >= 0 else "down"


def render_article(norm):
    t = norm["ticker"]
    tape = norm["tape"]
    chg = tape.get("chg_pct", 0) or 0
    cls = _updown(chg)
    sign = "+" if chg >= 0 else ""
    ev = norm["event"]
    jm = norm["junkman"]

    parts = []
    a = parts.append
    a(f'<article class="day-panel" id="day-{norm["mmdd"]}" role="tabpanel">')
    # mast
    a('<header class="mast"><div>')
    a(f'<h1>{esc(t)} 日内报告</h1>')
    a(f'<p class="date">{esc(norm["date_str"])}</p>')
    a('</div><div class="tape">')
    a(f'<strong>{tape["close"]:,.2f} <span class="{cls}">{sign}{chg:.2f}%</span></strong>')
    a(f'<span>开 {tape["open"]:,.2f} · 高 {tape["high"]:,.2f} · 低 {tape["low"]:,.2f}</span>')
    a('</div></header>')
    # event
    a('<aside class="event">')
    bias = ev.get("bias", "")
    bias_html = ""
    if ev["is_event"] and bias:
        bcls = "up" if bias == "利多" else ("down" if bias == "利空" else "")
        bias_html = f' <span class="event-bias {bcls}">· {esc(bias)}</span>'
    a(f'<div class="event-tag{" quiet" if not ev["is_event"] else ""}">{esc(ev["tag"])}{bias_html}</div>')
    a('<div>')
    a(f'<h2>{esc(ev["tag"])}{bias_html}</h2>')
    if ev.get("detail"):
        a(f'<p>{esc(ev["detail"])}</p>')
    a('</div></aside>')
    # 早盘分析
    a('<section aria-label="早盘分析">')
    a('<div class="section-head"><h2>早盘分析</h2><div class="source">')
    if jm["status"] == "ready":
        a(f'<p>{esc(jm["meta"])}</p>')
    else:
        a('<p>（Junk-man 早盘简报未抓取到，留空待补）</p>')
    a('</div></div>')
    if jm["status"] == "ready":
        a('<div class="brief"><pre class="verbatim">')
        a(esc(jm["full_text"]))
        a('</pre></div>')
        a('<p class="data-note">盘前预测 · 情况未发生</p>')
    else:
        a('<div class="brief"><p>（待补充 Junk-man 当日 #junkman-analysis 原文）</p></div>')
    a('</section>')
    # 图表 + heatmap 解读
    a('<section aria-label="K线与heatmap">')
    a(f'<div class="section-head"><h2>{esc(norm["chart_title"])}</h2><div class="source">')
    a(f'<p>{esc(norm["chart"]["caption"])}</p>')
    a('</div></div>')
    a(f'<figure class="chart-shell"><img src="{esc(norm["chart"]["rel"])}" alt="{esc(norm["chart"]["alt"])}" loading="lazy"></figure>')
    a('<div class="gex-intro">')
    a(f'<h3>{esc(norm["heatmap_title"])}</h3>')
    if norm.get("heatmap_intro"):
        a(f'<p>{esc(norm["heatmap_intro"])}</p>')
    if norm.get("key_reads"):
        a('<ul>')
        for kr in norm["key_reads"]:
            a(f'<li>{esc(kr)}</li>')
        a('</ul>')
    a('</div>')
    # TOP5
    a('<div class="top5-wrap"><h3>全天最大节点 TOP5</h3><div class="top5">')
    for i, n in enumerate(norm["top5"], 1):
        pos = n.get("pos", n.get("gex_m", 0) > 0)
        a(f'<article class="node{" purple" if not pos else ""}">')
        a(f'<span class="node-rank">{"①②③④⑤"[i-1]} · {esc(n.get("et", ""))}</span>')
        a(f'<strong>{n["strike"]}</strong>')
        a(f'<span class="metric">{n["gex_m"]:+.1f}M</span>')
        if n.get("analysis"):
            a(f'<p class="node-analysis">{esc(n["analysis"])}</p>')
        a('</article>')
    a('</div></div>')
    if norm.get("top5_method"):
        a(f'<div class="method"><p>{esc(norm["top5_method"])}</p></div>')
    # flow
    a('<div class="flow-summary">')
    a(f'<h3>{esc(norm["flow_title"])}</h3>')
    tl = norm.get("flow_trade_list")
    if tl:
        # 10min 同款逐笔明细表
        a('<div class="flow-table-wrap"><table class="flow-table">')
        a('<thead><tr><th>时间</th><th>Strike</th><th>C/P</th><th>DTE</th><th>Fill</th>'
          '<th>Bid/Ask</th><th>Side</th><th>Size</th><th>Prem</th><th>Vol</th><th>OI</th></tr></thead><tbody>')
        for t in tl:
            cls = "tr-call" if t.get("right") == "Call" else "tr-put"
            cp = "CALL" if t.get("right") == "Call" else "PUT"
            cp_c = "#158353" if t.get("right") == "Call" else "#c83c43"
            bid = f'{t["bid"]:.2f}' if t.get("bid") is not None else "—"
            ask = f'{t["ask"]:.2f}' if t.get("ask") is not None else "—"
            fill = f'${t["fill"]:.2f}' if t.get("fill") is not None else "—"
            a(f'<tr class="{cls}"><td>{esc(str(t.get("t", "")))}</td>'
              f'<td><b>{t.get("strike", "")}</b></td>'
              f'<td><b style="color:{cp_c}">{cp}</b></td>'
              f'<td>{t.get("dte", "")}</td><td>{fill}</td>'
              f'<td>{bid} / {ask}</td>'
              f'<td><span class="side-pill">{esc(str(t.get("side", "")))}</span></td>'
              f'<td>{t.get("size", "")}</td>'
              f'<td><b>${t.get("prem_m", 0):.3f}M</b></td>'
              f'<td>{t.get("vol", "")}</td><td>{t.get("oi", "")}</td></tr>')
        a('</tbody></table></div>')
        # footnote 按标的区分筛选口径
        tk = norm.get("ticker", "SPX")
        if tk == "SPX":
            fnote = ("筛选：09:30–16:00 ET ｜ premium ≥ $20万 ｜ size ≥ 50 ｜ 0DTE ｜ SPXW ｜ 单腿 ｜ "
                     "side=B/M/A ｜ kind=order ｜ YHS /v1/flow/trades。")
        else:
            fnote = (f"筛选：09:30–16:00 ET ｜ premium ≥ $20万 ｜ size ≥ 50 ｜ DTE≤1 ｜ {tk} ｜ 单腿 ｜ "
                     "side=B/M/A ｜ kind=order ｜ YHS /v1/flow/trades。")
        fnote += "ask=主动买（call买看多/put买看空），bid=主动卖（call卖看空/put卖看多）。mid 单独列示，不计入主动买卖。"
        a(f'<div class="footnote">{fnote}</div>')
    elif norm.get("flow_items"):
        a('<ul>')
        for it in norm["flow_items"]:
            a(f'<li>{esc(it)}</li>')
        a('</ul>')
    else:
        a('<p>（当日无符合筛子的 flow 记录，或数据未拉取到）</p>')
    if norm.get("flow_verdict"):
        a(f'<p class="verdict">{esc(norm["flow_verdict"])}</p>')
    a('</div>')
    a('</section>')
    # 分析总结
    a('<section aria-label="分析总结">')
    a('<div class="section-head">')
    a(f'<h2>{esc(norm.get("summary_title", "分析总结"))}</h2>')
    if norm.get("summary_sub"):
        a(f'<p>{esc(norm["summary_sub"])}</p>')
    a('</div>')
    a('<div class="summary-list">')
    for s in norm["summary"]:
        a(f'<article class="summary-item"><p>{esc(s)}</p></article>')
    a('</div></section>')
    if norm.get("method_note"):
        a(f'<p class="data-note">{esc(norm["method_note"])}</p>')
    a(f'<p class="data-note">{esc(norm.get("data_note", "收盘后的静态快照；页面不在打开时更新行情。"))}</p>')
    a('</article>')
    return "\n".join(parts)


TAB_JS = """
<script>
(function(){
  var tabs=[...document.querySelectorAll('.day-tab')];
  var panels=[...document.querySelectorAll('.day-panel')];
  function show(id,focus){
    panels.forEach(function(p){p.hidden=(p.id!==id);});
    tabs.forEach(function(t){
      var on=t.dataset.target===id;
      t.setAttribute('aria-selected',on?'true':'false');
      if(on&&focus)t.focus();
    });
    if(history.replaceState)history.replaceState(null,'','#'+id);
  }
  tabs.forEach(function(t){t.addEventListener('click',function(){show(t.dataset.target,false);});});
  var h=(location.hash||'').slice(1);
  var first=tabs.length?tabs[0].dataset.target:null;
  var init=(h&&document.getElementById(h))?h:first;
  if(init)show(init,false);
})();
</script>
"""


def page_head(title):
    return (f'<!doctype html>\n<html lang="zh">\n<head>\n<meta charset="utf-8">\n'
            f'<meta name="viewport" content="width=device-width,initial-scale=1">\n'
            f'<meta name="robots" content="noindex,nofollow">\n'
            f'<title>{esc(title)}</title>\n<style>\n{css_text()}\n</style>\n</head>\n<body>\n<main>\n')


def page_foot():
    return '</main>\n' + TAB_JS + '\n</body>\n</html>\n'


def sitenav(active):
    """全站单色导航条. active: HOME/SPX/MU/TSLA/META/SPX10."""
    items = [("../index.html", "首页", "HOME"),
             ("../spx-intraday/index.html", "SPX 日内报告", "SPX"),
             ("../spx-10min/index.html", "SPX 尾盘10分钟", "SPX10"),
             ("../mu/index.html", "MU 日内报告", "MU"),
             ("../tsla/index.html", "TSLA 日内报告", "TSLA"),
             ("../meta/index.html", "META 日内报告", "META")]
    links = "".join(
        f'<a href="{href}"{" class=\"on\"" if key == active else ""}>{label}</a>'
        for href, label, key in items)
    return f'<nav class="sitenav">{links}</nav>'


def render_standalone(ticker, label, norm):
    """单日独立页面 (无 tab, 带返回索引链接)."""
    h = [page_head(f'{label} {norm["date"]} 日内报告')]
    h.append(sitenav(ticker))
    h.append(f'<p style="margin:0 0 12px"><a href="index.html">← {esc(label)} 全部日期</a></p>')
    h.append(render_article(norm))
    h.append(page_foot())
    return "\n".join(h)



# 二十四节气 (公历日期, 每年大致固定)
SOLAR_TERMS = {
    # 2026
    "2026-01-05": "小寒", "2026-01-20": "大寒", "2026-02-04": "立春",
    "2026-02-18": "雨水", "2026-03-05": "惊蛰", "2026-03-20": "春分",
    "2026-04-04": "清明", "2026-04-20": "谷雨", "2026-05-05": "立夏",
    "2026-05-21": "小满", "2026-06-06": "芒种", "2026-06-21": "夏至",
    "2026-07-07": "小暑", "2026-07-22": "大暑", "2026-08-07": "立秋",
    "2026-08-23": "处暑", "2026-09-07": "白露", "2026-09-23": "秋分",
    "2026-10-08": "寒露", "2026-10-23": "霜降", "2026-11-07": "立冬",
    "2026-11-22": "小雪", "2026-12-07": "大雪", "2026-12-22": "冬至",
    # 2027
    "2027-01-05": "小寒", "2027-01-20": "大寒",
}

def _solar_term_mark(ds):
    """节气日返回节气名, 否则空. 用于日期 tab 蓝色标注."""
    return SOLAR_TERMS.get(ds, "")

def _important_event_mark(ds):
    """只标注重要事件 (OPEX/FOMC/非农), CPI 等次要事件不标. 返回短标签或空."""
    d = datetime.date.fromisoformat(ds)
    # 月度 OPEX: 第三个周五
    if _is_monthly_opex(ds):
        return "OPEX"
    # FOMC / 非农: 查宏观日历
    try:
        sys.path.insert(0, os.path.join(WS, "yhs_premarket", "daily"))
        from lib import EVENT_DAYS
        ed = EVENT_DAYS.get(ds)
        if ed:
            b = ed.get("brief_macro", "")
            if "FOMC" in b or "议息" in b or "利率决议" in b:
                return "FOMC"
            if "非农" in b:
                return "非农"
    except Exception:
        pass
    return ""


def build_index(ticker_dir, ticker, label):
    """扫描 ticker_dir 下的 <YYYY-MM-DD>.html, 按日期倒序生成 index.html.
    只追加/重排 tab, 永不删除任何旧日期文件.
    返回 (dates, index_path)."""
    files = []
    for f in sorted(os.listdir(ticker_dir)):
        if re.fullmatch(r"\d{4}-\d{2}-\d{2}\.html", f):
            files.append(f)
    dates = sorted((f[:-5] for f in files), reverse=True)
    if not dates:
        raise RuntimeError(f"{ticker_dir}: 没有找到任何日期报告, 索引无法生成")
    # 从各日期文件读取 tape 行做 tab 标签：显示当日最高-最低点差（不用涨跌%）
    tabs = []
    for ds in dates:
        d = datetime.date.fromisoformat(ds)
        wd = WEEK_CN[d.weekday()]
        label_extra = ""
        try:
            ht = open(os.path.join(ticker_dir, f"{ds}.html"), encoding="utf-8").read()
            m = re.search(r'高\s*([\d,\.]+)\s*·\s*低\s*([\d,\.]+)', ht)
            if m:
                hi = float(m.group(1).replace(",", ""))
                lo = float(m.group(2).replace(",", ""))
                rng = hi - lo
                mc = re.search(r'<span class="(up|down)">', ht)
                cls = mc.group(1) if mc else "up"
                label_extra = f' <span class="tab-move {cls}">{rng:,.2f}</span>'
        except Exception:
            pass
        # 重要事件标记 (只 OPEX/FOMC/非农)
        mark = _important_event_mark(ds)
        mark_html = f' <span class="tab-event">{mark}</span>' if mark else ""
        solar = _solar_term_mark(ds)
        if solar:
            mark_html += f' <span class="tab-solar">{solar}</span>' 
        tabs.append((ds, f'{d.month}/{d.day} {wd}{mark_html}{label_extra}'))
    h = [page_head(f"{label}（按日期）")]
    h.append(sitenav(ticker))
    h.append(f'<h1 style="margin:0 0 4px">{esc(label)}</h1>')
    h.append('<p class="data-note">历史数据库：每日收盘后追加当日报告，历史日期永久保留。</p>')
    h.append('<nav class="day-tabs" role="tablist" aria-label="日期">')
    for i, (ds, lab) in enumerate(tabs):
        mmdd = ds[5:7] + ds[8:10]
        h.append(f'<a class="day-tab" role="tab" data-target="day-{mmdd}" '
                 f'aria-selected="{"true" if i == 0 else "false"}" href="#day-{mmdd}">{lab}</a>')
    h.append('</nav>')
    # 日期 tab 收缩: 超过10个日期时默认收起, 手动按钮展开/收起 (不自动收起)
    if len(tabs) > 10:
        n = len(tabs)
        js = ("<button class=\"tabs-toggle\" type=\"button\">展开全部 %d 个日期 ▾</button>\n"
              "<script>(function(){var nav=document.querySelector('.day-tabs');if(!nav)return;"
              "var btn=document.querySelector('.tabs-toggle');var n=%d;nav.classList.add('collapsed');"
              "function setC(c){nav.classList.toggle('collapsed',c);"
              "btn.innerHTML=c?('展开全部 '+n+' 个日期 \\u25be'):'收起 \\u25b4';}"
              "btn.onclick=function(){setC(!nav.classList.contains('collapsed'));};})();</script>" % (n, n))
        h.append(js)
    # 日期 tab 切换: 点击 tab 显示对应面板 (之前缺这段 JS, 点 tab 只跳锚点导致空白)
    h.append(
        "<script>(function(){var tabs=document.querySelectorAll('.day-tab');if(!tabs.length)return;"
        "function show(id){document.querySelectorAll('.day-panel').forEach(function(p){"
        "p.hidden=(p.id!==id);});tabs.forEach(function(t){"
        "t.setAttribute('aria-selected',t.getAttribute('data-target')===id?'true':'false');});"
        "var el=document.getElementById(id);if(el)el.scrollIntoView();}"
        "tabs.forEach(function(t){t.addEventListener('click',function(e){e.preventDefault();"
        "show(t.getAttribute('data-target'));history.replaceState(null,'','#'+t.getAttribute('data-target'));});});"
        "var h=location.hash.slice(1);if(h&&document.getElementById(h))show(h);})();</script>"
    )
    for i, (ds, _) in enumerate(tabs):
        ht = open(os.path.join(ticker_dir, f"{ds}.html"), encoding="utf-8").read()
        m = re.search(r'(<article class="day-panel".*?</article>)\s*</main>', ht, re.S)
        if not m:
            m = re.search(r'(<article class="day-panel".*?</article>)', ht, re.S)
        art = m.group(1) if m else f"<p>（{ds} 文章解析失败）</p>"
        if i > 0:
            art = art.replace('<article class="day-panel"', '<article class="day-panel" hidden', 1)
        h.append(art)
    h.append(page_foot())
    idx = os.path.join(ticker_dir, "index.html")
    with open(idx, "w", encoding="utf-8") as f:
        f.write("\n".join(h))
    return dates, idx


def self_check(ticker_dirs, ds):
    """自检: 每个标的目录下 <ds>.html 必须存在. 缺失则返回错误列表."""
    missing = []
    for name, d in ticker_dirs.items():
        p = os.path.join(d, f"{ds}.html")
        if not os.path.exists(p):
            missing.append(f"{name}: {p} 缺失")
    return missing
