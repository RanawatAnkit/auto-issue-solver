from pydantic import BaseModel
from typing import Optional, List
from datetime import datetime

class UserBase(BaseModel):
    github_user_id: str
    github_login: str
    github_name: Optional[str] = None
    github_avatar_url: Optional[str] = None

class UserResponse(UserBase):
    id: int
    created_at: datetime
    updated_at: datetime
    class Config:
        from_attributes = True

class RepositoryBase(BaseModel):
    github_repo_id: str
    user_id: Optional[int] = None
    owner: str
    name: str
    full_name: str
    default_branch: str
    clone_url: str

class RepositoryResponse(RepositoryBase):
    id: int
    created_at: datetime
    updated_at: datetime
    class Config:
        from_attributes = True

class IssueBase(BaseModel):
    github_issue_id: str
    repository_id: int
    number: int
    title: str
    body: Optional[str] = None
    state: str
    labels: Optional[str] = None

class IssueResponse(IssueBase):
    id: int
    created_at: datetime
    updated_at: datetime
    class Config:
        from_attributes = True

class AgentRunBase(BaseModel):
    issue_id: int
    status: str
    current_step: Optional[str] = None
    error_message: Optional[str] = None
    retry_count: int = 0

class AgentRunResponse(AgentRunBase):
    id: int
    started_at: datetime
    completed_at: Optional[datetime] = None
    class Config:
        from_attributes = True

from typing import Any

class AgentRunDetailResponse(AgentRunResponse):
    issue_analysis: Optional[Any] = None
    solution_plan: Optional[Any] = None
    modified_files: Optional[Any] = None
    diff_summary: Optional[str] = None
    test_result: Optional[Any] = None
    repair_attempt: int = 0
    failure_analysis: Optional[Any] = None
    repair_plan: Optional[Any] = None
    termination_reason: Optional[str] = None
    total_runtime: float = 0.0
    repair_history: Optional[Any] = None

class PullRequestResponse(BaseModel):
    id: int
    issue_id: int
    github_pr_id: str
    branch_name: str
    pr_url: str
    status: str
    created_at: datetime
    merged_at: Optional[datetime] = None
    class Config:
        from_attributes = True