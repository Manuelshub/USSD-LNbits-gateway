"""Play the demo click path in a headless browser and save README screenshots.

    pip install playwright && playwright install chromium
    scripts/reset_demo.sh && python scripts/screenshots.py docs/img

Set CHROME=/path/to/chrome to use an already installed Chromium.
Registers Awa (FR) and Moussa (EN), pays Awa 2,500 sats from the outside
world, then Awa sends Moussa 500 sats by phone number.
"""
import os
import sys
from playwright.sync_api import sync_playwright

OUT = sys.argv[1] if len(sys.argv) > 1 else "."
with sync_playwright() as p:
    b = p.chromium.launch(executable_path=os.getenv("CHROME") or None)
    pg = b.new_page(viewport={"width": 1440, "height": 1000}, device_scale_factor=2)
    pg.goto("http://localhost:8000/demo"); pg.wait_for_timeout(1500)
    keys = pg.evaluate("Array.from(document.querySelectorAll('.user')).map(e=>e.id.replace('-card',''))")
    awa, mou = keys[0], keys[1]
    def typ(k, v=""):
        e = pg.locator(f"#{k}-entry"); e.fill(v); e.press("Enter"); pg.wait_for_timeout(700)
    typ(awa); typ(awa,"2"); typ(awa,"4821"); typ(awa,"4821")
    typ(mou); typ(mou,"1"); typ(mou,"1357"); typ(mou,"1357")
    pg.wait_for_timeout(1500)
    pg.screenshot(path=f"{OUT}/1-registered.png")
    pg.select_option("#payTo", index=0); pg.fill("#payAmt","2500"); pg.click("#payBtn")
    pg.wait_for_timeout(4000)
    pg.screenshot(path=f"{OUT}/2-lightning-address-paid.png")
    typ(awa, "0")  # any key: menu
    for v in ["3","1","772345678","500","4821"]: typ(awa, v)
    pg.wait_for_timeout(4000)
    pg.screenshot(path=f"{OUT}/3-sent-by-phone-number.png")
    b.close()
