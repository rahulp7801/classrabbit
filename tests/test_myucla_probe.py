from pathlib import Path
import re
import unittest
from types import SimpleNamespace

from playwright.sync_api import sync_playwright

from classrabbit.myucla_probe import is_class_search, target_rows


class ProbeScopeTests(unittest.TestCase):
    def test_public_schedule_cannot_masquerade_as_authenticated_source(self):
        self.assertFalse(is_class_search("https://sa.ucla.edu/ro/Public/SOC/Results"))
        self.assertTrue(is_class_search("https://sa.ucla.edu/ro/ClassSearch/Results"))
        self.assertFalse(is_class_search("https://unrelated.example/ro/ClassSearch"))
        self.assertEqual(target_rows(SimpleNamespace(url="https://sa.ucla.edu/ro/Public/SOC/"), "186827200"), [])

    def test_only_target_lecture_and_linked_rows_are_recorded(self):
        folder = Path(__file__).parent / "fixtures"
        markup = (folder / "ucla_engr186w_listing.html").read_text(encoding="utf-8")
        markup += (folder / "ucla_engr186w_children.html").read_text(encoding="utf-8")
        markup = re.sub(r"<script\b[^>]*>.*?</script>", "", markup, flags=re.S | re.I)
        markup += '<div class="data_row" id="999999999_OTHER"><div class="sectionColumn"><a>Other student data</a></div><div class="statusColumn">Open</div></div>'
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch()
            try:
                page = browser.new_page()
                page.set_content(markup)
                rows = target_rows(page.main_frame, "186827200")
                self.assertEqual(len(rows), 7)
                self.assertEqual(sum(row["is_lecture"] for row in rows), 1)
                self.assertNotIn("999999999", [row["class_id"] for row in rows])
                self.assertTrue(all(set(row) == {"class_id", "section", "status_text", "is_lecture"} for row in rows))
            finally:
                browser.close()
