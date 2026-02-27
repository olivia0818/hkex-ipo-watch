from __future__ import annotations

import json
import re
from collections import Counter
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional
from urllib.parse import urljoin

import pytz
from bs4 import BeautifulSoup
from playwright.sync_api import TimeoutError as PlaywrightTimeoutError
from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parents[1]
DATA_DIR = ROOT / "data"
ARCHIVE_DIR = DATA_DIR / "archive"
DEBUG_DIR = DATA_DIR / "debug"

# 强制中文入口（你指定的页面）
HKEX_APP_URL = "https://www1.hkexnews.hk/app/appindex.html?lang=zh"
HKT_TZ = pytz.timezone("Asia/Hong_Kong")


def ensure_dirs() -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    ARCHIVE_DIR.mkdir(parents=True, exist_ok=True)
    DEBUG_DIR.mkdir(parents=True, exist_ok=True)


def hkt_now() -> datetime:
    return datetime.now(HKT_TZ)


def normalize_spaces(s: str) -> str:
    return re.sub(r"\s+", " ", s or "").strip()


def save_debug_text(filename: str, content: str) -> None:
    (DEBUG_DIR / filename).write_text(content or "", encoding="utf-8")


def save_debug_json(filename: str, data: Any) -> None:
    (DEBUG_DIR / filename).write_text(
        json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8"
    )


def parse_any_date_to_iso(text: str) -> Optional[str]:
    text = (text or "").strip()

    # YYYY-MM-DD / YYYY/MM/DD / YYYY.MM.DD
    m = re.search(r"(20\d{2})[\/\-.](\d{1,2})[\/\-.](\d{1,2})", text)
    if m:
        y, mo, d = map(int, m.groups())
        try:
            return datetime(y, mo, d).date().isoformat()
        except ValueError:
            return None

    # DD/MM/YYYY or DD-MM-YYYY
    m = re.search(r"(\d{1,2})[\/\-](\d{1,2})[\/\-](20\d{2})", text)
    if m:
        d, mo, y = map(int, m.groups())
        try:
            return datetime(y, mo, d).date().isoformat()
        except ValueError:
            return None

    # 中文日期：2026年2月26日
    m = re.search(r"(20\d{2})年(\d{1,2})月(\d{1,2})日", text)
    if m:
        y, mo, d = map(int, m.groups())
        try:
            return datetime(y, mo, d).date().isoformat()
        except ValueError:
            return None

    return None


def detect_board(text: str) -> Optional[str]:
    raw = text or ""
    t = raw.lower()
    if "gem" in t:
        return "GEM"
    if "main board" in t or "mainboard" in t or "主板" in raw:
        return "Main Board"
    return None


def detect_doc_type(text: str) -> Optional[str]:
    raw = text or ""
    t = raw.lower()

    # PHIP 优先
    if (
        "phip" in t
        or "post hearing information pack" in t
        or "聆訊後資料集" in raw
        or "聆讯后资料集" in raw
    ):
        return "PHIP"

    # AP
    if "application proof" in t or "申請版本" in raw or "申请版本" in raw:
        return "AP"

    # 独立 AP
    if re.search(r"(?<![a-z])ap(?![a-z])", t):
        return "AP"

    return None


def looks_like_pdf_link(href: str) -> bool:
    h = (href or "").lower()
    return ".pdf" in h or "pdf" in h


def is_obvious_nav_noise(text: str) -> bool:
    t = (text or "").lower()
    noise_keywords = [
        "prolonged suspension status report",
        "board meeting notifications",
        "exchange reports",
        "monthly prolonged suspension",
        "status report on delisting proceedings and suspensions",
        "shareholding disclosures",
        "listed company publications",
        "market data",
        "circulars",
        "announcements and notices",
        "listed company information",
        "disclosure of interests",
        "new listings information",
        "home page",
    ]
    return any(k in t for k in noise_keywords)


def ap_markers() -> List[str]:
    return [
        "application proof",
        "phip",
        "post hearing information pack",
        "related materials",
        "申請版本",
        "申请版本",
        "聆訊後資料集",
        "聆讯后资料集",
    ]


def pick_best_link(links: List[Dict[str, str]]) -> Optional[str]:
    # 优先 PDF
    for l in links:
        href = l.get("href", "")
        if looks_like_pdf_link(href):
            return href
    return links[0].get("href") if links else None


def infer_applicant_name(row_text: str, links: List[Dict[str, str]]) -> str:
    # 优先中文标签提取
    combined = " ".join(
        [row_text or ""] + [normalize_spaces(l.get("text", "")) for l in links]
    )
    m = re.search(
        r"(申請人|申请人)\s*[:：]?\s*(.+?)(?=\s*(申請版本|申请版本|聆訊後資料集|聆讯后资料集|保薦人|保荐人|委任|登錄|登录|發布日期|发布日期|$))",
        combined,
    )
    if m:
        candidate = normalize_spaces(m.group(2))
        if candidate:
            return candidate[:300]

    # 再尝试最长链接文本
    link_texts = [
        normalize_spaces(l.get("text", ""))
        for l in links
        if normalize_spaces(l.get("text", ""))
    ]
    if link_texts:
        candidate = sorted(link_texts, key=len, reverse=True)[0]
        candidate = re.sub(
            r"\b(Application Proof|AP|PHIP|Post Hearing Information Pack)\b",
            "",
            candidate,
            flags=re.I,
        )
        candidate = (
            candidate.replace("申請版本", "")
            .replace("申请版本", "")
            .replace("聆訊後資料集", "")
            .replace("聆讯后资料集", "")
        )
        candidate = normalize_spaces(candidate)
        if candidate:
            return candidate[:300]

    # 最后从整行清洗
    cleaned = row_text or ""
    cleaned = re.sub(
        r"\b(Application Proof|AP|PHIP|Post Hearing Information Pack)\b",
        "",
        cleaned,
        flags=re.I,
    )
    cleaned = (
        cleaned.replace("申請版本", "")
        .replace("申请版本", "")
        .replace("聆訊後資料集", "")
        .replace("聆讯后资料集", "")
    )
    cleaned = re.sub(r"(20\d{2}[\/\-.]\d{1,2}[\/\-.]\d{1,2})", "", cleaned)
    cleaned = re.sub(r"(\d{1,2}[\/\-]\d{1,2}[\/\-]20\d{2})", "", cleaned)
    cleaned = re.sub(r"(20\d{2}年\d{1,2}月\d{1,2}日)", "", cleaned)
    cleaned = re.sub(r"\b(Main Board|GEM|Active|Inactive)\b", "", cleaned, flags=re.I)
    cleaned = cleaned.replace("主板", "").replace("GEM", "")
    cleaned = normalize_spaces(cleaned)

    if not cleaned:
        return "Unknown Applicant"
    return cleaned[:300]


def extract_rows_from_html(html: str, base_url: str) -> List[Dict[str, Any]]:
    soup = BeautifulSoup(html or "", "html.parser")
    rows: List[Dict[str, Any]] = []

    def append_candidate(source: str, node) -> None:
        text = normalize_spaces(node.get_text(" ", strip=True))
        if not text:
            return

        links = []
        for a in node.select("a[href]"):
            href = (a.get("href") or "").strip()
            if not href:
                continue
            links.append(
                {
                    "href": urljoin(base_url, href),
                    "text": normalize_spaces(a.get_text(" ", strip=True)),
                }
            )

        if not links:
            return

        rows.append({"source": source, "text": text, "links": links})

    # 1) 表格行（优先）
    for tr in soup.select("tr"):
        append_candidate("tr", tr)

    # 2) 列表项
    for li in soup.select("li"):
        append_candidate("li", li)

    # 3) div 兜底（放宽规则，避免误杀真实记录）
    for div in soup.select("div"):
        links = div.select("a[href]")
        if not links:
            continue

        text = normalize_spaces(div.get_text(" ", strip=True))
        if not text:
            continue

        # 放宽：真实记录可能很长
        if len(text) < 20 or len(text) > 5000:
            continue

        # 放宽：真实记录可能有较多链接
        if len(links) > 80:
            continue

        # 至少像“新股材料记录”
        if not re.search(
            r"(申請人|申请人|發布日期|发布日期|Application Proof|PHIP|申請版本|申请版本|聆訊後資料集|聆讯后资料集)",
            text,
            re.I,
        ):
            continue

        append_candidate("div", div)

    return rows


def row_to_record(row: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    text = row.get("text", "") or ""
    links = row.get("links", []) or []
    if not text or not links:
        return None

    if is_obvious_nav_noise(text):
        return None

    combined = " ".join(
        [text] + [l.get("text", "") for l in links] + [l.get("href", "") for l in links]
    )

    # 放宽：先只要求像新股材料记录，不再过早要求“申请人+发布日期同时出现”
    has_material_hint = bool(
        re.search(
            r"(申請版本|申请版本|聆訊後資料集|聆讯后资料集|Application Proof|PHIP)",
            combined,
            re.I,
        )
    )
    if not has_material_hint:
        return None

    best_link = pick_best_link(links)
    if not best_link:
        return None

    # 日期：优先从发布日期字段附近提取；提不到再从全文/链接文本找
    date_iso = None
    m = re.search(
        r"(發布日期|发布日期)\s*[:：]?\s*([0-9]{1,2}[/-][0-9]{1,2}[/-][0-9]{4}|20[0-9]{2}[./-][0-9]{1,2}[./-][0-9]{1,2}|20[0-9]{2}年[0-9]{1,2}月[0-9]{1,2}日)",
        combined,
    )
    if m:
        date_iso = parse_any_date_to_iso(m.group(2))

    if date_iso is None:
        date_iso = parse_any_date_to_iso(text)
    if date_iso is None:
        for l in links:
            date_iso = parse_any_date_to_iso(l.get("text", ""))
            if date_iso:
                break
    if date_iso is None:
        return None

    # 文档类型
    doc_type = detect_doc_type(combined)
    if doc_type is None:
        hrefs = " ".join(l.get("href", "") for l in links)
        doc_type = detect_doc_type(hrefs) or "Unknown"

    board = detect_board(combined) or "Unknown"

    applicant_name = infer_applicant_name(text, links)

    # 放宽长度限制（避免误杀中文长名称+附注）
    if len(applicant_name) > 300:
        return None

    return {
        "applicant_name": applicant_name,
        "board": board,
        "doc_type": doc_type,
        "posting_date_hkt": date_iso,
        "is_first_filing": None,
        "sponsors": [],
        "link": best_link,
        "raw_row_text": text,
        "source_node": row.get("source", ""),
    }


def dedupe_records(records: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    seen = set()
    out = []
    for r in records:
        key = (
            r.get("posting_date_hkt", ""),
            r.get("doc_type", ""),
            r.get("link", ""),
        )
        if key in seen:
            continue
        seen.add(key)
        out.append(r)
    return out


def page_has_ap_markers(page) -> bool:
    markers = ap_markers()

    # Page content
    try:
        html = page.content()
        low = (html or "").lower()
        if any(m.lower() in low for m in markers):
            return True
    except Exception:
        pass

    # Frame contents
    for frame in page.frames:
        try:
            html = frame.content()
        except Exception:
            continue
        low = (html or "").lower()
        if any(m.lower() in low for m in markers):
            return True
    return False


def click_warning_if_present(page) -> None:
    page.wait_for_timeout(1000)

    candidates = [
        "Accept",
        "I Agree",
        "Continue",
        "Proceed",
        "接受",
        "同意",
        "继续",
        "繼續",
    ]

    contexts = [page] + [f for f in page.frames if f != page.main_frame]
    for ctx in contexts:
        for name in candidates:
            try:
                loc = ctx.get_by_role("button", name=re.compile(name, re.I))
                if loc.count() > 0:
                    loc.first.click(timeout=2500)
                    page.wait_for_timeout(1200)
                    return
            except Exception:
                pass
            try:
                loc = ctx.get_by_role("link", name=re.compile(name, re.I))
                if loc.count() > 0:
                    loc.first.click(timeout=2500)
                    page.wait_for_timeout(1200)
                    return
            except Exception:
                pass

    # selector 兜底
    for sel in ["button", "a", "input[type='button']", "input[type='submit']"]:
        try:
            loc = page.locator(sel)
            n = min(loc.count(), 30)
            for i in range(n):
                try:
                    t = normalize_spaces(loc.nth(i).inner_text())
                except Exception:
                    t = ""
                if re.search(r"(accept|agree|continue|proceed|接受|同意|继续|繼續)", t, re.I):
                    try:
                        loc.nth(i).click(timeout=2500)
                        page.wait_for_timeout(1200)
                        return
                    except Exception:
                        continue
        except Exception:
            pass


def go_to_ap_page_or_fail(page) -> None:
    """
    强制进入 AP/PHIP 页面；若失败则报错（避免误报 count=0）。
    """
    target_url = HKEX_APP_URL

    # 第一次进入目标地址
    page.goto(target_url, wait_until="domcontentloaded", timeout=60000)
    page.wait_for_timeout(1200)

    # 尝试通过 warning
    click_warning_if_present(page)
    page.wait_for_timeout(1200)

    # 再次强制进入目标地址（warning后可能跑偏）
    page.goto(target_url, wait_until="domcontentloaded", timeout=60000)
    try:
        page.wait_for_load_state("networkidle", timeout=10000)
    except Exception:
        pass
    page.wait_for_timeout(2500)

    if page_has_ap_markers(page):
        return

    # 尝试点击中英文入口
    patterns = [
        re.compile(r"Application Proof.*PHIP.*Related Materials", re.I),
        re.compile(r"Application Proof", re.I),
        re.compile(r"PHIP", re.I),
        re.compile(r"申請版本|申请版本"),
        re.compile(r"聆訊後資料集|聆讯后资料集"),
    ]
    contexts = [page] + [f for f in page.frames if f != page.main_frame]

    for ctx in contexts:
        for pat in patterns:
            try:
                loc = ctx.get_by_role("link", name=pat)
                if loc.count() > 0:
                    loc.first.click(timeout=5000)
                    try:
                        page.wait_for_load_state("networkidle", timeout=10000)
                    except Exception:
                        pass
                    page.wait_for_timeout(2000)
                    if page_has_ap_markers(page):
                        return
            except Exception:
                pass

            try:
                loc = ctx.locator("a").filter(has_text=pat)
                if loc.count() > 0:
                    loc.first.click(timeout=5000)
                    try:
                        page.wait_for_load_state("networkidle", timeout=10000)
                    except Exception:
                        pass
                    page.wait_for_timeout(2000)
                    if page_has_ap_markers(page):
                        return
            except Exception:
                pass

    raise RuntimeError(
        "Did not reach HKEX AP/PHIP page. Current page is likely a non-AP section."
    )


def collect_frame_meta_and_save(page) -> None:
    metas = []
    for i, frame in enumerate(page.frames):
        meta = {
            "index": i,
            "name": frame.name or "",
            "url": frame.url or "",
        }
        try:
            html = frame.content()
            save_debug_text(f"frame_{i}.html", html)
            meta["html_size"] = len(html or "")
        except Exception as e:
            meta["error"] = repr(e)
        metas.append(meta)
    save_debug_json("frames_meta.json", metas)


def scrape_hkex_rows() -> List[Dict[str, Any]]:
    rows: List[Dict[str, Any]] = []

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        ctx = browser.new_context(locale="zh-HK")
        page = ctx.new_page()

        # 强制进入 AP 页面，否则抛错
        go_to_ap_page_or_fail(page)

        try:
            page.wait_for_load_state("networkidle", timeout=10000)
        except PlaywrightTimeoutError:
            pass

        page.wait_for_timeout(3000)

        # 尝试滚动触发懒加载
        try:
            page.mouse.wheel(0, 1500)
            page.wait_for_timeout(1000)
            page.mouse.wheel(0, 1500)
            page.wait_for_timeout(1000)
        except Exception:
            pass

        # 调试输出
        try:
            page.screenshot(path=str(DEBUG_DIR / "page.png"), full_page=True)
        except Exception:
            pass
        try:
            save_debug_text("main_page.html", page.content())
        except Exception:
            pass
        collect_frame_meta_and_save(page)

        markers = ap_markers()

        # 主页面
        try:
            main_html = page.content()
            rows.extend(extract_rows_from_html(main_html, page.url or HKEX_APP_URL))
        except Exception:
            pass

        # 仅解析命中 AP/PHIP 标识的 frame
        for frame in page.frames:
            try:
                html = frame.content()
            except Exception:
                continue

            low = (html or "").lower()
            if any(m.lower() in low for m in markers):
                rows.extend(extract_rows_from_html(html, frame.url or HKEX_APP_URL))

        ctx.close()
        browser.close()

    return rows


def build_payload() -> dict:
    now = hkt_now()
    target_date = (now - timedelta(days=1)).date().isoformat()

    raw_rows = scrape_hkex_rows()
    save_debug_json("raw_rows.json", raw_rows[:3000])

    parsed_records: List[Dict[str, Any]] = []
    for row in raw_rows:
        rec = row_to_record(row)
        if rec:
            parsed_records.append(rec)

    parsed_records = dedupe_records(parsed_records)

    doc_type_counter = Counter(r.get("doc_type", "Unknown") for r in parsed_records)
    date_counter = Counter(r.get("posting_date_hkt", "") for r in parsed_records)

    # 仅保留前一香港自然日 + AP
    filtered = [
        r
        for r in parsed_records
        if r.get("posting_date_hkt") == target_date and r.get("doc_type") == "AP"
    ]

    board_rank = {"Main Board": 0, "GEM": 1, "Unknown": 2}
    filtered.sort(
        key=lambda x: (
            board_rank.get(x.get("board", "Unknown"), 9),
            x.get("applicant_name", ""),
        )
    )

    payload = {
        "generated_at_hkt": now.isoformat(),
        "target_date_hkt": target_date,
        "source": "HKEX Application Proof / PHIP page (scraped by Playwright)",
        "count": len(filtered),
        "items": filtered,
        "message": (
            "No new HKEX AP postings found for the previous Hong Kong calendar day."
            if len(filtered) == 0
            else ""
        ),
        "debug": {
            "raw_row_count": len(raw_rows),
            "parsed_record_count": len(parsed_records),
            "doc_type_distribution": dict(doc_type_counter),
            "top_dates": dict(date_counter.most_common(10)),
            "debug_dir": "data/debug",
        },
    }
    return payload


def save_payload(payload: dict) -> None:
    latest_path = DATA_DIR / "latest.json"
    archive_path = ARCHIVE_DIR / f"{payload['target_date_hkt']}.json"

    latest_path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    archive_path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    print(f"Saved: {latest_path}")
    print(f"Saved: {archive_path}")
    print(f"Count: {payload.get('count', 0)}")
    print(f"Target date (HKT): {payload.get('target_date_hkt')}")
    print(f"Debug raw rows: {payload.get('debug', {}).get('raw_row_count')}")
    print(f"Debug parsed records: {payload.get('debug', {}).get('parsed_record_count')}")
    print(f"Debug doc types: {payload.get('debug', {}).get('doc_type_distribution')}")


def main() -> None:
    ensure_dirs()
    payload = build_payload()
    save_payload(payload)


if __name__ == "__main__":
    main()
