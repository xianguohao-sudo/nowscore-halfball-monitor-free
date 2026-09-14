"""Print the rendered schedule page controls needed for date navigation."""
import asyncio
import json
from nowscore import BASE, HEADERS


async def main():
    from playwright.async_api import async_playwright

    async with async_playwright() as playwright:
        browser = await playwright.chromium.launch(headless=True)
        page = await browser.new_page(user_agent=HEADERS["User-Agent"])
        await page.goto(BASE + "/schedule.aspx?f=ft2", wait_until="domcontentloaded", timeout=45000)
        await page.wait_for_timeout(4000)
        print("PAGE_URL", page.url)
        print("TITLE", await page.title())

        inputs = await page.locator("input").evaluate_all(
            """els => els.map(e => ({
                id:e.id, name:e.name, type:e.type, value:e.value,
                placeholder:e.placeholder, onchange:e.getAttribute('onchange'),
                onclick:e.getAttribute('onclick'), onkeydown:e.getAttribute('onkeydown')
            }))"""
        )
        print("INPUTS", json.dumps(inputs, ensure_ascii=False))

        date_controls = await page.locator("a,button,[onclick]").evaluate_all(
            """els => els.map(e => ({
                tag:e.tagName, text:(e.innerText || e.textContent || '').trim(),
                href:e.getAttribute('href'), onclick:e.getAttribute('onclick'),
                id:e.id, cls:e.className
            })).filter(x =>
                /2026-\d{2}-\d{2}/.test(x.text) ||
                /date|day|schedule/i.test((x.href || '') + ' ' + (x.onclick || '') + ' ' + (x.id || '') + ' ' + (x.cls || ''))
            ).slice(0, 80)"""
        )
        print("DATE_CONTROLS", json.dumps(date_controls, ensure_ascii=False))

        scripts = await page.locator("script").evaluate_all(
            """els => els.map(e => e.textContent || '')
                .filter(s => /changeDate|schedule|YYYY|currentDate|matchdata/i.test(s))
                .map(s => s.slice(0, 1500)).slice(0, 20)"""
        )
        print("SCRIPT_SNIPPETS", json.dumps(scripts, ensure_ascii=False))
        await browser.close()


if __name__ == "__main__":
    asyncio.run(main())
