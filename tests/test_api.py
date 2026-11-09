from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import tempfile
import threading
import unittest

from fastapi.testclient import TestClient

from classrabbit.api import create_app
from tests.support import Clock, FixtureSource, URL


class ApiTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.database = Path(self.temp.name) / "app.sqlite3"
        self.clock = Clock()
        self.source = FixtureSource(self.clock)
        self.app = create_app(self.database, self.source, worker=False)
        self.client = TestClient(self.app)
        self.client.__enter__()
        self.app.state.service.clock = self.clock

    def tearDown(self):
        self.client.__exit__(None, None, None)
        self.temp.cleanup()

    def add(self):
        return self.client.post("/api/watchlist", json={"source_url": URL})

    def test_health_source_and_openapi(self):
        self.assertEqual(self.client.get("/api/health").json()["status"], "ok")
        source = self.client.get("/api/source").json()
        self.assertFalse(source["realtime"])
        self.assertEqual(source["mode"], "fixture")
        self.assertIn("/api/watchlist/{watch_id}/refresh", self.client.get("/openapi.json").json()["paths"])
        self.assertEqual(self.client.get("/").status_code, 200)

    def test_search_validation_and_cache_normalization(self):
        for query in ["term=BAD&subject=COM%20SCI&course=31", "term=27W&subject=COM%20SCI&course=abc"]:
            self.assertEqual(self.client.get("/api/sections?" + query).status_code, 422)
        for course in ["31", "0031"]:
            result = self.client.get("/api/sections", params={"term": "27W", "subject": "com sci", "course": course})
            self.assertEqual(result.status_code, 200)
            self.assertEqual(len(result.json()["sections"]), 2)
        self.assertEqual(self.source.search_calls, 1)

    def test_upstream_search_failure_is_explicit(self):
        self.source.search_error = True
        response = self.client.get("/api/sections?term=27W&subject=COM%20SCI&course=31")
        self.assertEqual(response.status_code, 502)
        self.assertIn("No sections", response.json()["detail"])

    def test_add_is_idempotent_and_refresh_obeys_hourly_cooldown(self):
        response = self.add()
        self.assertEqual(response.status_code, 201)
        item = response.json()
        duplicate = self.add()
        self.assertEqual(duplicate.status_code, 200)
        self.assertEqual(duplicate.json()["id"], item["id"])
        self.source.status = "open"
        result = self.client.post(f"/api/watchlist/{item['id']}/refresh").json()
        self.assertTrue(result["cached"])
        self.assertEqual(result["watch"]["snapshot"]["status"], "closed")
        self.assertEqual(self.source.check_calls, 1)

    def test_one_reopening_event_and_error_preserves_snapshot(self):
        item = self.add().json()
        self.source.status = "open"
        self.clock.advance()
        result = self.client.post(f"/api/watchlist/{item['id']}/refresh").json()
        self.assertTrue(result["reopened"])
        self.client.post(f"/api/watchlist/{item['id']}/refresh")
        self.assertEqual(len(self.client.get("/api/events").json()), 1)
        good_snapshot = result["watch"]["snapshot"]
        self.source.error = True
        self.clock.advance()
        failed = self.client.post(f"/api/watchlist/{item['id']}/refresh").json()
        self.assertEqual(failed["watch"]["snapshot"], good_snapshot)
        self.assertTrue(failed["watch"]["stale"])
        self.assertIn("outage", failed["watch"]["error"])
        self.assertFalse(failed["reopened"])
        self.assertEqual(len(self.client.get("/api/events").json()), 1)

    def test_first_check_failure_keeps_unknown_watch_and_retries(self):
        self.source.error = True
        item = self.add().json()
        self.assertIsNone(item["snapshot"])
        self.assertEqual(item["failures"], 1)
        self.source.error = False
        self.clock.advance()
        self.app.state.service.run_due()
        updated = self.client.get("/api/watchlist").json()[0]
        self.assertIsNotNone(updated["snapshot"])
        self.assertIsNone(updated["error"])
        self.assertEqual(self.client.get("/api/events").json(), [])

    def test_scheduled_check_and_delete(self):
        item = self.add().json()
        self.source.status = "open"
        self.clock.advance()
        self.app.state.service.run_due()
        self.assertEqual(len(self.client.get("/api/events").json()), 1)
        self.assertEqual(self.client.delete(f"/api/watchlist/{item['id']}").status_code, 204)
        self.assertEqual(self.client.get("/api/watchlist").json(), [])
        self.assertEqual(self.client.post(f"/api/watchlist/{item['id']}/refresh").status_code, 404)
        self.add()
        self.assertEqual(len(self.client.get("/api/events").json()), 1, "Re-add must establish a new baseline")

    def test_background_worker_runs_due_checks_and_stops(self):
        self.add()
        self.clock.advance()
        self.source.status = "open"
        checked = threading.Event()
        original = self.source.check

        def observe(url):
            result = original(url)
            checked.set()
            return result

        self.source.check = observe
        service = self.app.state.service
        service.start()
        self.assertTrue(checked.wait(timeout=5), "Worker did not run the due check")
        self.assertEqual(len(self.client.get("/api/events").json()), 1)
        service.stop()
        self.assertFalse(service.worker.is_alive())

    def test_watchlist_and_events_survive_app_restart(self):
        item = self.add().json()
        self.source.status = "open"
        self.clock.advance()
        self.client.post(f"/api/watchlist/{item['id']}/refresh")
        with TestClient(create_app(self.database, self.source, worker=False)) as restarted:
            self.assertEqual(restarted.get("/api/watchlist").json()[0]["id"], item["id"])
            self.assertEqual(len(restarted.get("/api/events").json()), 1)

    def test_concurrent_adds_share_one_check(self):
        with ThreadPoolExecutor(max_workers=4) as executor:
            responses = list(executor.map(lambda _: self.add(), range(4)))
        self.assertEqual(sorted(r.status_code for r in responses), [200, 200, 200, 201])
        self.assertEqual(self.source.check_calls, 1)
        self.assertEqual(len(self.client.get("/api/watchlist").json()), 1)

    def test_local_origin_and_url_boundaries(self):
        self.assertEqual(self.client.post("/api/watchlist", json={"source_url": "http://127.0.0.1/private"}).status_code, 400)
        self.assertEqual(self.client.post("/api/watchlist", json={"source_url": "https://[invalid"}).status_code, 400)
        self.assertEqual(self.client.post("/api/watchlist", json={"source_url": URL}, headers={"Origin": "https://unrelated.example"}).status_code, 403)
        self.assertEqual(self.client.get("/api/health", headers={"Host": "unrelated.example"}).status_code, 400)
        self.assertEqual(self.source.check_calls, 0)


if __name__ == "__main__":
    unittest.main()
