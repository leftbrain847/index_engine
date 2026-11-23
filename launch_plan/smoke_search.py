"""Browser smoke check for the Launch plan's search bar, at phone width (not part of the page)."""
import asyncio, pathlib, sys
from playwright.async_api import async_playwright

DATA = pathlib.Path(sys.argv[sys.argv.index("--data") + 1] if "--data" in sys.argv else ".")
PAGE = (DATA / "index.html").resolve().as_uri()
VISIBLE = "[...document.querySelectorAll('#rules details, #phases details')].filter(d => !d.hidden && d.offsetParent).length"


async def main():
    async with async_playwright() as p:
        b = await p.chromium.launch()
        pg = await b.new_page(viewport={"width": 390, "height": 844})
        errs = []
        pg.on("pageerror", lambda e: errs.append(str(e)))
        await pg.goto(PAGE)
        total = await pg.evaluate(VISIBLE)
        await pg.fill("#q", "cloudflare token")
        print("all:", total, "| 'cloudflare token':", await pg.evaluate(VISIBLE), "|", await pg.locator("#qcount").text_content())
        print("opened:", await pg.evaluate("[...document.querySelectorAll('details')].filter(d => !d.hidden && d.open).length"))
        await pg.screenshot(path=str(DATA / "smoke_search.png"))
        await pg.fill("#q", "zzzz nothing")
        print("none:", await pg.evaluate(VISIBLE), "|", await pg.locator("#noresults").text_content())
        await pg.fill("#q", "")
        print("cleared:", await pg.evaluate(VISIBLE), "| open after clear:", await pg.evaluate("document.querySelectorAll('details[open]').length"))
        print("scrollWidth:", await pg.evaluate("document.documentElement.scrollWidth"), "| errors:", errs)
        await b.close()

asyncio.run(main())
