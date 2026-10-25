"""Requirement capture (run 332): the plan reached PLAN_READY without the README example the issue asked for,
and assumed "due dates are normalized to UTC" although no planned change normalized anything (naive dates
then crash the overdue comparison). Requirements are now taken from the issue text and must each be met by
planned work. No model is called: LLM answers are scripted."""
import os
import copy
import shutil
import tempfile
import pytest
from unittest.mock import patch
from app.agents.requirements import extract_requirements
from app.agents.planner import PlannerAgent, DraftIncomplete, _files_named_in
from app.agents.schemas import IssueAnalysis, RetrievedContextChunk, plan_draft_model
from app.services.symbol_index import SymbolIndex

# Abridged from the real issue #4 of Ankit7474738/taskflow-api.
ISSUE_BODY = """Labels: enhancement

Summary

Add an optional due date to tasks and allow clients to filter the task list to overdue tasks.

Requirements

Add an optional due_date field to task creation, update, and response schemas. Accept ISO-8601 dates or datetimes; choose one representation, document it, and use it consistently.

Add an overdue_only boolean query parameter to GET /tasks.

Add a README example request.

Acceptance criteria

Invalid due-date input returns HTTP 422.

GET /tasks?overdue_only=true excludes completed and undated tasks.

Scope

Do not add a database, authentication, background scheduler, or external date/time service.
"""


# ---- extraction ----

def test_requirements_and_criteria_sections_are_extracted_and_scope_is_not():
    reqs = extract_requirements(ISSUE_BODY)
    assert reqs == [
        "Add an optional due_date field to task creation, update, and response schemas. Accept ISO-8601 dates or "
        "datetimes; choose one representation, document it, and use it consistently.",
        "Add an overdue_only boolean query parameter to GET /tasks.",
        "Add a README example request.",
        "Invalid due-date input returns HTTP 422.",
        "GET /tasks?overdue_only=true excludes completed and undated tasks.",
    ]
    assert not any("database" in r for r in reqs)  # "Scope" lists constraints, not work


def test_markdown_headings_bullets_and_checkboxes():
    body = "## Expected behaviour\n- [ ] Return 404 for unknown ids\n* Log the request id\n\n## Notes\n- irrelevant note"
    assert extract_requirements(body) == ["Return 404 for unknown ids", "Log the request id"]


def test_without_requirement_headings_the_issue_list_items_are_used():
    body = "Steps:\n1. Call the endpoint twice\n2. Observe duplicates\n\nPlease fix."
    assert extract_requirements(body) == ["Call the endpoint twice", "Observe duplicates"]


def test_plain_prose_issue_yields_nothing_and_duplicates_collapse():
    assert extract_requirements("The app crashes when I click save. Please fix it.") == []
    assert extract_requirements("Requirements\n- Add a flag.\n- add a flag\n- Add a flag") == ["Add a flag."]


def test_files_named_in_a_requirement():
    files = ["README.md", "app/models.py", "app/schemas.py", "tests/test_tasks.py", "app/__init__.py"]
    assert _files_named_in("Add a README example request.", files) == {"README": ["README.md"]}
    assert _files_named_in("update the response schemas", files) == {"schemas": ["app/schemas.py"]}
    assert _files_named_in("Store it on the domain Task model.", files) == {}  # 'model' is not 'models'


# ---- planning ----

FILES = {
    "README.md": "# taskflow\n\n## Example\n\ncurl http://127.0.0.1:8000/tasks\n",
    "app/models.py": "from dataclasses import dataclass\n\n@dataclass\nclass Task:\n    title: str\n",
    "app/schemas.py": "from pydantic import BaseModel\n\nclass TaskCreate(BaseModel):\n    title: str\n",
    "app/services.py": "class TaskService:\n    def list_tasks(self, page=1, page_size=20):\n        return [], 0\n",
    "tests/test_tasks.py": "def test_create_task():\n    pass\n",
}
CRITERIA = ["Invalid due-date input returns HTTP 422.", "GET /tasks?overdue_only=true excludes completed and undated tasks."]
REQUIREMENTS = ["Add an optional due_date field to task creation, update, and response schemas.",
                "Add a README example request."]
ANALYSIS = IssueAnalysis(problem_summary="due dates", expected_behavior="e", current_behavior="c",
                         acceptance_criteria=CRITERIA)
MODEL = plan_draft_model(CRITERIA, [1, 2], REQUIREMENTS)


def change(symbol, action="modify", change="adds an optional due date", details="optional value",
           preserves="requests without it keep working"):
    return {"symbol": symbol, "action": action, "change": change, "details": details, "preserves": preserves}


def planned_test(name):
    return {"file_path": "tests/test_tasks.py", "name": name, "scenario": "call the API and check the response"}


GOOD = {
    "summary": "Add due_date, overdue filter and a README example.",
    "root_cause_hypotheses": [{"description": "list_tasks has no overdue filter", "confidence": 0.8,
                               "evidence": [{"file_path": "app/services.py", "start_line": 2, "end_line": 3,
                                             "current_code_behavior": "list_tasks only paginates the tasks"}]}],
    "affected_files": [
        {"path": "app/models.py", "operation": "modify", "reason": "store", "changes": [change("Task.due_date", "add")]},
        {"path": "app/schemas.py", "operation": "modify", "reason": "accept",
         "changes": [change("TaskCreate.due_date", "add", details="timezone-aware datetime; naive input is rejected")]},
        {"path": "app/services.py", "operation": "modify", "reason": "filter", "changes": [change("TaskService.list_tasks")]},
        {"path": "README.md", "operation": "modify", "reason": "document",
         "changes": [change("README example", "add", change="adds a GET /tasks?overdue_only=true example")]},
        {"path": "tests/test_tasks.py", "operation": "modify", "reason": "tests",
         "changes": [change("test_invalid_due_date", "add"), change("test_overdue_only", "add")]},
    ],
    "tests_for_criterion_1": [planned_test("test_invalid_due_date")],
    "tests_for_criterion_2": [planned_test("test_overdue_only")],
    "requirement_1": {"addressed_by": ["TaskCreate.due_date", "app/models.py"],
                      "how": "Due date is a timezone-aware datetime on the schema and the model."},
    "requirement_2": {"addressed_by": ["README.md"], "how": "Adds an overdue_only request example to the README."},
    "risks": ["Clients sending naive datetimes now get 422."],
    "assumptions": [],
    "dependencies": [],
}


@pytest.fixture
def index():
    root = tempfile.mkdtemp(prefix="reqs-")
    for rel, text in FILES.items():
        os.makedirs(os.path.join(root, os.path.dirname(rel) or "."), exist_ok=True)
        with open(os.path.join(root, rel), "w", encoding="utf-8") as f:
            f.write(text)
    yield SymbolIndex.build(root, list(FILES))
    shutil.rmtree(root, ignore_errors=True)


def draft(**overrides):
    data = copy.deepcopy(GOOD)
    data.update(overrides)
    return MODEL.model_validate(data)


def validate(d, index):
    PlannerAgent.validate_draft(d, index, CRITERIA, [], REQUIREMENTS)


def test_complete_plan_meets_every_requirement(index):
    validate(draft(), index)


def test_schema_requires_an_entry_per_requirement():
    schema = MODEL.model_json_schema()
    assert {"requirement_1", "requirement_2"} <= set(schema["required"])
    assert "Add a README example request." in schema["properties"]["requirement_2"]["description"]
    data = copy.deepcopy(GOOD)
    data.pop("requirement_2")
    with pytest.raises(Exception, match="requirement_2"):
        MODEL.model_validate(data)


def test_run_332_plan_is_rejected_for_the_missing_readme_and_the_unbacked_assumption(index):
    files = [f for f in copy.deepcopy(GOOD["affected_files"]) if f["path"] != "README.md"]
    files[1]["changes"][0]["details"] = "optional datetime"
    run_332 = draft(
        affected_files=files,
        requirement_2={"addressed_by": ["app/schemas.py"], "how": "The schema documents the format."},
        assumptions=["Due dates can be timezone-aware or naive datetimes and are normalized to UTC for comparison."])
    with pytest.raises(DraftIncomplete) as err:
        validate(run_332, index)
    message = str(err.value)
    assert "requirement 2 ('Add a README example request.') names README, but README.md is not in affected_files" in message
    assert "claims behavior ('normalized') that no planned change implements" in message


def test_assumption_backed_by_a_planned_change_is_accepted(index):
    files = copy.deepcopy(GOOD["affected_files"])
    files[1]["changes"][0]["details"] = "naive datetimes are normalized to UTC by a field validator"
    validate(draft(affected_files=files, assumptions=["Due dates are normalized to UTC before comparison."]), index)


def test_requirement_pointing_at_unplanned_work_is_rejected(index):
    with pytest.raises(DraftIncomplete, match=r"requirement 1 .* is addressed by \['app/routes.py'\], which the plan does not contain"):
        validate(draft(requirement_1={"addressed_by": ["app/routes.py"], "how": "the route accepts it"}), index)


@pytest.mark.parametrize("ref", ["tests/test_tasks.py::test_overdue_only", "test_overdue_only", "TaskCreate",
                                 "app/schemas.py:TaskCreate.due_date", "Task.due_date()"])
def test_requirement_references_in_common_forms_are_recognised(index, ref):
    validate(draft(requirement_1={"addressed_by": [ref], "how": "covered"}), index)


def test_planner_prompts_with_numbered_requirements_and_stores_the_coverage(index):
    ctx = [RetrievedContextChunk(file_path=p, start_line=1, end_line=len(t.split("\n")), content=t, score=1)
           for p, t in FILES.items()]
    with patch("app.agents.planner.LLMService") as llm_cls, patch("app.config.settings.MAX_LLM_RETRIES", 0), \
         patch("app.config.settings.PLAN_MAX_FILES", 4):
        llm = llm_cls.return_value
        llm.prompt_budget_chars.return_value = 60000
        llm.generate_structured.return_value = draft()
        plan = PlannerAgent().create_plan(ANALYSIS, ctx, "o/r", list(FILES), locations=[], symbol_index=index,
                                          requirements=REQUIREMENTS + CRITERIA)  # criteria duplicates are dropped
    system, user, model = llm.generate_structured.call_args.args[:3]
    assert "Requirements stated in the issue" in user and "2. Add a README example request." in user
    assert "requirement_3" not in model.model_fields  # the two criteria are already covered by tests
    assert "unless a planned change does it" in system
    assert plan.requirements[1] == {"requirement": "Add a README example request.", "addressed_by": ["README.md"],
                                    "how": "Adds an overdue_only request example to the README."}


# ---- live replay of run 332 with requirement capture exposed these ----

def test_library_facts_are_not_treated_as_promises(index):
    validate(draft(assumptions=["Pydantic parses ISO-8601 strings into datetime objects."]), index)


def test_documentation_files_do_not_count_toward_the_grounded_size_limit(index):
    # 4 source files + README + tests: the README the issue asks for must not push the plan over PLAN_MAX_FILES=4.
    from app.agents.schemas import SolutionPlan, FileChangePlan
    paths = ["app/models.py", "app/schemas.py", "app/services.py", "app/routes.py", "README.md", "tests/test_tasks.py"]
    plan = SolutionPlan(summary="s", acceptance_criteria=["a"],
                        affected_files=[FileChangePlan(path=p, operation="modify", reason="r") for p in paths])
    with patch("app.agents.planner.LLMService"), patch("app.config.settings.PLAN_MAX_FILES", 4):
        PlannerAgent().validate_plan(plan, paths + ["app/routes.py"], symbol_index=index)


def test_last_attempt_trim_keeps_docs_and_tests_and_does_not_fail_again(index):
    # Previously the trimmed plan was re-checked without grounding, counted tests again, and raised PlanTooLarge.
    too_big = copy.deepcopy(GOOD)
    too_big["affected_files"].insert(3, {"path": "app/routes.py", "operation": "create", "reason": "route",
                                         "changes": [change("list_tasks", "add")]})
    ctx = [RetrievedContextChunk(file_path=p, start_line=1, end_line=len(t.split("\n")), content=t, score=1)
           for p, t in FILES.items()]
    with patch("app.agents.planner.LLMService") as llm_cls, patch("app.config.settings.MAX_LLM_RETRIES", 0), \
         patch("app.config.settings.PLAN_MAX_FILES", 3):
        llm = llm_cls.return_value
        llm.prompt_budget_chars.return_value = 60000
        llm.generate_structured.return_value = MODEL.model_validate(too_big)
        plan = PlannerAgent().create_plan(ANALYSIS, ctx, "o/r", list(FILES), locations=[], symbol_index=index,
                                          requirements=REQUIREMENTS)
    paths = [f.path for f in plan.affected_files]
    assert "README.md" in paths and "tests/test_tasks.py" in paths
    assert len([p for p in paths if p.startswith("app/")]) == 3  # the source files were trimmed to the limit
