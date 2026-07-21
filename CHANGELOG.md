# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [0.1.0] - 2026-07-20

### Added

- **Real-time task streaming** — Live pipeline output pushed to the browser via Server-Sent Events (SSE) with HTML fragment rendering
- **Multi-agent pipeline support** — Research, Plan, Develop, Review, Test, and Push phases with configurable modes (`--fast`, `--wish`, `--push`)
- **Concurrent task queue** — Up to `MAX_CONCURRENT` (default 3) tasks run in parallel; excess tasks are queued
- **SQLite persistence** — Tasks, events, and token usage stored and queryable via a local database
- **HTMX-based SPA** — Zero JavaScript framework, full-page reactivity via HTMX and Alpine.js
- **Sidebar task list** — Collapsible sidebar with per-task status pills, progress, and OOB updates
- **Phase-view detail panel** — Streaming event log with stage headers, tool calls, thinking/output blocks, and verdicts
- **Dark mode** — Theme toggle with system preference detection and localStorage persistence
- **Heatmap dashboard** — 12-week daily task activity heatmap on the sidebar
- **Git integration** — `/api/workspace` and `/api/git/commits` endpoints for workspace info and commit history
- **Health endpoint** — `/health` returns server status, DB health, active task count, and mode
- **Mock CLI mode** — `MANGOPI_WEB_MODE=mock` or `--mock` flag to run against a simulated CLI for development
- **Environment-based configuration** — Host, port, DB path, mock mode, and concurrency limit via env vars
- **Pipeline OOB polling** — Out-of-band status updates via periodic polling (hx-trigger="every 2s")
- **Advance endpoint** — `POST /tasks/{id}/advance` returns OOB-only card updates triggered by SSE
- **Task deletion** — `DELETE /tasks/{id}` kills the subprocess, releases the slot, and cleans up DB records
- **Migration support** — Auto-migration for the `phases` column on existing databases

### Changed

- Merged all v0.0.x releases into a single v0.1.0 baseline
- Upgraded from a basic FastAPI demo to a full single-page application
- Replaced static polling with SSE-driven live updates for real-time event delivery
- Refactored CLI output parsing into a dedicated reader thread with JSONL protocol
- Improved responsive layout for mobile and desktop viewports
- Enhanced error handling with per-task subprocess timeout (10 min) and slot release guarantees

### Fixed

- SSE reconnection loop caused by card-level EventSource being torn down on OOB updates — resolved by using OOB-only responses that preserve the SSE connection
- Reader thread crashes no longer leave tasks stuck in "running" state
- Concurrency slot double-release race condition guarded with a lock and deduplication set

### Removed

- Static polling fallback for live events (replaced by SSE)

---

## [0.0.3] - 2026-07-20

### Added

- Event fragment partial (`event_fragment.html`) for unified event rendering
- Pipeline OOB partial (`pipeline_oob.html`) for out-of-band status updates
- Responsive layout improvements

### Changed

- Major UI overhaul — streamlined phase view, improved card design, and consistent spacing
- SSE client rewritten for robustness and reconnection handling
- Task item and detail templates refactored for OOB swapping

### Fixed

- SSE connection torn down on every event due to full-card replacement — now only OOB elements are swapped

## [0.0.2] - 2026-07-03

### Added

- Theme toggle (dark/light mode) with system preference detection
- Sidebar dashboard with task list and status summary
- Thinking and output event types rendered with distinct styling
- Auto-follow — event log automatically scrolls to the latest entry
- Mock CLI enhancements — more realistic tool call simulation

### Changed

- Base template refactored for sidebar + main layout
- Phase view improved with auto-follow toggle and better event grouping

## [0.0.1] - 2026-07-02

### Added

- Initial project structure with FastAPI application
- Basic task creation and listing via HTMX
- Mock CLI for development without real API keys
- Static assets bundled (HTMX, Alpine.js, SSE polyfill)
- Template partials for task items, phase pipeline, and detail view
- SQLite schema for tasks and events
- Basic polling-based live updates
- CLI entry point with `--host`, `--port`, `--mock`, and `--db` arguments
