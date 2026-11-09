"""Opt-in Playwright smoke check of an already-running real-source server.

Creates a real CS 31 watch only if not already present. It leaves the watch saved.
Live counts are compared with the current API result, never a fixture count.
"""

import argparse
from pathlib import Path

from playwright.sync_api import expect, sync_playwright


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--url", default="http://127.0.0.1:8000")
    args = parser.parse_args()
    artifacts = Path(".local/screenshots")
    artifacts.mkdir(parents=True, exist_ok=True)
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch()
        context = browser.new_context(viewport={"width": 1440, "height": 1050})
        context.tracing.start(screenshots=True, snapshots=True, sources=True)
        page = context.new_page()
        errors = []
        page.on("pageerror", lambda error: errors.append(str(error)))
        page.on("console", lambda message: errors.append(message.text) if message.type == "error" else None)
        try:
            source = context.request.get(args.url + "/api/source").json()
            assert source["mode"] == "live", "The live smoke check requires the real source adapter"
            page.goto(args.url)
            expect(page.get_by_role("heading", name="Your watchlist.")).to_be_visible()
            expect(page.locator("#fixture-banner")).not_to_be_visible()
            page.get_by_role("button", name="Add a class", exact=True).click()
            page.locator("#term").select_option("27W")
            page.get_by_role("button", name="Find sections").click()
            expect(page.locator(".search-result")).to_have_count(2, timeout=35000)
            first = page.locator(".search-result").filter(has_text="187093200")
            if first.get_by_role("button", name="Already watching").count():
                page.keyboard.press("Escape")
            else:
                first.get_by_role("button", name="Watch section", exact=True).click()
                expect(page.locator("#search-dialog")).not_to_be_visible(timeout=35000)
            data = context.request.get(args.url + "/api/watchlist").json()
            watch = next(item for item in data if item["class_id"] == "187093200" and item["term"] == "27W")
            assert watch["snapshot"] and not watch["error"], watch
            card = page.locator(f'[data-watch-id="{watch["id"]}"]')
            expect(card).to_contain_text("Introduction to Computer Science I")
            expect(card.locator(".seat-count")).to_have_text(str(watch["snapshot"]["available"]))
            page.screenshot(path=str(artifacts / "desktop-live.png"), full_page=True)
            card.get_by_role("button", name="Check section", exact=False).click()
            expect(page.locator("#toast")).to_contain_text("Already checked")
            page.reload()
            expect(page.locator(f'[data-watch-id="{watch["id"]}"]')).to_be_visible()
            page.set_viewport_size({"width": 390, "height": 844})
            assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth"), "Mobile horizontal overflow"
            page.screenshot(path=str(artifacts / "mobile-live.png"), full_page=True)
            page.goto(args.url + "/docs")
            expect(page.get_by_role("heading", name="Classrabbit API", exact=False)).to_be_visible(timeout=20000)
            assert not errors, errors
            print(f"LIVE PASS: CS 31 Lec 1; source reports {watch['snapshot']['available']}/{watch['snapshot']['capacity']} seats left; real API and browser agree; refresh cooldown, reload persistence, mobile, and Swagger docs verified.")
        except Exception:
            page.screenshot(path=str(artifacts / "live-failure.png"), full_page=True)
            raise
        finally:
            context.tracing.stop(path=str(artifacts / "live-trace.zip"))
            context.close()
            browser.close()


if __name__ == "__main__":
    main()
