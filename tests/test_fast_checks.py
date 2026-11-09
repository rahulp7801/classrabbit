from pathlib import Path
import json
import re
import tempfile
import unittest
from unittest.mock import patch
from urllib.parse import parse_qs, urlsplit

from classrabbit.service import PublicSource, WatchService
from tests.support import Clock, FixtureSource
from tests.immediate_proof import ENGR_URL
from tests.test_combinations import CHILDREN, DETAIL, LISTING

PRIMARY = (Path(__file__).parent / "fixtures" / "ucla_engr186w_primary_summary.html").read_text(encoding="utf-8")


class FastCheckTests(unittest.TestCase):
    def test_fast_default_and_invalid_intervals(self):
        self.assertEqual(PublicSource(30).interval, 30)
        for interval in [0, 14, 3601]:
            with self.subTest(interval=interval), self.assertRaises(ValueError):
                PublicSource(interval)

    def test_each_check_refreshes_both_summary_levels_but_reuses_metadata(self):
        calls = []

        def fetch(url):
            calls.append(url)
            if "ClassDetail?" in url:
                return DETAIL
            if "GetCourseTitlesPage?" in url:
                return json.dumps({"ClassPartialViewData": LISTING})
            model = json.loads(parse_qs(urlsplit(url).query)["model"][0])
            if model["IsRoot"]:
                return PRIMARY
            return CHILDREN.replace("186827200_ENGR0186W", model["Path"])

        with patch("classrabbit.ucla.fetch", side_effect=fetch), patch("classrabbit.combination.fetch", side_effect=fetch):
            source = PublicSource(30)
            for _ in range(2):
                snapshot = source.check(ENGR_URL)
                self.assertEqual(snapshot.combination_status, "blocked")
                self.assertEqual(len(snapshot.linked_sections), 6)
                self.assertEqual(snapshot.source, "ucla_public_summary")
        self.assertEqual(sum("ClassDetail?" in url for url in calls), 1)
        self.assertEqual(sum("GetCourseTitlesPage?" in url for url in calls), 1)
        self.assertEqual(sum("GetCourseSummary?" in url for url in calls), 4)

    def test_fresh_primary_closure_overrides_cached_open_detail(self):
        def fetch(url):
            if "ClassDetail?" in url:
                return DETAIL
            if "GetCourseTitlesPage?" in url:
                return json.dumps({"ClassPartialViewData": LISTING})
            model = json.loads(parse_qs(urlsplit(url).query)["model"][0])
            if model["IsRoot"]:
                return re.sub(r'(<div class="statusColumn" id="186827200_[^"]+-status_data">).*?(</div>)',
                              r'\1<p>Closed<br/>Class Full (120)</p>\2', PRIMARY, count=1, flags=re.S)
            self.fail("Closed lecture must not require child requests to suppress an opening")

        with patch("classrabbit.ucla.fetch", side_effect=fetch), patch("classrabbit.combination.fetch", side_effect=fetch):
            snapshot = PublicSource(30).check(ENGR_URL)
        self.assertEqual((snapshot.status, snapshot.available, snapshot.combination_status), ("closed", 0, "blocked"))

    def test_check_is_due_at_thirty_seconds_without_waiting_an_hour(self):
        with tempfile.TemporaryDirectory() as directory:
            clock = Clock()
            source = FixtureSource(clock)
            source.interval = 30
            service = WatchService(Path(directory) / "app.sqlite3", source, clock)
            from tests.support import URL
            service.add(URL)
            source.status = "open"
            clock.advance(29)
            service.run_due()
            self.assertEqual(source.check_calls, 1)
            clock.advance(1)
            service.run_due()
            self.assertEqual(source.check_calls, 2)
            self.assertEqual(len(service.events()), 1)
            self.assertEqual(service.source_info()["check_interval_seconds"], 30)
            self.assertIsNone(service.source_info()["refresh_interval_seconds"])

    def test_restart_with_fast_interval_clamps_an_existing_hourly_deadline(self):
        with tempfile.TemporaryDirectory() as directory:
            clock = Clock()
            source = FixtureSource(clock)
            database = Path(directory) / "app.sqlite3"
            original = WatchService(database, source, clock)
            from tests.support import URL
            original.add(URL)
            source.interval = 30
            source.status = "open"
            upgraded = WatchService(database, source, clock)
            clock.advance(30)
            upgraded.run_due()
            self.assertEqual(source.check_calls, 2)
            self.assertEqual(len(upgraded.events()), 1)

    def test_response_time_does_not_add_another_full_check_interval(self):
        with tempfile.TemporaryDirectory() as directory:
            clock = Clock()
            source = FixtureSource(clock)
            source.interval = 2
            original = source.check

            def slow_read(url):
                clock.advance(.6)
                return original(url)

            source.check = slow_read
            service = WatchService(Path(directory) / "app.sqlite3", source, clock)
            from tests.support import URL
            service.add(URL)
            source.status = "open"
            clock.advance(1.41)
            service.run_due()
            self.assertEqual(source.check_calls, 2)
            self.assertEqual(len(service.events()), 1)
