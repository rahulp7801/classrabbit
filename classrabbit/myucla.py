"""Read-only MyUCLA transport using the user's isolated local browser session.

Only the observed GET /ro/ClassSearch/Results/GetCourseSummary route is allowed.
Credentials and cookies remain inside Chromium; enrollment routes are rejected.
"""

from concurrent.futures import Future
import json
import os
from pathlib import Path
from queue import Empty, Queue
import threading
import time
from urllib.parse import parse_qs, urlencode, urlsplit

from . import ucla
from .combination import check_summary_combination

PROFILE = Path(".local/myucla-probe/browser-profile")
PRIVATE_PATH = "/ro/ClassSearch/Results/GetCourseSummary"


def read_only_url(url):
    """Translate only known public/private section reads into authenticated GETs."""
    try:
        parts = urlsplit(url)
    except ValueError as exc:
        raise ucla.SourceError("Invalid MyUCLA section-read URL.") from exc
    paths = {"/ro/public/soc/results/getcoursesummary", PRIVATE_PATH.lower()}
    if (parts.scheme != "https" or parts.netloc.lower() != "sa.ucla.edu"
            or parts.path.lower() not in paths or parts.fragment):
        raise ucla.SourceError("Only the observed read-only MyUCLA course-summary endpoint is permitted.")
    query = parse_qs(parts.query, keep_blank_values=True)
    allowed = {"model", "FilterFlags", "IsMultiListedTitles"}
    if set(query) - allowed or any(len(values) != 1 for values in query.values()) or "model" not in query:
        raise ucla.SourceError("Unexpected parameters on a MyUCLA section-read request.")
    try:
        model = json.loads(query["model"][0])
    except (ValueError, TypeError) as exc:
        raise ucla.SourceError("Invalid section-read model.") from exc
    model_keys = {"Term", "SubjectAreaCode", "CatalogNumber", "IsRoot", "SessionGroup",
                  "ClassNumber", "SequenceNumber", "Path", "MultiListedClassFlag", "Token"}
    if not isinstance(model, dict) or set(model) - model_keys:
        raise ucla.SourceError("Unexpected fields in a MyUCLA section-read model.")
    parameters = {key: value[0] for key, value in query.items()}
    # Match the site's cache-disabled AJAX reads; this is not an auth bypass.
    parameters["_"] = str(int(time.time() * 1000))
    return "https://sa.ucla.edu" + PRIVATE_PATH + "?" + urlencode(parameters)


class BrowserReader:
    def __init__(self, profile=PROFILE):
        self.profile = Path(profile).resolve()
        self.profile.mkdir(parents=True, exist_ok=True)
        self.queue = Queue()
        self.ready = threading.Event()
        self.session_status = "starting_browser"
        self.startup_error = None
        self.blocked_until = 0
        self.thread = threading.Thread(target=self._run, name="myucla-read-only-browser", daemon=True)
        self.thread.start()
        if not self.ready.wait(30):
            raise ucla.SourceError("The isolated MyUCLA browser did not start in time.")
        if self.startup_error:
            raise ucla.SourceError(self.startup_error)

    def _run(self):
        try:
            from playwright.sync_api import Error, TimeoutError, sync_playwright
            with sync_playwright() as playwright:
                context = playwright.chromium.launch_persistent_context(
                    str(self.profile), headless=False, viewport={"width": 1280, "height": 900})
                page = context.pages[0] if context.pages else context.new_page()
                try:
                    page.goto("https://my.ucla.edu/directLink.aspx?featureID=203",
                              wait_until="domcontentloaded", timeout=20000)
                except TimeoutError:
                    pass
                self.session_status = "browser_open"
                self.ready.set()
                try:
                    while True:
                        try:
                            job = self.queue.get(timeout=.1)
                        except Empty:
                            if context.pages:
                                context.pages[0].wait_for_timeout(50)
                            continue
                        if job is None:
                            break
                        url, future = job
                        if future.cancelled():
                            continue
                        try:
                            if time.monotonic() < self.blocked_until:
                                raise ucla.SourceError("MyUCLA requested a cooldown; section checks are temporarily paused.")
                            response = context.request.get(url, timeout=15000, max_redirects=0,
                                headers={"X-Requested-With": "XMLHttpRequest", "Cache-Control": "no-cache", "Pragma": "no-cache"})
                            try:
                                if response.status in {301, 302, 303, 307, 308, 401, 403}:
                                    self.session_status = "sign_in_required"
                                    raise ucla.SourceError("MyUCLA sign-in is required or expired. Complete sign-in and Duo in the isolated browser.")
                                if response.status == 429:
                                    retry = response.headers.get("retry-after", "60")
                                    self.blocked_until = time.monotonic() + (int(retry) if retry.isdigit() else 60)
                                    self.session_status = "rate_limited"
                                    raise ucla.SourceError("MyUCLA asked for fewer requests; retry cooldown applied.")
                                if response.status != 200:
                                    raise ucla.SourceError(f"MyUCLA returned HTTP {response.status}; availability is unknown.")
                                body = response.text()
                                if len(body) > 2_000_000:
                                    raise ucla.SourceError("Unexpectedly large MyUCLA section response.")
                                if "data_row" not in body or "AddToCourseData" not in body:
                                    self.session_status = "sign_in_required"
                                    raise ucla.SourceError("MyUCLA did not return class rows. Sign in again in the isolated browser.")
                                self.session_status = "active"
                                future.set_result(body)
                            finally:
                                response.dispose()
                        except ucla.SourceError as exc:
                            future.set_exception(exc)
                        except Error:
                            self.session_status = "browser_or_connection_error"
                            future.set_exception(ucla.SourceError("The MyUCLA browser or read request failed; no availability was recorded."))
                finally:
                    context.close()
        except Exception as exc:
            # Playwright errors can contain URLs. Do not expose them or session details.
            self.startup_error = "Could not open the local MyUCLA browser. Close other inspection windows using this profile and retry. Error type: " + type(exc).__name__
            self.session_status = "browser_error"
            self.ready.set()

    def get(self, url):
        private_url = read_only_url(url)
        if not self.thread.is_alive():
            raise ucla.SourceError("The isolated MyUCLA browser is closed. Restart the authenticated server.")
        future = Future()
        self.queue.put((private_url, future))
        try:
            return future.result(timeout=25)
        except TimeoutError as exc:
            future.cancel()
            raise ucla.SourceError("The MyUCLA section read timed out.") from exc

    def close(self):
        if self.thread.is_alive():
            self.queue.put(None)
            self.thread.join(timeout=20)


class MyUCLASource:
    id = "ucla_myucla"
    mode = "live"
    label = "MyUCLA signed-in session"
    freshness = None
    max_watches = 5
    notice = "Read-only signed-in MyUCLA checks. Keep the local browser signed in; confirm your enrollment pass and restrictions before enrolling."

    def __init__(self, reader=None, poll_seconds=None):
        self.interval = int(poll_seconds if poll_seconds is not None else os.environ.get("CLASSRABBIT_MYUCLA_POLL_SECONDS", "2"))
        if not 2 <= self.interval <= 60:
            raise ValueError("CLASSRABBIT_MYUCLA_POLL_SECONDS must be between 2 and 60.")
        self.reader = reader or BrowserReader()
        self.metadata = {}

    @property
    def session_status(self):
        return self.reader.session_status

    def search(self, term, subject, course):
        return ucla.find_sections(term, subject, course)

    def check(self, url):
        identity = ucla.validate_url(url)
        cached = self.metadata.get(url)
        if cached is None or time.monotonic() - cached[0] >= 3600:
            detail = ucla.parse_detail(ucla.fetch(url), url)
            listing = ucla.course_listing(identity["term_cd"], identity["subj_area_cd"], identity["crs_catlg_no"])
            if len(self.metadata) >= 5:
                self.metadata.pop(next(iter(self.metadata)))
            self.metadata[url] = (time.monotonic(), detail, listing)
        _, detail, listing = self.metadata[url]
        result = check_summary_combination(detail, listing, self.reader.get)
        from dataclasses import replace
        return replace(result, source=self.id,
                       source_notice="Counts read from the signed-in MyUCLA section-summary endpoint. Underlying change-to-publication latency has not been independently measured.")

    def close(self):
        self.reader.close()
