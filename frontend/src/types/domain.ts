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

export interface AgentRunDetailResponse extends AgentRunResponse {
    issue_analysis?: Record<string, unknown> | null;
    solution_plan?: Record<string, unknown> | null;
    modified_files?: Record<string, unknown> | null;
    diff_summary?: string | null;
    test_result?: Record<string, unknown> | null;
    repair_attempt: number;
    failure_analysis?: Record<string, unknown> | null;
    repair_plan?: Record<string, unknown> | null;
    termination_reason?: string | null;
    total_runtime: number;
    repair_history?: Record<string, unknown> | null;
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
