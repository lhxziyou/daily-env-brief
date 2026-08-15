# -*- coding: utf-8 -*-
"""
环保简报每日抓取脚本（纯标准库，云端 GitHub Actions 可直接运行）
抓取：福建省生态环境厅、福建司法厅 等官方源最新动态
输出：brief_data.json（兼容 render_brief.py）
后续可逐源扩展（生态环境部需浏览器绕过反爬，标记 TODO）
"""
import urllib.request, re, ssl, json, os, datetime, gzip

BASE = os.path.dirname(os.path.abspath(__file__))

UA = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
}
CTX = ssl.create_default_context()
CTX.check_hostname = False
CTX.verify_mode = ssl.CERT_NONE

# 旧归档栏目黑名单：生态环境部 xxgk2018/xxgk/xxgk0X 为 2018 信息公开旧框架
# 现已迁至 /zcwj/bwj/wj/、/zcwj/bgtwj/wj/、/ywgz/fgbz/ 等新栏目，抓取源绝不可回退到旧栏目
LEGACY_ARCHIVE_RE = re.compile(r'xxgk2018/xxgk/xxgk0[0-9]', re.I)

# 简报 13 板块（与 render_brief.py 的 CAT_CLASS 对应）
CAT_ORDER = [
    "法规·标准·指南（已落地）",
    "政策征求意见（即将落地预警）",
    "专家/职称/鉴定人公示",
    "生态环保督察通报",
    "招投标与项目机会",
    "环保大事记",
    "环保处罚（典型/警示）",
    "典型案例与执法",
    "培训/宣贯/继续教育",
    "资质与机构动态(CMA/名录)",
    "科技与新技术规范",
    "政策法规官方解读",
    "环评/验收专项动态",
]

def fetch(url, ref=None, timeout=25):
    h = dict(UA)
    if ref:
        h["Referer"] = ref
    try:
        req = urllib.request.Request(url, headers=h)
        r = urllib.request.urlopen(req, timeout=timeout, context=CTX)
        raw = r.read(800000)
        if raw[:2] == b'\x1f\x8b':
            raw = gzip.decompress(raw)
        enc = r.headers.get_content_charset() or 'utf-8'
        try:
            return raw.decode(enc, errors='ignore')
        except Exception:
            return raw.decode('gbk', errors='ignore')
    except Exception as e:
        print(f"[fetch ERR] {url} -> {repr(e)[:60]}")
        return ""

def normalize_url(u):
    if not u:
        return u
    if "big5.mee.gov.cn/gate/big5/" in u:
        u = u.replace("big5.mee.gov.cn/gate/big5/", "")
        if u.startswith("http://"):
            u = "https://" + u[7:]
        if not u.startswith("https://"):
            u = "https://" + u
    return u

def should_skip(title, source):
    """过滤与环保业务明显无关的条目"""
    t = title
    # 司法厅非环保业务
    if source == "福建司法厅":
        if re.search(r'民主法治示范村|法治示范|法律明白人|法治带头人|法治文化建设|一村一居', t):
            return True
        if re.search(r'消防设施|消防维保|消防检测|消防器材|消防工程|消防维护', t):
            return True
        if re.search(r'律师|公证|法律援助|人民调解|社区矫正|戒毒|监狱|仲裁|行政复议|法治副校长', t) and not re.search(r'环境损害|生态环境|司法鉴定', t):
            return True
    # 消防类若非生态环境/污染相关则跳过（如消防系统优化、消防维保）
    if re.search(r'消防', t) and not re.search(r'生态环境|环境应急|污染|环保', t):
        return True
    # 生态环境厅里的党建/工会/支部换届等纯内部事务（巡察整改除外）
    if re.search(r'党支部|支部委员会|党总支|党委|工会|团委|妇联', t) and not re.search(r'巡察|整改|督察|生态环境工作', t):
        return True
    return False

def classify(title, source):
    """返回 (简报板块, 业务影响标签)"""
    t = title
    impact = ""
    # —— 业务影响（13类业务）：严格匹配，避免机构名/宽泛词误触发 ——
    if re.search(r'土壤|地下水', t):
        if '调查' in t: impact = '涉及【土壤及地下水污染状况调查】'
        elif '风险' in t or '评估' in t: impact = '涉及【土壤及地下水污染风险评估】'
        elif '修复' in t: impact = '涉及【土壤及地下水污染修复方案】'
        elif '效果评估' in t: impact = '涉及【土壤及地下水污染效果评估】'
        elif '隐患' in t: impact = '涉及【土壤及地下水隐患排查】'
        elif '自行监测' in t: impact = '涉及【土壤及地下水自行监测方案及报告】'
        elif '环境损害' in t or '司法鉴定' in t: impact = '涉及【土壤及地下水环境损害司法鉴定】'
    elif re.search(r'竣工环保验收|建设项目竣工环境保护验收|环保验收', t):
        impact = '涉及【竣工环保验收】'
    elif '排污许可' in t:
        impact = '涉及【排污许可申报】'
    elif re.search(r'应急预案|突发环境事件应急|环境应急预案|应急演练', t):
        impact = '涉及【突发环境事件应急预案】'
    elif '固废' in t:
        impact = '涉及【固体废物属性鉴别】'
    elif re.search(r'危废|危险废物', t):
        impact = '涉及【危险废物属性鉴别】'
    elif '污染物性质' in t:
        impact = '涉及【污染物性质司法鉴定】'
    elif re.search(r'环境损害|生态环境损害|环境损害司法鉴定', t):
        impact = '涉及【土壤及地下水环境损害司法鉴定】'

    # —— 简报板块 ——
    if re.search(r'专家库|职称|鉴定人|人才库|入库|名单公示|评审公示|遴选|公示名单', t):
        cat = "专家/职称/鉴定人公示"
    elif re.search(r'采购|招标|投标|竞争性谈判|询价|中标|结果公告|采购意向|评审结果|成交|项目公告|运维保障', t):
        cat = "招投标与项目机会"
    elif re.search(r'征求意见|征询意见|草案|送审稿', t):
        cat = "政策征求意见（即将落地预警）"
    elif re.search(r'生态环保督察|中央生态环境保护督察|省督察|巡察.*整改|整改.*进展|督察.*整改|反馈问题.*整改|巡察组反馈', t):
        cat = "生态环保督察通报"
    elif re.search(r'处罚|失信|违法', t):
        cat = "环保处罚（典型/警示）"
    elif re.search(r'培训|宣贯|继续教育', t):
        cat = "培训/宣贯/继续教育"
    elif re.search(r'资质|CMA|名录|认定', t):
        cat = "资质与机构动态(CMA/名录)"
    elif re.search(r'建设项目竣工环境保护验收|环保验收|环评', t):
        cat = "环评/验收专项动态"
    elif re.search(r'解读', t):
        cat = "政策法规官方解读"
    elif re.search(r'技术指南|白皮书|方法', t):
        cat = "科技与新技术规范"
    elif re.search(r'大会|会议|签约|合作|揭牌|启动', t):
        cat = "环保大事记"
    elif re.search(r'典型案例|执法', t):
        cat = "典型案例与执法"
    else:
        cat = "法规·标准·指南（已落地）"
    return cat, impact

def parse_list(html, base, list_url, source):
    items = []
    for m in re.finditer(r'<a[^>]+href=["\']([^"\']+)["\'][^>]*>(.*?)</a>', html, re.S):
        href, inner = m.group(1), re.sub(r'<[^>]+>', '', m.group(2)).strip()
        if not re.search(r't20\d{6}_[\d]+\.htm', href):
            continue
        if len(inner) < 4:
            continue
        if should_skip(inner, source):
            continue
        if href.startswith('./'):
            href = list_url + href[2:]
        elif href.startswith('/'):
            href = base + href
        dm = re.search(r't(20\d{6})_', href)
        date = ''
        if dm:
            d = dm.group(1)
            date = f"{d[:4]}-{d[4:6]}-{d[6:8]}"
        cat, impact = classify(inner, source)
        items.append({
            "category": cat,
            "title": inner,
            "source": source,
            "pub_date": date,
            "summary": "",
            "impact": impact,
            "url": normalize_url(href),
        })
    return items

def crawl_fujian_ec():
    """福建省生态环境厅 - 公告公示"""
    base = "https://sthjt.fujian.gov.cn"
    url = f"{base}/zwgk/gsgg/"
    html = fetch(url, ref=f"{base}/")
    return parse_list(html, base, url, "福建省生态环境厅")

def crawl_fujian_justice():
    """福建司法厅 - 公告公示 + 采购招标"""
    base = "https://sft.fujian.gov.cn"
    out = []
    for path in ["/zwgk/gggs/", "/zwgk/czzj/zbgg_czzj/"]:
        url = base + path
        html = fetch(url, ref=f"{base}/")
        out += parse_list(html, base, url, "福建司法厅")
    return out

# TODO 第二阶段：信用中国(412)、公共资源交易(动态)、司法部(302)、生态环境部征求意见栏目(反爬)

def crawl_mee():
    """生态环境部 - 部文件 / 办公厅文件 / 法规标准 栏目（需浏览器绕过反爬）

    说明：生态环境部列表页对纯 urllib 有 WAF 拦截，必须用真实浏览器。
    本地用系统 Edge（channel=msedge）；云端 GitHub Actions 已装 chromium 时回退。
    未安装 playwright 时跳过本源，不影响其他源。
    """
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        print("[mee] playwright 未安装，跳过生态环境部抓取（云端需配置依赖）")
        return []
    cols = [
        ("部文件", "https://www.mee.gov.cn/zcwj/bwj/wj/"),
        ("办公厅文件", "https://www.mee.gov.cn/zcwj/bgtwj/wj/"),
        ("法规标准", "https://www.mee.gov.cn/ywgz/fgbz/"),
    ]
    # 源栏目守卫：若不慎把旧归档列表页（xxgk2018/xxgk/xxgk0X）当作抓取源，高亮告警
    # 注意：详情页 URL 含 xxgk2018/xxgk/xxgk0X 属正常现象（MEE 文档地址即如此），放行不告警
    for _name, _url in cols:
        if LEGACY_ARCHIVE_RE.search(_url):
            print(f"[mee][legacy!] 抓取源命中旧归档栏目，请改为 /zcwj/ 新栏目: {_url}")
    items = []
    today = datetime.date.today()
    try:
        with sync_playwright() as p:
            try:
                b = p.chromium.launch(channel="msedge", args=["--no-sandbox", "--disable-dev-shm-usage"])
            except Exception:
                b = p.chromium.launch(args=["--no-sandbox", "--disable-dev-shm-usage"])
            pg = b.new_page()
            for name, url in cols:
                try:
                    pg.goto(url, wait_until="domcontentloaded", timeout=25000)
                    pg.wait_for_timeout(3000)
                    html = pg.content()
                    for m in re.finditer(r'<a[^>]+href="([^"]+)"[^>]*>(.*?)</a>', html, re.S):
                        href, inner = m.group(1), re.sub(r'<[^>]+>', '', m.group(2)).strip()
                        if len(inner) < 6:
                            continue
                        if not re.search(r't20\d{6}|shtml', href):
                            continue
                        if href.startswith('./'):
                            href = url.rstrip('/') + '/' + href[2:]
                        elif href.startswith('/'):
                            href = "https://www.mee.gov.cn" + href
                        elif not href.startswith('http'):
                            href = "https://www.mee.gov.cn/" + href.lstrip('./')
                        # 旧归档栏目守卫：若不慎抓到 xxgk2018/xxgk/xxgk0X 旧框架链接，高亮告警（详情页仍有效，但不应作为新抓取源）
                        dm = re.search(r't(20\d{6})_', href) or re.search(r'/(\d{8})/', href)
                        date = ''
                        if dm:
                            d = dm.group(1)
                            date = f"{d[:4]}-{d[4:6]}-{d[6:8]}"
                        # 仅保留近 120 天内的条目，避免旧归档详情页海量历史链接拖累（详情页 URL 含 xxgk2018 属正常，不告警）
                        try:
                            if date and (today - datetime.date(int(d[:4]), int(d[4:6]), int(d[6:8]))).days > 120:
                                continue
                        except Exception:
                            pass
                        if should_skip(inner, "生态环境部"):
                            continue
                        cat, impact = classify(inner, "生态环境部")
                        items.append({
                            "category": cat,
                            "title": inner,
                            "source": f"生态环境部·{name}",
                            "pub_date": date,
                            "summary": "",
                            "impact": impact,
                            "url": normalize_url(href),
                        })
                except Exception as e:
                    print(f"[mee] {name} ERR {repr(e)[:60]}")
            b.close()
    except Exception as e:
        print(f"[mee] playwright 启动失败: {repr(e)[:80]}")
        return []
    return items

SOURCES = [crawl_fujian_ec, crawl_fujian_justice, crawl_mee]

def main():
    today = datetime.date.today()
    weekday_cn = ["星期一","星期二","星期三","星期四","星期五","星期六","星期日"][today.weekday()]
    is_monday = (today.weekday() == 0)
    # since：周一回看7天，其他回看1天
    since = today - datetime.timedelta(days=7 if is_monday else 1)
    since_str = since.strftime("%Y-%m-%d")

    all_items = []
    for src in SOURCES:
        try:
            all_items += src()
        except Exception as e:
            print(f"[source ERR] {src.__name__}: {repr(e)[:60]}")

    # 过滤：只保留 since 之后的（按 pub_date 字符串比较，无日期的保留）
    kept = []
    for it in all_items:
        d = it.get("pub_date", "")
        if d and d < since_str:
            continue
        kept.append(it)

    # 按板块排序，板块内按日期倒序
    kept.sort(key=lambda x: (CAT_ORDER.index(x["category"]) if x["category"] in CAT_ORDER else 99,
                             x.get("pub_date", "")), reverse=False)
    # 日期倒序（同板块内新在前）
    by_cat = {}
    for it in kept:
        by_cat.setdefault(it["category"], []).append(it)
    ordered = []
    for cat in CAT_ORDER:
        lst = by_cat.get(cat, [])
        lst.sort(key=lambda x: x.get("pub_date", ""), reverse=True)
        ordered += lst

    data = {
        "date": today.strftime("%Y-%m-%d"),
        "weekday": weekday_cn,
        "is_monday": is_monday,
        "items": ordered,
        "weekly_review": [],
    }
    out_path = os.path.join(BASE, os.environ.get("DATA_PATH", "brief_data.json"))
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=1)
    print(f"[crawl] 抓取 {len(all_items)} 条，过滤后 {len(ordered)} 条（since={since_str}）")
    # 板板块分布
    dist = {}
    for it in ordered:
        dist[it["category"]] = dist.get(it["category"], 0) + 1
    print("[crawl] 板块分布:", dist)

if __name__ == "__main__":
    main()
