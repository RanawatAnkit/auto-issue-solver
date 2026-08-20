import pytest
from app.agents.schemas import FailureAnalysis, RepairPlan
from app.agents.failure_analyzer import FailureAnalyzer
from app.agents.repair_planner import RepairPlanner

def test_failure_analysis_schema(monkeypatch):
    monkeypatch.setattr("app.config.settings.OPENAI_API_KEY", "mock")
    def mock_generate(self, prompt, schema):
        return schema(
            failure_type="ASSERTION_ERROR", root_cause="Mocked", affected_files=["app.py"], affected_symbols=["func"],
            evidence=["Err"], suggested_fix="Fix", confidence=0.9, unresolved_questions=[]
        )
    monkeypatch.setattr("app.services.llm_service.LLMService.generate_structured", mock_generate)
    
    agent = FailureAnalyzer()
    res = agent.generate_analysis("issue", "plan", "changes", "tests")
    assert res.failure_type == "ASSERTION_ERROR"
    assert res.root_cause == "Mocked"

def test_repair_planner_schema(monkeypatch):
    monkeypatch.setattr("app.config.settings.OPENAI_API_KEY", "mock")
    def mock_generate(self, prompt, schema):
        return schema(
            root_cause="Mocked", affected_files=["app.py"], changes=["Patch abc"],
            tests_to_run=["test_app"], reasoning="Due to Y", confidence=0.88
        )
    monkeypatch.setattr("app.services.llm_service.LLMService.generate_structured", mock_generate)
    
    agent = RepairPlanner()
    res = agent.generate_plan("analysis", "plan", "context")
    assert len(res.changes) == 1

def test_repair_loop_limits():
    from app.workflows.repair_graph import execute_repair_loop
    res = execute_repair_loop(1, {"simulate_infinite": True})
    assert res["status"] == "REPAIR_EXHAUSTED"
    assert res["termination_reason"] == "MAX_ATTEMPTS"
    
def test_repeated_failure_exhaustion():
    from app.workflows.repair_graph import execute_repair_loop
    res = execute_repair_loop(1, {"simulate_repeated_failure": True})
    assert res["status"] == "REPAIR_EXHAUSTED"
    assert res["termination_reason"] == "REPEATED_FAILURE"
    
def test_repair_success():
    from app.workflows.repair_graph import execute_repair_loop
    res = execute_repair_loop(1, {})
    assert res["status"] == "REPAIR_SUCCESS"
    assert res["attempts"] == 1