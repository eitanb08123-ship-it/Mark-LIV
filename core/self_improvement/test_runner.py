"""
core/self_improvement/test_runner.py — runs pytest inside an isolated
workspace and returns a structured result the evaluator can reason about.

Exit code 5 (pytest's "no tests collected") is deliberately NOT treated as a
pass: an improvement whose test never ran has not been verified, even though
nothing technically failed.
"""
from __future__ import annotations

import subprocess
from dataclasses import dataclass
from pathlib import Path

_NO_TESTS_COLLECTED = 5


@dataclass
class TestResult:
    passed: bool
    exit_code: int
    stdout: str
    stderr: str
    ran: bool  # False if pytest itself could not be invoked at all


def run_tests(workspace_path: Path, target: str | None = None, timeout: int = 120) -> TestResult:
    """Runs pytest inside `workspace_path`. `target` narrows it to the test
    file written for this specific improvement; omitted, it runs the whole
    suite (used for a final "did this break anything else" pass)."""
    args = ["python3", "-m", "pytest", "-q"]
    if target:
        args.append(target)

    try:
        result = subprocess.run(args, cwd=workspace_path, capture_output=True, text=True, timeout=timeout)
    except FileNotFoundError:
        return TestResult(passed=False, exit_code=-1, stdout="", stderr="pytest is not installed", ran=False)
    except subprocess.TimeoutExpired as e:
        return TestResult(
            passed=False, exit_code=-1,
            stdout=(e.stdout or "")[-4000:] if isinstance(e.stdout, str) else "",
            stderr=f"Tests timed out after {timeout}s", ran=True,
        )

    no_tests = result.returncode == _NO_TESTS_COLLECTED
    stderr = result.stderr[-4000:]
    if no_tests:
        stderr += "\n[pytest collected zero tests - nothing was actually verified]"

    return TestResult(
        passed=result.returncode == 0,
        exit_code=result.returncode,
        stdout=result.stdout[-8000:],
        stderr=stderr,
        ran=True,
    )
