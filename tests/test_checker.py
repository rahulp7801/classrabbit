from dataclasses import replace
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from classrabbit.cli import main
from classrabbit.state import State
from classrabbit.ucla import SourceError, find_sections, parse_detail, validate_url

FIXTURE = (Path(__file__).parent / "fixtures" / "ucla_detail.html").read_text(encoding="utf-8")
URL = ("https://sa.ucla.edu/ro/Public/SOC/Results/ClassDetail?term_cd=27W"
       "&subj_area_cd=COM%20SCI&crs_catlg_no=0031%20%20%20%20"
       "&class_id=187093200&class_no=%20001%20%20")


class ParsingTests(unittest.TestCase):
    def test_real_public_response_extracts_identity_counts_and_freshness(self):
        snapshot = parse_detail(FIXTURE, URL)
        self.assertEqual((snapshot.class_id, snapshot.term, snapshot.section), ("187093200", "27W", "Lec 1"))
        self.assertEqual((snapshot.status, snapshot.available, snapshot.capacity), ("open", 237, 240))
        self.assertEqual(snapshot.waitlist, "No Waitlist")
        self.assertIn("Majors", snapshot.restrictions)
        self.assertIn("once per hour", snapshot.source_notice)
        self.assertFalse(snapshot.realtime)

    def test_wrong_section_or_term_is_not_an_observation(self):
        for url in [URL.replace("187093200", "187093210"), URL.replace("27W", "26W")]:
            with self.subTest(url=url), self.assertRaises(SourceError):
                parse_detail(FIXTURE, url)

    def test_http_200_error_page_is_not_an_opening(self):
        with self.assertRaises(SourceError):
            parse_detail("<html><h2>Sorry We Can't Find That Page</h2></html>", URL)

    def test_missing_or_inconsistent_counts_are_not_an_opening(self):
        for replacement in ["Open", "Open: 0 of 240 Left", "Open: 241 of 240 Left", "Temporarily unavailable"]:
            with self.subTest(status=replacement), self.assertRaises(SourceError):
                parse_detail(FIXTURE.replace("Open: 237 of 240 Left", replacement), URL)

    def test_waitlist_and_cancelled_are_separate_from_open(self):
        for raw, expected in [("Closed: 0 of 240 Left", "closed"), ("Waitlist", "waitlist"), ("Cancelled", "cancelled")]:
            with self.subTest(status=raw):
                self.assertEqual(parse_detail(FIXTURE.replace("Open: 237 of 240 Left", raw), URL).status, expected)

    def test_no_freshness_notice_requires_review(self):
        with self.assertRaises(SourceError):
            parse_detail(FIXTURE.replace('class="message info light"', 'class="removed"'), URL)

    def test_arbitrary_hosts_login_pages_and_duplicate_identity_are_rejected(self):
        for url in [URL.replace("sa.ucla.edu", "example.com"), URL.replace("https:", "http:"),
                    "https://sa.ucla.edu/Account/Login", URL + "&class_id=187093210"]:
            with self.subTest(url=url), self.assertRaises(SourceError):
                validate_url(url)

    @patch("classrabbit.ucla.fetch", return_value="<html>Service unavailable</html>")
    def test_invalid_course_listing_is_not_empty_success(self, _fetch):
        with self.assertRaises(SourceError):
            find_sections("27W", "COM SCI", "31")

    @patch("classrabbit.ucla.fetch")
    def test_discovery_rejects_wrong_course(self, mock_fetch):
        import html
        import json
        mock_fetch.return_value = json.dumps({"ClassPartialViewData": '<a href="' + html.escape(URL) + '">Lec 1</a>'})
        with self.assertRaises(SourceError):
            find_sections("27W", "COM SCI", "32")


class StateTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.path = str(Path(self.temp.name) / "state.sqlite3")
        self.state = State(self.path)
        self.open = parse_detail(FIXTURE, URL)
        self.closed = replace(self.open, status="closed", available=0)

    def tearDown(self):
        self.state.close()
        self.temp.cleanup()

    def test_first_open_observation_is_a_baseline(self):
        self.assertFalse(self.state.record(self.open))
        self.assertFalse(self.state.record(self.open))
        self.assertEqual(self.state.db.execute("SELECT count(*) FROM events").fetchone()[0], 0)

    def test_one_event_per_closed_to_open_transition(self):
        self.assertFalse(self.state.record(self.closed))
        self.assertTrue(self.state.record(self.open))
        self.assertFalse(self.state.record(self.open))
        self.state.record(self.closed)
        self.assertTrue(self.state.record(self.open))
        self.assertEqual(self.state.db.execute("SELECT count(*) FROM events").fetchone()[0], 2)

    def test_state_survives_restart_and_is_separated_by_term(self):
        self.state.record(self.closed)
        self.state.close()
        self.state = State(self.path)
        self.assertFalse(self.state.record(replace(self.open, term="28W")))
        self.assertTrue(self.state.record(self.open))

    def test_failed_fetch_preserves_closed_baseline(self):
        self.state.record(self.closed)
        with patch("classrabbit.cli.check", side_effect=SourceError("Network unavailable")), patch("sys.stderr"):
            self.assertEqual(main(["--url", URL, "--state", self.path, "--json"]), 1)
        self.assertTrue(self.state.record(self.open))


if __name__ == "__main__":
    unittest.main()
