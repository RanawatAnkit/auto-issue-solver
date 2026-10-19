"""Regression tests for the Run 303 coding failures (scope violation, invalid
signature, mismatched search block, then "No valid change ... after 3 attempts")."""
import os
import hashlib
import tempfile
import pytest
from pydantic import ValidationError
from unittest.mock import patch
from app.agents.coding_agent import CodingAgent, NoValidPatchError
from app.agents.schemas import CodeChangePlan, CodeChange, TextEdit, single_file_change_plan
from app.services.patch_service import (
    PatchService, PatchError, PatchSyntaxError, ContextMismatchError, ScopeViolationError,
)

SERVICES = '''from datetime import datetime


class TaskService:
    def __init__(self, repository):
        self.repository = repository

    def list_tasks(
        self,
        status=None,
        priority=None,
        page: int = 1,
        page_size: int = 20,
    ) -> tuple[list, int]:
        tasks = self.repository.list()
        if status is not None:
            tasks = [t for t in tasks if t.status == status]
        total = len(tasks)
        start = (page - 1) * page_size
        return tasks[start:start + page_size], total
'''

ROUTES = '''from app.services import TaskService


def list_tasks(service: TaskService, page: int = 1):
    return service.list_tasks(page=page)
'''

NEW_LIST_TASKS = '''def list_tasks(
    self,
    status=None,
    priority=None,
    page: int = 1,
    page_size: int = 20,
    overdue_only: bool = False,
) -> tuple[list, int]:
    tasks = self.repository.list()
    if status is not None:
        tasks = [t for t in tasks if t.status == status]
    if overdue_only:
        now = datetime.now()
        tasks = [t for t in tasks if t.due_date is not None and t.due_date < now and t.status != "done"]
    total = len(tasks)
    start = (page - 1) * page_size
    return tasks[start:start + page_size], total'''

BAD_SIGNATURE = '''def list_tasks(due_date: datetime | None = None, self, status=None, page: int = 1, page_size: int = 20):
    return [], 0'''


@pytest.fixture
def repo():
    with tempfile.TemporaryDirectory() as td:
        os.makedirs(os.path.join(td, "app"))
        for rel, content in {"app/services.py": SERVICES, "app/routes.py": ROUTES}.items():
            with open(os.path.join(td, rel), "w", newline="") as f:
                f.write(content)
        yield td


def _digest(root):
    out = {}
    for r, _, files in os.walk(root):
        for name in files:
            p = os.path.join(r, name)
            with open(p, "rb") as f:
                out[os.path.relpath(p, root)] = hashlib.sha256(f.read()).hexdigest()
    return out


def _read(root, rel):
    with open(os.path.join(root, rel), newline="") as f:
        return f.read()


def _symbol_edit(path, symbol, replace):
    return CodeChangePlan(changes=[CodeChange(file_path=path, operation="modify",
                                              edits=[TextEdit(symbol=symbol, replace=replace)])])


def _search_edit(path, search, replace):
    return CodeChangePlan(changes=[CodeChange(file_path=path, operation="modify",
                                              edits=[TextEdit(search=search, replace=replace)])])


# 1. A valid Python method-signature change ---------------------------------------

def test_valid_method_signature_change_via_symbol(repo):
    PatchService(repo).apply_plan(_symbol_edit("app/services.py", "TaskService.list_tasks", NEW_LIST_TASKS))
    text = _read(repo, "app/services.py")
    assert "        overdue_only: bool = False," in text  # re-indented to method level
    assert "    def __init__(self, repository):" in text  # neighbours untouched
    compile(text, "services.py", "exec")


def test_valid_method_signature_change_via_exact_search(repo):
    PatchService(repo).apply_plan(_search_edit(
        "app/services.py", "        page_size: int = 20,\n    ) -> tuple[list, int]:",
        "        page_size: int = 20,\n        overdue_only: bool = False,\n    ) -> tuple[list, int]:"))
    assert "overdue_only: bool = False" in _read(repo, "app/services.py")


# 2. Invalid signature: default argument before self ------------------------------

def test_invalid_signature_is_rejected_with_error_and_lines(repo):
    before = _digest(repo)
    with pytest.raises(PatchSyntaxError) as exc:
        PatchService(repo).apply_plan(_symbol_edit("app/services.py", "TaskService.list_tasks", BAD_SIGNATURE))
    msg = str(exc.value)
    assert ("non-default argument follows default argument" in msg or
            "parameter without a default follows parameter with a default" in msg)
    assert "def list_tasks(due_date" in msg  # the offending source line is quoted back
    assert "original file was left unchanged" in msg
    assert _digest(repo) == before


# 3. Search block that does not match the source ----------------------------------

def test_mismatched_search_block_is_rejected_with_real_text_and_symbol_hint(repo):
    before = _digest(repo)
    with pytest.raises(ContextMismatchError) as exc:
        # Incomplete block that omits surrounding parameters (as in Run 303 attempt 3).
        PatchService(repo).apply_plan(_search_edit(
            "app/services.py", "status: TaskStatus | None = None,\npage: int = 1,", "x"))
    msg = str(exc.value)
    # The closest real region is quoted with surrounding lines, so a complete block can be copied.
    assert "        status=None,\n        priority=None,\n        page: int = 1," in msg
    assert "'symbol'" in msg
    assert _digest(repo) == before


def test_unknown_symbol_lists_available_names(repo):
    with pytest.raises(ContextMismatchError, match="TaskService.list_tasks"):
        PatchService(repo).apply_plan(_symbol_edit("app/services.py", "TaskService.list", "def list(self): pass"))


# 4. File outside the authorized scope --------------------------------------------

def test_step_schema_only_admits_the_authorized_file():
    step = single_file_change_plan("app/services.py", "modify")
    with pytest.raises(ValidationError):
        step.model_validate({"changes": [{"file_path": "app/routes.py", "operation": "modify", "edits": []}]})
    schema = step.model_json_schema()["$defs"]["CodeChange"]["properties"]["file_path"]
    assert schema["enum"] == ["app/services.py"]  # enforced by schema-constrained decoding


def test_out_of_scope_change_is_rejected_and_nothing_applied(repo, monkeypatch):
    monkeypatch.setattr("app.config.settings.MAX_LLM_RETRIES", 0)
    agent = CodingAgent()
    before = _digest(repo)
    out_of_scope = _search_edit("app/routes.py", "page: int = 1", "page: int = 2")
    with patch.object(agent, "generate_changes", return_value=out_of_scope):
        with pytest.raises(NoValidPatchError, match="SCOPE_VIOLATION"):
            agent.implement(repo, {}, {}, ["app/services.py"], ["app/services.py", "app/routes.py"])
    assert _digest(repo) == before


# 5 & 6. Multi-file / multi-edit failures leave the originals unchanged ----------------

def test_multi_file_patch_with_one_bad_edit_writes_nothing(repo):
    before = _digest(repo)
    plan = CodeChangePlan(changes=[
        CodeChange(file_path="app/services.py", operation="modify",
                   edits=[TextEdit(symbol="TaskService.list_tasks", replace=NEW_LIST_TASKS)]),
        CodeChange(file_path="app/routes.py", operation="modify",
                   edits=[TextEdit(search="does not exist", replace="x")]),
    ])
    with pytest.raises(ContextMismatchError):
        PatchService(repo).apply_plan(plan)
    assert _digest(repo) == before


def test_multi_edit_change_with_one_bad_edit_writes_nothing(repo):
    before = _digest(repo)
    plan = CodeChangePlan(changes=[CodeChange(file_path="app/services.py", operation="modify", edits=[
        TextEdit(symbol="TaskService.list_tasks", replace=NEW_LIST_TASKS),
        TextEdit(search="no such line", replace="x"),
    ])])
    with pytest.raises(ContextMismatchError):
        PatchService(repo).apply_plan(plan)
    assert _digest(repo) == before


def test_failure_on_later_file_restores_files_already_changed(repo, monkeypatch):
    monkeypatch.setattr("app.config.settings.MAX_LLM_RETRIES", 0)
    agent = CodingAgent()
    before = _digest(repo)
    responses = [_symbol_edit("app/services.py", "TaskService.list_tasks", NEW_LIST_TASKS),
                 _search_edit("app/routes.py", "not in the file", "x")]
    with patch.object(agent, "generate_changes", side_effect=responses):
        with pytest.raises(NoValidPatchError):
            agent.implement(repo, {}, {}, ["app/services.py", "app/routes.py"], ["app/services.py", "app/routes.py"])
    assert _digest(repo) == before  # app/services.py was applied, then rolled back


# 7. Retry with validation feedback -----------------------------------------------

def test_retry_receives_category_error_and_rejected_answer(repo):
    agent = CodingAgent()
    bad = _symbol_edit("app/services.py", "TaskService.list_tasks", BAD_SIGNATURE)
    good = _symbol_edit("app/services.py", "TaskService.list_tasks", NEW_LIST_TASKS)
    with patch.object(agent, "generate_changes", side_effect=[bad, good]) as gen:
        metrics = agent.implement(repo, {}, {}, ["app/services.py"], ["app/services.py", "app/routes.py"])
    assert metrics["changed_files"] == ["app/services.py"]
    feedback = gen.call_args_list[1].args[3]
    assert "[SYNTAX_ERROR]" in feedback
    assert ("non-default argument follows default argument" in feedback or
            "parameter without a default follows parameter with a default" in feedback)
    assert "due_date: datetime | None = None, self" in feedback  # its own rejected answer
    assert "Nothing from that answer was applied" in feedback
    # Each step is generated against a schema restricted to the target file.
    step_model = gen.call_args_list[0].kwargs["response_model"]
    assert step_model.model_json_schema()["$defs"]["CodeChange"]["properties"]["file_path"]["enum"] == ["app/services.py"]


def test_identical_repeated_patch_is_not_retried_blindly(repo, monkeypatch):
    monkeypatch.setattr("app.config.settings.MAX_LLM_RETRIES", 1)
    agent = CodingAgent()
    bad = _search_edit("app/services.py", "nope", "x")
    with patch.object(agent, "generate_changes", side_effect=[bad, bad]):
        with pytest.raises(NoValidPatchError) as exc:
            agent.implement(repo, {}, {}, ["app/services.py"], ["app/services.py"])
    assert "DUPLICATE_PATCH" in str(exc.value)


def test_plan_entry_for_the_file_is_shown_in_the_step_prompt(repo):
    agent = CodingAgent()
    plan = {"affected_files": [{"path": "app/services.py", "operation": "modify", "reason": "Filter overdue tasks",
                                "symbols": ["TaskService.list_tasks"], "planned_changes": ["add overdue_only"]}]}
    with patch.object(agent, "generate_changes", return_value=_symbol_edit("app/services.py", "TaskService.list_tasks", NEW_LIST_TASKS)) as gen:
        agent.implement(repo, {}, plan, ["app/services.py"], ["app/services.py"])
    prompt = gen.call_args.args[2]
    assert "Why: Filter overdue tasks" in prompt and "- add overdue_only" in prompt and "TaskService.list_tasks" in prompt


# 8. Multiple explicitly authorized files -----------------------------------------

def test_multiple_authorized_files_are_all_changed(repo):
    agent = CodingAgent()
    responses = [
        _symbol_edit("app/services.py", "TaskService.list_tasks", NEW_LIST_TASKS),
        _symbol_edit("app/routes.py", "list_tasks",
                     "def list_tasks(service: TaskService, page: int = 1, overdue_only: bool = False):\n"
                     "    return service.list_tasks(page=page, overdue_only=overdue_only)"),
    ]
    with patch.object(agent, "generate_changes", side_effect=responses) as gen:
        metrics = agent.implement(repo, {}, {}, ["app/services.py", "app/routes.py"], ["app/services.py", "app/routes.py"])
    assert metrics["changed_files"] == ["app/services.py", "app/routes.py"]  # plan order, one step each
    assert "overdue_only=overdue_only" in _read(repo, "app/routes.py")
    assert "overdue_only: bool = False" in _read(repo, "app/services.py")
    assert [c.kwargs["response_model"].model_json_schema()["$defs"]["CodeChange"]["properties"]["file_path"]["enum"][0]
            for c in gen.call_args_list] == ["app/services.py", "app/routes.py"]


# 9. No valid patch within the retry limit ----------------------------------------

def test_run_303_sequence_ends_with_accurate_failure(repo, monkeypatch):
    """Replays Run 303: scope violation, invalid signature, mismatched search."""
    monkeypatch.setattr("app.config.settings.MAX_LLM_RETRIES", 2)
    agent = CodingAgent()
    before = _digest(repo)
    responses = [
        _search_edit("app/routes.py", "page: int = 1", "page: int = 1, overdue_only: bool = False"),
        _symbol_edit("app/services.py", "TaskService.list_tasks", BAD_SIGNATURE),
        _search_edit("app/services.py", "status: TaskStatus | None = None,\npage: int = 1,", "x"),
    ]
    with patch.object(agent, "generate_changes", side_effect=responses):
        with pytest.raises(NoValidPatchError) as exc:
            agent.implement(repo, {}, {}, ["app/services.py"], ["app/services.py", "app/routes.py"])
    err = exc.value
    assert len(err.failures) == 3
    assert [f.split("[")[1].split("]")[0] for f in err.failures] == ["SCOPE_VIOLATION", "SYNTAX_ERROR", "CONTEXT_MISMATCH"]
    assert _digest(repo) == before
