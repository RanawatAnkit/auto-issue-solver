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
