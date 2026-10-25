"""SWE-bench harness: log parsing, grading, outcome classification and the
checkout -> patch -> test-patch -> run flow (with a real git repo and a fake Docker layer)."""
import json
import os
import re
import subprocess

import pytest

from app.eval import grading, harness
from app.eval.environments import UnsupportedTask, spec_for
from app.eval.harness import Harness, classify_agent_exception
from app.eval.repos import RepoCache, git
from app.eval.sandbox import EnvironmentFailure, TestRun
from app.eval.store import ResultsStore, summarize
from app.services.llm_service import LLMOutputError, LLMUnavailableError
from app.services.patch_service import PatchError

# ---- log parsing and grading ---------------------------------------------------

PYTEST_LOG = """\
============================= test session starts ==============================
collected 6 items

tests/test_config.py ..F.sx                                              [100%]
=========================== short test summary info ============================
PASSED tests/test_config.py::test_a
PASSED tests/test_config.py::test_param[a b]
FAILED tests/test_config.py::test_b - AssertionError: assert 1 == 2
ERROR tests/test_config.py::test_c - fixture 'x' not found
SKIPPED [1] tests/test_config.py:40: dotenv is not installed
XFAIL tests/test_config.py::test_d - reason: known bug
"""


def test_parse_pytest_log_matches_swebench_rules():
    status = grading.parse_pytest_log(PYTEST_LOG)
    assert status["tests/test_config.py::test_a"] == "PASSED"
    assert status["tests/test_config.py::test_b"] == "FAILED"
    assert status["tests/test_config.py::test_c"] == "ERROR"
    assert status["tests/test_config.py::test_d"] == "XFAIL"
    # SWE-bench splits on whitespace, so parametrized IDs with spaces are cut short
    # (the dataset's test lists contain exactly these truncated IDs).
    assert status["tests/test_config.py::test_param[a"] == "PASSED"
    # Skips are reported by location, not test ID.
    assert status["[1]"] == "SKIPPED"


def test_grade_resolution_rules():
    status = {"t::pass": "PASSED", "t::xfail": "XFAIL", "t::fail": "FAILED", "t::err": "ERROR", "t::skip": "SKIPPED"}
    assert grading.grade(status, ["t::pass", "t::xfail"], ["t::pass", "t::skip"]).resolved
    # A FAIL_TO_PASS test that never ran (missing from the log) counts as failed.
    report = grading.grade(status, ["t::pass", "t::missing"], [])
    assert not report.resolved and report.f2p_failed == ["t::missing"]
    report = grading.grade(status, ["t::pass"], ["t::err"])
    assert not report.resolved and report.p2p_failed == ["t::err"]
    assert report.to_dict()["p2p_total"] == 1


def test_test_directives_skip_data_files():
    patch = ("diff --git a/tests/test_config.py b/tests/test_config.py\n--- a/x\n+++ b/x\n"
             "diff --git a/tests/static/config.toml b/tests/static/config.toml\n--- a/y\n+++ b/y\n")
    assert grading.test_files_from_patch(patch) == ["tests/test_config.py", "tests/static/config.toml"]
    assert grading.test_directives(patch) == ["tests/test_config.py"]


def test_benchmark_dataset_is_well_formed():
    tasks = harness.load_tasks()
    assert len({t["instance_id"] for t in tasks}) == len(tasks)
    for t in tasks:
        assert re.fullmatch(r"[0-9a-f]{40}", t["base_commit"]), t["instance_id"]
        assert t["FAIL_TO_PASS"], t["instance_id"]
        assert t["patch"].startswith("diff --git") and grading.test_directives(t["test_patch"]), t["instance_id"]
        spec_for(t["repo"], t["version"])  # every task has an environment


def test_unknown_environment_is_rejected():
    with pytest.raises(UnsupportedTask):
        spec_for("django/django", "3.0")


# ---- exception classification ----------------------------------------------------

class _StatusError(Exception):
    def __init__(self, msg, status_code):
        super().__init__(msg)
        self.status_code = status_code


def _wrapped(inner: Exception) -> Exception:
    try:
        try:
            raise inner
        except Exception as e:
            raise RuntimeError("[gemini] maximum retry bounds exceeded") from e
    except RuntimeError as outer:
        return outer


@pytest.mark.parametrize("exc, outcome, fatal", [
    (_StatusError("You exceeded your current quota", 429), harness.LLM_ERROR, True),
    (ValueError("Configuration Error: GEMINI_API_KEY not configured."), harness.LLM_ERROR, True),
    (_wrapped(LLMUnavailableError("Cannot connect to Ollama")), harness.LLM_ERROR, False),
    (_wrapped(LLMOutputError("not valid JSON")), harness.NO_PATCH, False),
    (PatchError("search text not found"), harness.NO_PATCH, False),
    (ValueError("Solution Plan validation failed after 3 attempts"), harness.NO_PATCH, False),
    (_StatusError("Request too large for model on tokens per minute (TPM): Limit 8000", 413), harness.LLM_ERROR, True),
    (KeyError("boom"), harness.HARNESS_ERROR, False),
])
def test_classify_agent_exception(exc, outcome, fatal):
    err = classify_agent_exception(exc)
    assert (err.outcome, err.fatal) == (outcome, fatal)


# ---- end-to-end flow with a local repository and a fake Docker layer ---------------

def _commit_all(path, message):
    git(path, "add", "-A")
    git(path, "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-q", "-m", message)
    return git(path, "rev-parse", "HEAD").strip()


def _diff(path, base):
    git(path, "add", "-A")
    out = git(path, "diff", "--cached", base)
    git(path, "reset", "-q", "--hard", base)
    return out


@pytest.fixture
def benchmark(tmp_path):
    """A tiny repo in the harness cache with a bug, its fix and a hidden test."""
    cache = tmp_path / "cache"
    repo = cache / "repos" / "pallets__flask"
    repo.mkdir(parents=True)
    git(str(repo), "init", "-q")
    (repo / "calc.py").write_text("def add(a, b):\n    return a - b\n", newline="\n")
    (repo / "test_calc.py").write_text("from calc import add\n\ndef test_old():\n    assert True\n", newline="\n")
    base = _commit_all(str(repo), "base")
    (repo / "calc.py").write_text("def add(a, b):\n    return a + b\n", newline="\n")
    gold = _diff(str(repo), base)
    (repo / "test_calc.py").write_text(
        "from calc import add\n\ndef test_old():\n    assert True\n\ndef test_add():\n    assert add(2, 2) == 4\n",
        newline="\n")
    test_patch = _diff(str(repo), base)
    task = {
        "instance_id": "pallets__flask-1", "repo": "pallets/flask", "version": "2.3", "base_commit": base,
        "problem_statement": "add() subtracts", "patch": gold, "test_patch": test_patch,
        "FAIL_TO_PASS": ["test_calc.py::test_add"], "PASS_TO_PASS": ["test_calc.py::test_old"],
    }
    return cache, task


class FakeSandbox:
    """Grades by looking at the files the harness prepared, like real pytest would."""

    def __init__(self):
        self.calls = []

    def ensure_env_image(self, repo, version, spec):
        return "env:test"

    def run_tests(self, workspace, env_tag, spec, test_files, network, timeout):
        self.calls.append({"test_files": test_files, "network": network})
        src = open(os.path.join(workspace, "calc.py")).read()
        tests = open(os.path.join(workspace, "test_calc.py")).read()
        lines = ["PASSED test_calc.py::test_old"]
        if "def test_add" in tests:
            lines.append(("PASSED" if "a + b" in src else "FAILED") + " test_calc.py::test_add")
        return TestRun(log="\n".join(lines), exit_code=0, timed_out=False, duration_seconds=0.1)


def _harness(cache, agent=None, sandbox=None, **kw):
    return Harness(cache_dir=str(cache), sandbox=sandbox or FakeSandbox(),
                   agent=agent or (lambda ws, task, trace, run_tests=None: None), **kw)


def test_gold_patch_resolves(benchmark):
    cache, task = benchmark
    sandbox = FakeSandbox()
    result = _harness(cache, sandbox=sandbox).evaluate(task, mode=harness.GOLD)
    assert result.outcome == harness.RESOLVED, result.detail
    assert result.tests["f2p_passed"] == 1
    assert sandbox.calls == [{"test_files": ["test_calc.py"], "network": False}]


def _fixing_agent(ws, task, trace, run_tests=None):
    trace["analysis"], trace["plan"] = {"problem_summary": "x"}, {"summary": "y"}
    with open(os.path.join(ws, "calc.py"), "w", newline="\n") as f:
        f.write("def add(a, b):\n    return a + b\n")


def test_agent_fix_is_graded_against_hidden_tests(benchmark):
    cache, task = benchmark
    result = _harness(cache, agent=_fixing_agent).evaluate(task)
    assert result.outcome == harness.RESOLVED, result.detail
    assert "a + b" in result.patch and result.analysis == {"problem_summary": "x"}


def test_agent_sees_only_the_problem_statement(benchmark):
    cache, task = benchmark
    seen = {}

    def spy(ws, visible, trace, run_tests=None):
        seen.update(visible)
        _fixing_agent(ws, visible, trace)

    _harness(cache, agent=spy).evaluate(task)
    assert set(seen) == {"instance_id", "repo", "problem_statement"}


def test_wrong_fix_is_unresolved(benchmark):
    cache, task = benchmark

    def wrong(ws, t, trace, run_tests=None):
        with open(os.path.join(ws, "calc.py"), "w", newline="\n") as f:
            f.write("def add(a, b):\n    return a * b\n")

    result = _harness(cache, agent=wrong).evaluate(task)
    assert result.outcome == harness.UNRESOLVED
    assert result.tests["f2p_failed"] == ["test_calc.py::test_add"]


def test_agent_edits_to_test_files_are_replaced_by_hidden_tests(benchmark):
    cache, task = benchmark

    def cheat(ws, t, trace, run_tests=None):
        _fixing_agent(ws, t, trace)
        with open(os.path.join(ws, "test_calc.py"), "w", newline="\n") as f:
            f.write("def test_old():\n    assert True\n")  # deletes nothing useful but conflicts with test_patch

    result = _harness(cache, agent=cheat).evaluate(task)
    assert result.outcome == harness.RESOLVED, result.detail


def test_empty_change_is_no_patch(benchmark):
    cache, task = benchmark
    result = _harness(cache).evaluate(task)
    assert result.outcome == harness.NO_PATCH


def test_environment_failure_is_not_blamed_on_agent(benchmark):
    cache, task = benchmark

    class Broken(FakeSandbox):
        def ensure_env_image(self, *a):
            raise EnvironmentFailure("pip install failed")

    result = _harness(cache, agent=_fixing_agent, sandbox=Broken()).evaluate(task)
    assert result.outcome == harness.ENV_ERROR


def test_network_tasks_need_explicit_permission(benchmark):
    cache, task = benchmark
    task = {**task, "repo": "psf/requests", "version": "2.3"}
    (cache / "repos" / "pallets__flask").rename(cache / "repos" / "psf__requests")
    assert _harness(cache).evaluate(task, mode=harness.GOLD).outcome == harness.ENV_ERROR
    sandbox = FakeSandbox()
    result = _harness(cache, sandbox=sandbox, allow_network=True).evaluate(task, mode=harness.GOLD)
    assert result.outcome == harness.RESOLVED and sandbox.calls[0]["network"] is True


def test_quota_error_stops_the_batch(benchmark):
    cache, task = benchmark

    def out_of_quota(ws, t, trace, run_tests=None):
        raise _StatusError("You exceeded your current quota", 429)

    tasks = [task, {**task, "instance_id": "pallets__flask-2"}]
    results, stopped = _harness(cache, agent=out_of_quota).evaluate_many(tasks)
    assert [r.outcome for r in results] == [harness.LLM_ERROR]
    assert stopped and "quota" in stopped


def test_store_and_summary_exclude_infrastructure_failures(tmp_path, benchmark):
    cache, task = benchmark
    store = ResultsStore(str(tmp_path / "results"))
    h = _harness(cache, agent=_fixing_agent)
    resolved = h.evaluate(task)
    other = {**task, "instance_id": "pallets__flask-2"}
    llm_failed = harness.TaskResult(run_id="r", instance_id=other["instance_id"], repo=other["repo"],
                                    mode=harness.AGENT, outcome=harness.LLM_ERROR)
    store.append(resolved)
    store.append(llm_failed)
    store.append(h.evaluate(task, mode=harness.GOLD))

    stats = summarize([task, other], store)
    assert stats["resolved"] == 1 and stats["attempted"] == 1 and stats["evaluated"] == 2
    assert stats["resolution_rate_attempted"] == 100.0 and stats["resolution_rate_total"] == 50.0
    assert stats["environment_validated"] == 1
    # Full test log goes to the artifacts directory, not the history line.
    record = store.history()[0]
    assert "full_test_log" not in record
    assert os.path.exists(os.path.join(store.dir, record["artifacts"] + ".log"))


def test_patch_bytes_survive_windows_text_mode(benchmark):
    """Patches piped to git must keep LF line endings (text-mode stdin on Windows adds CR)."""
    cache, task = benchmark
    ws = RepoCache(str(cache)).checkout(task["repo"], task["base_commit"])
    try:
        harness.apply_patch(ws, task["patch"])
        assert open(os.path.join(ws, "calc.py"), "rb").read() == b"def add(a, b):\n    return a + b\n"
    finally:
        harness.remove_workspace(ws)


def test_tasks_with_failed_gold_check_do_not_count_as_attempts(tmp_path):
    store = ResultsStore(str(tmp_path))
    tasks = [{"instance_id": "a"}, {"instance_id": "b"}]
    mk = lambda iid, mode, outcome: harness.TaskResult(run_id="r", instance_id=iid, repo="x", mode=mode, outcome=outcome)
    store.append(mk("a", harness.GOLD, harness.UNRESOLVED))  # reference fix fails here: environment is broken
    store.append(mk("a", harness.AGENT, harness.UNRESOLVED))
    store.append(mk("b", harness.GOLD, harness.RESOLVED))
    store.append(mk("b", harness.AGENT, harness.RESOLVED))
    stats = summarize(tasks, store)
    assert stats["environment_invalid"] == ["a"]
    assert stats["attempted"] == 1 and stats["resolution_rate_attempted"] == 100.0
    assert stats["resolution_rate_total"] == 50.0  # the strict number still counts every task


def test_failed_agent_run_keeps_its_analysis_and_plan(benchmark):
    cache, task = benchmark

    def fails_while_coding(ws, t, trace, run_tests=None):
        trace["analysis"], trace["plan"] = {"problem_summary": "x"}, {"summary": "edit calc.py"}
        raise PatchError("search text not found")

    result = _harness(cache, agent=fails_while_coding).evaluate(task)
    assert result.outcome == harness.NO_PATCH and result.plan == {"summary": "edit calc.py"}


def test_run_agent_runs_its_own_tests_and_repairs_on_failure(tmp_path, monkeypatch):
    from app.agents.schemas import IssueAnalysis, SolutionPlan, FileChangePlan
    ws = tmp_path / "ws"
    ws.mkdir()
    git(str(ws), "init", "-q")
    (ws / "calc.py").write_text("def add(a, b):\n    return a - b\n", newline="\n")
    (ws / "README.md").write_text("calc\n", newline="\n")
    _commit_all(str(ws), "base")

    analysis = IssueAnalysis(problem_summary="add subtracts", expected_behavior="sum", current_behavior="difference")
    plan = SolutionPlan(summary="fix add", acceptance_criteria=["add(2,2)==4"], affected_files=[
        FileChangePlan(path="calc.py", operation="modify", reason="bug"),
        FileChangePlan(path="README.md", operation="modify", reason="docs"),
        FileChangePlan(path="tests/test_calc.py", operation="create", reason="test")])
    monkeypatch.setattr("app.agents.issue_analyzer.IssueAnalyzer.analyze", lambda self, *a, **k: analysis)
    monkeypatch.setattr("app.agents.planner.PlannerAgent.create_plan", lambda self, *a, **k: plan)
    coded = {}

    def implement(self, workspace, analysis, plan, target_files, **kw):
        coded["targets"] = list(target_files)
        return {"changed_files": ["calc.py", "tests/test_calc.py"], "summary": ""}
    monkeypatch.setattr("app.agents.coding_agent.CodingAgent.implement", implement)

    repairs = {}

    def fake_repair(run_id, initial_context, workspace_path, runner, max_attempts, **kw):
        repairs.update(first=initial_context["test_result"]["outcome"], attempts=max_attempts, runner=runner)
        return {"status": "REPAIR_SUCCESS", "attempts": 1, "termination_reason": None,
                "test_result": {"outcome": "PASSED"}}
    monkeypatch.setattr("app.workflows.repair_graph.execute_repair_loop", fake_repair)

    ran = []

    def run_tests(workspace, files):
        ran.append(files)
        return TestRun(log="FAILED tests/test_calc.py::test_add - assert -2 == 4\n1 failed", exit_code=1,
                       timed_out=False, duration_seconds=0.1)

    trace = {}
    harness.run_agent(str(ws), {"instance_id": "x", "repo": "pallets/flask", "problem_statement": "add subtracts"},
                      trace, run_tests)
    assert coded["targets"] == ["calc.py", "tests/test_calc.py"] and trace["skipped_doc_files"] == ["README.md"]
    assert ran == [["tests/test_calc.py"]]
    assert trace["feedback"]["outcome"] == "TESTS_FAILED"
    assert repairs["first"] == "TESTS_FAILED" and repairs["runner"].test_files == ["tests/test_calc.py"]
    assert trace["repair"]["status"] == "REPAIR_SUCCESS" and trace["repair"]["final_outcome"] == "PASSED"


def test_per_model_stats_keep_each_models_own_results(tmp_path):
    """A later, weaker model's NO_PATCH must not hide an earlier model's RESOLVED in the per-model view."""
    from app.eval.store import by_model
    store = ResultsStore(str(tmp_path))
    for model, outcome in (("gemini-x", harness.RESOLVED), ("gpt-oss:20b", harness.NO_PATCH)):
        store.append(harness.TaskResult(run_id="r", instance_id="a", repo="x", mode=harness.AGENT,
                                        outcome=outcome, model=model, provider="p"))
    rows = {r["model"]: r for r in by_model([{"instance_id": "a"}], store)}
    assert rows["gemini-x"]["resolved_ids"] == ["a"] and rows["gpt-oss:20b"]["resolved"] == 0
    assert summarize([{"instance_id": "a"}], store)["resolved"] == 0  # headline = latest result of any model
