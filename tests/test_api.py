"""Unit tests for REST API endpoints."""
import json
import os
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

import anyio
import httpx
from httpx import ASGITransport
from starlette.testclient import TestClient

from mangopi_web import (
    app, event_bus, init_db, insert_task, update_task,
    append_event, list_events, get_task,
)


async def _sse_read_until_close(task_id: str) -> list[bytes]:
    """Connect to /events/{task_id} over ASGI, push a terminal event,
    and collect all SSE data lines until 'event: close' is received.

    Using ``httpx.AsyncClient`` + ``ASGITransport`` avoids the deadlock
    that ``TestClient.stream()`` suffers from: ``portal.call()`` blocks
    until the full body is consumed, but an SSE generator never completes
    on its own — it awaits ``queue.get()`` forever.  With the async
    client the response body is consumed lazily via ``aiter_lines()``,
    giving us a chance to push a queue item from the same coroutine.
    """
    lines: list[bytes] = []
    transport = ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        async with client.stream("GET", f"/events/{task_id}") as resp:
            assert resp.status_code == 200
            assert "text/event-stream" in resp.headers.get("content-type", "")
            async for line in resp.aiter_lines():
                lines.append(line.encode())
                # After receiving any line, push the terminal event
                # so the async generator breaks out of queue.get()
                q = event_bus.get(task_id)
                if q:
                    q[0].put_nowait({"type": "complete", "_seq": 999})
                # Stop once we see the close event
                if b"event: close" in lines[-1]:
                    break
    return lines


class TestAPI(unittest.TestCase):
    """Test all HTTP endpoints with a temporary DB and mock CLI mode."""

    @classmethod
    def setUpClass(cls):
        # Enable mock mode so spawned CLI uses fake_cli.py (deterministic)
        os.environ["MANGOPI_WEB_MODE"] = "mock"

        # Use a temporary DB for all API tests
        cls._tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        cls._tmp.close()
        cls._db_path = cls._tmp.name
        # Monkey-patch before init_db is called by the module
        import mangopi_web
        mangopi_web.DEFAULT_DB = Path(cls._db_path)
        mangopi_web.MOCK_MODE = True
        init_db()
        cls.client = TestClient(app)

    @classmethod
    def tearDownClass(cls):
        os.unlink(cls._db_path)
        os.environ.pop("MANGOPI_WEB_MODE", None)

    def setUp(self):
        """Reset concurrency state AND clean DB between tests so
        tasks/events inserted by one test don't leak into the next."""
        import mangopi_web as mw
        # Reset the semaphore to its initial value (MAX_CONCURRENT)
        mw.active_slots._value = mw.MAX_CONCURRENT
        mw._released_slots.clear()
        # Wipe DB tables — guarantees test isolation
        with mw.db_conn() as c:
            c.execute("DELETE FROM task_events")
            c.execute("DELETE FROM tasks")

    def tearDown(self):
        """Kill any lingering subprocesses and clean up bus state."""
        import mangopi_web as mw
        for tid, proc in list(mw._processes.items()):
            if proc.poll() is None:
                try:
                    proc.kill()
                    proc.wait(timeout=5)
                except Exception:
                    pass
        mw._processes.clear()
        mw.event_bus.clear()

    # ── Health ───────────────────────────────────────────────────

    def test_health_ok(self):
        resp = self.client.get("/health")
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertEqual(data["web"], "ok")
        self.assertIn("mode", data)
        self.assertIn("active", data)
        self.assertIn("max_concurrent", data)

    def test_health_structure(self):
        """GET /health returns all expected fields with correct mode."""
        resp = self.client.get("/health")
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertEqual(data["web"], "ok")
        self.assertIn("db", data)
        self.assertIn("mode", data)
        self.assertIn("active", data)
        self.assertIn("max_concurrent", data)
        self.assertEqual(data["mode"], "mock")   # we set mock mode

    # ── Workspace ────────────────────────────────────────────────

    def test_workspace(self):
        resp = self.client.get("/api/workspace")
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertIn("path", data)
        self.assertIn("branch", data)
        self.assertGreater(len(data["path"]), 0)

    def test_workspace_git_failure(self):
        """GET /api/workspace should use fallback when git fails."""
        with patch("subprocess.run", side_effect=Exception("git error")):
            resp = self.client.get("/api/workspace")
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertIn("path", data)
        self.assertIn("branch", data)
        self.assertEqual(data["branch"], "main")  # fallback value

    # ── Git Commits ──────────────────────────────────────────────

    def test_git_commits(self):
        resp = self.client.get("/api/git/commits")
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        # Should return a list (may be empty in shallow clones)
        self.assertIsInstance(data, list)
        if data:
            self.assertIn("hash", data[0])
            self.assertIn("msg", data[0])

    def test_git_commits_mock_fallback(self):
        """GET /api/git/commits should return mock data when git fails."""
        with patch("subprocess.run", side_effect=Exception("git error")):
            resp = self.client.get("/api/git/commits")
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertIsInstance(data, list)
        self.assertGreater(len(data), 0)  # mock data is non-empty
        self.assertIn("hash", data[0])
        self.assertIn("msg", data[0])
        self.assertIn("files", data[0])

    # ── Index & Task List ────────────────────────────────────────

    def test_index_page(self):
        """GET / should return the SPA shell."""
        resp = self.client.get("/")
        self.assertEqual(resp.status_code, 200)
        self.assertIn("text/html", resp.headers.get("content-type", ""))
        self.assertIn("mangopi", resp.text.lower())

    def test_tasks_partial_empty(self):
        """GET /tasks with zero tasks should show the empty-state placeholder."""
        resp = self.client.get("/tasks")
        self.assertEqual(resp.status_code, 200)
        self.assertIn("text/html", resp.headers.get("content-type", ""))
        self.assertIn("No tasks yet", resp.text)

    def test_tasks_partial_with_tasks(self):
        """GET /tasks should list tasks newest-first."""
        insert_task("t1", "Task One", "goal 1")
        insert_task("t2", "Task Two", "goal 2")
        resp = self.client.get("/tasks")
        self.assertEqual(resp.status_code, 200)
        self.assertIn("Task One", resp.text)
        self.assertIn("Task Two", resp.text)

    # ── Tasks CRUD via API ───────────────────────────────────────

    def test_create_task(self):
        resp = self.client.post("/tasks", data={
            "name": "API Test", "goal": "test the api endpoints"
        })
        self.assertEqual(resp.status_code, 200)
        # Response should contain HX-Trigger
        self.assertIn("HX-Trigger", resp.headers)
        trigger = json.loads(resp.headers["HX-Trigger"])
        self.assertIn("load-phase-view", trigger)
        self.assertIn("close-modal", trigger)
        # Load-phase-view value is the 8-char hex task_id
        self.assertIsInstance(trigger["load-phase-view"], str)
        self.assertEqual(len(trigger["load-phase-view"]), 8)

        # Verify the task was actually inserted in the DB
        task_id = trigger["load-phase-view"]
        task = get_task(task_id)
        self.assertIsNotNone(task)
        self.assertEqual(task["name"], "API Test")
        self.assertEqual(task["goal"], "test the api endpoints")
        self.assertEqual(task["status"], "running")

    def test_create_task_with_mode_fast(self):
        """POST /tasks with mode=fast should create a task."""
        resp = self.client.post("/tasks", data={
            "name": "Fast", "goal": "do it fast", "mode": "fast"
        })
        self.assertEqual(resp.status_code, 200)
        self.assertIn("HX-Trigger", resp.headers)
        trigger = json.loads(resp.headers["HX-Trigger"])
        self.assertIn("load-phase-view", trigger)

    def test_create_task_with_mode_wish(self):
        """POST /tasks with mode=wish should create a task."""
        resp = self.client.post("/tasks", data={
            "name": "Wish", "goal": "research this", "mode": "wish"
        })
        self.assertEqual(resp.status_code, 200)
        self.assertIn("HX-Trigger", resp.headers)
        trigger = json.loads(resp.headers["HX-Trigger"])
        self.assertIn("load-phase-view", trigger)

    def test_create_task_with_push(self):
        """POST /tasks with push=on should create a task with push phase."""
        resp = self.client.post("/tasks", data={
            "name": "Push", "goal": "commit", "push": "on"
        })
        self.assertEqual(resp.status_code, 200)
        self.assertIn("HX-Trigger", resp.headers)

    def test_create_task_empty_name(self):
        """POST /tasks with empty name should auto-generate from goal."""
        resp = self.client.post("/tasks", data={
            "name": "", "goal": "fix the login bug"
        })
        self.assertEqual(resp.status_code, 200)
        # The rendered task row contains task.name which equals the goal
        self.assertIn("fix the login bug", resp.text)

    def test_create_task_queue_full(self):
        """POST /tasks when at max concurrent should return 503."""
        import mangopi_web as mw
        original_locked = mw.active_slots.locked
        mw.active_slots.locked = lambda: True
        try:
            resp = self.client.post("/tasks", data={
                "name": "Overflow", "goal": "should not run"
            })
            self.assertEqual(resp.status_code, 503)
            self.assertIn("queue full", resp.text.lower())
        finally:
            mw.active_slots.locked = original_locked

    def test_create_task_spawn_failure(self):
        """POST /tasks when spawn_cli raises should return 500 and release slot."""
        import mangopi_web as mw
        original_spawn = mw.spawn_cli

        def _spawn_fail(*args, **kwargs):
            raise RuntimeError("mock spawn failure")
        mw.spawn_cli = _spawn_fail

        try:
            resp = self.client.post("/tasks", data={
                "name": "SpawnFail", "goal": "will not spawn"
            })
            self.assertEqual(resp.status_code, 500)
            self.assertIn("spawn failed", resp.text.lower())
            # Slot should have been released (semaphore value stays unchanged)
            self.assertEqual(mw.active_slots._value, mw.MAX_CONCURRENT)
        finally:
            mw.spawn_cli = original_spawn

    # ── Task Detail ──────────────────────────────────────────────

    def test_get_task_detail(self):
        """GET /tasks/{id} with HX-Request returns partial + OOB swaps."""
        insert_task("detail-test", "Detail", "show details")
        update_task("detail-test", status="running", current_phase="plan")
        resp = self.client.get("/tasks/detail-test",
                               headers={"HX-Request": "true"})
        self.assertEqual(resp.status_code, 200)
        # Response should include OOB swaps and the task name
        self.assertIn("hx-swap-oob", resp.text)
        self.assertIn("Detail", resp.text)

    def test_get_task_not_found(self):
        resp = self.client.get("/tasks/nonexistent")
        self.assertEqual(resp.status_code, 404)

    def test_task_detail_non_htmx(self):
        """GET /tasks/{id} without HX-Request returns full detail page."""
        insert_task("non-htmx", "NonHTMX", "detail page")
        update_task("non-htmx", status="running")
        resp = self.client.get("/tasks/non-htmx")
        self.assertEqual(resp.status_code, 200)
        self.assertIn("text/html", resp.headers.get("content-type", ""))
        self.assertIn("NonHTMX", resp.text)

    def test_task_detail_realtime(self):
        """GET /tasks/{id}?realtime=1&last_seq=N filters older events."""
        insert_task("rt1", "Realtime", "realtime test")
        update_task("rt1", status="running")
        # Append two events; second event carries "develop" phase
        append_event("rt1", {"type": "iter", "n": 1, "phase": "plan"})
        time.sleep(0.01)  # ensure distinct seq
        append_event("rt1", {"type": "iter", "n": 2, "phase": "develop"})
        resp = self.client.get(
            "/tasks/rt1?realtime=1&last_seq=1",
            headers={"HX-Request": "true"},
        )
        self.assertEqual(resp.status_code, 200)
        # Only the second event (seq=2) should be rendered; the first (seq=1)
        # must be filtered out by the realtime last_seq=1 parameter.
        # We assert on data-event-seq attributes rather than phase names
        # because "plan" and "develop" both appear in the stage-header
        # detail text "plan + develop" regardless of which event is present.
        self.assertIn('data-event-seq="2"', resp.text)
        self.assertNotIn('data-event-seq="1"', resp.text)

    # ── Advance & Pipeline OOB ───────────────────────────────────

    def test_advance_task(self):
        """POST /tasks/{id}/advance returns OOB card updates."""
        insert_task("adv1", "AdvanceMe", "advance test")
        update_task("adv1", status="running", current_phase="plan")
        resp = self.client.post("/tasks/adv1/advance")
        self.assertEqual(resp.status_code, 200)
        self.assertIn("text/html", resp.headers.get("content-type", ""))
        # OOB swap attributes from task_item_oob.html (no task name)
        self.assertIn("hx-swap-oob", resp.text)
        self.assertIn("adv1-dot", resp.text)

    def test_advance_task_not_found(self):
        """POST /tasks/{id}/advance for nonexistent task → 404."""
        resp = self.client.post("/tasks/nonexistent/advance")
        self.assertEqual(resp.status_code, 404)

    def test_pipeline_oob(self):
        """GET /tasks/{id}/pipeline-oob returns pipeline status updates."""
        insert_task("poob1", "PipeOOB", "pipeline test")
        update_task("poob1", status="running")
        resp = self.client.get("/tasks/poob1/pipeline-oob")
        self.assertEqual(resp.status_code, 200)
        self.assertIn("text/html", resp.headers.get("content-type", ""))
        # OOB swap elements from pipeline_oob.html (no task name)
        self.assertIn("hx-swap-oob", resp.text)
        self.assertIn("phase-timeline-placeholder", resp.text)
        self.assertIn("phase-event-count", resp.text)

    def test_pipeline_oob_not_found(self):
        """GET /tasks/{id}/pipeline-oob for nonexistent task → 404."""
        resp = self.client.get("/tasks/nonexistent/pipeline-oob")
        self.assertEqual(resp.status_code, 404)

    # ── Delete ───────────────────────────────────────────────────

    def test_delete_task(self):
        insert_task("del-task", "Delete Me", "to be deleted")
        resp = self.client.delete("/tasks/del-task")
        self.assertEqual(resp.status_code, 200)
        # Should no longer be in the DB
        self.assertIsNone(get_task("del-task"))

    def test_delete_nonexistent_task(self):
        resp = self.client.delete("/tasks/nonexistent")
        self.assertEqual(resp.status_code, 404)

    def test_delete_running_task(self):
        """DELETE /tasks/{id} should kill its subprocess and clean up."""
        import mangopi_web as mw
        insert_task("running-del", "Running Delete", "to be deleted")
        update_task("running-del", status="running")
        # Simulate a running subprocess attached to this task
        proc = subprocess.Popen(
            [sys.executable, "-c", "import time; time.sleep(30)"],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )
        mw._processes["running-del"] = proc
        try:
            resp = self.client.delete("/tasks/running-del")
            self.assertEqual(resp.status_code, 200)
            self.assertIsNone(get_task("running-del"))
            # The process should have been killed
            proc.wait(timeout=5)
            self.assertIsNotNone(proc.returncode)
        finally:
            if proc.poll() is None:
                proc.kill()
                proc.wait()

    # ── SSE Events ───────────────────────────────────────────────

    def test_sse_event_stream(self):
        """SSE stream returns heartbeat, accepts a pushed event, and closes.

        Uses ``httpx.AsyncClient`` + ``ASGITransport`` to avoid the
        ``TestClient.stream()`` deadlock (see ``_sse_read_until_close``).
        """
        insert_task("sse-live", "SSE Live", "live sse test")
        lines = anyio.run(_sse_read_until_close, "sse-live")
        self.assertTrue(any(b": connected" in line for line in lines))
        self.assertTrue(any(b"event: close" in line for line in lines))

    def test_sse_done_task_returns_empty_stream(self):
        """GET /events/{id} for a done task yields empty body."""
        insert_task("sse-done", "SSE Done", "already done")
        update_task("sse-done", status="done", finished_at=time.time())
        with self.client.stream("GET", "/events/sse-done") as resp:
            self.assertEqual(resp.status_code, 200)
            self.assertIn(
                "text/event-stream",
                resp.headers.get("content-type", ""),
            )
            # The body should be empty (no yield for done tasks)
            body = b"".join(resp.iter_lines())
            self.assertEqual(body, b"")

    def test_sse_nonexistent_task(self):
        with self.client.stream("GET", "/events/nonexistent") as resp:
            self.assertEqual(resp.status_code, 404)

    # ── Heatmap ──────────────────────────────────────────────────

    def test_heatmap(self):
        """GET /api/heatmap returns 84 daily entries."""
        insert_task("hm1", "Heat", "heatmap test")
        resp = self.client.get("/api/heatmap")
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertIsInstance(data, list)
        self.assertGreaterEqual(len(data), 84, "expected 12 weeks of data")
        self.assertIn("date", data[0])
        self.assertIn("count", data[0])
        # Today's entry should have count >= 1 (our inserted task)
        today = time.strftime("%Y-%m-%d")
        today_entry = next((d for d in data if d["date"] == today), None)
        if today_entry:
            self.assertGreaterEqual(today_entry["count"], 1)


if __name__ == "__main__":
    unittest.main()
