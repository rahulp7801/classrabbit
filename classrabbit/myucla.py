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
from .request_safety import RequestSafety, SafetyHold

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
        self.stopping = threading.Event()
        self.safety = RequestSafety(self.profile.parent / "read-safety.json")
        self.session_status = "starting_browser"
        self.startup_error = None
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
                    self.safety.check()
                    page.goto("https://my.ucla.edu/directLink.aspx?featureID=203",
                              wait_until="domcontentloaded", timeout=20000)
                except (TimeoutError, SafetyHold):
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
                            body = self.read_summary(context, url)
                            if not future.cancelled():
                                future.set_result(body)
                        except ucla.SourceError as exc:
                            if not future.cancelled():
                                future.set_exception(exc)
                        except Error:
                            self.session_status = "browser_or_connection_error"
                            try:
                                self.safety.response(503)
                            except ucla.SourceError as exc:
                                if not future.cancelled():
                                    future.set_exception(exc)
                finally:
                    context.close()
        except Exception as exc:
            # Playwright errors can contain URLs. Do not expose them or session details.
            self.startup_error = "Could not open the local MyUCLA browser. Close other inspection windows using this profile and retry. Error type: " + type(exc).__name__
            self.session_status = "browser_error"
            self.ready.set()

    def read_summary(self, context, url):
        self.safety.acquire(self.stopping)
        response = context.request.get(url, timeout=15000, max_redirects=0,
            headers={"X-Requested-With": "XMLHttpRequest", "Cache-Control": "no-cache", "Pragma": "no-cache"})
        try:
            self.safety.response(response.status, response.headers.get("retry-after"))
            body = response.text()
            if len(body) > 2_000_000:
                self.safety.response(502)
            if "data_row" not in body or "AddToCourseData" not in body:
                self.safety.pause_for_sign_in()
            self.session_status = "active"
            return body
        finally:
            response.dispose()

    def get(self, url):
        private_url = read_only_url(url)
        self.safety.check()
        if not self.thread.is_alive():
            raise ucla.SourceError("The isolated MyUCLA browser is closed. Restart the authenticated server.")
        future = Future()
        self.queue.put((private_url, future))
        try:
            return future.result(timeout=45)
        except TimeoutError as exc:
            future.cancel()
            raise ucla.SourceError("The MyUCLA section read timed out.") from exc

    def close(self):
        self.stopping.set()
        if self.thread.is_alive():
            self.queue.put(None)
            self.thread.join(timeout=20)


class MyUCLASource:
    id = "ucla_myucla"
    mode = "live"
    label = "MyUCLA signed-in session"
    freshness = None
    max_watches = 5
    notice = "Read-only MyUCLA checks target 30 seconds by default; linked reads share a 10-second minimum gap. No UCLA-approved quota is configured. Checks may pause or take longer; confirm eligibility in MyUCLA."

    def __init__(self, reader=None, poll_seconds=None):
        self.interval = int(poll_seconds if poll_seconds is not None else os.environ.get("CLASSRABBIT_MYUCLA_POLL_SECONDS", "30"))
        if not 30 <= self.interval <= 3600:
            raise ValueError("CLASSRABBIT_MYUCLA_POLL_SECONDS must be between 30 and 3600.")
        self.reader = reader or BrowserReader()
        self.metadata = {}

    @property
    def session_status(self):
        safety = getattr(self.reader, "safety", None)
        if safety:
            info = safety.info()
            if info["pause_reason"]:
                return info["pause_reason"]
            if time.time() < info["cooldown_until"]:
                return "cooldown"
        return self.reader.session_status

    @property
    def safety_status(self):
        safety = getattr(self.reader, "safety", None)
        return safety.info() if safety else None

    def resume_sign_in(self):
        self.reader.safety.resume_sign_in()

    def ensure_available(self):
        safety = getattr(self.reader, "safety", None)
        if safety:
            safety.check()

    def search(self, term, subject, course):
        self.ensure_available()
        return ucla.find_sections(term, subject, course, fetcher=self.metadata_fetch)

    def metadata_fetch(self, url):
        safety = getattr(self.reader, "safety", None)
        if safety:
            return ucla.fetch(url, safety=safety, stop_event=self.reader.stopping)
        return ucla.fetch(url)

    def check(self, url):
        self.ensure_available()
        identity = ucla.validate_url(url)
        cached = self.metadata.get(url)
        if cached is None or time.monotonic() - cached[0] >= 3600:
            detail = ucla.parse_detail(self.metadata_fetch(url), url)
            listing = ucla.course_listing(identity["term_cd"], identity["subj_area_cd"], identity["crs_catlg_no"], fetcher=self.metadata_fetch)
            if len(self.metadata) >= 5:
                self.metadata.pop(next(iter(self.metadata)))
            self.metadata[url] = (time.monotonic(), detail, listing)
        _, detail, listing = self.metadata[url]
        result = check_summary_combination(detail, listing, self.reader.get)
        # Linked-section parsing can turn SourceError into an unknown snapshot.
        # Preserve transport holds as errors, including their retry deadlines.
        self.ensure_available()
        from dataclasses import replace
        return replace(result, source=self.id,
                       source_notice="Counts read from the signed-in MyUCLA section-summary endpoint. Underlying change-to-publication latency has not been independently measured.")

    def close(self):
        self.reader.close()
