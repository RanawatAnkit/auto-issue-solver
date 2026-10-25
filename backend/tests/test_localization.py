"""Hint extraction, reference verification, the bounded localizer, plan grounding,
symbol-level context and LLM usage metering. No model is called: the LLM is scripted."""
import os
import json
import shutil
import tempfile
import pytest
from unittest.mock import MagicMock, patch
from app.services.symbol_index import SymbolIndex
from app.agents.context_builder import ContextBuilder, extract_hints, seed_locations
from app.agents.localizer import Localizer, verify_reference, HARD_MAX_ROUNDS
from app.agents.planner import PlannerAgent
from app.agents.schemas import (
    IssueAnalysis, LocalizationStep, ProposedLocation, SearchCall, SolutionPlan, FileChangePlan,
    RootCauseHypothesis, Evidence, CodeLocation, RetrievedContextChunk,
)
from app.services.llm_service import LLMService, track_llm_usage, summarize_llm_usage

SERVICE = (
    "class TaskService:\n"                                  # 1
    "    def list_tasks(self, user):\n"                     # 2
    "        return [t for t in self.tasks if t.user == user]\n"  # 3
    "\n"                                                    # 4
    "    def overdue_tasks(self, now):\n"                   # 5
    "        return [t for t in self.tasks if t.due > now]\n"  # 6  (bug: should be <)
    "\n"                                                    # 7
    "def format_task(task):\n"                              # 8
    "    return task.title\n"                               # 9
)
FILES = {
    "app/services.py": SERVICE,
    "app/routes.py": "from app.services import TaskService\n\ndef overdue_route(svc, now):\n    return svc.overdue_tasks(now)\n",
    "tests/test_services.py": "def test_overdue():\n    pass\n",
    "docs/notes.md": "notes\n",
}
ANALYSIS = IssueAnalysis(problem_summary="overdue tasks wrong", expected_behavior="past-due tasks listed",
                         current_behavior="future tasks listed", affected_areas=["app/services.py"])


@pytest.fixture
def checkout():
    root = tempfile.mkdtemp(prefix="loc-")
    for rel, text in FILES.items():
        full = os.path.join(root, rel)
        os.makedirs(os.path.dirname(full), exist_ok=True)
        with open(full, "w", encoding="utf-8") as f:
            f.write(text)
    yield root
    shutil.rmtree(root, ignore_errors=True)


@pytest.fixture
def index(checkout):
    return SymbolIndex.build(checkout, list(FILES))


class ScriptedLLM:
    """Stands in for LLMService: returns scripted LocalizationSteps (or raises them)."""
    def __init__(self, steps, budget=60000):
        self.steps, self.prompts, self.budget = list(steps), [], budget

    def prompt_budget_chars(self):
        return self.budget

    def generate_structured(self, system, user, model, **kwargs):
        self.prompts.append(user)
        step = self.steps.pop(0)
        if isinstance(step, Exception):
            raise step
        return step


def _loc(path, symbol="", **kw):
    return ProposedLocation(file_path=path, symbol=symbol, reason=kw.pop("reason", "r"), **kw)


# ---- deterministic hints ----

def test_extract_hints_finds_frames_paths_and_identifiers():
    text = ('Traceback:\n  File "/usr/lib/site-packages/app/services.py", line 6, in overdue_tasks\n'
            "Calling `TaskService.overdue_tasks(now)` in app/routes.py returns future tasks; format_task() is fine.")
    hints = extract_hints(text)
    assert hints.frames == [("/usr/lib/site-packages/app/services.py", 6, "overdue_tasks")]
    assert "app/routes.py" in hints.paths
    names = [n for n, _ in hints.identifiers]
    assert names[0] == "TaskService.overdue_tasks"     # code spans come first
    assert "format_task" in names
    assert "print" not in names and "self" not in names


def test_seed_locations_maps_traceback_and_mentions_to_verified_symbols(index):
    hints = extract_hints('File "/srv/site-packages/app/services.py", line 6, in overdue_tasks\n'
                          "see `format_task` and the made_up_helper function")
    seeds = seed_locations(index, hints)
    assert seeds[0].symbol == "TaskService.overdue_tasks"
    assert seeds[0].evidence[0].startswith("traceback frame")
    assert any(s.symbol == "format_task" for s in seeds)
    assert not any("made_up_helper" in s.symbol for s in seeds)
    assert all(s.snippet for s in seeds)


def test_ambiguous_names_need_a_hinted_file(index):
    many = [MagicMock(file_path=f"f{i}.py") for i in range(5)]
    with patch.object(index, "find_class", return_value=[]), patch.object(index, "find_function", return_value=many):
        assert seed_locations(index, extract_hints("call run() please")) == []


# ---- reference verification ----

@pytest.mark.parametrize("args,error", [
    (("app/missing.py",), "does not exist in the checkout"),
    (("../etc/passwd",), "Path Traversal"),
    (("app/.git/config",), "Git restriction"),
    (("app/services.py", "TaskService.made_up"), "does not exist in app/services.py"),
    (("app/services.py", "", 50, 60), "not a valid range"),
    (("docs/notes.md", "something"), "cannot be verified"),
    (("app/services.py", "format_task", 1, 3), "outside format_task"),
])
def test_verify_reference_rejects_what_the_checkout_does_not_contain(index, args, error):
    loc, reason = verify_reference(index, *args)
    assert loc is None and error in reason


def test_verify_reference_returns_authoritative_lines(index):
    loc, err = verify_reference(index, "./app/services.py", "overdue_tasks", 6, 6)
    assert err is None
    assert (loc.symbol, loc.kind, loc.start_line, loc.end_line) == ("TaskService.overdue_tasks", "method", 5, 6)
    loc, _ = verify_reference(index, "src/app/services.py")  # reconciled prefix
    assert (loc.file_path, loc.kind, loc.end_line) == ("app/services.py", "file", 10)


# ---- bounded localizer ----

def test_localizer_searches_then_reports_verified_location(index):
    llm = ScriptedLLM([
        LocalizationStep(analysis="look", searches=[SearchCall(tool="find_class", name="TaskService")]),
        LocalizationStep(analysis="found", done=True,
                         locations=[_loc("app/services.py", "TaskService.overdue_tasks", intended_behavior="use <")]),
    ])
    result = Localizer(index, llm).localize("Overdue wrong", "body", ANALYSIS)
    assert result.status == "localized" and result.rounds == 2
    (loc,) = result.locations
    assert (loc.symbol, loc.start_line, loc.end_line) == ("TaskService.overdue_tasks", 5, 6)
    assert "t.due > now" in loc.snippet and loc.intended_behavior == "use <"
    assert result.searches[0]["call"]["tool"] == "find_class" and result.searches[0]["found"]
    assert "def overdue_tasks(self, now):" in llm.prompts[1]  # search results reach the next round


def test_made_up_location_is_rejected_and_fed_back(index):
    llm = ScriptedLLM([
        LocalizationStep(analysis="guess", done=True, locations=[_loc("app/services.py", "TaskService.made_up")]),
        LocalizationStep(analysis="fixed", done=True, locations=[_loc("app/services.py", "overdue_tasks")]),
    ])
    result = Localizer(index, llm).localize("t", "b", ANALYSIS)
    assert result.status == "localized" and result.rounds == 2
    assert any("TaskService.made_up" in r for r in result.rejected)
    assert "were rejected" in llm.prompts[1] and "TaskService.made_up" in llm.prompts[1]


def test_rounds_never_exceed_the_hard_cap(index, monkeypatch):
    monkeypatch.setattr("app.config.settings.LOCALIZATION_MAX_ROUNDS", 50)  # cannot exceed the cap
    # Every round makes progress (a new search), so only the cap ends the loop.
    steps = [LocalizationStep(analysis="?", searches=[SearchCall(tool="find_text", text=f"t{i}")]) for i in range(10)]
    llm = ScriptedLLM(steps)
    result = Localizer(index, llm).localize("t", "b", ANALYSIS)
    assert result.status == "fallback" and result.locations == []
    assert result.rounds == HARD_MAX_ROUNDS == 5
    assert len(llm.prompts) == 5
    assert "final round" in llm.prompts[-1]


def test_round_limit_keeps_last_verified_locations(index, monkeypatch):
    monkeypatch.setattr("app.config.settings.LOCALIZATION_MAX_ROUNDS", 2)
    step = LocalizationStep(analysis="more", locations=[_loc("app/services.py", "format_task")],
                            searches=[SearchCall(tool="find_function", name="format_task")])
    result = Localizer(index, ScriptedLLM([step, step])).localize("t", "b", ANALYSIS)
    assert result.status == "localized" and [l.symbol for l in result.locations] == ["format_task"]
    assert all(s["round"] == 1 for s in result.searches)  # no searches run on the final round


def test_llm_failure_falls_back(index):
    result = Localizer(index, ScriptedLLM([RuntimeError("ollama down")])).localize("t", "b", ANALYSIS)
    assert result.status == "fallback" and "LLM call failed in round 1" in result.reason
    assert result.llm_error == "RuntimeError: ollama down"


def test_time_budget_falls_back(index, monkeypatch):
    monkeypatch.setattr("app.config.settings.LOCALIZATION_TIME_BUDGET_SECONDS", -1)
    llm = ScriptedLLM([])
    result = Localizer(index, llm).localize("t", "b", ANALYSIS)
    assert result.status == "fallback" and "time budget" in result.reason
    assert llm.prompts == []


def test_search_tools_validate_arguments_and_report_inheritance(checkout):
    with open(os.path.join(checkout, "app/special.py"), "w") as f:
        f.write("from app.services import TaskService\n\nclass Special(TaskService):\n    pass\n")
    idx = SymbolIndex.build(checkout, list(FILES) + ["app/special.py"])
    loc = Localizer(idx, ScriptedLLM([]))
    out, ok = loc._run_search(SearchCall(tool="find_method_in_class", name="overdue_tasks", class_name="Special"))
    assert ok and "inherited from TaskService" in out
    assert loc._run_search(SearchCall(tool="find_method_in_class", name="x"))[1] is False
    assert loc._run_search(SearchCall(tool="read_lines", file_path="../secret", start_line=1))[0].startswith("error")
    out, ok = loc._run_search(SearchCall(tool="symbols_in_file", file_path="app/services.py"))
    assert ok and "5-6 method TaskService.overdue_tasks" in out
    out, ok = loc._run_search(SearchCall(tool="find_text", text="t.due > now"))
    assert ok and "app/services.py:6 in method TaskService.overdue_tasks" in out


# ---- evidence-based plan validation ----

def _plan(symbols=None, new_symbols=None, evidence=None, path="app/services.py"):
    hyps = [RootCauseHypothesis(description="wrong comparison", evidence=evidence)] if evidence is not None else []
    return SolutionPlan(summary="s", root_cause_hypotheses=hyps, acceptance_criteria=["a"],
                        affected_files=[FileChangePlan(path=path, operation="modify", reason="r",
                                                       symbols=symbols or [], new_symbols=new_symbols or [])])


def _validate(plan, index, locations=(), shown=(("app/services.py", 1, 10),)):
    with patch("app.agents.planner.LLMService"):
        PlannerAgent().validate_plan(plan, list(FILES), symbol_index=index, locations=list(locations),
                                     shown_regions=list(shown))


LOC = CodeLocation(file_path="app/services.py", symbol="TaskService.overdue_tasks", kind="method", start_line=5, end_line=6)


def test_made_up_symbol_is_rejected(index):
    with pytest.raises(ValueError, match="symbol 'TaskService.archive' does not exist"):
        _validate(_plan(symbols=["TaskService.archive"], evidence=[Evidence(location_id="L1", claim="c")]), index, [LOC])


def test_new_symbols_and_qualified_normalization_are_accepted(index):
    plan = _plan(symbols=["overdue_tasks", "TaskService.archive"], new_symbols=["TaskService.archive"],
                 evidence=[Evidence(location_id="L1", claim="uses >")])
    _validate(plan, index, [LOC])
    assert plan.affected_files[0].symbols == ["TaskService.overdue_tasks"]
    ev = plan.root_cause_hypotheses[0].evidence[0]
    assert (ev.file_path, ev.start_line, ev.end_line) == ("app/services.py", 5, 6)


def test_evidence_must_exist_and_have_been_shown(index):
    with pytest.raises(ValueError, match="unknown location id 'L7'"):
        _validate(_plan(evidence=[Evidence(location_id="L7", claim="c")]), index, [LOC])
    with pytest.raises(ValueError, match="not a valid range"):
        _validate(_plan(evidence=[Evidence(file_path="app/services.py", start_line=90, end_line=95, claim="c")]), index)
    with pytest.raises(ValueError, match="was not shown"):
        _validate(_plan(evidence=[Evidence(file_path="app/routes.py", start_line=3, end_line=4, claim="c")]), index)
    with pytest.raises(ValueError, match="needs a location_id or a file_path"):
        _validate(_plan(evidence=[Evidence(claim="trust me")]), index)


def test_plan_without_evidence_is_rejected_when_grounding_is_available(index):
    with pytest.raises(ValueError, match="at least one hypothesis with evidence"):
        _validate(_plan(), index)


def test_valid_file_evidence_is_normalized(index):
    plan = _plan(evidence=[Evidence(file_path="./app/services.py", symbol="overdue_tasks", start_line=6, end_line=6, claim="c")])
    _validate(plan, index)
    ev = plan.root_cause_hypotheses[0].evidence[0]
    assert (ev.symbol, ev.start_line, ev.end_line) == ("TaskService.overdue_tasks", 5, 6)


def test_without_symbol_index_validation_is_unchanged(index):
    with patch("app.agents.planner.LLMService"):
        PlannerAgent().validate_plan(_plan(symbols=["anything"]), list(FILES))  # no grounding requirement


def _draft(symbol):
    from app.agents.schemas import plan_draft_model
    return plan_draft_model([ANALYSIS.expected_behavior], [1]).model_validate({
        "summary": "s", "risks": ["r"], "assumptions": [],
        "dependencies": [],
        "root_cause_hypotheses": [{"description": "d", "confidence": 0.8,
                                   "evidence": [{"location_id": "L1", "current_code_behavior": "it filters only by status and page"}]}],
        "affected_files": [
            {"path": "app/services.py", "operation": "modify", "reason": "r",
             "changes": [{"symbol": symbol, "action": "modify", "change": "c", "details": "d", "preserves": "p"}]},
            {"path": "tests/test_services.py", "operation": "modify", "reason": "r",
             "changes": [{"symbol": "test_overdue_excludes_future", "action": "add", "change": "c", "details": "d", "preserves": "p"}]}],
        "tests_for_criterion_1": [{"file_path": "tests/test_services.py", "name": "test_overdue_excludes_future", "scenario": "s"}],
    })


def test_planner_retries_after_made_up_symbol(index):
    bad, good = _draft("TaskService.archive"), _draft("TaskService.overdue_tasks")
    with patch("app.agents.planner.LLMService") as llm_cls:
        llm = llm_cls.return_value
        llm.prompt_budget_chars.return_value = 60000
        llm.generate_structured.side_effect = [bad, good]
        plan = PlannerAgent().create_plan(ANALYSIS, [], "o/r", list(FILES), locations=[LOC], symbol_index=index)
    assert plan.affected_files[0].symbols == ["TaskService.overdue_tasks"]
    retry_prompt = llm.generate_structured.call_args_list[1].args[1]
    assert "TaskService.archive" in retry_prompt and "[L1] app/services.py:5-6" in retry_prompt


# ---- symbol-level context ----

def test_large_file_context_uses_symbols_with_real_line_ranges(checkout):
    # Regression: large files were cut at 6000 characters with start_line=1, losing code further down.
    filler = "".join(f"def filler_{i}(x):\n    return x + {i}\n\n" for i in range(400))
    big = filler + "def compute_overdue_total(tasks):\n    return sum(t.cost for t in tasks if t.overdue)\n"
    with open(os.path.join(checkout, "app/big.py"), "w") as f:
        f.write(big)
    tracked = list(FILES) + ["app/big.py"]
    idx = SymbolIndex.build(checkout, tracked)
    builder = ContextBuilder(MagicMock())
    builder.retriever = None
    analysis = IssueAnalysis(problem_summary="compute_overdue_total wrong", expected_behavior="e",
                             current_behavior="c", affected_areas=["app/big.py"])
    ctx = builder.build_context(1, analysis, checkout, tracked, symbol_index=idx)
    chunk = next(c for c in ctx if c.symbol == "compute_overdue_total")
    assert chunk.start_line == 1201 and chunk.end_line == 1202
    assert "sum(t.cost" in chunk.content


def test_hint_seeds_are_included_and_exposed(index, checkout):
    builder = ContextBuilder(MagicMock())
    builder.retriever = None
    ctx = builder.build_context(1, ANALYSIS, checkout, list(FILES), symbol_index=index,
                                issue_text="`TaskService.overdue_tasks` returns future tasks")
    assert ctx[0].source == "hint" and ctx[0].symbol == "TaskService.overdue_tasks"
    assert (ctx[0].start_line, ctx[0].end_line) == (5, 6)
    assert builder.seed_locations[0].symbol == "TaskService.overdue_tasks"


# ---- usage metering ----

def _ollama_reply(content, **extra):
    resp = MagicMock()
    resp.raise_for_status = MagicMock()
    resp.json.return_value = {"message": {"content": content}, "done": True, **extra}
    return resp


@patch("app.services.llm_service.settings")
@patch("app.services.llm_service.httpx.post")
def test_usage_is_metered_per_attempt_including_failures(mock_post, mock_settings):
    for k, v in dict(LLM_PROVIDER="ollama", OLLAMA_MODEL="m", OLLAMA_BASE_URL="http://localhost:11434",
                     OLLAMA_TIMEOUT=60, OLLAMA_NUM_CTX=16384, OLLAMA_NUM_PREDICT=2048, MAX_LLM_RETRIES=1,
                     LLM_FALLBACK_PROVIDER=None, LLM_ALLOW_PAID_FALLBACK=False, LLM_FALLBACK_ON="quota").items():
        setattr(mock_settings, k, v)
    valid = json.dumps({"problem_summary": "p", "expected_behavior": "e", "current_behavior": "c"})
    mock_post.side_effect = [_ollama_reply("not json", prompt_eval_count=100, eval_count=5),
                             _ollama_reply(valid, prompt_eval_count=120, eval_count=40)]
    with track_llm_usage() as records:
        LLMService().generate_structured("sys", "user", IssueAnalysis)
    assert [r["success"] for r in records] == [False, True]
    summary = summarize_llm_usage(records)
    assert (summary["calls"], summary["failed_calls"], summary["prompt_tokens"], summary["completion_tokens"]) == (2, 1, 220, 45)
    assert summary["by_schema"]["IssueAnalysis"]["calls"] == 2


@patch("app.services.llm_service.settings")
@patch("app.services.llm_service.httpx.post")
def test_nothing_is_recorded_outside_a_tracking_block(mock_post, mock_settings):
    for k, v in dict(LLM_PROVIDER="ollama", OLLAMA_MODEL="m", OLLAMA_BASE_URL="http://x", OLLAMA_TIMEOUT=60,
                     OLLAMA_NUM_CTX=16384, OLLAMA_NUM_PREDICT=2048, MAX_LLM_RETRIES=0, LLM_FALLBACK_PROVIDER=None).items():
        setattr(mock_settings, k, v)
    mock_post.return_value = _ollama_reply(json.dumps({"problem_summary": "p", "expected_behavior": "e", "current_behavior": "c"}))
    with track_llm_usage() as outer:
        pass
    LLMService().generate_structured("sys", "user", IssueAnalysis)
    assert outer == []
