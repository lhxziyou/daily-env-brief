# -*- coding: utf-8 -*-
"""
每日环保简报渲染 + 双通道推送引擎（云/本地两线共用）

读取 brief_data.json -> 生成朋友圈海报 PNG + 微信推送版(可点击)
-> 推送：PushPlus（公众号/服务通知）+ WxPusher（App/Topic），按 token 存在情况自动选择通道

双线约定（详见 云端部署实施计划.md）：
- 云线：GitHub Actions 跑 brief_crawl.py + 本脚本，PUSH_CHANNELS 默认 pushplus
- 本地线：WorkBuddy 自动化跑（WebSearch 生成数据）+ 本脚本，PUSH_CHANNELS 默认 pushplus,wxpusher
- 两套线共用本文件与 effective_calendar.json，故任一线修正都自动同步到另一线
- DATA_PATH：数据文件名（默认 brief_data.json；云线建议 brief_data_cloud.json 防冲突）
- LINE：输出子目录后缀（默认空；云线可设 cloud / 本地线设 local，避免同目录互相覆盖）
"""
import json, os, sys, csv, hashlib, urllib.request, datetime, re

BASE = os.path.dirname(os.path.abspath(__file__))
CFG_PATH = os.path.join(BASE, "wxpusher_config.json")
DATA_PATH = os.path.join(BASE, os.environ.get("DATA_PATH", "brief_data.json"))
HISTORY_PATH = os.path.join(BASE, "history", "items_history.json")
CSV_PATH = os.path.join(BASE, "汇总.csv")
QUOTES_PATH = os.path.join(BASE, "quotes.json")
CALENDAR_PATH = os.path.join(BASE, "effective_calendar.json")

CAT_CLASS = {
    "法规·标准·指南（近期发布）": "",
    "政策征求意见（即将落地预警）": "warn",
    "专家/职称/鉴定人公示": "expert",
    "生态环保督察通报": "inspect",
    "招投标与项目机会": "tender",
    "环保大事记": "event",
    "环保处罚（典型/警示）": "penalty",
    "典型案例与执法": "case",
    "培训/宣贯/继续教育": "train",
    "资质与机构动态(CMA/名录)": "qual",
    "科技与新技术规范": "tech",
    "政策法规官方解读": "read",
    "环评/验收专项动态": "accept",
    "典型突发环境事件案例": "accident",
    "环境损害司法鉴定典型案例": "judicial",
}
CAT_ORDER = list(CAT_CLASS.keys())

# 旧归档栏目黑名单（生态环境部 xxgk2018/xxgk/xxgk0X 为 2018 信息公开旧框架，已迁至 /zcwj/）
# 详情页旧 URL 仍有效可保留，但抓取源绝不可回退到这些栏目
LEGACY_ARCHIVE_RE = re.compile(r'xxgk2018/xxgk/xxgk0[0-9]', re.I)

def url_hash(u):
    return hashlib.md5(u.strip().encode("utf-8")).hexdigest()[:12]

def normalize_url(u):
    """把官方繁体网关地址统一转简体入口；旧归档详情页保留（仍有效），仅告警"""
    if not u:
        return u
    # 生态环境部 big5 繁体网关 -> 简体官网
    if "big5.mee.gov.cn/gate/big5/" in u:
        u = u.replace("big5.mee.gov.cn/gate/big5/", "")
        if u.startswith("http://"):
            u = "https://" + u[7:]
        if not u.startswith("https://"):
            u = "https://" + u
    # 注：详情页 URL 含 xxgk2018/xxgk/xxgk0X 属 MEE 正常文档地址，保留不告警；
    #     真正的旧归档"栏目"守卫在 brief_crawl.py 的抓取源处。
    return u

def get_quote(data):
    """优先取 brief_data.json 里的 quote，否则按一年中的第几天从 quotes.json 轮询"""
    if data.get("quote"):
        return data["quote"]
    if not os.path.exists(QUOTES_PATH):
        return ""
    quotes = load_json(QUOTES_PATH)
    if not quotes:
        return ""
    from datetime import datetime as _dt
    try:
        dt = _dt.strptime(data.get("date", "2026-01-01"), "%Y-%m-%d")
        day = dt.timetuple().tm_yday % len(quotes)
    except Exception:
        day = 0
    return quotes[day]

def load_json(p):
    with open(p, encoding="utf-8") as f:
        return json.load(f)

def load_effective_calendar():
    if not os.path.exists(CALENDAR_PATH):
        return []
    try:
        d = load_json(CALENDAR_PATH)
        return d.get("entries", []) if isinstance(d, dict) else d
    except Exception as e:
        print(f"[calendar] 读取失败: {e}")
        return []

def get_today_effective(today_str):
    """返回今日正式实施的条目列表（effective_date == 当日）"""
    out = []
    for e in load_effective_calendar():
        if str(e.get("effective_date", "")).strip() == today_str:
            out.append({
                "title": e.get("title", ""),
                "url": e.get("url", "") or "",
                "source": e.get("source", ""),
                "note": e.get("note", ""),
            })
    return out

def _parse_date(s):
    """把 pub_date 字符串解析为 date；支持 YYYY-MM-DD 与常见变体"""
    if not s:
        return None
    s = str(s).strip()
    for fmt in ("%Y-%m-%d", "%Y/%m/%d", "%Y.%m.%d", "%Y-%m-%d %H:%M:%S"):
        try:
            return datetime.datetime.strptime(s[:len(fmt.split()[0]) + (fmt.count(' ') and len(s.split()[1]) or 0)] if ' ' in fmt else s[:10], fmt).date()
        except Exception:
            continue
    # 尝试正则
    m = re.search(r'(20\d{2})\D(0?[1-9]|1[0-2])\D(0?[1-9]|[12]\d|3[01])', s)
    if m:
        try:
            return datetime.date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
        except Exception:
            return None
    return None

# 参考/知识型板块：全国搜罗、发布不频繁，放宽到近30天，不受24h限制
EXEMPT_RECENT = {"典型突发环境事件案例", "环境损害司法鉴定典型案例"}

def filter_recent_24h(items, today_str):
    """
    过滤条目，只保留发布时间在 [today-1天, today+1天) 内的消息。
    简报以每日新闻为主，旧闻（>24h）不硬填；周一回顾模块单独走 weekly_review。
    pub_date 为空或无法解析的条目默认保留（避免误丢）。
    例外：典型突发环境事件案例 / 环境损害司法鉴定典型案例 属参考型板块，
    面向全国搜罗、发布不频繁，放宽到近30天。
    """
    try:
        today = datetime.datetime.strptime(today_str, "%Y-%m-%d").date()
    except Exception:
        return items
    since = today - datetime.timedelta(days=1)
    since_exempt = today - datetime.timedelta(days=30)
    keep = []
    for it in items:
        if it.get("category", "") in EXEMPT_RECENT:
            keep.append(it)
            continue
        d = _parse_date(it.get("pub_date", ""))
        if d is None:
            keep.append(it)
            continue
        if since <= d <= today + datetime.timedelta(days=1):
            keep.append(it)
        else:
            print(f"[filter] 超出24h窗口，丢弃旧闻: {it.get('title','')[:40]} ({d})")
    return keep

def filter_today_effective_duplicates(items, today_effective):
    """避免今日正式实施条目又在常规板块重复出现"""
    eff_urls = {normalize_url(e.get("url", "")) for e in today_effective if e.get("url")}
    keep = []
    for it in items:
        u = normalize_url(it.get("url", ""))
        if u and u in eff_urls:
            print(f"[filter] 已列入今日正式实施，常规板块去重: {it.get('title','')[:40]}")
            continue
        keep.append(it)
    return keep

def dedup(items, history):
    seen = {h.get("h") for h in history}
    new_items, dropped = [], 0
    for it in items:
        h = url_hash(it.get("url", it.get("title", "")))
        if h in seen:
            dropped += 1
            continue
        new_items.append(it)
        history.append({"h": h, "date": it.get("pub_date", ""), "title": it.get("title", "")})
    return new_items, dropped

def esc(s):
    return (str(s).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;"))

CSS = """
* { margin:0; padding:0; box-sizing:border-box; }
body { font-family:"PingFang SC","Microsoft YaHei","Hiragino Sans GB",sans-serif; background:#e8f0ea; display:flex; justify-content:center; padding:24px 12px; }
.poster { width:440px; background:#fff; border-radius:18px; overflow:hidden; box-shadow:0 8px 28px rgba(34,90,59,0.18); }
.head { background:linear-gradient(135deg,#1a8c52 0%,#2ebd72 100%); color:#fff; padding:22px 22px 18px; text-align:center; }
.head .badge { display:inline-block; font-size:11px; letter-spacing:2px; opacity:0.9; border:1px solid rgba(255,255,255,0.45); border-radius:20px; padding:2px 10px; margin-bottom:10px; }
.head h1 { font-size:26px; font-weight:800; letter-spacing:1px; }
.head .date { font-size:13px; opacity:0.92; margin-top:6px; font-weight:400; }
.head .count { display:inline-block; margin-top:8px; font-size:12px; background:rgba(255,255,255,0.18); padding:3px 12px; border-radius:20px; }
.effective { margin:16px 16px 0; background:linear-gradient(135deg,#fff8ef,#ffedd3); border:1px solid #f5c47a; border-left:5px solid #f2930e; border-radius:12px; padding:13px 14px; box-shadow:0 2px 8px rgba(242,147,14,0.08); }
.effective .et { font-size:14px; font-weight:800; color:#a64d00; margin-bottom:9px; display:flex; align-items:center; gap:6px; }
.effective .ei { font-size:12.5px; color:#5a3a12; line-height:1.55; margin:6px 0; padding:8px 10px; background:rgba(255,255,255,0.55); border-radius:8px; }
.effective .ei a { color:#a64d00; font-weight:700; text-decoration:none; }
.effective .en { font-size:10.5px; color:#8a6a3a; margin-top:4px; }
.overview { display:flex; flex-wrap:wrap; gap:8px; padding:14px 16px; background:#f3f9f5; border-bottom:1px solid #e3efe8; }
.ov { flex:0 0 calc(33.333% - 6px); text-align:center; background:#fff; border-radius:10px; padding:9px 0; border:1px solid #e3efe8; box-shadow:0 1px 3px rgba(0,0,0,0.03); }
.ov .n { font-size:20px; font-weight:800; color:#1f7a4d; }
.ov .t { font-size:11px; color:#5a6b62; margin-top:2px; }
.body { padding:6px 16px 18px; }
.sec { margin-top:18px; }
.sec-title { font-size:14.5px; font-weight:800; color:#1f7a4d; display:flex; align-items:center; gap:7px; margin-bottom:9px; }
.sec-title .bar { width:4px; height:16px; border-radius:2px; background:#2fa968; }
.item { background:#fff; border:1px solid #edf2ef; border-left:4px solid #2fa968; border-radius:10px; padding:11px 13px; margin-bottom:9px; box-shadow:0 2px 6px rgba(31,90,59,0.05); }
.item.warn { border-left-color:#e0a32e; }
.item.penalty { border-left-color:#d9534f; }
.item.event { border-left-color:#3a7bd5; }
.item.expert { border-left-color:#8e44ad; }
.item.inspect { border-left-color:#16a085; }
.item.tender { border-left-color:#2980b9; }
.item.case { border-left-color:#c0392b; }
.item.train { border-left-color:#d68910; }
.item.qual { border-left-color:#707b7c; }
.item.tech { border-left-color:#1abc9c; }
.item.read { border-left-color:#5d6d7e; }
.item.accept { border-left-color:#2e86c1; }
.item.accident { border-left-color:#e67e22; }
.item.judicial { border-left-color:#9b59b6; }
.item .title { font-size:13.5px; font-weight:700; color:#21332a; line-height:1.45; }
.item .summary { font-size:11.5px; color:#5a6b62; line-height:1.45; margin-top:5px; }
.item .meta { font-size:11px; color:#7a8b82; margin-top:7px; display:flex; justify-content:space-between; align-items:center; flex-wrap:wrap; gap:5px; }
.item .impact { font-size:10px; color:#1f7a4d; background:#eef7f1; border-radius:12px; padding:3px 8px; display:inline-block; white-space:nowrap; font-weight:600; }
.item.expert .impact { color:#8e44ad; background:#f5eef9; }
.item.inspect .impact { color:#138a72; background:#e8f6f2; }
.item.tender .impact { color:#2471a3; background:#eaf2f9; }
.item.case .impact { color:#c0392b; background:#fdecea; }
.item.train .impact { color:#b9770e; background:#fef5e7; }
.item.qual .impact { color:#566573; background:#f0f1f2; }
.item.tech .impact { color:#138d75; background:#e8f8f3; }
.item.read .impact { color:#515a5a; background:#f0f2f2; }
.item.accept .impact { color:#2471a3; background:#eaf2f9; }
.item.accident .impact { color:#ca6510; background:#fdebd0; }
.item.judicial .impact { color:#8e44ad; background:#f5eef9; }
.weekly { margin:16px 16px 0; background:#fff8ec; border:1px dashed #e0a32e; border-radius:10px; padding:12px 14px; }
.weekly .wt { font-size:13px; font-weight:700; color:#b9821b; margin-bottom:6px; }
.weekly li { font-size:12px; color:#5a4a2e; margin:3px 0 3px 16px; }
.quote { margin:16px 16px 0; padding:12px 14px; background:#f3f9f5; border-left:4px solid #1f7a4d; border-radius:8px; font-size:12.5px; color:#1f5c3a; line-height:1.6; }
.view-full { margin:14px 16px 0; text-align:center; }
.view-full .btn { display:inline-block; background:linear-gradient(135deg,#1a8c52,#2ebd72); color:#fff; font-size:13px; font-weight:700; padding:10px 28px; border-radius:24px; box-shadow:0 4px 12px rgba(31,122,77,0.25); }
.foot { padding:12px 16px 16px; font-size:10.5px; color:#9aa8a0; text-align:center; border-top:1px solid #eef3f0; margin-top:14px; }
"""

CAT_ICON = {
    "法规·标准·指南（近期发布）": "📋",
    "政策征求意见（即将落地预警）": "⏳",
    "专家/职称/鉴定人公示": "👤",
    "生态环保督察通报": "🔍",
    "招投标与项目机会": "💼",
    "环保大事记": "📰",
    "环保处罚（典型/警示）": "⚠️",
    "典型案例与执法": "⚖️",
    "培训/宣贯/继续教育": "🎓",
    "资质与机构动态(CMA/名录)": "🏛️",
    "科技与新技术规范": "🔬",
    "政策法规官方解读": "📖",
    "环评/验收专项动态": "📝",
    "典型突发环境事件案例": "🚨",
    "环境损害司法鉴定典型案例": "⚖️",
}

def build_effective_html(today_effective, for_push=False):
    if not today_effective:
        return ""
    cards = ""
    for e in today_effective:
        url = esc(e.get("url", ""))
        title = esc(e.get("title", ""))
        src = esc(e.get("source", ""))
        note = esc(e.get("note", ""))
        if for_push:
            t = f'<a href="{url}" style="color:#b4560b;font-weight:700;text-decoration:none;">{title}</a>' if url else f'<b style="color:#b4560b;">{title}</b>'
            cards += (
                f'<div style="margin:8px 0;padding:8px 10px;background:#fff4e0;border-left:4px solid #e8820e;border-radius:6px;">'
                f'{t}'
                f'<div style="font-size:12px;color:#8a6a3a;margin-top:2px;">{src}</div>'
                + (f'<div style="font-size:11px;color:#a07a4a;margin-top:2px;">{note}</div>' if note else "")
                + f'</div>'
            )
        else:
            t = f'<a href="{url}">{title}</a>' if url else f'<span>{title}</span>'
            cards += (
                f'<div class="ei">{t}'
                f'<div class="en">{src}</div>'
                + (f'<div class="en">{note}</div>' if note else "")
                + f'</div>'
            )
    if for_push:
        return f'<div style="margin-top:14px;"><b style="color:#b4560b;">📌 今日正式实施（法规·标准）</b>{cards}</div>'
    return f'<div class="effective"><div class="et">📌 今日正式实施（法规·标准）</div>{cards}</div>'

def build_poster_html(data, items_by_cat, overview, today_effective):
    total = data.get("count", 0)
    chips = "".join(
        f'<div class="ov"><div class="n">{n}</div><div class="t">{esc(t)}</div></div>'
        for t, n in overview
    )
    if not chips:
        chips = '<div class="ov"><div class="n">0</div><div class="t">今日无更新</div></div>'
    sections = ""
    for cat in CAT_ORDER:
        its = items_by_cat.get(cat)
        if not its:
            continue
        cls = CAT_CLASS[cat]
        icon = CAT_ICON.get(cat, "•")
        cards = ""
        for it in its:
            impact = f'<span class="impact">{esc(it.get("impact",""))}</span>' if it.get("impact") else ""
            summary = f'<div class="summary">{esc(it.get("summary",""))}</div>' if it.get("summary") else ""
            cards += (
                f'<div class="item {cls}">'
                f'<div class="title">{esc(it.get("title",""))}</div>'
                f'{summary}'
                f'<div class="meta">'
                f'<span>{esc(it.get("source",""))} · {esc(it.get("pub_date",""))}</span>'
                f'{impact}'
                f'</div>'
                f'</div>'
            )
        sections += (
            f'<div class="sec"><div class="sec-title"><span class="bar"></span>{icon} {esc(cat)}</div>{cards}</div>'
        )
    weekly = ""
    if data.get("is_monday") and data.get("weekly_review"):
        lis = "".join(f"<li>{esc(x)}</li>" for x in data["weekly_review"])
        weekly = f'<div class="weekly"><div class="wt">📅 周一回顾 · 上周重大变动</div><ul>{lis}</ul></div>'
    quote_html = f'<div class="quote">🌿 {esc(data.get("quote",""))}</div>' if data.get("quote") else ""
    view_full = '<div class="view-full"><span class="btn">查看完整长图 👇</span></div>'
    eff_html = build_effective_html(today_effective, for_push=False)
    return f"""<!DOCTYPE html><html lang="zh-CN"><head><meta charset="UTF-8"><style>{CSS}</style></head>
<body><div class="poster">
<div class="head"><span class="badge">ENVIRON · 每日环保简报</span><h1>🌿 每日环保简报</h1>
<div class="date">{esc(data['date'])}　{esc(data.get('weekday',''))}</div>
<div class="count">本日共 {total} 条</div></div>
{eff_html}
<div class="overview">{chips}</div>
<div class="body">{sections}</div>
{weekly}
{quote_html}
{view_full}
<div class="foot">来源：生态环境部 · 福建省生态环境厅 · 各地市局 · 信用中国 · 公共资源交易网 等<br>每日 08:30 自动整理 · 微信推送版标题可点击跳转官方原文</div>
</div></body></html>"""

def build_push_html(data, items_by_cat, today_effective):
    sections = ""
    for cat in CAT_ORDER:
        its = items_by_cat.get(cat)
        if not its:
            continue
        cards = ""
        for it in its:
            url = esc(it.get("url", "#"))
            title = esc(it.get("title", ""))
            impact = f'<span style="font-size:11px;color:#1f7a4d;background:#eef7f1;border-radius:5px;padding:2px 6px;white-space:nowrap;">{esc(it.get("impact",""))}</span>' if it.get("impact") else ""
            cards += (
                f'<div style="margin:8px 0;padding:8px 10px;background:#fafdfb;border-left:4px solid #2fa968;border-radius:6px;">'
                f'<a href="{url}" style="color:#1f5c3a;font-weight:600;text-decoration:none;">{title}</a>'
                f'<div style="font-size:12px;color:#7a8b82;margin:3px 0;display:flex;justify-content:space-between;align-items:center;flex-wrap:wrap;gap:4px;">'
                f'<span>{esc(it.get("source",""))} · {esc(it.get("pub_date",""))}</span>'
                f'{impact}'
                f'</div>'
                f'</div>'
            )
        sections += f'<div style="margin-top:14px;"><b style="color:#1f7a4d;">{esc(cat)}</b>{cards}</div>'
    quote_html = (
        f'<div style="margin-top:14px;padding:10px 12px;background:#f3f9f5;border-left:4px solid #1f7a4d;border-radius:6px;font-size:13px;color:#1f5c3a;line-height:1.6;">'
        f'🌿 {esc(data.get("quote",""))}'
        f'</div>'
        if data.get("quote") else ""
    )
    eff_html = build_effective_html(today_effective, for_push=True)
    return (
        f'<div style="font-family:sans-serif;">'
        f'<h2 style="color:#1f7a4d;">🌿 每日环保简报 {esc(data["date"])}（{data.get("count",0)}条）</h2>'
        f'{eff_html}'
        f'{sections}'
        f'{quote_html}'
        f'<hr style="margin-top:14px;border:none;border-top:1px solid #e3efe8;">'
        f'<div style="font-size:12px;color:#9aa8a0;">标题可点击跳转官方原文 · 每日 08:30 自动整理</div>'
        f'</div>'
    )

def render_png(html, png_path):
    from playwright.sync_api import sync_playwright
    with sync_playwright() as p:
        try:
            b = p.chromium.launch(channel="msedge")
        except Exception:
            b = p.chromium.launch()  # fallback to playwright-managed chromium
        pg = b.new_page(viewport={"width": 440, "height": 1200}, device_scale_factor=3)
        pg.set_content(html, wait_until="networkidle")
        pg.screenshot(path=png_path, full_page=True)
        b.close()

# ---------- 推送通道 ----------

def get_pushplus_cfg():
    """优先读环境变量（GitHub Actions Secrets），否则读本地 pushplus_config.json"""
    token = os.environ.get("PUSHPLUS_TOKEN", "")
    topic = os.environ.get("PUSHPLUS_TOPIC", "")
    if token:
        return token, topic
    pp = os.path.join(BASE, "pushplus_config.json")
    if os.path.exists(pp):
        c = load_json(pp)
        return c.get("token", ""), c.get("topic_id", "")
    return "", ""

def push_pushplus(data, push_html, count):
    token, topic = get_pushplus_cfg()
    if not token:
        print("[push] 未配置 PUSHPLUS_TOKEN，跳过 PushPlus 通道")
        return "NO_TOKEN"
    payload = {
        "token": token,
        "title": f"每日环保简报 {data['date']}（{count}条）",
        "content": push_html,
        "template": "html",
    }
    if topic:
        payload["topicId"] = topic
    req = urllib.request.Request(
        "https://www.pushplus.plus/send",
        data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        headers={"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=30) as r:
        return r.read().decode("utf-8")

def get_wxpusher_cfg():
    if not os.path.exists(CFG_PATH):
        return None
    c = load_json(CFG_PATH)
    return c

def push_wxpusher(data, push_html, count):
    cfg = get_wxpusher_cfg()
    if not cfg or not cfg.get("app_token"):
        print("[push] 未配置 WxPusher(app_token)，跳过 WxPusher 通道")
        return "NO_TOKEN"
    # 优先走 Topic（用户订阅的 46054）；仅当无 topic 时才回退到 uid，避免带无效 uid 导致半失败
    topic_ids = [int(cfg["topic_id"])] if str(cfg.get("topic_id", "")).strip() else []
    raw_uid = str(cfg.get("uid", "")).strip()
    uid_list = [raw_uid] if (raw_uid and not topic_ids) else []
    if not topic_ids and not uid_list:
        print("[push] WxPusher 未配置有效 topic/uid，跳过")
        return "NO_TARGET"
    payload = {
        "appToken": cfg["app_token"],
        "content": push_html,
        "summary": f"每日环保简报 {data['date']}（{count}条）",
        "contentType": 2,  # 2=HTML
        "topicIds": topic_ids,
        "uids": uid_list,
    }
    req = urllib.request.Request(
        "https://wxpusher.zjiecode.com/api/send/message",
        data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        headers={"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=30) as r:
        return r.read().decode("utf-8")

# ---------- 企业微信（云线：普通微信接收）----------
def get_wecom_cfg():
    """读环境变量 WECOM_WEBHOOK（GitHub Actions Secrets），本地留空则跳过"""
    return os.environ.get("WECOM_WEBHOOK", "").strip()

def build_wecom_markdown(data, items_by_cat, today_effective):
    """生成企业微信群机器人 markdown（支持 # 标题/**加粗**/[链接]/引用，单条上限4096字节）"""
    lines = [f"# 🌿 每日环保简报 {data['date']}（{data.get('count', 0)}条）", ""]
    if today_effective:
        lines.append("> 📌 **今日正式实施（法规·标准）**")
        for e in today_effective:
            title, url, src = e.get("title", ""), e.get("url", ""), e.get("source", "")
            lines.append(f"> [{title}]({url})　{src}" if url else f"> {title}　{src}")
        lines.append("")
    for cat in CAT_ORDER:
        its = items_by_cat.get(cat)
        if not its:
            continue
        lines.append(f"**{cat}**")
        for it in its:
            url = it.get("url", "")
            title = it.get("title", "")
            meta = f"{it.get('source', '')} · {it.get('pub_date', '')}"
            lines.append(f"- [{title}]({url})　{meta}" if url else f"- {title}　{meta}")
        lines.append("")
    if data.get("quote"):
        lines.append(f"🌿 {data.get('quote', '')}")
        lines.append("")
    lines.append("> 标题可点击跳转官方原文 · 每日 08:30 自动整理")
    md = "\n".join(lines)
    # 企业微信单条 markdown 上限 4096 字节，超限按字节截断并提示
    if len(md.encode("utf-8")) > 4000:
        cut = md.encode("utf-8")[:4000].decode("utf-8", "ignore")
        idx = cut.rfind("\n")
        cut = cut[:idx] if idx > 0 else cut
        md = cut + "\n\n> …（内容过长已截断，详见完整版）"
    return md

def push_wecom(data, wecom_md, count):
    url = get_wecom_cfg()
    if not url:
        print("[push] 未配置 WECOM_WEBHOOK，跳过 WeCom 通道")
        return "NO_TOKEN"
    payload = {"msgtype": "markdown", "markdown": {"content": wecom_md}}
    req = urllib.request.Request(
        url,
        data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        headers={"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=30) as r:
        return r.read().decode("utf-8")

def resolve_channels():
    """返回本次要使用的通道列表。env PUSH_CHANNELS 逗号分隔；默认 pushplus,wxpusher（按 token 实际可用性再滤）"""
    raw = os.environ.get("PUSH_CHANNELS", "pushplus,wxpusher")
    return [c.strip().lower() for c in raw.split(",") if c.strip()]

def main():
    cfg = load_json(CFG_PATH) if os.path.exists(CFG_PATH) else {}
    data = load_json(DATA_PATH)
    items = data.get("items", [])
    # 统一转简体入口 + 旧归档提示
    for it in items:
        it["url"] = normalize_url(it.get("url", ""))
    # 今日正式实施（顶部模块，独立于常规分类）
    today_effective = get_today_effective(data.get("date", ""))
    # 每日金句
    data["quote"] = get_quote(data)
    # 每日简报只保留近24h内发布的新闻；旧闻不硬填，周一回顾另有 weekly_review
    items = filter_recent_24h(items, data.get("date", ""))
    # 避免今日正式实施条目在常规板块重复出现
    items = filter_today_effective_duplicates(items, today_effective)
    # dedup
    history = []
    if os.path.exists(HISTORY_PATH):
        history = load_json(HISTORY_PATH)
    items, dropped = dedup(items, history)
    data["count"] = len(items)
    print(f"[dedup] 原 {len(data.get('items',[]))} 条，去重后 {len(items)} 条，跳过 {dropped} 条")
    print(f"[effective] 今日正式实施 {len(today_effective)} 条: " + ", ".join(e['title'] for e in today_effective) if today_effective else "[effective] 今日无新实施条目")

    # group by category
    POSTER_LIMIT = 8
    poster_items = items[:POSTER_LIMIT]
    items_by_cat = {}
    for it in items:
        items_by_cat.setdefault(it.get("category", "其他"), []).append(it)
    poster_by_cat = {}
    for it in poster_items:
        poster_by_cat.setdefault(it.get("category", "其他"), []).append(it)
    # overview: 基于全部条目统计分类分布，方便一眼看全；海报精选只展示前8条
    overview = [(c, len(items_by_cat[c])) for c in CAT_ORDER if c in items_by_cat]
    if not overview:
        overview = [("今日无更新", 0)]

    poster_html = build_poster_html(data, poster_by_cat, overview, today_effective)
    push_html = build_push_html(data, items_by_cat, today_effective)
    wecom_md = build_wecom_markdown(data, items_by_cat, today_effective)

    line = os.environ.get("LINE", "")
    date = data["date"]
    day_dir = os.path.join(BASE, (date + "_" + line) if line else date)  # 双线可按 LINE 分目录，避免互相覆盖
    os.makedirs(day_dir, exist_ok=True)
    os.makedirs(os.path.dirname(HISTORY_PATH), exist_ok=True)

    png_path = os.path.join(day_dir, "poster.png")
    poster_path = os.path.join(day_dir, "poster.html")
    push_path = os.path.join(day_dir, "brief_push.html")

    with open(poster_path, "w", encoding="utf-8") as f:
        f.write(poster_html)
    with open(push_path, "w", encoding="utf-8") as f:
        f.write(push_html)

    # 历史 + 汇总
    with open(HISTORY_PATH, "w", encoding="utf-8") as f:
        json.dump(history, f, ensure_ascii=False, indent=1)
    new_file = not os.path.exists(CSV_PATH)
    with open(CSV_PATH, "a", encoding="utf-8-sig", newline="") as f:
        w = csv.writer(f)
        if new_file:
            w.writerow(["日期", "分类", "标题", "来源", "发布日期", "业务影响", "链接"])
        for it in items:
            w.writerow([date, it.get("category", ""), it.get("title", ""), it.get("source", ""),
                        it.get("pub_date", ""), it.get("impact", ""), it.get("url", "")])

    # —— 双通道推送（先推送，不依赖 chromium）——
    channels = resolve_channels()
    print(f"[push] 启用通道: {channels}")
    for ch in channels:
        try:
            if ch == "pushplus":
                resp = push_pushplus(data, push_html, len(items))
                print("[push] PushPlus 返回:", resp)
            elif ch == "wxpusher":
                resp = push_wxpusher(data, push_html, len(items))
                print("[push] WxPusher 返回:", resp)
            elif ch == "wecom":
                resp = push_wecom(data, wecom_md, len(items))
                print("[push] WeCom 返回:", resp)
            else:
                print(f"[push] 未知通道: {ch}")
        except Exception as e:
            print(f"[push] {ch} 推送异常:", repr(e))

    # —— 渲染海报（失败不影响推送）——
    print("[render] 渲染 PNG ...")
    if os.environ.get("SKIP_PNG") == "1":
        print("[render] 已设 SKIP_PNG=1，云端跳过 PNG（海报本地按需生成）")
    else:
        try:
            render_png(poster_html, png_path)
            print(f"[render] 已生成 {png_path}")
        except Exception as e:
            print("[render] 海报渲染跳过（chromium 未就绪）:", repr(e))
    print("DONE")

if __name__ == "__main__":
    main()
