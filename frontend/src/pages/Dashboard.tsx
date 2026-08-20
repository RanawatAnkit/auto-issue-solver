import React, { useEffect, useState } from 'react';
import { getAgentRuns, getPullRequests } from '../services/api';
import { AgentRunResponse, PullRequestResponse } from '../types/domain';
import { Activity, CheckCircle, GitPullRequest, XCircle } from 'lucide-react';
import { StatusBadge } from '../components/layout/StatusBadge';
import { Link } from 'react-router-dom';
import { formatDistanceToNow } from 'date-fns';

export const Dashboard: React.FC = () => {
    const [runs, setRuns] = useState<AgentRunResponse[]>([]);
    const [prs, setPrs] = useState<PullRequestResponse[]>([]);
    const [loading, setLoading] = useState(true);
    const [error, setError] = useState<string | null>(null);

    useEffect(() => {
        Promise.all([getAgentRuns(), getPullRequests()])
            .then(([r, p]) => { setRuns(r); setPrs(p); setLoading(false); })
            .catch((e) => { setError(e.message); setLoading(false); });
    }, []);

    if (loading) return <div className="p-8 text-slate-400 animate-pulse">Loading Dashboard...</div>;
    if (error) return <div className="p-8 text-rose-500">Error loading dashboard: {error}</div>;

    const successfulRuns = runs.filter(r => r.status.includes('SUCCESS') || r.status.includes('COMPLETED') || r.status.includes('PR_CREATED')).length;
    const failedRuns = runs.filter(r => r.status.includes('FAIL') || r.status.includes('EXHAUSTED')).length;

    // Recent runs (last 5)
    const recentRuns = [...runs].sort((a, b) => new Date(b.started_at).getTime() - new Date(a.started_at).getTime()).slice(0, 5);

    return (
        <div className="p-8 max-w-7xl mx-auto space-y-8">
            <h2 className="text-2xl font-bold text-white mb-6">Overview</h2>

            <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-4 gap-6">
                <div className="bg-slate-800/50 p-6 rounded-xl border border-slate-700/50 flex flex-col">
                    <span className="text-slate-400 font-medium text-sm flex items-center gap-2"><Activity size={16} /> Total Runs</span>
                    <span className="text-3xl font-bold text-white mt-2">{runs.length}</span>
                </div>
                <div className="bg-emerald-950/20 p-6 rounded-xl border border-emerald-900/50 flex flex-col">
                    <span className="text-emerald-400/80 font-medium text-sm flex items-center gap-2"><CheckCircle size={16} /> Successful</span>
                    <span className="text-3xl font-bold text-emerald-400 mt-2">{successfulRuns}</span>
                </div>
                <div className="bg-rose-950/20 p-6 rounded-xl border border-rose-900/50 flex flex-col">
                    <span className="text-rose-400/80 font-medium text-sm flex items-center gap-2"><XCircle size={16} /> Failed</span>
                    <span className="text-3xl font-bold text-rose-400 mt-2">{failedRuns}</span>
                </div>
                <div className="bg-indigo-950/20 p-6 rounded-xl border border-indigo-900/50 flex flex-col">
                    <span className="text-indigo-400/80 font-medium text-sm flex items-center gap-2"><GitPullRequest size={16} /> PRs Created</span>
                    <span className="text-3xl font-bold text-indigo-400 mt-2">{prs.length}</span>
                </div>
            </div>

            <div className="mt-12 space-y-4">
                <h3 className="text-xl font-bold text-white">Recent Agent Runs</h3>
                {recentRuns.length === 0 ? (
                    <div className="bg-slate-800/30 rounded-xl p-8 text-center text-slate-500 border border-slate-800">
                        No agent runs yet.
                    </div>
                ) : (
                    <div className="border border-slate-800 rounded-xl overflow-hidden shadow-sm bg-slate-900">
                        <table className="w-full text-left text-sm text-slate-400">
                            <thead className="text-xs uppercase bg-slate-800/80 text-slate-300">
                                <tr>
                                    <th className="px-6 py-4 font-semibold">Run ID</th>
                                    <th className="px-6 py-4 font-semibold">Issue #</th>
                                    <th className="px-6 py-4 font-semibold">Status</th>
                                    <th className="px-6 py-4 font-semibold">Started</th>
                                    <th className="px-6 py-4 font-semibold">Retries</th>
                                </tr>
                            </thead>
                            <tbody className="divide-y divide-slate-800/50">
                                {recentRuns.map((r) => (
                                    <tr key={r.id} className="hover:bg-slate-800/30 transition-colors group cursor-pointer">
                                        <td className="px-6 py-4 font-medium text-white group-hover:text-indigo-400">
                                            <Link to={`/agent-runs/${r.id}`} className="block">#{r.id}</Link>
                                        </td>
                                        <td className="px-6 py-4">#{r.issue_id}</td>
                                        <td className="px-6 py-4"><StatusBadge status={r.status} /></td>
                                        <td className="px-6 py-4">{formatDistanceToNow(new Date(r.started_at))} ago</td>
                                        <td className="px-6 py-4">{r.retry_count}</td>
                                    </tr>
                                ))}
                            </tbody>
                        </table>
                    </div>
                )}
            </div>
        </div>
    );
};
