import asyncio,csv
from pathlib import Path
from playwright.async_api import async_playwright
from history_collector.validate_history_v33 import discover_matches

OUT=Path("data/history/v4_manifest/epl_2025_26_manifest.csv")
async def main():
    OUT.parent.mkdir(parents=True,exist_ok=True)
    async with async_playwright() as p:
        b=await p.chromium.launch(headless=True);page=await b.new_page(locale="zh-CN",viewport={"width":1600,"height":1200})
        found=await discover_matches(page);await b.close()
    rows=sorted(found.items(),key=lambda x:(x[1],int(x[0])))
    print("canonical manifest matches",len(rows))
    if len(rows)!=380:raise SystemExit(f"MANIFEST FAILED: expected 380 got {len(rows)}")
    ids=[x[0] for x in rows]
    if len(set(ids))!=380:raise SystemExit("MANIFEST FAILED: duplicate match ids")
    with OUT.open("w",encoding="utf-8-sig",newline="") as f:
        w=csv.DictWriter(f,fieldnames=["index","match_id","round"]);w.writeheader()
        for i,(mid,rnd) in enumerate(rows):w.writerow({"index":i,"match_id":mid,"round":rnd})
    print("manifest",OUT)
if __name__=="__main__":asyncio.run(main())
