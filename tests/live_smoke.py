"""Opt-in Playwright smoke check of an already-running real-source server.

Creates the requested real watch only if not already present. It leaves the watch saved.
Live counts are compared with the current API result, never a fixture count.
"""

import argparse
from pathlib import Path

from playwright.sync_api import expect, sync_playwright


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--url", default="http://127.0.0.1:8000")
    parser.add_argument("--term", default="27W")
    parser.add_argument("--subject", default="ENGR")
    parser.add_argument("--course", default="186W")
    parser.add_argument("--class-id", default="186827200")
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
            expect(page.locator("#alert-connection")).to_contain_text("In-app alerts connected")
            page.get_by_role("button", name="Add a class", exact=True).click()
            page.locator("#term").select_option(args.term)
            page.get_by_label("Subject area", exact=True).fill(args.subject)
            page.get_by_label("Course number", exact=True).fill(args.course)
            page.get_by_role("button", name="Find sections").click()
            first = page.locator(".search-result").filter(has_text=args.class_id)
            expect(first).to_be_visible(timeout=35000)
            if first.get_by_role("button", name="Already watching").count():
                page.keyboard.press("Escape")
            else:
                first.get_by_role("button", name="Watch section", exact=True).click()
                expect(page.locator("#search-dialog")).not_to_be_visible(timeout=35000)
            data = context.request.get(args.url + "/api/watchlist").json()
            watch = next(item for item in data if item["class_id"] == args.class_id and item["term"] == args.term)
            assert watch["snapshot"] and not watch["error"], watch
            assert watch["snapshot"]["combination_status"] in {"available", "blocked"}, watch
            card = page.locator(f'[data-watch-id="{watch["id"]}"]')
            expect(card).to_contain_text(f"{args.subject.upper()} {args.course.upper()}")
            expect(card.locator(".seat-count")).to_have_text(str(watch["snapshot"]["available"]))
            expect(card.get_by_test_id("linked-section")).to_have_count(len(watch["snapshot"]["linked_sections"]))
            if watch["snapshot"]["combination_status"] == "blocked":
                expect(card).to_contain_text("No complete seat combination")
                expect(card.locator(".status-badge")).not_to_have_class("status-badge open")
            page.screenshot(path=str(artifacts / "desktop-live.png"), full_page=True)
            with page.expect_response(lambda response: response.url.endswith(f"/api/watchlist/{watch['id']}/refresh")) as check_response:
                card.get_by_role("button", name="Check section", exact=False).click()
            result = check_response.value.json()
            expected_message = ("Already checked" if result["cached"] else "Availability is unknown" if result["watch"]["error"]
                                else "A seat opening" if result["reopened"] else "No new reopening")
            expect(page.locator("#toast")).to_contain_text(expected_message)
            duplicate = context.request.post(args.url + f"/api/watchlist/{watch['id']}/refresh").json()
            assert duplicate["cached"] is True
            page.reload()
            expect(page.locator(f'[data-watch-id="{watch["id"]}"]')).to_be_visible()
            page.set_viewport_size({"width": 390, "height": 844})
            assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth"), "Mobile horizontal overflow"
            page.screenshot(path=str(artifacts / "mobile-live.png"), full_page=True)
            page.goto(args.url + "/docs")
            expect(page.get_by_role("heading", name="Classrabbit API", exact=False)).to_be_visible(timeout=20000)
            assert not errors, errors
            print(f"LIVE PASS: {args.subject} {args.course}; section seats {watch['snapshot']['available']}/{watch['snapshot']['capacity']}; complete combination {watch['snapshot']['combination_status']}; {len(watch['snapshot']['linked_sections'])} linked sections verified; real API and browser agree; stream, refresh cooldown, persistence, mobile, and Swagger docs verified.")
        except Exception:
            page.screenshot(path=str(artifacts / "live-failure.png"), full_page=True)
            raise
        finally:
            context.tracing.stop(path=str(artifacts / "live-trace.zip"))
            context.close()
            browser.close()


if __name__ == "__main__":
    main()
