"""Worker pipeline tests.

These run the real worker against a real temporary git repository (real clone,
real file edits through PatchService, real `git status`/commit). Only the LLM
agents, the Docker test runner and the GitHub API are mocked.
"""
import os
import uuid
import shutil
import hashlib
import subprocess
import tempfile
import pytest
from unittest.mock import patch, MagicMock
from app.workers.issue_worker import process_agent_run, _fail_interrupted_runs
from app.db.session import SessionLocal
from app.models.domain import AgentRun, AgentAction, Issue, Repository, AgentStateEnum, PullRequest
from app.agents.schemas import (IssueAnalysis, SolutionPlan, FileChangePlan, CodeChangePlan, CodeChange, TextEdit,
                                LocalizationResult, CodeLocation)
from app.services.github_service import GitWorkspace
from app.services.patch_service import PatchService


def _git(cwd, *args):
    subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True)


@pytest.fixture
def origin_repo():
    """A small committed repository standing in for the GitHub remote."""
    d = tempfile.mkdtemp(prefix="origin-")
    os.makedirs(os.path.join(d, "tests"))
    with open(os.path.join(d, "calc.py"), "w") as f:
        f.write("def add(a, b):\n    return a - b\n")
    with open(os.path.join(d, "tests", "test_calc.py"), "w") as f:
        f.write("from calc import add\n\n\ndef test_add():\n    assert add(1, 1) == 2\n")
    _git(d, "init", "-q", "-b", "main")
    _git(d, "add", "-A")
    _git(d, "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-q", "-m", "init")
    yield d
    shutil.rmtree(d, ignore_errors=True)


@pytest.fixture
def env(origin_repo, monkeypatch):
    """Patch the external boundaries and expose the mocks plus the cloned workspace."""
    workspaces = []

    class LocalWorkspace(GitWorkspace):
        def __init__(self, clone_url):
            super().__init__(clone_url)
            workspaces.append(self)
        def clone(self, auth_token=None):
            self.run_git_command(["clone", "-q", origin_repo, "."])
        push_branch = MagicMock()
        def cleanup(self):
            self.cleaned_up = True  # removed after the test so it can be inspected

    monkeypatch.setattr("app.config.settings.ENABLE_CODE_MUTATION", True)
    monkeypatch.setattr("app.config.settings.ENABLE_PR_CREATION", True)
    patches = {
        "gh": patch("app.workers.issue_worker.GitHubService"),
        "analyzer": patch("app.workers.issue_worker.IssueAnalyzer"),
        "context": patch("app.workers.issue_worker.ContextBuilder"),
        "planner": patch("app.workers.issue_worker.PlannerAgent"),
        "coder": patch("app.workers.issue_worker.CodingAgent"),
        "runner": patch("app.workers.issue_worker.TestRunner"),
        "repair": patch("app.workers.issue_worker.execute_repair_loop"),
        "indexer": patch("app.services.repository_indexer.RepositoryIndexer"),
        # Never reach a real model from the worker tests.
        "localizer": patch("app.workers.issue_worker.Localizer"),
    }
    mocks = {k: p.start().return_value if k not in ("repair",) else p.start() for k, p in patches.items()}
    ws_patch = patch("app.workers.issue_worker.GitWorkspace", LocalWorkspace)
    ws_patch.start()
    LocalWorkspace.push_branch = MagicMock()

    gh = mocks["gh"]
    gh.get_issue.return_value = MagicMock(title="add() subtracts", body="add(1, 1) returns 0")
    gh.get_installation_token.return_value = "ghs_testtoken"
    gh.get_branch_sha.return_value = None
    gh.find_existing_pull_request.return_value = None
    gh.create_pull_request.return_value = MagicMock(html_url="https://github.com/o/n/pull/7", id=7, number=7)
    mocks["analyzer"].analyze.return_value = IssueAnalysis(
        problem_summary="add subtracts", current_behavior="add(1,1)==0", expected_behavior="add(1,1)==2",
        affected_areas=["calc.py"], constraints=[], acceptance_criteria=["add(1,1)==2"])
    mocks["context"].build_context.return_value = []
    mocks["localizer"].localize.return_value = LocalizationResult(status="fallback", reason="test")
    mocks["planner"].create_plan.return_value = SolutionPlan(
        summary="Use + in add",
        affected_files=[FileChangePlan(path="calc.py", operation="modify", reason="bug", symbols=["add"], planned_changes=["a + b"])],
        acceptance_criteria=["add(1,1)==2"])

    def fix_add(workspace_path, **kwargs):
        return PatchService(workspace_path).apply_plan(CodeChangePlan(changes=[CodeChange(
            file_path="calc.py", operation="modify", edits=[TextEdit(search="return a - b", replace="return a + b")])]))
    mocks["coder"].implement.side_effect = fix_add

    failing = {"success": False, "outcome": "TESTS_FAILED", "exit_code": 1, "stdout": "FAILED tests/test_calc.py::test_add", "stderr": "", "summary": {"failed": 1}}
    passing = {"success": True, "outcome": "PASSED", "exit_code": 0, "stdout": "1 passed", "stderr": "", "summary": {"passed": 1}, "command": ["python", "-m", "pytest"]}
    mocks["runner"].run_tests.side_effect = [failing, passing]
    mocks["failing"], mocks["passing"] = failing, passing
    mocks["workspaces"] = workspaces
    mocks["push"] = LocalWorkspace.push_branch

    yield mocks

    for p in patches.values():
        p.stop()
    ws_patch.stop()
    for w in workspaces:
        shutil.rmtree(w.workspace_path, ignore_errors=True)


def _make_run(number=1):
    db = SessionLocal()
    ext = uuid.uuid4().hex
    repo = Repository(github_repo_id=f"r_{ext}", owner="o", name="n", full_name=f"o/n_{ext}", clone_url="https://github.com/o/n.git", default_branch="main")
    db.add(repo); db.commit()
    issue = Issue(github_issue_id=f"i_{ext}", repository_id=repo.id, number=number, title="add() subtracts", body="", state="open")
    db.add(issue); db.commit()
    run = AgentRun(issue_id=issue.id, status=AgentStateEnum.QUEUED)
    db.add(run); db.commit()
    return db, repo, issue, run


def _git_out(cwd, *args):
    return subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True, text=True).stdout


def test_worker_transitions_to_plan_ready_without_mutations(env, monkeypatch):
    monkeypatch.setattr("app.config.settings.ENABLE_CODE_MUTATION", False)
    db, repo, issue, run = _make_run()

    process_agent_run({"agent_run_id": run.id, "installation_id": 123})
    db.refresh(run)

    assert run.status == AgentStateEnum.PLAN_READY
    assert run.current_step == "Solution plan generated successfully"
    assert run.completed_at is not None
    assert run.solution_plan["affected_files"][0]["path"] == "calc.py"
    env["coder"].implement.assert_not_called()
    env["runner"].run_tests.assert_not_called()
    env["push"].assert_not_called()
    env["gh"].create_pull_request.assert_not_called()
    env["gh"].comment_on_issue.assert_not_called()
    ws = env["workspaces"][0]
    assert ws.cleaned_up
    assert _git_out(ws.workspace_path, "status", "--porcelain") == ""  # checkout left untouched


def test_worker_passes_tracked_files_to_planner(env, monkeypatch):
    # Regression: plan paths were validated against the indexing scan (selected
    # extensions only), so real files such as pyproject.toml counted as "untracked".
    monkeypatch.setattr("app.config.settings.ENABLE_CODE_MUTATION", False)
    db, repo, issue, run = _make_run()
    process_agent_run({"agent_run_id": run.id, "installation_id": 123})
    available = env["planner"].create_plan.call_args.args[3]
    assert sorted(available) == ["calc.py", "tests/test_calc.py"]


def test_worker_transitions_failure_on_exception(env):
    db, repo, issue, run = _make_run()
    env["analyzer"].analyze.side_effect = RuntimeError("LLM service unavailable")

    process_agent_run({"agent_run_id": run.id, "installation_id": 123})
    db.refresh(run)

    assert run.status == AgentStateEnum.FAILED
    assert "LLM service unavailable" in run.error_message
    assert run.completed_at is not None
    assert env["workspaces"][0].cleaned_up


def test_worker_end_to_end_success_creates_pr_with_only_agent_changes(env):
    db, repo, issue, run = _make_run(number=77)

    process_agent_run({"agent_run_id": run.id, "installation_id": 123})
    db.refresh(run)

    assert run.status == AgentStateEnum.PR_CREATED, run.error_message
    assert run.baseline_test_result["outcome"] == "TESTS_FAILED"
    assert run.test_result["outcome"] == "PASSED"
    assert run.modified_files == ["calc.py"]
    ws = env["workspaces"][0].workspace_path
    branch = f"agent/issue-77-run-{run.id}"
    assert _git_out(ws, "rev-parse", "--abbrev-ref", "HEAD").strip() == branch
    committed = _git_out(ws, "show", "--name-only", "--format=", "HEAD").split()
    assert committed == ["calc.py"]
    assert "return a + b" in _git_out(ws, "show", "HEAD:calc.py")
    env["push"].assert_called_once()
    assert env["push"].call_args.args[-1] == branch
    pr_body = env["gh"].create_pull_request.call_args.args[4]
    assert "Baseline (before change): TESTS_FAILED" in pr_body and "After change: PASSED" in pr_body
    pr = db.query(PullRequest).filter(PullRequest.issue_id == issue.id).one()
    assert pr.pr_url == "https://github.com/o/n/pull/7"
    actions = [a.action_type for a in db.query(AgentAction).filter(AgentAction.agent_run_id == run.id)]
    assert "baseline_tests" in actions and "create_pull_request" in actions


def test_worker_ignores_files_written_by_tests(env):
    def fix_and_litter(workspace_path, **kwargs):
        with open(os.path.join(workspace_path, "test.db"), "w") as f:
            f.write("sqlite junk created by the test suite")
        return env["coder"].implement.side_effect_orig(workspace_path, **kwargs)
    env["coder"].implement.side_effect_orig = env["coder"].implement.side_effect
    env["coder"].implement.side_effect = fix_and_litter
    db, repo, issue, run = _make_run()

    process_agent_run({"agent_run_id": run.id, "installation_id": 123})
    db.refresh(run)

    assert run.status == AgentStateEnum.PR_CREATED, run.error_message
    committed = _git_out(env["workspaces"][0].workspace_path, "show", "--name-only", "--format=", "HEAD").split()
    assert committed == ["calc.py"]


def test_worker_preserves_repair_failure_when_mutation_enabled(env):
    db, repo, issue, run = _make_run()
    env["runner"].run_tests.side_effect = [env["failing"], env["failing"]]
    env["repair"].return_value = {"status": "REPAIR_EXHAUSTED", "attempts": 3, "termination_reason": "MAX_ATTEMPTS",
                                  "test_result": env["failing"], "modified_files": ["calc.py"], "repair_history": []}

    process_agent_run({"agent_run_id": run.id, "installation_id": 123})
    db.refresh(run)

    assert run.status == AgentStateEnum.REPAIR_EXHAUSTED
    assert run.termination_reason == "MAX_ATTEMPTS"
    assert run.repair_attempt == 3
    env["push"].assert_not_called()
    # The repair loop receives the baseline so it can tell environment failures apart.
    assert env["repair"].call_args.kwargs["initial_context"]["baseline_test_result"]["outcome"] == "TESTS_FAILED"


def test_worker_stops_early_when_sandbox_cannot_run_tests(env):
    db, repo, issue, run = _make_run()
    env["runner"].run_tests.side_effect = [{"success": False, "outcome": "ENVIRONMENT_ERROR", "exit_code": 127,
                                            "stdout": "", "stderr": "Docker engine is not reachable"}]

    process_agent_run({"agent_run_id": run.id, "installation_id": 123})
    db.refresh(run)

    assert run.status == AgentStateEnum.FAILED
    assert run.termination_reason == "ENVIRONMENT_FAILURE"
    assert "Docker engine is not reachable" in run.error_message
    env["coder"].implement.assert_not_called()


def test_worker_validation_fails_on_empty_diff(env):
    env["coder"].implement.side_effect = None
    env["coder"].implement.return_value = {"changed_files": ["calc.py"], "summary": ""}  # claims a change, writes nothing
    db, repo, issue, run = _make_run()

    process_agent_run({"agent_run_id": run.id, "installation_id": 123})
    db.refresh(run)

    assert run.status == AgentStateEnum.VALIDATION_FAILED
    assert "Empty diff" in run.error_message
    env["push"].assert_not_called()
    env["gh"].create_pull_request.assert_not_called()


def test_worker_validation_fails_on_secret_detected(env):
    def leak(workspace_path, **kwargs):
        return PatchService(workspace_path).apply_plan(CodeChangePlan(changes=[CodeChange(
            file_path="calc.py", operation="modify",
            edits=[TextEdit(search="return a - b", replace="TOKEN = 'ghp_" + "a" * 36 + "'\n    return a + b")])]))
    env["coder"].implement.side_effect = leak
    db, repo, issue, run = _make_run()

    process_agent_run({"agent_run_id": run.id, "installation_id": 123})
    db.refresh(run)

    assert run.status == AgentStateEnum.VALIDATION_FAILED
    assert "secret" in run.error_message.lower()
    env["push"].assert_not_called()


def test_worker_pr_creation_can_be_disabled(env, monkeypatch):
    monkeypatch.setattr("app.config.settings.ENABLE_PR_CREATION", False)
    db, repo, issue, run = _make_run()

    process_agent_run({"agent_run_id": run.id, "installation_id": 123})
    db.refresh(run)

    assert run.status == AgentStateEnum.COMPLETED
    env["push"].assert_not_called()
    env["gh"].create_pull_request.assert_not_called()


def test_worker_branch_creation_failure_transitions_to_branch_creation_failed(env, monkeypatch):
    original = GitWorkspace.checkout_branch
    def failing_checkout(self, name, create=False):
        if create:
            raise RuntimeError("Permission denied: cannot create branch")
        return original(self, name, create)
    monkeypatch.setattr(GitWorkspace, "checkout_branch", failing_checkout)
    db, repo, issue, run = _make_run()

    process_agent_run({"agent_run_id": run.id, "installation_id": 123})
    db.refresh(run)

    assert run.status == AgentStateEnum.BRANCH_CREATION_FAILED
    assert "cannot create branch" in run.error_message
    env["push"].assert_not_called()


def test_worker_push_failure_scrubs_secrets_and_transitions_to_push_failed(env):
    env["push"].side_effect = RuntimeError("Push rejected to https://x-access-token:ghs_SECRET123@github.com/o/n.git")
    db, repo, issue, run = _make_run()

    process_agent_run({"agent_run_id": run.id, "installation_id": 123})
    db.refresh(run)

    assert run.status == AgentStateEnum.PUSH_FAILED
    assert "ghs_SECRET123" not in run.error_message
    assert "x-access-token:***@" in run.error_message
    env["gh"].create_pull_request.assert_not_called()


def test_worker_refuses_to_overwrite_existing_remote_branch(env):
    env["gh"].get_branch_sha.return_value = "abc123"
    db, repo, issue, run = _make_run()

    process_agent_run({"agent_run_id": run.id, "installation_id": 123})
    db.refresh(run)

    assert run.status == AgentStateEnum.PUSH_FAILED
    assert "already exists" in run.error_message
    env["push"].assert_not_called()


def test_worker_pr_idempotency_reuses_existing_pr(env):
    env["gh"].find_existing_pull_request.return_value = MagicMock(html_url="https://github.com/o/n/pull/3", id=3, number=3)
    db, repo, issue, run = _make_run()

    process_agent_run({"agent_run_id": run.id, "installation_id": 123})
    db.refresh(run)

    assert run.status == AgentStateEnum.PR_CREATED
    env["gh"].create_pull_request.assert_not_called()
    assert db.query(PullRequest).filter(PullRequest.issue_id == issue.id).one().pr_url == "https://github.com/o/n/pull/3"


def test_worker_ignores_duplicate_job_for_finished_run(env):
    db, repo, issue, run = _make_run()
    run.status = AgentStateEnum.PR_CREATED
    db.commit()

    process_agent_run({"agent_run_id": run.id, "installation_id": 123})

    env["analyzer"].analyze.assert_not_called()
    assert env["workspaces"] == []


def test_interrupted_jobs_are_marked_failed_not_retried(env):
    db, repo, issue, run = _make_run()
    run.status = AgentStateEnum.CODING
    db.commit()
    with patch("app.workers.issue_worker.queue_service.drain_interrupted_jobs", return_value=[{"agent_run_id": run.id}]):
        _fail_interrupted_runs()
    db.refresh(run)
    assert run.status == AgentStateEnum.FAILED
    assert "Worker stopped" in run.error_message


def test_worker_reports_no_valid_patch_accurately(env):
    from app.agents.coding_agent import NoValidPatchError
    env["coder"].implement.side_effect = NoValidPatchError(
        "app/services.py", ["attempt 1 [SCOPE_VIOLATION]: x", "attempt 2 [SYNTAX_ERROR]: y", "attempt 3 [CONTEXT_MISMATCH]: z"])
    db, repo, issue, run = _make_run()

    process_agent_run({"agent_run_id": run.id, "installation_id": 123})
    db.refresh(run)

    assert run.status == AgentStateEnum.FAILED
    assert run.termination_reason == "NO_VALID_PATCH"
    assert "SYNTAX_ERROR" in run.error_message and "after 3 attempt(s)" in run.error_message
    env["runner"].run_tests.assert_called_once()  # baseline only; no tests on a non-existent change
    env["push"].assert_not_called()


# ---- Planning milestone: localization, read-only checkout, usage metering ----

def _actions(db, run):
    return {a.action_type: a for a in db.query(AgentAction).filter(AgentAction.agent_run_id == run.id).all()}


def test_plan_ready_receives_verified_locations_and_leaves_checkout_untouched(env, monkeypatch):
    monkeypatch.setattr("app.config.settings.ENABLE_CODE_MUTATION", False)
    loc = CodeLocation(file_path="calc.py", symbol="add", kind="function", start_line=1, end_line=2, snippet="x")
    env["localizer"].localize.return_value = LocalizationResult(status="localized", locations=[loc], rounds=2)
    db, repo, issue, run = _make_run()

    process_agent_run({"agent_run_id": run.id, "installation_id": 123})
    db.refresh(run)

    assert run.status == AgentStateEnum.PLAN_READY
    kwargs = env["planner"].create_plan.call_args.kwargs
    assert [l.symbol for l in kwargs["locations"]] == ["add"]
    assert "calc.py" in kwargs["symbol_index"].python_files  # built from the real checkout
    assert run.solution_plan["localization"]["status"] == "localized"
    assert "snippet" not in run.solution_plan["localization"]["locations"][0]
    actions = _actions(db, run)
    assert {"symbol_index", "localization", "workspace_check"} <= set(actions)
    env["coder"].implement.assert_not_called()
    env["runner"].run_tests.assert_not_called()
    env["gh"].create_pull_request.assert_not_called()
    assert _git_out(env["workspaces"][0].workspace_path, "status", "--porcelain") == ""


def test_localization_fallback_plans_from_retrieved_context(env, monkeypatch):
    monkeypatch.setattr("app.config.settings.ENABLE_CODE_MUTATION", False)
    env["localizer"].localize.side_effect = RuntimeError("unexpected localizer bug")
    db, repo, issue, run = _make_run()

    process_agent_run({"agent_run_id": run.id, "installation_id": 123})
    db.refresh(run)

    assert run.status == AgentStateEnum.PLAN_READY
    assert env["planner"].create_plan.call_args.kwargs["locations"] == []
    assert run.solution_plan["localization"]["status"] == "fallback"
    assert "unexpected localizer bug" in run.solution_plan["localization"]["reason"]


def test_localization_disabled_skips_localizer(env, monkeypatch):
    monkeypatch.setattr("app.config.settings.ENABLE_CODE_MUTATION", False)
    monkeypatch.setattr("app.config.settings.LOCALIZATION_ENABLED", False)
    db, repo, issue, run = _make_run()
    process_agent_run({"agent_run_id": run.id, "installation_id": 123})
    db.refresh(run)
    assert run.status == AgentStateEnum.PLAN_READY
    env["localizer"].localize.assert_not_called()
    assert "localization" not in run.solution_plan


@pytest.mark.parametrize("mutation", [False, True])
def test_workspace_modified_during_planning_fails_before_any_coding(env, monkeypatch, mutation):
    # Planning must be read-only; with mutation on, the run must still never reach coding.
    monkeypatch.setattr("app.config.settings.ENABLE_CODE_MUTATION", mutation)
    def plan_and_scribble(analysis, context, repo_name, tracked, **kwargs):
        with open(os.path.join(env["workspaces"][0].workspace_path, "calc.py"), "a") as f:
            f.write("# changed by planning\n")
        return SolutionPlan(summary="s", affected_files=[FileChangePlan(path="calc.py", operation="modify", reason="r")],
                            acceptance_criteria=["a"])
    env["planner"].create_plan.side_effect = plan_and_scribble
    db, repo, issue, run = _make_run()

    process_agent_run({"agent_run_id": run.id, "installation_id": 123})
    db.refresh(run)

    assert run.status == AgentStateEnum.FAILED
    assert run.termination_reason == "WORKSPACE_MODIFIED_DURING_PLANNING"
    assert "calc.py" in run.error_message
    env["coder"].implement.assert_not_called()
    env["runner"].run_tests.assert_not_called()
    env["gh"].create_pull_request.assert_not_called()


def test_llm_usage_is_recorded_per_run(env, monkeypatch):
    from app.services.llm_service import _record_usage
    monkeypatch.setattr("app.config.settings.ENABLE_CODE_MUTATION", False)
    analysis = env["analyzer"].analyze.return_value
    def analyze_with_usage(*args, **kwargs):
        _record_usage(provider="ollama", model="m", schema="IssueAnalysis", success=True, error=None,
                      prompt_tokens=120, completion_tokens=30, duration_seconds=1.5)
        return analysis
    env["analyzer"].analyze.side_effect = analyze_with_usage
    db, repo, issue, run = _make_run()

    process_agent_run({"agent_run_id": run.id, "installation_id": 123})

    import json as _json
    usage = _json.loads(_actions(db, run)["llm_usage"].output_data)
    assert usage["calls"] == 1 and usage["prompt_tokens"] == 120 and usage["total_tokens"] == 150
    assert usage["by_schema"]["IssueAnalysis"]["calls"] == 1


def test_seeded_localization_reaches_the_planner_as_unconfirmed(env, monkeypatch):
    monkeypatch.setattr("app.config.settings.ENABLE_CODE_MUTATION", False)
    seed = CodeLocation(file_path="calc.py", symbol="add", kind="function", start_line=1, end_line=2)
    env["localizer"].localize.return_value = LocalizationResult(
        status="seeded", reason="LLM call failed in round 1 (RuntimeError)", locations=[seed],
        llm_error="RuntimeError: response failed validation")
    db, repo, issue, run = _make_run()
    process_agent_run({"agent_run_id": run.id, "installation_id": 123})
    db.refresh(run)
    assert run.status == AgentStateEnum.PLAN_READY
    kwargs = env["planner"].create_plan.call_args.kwargs
    assert [l.symbol for l in kwargs["locations"]] == ["add"] and kwargs["locations_confirmed"] is False
    action = _actions(db, run)["localization"]
    assert not action.success  # the model's localization failed; seeds are only candidates
    assert "response failed validation" in action.output_data
    assert run.solution_plan["localization"]["llm_error"] == "RuntimeError: response failed validation"


def test_confirmed_localization_is_passed_as_confirmed(env, monkeypatch):
    monkeypatch.setattr("app.config.settings.ENABLE_CODE_MUTATION", False)
    loc = CodeLocation(file_path="calc.py", symbol="add", kind="function", start_line=1, end_line=2)
    env["localizer"].localize.return_value = LocalizationResult(status="localized", locations=[loc])
    db, repo, issue, run = _make_run()
    process_agent_run({"agent_run_id": run.id, "installation_id": 123})
    db.refresh(run)
    assert env["planner"].create_plan.call_args.kwargs["locations_confirmed"] is True
    assert _actions(db, run)["localization"].success


def test_planning_failure_is_reported_with_every_rejected_attempt(env, monkeypatch):
    monkeypatch.setattr("app.config.settings.ENABLE_CODE_MUTATION", False)
    env["planner"].attempt_errors = ["attempt 1: Plan incomplete: no planned test covers acceptance criteria 2"]
    env["planner"].create_plan.side_effect = ValueError("Solution Plan validation failed after 3 attempts: Plan incomplete")
    db, repo, issue, run = _make_run()
    process_agent_run({"agent_run_id": run.id, "installation_id": 123})
    db.refresh(run)
    assert run.status == AgentStateEnum.FAILED and "Plan incomplete" in run.error_message
    assert "no planned test covers" in _actions(db, run)["plan_rejected_attempts"].output_data
    env["coder"].implement.assert_not_called()


def test_issue_requirements_are_extracted_logged_and_passed_to_the_planner(env, monkeypatch):
    monkeypatch.setattr("app.config.settings.ENABLE_CODE_MUTATION", False)
    env["gh"].get_issue.return_value = MagicMock(
        title="add() subtracts", body="Requirements\n- add() must return a + b\n- Add a README example request.")
    db, repo, issue, run = _make_run()
    process_agent_run({"agent_run_id": run.id, "installation_id": 123})
    db.refresh(run)
    assert run.status == AgentStateEnum.PLAN_READY
    assert env["planner"].create_plan.call_args.kwargs["requirements"] == [
        "add() must return a + b", "Add a README example request."]
    assert "README example" in _actions(db, run)["issue_requirements"].output_data
