import argparse
import asyncio
import json
import re
from pathlib import Path
from urllib.parse import urlparse

from playwright.async_api import async_playwright

OUT = Path("data/history/v3_probe")
KEYWORDS = ("odd", "odds", "handicap", "asian", "europe", "1x2", "change", "history", "detail", "analysis", "ajax")


def interesting(url: str) -> bool:
    u = url.lower()
    return "nowscore" in u and any(k in u for k in KEYWORDS)


def safe_name(url: str, idx: int) -> str:
    p = urlparse(url)
    base = re.sub(r"[^a-zA-Z0-9._-]+", "_", p.path.strip("/") or "root")[-100:]
    return f"{idx:03d}_{base}.txt"


async def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--match-id", default="2789129")
    ap.add_argument("--wait", type=int, default=8)
    args = ap.parse_args()

    OUT.mkdir(parents=True, exist_ok=True)
    events = []
    bodies = []

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        context = await browser.new_context(locale="zh-CN", viewport={"width": 1600, "height": 1200})
        page = await context.new_page()

        def on_request(req):
            if interesting(req.url):
                events.append({"kind": "request", "method": req.method, "resource_type": req.resource_type,
                               "url": req.url, "post_data": req.post_data})

        async def capture_response(resp):
            if not interesting(resp.url):
                return
            rec = {"kind": "response", "status": resp.status, "url": resp.url,
                   "content_type": resp.headers.get("content-type", "")}
            events.append(rec)
            try:
                text = await resp.text()
                if text:
                    bodies.append((resp.url, text[:1000000]))
            except Exception as e:
                rec["body_error"] = repr(e)

        page.on("request", on_request)
        page.on("response", lambda r: asyncio.create_task(capture_response(r)))

        urls = [
            f"https://live.nowscore.com/odds/match/{args.match_id}.htm",
            f"https://live.nowscore.com/odds/match.aspx?id={args.match_id}",
            f"https://m.nowscore.com/Analy/JcOddsDetail?scheid={args.match_id}",
        ]

        page_reports = []
        for url in urls:
            try:
                print("OPEN:", url)
                resp = await page.goto(url, wait_until="domcontentloaded", timeout=90000)
                await page.wait_for_timeout(args.wait * 1000)
                title = await page.title()
                html = await page.content()
                page_reports.append({"url": url, "status": resp.status if resp else None, "title": title,
                                     "html_len": len(html), "final_url": page.url})
                (OUT / f"page_{len(page_reports)}.html").write_text(html, encoding="utf-8")

                # 尝试点击页面中“变化/历史/详/亚/欧”等入口，以触发隐藏的 XHR/fetch。
                for pat in ("变化", "历史", "亚", "欧", "详"):
                    loc = page.get_by_text(re.compile(pat))
                    count = min(await loc.count(), 8)
                    for i in range(count):
                        try:
                            el = loc.nth(i)
                            if await el.is_visible():
                                await el.click(timeout=1500)
                                await page.wait_for_timeout(1200)
                        except Exception:
                            pass
            except Exception as e:
                page_reports.append({"url": url, "error": repr(e)})

        await page.wait_for_timeout(3000)
        await browser.close()

    # 去重网络事件，完整保留 URL/参数用于下一轮反查接口。
    seen, uniq = set(), []
    for e in events:
        key = (e.get("kind"), e.get("method"), e.get("status"), e.get("url"), e.get("post_data"))
        if key not in seen:
            seen.add(key); uniq.append(e)

    (OUT / "network.json").write_text(json.dumps(uniq, ensure_ascii=False, indent=2), encoding="utf-8")
    (OUT / "pages.json").write_text(json.dumps(page_reports, ensure_ascii=False, indent=2), encoding="utf-8")

    for i, (url, body) in enumerate(bodies, 1):
        (OUT / safe_name(url, i)).write_text(f"URL: {url}\n\n{body}", encoding="utf-8")

    print("=" * 72)
    print("V3 NowScore 赔率历史网络穿透")
    print("Match ID:", args.match_id)
    print("页面:", len(page_reports))
    print("相关网络事件:", len(uniq))
    print("保存响应正文:", len(bodies))
    print("输出目录:", OUT)
    print("注意：V3 只发现真实接口，不把任何 page-current 当作赛前 CLOSE。")
    print("=" * 72)
    print("\n=== 候选接口 ===")
    for e in uniq:
        print(e.get("kind"), e.get("status", ""), e.get("method", ""), e.get("url", ""))
        if e.get("post_data"):
            print("POST:", e["post_data"][:500])


if __name__ == "__main__":
    asyncio.run(main())
