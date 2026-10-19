import os
import tempfile
import pytest
from unittest.mock import patch, MagicMock
from app.agents.schemas import FailureAnalysis, RepairPlan
from app.agents.failure_analyzer import FailureAnalyzer
from app.agents.repair_planner import RepairPlanner
from app.workflows.repair_graph import execute_repair_loop, failure_signature


def test_failure_analysis_schema(monkeypatch):
    captured = {}
    def mock_generate(self, system, user, schema):
        captured["user"] = user
        return schema(
            failure_type="ASSERTION_ERROR", root_cause="Mocked", affected_files=["app.py"], affected_symbols=["func"],
            evidence=["Err"], suggested_fix="Fix", confidence=0.9, unresolved_questions=[]
        )
    monkeypatch.setattr("app.services.llm_service.LLMService.generate_structured", mock_generate)

    agent = FailureAnalyzer()
    long_output = "session banner\n" + ("noise\n" * 5000) + "E   AssertionError: expected 2 got 0\n1 failed"
    res = agent.generate_analysis("issue", "plan", "changes", long_output)
    assert res.failure_type == "ASSERTION_ERROR"
    assert res.root_cause == "Mocked"
    # Regression: the analyzer used to see only the first 4000 characters (the banner);
    # the traceback at the end of the output must reach the model.
    assert "AssertionError: expected 2 got 0" in captured["user"]

def test_repair_planner_schema(monkeypatch):
    def mock_generate(self, system, user, schema):
        return schema(
            root_cause="Mocked", affected_files=["app.py"], changes=["Patch abc"],
            tests_to_run=["test_app"], reasoning="Due to Y", confidence=0.88
        )
    monkeypatch.setattr("app.services.llm_service.LLMService.generate_structured", mock_generate)

    agent = RepairPlanner()
    res = agent.generate_plan("analysis", "plan", "context")
    assert len(res.changes) == 1

def test_failure_type_and_confidence_are_normalized():
    fa = FailureAnalysis(failure_type="assertion error", root_cause="r", suggested_fix="s", confidence=3)
    assert fa.failure_type == "ASSERTION_ERROR"
    assert fa.confidence == 1.0
    assert FailureAnalysis(failure_type="weird", root_cause="r", suggested_fix="s").failure_type == "UNKNOWN"


# ---------------------------------------------------------------------------
# Repair loop: real control flow, mocked LLM agents and sandbox
# ---------------------------------------------------------------------------

def _failing(test_id: str, message: str = "AssertionError: boom") -> dict:
    out = f"F\n=== FAILURES ===\nE   {message}\nFAILED {test_id} - {message}\n1 failed in 0.1s"
    return {"success": False, "outcome": "TESTS_FAILED", "exit_code": 1, "stdout": out, "stderr": "",
            "verification": {"valid": True}}

PASSING = {"success": True, "outcome": "PASSED", "exit_code": 0, "stdout": "1 passed", "stderr": "",
           "verification": {"valid": True}, "summary": {"passed": 1}}


@pytest.fixture
def workspace():
    with tempfile.TemporaryDirectory() as td:
        with open(os.path.join(td, "calc.py"), "w") as f:
            f.write("def add(a, b):\n    return a - b\n")
        os.makedirs(os.path.join(td, "tests"))
        with open(os.path.join(td, "tests", "test_calc.py"), "w") as f:
            f.write("from calc import add\n\ndef test_add():\n    assert add(1, 1) == 2\n")
        yield td


@pytest.fixture
def agents():
    with patch("app.workflows.repair_graph.FailureAnalyzer") as fa, \
         patch("app.workflows.repair_graph.RepairPlanner") as rp, \
         patch("app.workflows.repair_graph.CodingAgent") as ca, \
         patch("app.workflows.repair_graph.TestRunner") as tr:
        fa.return_value.generate_analysis.return_value = FailureAnalysis(
            failure_type="ASSERTION_ERROR", root_cause="add subtracts", affected_files=["calc.py"],
            suggested_fix="use +")
        rp.return_value.generate_plan.return_value = RepairPlan(
            root_cause="add subtracts", affected_files=["calc.py"], changes=["return a + b"])
        ca.return_value.implement.return_value = {"changed_files": ["calc.py"], "summary": "diff"}
        yield {"analyzer": fa.return_value, "planner": rp.return_value, "coder": ca.return_value, "runner": tr.return_value}


def _ctx(test_result, **extra):
    return {"issue_context": "Issue #1: add() is wrong", "solution_plan": {"summary": "fix add"},
            "test_result": test_result, "modified_files": ["tests/test_calc.py"], **extra}


def test_repair_success(workspace, agents):
    agents["runner"].run_tests.return_value = PASSING
    res = execute_repair_loop(1, _ctx(_failing("tests/test_calc.py::test_add")), workspace_path=workspace)
    assert res["status"] == "REPAIR_SUCCESS"
    assert res["attempts"] == 1
    assert res["test_result"]["success"] is True
    # Files changed by the initial attempt and by the repair are both reported (and later committed).
    assert set(res["modified_files"]) == {"tests/test_calc.py", "calc.py"}
    # The repair coder must see the original issue, not only the failure analysis.
    implement_kwargs = agents["coder"].implement.call_args.kwargs
    assert "add() is wrong" in str(implement_kwargs["analysis"])
    assert implement_kwargs["target_files"] == ["calc.py"]

def test_repair_loop_limits(workspace, agents, monkeypatch):
    monkeypatch.setattr("app.config.settings.MAX_REPAIR_ATTEMPTS", 3)
    # Every attempt fails differently, so only the attempt limit stops the loop.
    agents["runner"].run_tests.side_effect = [_failing(f"tests/test_calc.py::test_{i}") for i in range(3)]
    res = execute_repair_loop(1, _ctx(_failing("tests/test_calc.py::test_add")), workspace_path=workspace)
    assert res["status"] == "REPAIR_EXHAUSTED"
    assert res["termination_reason"] == "MAX_ATTEMPTS"
    assert res["attempts"] == 3
    assert len(res["repair_history"]) == 3

def test_repeated_failure_exhaustion(workspace, agents):
    same = _failing("tests/test_calc.py::test_add")
    agents["runner"].run_tests.return_value = same
    res = execute_repair_loop(1, _ctx(same), workspace_path=workspace)
    assert res["status"] == "REPAIR_EXHAUSTED"
    assert res["termination_reason"] == "REPEATED_FAILURE"
    assert res["attempts"] == 1

def test_repair_loop_never_fabricates_success_without_workspace():
    # Regression: a missing workspace used to return REPAIR_SUCCESS with a made-up example.py.
    with pytest.raises(ValueError, match="workspace_path"):
        execute_repair_loop(1, {})

def test_repair_loop_reports_llm_failure_instead_of_guessing(workspace, agents):
    agents["analyzer"].generate_analysis.side_effect = RuntimeError("[ollama] maximum retry bounds exceeded")
    res = execute_repair_loop(1, _ctx(_failing("tests/test_calc.py::test_add")), workspace_path=workspace)
    assert res["status"] == "REPAIR_EXHAUSTED"
    assert res["termination_reason"] == "LLM_ERROR"
    agents["coder"].implement.assert_not_called()

def test_repair_attempt_without_applicable_patch_counts_as_failed_attempt(workspace, agents, monkeypatch):
    monkeypatch.setattr("app.config.settings.MAX_REPAIR_ATTEMPTS", 2)
    from app.services.patch_service import PatchError
    agents["coder"].implement.side_effect = PatchError("search text not found")
    res = execute_repair_loop(1, _ctx(_failing("tests/test_calc.py::test_add")), workspace_path=workspace)
    assert res["status"] == "REPAIR_EXHAUSTED"
    assert res["termination_reason"] == "MAX_ATTEMPTS"
    assert "search text not found" in res["repair_history"][0]["error"]
    agents["runner"].run_tests.assert_not_called()

def test_repair_loop_halts_on_environment_dependency_failure(workspace):
    res = execute_repair_loop(
        run_id=1,
        initial_context={
            "workspace_path": workspace,
            "test_result": {
                "success": False,
                "exit_code": 127,
                "stderr": "missing pytest",
                "verification": {"valid": False, "error": "missing pytest"}
            }
        },
        workspace_path=workspace
    )
    assert res["status"] == "REPAIR_EXHAUSTED"
    assert res["termination_reason"] == "ENVIRONMENT_FAILURE"
    assert res["attempts"] == 1

def test_dependency_error_introduced_by_change_is_repairable(workspace, agents):
    # The baseline ran fine, so a missing module after the change is the change's fault.
    broken = {**_failing("tests/test_calc.py::test_add", "ModuleNotFoundError: No module named 'requests'"),
              "outcome": "DEPENDENCY_ERROR", "exit_code": 2}
    agents["runner"].run_tests.return_value = PASSING
    res = execute_repair_loop(1, _ctx(broken, baseline_test_result=PASSING), workspace_path=workspace)
    assert res["status"] == "REPAIR_SUCCESS"

def test_failure_signature_ignores_timing_noise():
    a = _failing("tests/t.py::test_x")
    b = dict(a, stdout=a["stdout"].replace("0.1s", "0.7s"))
    assert failure_signature(a) == failure_signature(b)
    assert failure_signature(a) != failure_signature(_failing("tests/t.py::test_y"))
