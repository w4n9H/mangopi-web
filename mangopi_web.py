#!/usr/bin/env python3
"""mangopi-web · Task-driven SPA over htmx + FastAPI + Jinja2.

v0.1: mock-mode only (real CLI wired in v0.1.1).
Architecture: subprocess + JSONL over stdout. See design doc.

§ 1 Imports & app setup
§ 2 DB layer (sqlite3)
§ 3 CLI runner (Popen + JSONL reader thread)
§ 4 Routes (FastAPI endpoints)
§ 5 Auth placeholder (v0.2)
§ 6 CLI entry
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import sqlite3
import subprocess
import sys
import threading
import time
import uuid
from contextlib import asynccontextmanager, contextmanager
from pathlib import Path
from typing import Any, Optional

from fastapi import FastAPI, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, Response, StreamingResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

# === § 1 Imports & app setup ===========================================

HERE = Path(__file__).parent
TEMPLATES = Jinja2Templates(directory=HERE / "templates")


def _from_json(value):
    """Jinja filter: parse a JSON string into a dict (or return as-is)."""
    if isinstance(value, str):
        try:
            return json.loads(value)
        except (json.JSONDecodeError, TypeError):
            return {}
    return value or {}


def _ago_filter(unix_ts):
    """Jinja filter: convert a Unix timestamp to a short relative time."""
    if not unix_ts:
        return ""
    now = time.time()
    diff = int(now - unix_ts)
    if diff < 5:
        return "just now"
    if diff < 60:
        return f"{diff}s"
    if diff < 3600:
        return f"{diff // 60}m"
    if diff < 86400:
        return f"{diff // 3600}h"
    return f"{diff // 86400}d"


TEMPLATES.env.filters["from_json"] = _from_json
TEMPLATES.env.filters["ago"] = _ago_filter

DEFAULT_DB = Path(os.environ.get(
    "MANGOPI_WEB_DB",
    str(HERE / ".mangocli" / "web.db")))
PHASES = ["plan", "develop", "review", "test", "push"]
MAX_CONCURRENT = int(os.environ.get("MANGOPI_WEB_MAX_CONCURRENT", "3"))
MOCK_MODE = os.environ.get("MANGOPI_WEB_MODE", "mock") == "mock"
HOST = os.environ.get("MANGOPI_WEB_HOST", "127.0.0.1")
PORT = int(os.environ.get("MANGOPI_WEB_PORT", "8080"))


@asynccontextmanager
async def _lifespan(app: FastAPI):
    """Startup/shutdown lifespan."""
    init_db()
    yield


app = FastAPI(title="mangopi-web", version="0.1.0", lifespan=_lifespan)
app.mount("/static", StaticFiles(directory=HERE / "static"), name="static")
active_slots = asyncio.Semaphore(MAX_CONCURRENT)
event_bus: dict[str, list[asyncio.Queue]] = {}
_processes: dict[str, subprocess.Popen] = {}    # task_id → running CLI proc
_released_slots: set[str] = set()
_release_lock = threading.Lock()


# === § 2 DB layer =====================================================

def _db_path() -> Path:
    DEFAULT_DB.parent.mkdir(parents=True, exist_ok=True)
    return DEFAULT_DB


@contextmanager
def db_conn():
    conn = sqlite3.connect(_db_path())
    conn.row_factory = sqlite3.Row
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


def init_db() -> None:
    with db_conn() as c:
        c.executescript("""
        CREATE TABLE IF NOT EXISTS tasks (
            id              TEXT PRIMARY KEY,
            name            TEXT NOT NULL,
            goal            TEXT NOT NULL,
            status          TEXT NOT NULL,
            current_phase   TEXT,
            current_iter    INTEGER DEFAULT 0,
            max_iter        INTEGER DEFAULT 5,
            cli_ctx_path    TEXT,
            total_usage     TEXT,
            created_at      REAL NOT NULL,
            started_at      REAL,
            finished_at     REAL,
            exit_code       INTEGER,
            last_event_at   REAL
        );
        CREATE TABLE IF NOT EXISTS task_events (
            task_id     TEXT NOT NULL,
            seq         INTEGER NOT NULL,
            type        TEXT NOT NULL,
            payload     TEXT NOT NULL,
            received_at REAL NOT NULL,
            PRIMARY KEY (task_id, seq)
        );
        CREATE INDEX IF NOT EXISTS idx_tasks_status ON tasks(status);
        CREATE INDEX IF NOT EXISTS idx_events_task  ON task_events(task_id, seq);
        """)


def insert_task(task_id: str, name: str, goal: str) -> None:
    with db_conn() as c:
        c.execute("""
        INSERT INTO tasks (id, name, goal, status, max_iter, created_at)
        VALUES (?, ?, ?, 'queued', ?, ?)
        """, (task_id, name, goal, 5, time.time()))


def get_task(task_id: str) -> Optional[dict]:
    with db_conn() as c:
        row = c.execute("SELECT * FROM tasks WHERE id=?",
                        (task_id,)).fetchone()
        return dict(row) if row else None


def list_tasks() -> list[dict]:
    with db_conn() as c:
        return [dict(r) for r in c.execute(
            "SELECT * FROM tasks ORDER BY created_at DESC LIMIT 50"
        ).fetchall()]


def update_task(task_id: str, **fields: Any) -> None:
    if not fields:
        return
    cols = ", ".join(f"{k}=?" for k in fields)
    vals = list(fields.values()) + [task_id]
    with db_conn() as c:
        c.execute(f"UPDATE tasks SET {cols} WHERE id=?", vals)


def append_event(task_id: str, event: dict) -> int:
    """Insert event into task_events; return its 1-indexed seq."""
    with db_conn() as c:
        row = c.execute(
            "SELECT COALESCE(MAX(seq), 0) FROM task_events WHERE task_id=?",
            (task_id,)).fetchone()
        seq = (row[0] or 0) + 1
        c.execute("""
        INSERT INTO task_events (task_id, seq, type, payload, received_at)
        VALUES (?, ?, ?, ?, ?)
        """, (task_id, seq, event.get("type", "unknown"),
              json.dumps(event, ensure_ascii=False), time.time()))
        c.execute("UPDATE tasks SET last_event_at=? WHERE id=?",
                  (time.time(), task_id))
    return seq


def list_events(task_id: str) -> list[dict]:
    with db_conn() as c:
        return [dict(r) for r in c.execute(
            "SELECT * FROM task_events WHERE task_id=? ORDER BY seq",
            (task_id,)).fetchall()]


# === § 3 CLI runner (Popen + JSONL reader) ============================

def spawn_cli(goal: str, task_id: str) -> subprocess.Popen:
    """Spawn the CLI (or mock). Returns the Popen object.

    The output protocol is JSONL on stdout, regardless of mode.
    """
    if MOCK_MODE:
        cmd = [sys.executable, str(HERE / "mock" / "fake_cli.py"),
               "--goal", goal, "--task-id", task_id, "--output", "jsonl"]
    else:
        cli_bin = os.environ.get("MANGOPI_CLI_BIN")
        if cli_bin:
            cmd = [cli_bin, "run-loop",
                   "--goal", goal, "--task-id", task_id, "--output", "jsonl"]
        else:
            cmd = [sys.executable, "-m", "mangopi_cli", "run-loop",
                   "--goal", goal, "--task-id", task_id, "--output", "jsonl"]
    return subprocess.Popen(
        cmd,
        stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        bufsize=0, text=True,
    )


async def _release_slot_async(task_id: str) -> None:
    """Release the active slot exactly once per task."""
    with _release_lock:
        if task_id in _released_slots:
            return
        try:
            active_slots.release()
        except ValueError:
            # Already at max value; ignore.
            pass
        _released_slots.add(task_id)


def start_reader_thread(proc: subprocess.Popen, task_id: str, loop: asyncio.AbstractEventLoop) -> None:
    """Spawn a daemon thread that reads stdout line-by-line, parses
    JSON, persists into DB, and fans out to all SSE subscribers."""
    queues = event_bus.setdefault(task_id, [])

    def _reader():
        try:
            for line in proc.stdout:
                line = line.strip()
                if not line:
                    continue
                try:
                    event = json.loads(line)
                except json.JSONDecodeError:
                    event = {"type": "error", "stage": "parse",
                             "message": f"non-JSON: {line[:200]}"}
                # persist + derive state + fan out
                seq = append_event(task_id, event)
                event["_seq"] = seq               # tag for SSE dedup
                _update_task_state(task_id, event)
                for q in queues:
                    try:
                        q.put_nowait(event)
                    except asyncio.QueueFull:
                        pass
        except Exception as e:
            err = {"type": "error", "stage": "reader", "message": str(e)}
            seq = append_event(task_id, err)
            err["_seq"] = seq
            for q in queues:
                try:
                    q.put_nowait(err)
                except asyncio.QueueFull:
                    pass
        finally:
            # Wait for the process with a generous timeout so a hung
            # subprocess doesn't leave the task stuck in "running"
            # forever (and the concurrency slot unreleased).
            _WAIT_TIMEOUT = 600  # 10 minutes
            try:
                proc.wait(timeout=_WAIT_TIMEOUT)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.wait()
            exit_evt = {"type": "exit", "code": proc.returncode}
            seq = append_event(task_id, exit_evt)
            exit_evt["_seq"] = seq
            update_task(task_id,
                        status=("done" if proc.returncode == 0 else "failed"),
                        finished_at=time.time(),
                        exit_code=proc.returncode)
            for q in queues:
                try:
                    q.put_nowait(exit_evt)
                except asyncio.QueueFull:
                    pass
            asyncio.run_coroutine_threadsafe(_release_slot_async(task_id), loop)

    t = threading.Thread(target=_reader, daemon=True,
                         name=f"reader-{task_id}")
    t.start()


def _update_task_state(task_id: str, event: dict) -> None:
    """Update tasks row based on event content."""
    et = event.get("type")
    if et == "iter":
        update_task(task_id,
                    current_iter=event.get("n", 0),
                    current_phase=event.get("phase"),
                    status="running")
    elif et == "phase":
        update_task(task_id, current_phase=event.get("to"))
    elif et == "complete":
        update_task(task_id,
                    total_usage=json.dumps(event.get("total_usage", {})))
    elif et == "error":
        # A reader-level error means the reader thread itself crashed — no
        # further events will arrive, so mark the task failed immediately.
        # CLI-level errors (parse, tool, api) are noted but non-fatal;
        # the task keeps running and the finally block determines the
        # final outcome when the process exits.
        if event.get("stage") == "reader":
            update_task(task_id, status="failed", finished_at=time.time())


# === § 4 Routes =======================================================

@app.get("/", response_class=HTMLResponse)
async def index(request: Request) -> HTMLResponse:
    return TEMPLATES.TemplateResponse(
        "index.html", {"request": request, "phases": PHASES})


@app.get("/tasks", response_class=HTMLResponse)
async def tasks_partial(request: Request) -> HTMLResponse:
    return TEMPLATES.TemplateResponse(
        "partials/task_list.html",
        {"request": request, "tasks": list_tasks(), "phases": PHASES})


@app.post("/tasks", response_class=HTMLResponse)
async def create_task(request: Request,
                      name: str = Form(...),
                      goal: str = Form(...)) -> HTMLResponse:
    if active_slots.locked():
        raise HTTPException(503, f"queue full, max {MAX_CONCURRENT} concurrent")

    await active_slots.acquire()
    task_id = uuid.uuid4().hex[:8]
    insert_task(task_id, name, goal)
    loop = asyncio.get_running_loop()

    try:
        proc = spawn_cli(goal, task_id)
        _processes[task_id] = proc
        update_task(task_id, status="running", started_at=time.time())
        start_reader_thread(proc, task_id, loop)
    except Exception as e:
        _processes.pop(task_id, None)
        await _release_slot_async(task_id)
        update_task(task_id, status="failed", finished_at=time.time())
        raise HTTPException(500, f"spawn failed: {e}")

    task = get_task(task_id)
    response = TEMPLATES.TemplateResponse(
        "partials/task_item.html",
        {"request": request, "task": task, "phases": PHASES})
    # Fire a client-side event that auto-loads this task's phase view
    # into the Main area. Keeps sidebar and Main in sync from the moment
    # of creation without coupling the two responses.
    response.headers["HX-Trigger"] = json.dumps({
        "close-modal": True,
        "load-phase-view": task_id
    })
    return response


@app.delete("/tasks/{task_id}")
async def delete_task(task_id: str) -> Response:
    """Delete a task, its events, and kill its subprocess if running."""
    t = get_task(task_id)
    if not t:
        raise HTTPException(404, "task not found")

    # Kill subprocess if still running
    proc = _processes.pop(task_id, None)
    if proc is not None and proc.poll() is None:
        try:
            proc.kill()
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            pass

    # Release concurrency slot if the task held one
    if t.get("status") in ("running", "queued"):
        try:
            active_slots.release()
        except ValueError:
            pass
        _released_slots.discard(task_id)

    # Clean up event bus
    event_bus.pop(task_id, None)

    # Delete from database
    with db_conn() as c:
        c.execute("DELETE FROM task_events WHERE task_id=?", (task_id,))
        c.execute("DELETE FROM tasks WHERE id=?", (task_id,))

    # HTMX hx-swap="delete" just needs an empty 200
    return Response(status_code=200)


@app.post("/tasks/{task_id}/advance", response_class=HTMLResponse)
async def advance_task(request: Request, task_id: str) -> HTMLResponse:
    """Return OOB updates for the card's status pill, progress, and meta.

    The SSE trigger on each card posts here whenever a new event arrives.
    We respond with OOB-only markup that updates the changeable parts of
    the card in place — never replacing the card itself. That way the
    SSE EventSource (which lives on the card's outer div) is not torn
    down and recreated on every event, which would otherwise replay
    history and re-trigger /advance in a tight loop.
    """
    t = get_task(task_id)
    if not t:
        raise HTTPException(404, "task not found")
    return TEMPLATES.TemplateResponse(
        "partials/task_item_oob.html",
        {"request": request, "task": t, "phases": PHASES})


@app.get("/tasks/{task_id}", response_class=HTMLResponse)
async def task_detail(request: Request, task_id: str) -> HTMLResponse:
    t = get_task(task_id)
    if not t:
        raise HTTPException(404)
    events = list_events(task_id)
    # HTMX requests from the SPA (index page) get a partial that
    # updates #phase-content and header elements via OOB swaps.
    if request.headers.get("HX-Request"):
        # In realtime mode (SSE-triggered fetch), only return events
        # the client hasn't seen yet — identified by the last_seq
        # query param. The client uses hx-swap="beforeend" to append
        # them, giving a streaming effect.
        if request.query_params.get("realtime") == "1":
            try:
                last_seq = int(request.query_params.get("last_seq", 0))
            except ValueError:
                last_seq = 0
            events = [e for e in events if e["seq"] > last_seq]
        return TEMPLATES.TemplateResponse(
            "partials/task_phase_view.html",
            {"request": request, "task": t,
             "events": events, "phases": PHASES})
    # Direct browser navigation gets the full standalone detail page.
    return TEMPLATES.TemplateResponse(
        "partials/task_detail.html",
        {"request": request, "task": t,
         "events": events, "phases": PHASES})


@app.get("/events/{task_id}")
async def sse_stream(task_id: str) -> StreamingResponse:
    """Per-task live SSE stream. No replay by default (v0.2)."""
    t = get_task(task_id)
    if not t:
        raise HTTPException(404, "task not found")

    queue: asyncio.Queue = asyncio.Queue()
    event_bus.setdefault(task_id, []).append(queue)

    async def gen():
        try:
            # If the task already finished, there is nothing to stream live.
            # Skip the heartbeat so the browser EventSource never opens —
            # this prevents the infinite reconnect loop for completed tasks.
            if t.get("status") in ("done", "failed"):
                return
            yield ": connected\n\n"  # initial heartbeat (only for active tasks)

            # Replay events that were persisted before this SSE client
            # connected.  The reader thread starts immediately while the
            # browser may take a few hundred ms to establish the SSE
            # connection — without a catch-up replay those early events
            # would be silently lost.
            last_seq = 0
            for ev in list_events(task_id):
                last_seq = ev["seq"]
                yield f"event: {ev['type']}\ndata: {ev['payload']}\n\n"

            # Now stream live events, skipping anything we already
            # replayed above (identified by the _seq tag).
            while True:
                event = await queue.get()
                if event.get("_seq", 0) > last_seq:
                    yield f"event: {event['type']}\ndata: {json.dumps(event, ensure_ascii=False)}\n\n"
                    last_seq = event["_seq"]
                if event["type"] in ("exit", "complete"):
                    break
        finally:
            try:
                lst = event_bus[task_id]
                lst.remove(queue)
                if not lst:
                    # All subscribers gone. If the task is finished the
                    # reader thread has exited and we can safely delete
                    # the bus entry; otherwise keep it so that a new
                    # subscriber can re-attach to the same list the
                    # reader thread is still fanning out to.
                    task = get_task(task_id)
                    if task and task.get("status") in ("done", "failed"):
                        try:
                            del event_bus[task_id]
                        except KeyError:
                            pass
            except (KeyError, ValueError):
                pass

    return StreamingResponse(gen(), media_type="text/event-stream")


@app.get("/health")
async def health() -> JSONResponse:
    db_ok = True
    try:
        with db_conn() as c:
            c.execute("SELECT 1").fetchone()
    except Exception:
        db_ok = False
    return JSONResponse({
        "web": "ok",
        "db": db_ok,
        "mode": "mock" if MOCK_MODE else "real",
        "active": MAX_CONCURRENT - active_slots._value,
        "max_concurrent": MAX_CONCURRENT,
    })


# === § 5 API: Workspace & Git (v0.1.2) ================================


@app.get("/api/workspace")
async def api_workspace() -> JSONResponse:
    """Return workspace path and current git branch.

    Reads from the actual filesystem: the working directory is the
    project root (mangopi_web.py's parent). In mock mode we still
    hit the real filesystem — the data is real regardless of mode.
    """
    cwd = Path.cwd()
    branch = "main"
    try:
        result = subprocess.run(
            ["git", "branch", "--show-current"],
            capture_output=True, text=True,
            cwd=cwd, timeout=5,
        )
        if result.returncode == 0:
            branch = result.stdout.strip()
    except Exception:
        pass
    # Display the last two path components for brevity
    display_path = str(cwd)
    if cwd.parent and cwd.parent.name:
        display_path = f"…/{cwd.parent.name}/{cwd.name}"
    return JSONResponse({
        "path": display_path,
        "branch": branch or "main",
    })


@app.get("/api/git/commits")
async def api_git_commits() -> JSONResponse:
    """Return recent commit history from the actual git log.

    Each commit includes hash, message, relative time, and the
    list of files changed. Commits with >10 files are truncated
    with a \"… N more files\" placeholder entry.
    """
    cwd = Path.cwd()
    MAX_FILES = 10
    commits = []

    try:
        # 1) Get commit list (hash|message|relative_time)
        log = subprocess.run(
            ["git", "log", "-n", "10", "--format=%H|%s|%ar"],
            capture_output=True, text=True,
            cwd=cwd, timeout=10,
        )
        if log.returncode != 0:
            return JSONResponse(mock_commits())

        raw_lines = [l.strip() for l in log.stdout.split("\n") if l.strip()]
        for line in raw_lines:
            parts = line.split("|", 2)
            if len(parts) < 3:
                continue
            hsh, msg, rtime = parts[0], parts[1], parts[2]

            # 2) Get per-commit file stats
            stat = subprocess.run(
                ["git", "show", "--numstat", "--format=", hsh],
                capture_output=True, text=True,
                cwd=cwd, timeout=5,
            )
            files = []
            total_add = total_del = 0
            if stat.returncode == 0:
                for sl in stat.stdout.strip().split("\n"):
                    sl = sl.strip()
                    if not sl:
                        continue
                    f_parts = sl.split("\t", 2)
                    if len(f_parts) < 3:
                        continue
                    try:
                        adds = int(f_parts[0]) if f_parts[0] != "-" else 0
                        dels = int(f_parts[1]) if f_parts[1] != "-" else 0
                    except ValueError:
                        continue
                    fpath = f_parts[2]
                    total_add += adds
                    total_del += dels
                    if len(files) < MAX_FILES:
                        ftype = "A" if adds > 0 and dels == 0 else ("D" if dels > 0 and adds == 0 else "M")
                        files.append({
                            "type": ftype,
                            "path": fpath,
                            "add": adds,
                            "del": dels,
                        })

            # 3) Truncation marker if too many files
            total_files = len(files) + (
                1 if any(l.strip() for l in stat.stdout.strip().split("\n") if l.strip()) else 0
            ) - len(files)
            # Simpler: check if the stat output has more lines than MAX_FILES
            stat_lines = [l for l in stat.stdout.strip().split("\n") if l.strip()]
            if len(stat_lines) > MAX_FILES:
                remaining = len(stat_lines) - MAX_FILES
                files.append({"type": "…", "path": f"{remaining} more files", "add": total_add, "del": total_del})

            commits.append({
                "hash": hsh,
                "msg": msg,
                "time": rtime,
                "files": files,
            })
    except Exception:
        return JSONResponse(mock_commits())

    return JSONResponse(commits)


def mock_commits() -> list[dict]:
    """Fallback demo data when git is unavailable."""
    return [
        {
            "hash": "2a1b3c4",
            "msg": "fix: login timeout issue",
            "time": "2m ago",
            "files": [
                {"type": "M", "path": "src/auth/login.py", "add": 3, "del": 1},
                {"type": "A", "path": "tests/test_login.py", "add": 45, "del": 0},
                {"type": "M", "path": "src/utils/lock.py", "add": 1, "del": 1},
            ],
        },
        {
            "hash": "7d8e9f0",
            "msg": "refactor session handler",
            "time": "1h ago",
            "files": [
                {"type": "M", "path": "src/auth/session.py", "add": 8, "del": 4},
                {"type": "D", "path": "src/old_session.py", "add": 0, "del": 26},
            ],
        },
        {
            "hash": "c0d1e2f",
            "msg": "add auth middleware",
            "time": "3h ago",
            "files": [
                {"type": "A", "path": "src/middleware/auth.py", "add": 62, "del": 0},
                {"type": "M", "path": "requirements.txt", "add": 1, "del": 0},
            ],
        },
    ]


# === § 6 Auth placeholder (v0.2) ======================================
# def check_token(request: Request) -> bool: ...
# def require_auth(): return Depends(check_token)


# === § 6 CLI entry ====================================================

if __name__ == "__main__":
    import uvicorn

    parser = argparse.ArgumentParser(description="mangopi-web · task-driven loop_engine UI")
    parser.add_argument("--host", default=HOST,
                        help="bind address (default: 127.0.0.1)")
    parser.add_argument("--port", type=int, default=PORT,
                        help="bind port (default: 8080)")
    parser.add_argument("--mock", action="store_true",
                        help="use mock CLI (default if MANGOPI_WEB_MODE=mock)")
    parser.add_argument("--db", default=str(DEFAULT_DB),
                        help="path to sqlite database")
    args = parser.parse_args()

    if args.host in ("0.0.0.0", "::"):
        raise SystemExit(
            "error: binding to 0.0.0.0/:: is disabled in v0.1 "
            "(localhost-only, no auth). Use a reverse proxy for remote access.")

    MOCK_MODE = True if args.mock else MOCK_MODE
    DEFAULT_DB = Path(args.db)
    HOST = args.host
    PORT = args.port

    uvicorn.run(app, host=HOST, port=PORT, log_level="info")