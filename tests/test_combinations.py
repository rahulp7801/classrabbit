from dataclasses import replace
from pathlib import Path
import re
import tempfile
import unittest

from classrabbit.combination import assess_combination
from classrabbit.state import State
from classrabbit.ucla import parse_detail
from tests.immediate_proof import ENGR_URL

FIXTURES = Path(__file__).parent / "fixtures"
DETAIL = (FIXTURES / "ucla_engr186w_detail.html").read_text(encoding="utf-8")
LISTING = (FIXTURES / "ucla_engr186w_listing.html").read_text(encoding="utf-8")
CHILDREN = (FIXTURES / "ucla_engr186w_children.html").read_text(encoding="utf-8")


def opened_discussion(markup=CHILDREN):
    return re.sub(r'(<div class="statusColumn" id="186827001_186827200_ENGR0186W-status_data">).*?(</div>)',
                  r'\1<p>Open<br/>21 of 22 Enrolled<br/>1 Spots Left</p>\2', markup, count=1, flags=re.S)


class CombinationTests(unittest.TestCase):
    def setUp(self):
        self.lecture = parse_detail(DETAIL, ENGR_URL)

    def assess(self, listing=LISTING, children=CHILDREN):
        return assess_combination(self.lecture, listing, lambda url: children)

    def test_real_lecture_ten_seats_all_six_discussions_closed_is_blocked(self):
        result = self.assess()
        self.assertEqual((result.status, result.available), ("open", 10))
        self.assertEqual(result.combination_status, "blocked")
        self.assertEqual(len(result.linked_sections), 6)
        self.assertTrue(all(s.status == "closed" for s in result.linked_sections))
        self.assertEqual(result.open_combinations, [])

    def test_one_required_discussion_opens_while_lecture_is_unchanged(self):
        result = self.assess(children=opened_discussion())
        self.assertEqual(result.combination_status, "available")
        self.assertEqual(result.available, 10)
        self.assertEqual(result.open_combinations, [["186827200", "186827001"]])

    def test_empty_missing_or_wrong_parent_children_cannot_produce_opening(self):
        for children in ["<html>Unavailable</html>", CHILDREN.replace('class="row-fluid data_row', 'class="removed'),
                         opened_discussion().replace("186827001_186827200_ENGR0186W", "186827001_999999999_ENGR0186W")]:
            with self.subTest(children=children[:30]):
                self.assertEqual(self.assess(children=children).combination_status, "unknown")

    def test_waitlist_is_not_a_seat_and_unknown_counts_fail_closed(self):
        for status in ["Waitlist: 1 of 20 Taken", "Open", "Open 0 of 22 Enrolled 0 Spots Left", "Unexpected status"]:
            children = re.sub(r'(<div class="statusColumn" id="186827001_186827200_ENGR0186W-status_data">).*?(</div>)',
                              lambda m: m[1] + status + m[2], CHILDREN, count=1, flags=re.S)
            self.assertNotEqual(self.assess(children=children).combination_status, "available")

    def test_required_model_or_instructions_missing_is_unknown(self):
        for listing in [LISTING.replace('"SequenceNumber":"1"', '"SequenceNumber":null'),
                        LISTING.replace("a Discussion", "a Laboratory"),
                        LISTING.replace('class="class-note lower-note"', 'class="removed"')]:
            self.assertEqual(self.assess(listing=listing, children=opened_discussion()).combination_status, "unknown")

    def test_discussion_opening_emits_once_and_unknown_check_preserves_baseline(self):
        with tempfile.TemporaryDirectory() as directory:
            state = State(str(Path(directory) / "test.sqlite3"))
            try:
                self.assertFalse(state.record(self.assess()))
                unknown = self.assess(children="<h1>Temporary outage</h1>")
                self.assertFalse(state.record(unknown))
                self.assertTrue(state.record(self.assess(children=opened_discussion())))
                self.assertFalse(state.record(self.assess(children=opened_discussion())))
                self.assertEqual(state.db.execute("SELECT count(*) FROM events").fetchone()[0], 1)
            finally:
                state.close()

    def test_old_lecture_only_baseline_does_not_generate_a_combination_alert(self):
        with tempfile.TemporaryDirectory() as directory:
            state = State(str(Path(directory) / "test.sqlite3"))
            try:
                self.assertFalse(state.record(replace(self.lecture, status="closed", available=0)))
                self.assertFalse(state.record(self.assess(children=opened_discussion())))
            finally:
                state.close()
