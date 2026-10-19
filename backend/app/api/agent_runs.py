from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from app.db.session import get_db
from app.models.domain import AgentRun, AgentAction, Issue, Repository, User
from app.schemas.domain import AgentRunResponse, AgentRunDetailResponse, AgentActionResponse
from app.api.auth import get_current_user
from app.services.github_service import GitHubService
from app.services.run_service import start_agent_run, ActiveRunExists, EnqueueFailed
from typing import List

router = APIRouter()

def _owned_run(db: Session, run_id: int, user: User) -> AgentRun:
    run = db.query(AgentRun).join(Issue).join(Repository).filter(AgentRun.id == run_id, Repository.user_id == user.id).first()
    if not run: raise HTTPException(status_code=404, detail="Not found")
    return run

@router.get("/", response_model=List[AgentRunResponse])
def get_agent_runs(skip: int = 0, limit: int = 100, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    return db.query(AgentRun).join(Issue).join(Repository).filter(Repository.user_id == current_user.id).order_by(AgentRun.id.desc()).offset(skip).limit(limit).all()

@router.get("/{run_id}", response_model=AgentRunDetailResponse)
def get_agent_run(run_id: int, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    return _owned_run(db, run_id, current_user)

@router.get("/{run_id}/actions", response_model=List[AgentActionResponse])
def get_agent_run_actions(run_id: int, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    run = _owned_run(db, run_id, current_user)
    return db.query(AgentAction).filter(AgentAction.agent_run_id == run.id).order_by(AgentAction.id).all()

# The per-stage endpoints that used to live here (/analyze, /code, /repair,
# /finalize) ran against empty temp directories and recorded mock pull
# requests, so they reported results that never happened. Every stage now runs
# only inside the worker pipeline against a real checkout; use /rerun to start
# a fresh attempt.

@router.post("/{run_id}/rerun")
def rerun_agent_run(run_id: int, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    run = _owned_run(db, run_id, current_user)
    issue = db.query(Issue).filter(Issue.id == run.issue_id).first()
    repo = db.query(Repository).filter(Repository.id == issue.repository_id).first()
    try:
        inst_id = GitHubService.get_installation_id_for_repo(repo.full_name)
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"GitHub App access failed for {repo.full_name}: {e}")
    try:
        new_run = start_agent_run(db, issue, inst_id)
    except ActiveRunExists as e:
        raise HTTPException(status_code=409, detail=f"AgentRun {e.run.id} is already active for this issue ({e.run.status.value}).")
    except EnqueueFailed as e:
        raise HTTPException(status_code=503, detail=str(e))
    return {"run_id": new_run.id, "status": "queued"}
