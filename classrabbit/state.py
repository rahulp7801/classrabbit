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
            CREATE TABLE IF NOT EXISTS verified_combinations (
                school TEXT, term TEXT, class_id TEXT, status TEXT NOT NULL,
                PRIMARY KEY (school, term, class_id)
            );
        """)

    def record(self, snapshot):
        key = (snapshot.school, snapshot.term, snapshot.class_id)
        serialized = json.dumps(snapshot.as_dict())
        with self.db:
            old = self.db.execute("SELECT status FROM verified_combinations WHERE school=? AND term=? AND class_id=?", key).fetchone()
            verified = snapshot.combination_status in {"available", "blocked"}
            available = (snapshot.combination_status == "available" and snapshot.status == "open"
                         and snapshot.available and snapshot.available > 0 and snapshot.open_combinations)
            reopened = bool(old and old[0] == "blocked" and available)
            # Unknown linked-section checks cannot erase a last verified baseline.
            if verified and (snapshot.combination_status == "blocked" or available):
                self.db.execute("INSERT OR REPLACE INTO verified_combinations VALUES (?, ?, ?, ?)",
                                (*key, snapshot.combination_status))
            self.db.execute("INSERT OR REPLACE INTO observations VALUES (?, ?, ?, ?)", (*key, serialized))
            if reopened:
                self.db.execute("INSERT INTO events (school, term, class_id, observed_at, snapshot) VALUES (?, ?, ?, ?, ?)",
                                (*key, snapshot.checked_at, serialized))
        return reopened

    def close(self):
        self.db.close()
