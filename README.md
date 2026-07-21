# mangopi-web

A real-time web UI for [mangopi-cli](https://github.com/w4n9H/mangopi-cli), an AI-driven task automation pipeline. Built with **FastAPI**, **HTMX**, **Alpine.js**, and **Server-Sent Events (SSE)**.

Manage, monitor, and interact with AI agent pipelines through a modern single-page application — no page reloads, no heavy JavaScript frameworks.

---

## Features

- **Real-time streaming** — Live task output pushed via SSE and rendered as HTML fragments
- **Multi-agent pipeline** — Supports Research, Plan, Develop, Review, Test, and Push phases
- **Concurrent task management** — Queue and run up to `MAX_CONCURRENT` tasks in parallel
- **SQLite persistence** — Task state, events, and usage data stored locally
- **OOB (Out-of-Band) updates** — HTMX-powered sidebar and status pills update automatically
- **Dark mode** — Built-in theme toggle with system preference detection
- **Heatmap dashboard** — Daily task activity for the last 12 weeks
- **Git integration** — Workspace info and commit history at a glance
- **Mock mode** — Run the UI against a simulated CLI for development and testing
- **Lightweight** — Single Python file, zero-node frontend (HTMX + Alpine.js via CDN-free static files)

---

## Screenshot

```
┌──────────────────────────────────────────────────────────────┐
│  Sidebar           │  Main Area (Phase View)                │
│  ┌────────────┐   │  ┌──────────────────────────────────┐  │
│  │ Task list   │   │  │ Pipeline progress                │  │
│  │ ● Task 1    │   │  │ Plan  ● Dev  ● Review  ● Test   │  │
│  │ ● Task 2    │   │  │                                  │  │
│  │ + New Task  │   │  │ [Event log - streaming output]   │  │
│  └────────────┘   │  └──────────────────────────────────┘  │
└──────────────────────────────────────────────────────────────┘
```

---

## Installation

```bash
pip install mangopi-web
```

### From source

```bash
git clone https://github.com/w4n9H/mangopi-web.git
cd mangopi-web
pip install -e .
```

---

## Quick Start

### 1. Start the web server

```bash
mangopi-web
```

Opens at [http://127.0.0.1:8080](http://127.0.0.1:8080).

### 2. Create a task

Click **New Task**, enter a goal (e.g. "fix login bug"), choose an optional mode, and submit. The pipeline runs immediately and output streams into the phase view.

### 3. Modes

| Mode | Flag | Pipeline |
|------|------|----------|
| Normal | *(none)* | plan → develop → review → test |
| Fast | `--fast` | develop → test |
| Wish | `--wish` | research → plan → develop → review → test |
| Fast + Wish | both | research → develop → test |
| Push | `--push` | (append `push` to any of the above) |

---

## Configuration

All settings are controlled via environment variables:

| Variable | Default | Description |
|----------|---------|-------------|
| `MANGOPI_WEB_HOST` | `127.0.0.1` | Bind address |
| `MANGOPI_WEB_PORT` | `8080` | Bind port |
| `MANGOPI_WEB_DB` | `./.mangocli/web.db` | SQLite database path |
| `MANGOPI_WEB_MODE` | *(unset)* | Set to `mock` for mock CLI mode |
| `MANGOPI_WEB_MAX_CONCURRENT` | `3` | Max parallel tasks |

**Security note:** Binding to `0.0.0.0` is blocked unless you use a reverse proxy. There is no authentication in v0.1.

---

## Development

### Project structure

```
mangopi-web/
├── mangopi_web.py              # Main application (single module)
├── mock/
│   └── fake_cli.py             # Mock CLI for testing without real API keys
├── static/
│   ├── alpine.min.js           # Alpine.js
│   ├── htmx.min.js             # HTMX
│   └── sse.js                  # SSE polyfill / client
├── templates/
│   ├── base.html               # Base layout with sidebar
│   ├── index.html              # SPA shell
│   └── partials/
│       ├── event_fragment.html  # Single event renderer
│       ├── phase_pipeline.html  # Pipeline step indicators
│       ├── pipeline_oob.html    # OOB status updates
│       ├── task_detail.html     # Full task detail page
│       ├── task_item.html       # Sidebar task card
│       ├── task_item_oob.html   # OOB card updates
│       ├── task_list.html       # Task list fragment
│       └── task_phase_view.html # Phase view + event log
├── pyproject.toml
└── README.md
```

### Running in mock mode

```bash
MANGOPI_WEB_MODE=mock python mangopi_web.py
# or
python mangopi_web.py --mock
```

This uses `mock/fake_cli.py` instead of the real `mangopi-cli`, so no API keys are needed.

### Live reload during development

```bash
pip install uvicorn[standard]
uvicorn mangopi_web:app --reload --port 8080
```

---

## API Endpoints

| Method | Path | Description |
|--------|------|-------------|
| `GET` | `/` | SPA index page |
| `GET` | `/tasks` | Task list fragment (HTMX) |
| `POST` | `/tasks` | Create and start a new task |
| `DELETE` | `/tasks/{id}` | Delete a task and kill its process |
| `GET` | `/tasks/{id}` | Task detail / phase view |
| `POST` | `/tasks/{id}/advance` | OOB card updates (triggered by SSE) |
| `GET` | `/tasks/{id}/pipeline-oob` | Pipeline OOB status poll |
| `GET` | `/events/{id}` | SSE stream for live task events |
| `GET` | `/health` | Health check JSON |
| `GET` | `/api/heatmap` | Daily task count for last 12 weeks |
| `GET` | `/api/workspace` | Workspace path & git branch |
| `GET` | `/api/git/commits` | Recent git commit history |

---

## License

Apache License 2.0 — see [LICENSE](LICENSE).
