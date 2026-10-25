"""Regression tests for run 330: every planner attempt failed with "'Task.due_date' adds data to Task, but the
related classes TaskCreate, TaskUpdate, TaskResponse are not planned", because the validator only accepted a
change named exactly 'TaskCreate' while the model (as the prompt instructs) planned 'TaskCreate.due_date'.
Each retry then regenerated the whole plan. No model is called: LLM answers are scripted."""
import os
import copy
import shutil
import tempfile
import pytest
from unittest.mock import patch
from app.services.symbol_index import SymbolIndex
from app.agents.planner import PlannerAgent, DraftIncomplete
from app.agents.schemas import (IssueAnalysis, RetrievedContextChunk, plan_draft_model, related_class_repair_model,
                                related_class_field)

FILES = {
    "app/models.py": "from dataclasses import dataclass\n\n@dataclass\nclass Task:\n    title: str\n    status: str = 'todo'\n",
    "app/schemas.py": ("from pydantic import BaseModel\n\n"
                       "class TaskCreate(BaseModel):\n    title: str\n\n"
                       "class TaskUpdate(BaseModel):\n    title: str | None = None\n\n"
                       "class TaskResponse(BaseModel):\n    id: str\n    title: str\n"),
    "app/services.py": "class TaskService:\n    def list_tasks(self, status=None, page=1, page_size=20):\n        return [], 0\n",
    "tests/test_tasks.py": "def test_create_and_get_task():\n    pass\n",
}
SCHEMA_CLASSES = ["TaskCreate", "TaskUpdate", "TaskResponse"]
CRITERIA = ["Invalid due-date input returns HTTP 422.", "GET /tasks?overdue_only=true excludes completed and undated tasks."]
ANALYSIS = IssueAnalysis(problem_summary="due dates and overdue filter", expected_behavior="e", current_behavior="c",
                         acceptance_criteria=CRITERIA)
MODEL = plan_draft_model(CRITERIA, [1, 2])


def change(symbol, action="modify", change="adds an optional due date", details="optional datetime value",
           preserves="requests without it keep working"):
    return {"symbol": symbol, "action": action, "change": change, "details": details, "preserves": preserves}


def test_(name):
    return {"file_path": "tests/test_tasks.py", "name": name, "scenario": "create a task and check the response"}

test_.__test__ = False  # a helper, not a test


BASE = {
    "summary": "Add due_date and an overdue_only filter.",
    "root_cause_hypotheses": [{"description": "list_tasks filters only by status and page", "confidence": 0.8,
                               "evidence": [{"file_path": "app/services.py", "start_line": 2, "end_line": 3,
                                             "current_code_behavior": "list_tasks takes no due-date parameter"}]}],
    "affected_files": [
        {"path": "app/models.py", "operation": "modify", "reason": "store due date",
         "changes": [change("Task.due_date", "add")]},
        {"path": "app/services.py", "operation": "modify", "reason": "filter",
         "changes": [change("TaskService.list_tasks")]},
        {"path": "tests/test_tasks.py", "operation": "modify", "reason": "tests",
         "changes": [change("test_invalid_due_date_returns_422", "add"), change("test_overdue_only_filter", "add")]},
    ],
    "tests_for_criterion_1": [test_("test_invalid_due_date_returns_422")],
    "tests_for_criterion_2": [test_("test_overdue_only_filter")],
    "risks": ["Existing clients could break if due_date became required."],
    "assumptions": [],
    "dependencies": [],
}


def draft_with(schema_changes=None, **overrides):
    data = copy.deepcopy(BASE)
    if schema_changes is not None:
        data["affected_files"].insert(1, {"path": "app/schemas.py", "operation": "modify", "reason": "carry due date",
                                          "changes": schema_changes})
    data.update(overrides)
    return MODEL.model_validate(data)


@pytest.fixture
def index():
    root = tempfile.mkdtemp(prefix="related-")
    for rel, text in FILES.items():
        os.makedirs(os.path.join(root, os.path.dirname(rel)), exist_ok=True)
        with open(os.path.join(root, rel), "w", encoding="utf-8") as f:
            f.write(text)
    yield SymbolIndex.build(root, list(FILES))
    shutil.rmtree(root, ignore_errors=True)


def validate(draft, index):
    PlannerAgent.validate_draft(draft, index, CRITERIA)


# ---- the validator ----

def test_plan_missing_all_three_schema_classes_is_rejected_with_each_named(index):
    with pytest.raises(DraftIncomplete) as err:
        validate(draft_with(None), index)
    assert [m[0] for m in err.value.missing_related] == SCHEMA_CLASSES
    assert all(m[1] == "app/schemas.py" and m[2] == "Task.due_date" for m in err.value.missing_related)
    assert err.value.other_errors == []
    assert "TaskCreate (app/schemas.py), TaskUpdate (app/schemas.py), TaskResponse (app/schemas.py)" in str(err.value)


def test_plan_that_modifies_all_three_classes_passes(index):
    validate(draft_with([change(name) for name in SCHEMA_CLASSES]), index)


def test_plan_that_adds_fields_to_all_three_classes_passes(index):
    # The run-330 shape: the prompt asks for action='add' with 'Class.field' for new fields.
    validate(draft_with([change(f"{name}.due_date", "add") for name in SCHEMA_CLASSES]), index)


def test_a_member_of_a_different_class_with_the_same_prefix_does_not_count(index):
    # 'TaskCreateExtra.x' must not count as covering 'TaskCreate'.
    with pytest.raises(DraftIncomplete) as err:
        validate(draft_with([change("TaskCreateExtra.due_date", "add"), change("TaskUpdate"), change("TaskResponse")]),
                 index)
    assert [m[0] for m in err.value.missing_related] == ["TaskCreate"]


def test_assumption_still_rules_a_class_out(index):
    validate(draft_with([change("TaskCreate"), change("TaskUpdate")],
                        assumptions=["TaskResponse needs no change: it already returns every Task field."]), index)


# ---- the correction loop ----

def _plan(index, outputs, retries=2):
    ctx = [RetrievedContextChunk(file_path=p, start_line=1, end_line=len(t.split("\n")), content=t, score=1)
           for p, t in FILES.items()]
    with patch("app.agents.planner.LLMService") as llm_cls, patch("app.config.settings.MAX_LLM_RETRIES", retries), \
         patch("app.config.settings.PLAN_MAX_FILES", 4):
        llm = llm_cls.return_value
        llm.prompt_budget_chars.return_value = 60000
        llm.generate_structured.side_effect = outputs
        planner = PlannerAgent()
        try:
            result = planner.create_plan(ANALYSIS, ctx, "o/r", list(FILES), locations=[], symbol_index=index)
        except ValueError as e:
            result = e
    return planner, llm, result


def repair(**decisions):
    """A scripted answer to the targeted repair call."""
    model = related_class_repair_model(SCHEMA_CLASSES)
    return model.model_validate({related_class_field(name): d for name, d in decisions.items()})


def decision(needs_change=True, change="add an optional due_date field", details="optional datetime, ISO-8601",
             preserves="requests without due_date keep working"):
    return {"needs_change": needs_change, "change": change, "details": details, "preserves": preserves}


def test_correction_adds_the_missing_classes_with_one_small_call(index):
    answer = repair(TaskCreate=decision(), TaskUpdate=decision(),
                    TaskResponse=decision(change="return the due_date field"))
    planner, llm, plan = _plan(index, [draft_with(None), answer])

    assert not isinstance(plan, Exception), plan
    calls = llm.generate_structured.call_args_list
    assert len(calls) == 2
    repair_model = calls[1].args[2]
    assert set(repair_model.model_fields) == {related_class_field(n) for n in SCHEMA_CLASSES}
    repair_prompt = calls[1].args[1]
    assert "class TaskCreate(BaseModel)" in repair_prompt and "Task.due_date" in repair_prompt
    schemas = next(f for f in plan.affected_files if f.path == "app/schemas.py")
    assert schemas.symbols == SCHEMA_CLASSES
    assert any("return the due_date field" in c for c in schemas.planned_changes)
    # The valid parts of the first plan are kept as they were.
    assert [f.path for f in plan.affected_files] == ["app/models.py", "app/services.py", "tests/test_tasks.py",
                                                     "app/schemas.py"]
    assert plan.tests and plan.root_cause_hypotheses[0].evidence[0].claim == "list_tasks takes no due-date parameter"
    assert planner.attempt_errors[0].startswith("attempt 1 (initial)")


def test_correction_can_rule_a_class_out_as_an_explicit_assumption(index):
    answer = repair(TaskCreate=decision(), TaskUpdate=decision(),
                    TaskResponse=decision(needs_change=False, change="it already serializes every Task attribute"))
    _, _, plan = _plan(index, [draft_with(None), answer])
    assert "TaskResponse needs no change: it already serializes every Task attribute" in plan.assumptions
    schemas = next(f for f in plan.affected_files if f.path == "app/schemas.py")
    assert schemas.symbols == ["TaskCreate", "TaskUpdate"]


def test_correction_that_still_fails_stops_at_the_retry_limit(index):
    bad = repair(TaskCreate=decision(change="def due_date(self): return self._due"), TaskUpdate=decision(),
                 TaskResponse=decision())
    planner, llm, err = _plan(index, [draft_with(None), bad, draft_with(None)])

    assert isinstance(err, ValueError) and "failed after 3 attempts" in str(err)
    modes = [e.split(":")[0] for e in planner.attempt_errors]
    assert modes == ["attempt 1 (initial)", "attempt 2 (targeted repair)", "attempt 3 (regeneration)"]
    assert "contains code" in planner.attempt_errors[1]
    # The regeneration shows the model its previous (repaired) plan and what was wrong with it.
    regen_prompt = llm.generate_structured.call_args_list[2].args[1]
    assert "YOUR PREVIOUS PLAN (keep every part that was valid" in regen_prompt
    assert '"TaskUpdate"' in regen_prompt and "contains code" in regen_prompt


def test_other_errors_regenerate_with_the_previous_plan_instead_of_repairing(index):
    first = draft_with([change(name) for name in SCHEMA_CLASSES], dependencies=["TaskService"])
    _, llm, plan = _plan(index, [first, draft_with([change(name) for name in SCHEMA_CLASSES])])
    assert not isinstance(plan, Exception), plan
    second = llm.generate_structured.call_args_list[1]
    assert "tests_for_criterion_1" in second.args[2].model_fields  # the full plan schema, not the repair one
    assert "YOUR PREVIOUS PLAN (keep every part that was valid" in second.args[1]
