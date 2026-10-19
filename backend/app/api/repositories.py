from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from app.db.session import get_db
from app.models.domain import Repository, User, Issue, AgentRun, AgentStateEnum, TERMINAL_AGENT_STATES
from app.schemas.domain import RepositoryResponse, IssueResponse
from app.api.auth import get_current_user
from app.services.github_service import GitHubService, GitWorkspace
from app.services.run_service import start_agent_run, ActiveRunExists, EnqueueFailed
from typing import List, Dict, Any
from pydantic import BaseModel

router = APIRouter()

class ConnectRepoRequest(BaseModel):
    full_name: str

@router.get("/", response_model=List[RepositoryResponse])
def get_repositories(skip: int = 0, limit: int = 100, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    return db.query(Repository).filter(Repository.user_id == current_user.id).offset(skip).limit(limit).all()

@router.get("/{repository_id}", response_model=RepositoryResponse)
def get_repository(repository_id: int, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    x = db.query(Repository).filter(Repository.id == repository_id, Repository.user_id == current_user.id).first()
    if not x: raise HTTPException(status_code=404, detail="Not found")
    return x

@router.post("/connect", response_model=RepositoryResponse)
def connect_repository(req: ConnectRepoRequest, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    from app.config import settings
    try:
        inst_id = GitHubService.get_installation_id_for_repo(req.full_name)
        gh = GitHubService(installation_id=inst_id) 
        gh.check_repository_access(req.full_name)
        gh_repo = gh.get_repository(req.full_name)
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"GitHub validation failed: {str(e)}")

    existing = db.query(Repository).filter(Repository.full_name == req.full_name).first()
    if existing:
        if existing.user_id == current_user.id:
            raise HTTPException(status_code=400, detail="Repository already connected to you.")
        else:
            # Transfer unclaimed
            existing.user_id = current_user.id
            db.commit()
            db.refresh(existing)
            return existing

    new_repo = Repository(
        user_id=current_user.id,
        github_repo_id=str(gh_repo.id),
        owner=gh_repo.owner.login,
        name=gh_repo.name,
        full_name=gh_repo.full_name,
        default_branch=gh_repo.default_branch,
        clone_url=gh_repo.clone_url
    )
    db.add(new_repo)
    db.commit()
    db.refresh(new_repo)
    return new_repo

@router.get("/{repository_id}/issues")
def get_issues(repository_id: int, state: str = "open", db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    repo = db.query(Repository).filter(Repository.id == repository_id, Repository.user_id == current_user.id).first()
    if not repo: raise HTTPException(status_code=404, detail="Repository not found")
    
    inst_id = GitHubService.get_installation_id_for_repo(repo.full_name)
    gh = GitHubService(installation_id=inst_id)
    issues = gh.list_issues(repo.full_name, state=state)
    return [{"id": i.id, "number": i.number, "title": i.title, "body": i.body, "labels": [l.name for l in i.labels], "state": i.state, "created_at": i.created_at.isoformat()} for i in issues]

@router.get("/{repository_id}/issues/{number}")
def get_issue(repository_id: int, number: int, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    repo = db.query(Repository).filter(Repository.id == repository_id, Repository.user_id == current_user.id).first()
    if not repo: raise HTTPException(status_code=404, detail="Repository not found")
    
    inst_id = GitHubService.get_installation_id_for_repo(repo.full_name)
    gh = GitHubService(installation_id=inst_id)
    try:
        i = gh.get_issue(repo.full_name, number)
    except Exception:
        raise HTTPException(status_code=404, detail="Issue not found on GitHub")
        
    return {"id": i.id, "number": i.number, "title": i.title, "body": i.body, "labels": [l.name for l in i.labels], "state": i.state, "created_at": i.created_at.isoformat()}

@router.post("/{repository_id}/issues/{number}/solve")
def solve_issue(repository_id: int, number: int, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    repo = db.query(Repository).filter(Repository.id == repository_id, Repository.user_id == current_user.id).first()
    if not repo: raise HTTPException(status_code=404, detail="Repository not found")

    try:
        inst_id = GitHubService.get_installation_id_for_repo(repo.full_name)
        gh = GitHubService(installation_id=inst_id)
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"GitHub App access failed for {repo.full_name}: {e}")
    try:
        gh_issue = gh.get_issue(repo.full_name, number)
    except Exception:
        raise HTTPException(status_code=404, detail="Issue not found on GitHub")
    if getattr(gh_issue, "pull_request", None):
        raise HTTPException(status_code=400, detail=f"#{number} is a pull request, not an issue.")

    labels = ",".join(l.name for l in (gh_issue.labels or []))
    issue = db.query(Issue).filter(Issue.repository_id == repo.id, Issue.number == number).first()
    if not issue:
        issue = Issue(
            repository_id=repo.id,
            github_issue_id=str(gh_issue.id),
            number=gh_issue.number,
            title=gh_issue.title,
            body=gh_issue.body,
            state=gh_issue.state,
            labels=labels,
        )
        db.add(issue)
    else:
        # GitHub is the source of truth; the issue may have been edited since the last run.
        issue.title = gh_issue.title
        issue.body = gh_issue.body
        issue.state = gh_issue.state
        issue.labels = labels
    db.commit()
    db.refresh(issue)

    try:
        new_run = start_agent_run(db, issue, inst_id)
    except ActiveRunExists as e:
        raise HTTPException(status_code=409, detail=f"AgentRun {e.run.id} is already active for this issue ({e.run.status.value}).")
    except EnqueueFailed as e:
        raise HTTPException(status_code=503, detail=str(e))

    return {"run_id": new_run.id, "status": "queued"}

from app.services.repository_indexer import RepositoryIndexer
from app.services.code_retriever import CodeRetriever

@router.post("/{repository_id}/index")
def index_repo(repository_id: int, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    # Index a fresh clone of the repository. (This used to accept an arbitrary
    # server-side path from the client, which allowed indexing any local directory.)
    repo = db.query(Repository).filter(Repository.id == repository_id, Repository.user_id == current_user.id).first()
    if not repo: raise HTTPException(status_code=404, detail="Repository not found")
    try:
        inst_id = GitHubService.get_installation_id_for_repo(repo.full_name)
        token = GitHubService(installation_id=inst_id).get_installation_token()
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"GitHub App access failed for {repo.full_name}: {e}")
    workspace = GitWorkspace(repo.clone_url)
    try:
        workspace.clone(auth_token=token)
        chunk_count = RepositoryIndexer(db).index_repository(repository_id, workspace.workspace_path)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Indexing failed: {e}")
    finally:
        workspace.cleanup()
    return {"status": "indexed", "chunks": chunk_count}

@router.get("/{repository_id}/search")
def search_repo(repository_id: int, query: str, top_k: int = 10, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    repo = db.query(Repository).filter(Repository.id == repository_id, Repository.user_id == current_user.id).first()
    if not repo: raise HTTPException(status_code=404, detail="Repository not found")
    retriever = CodeRetriever(db)
    return retriever.search(repository_id, query, top_k)
