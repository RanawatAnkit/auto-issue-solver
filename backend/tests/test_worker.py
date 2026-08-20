import pytest
import uuid
from unittest.mock import patch
from app.workers.issue_worker import process_agent_run
from app.db.session import SessionLocal
from app.models.domain import AgentRun, Issue, Repository, AgentStateEnum
from app.agents.schemas import IssueAnalysis, SolutionPlan, CodeChangePlan

@patch("app.workers.issue_worker.GitHubService")
@patch("app.workers.issue_worker.GitWorkspace")
@patch("app.agents.issue_analyzer.IssueAnalyzer")
@patch("app.agents.context_builder.ContextBuilder")
@patch("app.agents.planner.PlannerAgent")
@patch("app.agents.coding_agent.CodingAgent")
@patch("app.services.patch_service.PatchService")
@patch("app.sandbox.test_runner.TestRunner")
@patch("app.services.github_service.validate_no_secrets", return_value=True)
def test_worker_transitions_success(mock_validate, mock_tester, mock_patcher, mock_coder, mock_planner, mock_context, mock_analyzer, mock_ws, mock_gh):
    db = SessionLocal()
    
    unique_ext = str(uuid.uuid4().hex)
    repo = Repository(github_repo_id=f"t1_{unique_ext}", owner="o", name="n", full_name=f"o/n_{unique_ext}", clone_url="x")
    db.add(repo)
    db.commit()
    
    issue = Issue(github_issue_id=f"i1_{unique_ext}", repository_id=repo.id, number=19, title="t", state="open")
    db.add(issue)
    db.commit()
    
    run = AgentRun(issue_id=issue.id, status=AgentStateEnum.RECEIVED)
    db.add(run)
    db.commit()
    
    mock_ws.return_value.workspace_path = "/tmp"
    mock_analyzer.return_value.analyze.return_value = IssueAnalysis(problem_summary="p", current_behavior="c", expected_behavior="e", affected_areas=[], constraints=[])
    mock_planner.return_value.create_plan.return_value = SolutionPlan(summary="summary", affected_files=[], tests=[], dependencies=[], risks=[], assumptions=[], acceptance_criteria=[])
    mock_coder.return_value.generate_changes.return_value = CodeChangePlan(changes=[])
    mock_tester.return_value.run_tests.return_value = {"success": True, "exit_code": 0}
    
    mock_gh.return_value.find_existing_pull_request.return_value = None
    mock_gh.return_value.get_branch_sha.return_value = None
    
    # Needs Mock PR Response object
    class MockPR:
        html_url = "http://mock/pr"
    mock_gh.return_value.create_pull_request.return_value = MockPR()
    
    process_agent_run({"agent_run_id": run.id, "installation_id": 123})
    db.refresh(run)

    # Validates State progress and GitHub abstraction mocking
    assert run.status == AgentStateEnum.PR_CREATED
    
    # Validate branch mapping specifically verifies issue requirement natively securely limiting scopes properly!
    mock_gh.return_value.create_branch.assert_called_with(repo.full_name, f"agent/issue-{issue.number}-run-{run.id}", "main")
    
    # Assert temp dir cleanup successfully ran inside finally blocks cleanly securely
    mock_ws.return_value.cleanup.assert_called_once()
    
@patch("app.workers.issue_worker.GitHubService")
@patch("app.workers.issue_worker.GitWorkspace")
@patch("app.agents.issue_analyzer.IssueAnalyzer")
@patch("app.agents.context_builder.ContextBuilder")
@patch("app.agents.planner.PlannerAgent")
@patch("app.agents.coding_agent.CodingAgent")
@patch("app.services.patch_service.PatchService")
@patch("app.sandbox.test_runner.TestRunner")
@patch("app.workflows.repair_graph.execute_repair_loop")
def test_worker_transitions_failure(mock_repair, mock_tester, mock_patcher, mock_coder, mock_planner, mock_context, mock_analyzer, mock_ws, mock_gh):
    db = SessionLocal()
    
    unique_ext = str(uuid.uuid4().hex)
    repo = Repository(github_repo_id=f"t1_{unique_ext}", owner="o", name="n", full_name=f"o/n_{unique_ext}", clone_url="x")
    db.add(repo)
    db.commit()
    
    issue = Issue(github_issue_id=f"i1_{unique_ext}", repository_id=repo.id, number=1, title="t", state="open")
    db.add(issue)
    db.commit()
    
    run = AgentRun(issue_id=issue.id, status=AgentStateEnum.RECEIVED, retry_count=0)
    db.add(run)
    db.commit()
    
    mock_analyzer.return_value.analyze.return_value = IssueAnalysis(problem_summary="p", current_behavior="c", expected_behavior="e", affected_areas=[], constraints=[])
    mock_planner.return_value.create_plan.return_value = SolutionPlan(summary="summary", affected_files=[], tests=[], dependencies=[], risks=[], assumptions=[], acceptance_criteria=[])
    mock_coder.return_value.generate_changes.return_value = CodeChangePlan(changes=[])
    mock_tester.return_value.run_tests.return_value = {"success": False, "exit_code": 1}
    
    mock_repair.return_value = {"status": "REPAIR_EXHAUSTED", "attempts": 3, "termination_reason": "MAX_ATTEMPTS"}
    
    process_agent_run({"agent_run_id": run.id, "installation_id": 123})
    db.refresh(run)

    assert run.status == AgentStateEnum.REPAIR_EXHAUSTED
    mock_ws.return_value.cleanup.assert_called_once()
