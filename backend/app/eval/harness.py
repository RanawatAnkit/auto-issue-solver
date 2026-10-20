"""SWE-bench harness: run the agent on a benchmark instance and grade its patch.

For every instance:
  1. check out the repository at `base_commit`;
  2. (agent mode) run the issue -> analysis -> plan -> code pipeline on the
     problem statement and take `git diff` as the model patch, or
     (gold mode) use the reference patch to validate the environment;
  3. in a clean checkout apply the patch, reset the files touched by the
     hidden `test_patch`, apply it, install the repo in Docker and run those
     test files;
  4. grade the log against FAIL_TO_PASS / PASS_TO_PASS.

The agent only ever sees the problem statement and the repository: the gold
patch, the test patch and the test lists are used for grading only.
"""
import json
import logging
import os
import time
import traceback
import uuid
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from typing import Callable, Iterable

import openai

from app.config import settings
from app.eval import grading
from app.eval.environments import UnsupportedTask, spec_for
from app.eval.repos import GitError, RepoCache, apply_patch, diff_against, git, remove_workspace
from app.eval.sandbox import EnvironmentFailure, EvalSandbox
from app.services.llm_service import (
    LLMOutputError, LLMRequestError, LLMUnavailableError, _is_quota_error,
)
from app.services.patch_service import PatchError

logger = logging.getLogger(__name__)

# Outcomes. The first four mean the agent got a fair attempt and count towards
# the resolution rate; the others are infrastructure problems that say nothing
# about the agent and are reported separately.
RESOLVED = "RESOLVED"
UNRESOLVED = "UNRESOLVED"
NO_PATCH = "NO_PATCH"
PATCH_APPLY_FAILED = "PATCH_APPLY_FAILED"
LLM_ERROR = "LLM_ERROR"
ENV_ERROR = "ENV_ERROR"
HARNESS_ERROR = "HARNESS_ERROR"
ATTEMPTED_OUTCOMES = (RESOLVED, UNRESOLVED, NO_PATCH, PATCH_APPLY_FAILED)

AGENT, GOLD = "agent", "gold"
_LOG_TAIL_CHARS = 6000


@dataclass
class TaskResult:
    run_id: str
    instance_id: str
    repo: str
    mode: str
    outcome: str
    detail: str = ""
    provider: str | None = None
    model: str | None = None
    runtime_seconds: float = 0.0
    started_at: str = ""
    completed_at: str = ""
    patch: str = ""
    tests: dict | None = None
    analysis: dict | None = None
    plan: dict | None = None
    test_log_tail: str = ""
    stage_seconds: dict = field(default_factory=dict)
    # LLM errors that will repeat for every remaining task (quota, bad key/model).
    fatal: bool = False
    # Written to the artifacts directory, not into the history record.
    full_test_log: str = field(default="", repr=False)

    @property
    def resolved(self) -> bool:
        return self.outcome == RESOLVED

    def to_dict(self) -> dict:
        data = asdict(self)
        data.pop("full_test_log")
        data["resolved"] = self.resolved
        return data


class AgentStageError(Exception):
    def __init__(self, outcome: str, detail: str, fatal: bool = False):
        super().__init__(detail)
        self.outcome, self.detail, self.fatal = outcome, detail, fatal


def _chain(exc: BaseException):
    seen = set()
    while exc is not None and id(exc) not in seen:
        seen.add(id(exc))
        yield exc
        exc = exc.__cause__ or exc.__context__


def classify_agent_exception(exc: Exception) -> AgentStageError:
    """Decide whether an exception from the agent pipeline is the agent's fault or the provider's."""
    chain = list(_chain(exc))
    message = str(exc)[:1500]
    if any(isinstance(e, PatchError) for e in chain):
        return AgentStageError(NO_PATCH, f"No applicable code change: {message}")
    if any(_is_quota_error(e) for e in chain):
        return AgentStageError(LLM_ERROR, f"LLM quota or rate limit exhausted: {message}", fatal=True)
    if any(isinstance(e, (openai.AuthenticationError, openai.PermissionDeniedError, openai.NotFoundError,
                          LLMRequestError)) for e in chain) or "API_KEY not configured" in message:
        return AgentStageError(LLM_ERROR, f"LLM provider rejected the request (check key/model): {message}",
                               fatal=True)
    if any(isinstance(e, LLMOutputError) for e in chain):
        # The model answered but never produced output valid for the schema: an agent failure.
        return AgentStageError(NO_PATCH, f"Model output never satisfied the schema: {message}")
    if any(isinstance(e, (LLMUnavailableError, openai.APIError)) for e in chain):
        return AgentStageError(LLM_ERROR, f"LLM provider unavailable: {message}")
    if isinstance(exc, ValueError) and "Plan" in message:
        return AgentStageError(NO_PATCH, f"No valid plan: {message}")
    return AgentStageError(HARNESS_ERROR, f"{type(exc).__name__} in agent pipeline: {message}")


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def llm_identity() -> tuple[str, str]:
    from app.services.llm_service import LLMService
    try:
        llm = LLMService()
        return llm.primary["name"], str(llm.primary["model"])
    except Exception:
        return str(getattr(settings, "LLM_PROVIDER", "unknown")), "unknown"


# What the agent may see. Everything else in a task (gold patch, test patch,
# test lists) is grading data.
AGENT_VISIBLE_FIELDS = ("instance_id", "repo", "problem_statement")


def agent_view(task: dict) -> dict:
    return {k: task[k] for k in AGENT_VISIBLE_FIELDS}


def run_agent(workspace: str, task: dict, trace: dict) -> None:
    """Run the production agent stages on a checkout; edits land in `workspace`.

    Each stage's output is put in `trace` as soon as it exists, so a run that fails
    later (e.g. in code generation) still records the analysis and plan it worked from.
    """
    from app.agents.issue_analyzer import IssueAnalyzer
    from app.agents.context_builder import ContextBuilder
    from app.agents.planner import PlannerAgent
    from app.agents.coding_agent import CodingAgent
    from app.services.repository_scanner import list_tracked_files

    statement = task["problem_statement"].strip()
    title = statement.split("\n", 1)[0][:200]
    tracked = list_tracked_files(workspace)
    analysis = IssueAnalyzer().analyze(title, statement, "", task["repo"], tracked)
    trace["analysis"] = analysis.model_dump()
    # No database here: retrieval is lexical over the checkout.
    context = ContextBuilder(None).build_context(0, analysis, workspace, tracked)
    trace["context_files"] = [c.file_path for c in context]
    plan = PlannerAgent().create_plan(analysis, context, task["repo"], tracked)
    trace["plan"] = plan.model_dump()
    CodingAgent().implement(
        workspace,
        analysis=analysis.model_dump(),
        plan=plan.model_dump(),
        target_files=[f.path for f in plan.affected_files if f.operation != "delete"],
        tracked_files=tracked,
        extra_context_files=[c.file_path for c in context],
    )


class Harness:
    def __init__(self, cache_dir: str | None = None, allow_network: bool = False,
                 test_timeout: int | None = None, sandbox: EvalSandbox | None = None,
                 agent: Callable[[str, dict, dict], None] = run_agent):
        self.repos = RepoCache(cache_dir or settings.EVAL_CACHE_DIR)
        self.allow_network = allow_network
        self.test_timeout = test_timeout or settings.EVAL_TEST_TIMEOUT_SECONDS
        self._sandbox = sandbox
        self.agent = agent

    @property
    def sandbox(self) -> EvalSandbox:
        if self._sandbox is None:
            self._sandbox = EvalSandbox()
        return self._sandbox

    # ---- grading ---------------------------------------------------------------

    def grade_patch(self, task: dict, patch: str) -> tuple[str, str, dict | None, str]:
        """Apply `patch` + the hidden test patch to a clean checkout and run the tests.

        Returns (outcome, detail, test report dict, full test log).
        """
        spec = spec_for(task["repo"], task["version"])
        if spec.needs_network and not self.allow_network:
            return ENV_ERROR, ("This instance's tests call external services; rerun with network "
                               "allowed (--allow-network) to grade it."), None, ""
        test_files = grading.test_files_from_patch(task["test_patch"])
        workspace = self.repos.checkout(task["repo"], task["base_commit"])
        try:
            try:
                apply_patch(workspace, patch)
            except GitError as e:
                return PATCH_APPLY_FAILED, f"Patch does not apply to {task['base_commit'][:12]}: {e}", None, ""
            # The hidden tests replace whatever the patch did to the same files.
            existing = [f for f in test_files if _exists_at(workspace, task["base_commit"], f)]
            if existing:
                git(workspace, "checkout", task["base_commit"], "--", *existing)
            for f in test_files:
                if f not in existing and os.path.exists(os.path.join(workspace, f)):
                    os.remove(os.path.join(workspace, f))
            try:
                apply_patch(workspace, task["test_patch"])
            except GitError as e:
                return HARNESS_ERROR, f"The benchmark's test patch does not apply: {e}", None, ""

            env_tag = self.sandbox.ensure_env_image(task["repo"], task["version"], spec)
            run = self.sandbox.run_tests(workspace, env_tag, spec, grading.test_directives(task["test_patch"]),
                                         network=spec.needs_network and self.allow_network,
                                         timeout=self.test_timeout)
        finally:
            remove_workspace(workspace)

        report = grading.grade(grading.parse_pytest_log(run.log), task["FAIL_TO_PASS"], task["PASS_TO_PASS"])
        if run.timed_out:
            return UNRESOLVED, f"Tests timed out after {self.test_timeout}s. {report.summary()}", report.to_dict(), run.log
        if report.resolved:
            return RESOLVED, report.summary(), report.to_dict(), run.log
        return UNRESOLVED, report.summary(), report.to_dict(), run.log

    # ---- one instance ------------------------------------------------------------

    def evaluate(self, task: dict, mode: str = AGENT, run_id: str | None = None) -> TaskResult:
        started = time.monotonic()
        provider, model = llm_identity() if mode == AGENT else (None, None)
        result = TaskResult(run_id=run_id or new_run_id(), instance_id=task["instance_id"], repo=task["repo"],
                            mode=mode, outcome=HARNESS_ERROR, provider=provider, model=model, started_at=_now())
        try:
            spec_for(task["repo"], task["version"])
            if mode == GOLD:
                result.patch = task["patch"]
            else:
                stage = time.monotonic()
                result.patch = self._agent_patch(task, result)
                result.stage_seconds["agent"] = round(time.monotonic() - stage, 1)
            if not result.patch.strip():
                result.outcome, result.detail = NO_PATCH, "The agent did not change any file."
            else:
                stage = time.monotonic()
                result.outcome, result.detail, result.tests, result.full_test_log = self.grade_patch(task, result.patch)
                result.test_log_tail = result.full_test_log[-_LOG_TAIL_CHARS:]
                result.stage_seconds["grading"] = round(time.monotonic() - stage, 1)
        except AgentStageError as e:
            result.outcome, result.detail, result.fatal = e.outcome, e.detail, e.fatal
        except UnsupportedTask as e:
            result.outcome, result.detail = ENV_ERROR, str(e)
        except EnvironmentFailure as e:
            result.outcome, result.detail = ENV_ERROR, str(e)[-3000:]
        except Exception as e:
            logger.error("Harness error on %s: %s\n%s", task["instance_id"], e, traceback.format_exc())
            result.outcome, result.detail = HARNESS_ERROR, f"{type(e).__name__}: {str(e)[:2000]}"
        result.runtime_seconds = round(time.monotonic() - started, 1)
        result.completed_at = _now()
        logger.info("%s [%s] -> %s (%.0fs): %s", task["instance_id"], mode, result.outcome,
                    result.runtime_seconds, result.detail[:200])
        return result

    def _agent_patch(self, task: dict, result: TaskResult) -> str:
        workspace = self.repos.checkout(task["repo"], task["base_commit"])
        try:
            trace: dict = {}
            try:
                self.agent(workspace, agent_view(task), trace)
            except Exception as e:
                raise classify_agent_exception(e) from e
            finally:
                result.analysis, result.plan = trace.get("analysis"), trace.get("plan")
            return diff_against(workspace, task["base_commit"])
        finally:
            remove_workspace(workspace)

    # ---- batches -------------------------------------------------------------------

    def split_runnable(self, tasks: Iterable[dict]) -> tuple[list[dict], list[str]]:
        """(tasks this harness can grade, ids skipped because they need network that was not allowed).

        Skipped tasks are not recorded at all, so a run without network does not
        overwrite an earlier real result with "network not allowed".
        """
        runnable, skipped = [], []
        for task in tasks:
            try:
                needs_network = spec_for(task["repo"], task["version"]).needs_network
            except UnsupportedTask:
                needs_network = False  # evaluate() records the ENV_ERROR
            (skipped.append(task["instance_id"]) if needs_network and not self.allow_network
             else runnable.append(task))
        return runnable, skipped

    def evaluate_many(self, tasks: Iterable[dict], mode: str = AGENT, run_id: str | None = None,
                      on_result: Callable[[TaskResult], None] | None = None,
                      should_stop: Callable[[], bool] | None = None) -> tuple[list[TaskResult], str | None]:
        """Evaluate tasks in order. Stops early (returning the reason) on an LLM error that would
        repeat for every remaining task, so a spent quota is not recorded as 30 agent failures."""
        run_id = run_id or new_run_id()
        results: list[TaskResult] = []
        for task in tasks:
            if should_stop and should_stop():
                return results, "Stopped on request."
            result = self.evaluate(task, mode=mode, run_id=run_id)
            results.append(result)
            if on_result:
                on_result(result)
            if result.fatal:
                return results, f"Stopped after {task['instance_id']}: {result.detail[:300]}"
        return results, None


def _exists_at(workspace: str, commit: str, path: str) -> bool:
    try:
        git(workspace, "cat-file", "-e", f"{commit}:{path}")
        return True
    except GitError:
        return False


def new_run_id() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S") + "-" + uuid.uuid4().hex[:6]


def load_tasks(path: str | None = None) -> list[dict]:
    path = path or os.path.join(os.path.dirname(__file__), "benchmark_tasks.json")
    with open(path, encoding="utf-8") as f:
        return json.load(f)
