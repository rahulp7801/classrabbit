"""Prove one real 30-second background cycle using controlled discussion data.

No clock acceleration, manual refresh, or real UCLA enrollment changes.
"""

import json
from pathlib import Path
import time

from playwright.sync_api import expect, sync_playwright

from tests.browser_suite import server
from tests.immediate_proof import EngrSource


def fast_source(clock):
    source = EngrSource(clock)
    source.interval = 30
    return source


def main():
    folder = Path(".local/fast-check-proof")
    folder.mkdir(parents=True, exist_ok=True)
    with server(fast_source, worker=True, real_clock=True) as (url, source, _), sync_playwright() as p:
        browser = p.chromium.launch()
        context = browser.new_context(viewport={"width": 1280, "height": 1100})
        context.tracing.start(screenshots=True, snapshots=True, sources=True)
        page = context.new_page()
        errors = []
        page.on("pageerror", lambda error: errors.append(str(error)))
        try:
            page.goto(url)
            expect(page.locator("#source-heading")).to_contain_text("30-second checks")
            expect(page.locator("#alert-connection")).to_contain_text("In-app alerts connected")
            page.get_by_role("button", name="Add a class", exact=True).click()
            page.locator("#term").select_option("27W")
            page.get_by_label("Subject area", exact=True).fill("ENGR")
            page.get_by_label("Course number", exact=True).fill("186W")
            page.get_by_role("button", name="Find sections").click()
            page.get_by_role("button", name="Watch section", exact=True).click()
            expect(page.get_by_test_id("watch-card")).to_contain_text("No complete seat combination")
            expect(page.locator(".seat-count")).to_have_text("10")
            source.status = "open"  # Only the required Dis 1A becomes available.
            changed = time.perf_counter()
            print("Waiting for the real 30-second scheduler; no refresh request or clock acceleration.", flush=True)
            expect(page.locator("#toast")).to_have_attribute("data-event-id", "1", timeout=40000)
            expect(page.locator("#toast")).to_be_visible()
            observed = time.perf_counter()
            expect(page.locator("#toast")).to_contain_text("Lec 1 + Dis 1A")
            seconds = observed - changed
            delivery_ms = (observed - source.returned_at) * 1000
            assert 20 <= seconds <= 35, seconds
            assert delivery_ms < 1000, delivery_ms
            assert source.check_calls == 2, source.check_calls
            assert not errors, errors
            report = {"result": "pass", "course": "ENGR 186W", "class_id": "186827200",
                      "source_mode": "controlled discussion availability; real scheduler, SQLite, SSE, and Chromium",
                      "configured_check_interval_seconds": 30,
                      "clock_accelerated": False, "manual_refresh": False,
                      "source_change_to_visible_alert_seconds": round(seconds, 3),
                      "source_return_to_visible_alert_ms": round(delivery_ms, 2),
                      "lecture_seats_unchanged": 10,
                      "does_not_prove": "Real UCLA publication delay or background phone delivery."}
            (folder / "timings.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
            page.screenshot(path=str(folder / "scheduled-discussion-alert.png"), full_page=True)
            print(json.dumps(report), flush=True)
        finally:
            context.tracing.stop(path=str(folder / "trace.zip"))
            context.close()
            browser.close()


if __name__ == "__main__":
    main()
