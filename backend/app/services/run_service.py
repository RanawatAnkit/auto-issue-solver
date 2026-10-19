import logging
from datetime import datetime, timedelta
from sqlalchemy.orm import Session
from app.config import settings
from app.models.domain import AgentRun, AgentStateEnum, Issue, TERMINAL_AGENT_STATES
from app.services import queue_service

logger = logging.getLogger(__name__)


class ActiveRunExists(Exception):
    def __init__(self, run: AgentRun):
        self.run = run
        super().__init__(f"AgentRun {run.id} is already active for this issue (status {run.status.value}).")


class EnqueueFailed(Exception):
    pass


def find_active_run(db: Session, issue_id: int) -> AgentRun | None:
    """Return the non-terminal run for an issue, retiring runs abandoned by a crashed worker."""
    active = db.query(AgentRun).filter(
        AgentRun.issue_id == issue_id,
        AgentRun.status.notin_(TERMINAL_AGENT_STATES),
    ).order_by(AgentRun.id.desc()).all()

    stale_before = datetime.utcnow() - timedelta(minutes=settings.RUN_STALE_AFTER_MINUTES)
    live = None
    for run in active:
        if run.started_at and run.started_at < stale_before:
            previous = run.status.value
            logger.warning("Marking abandoned AgentRun %s (status %s since %s) as FAILED", run.id, previous, run.started_at)
            run.status = AgentStateEnum.FAILED
            run.error_message = (
                f"Run abandoned: still in state {previous} more than {settings.RUN_STALE_AFTER_MINUTES} "
                "minutes after it started. The worker was probably stopped or crashed."
            )
            run.completed_at = datetime.utcnow()
        elif live is None:
            live = run
    db.commit()
    return live


def start_agent_run(db: Session, issue: Issue, installation_id: int | None) -> AgentRun:
    """Create a QUEUED run for the issue and enqueue it for the worker.

    Raises ActiveRunExists for duplicates and EnqueueFailed (after recording the
    failure on the run) when the job could not be queued, so runs never sit in
    QUEUED forever without a job behind them.
    """
    existing = find_active_run(db, issue.id)
    if existing:
        raise ActiveRunExists(existing)

    run = AgentRun(issue_id=issue.id, status=AgentStateEnum.QUEUED, current_step="Waiting for worker")
    db.add(run)
    db.commit()
    db.refresh(run)

    try:
        queue_service.enqueue_issue_job({"agent_run_id": run.id, "installation_id": installation_id})
    except Exception as e:
        logger.error("Could not enqueue AgentRun %s: %s", run.id, e)
        run.status = AgentStateEnum.FAILED
        run.error_message = f"Could not enqueue job (is Redis running at the configured REDIS_URL?): {e}"
        run.completed_at = datetime.utcnow()
        db.commit()
        raise EnqueueFailed(run.error_message) from e

    return run
