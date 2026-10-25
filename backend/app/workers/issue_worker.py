import json
import re
import time
import logging
import traceback
from datetime import datetime
from app.services import queue_service
from app.services.github_service import GitHubService, GitWorkspace, find_secrets
from app.db.session import SessionLocal
from app.models.domain import AgentRun, AgentStateEnum, Repository, Issue, AgentAction, PullRequest, TERMINAL_AGENT_STATES
from app.config import settings
from app.services.llm_service import verify_ollama_available, track_llm_usage, summarize_llm_usage
from app.services.repository_scanner import RepositoryScanner, list_tracked_files
from app.services.symbol_index import SymbolIndex
from app.agents.issue_analyzer import IssueAnalyzer
from app.agents.context_builder import ContextBuilder
from app.agents.localizer import Localizer
from app.agents.requirements import extract_requirements
from app.agents.planner import PlannerAgent
from app.agents.schemas import LocalizationResult
from app.agents.coding_agent import CodingAgent
from app.services.patch_service import PatchError
from app.sandbox.test_runner import TestRunner, ENVIRONMENT_OUTCOMES
from app.workflows.repair_graph import execute_repair_loop

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

def _scrub_secrets(text: str) -> str:
    """Scrub tokens, secrets, and auth headers from strings."""
    if not text:
        return ""
    scrubbed = re.sub(r'x-access-token:[^@\s]+@', 'x-access-token:***@', text)
    scrubbed = re.sub(r'gh[pousr]_[a-zA-Z0-9]+', '***', scrubbed)
    scrubbed = re.sub(r'github_pat_[A-Za-z0-9_]+', '***', scrubbed)
    scrubbed = re.sub(r'Basic [A-Za-z0-9+/=]+', 'Basic ***', scrubbed)
    scrubbed = re.sub(r'Bearer [A-Za-z0-9._\-]+', 'Bearer ***', scrubbed)
    return scrubbed


def _flag(name: str, default: bool = False) -> bool:
    value = getattr(settings, name, default)
    return value if isinstance(value, bool) else default


class _Run:
    """Small helper that keeps status changes, the action log and timing consistent."""

    def __init__(self, db, run: AgentRun):
        self.db, self.run = db, run

    def status(self, status: AgentStateEnum, step: str):
        self.run.status = status
        self.run.current_step = step
        self.db.commit()
        logger.info(f"Run {self.run.id}: {status.value} - {step}")

    def action(self, action_type: str, input_data=None, output_data=None, success: bool = True):
        def text(v):
            if v is None or isinstance(v, str):
                return _scrub_secrets(v) if v else v
            return _scrub_secrets(json.dumps(v, default=str))[:20000]
        self.db.add(AgentAction(agent_run_id=self.run.id, action_type=action_type,
                                input_data=text(input_data), output_data=text(output_data), success=success))
        self.db.commit()

    def finish(self, status: AgentStateEnum, step: str, error: str | None = None, termination_reason: str | None = None):
        self.run.status = status
        self.run.current_step = step
        if error:
            self.run.error_message = _scrub_secrets(error)
        if termination_reason:
            self.run.termination_reason = termination_reason
        self.run.completed_at = datetime.utcnow()
        if self.run.started_at:
            self.run.total_runtime = (self.run.completed_at - self.run.started_at).total_seconds()
        self.db.commit()
        logger.info(f"Run {self.run.id} finished: {status.value} - {step}")


def _test_evidence(result: dict | None) -> str:
    if not result:
        return "not run"
    counts = result.get("summary") or {}
    counts_str = ", ".join(f"{v} {k}" for k, v in counts.items()) or "no counts reported"
    return f"{result.get('outcome', 'UNKNOWN')} (exit code {result.get('exit_code')}; {counts_str})"


def process_agent_run(payload: dict):
    with track_llm_usage() as usage:
        _process_agent_run(payload, usage)


def _log_llm_usage(db, agent_run_id, usage: list):
    if not usage:
        return
    try:
        db.add(AgentAction(agent_run_id=agent_run_id, action_type="llm_usage",
                           output_data=json.dumps(summarize_llm_usage(usage)), success=True))
        db.commit()
    except Exception as e:
        db.rollback()
        logger.warning(f"Could not record LLM usage for run {agent_run_id}: {e}")


def _process_agent_run(payload: dict, usage: list):
    agent_run_id = payload.get("agent_run_id")
    installation_id = payload.get("installation_id")

    db = SessionLocal()
    workspace = None
    try:
        run = db.query(AgentRun).filter(AgentRun.id == agent_run_id).first()
        if not run:
            logger.error(f"Job references unknown AgentRun {agent_run_id}; skipping")
            return
        if run.status in TERMINAL_AGENT_STATES:
            logger.warning(f"AgentRun {run.id} is already {run.status.value}; ignoring duplicate job")
            return
        tracker = _Run(db, run)

        issue = db.query(Issue).filter(Issue.id == run.issue_id).first()
        repo = db.query(Repository).filter(Repository.id == issue.repository_id).first()

        if not run.started_at:
            run.started_at = datetime.utcnow()

        # Step 1: Repository access and isolated workspace
        tracker.status(AgentStateEnum.ANALYZING_REPOSITORY, f"Cloning repository {repo.full_name}")
        if not installation_id:
            installation_id = GitHubService.get_installation_id_for_repo(repo.full_name)
        github_svc = GitHubService(installation_id)

        # GitHub is the trusted source for the issue text (it may have been edited).
        gh_issue = github_svc.get_issue(repo.full_name, issue.number)
        if isinstance(getattr(gh_issue, "title", None), str):
            issue.title = gh_issue.title
            issue.body = gh_issue.body if isinstance(gh_issue.body, str) else issue.body
            db.commit()

        workspace = GitWorkspace(repo.clone_url)
        token = github_svc.get_installation_token()
        workspace.clone(auth_token=token)

        base_branch = repo.default_branch or "main"
        workspace.checkout_branch(base_branch)
        base_commit_raw = workspace.run_git_command(["rev-parse", "HEAD"])
        base_commit_sha = base_commit_raw.strip() if isinstance(base_commit_raw, str) else "0000000"
        tracked_files = list_tracked_files(workspace.workspace_path)
        tracker.action("repo_clone", repo.clone_url,
                       f"Checked out {base_branch} at {base_commit_sha} ({len(tracked_files)} tracked files)")

        # Best-effort semantic index of this exact revision; lexical retrieval covers failures.
        try:
            from app.services.repository_indexer import RepositoryIndexer
            chunks = RepositoryIndexer(db).index_repository(repo.id, workspace.workspace_path)
            tracker.action("index_repository", None, f"Indexed {chunks} chunks")
        except Exception as idx_err:
            db.rollback()
            logger.warning(f"Repository indexing failed; continuing with lexical retrieval: {idx_err}")
            tracker.action("index_repository", None, f"Indexing failed: {idx_err}", success=False)

        # Step 2: Issue analysis
        tracker.status(AgentStateEnum.ANALYZING_ISSUE, "Analyzing issue with LLM")
        analysis = IssueAnalyzer().analyze(issue.title, issue.body, issue.labels, repo.full_name, tracked_files)
        run.issue_analysis = analysis.model_dump()
        tracker.action("issue_analysis", issue.title, run.issue_analysis)

        # Step 3: Context retrieval, localization and planning
        tracker.status(AgentStateEnum.RETRIEVING_CONTEXT, "Retrieving relevant code")
        symbol_index = None
        try:
            symbol_index = SymbolIndex.build(workspace.workspace_path, tracked_files)
            tracker.action("symbol_index", None, {"symbols": len(symbol_index.symbols),
                                                  "python_files": len(symbol_index.python_files),
                                                  "not_parsed": sorted(symbol_index.parse_errors)[:50]})
        except Exception as sym_err:
            logger.warning(f"Symbol index unavailable; continuing without localization: {sym_err}")
            tracker.action("symbol_index", None, f"Symbol index failed: {sym_err}", success=False)

        builder = ContextBuilder(db)
        context = builder.build_context(repo.id, analysis, workspace.workspace_path, tracked_files,
                                        symbol_index=symbol_index, issue_text=f"{issue.title}\n{issue.body or ''}")
        tracker.action("retrieve_context", None, [c.file_path for c in context])

        localization = None
        if symbol_index is not None and _flag("LOCALIZATION_ENABLED", True):
            tracker.status(AgentStateEnum.RETRIEVING_CONTEXT, "Localizing the probable root cause")
            try:
                localization = Localizer(symbol_index).localize(
                    issue.title, issue.body, analysis, getattr(builder, "seed_locations", None), context,
                    on_round=lambda n, info: tracker.action(f"localization_round_{n}", None, info,
                                                            success=not info.get("rejected")),
                )
            except Exception as loc_err:
                logger.warning(f"Localization crashed; planning from retrieved context: {loc_err}")
                localization = LocalizationResult(status="fallback", reason=f"localizer error: {type(loc_err).__name__}: {loc_err}")
            if isinstance(localization, LocalizationResult):
                tracker.action("localization", localization.status,
                               localization.model_dump(exclude={"locations": {"__all__": {"snippet"}}}),
                               # "seeded" means the model's localization failed: not a success.
                               success=localization.status == "localized")
            else:
                localization = None
        locations = localization.locations if localization and localization.status in ("localized", "seeded") else []
        locations_confirmed = bool(localization and localization.status == "localized")

        tracker.status(AgentStateEnum.PLANNING, "Generating solution plan")
        # Explicit requirements from the issue text itself, so the plan cannot silently drop one.
        requirements = extract_requirements(issue.body or "")
        if requirements:
            tracker.action("issue_requirements", None, requirements)
        planner = PlannerAgent()
        try:
            plan = planner.create_plan(analysis, context, repo.full_name, tracked_files,
                                       locations=locations, symbol_index=symbol_index,
                                       locations_confirmed=locations_confirmed, requirements=requirements)
        finally:
            rejected_attempts = getattr(planner, "attempt_errors", None)
            if isinstance(rejected_attempts, list) and rejected_attempts:
                tracker.action("plan_rejected_attempts", None, rejected_attempts, success=False)
        run.solution_plan = plan.model_dump()
        if localization is not None:
            run.solution_plan["localization"] = localization.model_dump(
                exclude={"locations": {"__all__": {"snippet"}}, "searches": True})
        tracker.action("solution_plan", None, run.solution_plan)

        # Planning is read-only: refuse to go further if anything touched the checkout.
        modified = list(workspace.changed_files() or [])
        if modified:
            tracker.finish(AgentStateEnum.FAILED, "Workspace modified during planning",
                           error=f"Planning must not modify the repository, but these files changed: {modified[:20]}",
                           termination_reason="WORKSPACE_MODIFIED_DURING_PLANNING")
            return
        tracker.action("workspace_check", base_commit_sha, "Checkout unchanged after planning")

        if not _flag("ENABLE_CODE_MUTATION"):
            tracker.finish(AgentStateEnum.PLAN_READY, "Solution plan generated successfully")
            logger.info("Code mutation disabled (ENABLE_CODE_MUTATION=False). Worker completed milestone at PLAN_READY.")
            return
        tracker.status(AgentStateEnum.PLAN_READY, "Solution plan generated successfully")

        # Step 4: Baseline tests on the untouched checkout
        runner = TestRunner(workspace.workspace_path)
        tracker.status(AgentStateEnum.TESTING, "Running existing tests on the unmodified repository (baseline)")
        baseline = runner.run_tests()
        run.baseline_test_result = baseline
        tracker.action("baseline_tests", baseline.get("command"), _test_evidence(baseline), success=bool(baseline.get("success")))
        if baseline.get("outcome") in ENVIRONMENT_OUTCOMES:
            tracker.finish(
                AgentStateEnum.FAILED,
                "Sandbox cannot run this repository's tests",
                error=f"Tests could not run on the unmodified repository ({baseline.get('outcome')}): "
                      f"{(baseline.get('stderr') or '')[-1500:]}",
                termination_reason="ENVIRONMENT_FAILURE",
            )
            return

        # Step 5: Grounded code generation
        tracker.status(AgentStateEnum.CODING, "Generating code changes from solution plan")
        try:
            metrics = CodingAgent().implement(
                workspace.workspace_path,
                analysis=run.issue_analysis,
                plan=run.solution_plan,
                target_files=[f.path for f in plan.affected_files if f.operation != "delete"],
                tracked_files=tracked_files,
                extra_context_files=[c.file_path for c in context],
            )
        except PatchError as patch_err:
            # The coding agent has already restored every file it touched.
            tracker.action("apply_patch", None, str(patch_err), success=False)
            tracker.finish(AgentStateEnum.FAILED, "No valid code change could be produced",
                           error=str(patch_err), termination_reason="NO_VALID_PATCH")
            return
        run.modified_files = metrics.get("changed_files", [])
        run.diff_summary = metrics.get("summary", "")
        tracker.action("apply_patch", None, {"changed_files": run.modified_files})
        tracker.status(AgentStateEnum.PATCH_APPLIED, f"Patch applied to {len(run.modified_files)} file(s)")

        # Step 6: Sandbox tests and bounded repair
        tracker.status(AgentStateEnum.TESTING, "Executing tests in isolated sandbox")
        test_res = runner.run_tests()
        run.test_result = test_res
        tracker.action("run_tests", test_res.get("command"), _test_evidence(test_res), success=bool(test_res.get("success")))

        if not test_res.get("success", False):
            tracker.status(AgentStateEnum.TEST_FAILED, f"Tests failed after the change: {_test_evidence(test_res)}")
            repair_result = execute_repair_loop(
                run_id=run.id,
                initial_context={
                    "issue_context": f"Issue #{issue.number}: {issue.title}\n{issue.body or ''}",
                    "solution_plan": run.solution_plan,
                    "test_result": test_res,
                    "baseline_test_result": baseline,
                    "modified_files": list(run.modified_files or []),
                    "tracked_files": tracked_files,
                },
                workspace_path=workspace.workspace_path,
                on_progress=lambda status, step: tracker.status(AgentStateEnum[status], step),
            )
            run.repair_attempt = repair_result.get("attempts", 0)
            run.repair_history = repair_result.get("repair_history")
            run.termination_reason = repair_result.get("termination_reason")
            run.failure_analysis = repair_result.get("failure_analysis")
            run.repair_plan = repair_result.get("repair_plan")
            run.test_result = repair_result.get("test_result") or run.test_result
            run.modified_files = sorted(set(run.modified_files or []) | set(repair_result.get("modified_files") or []))
            db.commit()
            tracker.action("repair_loop", None, {k: repair_result.get(k) for k in ("status", "attempts", "termination_reason")},
                           success=repair_result.get("status") == "REPAIR_SUCCESS")

            if repair_result.get("status") != "REPAIR_SUCCESS":
                tracker.finish(
                    AgentStateEnum.REPAIR_EXHAUSTED,
                    f"Repair stopped ({run.termination_reason}); last test result: {_test_evidence(run.test_result)}",
                    termination_reason=run.termination_reason,
                )
                return

        tracker.status(AgentStateEnum.REPAIR_SUCCESS, f"Tests passed: {_test_evidence(run.test_result)}")

        # Step 7: Review & security checks on what actually changed on disk
        tracker.status(AgentStateEnum.VALIDATING, "Validating git diff and scanning for secrets")
        agent_files = set(run.modified_files or [])
        changed = set(workspace.changed_files())
        stage_targets = sorted(agent_files & changed)
        stray = sorted(changed - agent_files)
        if stray:
            # e.g. files written by the test suite itself; never commit them.
            logger.info(f"Ignoring files changed outside the agent's edits: {stray}")
        if not stage_targets:
            tracker.finish(AgentStateEnum.VALIDATION_FAILED, "No changes to commit",
                           error="Validation rejected: Empty diff against base commit.")
            return

        workspace.stage_files(stage_targets)
        staged_diff = workspace.run_git_command(["diff", "--cached", base_commit_sha])
        run.diff_summary = staged_diff if isinstance(staged_diff, str) else run.diff_summary
        added_lines = [l[1:] for l in (staged_diff or "").splitlines() if l.startswith("+") and not l.startswith("+++")]
        secret_hits = find_secrets(["\n".join(added_lines)])
        tracker.action("validate_changes", stage_targets, {"secret_patterns": len(secret_hits), "ignored_files": stray},
                       success=not secret_hits)
        if secret_hits:
            tracker.finish(AgentStateEnum.VALIDATION_FAILED, "Potential secret detected",
                           error="Validation rejected: Potential secret or credential detected in modified files.")
            return

        if not _flag("ENABLE_PR_CREATION", True):
            tracker.finish(AgentStateEnum.COMPLETED,
                           "Change verified locally; PR creation disabled (ENABLE_PR_CREATION=false)")
            return

        # Step 8: Branch, Commit, Push, and Pull Request (Idempotent)
        branch_name = f"agent/issue-{issue.number}-run-{run.id}"

        try:
            workspace.checkout_branch(branch_name, create=True)
            tracker.status(AgentStateEnum.BRANCH_CREATED, f"Created branch {branch_name}")
        except Exception as branch_err:
            logger.error(f"Branch creation error: {_scrub_secrets(str(branch_err))}")
            tracker.finish(AgentStateEnum.BRANCH_CREATION_FAILED, "Branch creation failed",
                           error=f"Branch creation failed: {branch_err}")
            return

        try:
            tracker.status(AgentStateEnum.COMMITTING, "Committing validated changes")
            commit_msg = f"fix(issue-{issue.number}): resolve {issue.title}\n\nAutomated fix generated by Auto Issue Solver for #{issue.number}."
            workspace.commit(commit_msg)
            commit_raw = workspace.run_git_command(["rev-parse", "HEAD"])
            commit_sha = commit_raw.strip() if isinstance(commit_raw, str) else ""
            tracker.status(AgentStateEnum.COMMITTED, f"Committed changes ({commit_sha[:7]})")
        except Exception as commit_err:
            logger.error(f"Commit error: {_scrub_secrets(str(commit_err))}")
            tracker.finish(AgentStateEnum.COMMIT_FAILED, "Commit failed", error=f"Commit failed: {commit_err}")
            return

        try:
            tracker.status(AgentStateEnum.PUSHING, f"Pushing branch {branch_name} to remote")
            if github_svc.get_branch_sha(repo.full_name, branch_name):
                raise RuntimeError(f"Remote branch {branch_name} already exists; refusing to overwrite it.")
            workspace.push_branch(github_svc.get_installation_token(), branch_name)
            tracker.status(AgentStateEnum.PUSHED, f"Pushed branch {branch_name} to remote")
        except Exception as push_err:
            logger.error(f"Push error: {_scrub_secrets(str(push_err))}")
            tracker.finish(AgentStateEnum.PUSH_FAILED, "Push failed", error=f"Push failed: {push_err}")
            return

        try:
            tracker.status(AgentStateEnum.CREATING_PR, "Creating GitHub Pull Request")
            existing_pr = github_svc.find_existing_pull_request(repo.full_name, issue.number, branch_name)
            if existing_pr:
                logger.info(f"Idempotency: Reusing existing PR #{existing_pr.number}")
                pr = existing_pr
            else:
                plan_summary = (run.solution_plan or {}).get("summary", "N/A")
                pr_body = (
                    f"## Summary\n"
                    f"Automated change for issue #{issue.number}, generated by Auto Issue Solver.\n\n"
                    f"Resolves #{issue.number}\n\n"
                    f"### Approach\n{plan_summary}\n\n"
                    f"### Files changed\n" + "".join(f"- `{p}`\n" for p in stage_targets) + "\n"
                    f"### Test evidence (sandbox)\n"
                    f"- Baseline (before change): {_test_evidence(run.baseline_test_result)}\n"
                    f"- After change: {_test_evidence(run.test_result)}\n"
                    f"- Command: `{' '.join((run.test_result or {}).get('command') or [])}`\n"
                    f"- Repair attempts: {run.repair_attempt or 0}\n\n"
                    f"### Commits\n- Base: `{base_commit_sha[:7]}`\n- Head: `{commit_sha[:7]}`\n\n"
                    f"### Review notes\nThis change was written by an LLM and verified only by the repository's "
                    f"existing and generated tests. Please review it carefully before merging.\n"
                )
                pr = github_svc.create_pull_request(repo.full_name, branch_name, base_branch,
                                                    f"fix: resolve #{issue.number} - {issue.title}", pr_body)
            pr_url = getattr(pr, "html_url", None)
            if not isinstance(pr_url, str) or not pr_url:
                raise RuntimeError("GitHub did not return a pull request URL")
            pr_id = str(getattr(pr, "id", "") or getattr(pr, "number", ""))

            if not db.query(PullRequest).filter(PullRequest.issue_id == issue.id, PullRequest.branch_name == branch_name).first():
                db.add(PullRequest(issue_id=issue.id, github_pr_id=pr_id, branch_name=branch_name, pr_url=pr_url, status="open"))
                db.commit()

            try:
                github_svc.comment_on_issue(repo.full_name, issue.number, f"Automated fix prepared in PR: {pr_url}")
            except Exception as comment_err:
                logger.warning(f"Could not comment on issue #{issue.number}: {_scrub_secrets(str(comment_err))}")

            tracker.action("create_pull_request", branch_name, pr_url)
            tracker.finish(AgentStateEnum.PR_CREATED, f"Pull request created: {pr_url}")
        except Exception as pr_err:
            logger.error(f"PR creation error: {_scrub_secrets(str(pr_err))}")
            tracker.finish(AgentStateEnum.PR_CREATION_FAILED, "PR creation failed", error=f"PR creation failed: {pr_err}")

    except Exception as e:
        scrubbed_err = _scrub_secrets(str(e))
        logger.error(f"Worker unrecoverable error: {scrubbed_err}\n{_scrub_secrets(traceback.format_exc())}")
        db.rollback()
        run = db.query(AgentRun).filter(AgentRun.id == agent_run_id).first()
        if run and run.status not in TERMINAL_AGENT_STATES:
            run.error_message = f"{type(e).__name__} while {run.current_step or 'starting'}: {scrubbed_err}"
            _Run(db, run).finish(AgentStateEnum.FAILED, run.current_step or "Failed")
    finally:
        if workspace:
            try:
                workspace.cleanup()
                logger.info("Workspace cleaned up successfully.")
            except Exception as cleanup_err:
                logger.warning(f"Workspace cleanup failed: {cleanup_err}")
        _log_llm_usage(db, agent_run_id, usage)
        db.close()


def _fail_interrupted_runs():
    """Mark runs whose job was in progress when a previous worker died as FAILED (not retried)."""
    try:
        interrupted = queue_service.drain_interrupted_jobs()
    except Exception as e:
        logger.error(f"Could not inspect interrupted jobs: {e}")
        return
    if not interrupted:
        return
    db = SessionLocal()
    try:
        for job in interrupted:
            run = db.query(AgentRun).filter(AgentRun.id == job.get("agent_run_id")).first()
            if run and run.status not in TERMINAL_AGENT_STATES:
                run.error_message = f"Worker stopped while the run was in state {run.status.value}; start a new run to retry."
                _Run(db, run).finish(AgentStateEnum.FAILED, run.current_step or "Interrupted")
                logger.warning(f"Marked interrupted AgentRun {run.id} as FAILED")
    finally:
        db.close()


def run_worker():
    logger.info("Initializing worker (single-worker mode: interrupted jobs are recovered at startup)...")
    active_provider = getattr(settings, "LLM_PROVIDER", "ollama").lower()
    logger.info("Active LLM Provider configured: %s", active_provider)
    if active_provider == "ollama":
        check = verify_ollama_available()
        if check["available"]:
            logger.info("Ollama preflight verification passed: %s", check["message"])
        else:
            logger.error("Ollama preflight verification failed: %s", check["message"])

    _fail_interrupted_runs()

    while True:
        try:
            job = queue_service.dequeue_issue_job()
            if not job:
                continue
            payload, raw = job
            try:
                process_agent_run(payload)
            finally:
                queue_service.ack_issue_job(raw)
        except KeyboardInterrupt:
            logger.info("Worker stopped.")
            break
        except Exception as err:
            logger.error(f"Worker loop error (is Redis reachable at REDIS_URL?): {err}")
            time.sleep(3)

if __name__ == "__main__":
    run_worker()
