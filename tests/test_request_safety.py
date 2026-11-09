from datetime import datetime, timezone
from email.utils import format_datetime
from pathlib import Path
import tempfile
import threading
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch
from urllib.error import HTTPError

from fastapi.testclient import TestClient

from classrabbit.api import create_app
from classrabbit.myucla import BrowserReader, MyUCLASource
from classrabbit.request_safety import RequestSafety, SafetyHold, retry_after_seconds
from classrabbit.service import WatchService
from classrabbit import ucla
from tests.support import Clock, FixtureSource, URL


class AdvancingWait:
    def __init__(self, clock):
        self.clock = clock

    def is_set(self):
        return False

    def wait(self, delay):
        self.clock.advance(delay)
        return False


class RequestSafetyTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.path = Path(self.temp.name) / "read-safety.json"
        self.clock = Clock()
        self.guard = self.make_guard()

    def tearDown(self):
        self.temp.cleanup()

    def make_guard(self):
        return RequestSafety(self.path, self.clock, jitter=lambda: 0)

    def test_all_reads_share_spacing_and_restart_preserves_it(self):
        starts = []
        for _ in range(8):
            self.guard.acquire(AdvancingWait(self.clock))
            starts.append(self.clock())
            self.guard = self.make_guard()
        self.assertEqual([b - a for a, b in zip(starts, starts[1:])], [10] * 7)
        self.assertLessEqual(sum(t < starts[0] + 60 for t in starts), 6)

    def test_retry_after_seconds_date_invalid_and_past(self):
        future = format_datetime(datetime.fromtimestamp(self.clock() + 7200, timezone.utc))
        self.assertEqual(retry_after_seconds("7200", self.clock()), 7200)
        self.assertAlmostEqual(retry_after_seconds(future, self.clock()), 7200, delta=1)
        for value in [None, "garbage", "-10", "Wed, 01 Jan 2020 00:00:00 GMT"]:
            self.assertEqual(retry_after_seconds(value, self.clock()), 0)

    def test_429_honors_long_date_and_does_not_reset_on_restart(self):
        future = format_datetime(datetime.fromtimestamp(self.clock() + 7200, timezone.utc))
        with self.assertRaises(SafetyHold):
            self.guard.response(429, future)
        self.guard = self.make_guard()
        with self.assertRaises(SafetyHold) as caught:
            self.guard.acquire(AdvancingWait(self.clock))
        self.assertGreater(caught.exception.retry_at - self.clock(), 7198)
        with self.assertRaises(SafetyHold):
            self.guard.resume_sign_in()
        self.clock.advance(7201)
        self.guard.check()
        with self.assertRaises(SafetyHold):
            self.guard.response(429, "1")
        self.clock.advance(100000)
        with self.assertRaisesRegex(SafetyHold, "repeated rate limiting"):
            self.make_guard().check()

    def test_short_retry_after_still_gets_fifteen_minute_cooldown(self):
        with self.assertRaises(SafetyHold):
            self.guard.response(429, "2")
        self.assertEqual(self.guard.info()["cooldown_until"], self.clock() + 900)

    def test_access_denial_is_durable_and_cannot_resume_as_sign_in(self):
        with self.assertRaises(SafetyHold):
            self.guard.response(403)
        self.clock.advance(100000)
        with self.assertRaisesRegex(SafetyHold, "HTTP 403"):
            self.make_guard().check()
        with self.assertRaises(SafetyHold):
            self.guard.resume_sign_in()

    def test_sign_in_redirect_and_401_require_explicit_resume(self):
        for status in [301, 302, 303, 307, 308, 401]:
            with self.assertRaises(SafetyHold):
                self.guard.response(status)
            self.guard = self.make_guard()
            with self.assertRaises(SafetyHold):
                self.guard.check()
            self.guard.resume_sign_in()
            self.guard.check()

    def test_transient_failure_backoff_and_retry_after(self):
        for expected in [60, 120, 240]:
            now = self.clock()
            with self.assertRaises(SafetyHold):
                self.guard.response(503)
            self.assertEqual(self.guard.info()["cooldown_until"], now + expected)
            self.clock.advance(expected)
        with self.assertRaises(SafetyHold):
            self.guard.response(503, "7200")
        self.assertEqual(self.guard.info()["cooldown_until"], self.clock() + 7200)

    def test_corrupt_or_unwritable_state_fails_closed(self):
        self.path.write_text('{"cooldown_until": "bad"}', encoding="utf-8")
        with self.assertRaisesRegex(SafetyHold, "repair"):
            self.make_guard().check()
        with patch.object(Path, "write_text", side_effect=OSError("disk unavailable")):
            with self.assertRaisesRegex(SafetyHold, "persist"):
                self.guard.acquire(AdvancingWait(self.clock))

    def test_transport_sends_nothing_during_hold_or_after_restart(self):
        response = Mock(status=429, headers={"retry-after": "1800"})
        context = SimpleNamespace(request=Mock())
        context.request.get.return_value = response
        reader = BrowserReader.__new__(BrowserReader)
        reader.safety = self.guard
        reader.stopping = threading.Event()
        for _ in range(5):
            with self.assertRaises(SafetyHold):
                reader.read_summary(context, "https://sa.ucla.edu/ro/ClassSearch/Results/GetCourseSummary?model={}")
            reader.safety = self.make_guard()
        self.assertEqual(context.request.get.call_count, 1)
        self.assertEqual(context.request.get.call_args.kwargs["max_redirects"], 0)
        response.dispose.assert_called_once()

    def test_metadata_uses_same_budget_and_records_rate_response(self):
        error = HTTPError(URL, 429, "limited", {"Retry-After": "1800"}, None)
        with patch("classrabbit.ucla.build_opener") as opener:
            opener.return_value.open.side_effect = error
            for _ in range(3):
                with self.assertRaises(SafetyHold):
                    ucla.fetch(URL, safety=self.guard, stop_event=threading.Event())
            self.assertEqual(opener.return_value.open.call_count, 1)
        self.assertEqual(self.make_guard().info()["cooldown_until"], self.clock() + 1800)

    def test_stopping_interrupts_pacing_without_another_request(self):
        stopped = threading.Event()
        stopped.set()
        with self.assertRaisesRegex(SafetyHold, "stopping"):
            self.guard.acquire(stopped)

    def test_scheduler_respects_hold_deadline_and_cached_refresh(self):
        source = FixtureSource(self.clock)
        source.interval = 30
        deadline = self.clock() + 7200
        source.check = Mock(side_effect=SafetyHold("cooldown", deadline))
        database = self.path.parent / "app.sqlite3"
        service = WatchService(database, source, self.clock)
        item, _ = service.add(URL)
        for _ in range(5):
            self.assertTrue(service.refresh(item["id"])["cached"])
        self.assertEqual(source.check.call_count, 1)
        self.assertAlmostEqual(datetime.fromisoformat(item["next_check_at"]).timestamp(), deadline, places=5)

    def test_paused_source_marks_other_watch_observations_unverified(self):
        source = FixtureSource(self.clock)
        source.status = "open"
        service = WatchService(self.path.parent / "app.sqlite3", source, self.clock)
        service.add(URL)
        source.safety_status = {"pause_reason": "access_denied", "cooldown_until": 0}
        item = service.list_watches()[0]
        self.assertTrue(item["stale"])
        self.assertIsNotNone(item["error"])

    def test_api_manual_sign_in_resume_does_not_clear_access_hold(self):
        reader = SimpleNamespace(safety=self.guard, session_status="browser_open", close=lambda: None)
        source = MyUCLASource(reader)
        with TestClient(create_app(self.path.parent / "api.sqlite3", source, worker=False)) as client:
            with self.assertRaises(SafetyHold):
                self.guard.response(401)
            self.assertEqual(client.get("/api/source").json()["session_status"], "sign_in_required")
            self.assertEqual(client.post("/api/source/resume").status_code, 200)
            with self.assertRaises(SafetyHold):
                self.guard.response(403)
            self.assertEqual(client.post("/api/source/resume").status_code, 409)
            self.assertEqual(self.guard.info()["pause_reason"], "access_denied")

    def test_fast_intervals_are_rejected(self):
        for interval in [1, 2, 10, 29, 3601]:
            with self.assertRaises(ValueError):
                MyUCLASource(reader=object(), poll_seconds=interval)
