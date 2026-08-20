import hmac
import hashlib
from fastapi import APIRouter, Request, HTTPException, Depends
from sqlalchemy.orm import Session
from app.config import settings
from app.db.session import get_db
from app.models.domain import Repository, Issue, AgentRun, AgentStateEnum
from app.services.queue_service import enqueue_issue_job
import logging

logger = logging.getLogger(__name__)
router = APIRouter()

def verify_signature(payload: bytes, signature: str, secret: str) -> bool:
    if not signature: return False
    mac = hmac.new(secret.encode('utf-8'), msg=payload, digestmod=hashlib.sha256)
    expected_mac = "sha256=" + mac.hexdigest()
    return hmac.compare_digest(expected_mac, signature)

@router.post("/github")
async def github_webhook(request: Request, db: Session = Depends(get_db)):
    sig = request.headers.get("x-hub-signature-256")
    body = await request.body()
    if not verify_signature(body, sig, settings.GITHUB_WEBHOOK_SECRET):
        raise HTTPException(status_code=401, detail="Invalid signature")
        
    event = request.headers.get("x-github-event")
    payload = await request.json()
    
    if event == "issues":
        action = payload.get("action")
        if action not in ["opened", "edited", "reopened", "closed"]:
            return {"status": "ignored", "reason": "unsupported action"}
            
        issue_data = payload["issue"]
        repo_data = payload["repository"]
        installation_id = payload.get("installation", {}).get("id")
        
        # Idempotent Repository Upsert
        repo = db.query(Repository).filter(Repository.github_repo_id == str(repo_data["id"])).first()
        if not repo:
            repo = Repository(
                github_repo_id=str(repo_data["id"]),
                owner=repo_data["owner"]["login"],
                name=repo_data["name"],
                full_name=repo_data["full_name"],
                default_branch=repo_data.get("default_branch", "main"),
                clone_url=repo_data["clone_url"]
            )
            db.add(repo)
            db.commit()
            db.refresh(repo)
            
        # Idempotent Issue Upsert
        issue = db.query(Issue).filter(Issue.github_issue_id == str(issue_data["id"])).first()
        if not issue:
            labels = ",".join([lbl["name"] for lbl in issue_data.get("labels", [])] if issue_data.get("labels") else [])
            issue = Issue(
                github_issue_id=str(issue_data["id"]),
                repository_id=repo.id,
                number=issue_data["number"],
                title=issue_data["title"],
                body=issue_data.get("body", ""),
                state=issue_data["state"],
                labels=labels
            )
            db.add(issue)
            db.commit()
            db.refresh(issue)
        else:
            issue.state = issue_data["state"]
            issue.title = issue_data["title"]
            db.commit()
            db.refresh(issue)
            
        # Dispatch Jobs for actionable states
        if action in ["opened", "reopened"]:
            existing_run = db.query(AgentRun).filter(AgentRun.issue_id == issue.id, AgentRun.status.in_([AgentStateEnum.RECEIVED, AgentStateEnum.ANALYZING_ISSUE, AgentStateEnum.ANALYZING_REPOSITORY])).first()
            if not existing_run:
                agent_run = AgentRun(issue_id=issue.id, status=AgentStateEnum.RECEIVED, current_step="Enqueued Job")
                db.add(agent_run)
                db.commit()
                db.refresh(agent_run)
                
                enqueue_issue_job({"agent_run_id": agent_run.id, "installation_id": installation_id})
                return {"status": "accepted", "agent_run_id": agent_run.id}
    
    return {"status": "ignored"}