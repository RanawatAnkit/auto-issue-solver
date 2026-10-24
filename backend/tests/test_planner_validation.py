"""Regression tests for plan path validation (the "out-of-context missing untracked file" failures)."""
import pytest
from app.agents.planner import PlannerAgent, is_test_file
from app.agents.schemas import SolutionPlan, FileChangePlan


def _plan(*files):
    return SolutionPlan(
        summary="s",
        affected_files=[FileChangePlan(path=p, operation=op, reason="r") for p, op in files],
        acceptance_criteria=["works"],
    )


REPO = ["app/main.py", "app/models.py", "tests/test_tasks.py", "pyproject.toml", "Dockerfile"]


def test_non_source_tracked_files_can_be_modified():
    plan = _plan(("pyproject.toml", "modify"), ("Dockerfile", "modify"))
    PlannerAgent().validate_plan(plan, REPO)
    assert [f.path for f in plan.affected_files] == ["pyproject.toml", "Dockerfile"]


def test_modify_of_missing_test_file_becomes_create():
    # The model often says "modify tests/test_due_dates.py" when the file is new.
    plan = _plan(("app/models.py", "modify"), ("tests/test_due_dates.py", "modify"))
    PlannerAgent().validate_plan(plan, REPO)
    assert plan.affected_files[1].path == "tests/test_due_dates.py"
    assert plan.affected_files[1].operation == "create"


def test_prefixed_test_path_is_reconciled_to_existing_file():
    plan = _plan(("app/tests/test_tasks.py", "modify"))
    PlannerAgent().validate_plan(plan, REPO)
    assert plan.affected_files[0].path == "tests/test_tasks.py"
    assert plan.affected_files[0].operation == "modify"


def test_create_of_existing_file_becomes_modify():
    plan = _plan(("app/models.py", "create"))
    PlannerAgent().validate_plan(plan, REPO)
    assert plan.affected_files[0].operation == "modify"


def test_create_paths_are_not_remapped_onto_other_files():
    # A new app/utils/models.py must not be "reconciled" onto the existing app/models.py.
    plan = _plan(("app/utils/models.py", "create"))
    PlannerAgent().validate_plan(plan, REPO)
    assert plan.affected_files[0].path == "app/utils/models.py"
    assert plan.affected_files[0].operation == "create"


def test_missing_source_file_rejected_with_suggestion():
    plan = _plan(("app/model.py", "modify"))
    with pytest.raises(ValueError, match="Did you mean: app/models.py"):
        PlannerAgent().validate_plan(plan, REPO)


def test_duplicate_entries_are_merged():
    plan = _plan(("app/main.py", "modify"), ("./app/main.py", "modify"))
    PlannerAgent().validate_plan(plan, REPO)
    assert len(plan.affected_files) == 1


@pytest.mark.parametrize("path", ["C:/Windows/x.py", "/etc/passwd", "a/../../b.py", "src/.git/config"])
def test_unsafe_paths_rejected(path):
    with pytest.raises(ValueError):
        PlannerAgent().validate_plan(_plan((path, "create")), REPO)


@pytest.mark.parametrize("path,expected", [
    ("tests/test_x.py", True), ("pkg/test_x.py", True), ("x_test.go", True), ("src/a.spec.ts", True),
    ("src/__tests__/a.js", True), ("app/models.py", False), ("contest.py", False),
])
def test_is_test_file(path, expected):
    assert is_test_file(path) is expected


def test_oversized_plans_are_sent_back_to_be_narrowed():
    from app.agents.planner import PlannerAgent
    from app.agents.schemas import SolutionPlan, FileChangePlan
    files = [f"src/m{i}.py" for i in range(6)]
    wide = SolutionPlan(summary="s", acceptance_criteria=["a"], affected_files=[
        FileChangePlan(path=p, operation="modify", reason="r") for p in files])
    with pytest.raises(ValueError, match="at most 4"):
        PlannerAgent().validate_plan(wide, files)
    deep = SolutionPlan(summary="s", acceptance_criteria=["a"], affected_files=[
        FileChangePlan(path="src/m0.py", operation="modify", reason="r", symbols=[f"f{i}" for i in range(10)])])
    with pytest.raises(ValueError, match="symbols"):
        PlannerAgent().validate_plan(deep, files)


def test_persistently_oversized_plan_is_trimmed_not_fatal(monkeypatch):
    """Run 14 failed with 'Plan touches 5 files' after 3 attempts; size limits must not kill a run."""
    from app.agents.planner import PlannerAgent
    from app.agents.schemas import IssueAnalysis, SolutionPlan, FileChangePlan
    files = ["app/main.py", "app/models.py", "app/routes.py", "README.md", "tests/test_main.py"]
    plan = SolutionPlan(summary="s", acceptance_criteria=["a"], affected_files=[
        FileChangePlan(path=p, operation="modify", reason="r", symbols=[f"f{i}" for i in range(6)]) for p in files])
    agent = PlannerAgent()
    monkeypatch.setattr(agent.llm, "generate_structured", lambda *a, **k: plan.model_copy(deep=True))
    analysis = IssueAnalysis(problem_summary="p", expected_behavior="e", current_behavior="c")
    result = agent.create_plan(analysis, [], "o/r", files)
    kept = [f.path for f in result.affected_files]
    assert len(kept) == 4 and "README.md" not in kept and "tests/test_main.py" in kept
    assert all(len(f.symbols) == 4 for f in result.affected_files)
