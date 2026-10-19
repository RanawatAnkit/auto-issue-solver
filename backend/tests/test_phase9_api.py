import pytest
from fastapi.testclient import TestClient
from app.main import app
from app.models.domain import AgentRun, PullRequest, Issue, Repository, User
from app.db.session import get_db
from app.api.auth import get_current_user

app.dependency_overrides[get_current_user] = lambda: User(id=1)

# Use existing client pattern, but assume DB works inside db_session if we can mock or use a fixture.
# Actually, the user's test suite uses pytest fixtures from tests/conftest.py implicitly (like client, db_session)
# Let's create tests relying on TestClient and db fixtures directly since other tests use them!

from datetime import datetime

class MockQuery:
    def __init__(self, data):
        self.data = data
    def filter(self, *args):
        return self
    def join(self, *args, **kwargs):
        return self
    def offset(self, *args):
        return self
    def limit(self, *args):
        return self
    def first(self):
        return self.data[0] if self.data else None
    def all(self):
        return self.data

class MockSession:
    def __init__(self, data_map):
        self.data_map = data_map
    def query(self, model):
        return MockQuery(self.data_map.get(model, []))

def test_missing_agent_run(client):
    app.dependency_overrides[get_db] = lambda: MockSession({})
    response = client.get("/agent-runs/999999")
    assert response.status_code == 404

def test_missing_pull_request(client):
    app.dependency_overrides[get_db] = lambda: MockSession({})
    response = client.get("/pull-requests/999999")
    assert response.status_code == 404

def test_agent_run_detail_response(client):
    run = AgentRun(id=1, issue_id=1, status="RECEIVED", issue_analysis={"key": "val"}, started_at=datetime.utcnow(), retry_count=0, repair_attempt=0, total_runtime=0.0)
    app.dependency_overrides[get_db] = lambda: MockSession({AgentRun: [run]})
    
    response = client.get(f"/agent-runs/1")
    assert response.status_code == 200
    assert response.json()["issue_analysis"] == {"key": "val"}

def test_list_pull_requests(client):
    pr = PullRequest(id=1, issue_id=1, github_pr_id="123", branch_name="b", pr_url="url", status="open", created_at=datetime.utcnow())
    app.dependency_overrides[get_db] = lambda: MockSession({PullRequest: [pr]})
    
    response = client.get("/pull-requests/")
    assert response.status_code == 200
    data = response.json()
    assert len(data) >= 1
    assert data[-1]["branch_name"] == "b"

def test_get_pull_request(client):
    pr = PullRequest(id=1, issue_id=1, github_pr_id="1234", branch_name="c", pr_url="url2", status="open", created_at=datetime.utcnow())
    app.dependency_overrides[get_db] = lambda: MockSession({PullRequest: [pr]})
    
    response = client.get(f"/pull-requests/{pr.id}")
    assert response.status_code == 200
    assert response.json()["branch_name"] == "c"

def test_terminal_agent_states_includes_plan_ready():
    from app.api.repositories import TERMINAL_AGENT_STATES
    from app.models.domain import AgentStateEnum
    assert AgentStateEnum.PLAN_READY in TERMINAL_AGENT_STATES
    # Ensure completed planning runs are recognized as terminal for background pipelines
    assert AgentStateEnum.COMPLETED in TERMINAL_AGENT_STATES
    assert AgentStateEnum.PR_CREATED in TERMINAL_AGENT_STATES
