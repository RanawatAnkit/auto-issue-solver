import os
import re
import json
import time
import logging
from typing import Dict, Any, List, Callable, Optional
from app.config import settings
from app.agents.failure_analyzer import FailureAnalyzer
from app.agents.repair_planner import RepairPlanner
from app.agents.coding_agent import CodingAgent
from app.agents.planner import normalize_repo_path, check_path_safety, is_test_file
from app.agents.prompting import files_block
from app.services.repository_scanner import list_tracked_files
from app.sandbox.test_runner import TestRunner, ENVIRONMENT_OUTCOMES

logger = logging.getLogger(__name__)


def failure_signature(test_result: dict) -> tuple:
    """Identify a failure by which tests failed and how, independent of LLM wording."""
    output = (test_result.get("stdout") or "") + "\n" + (test_result.get("stderr") or "")
    ids = sorted(set(re.findall(r"^(?:FAILED|ERROR) (\S+)", output, re.M)))
    errors = sorted(set(re.findall(r"^E\s+(\w+(?:Error|Exception)\b[^\n]{0,120})", output, re.M)))
    return (test_result.get("outcome"), test_result.get("exit_code"), tuple(ids), tuple(errors[:10]))


def _traceback_files(test_result: dict, tracked: set) -> List[str]:
    output = (test_result.get("stdout") or "") + "\n" + (test_result.get("stderr") or "")
    found = []
    for m in re.finditer(r"([\w./\\-]+\.(?:py|js|ts|jsx|tsx))(?::\d+|\", line \d+)", output):
        # Paths inside the sandbox are reported relative to /workspace.
        p = normalize_repo_path(m.group(1)).lstrip("/")
        if p.startswith("workspace/"):
            p = p[len("workspace/"):]
        if p in tracked and p not in found:
            found.append(p)
    return found


def _is_environment_failure(test_result: dict, baseline: dict | None) -> bool:
    verification = test_result.get("verification")
    if isinstance(verification, dict) and verification.get("valid") is False:
        return True
    if test_result.get("outcome") in ENVIRONMENT_OUTCOMES:
        # If the untouched checkout failed the same way, the sandbox (not the change) is at fault.
        # An environment failure that only appears after the change (e.g. a newly
        # imported package that is not installed) is something the code can fix.
        return baseline is None or baseline.get("outcome") in ENVIRONMENT_OUTCOMES
    return False


def execute_repair_loop(run_id: int, initial_context: dict, workspace_path: str = None,
                        on_progress: Optional[Callable[[str, str], None]] = None) -> dict:
    '''
    Bounded repair loop: analyze the failing test output, plan a targeted repair,
    apply validated edits and re-run the tests, until the tests pass or a limit
    (attempts, time budget, repeated identical failure, environment failure) is hit.
    Success is only reported when the sandbox tests actually pass.
    '''
    MAX_ATTEMPTS = getattr(settings, "MAX_REPAIR_ATTEMPTS", 3)
    time_budget = getattr(settings, "REPAIR_TIME_BUDGET_SECONDS", 3600)
    history: List[Dict[str, Any]] = []
    progress = on_progress or (lambda status, step: None)

    workspace_path = workspace_path or initial_context.get("workspace_path")
    if not workspace_path or not os.path.isdir(workspace_path):
        raise ValueError("execute_repair_loop requires an existing workspace_path")

    issue_context = str(initial_context.get("issue_context", ""))
    solution_plan = initial_context.get("solution_plan", {})
    last_test_res = initial_context.get("test_result") or {}
    baseline = initial_context.get("baseline_test_result")
    modified_files: List[str] = list(initial_context.get("modified_files") or [])
    tracked_files = initial_context.get("tracked_files") or list_tracked_files(workspace_path)

    analyzer = FailureAnalyzer()
    planner = RepairPlanner()
    coder = CodingAgent()
    runner = TestRunner(workspace_path)

    started = time.monotonic()
    seen_signatures = {failure_signature(last_test_res)}
    last_analysis, last_plan = None, None

    def result(status, reason, attempts):
        return {
            "status": status, "termination_reason": reason, "attempts": attempts,
            "repair_history": history, "test_result": last_test_res, "modified_files": modified_files,
            "failure_analysis": last_analysis, "repair_plan": last_plan,
        }

    for attempt in range(1, MAX_ATTEMPTS + 1):
        logger.info(f"Autonomous Repair Loop: Attempt {attempt}/{MAX_ATTEMPTS} for Run {run_id}")

        if _is_environment_failure(last_test_res, baseline):
            logger.error("Tests cannot run in the sandbox environment; code changes cannot fix this. Terminating.")
            return result("REPAIR_EXHAUSTED", "ENVIRONMENT_FAILURE", attempt)
        if time.monotonic() - started > time_budget:
            logger.warning(f"Repair time budget of {time_budget}s exhausted for Run {run_id}")
            return result("REPAIR_EXHAUSTED", "TIME_BUDGET", attempt - 1)

        tracked_now = set(list_tracked_files(workspace_path)) | set(modified_files)
        test_output = (last_test_res.get("stdout") or "") + "\n" + (last_test_res.get("stderr") or "")
        tb_files = _traceback_files(last_test_res, tracked_now)
        code_context, _ = files_block(workspace_path, modified_files + tb_files, 12000)
        attempt_record: Dict[str, Any] = {"attempt": attempt}

        # 1. Failure analysis
        progress("ANALYZING_FAILURE", f"Repair attempt {attempt}/{MAX_ATTEMPTS}: analyzing test failure")
        try:
            analysis = analyzer.generate_analysis(
                issue_context=issue_context,
                plan=json.dumps(solution_plan, default=str),
                previous_changes=", ".join(modified_files) or "(none)",
                test_output=test_output,
                code_context=code_context,
            )
            last_analysis = analysis.model_dump()
        except Exception as e:
            logger.error(f"Repair failure analysis error: {e}")
            history.append({**attempt_record, "error": f"Failure analysis failed: {e}"})
            return result("REPAIR_EXHAUSTED", "LLM_ERROR", attempt)
        attempt_record["analysis"] = last_analysis

        # 2. Repair plan
        progress("REPAIR_PLANNING", f"Repair attempt {attempt}/{MAX_ATTEMPTS}: planning repair")
        try:
            repair_plan = planner.generate_plan(
                analysis_json=json.dumps(last_analysis),
                previous_plan=json.dumps(solution_plan, default=str),
                context=issue_context,
                repository_files="\n".join(sorted(tracked_now)),
            )
            last_plan = repair_plan.model_dump()
        except Exception as e:
            logger.error(f"Repair planning error: {e}")
            history.append({**attempt_record, "error": f"Repair planning failed: {e}"})
            return result("REPAIR_EXHAUSTED", "LLM_ERROR", attempt)
        attempt_record["repair_plan"] = last_plan

        # Only repair files that exist (or new test files); fall back to what we already touched.
        targets = []
        for p in last_plan.get("affected_files", []) + last_analysis.get("affected_files", []):
            p = normalize_repo_path(p)
            try:
                check_path_safety(p)
            except ValueError:
                continue
            if (p in tracked_now or is_test_file(p)) and p not in targets:
                targets.append(p)
        if not targets:
            targets = [p for p in modified_files + tb_files if p in tracked_now]
        attempt_record["target_files"] = targets
        if not targets:
            history.append({**attempt_record, "error": "Repair plan did not identify any repository file to change."})
            continue

        # 3. Grounded code generation, validated against the workspace
        progress("REPAIRING", f"Repair attempt {attempt}/{MAX_ATTEMPTS}: applying repair to {', '.join(targets)}")
        try:
            metrics = coder.implement(
                workspace_path,
                analysis={"original_issue": issue_context, "failure_analysis": last_analysis},
                plan=last_plan,
                target_files=targets,
                tracked_files=sorted(tracked_now),
                extra_context_files=tb_files,
            )
        except Exception as e:
            # PatchError: the model never produced an applicable change; LLM errors land here too.
            logger.error(f"Repair patch application error: {e}")
            history.append({**attempt_record, "error": f"No valid repair could be applied: {e}"})
            continue
        for p in metrics.get("changed_files", []):
            if p not in modified_files:
                modified_files.append(p)
        attempt_record["changed_files"] = metrics.get("changed_files", [])
        attempt_record["diff"] = metrics.get("summary", "")[:20000]

        # 4. Sandbox test verification
        progress("TESTING_REPAIR", f"Repair attempt {attempt}/{MAX_ATTEMPTS}: re-running tests")
        new_test_res = runner.run_tests()
        attempt_record["test_result"] = new_test_res
        history.append(attempt_record)
        last_test_res = new_test_res

        if new_test_res.get("success", False):
            logger.info(f"Autonomous Repair Succeeded on attempt {attempt} for Run {run_id}")
            return result("REPAIR_SUCCESS", None, attempt)

        signature = failure_signature(new_test_res)
        if signature in seen_signatures:
            logger.warning(f"Repair attempt {attempt} reproduced an earlier failure exactly. Terminating repair loop.")
            return result("REPAIR_EXHAUSTED", "REPEATED_FAILURE", attempt)
        seen_signatures.add(signature)

    logger.warning(f"Autonomous Repair Exhausted all {MAX_ATTEMPTS} attempts for Run {run_id}")
    return result("REPAIR_EXHAUSTED", "MAX_ATTEMPTS", MAX_ATTEMPTS)
