"""A single-process watchlist service with shared checks and durable local state."""

from contextlib import contextmanager
from datetime import datetime, timezone
import json
import logging
import sqlite3
import threading
import time
from urllib.parse import urlencode, parse_qs, urlsplit
from uuid import uuid4

from .state import State
from .eventhub import EventHub
from . import ucla


def iso(timestamp):
    return datetime.fromtimestamp(timestamp, timezone.utc).isoformat()


class MissingWatch(Exception):
    pass


class WatchLimit(Exception):
    pass


class PublicSource:
    interval = 3600
    mode = "live"
    label = "UCLA public schedule"

    def search(self, term, subject, course):
        return ucla.find_sections(term, subject, course)

    def check(self, url):
        return ucla.check(url)


class WatchService:
    def __init__(self, database, source=None, clock=time.time):
        self.database = str(database)
        self.source = source or PublicSource()
        self.clock = clock
        self.lock = threading.RLock()
        self.search_cache = {}
        self.stop_event = threading.Event()
        self.worker = None
        self.event_hub = EventHub()
        with self.storage() as store:
            store.db.execute("""CREATE TABLE IF NOT EXISTS watches (
                id TEXT PRIMARY KEY, term TEXT NOT NULL, class_id TEXT NOT NULL,
                source_url TEXT NOT NULL, added_at REAL NOT NULL,
                last_attempt_at REAL, next_check_at REAL NOT NULL,
                error TEXT, failures INTEGER NOT NULL DEFAULT 0,
                UNIQUE(term, class_id)
            )""")
            store.db.commit()
            # Old lecture-only observations must be rechecked after this upgrade.
            for row in store.db.execute("SELECT term, class_id, snapshot FROM observations").fetchall():
                if "combination_status" not in json.loads(row["snapshot"]):
                    store.db.execute("UPDATE watches SET next_check_at=0 WHERE term=? AND class_id=?",
                                     (row["term"], row["class_id"]))
            store.db.commit()

    @contextmanager
    def storage(self):
        # Each calling thread owns its SQLite connection.
        store = State(self.database)
        store.db.row_factory = sqlite3.Row
        try:
            yield store
        finally:
            store.close()

    def source_info(self):
        return {"id": "ucla_public_detail", "label": self.source.label,
                "mode": self.source.mode, "realtime": False,
                "refresh_interval_seconds": self.source.interval,
                "notice": "Public section counts update hourly. Confirm current availability and eligibility in MyUCLA."}

    def search(self, term, subject, course):
        key = (term, subject.strip().upper(), ucla.normalize_course(course))
        with self.lock:
            cached = self.search_cache.get(key)
            if cached and self.clock() - cached[0] < 300:
                return {"sections": cached[1], "source": self.source_info(), "cached": True}
            sections = self.source.search(*key)
            # Bounded memory cache; never used as a source of section availability.
            if len(self.search_cache) >= 100:
                self.search_cache.pop(next(iter(self.search_cache)))
            self.search_cache[key] = (self.clock(), sections)
            return {"sections": sections, "source": self.source_info(), "cached": False}

    def _item(self, store, row):
        observation = store.db.execute(
            "SELECT snapshot FROM observations WHERE school='ucla' AND term=? AND class_id=?",
            (row["term"], row["class_id"])).fetchone()
        snapshot = json.loads(observation[0]) if observation else None
        query = parse_qs(urlsplit(row["source_url"]).query)
        fallback = query["subj_area_cd"][0].strip() + " " + query["crs_catlg_no"][0].strip().lstrip("0")
        stale = snapshot is None or bool(row["error"])
        if snapshot:
            stale = stale or self.clock() - datetime.fromisoformat(snapshot["checked_at"]).timestamp() >= max(self.source.interval, 1)
        return {"id": row["id"], "term": row["term"], "class_id": row["class_id"],
                "label": snapshot["course"] if snapshot else fallback,
                "snapshot": snapshot, "error": row["error"], "stale": stale,
                "failures": row["failures"], "added_at": iso(row["added_at"]),
                "last_attempt_at": iso(row["last_attempt_at"]) if row["last_attempt_at"] is not None else None,
                "next_check_at": iso(row["next_check_at"])}

    def list_watches(self):
        with self.lock, self.storage() as store:
            rows = store.db.execute("SELECT * FROM watches ORDER BY added_at, id").fetchall()
            return [self._item(store, row) for row in rows]

    def add(self, url):
        identity = ucla.validate_url(url)
        url = ucla.BASE + "Results/ClassDetail?" + urlencode(sorted(identity.items()))
        with self.lock, self.storage() as store:
            existing = store.db.execute("SELECT * FROM watches WHERE term=? AND class_id=?",
                                        (identity["term_cd"], identity["class_id"])).fetchone()
            if existing:
                return self._item(store, existing), False
            if store.db.execute("SELECT count(*) FROM watches").fetchone()[0] >= 25:
                raise WatchLimit("This local prototype supports up to 25 watched sections.")
            watch_id = str(uuid4())
            now = self.clock()
            store.db.execute("INSERT INTO watches (id, term, class_id, source_url, added_at, next_check_at) VALUES (?, ?, ?, ?, ?, ?)",
                             (watch_id, identity["term_cd"], identity["class_id"], url, now, now))
            store.db.commit()
            # Re-adding a previously removed section starts a fresh baseline.
            store.db.execute("DELETE FROM observations WHERE school='ucla' AND term=? AND class_id=?",
                             (identity["term_cd"], identity["class_id"]))
            store.db.execute("DELETE FROM verified_combinations WHERE school='ucla' AND term=? AND class_id=?",
                             (identity["term_cd"], identity["class_id"]))
            store.db.commit()
            return self.refresh(watch_id)["watch"], True

    def refresh(self, watch_id):
        with self.lock, self.storage() as store:
            row = store.db.execute("SELECT * FROM watches WHERE id=?", (watch_id,)).fetchone()
            if row is None:
                raise MissingWatch()
            now = self.clock()
            if now < row["next_check_at"]:
                return {"watch": self._item(store, row), "cached": True, "reopened": False}
            reopened = False
            try:
                snapshot = self.source.check(row["source_url"])
                if snapshot.term != row["term"] or snapshot.class_id != row["class_id"]:
                    raise ucla.SourceError("Source returned a different section; previous observation preserved.")
                reopened = store.record(snapshot)
                next_check = self.clock() + self.source.interval
                store.db.execute("UPDATE watches SET last_attempt_at=?, next_check_at=?, error=NULL, failures=0 WHERE id=?",
                                 (now, next_check, watch_id))
            except ucla.SourceError as exc:
                failures = row["failures"] + 1
                delay = max(300, self.source.interval) * min(2 ** min(failures - 1, 2), 4)
                store.db.execute("UPDATE watches SET last_attempt_at=?, next_check_at=?, error=?, failures=? WHERE id=?",
                                 (now, self.clock() + delay, str(exc), failures, watch_id))
            store.db.commit()
            updated = store.db.execute("SELECT * FROM watches WHERE id=?", (watch_id,)).fetchone()
            if reopened:
                event = store.db.execute("SELECT * FROM events ORDER BY id DESC LIMIT 1").fetchone()
                self.event_hub.publish(self._event(event))
            return {"watch": self._item(store, updated), "cached": False, "reopened": reopened}

    def remove(self, watch_id):
        with self.lock, self.storage() as store:
            cursor = store.db.execute("DELETE FROM watches WHERE id=?", (watch_id,))
            store.db.commit()
            if cursor.rowcount == 0:
                raise MissingWatch()

    def events(self, limit=50):
        with self.lock, self.storage() as store:
            rows = store.db.execute("SELECT * FROM events ORDER BY id DESC LIMIT ?", (limit,)).fetchall()
            return [self._event(row) for row in rows]

    @staticmethod
    def _event(row):
        snapshot = json.loads(row["snapshot"])
        kind = "combination_opening" if snapshot.get("combination_status") == "available" else "legacy_lecture_opening"
        return {"id": row["id"], "term": row["term"], "class_id": row["class_id"],
                "observed_at": row["observed_at"], "snapshot": snapshot, "kind": kind}

    def run_due(self):
        with self.lock, self.storage() as store:
            due = [row[0] for row in store.db.execute("SELECT id FROM watches WHERE next_check_at<=? ORDER BY next_check_at", (self.clock(),))]
        for watch_id in due:
            if self.stop_event.is_set():
                break
            try:
                self.refresh(watch_id)
            except MissingWatch:
                pass

    def start(self):
        def run():
            while not self.stop_event.is_set():
                try:
                    self.run_due()
                except Exception:
                    logging.exception("Scheduled section check failed; retrying on the next worker pass")
                self.stop_event.wait(15)
        self.worker = threading.Thread(target=run, name="classrabbit-checker", daemon=True)
        self.worker.start()

    def stop(self):
        self.stop_event.set()
        if self.worker:
            self.worker.join(timeout=30)
