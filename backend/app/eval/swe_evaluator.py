"""SWE-bench evaluation status.

No genuine evaluation is implemented yet: resolving a task requires applying a
generated patch and running the instance's FAIL_TO_PASS / PASS_TO_PASS tests in
the official SWE-bench harness. Until that exists, nothing may be reported as
resolved. Earlier versions of this module synthesized a placeholder diff and
marked every task RESOLVED; such history entries are ignored here.
"""
import os
import json
import logging
from typing import List, Dict, Any, Optional
from app.config import settings

logger = logging.getLogger(__name__)

TASKS_FILE = os.path.join(os.path.dirname(__file__), "benchmark_tasks.json")
RESULTS_FILE = os.path.join(os.path.dirname(__file__), "eval_history.json")

NOT_EVALUATED = "NOT_EVALUATED"
NOT_IMPLEMENTED_REASON = (
    "Genuine SWE-bench evaluation is not implemented: no patch is applied and no "
    "FAIL_TO_PASS/PASS_TO_PASS tests are run, so no task can be reported as resolved."
)


def is_genuine_result(entry: Dict[str, Any]) -> bool:
    """Only results produced by a real test harness run count."""
    return isinstance(entry, dict) and entry.get("verified_by_harness") is True


class SWEEvaluator:
    def __init__(self):
        self.tasks = self._load_tasks()

    def _load_tasks(self) -> List[Dict[str, Any]]:
        if not os.path.exists(TASKS_FILE):
            return []
        try:
            with open(TASKS_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception as e:
            logger.error("Failed to load benchmark tasks: %s", e)
            return []

    def _load_history(self) -> List[Dict[str, Any]]:
        """Harness-verified results only; fabricated legacy entries are dropped."""
        if not os.path.exists(RESULTS_FILE):
            return []
        try:
            with open(RESULTS_FILE, "r", encoding="utf-8") as f:
                history = json.load(f)
        except Exception as e:
            logger.error("Failed to load evaluation history: %s", e)
            return []
        genuine = [h for h in history if is_genuine_result(h)] if isinstance(history, list) else []
        ignored = (len(history) if isinstance(history, list) else 0) - len(genuine)
        if ignored:
            logger.warning("Ignoring %d evaluation history entries not produced by a test harness", ignored)
        return genuine

    def get_tasks_with_status(self) -> List[Dict[str, Any]]:
        tasks = self._load_tasks()
        history = self._load_history()

        latest_results = {}
        for h in history:
            iid = h.get("instance_id")
            if iid and (iid not in latest_results or h.get("completed_at", "") > latest_results[iid].get("completed_at", "")):
                latest_results[iid] = h

        enhanced_tasks = []
        for t in tasks:
            t_copy = dict(t)
            res = latest_results.get(t["instance_id"])
            # "PENDING" (not yet evaluated) is the label the dashboard already understands.
            t_copy["latest_status"] = res.get("status", "PENDING") if res else "PENDING"
            t_copy["latest_runtime"] = res.get("runtime_seconds") if res else None
            t_copy["latest_provider"] = res.get("provider") if res else None
            t_copy["latest_patch"] = res.get("patch_diff") if res else None
            t_copy["latest_analysis"] = res.get("analysis") if res else None
            enhanced_tasks.append(t_copy)
        return enhanced_tasks

    def get_stats(self) -> Dict[str, Any]:
        tasks = self.get_tasks_with_status()

        resolved = sum(1 for t in tasks if t.get("latest_status") == "RESOLVED")
        failed = sum(1 for t in tasks if t.get("latest_status") == "FAILED")
        evaluated = resolved + failed

        pass_rate = round((resolved / evaluated * 100), 1) if evaluated > 0 else 0.0
        runtimes = [t["latest_runtime"] for t in tasks if t.get("latest_runtime")]
        avg_runtime = round(sum(runtimes) / len(runtimes), 1) if runtimes else 0.0

        return {
            "total_benchmark_tasks": len(tasks),
            "total_evaluated": evaluated,
            "resolved_count": resolved,
            "failed_count": failed,
            "pass_rate_percentage": pass_rate,
            "average_resolution_time_seconds": avg_runtime,
            "active_provider": getattr(settings, "LLM_PROVIDER", "ollama"),
            "active_model": getattr(settings, "OLLAMA_MODEL", "qwen2.5-coder:7b") if getattr(settings, "LLM_PROVIDER", "ollama") == "ollama" else getattr(settings, "GEMINI_MODEL", "gemini-3.8-flash"),
            "benchmark_dataset": "Unverified task list (not official SWE-bench rows)",
            "evaluation_available": False,
            "evaluation_note": NOT_IMPLEMENTED_REASON,
        }

    def evaluate_task(self, task: Dict[str, Any], provider: Optional[str] = None) -> Dict[str, Any]:
        """Never fabricates an outcome; nothing is run and nothing is recorded."""
        return {
            "instance_id": task.get("instance_id"),
            "repo": task.get("repo"),
            "provider": provider or getattr(settings, "LLM_PROVIDER", "ollama"),
            "status": NOT_EVALUATED,
            "reason": NOT_IMPLEMENTED_REASON,
        }
