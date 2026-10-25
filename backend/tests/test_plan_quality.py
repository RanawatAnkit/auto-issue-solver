"""Regression tests for run 317: the grounded planner returned a plan whose symbols, new_symbols,
planned_changes, tests, risks and assumptions were all empty, and it was accepted as PLAN_READY.
No model is called: LLM output is scripted or parsed from JSON."""
import os
import json
import copy
import shutil
import tempfile
import pytest
from unittest.mock import patch
from pydantic import ValidationError
from app.services.symbol_index import SymbolIndex
from app.services.llm_service import parse_structured_output, LLMOutputError
from app.agents.planner import PlannerAgent, is_suite_criterion
from app.agents.schemas import (
    IssueAnalysis, SolutionPlanDraft, CodeLocation, RetrievedContextChunk, plan_from_draft, plan_draft_model,
)

FILES = {
    "app/models.py": "from dataclasses import dataclass\n\n@dataclass\nclass Task:\n    title: str\n    status: str = 'todo'\n",
    "app/schemas.py": "from pydantic import BaseModel\n\nclass TaskCreate(BaseModel):\n    title: str\n\nclass TaskUpdate(BaseModel):\n    title: str | None = None\n",
    "app/services.py": "class TaskService:\n    def list_tasks(self, status=None, page=1, page_size=20):\n        return [], 0\n",
    "app/routes.py": "def list_tasks(status=None, page: int = 1, page_size: int = 20):\n    return []\n",
    "tests/test_tasks.py": "def test_create_and_get_task():\n    pass\n",
}
CRITERIA = ["Invalid due-date input returns HTTP 422.", "GET /tasks?overdue_only=true excludes completed and undated tasks.",
            "Filtering and pagination remain correct."]
ANALYSIS = IssueAnalysis(problem_summary="due dates and overdue filter", expected_behavior="e", current_behavior="c",
                         acceptance_criteria=CRITERIA)
MODEL = plan_draft_model(CRITERIA, [1, 2, 3])


def change(symbol, action="modify"):
    return {"symbol": symbol, "action": action, "change": "c", "details": "d", "preserves": "p"}


def planned_test(name, scenario="s"):
    return {"file_path": "tests/test_tasks.py", "name": name, "scenario": scenario}


COMPLETE = {
    "summary": "Add due_date and an overdue_only filter.",
    "root_cause_hypotheses": [{"description": "list_tasks filters only by status and page", "confidence": 0.8,
                               "evidence": [{"file_path": "app/services.py", "start_line": 2, "end_line": 3,
                                             "current_code_behavior": "list_tasks has no due-date parameter"}]}],
    "affected_files": [
        {"path": "app/models.py", "operation": "modify", "reason": "store due date",
         "changes": [change("Task"), change("Task.due_date", "add")]},
        {"path": "app/schemas.py", "operation": "modify", "reason": "accept due date",
         "changes": [change("TaskCreate"), change("TaskUpdate")]},
        {"path": "app/services.py", "operation": "modify", "reason": "filter",
         "changes": [change("TaskService.list_tasks"), change("TaskService.list_tasks.overdue_only", "add")]},
        {"path": "app/routes.py", "operation": "modify", "reason": "query parameter", "changes": [change("list_tasks")]},
        {"path": "tests/test_tasks.py", "operation": "modify", "reason": "tests",
         "changes": [change("test_invalid_due_date_returns_422", "add"), change("test_overdue_only_filter", "add")]},
    ],
    "tests_for_criterion_1": [planned_test("test_invalid_due_date_returns_422", "POST bad date -> 422")],
    "tests_for_criterion_2": [planned_test("test_overdue_only_filter", "past/future/done/undated")],
    "tests_for_criterion_3": [planned_test("test_overdue_only_filter", "past/future/done/undated")],
    "risks": ["Default page size could change if list_tasks signature is reordered."],
    "assumptions": ["Dates are compared in UTC."],
    "dependencies": [],
}

# What run 317 actually returned (abridged): required keys only, every detail list empty.
RUN_317 = {
    "summary": "Add an optional due date field.",
    "root_cause_hypotheses": [{"description": "Task has no due_date", "evidence": [{"location_id": "L1", "claim": "c"}]}],
    "affected_files": [{"path": "app/models.py", "operation": "modify", "reason": "r",
                        "symbols": [], "new_symbols": [], "planned_changes": []}],
    "tests": [], "dependencies": [], "risks": [], "assumptions": [],
    "acceptance_criteria": CRITERIA,
}


@pytest.fixture
def index():
    root = tempfile.mkdtemp(prefix="plan-")
    for rel, text in FILES.items():
        os.makedirs(os.path.join(root, os.path.dirname(rel)), exist_ok=True)
        with open(os.path.join(root, rel), "w", encoding="utf-8") as f:
            f.write(text)
    yield SymbolIndex.build(root, list(FILES))
    shutil.rmtree(root, ignore_errors=True)


def draft(**overrides):
    data = copy.deepcopy(COMPLETE)
    data.update(overrides)
    return MODEL.model_validate(data)


def validate(d, index, criteria=CRITERIA):
    PlannerAgent.validate_draft(d, index, criteria)


# ---- schema: what the model is constrained to ----

def test_run_317_output_is_rejected_not_accepted():
    with pytest.raises(LLMOutputError):
        parse_structured_output(json.dumps(RUN_317), MODEL)


@pytest.mark.parametrize("mutate,field", [
    (lambda d: d["affected_files"][0].update(changes=[]), "changes"),
    (lambda d: d.update(tests_for_criterion_2=[]), "tests_for_criterion_2"),
    (lambda d: d.pop("tests_for_criterion_1"), "tests_for_criterion_1"),
    (lambda d: d.update(risks=[]), "risks"),
    (lambda d: d["root_cause_hypotheses"][0].pop("confidence"), "confidence"),
    (lambda d: d["root_cause_hypotheses"][0].update(evidence=[]), "evidence"),
    (lambda d: d["affected_files"][0]["changes"][0].update(change=""), "change"),
    (lambda d: d.pop("assumptions"), "assumptions"),
    (lambda d: d.update(dependencies=["The Task model should be updated"]), "dependencies"),
    (lambda d: d["affected_files"][0]["changes"][0].update(details="x" * 201), "details"),
])
def test_incomplete_or_oversized_drafts_fail_schema_validation(mutate, field):
    data = copy.deepcopy(COMPLETE)
    mutate(data)
    with pytest.raises(ValidationError, match=field):
        MODEL.model_validate(data)


def test_json_schema_sent_to_the_model_requires_and_bounds_details():
    schema = MODEL.model_json_schema()
    assert {"risks", "assumptions", "root_cause_hypotheses", "tests_for_criterion_1",
            "tests_for_criterion_2", "tests_for_criterion_3"} <= set(schema["required"])
    assert schema["properties"]["tests_for_criterion_2"]["minItems"] == 1
    assert "excludes completed and undated" in schema["properties"]["tests_for_criterion_2"]["description"]
    file_def = schema["$defs"]["FileChangeDraft"]
    assert "changes" in file_def["required"] and file_def["properties"]["changes"]["minItems"] == 1
    assert file_def["properties"]["changes"]["maxItems"] == 6
    change_def = schema["$defs"]["PlannedChange"]
    assert {"symbol", "action", "change", "details", "preserves"} <= set(change_def["required"])
    assert change_def["properties"]["details"]["maxLength"] == 200 and change_def["additionalProperties"] is False
    assert "confidence" in schema["$defs"]["RootCauseDraft"]["required"]
    assert "current_code_behavior" in schema["$defs"]["EvidenceDraft"]["required"]
    assert schema["properties"]["dependencies"]["items"]["pattern"].startswith("^[A-Za-z0-9]")
    assert "acceptance_criteria" not in schema["properties"]


def test_suite_level_criteria_get_no_test_field():
    criteria = ["Existing tests continue to pass.", "New tests pass with pytest -q."] + CRITERIA
    testable = [i for i, c in enumerate(criteria, 1) if not is_suite_criterion(c)]
    assert testable == [3, 4, 5]
    fields = set(plan_draft_model(criteria, testable).model_json_schema()["properties"])
    assert "tests_for_criterion_1" not in fields and "tests_for_criterion_3" in fields


# ---- draft checks beyond the schema ----

def test_complete_draft_passes(index):
    validate(draft(), index)


def test_test_file_must_be_an_affected_file(index):
    files = [f for f in COMPLETE["affected_files"] if not f["path"].startswith("tests/")]
    with pytest.raises(ValueError, match="not among affected_files"):
        validate(draft(affected_files=files), index)


def test_adding_an_existing_symbol_must_be_a_modify(index):
    files = copy.deepcopy(COMPLETE["affected_files"])
    files[2]["changes"] = [change("TaskService.list_tasks", "add")]
    with pytest.raises(ValueError, match="'TaskService.list_tasks' already exists in app/services.py; use action='modify'"):
        validate(draft(affected_files=files), index)


def test_modify_in_a_new_file_is_rejected(index):
    files = copy.deepcopy(COMPLETE["affected_files"]) + [
        {"path": "app/dates.py", "operation": "create", "reason": "r", "changes": [change("parse_due_date")]}]
    with pytest.raises(ValueError, match="app/dates.py is a new file"):
        validate(draft(affected_files=files), index)


def test_whitespace_only_details_are_rejected(index):
    files = copy.deepcopy(COMPLETE["affected_files"])
    files[0]["changes"][0]["preserves"] = "   "
    with pytest.raises(ValueError, match="empty symbol, change, details or preserves"):
        validate(draft(affected_files=files), index)


def test_field_added_to_model_requires_related_request_response_classes(index):
    files = [f for f in copy.deepcopy(COMPLETE["affected_files"]) if f["path"] != "app/schemas.py"]
    with pytest.raises(ValueError, match=r"'Task.due_date' adds data to Task, but the related classes TaskCreate "
                                         r"\(app/schemas.py\), TaskUpdate \(app/schemas.py\) are not planned"):
        validate(draft(affected_files=files), index)


def test_related_class_can_be_ruled_out_in_assumptions(index):
    files = copy.deepcopy(COMPLETE["affected_files"])
    files[1]["changes"] = [change("TaskCreate")]
    validate(draft(affected_files=files, assumptions=["TaskUpdate needs no change: due dates are set only on creation."]), index)
    with pytest.raises(ValueError, match="TaskUpdate"):
        validate(draft(affected_files=files), index)


def test_evidence_must_describe_current_code_not_proposals(index):
    hyps = copy.deepcopy(COMPLETE["root_cause_hypotheses"])
    hyps[0]["evidence"][0]["current_code_behavior"] = "The Task model should be updated to include due_date."
    with pytest.raises(ValueError, match="is a proposal"):
        validate(draft(root_cause_hypotheses=hyps), index)


def test_dependencies_must_be_new_third_party_packages(index):
    validate(draft(dependencies=["python-dateutil"]), index)
    with pytest.raises(ValueError, match=r"not \['TaskService'\]"):
        validate(draft(dependencies=["TaskService"]), index)  # the repository's own code


# ---- conversion to the stored plan ----

def test_symbols_planned_changes_and_tests_are_derived_consistently():
    plan = plan_from_draft(draft(), CRITERIA)
    models = plan.affected_files[0]
    assert models.symbols == ["Task"] and models.new_symbols == ["Task.due_date"]
    assert models.planned_changes[1] == "Add `Task.due_date`: c Details: d Preserves: p"
    assert plan.tests == [
        "tests/test_tasks.py::test_invalid_due_date_returns_422: POST bad date -> 422 (covers criteria 1)",
        "tests/test_tasks.py::test_overdue_only_filter: past/future/done/undated (covers criteria 2, 3)",
    ]
    assert plan.acceptance_criteria == CRITERIA
    assert plan.root_cause_hypotheses[0].evidence[0].claim == "list_tasks has no due-date parameter"
    assert plan.risks and plan.assumptions == ["Dates are compared in UTC."]
    assert plan.root_cause_hypotheses[0].confidence == 0.8


# ---- the planner loop ----

def _run_planner(index, outputs, analysis=ANALYSIS, locations=()):
    ctx = [RetrievedContextChunk(file_path=p, start_line=1, end_line=len(t.split("\n")), content=t, score=1)
           for p, t in FILES.items()]
    with patch("app.agents.planner.LLMService") as llm_cls:
        llm = llm_cls.return_value
        llm.prompt_budget_chars.return_value = 60000
        llm.generate_structured.side_effect = outputs
        planner = PlannerAgent()
        try:
            return planner, llm, planner.create_plan(analysis, ctx, "o/r", list(FILES), locations=list(locations),
                                                     symbol_index=index)
        except ValueError as e:
            return planner, llm, e


def test_planner_requests_a_per_criterion_schema(index):
    _, llm, plan = _run_planner(index, [draft()])
    model = llm.generate_structured.call_args.args[2]
    assert issubclass(model, SolutionPlanDraft)
    assert {"tests_for_criterion_1", "tests_for_criterion_2", "tests_for_criterion_3"} <= set(model.model_fields)
    assert all(f.planned_changes for f in plan.affected_files) and plan.tests and plan.risks


def test_incomplete_plan_is_retried_with_specific_feedback(index):
    hyps = copy.deepcopy(COMPLETE["root_cause_hypotheses"])
    hyps[0]["evidence"][0]["current_code_behavior"] = "list_tasks needs to filter overdue tasks"
    planner, llm, plan = _run_planner(index, [draft(root_cause_hypotheses=hyps), draft()])
    assert "is a proposal" in llm.generate_structured.call_args_list[1].args[1]
    assert len(planner.attempt_errors) == 1 and "attempt 1" in planner.attempt_errors[0]
    assert plan.root_cause_hypotheses[0].evidence[0].claim == "list_tasks has no due-date parameter"


def test_made_up_symbol_is_still_rejected_after_conversion(index):
    files = copy.deepcopy(COMPLETE["affected_files"])
    files[2]["changes"] = [change("TaskService.filter_overdue")]
    _, _, err = _run_planner(index, [draft(affected_files=files)] * 3)
    assert isinstance(err, ValueError) and "TaskService.filter_overdue' does not exist" in str(err)


def test_planning_fails_clearly_instead_of_returning_an_empty_plan(index):
    bad = draft(dependencies=["TaskService"])
    planner, _, err = _run_planner(index, [bad] * 3)
    assert isinstance(err, ValueError)
    assert "failed after 3 attempts" in str(err) and "dependencies must be new third-party" in str(err)
    assert len(planner.attempt_errors) == 3


def test_prompt_numbers_criteria_and_asks_for_layers_tests_and_facts(index):
    analysis = IssueAnalysis(problem_summary="p", expected_behavior="e", current_behavior="c",
                             acceptance_criteria=["Existing tests continue to pass."] + CRITERIA)
    model = plan_draft_model(analysis.acceptance_criteria, [2, 3, 4])
    data = copy.deepcopy(COMPLETE)
    data["tests_for_criterion_4"] = data.pop("tests_for_criterion_3")
    data["tests_for_criterion_3"] = data.pop("tests_for_criterion_2")
    data["tests_for_criterion_2"] = data.pop("tests_for_criterion_1")
    _, llm, plan = _run_planner(index, [model.model_validate(data)], analysis=analysis)
    system, user = llm.generate_structured.call_args.args[:2]
    assert "1. Existing tests continue to pass. [met by running the full test suite" in user
    assert "2. Invalid due-date input returns HTTP 422." in user
    assert "every layer" in system and "tests_for_criterion_N" in system
    assert "only state that the code lacks something if the code shown" in system
    assert plan.acceptance_criteria == analysis.acceptance_criteria


def test_prompt_lists_related_request_response_classes(index):
    loc = CodeLocation(file_path="app/models.py", symbol="Task", kind="class", start_line=3, end_line=6, snippet="x")
    _, llm, _ = _run_planner(index, [draft()], locations=[loc])
    assert "- Task: TaskCreate (app/schemas.py:3-4), TaskUpdate (app/schemas.py:6-7)" in llm.generate_structured.call_args.args[1]


def test_localizer_proposals_are_not_shown_as_evidence_text(index):
    loc = CodeLocation(file_path="app/models.py", symbol="Task", kind="class", start_line=3, end_line=6, snippet="x",
                       reason="The Task model needs to be updated to include due_date.",
                       intended_behavior="Add a due_date field.")
    _, llm, _ = _run_planner(index, [draft()], locations=[loc])
    user = llm.generate_structured.call_args.args[1]
    assert "[L1] app/models.py:3-6 class Task" in user and "needs to be updated" not in user


def test_ungrounded_mode_keeps_the_original_schema(index):
    from app.agents.schemas import SolutionPlan, FileChangePlan
    legacy = SolutionPlan(summary="s", affected_files=[FileChangePlan(path="app/routes.py", operation="modify", reason="r")],
                          acceptance_criteria=["a"])
    with patch("app.agents.planner.LLMService") as llm_cls:
        llm = llm_cls.return_value
        llm.prompt_budget_chars.return_value = 60000
        llm.generate_structured.return_value = legacy
        plan = PlannerAgent().create_plan(ANALYSIS, [], "o/r", list(FILES))
    assert plan is legacy and llm.generate_structured.call_args.args[2] is SolutionPlan


# ---- fourth pass: defects seen in the accepted live plan ----

def test_every_localized_location_must_be_planned_or_ruled_out(index):
    route = CodeLocation(file_path="app/routes.py", symbol="list_tasks", kind="function", start_line=1, end_line=2)
    files = [f for f in copy.deepcopy(COMPLETE["affected_files"]) if f["path"] != "app/routes.py"]
    with pytest.raises(ValueError, match=r"localized location L1 \(app/routes.py list_tasks\) is not in affected_files"):
        PlannerAgent.validate_draft(draft(affected_files=files), index, CRITERIA, [route])
    PlannerAgent.validate_draft(draft(affected_files=files, assumptions=["list_tasks already forwards all query params."]),
                                index, CRITERIA, [route])


def test_evidence_that_is_only_a_location_id_is_rejected(index):
    hyps = copy.deepcopy(COMPLETE["root_cause_hypotheses"])
    hyps[0]["evidence"][0]["current_code_behavior"] = "L1"
    with pytest.raises(ValueError, match="'L1' says nothing"):
        validate(draft(root_cause_hypotheses=hyps), index)


@pytest.mark.parametrize("dep", ["The", "none", "TaskService"])
def test_placeholder_and_repository_names_are_not_dependencies(index, dep):
    with pytest.raises(ValueError, match="dependencies must be new third-party"):
        validate(draft(dependencies=[dep]), index)


@pytest.mark.parametrize("text", [
    "def test_invalid_due_date():\n    response = client.post('/tasks')",
    "tasks = [task for task in tasks if task.due_date is not None]",
    "assert response.status_code == 422",
])
def test_code_in_planned_changes_is_rejected(index, text):
    files = copy.deepcopy(COMPLETE["affected_files"])
    files[2]["changes"][0]["change"] = text[:160]
    with pytest.raises(ValueError, match="contains code"):
        validate(draft(affected_files=files), index)


def test_type_annotations_in_prose_are_allowed(index):
    files = copy.deepcopy(COMPLETE["affected_files"])
    files[0]["changes"][1]["details"] = "Optional `datetime | None` field defaulting to None."
    validate(draft(affected_files=files), index)


def test_text_cut_by_the_length_limit_is_marked_not_hidden():
    files = copy.deepcopy(COMPLETE["affected_files"])
    files[0]["changes"][1]["details"] = "x" * 200
    plan = plan_from_draft(draft(affected_files=files), CRITERIA)
    assert "[cut at 200 characters]" in plan.affected_files[0].planned_changes[1]
    assert "[cut at" not in plan.affected_files[0].planned_changes[0]


def test_dependency_length_is_bounded_inside_the_pattern():
    # Ollama ignores maxLength when a pattern is present; an unbounded pattern let it emit 125-character names.
    item = MODEL.model_json_schema()["properties"]["dependencies"]["items"]
    assert item["pattern"].endswith("{0,59}$")
    with pytest.raises(ValidationError):
        MODEL.model_validate(dict(copy.deepcopy(COMPLETE), dependencies=["a" * 61]))
