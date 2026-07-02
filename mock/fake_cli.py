#!/usr/bin/env python3
"""Mock CLI for v0.1: realistic 3-stage agent loop.

Scenarios (controlled by --goal):
  "fix login bug"    — normal PASS (Implementer → Verifier → Push → done)
  "fix flaky test"   — Verifier FAIL → Updater → retry Implementer → PASS → done
"""
from __future__ import annotations

import argparse
import json
import sys
import time


def emit(d: dict) -> None:
    print(json.dumps(d, ensure_ascii=False), flush=True)


def implementer_phase(phase: str, goal: str, round_n: int, max_rounds: int):
    """Simulate Implementer reading + editing files."""
    emit({"type": "iter", "n": round_n, "max_iter": max_rounds,
          "agent": "implementer", "phase": phase})

    if phase == "plan":
        # Read the target file
        emit({"type": "tool", "name": "read",
              "args_preview": f"src/{goal.replace(' ', '_')}.py",
              "round": round_n})
        time.sleep(0.8)
        emit({"type": "tool_result", "name": "read", "ok": True,
              "snippet": f"# {goal} — existing code with known issue",
              "round": round_n})
        time.sleep(0.5)

        # Read related modules
        emit({"type": "tool", "name": "read",
              "args_preview": "src/utils.py",
              "round": round_n})
        time.sleep(0.5)
        emit({"type": "tool_result", "name": "read", "ok": True,
              "snippet": "# utility functions",
              "round": round_n})
        time.sleep(0.5)

    elif phase == "develop":
        # Edit the file
        emit({"type": "tool", "name": "edit",
              "args_preview": f"src/{goal.replace(' ', '_')}.py (line 24)",
              "round": round_n})
        time.sleep(0.8)
        emit({"type": "tool_result", "name": "edit", "ok": True,
              "snippet": "+   # applied fix for: " + goal,
              "round": round_n})
        time.sleep(0.5)

        # Run lint
        emit({"type": "tool", "name": "bash",
              "args_preview": "ruff check src/",
              "round": round_n})
        time.sleep(0.5)
        emit({"type": "tool_result", "name": "bash", "ok": True,
              "snippet": "# all checks passed",
              "round": round_n})
        time.sleep(0.5)

    emit({"type": "usage", "prompt_tokens": 1800 + round_n * 80,
          "completion_tokens": 300 + round_n * 25,
          "total": 2100 + round_n * 105})


def verifier_phase(phase: str, round_n: int, max_rounds: int,
                   fail_on_test: bool = False) -> bool:
    """Simulate Verifier reviewing + testing. Returns True if PASS."""
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
            emit({"type": "verdict", "verdict": "FAIL: test_login_timeout",
                  "reason": "test exceeded 5s timeout",
                  "round": round_n})
            return False
        else:
            emit({"type": "tool_result", "name": "bash", "ok": True,
                  "snippet": "PASSED tests/test_auth.py::test_login \\nPASSED tests/test_auth.py::test_session",
                  "round": round_n})
            time.sleep(0.5)
            emit({"type": "verdict", "verdict": "PASS, NO_ISSUES",
                  "reason": "",
                  "round": round_n})
            return True

    return True


def updater_phase(round_n: int, max_rounds: int):
    """Simulate Updater analysing failure + updating prompt."""
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

    emit({"type": "phase", "from": "verifier", "to": "updater",
          "round": round_n})
    time.sleep(0.3)


def push_phase(goal: str, round_n: int):
    """Simulate finalisation / merge step."""
    emit({"type": "phase", "from": "test", "to": "push",
          "round": round_n})
    time.sleep(0.3)

    emit({"type": "iter", "n": round_n, "max_iter": 5,
          "agent": "implementer", "phase": "push"})
    time.sleep(0.5)

    emit({"type": "tool", "name": "bash",
          "args_preview": "git add -A && git commit -m " + f'"{goal}"',
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
    ap = argparse.ArgumentParser()
    ap.add_argument("--goal", required=True)
    ap.add_argument("--task-id", required=True)
    ap.add_argument("--output", default="jsonl")
    args = ap.parse_args()

    goal = args.goal.strip().lower()
    scenario_fail = "flaky" in goal and "test" in goal
    max_rounds = 5
    total_p = total_c = 0
    code = 0

    emit({"type": "start", "task_id": args.task_id,
          "goal": args.goal, "started_at": time.time()})

    for round_n in range(1, max_rounds + 1):
        # ── Implementer (plan + develop) ─────────────
        implementer_phase("plan", goal, round_n, max_rounds)
        implementer_phase("develop", goal, round_n, max_rounds)

        # ── Verifier (review + test) ─────────────────
        verifier_phase("review", round_n, max_rounds)
        passed = verifier_phase("test", round_n, max_rounds,
                                fail_on_test=scenario_fail)

        if not passed:
            # ── Updater → retry in next round ────────
            updater_phase(round_n, max_rounds)
            code = 0  # was a test failure, not a crash
            # Next round will re-run implementer + verifier
            # Flip the flag so round 2 (retry) won't fail again
            scenario_fail = False
            continue

        # ── Push (only on PASS) ─────────────────────
        push_phase(goal, round_n)

        # ── Complete ────────────────────────────────
        emit({"type": "complete", "result": f"All rounds completed: {args.goal[:60]}",
              "iters": round_n,
              "total_usage": {"prompt": total_p, "completion": total_c,
                              "total": total_p + total_c}})
        break
    else:
        code = 1  # max rounds exhausted without PASS

    sys.exit(code)


if __name__ == "__main__":
    main()
