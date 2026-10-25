"""Regression tests for run 316: a feature request where the localizer searched for symbols the issue
asks to add, burned rounds on empty responses, fell back, and the planner then cited location ids
that did not exist. No model is called: the LLM is scripted."""
import os
import json
import shutil
import tempfile
import pytest
from unittest.mock import patch
from app.services.symbol_index import SymbolIndex
from app.agents.context_builder import extract_hints, seed_locations, route_handlers
from app.agents.localizer import Localizer
from app.agents.planner import PlannerAgent
from app.agents.schemas import (
    IssueAnalysis, LocalizationStep, ProposedLocation, SearchCall, SolutionPlan, FileChangePlan,
    RootCauseHypothesis, Evidence, RetrievedContextChunk,
)

TASKFLOW = {
    "app/models.py": "from dataclasses import dataclass\n\n@dataclass\nclass Task:\n    title: str\n    status: str = 'todo'\n\n    def touch(self):\n        pass\n",
    "app/services.py": (
        "class TaskService:\n"                                                   # 1
        "    def __init__(self, repository):\n"                                  # 2
        "        self.repository = repository\n"                                 # 3
        "\n"                                                                     # 4
        "    def list_tasks(self, status=None, priority=None, page=1, page_size=20):\n"  # 5
        "        tasks = self.repository.list()\n"                               # 6
        "        return tasks[(page - 1) * page_size:page * page_size], len(tasks)\n"  # 7
        "\n"                                                                     # 8
        "    def get(self, task_id):\n"                                          # 9
        "        return self.repository.get(task_id)\n"                          # 10
    ),
    "app/routes.py": (
        "from fastapi import APIRouter\n"
        "router = APIRouter(prefix='/tasks', tags=['tasks'])\n"
        "\n"
        "@router.get('', response_model=list)\n"
        "def list_tasks(status=None, page: int = 1, page_size: int = 20):\n"
        "    return []\n"
        "\n"
        "@router.get('/{task_id}')\n"
        "def get_task(task_id: str):\n"
        "    return None\n"
        "\n"
        "@router.post('')\n"
        "def create_task(payload):\n"
        "    return payload\n"
    ),
    "tests/test_tasks.py": "def test_create_and_get_task():\n    pass\n",
}
ISSUE = ("Add due-date support and overdue filtering for tasks\n"
         "Add an optional due_date field. Add an overdue_only boolean query parameter to GET /tasks. "
         "Compose this filter with existing status, priority, page, and page_size filters. "
         "Store the due date on the domain Task model.")
ANALYSIS = IssueAnalysis(problem_summary="add due dates and overdue filter", expected_behavior="overdue filter",
                         current_behavior="no due dates", affected_areas=[])


def _loc(path, symbol=""):
    return ProposedLocation(file_path=path, symbol=symbol, reason="r")


# What the model actually did in run 316 (from the run's localization_round_* actions).
FUTURE_SEARCHES = [SearchCall(tool="find_method_in_class", name="add_due_date", class_name="Task"),
                   SearchCall(tool="find_method_in_class", name="get_overdue_tasks", class_name="TaskService"),
                   SearchCall(tool="find_method_in_class", name="get_tasks", class_name="TaskController")]
INVALID = [_loc("app/models.py", "Task.due_date"), _loc("app/services.py", "TaskService.filter_overdue_tasks"),
           _loc("app/controllers.py", "TaskController.get_tasks")]


class ScriptedLLM:
    def __init__(self, steps):
        self.steps, self.prompts = list(steps), []

    def prompt_budget_chars(self):
        return 60000

    def generate_structured(self, system, user, model, **kwargs):
        self.prompts.append(user)
        return self.steps.pop(0)


@pytest.fixture
def taskflow():
    root = tempfile.mkdtemp(prefix="taskflow-")
    for rel, text in TASKFLOW.items():
        full = os.path.join(root, rel)
        os.makedirs(os.path.dirname(full), exist_ok=True)
        with open(full, "w", encoding="utf-8") as f:
            f.write(text)
    yield SymbolIndex.build(root, list(TASKFLOW))
    shutil.rmtree(root, ignore_errors=True)


def _seeds(idx):
    return seed_locations(idx, extract_hints(ISSUE))


# ---- deterministic seeding ----

def test_seeds_find_existing_route_service_and_model_for_a_feature_request(taskflow):
    seeds = {s.symbol: s.evidence for s in _seeds(taskflow)}
    assert "handles `GET /tasks` named in the issue" in seeds["list_tasks"]
    assert any("page_size" in e for e in seeds["TaskService.list_tasks"])
    assert "Task" in seeds
    assert "get_task" not in seeds and "create_task" not in seeds  # other path / method


def test_route_matching_supports_flask_style_routes(tmp_path):
    (tmp_path / "views.py").write_text(
        "@app.route('/items/<int:item_id>', methods=['DELETE'])\ndef remove(item_id):\n    pass\n\n"
        "@app.route('/items')\ndef items():\n    pass\n")
    idx = SymbolIndex.build(str(tmp_path), ["views.py"])
    assert [s.qualname for s in route_handlers(idx, "DELETE", "/items/{id}")] == ["remove"]
    assert [s.qualname for s in route_handlers(idx, "GET", "/items")] == ["items"]
    assert route_handlers(idx, "POST", "/items") == []


# ---- the localization loop ----

def test_run_316_pattern_stops_early_and_uses_verified_seeds(taskflow):
    empty = LocalizationStep(analysis="")
    llm = ScriptedLLM([
        LocalizationStep(analysis="search future names", searches=FUTURE_SEARCHES),
        LocalizationStep(analysis="guess", locations=INVALID),  # no searches, invalid, done=false
        empty, empty, empty,                                     # rounds 3-5 of run 316
    ])
    result = Localizer(taskflow, llm).localize("Add due-date support", ISSUE, ANALYSIS, _seeds(taskflow))

    assert len(llm.prompts) == 3  # stalled twice, then stopped (run 316 spent all 5 rounds)
    assert result.status == "seeded"
    assert {l.symbol for l in result.locations} >= {"list_tasks", "TaskService.list_tasks", "Task"}
    assert all(taskflow.resolve(l.symbol, l.file_path) for l in result.locations)
    # Misses explain themselves and point at existing code.
    assert "no class named 'TaskController' exists" in llm.prompts[1]
    assert "Closest existing" in llm.prompts[1] and "TaskService.list_tasks" in llm.prompts[1]
    # The first prompt already lists what exists, including the route found from `GET /tasks`.
    assert "TaskService.list_tasks (5-7)" in llm.prompts[0]
    assert "handles `GET /tasks`" in llm.prompts[0]


def test_rejections_persist_across_rounds(taskflow):
    llm = ScriptedLLM([
        LocalizationStep(analysis="guess", locations=INVALID),
        LocalizationStep(analysis="search", searches=[SearchCall(tool="find_class", name="TaskService")]),
        LocalizationStep(analysis="ok", done=True, locations=[_loc("app/services.py", "TaskService.list_tasks")]),
    ])
    result = Localizer(taskflow, llm).localize("t", ISSUE, ANALYSIS)
    assert result.status == "localized" and [l.symbol for l in result.locations] == ["TaskService.list_tasks"]
    # Round 2 produced no rejections of its own; round 3 still sees round 1's.
    assert "Task.due_date" in llm.prompts[2] and "do not report these again" in llm.prompts[2]
    assert "closest:" in llm.prompts[1]


def test_empty_model_responses_fall_back_without_seeds(taskflow):
    llm = ScriptedLLM([LocalizationStep(analysis="")] * 5)
    result = Localizer(taskflow, llm).localize("t", "b", ANALYSIS)
    assert result.status == "fallback" and result.locations == []
    assert result.rounds == 2 and len(llm.prompts) == 2 and "no progress" in result.reason
    assert "no new searches and no verifiable location" in llm.prompts[1]


def test_one_stall_is_retried_then_recovers(taskflow):
    llm = ScriptedLLM([LocalizationStep(analysis=""),
                       LocalizationStep(analysis="ok", done=True, locations=[_loc("app/routes.py", "list_tasks")])])
    result = Localizer(taskflow, llm).localize("t", "b", ANALYSIS)
    assert result.status == "localized" and result.locations[0].symbol == "list_tasks"


def test_duplicate_only_searches_count_as_no_progress(taskflow):
    same = LocalizationStep(analysis="again", searches=[SearchCall(tool="find_class", name="Task")])
    llm = ScriptedLLM([same] * 5)
    result = Localizer(taskflow, llm).localize("t", "b", ANALYSIS)
    assert result.rounds == 3 and result.status == "fallback"  # 1 useful round, then 2 duplicate-only ones


def test_time_budget_stops_before_a_round_that_would_overrun(taskflow, monkeypatch):
    clock = {"t": 0.0}
    monkeypatch.setattr("app.agents.localizer.time.monotonic", lambda: clock["t"])
    monkeypatch.setattr("app.config.settings.LOCALIZATION_TIME_BUDGET_SECONDS", 250)

    class SlowLLM(ScriptedLLM):
        def generate_structured(self, system, user, model, **kwargs):
            clock["t"] += 100  # each model call takes 100 s
            return super().generate_structured(system, user, model, **kwargs)

    steps = [LocalizationStep(analysis="?", searches=[SearchCall(tool="find_text", text=f"x{i}")]) for i in range(5)]
    llm = SlowLLM(steps)
    result = Localizer(taskflow, llm).localize("t", "b", ANALYSIS, _seeds(taskflow))
    # Round 3 would start at 200 s and is projected to end at 300 s > 250 s, so it never starts.
    assert len(llm.prompts) == 2 and result.rounds == 2
    assert "time budget" in result.reason and result.status == "seeded"


def test_unknown_keys_in_model_output_are_rejected_not_dropped():
    from pydantic import ValidationError
    from app.services.llm_service import parse_structured_output, LLMOutputError
    raw = {"analysis": "a", "search_calls": [{"tool": "find_class", "name": "Task"}]}
    with pytest.raises(ValidationError):
        LocalizationStep.model_validate(raw)
    with pytest.raises(LLMOutputError):
        parse_structured_output(json.dumps(raw), LocalizationStep)


# ---- the planner: run 316 cited 'L1'-'L4' although localization produced no locations ----

def _plan(evidence):
    return SolutionPlan(summary="s", acceptance_criteria=["a"],
                        root_cause_hypotheses=[RootCauseHypothesis(description="d", evidence=evidence)],
                        affected_files=[FileChangePlan(path="app/services.py", operation="modify", reason="r")])


def _validate(plan, idx):
    with patch("app.agents.planner.LLMService"):
        PlannerAgent().validate_plan(plan, list(TASKFLOW), symbol_index=idx, locations=[],
                                     shown_regions=[("app/services.py", 1, 10)])


def test_planner_prompt_offers_location_ids_only_when_locations_exist(taskflow):
    from app.agents.schemas import SolutionPlanDraft
    from app.agents.schemas import plan_draft_model
    good = plan_draft_model([ANALYSIS.expected_behavior], [1]).model_validate({
        "summary": "s", "risks": ["r"], "assumptions": [], "dependencies": [],
        "root_cause_hypotheses": [{"description": "d", "confidence": 0.7, "evidence": [
            {"file_path": "app/services.py", "start_line": 5, "end_line": 7, "current_code_behavior": "it filters only by status and page"}]}],
        "affected_files": [{"path": "tests/test_tasks.py", "operation": "modify", "reason": "r", "changes": [
            {"symbol": "test_overdue", "action": "add", "change": "c", "details": "d", "preserves": "p"}]}],
        "tests_for_criterion_1": [{"file_path": "tests/test_tasks.py", "name": "test_overdue", "scenario": "s"}],
    })
    ctx = [RetrievedContextChunk(file_path="app/services.py", start_line=1, end_line=10,
                                 content=TASKFLOW["app/services.py"], score=1)]
    with patch("app.agents.planner.LLMService") as llm_cls:
        llm = llm_cls.return_value
        llm.prompt_budget_chars.return_value = 60000
        llm.generate_structured.return_value = good
        PlannerAgent().create_plan(ANALYSIS, ctx, "o/r", list(TASKFLOW), locations=[], symbol_index=taskflow)
    system = llm.generate_structured.call_args.args[0]
    assert "'L1'" not in system and "leave location_id empty" in system


def test_unknown_location_id_with_a_real_reference_is_verified_by_path(taskflow):
    plan = _plan([Evidence(location_id="L1", file_path="app/services.py", start_line=5, end_line=7, claim="c")])
    _validate(plan, taskflow)
    ev = plan.root_cause_hypotheses[0].evidence[0]
    assert ev.location_id == "" and (ev.file_path, ev.start_line, ev.end_line) == ("app/services.py", 5, 7)


def test_unknown_location_id_alone_gets_actionable_feedback(taskflow):
    with pytest.raises(ValueError, match=r"there are no location ids\); cite a file_path"):
        _validate(_plan([Evidence(location_id="L1", claim="c")]), taskflow)
