import os
import tempfile
import pytest
from unittest.mock import patch
from app.agents.coding_agent import CodingAgent
from app.agents.schemas import CodeChangePlan, CodeChange, TextEdit
from app.services.patch_service import PatchError


@pytest.fixture
def ws():
    with tempfile.TemporaryDirectory() as td:
        os.makedirs(os.path.join(td, "tests"))
        with open(os.path.join(td, "calc.py"), "w") as f:
            f.write("def add(a, b):\n    return a - b\n")
        with open(os.path.join(td, "tests", "conftest.py"), "w") as f:
            f.write("import pytest\n\n@pytest.fixture\ndef client():\n    return 'c'\n")
        yield td


def _modify(path, search, replace):
    return CodeChangePlan(changes=[CodeChange(file_path=path, operation="modify", edits=[TextEdit(search=search, replace=replace)])])


def test_invalid_edits_are_fed_back_and_retried(ws):
    agent = CodingAgent()
    responses = [_modify("calc.py", "return a * b", "return a + b"), _modify("calc.py", "return a - b", "return a + b")]
    with patch.object(agent, "generate_changes", side_effect=responses) as gen:
        metrics = agent.implement(ws, {"problem": "add"}, {"plan": "fix"}, ["calc.py"], ["calc.py", "tests/conftest.py"])
    assert metrics["changed_files"] == ["calc.py"]
    assert "not found" in gen.call_args_list[1].args[3]  # feedback passed on the retry
    with open(os.path.join(ws, "calc.py")) as f:
        assert "return a + b" in f.read()


def test_changes_outside_planned_files_are_rejected(ws, monkeypatch):
    monkeypatch.setattr("app.config.settings.MAX_LLM_RETRIES", 0)
    agent = CodingAgent()
    with patch.object(agent, "generate_changes", return_value=_modify("tests/conftest.py", "return 'c'", "return 'd'")):
        with pytest.raises(PatchError, match="outside the planned scope"):
            agent.implement(ws, {}, {}, ["calc.py"], ["calc.py", "tests/conftest.py"])


def test_files_are_edited_one_at_a_time_source_before_tests(ws):
    agent = CodingAgent()
    create_test = CodeChangePlan(changes=[CodeChange(file_path="tests/test_calc.py", operation="create",
                                                     content="from calc import add\n\ndef test_add():\n    assert add(1, 1) == 2\n")])
    fix = _modify("calc.py", "return a - b", "return a + b")
    with patch.object(agent, "generate_changes", side_effect=[fix, create_test]) as gen:
        metrics = agent.implement(ws, {}, {}, ["tests/test_calc.py", "calc.py"], ["calc.py", "tests/conftest.py"])
    assert metrics["changed_files"] == ["calc.py", "tests/test_calc.py"]
    assert "Change ONLY this file now: calc.py" in gen.call_args_list[0].args[2]
    second_prompt = gen.call_args_list[1].args[2]
    assert "Change ONLY this file now: tests/test_calc.py" in second_prompt
    assert "return a + b" in second_prompt  # the test step sees the already-updated source


def test_failure_on_a_later_file_rolls_back_earlier_files(ws, monkeypatch):
    monkeypatch.setattr("app.config.settings.MAX_LLM_RETRIES", 0)
    agent = CodingAgent()
    bad_test = _modify("tests/conftest.py", "no such text", "x")
    with patch.object(agent, "generate_changes", side_effect=[_modify("calc.py", "return a - b", "return a + b"), bad_test]):
        with pytest.raises(PatchError):
            agent.implement(ws, {}, {}, ["calc.py", "tests/conftest.py"], ["calc.py", "tests/conftest.py"])
    with open(os.path.join(ws, "calc.py")) as f:
        assert "return a - b" in f.read()  # first file restored


def test_new_test_file_gets_existing_conftest_as_context(ws):
    agent = CodingAgent()
    create = CodeChangePlan(changes=[CodeChange(file_path="tests/test_calc.py", operation="create",
                                                content="def test_add(client):\n    assert client == 'c'\n")])
    with patch.object(agent, "generate_changes", return_value=create) as gen:
        agent.implement(ws, {}, {}, ["tests/test_calc.py"], ["calc.py", "tests/conftest.py"])
    context = gen.call_args.args[2]
    assert "def client():" in context  # fixtures that really exist are shown to the model


def _big_module(path, filler_functions=400):
    filler = "".join(f"def helper_{i}(x):\n    return x + {i}\n\n\n" for i in range(filler_functions))
    with open(path, "w", newline="\n") as f:
        f.write("import os\n\n\n" + filler +
                "class Blueprint:\n    def __init__(self, name):\n        self.name = name\n\n"
                "    def other(self):\n        return 1\n\n\n" + filler.replace("helper_", "tail_"))


def test_excerpt_shows_named_symbol_and_marks_omissions(ws):
    from app.agents.prompting import excerpt_block
    _big_module(os.path.join(ws, "blueprints.py"))
    block = excerpt_block(ws, "blueprints.py", ["Blueprint.__init__"], 4000)
    assert "def __init__(self, name):" in block and "import os" in block
    assert "not shown" in block and "def helper_200" not in block
    assert len(block) < 4500


def test_large_target_file_is_edited_from_an_excerpt(ws):
    """Files over the prompt budget used to be refused outright (NO_PATCH on most real repos)."""
    _big_module(os.path.join(ws, "blueprints.py"), filler_functions=3000)
    agent = CodingAgent()
    plan = {"affected_files": [{"path": "blueprints.py", "operation": "modify", "reason": "validate name",
                                "symbols": ["Blueprint.__init__"], "planned_changes": []}]}
    fix = CodeChangePlan(changes=[CodeChange(file_path="blueprints.py", operation="modify", edits=[TextEdit(
        symbol="Blueprint.__init__",
        replace="def __init__(self, name):\n    if '.' in name:\n        raise ValueError('no dots')\n    self.name = name\n")])])
    with patch.object(agent, "generate_changes", return_value=fix) as gen:
        metrics = agent.implement(ws, {}, plan, ["blueprints.py"], ["blueprints.py", "calc.py"])
    prompt = gen.call_args_list[0].args[2]
    assert "EXCERPT" in prompt and "def __init__(self, name):" in prompt
    assert len(prompt) < agent.llm.prompt_budget_chars()
    assert metrics["changed_files"] == ["blueprints.py"]
    with open(os.path.join(ws, "blueprints.py")) as f:
        text = f.read()
    assert "raise ValueError('no dots')" in text and "def tail_2999(x):" in text  # rest of the file intact


def test_step_schema_bounds_lists_so_constrained_decoding_terminates():
    """Unbounded lists let small models repeat one edit until the token limit."""
    from app.agents.schemas import MAX_STEP_EDITS, single_file_change_plan
    schema = single_file_change_plan("calc.py", "modify").model_json_schema()
    assert schema["properties"]["changes"]["maxItems"] == 1
    assert schema["$defs"]["CodeChange"]["properties"]["edits"]["maxItems"] == MAX_STEP_EDITS
