from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from app.api import health, webhooks, issues, repositories, agent_runs, pull_requests, auth
from app.config import settings

app = FastAPI(title="Auto Issue Solver API")

_frontend = settings.FRONTEND_URL.rstrip("/")
app.add_middleware(
    CORSMiddleware,
    allow_origins=sorted({_frontend, _frontend.replace("localhost", "127.0.0.1")}),
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(health.router, tags=["health"])
app.include_router(webhooks.router, prefix="/webhooks", tags=["webhooks"])
app.include_router(issues.router, prefix="/issues", tags=["issues"])
app.include_router(repositories.router, prefix="/repositories", tags=["repositories"])
app.include_router(agent_runs.router, prefix="/agent-runs", tags=["agent_runs"])
app.include_router(pull_requests.router, prefix="/pull-requests", tags=["pull_requests"])
app.include_router(auth.router, prefix="/auth", tags=["auth"])