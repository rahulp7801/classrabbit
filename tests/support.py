from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path
import time

from classrabbit.ucla import LinkedSection, SourceError, parse_detail, validate_url

URL = ("https://sa.ucla.edu/ro/Public/SOC/Results/ClassDetail?term_cd=27W"
       "&subj_area_cd=COM%20SCI&crs_catlg_no=0031%20%20%20%20"
       "&class_id=187093200&class_no=%20001%20%20")
FIXTURE = (Path(__file__).parent / "fixtures" / "ucla_detail.html").read_text(encoding="utf-8")


class Clock:
    def __init__(self):
        self.now = time.time()

    def __call__(self):
        return self.now

    def advance(self, seconds=3601):
        self.now += seconds


class FixtureSource:
    """Injected test-only source; never exposed as production API control routes."""
    interval = 3600
    mode = "fixture"
    label = "Simulated source for automated tests"

    def __init__(self, clock=None):
        self.clock = clock or Clock()
        self.status = "closed"
        self.error = False
        self.search_error = False
        self.check_calls = 0
        self.search_calls = 0

    def search(self, term, subject, course):
        self.search_calls += 1
        if self.search_error or course == "9999":
            raise SourceError("No sections found. Verify the term, subject, and course.")
        if (term, subject, course) != ("27W", "COM SCI", "31"):
            raise SourceError("No matching sections in this test fixture.")
        return [{"class_id": "187093200", "section": "Lec 1", "url": URL},
                {"class_id": "187093210", "section": "Lec 2", "url": URL.replace("187093200", "187093210").replace("%20001", "%20002")}]

    def check(self, url):
        self.check_calls += 1
        if self.error:
            raise SourceError("Simulated UCLA outage; retry later.")
        identity = validate_url(url)
        baseline = parse_detail(FIXTURE, URL)
        is_open = self.status == "open"
        linked_id = "187093001" if identity["class_id"] == "187093200" else "187093011"
        linked = LinkedSection(linked_id, identity["class_id"], "Dis 1A", "discussion", self.status,
                               1 if is_open else 0, 30, url.replace(identity["class_id"], linked_id), False)
        return replace(baseline, term=identity["term_cd"], class_id=identity["class_id"], source_url=url,
                       section="Lec 1" if identity["class_id"] == "187093200" else "Lec 2",
                       status=self.status, available=2 if self.status == "open" else 0,
                       combination_status="available" if is_open else "blocked",
                       combination_reason="A complete section set reports seats." if is_open else "No complete seat combination is available.",
                       linked_sections=[linked], open_combinations=[[identity["class_id"], linked_id]] if is_open else [],
                       checked_at=datetime.fromtimestamp(self.clock(), timezone.utc).isoformat())
