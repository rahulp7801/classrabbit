"""Manual-sign-in, read-only MyUCLA inspection for the requested section.

This is a source-verification tool, not a working authenticated alert adapter.
It never enters credentials, clicks enrollment controls, or changes classes.
Only the specified lecture and its linked rows are recorded, under .local/.
"""

import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import re
import time
from urllib.parse import urlsplit

from playwright.sync_api import Error, TimeoutError, sync_playwright

ENTRY = "https://my.ucla.edu/directLink.aspx?featureID=203"
HOSTS = {"my.ucla.edu", "sa.ucla.edu"}
READ_ENDPOINTS = {"/ro/classsearch/results/getcoursesummary", "/ro/classsearch/results/getcoursetitlespage"}


def is_class_search(url):
    parts = urlsplit(url)
    return parts.scheme == "https" and parts.hostname == "sa.ucla.edu" and parts.path.lower().startswith("/ro/classsearch")


def target_rows(frame, lecture_id):
    """Read only this lecture's rows, including open-shadow-DOM components."""
    if frame.url != "about:blank" and not is_class_search(frame.url):
        return []
    selector = f'.data_row[id^="{lecture_id}_"], .data_row[id*="_{lecture_id}_"]'
    rows = frame.locator(selector)
    result = {}
    for index in range(min(rows.count(), 100)):
        row = rows.nth(index)
        path = row.get_attribute("id") or ""
        class_id = path.split("_")[0]
        if not re.fullmatch(r"\d{9}", class_id):
            continue
        section = row.locator(".sectionColumn a").first
        status = row.locator(".statusColumn").first
        if section.count() != 1 or status.count() != 1:
            continue
        result[class_id] = {"class_id": class_id, "section": " ".join(section.inner_text().split()),
                            "status_text": " ".join(status.inner_text().split()),
                            "is_lecture": class_id == lecture_id}
    return list(result.values())


def write_status(folder, state, **data):
    status = {"state": state, "observed_at": datetime.now(timezone.utc).isoformat(), **data}
    (folder / "status.json").write_text(json.dumps(status, indent=2), encoding="utf-8")


def main():
    parser = argparse.ArgumentParser(description="Open an isolated MyUCLA browser for manual sign-in and read-only class inspection.")
    parser.add_argument("--class-id", default="186827200")
    parser.add_argument("--minutes", type=int, default=20)
    args = parser.parse_args()
    if not re.fullmatch(r"\d{9}", args.class_id) or not 1 <= args.minutes <= 60:
        parser.error("Use a nine-digit lecture ID and 1-60 minutes.")
    folder = Path(".local/myucla-probe").resolve()
    folder.mkdir(parents=True, exist_ok=True)
    with sync_playwright() as playwright:
        context = playwright.chromium.launch_persistent_context(
            str(folder / "browser-profile"), headless=False, viewport={"width": 1280, "height": 900})
        page = context.pages[0] if context.pages else context.new_page()

        def response_metadata(response):
            parts = urlsplit(response.url)
            if (parts.hostname == "sa.ucla.edu" and parts.path.lower() in READ_ENDPOINTS
                    and response.request.method == "GET"):
                # No request headers, query strings, cookies, or response bodies are logged.
                item = {"observed_at": datetime.now(timezone.utc).isoformat(), "path": parts.path,
                        "status": response.status, "cache_control": response.headers.get("cache-control")}
                with (folder / "endpoint-metadata.jsonl").open("a", encoding="utf-8") as stream:
                    stream.write(json.dumps(item) + "\n")

        context.on("page", lambda new_page: new_page.on("response", response_metadata))
        page.on("response", response_metadata)
        write_status(folder, "opening_browser", lecture_id=args.class_id)
        print("An isolated browser is opening. Complete UCLA sign-in and Duo manually.", flush=True)
        print("Then open Find a Class and Enroll, choose Winter 2027 ENGR 186W, and expand Lecture 1 discussions.", flush=True)
        try:
            try:
                page.goto(ENTRY, wait_until="domcontentloaded", timeout=30000)
            except TimeoutError:
                print("Initial navigation is still loading; use the visible browser window.", flush=True)
            previous = None
            deadline = time.monotonic() + args.minutes * 60
            while context.pages and time.monotonic() < deadline:
                found = {}
                class_search_visible = False
                for current_page in context.pages:
                    for frame in current_page.frames:
                        host = urlsplit(frame.url).hostname
                        if not is_class_search(frame.url):
                            continue
                        class_search_visible = True
                        try:
                            for row in target_rows(frame, args.class_id):
                                found[row["class_id"]] = row
                        except Error:
                            continue
                rows = sorted(found.values(), key=lambda row: (not row["is_lecture"], row["class_id"]))
                state = "target_rows_visible" if rows else "waiting_for_manual_sign_in_and_class_selection"
                write_status(folder, state, lecture_id=args.class_id, rows=rows,
                             on_ucla_class_search=class_search_visible)
                signature = json.dumps(rows, sort_keys=True)
                if signature != previous:
                    print(json.dumps({"state": state, "lecture_id": args.class_id, "target_rows": rows}), flush=True)
                    previous = signature
                page.wait_for_timeout(1000)
        except Error:
            print("The inspection browser was closed or disconnected.", flush=True)
        except KeyboardInterrupt:
            pass
        finally:
            context.close()
            print("Inspection stopped. Local sign-in profile remains excluded from Git.", flush=True)


if __name__ == "__main__":
    main()
