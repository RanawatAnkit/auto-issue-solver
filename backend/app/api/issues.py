from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from app.db.session import get_db
from app.models.domain import Issue, Repository, User
from app.schemas.domain import IssueResponse
from app.api.auth import get_current_user
from typing import List

router = APIRouter()

@router.get("/", response_model=List[IssueResponse])
def get_issues(skip: int = 0, limit: int = 100, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    return db.query(Issue).join(Repository).filter(Repository.user_id == current_user.id).offset(skip).limit(limit).all()

@router.get("/{issue_id}", response_model=IssueResponse)
def get_issue(issue_id: int, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    x = db.query(Issue).join(Repository).filter(Issue.id == issue_id, Repository.user_id == current_user.id).first()
    if not x: raise HTTPException(status_code=404, detail="Not found")
    return x
