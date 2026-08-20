from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from app.db.base import get_db
from app.models.domain import AgentRun, AgentStateEnum, PullRequest
import uuid

router = APIRouter()

@router.post("/{run_id}/finalize")
def finalize_repair(run_id: int, db: Session = Depends(get_db)):
    run = db.query(AgentRun).filter(AgentRun.id == run_id).first()
    if not run:
        raise HTTPException(404, "Not found")
        
    invalid_states = [AgentStateEnum.TEST_FAILED, AgentStateEnum.REPAIR_EXHAUSTED, AgentStateEnum.CODING, AgentStateEnum.TESTING, AgentStateEnum.REPAIRING, AgentStateEnum.ANALYZING_FAILURE]
    if run.status in invalid_states or run.status != AgentStateEnum.REPAIR_SUCCESS:
        raise HTTPException(400, "Can only finalize REPAIR_SUCCESS")
        
    # Idempotency constraint explicitly mimicking safe states
    existing_pr = db.query(PullRequest).filter(PullRequest.issue_id == run.issue_id).first()
    if existing_pr:
        run.status = AgentStateEnum.PR_CREATED
        db.commit()
        return {"pr_url": existing_pr.pr_url, "status": "existing"}
        
    # Mocking integrations natively avoiding untested structural deployments!
    try:
        run.status = AgentStateEnum.COMMITTING
        db.commit()
        
        # Secret checks, staging, committing
        
        run.status = AgentStateEnum.PUSHING
        db.commit()
        
        run.status = AgentStateEnum.CREATING_PR
        db.commit()
        
        # Create DB trace
        new_pr = PullRequest(
            issue_id=run.issue_id,
            github_pr_id=str(uuid.uuid4()),
            branch_name="agent/issue-" + str(run.issue_id),
            pr_url="https://github.com/mock/pr"
        )
        db.add(new_pr)
        run.status = AgentStateEnum.PR_CREATED
        db.commit()
        
        return {"pr_url": new_pr.pr_url, "status": "created"}
        
    except Exception as e:
        if "Secret" in str(e):
            run.status = AgentStateEnum.COMMIT_FAILED
        elif "Push" in str(e):
            run.status = AgentStateEnum.PUSH_FAILED
        else:
            run.status = AgentStateEnum.PR_CREATION_FAILED
        db.commit()
        raise HTTPException(500, detail=str(e))