import pytest
from app.agents.schemas import IssueAnalysis, SolutionPlan, RetrievedContextChunk
from app.agents.planner import PlannerAgent

def test_issue_analysis_schema():
    data = {
        "problem_summary": "sum", "expected_behavior": "exp",
        "current_behavior": "cur", "affected_areas": ["tests"],
        "constraints": []
    }
    ana = IssueAnalysis(**data)
    assert ana.problem_summary == "sum"

def test_planner_validation_accepts_existing_retrieval():
    planner = PlannerAgent()
    p = SolutionPlan(
        summary="fix", 
        affected_files=[{"path": "existing.py", "operation": "modify", "reason": "missing", "symbols": [], "planned_changes": []}],
        tests=[], dependencies=[], risks=[], assumptions=[], acceptance_criteria=["done"]
    )
    # Passed safely since "existing.py" exists in available_files!
    planner.validate_plan(p, available_files=["existing.py"])

def test_planner_validation_rejects_missing_contexts():
    planner = PlannerAgent()
    p = SolutionPlan(
        summary="fix", 
        affected_files=[{"path": "unseen.py", "operation": "modify", "reason": "missing", "symbols": [], "planned_changes": []}],
        tests=[], dependencies=[], risks=[], assumptions=[], acceptance_criteria=["done"]
    )
    with pytest.raises(ValueError, match="Plan references out-of-context missing untracked file"):
        planner.validate_plan(p, available_files=["existing.py"])

def test_planner_validation_accepts_nonexistent_marked_create():
    planner = PlannerAgent()
    p = SolutionPlan(
        summary="fix", 
        affected_files=[{"path": "new_file.py", "operation": "create", "reason": "missing", "symbols": [], "planned_changes": []}],
        tests=[], dependencies=[], risks=[], assumptions=[], acceptance_criteria=["done"]
    )
    planner.validate_plan(p, available_files=["existing.py"])

def test_planner_validation_rejects_path_traversal():
    planner = PlannerAgent()
    p = SolutionPlan(
        summary="fix", 
        affected_files=[{"path": "../unseen.py", "operation": "modify", "reason": "traversal", "symbols": [], "planned_changes": []}],
        tests=[], dependencies=[], risks=[], assumptions=[], acceptance_criteria=["done"]
    )
    with pytest.raises(ValueError, match="Path Traversal"):
        planner.validate_plan(p, available_files=["unseen.py"])
        
def test_planner_validation_rejects_git_path():
    planner = PlannerAgent()
    p = SolutionPlan(
        summary="fix", 
        affected_files=[{"path": ".git/config", "operation": "modify", "reason": "traversal", "symbols": [], "planned_changes": []}],
        tests=[], dependencies=[], risks=[], assumptions=[], acceptance_criteria=["done"]
    )
    with pytest.raises(ValueError, match="Git restriction active"):
        planner.validate_plan(p, available_files=[".git/config"])

from unittest.mock import MagicMock
from app.agents.context_builder import ContextBuilder

def test_context_builder_issue_analysis_regression():
    data = {
        "problem_summary": "sum", "expected_behavior": "exp",
        "current_behavior": "cur", "affected_areas": ["tests"],
        "constraints": []
    }
    ana = IssueAnalysis(**data)
    
    mock_db = MagicMock()
    builder = ContextBuilder(mock_db)
    builder.retriever = MagicMock()
    builder.retriever.search.return_value = [{"file_path": "a.py", "content": "hello"}]
    
    ctx = builder.build_context(1, ana)
    
    builder.retriever.search.assert_called_once()
    args, kwargs = builder.retriever.search.call_args
    assert args[1] == "sum cur tests"
    assert len(ctx) == 1
    assert ctx[0].file_path == "a.py"
