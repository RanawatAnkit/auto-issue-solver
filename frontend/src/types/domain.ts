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
