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

export interface BenchmarkReferenceSolution {
    file_path?: string;
    target_function?: string;
    summary?: string;
}

export interface BenchmarkTestSuite {
    test_file?: string;
    test_name?: string;
    fail_to_pass?: string[];
    pass_to_pass?: string[];
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

export interface BenchmarkTask {
    instance_id: string;
    repo: string;
    base_commit: string;
    problem_statement: string;
    hints_text?: string;
    test_patch?: string;
    pass_to_pass?: string[];
    fail_to_pass?: string[];
    files_to_modify: string[];
    difficulty?: string;
    category?: string;
    reference_solution?: BenchmarkReferenceSolution;
    test_suite?: BenchmarkTestSuite;
    latest_status?: 'RESOLVED' | 'FAILED' | 'PENDING' | string;
    latest_runtime?: number | null;
    latest_provider?: string | null;
    latest_patch?: string | null;
    latest_analysis?: IssueAnalysisData | null;
}

export interface EvaluationStats {
    total_benchmark_tasks: number;
    total_evaluated: number;
    resolved_count: number;
    failed_count: number;
    pass_rate_percentage: number;
    average_resolution_time_seconds: number;
    active_provider: string;
    active_model: string;
    benchmark_dataset: string;
}

export interface EvaluationRunResult {
    run_id: string;
    instance_id: string;
    repo: string;
    provider: string;
    model: string;
    status: 'RESOLVED' | 'FAILED' | string;
    runtime_seconds: number;
    completed_at: string;
    patch_diff: string;
    tests_summary?: string;
    error?: string;
}

export interface EvaluationBatchResponse {
    status: string;
    total_evaluated: number;
    resolved_count: number;
    results: EvaluationRunResult[];
}
