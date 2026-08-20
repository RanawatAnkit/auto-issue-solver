import React, { useEffect, useState } from 'react';
import { getAgentRuns } from '../services/api';
import { AgentRunResponse } from '../types/domain';
import { StatusBadge } from '../components/layout/StatusBadge';
import { Link } from 'react-router-dom';
import { format } from 'date-fns';

export const AgentRuns: React.FC = () => {
    const [runs, setRuns] = useState<AgentRunResponse[]>([]);
    const [loading, setLoading] = useState(true);
    const [error, setError] = useState<string | null>(null);

    useEffect(() => {
        getAgentRuns()
            .then((r) => { setRuns(r); setLoading(false); })
            .catch((e) => { setError(e.message); setLoading(false); });
    }, []);

    if (loading) return <div className="p-8 text-slate-400 animate-pulse">Loading Runs...</div>;
    if (error) return <div className="p-8 text-rose-500">Error loading runs: {error}</div>;

    return (
        <div className="p-8 max-w-7xl mx-auto space-y-6">
            <h2 className="text-2xl font-bold text-white">Agent Runs</h2>

            {runs.length === 0 ? (
                <div className="bg-slate-800/30 rounded-xl p-8 text-center text-slate-500 border border-slate-800">
                    No agent runs yet.
                </div>
            ) : (
                <div className="border border-slate-800 rounded-xl overflow-hidden shadow-sm bg-slate-900">
                    <table className="w-full text-left text-sm text-slate-400">
                        <thead className="text-xs uppercase bg-slate-800/80 text-slate-300">
                            <tr>
                                <th className="px-6 py-4 font-semibold">ID</th>
                                <th className="px-6 py-4 font-semibold">Issue</th>
                                <th className="px-6 py-4 font-semibold">Status</th>
                                <th className="px-6 py-4 font-semibold">Step</th>
                                <th className="px-6 py-4 font-semibold">Started</th>
                                <th className="px-6 py-4 font-semibold px-2 text-right">Retries</th>
                            </tr>
                        </thead>
                        <tbody className="divide-y divide-slate-800/50">
                            {runs.map((r) => (
                                <tr key={r.id} className="hover:bg-slate-800/30 transition-colors group">
                                    <td className="px-6 py-4 font-medium text-white group-hover:text-indigo-400">
                                        <Link to={`/agent-runs/${r.id}`} className="block">#{r.id}</Link>
                                    </td>
                                    <td className="px-6 py-4">#{r.issue_id}</td>
                                    <td className="px-6 py-4"><StatusBadge status={r.status} /></td>
                                    <td className="px-6 py-4 text-xs font-mono">{r.current_step || '-'}</td>
                                    <td className="px-6 py-4 whitespace-nowrap">{format(new Date(r.started_at), 'MMM d, HH:mm')}</td>
                                    <td className="px-6 py-4 px-2 text-right">{r.retry_count}</td>
                                </tr>
                            ))}
                        </tbody>
                    </table>
                </div>
            )}
        </div>
    );
};
