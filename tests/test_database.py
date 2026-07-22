"""Unit tests for database operations (CRUD)."""
import os
import tempfile
import unittest
from pathlib import Path
from mangopi_web import (
    init_db, get_task, list_tasks,
    insert_task, update_task, append_event, list_events,
)


class TestDatabase(unittest.TestCase):
    """Test SQLite database CRUD used by the web app."""

    def setUp(self):
        self._tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        self._tmp.close()
        self._db_path = self._tmp.name
        import mangopi_web
        mangopi_web.DEFAULT_DB = Path(self._db_path)
        init_db()

    def tearDown(self):
        import sqlite3
        try:
            sqlite3.connect(self._db_path).close()
        except Exception:
            pass
        try:
            os.unlink(self._db_path)
        except Exception:
            pass

    # ── CRUD ─────────────────────────────────────────────────────

    def test_insert_and_get_task(self):
        insert_task("my_id", "Test Task", "fix the bug")
        task = get_task("my_id")
        self.assertIsNotNone(task)
        self.assertEqual(task["id"], "my_id")
        self.assertEqual(task["name"], "Test Task")
        self.assertEqual(task["goal"], "fix the bug")
        self.assertEqual(task["status"], "queued")

    def test_insert_duplicate_id_raises(self):
        insert_task("dup", "A", "goal")
        with self.assertRaises(Exception):
            insert_task("dup", "B", "goal")

    def test_update_task_status(self):
        insert_task("t1", "T", "g")
        update_task("t1", status="running", current_phase="plan")
        task = get_task("t1")
        self.assertEqual(task["status"], "running")
        self.assertEqual(task["current_phase"], "plan")

    def test_update_task_iter(self):
        insert_task("t2", "T", "g")
        update_task("t2", current_iter=3, max_iter=5)
        task = get_task("t2")
        self.assertEqual(task["current_iter"], 3)
        self.assertEqual(task["max_iter"], 5)

    def test_list_tasks_empty(self):
        tasks = list_tasks()
        self.assertEqual(tasks, [])

    def test_list_tasks_ordered_by_created(self):
        insert_task("first", "A", "g1")
        import time
        time.sleep(0.01)
        insert_task("second", "B", "g2")
        tasks = list_tasks()
        self.assertEqual(len(tasks), 2)
        self.assertEqual(tasks[0]["id"], "second")  # most recent first
        self.assertEqual(tasks[1]["id"], "first")

    # ── Events ───────────────────────────────────────────────────

    def test_append_and_list_events(self):
        insert_task("ev1", "T", "g")
        append_event("ev1", {"type": "start", "goal": "g"})
        append_event("ev1", {"type": "iter", "n": 1})
        events = list_events("ev1")
        self.assertEqual(len(events), 2)
        self.assertEqual(events[0]["type"], "start")
        self.assertEqual(events[0]["seq"], 1)
        self.assertEqual(events[1]["type"], "iter")
        self.assertEqual(events[1]["seq"], 2)

    def test_events_empty_for_missing_task(self):
        events = list_events("no_such_task")
        self.assertEqual(events, [])

    def test_events_payload_roundtrip(self):
        import json
        insert_task("p1", "T", "g")
        payload = {"type": "tool", "name": "read", "args_preview": "a.py"}
        append_event("p1", payload)
        events = list_events("p1")
        raw = events[0]["payload"]
        # payload is a string (stored as JSON) or already parsed
        if isinstance(raw, str):
            parsed = json.loads(raw)
        else:
            parsed = raw
        self.assertEqual(parsed["name"], "read")
        self.assertEqual(parsed["args_preview"], "a.py")

    # ── Status constants ─────────────────────────────────────────

    def test_task_default_status_queued(self):
        insert_task("q1", "T", "g")
        task = get_task("q1")
        self.assertEqual(task["status"], "queued")

    def test_task_finished_at_on_done(self):
        import time
        now = time.time()
        insert_task("done1", "T", "g")
        update_task("done1", status="done", finished_at=now)
        task = get_task("done1")
        self.assertEqual(task["status"], "done")
        self.assertAlmostEqual(task["finished_at"], now, places=2)


if __name__ == "__main__":
    unittest.main()
