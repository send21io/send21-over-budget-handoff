"""sqlite state: runs, webhook deliveries, events."""
from __future__ import annotations

import json
import sqlite3
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

SCHEMA = """
CREATE TABLE IF NOT EXISTS runs (
  run_id TEXT PRIMARY KEY, order_id TEXT UNIQUE, status TEXT,
  cap TEXT, spent TEXT, pending_cost TEXT, currency TEXT,
  idempotency_key TEXT, payment_request_id TEXT, pay_link TEXT,
  created_at TEXT, updated_at TEXT);
CREATE TABLE IF NOT EXISTS deliveries (delivery_id TEXT PRIMARY KEY, event TEXT, received_at TEXT);
CREATE TABLE IF NOT EXISTS events (id INTEGER PRIMARY KEY, run_id TEXT, event TEXT, body TEXT, received_at TEXT);
"""

# Status only moves forward; a late event never moves a run backwards.
ORDER = ["running", "paused", "needs_review", "confirmed", "resumed", "done"]


def now() -> str:
    return datetime.now(timezone.utc).isoformat()


class Store:
    def __init__(self, path: str = ":memory:") -> None:
        if path != ":memory:":
            Path(path).parent.mkdir(parents=True, exist_ok=True)
        self._db = sqlite3.connect(path, check_same_thread=False)
        self._db.row_factory = sqlite3.Row
        self._lock = threading.Lock()
        with self._lock:
            self._db.executescript(SCHEMA)

    def upsert_run(self, run_id: str, **fields: Any) -> None:
        with self._lock:
            row = self._db.execute("SELECT run_id FROM runs WHERE run_id=?", (run_id,)).fetchone()
            fields["updated_at"] = now()
            if row is None:
                fields.setdefault("created_at", now())
                cols = ["run_id", *fields]
                self._db.execute(f"INSERT INTO runs ({','.join(cols)}) VALUES ({','.join('?' * len(cols))})",
                                 [run_id, *fields.values()])
            else:
                sets = ",".join(f"{k}=?" for k in fields)
                self._db.execute(f"UPDATE runs SET {sets} WHERE run_id=?", [*fields.values(), run_id])
            self._db.commit()

    def get_run(self, run_id: str) -> dict | None:
        r = self._db.execute("SELECT * FROM runs WHERE run_id=?", (run_id,)).fetchone()
        return dict(r) if r else None

    def run_by_order(self, order_id: str) -> dict | None:
        r = self._db.execute("SELECT * FROM runs WHERE order_id=?", (order_id,)).fetchone()
        return dict(r) if r else None

    def advance(self, run_id: str, status: str) -> bool:
        """Move status forward only. Returns True if it changed."""
        with self._lock:
            r = self._db.execute("SELECT status FROM runs WHERE run_id=?", (run_id,)).fetchone()
            if r is None or ORDER.index(status) <= ORDER.index(r["status"]):
                return False
            self._db.execute("UPDATE runs SET status=?, updated_at=? WHERE run_id=?", (status, now(), run_id))
            self._db.commit()
            return True

    def seen_delivery(self, delivery_id: str) -> bool:
        return self._db.execute("SELECT 1 FROM deliveries WHERE delivery_id=?", (delivery_id,)).fetchone() is not None

    def add_delivery(self, delivery_id: str, event: str) -> bool:
        with self._lock:
            try:
                self._db.execute("INSERT INTO deliveries VALUES (?,?,?)", (delivery_id, event, now()))
                self._db.commit()
                return True
            except sqlite3.IntegrityError:
                return False

    def add_event(self, run_id: str | None, event: str, body: dict) -> None:
        with self._lock:
            self._db.execute("INSERT INTO events (run_id,event,body,received_at) VALUES (?,?,?,?)",
                             (run_id, event, json.dumps(body), now()))
            self._db.commit()
