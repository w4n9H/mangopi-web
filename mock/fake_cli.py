#!/usr/bin/env python3
"""Mock CLI — matches real mangopi_cli.py JSONL output format.

Pipeline modes (controlled by --push / --fast / --wish):
  Default        : plan → develop → review → test → succeed
  --push         : plan → develop → review → test → push
  --fast         : develop → test → succeed
  --wish          : research → (default pipeline)
  --fast --wish   : research → develop → test → succeed

Scenarios (controlled by --goal):
  "fix login bug"    — normal PASS
  "fix flaky test"   — Verifier FAIL → Updater → retry → PASS
"""
from __future__ import annotations

import argparse
import json
import sys
import time


def emit(d: dict) -> None:
    print(json.dumps(d, ensure_ascii=False), flush=True)


# ── ResearchAgent ──────────────────────────────────────────────
def research_phase(goal: str, round_n: int, max_rounds: int):
    """Simulate ResearchAgent: web search + synthesis."""
    emit({"type": "iter", "n": round_n, "max_iter": max_rounds,
          "agent": "researcher", "phase": "research"})

    emit({"type": "tool", "name": "web_search",
          "args_preview": f"{goal} best practices API reference",
          "round": round_n})
    time.sleep(0.6)
    emit({"type": "tool_result", "name": "web_search", "ok": True,
          "snippet": "Found 3 relevant docs: official API, StackOverflow, blog post",
          "round": round_n})
    time.sleep(0.4)

    emit({"type": "tool", "name": "web_search",
          "args_preview": f"{goal} example implementation github",
          "round": round_n})
    time.sleep(0.6)
    emit({"type": "tool_result", "name": "web_search", "ok": True,
          "snippet": "2 example repos: similar-auth-flow, rest-login-pattern",
          "round": round_n})
    time.sleep(0.4)

    emit({"type": "thinking",
          "content": "Synthesising research: the standard pattern is token-refresh + retry…"})
    time.sleep(0.5)
    emit({"type": "output",
          "content": "Research summary: use OAuth2 token refresh with exponential backoff. "
                      "Recommended: refresh_token before 401, retry 3x with jitter."})
    time.sleep(0.3)


# ── DesignAgent / DevAgent ─────────────────────────────────────
def implementer_phase(phase: str, goal: str, round_n: int, max_rounds: int):
    """Simulate DesignAgent (plan) or DevAgent (develop)."""
    emit({"type": "iter", "n": round_n, "max_iter": max_rounds,
          "agent": "implementer", "phase": phase})

    if phase == "plan":
        emit({"type": "tool", "name": "read",
              "args_preview": f"src/{goal.replace(' ', '_')}.py",
              "round": round_n})
        time.sleep(0.5)
        emit({"type": "thinking",
              "content": f"Analysing {goal}: reviewing imports and dependencies…"})
        time.sleep(0.3)
        emit({"type": "output",
              "content": "login.py imports: session, crypto, db\n"
                          "→ session.py handles token refresh\n"
                          "→ crypto.py has AES-256 helper"})
        time.sleep(0.3)
        emit({"type": "tool_result", "name": "read", "ok": True,
              "snippet": f"# {goal} — existing code with known issue",
              "round": round_n})
        time.sleep(0.5)

        emit({"type": "tool", "name": "read",
              "args_preview": "src/utils.py",
              "round": round_n})
        time.sleep(0.5)
        emit({"type": "tool_result", "name": "read", "ok": True,
              "snippet": "# utility functions",
              "round": round_n})
        time.sleep(0.5)

    elif phase == "develop":
        emit({"type": "tool", "name": "edit",
              "args_preview": f"src/{goal.replace(' ', '_')}.py (line 24)",
              "round": round_n})
        time.sleep(0.8)
        emit({"type": "tool_result", "name": "edit", "ok": True,
              "snippet": "+   # applied fix for: " + goal,
              "round": round_n})
        time.sleep(0.5)

        emit({"type": "tool", "name": "bash",
              "args_preview": "ruff check src/",
              "round": round_n})
        time.sleep(0.5)
        emit({"type": "tool_result", "name": "bash", "ok": True,
              "snippet": "# all checks passed",
              "round": round_n})
        time.sleep(0.5)

    emit({"type": "usage",
          "prompt_tokens": 1800 + round_n * 80,
          "completion_tokens": 300 + round_n * 25,
          "total": 2100 + round_n * 105})


# ── ReviewAgent / TestAgent ────────────────────────────────────
def verifier_phase(phase: str, round_n: int, max_rounds: int,
                   fail_on_test: bool = False) -> bool:
    """Simulate ReviewAgent (review) or TestAgent (test). Returns True if PASS."""
    emit({"type": "iter", "n": round_n, "max_iter": max_rounds,
          "agent": "verifier", "phase": phase})

    if phase == "review":
        emit({"type": "tool", "name": "read",
              "args_preview": "src/auth/login.py (review)",
              "round": round_n})
        time.sleep(0.8)
        emit({"type": "tool_result", "name": "read", "ok": True,
              "snippet": "# code review — looks correct",
              "round": round_n})
        time.sleep(0.5)

        emit({"type": "tool", "name": "read",
              "args_preview": "tests/test_auth.py",
              "round": round_n})
        time.sleep(0.5)
        emit({"type": "tool_result", "name": "read", "ok": True,
              "snippet": "# test coverage is adequate",
              "round": round_n})
        time.sleep(0.5)

        return True

    elif phase == "test":
        emit({"type": "tool", "name": "bash",
              "args_preview": "pytest tests/test_auth.py -v",
              "round": round_n})
        time.sleep(1.0)

        if fail_on_test:
            emit({"type": "tool_result", "name": "bash", "ok": False,
                  "snippet": "FAILED tests/test_auth.py::test_login_timeout — AssertionError",
                  "round": round_n})
            time.sleep(0.5)
            emit({"type": "verdict",
                  "verdict": "VERIFY: FAIL: test_login_timeout exceeded 5s timeout",
                  "reason": "",
                  "round": round_n})
            return False
        else:
            emit({"type": "tool_result", "name": "bash", "ok": True,
                  "snippet": "PASSED tests/test_auth.py::test_login\n"
                              "PASSED tests/test_auth.py::test_session",
                  "round": round_n})
            time.sleep(0.5)
            emit({"type": "verdict",
                  "verdict": "VERIFY: PASS",
                  "reason": "",
                  "round": round_n})
            return True


# ── UpdaterAgent ───────────────────────────────────────────────
def updater_phase(round_n: int, max_rounds: int):
    """Simulate UpdaterAgent analysing failure and refining prompt.
    Note: real CLI uses phase="push", agent="updater"."""
    emit({"type": "iter", "n": round_n, "max_iter": max_rounds,
          "agent": "updater", "phase": "push"})
    time.sleep(0.5)

    emit({"type": "tool", "name": "read",
          "args_preview": "pytest output (failure analysis)",
          "round": round_n})
    time.sleep(0.8)
    emit({"type": "tool_result", "name": "read", "ok": True,
          "snippet": "# test_login_timeout — need to increase timeout or optimise query",
          "round": round_n})
    time.sleep(0.5)

    emit({"type": "tool", "name": "edit",
          "args_preview": "prompt.md (correction directive)",
          "round": round_n})
    time.sleep(0.5)
    emit({"type": "tool_result", "name": "edit", "ok": True,
          "snippet": "+ instruction: add retry logic for login timeout",
          "round": round_n})
    time.sleep(0.5)


# ── PushAgent ──────────────────────────────────────────────────
def push_phase(goal: str, round_n: int):
    """Simulate PushAgent. Real CLI: agent="implementer", phase="push"."""
    emit({"type": "iter", "n": round_n, "max_iter": 5,
          "agent": "implementer", "phase": "push"})
    time.sleep(0.5)

    emit({"type": "tool", "name": "bash",
          "args_preview": "git add -A && git commit -m \"" + goal + "\"",
          "round": round_n})
    time.sleep(0.8)
    emit({"type": "tool_result", "name": "bash", "ok": True,
          "snippet": "[main 2a1b3c4] " + goal,
          "round": round_n})
    time.sleep(0.3)

    emit({"type": "tool", "name": "bash",
          "args_preview": "git push origin main",
          "round": round_n})
    time.sleep(0.5)
    emit({"type": "tool_result", "name": "bash", "ok": True,
          "snippet": "remote: ✓ main -> main",
          "round": round_n})


def main() -> int:
    ap = argparse.ArgumentParser(prog="mangopi-cli")
    ap.add_argument("--goal", required=True, help="Goal for loop iteration")
    ap.add_argument("--task-id", required=True, help="Task identifier")
    ap.add_argument("--output", default="jsonl",
                    choices=["console", "jsonl"],
                    help="Output mode (default: jsonl)")
    ap.add_argument("--push", action="store_true",
                    help="Commit verified changes on PASS")
    ap.add_argument("--fast", action="store_true",
                    help="Skip design/review, only dev → test → push")
    ap.add_argument("--wish", action="store_true",
                    help="Prepend research before normal pipeline")
    ap.add_argument("--max-iter", type=int, default=5,
                    help="Max iterations (default: 5)")
    args = ap.parse_args()

    goal = args.goal.strip().lower()
    scenario_fail = "flaky" in goal and "test" in goal
    max_rounds = args.max_iter

    emit({"type": "start", "task_id": args.task_id,
          "goal": args.goal, "started_at": time.time()})

    # ── Wish mode: prepend ResearchAgent ────────────────────
    if args.wish:
        research_phase(goal, 1, max_rounds)

    # ── Build phase list based on flags ─────────────────────
    if args.fast:
        phases = ["develop", "test"]
    else:
        phases = ["plan", "develop", "review", "test"]

    code = 0

    for round_n in range(1, max_rounds + 1):
        # ── Implementer / Verifier ──────────────────────────
        if not args.fast:
            implementer_phase("plan", goal, round_n, max_rounds)
        implementer_phase("develop", goal, round_n, max_rounds)

        if not args.fast:
            verifier_phase("review", round_n, max_rounds)
        passed = verifier_phase("test", round_n, max_rounds,
                                fail_on_test=scenario_fail)

        if not passed:
            # UpdaterAgent → retry next round
            updater_phase(round_n, max_rounds)
            code = 0
            scenario_fail = False  # round 2 retry will PASS
            continue

        # ── PushAgent or SucceedStep ────────────────────────
        if args.push:
            push_phase(goal, round_n)
        # SucceedStep: no extra events, just emit complete

        emit({"type": "complete",
              "result": f"All rounds completed: {args.goal[:60]}",
              "iters": round_n})
        break
    else:
        # Max rounds exhausted
        code = 1
        emit({"type": "complete",
              "result": None,
              "iters": max_rounds})

    sys.exit(code)


if __name__ == "__main__":
    main()
