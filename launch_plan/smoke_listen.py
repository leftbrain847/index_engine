"""Browser smoke check for the Launch plan's Listen player, at phone width (not part of the page)."""
import asyncio, pathlib, sys
from playwright.async_api import async_playwright

DATA = pathlib.Path(sys.argv[sys.argv.index("--data") + 1] if "--data" in sys.argv else ".")
PAGE = (DATA / "index.html").resolve().as_uri()


async def main():
    async with async_playwright() as p:
        b = await p.chromium.launch()
        pg = await b.new_page(viewport={"width": 390, "height": 844})
        errs = []
        pg.on("pageerror", lambda e: errs.append(str(e)))
        await pg.goto(PAGE)
        print("listen buttons:", await pg.locator("[data-listen]").count())
        await pg.locator('[data-listen="s"]').click()
        await pg.wait_for_timeout(2500)
        print("now:", await pg.locator("#lNow").text_content(), "| status:", await pg.locator("#lStatus").text_content())
        print("play button:", await pg.locator("#lPlay").get_attribute("aria-label"), "| highlighted:", await pg.locator("details.playing").count())
        await pg.locator("#lNext").click()
        await pg.wait_for_timeout(1000)
        print("after next:", await pg.locator("#lNow").text_content())
        await pg.locator("#lRepeat").click()
        print("repeat:", await pg.locator("#lRepeatN").text_content())
        await pg.locator("#lNow").click()
        print("queue rows:", await pg.locator("#lNowList button").count())
        await pg.screenshot(path=str(DATA / "smoke_shot.png"))
        cb = pg.locator("#d_s1 summary input")
        await cb.click()
        await pg.wait_for_timeout(200)
        print("tick:", await cb.is_checked(), "| opened by tick:", await pg.evaluate("document.querySelector('#d_s1').open"))
        print("scrollWidth:", await pg.evaluate("document.documentElement.scrollWidth"))
        print("errors:", errs)
        await b.close()

asyncio.run(main())
