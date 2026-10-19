import hmac
import json
import hashlib
from fastapi import APIRouter, Request, HTTPException, Depends
from sqlalchemy.orm import Session
from app.config import settings
from app.db.session import get_db
from app.models.domain import Repository, Issue
from app.services import queue_service
from app.services.run_service import start_agent_run, ActiveRunExists, EnqueueFailed
import logging

logger = logging.getLogger(__name__)
router = APIRouter()

DELIVERY_TTL_SECONDS = 24 * 3600

def verify_signature(payload: bytes, signature: str, secret: str) -> bool:
    if not signature or not secret: return False
    mac = hmac.new(secret.encode('utf-8'), msg=payload, digestmod=hashlib.sha256)
    expected_mac = "sha256=" + mac.hexdigest()
    return hmac.compare_digest(expected_mac, signature)

def _first_delivery(delivery_id: str | None) -> bool:
    """False if GitHub already delivered this event (redeliveries reuse the ID)."""
    if not delivery_id:
        return True
    try:
        return bool(queue_service.redis_client.set(f"webhook:delivery:{delivery_id}", 1, nx=True, ex=DELIVERY_TTL_SECONDS))
    except Exception as e:
        # Duplicate runs are still prevented by the active-run check below.
        logger.warning(f"Could not record webhook delivery {delivery_id}: {e}")
        return True

@router.post("/github")
async def github_webhook(request: Request, db: Session = Depends(get_db)):
    if not settings.GITHUB_WEBHOOK_SECRET:
        raise HTTPException(status_code=503, detail="Webhook secret not configured: set GITHUB_WEBHOOK_SECRET.")
    sig = request.headers.get("x-hub-signature-256")
    body = await request.body()
    if not verify_signature(body, sig, settings.GITHUB_WEBHOOK_SECRET):
        raise HTTPException(status_code=401, detail="Invalid signature")

    event = request.headers.get("x-github-event")
    try:
        payload = json.loads(body)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid JSON payload")

    if event != "issues":
        return {"status": "ignored", "reason": f"unsupported event '{event}'"}

    action = payload.get("action")
    if action not in ["opened", "edited", "reopened", "closed"]:
        return {"status": "ignored", "reason": "unsupported action"}

    issue_data = payload.get("issue")
    repo_data = payload.get("repository")
    if not issue_data or not repo_data:
        raise HTTPException(status_code=400, detail="Payload missing issue or repository")
    if issue_data.get("pull_request"):
        return {"status": "ignored", "reason": "pull request event"}

    if not _first_delivery(request.headers.get("x-github-delivery")):
        return {"status": "ignored", "reason": "duplicate delivery"}

    installation_id = (payload.get("installation") or {}).get("id")
    labels = ",".join(lbl["name"] for lbl in issue_data.get("labels") or [])

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
    else:
        repo.default_branch = repo_data.get("default_branch", repo.default_branch)
    db.commit()
    db.refresh(repo)

    # Idempotent Issue Upsert
    issue = db.query(Issue).filter(Issue.github_issue_id == str(issue_data["id"])).first()
    if not issue:
        issue = Issue(
            github_issue_id=str(issue_data["id"]),
            repository_id=repo.id,
            number=issue_data["number"],
            title=issue_data["title"],
            body=issue_data.get("body") or "",
            state=issue_data["state"],
            labels=labels
        )
        db.add(issue)
    else:
        issue.state = issue_data["state"]
        issue.title = issue_data["title"]
        issue.body = issue_data.get("body") or ""
        issue.labels = labels
    db.commit()
    db.refresh(issue)

    if action not in ["opened", "reopened"]:
        return {"status": "updated", "issue_id": issue.id}

    if not installation_id:
        return {"status": "ignored", "reason": "event did not come from a GitHub App installation"}

    try:
        agent_run = start_agent_run(db, issue, installation_id)
    except ActiveRunExists as e:
        return {"status": "ignored", "reason": f"active run {e.run.id} already in progress"}
    except EnqueueFailed as e:
        raise HTTPException(status_code=503, detail=str(e))
    return {"status": "accepted", "agent_run_id": agent_run.id}
