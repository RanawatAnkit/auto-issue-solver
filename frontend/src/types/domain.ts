export interface AgentRunResponse {
    id: number;
    issue_id: number;
    status: string;
    current_step?: string | null;
    error_message?: string | null;
    retry_count: number;
    started_at: string;
    completed_at?: string | null;
}

// Outcome of a sandbox test run. Only PASSED is evidence that a change works;
// ENVIRONMENT_ERROR / DEPENDENCY_ERROR mean the tests could not run at all.
export type TestOutcome =
    | 'PASSED' | 'TESTS_FAILED' | 'COLLECTION_ERROR' | 'NO_TESTS'
    | 'DEPENDENCY_ERROR' | 'ENVIRONMENT_ERROR' | 'TIMEOUT';

export interface TestResult {
    success: boolean;
    outcome?: TestOutcome;
    exit_code: number;
    stdout?: string;
    stderr?: string;
    duration_seconds?: number;
    timed_out?: boolean;
    command?: string[] | null;
    summary?: Record<string, number>;
}

export interface RepairAttempt {
    attempt: number;
    target_files?: string[];
    changed_files?: string[];
    error?: string;
    analysis?: Record<string, unknown>;
    repair_plan?: Record<string, unknown>;
    test_result?: TestResult;
}

export interface AgentRunDetailResponse extends AgentRunResponse {
    issue_analysis?: Record<string, unknown> | null;
    solution_plan?: Record<string, unknown> | null;
    modified_files?: string[] | null;
    diff_summary?: string | null;
    test_result?: TestResult | null;
    baseline_test_result?: TestResult | null;
    repair_attempt?: number | null;
    failure_analysis?: Record<string, unknown> | null;
    repair_plan?: Record<string, unknown> | null;
    termination_reason?: string | null;
    total_runtime?: number | null;
    repair_history?: RepairAttempt[] | null;
}

export interface AgentAction {
    id: number;
    action_type: string;
    input_data?: string | null;
    output_data?: string | null;
    success?: boolean | null;
    created_at?: string | null;
}

export interface PullRequestResponse {
    id: number;
    issue_id: number;
    github_pr_id: string;
    branch_name: string;
    pr_url: string;
    status: string;
    created_at: string;
    merged_at?: string | null;
}

export interface Repository {
    id: number;
    github_repo_id: string;
    owner: string;
    name: string;
    full_name: string;
    default_branch: string;
    clone_url: string;
}

export interface GitHubIssue {
    id: number;
    number: number;
    title: string;
    body: string | null;
    labels: string[];
    state: string;
    created_at: string;
}

export interface UserProfile {
    id: number;
    github_user_id: string;
    github_login: string;
    github_name: string | null;
    github_avatar_url: string | null;
}

export interface IssueAnalysisData {
    problem_summary?: string;
    expected_behavior?: string;
    current_behavior?: string;
    affected_areas?: string[];
    constraints?: string[];
    acceptance_criteria?: string[];
    [key: string]: unknown;
}

// Outcomes reported by the SWE-bench harness (backend/app/eval/harness.py).
// RESOLVED / UNRESOLVED / NO_PATCH / PATCH_APPLY_FAILED are agent attempts;
// LLM_ERROR / ENV_ERROR / HARNESS_ERROR are infrastructure failures.
export type EvaluationOutcome =
    | 'RESOLVED'
    | 'UNRESOLVED'
    | 'NO_PATCH'
    | 'PATCH_APPLY_FAILED'
    | 'LLM_ERROR'
    | 'ENV_ERROR'
    | 'HARNESS_ERROR';

export interface EvaluationTestReport {
    f2p_passed: number;
    f2p_total: number;
    p2p_passed: number;
    p2p_total: number;
    f2p_failed: string[];
    p2p_failed: string[];
}

export interface EvaluationResult {
    run_id: string;
    instance_id: string;
    repo: string;
    mode: 'agent' | 'gold';
    outcome: EvaluationOutcome;
    resolved: boolean;
    detail: string;
    provider: string | null;
    model: string | null;
    runtime_seconds: number;
    started_at: string;
    completed_at: string;
    patch: string;
    tests: EvaluationTestReport | null;
    analysis: IssueAnalysisData | null;
    plan: Record<string, unknown> | null;
    test_log_tail: string;
    stage_seconds: Record<string, number>;
    artifacts?: string;
}

export interface BenchmarkTask {
    instance_id: string;
    repo: string;
    version: string;
    base_commit: string;
    created_at: string;
    problem_statement: string;
    hints_text?: string;
    patch: string;
    test_patch: string;
    FAIL_TO_PASS: string[];
    PASS_TO_PASS: string[];
    latest_result: EvaluationResult | null;
    environment_check: EvaluationResult | null;
}

export interface EvaluationStats {
    total_tasks: number;
    evaluated: number;
    attempted: number;
    resolved: number;
    resolution_rate_attempted: number | null;
    resolution_rate_total: number | null;
    outcomes: Partial<Record<EvaluationOutcome, number>>;
    average_runtime_seconds: number | null;
    environment_validated: number;
    environment_checked: number;
    environment_invalid: string[];
    last_provider: string | null;
    last_model: string | null;
    dataset: string;
}

export interface EvaluationJobStatus {
    running: boolean;
    run_id?: string;
    mode?: 'agent' | 'gold';
    total?: number;
    done?: number;
    current?: string | null;
    started_at?: string;
    finished_at?: string | null;
    stopped_reason?: string | null;
    error?: string | null;
    outcomes?: Partial<Record<EvaluationOutcome, number>>;
    // Tasks left out because they need network and the run did not allow it.
    skipped?: string[];
}
