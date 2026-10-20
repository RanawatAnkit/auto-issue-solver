from typing import Any, Dict, List, Literal, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from app.api.auth import get_current_user
from app.config import settings
from app.eval import jobs
from app.eval.harness import AGENT, GOLD, load_tasks
from app.eval.store import ResultsStore, summarize
from app.models.domain import User

router = APIRouter()


class RunEvaluationRequest(BaseModel):
    task_ids: Optional[List[str]] = None
    # "gold" grades the reference patches: it checks the environments, not the agent.
    mode: Literal["agent", "gold"] = AGENT
    allow_network: bool = False


def _store() -> ResultsStore:
    return ResultsStore(settings.EVAL_RESULTS_DIR)


@router.get("/stats")
def get_evaluation_stats(current_user: User = Depends(get_current_user)) -> Dict[str, Any]:
    """Headline numbers over the latest agent result for each benchmark task."""
    return summarize(load_tasks(), _store())


@router.get("/tasks")
def get_benchmark_tasks(current_user: User = Depends(get_current_user)) -> List[Dict[str, Any]]:
    """Benchmark tasks with their latest agent result and environment (gold) check."""
    store = _store()
    agent, gold = store.latest(AGENT), store.latest(GOLD)
    return [{**t, "latest_result": agent.get(t["instance_id"]), "environment_check": gold.get(t["instance_id"])}
            for t in load_tasks()]


@router.get("/history")
def get_evaluation_history(limit: int = 200, current_user: User = Depends(get_current_user)) -> List[Dict[str, Any]]:
    """Most recent results first."""
    return list(reversed(_store().history()))[:max(limit, 0)]


@router.get("/status")
def get_evaluation_status(current_user: User = Depends(get_current_user)) -> Dict[str, Any]:
    return jobs.runner.status()


@router.post("/run", status_code=202)
def trigger_evaluation(req: RunEvaluationRequest, current_user: User = Depends(get_current_user)) -> Dict[str, Any]:
    """Start a background run over the given tasks (default: all). Poll /evaluation/status."""
    tasks = load_tasks()
    if req.task_ids:
        unknown = sorted(set(req.task_ids) - {t["instance_id"] for t in tasks})
        if unknown:
            raise HTTPException(status_code=400, detail=f"Unknown benchmark task(s): {', '.join(unknown)}")
        tasks = [t for t in tasks if t["instance_id"] in req.task_ids]
    try:
        return jobs.runner.start(tasks, mode=GOLD if req.mode == GOLD else AGENT, allow_network=req.allow_network)
    except jobs.JobAlreadyRunning as e:
        raise HTTPException(status_code=409, detail=str(e))


@router.post("/stop")
def stop_evaluation(current_user: User = Depends(get_current_user)) -> Dict[str, Any]:
    """Stop after the task currently being evaluated."""
    jobs.runner.request_stop()
    return jobs.runner.status()
