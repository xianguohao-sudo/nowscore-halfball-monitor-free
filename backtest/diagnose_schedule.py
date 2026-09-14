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
        form_info = await page.locator("input[name=date]").evaluate(
            """el => ({action:el.form && el.form.action, method:el.form && el.form.method,
                       html:el.form && el.form.outerHTML.slice(0, 1200)})"""
        )
        print("DATE_FORM", json.dumps(form_info, ensure_ascii=False))

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

        target = "2026-09-01"
        await page.locator("input[name=date]").fill(target)
        await page.locator("input[name=date]").evaluate(
            """el => el.form.submit()"""
        )
        await page.wait_for_load_state("domcontentloaded", timeout=45000)
        await page.wait_for_timeout(4000)
        print("AFTER_SUBMIT_URL", page.url)
        print("AFTER_SUBMIT_DATE", target)
        after_links = await page.locator("a").evaluate_all(
            """els => els.map(e => (e.innerText || '').trim())
                .filter(t => /^2026-\\d{2}-\\d{2}/.test(t)).slice(0, 15)"""
        )
        after_filename = await page.locator("script").evaluate_all(
            """els => els.map(e => e.textContent || '')
                .map(s => (s.match(/filename2\\s*=\\s*["']([^"']+)/) || [])[1])
                .filter(Boolean).slice(0, 5)"""
        )
        after_ids = await page.locator("a").evaluate_all(
            """els => Array.from(new Set(els.map(e => e.href || '')
                .map(h => (h.match(/(?:odds\\/match\\/|analysis\\/|MatchDetail\\/|id=)(\\d+)/i) || [])[1])
                .filter(Boolean))).slice(0, 10)"""
        )
        print("AFTER_SUBMIT_LINK_DATES", json.dumps(after_links, ensure_ascii=False))
        print("AFTER_SUBMIT_FILENAME", json.dumps(after_filename, ensure_ascii=False))
        print("AFTER_SUBMIT_IDS", json.dumps(after_ids, ensure_ascii=False))
        sample_rows = await page.locator("tr").evaluate_all(
            """trs => trs.map(tr => {
                const hrefs = Array.from(tr.querySelectorAll('a')).map(a => a.href || '');
                const matchId = hrefs.map(h => (h.match(/(?:odds\\/match\\/|analysis\\/|MatchDetail\\/)(\\d+)/i) || [])[1]).find(Boolean);
                if (!matchId) return null;
                return {
                    matchId,
                    cells:Array.from(tr.querySelectorAll('td')).map(td => (td.innerText || '').trim()),
                    html:tr.outerHTML.slice(0, 2500)
                };
            }).filter(Boolean).slice(0, 8)"""
        )
        print("AFTER_SUBMIT_SAMPLE_ROWS", json.dumps(sample_rows, ensure_ascii=False))
        await browser.close()


if __name__ == "__main__":
    asyncio.run(main())
