from fastapi import APIRouter, Depends, HTTPException, BackgroundTasks
from pydantic import BaseModel
from typing import List, Optional, Dict, Any
from app.eval.swe_evaluator import SWEEvaluator
from app.api.auth import get_current_user
from app.models.domain import User

router = APIRouter()
evaluator = SWEEvaluator()

class RunEvaluationRequest(BaseModel):
    task_ids: Optional[List[str]] = None
    provider: Optional[str] = None

@router.get("/stats")
def get_evaluation_stats(current_user: User = Depends(get_current_user)) -> Dict[str, Any]:
    """Returns overall SWE-bench metrics, resolution rate, and benchmark metadata."""
    return evaluator.get_stats()

@router.get("/tasks")
def get_benchmark_tasks(current_user: User = Depends(get_current_user)) -> List[Dict[str, Any]]:
    """Lists all curated SWE-bench benchmark tasks with their latest evaluation status."""
    return evaluator.get_tasks_with_status()

@router.get("/history")
def get_evaluation_history(current_user: User = Depends(get_current_user)) -> List[Dict[str, Any]]:
    """Returns the historical log of all benchmark evaluation runs."""
    return evaluator._load_history()

@router.post("/run")
def trigger_evaluation(req: RunEvaluationRequest, current_user: User = Depends(get_current_user)) -> Dict[str, Any]:
    """Runs SWE-bench evaluation on specified tasks or all benchmark tasks."""
    tasks = evaluator.get_tasks_with_status()
    if req.task_ids:
        selected_tasks = [t for t in tasks if t["instance_id"] in req.task_ids]
    else:
        selected_tasks = tasks

    if not selected_tasks:
        raise HTTPException(status_code=400, detail="No matching benchmark tasks found.")

    results = []
    for t in selected_tasks:
        res = evaluator.evaluate_task(t, provider=req.provider)
        results.append(res)

    resolved_count = sum(1 for r in results if r.get("status") == "RESOLVED")
    return {
        "status": "completed",
        "total_evaluated": len(results),
        "resolved_count": resolved_count,
        "results": results
    }
