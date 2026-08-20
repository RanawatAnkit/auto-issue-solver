import axios from 'axios';
import { AgentRunDetailResponse, AgentRunResponse, PullRequestResponse } from '../types/domain';

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

export const getPullRequests = async (): Promise<PullRequestResponse[]> => {
    const response = await apiClient.get<PullRequestResponse[]>('/pull-requests/');
    return response.data;
};

export const getPullRequest = async (id: string | number): Promise<PullRequestResponse> => {
    const response = await apiClient.get<PullRequestResponse>(`/pull-requests/${id}`);
    return response.data;
};

// --- AUTHENTICATION ---
export const getMe = async (): Promise<any> => {
    const response = await apiClient.get('/auth/me');
    return response.data;
};

export const logout = async (): Promise<void> => {
    await apiClient.post('/auth/logout');
};

// --- REPOSITORIES ---
export const getRepositories = async (): Promise<any[]> => {
    const response = await apiClient.get('/repositories/');
    return response.data;
};

export const connectRepository = async (fullName: string): Promise<any> => {
    const response = await apiClient.post('/repositories/connect', { full_name: fullName });
    return response.data;
};

export const getIssues = async (repoId: number, state: string = 'open'): Promise<any[]> => {
    const response = await apiClient.get(`/repositories/${repoId}/issues`, { params: { state } });
    return response.data;
};

export const solveIssue = async (repoId: number | string, issueNumber: number | string): Promise<any> => {
    const response = await apiClient.post(`/repositories/${repoId}/issues/${issueNumber}/solve`);
    return response.data;
};

export const getRepositoryIssue = async (repoId: number | string, issueNumber: number | string): Promise<any> => {
    const response = await apiClient.get(`/repositories/${repoId}/issues/${issueNumber}`);
    return response.data;
};
