from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from app.db.session import get_db
from app.models.domain import PullRequest, Issue, Repository, User
from app.schemas.domain import PullRequestResponse
from app.api.auth import get_current_user
from typing import List

router = APIRouter()

@router.get("/", response_model=List[PullRequestResponse])
def get_pull_requests(skip: int = 0, limit: int = 100, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    return db.query(PullRequest).join(Issue).join(Repository).filter(Repository.user_id == current_user.id).offset(skip).limit(limit).all()

@router.get("/{pr_id}", response_model=PullRequestResponse)
def get_pull_request(pr_id: int, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    x = db.query(PullRequest).join(Issue).join(Repository).filter(PullRequest.id == pr_id, Repository.user_id == current_user.id).first()
    if not x: raise HTTPException(status_code=404, detail="Not found")
    return x
