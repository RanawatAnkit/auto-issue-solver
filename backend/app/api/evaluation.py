from fastapi import APIRouter, Depends, HTTPException, BackgroundTasks
from pydantic import BaseModel
from typing import List, Optional, Dict, Any
from app.eval.swe_evaluator import SWEEvaluator, NOT_IMPLEMENTED_REASON
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
    """Disabled: refuse rather than report placeholder outcomes (see swe_evaluator)."""
    raise HTTPException(status_code=501, detail=NOT_IMPLEMENTED_REASON)
