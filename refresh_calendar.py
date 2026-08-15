# -*- coding: utf-8 -*-
"""
今日实施日历维护助手（本地按需运行，不进每日自动流）

功能：
1. 读取 brief_data.json 中「法规·标准·指南（已落地）」条目的详情页；
2. 用正则抽取「自YYYY年MM月DD日起施行 / YYYY年MM月DD日起实施」日期；
3. 把结果写入 effective_calendar.json 的 auto 条目（curated 条目绝不覆盖）；
4. 已存在的 auto 条目按 url 去重更新。

用法：
  python refresh_calendar.py            # 处理 brief_data.json 里的法规标准条目
  python refresh_calendar.py --url "https://..." --title "..."   # 单独补一条

说明：生态环境部详情页需浏览器，本脚本用本地 Edge（channel=msedge）渲染后取文本；
其他官网用 urllib 直接取。失败单条跳过，不影响整体。
"""
import os, sys, re, json, argparse, urllib.request, ssl

BASE = os.path.dirname(os.path.abspath(__file__))
DATA_PATH = os.path.join(BASE, os.environ.get("DATA_PATH", "brief_data.json"))
CALENDAR_PATH = os.path.join(BASE, "effective_calendar.json")
CTX = ssl.create_default_context()
CTX.check_hostname = False
CTX.verify_mode = ssl.CERT_NONE
UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"}

# 施行/实施 日期抽取（兼容全/半角、有无"起"字）
EFF_RE = re.compile(
    r'(?:自\s*)?(\d{4})\s*年\s*(\d{1,2})\s*月\s*(\d{1,2})\s*日[起]?\s*(?:施行|实施)',
    re.I,
)

def extract_effective_date(text):
    m = EFF_RE.search(text or "")
    if not m:
        return ""
    y, mo, d = m.group(1), int(m.group(2)), int(m.group(3))
    if not (1 <= mo <= 12 and 1 <= d <= 31):
        return ""
    return f"{y}-{mo:02d}-{d:02d}"

def fetch_text(url):
    if "mee.gov.cn" in url:
        return fetch_mee(url)
    try:
        req = urllib.request.Request(url, headers=UA)
        r = urllib.request.urlopen(req, timeout=25, context=CTX)
        raw = r.read(600000)
        enc = r.headers.get_content_charset() or "utf-8"
        try:
            return raw.decode(enc, errors="ignore")
        except Exception:
            return raw.decode("gbk", errors="ignore")
    except Exception as e:
        print(f"[fetch ERR] {url} -> {repr(e)[:60]}")
        return ""

def fetch_mee(url):
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        print("[mee] 未装 playwright，回退 urllib 取文本（可能不全）")
        return fetch_text(url)
    try:
        with sync_playwright() as p:
            try:
                b = p.chromium.launch(channel="msedge", args=["--no-sandbox"])
            except Exception:
                b = p.chromium.launch(args=["--no-sandbox"])
            pg = b.new_page()
            pg.goto(url, wait_until="domcontentloaded", timeout=25000)
            pg.wait_for_timeout(2500)
            txt = pg.inner_text("body")
            b.close()
            return txt
    except Exception as e:
        print(f"[mee ERR] {url} -> {repr(e)[:60]}")
        return ""

def load_calendar():
    if not os.path.exists(CALENDAR_PATH):
        return {"_comment": "auto-managed", "entries": []}
    return json.load(open(CALENDAR_PATH, encoding="utf-8"))

def save_calendar(cal):
    json.dump(cal, open(CALENDAR_PATH, "w", encoding="utf-8"), ensure_ascii=False, indent=2)

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--url", help="单独补一条的详情页 URL")
    ap.add_argument("--title", help="单独补一条的标题")
    args = ap.parse_args()

    cal = load_calendar()
    entries = cal.setdefault("entries", [])
    by_url = {e.get("url", ""): e for e in entries if e.get("url")}

    targets = []
    if args.url:
        targets.append((args.url, args.title or args.url))
    else:
        data = json.load(open(DATA_PATH, encoding="utf-8"))
        for it in data.get("items", []):
            if it.get("category", "").startswith("法规") and it.get("url"):
                targets.append((it["url"], it.get("title", "")))

    added = updated = skipped = 0
    for url, title in targets:
        if not url:
            continue
        print(f"[scan] {title[:30]}  <- {url}")
        d = extract_effective_date(fetch_text(url))
        if not d:
            print("      未抽到施行日期，跳过")
            skipped += 1
            continue
        if url in by_url:
            e = by_url[url]
            if e.get("curated"):
                print(f"      curated 条目，保留原 effective_date={e.get('effective_date')}，不覆盖")
                skipped += 1
                continue
            if e.get("effective_date") != d:
                e["effective_date"] = d
                e["auto"] = True
                updated += 1
                print(f"      更新 auto 条目 -> {d}")
            else:
                skipped += 1
        else:
            entries.append({
                "title": title, "effective_date": d, "url": url,
                "source": "", "note": "auto 抽取自详情页施行日期", "curated": False, "auto": True,
            })
            by_url[url] = entries[-1]
            added += 1
            print(f"      新增 auto 条目 -> {d}")

    save_calendar(cal)
    print(f"[done] 新增 {added} / 更新 {updated} / 跳过 {skipped}；日历共 {len(entries)} 条")

if __name__ == "__main__":
    main()
