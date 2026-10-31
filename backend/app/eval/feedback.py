"""Test feedback the agent may use while solving a benchmark task.

Only what a developer would have: the repository's existing tests near the code being
changed, and tests the agent writes itself. The benchmark's hidden tests are never used.
Existing tests run once on the untouched checkout first, so a test that already failed
there is reported but does not count against the change.
"""
import os
import re
from typing import Callable, Iterable

from app.eval import grading
from app.eval.sandbox import EnvironmentFailure

# Each run installs the working copy into a fresh image, so keep the selection small.
MAX_RELATED_TEST_FILES = 5
_RUNNABLE_TEST_RE = re.compile(r"(^|/)(test_[^/]+|[^/]+_test)\.py$")
_SOURCE_ROOTS = ("src/", "lib/")


def is_runnable_test(path: str) -> bool:
    return bool(_RUNNABLE_TEST_RE.search(path))


def _module_name(path: str) -> str:
    """Dotted import name of a source file, e.g. src/flask/json/tag.py -> flask.json.tag."""
    for root in _SOURCE_ROOTS:
        if path.startswith(root):
            path = path[len(root):]
    name = path[:-3].replace("/", ".")
    return name[:-len(".__init__")] if name.endswith(".__init__") else name


def _imports(text: str, module: str) -> bool:
    parent, _, leaf = module.rpartition(".")
    patterns = [rf"^\s*(from|import)\s+{re.escape(module)}\b"]
    if parent:
        patterns.append(rf"^\s*from\s+{re.escape(parent)}\s+import\s+[^\n]*\b{re.escape(leaf)}\b")
    return any(re.search(p, text, re.M) for p in patterns)


def related_test_files(source_files: Iterable[str], tracked_files: Iterable[str], workspace: str,
                       limit: int = MAX_RELATED_TEST_FILES) -> list[str]:
    """Existing test files for `source_files`: first those named after one (test_<name>.py),
    then those that import one of them."""
    sources = [p for p in source_files if p.endswith(".py") and not is_runnable_test(p)]
    tests = sorted(p for p in tracked_files if is_runnable_test(p))
    if not sources or not tests:
        return []
    stems = {os.path.basename(p)[:-3] for p in sources} - {"__init__"}
    named = [t for t in tests if re.sub(r"^test_|_test$", "", os.path.basename(t)[:-3]) in stems]
    modules = [_module_name(p) for p in sources]
    importing = []
    for t in tests:
        if t in named:
            continue
        try:
            with open(os.path.join(workspace, t), encoding="utf-8", errors="replace") as f:
                text = f.read(200_000)
        except OSError:
            continue
        if any(_imports(text, m) for m in modules):
            importing.append(t)
    return (named + importing)[:limit]


def _failing(statuses: dict[str, str]) -> set[str]:
    return {t for t, s in statuses.items() if s in (grading.FAILED, grading.ERROR)}


class FeedbackRunner:
    """TestRunner-shaped wrapper (the interface the repair loop expects) around the task's Docker environment.

    preexisting: test IDs that already failed on the untouched checkout. When they are the only
    failures the run counts as passed, and the log says which they are.
    """

    def __init__(self, run_tests: Callable[[str, list[str]], "TestRun"], workspace: str, test_files: list[str],
                 preexisting: Iterable[str] = ()):
        self._run, self.workspace, self.test_files = run_tests, workspace, test_files
        self.preexisting = set(preexisting)
        self.statuses: dict[str, str] = {}  # test ID -> status of the last run

    @property
    def failing(self) -> set[str]:
        return _failing(self.statuses)

    def run_tests(self, *_args, **_kwargs) -> dict:
        from app.sandbox.test_runner import (ENVIRONMENT_ERROR, PASSED, TESTS_FAILED, TIMEOUT, _summary_counts,
                                             classify_pytest_result)
        from app.services.repository_scanner import list_tracked_files
        command = ["pytest", "-rA", *self.test_files]
        try:
            run = self._run(self.workspace, self.test_files)
        except EnvironmentFailure as e:
            self.statuses = {}
            return {"success": False, "outcome": ENVIRONMENT_ERROR, "exit_code": 127, "stdout": "",
                    "stderr": str(e)[-3000:], "summary": {}, "command": command}
        self.statuses = grading.parse_pytest_log(run.log)
        outcome = TIMEOUT if run.timed_out else classify_pytest_result(
            run.exit_code, run.log, list_tracked_files(self.workspace))
        failing = self.failing
        known = sorted(failing & self.preexisting)
        if outcome == TESTS_FAILED and failing and not failing - self.preexisting:
            outcome = PASSED
        log = run.log[-100_000:]
        if known:
            log = (f"NOTE: {len(known)} test(s) already failed before the change; they are not part of this "
                   f"issue and need no fix: {', '.join(known[:20])}\n\n{log}")
        return {"success": outcome == PASSED, "outcome": outcome, "exit_code": run.exit_code,
                "stdout": log, "stderr": "", "summary": _summary_counts(run.log),
                "preexisting_failures": known, "command": command, "duration_seconds": run.duration_seconds}
