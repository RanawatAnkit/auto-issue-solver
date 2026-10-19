from datetime import datetime
from sqlalchemy import Column, Integer, Float, String, Text, DateTime, ForeignKey, Boolean, Enum as SQLEnum, JSON
from sqlalchemy.orm import relationship
from pgvector.sqlalchemy import Vector
from app.db.base import Base
import enum

class AgentStateEnum(str, enum.Enum):
    # Core intake & analysis states
    QUEUED = "QUEUED"
    RECEIVED = "RECEIVED"
    PENDING = "PENDING"
    ANALYZING = "ANALYZING"
    ANALYZING_ISSUE = "ANALYZING_ISSUE"
    ANALYZING_REPOSITORY = "ANALYZING_REPOSITORY"
    RETRIEVING_CONTEXT = "RETRIEVING_CONTEXT"
    PLANNING = "PLANNING"
    PLAN_READY = "PLAN_READY"
    
    # Implementation & test states
    CODING = "CODING"
    PATCH_APPLIED = "PATCH_APPLIED"
    CODE_CHANGED = "CODE_CHANGED"
    TESTING = "TESTING"
    TEST_FAILED = "TEST_FAILED"
    
    # Autonomous repair loop states
    ANALYZING_FAILURE = "ANALYZING_FAILURE"
    REPAIR_PLANNING = "REPAIR_PLANNING"
    REPAIRING = "REPAIRING"
    TESTING_REPAIR = "TESTING_REPAIR"
    REPAIR_SUCCESS = "REPAIR_SUCCESS"
    REPAIR_EXHAUSTED = "REPAIR_EXHAUSTED"
    
    # Validation & Git finalization states
    VALIDATING = "VALIDATING"
    BRANCH_CREATED = "BRANCH_CREATED"
    COMMITTING = "COMMITTING"
    COMMITTED = "COMMITTED"
    PUSHING = "PUSHING"
    PUSHED = "PUSHED"
    CREATING_PR = "CREATING_PR"
    PR_CREATED = "PR_CREATED"
    COMPLETED = "COMPLETED"
    FIXING = "FIXING"
    
    # Terminal failure and cancellation states
    FAILED = "FAILED"
    VALIDATION_FAILED = "VALIDATION_FAILED"
    BRANCH_CREATION_FAILED = "BRANCH_CREATION_FAILED"
    COMMIT_FAILED = "COMMIT_FAILED"
    PUSH_FAILED = "PUSH_FAILED"
    PR_CREATION_FAILED = "PR_CREATION_FAILED"
    CANCELLED = "CANCELLED"


# States in which a run has finished and no worker will touch it again.
# PLAN_READY is terminal when ENABLE_CODE_MUTATION is off.
TERMINAL_AGENT_STATES = [
    AgentStateEnum.COMPLETED,
    AgentStateEnum.FAILED,
    AgentStateEnum.PR_CREATED,
    AgentStateEnum.REPAIR_EXHAUSTED,
    AgentStateEnum.VALIDATION_FAILED,
    AgentStateEnum.BRANCH_CREATION_FAILED,
    AgentStateEnum.COMMIT_FAILED,
    AgentStateEnum.PUSH_FAILED,
    AgentStateEnum.PR_CREATION_FAILED,
    AgentStateEnum.CANCELLED,
    AgentStateEnum.PLAN_READY,
]


class User(Base):
    __tablename__ = "users"
    id = Column(Integer, primary_key=True, index=True)
    github_user_id = Column(String, index=True, unique=True, nullable=False)
    github_login = Column(String, nullable=False)
    github_name = Column(String, nullable=True)
    github_avatar_url = Column(String, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    repositories = relationship("Repository", back_populates="user")
    sessions = relationship("Session", back_populates="user", cascade="all, delete-orphan")

class Session(Base):
    __tablename__ = "sessions"
    id = Column(Integer, primary_key=True, index=True)
    session_id = Column(String, index=True, unique=True, nullable=False)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    expires_at = Column(DateTime, nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow)
    
    user = relationship("User", back_populates="sessions")

class Repository(Base):
    __tablename__ = "repositories"
    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=True)
    github_repo_id = Column(String, index=True, unique=True, nullable=False)
    owner = Column(String, nullable=False)
    name = Column(String, nullable=False)
    full_name = Column(String, nullable=False, unique=True)
    default_branch = Column(String, default="main")
    clone_url = Column(String, nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    user = relationship("User", back_populates="repositories")
    issues = relationship("Issue", back_populates="repository")
    code_chunks = relationship("CodeChunk", back_populates="repository")

class Issue(Base):
    __tablename__ = "issues"
    id = Column(Integer, primary_key=True, index=True)
    github_issue_id = Column(String, index=True, unique=True, nullable=False)
    repository_id = Column(Integer, ForeignKey("repositories.id"), nullable=False)
    number = Column(Integer, nullable=False)
    title = Column(String, nullable=False)
    body = Column(Text, nullable=True)
    state = Column(String, nullable=False)
    labels = Column(String, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    repository = relationship("Repository", back_populates="issues")
    agent_runs = relationship("AgentRun", back_populates="issue")
    pull_requests = relationship("PullRequest", back_populates="issue")

class AgentRun(Base):
    __tablename__ = "agent_runs"
    id = Column(Integer, primary_key=True, index=True)
    issue_id = Column(Integer, ForeignKey("issues.id"), nullable=False)
    status = Column(SQLEnum(AgentStateEnum), default=AgentStateEnum.RECEIVED, nullable=False)
    current_step = Column(String, nullable=True)
    issue_analysis = Column(JSON, nullable=True)
    solution_plan = Column(JSON, nullable=True)
    modified_files = Column(JSON, nullable=True)
    diff_summary = Column(Text, nullable=True)
    test_result = Column(JSON, nullable=True)
    # Test outcome on the unmodified checkout, used to tell pre-existing or
    # environment failures apart from failures caused by the generated change.
    baseline_test_result = Column(JSON, nullable=True)

    # Phase 7 additions
    repair_attempt = Column(Integer, default=0)
    failure_analysis = Column(JSON, nullable=True)
    repair_plan = Column(JSON, nullable=True)
    termination_reason = Column(String, nullable=True)
    total_runtime = Column(Float, default=0.0)
    repair_history = Column(JSON, nullable=True)
    started_at = Column(DateTime, default=datetime.utcnow)
    completed_at = Column(DateTime, nullable=True)
    error_message = Column(Text, nullable=True)
    retry_count = Column(Integer, default=0)

    issue = relationship("Issue", back_populates="agent_runs")
    actions = relationship("AgentAction", back_populates="agent_run")

class CodeChunk(Base):
    __tablename__ = "code_chunks"
    id = Column(Integer, primary_key=True, index=True)
    repository_id = Column(Integer, ForeignKey("repositories.id"), nullable=False)
    file_path = Column(String, nullable=False)
    chunk_index = Column(Integer, nullable=False)
    content = Column(Text, nullable=False)
    embedding = Column(Vector(384), nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)

    repository = relationship("Repository", back_populates="code_chunks")

class AgentAction(Base):
    __tablename__ = "agent_actions"
    id = Column(Integer, primary_key=True, index=True)
    agent_run_id = Column(Integer, ForeignKey("agent_runs.id"), nullable=False)
    action_type = Column(String, nullable=False)
    input_data = Column(Text, nullable=True)
    output_data = Column(Text, nullable=True)
    success = Column(Boolean, default=True)
    created_at = Column(DateTime, default=datetime.utcnow)

    agent_run = relationship("AgentRun", back_populates="actions")

class PullRequest(Base):
    __tablename__ = "pull_requests"
    id = Column(Integer, primary_key=True, index=True)
    issue_id = Column(Integer, ForeignKey("issues.id"), nullable=False)
    github_pr_id = Column(String, nullable=False)
    branch_name = Column(String, nullable=False)
    pr_url = Column(String, nullable=False)
    status = Column(String, default="open")
    created_at = Column(DateTime, default=datetime.utcnow)
    merged_at = Column(DateTime, nullable=True)

    issue = relationship("Issue", back_populates="pull_requests")