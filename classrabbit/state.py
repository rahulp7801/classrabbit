"""Persist observations and one local event per observed reopening."""

import json
from pathlib import Path
import sqlite3


class State:
    def __init__(self, path):
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(path)
        self.db.executescript("""
            CREATE TABLE IF NOT EXISTS observations (
                school TEXT, term TEXT, class_id TEXT, snapshot TEXT NOT NULL,
                PRIMARY KEY (school, term, class_id)
            );
            CREATE TABLE IF NOT EXISTS events (
                id INTEGER PRIMARY KEY, school TEXT, term TEXT, class_id TEXT,
                observed_at TEXT, snapshot TEXT NOT NULL
            );
        """)

    def record(self, snapshot):
        key = (snapshot.school, snapshot.term, snapshot.class_id)
        serialized = json.dumps(snapshot.as_dict())
        with self.db:
            old = self.db.execute("SELECT snapshot FROM observations WHERE school=? AND term=? AND class_id=?", key).fetchone()
            was_closed = old and json.loads(old[0])["status"] in {"closed", "waitlist"}
            reopened = bool(was_closed and snapshot.status == "open" and snapshot.available and snapshot.available > 0)
            self.db.execute("INSERT OR REPLACE INTO observations VALUES (?, ?, ?, ?)", (*key, serialized))
            if reopened:
                self.db.execute("INSERT INTO events (school, term, class_id, observed_at, snapshot) VALUES (?, ?, ?, ?, ?)",
                                (*key, snapshot.checked_at, serialized))
        return reopened

    def close(self):
        self.db.close()
