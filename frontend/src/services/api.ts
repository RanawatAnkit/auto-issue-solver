import axios from 'axios';
import { 
    AgentAction, 
    AgentRunDetailResponse, 
    AgentRunResponse, 
    GitHubIssue, 
    PullRequestResponse, 
    Repository, 
    UserProfile,
    BenchmarkTask,
    EvaluationStats,
    EvaluationResult,
    EvaluationJobStatus
} from '../types/domain';

const API_BASE_URL = import.meta.env.VITE_API_BASE_URL || 'http://localhost:8000';

const apiClient = axios.create({
    baseURL: API_BASE_URL,
    withCredentials: true,
    headers: {
        'Content-Type': 'application/json',
    },
});

export const getAgentRuns = async (): Promise<AgentRunResponse[]> => {
    const response = await apiClient.get<AgentRunResponse[]>('/agent-runs/');
    return response.data;
};

export const getAgentRun = async (runId: string | number): Promise<AgentRunDetailResponse> => {
    const response = await apiClient.get<AgentRunDetailResponse>(`/agent-runs/${runId}`);
    return response.data;
};

export const getAgentRunActions = async (runId: string | number): Promise<AgentAction[]> => {
    const response = await apiClient.get<AgentAction[]>(`/agent-runs/${runId}/actions`);
    return response.data;
};

export const rerunAgentRun = async (runId: string | number): Promise<{ run_id: number; status: string }> => {
    const response = await apiClient.post(`/agent-runs/${runId}/rerun`);
    return response.data;
};

export const errorStatus = (err: unknown): number | undefined =>
    axios.isAxiosError(err) ? err.response?.status : undefined;

/** The backend's explanation for a failed request (FastAPI `detail`), falling back to the transport error. */
export const errorMessage = (err: unknown, fallback = 'Request failed'): string => {
    if (axios.isAxiosError(err)) {
        const detail = err.response?.data?.detail;
        if (typeof detail === 'string') return detail;
        if (Array.isArray(detail)) return detail.map((d) => d?.msg ?? String(d)).join('; ');
        return err.message || fallback;
    }
    return err instanceof Error ? err.message : fallback;
};

export const getPullRequests = async (): Promise<PullRequestResponse[]> => {
    const response = await apiClient.get<PullRequestResponse[]>('/pull-requests/');
    return response.data;
};

export const getPullRequest = async (id: string | number): Promise<PullRequestResponse> => {
    const response = await apiClient.get<PullRequestResponse>(`/pull-requests/${id}`);
    return response.data;
};

// --- AUTHENTICATION ---
export const getMe = async (): Promise<UserProfile> => {
    const response = await apiClient.get<UserProfile>('/auth/me');
    return response.data;
};

export const logout = async (): Promise<void> => {
    await apiClient.post('/auth/logout');
};

// --- REPOSITORIES ---
export const getRepositories = async (): Promise<Repository[]> => {
    const response = await apiClient.get<Repository[]>('/repositories/');
    return response.data;
};

export const connectRepository = async (fullName: string): Promise<Repository> => {
    const response = await apiClient.post<Repository>('/repositories/connect', { full_name: fullName });
    return response.data;
};

export const getIssues = async (repoId: number, state: string = 'open'): Promise<GitHubIssue[]> => {
    const response = await apiClient.get<GitHubIssue[]>(`/repositories/${repoId}/issues`, { params: { state } });
    return response.data;
};

export const solveIssue = async (repoId: number | string, issueNumber: number | string): Promise<{ run_id: number; status: string }> => {
    const response = await apiClient.post(`/repositories/${repoId}/issues/${issueNumber}/solve`);
    return response.data;
};

export const getRepositoryIssue = async (repoId: number | string, issueNumber: number | string): Promise<GitHubIssue> => {
    const response = await apiClient.get<GitHubIssue>(`/repositories/${repoId}/issues/${issueNumber}`);
    return response.data;
};

// --- SWE-BENCH EVALUATION ---
export const getEvaluationStats = async (): Promise<EvaluationStats> => {
    const response = await apiClient.get<EvaluationStats>('/evaluation/stats');
    return response.data;
};

export const getBenchmarkTasks = async (): Promise<BenchmarkTask[]> => {
    const response = await apiClient.get<BenchmarkTask[]>('/evaluation/tasks');
    return response.data;
};

export const getEvaluationHistory = async (): Promise<EvaluationResult[]> => {
    const response = await apiClient.get<EvaluationResult[]>('/evaluation/history');
    return response.data;
};

export const getEvaluationStatus = async (): Promise<EvaluationJobStatus> => {
    const response = await apiClient.get<EvaluationJobStatus>('/evaluation/status');
    return response.data;
};

// Starts a background run and returns immediately; poll getEvaluationStatus.
export const triggerEvaluationRun = async (
    taskIds: string[] | undefined,
    mode: 'agent' | 'gold' = 'agent',
    allowNetwork = false,
    onlyPending = false
): Promise<EvaluationJobStatus> => {
    const response = await apiClient.post<EvaluationJobStatus>('/evaluation/run', {
        task_ids: taskIds && taskIds.length > 0 ? taskIds : undefined,
        mode,
        allow_network: allowNetwork,
        only_pending: onlyPending
    });
    return response.data;
};

export const stopEvaluationRun = async (): Promise<EvaluationJobStatus> => {
    const response = await apiClient.post<EvaluationJobStatus>('/evaluation/stop');
    return response.data;
};

