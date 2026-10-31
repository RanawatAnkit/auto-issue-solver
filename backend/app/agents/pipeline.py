"""The stages every issue goes through, from issue analysis to tested code.

plan_issue: symbol index, context retrieval, localization, issue requirements and the grounded plan.
solve_issue: analysis, planning, baseline tests, code generation, tests and the bounded repair loop.

Shared by the worker and the evaluation harness so a benchmark task is solved the same way as a real issue.
What happens around a solve (cloning, validation, the pull request, grading) stays with the caller.
"""
import logging
from dataclasses import dataclass, field
from typing import Any, Callable, List, Optional

from app.config import settings
from app.services.symbol_index import SymbolIndex
from app.services.patch_service import PatchError
from app.sandbox.test_runner import ENVIRONMENT_OUTCOMES
from app.agents.issue_analyzer import IssueAnalyzer
from app.agents.context_builder import ContextBuilder
from app.agents.localizer import Localizer
from app.agents.requirements import extract_requirements
from app.agents.planner import PlannerAgent
from app.agents.coding_agent import CodingAgent
from app.agents.schemas import LocalizationResult, IssueAnalysis, SolutionPlan
from app.workflows.repair_graph import execute_repair_loop

logger = logging.getLogger(__name__)

# record(action_type, input_data, output_data, success): an audit entry for one stage.
Recorder = Callable[..., None]
# on_stage(state_name, step): the run moves to a new state, e.g. ("PLANNING", "Generating solution plan").
StageCallback = Callable[[str, str], None]
# on_output(name, value): a stage's result, as soon as it exists ("analysis", "plan", "baseline", "patch",
# "test_result"), so a run that fails later still keeps what it worked from.
OutputCallback = Callable[[str, Any], None]
# tests_for(changed_files): something with run_tests() for the checkout once `changed_files` were edited
# (empty: the untouched checkout, for the baseline), or None to skip that test run.
TestsFor = Callable[[List[str]], Any]

# How solve_issue ended; the caller turns each into its own terminal state.
PLAN_ONLY = "PLAN_ONLY"  # code mutation disabled: stopped with the plan
WORKSPACE_MODIFIED = "WORKSPACE_MODIFIED_DURING_PLANNING"
ENVIRONMENT_FAILURE = "ENVIRONMENT_FAILURE"  # the tests cannot run on the untouched checkout
NO_VALID_PATCH = "NO_VALID_PATCH"
NOT_TESTED = "NOT_TESTED"  # a change was made but there were no tests to run on it
TESTS_PASSED = "TESTS_PASSED"  # straight away or after repair
REPAIR_EXHAUSTED = "REPAIR_EXHAUSTED"


@dataclass
class PlanningResult:
    plan: SolutionPlan
    context: list
    symbol_index: Optional[SymbolIndex] = None
    localization: Optional[LocalizationResult] = None
    requirements: List[str] = field(default_factory=list)


def _flag(name: str, default: bool = False) -> bool:
    value = getattr(settings, name, default)
    return value if isinstance(value, bool) else default


def plan_issue(analysis: IssueAnalysis, workspace_path: str, tracked_files: List[str], repo_name: str,
               issue_title: str, issue_body: str, db=None, repo_id: int = 0,
               record: Recorder | None = None, on_stage: StageCallback | None = None) -> PlanningResult:
    """Run the planning stages on a checkout; nothing in it is modified.

    db: session for the semantic index (None: lexical retrieval over the checkout only).
    Raises whatever create_plan raises once its attempts are used up.
    """
    record = record or (lambda *a, **k: None)
    on_stage = on_stage or (lambda *a: None)

    symbol_index = None
    try:
        symbol_index = SymbolIndex.build(workspace_path, tracked_files)
        record("symbol_index", None, {"symbols": len(symbol_index.symbols),
                                      "python_files": len(symbol_index.python_files),
                                      "not_parsed": sorted(symbol_index.parse_errors)[:50]})
    except Exception as sym_err:
        logger.warning(f"Symbol index unavailable; continuing without localization: {sym_err}")
        record("symbol_index", None, f"Symbol index failed: {sym_err}", success=False)

    builder = ContextBuilder(db)
    context = builder.build_context(repo_id, analysis, workspace_path, tracked_files,
                                    symbol_index=symbol_index, issue_text=f"{issue_title}\n{issue_body or ''}")
    record("retrieve_context", None, [c.file_path for c in context])

    localization = None
    if symbol_index is not None and _flag("LOCALIZATION_ENABLED", True):
        on_stage("RETRIEVING_CONTEXT", "Localizing the probable root cause")
        try:
            localization = Localizer(symbol_index).localize(
                issue_title, issue_body, analysis, getattr(builder, "seed_locations", None), context,
                on_round=lambda n, info: record(f"localization_round_{n}", None, info,
                                                success=not info.get("rejected")),
            )
        except Exception as loc_err:
            logger.warning(f"Localization crashed; planning from retrieved context: {loc_err}")
            localization = LocalizationResult(status="fallback", reason=f"localizer error: {type(loc_err).__name__}: {loc_err}")
        if isinstance(localization, LocalizationResult):
            record("localization", localization.status,
                   localization.model_dump(exclude={"locations": {"__all__": {"snippet"}}}),
                   # "seeded" means the model's localization failed: not a success.
                   success=localization.status == "localized")
        else:
            localization = None
    locations = localization.locations if localization and localization.status in ("localized", "seeded") else []
    locations_confirmed = bool(localization and localization.status == "localized")

    on_stage("PLANNING", "Generating solution plan")
    # Explicit requirements from the issue text itself, so the plan cannot silently drop one.
    requirements = extract_requirements(issue_body or "")
    if requirements:
        record("issue_requirements", None, requirements)
    planner = PlannerAgent()
    try:
        plan = planner.create_plan(analysis, context, repo_name, tracked_files,
                                   locations=locations, symbol_index=symbol_index,
                                   locations_confirmed=locations_confirmed, requirements=requirements)
    finally:
        rejected_attempts = getattr(planner, "attempt_errors", None)
        if isinstance(rejected_attempts, list) and rejected_attempts:
            record("plan_rejected_attempts", None, rejected_attempts, success=False)
    return PlanningResult(plan=plan, context=context, symbol_index=symbol_index, localization=localization,
                          requirements=requirements)


def test_evidence(result: dict | None) -> str:
    if not result:
        return "not run"
    counts = result.get("summary") or {}
    counts_str = ", ".join(f"{v} {k}" for k, v in counts.items()) or "no counts reported"
    return f"{result.get('outcome', 'UNKNOWN')} (exit code {result.get('exit_code')}; {counts_str})"


@dataclass
class SolveResult:
    status: str
    analysis: IssueAnalysis
    planning: PlanningResult
    plan: dict  # the plan as handed to the coding agent and the repair loop (with its localization)
    baseline: Optional[dict] = None
    modified_files: List[str] = field(default_factory=list)
    diff_summary: str = ""
    skipped_targets: List[str] = field(default_factory=list)
    test_result: Optional[dict] = None
    repair: Optional[dict] = None
    error: Optional[Exception] = None


def solve_issue(title: str, body: str, labels, repo_name: str, workspace_path: str, tracked_files: List[str],
                tests_for: TestsFor, db=None, repo_id: int = 0, mutate: bool = True,
                changed_files: Callable[[], List[str]] | None = None,
                skip_target: Callable[[str], bool] | None = None, max_repair_attempts: int | None = None,
                run_id: int = 0, issue_context: str | None = None, record: Recorder | None = None,
                on_stage: StageCallback | None = None, on_output: OutputCallback | None = None) -> SolveResult:
    """Solve one issue on a checkout; edits land in `workspace_path`.

    changed_files: lists the checkout's changes, to check that planning left it untouched (None: not checked).
    skip_target: planned files not to edit (e.g. documentation), unless every target is one.
    issue_context: the issue as the repair loop sees it (default: title and body).
    Raises whatever analysis or planning raises; a change that cannot be applied is NO_VALID_PATCH.
    """
    record = record or (lambda *a, **k: None)
    on_stage = on_stage or (lambda *a: None)
    on_output = on_output or (lambda *a: None)

    on_stage("ANALYZING_ISSUE", "Analyzing issue with LLM")
    analysis = IssueAnalyzer().analyze(title, body, labels, repo_name, tracked_files)
    on_output("analysis", analysis.model_dump())
    record("issue_analysis", title, analysis.model_dump())

    on_stage("RETRIEVING_CONTEXT", "Retrieving relevant code")
    planning = plan_issue(analysis, workspace_path, tracked_files, repo_name, title, body,
                          db=db, repo_id=repo_id, record=record, on_stage=on_stage)
    plan = planning.plan.model_dump()
    if planning.localization is not None:
        plan["localization"] = planning.localization.model_dump(
            exclude={"locations": {"__all__": {"snippet"}}, "searches": True})
    on_output("plan", plan)
    record("solution_plan", None, plan)
    result = SolveResult(PLAN_ONLY, analysis, planning, plan)

    if changed_files is not None:
        # Planning is read-only: refuse to go further if anything touched the checkout.
        modified = list(changed_files() or [])
        if modified:
            result.status = WORKSPACE_MODIFIED
            result.error = RuntimeError(f"Planning must not modify the repository, but these files changed: "
                                        f"{modified[:20]}")
            return result
        record("workspace_check", None, "Checkout unchanged after planning")

    if not mutate:
        return result
    on_stage("PLAN_READY", "Solution plan generated successfully")

    baseline_runner = tests_for([])
    if baseline_runner is not None:
        on_stage("TESTING", "Running existing tests on the unmodified repository (baseline)")
        baseline = result.baseline = baseline_runner.run_tests()
        on_output("baseline", baseline)
        record("baseline_tests", baseline.get("command"), test_evidence(baseline),
               success=bool(baseline.get("success")))
        if baseline.get("outcome") in ENVIRONMENT_OUTCOMES:
            result.status = ENVIRONMENT_FAILURE
            result.error = RuntimeError(f"Tests could not run on the unmodified repository "
                                        f"({baseline.get('outcome')}): {(baseline.get('stderr') or '')[-1500:]}")
            return result

    on_stage("CODING", "Generating code changes from solution plan")
    targets = [f.path for f in planning.plan.affected_files if f.operation != "delete"]
    kept = [p for p in targets if not (skip_target and skip_target(p))]
    result.skipped_targets = [p for p in targets if p not in kept]
    try:
        metrics = CodingAgent().implement(
            workspace_path,
            analysis=analysis.model_dump(),
            plan=plan,
            target_files=kept or targets,
            tracked_files=tracked_files,
            extra_context_files=[c.file_path for c in planning.context],
        )
    except PatchError as patch_err:
        # The coding agent has already restored every file it touched.
        record("apply_patch", None, str(patch_err), success=False)
        result.status, result.error = NO_VALID_PATCH, patch_err
        return result
    result.modified_files = list(metrics.get("changed_files", []))
    result.diff_summary = metrics.get("summary", "")
    on_output("patch", {"changed_files": result.modified_files, "summary": result.diff_summary})
    record("apply_patch", None, {"changed_files": result.modified_files})
    on_stage("PATCH_APPLIED", f"Patch applied to {len(result.modified_files)} file(s)")

    runner = tests_for(result.modified_files)
    if runner is None:
        result.status = NOT_TESTED
        return result
    on_stage("TESTING", "Executing tests in isolated sandbox")
    test_res = result.test_result = runner.run_tests()
    on_output("test_result", test_res)
    record("run_tests", test_res.get("command"), test_evidence(test_res), success=bool(test_res.get("success")))

    if not test_res.get("success", False):
        on_stage("TEST_FAILED", f"Tests failed after the change: {test_evidence(test_res)}")
        repair = result.repair = execute_repair_loop(
            run_id=run_id,
            initial_context={
                "issue_context": issue_context or f"{title}\n{body or ''}",
                "solution_plan": plan,
                "test_result": test_res,
                "baseline_test_result": result.baseline,
                "modified_files": list(result.modified_files),
                "tracked_files": tracked_files,
            },
            workspace_path=workspace_path, runner=runner, max_attempts=max_repair_attempts, on_progress=on_stage,
        )
        result.test_result = repair.get("test_result") or result.test_result
        result.modified_files = sorted(set(result.modified_files) | set(repair.get("modified_files") or []))
        record("repair_loop", None, {k: repair.get(k) for k in ("status", "attempts", "termination_reason")},
               success=repair.get("status") == "REPAIR_SUCCESS")
        if repair.get("status") != "REPAIR_SUCCESS":
            result.status = REPAIR_EXHAUSTED
            return result

    on_stage("REPAIR_SUCCESS", f"Tests passed: {test_evidence(result.test_result)}")
    result.status = TESTS_PASSED
    return result
