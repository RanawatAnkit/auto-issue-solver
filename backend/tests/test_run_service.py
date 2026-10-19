import uuid
import pytest
from datetime import datetime, timedelta
from unittest.mock import patch
from app.db.session import SessionLocal
from app.models.domain import AgentRun, AgentStateEnum, Issue, Repository
from app.services.run_service import start_agent_run, ActiveRunExists, EnqueueFailed


@pytest.fixture
def issue():
    db = SessionLocal()
    ext = uuid.uuid4().hex
    repo = Repository(github_repo_id=f"r_{ext}", owner="o", name="n", full_name=f"o/n_{ext}", clone_url="u")
    db.add(repo); db.commit()
    iss = Issue(github_issue_id=f"i_{ext}", repository_id=repo.id, number=1, title="t", state="open")
    db.add(iss); db.commit()
    yield db, iss
    db.close()


def test_start_run_enqueues_job(issue):
    db, iss = issue
    with patch("app.services.run_service.queue_service.enqueue_issue_job") as enqueue:
        run = start_agent_run(db, iss, 42)
    assert run.status == AgentStateEnum.QUEUED
    enqueue.assert_called_once_with({"agent_run_id": run.id, "installation_id": 42})


def test_enqueue_failure_marks_run_failed_instead_of_stranding_it(issue):
    # Regression: Redis errors were swallowed, leaving runs RECEIVED forever and blocking retries.
    db, iss = issue
    with patch("app.services.run_service.queue_service.enqueue_issue_job", side_effect=ConnectionError("redis down")):
        with pytest.raises(EnqueueFailed, match="Redis"):
            start_agent_run(db, iss, 42)
    run = db.query(AgentRun).filter(AgentRun.issue_id == iss.id).one()
    assert run.status == AgentStateEnum.FAILED and run.completed_at is not None
    with patch("app.services.run_service.queue_service.enqueue_issue_job"):
        start_agent_run(db, iss, 42)  # a failed run does not block a new attempt


def test_duplicate_active_run_rejected(issue):
    db, iss = issue
    with patch("app.services.run_service.queue_service.enqueue_issue_job"):
        first = start_agent_run(db, iss, 1)
        with pytest.raises(ActiveRunExists) as exc:
            start_agent_run(db, iss, 1)
    assert exc.value.run.id == first.id


def test_abandoned_run_no_longer_blocks_new_runs(issue, monkeypatch):
    db, iss = issue
    monkeypatch.setattr("app.config.settings.RUN_STALE_AFTER_MINUTES", 60)
    stuck = AgentRun(issue_id=iss.id, status=AgentStateEnum.ANALYZING_REPOSITORY,
                     started_at=datetime.utcnow() - timedelta(hours=5))
    db.add(stuck); db.commit()
    with patch("app.services.run_service.queue_service.enqueue_issue_job"):
        new = start_agent_run(db, iss, 1)
    db.refresh(stuck)
    assert stuck.status == AgentStateEnum.FAILED
    assert "ANALYZING_REPOSITORY" in stuck.error_message
    assert new.status == AgentStateEnum.QUEUED
