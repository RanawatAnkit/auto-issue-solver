import React from 'react';
import { BrowserRouter, Routes, Route } from 'react-router-dom';
import { Dashboard } from './pages/Dashboard';
import { AgentRuns } from './pages/AgentRuns';
import { PullRequests } from './pages/PullRequests';
import AgentRunDetails from './pages/AgentRunDetails';

import { AuthProvider } from './context/AuthContext';
import { ProtectedRoute } from './components/common/ProtectedRoute';
import { Login } from './pages/Login';
import Repositories from './pages/Repositories';
import RepositoryIssues from './pages/RepositoryIssues';
import IssueDetails from './pages/IssueDetails';
import { Evaluation } from './pages/Evaluation';

const App: React.FC = () => {
    return (
        <AuthProvider>
            <BrowserRouter>
                <Routes>
                    <Route path="/login" element={<Login />} />
                    <Route element={<ProtectedRoute />}>
                        <Route path="/" element={<Dashboard />} />
                        <Route path="/agent-runs" element={<AgentRuns />} />
                        <Route path="/agent-runs/:runId" element={<AgentRunDetails />} />
                        <Route path="/repositories" element={<Repositories />} />
                        <Route path="/repositories/:repositoryId/issues" element={<RepositoryIssues />} />
                        <Route path="/repositories/:repositoryId/issues/:issueNumber" element={<IssueDetails />} />
                        <Route path="/pull-requests" element={<PullRequests />} />
                        <Route path="/evaluation" element={<Evaluation />} />
                    </Route>
                </Routes>
            </BrowserRouter>
        </AuthProvider>
    );
};

export default App;
