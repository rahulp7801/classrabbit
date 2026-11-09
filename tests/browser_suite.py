"""Run real Chromium against a real API server with an injected test data source.

Usage: python -m tests.browser_suite --iterations 3
No browser API routes are mocked except the explicit disconnected-server scenario.
"""

import argparse
from contextlib import contextmanager
from pathlib import Path
import socket
import tempfile
import threading
import time

from playwright.sync_api import expect, sync_playwright
import uvicorn

from classrabbit.api import create_app
from tests.support import Clock, FixtureSource

ARTIFACTS = Path(".local/browser-tests")


@contextmanager
def server(source_factory=FixtureSource, *, worker=False, real_clock=False):
    with tempfile.TemporaryDirectory() as directory:
        clock = time.time if real_clock else Clock()
        source = source_factory(clock)
        app = create_app(Path(directory) / "browser.sqlite3", source, worker=worker)
        listener = socket.socket()
        listener.bind(("127.0.0.1", 0))
        port = listener.getsockname()[1]
        running = uvicorn.Server(uvicorn.Config(app, log_level="error"))
        thread = threading.Thread(target=running.run, kwargs={"sockets": [listener]}, daemon=True)
        thread.start()
        deadline = time.monotonic() + 10
        try:
            while not running.started:
                if not thread.is_alive() or time.monotonic() > deadline:
                    raise RuntimeError("Browser-test API failed to start")
                time.sleep(.02)
            app.state.service.clock = clock
            yield f"http://127.0.0.1:{port}", source, clock
        finally:
            running.should_exit = True
            thread.join(timeout=10)
            listener.close()
            if thread.is_alive():
                raise RuntimeError("Browser-test API failed to stop")


def search_and_add(page):
    page.get_by_role("button", name="Add a class", exact=True).click()
    page.locator("#term").select_option("27W")
    page.get_by_role("button", name="Find sections").click()
    expect(page.locator(".search-result")).to_have_count(2)
    page.get_by_role("button", name="Watch section", exact=True).first.click()
    expect(page.get_by_test_id("watch-card")).to_have_count(1)
    expect(page.locator("#search-dialog")).not_to_be_visible()


def lifecycle(page, url, source, clock, suffix):
    page.goto(url)
    expect(page.locator("#empty-state")).to_be_visible()
    expect(page.locator("#fixture-banner")).to_be_visible()
    page.screenshot(path=str(ARTIFACTS / f"empty-{suffix}.png"), full_page=True)
    search_and_add(page)
    expect(page.get_by_test_id("watch-card")).to_contain_text("No complete seat combination")
    expect(page.locator("#closed-count")).to_have_text("1")
    page.reload()
    expect(page.get_by_test_id("watch-card")).to_have_count(1)
    assert source.check_calls == 1, "Reload must not contact UCLA again"
    # Duplicate detection is visible in the actual search UI.
    page.get_by_role("button", name="Add a class", exact=True).click()
    page.get_by_role("button", name="Find sections").click()
    expect(page.get_by_role("button", name="Already watching")).to_be_disabled()
    page.keyboard.press("Escape")
    source.status = "open"
    clock.advance()
    page.get_by_role("button", name="Check section", exact=False).click()
    expect(page.locator("#open-count")).to_have_text("1")
    expect(page.locator(".seat-count")).to_have_text("2")
    expect(page.locator("#toast")).to_contain_text("seat opening")
    page.screenshot(path=str(ARTIFACTS / f"open-{suffix}.png"), full_page=True)
    page.get_by_role("button", name="Check section", exact=False).click()
    expect(page.locator("#toast")).to_contain_text("Already checked")
    page.get_by_role("button", name="Activity", exact=True).click()
    expect(page.get_by_test_id("reopening-event")).to_have_count(1)
    expect(page.get_by_test_id("reopening-event")).to_contain_text("COM SCI 31")
    page.get_by_role("button", name="Watchlist", exact=False).click()
    source.error = True
    clock.advance()
    page.get_by_role("button", name="Check section", exact=False).click()
    expect(page.get_by_test_id("watch-card")).to_contain_text("Complete set unverified")
    expect(page.get_by_test_id("watch-card")).to_contain_text("Previous successful report: section open")
    expect(page.locator(".seat-count")).to_have_text("—")
    expect(page.locator("#open-count")).to_have_text("0")
    page.screenshot(path=str(ARTIFACTS / f"outage-{suffix}.png"), full_page=True)
    page.get_by_role("button", name="Remove COM SCI 31 Lec 1").click()
    expect(page.locator("#empty-state")).to_be_visible()
    page.get_by_role("button", name="Activity", exact=True).click()
    expect(page.get_by_test_id("reopening-event")).to_have_count(1)


def validation_and_errors(page, url, source, clock, suffix):
    page.goto(url)
    page.get_by_role("button", name="Add a class", exact=True).click()
    page.locator("#term").select_option("27W")
    page.get_by_label("Course number", exact=True).fill("abc")
    page.get_by_role("button", name="Find sections").click()
    assert not page.locator("#course").evaluate("node => node.checkValidity()")
    assert source.search_calls == 0
    page.get_by_label("Course number", exact=True).fill("31")
    source.search_error = True
    page.get_by_role("button", name="Find sections").click()
    expect(page.locator("#search-error")).to_contain_text("No sections found")
    expect(page.get_by_role("button", name="Find sections")).to_be_enabled()
    source.search_error = False
    page.get_by_role("button", name="Find sections").click()
    expect(page.locator(".search-result")).to_have_count(2)
    page.locator("#term").select_option("26F")
    expect(page.locator(".search-result")).to_have_count(0)
    page.locator("#term").select_option("27W")
    page.get_by_text("Have a UCLA section link?", exact=True).click()
    page.get_by_label("Public ClassDetail link", exact=True).fill("https://example.com/")
    page.get_by_role("button", name="Watch this section", exact=True).click()
    expect(page.locator("#search-error")).to_contain_text("public HTTPS UCLA")
    assert source.check_calls == 0
    page.keyboard.press("Escape")
    expect(page.locator("#search-dialog")).not_to_be_visible()
    expect(page.get_by_role("button", name="Add a class", exact=True)).to_be_focused()


def mobile_and_filters(page, url, source, clock, suffix):
    page.set_viewport_size({"width": 390, "height": 844})
    page.goto(url)
    expect(page.locator("#empty-state")).to_be_visible()
    search_and_add(page)
    page.get_by_role("button", name="Open", exact=True).click()
    expect(page.locator("#no-filter-results")).to_be_visible()
    expect(page.locator("#empty-state")).not_to_be_visible()
    page.get_by_role("button", name="Waiting", exact=True).click()
    expect(page.get_by_test_id("watch-card")).to_have_count(1)
    page.screenshot(path=str(ARTIFACTS / f"mobile-{suffix}.png"), full_page=True)
    for width in [320, 390, 768, 1440]:
        page.set_viewport_size({"width": width, "height": 900})
        assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth"), f"Horizontal overflow at {width}px"
    page.get_by_role("button", name="Activity", exact=True).click()
    expect(page.locator("#empty-events")).to_be_visible()
    expect(page.get_by_role("link", name="API docs")).to_have_attribute("href", "/docs")


def unavailable_source_and_disconnect(page, url, source, clock, suffix):
    page.goto(url)
    source.error = True
    search_and_add(page)
    expect(page.get_by_test_id("watch-card")).to_contain_text("Complete set unverified")
    expect(page.get_by_test_id("watch-card")).to_contain_text("Not yet checked")
    page.route("**/api/watchlist", lambda route: route.abort("failed"))
    page.get_by_role("button", name="Refresh list", exact=False).click()
    expect(page.locator("#toast")).to_contain_text("Could not reach the local app")
    expect(page.get_by_test_id("watch-card")).to_have_count(1)
    expect(page.get_by_role("button", name="Refresh list", exact=False)).to_be_enabled()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--iterations", type=int, default=3)
    args = parser.parse_args()
    if not 1 <= args.iterations <= 20:
        parser.error("Choose between 1 and 20 iterations")
    ARTIFACTS.mkdir(parents=True, exist_ok=True)
    scenarios = [lifecycle, validation_and_errors, mobile_and_filters, unavailable_source_and_disconnect]
    total = 0
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch()
        try:
            for iteration in range(1, args.iterations + 1):
                for scenario in scenarios:
                    name = f"{scenario.__name__}-{iteration}"
                    with server() as (url, source, clock):
                        context = browser.new_context(viewport={"width": 1440, "height": 1050})
                        context.tracing.start(screenshots=True, snapshots=True, sources=True)
                        page = context.new_page()
                        page_errors = []
                        console_errors = []
                        page.on("pageerror", lambda error: page_errors.append(str(error)))
                        page.on("console", lambda message: console_errors.append(message.text) if message.type == "error" else None)
                        try:
                            scenario(page, url, source, clock, name)
                            assert not page_errors, page_errors
                            if scenario in {lifecycle, mobile_and_filters}:
                                assert not console_errors, console_errors
                            total += 1
                            print(f"PASS {name}", flush=True)
                        except Exception:
                            page.screenshot(path=str(ARTIFACTS / f"FAILED-{name}.png"), full_page=True)
                            print(f"FAIL {name}; page errors={page_errors}; console errors={console_errors}", flush=True)
                            raise
                        finally:
                            context.tracing.stop(path=str(ARTIFACTS / f"trace-{name}.zip"))
                            context.close()
        finally:
            browser.close()
    print(f"PASS: {total} Chromium scenarios across {args.iterations} iterations. Artifacts: {ARTIFACTS}", flush=True)


if __name__ == "__main__":
    main()
