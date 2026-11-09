"""Time the real API -> event stream -> Chromium alert path for ENGR 186W.

The enrollment change is CONTROLLED TEST DATA, never a claim of a live dropped seat.
No production endpoint allows injecting enrollment states.
"""

import argparse
from dataclasses import replace
from datetime import datetime, timezone
import json
from pathlib import Path
import statistics
import time
import re

from playwright.sync_api import expect, sync_playwright

from classrabbit.ucla import SourceError, parse_detail
from classrabbit.combination import assess_combination
from tests.browser_suite import server
from tests.support import FixtureSource

ENGR_URL = ("https://sa.ucla.edu/ro/Public/SOC/Results/ClassDetail?term_cd=27W"
            "&subj_area_cd=ENGR%20%20%20&crs_catlg_no=0186W%20%20%20"
            "&class_id=186827200&class_no=%20001%20%20")


class EngrSource(FixtureSource):
    label = "Controlled ENGR 186W timing test (not live UCLA data)"

    def search(self, term, subject, course):
        if (term, subject, course) != ("27W", "ENGR", "186W"):
            raise SourceError("This timing fixture contains only Winter 2027 ENGR 186W.")
        return [{"class_id": "186827200", "section": "Lec 1", "url": ENGR_URL}]

    def check(self, url):
        self.check_calls += 1
        markup = (Path(__file__).parent / "fixtures" / "ucla_engr186w_detail.html").read_text(encoding="utf-8")
        snapshot = parse_detail(markup, ENGR_URL)
        snapshot = replace(snapshot, source_url=url, checked_at=datetime.fromtimestamp(self.clock(), timezone.utc).isoformat())
        listing = (Path(__file__).parent / "fixtures" / "ucla_engr186w_listing.html").read_text(encoding="utf-8")
        children = (Path(__file__).parent / "fixtures" / "ucla_engr186w_children.html").read_text(encoding="utf-8")
        if self.status == "open":
            children = re.sub(r'(<div class="statusColumn" id="186827001_186827200_ENGR0186W-status_data">).*?(</div>)',
                              r'\1<p>Open<br/>21 of 22 Enrolled<br/>1 Spots Left</p>\2', children, count=1, flags=re.S)
        result = assess_combination(snapshot, listing, lambda _url: children)
        self.returned_at = time.perf_counter()
        return result


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--iterations", type=int, default=10)
    args = parser.parse_args()
    if not 1 <= args.iterations <= 100:
        parser.error("Choose 1–100 controlled transitions")
    folder = Path(".local/immediate-proof")
    folder.mkdir(parents=True, exist_ok=True)
    samples = []
    with server(EngrSource) as (url, source, clock), sync_playwright() as playwright:
        browser = playwright.chromium.launch()
        context = browser.new_context(viewport={"width": 1280, "height": 1000})
        context.tracing.start(screenshots=True, snapshots=True, sources=True)
        page = context.new_page()
        errors = []
        page.on("pageerror", lambda error: errors.append(str(error)))
        page.on("console", lambda message: errors.append(message.text) if message.type == "error" else None)
        try:
            page.goto(url)
            expect(page.locator("#alert-connection")).to_contain_text("connected")
            expect(page.locator("#fixture-banner")).to_be_visible()
            page.get_by_role("button", name="Add a class", exact=True).click()
            page.locator("#term").select_option("27W")
            page.get_by_label("Subject area", exact=True).fill("ENGR")
            page.get_by_label("Course number", exact=True).fill("186W")
            page.get_by_role("button", name="Find sections").click()
            expect(page.locator(".search-result")).to_have_count(1)
            page.get_by_role("button", name="Watch section", exact=True).click()
            expect(page.get_by_test_id("watch-card")).to_contain_text("ENGR 186W")
            expect(page.get_by_test_id("watch-card")).to_contain_text("No complete seat combination")
            expect(page.locator(".seat-count")).to_have_text("10")
            expect(page.get_by_test_id("linked-section")).to_have_count(6)
            item = context.request.get(url + "/api/watchlist").json()[0]
            refresh_url = f"{url}/api/watchlist/{item['id']}/refresh"
            for index in range(1, args.iterations + 1):
                if index > 1:
                    source.status = "closed"
                    clock.advance()
                    assert context.request.post(refresh_url).json()["reopened"] is False
                source.status = "open"
                clock.advance()
                started = time.perf_counter()
                result = context.request.post(refresh_url).json()
                assert result["reopened"] is True
                # This browser receives an unsolicited server event. It does not click Refresh.
                expect(page.locator("#toast")).to_have_attribute("data-event-id", str(index), timeout=2000)
                expect(page.locator("#toast")).to_be_visible()
                observed = time.perf_counter()
                expect(page.locator("#toast")).to_contain_text("ENGR 186W Lec 1")
                expect(page.locator("#toast")).to_contain_text("Dis 1A")
                expect(page.locator(".seat-count")).to_have_text("10")
                samples.append({"iteration": index,
                                "source_return_to_browser_assertion_ms": round((observed - source.returned_at) * 1000, 2),
                                "refresh_request_to_browser_assertion_ms": round((observed - started) * 1000, 2)})
                print(f"PASS controlled ENGR 186W transition {index}: {samples[-1]['source_return_to_browser_assertion_ms']} ms", flush=True)
                # Repeated open observations must not create another event.
                duplicate = context.request.post(refresh_url).json()
                assert duplicate["cached"] and not duplicate["reopened"]
            event_count = len(context.request.get(url + "/api/events?limit=100").json())
            assert event_count == args.iterations
            assert not errors, errors
            elapsed = [sample["source_return_to_browser_assertion_ms"] for sample in samples]
            assert max(elapsed) < 1000, f"Alert latency exceeded 1 second: {elapsed}"
            report = {"result": "pass", "course": "ENGR 186W", "term": "27W", "class_id": "186827200",
                      "source_mode": "controlled fixture built from a real public UCLA page",
                      "iterations": args.iterations, "median_ms": round(statistics.median(elapsed), 2),
                      "maximum_ms": max(elapsed), "samples": samples,
                      "proves": "Required Dis 1A changes closed to open while lecture remains at 10 seats -> verified complete combination -> durable event -> visible Chromium alert.",
                      "does_not_prove": "Time from an actual UCLA seat drop to an observable source update, or phone delivery."}
            (folder / "timings.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
            page.screenshot(path=str(folder / "engr186w-controlled-alert.png"), full_page=True)
            print(json.dumps({k: report[k] for k in ["result", "iterations", "median_ms", "maximum_ms", "does_not_prove"]}), flush=True)
        finally:
            context.tracing.stop(path=str(folder / "trace.zip"))
            context.close()
            browser.close()


if __name__ == "__main__":
    main()
