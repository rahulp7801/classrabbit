import json
import re
import unittest
from unittest.mock import patch
from urllib.parse import parse_qs, urlencode, urlsplit

from classrabbit.myucla import MyUCLASource, read_only_url
from classrabbit.ucla import SourceError
from tests.immediate_proof import ENGR_URL
from tests.test_combinations import DETAIL, LISTING
from tests.test_fast_checks import PRIMARY


class FakeReader:
    session_status = "active"

    def __init__(self, fail=False):
        self.calls = []
        self.fail = fail

    def get(self, url):
        self.calls.append(read_only_url(url))
        if self.fail:
            raise SourceError("MyUCLA sign-in required")
        # Synthetic count chosen for this test; no account-derived response is stored.
        response = re.sub(r'(<div class="statusColumn" id="186827200_[^"]+-status_data">).*?(</div>)',
                          r'\1<p>Closed<br/>Class Full (100)</p>\2', PRIMARY, count=1, flags=re.S)
        return response.replace("/ro/Public/SOC/Results/ClassDetail", "/ro/ClassSearch/Results/ClassDetail")

    def close(self):
        pass


class MyUCLASourceTests(unittest.TestCase):
    def test_enrollment_and_arbitrary_urls_cannot_be_requested(self):
        for url in ["https://sa.ucla.edu/ro/ClassSearch/Enrollment/EnrollAClass",
                    "https://sa.ucla.edu/ro/ClassSearch/Results/GetCourseSummary?model={}&action=enroll",
                    "https://example.com/ro/ClassSearch/Results/GetCourseSummary?model={}",
                    "http://sa.ucla.edu/ro/ClassSearch/Results/GetCourseSummary?model={}",
                    "https://sa.ucla.edu/ro/ClassSearch/Results/GetCourseSummary?model={}&model={}"]:
            with self.subTest(url=url), self.assertRaises(SourceError):
                read_only_url(url)

    def test_only_known_section_model_fields_are_allowed(self):
        url = "https://sa.ucla.edu/ro/Public/SOC/Results/GetCourseSummary?" + urlencode({"model": json.dumps({"StudentID": "unneeded"})})
        with self.assertRaises(SourceError):
            read_only_url(url)

    def test_authenticated_counts_override_public_counts(self):
        def fetch(url):
            return DETAIL if "ClassDetail?" in url else json.dumps({"ClassPartialViewData": LISTING})
        reader = FakeReader()
        with patch("classrabbit.ucla.fetch", side_effect=fetch):
            source = MyUCLASource(reader)
            snapshot = source.check(ENGR_URL)
        self.assertEqual(source.interval, 2)
        self.assertEqual((snapshot.status, snapshot.available, snapshot.capacity), ("closed", 0, 100))
        self.assertEqual(snapshot.combination_status, "blocked")
        self.assertEqual(snapshot.source, "ucla_myucla")
        self.assertTrue(all(urlsplit(url).path == "/ro/ClassSearch/Results/GetCourseSummary" for url in reader.calls))

    def test_expired_sign_in_never_falls_back_to_public_counts(self):
        def fetch(url):
            return DETAIL if "ClassDetail?" in url else json.dumps({"ClassPartialViewData": LISTING})
        with patch("classrabbit.ucla.fetch", side_effect=fetch):
            source = MyUCLASource(FakeReader(fail=True))
            with self.assertRaisesRegex(SourceError, "sign-in required"):
                source.check(ENGR_URL)
